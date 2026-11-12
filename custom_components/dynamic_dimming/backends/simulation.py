"""Stepped simulation of continuous dimming via light.turn_on."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.light import LightEntityFeature
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
from ..traffic import TrafficBudget, write_costs
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
    per-tick step and leave a ramp that never reaches a rail and so never
    ends.
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


class _Writer:
    """Paces one ramp's absolute writes to one entity.

    The ramp itself keeps advancing every tick, so its timing stays true; this
    decides which of those levels actually become a ``light.turn_on``. Three
    rules, each of which only ever removes writes:

    - **One in flight.** A write is awaited, and no new one starts until it
      returns. An integration that takes 300 ms to acknowledge a command gets
      a write every 300 ms, not a queue of stale levels growing behind it.
    - **Latest wins.** Levels that arrive while a write is in flight, or
      inside a budget gap, replace each other; only the newest goes out.
    - **Budgeted transports.** On a mesh or bridge the gap between writes is
      set by ``traffic.TrafficBudget``, shared with every other ramp on that
      transport. Where the light supports ``transition`` the gap is passed
      along, so fewer writes still read as one continuous ramp.

    A level identical to the last one sent is never repeated. The level a ramp
    ends on is always delivered, however many were skipped on the way there.
    """

    def __init__(self, hass: HomeAssistant, entity_id: str, budget: TrafficBudget) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self._budget = budget
        self._costs = write_costs(hass, entity_id)
        budget.acquire(self._costs)
        self._closed = False
        self._in_flight = False
        self._final = False
        self._pending: int | None = None
        self._extra: dict = {}
        self._last_sent: int | None = None
        self._ticks_since_send: int | None = None  # None: nothing sent yet
        self._reported_failure = False

    def offer(self, brightness: int, extra: dict | None = None, final: bool = False) -> None:
        """Hand over this tick's level. Call exactly once per tick."""
        if self._ticks_since_send is not None:
            self._ticks_since_send += 1
        if extra:
            # Sticky until a write carries it: a color that rode on a level
            # which was then superseded must not be lost with it.
            self._extra.update(extra)
        self._final = self._final or final
        if brightness == self._last_sent and not self._extra:
            self._pending = None
        else:
            self._pending = brightness
        self._maybe_send()

    def _gap_ticks(self) -> tuple[int, float]:
        interval = self._budget.interval(self._costs)
        return int(round(interval / _TICK_SECONDS)), interval

    def _maybe_send(self) -> None:
        if self._in_flight or self._pending is None:
            return
        gap_ticks, interval = self._gap_ticks()
        if (
            not self._final
            and self._ticks_since_send is not None
            and self._ticks_since_send < gap_ticks
        ):
            return
        data: dict = {"entity_id": self.entity_id, "brightness": self._pending}
        data.update(self._extra)
        if gap_ticks > 1 and self._supports_transition():
            data["transition"] = interval
        self._last_sent = self._pending
        self._pending = None
        self._extra = {}
        self._ticks_since_send = 0
        self._in_flight = True
        self.hass.async_create_task(
            self._send(data), f"dynamic_dimming write {self.entity_id}"
        )

    def _supports_transition(self) -> bool:
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return False
        features = state.attributes.get("supported_features") or 0
        return bool(features & LightEntityFeature.TRANSITION)

    async def _send(self, data: dict) -> None:
        try:
            await self.hass.services.async_call(
                "light", "turn_on", data, blocking=True
            )
        except Exception as err:  # noqa: BLE001
            # Deliberately broad: whatever the light's own integration raises
            # for a write it could not deliver is worth one log line, never a
            # dead ramp. Cancellation is not an Exception and still propagates.
            if self._reported_failure:
                _LOGGER.debug("write to %s failed: %s", self.entity_id, err)
            else:
                self._reported_failure = True
                _LOGGER.warning("write to %s failed: %s", self.entity_id, err)
        finally:
            self._in_flight = False
            # Ticks drive every write but the last: once the ramp has ended
            # there are none left, so the level it ended on goes out from here.
            if self._final:
                self._maybe_send()

    def close(self) -> None:
        """End of the ramp: give the budget back.

        A ramp that finished keeps its last level queued for delivery. One
        that was cut short (a stop, a superseding move) drops whatever had not
        gone out yet, because stopping means staying where the light is.
        """
        if self._closed:
            return
        self._closed = True
        self._budget.release(self._costs)
        if not self._final:
            self._pending = None


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
    4. Each tick: if the entity is gone, stop; else advance ``target``, hand
       the rounded brightness to the job's ``_Writer`` (which decides whether
       it goes out now, later, or is superseded), stop when a rail is hit.
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
        # Shared by every ramp this backend runs, so concurrent ramps on one
        # transport divide its write budget instead of each spending it whole.
        self._budget = TrafficBudget()

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
        color_temp_kelvin: int | None = None,
    ) -> CALLBACK_TYPE | None:
        """Fade by writing absolute brightness through the light entity.

        This is the fallback path for every backend that cannot fade itself,
        and it is a good one: because each tick carries an absolute value, the
        fade always lands exactly on the target however many ticks were dropped
        on the way.

        ``color_temp_kelvin`` goes out with the first write only: this is an
        acknowledged transport, so once asserted the color sticks, and
        repeating it on every write would just tax the device for nothing.

        Writes are paced by ``_Writer``: never more than one in flight, never
        a repeated level, and on a mesh or bridge no faster than the
        transport's budget. The target itself is always delivered.
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
        pending_temp = color_temp_kelvin

        async def _tick(_now: datetime) -> None:
            nonlocal pending_temp
            if current_brightness(self.hass, entity_id) is None:  # unavailable
                self._stop_job(entity_id)
                return
            target = fade.advance()
            extra = None
            if pending_temp is not None:
                extra = {"color_temp_kelvin": int(pending_temp)}
                pending_temp = None
            writer.offer(int(round(target)), extra, final=fade.done)
            if fade.done:
                self._stop_job(entity_id)

        self._stop_job(entity_id)
        writer = _Writer(self.hass, entity_id, self._budget)
        real_unsub = async_track_time_interval(self.hass, _tick, TICK_INTERVAL)

        def _unsub() -> None:
            nonlocal real_unsub
            if real_unsub is not None:
                real_unsub()
                real_unsub = None
            writer.close()
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
            writer.offer(int(round(target)), final=ramp.at_rail)
            if ramp.at_rail:  # bottomed out (min-on) or topped out (full)
                self._stop_job(entity_id)

        # Supersede any existing interval for this entity, then register the new
        # one wrapped in an idempotent unsub so controller-cancel and self-stop
        # can both call it safely.
        self._stop_job(entity_id)
        writer = _Writer(self.hass, entity_id, self._budget)
        real_unsub = async_track_time_interval(self.hass, _tick, TICK_INTERVAL)

        def _unsub() -> None:
            nonlocal real_unsub
            if real_unsub is not None:
                real_unsub()
                real_unsub = None
            writer.close()
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
