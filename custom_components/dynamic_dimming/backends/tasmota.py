"""Native Tasmota backend: Dimmer > / < / ! over MQTT (arendst/Tasmota#11269)."""

from __future__ import annotations

import json

from homeassistant.components import mqtt
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DIRECTION_UP
from .base import DimmingBackend

DISCOVERY_TOPIC = "tasmota/discovery/+/config"

_MOVE_UP = ">"
_MOVE_DOWN = "<"
_STOP = "!"
_STEP_UP = "+"
_STEP_DOWN = "-"


def _normalize_mac(mac: str) -> str:
    return mac.replace(":", "").replace("-", "").lower()


class TasmotaBackend(DimmingBackend):
    """Publishes Dimmer commands to Tasmota devices' cmnd topics.

    Command topics come from the same retained ``tasmota/discovery/<MAC>/config``
    messages the Tasmota integration consumes: payload key ``t`` is the device
    topic, ``ft`` the full-topic pattern. Ramp speed and step size are the
    device's own ``Speed``/``Fade``/``DimmerStep`` settings — ``rate`` and
    ``step_pct`` are ignored on this path, and device config is never mutated.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # normalized MAC -> command topic prefix, e.g. "cmnd/office_lamp/"
        self._cmnd_prefix: dict[str, str] = {}
        self._unsub: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        if "mqtt" not in self.hass.config.components:
            return
        self._unsub = await mqtt.async_subscribe(
            self.hass, DISCOVERY_TOPIC, self._on_discovery
        )

    async def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _on_discovery(self, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        topic, full_topic = data.get("t"), data.get("ft")
        if not topic or not full_topic:
            return
        mac = _normalize_mac(msg.topic.split("/")[2])
        self._cmnd_prefix[mac] = full_topic.replace("%prefix%", "cmnd").replace(
            "%topic%", topic
        )

    def _cmnd_topic(self, entity_id: str) -> str | None:
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None or entity.device_id is None:
            return None
        device = dr.async_get(self.hass).async_get(entity.device_id)
        if device is None:
            return None
        if not any(
            (entry := self.hass.config_entries.async_get_entry(entry_id))
            and entry.domain == "tasmota"
            for entry_id in device.config_entries
        ):
            return None
        for conn_type, conn in device.connections:
            if conn_type == "mac":
                prefix = self._cmnd_prefix.get(_normalize_mac(conn))
                if prefix:
                    return f"{prefix}Dimmer"
        return None

    def claims(self, entity_id: str) -> bool:
        return self._cmnd_topic(entity_id) is not None

    async def async_move(
        self, entity_id: str, direction: str, rate: str | float | None
    ) -> CALLBACK_TYPE | None:
        # `rate` intentionally unused: ramp speed is the device's Speed/Fade.
        topic = self._cmnd_topic(entity_id)
        if topic is None:
            return None
        await mqtt.async_publish(
            self.hass, topic, _MOVE_UP if direction == DIRECTION_UP else _MOVE_DOWN
        )
        return None

    async def async_stop(self, entity_id: str) -> None:
        topic = self._cmnd_topic(entity_id)
        if topic is None:
            return
        await mqtt.async_publish(self.hass, topic, _STOP)

    async def async_step(self, entity_id: str, direction: str, step_pct: float) -> None:
        # `step_pct` intentionally unused: step size is the device's DimmerStep.
        topic = self._cmnd_topic(entity_id)
        if topic is None:
            return
        await mqtt.async_publish(
            self.hass, topic, _STEP_UP if direction == DIRECTION_UP else _STEP_DOWN
        )
