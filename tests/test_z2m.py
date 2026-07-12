"""Tests for the Zigbee2MQTT native backend."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dynamic_dimming.backends.z2m import Z2MBackend
from custom_components.dynamic_dimming.const import (
    CONF_Z2M_BASE_TOPIC,
    DIRECTION_DOWN,
    DIRECTION_UP,
    DOMAIN,
)

from .conftest import register_device_light

IEEE = "0x588e81fffe512c62"


def _backend(hass, options=None):
    entry = MockConfigEntry(domain=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    return Z2MBackend(hass, entry)


def _z2m_light(hass, object_id="strip"):
    return register_device_light(
        hass,
        object_id,
        domain="mqtt",
        identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")},
    )


async def test_claims_z2m_device(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    assert _backend(hass).claims(entity_id)


async def test_does_not_claim_other_mqtt_device(hass, mqtt_mock):
    entity_id = register_device_light(
        hass, "other", domain="mqtt", identifiers={("mqtt", "somebridge_abc")}
    )
    assert not _backend(hass).claims(entity_id)


async def test_does_not_claim_without_mqtt_loaded(hass):
    entity_id = _z2m_light(hass)
    assert not _backend(hass).claims(entity_id)


async def test_does_not_claim_deviceless_entity(hass, mqtt_mock):
    hass.states.async_set(
        "light.bare", "on", {"supported_color_modes": ["brightness"]}
    )
    assert not _backend(hass).claims("light.bare")


async def test_move_up_publishes_rate(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    await _backend(hass).async_move(entity_id, DIRECTION_UP, "medium")
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": 90}', 0, False
    )


async def test_move_down_publishes_negative_numeric_rate(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    await _backend(hass).async_move(entity_id, DIRECTION_DOWN, 40)
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": -40}', 0, False
    )


async def test_move_returns_no_job_handle(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    assert await _backend(hass).async_move(entity_id, DIRECTION_UP, None) is None


async def test_stop_publishes_stop(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    await _backend(hass).async_stop(entity_id)
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_move": "stop"}', 0, False
    )


async def test_step_converts_pct_to_units(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    await _backend(hass).async_step(entity_id, DIRECTION_DOWN, 5.0)
    mqtt_mock.async_publish.assert_called_once_with(
        f"zigbee2mqtt/{IEEE}/set", '{"brightness_step": -13}', 0, False
    )


async def test_custom_base_topic(hass, mqtt_mock):
    entity_id = _z2m_light(hass)
    backend = _backend(hass, {CONF_Z2M_BASE_TOPIC: "z2m"})
    await backend.async_stop(entity_id)
    mqtt_mock.async_publish.assert_called_once_with(
        f"z2m/{IEEE}/set", '{"brightness_move": "stop"}', 0, False
    )
