"""Native Hue backend: dimming_delta start/stop through the bridge's v2 API.

The Hue bridge's CLIP v2 API has the hold-to-dim primitive nearly verbatim:
``dimming_delta`` with an ``action`` of ``up`` or ``down`` starts a relative
brightness transition the *bridge* runs — pacing the Zigbee traffic to the bulb
itself — and ``action: stop`` halts it in place. Home Assistant's Hue
integration only ever writes absolute ``dimming`` targets, so a hold gesture on
a Hue bulb otherwise costs twenty API requests a second against a bridge that
documents itself as comfortable with about ten. One request per gesture is not
just smoother; it is the difference between working and tripping the bridge's
rate limiter.

Rather than opening a second connection to the bridge, commands go through the
aiohue client the loaded Hue config entry already holds — the bridge enforces
a small concurrent-connection budget, and aiohue's client already carries the
application key, the bridge's self-signed TLS pinning and the retry logic. Only
its generic ``request`` method is used, with payloads built here, so nothing
depends on aiohue's typed models. Nothing goes stale either: the bridge pushes
every level change back over the same client's event stream, so HA's state
machine follows the ramp with no resync from us.

The bridge has no notion of a rate, only of a transition duration, so the shape
of a move is "delta to the rail, over the time that distance takes at the
requested rate" — the same trade the Z-Wave JS backend makes, computed from the
entity's current brightness. Grouped lights answer the same verbs at their own
resource path, so a Hue room or zone entity is claimed too and ramps as one
bridge-coordinated command.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

from homeassistant.const import CONF_API_VERSION
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    DIRECTION_UP,
    HUE_ACTION_DOWN,
    HUE_ACTION_STOP,
    HUE_ACTION_UP,
    HUE_API_V2,
    HUE_DOMAIN,
    HUE_MAX_BRIGHTNESS_PCT,
    HUE_MAX_MIREK,
    HUE_MIN_MIREK,
    HUE_RESOURCE_GROUPED_LIGHT,
    HUE_RESOURCE_LIGHT,
)
from .base import DimmingBackend
from .simulation import current_brightness, resolve_rate

_LOGGER = logging.getLogger(__name__)

_MAX_BRIGHTNESS = 255
# Bounds a gesture against a bridge that has stopped answering; aiohue's own
# retry loop would otherwise happily wait out a multi-second overload backoff.
_REQUEST_TIMEOUT = 5.0


class _Target(NamedTuple):
    """One dimmable CLIP resource on one bridge's API client."""

    api: Any  # aiohue HueBridgeV2, held duck-typed
    resource_type: str
    resource_id: str


def to_brightness_pct(brightness: float) -> float:
    """Map HA's 0-255 brightness onto CLIP's 0-100 percent scale."""
    scaled = brightness / _MAX_BRIGHTNESS * HUE_MAX_BRIGHTNESS_PCT
    return round(max(0.0, min(HUE_MAX_BRIGHTNESS_PCT, scaled)), 2)


class HueBackend(DimmingBackend):
    """PUTs dimming_delta transitions to Hue v2 light resources."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Resources whose last command failed, so a bridge outage warns once
        # per resource instead of once per button press.
        self._reported_failures: set[str] = set()

    # -- entity -> CLIP resource --------------------------------------------------

    @staticmethod
    def _holds(controller: Any, resource_id: str) -> bool:
        """Whether an aiohue resource controller knows this id.

        Duck-typed and deliberately forgiving: the controllers are another
        library's objects, and any surprise in their shape must read as "not
        ours" rather than an exception out of ``claims``.
        """
        try:
            return controller is not None and controller.get(resource_id) is not None
        except Exception:  # noqa: BLE001
            return False

    def _target(self, entity_id: str) -> _Target | None:
        """Resolve an entity to a bridge client and CLIP resource, or None.

        None means "not ours", and the entity degrades to simulation. A v1
        bridge has no dimming_delta to speak; an entry that is loaded but not
        connected has no runtime client; and an id the client's cache does not
        hold is either a v1-era unique_id or a resource the bridge no longer
        knows — all the same honest answer.

        The unique_id doubles as the address: HA's hue integration writes each
        v2 entity's unique_id as the CLIP resource UUID itself. Whether that
        UUID names a ``light`` or a ``grouped_light`` decides the request path,
        and the client's own resource cache is what can tell the two apart.
        """
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != HUE_DOMAIN:
            return None
        if entity.config_entry_id is None:
            return None
        entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
        if entry is None or entry.domain != HUE_DOMAIN:
            return None
        if entry.data.get(CONF_API_VERSION) != HUE_API_V2:
            return None
        api = getattr(getattr(entry, "runtime_data", None), "api", None)
        if api is None:
            return None
        resource_id = entity.unique_id
        if self._holds(getattr(api, "lights", None), resource_id):
            return _Target(api, HUE_RESOURCE_LIGHT, resource_id)
        grouped = getattr(getattr(api, "groups", None), "grouped_light", None)
        if self._holds(grouped, resource_id):
            return _Target(api, HUE_RESOURCE_GROUPED_LIGHT, resource_id)
        return None

    def claims(self, entity_id: str) -> bool:
        return self._target(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    async def _put(self, target: _Target, payload: dict) -> bool:
        """PUT one state change to the resource. Returns whether it landed.

        The broad except is deliberate: aiohue's exception types cannot be
        imported here without adding a dependency, and a failed dimming command
        is worth a log line, never a traceback out of a button press.
        """
        path = f"clip/v2/resource/{target.resource_type}/{target.resource_id}"
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                await target.api.request("put", path, json=payload)
        except Exception as err:  # noqa: BLE001
            if target.resource_id in self._reported_failures:
                _LOGGER.debug("Hue command for %s failed: %s", path, err)
            else:
                self._reported_failures.add(target.resource_id)
                _LOGGER.warning("Hue command for %s failed: %s", path, err)
            return False
        self._reported_failures.discard(target.resource_id)
        return True

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        """One dimming_delta transition toward the rail.

        `curve` intentionally unused: the bridge runs this ramp, with whatever
        interpolation its engine applies, and this integration does not rewrite
        bridge config to change that.

        The delta is the distance to the rail as HA currently knows it, and the
        duration is that distance at the requested rate — the bridge transitions
        to a target over a duration, so rate has to be reconstructed from the
        two. A stale brightness skews the sweep time, not the destination; the
        floor of one percent keeps a light whose state claims it is already at
        the rail movable anyway, since the bridge, not the cache, is the
        authority on where the level really is. Dimming down clips at the
        light's own minimum dim level and stays lit, which is the same
        stays-on floor the Zigbee Move command gives the other backends.
        """
        target = self._target(entity_id)
        if target is None:
            return None
        current = current_brightness(self.hass, entity_id)
        current_pct = (
            to_brightness_pct(current) if current is not None else None
        )
        if direction == DIRECTION_UP:
            action = HUE_ACTION_UP
            distance = (
                HUE_MAX_BRIGHTNESS_PCT - current_pct
                if current_pct is not None
                else HUE_MAX_BRIGHTNESS_PCT
            )
        else:
            action = HUE_ACTION_DOWN
            distance = (
                current_pct if current_pct is not None else HUE_MAX_BRIGHTNESS_PCT
            )
        distance = max(1.0, min(HUE_MAX_BRIGHTNESS_PCT, distance))
        pct_per_second = resolve_rate(rate) / _MAX_BRIGHTNESS * HUE_MAX_BRIGHTNESS_PCT
        await self._put(
            target,
            {
                "dimming_delta": {
                    "action": action,
                    "brightness_delta": round(distance, 2),
                },
                "dynamics": {
                    "duration": int(round(distance / pct_per_second * 1000))
                },
            },
        )
        # No job handle: the bridge owns the ramp until a stop lands.
        return None

    async def async_stop(self, entity_id: str) -> None:
        target = self._target(entity_id)
        if target is None:
            return
        await self._put(target, {"dimming_delta": {"action": HUE_ACTION_STOP}})

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # `curve` intentionally unused, as for `move`. No dynamics either: the
        # bridge's own default smoothing is the platform's native feel for a
        # single nudge, and fighting it with a zero duration would make a Hue
        # step snap where every other Hue transition glides.
        target = self._target(entity_id)
        if target is None:
            return
        await self._put(
            target,
            {
                "dimming_delta": {
                    "action": (
                        HUE_ACTION_UP if direction == DIRECTION_UP else HUE_ACTION_DOWN
                    ),
                    "brightness_delta": round(
                        max(0.0, min(HUE_MAX_BRIGHTNESS_PCT, step_pct)), 2
                    ),
                }
            },
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
        """Hand the whole fade to the bridge as one dimming transition.

        ``on`` rides along so a fade lights a dark fixture, matching the
        MoveToLevelWithOnOff semantics the Zigbee-family backends chose.

        ``color_temp_kelvin`` goes out first as its own zero-duration write,
        because the contract is that color is asserted from the first moment
        rather than faded — folding it into the ramp request would make the
        white glide along with the brightness. The two PUTs are awaited in
        order on the same client, and a color the light cannot take (no color
        temperature channel, an out-of-range value the bridge refuses) costs a
        warning and the fade its white, never the ramp itself.
        """
        target = self._target(entity_id)
        if target is None:
            return None
        if color_temp_kelvin is not None:
            await self._put(
                target,
                {
                    "color_temperature": {
                        "mirek": max(
                            HUE_MIN_MIREK,
                            min(
                                HUE_MAX_MIREK,
                                int(round(1_000_000 / color_temp_kelvin)),
                            ),
                        )
                    },
                    "dynamics": {"duration": 0},
                },
            )
        await self._put(
            target,
            {
                "on": {"on": True},
                "dimming": {"brightness": to_brightness_pct(target_brightness)},
                "dynamics": {"duration": max(0, int(round(duration * 1000)))},
            },
        )
        # No job handle: the bridge owns the ramp, and `stop` still reaches it
        # through dimming_delta's stop action.
        return None
