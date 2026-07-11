"""Stepped simulation of continuous dimming via light.turn_on."""

from __future__ import annotations

from datetime import datetime

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from ..const import (
    DEFAULT_RATE,
    DIRECTION_UP,
    RATE_PROFILES,
    TICK_INTERVAL,
)
from .base import DimmingBackend

_MAX = 255
_MIN = 0
_TICK_SECONDS = TICK_INTERVAL.total_seconds()


def resolve_rate(rate: str | float | None) -> float:
    """Map a profile name or number to brightness units per second."""
    if rate is None:
        return RATE_PROFILES[DEFAULT_RATE]
    if isinstance(rate, (int, float)):
        return float(rate)
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
    """Steps brightness toward a rail at a fixed tick, size scaled by rate."""

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
            target = max(_MIN, min(_MAX, target + sign * step))
            await self.hass.services.async_call(
                "light", "turn_on",
                {"entity_id": entity_id, "brightness": int(round(target))},
                blocking=False,
            )
            if target in (_MIN, _MAX):  # reached the rail
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
        target = max(_MIN, min(_MAX, current + sign * delta))
        await self.hass.services.async_call(
            "light", "turn_on",
            {"entity_id": entity_id, "brightness": int(round(target))},
            blocking=False,
        )
