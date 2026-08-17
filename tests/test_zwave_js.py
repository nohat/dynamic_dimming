"""Tests for the native Z-Wave JS backend."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.dynamic_dimming.backends.zwave_js import (
    ZwaveJsBackend,
    has_value_id,
    to_duration,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    ZWAVE_COMMAND_CLASS_MULTILEVEL_SWITCH,
    ZWAVE_JS_DOMAIN,
    ZWAVE_JS_SERVICE_INVOKE_CC_API,
    ZWAVE_METHOD_START_LEVEL_CHANGE,
    ZWAVE_METHOD_STOP_LEVEL_CHANGE,
)

from .conftest import register_device_light

# zwave_js builds a unique_id as "<home_id>.<value_id>".
UNIQUE_ID = "3245146787.16-38-0-currentValue"


@pytest.fixture
def zwave_calls(hass):
    """Stand in for zwave_js.invoke_cc_api, recording every call."""
    return async_mock_service(
        hass, ZWAVE_JS_DOMAIN, ZWAVE_JS_SERVICE_INVOKE_CC_API
    )


def _zwave_light(hass, object_id="dimmer", *, unique_id=UNIQUE_ID):
    return register_device_light(
        hass,
        object_id,
        domain=ZWAVE_JS_DOMAIN,
        identifiers={(ZWAVE_JS_DOMAIN, "3245146787-16")},
        unique_id=unique_id,
    )


def _only(calls):
    """The single recorded call's data, asserting there was exactly one."""
    assert len(calls) == 1
    return calls[0].data


# -- unique_id / duration ---------------------------------------------------------


def test_has_value_id_accepts_a_real_unique_id():
    assert has_value_id(UNIQUE_ID)


def test_has_value_id_rejects_a_valueless_unique_id():
    """The node-status style unique_id the service itself skips."""
    assert not has_value_id("3245146787.16")


def test_has_value_id_rejects_a_unique_id_without_a_home_id():
    assert not has_value_id("16-38-0-currentValue")


@pytest.mark.parametrize(
    ("rate", "duration"),
    [("slow", "6s"), ("medium", "3s"), ("fast", "2s"), (None, "3s")],
)
def test_to_duration_expresses_a_rate_as_a_full_sweep(rate, duration):
    assert to_duration(rate) == duration


def test_to_duration_floors_a_very_fast_rate_at_one_second():
    assert to_duration(5000) == "1s"


def test_to_duration_caps_a_very_slow_rate():
    # 255/1 = 255s, above the 127s the encoding can carry in seconds.
    assert to_duration(1) == "127s"


# -- claiming ---------------------------------------------------------------------


async def test_claims_zwave_light(hass, zwave_calls):
    assert ZwaveJsBackend(hass).claims(_zwave_light(hass))


async def test_does_not_claim_without_the_zwave_service(hass):
    entity_id = _zwave_light(hass)
    assert not ZwaveJsBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass, zwave_calls):
    entity_id = register_device_light(
        hass, "other", domain="hue", identifiers={("hue", "abc")}
    )
    assert not ZwaveJsBackend(hass).claims(entity_id)


async def test_does_not_claim_entity_the_service_would_skip(hass, zwave_calls):
    entity_id = _zwave_light(hass, unique_id="3245146787.16")
    assert not ZwaveJsBackend(hass).claims(entity_id)


# -- move / stop ------------------------------------------------------------------


async def test_move_up_starts_a_level_change(hass, zwave_calls):
    entity_id = _zwave_light(hass)
    await ZwaveJsBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    data = _only(zwave_calls)
    # Targeted by entity so zwave_js resolves the endpoint from the primary value.
    assert data["entity_id"] == entity_id
    assert data["command_class"] == ZWAVE_COMMAND_CLASS_MULTILEVEL_SWITCH
    assert data["method_name"] == ZWAVE_METHOD_START_LEVEL_CHANGE
    assert data["parameters"] == [
        {"direction": "up", "ignoreStartLevel": True, "duration": "3s"}
    ]


async def test_move_down_reverses_the_direction(hass, zwave_calls):
    entity_id = _zwave_light(hass)
    await ZwaveJsBackend(hass).async_move(entity_id, DIRECTION_DOWN, "slow")
    assert _only(zwave_calls)["parameters"] == [
        {"direction": "down", "ignoreStartLevel": True, "duration": "6s"}
    ]


async def test_move_returns_no_job_handle(hass, zwave_calls):
    entity_id = _zwave_light(hass)
    assert await ZwaveJsBackend(hass).async_move(entity_id, DIRECTION_UP, None) is None


async def test_move_on_unclaimed_entity_sends_nothing(hass, zwave_calls):
    register_device_light(hass, "other", domain="hue", identifiers={("hue", "abc")})
    await ZwaveJsBackend(hass).async_move("light.other", DIRECTION_UP, "medium")
    assert not zwave_calls


async def test_stop_stops_the_level_change(hass, zwave_calls):
    entity_id = _zwave_light(hass)
    await ZwaveJsBackend(hass).async_stop(entity_id)
    data = _only(zwave_calls)
    assert data["method_name"] == ZWAVE_METHOD_STOP_LEVEL_CHANGE
    assert data["parameters"] == []


async def test_stop_on_unclaimed_entity_sends_nothing(hass, zwave_calls):
    register_device_light(hass, "other", domain="hue", identifiers={("hue", "abc")})
    await ZwaveJsBackend(hass).async_stop("light.other")
    assert not zwave_calls


# -- step -------------------------------------------------------------------------


def test_backend_disclaims_step(hass):
    """Multilevel Switch has no Step, so the controller simulates it."""
    assert not ZwaveJsBackend(hass).supports_step


def test_backend_disclaims_fade(hass):
    """Set-with-duration is whole seconds only, too coarse for the fade contract."""
    assert not ZwaveJsBackend(hass).supports_fade


async def test_step_sends_nothing_natively(hass, zwave_calls):
    entity_id = _zwave_light(hass)
    await ZwaveJsBackend(hass).async_step(entity_id, DIRECTION_UP, 5.0)
    assert not zwave_calls
