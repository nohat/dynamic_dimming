"""Native WiZ backend: fire-and-forget UDP setPilot ramps on the local network.

Unlike Zigbee2MQTT and Tasmota, WiZ firmware has no ramp primitive — there is no
"start moving and keep going" command to hand off to, and the HA integration does
not advertise ``LightEntityFeature.TRANSITION``, so a WiZ bulb cannot fade itself
either. The movement therefore still has to be stepped by us.

What makes this a *native* backend is the transport, not the ramp. Measured against
SHRGB firmware 1.37/1.38 on a quiet LAN, an acknowledged ``setPilot`` round-trip is
38-476 ms (median ~160 ms) — so driving a 20 Hz ramp through ``light.turn_on``, which
waits on that acknowledgement, cannot work. The same datagram sent fire-and-forget
costs ~0.3 ms. Dropping the acknowledgement is what buys a smooth ramp, and it is
safe here because every tick sends an *absolute* brightness: a lost datagram is
corrected 50 ms later rather than accumulating error.

The cost of bypassing ``light.turn_on`` is that Home Assistant's state machine goes
stale for the duration of the movement, so ``async_stop`` and ``async_step`` re-assert
the final level through the light entity to put HA and the bulb back in agreement.
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from ..const import (
    DIRECTION_UP,
    TICK_INTERVAL,
    WIZ_DOMAIN,
    WIZ_MAX_DIMMING,
    WIZ_MIN_DIMMING,
    WIZ_PORT,
)
from ..curve import Ramp, curve_shape, from_position, to_position
from .base import DimmingBackend
from .simulation import current_brightness, resolve_rate

_LOGGER = logging.getLogger(__name__)

_MAX_BRIGHTNESS = 255
_TICK_SECONDS = TICK_INTERVAL.total_seconds()
# Groups nest shallowly in practice; this only stops a self-referential group
# from recursing forever.
_MAX_GROUP_DEPTH = 4
# Replies to our fire-and-forget writes are unwanted but still arrive. Drain a
# bounded number per tick so the receive buffer doesn't sit full for the life of
# the socket; the cap keeps a chatty LAN from turning the drain into the
# expensive part of the tick.
_MAX_DRAIN_PER_TICK = 32


def to_dimming(brightness: float) -> int:
    """Map HA's 0-255 brightness onto WiZ's 1-100 ``dimming`` scale."""
    scaled = round(brightness / _MAX_BRIGHTNESS * WIZ_MAX_DIMMING)
    return max(WIZ_MIN_DIMMING, min(WIZ_MAX_DIMMING, int(scaled)))


class WizBackend(DimmingBackend):
    """Ramps WiZ bulbs by streaming absolute setPilot datagrams at the tick rate.

    Claims a plain WiZ light, and also a light group whose members are *all* WiZ
    bulbs — fanning one ramp out to every member from a single tick keeps a
    multi-bulb ceiling visibly in step, which per-entity service calls do not.
    A group with even one non-WiZ member is left to simulation, which can still
    drive it through ``light.turn_on``.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        self.hass = hass
        self._entry = entry
        self._sock: socket.socket | None = None
        self._unsubs: dict[str, CALLBACK_TYPE] = {}
        # Last brightness this backend commanded, per entity — the value
        # re-asserted through the light entity when the movement stops.
        self._last: dict[str, float] = {}

    def _ensure_sock(self) -> socket.socket | None:
        """Open the send socket on first use.

        Lazy so a house with no WiZ bulbs never opens one, and so a socket
        failure degrades this backend instead of failing the whole config entry
        during setup.
        """
        if self._sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
            except OSError as err:
                _LOGGER.warning("WiZ backend could not open a UDP socket: %s", err)
                return None
            self._sock = sock
        return self._sock

    async def async_unload(self) -> None:
        for entity_id in list(self._unsubs):
            self._stop_job(entity_id)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- entity -> bulb addresses -------------------------------------------------

    def _host(self, entity_id: str) -> str | None:
        """Return the bulb's IP from its WiZ config entry, or None."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != WIZ_DOMAIN or entry.config_entry_id is None:
            return None
        config_entry = self.hass.config_entries.async_get_entry(entry.config_entry_id)
        if config_entry is None or config_entry.domain != WIZ_DOMAIN:
            return None
        return config_entry.data.get(CONF_HOST)

    def _hosts(self, entity_id: str, depth: int = 0) -> list[str]:
        """Resolve an entity to the bulb addresses it drives.

        Empty means "not ours" — including the all-or-nothing group rule, so a
        mixed group degrades to simulation rather than being half-driven here.
        """
        host = self._host(entity_id)
        if host:
            return [host]
        if depth >= _MAX_GROUP_DEPTH:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        members = state.attributes.get("entity_id") or ()
        if isinstance(members, str):
            members = (members,)
        hosts: list[str] = []
        for member in members:
            member_hosts = self._hosts(member, depth + 1)
            if not member_hosts:
                return []
            hosts.extend(member_hosts)
        return hosts

    def claims(self, entity_id: str) -> bool:
        return bool(self._hosts(entity_id))

    # -- wire ---------------------------------------------------------------------

    def _drain(self, sock: socket.socket) -> None:
        """Discard queued replies so the receive buffer doesn't stay full."""
        for _ in range(_MAX_DRAIN_PER_TICK):
            try:
                sock.recvfrom(512)
            except OSError:
                return

    def _send(self, hosts: list[str], params: dict) -> None:
        sock = self._ensure_sock()
        if sock is None:
            return
        self._drain(sock)
        payload = json.dumps({"method": "setPilot", "params": params}).encode()
        for host in hosts:
            try:
                sock.sendto(payload, (host, WIZ_PORT))
            except OSError as err:
                # A ramp is 20 datagrams a second per bulb; the next tick carries
                # an absolute level, so a transient send failure self-corrects.
                _LOGGER.debug("WiZ send to %s failed: %s", host, err)

    async def _resync(self, entity_id: str) -> None:
        """Re-assert the last commanded level through the light entity.

        Puts HA's state machine back in agreement with the bulbs after a stretch
        of writes it never saw.
        """
        brightness = self._last.pop(entity_id, None)
        if brightness is None:
            return
        await self.hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": entity_id, "brightness": int(round(brightness))},
            blocking=False,
        )

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        hosts = self._hosts(entity_id)
        if not hosts:
            return None
        start = current_brightness(self.hass, entity_id)
        if start is None:  # unavailable
            return None

        gamma, min_brightness = curve_shape(self._entry, curve)
        ramp = Ramp(
            start_brightness=float(start),
            direction_sign=1 if direction == DIRECTION_UP else -1,
            units_per_second=resolve_rate(rate),
            tick_seconds=_TICK_SECONDS,
            gamma=gamma,
            min_brightness=min_brightness,
        )

        async def _tick(_now: datetime) -> None:
            if current_brightness(self.hass, entity_id) is None:
                self._stop_job(entity_id)
                return
            target = ramp.advance()
            self._last[entity_id] = target
            # `state` rides along so dimming up off a dark bulb lights it; dimming
            # down floors at the configured minimum and stays lit, matching the
            # Zigbee Move (not Move-with-On/Off) semantics the other backends use.
            self._send(hosts, {"state": True, "dimming": to_dimming(target)})
            if ramp.at_rail:
                self._stop_job(entity_id)
                await self._resync(entity_id)

        self._stop_job(entity_id)
        real_unsub = async_track_time_interval(self.hass, _tick, TICK_INTERVAL)

        def _unsub() -> None:
            nonlocal real_unsub
            if real_unsub is not None:
                real_unsub()
                real_unsub = None
            if self._unsubs.get(entity_id) is _unsub:
                self._unsubs.pop(entity_id, None)

        self._unsubs[entity_id] = _unsub
        return _unsub

    def _stop_job(self, entity_id: str) -> None:
        unsub = self._unsubs.get(entity_id)
        if unsub is not None:
            unsub()

    async def async_stop(self, entity_id: str) -> None:
        self._stop_job(entity_id)
        await self._resync(entity_id)

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        hosts = self._hosts(entity_id)
        if not hosts:
            return
        current = current_brightness(self.hass, entity_id)
        if current is None:
            return
        gamma, min_brightness = curve_shape(self._entry, curve)
        # A step is a percentage of *perceived* travel, so one tap moves the same
        # apparent amount at the bottom of the range as at the top.
        sign = 1 if direction == DIRECTION_UP else -1
        position = to_position(current, gamma, min_brightness)
        position += sign * (step_pct / 100.0)
        target = from_position(position, gamma, min_brightness)
        # UDP first so the change is visible immediately, then through the light
        # entity so HA's state matches what the bulb is actually doing.
        self._send(hosts, {"state": True, "dimming": to_dimming(target)})
        self._last[entity_id] = target
        await self._resync(entity_id)
