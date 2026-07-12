"""Shared fixtures for Dynamic Dimming tests."""

from __future__ import annotations

import pytest

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


def set_light_state(hass, entity_id, *, brightness=None, color_modes=("brightness",), on=True):
    """Helper: register a light state with the given color-mode support."""
    attrs = {"supported_color_modes": list(color_modes)}
    if brightness is not None:
        attrs["brightness"] = brightness
    hass.states.async_set(entity_id, "on" if on else "off", attrs)


def register_device_light(
    hass, object_id, *, domain, identifiers=None, connections=None, brightness=100
):
    """Register light.<object_id> backed by a device from integration ``domain``.

    Mirrors how a real integration populates the registries: config entry ->
    device (with the given identifiers/connections) -> entity. Sets a
    brightness-capable state and returns the entity_id.
    """
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=set(identifiers or ()),
        connections=set(connections or ()),
    )
    er.async_get(hass).async_get_or_create(
        "light",
        domain,
        f"uid_{object_id}",
        suggested_object_id=object_id,
        device_id=device.id,
    )
    entity_id = f"light.{object_id}"
    set_light_state(hass, entity_id, brightness=brightness)
    return entity_id
