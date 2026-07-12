"""Constants for Dynamic Dimming."""

from __future__ import annotations

from datetime import timedelta
from enum import Enum

DOMAIN = "dynamic_dimming"


class DimmingClass(Enum):
    """How an entity can be driven."""

    NATIVE = "native"
    SIMULATED = "simulated"
    UNSUPPORTED = "unsupported"


# Services
SERVICE_MOVE = "move"
SERVICE_STOP = "stop"
SERVICE_STEP = "step"

# Service data keys
ATTR_DIRECTION = "direction"
ATTR_RATE = "rate"
ATTR_STEP_PCT = "step_pct"

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"

# Backend override values for the `backend` service field.
ATTR_BACKEND = "backend"
BACKEND_AUTO = "auto"
BACKEND_SIMULATED = "simulated"
BACKEND_NATIVE = "native"

# Zigbee2MQTT
CONF_Z2M_BASE_TOPIC = "z2m_base_topic"
DEFAULT_Z2M_BASE_TOPIC = "zigbee2mqtt"

# Simulation tuning
TICK_INTERVAL = timedelta(milliseconds=50)  # 20 Hz cap
# Named rate profiles -> brightness units (0-255) per second.
RATE_PROFILES: dict[str, float] = {"slow": 40.0, "medium": 90.0, "fast": 160.0}
DEFAULT_RATE = "medium"
DEFAULT_STEP_PCT = 5.0
# Dimming down floors here — a still-on minimum, never 0/off. This is the Zigbee
# Level Control "Move" (vs "Move with On/Off") semantics: hold-to-dim-down bottoms
# out at the lowest on-level and stays lit. Use light.turn_off to actually turn off.
DEFAULT_MIN_BRIGHTNESS = 1
