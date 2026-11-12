"""Traffic budgets for the simulated (stepped) path.

A native backend puts one command on the wire per gesture, so it needs no
budget. Simulation is different: it writes absolute levels through
``light.turn_on`` for as long as a ramp runs, and where those writes land on a
radio mesh or a bridge, twenty a second per light is more than the transport
can carry. Simulation reaches such lights more often than it looks: a Home
Assistant light group of Zigbee bulbs, a ZHA group, a Z-Wave ``fade``, a
``backend: simulated`` override, or any light whose native claim failed.

This module decides how often a simulated ramp may write. Each transport has a
budget in writes per second that is shared by every simulated ramp running on
it, so ten lights ramping at once slow each other down rather than multiplying
the load. Transports with no entry here are paced only by the tick and by the
one-write-in-flight rule the simulation applies everywhere.

The numbers are deliberately conservative defaults, not measurements:

- Zigbee and Thread unicast, and the Hue bridge, are budgeted at five writes a
  second (Signify's guidance for the bridge is about ten light commands a
  second; half leaves room for everything else in the house).
- A Zigbee group command is a broadcast, and a Zigbee network may only relay
  about eight broadcasts in nine seconds, so a ZHA or Zigbee2MQTT group costs
  a full second per write.
- Z-Wave carries roughly one acknowledged command a second per route without
  queuing, so it gets one.
"""

from __future__ import annotations

import re
from collections import Counter

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

# Writes per second each transport may carry across all simulated ramps.
TRANSPORT_BUDGETS: dict[str, float] = {
    "zha": 5.0,
    "zigbee2mqtt": 5.0,
    "deconz": 5.0,
    "hue": 5.0,
    "matter": 5.0,
    "zwave_js": 1.0,
}
# What one write to a Zigbee group costs against its budget: a group command
# is a network-wide broadcast, so it is charged a whole second.
_GROUP_COST = 5.0
# No budget stretches the gap between writes beyond this. Past it a ramp stops
# reading as a ramp, and the one-write-in-flight rule is the protection left.
MAX_WRITE_INTERVAL = 2.0
# How deep group-in-group nesting is followed before giving up.
_MAX_GROUP_DEPTH = 4

_Z2M_IDENTIFIER_PREFIX = "zigbee2mqtt_"
_IEEE = re.compile(r"0x[0-9a-fA-F]{16}")


def _leaf_cost(hass: HomeAssistant, entity_id: str) -> tuple[str, float] | None:
    """The transport one non-group light writes to, and what a write costs."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        return None
    platform = entity.platform
    if platform == "mqtt":
        # Only Zigbee2MQTT lights sit on a mesh; other MQTT lights are their
        # own devices on IP.
        device = (
            dr.async_get(hass).async_get(entity.device_id)
            if entity.device_id
            else None
        )
        z2m_id = next(
            (
                value.removeprefix(_Z2M_IDENTIFIER_PREFIX)
                for domain, value in (device.identifiers if device else ())
                if domain == "mqtt" and value.startswith(_Z2M_IDENTIFIER_PREFIX)
            ),
            None,
        )
        if z2m_id is None:
            return None
        # A device is identified by its IEEE address; anything else under the
        # prefix is a Zigbee2MQTT group.
        return "zigbee2mqtt", 1.0 if _IEEE.fullmatch(z2m_id) else _GROUP_COST
    if platform not in TRANSPORT_BUDGETS:
        return None
    if platform == "zha" and "zha_group" in (entity.unique_id or ""):
        return platform, _GROUP_COST
    return platform, 1.0


def write_costs(hass: HomeAssistant, entity_id: str) -> Counter[str]:
    """Budgeted writes one ``light.turn_on`` to ``entity_id`` puts on each transport.

    A group that lists its members in its ``entity_id`` state attribute (Home
    Assistant light groups do) is charged for every member, because the group
    fans one call out to all of them.
    """
    costs: Counter[str] = Counter()
    seen: set[str] = set()

    def _walk(current: str, depth: int) -> None:
        if current in seen or depth > _MAX_GROUP_DEPTH:
            return
        seen.add(current)
        state = hass.states.get(current)
        members = state.attributes.get("entity_id") if state is not None else None
        if isinstance(members, (list, tuple)) and members:
            for member in members:
                if isinstance(member, str):
                    _walk(member, depth + 1)
            return
        leaf = _leaf_cost(hass, current)
        if leaf is not None:
            costs[leaf[0]] += leaf[1]

    _walk(entity_id, 0)
    return costs


class TrafficBudget:
    """Shares each transport's write budget among the ramps running on it."""

    def __init__(self) -> None:
        self._active: Counter[str] = Counter()

    def acquire(self, costs: Counter[str]) -> None:
        """Count a ramp's writes against its transports while it runs."""
        self._active.update(costs)

    def release(self, costs: Counter[str]) -> None:
        self._active.subtract(costs)
        for transport in [t for t, cost in self._active.items() if cost <= 0]:
            del self._active[transport]

    def interval(self, costs: Counter[str]) -> float:
        """Seconds a ramp with these costs must leave between its writes.

        Zero means unbudgeted. The gap is what keeps the transport's total at
        or under budget given everything currently ramping on it.
        """
        gap = 0.0
        for transport in costs:
            budget = TRANSPORT_BUDGETS.get(transport)
            if budget:
                gap = max(gap, self._active.get(transport, 0.0) / budget)
        return min(MAX_WRITE_INTERVAL, gap)
