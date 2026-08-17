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
SERVICE_FADE = "fade"

# Service data keys
ATTR_DIRECTION = "direction"
ATTR_RATE = "rate"
ATTR_STEP_PCT = "step_pct"
ATTR_CURVE = "curve"

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

# Dimming curve (see curve.py)
CONF_CURVE = "curve"
CONF_MIN_BRIGHTNESS = "min_brightness"

# Matter
MATTER_DOMAIN = "matter"
# Level Control cluster and its Move/Step direction enums (Matter Application
# Clusters spec, "Level Control"). Color temperature rides on Color Control.
MATTER_LEVEL_CONTROL_CLUSTER = 0x0008
MATTER_COLOR_CONTROL_CLUSTER = 0x0300
MATTER_MOVE_MODE_UP = 0x00
MATTER_MOVE_MODE_DOWN = 0x01
# Matter levels run 1-254: 0 is "off" (and only reachable via the WithOnOff
# command variants) and 255 is reserved.
MATTER_MIN_LEVEL = 1
MATTER_MAX_LEVEL = 254
# Move `rate` is a uint8 of level units per second, so it shares the 1-254 range.
# transitionTime is a uint16 of *tenths* of a second, with 65535 reserved for null.
MATTER_MAX_TRANSITION_TENTHS = 65534

# WiZ
WIZ_DOMAIN = "wiz"
WIZ_PORT = 38899
# WiZ clamps setPilot `dimming` into this range; 0 does not switch a bulb off.
WIZ_MIN_DIMMING = 1
WIZ_MAX_DIMMING = 100

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
