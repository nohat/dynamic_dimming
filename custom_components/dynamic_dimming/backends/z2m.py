"""Native Zigbee2MQTT backend: brightness_move / brightness_step over MQTT."""

from __future__ import annotations

import json

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import CONF_Z2M_BASE_TOPIC, DEFAULT_Z2M_BASE_TOPIC, DIRECTION_UP
from .base import DimmingBackend
from .simulation import resolve_rate

# Z2M-discovered devices carry ("mqtt", "zigbee2mqtt_<ieee>") in the device
# registry; the IEEE address doubles as the device's MQTT topic, which
# survives friendly-name renames.
_IDENTIFIER_PREFIX = "zigbee2mqtt_"


class Z2MBackend(DimmingBackend):
    """Publishes brightness_move / brightness_step to <base_topic>/<ieee>/set.

    Plain ``brightness_move`` maps to the Zigbee Level cluster Move command
    (not Move-with-On/Off): the device floors at its minimum on-level and
    never turns itself off. ``brightness_move_onoff`` is deliberately unused.
    Rates are brightness units/second on Z2M's 0-254 scale, so the shared
    rate profiles pass through unchanged.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry

    @property
    def _base_topic(self) -> str:
        return self._entry.options.get(CONF_Z2M_BASE_TOPIC, DEFAULT_Z2M_BASE_TOPIC)

    def _ieee(self, entity_id: str) -> str | None:
        if "mqtt" not in self.hass.config.components:
            return None
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.device_id is None:
            return None
        device = dr.async_get(self.hass).async_get(entity.device_id)
        if device is None:
            return None
        for domain, value in device.identifiers:
            if domain == "mqtt" and value.startswith(_IDENTIFIER_PREFIX):
                return value.removeprefix(_IDENTIFIER_PREFIX)
        return None

    def claims(self, entity_id: str) -> bool:
        return self._ieee(entity_id) is not None

    async def _publish(self, ieee: str, payload: dict) -> None:
        await mqtt.async_publish(
            self.hass, f"{self._base_topic}/{ieee}/set", json.dumps(payload)
        )

    async def async_move(
        self, entity_id: str, direction: str, rate: str | float | None
    ) -> CALLBACK_TYPE | None:
        ieee = self._ieee(entity_id)
        if ieee is None:
            return None
        sign = 1 if direction == DIRECTION_UP else -1
        await self._publish(
            ieee, {"brightness_move": sign * int(round(resolve_rate(rate)))}
        )
        return None

    async def async_stop(self, entity_id: str) -> None:
        ieee = self._ieee(entity_id)
        if ieee is None:
            return
        await self._publish(ieee, {"brightness_move": "stop"})

    async def async_step(self, entity_id: str, direction: str, step_pct: float) -> None:
        ieee = self._ieee(entity_id)
        if ieee is None:
            return
        sign = 1 if direction == DIRECTION_UP else -1
        await self._publish(
            ieee, {"brightness_step": sign * int(round(step_pct / 100.0 * 254))}
        )
