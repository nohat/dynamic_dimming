"""Routes dimming service calls to a backend and tracks active jobs."""

from __future__ import annotations

import logging

from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .backends.base import DimmingBackend
from .backends.simulation import SimulationBackend
from .capability import classify
from .const import DimmingClass

_LOGGER = logging.getLogger(__name__)


class DimmingController:
    """One controller per config entry; owns per-entity job handles."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._simulation = SimulationBackend(hass)
        self._jobs: dict[str, CALLBACK_TYPE] = {}

    def _backend_for(self, cls: DimmingClass) -> DimmingBackend | None:
        if cls is DimmingClass.SIMULATED:
            return self._simulation
        # NATIVE backends arrive in v0.1b; nothing claims entities yet.
        return None

    def _cancel_job(self, entity_id: str) -> None:
        unsub = self._jobs.pop(entity_id, None)
        if unsub is not None:
            unsub()

    async def async_move(self, entity_id: str, direction: str, rate) -> None:
        cls = classify(self.hass, entity_id)
        backend = self._backend_for(cls)
        if backend is None:
            _LOGGER.debug("move ignored: %s is %s", entity_id, cls.value)
            return
        self._cancel_job(entity_id)
        unsub = await backend.async_move(entity_id, direction, rate)
        if unsub is not None:
            self._jobs[entity_id] = unsub

    async def async_stop(self, entity_id: str) -> None:
        self._cancel_job(entity_id)
        backend = self._backend_for(classify(self.hass, entity_id))
        if backend is not None:
            await backend.async_stop(entity_id)

    async def async_step(self, entity_id: str, direction: str, step_pct: float) -> None:
        cls = classify(self.hass, entity_id)
        backend = self._backend_for(cls)
        if backend is None:
            _LOGGER.debug("step ignored: %s is %s", entity_id, cls.value)
            return
        await backend.async_step(entity_id, direction, step_pct)
