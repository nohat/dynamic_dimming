"""Tests for the perceptual dimming curve."""

from __future__ import annotations

import pytest

from custom_components.dynamic_dimming.curve import (
    CURVE_LINEAR,
    CURVE_PERCEPTUAL,
    MAX_BRIGHTNESS,
    Ramp,
    curve_shape,
    from_position,
    resolve_curve,
    resolve_min_brightness,
    to_position,
)

PERCEPTUAL = resolve_curve(CURVE_PERCEPTUAL)
LINEAR = resolve_curve(CURVE_LINEAR)


def test_named_curves():
    assert LINEAR == 1.0
    assert PERCEPTUAL == 3.0
    assert resolve_curve(None) == PERCEPTUAL  # perceptual is the default


def test_numeric_gamma_is_clamped_to_a_sane_range():
    assert resolve_curve(2.2) == 2.2
    assert resolve_curve("2.2") == 2.2
    # Below 1 the curve would bend the wrong way and make the top *slower*,
    # which is the problem this module exists to fix.
    assert resolve_curve(0.2) == 1.0
    assert resolve_curve(99) == 6.0
    assert resolve_curve("nonsense") == PERCEPTUAL


def test_min_brightness_is_clamped_to_leave_travel():
    assert resolve_min_brightness(26) == 26.0
    assert resolve_min_brightness(0) == 1.0
    assert resolve_min_brightness(9999) == 200.0
    assert resolve_min_brightness("junk") == 1.0


@pytest.mark.parametrize("gamma", [LINEAR, 2.2, PERCEPTUAL])
@pytest.mark.parametrize("floor", [1.0, 26.0])
def test_position_and_brightness_round_trip(gamma, floor):
    for brightness in (floor, 50.0, 128.0, 200.0, MAX_BRIGHTNESS):
        position = to_position(brightness, gamma, floor)
        assert from_position(position, gamma, floor) == pytest.approx(brightness, abs=0.01)


@pytest.mark.parametrize("gamma", [LINEAR, 2.2, PERCEPTUAL])
def test_endpoints_are_exact(gamma):
    assert to_position(1.0, gamma, 1.0) == 0.0
    assert to_position(MAX_BRIGHTNESS, gamma, 1.0) == 1.0
    assert from_position(0.0, gamma, 1.0) == 1.0
    assert from_position(1.0, gamma, 1.0) == MAX_BRIGHTNESS


def test_off_light_sits_at_the_bottom_of_the_travel():
    # Brightness 0 (an off light) must not read as somewhere up the curve, or a
    # hold-to-raise from dark would jump partway in.
    assert to_position(0, PERCEPTUAL, 26.0) == 0.0


def test_perceptual_midpoint_is_dim_not_half_brightness():
    # The whole point: halfway through a hold should *look* halfway, which is
    # nowhere near half of full output.
    mid = from_position(0.5, PERCEPTUAL, 1.0)
    assert 25 <= mid <= 40  # ~13% of full scale
    # Linear, by contrast, is exactly half.
    assert from_position(0.5, LINEAR, 1.0) == pytest.approx(128.0, abs=1)


def test_perceptual_ramp_spends_travel_evenly_instead_of_stalling_at_the_top():
    # The reported symptom: with a linear ramp most of the hold is spent in the
    # top of the range, where a given step is least visible. Compare how much of
    # a full-range hold each curve spends above 50% output.
    def fraction_above_half(gamma):
        above = sum(
            1
            for i in range(1000)
            if from_position(i / 999, gamma, 1.0) > MAX_BRIGHTNESS / 2
        )
        return above / 1000

    assert fraction_above_half(LINEAR) == pytest.approx(0.5, abs=0.02)
    # Perceptual spends only ~20% of the hold in the top half of raw output,
    # freeing the rest for the bottom where changes are actually visible.
    assert fraction_above_half(PERCEPTUAL) < 0.25


def test_ramp_advances_by_equal_position_not_equal_brightness():
    ramp = Ramp(
        start_brightness=1.0,
        direction_sign=1,
        units_per_second=MAX_BRIGHTNESS,  # full travel in 1 second
        tick_seconds=0.1,
        gamma=PERCEPTUAL,
        min_brightness=1.0,
    )
    steps = [ramp.advance() for _ in range(10)]
    assert ramp.position == pytest.approx(1.0)
    assert steps[-1] == pytest.approx(MAX_BRIGHTNESS)
    assert steps == sorted(steps)
    # Brightness deltas grow as the ramp climbs — small where the eye is
    # sensitive, large where it is not.
    deltas = [b - a for a, b in zip(steps, steps[1:])]
    assert deltas == sorted(deltas)
    assert deltas[-1] > deltas[0] * 10


def test_ramp_reaches_rails_and_reports_them():
    up = Ramp(
        start_brightness=250.0, direction_sign=1, units_per_second=MAX_BRIGHTNESS,
        tick_seconds=0.5, gamma=PERCEPTUAL, min_brightness=1.0,
    )
    assert not up.at_rail
    up.advance()
    assert up.at_rail
    assert up.position == 1.0

    down = Ramp(
        start_brightness=10.0, direction_sign=-1, units_per_second=MAX_BRIGHTNESS,
        tick_seconds=0.5, gamma=PERCEPTUAL, min_brightness=1.0,
    )
    down.advance()
    assert down.at_rail
    # Bottoming out lands on the floor, never off.
    assert down.advance() == 1.0


def test_ramp_floor_is_the_configured_minimum():
    # With a device floor of 26, a full hold travels 26..255 — no part of the
    # ramp is spent below what the hardware can actually show.
    ramp = Ramp(
        start_brightness=255.0, direction_sign=-1, units_per_second=MAX_BRIGHTNESS,
        tick_seconds=0.1, gamma=PERCEPTUAL, min_brightness=26.0,
    )
    values = [ramp.advance() for _ in range(10)]
    assert min(values) == pytest.approx(26.0)
    assert all(v >= 26.0 for v in values)


def test_curve_shape_reads_entry_options_and_honors_override():
    class Entry:
        options = {"curve": CURVE_LINEAR, "min_brightness": 26}

    assert curve_shape(Entry()) == (1.0, 26.0)
    # A per-call override beats the configured default, but the floor is a
    # device property and stays put.
    assert curve_shape(Entry(), CURVE_PERCEPTUAL) == (3.0, 26.0)
    # No entry at all (backend used standalone) still yields usable defaults.
    assert curve_shape(None) == (PERCEPTUAL, 1.0)
