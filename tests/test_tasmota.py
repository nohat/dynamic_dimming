"""Tests for the Tasmota native backend."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

from custom_components.dynamic_dimming.backends.tasmota import TasmotaBackend
from custom_components.dynamic_dimming.const import DIRECTION_DOWN, DIRECTION_UP

from .conftest import register_device_light

MAC_COLONED = "c8:2b:96:57:61:3d"
MAC_DISCOVERY = "C82B9657613D"


@pytest.fixture(autouse=True)
def silence_mqtt_integration_discovery():
    """Publishing on the real tasmota discovery topic would otherwise trigger
    HA's MQTT integration discovery, which tries to import the real tasmota
    integration's config flow — unavailable in this test env."""
    with patch(
        "homeassistant.components.mqtt.discovery.async_get_mqtt", return_value={}
    ):
        yield


async def _discovered_backend(hass):
    backend = TasmotaBackend(hass)
    await backend.async_setup()
    async_fire_mqtt_message(
        hass,
        f"tasmota/discovery/{MAC_DISCOVERY}/config",
        json.dumps({"t": "office_lamp", "ft": "%prefix%/%topic%/"}),
    )
    await hass.async_block_till_done()
    return backend


def _tasmota_light(hass, object_id="lamp", mac=MAC_COLONED):
    return register_device_light(
        hass, object_id, domain="tasmota", connections={("mac", mac)}
    )


async def test_claims_discovered_tasmota_light(hass, mqtt_mock):
    entity_id = _tasmota_light(hass)
    backend = await _discovered_backend(hass)
    assert backend.claims(entity_id)


async def test_does_not_claim_without_discovery(hass, mqtt_mock):
    entity_id = _tasmota_light(hass)
    backend = TasmotaBackend(hass)
    await backend.async_setup()
    assert not backend.claims(entity_id)


async def test_does_not_claim_non_tasmota_device(hass, mqtt_mock):
    entity_id = register_device_light(
        hass, "other", domain="hue", connections={("mac", MAC_COLONED)}
    )
    backend = await _discovered_backend(hass)
    assert not backend.claims(entity_id)


async def test_discovery_ignores_malformed_payloads(hass, mqtt_mock):
    backend = TasmotaBackend(hass)
    await backend.async_setup()
    async_fire_mqtt_message(
        hass, "tasmota/discovery/AABBCCDDEEFF/config", "not json"
    )
    async_fire_mqtt_message(
        hass, "tasmota/discovery/AABBCCDDEEFF/config", json.dumps({"t": "x"})
    )
    await hass.async_block_till_done()
    assert backend._cmnd_prefix == {}


async def test_move_publishes_dimmer_arrows(hass, mqtt_mock):
    entity_id = _tasmota_light(hass)
    backend = await _discovered_backend(hass)
    mqtt_mock.async_publish.reset_mock()
    assert await backend.async_move(entity_id, DIRECTION_UP, "fast") is None
    mqtt_mock.async_publish.assert_called_once_with(
        "cmnd/office_lamp/Dimmer", ">", 0, False
    )
    mqtt_mock.async_publish.reset_mock()
    await backend.async_move(entity_id, DIRECTION_DOWN, None)
    mqtt_mock.async_publish.assert_called_once_with(
        "cmnd/office_lamp/Dimmer", "<", 0, False
    )


async def test_stop_publishes_bang(hass, mqtt_mock):
    entity_id = _tasmota_light(hass)
    backend = await _discovered_backend(hass)
    mqtt_mock.async_publish.reset_mock()
    await backend.async_stop(entity_id)
    mqtt_mock.async_publish.assert_called_once_with(
        "cmnd/office_lamp/Dimmer", "!", 0, False
    )


async def test_step_publishes_plus_minus(hass, mqtt_mock):
    entity_id = _tasmota_light(hass)
    backend = await _discovered_backend(hass)
    mqtt_mock.async_publish.reset_mock()
    await backend.async_step(entity_id, DIRECTION_UP, 5.0)
    mqtt_mock.async_publish.assert_called_once_with(
        "cmnd/office_lamp/Dimmer", "+", 0, False
    )


async def test_unload_unsubscribes(hass, mqtt_mock):
    backend = TasmotaBackend(hass)
    await backend.async_setup()
    await backend.async_unload()
    async_fire_mqtt_message(
        hass,
        f"tasmota/discovery/{MAC_DISCOVERY}/config",
        json.dumps({"t": "office_lamp", "ft": "%prefix%/%topic%/"}),
    )
    await hass.async_block_till_done()
    assert backend._cmnd_prefix == {}


async def test_setup_without_mqtt_is_noop(hass):
    backend = TasmotaBackend(hass)
    await backend.async_setup()  # must not raise
    assert not backend.claims("light.anything")
