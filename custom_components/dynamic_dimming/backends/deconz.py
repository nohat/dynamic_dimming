"""Native deCONZ backend: bri_inc ramps through the deconz.configure service.

deCONZ inherited the Hue v1 API's continuous-dimming idiom whole: writing
``bri_inc`` with a ``transitiontime`` starts the gateway transitioning the
brightness by that increment over that time — the press half of a hold — and
writing ``bri_inc: 0`` stops the transition in place, which is the release.
Home Assistant's deCONZ light platform only ever writes absolute ``bri``
targets, so on a deCONZ Zigbee light a hold gesture otherwise means simulation
putting twenty REST calls a second through the gateway onto the mesh.

Like ZHA and Z-Wave JS, this backend drives another integration's *public
service* rather than a transport it owns: ``deconz.configure`` takes an entity
id, resolves it to the gateway's REST path for that resource, and PUTs the data
— so this backend is a thin translation from a gesture to that call, with no
library import and no reaching into the integration's runtime data. The
service's presence doubles as the liveness check, exactly as it does for ZHA.
The entity's own config entry names its gateway (``bridgeid``), which is passed
along so a house with two gateways drives the right one rather than whichever
is master.

deCONZ speaks target-and-duration rather than rate, so a move is "increment to
the rail, over the time that distance takes at the requested rate", computed
from the entity's current brightness — the same trade the Z-Wave JS and Hue
backends make. Dimming down aims one unit above zero, so the light bottoms out
at its lowest on-level and stays lit. deCONZ **group** entities are claimed
too: their state lives under ``/action`` instead of ``/state``, and the gateway
ramps the whole group as one Zigbee group cast — the thing the ZHA backend has
to decline for lack of a group service.

Nothing goes stale: the gateway streams every brightness change back over the
deCONZ integration's own websocket, so HA's state machine follows the ramp
without any resync from us.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    DECONZ_CONF_BRIDGE_ID,
    DECONZ_DOMAIN,
    DECONZ_FIELD_ACTION,
    DECONZ_FIELD_STATE,
    DECONZ_MAX_BRI,
    DECONZ_MAX_BRI_INC,
    DECONZ_MAX_TRANSITION_TENTHS,
    DECONZ_MIN_BRI,
    DECONZ_SERVICE_CONFIGURE,
    DIRECTION_UP,
)
from .base import DimmingBackend
from .simulation import current_brightness, resolve_rate

_LOGGER = logging.getLogger(__name__)

# Mireds are a uint16 and 0 would be an infinite color temperature.
_MIN_MIREDS = 1
_MAX_MIREDS = 65279
# How long a fade will wait for its color command to be acknowledged before
# starting the ramp anyway — the same bound the ZHA backend uses, for the same
# reason: "first" has to mean first at the gateway, but not at any price.
_COLOR_ACK_TIMEOUT = 2.0
# An individual light's unique_id leads with its Zigbee serial, which carries
# exactly seven colons; a group's unique_id is "<bridgeid>-/groups/<id>".
_SERIAL_COLONS = 7
_GROUP_MARKER = "-/groups/"


class _Target(NamedTuple):
    """One deCONZ REST resource: the entity, its sub-path and its gateway."""

    entity_id: str
    field: str
    bridge_id: str | None


def parse_field(unique_id: str) -> str | None:
    """Which REST sub-path a deCONZ entity's state lives under, or None.

    The two claimable shapes are an individual light (a colon-separated Zigbee
    serial, dimmed under ``/state``) and a group (its gateway's id plus the
    group's REST path, dimmed under ``/action``). Anything else — a scene, a
    v1-era oddity — is not a light this backend knows how to address.
    """
    if unique_id.count(":") == _SERIAL_COLONS:
        return DECONZ_FIELD_STATE
    if _GROUP_MARKER in unique_id:
        return DECONZ_FIELD_ACTION
    return None


def to_bri(brightness: float) -> int:
    """Map HA's 0-255 brightness onto a still-on 1-255 deCONZ bri."""
    return max(DECONZ_MIN_BRI, min(DECONZ_MAX_BRI, int(round(brightness))))


class DeconzBackend(DimmingBackend):
    """Writes bri_inc transitions to deCONZ resources through deconz.configure."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    # -- entity -> REST resource --------------------------------------------------

    def _target(self, entity_id: str) -> _Target | None:
        """Resolve an entity to a configure-service target, or None.

        None means "not ours", and the entity degrades to simulation. The
        service's absence is the "deCONZ is down or reloading" signal, since
        the integration registers it on setup and removes it on unload.
        """
        if not self.hass.services.has_service(
            DECONZ_DOMAIN, DECONZ_SERVICE_CONFIGURE
        ):
            return None
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != DECONZ_DOMAIN:
            return None
        field = parse_field(entity.unique_id)
        if field is None:
            return None
        bridge_id = None
        if entity.config_entry_id is not None:
            entry = self.hass.config_entries.async_get_entry(entity.config_entry_id)
            if entry is not None:
                bridge_id = entry.unique_id
        return _Target(entity_id, field, bridge_id)

    def claims(self, entity_id: str) -> bool:
        return self._target(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    async def _configure(
        self, target: _Target, data: dict, blocking: bool = False
    ) -> None:
        """PUT one state change through the configure service.

        Fire-and-forget by default, like the ZHA backend's cluster commands:
        the press half of a gesture should not wait on a REST round-trip.
        ``bridgeid`` pins the call to the entity's own gateway; without it the
        service falls back to the master gateway, which is only correct in a
        one-gateway house.
        """
        service_data: dict = {
            "entity": target.entity_id,
            "field": target.field,
            "data": data,
        }
        if target.bridge_id:
            service_data[DECONZ_CONF_BRIDGE_ID] = target.bridge_id
        await self.hass.services.async_call(
            DECONZ_DOMAIN,
            DECONZ_SERVICE_CONFIGURE,
            service_data,
            blocking=blocking,
        )

    def _transition_tenths(self, distance: float, rate: str | float | None) -> int:
        """The transitiontime that makes ``distance`` travel at ``rate``."""
        tenths = int(round(distance / resolve_rate(rate) * 10))
        return max(0, min(DECONZ_MAX_TRANSITION_TENTHS, tenths))

    # -- backend interface --------------------------------------------------------

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        """One bri_inc transition toward the rail.

        `curve` intentionally unused: the gateway runs this ramp, with whatever
        interpolation it applies, and this integration does not rewrite gateway
        config to change that.

        The increment is the distance to the rail as HA currently knows it,
        aimed at 254 going up and at bri 1 going down so the light bottoms out
        lit. A stale brightness skews the sweep time, not the destination; the
        floor of one unit keeps a light whose cached state already sits at the
        rail movable anyway, because the gateway — not the cache — knows where
        the level really is.
        """
        target = self._target(entity_id)
        if target is None:
            return None
        current = current_brightness(self.hass, entity_id)
        if direction == DIRECTION_UP:
            distance = (
                DECONZ_MAX_BRI_INC - current
                if current is not None
                else DECONZ_MAX_BRI_INC
            )
        else:
            distance = (
                current - DECONZ_MIN_BRI
                if current is not None
                else DECONZ_MAX_BRI_INC
            )
        distance = max(1, min(DECONZ_MAX_BRI_INC, int(round(distance))))
        sign = 1 if direction == DIRECTION_UP else -1
        await self._configure(
            target,
            {
                "bri_inc": sign * distance,
                "transitiontime": self._transition_tenths(distance, rate),
            },
        )
        # No job handle: the gateway owns the ramp until bri_inc 0 lands.
        return None

    async def async_stop(self, entity_id: str) -> None:
        target = self._target(entity_id)
        if target is None:
            return
        # bri_inc 0 is the API's "stop the transition where it is".
        await self._configure(target, {"bri_inc": 0})

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # `curve` intentionally unused, as for `move`. transitiontime 0 matches
        # what the Zigbee2MQTT and ZHA backends put on the wire for a step —
        # three gateways to the same silicon should not feel different.
        target = self._target(entity_id)
        if target is None:
            return
        sign = 1 if direction == DIRECTION_UP else -1
        increment = max(
            1,
            min(
                DECONZ_MAX_BRI_INC,
                int(round(step_pct / 100.0 * DECONZ_MAX_BRI_INC)),
            ),
        )
        await self._configure(
            target, {"bri_inc": sign * increment, "transitiontime": 0}
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
        """Hand the whole fade to the gateway as one bri transition.

        ``on`` rides along so a fade lights a dark fixture, matching the
        MoveToLevelWithOnOff semantics the Zigbee-family backends chose.

        ``color_temp_kelvin`` goes out first as its own zero-transition write,
        because the contract is that color is asserted from the first moment
        rather than faded — in a combined write the ``transitiontime`` would
        glide the white along with the brightness. It is sent blocking with a
        bounded wait, exactly as the ZHA backend does, so "first" means first
        at the gateway; a light with no color temperature to set costs the fade
        its white and nothing else.
        """
        target = self._target(entity_id)
        if target is None:
            return None
        if color_temp_kelvin is not None:
            try:
                async with asyncio.timeout(_COLOR_ACK_TIMEOUT):
                    await self._configure(
                        target,
                        {
                            "ct": max(
                                _MIN_MIREDS,
                                min(
                                    _MAX_MIREDS,
                                    int(round(1_000_000 / color_temp_kelvin)),
                                ),
                            ),
                            "transitiontime": 0,
                        },
                        blocking=True,
                    )
            except Exception as err:  # noqa: BLE001
                # Deliberately broad, as in the ZHA backend: the timeout above,
                # the service rejecting the field, the gateway refusing it —
                # none is worth losing the fade over, and cancellation is not
                # an Exception so it still propagates.
                _LOGGER.warning(
                    "color temperature for %s was not applied before its fade: %s",
                    entity_id,
                    err,
                )
        await self._configure(
            target,
            {
                "on": True,
                "bri": to_bri(target_brightness),
                "transitiontime": max(
                    0,
                    min(DECONZ_MAX_TRANSITION_TENTHS, int(round(duration * 10))),
                ),
            },
        )
        # No job handle: the gateway owns the ramp, and `stop` still reaches it
        # through bri_inc 0.
        return None
