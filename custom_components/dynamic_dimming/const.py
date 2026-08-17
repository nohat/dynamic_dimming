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

# ZHA (Zigbee Cluster Library)
ZHA_DOMAIN = "zha"
ZHA_SERVICE_ISSUE_CLUSTER_COMMAND = "issue_zigbee_cluster_command"
# Level Control and Color Control, and the command ids within them. Matter's
# clusters descend from these and carry the same numbers today; they are spelled
# out separately because they are two specifications that are free to diverge.
ZIGBEE_LEVEL_CONTROL_CLUSTER = 0x0008
ZIGBEE_COLOR_CONTROL_CLUSTER = 0x0300
ZIGBEE_COMMAND_MOVE = 0x01
ZIGBEE_COMMAND_STEP = 0x02
ZIGBEE_COMMAND_STOP = 0x03
ZIGBEE_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF = 0x04
ZIGBEE_COMMAND_MOVE_TO_COLOR_TEMP = 0x0A
ZIGBEE_MOVE_MODE_UP = 0x00
ZIGBEE_MOVE_MODE_DOWN = 0x01
# Levels run 1-254: 0 is off and only reachable through the WithOnOff command
# variants, and 255 is reserved.
ZIGBEE_MIN_LEVEL = 1
ZIGBEE_MAX_LEVEL = 254
# Transition times are uint16 tenths of a second, 65535 reserved for null.
ZIGBEE_MAX_TRANSITION_TENTHS = 65534
# Options bitmaps on Level Control / Color Control. Setting both mask and
# override bit 0 turns ExecuteIfOff on for that one command.
ZIGBEE_OPTION_EXECUTE_IF_OFF = 0b0000_0001

# Z-Wave JS
ZWAVE_JS_DOMAIN = "zwave_js"
ZWAVE_JS_SERVICE_INVOKE_CC_API = "invoke_cc_api"
# Multilevel Switch CC (0x26), whose StartLevelChange/StopLevelChange pair is
# Z-Wave's hold-to-dim primitive.
ZWAVE_COMMAND_CLASS_MULTILEVEL_SWITCH = 38
ZWAVE_METHOD_START_LEVEL_CHANGE = "startLevelChange"
ZWAVE_METHOD_STOP_LEVEL_CHANGE = "stopLevelChange"
# A Z-Wave duration is the time for a *full-scale* sweep, encoded as whole
# seconds up to 127 (above that it becomes whole minutes, which is far coarser
# than any dimming gesture needs).
ZWAVE_MIN_DURATION_SECONDS = 1
ZWAVE_MAX_DURATION_SECONDS = 127

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
