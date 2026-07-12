"""Routes dimming service calls to a backend and tracks active jobs."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .backends.base import DimmingBackend
from .backends.simulation import SimulationBackend
from .backends.tasmota import TasmotaBackend
from .backends.z2m import Z2MBackend
from .capability import classify
from .const import (
    BACKEND_NATIVE,
    BACKEND_SIMULATED,
    DOMAIN,
    DimmingClass,
)

_LOGGER = logging.getLogger(__name__)


class DimmingController:
    """One controller per config entry; owns per-entity job handles."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._simulation = SimulationBackend(hass)
        # Ordered: the first backend to claim an entity drives it natively.
        self.native_backends: list[DimmingBackend] = [
            Z2MBackend(hass, entry),
            TasmotaBackend(hass),
        ]
        self._jobs: dict[str, CALLBACK_TYPE] = {}

    async def async_setup(self) -> None:
        for backend in self.native_backends:
            await backend.async_setup()

    async def async_unload(self) -> None:
        # Drain active simulation jobs first: an unload mid-move must not
        # leave a 20 Hz interval firing after the config entry is gone.
        for entity_id in list(self._jobs):
            self._cancel_job(entity_id)
        for backend in self.native_backends:
            await backend.async_unload()

    def _claimer(self, entity_id: str) -> DimmingBackend | None:
        return next(
            (b for b in self.native_backends if b.claims(entity_id)), None
        )

    def _raise_no_native(self, entity_id: str) -> None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_native_backend",
            translation_placeholders={"entity_id": entity_id},
        )

    def _backend_for(self, entity_id: str, override: str) -> DimmingBackend | None:
        cls = classify(self.hass, entity_id, self.native_backends)
        if cls is DimmingClass.UNSUPPORTED:
            if override == BACKEND_NATIVE:
                self._raise_no_native(entity_id)
            return None
        if override == BACKEND_SIMULATED:
            return self._simulation
        native = self._claimer(entity_id)
        if override == BACKEND_NATIVE:
            if native is None:
                self._raise_no_native(entity_id)
            return native
        return native or self._simulation

    def _cancel_job(self, entity_id: str) -> None:
        unsub = self._jobs.pop(entity_id, None)
        if unsub is not None:
            unsub()

    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        backend: str = "auto",
    ) -> None:
        target = self._backend_for(entity_id, backend)
        if target is None:
            _LOGGER.debug("move ignored: %s is unsupported", entity_id)
            return
        # Superseding a native ramp here does not publish a native stop: the
        # next absolute or native command replaces the device's own ramp per
        # Zigbee Level-cluster/Tasmota semantics, so the overlap when
        # superseding to simulation is at most one tick.
        self._cancel_job(entity_id)
        unsub = await target.async_move(entity_id, direction, rate)
        if unsub is not None:
            self._jobs[entity_id] = unsub

    async def async_stop(self, entity_id: str) -> None:
        # Belt and braces: kill any simulation job AND tell a claiming native
        # backend to stop, regardless of how the move was started — a light
        # moved under override must never end up unstoppable.
        self._cancel_job(entity_id)
        native = self._claimer(entity_id)
        if native is not None:
            await native.async_stop(entity_id)

    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        backend: str = "auto",
    ) -> None:
        target = self._backend_for(entity_id, backend)
        if target is None:
            _LOGGER.debug("step ignored: %s is unsupported", entity_id)
            return
        await target.async_step(entity_id, direction, step_pct)
