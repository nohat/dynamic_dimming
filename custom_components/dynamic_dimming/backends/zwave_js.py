"""Native Z-Wave JS backend: Multilevel Switch StartLevelChange/StopLevelChange.

Z-Wave's hold-to-dim primitive lives in the Multilevel Switch command class,
whose ``StartLevelChange`` and ``StopLevelChange`` pair is exactly the shape this
integration wants: one command starts the device ramping its own level, one
command stops it. Home Assistant's ``zwave_js`` light platform never sends
either — it only sets target values — so a hold gesture on a Z-Wave dimmer
otherwise costs twenty absolute writes a second onto a mesh whose raw throughput
is a fraction of Wi-Fi's, and which routes those writes through however many
mains-powered hops lie between the controller and the switch. This is the
transport where the difference is most visible.

Reaching the command class needs no library import and no runtime-data access:
``zwave_js.invoke_cc_api`` is a public service that takes a target and a method
name. Targeting it by ``entity_id`` is deliberate — the service resolves the
endpoint from the entity's own primary value, which is what makes a multi-channel
dimmer address its own channel instead of endpoint 0.

Nothing goes stale: the device reports its new value and the Z-Wave JS
integration's subscription feeds that back into the state machine.
"""

from __future__ import annotations

import logging

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    DIRECTION_UP,
    ZWAVE_COMMAND_CLASS_MULTILEVEL_SWITCH,
    ZWAVE_JS_DOMAIN,
    ZWAVE_JS_SERVICE_INVOKE_CC_API,
    ZWAVE_MAX_DURATION_SECONDS,
    ZWAVE_METHOD_START_LEVEL_CHANGE,
    ZWAVE_METHOD_STOP_LEVEL_CHANGE,
    ZWAVE_MIN_DURATION_SECONDS,
)
from .base import DimmingBackend
from .simulation import resolve_rate

_LOGGER = logging.getLogger(__name__)

_FULL_SCALE = 255.0


def has_value_id(unique_id: str) -> bool:
    """Whether a Z-Wave JS unique_id carries the value id the service needs.

    ``zwave_js`` builds an entity's unique_id as ``<home_id>.<value_id>``, and
    ``invoke_cc_api`` skips any entity it cannot pull a value id back out of —
    it needs one to find the endpoint. Checking the same thing here means such
    an entity degrades to simulation rather than being claimed and then silently
    dropped on the floor by the service.
    """
    parts = unique_id.split(".")
    return len(parts) > 1 and "-" in parts[1]


def to_duration(rate: str | float | None) -> str:
    """Express a rate as the Z-Wave duration of a full-scale sweep.

    Z-Wave does not carry a rate. It carries the time a complete 0-to-full
    change should take, so a rate in brightness units per second becomes the
    time to cross all of them. The encoding is whole seconds up to 127, which is
    coarse next to the tenths Zigbee and Matter allow — the named profiles land
    on 6, 3 and 2 seconds — but it is the only knob the command class offers,
    and it is still the device doing the ramping.
    """
    seconds = round(_FULL_SCALE / resolve_rate(rate))
    clamped = max(
        ZWAVE_MIN_DURATION_SECONDS, min(ZWAVE_MAX_DURATION_SECONDS, int(seconds))
    )
    return f"{clamped}s"


class ZwaveJsBackend(DimmingBackend):
    """Invokes Multilevel Switch CC methods through the zwave_js service."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def claims(self, entity_id: str) -> bool:
        """Whether this is a Z-Wave JS light the invoke service can address.

        The service's own presence stands in for "the Z-Wave stack is up":
        ``zwave_js`` registers it on setup and removes it on unload, so an entity
        belonging to an unloaded entry degrades to simulation rather than going
        dead.
        """
        if not self.hass.services.has_service(
            ZWAVE_JS_DOMAIN, ZWAVE_JS_SERVICE_INVOKE_CC_API
        ):
            return False
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != ZWAVE_JS_DOMAIN:
            return False
        return has_value_id(entity.unique_id)

    async def _invoke(self, entity_id: str, method: str, parameters: list) -> None:
        await self.hass.services.async_call(
            ZWAVE_JS_DOMAIN,
            ZWAVE_JS_SERVICE_INVOKE_CC_API,
            {
                "entity_id": entity_id,
                "command_class": ZWAVE_COMMAND_CLASS_MULTILEVEL_SWITCH,
                "method_name": method,
                "parameters": parameters,
            },
            blocking=False,
        )

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
        if not self.claims(entity_id):
            return None
        await self._invoke(
            entity_id,
            ZWAVE_METHOD_START_LEVEL_CHANGE,
            [
                {
                    "direction": "up" if direction == DIRECTION_UP else "down",
                    # Start from wherever the light is now rather than from a
                    # level we name. Naming one would make a hold jump before it
                    # ramps, and the value we could name is a cached one.
                    "ignoreStartLevel": True,
                    "duration": to_duration(rate),
                }
            ],
        )
        # No job handle: the device owns the ramp until StopLevelChange.
        return None

    async def async_stop(self, entity_id: str) -> None:
        if not self.claims(entity_id):
            return
        await self._invoke(entity_id, ZWAVE_METHOD_STOP_LEVEL_CHANGE, [])

    @property
    def supports_step(self) -> bool:
        """Multilevel Switch has no Step; the controller simulates it instead.

        The command class is Set, Get, StartLevelChange, StopLevelChange and
        nothing else, so there is no relative nudge to send. Simulation's step
        reads the current level and writes an absolute one, which is a single
        command — the same cost on the mesh as a native step would have been.
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
            "step on %s has no Multilevel Switch equivalent; expected to be "
            "simulated",
            entity_id,
        )
