"""Native Shelly backend: DimUp / DimDown / DimStop over the device's own RPC.

Shelly's Gen2+ firmware carries exactly the primitive this integration exists
to reach: every light-bearing RPC component (``Light``, ``CCT``, ``RGB``,
``RGBW``, ``RGBCCT``) answers ``DimUp``, ``DimDown`` and ``DimStop`` — one call
starts a ramp the device runs itself, one call halts it, holding the level.
Home Assistant's Shelly integration drives lights through ``Set`` alone, so the
primitive sits unused on hardware that was built around it: the (Pro) Dimmer
family runs its own wall buttons through these very calls.

Like the Matter backend, this one owns its transport rather than reaching into
another integration's runtime data: each command is one HTTP POST to the
device's ``/rpc`` endpoint, addressed from the host the Shelly config entry
already stores. Password-protected devices are driven with digest auth from the
same entry. Nothing goes stale — the device notifies the Shelly integration's
own websocket of every brightness change, so HA's state machine follows the
ramp without any resync from us.

The one wrinkle is rate. ``fade_rate`` is not units-per-second but a discrete
speed class, 1 (slowest) to 5 (fastest), so the shared rate profiles are mapped
onto it — slow, medium and fast land on 1, 3 and 5 — and the exact sweep time
is the firmware's own. Omitted entirely on this path: a relative Step, which
the RPC does not offer (steps fall back to simulation's single absolute write),
and Gen1 devices, whose HTTP API only carries ``dim`` on the Dimmer models and
cannot be told apart from a bulb by registry data alone — they degrade to
simulation rather than being mis-addressed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    DIRECTION_UP,
    SHELLY_CONF_GEN,
    SHELLY_CONF_SLEEP_PERIOD,
    SHELLY_DEFAULT_HTTP_PORT,
    SHELLY_DIM_COMPONENTS,
    SHELLY_DOMAIN,
    SHELLY_FADE_RATE_MAX,
    SHELLY_FADE_RATE_MIN,
    SHELLY_MAX_BRIGHTNESS_PCT,
    SHELLY_MAX_TRANSITION_SECONDS,
    SHELLY_MIN_TRANSITION_SECONDS,
)
from .base import DimmingBackend
from .simulation import resolve_rate

_LOGGER = logging.getLogger(__name__)

_MAX_BRIGHTNESS = 255
# A LAN round-trip is tens of milliseconds; this only bounds how long a gesture
# can hang on a device that has dropped off the network.
_REQUEST_TIMEOUT = 3.0
# The shared rate profiles (units/second) pinned to Shelly's ends and middle;
# rates between anchors interpolate, so an explicit numeric rate still lands on
# a sensibly ordered speed class.
_FADE_RATE_ANCHORS = ((40.0, 1.0), (90.0, 3.0), (160.0, 5.0))


class _Target(NamedTuple):
    """Everything needed to address one light component on one device."""

    base_url: str
    component: str
    light_id: int
    username: str | None
    password: str | None


def parse_component(unique_id: str) -> tuple[str, int] | None:
    """Pull ``(component, id)`` out of a Shelly RPC light's unique_id.

    The Shelly integration writes an RPC light's unique_id as
    ``<MAC>-<key>`` where the key is ``light:0``, ``cct:0``, ``rgb:0`` and so
    on. The MAC is plain hex and contributes no dash, which makes the key the
    remainder after the first one. Anything that does not parse — a Gen1
    ``<MAC>-light_0``, a sensor suffix, a relay's ``switch:0`` — is not a
    dimmable RPC light and returns None.
    """
    parts = unique_id.split("-", 1)
    if len(parts) != 2:
        return None
    key = parts[1].split(":", 1)
    if len(key) != 2:
        return None
    prefix, component_id = key
    component = SHELLY_DIM_COMPONENTS.get(prefix)
    if component is None:
        return None
    try:
        return component, int(component_id)
    except ValueError:
        return None


def to_fade_rate(rate: str | float | None) -> int:
    """Map a rate onto Shelly's discrete 1-5 speed class.

    The RPC carries no units-per-second — ``fade_rate`` is an ordered speed
    class where 5 is fastest. Piecewise-linear interpolation through the
    anchors keeps the three named profiles maximally distinguishable (slow,
    medium, fast → 1, 3, 5) and keeps any explicit numeric rate monotonic
    between them. The exact sweep time each class produces is the firmware's.
    """
    units = resolve_rate(rate)
    result = _FADE_RATE_ANCHORS[-1][1]
    for (low_units, low_class), (high_units, high_class) in zip(
        _FADE_RATE_ANCHORS, _FADE_RATE_ANCHORS[1:]
    ):
        if units <= low_units:
            result = low_class
            break
        if units <= high_units:
            fraction = (units - low_units) / (high_units - low_units)
            result = low_class + fraction * (high_class - low_class)
            break
    return max(SHELLY_FADE_RATE_MIN, min(SHELLY_FADE_RATE_MAX, round(result)))


def to_brightness_pct(brightness: float) -> int:
    """Map HA's 0-255 brightness onto Shelly's 0-100 percent scale."""
    scaled = round(brightness / _MAX_BRIGHTNESS * SHELLY_MAX_BRIGHTNESS_PCT)
    return max(0, min(SHELLY_MAX_BRIGHTNESS_PCT, int(scaled)))


class ShellyBackend(DimmingBackend):
    """POSTs DimUp/DimDown/DimStop to Gen2+ Shelly devices' RPC endpoints."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Hosts whose last command failed, so a device that is down warns once
        # per outage instead of once per button press.
        self._reported_failures: set[str] = set()

    # -- entity -> device ---------------------------------------------------------

    def _target(self, entity_id: str) -> _Target | None:
        """Resolve an entity to an RPC address, or None.

        None means "not ours", and the entity degrades to simulation. That
        covers Gen1 devices (no RPC), sleepy battery devices (an HTTP call
        would just time out against a sleeping radio), relays exposed as
        lights, and anything whose config entry no longer carries a host.
        """
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != SHELLY_DOMAIN:
            return None
        if entity.config_entry_id is None:
            return None
        entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        if entry is None or entry.domain != SHELLY_DOMAIN:
            return None
        try:
            if int(entry.data.get(SHELLY_CONF_GEN) or 0) < 2:
                return None
        except (TypeError, ValueError):
            return None
        if entry.data.get(SHELLY_CONF_SLEEP_PERIOD):
            return None
        host = entry.data.get(CONF_HOST)
        if not host:
            return None
        parsed = parse_component(entity.unique_id)
        if parsed is None:
            return None
        component, light_id = parsed
        port = entry.data.get(CONF_PORT) or SHELLY_DEFAULT_HTTP_PORT
        return _Target(
            f"http://{host}:{port}",
            component,
            light_id,
            entry.data.get(CONF_USERNAME),
            entry.data.get(CONF_PASSWORD),
        )

    def claims(self, entity_id: str) -> bool:
        return self._target(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    async def _call(self, target: _Target, method: str, params: dict) -> None:
        """POST one RPC call to the device.

        Awaited, because HTTP has no fire-and-forget: the ramp starts when the
        request lands regardless, and reading the response is the only way an
        error can surface. The timeout bounds what an unplugged device can cost
        a gesture.

        Devices with restricted login answer 401 with a digest challenge;
        aiohttp's digest middleware replays the request with credentials from
        the config entry — the same ones the Shelly integration connects with.
        """
        session = async_get_clientsession(self.hass)
        url = f"{target.base_url}/rpc/{target.component}.{method}"
        kwargs: dict = {"json": {"id": target.light_id, **params}}
        if target.password:
            kwargs["middlewares"] = (
                aiohttp.DigestAuthMiddleware(
                    target.username or "admin", target.password
                ),
            )
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                async with session.post(url, **kwargs) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        self._log_failure(
                            target, f"HTTP {resp.status} for {method}: {body}"
                        )
                        return
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            self._log_failure(target, f"{method} failed: {err}")
            return
        self._reported_failures.discard(target.base_url)

    def _log_failure(self, target: _Target, detail: str) -> None:
        """Warn on the first failure of an outage, then stay quiet about it."""
        if target.base_url in self._reported_failures:
            _LOGGER.debug("Shelly device %s: %s", target.base_url, detail)
            return
        self._reported_failures.add(target.base_url)
        _LOGGER.warning("Shelly device %s: %s", target.base_url, detail)

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        # `curve` intentionally unused: the device runs this ramp, with whatever
        # curve its firmware applies, and this integration does not rewrite
        # device config to change that.
        target = self._target(entity_id)
        if target is None:
            return None
        await self._call(
            target,
            "DimUp" if direction == DIRECTION_UP else "DimDown",
            {"fade_rate": to_fade_rate(rate)},
        )
        # No job handle: the device owns the ramp until DimStop.
        return None

    async def async_stop(self, entity_id: str) -> None:
        target = self._target(entity_id)
        if target is None:
            return
        await self._call(target, "DimStop", {})

    @property
    def supports_step(self) -> bool:
        """The RPC has no relative step; the controller simulates it instead.

        The components' verb set is Set, Toggle, DimUp, DimDown, DimStop —
        nothing nudges a level once. Simulation's step reads the current level
        and writes an absolute one through ``light.turn_on``, which reaches the
        device as the same single ``Set`` a native step would have been.
        """
        return False

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # Unreachable through the controller, which routes steps to simulation
        # because `supports_step` is False. Present because the interface is
        # abstract, and a no-op rather than a wrong guess if it is ever called
        # directly.
        _LOGGER.debug(
            "step on %s has no Shelly RPC equivalent; expected to be simulated",
            entity_id,
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
        """Hand the whole fade to the device as one Set with a transition.

        ``on`` rides along so a fade lights a dark fixture, matching the
        MoveToLevelWithOnOff semantics the Zigbee-family backends chose.
        Firmware clamps transitions below half a second anyway, so the clamp
        here just makes the wire honest about it.

        ``color_temp_kelvin`` needs no separate first write on this transport:
        ``ct`` travels in the same call, on the components that have a white
        channel to point it at. On an ``RGBCCT`` light that also means naming
        the mode, exactly as the Shelly integration's own turn_on does; on
        ``Light``/``RGB``/``RGBW`` there is no color temperature to assert and
        the request is sent without it.
        """
        target = self._target(entity_id)
        if target is None:
            return None
        params: dict = {
            "on": True,
            "brightness": to_brightness_pct(target_brightness),
            "transition_duration": max(
                SHELLY_MIN_TRANSITION_SECONDS,
                min(SHELLY_MAX_TRANSITION_SECONDS, duration),
            ),
        }
        if color_temp_kelvin is not None and target.component in ("CCT", "RGBCCT"):
            params["ct"] = int(color_temp_kelvin)
            if target.component == "RGBCCT":
                params["mode"] = "cct"
        await self._call(target, "Set", params)
        # No job handle: the device owns the ramp, and `stop` still reaches it
        # through DimStop.
        return None
