"""Native LIFX backend: duration-carrying SetColor over the LAN protocol.

LIFX firmware has no move/stop verb. What it has is better than WiZ's nothing
and less than Zigbee's Move: every ``SetColor`` carries a ``duration``, and the
bulb interpolates the change itself, linearly, for up to that many milliseconds.
So a hold becomes one datagram — "head for the rail, at a duration that makes
the requested rate true" — and the bulb runs the ramp with no further traffic.
Release is the half LIFX does not answer: there is no "stop where you are"
command, and nothing to ask mid-ramp without paying a round-trip on the press
of a button. But the ramp we started is fully determined — start level, target,
duration, wall clock — so the level the bulb holds *right now* is arithmetic,
not a question. ``stop`` computes it and pins it with a zero-duration
``SetColor``. Two datagrams per gesture, fire-and-forget, against the forty
per second the stepped path would stream.

The transport is the WiZ playbook: a raw UDP socket this backend owns, the
bulb's address read from the LIFX config entry, and unacknowledged writes —
a lost datagram costs one gesture, not an accumulating error, because every
write is absolute. And as with WiZ, bypassing ``light.turn_on`` leaves HA's
state machine stale while the bulb moves, so ``stop`` re-asserts the pinned
level through the light entity; a ramp that runs out to the rail on its own is
picked up by the LIFX integration's next poll instead.

``SetColor`` takes a full HSBK, not a bare brightness, so every write must
carry the bulb's color to avoid shifting it. That color comes from HA's state
at gesture start (or from the ramp we ourselves started, which is fresher) —
one more consequence of never querying the bulb, and the reason a move on a
LIFX light that is *off* is declined rather than sent: a SetColor while off
would silently rewrite the stored level without lighting anything, which is
neither Zigbee's "off stays off" nor WiZ's "light it", just confusion.
"""

from __future__ import annotations

import logging
import socket
import struct
import time
from dataclasses import dataclass

from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    DIRECTION_UP,
    LIFX_DEFAULT_KELVIN,
    LIFX_DOMAIN,
    LIFX_MAX_BRI,
    LIFX_MAX_KELVIN,
    LIFX_MIN_BRI,
    LIFX_MIN_KELVIN,
    LIFX_PKT_SET_COLOR,
    LIFX_PKT_SET_LIGHT_POWER,
    LIFX_PORT,
    LIFX_POWER_ON,
)
from .base import DimmingBackend
from .simulation import resolve_rate

_LOGGER = logging.getLogger(__name__)

_MAX_BRIGHTNESS = 255
# LIFX LAN header (lan.developer.lifx.com/docs/packet-contents), all
# little-endian: frame (size, protocol 1024 | addressable, source), frame
# address (8-byte target = 6-byte serial + 2 zeros, 6 reserved, flags,
# sequence), protocol header (reserved, packet type, reserved).
_HEADER = struct.Struct("<HHI8s6sBBQHH")
_PROTOCOL_ADDRESSABLE = 1024 | (1 << 12)
# Identifies this client in the source field; replies would echo it, but none
# are requested (res_required and ack_required both stay clear).
_SOURCE = 0x64696D6D  # "dimm"
_SET_COLOR = struct.Struct("<BHHHHI")  # reserved, HSBK, duration ms
_SET_POWER = struct.Struct("<HI")  # level, duration ms
_MAX_DURATION_MS = 0xFFFFFFFF


def parse_serial(unique_id: str) -> bytes | None:
    """Pull the 6-byte serial out of a LIFX entity's unique_id.

    The LIFX integration writes a light's unique_id as the bulb's serial the
    way aiolifx reports it — colon-separated hex octets. The serial's bytes go
    onto the wire in order, left-justified in the 8-byte target field.
    """
    raw = unique_id.replace(":", "")
    if len(raw) != 12:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return None


def to_bri16(brightness: float) -> int:
    """Map HA's 0-255 brightness onto LIFX's 16-bit scale."""
    scaled = round(brightness / _MAX_BRIGHTNESS * LIFX_MAX_BRI)
    return max(0, min(LIFX_MAX_BRI, int(scaled)))


def _clamp_kelvin(kelvin: float) -> int:
    return max(LIFX_MIN_KELVIN, min(LIFX_MAX_KELVIN, int(round(kelvin))))


@dataclass
class _Transition:
    """One ramp this backend asked a bulb to run, enough to replay its math.

    The bulb interpolates linearly from ``start_bri`` to ``target_bri`` over
    ``duration`` seconds, so the level at any instant is a lerp on the wall
    clock — which is what lets ``stop`` pin the ramp without querying the bulb,
    and a reversed hold start from where the light visibly is rather than from
    a stale cache.
    """

    start_bri: float
    target_bri: float
    started: float
    duration: float
    hue: int
    saturation: int
    kelvin: int
    # A fade that asserted an explicit color re-asserts it in the resync.
    color_asserted: bool = False
    # A fade up from off ramps power rather than color; its stop must pin both.
    power_fade: bool = False

    def estimate(self, now: float) -> float:
        if self.duration <= 0:
            return self.target_bri
        fraction = min(1.0, max(0.0, (now - self.started) / self.duration))
        return self.start_bri + (self.target_bri - self.start_bri) * fraction


class LifxBackend(DimmingBackend):
    """Rides LIFX's per-command duration to make the bulb run its own ramps."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._sock: socket.socket | None = None
        self._sequence = 0
        # entity_id -> the ramp its bulb is (or last was) running.
        self._transitions: dict[str, _Transition] = {}

    def _ensure_sock(self) -> socket.socket | None:
        """Open the send socket on first use — lazy for the same reasons WiZ's is."""
        if self._sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
            except OSError as err:
                _LOGGER.warning("LIFX backend could not open a UDP socket: %s", err)
                return None
            self._sock = sock
        return self._sock

    async def async_unload(self) -> None:
        self._transitions.clear()
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- entity -> bulb -----------------------------------------------------------

    def _address(self, entity_id: str) -> tuple[str, bytes] | None:
        """Resolve an entity to the bulb's IP and serial, or None."""
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != LIFX_DOMAIN:
            return None
        if entity.config_entry_id is None:
            return None
        entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        if entry is None or entry.domain != LIFX_DOMAIN:
            return None
        host = entry.data.get(CONF_HOST)
        if not host:
            return None
        serial = parse_serial(entity.unique_id)
        if serial is None:
            return None
        return host, serial

    def claims(self, entity_id: str) -> bool:
        return self._address(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    def _send(self, address: tuple[str, bytes], pkt_type: int, payload: bytes) -> None:
        sock = self._ensure_sock()
        if sock is None:
            return
        host, serial = address
        self._sequence = (self._sequence + 1) % 256
        packet = (
            _HEADER.pack(
                _HEADER.size + len(payload),
                _PROTOCOL_ADDRESSABLE,
                _SOURCE,
                serial + b"\x00\x00",
                b"\x00" * 6,
                0,  # neither a response nor an ack is requested
                self._sequence,
                0,
                pkt_type,
                0,
            )
            + payload
        )
        try:
            sock.sendto(packet, (host, LIFX_PORT))
        except OSError as err:
            # Two datagrams per gesture; the user's retry is pressing again.
            _LOGGER.debug("LIFX send to %s failed: %s", host, err)

    def _set_color(
        self,
        address: tuple[str, bytes],
        hue: int,
        saturation: int,
        brightness: int,
        kelvin: int,
        duration_ms: int,
    ) -> None:
        self._send(
            address,
            LIFX_PKT_SET_COLOR,
            _SET_COLOR.pack(
                0,
                hue,
                saturation,
                brightness,
                kelvin,
                min(_MAX_DURATION_MS, max(0, duration_ms)),
            ),
        )

    # -- bulb state, reconstructed without asking the bulb ------------------------

    def _color_of(self, entity_id: str) -> tuple[int, int, int]:
        """The (hue, saturation, kelvin) every write should carry.

        An active transition is the freshest source — it is what the bulb was
        last told. Failing that, HA's state: a light sitting in color-temp mode
        is a white (saturation 0) at its reported kelvin, anything with an
        hs_color keeps that color, and a bare-brightness light is a default
        white. Stale is possible; wrong-by-a-little beats a round-trip on a
        button press.
        """
        active = self._transitions.get(entity_id)
        if active is not None:
            return active.hue, active.saturation, active.kelvin
        state = self.hass.states.get(entity_id)
        attrs = state.attributes if state is not None else {}
        hs = attrs.get("hs_color")
        if attrs.get("color_mode") == "color_temp" or not hs:
            kelvin = attrs.get("color_temp_kelvin")
            return 0, 0, _clamp_kelvin(kelvin) if kelvin else LIFX_DEFAULT_KELVIN
        hue = int(round(hs[0] % 360 / 360.0 * 65535))
        saturation = max(0, min(65535, int(round(hs[1] / 100.0 * 65535))))
        return hue, saturation, LIFX_DEFAULT_KELVIN

    def _current_bri16(self, entity_id: str, now: float) -> float:
        active = self._transitions.get(entity_id)
        if active is not None:
            return active.estimate(now)
        state = self.hass.states.get(entity_id)
        if state is None:
            return 0.0
        return float(to_bri16(state.attributes.get("brightness") or 0))

    async def _resync(self, entity_id: str, record: _Transition, bri16: float) -> None:
        """Re-assert the pinned level through the light entity, WiZ-style."""
        data: dict = {
            "entity_id": entity_id,
            "brightness": max(
                1, min(_MAX_BRIGHTNESS, int(round(bri16 / LIFX_MAX_BRI * 255)))
            ),
        }
        if record.color_asserted:
            data["color_temp_kelvin"] = record.kelvin
        await self.hass.services.async_call("light", "turn_on", data, blocking=False)

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        # `curve` intentionally unused: the bulb interpolates this ramp itself,
        # linearly, and this integration does not rewrite device behavior.
        address = self._address(entity_id)
        if address is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "on":
            # Off stays off, as on the Zigbee-family paths — but here because a
            # SetColor while off would rewrite the stored level invisibly.
            return None
        now = time.monotonic()
        start = self._current_bri16(entity_id, now)
        hue, saturation, kelvin = self._color_of(entity_id)
        target = float(LIFX_MAX_BRI if direction == DIRECTION_UP else LIFX_MIN_BRI)
        # The rate is HA-scale units/second; the duration is whatever makes the
        # remaining distance take exactly that rate.
        units16_per_second = resolve_rate(rate) * (LIFX_MAX_BRI / _MAX_BRIGHTNESS)
        duration = abs(target - start) / units16_per_second
        self._set_color(
            address, hue, saturation, int(target), kelvin, int(round(duration * 1000))
        )
        self._transitions[entity_id] = _Transition(
            start, target, now, duration, hue, saturation, kelvin
        )
        # No job handle: the bulb owns the ramp; `stop` pins it by arithmetic.
        return None

    async def async_stop(self, entity_id: str) -> None:
        record = self._transitions.pop(entity_id, None)
        if record is None:
            return  # nothing this backend set moving; silently a no-op
        address = self._address(entity_id)
        if address is None:
            return
        level = record.estimate(time.monotonic())
        self._set_color(
            address,
            record.hue,
            record.saturation,
            int(round(level)),
            record.kelvin,
            0,
        )
        if record.power_fade:
            # The ramp was a power fade; pinning the color alone would leave
            # the power interpolation running underneath it.
            self._send(
                address,
                LIFX_PKT_SET_LIGHT_POWER,
                _SET_POWER.pack(LIFX_POWER_ON, 0),
            )
        await self._resync(entity_id, record, level)

    @property
    def supports_step(self) -> bool:
        """No relative nudge on the wire; the controller simulates it instead.

        Simulation's step reads the level and writes an absolute one through
        ``light.turn_on``, which the LIFX integration delivers as the same
        single acknowledged packet a native step would have been — and keeps
        HA's state fresh for free, which our fire-and-forget writes would not.
        """
        return False

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # Unreachable through the controller (supports_step is False); a no-op
        # rather than a wrong guess if ever called directly.
        _LOGGER.debug(
            "step on %s has no LIFX equivalent; expected to be simulated", entity_id
        )

    @property
    def supports_fade(self) -> bool:
        return True

    async def async_fade(
        self,
        entity_id: str,
        target_brightness: int,
        duration: float,
        curve: str | float | None = None,
        color_temp_kelvin: int | None = None,
    ) -> CALLBACK_TYPE | None:
        """Hand the whole fade to the bulb as one interpolated SetColor.

        `curve` cannot be honored — the firmware interpolates linearly — and
        the fade is claimed anyway for the same reason the ZHA backend claims
        its: one datagram against a twenty-per-second stream.

        ``color_temp_kelvin`` is asserted by a zero-duration SetColor at the
        current level *before* the ramp goes out, so the fade starts at the
        right white instead of gliding to it. A fade on a light that is *off*
        becomes a power fade — color and target level set instantly while dark,
        then SetLightPower with the duration, which is LIFX's own idiom for
        fading in from black and the analog of MoveToLevelWithOnOff.
        """
        address = self._address(entity_id)
        if address is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        now = time.monotonic()
        target = float(to_bri16(target_brightness))
        if color_temp_kelvin is not None:
            hue, saturation, kelvin = 0, 0, _clamp_kelvin(color_temp_kelvin)
        else:
            hue, saturation, kelvin = self._color_of(entity_id)
        duration_ms = int(round(duration * 1000))

        if state.state == "off":
            self._set_color(address, hue, saturation, int(target), kelvin, 0)
            self._send(
                address,
                LIFX_PKT_SET_LIGHT_POWER,
                _SET_POWER.pack(LIFX_POWER_ON, min(_MAX_DURATION_MS, duration_ms)),
            )
            self._transitions[entity_id] = _Transition(
                0.0,
                target,
                now,
                duration,
                hue,
                saturation,
                kelvin,
                color_asserted=color_temp_kelvin is not None,
                power_fade=True,
            )
            return None

        start = self._current_bri16(entity_id, now)
        if color_temp_kelvin is not None:
            self._set_color(address, hue, saturation, int(round(start)), kelvin, 0)
        self._set_color(address, hue, saturation, int(target), kelvin, duration_ms)
        self._transitions[entity_id] = _Transition(
            start,
            target,
            now,
            duration,
            hue,
            saturation,
            kelvin,
            color_asserted=color_temp_kelvin is not None,
        )
        # No job handle: the bulb owns the ramp; `stop` pins it by arithmetic.
        return None
