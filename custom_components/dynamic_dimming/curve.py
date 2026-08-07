"""Perceptual dimming curves.

A ramp that advances brightness by a fixed number of units per tick is *not* a
ramp that looks steady. Perceived lightness goes roughly as the cube root of
luminance, so a linear ramp races through the bottom of the range — where a few
units are a large visible change — and then crawls through the top, where they
are barely distinguishable. Held from off to full it reads as "flash, then
nothing much for two seconds".

The fix is to move at a constant rate through *perceived* lightness and let the
brightness step vary: tiny near the bottom, large near the top. That is what
`position` is here — a 0..1 coordinate in which equal distances look like equal
changes in brightness.

Everything is expressed over a configurable ``min_brightness``..255 span rather
than 0..255. On a device with coarse brightness resolution this matters more than
the curve itself: WiZ bulbs accept only 100 discrete levels and declare a minimum
usable level of 10 (`minDimLevel` in their `getModelConfig`), so a textbook curve
anchored at zero would spend its first third of travel below the level at which
the bulb does anything. Anchoring at the device's real floor spends the whole
hold on brightnesses that exist.
"""

from __future__ import annotations

import logging

from .const import (
    CONF_CURVE,
    CONF_MIN_BRIGHTNESS,
    DEFAULT_MIN_BRIGHTNESS,
)

_LOGGER = logging.getLogger(__name__)

MAX_BRIGHTNESS = 255

CURVE_LINEAR = "linear"
CURVE_PERCEPTUAL = "perceptual"

# Named curves map to a gamma exponent applied to the 0..1 position:
#   brightness_fraction = position ** gamma
# 1.0 is the old linear behaviour. 3.0 closely tracks CIE L*, the standard
# perceptual lightness scale, without its piecewise linear toe — the toe only
# matters below ~1% luminance, which is under the usable floor of every device
# this integration drives.
_NAMED_CURVES: dict[str, float] = {
    CURVE_LINEAR: 1.0,
    CURVE_PERCEPTUAL: 3.0,
}

# Guard rails for a free-text gamma. Below 1 the curve bends the wrong way
# (more travel at the top, which is the problem this module exists to fix);
# far above 3 the bottom becomes unreachably slow.
_MIN_GAMMA = 1.0
_MAX_GAMMA = 6.0


def resolve_curve(curve: str | float | None) -> float:
    """Map a curve name or gamma number to an exponent."""
    if curve is None:
        return _NAMED_CURVES[CURVE_PERCEPTUAL]
    if isinstance(curve, (int, float)) and not isinstance(curve, bool):
        return min(_MAX_GAMMA, max(_MIN_GAMMA, float(curve)))
    if curve in _NAMED_CURVES:
        return _NAMED_CURVES[curve]
    try:
        return min(_MAX_GAMMA, max(_MIN_GAMMA, float(curve)))
    except (TypeError, ValueError):
        _LOGGER.debug("unknown curve %r; falling back to %s", curve, CURVE_PERCEPTUAL)
        return _NAMED_CURVES[CURVE_PERCEPTUAL]


def resolve_min_brightness(value) -> float:
    """Clamp a configured floor into a range that leaves a ramp to travel."""
    try:
        return min(200.0, max(1.0, float(value)))
    except (TypeError, ValueError):
        return float(DEFAULT_MIN_BRIGHTNESS)


def curve_shape(entry, override: str | float | None = None) -> tuple[float, float]:
    """Resolve (gamma, min_brightness) from a config entry plus a per-call override.

    ``entry`` may be None, which is how the backends behave before an options
    flow has ever been saved and how they are exercised in isolation in tests.
    """
    options = getattr(entry, "options", None) or {}
    gamma = resolve_curve(override if override is not None else options.get(CONF_CURVE))
    min_brightness = resolve_min_brightness(
        options.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS)
    )
    return gamma, min_brightness


def to_position(
    brightness: float, gamma: float, min_brightness: float = 1.0
) -> float:
    """Where ``brightness`` sits on the 0..1 perceptual travel of this device.

    0 is ``min_brightness`` (or anything below it, including an off light, so a
    hold-to-raise from dark starts at the bottom of the ramp rather than partway
    up it) and 1 is full.
    """
    span = MAX_BRIGHTNESS - min_brightness
    if span <= 0:
        return 0.0
    fraction = (brightness - min_brightness) / span
    if fraction <= 0:
        return 0.0
    if fraction >= 1:
        return 1.0
    return fraction ** (1.0 / gamma)


def from_position(
    position: float, gamma: float, min_brightness: float = 1.0
) -> float:
    """Brightness at ``position`` on the 0..1 perceptual travel."""
    position = min(1.0, max(0.0, position))
    span = MAX_BRIGHTNESS - min_brightness
    return min_brightness + (position**gamma) * span


class Ramp:
    """One move's travel along the perceptual curve.

    Position, not brightness, is what advances by a fixed amount each tick; the
    brightness that comes out is whatever that position maps to. Position is
    kept as a float and never round-tripped through the emitted brightness, so
    the coarse steps a device like WiZ actually receives don't quantise the
    ramp's own progress.
    """

    def __init__(
        self,
        *,
        start_brightness: float,
        direction_sign: int,
        units_per_second: float,
        tick_seconds: float,
        gamma: float,
        min_brightness: float,
    ) -> None:
        self._gamma = gamma
        self._min_brightness = min_brightness
        self._sign = direction_sign
        # `units_per_second` stays on the familiar 0-255 scale so the existing
        # rate profiles keep meaning "this long end to end"; the curve changes
        # how that time is distributed, not how much of it there is.
        self._step = (units_per_second / MAX_BRIGHTNESS) * tick_seconds
        self.position = to_position(start_brightness, gamma, min_brightness)

    def advance(self) -> float:
        """Take one tick and return the brightness to command."""
        self.position = min(1.0, max(0.0, self.position + self._sign * self._step))
        return from_position(self.position, self._gamma, self._min_brightness)

    @property
    def at_rail(self) -> bool:
        """Whether the travel has bottomed out or topped out."""
        return self.position <= 0.0 or self.position >= 1.0


class Fade:
    """One fade's travel: start to target, in a fixed duration.

    Interpolates in *position*, not brightness, for the same reason ``Ramp``
    advances position: a fade that moved linearly in brightness would race
    through the bottom of the range and crawl through the top, which is exactly
    the complaint the perceptual curve exists to answer. A fade and a
    hold-to-dim over the same distance therefore look like the same motion.

    Unlike ``Ramp`` this has an end. ``done`` is true once the last tick has
    been handed out, so the caller can stop the interval and reconcile.
    """

    def __init__(
        self,
        *,
        start_brightness: float,
        target_brightness: float,
        duration: float,
        tick_seconds: float,
        gamma: float,
        min_brightness: float,
    ) -> None:
        self._gamma = gamma
        self._min_brightness = min_brightness
        self._start = to_position(start_brightness, gamma, min_brightness)
        self._end = to_position(target_brightness, gamma, min_brightness)
        self._ticks = max(1, int(round(duration / tick_seconds)))
        self._tick = 0
        self.position = self._start

    def advance(self) -> float:
        self._tick += 1
        fraction = min(1.0, self._tick / self._ticks)
        self.position = self._start + (self._end - self._start) * fraction
        return from_position(self.position, self._gamma, self._min_brightness)

    @property
    def done(self) -> bool:
        return self._tick >= self._ticks
