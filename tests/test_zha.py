"""Tests for the native ZHA backend."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.dynamic_dimming.backends.zha import (
    ZhaBackend,
    parse_endpoint_id,
    to_level,
)
from custom_components.dynamic_dimming.const import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    ZHA_DOMAIN,
    ZHA_SERVICE_ISSUE_CLUSTER_COMMAND,
    ZIGBEE_COLOR_CONTROL_CLUSTER,
    ZIGBEE_COMMAND_MOVE,
    ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP,
    ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
    ZIGBEE_COMMAND_STEP,
    ZIGBEE_COMMAND_STOP,
    ZIGBEE_LEVEL_CONTROL_CLUSTER,
)

from .conftest import register_device_light

IEEE = "00:0d:6f:00:05:7d:2d:34"


@pytest.fixture
def zha_calls(hass):
    """Stand in for ZHA's issue_zigbee_cluster_command, recording every call."""
    return async_mock_service(hass, ZHA_DOMAIN, ZHA_SERVICE_ISSUE_CLUSTER_COMMAND)


def _zha_light(hass, object_id="lamp", *, unique_id=f"{IEEE}-1", ieee=IEEE):
    return register_device_light(
        hass,
        object_id,
        domain=ZHA_DOMAIN,
        identifiers={(ZHA_DOMAIN, ieee)},
        unique_id=unique_id,
    )


def _only(calls):
    """The single recorded call's data, asserting there was exactly one."""
    assert len(calls) == 1
    return calls[0].data


# -- address resolution -----------------------------------------------------------


def test_parse_endpoint_id_takes_the_segment_after_the_address():
    assert parse_endpoint_id(f"{IEEE}-1", IEEE) == 1


def test_parse_endpoint_id_ignores_trailing_segments():
    assert parse_endpoint_id(f"{IEEE}-2-8", IEEE) == 2


def test_parse_endpoint_id_rejects_a_foreign_address():
    assert parse_endpoint_id("11:22:33:44:55:66:77:88-1", IEEE) is None


def test_parse_endpoint_id_rejects_a_non_numeric_endpoint():
    assert parse_endpoint_id(f"{IEEE}-main", IEEE) is None


@pytest.mark.parametrize(
    ("brightness", "level"), [(0, 1), (1, 1), (128, 127), (255, 254)]
)
def test_to_level_clamps_into_the_zigbee_range(brightness, level):
    assert to_level(brightness) == level


# -- claiming ---------------------------------------------------------------------


async def test_claims_zha_light(hass, zha_calls):
    assert ZhaBackend(hass).claims(_zha_light(hass))


async def test_does_not_claim_without_the_zha_service(hass):
    entity_id = _zha_light(hass)
    assert not ZhaBackend(hass).claims(entity_id)


async def test_does_not_claim_other_platform(hass, zha_calls):
    entity_id = register_device_light(
        hass, "other", domain="hue", identifiers={("hue", "abc")}
    )
    assert not ZhaBackend(hass).claims(entity_id)


async def test_does_not_claim_device_without_zha_identifier(hass, zha_calls):
    """A ZHA group light has no IEEE address and needs a group command."""
    entity_id = register_device_light(
        hass,
        "group",
        domain=ZHA_DOMAIN,
        identifiers={(ZHA_DOMAIN, "group_7")},
        unique_id="7_group",
    )
    assert not ZhaBackend(hass).claims(entity_id)


async def test_does_not_claim_when_unique_id_lacks_the_endpoint(hass, zha_calls):
    entity_id = _zha_light(hass, unique_id="something-else-1")
    assert not ZhaBackend(hass).claims(entity_id)


# -- move / stop / step -----------------------------------------------------------


async def test_move_up_sends_move_with_profile_rate(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    data = _only(zha_calls)
    assert data["ieee"] == IEEE
    assert data["endpoint_id"] == 1
    assert data["cluster_id"] == ZIGBEE_LEVEL_CONTROL_CLUSTER
    assert data["cluster_type"] == "in"
    assert data["command"] == ZIGBEE_COMMAND_MOVE
    assert data["command_type"] == "server"
    assert data["params"] == {"move_mode": 0, "rate": 90}


async def test_move_down_sends_the_down_mode(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_move(entity_id, DIRECTION_DOWN, 40)
    assert _only(zha_calls)["params"] == {"move_mode": 1, "rate": 40}


async def test_move_clamps_rate_into_the_uint8_range(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_move(entity_id, DIRECTION_UP, 9000)
    assert _only(zha_calls)["params"]["rate"] == 254


async def test_move_addresses_the_endpoint_from_the_unique_id(hass, zha_calls):
    entity_id = _zha_light(hass, "gang2", unique_id=f"{IEEE}-2")
    await ZhaBackend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    assert _only(zha_calls)["endpoint_id"] == 2


async def test_move_returns_no_job_handle(hass, zha_calls):
    entity_id = _zha_light(hass)
    assert await ZhaBackend(hass).async_move(entity_id, DIRECTION_UP, None) is None


async def test_move_on_unclaimed_entity_sends_nothing(hass, zha_calls):
    register_device_light(hass, "other", domain="hue", identifiers={("hue", "abc")})
    await ZhaBackend(hass).async_move("light.other", DIRECTION_UP, "medium")
    assert not zha_calls


async def test_stop_sends_the_stop_command(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_stop(entity_id)
    data = _only(zha_calls)
    assert data["cluster_id"] == ZIGBEE_LEVEL_CONTROL_CLUSTER
    assert data["command"] == ZIGBEE_COMMAND_STOP
    assert data["params"] == {}


async def test_step_converts_pct_to_level_units(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_step(entity_id, DIRECTION_DOWN, 5.0)
    data = _only(zha_calls)
    assert data["command"] == ZIGBEE_COMMAND_STEP
    assert data["params"] == {
        "step_mode": 1,
        "step_size": 13,
        "transition_time": 0,
    }


async def test_step_never_rounds_down_to_nothing(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_step(entity_id, DIRECTION_UP, 0.1)
    assert _only(zha_calls)["params"]["step_size"] == 1


# -- fade -------------------------------------------------------------------------


def test_backend_claims_fade(hass):
    assert ZhaBackend(hass).supports_fade


async def test_fade_sends_one_move_to_level_with_transition(hass, zha_calls):
    entity_id = _zha_light(hass)
    assert await ZhaBackend(hass).async_fade(entity_id, 255, 2.5) is None
    data = _only(zha_calls)
    assert data["command"] == ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF
    assert data["params"] == {"level": 254, "transition_time": 25}


async def test_fade_asserts_color_before_the_ramp(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    assert len(zha_calls) == 2
    color, level = zha_calls[0].data, zha_calls[1].data
    assert color["cluster_id"] == ZIGBEE_COLOR_CONTROL_CLUSTER
    assert color["command"] == ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP
    assert color["params"] == {
        "color_temp_mireds": 370,
        "transition_time": 0,
        # ExecuteIfOff, so a fade up from off arrives at the right white.
        "options_mask": 1,
        "options_override": 1,
    }
    assert level["command"] == ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF


async def test_fade_still_ramps_when_the_color_command_fails(hass):
    """A device with no Color Control cluster loses its white, not its fade."""
    seen: list = []

    async def _handler(call):
        seen.append(call.data)
        if call.data["cluster_id"] == ZIGBEE_COLOR_CONTROL_CLUSTER:
            raise ValueError("Cluster 768 not found on endpoint 1")

    hass.services.async_register(
        ZHA_DOMAIN, ZHA_SERVICE_ISSUE_CLUSTER_COMMAND, _handler
    )
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_fade(entity_id, 128, 1.0, color_temp_kelvin=2700)
    await hass.async_block_till_done()
    assert [data["command"] for data in seen] == [
        ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP,
        ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
    ]


async def test_fade_clamps_a_very_long_transition(hass, zha_calls):
    entity_id = _zha_light(hass)
    await ZhaBackend(hass).async_fade(entity_id, 255, 100_000.0)
    assert _only(zha_calls)["params"]["transition_time"] == 65534
