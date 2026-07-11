"""Backend interface for dynamic dimming."""

from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.core import CALLBACK_TYPE


class DimmingBackend(ABC):
    """Drives continuous dimming for a class of entities.

    Simulation and every future native backend implement this. ``async_move``
    may return an unsubscribe callable (simulation's interval) or ``None``
    (a fire-and-forget native command); the controller stores it as the entity's
    active job and calls it to supersede or stop.
    """

    @abstractmethod
    async def async_move(
        self, entity_id: str, direction: str, rate: str | float | None
    ) -> CALLBACK_TYPE | None:
        """Begin moving ``entity_id`` in ``direction`` at ``rate``."""

    @abstractmethod
    async def async_stop(self, entity_id: str) -> None:
        """Stop any native movement (no-op for simulation)."""

    @abstractmethod
    async def async_step(self, entity_id: str, direction: str, step_pct: float) -> None:
        """Apply a single relative brightness change."""
