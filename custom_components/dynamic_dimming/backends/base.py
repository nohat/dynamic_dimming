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

    @property
    def supports_fade(self) -> bool:
        """Whether this backend can fade to an absolute target itself.

        Default ``False``. A backend that hands ramps to device firmware has no
        way to say "reach exactly this level in exactly this long", so the
        controller runs the fade through simulation instead — which writes
        absolute values and therefore always lands on the target.
        """
        return False

    async def async_fade(
        self,
        entity_id: str,
        target_brightness: int,
        duration: float,
        curve: str | float | None = None,
        color_temp_kelvin: int | None = None,
    ) -> CALLBACK_TYPE | None:
        """Fade to an absolute brightness over ``duration`` seconds.

        ``color_temp_kelvin``, when given, is asserted alongside the ramp from
        the very first write. It exists because a fade otherwise lights a bulb
        at whatever color it last had, and a parallel acknowledged
        ``light.turn_on`` carrying the color arrives whole round-trips later —
        long enough to read as a flash of stale white. The color itself does
        not fade; only brightness does.
        """
        raise NotImplementedError

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
