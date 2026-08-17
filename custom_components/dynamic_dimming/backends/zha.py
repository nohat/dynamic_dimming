"""Native ZHA backend: Level Control Move/Stop via zha.issue_zigbee_cluster_command.

ZHA's light platform can set a brightness and fade to one, but like every other
light platform it has no notion of "start dimming and keep going until I let go".
The Level Control cluster's ``Move`` and ``Stop`` commands are what provide that,
and ZHA does not surface them on the light entity.

Unlike the Matter backend, no second connection is needed to reach them. ZHA
ships a public service, ``zha.issue_zigbee_cluster_command``, that addresses any
cluster on any node by IEEE and endpoint — so this backend is a thin translation
from an entity id to a service call, with no library import and no reaching into
the integration's runtime data. That the service exists at all is also the
liveness check: ZHA registers it on entry setup and removes it on unload, so
``claims`` degrading to simulation when it is absent is exactly the "the Zigbee
stack is down or reloading" fallback the Matter backend spells out by hand.

This is the same Level Control cluster the Zigbee2MQTT backend drives through
MQTT, so the two make the same choices for the same reasons: plain ``Move`` and
``Step`` rather than the ``WithOnOff`` variants, so dimming down bottoms out at
the device's minimum on-level and stays lit — and, per the Level Control spec's
Options handling, so that a Move on a light that is *off* does nothing. Turn it
on first. Rates are level units per second on the same 0-254 scale the shared
rate profiles use, so they pass straight through.

Nothing goes stale here: the device reports its own ``CurrentLevel`` and ZHA's
attribute subscription feeds that back into the state machine.
"""

from __future__ import annotations

import asyncio
import logging
from typing import NamedTuple

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import (
    DIRECTION_UP,
    ZHA_DOMAIN,
    ZHA_SERVICE_ISSUE_CLUSTER_COMMAND,
    ZIGBEE_COLOR_CONTROL_CLUSTER,
    ZIGBEE_COMMAND_MOVE,
    ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP,
    ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZIGBEE_COMMAND_STEP,
    ZIGBEE_COMMAND_STOP,
    ZIGBEE_LEVEL_CONTROL_CLUSTER,
    ZIGBEE_MAX_LEVEL,
    ZIGBEE_MAX_TRANSITION_TENTHS,
    ZIGBEE_MIN_LEVEL,
    ZIGBEE_MOVE_MODE_DOWN,
    ZIGBEE_MOVE_MODE_UP,
    ZIGBEE_OPTION_EXECUTE_IF_OFF,
)
from .base import DimmingBackend
from .simulation import resolve_rate

_LOGGER = logging.getLogger(__name__)

_MAX_BRIGHTNESS = 255
# Mireds are a uint16 and 0 would be an infinite color temperature.
_MIN_MIREDS = 1
_MAX_MIREDS = 65279
# How long a fade will wait for its color command to be acknowledged before
# starting the ramp anyway. Long enough to cover a healthy mesh round-trip,
# short enough that an unreachable device costs a wrong-looking white rather
# than a fade that visibly fails to start.
_COLOR_ACK_TIMEOUT = 2.0


class _Target(NamedTuple):
    """The IEEE address and endpoint that address one Zigbee light."""

    ieee: str
    endpoint_id: int


def parse_endpoint_id(unique_id: str, ieee: str) -> int | None:
    """Pull the endpoint id out of a ZHA entity's unique_id.

    ZHA builds a light's unique_id as ``<ieee>-<endpoint_id>``, sometimes with
    further dash-separated segments after it. An IEEE address is colon-separated
    and so contributes no dashes of its own, which makes the segment immediately
    after the address unambiguous.

    Taking the address from the device registry rather than from this string
    keeps the two halves independent: the registry entry is what ZHA itself
    treats as the device's identity, and only the endpoint has to be recovered
    from the entity.
    """
    if not unique_id.startswith(f"{ieee}-"):
        return None
    endpoint = unique_id.removeprefix(f"{ieee}-").split("-")[0]
    try:
        return int(endpoint)
    except ValueError:
        return None


def to_level(brightness: float) -> int:
    """Map HA's 0-255 brightness onto Zigbee's 1-254 level scale."""
    scaled = round(brightness / _MAX_BRIGHTNESS * ZIGBEE_MAX_LEVEL)
    return max(ZIGBEE_MIN_LEVEL, min(ZIGBEE_MAX_LEVEL, int(scaled)))


class ZhaBackend(DimmingBackend):
    """Issues Level Control commands to ZHA nodes through ZHA's own service."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    # -- entity -> Zigbee node ----------------------------------------------------

    def _target(self, entity_id: str) -> _Target | None:
        """Resolve an entity to an IEEE address and endpoint, or None.

        None means "not ours", and the entity degrades to simulation. Group
        lights land here too: their device carries no IEEE identifier, and
        driving a group needs ``issue_zigbee_group_command`` rather than this
        one, so they fall through to simulation instead of being mis-addressed.
        """
        if not self.hass.services.has_service(
            ZHA_DOMAIN, ZHA_SERVICE_ISSUE_CLUSTER_COMMAND
        ):
            return None
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.platform != ZHA_DOMAIN:
            return None
        if entity.device_id is None:
            return None
        device = dr.async_get(self.hass).async_get(entity.device_id)
        if device is None:
            return None
        ieee = next(
            (value for domain, value in device.identifiers if domain == ZHA_DOMAIN),
            None,
        )
        if ieee is None:
            return None
        endpoint_id = parse_endpoint_id(entity.unique_id, ieee)
        if endpoint_id is None:
            return None
        return _Target(ieee, endpoint_id)

    def claims(self, entity_id: str) -> bool:
        return self._target(entity_id) is not None

    # -- wire ---------------------------------------------------------------------

    async def _command(
        self,
        target: _Target,
        cluster_id: int,
        command: int,
        params: dict,
        blocking: bool = False,
    ) -> None:
        """Issue one cluster command.

        Fire-and-forget by default: a Zigbee round-trip is tens to hundreds of
        milliseconds and the press half of a hold-to-dim gesture must not pay
        it. ``blocking`` exists for the one case that needs two commands to
        arrive in a known order.

        No context is passed along. ``issue_zigbee_cluster_command`` is
        registered as an admin service, and a context carrying a non-admin
        user's id would be refused; a fresh context has no user attached and so
        runs as the integration itself, which is what a dimming command driven
        by an automation or a wall switch should do.
        """
        await self.hass.services.async_call(
            ZHA_DOMAIN,
            ZHA_SERVICE_ISSUE_CLUSTER_COMMAND,
            {
                "ieee": target.ieee,
                "endpoint_id": target.endpoint_id,
                "cluster_id": cluster_id,
                "cluster_type": "in",
                "command": command,
                "command_type": "server",
                "params": params,
            },
            blocking=blocking,
        )

    async def _level_command(
        self, target: _Target, command: int, params: dict
    ) -> None:
        await self._command(target, ZIGBEE_LEVEL_CONTROL_CLUSTER, command, params)

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
        # `rate` is a uint8 of level units per second on the same 0-254 scale the
        # shared rate profiles use, hence the pass-through and the clamp.
        units_per_second = max(
            ZIGBEE_MIN_LEVEL, min(ZIGBEE_MAX_LEVEL, int(round(resolve_rate(rate))))
        )
        await self._level_command(
            target,
            ZIGBEE_COMMAND_MOVE,
            {
                "move_mode": (
                    ZIGBEE_MOVE_MODE_UP
                    if direction == DIRECTION_UP
                    else ZIGBEE_MOVE_MODE_DOWN
                ),
                "rate": units_per_second,
                # options_mask/options_override are omitted so the spec defaults
                # apply: ExecuteIfOff stays clear, and a Move on an off light is
                # correctly ignored rather than silently winding up a level the
                # user cannot see.
            },
        )
        # No job handle: the device owns the ramp until Stop.
        return None

    async def async_stop(self, entity_id: str) -> None:
        target = self._target(entity_id)
        if target is None:
            return
        # Stop also terminates a MoveToLevel transition, so this is the right
        # command whether the movement came from `move` or from `fade`.
        await self._level_command(target, ZIGBEE_COMMAND_STOP, {})

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        # `curve` intentionally unused, as for `move`.
        target = self._target(entity_id)
        if target is None:
            return
        step_size = max(
            1, min(ZIGBEE_MAX_LEVEL, int(round(step_pct / 100.0 * ZIGBEE_MAX_LEVEL)))
        )
        await self._level_command(
            target,
            ZIGBEE_COMMAND_STEP,
            {
                "step_mode": (
                    ZIGBEE_MOVE_MODE_UP
                    if direction == DIRECTION_UP
                    else ZIGBEE_MOVE_MODE_DOWN
                ),
                "step_size": step_size,
                # transition_time is required by the command schema, unlike
                # Matter's, where it can be left null. Zero matches what the
                # Zigbee2MQTT backend already puts on the wire for a step, and
                # the two should not feel different on the same hardware.
                "transition_time": 0,
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
        """Hand the whole fade to the device as one MoveToLevelWithOnOff.

        The alternative — falling back to simulation — would put twenty absolute
        writes a second onto a Zigbee mesh for the length of the fade. One
        command with a transition time gets the same result for one round-trip,
        so this backend claims the fade even though `curve` cannot be honored:
        the device interpolates its own level linearly, and this integration
        does not rewrite device config to change that.

        ``color_temp_kelvin`` goes out first, with a zero transition, because
        the contract is that color is asserted from the first write rather than
        faded. It carries ExecuteIfOff so that a fade *up from off* arrives at
        the right white instead of flashing whatever the device last restored.
        It is also the one command here sent blocking: two fire-and-forget
        service calls are two tasks, and "first" has to mean first on the radio,
        not merely first to be scheduled. A fade is a scene transition rather
        than a hold gesture, so it can afford the round-trip that buys the
        ordering — but not an unbounded one, and not a failure. A device that
        does not answer, or has no Color Control cluster to answer with, costs
        the fade its color and nothing else; the ramp still goes out.
        """
        target = self._target(entity_id)
        if target is None:
            return None

        if color_temp_kelvin is not None:
            try:
                async with asyncio.timeout(_COLOR_ACK_TIMEOUT):
                    await self._command(
                        target,
                        ZIGBEE_COLOR_CONTROL_CLUSTER,
                        ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP,
                        {
                            "color_temp_mireds": max(
                                _MIN_MIREDS,
                                min(
                                    _MAX_MIREDS,
                                    int(round(1_000_000 / color_temp_kelvin)),
                                ),
                            ),
                            "transition_time": 0,
                            "options_mask": ZIGBEE_OPTION_EXECUTE_IF_OFF,
                            "options_override": ZIGBEE_OPTION_EXECUTE_IF_OFF,
                        },
                        blocking=True,
                    )
            except Exception as err:  # noqa: BLE001
                # Deliberately broad: this spans the timeout above, ZHA raising
                # for an absent cluster, and zigpy raising for a delivery
                # failure — no base class worth naming covers all three, and
                # none of them is worth losing the fade over. Cancellation is
                # not an Exception and so still propagates.
                _LOGGER.warning(
                    "color temperature for %s was not applied before its fade: %s",
                    entity_id,
                    err,
                )

        await self._level_command(
            target,
            ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
            {
                "level": to_level(target_brightness),
                # Zigbee measures a transition in tenths of a second.
                "transition_time": max(
                    0, min(ZIGBEE_MAX_TRANSITION_TENTHS, int(round(duration * 10)))
                ),
            },
        )
        # No job handle: the device owns the ramp, and `stop` reaches it.
        return None
