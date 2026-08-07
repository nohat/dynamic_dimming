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

    def claims(self, entity_id: str) -> bool:
        """Whether this backend can natively drive ``entity_id``.

        Default ``False``: simulation is the fallback, never a claimer. A
        native backend that would match but cannot resolve what it needs
        (MQTT not loaded, topic unknown) must also return ``False`` so the
        entity degrades to simulation rather than going dead.
        """
        return False

    async def async_setup(self) -> None:
        """One-time initialization (e.g. MQTT subscriptions)."""

    async def async_unload(self) -> None:
        """Release anything acquired in async_setup."""

    @abstractmethod
    async def async_move(
        self,
        entity_id: str,
        direction: str,
        rate: str | float | None,
        curve: str | float | None = None,
    ) -> CALLBACK_TYPE | None:
        """Begin moving ``entity_id`` in ``direction`` at ``rate``.

        ``curve`` shapes how the travel is distributed across the range and is
        meaningful only to backends that step the ramp themselves. A backend that
        hands the move off to device firmware ignores it: the device's own curve
        applies, and this integration does not mutate device config.
        """

    @abstractmethod
    async def async_stop(self, entity_id: str) -> None:
        """Stop any native movement (no-op for simulation)."""

    @abstractmethod
    async def async_step(
        self,
        entity_id: str,
        direction: str,
        step_pct: float,
        curve: str | float | None = None,
    ) -> None:
        """Apply a single relative brightness change."""
