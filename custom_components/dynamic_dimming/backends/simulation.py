"""Stepped simulation of continuous dimming via light.turn_on."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from ..const import (
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_RATE,
    DIRECTION_UP,
    RATE_PROFILES,
    TICK_INTERVAL,
)
from .base import DimmingBackend

_LOGGER = logging.getLogger(__name__)

_MAX = 255
_MIN = 0
# Floor for `move`/`step` down: bottom out at the lowest on-level, never off.
_MIN_ON = DEFAULT_MIN_BRIGHTNESS
_TICK_SECONDS = TICK_INTERVAL.total_seconds()


def resolve_rate(rate: str | float | None) -> float:
    """Map a profile name or number to brightness units per second.

    Numeric rates are clamped to a positive floor so a non-positive rate
    (e.g. ``0`` from the free-text service field) can't zero out the
    per-tick step and flood the mesh with an endless stream of identical
    ``light.turn_on`` calls at the 20 Hz tick rate.
    """
    if rate is None:
        return RATE_PROFILES[DEFAULT_RATE]
    if isinstance(rate, (int, float)):
        return max(1.0, float(rate))
    if rate not in RATE_PROFILES:
        _LOGGER.debug("unknown rate profile %r; falling back to %s", rate, DEFAULT_RATE)
    return RATE_PROFILES.get(rate, RATE_PROFILES[DEFAULT_RATE])


def _current_brightness(hass: HomeAssistant, entity_id: str) -> int | None:
    """Return current brightness, or None if the entity is unusable."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    if state.state == "off":
        return _MIN
    value = state.attributes.get("brightness")
    return int(value) if value is not None else _MIN


class SimulationBackend(DimmingBackend):
    """Steps brightness toward a rail at a fixed tick, size scaled by rate.

    Data model
    ----------
    ``_unsubs``: at most one active move job per ``entity_id``. Value is an
    idempotent cancel callable (clears the HA interval and removes itself from
    the map). The controller also holds that same callable in its own job map;
    either side may cancel.

    Flow (``async_move``)
    ---------------------
    1. Derive ``step`` / ``sign`` from rate and direction.
    2. Seed a float ``target`` from current brightness (accumulator for the
       commanded level; rounded only when writing ``light.turn_on``).
    3. Cancel any prior job for this entity, then register a time-interval
       ``_tick`` and return the wrapped unsub to the controller.
    4. Each tick: if the entity is gone, stop; else advance ``target``, write
       rounded brightness, stop when a rail (0 / 255) is hit.
    ``async_step`` is one-shot (no interval). ``async_stop`` is a no-op here
    because the controller already cancels via the shared unsub.

    Lifetimes / scope
    -----------------
    - ``self._unsubs``: backend-lifetime; entries live only while a move runs.
    - ``step``, ``sign``, ``target``, ``_tick``, ``_unsub``: closed over by the
      interval for one move; discarded when that job's unsub runs.
    - ``real_unsub``: HA's raw interval cancel; nulled after first call so the
      wrapper is safe if both controller and rail-stop invoke it.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # entity_id -> idempotent unsub for its active interval.
        self._unsubs: dict[str, CALLBACK_TYPE] = {}

    async def async_move(
        self, entity_id: str, direction: str, rate: str | float | None
    ) -> CALLBACK_TYPE | None:
        step = resolve_rate(rate) * _TICK_SECONDS
        sign = 1 if direction == DIRECTION_UP else -1
        # Accumulate a float target so fractional per-tick steps don't lose
        # precision to rounding on each write.
        start = _current_brightness(self.hass, entity_id)
        target = float(start if start is not None else _MIN)

        async def _tick(_now: datetime) -> None:
            nonlocal target
            if _current_brightness(self.hass, entity_id) is None:  # unavailable
                self._stop_job(entity_id)
                return
            target = max(_MIN_ON, min(_MAX, target + sign * step))
            await self.hass.services.async_call(
                "light", "turn_on",
                {"entity_id": entity_id, "brightness": int(round(target))},
                blocking=False,
            )
            if target in (_MIN_ON, _MAX):  # reached a rail (min-on / full)
                self._stop_job(entity_id)

        # Supersede any existing interval for this entity, then register the new
        # one wrapped in an idempotent unsub so controller-cancel and self-stop
        # can both call it safely.
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
        # Interval cancellation is driven by the controller (it holds the same
        # idempotent unsub). Nothing device-side to send for simulation.
        return None

    async def async_step(self, entity_id: str, direction: str, step_pct: float) -> None:
        current = _current_brightness(self.hass, entity_id)
        if current is None:
            return
        delta = (step_pct / 100.0) * _MAX
        sign = 1 if direction == DIRECTION_UP else -1
        target = max(_MIN_ON, min(_MAX, current + sign * delta))
        await self.hass.services.async_call(
            "light", "turn_on",
            {"entity_id": entity_id, "brightness": int(round(target))},
            blocking=False,
        )
