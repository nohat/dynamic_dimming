"""Stepped simulation of continuous dimming via light.turn_on."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from ..const import (
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_RATE,
    DIRECTION_UP,
    RATE_PROFILES,
    TICK_INTERVAL,
)
from ..curve import Fade, Ramp, curve_shape, from_position, to_position
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


def current_brightness(hass: HomeAssistant, entity_id: str) -> int | None:
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

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        self.hass = hass
        self._entry = entry
        # entity_id -> idempotent unsub for its active interval.
        self._unsubs: dict[str, CALLBACK_TYPE] = {}

    def _shape(self, curve: str | float | None) -> tuple[float, float]:
        """The gamma and floor this move should travel between."""
        return curve_shape(self._entry, curve)

    @property
    def supports_fade(self) -> bool:
        return True

    async def async_fade(
        self,
        entity_id: str,
        target_brightness: int,
        duration: float,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        """Fade by writing absolute brightness through the light entity.

        This is the fallback path for every backend that cannot fade itself,
        and it is a good one: because each tick carries an absolute value, the
        fade always lands exactly on the target however many ticks were dropped
        on the way.
        """
        gamma, min_brightness = self._shape(curve)
        start = current_brightness(self.hass, entity_id)
        fade = Fade(
            start_brightness=float(start if start is not None else min_brightness),
            target_brightness=float(target_brightness),
            duration=duration,
            tick_seconds=_TICK_SECONDS,
            gamma=gamma,
            min_brightness=min_brightness,
        )

        async def _tick(_now: datetime) -> None:
            target = fade.advance()
            await self.hass.services.async_call(
                "light", "turn_on",
                {"entity_id": entity_id, "brightness": int(round(target))},
                blocking=False,
            )
            if fade.done:
                self._cancel(entity_id)

        self._cancel(entity_id)
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

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        gamma, min_brightness = self._shape(curve)
        start = current_brightness(self.hass, entity_id)
        ramp = Ramp(
            start_brightness=float(start if start is not None else _MIN),
            direction_sign=1 if direction == DIRECTION_UP else -1,
            units_per_second=resolve_rate(rate),
            tick_seconds=_TICK_SECONDS,
            gamma=gamma,
            min_brightness=min_brightness,
        )

        async def _tick(_now: datetime) -> None:
            if current_brightness(self.hass, entity_id) is None:  # unavailable
                self._stop_job(entity_id)
                return
            target = ramp.advance()
            await self.hass.services.async_call(
                "light", "turn_on",
                {"entity_id": entity_id, "brightness": int(round(target))},
                blocking=False,
            )
            if ramp.at_rail:  # bottomed out (min-on) or topped out (full)
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

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        current = current_brightness(self.hass, entity_id)
        if current is None:
            return
        gamma, min_brightness = self._shape(curve)
        # A step is a percentage of *perceived* travel, so one tap moves the
        # same apparent amount at the bottom of the range as at the top.
        sign = 1 if direction == DIRECTION_UP else -1
        position = to_position(current, gamma, min_brightness)
        position += sign * (step_pct / 100.0)
        target = from_position(position, gamma, min_brightness)
        await self.hass.services.async_call(
            "light", "turn_on",
            {"entity_id": entity_id, "brightness": int(round(target))},
            blocking=False,
        )
