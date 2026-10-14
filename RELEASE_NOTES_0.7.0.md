# Dynamic Dimming 0.7.0

This release makes the accumulated backend and targeting work available through HACS. It adds ZHA, Z-Wave JS, Shelly Gen2+, Hue v2, deCONZ and LIFX support to the previously released Zigbee2MQTT, Tasmota, Matter and WiZ paths.

## Changes

- ZHA uses the integration's cluster-command service for move, stop, step and fade. ZHA groups use simulation.
- Z-Wave JS uses StartLevelChange and StopLevelChange. Step and fade use the HA light-service fallback.
- Shelly Gen2+ light components use local RPC dimming commands and device-side fades. Gen1 uses simulation.
- Hue v2 supports individual lights and room/zone grouped lights through the existing bridge client. Hue v1 uses simulation.
- deCONZ supports individual lights and groups through `deconz.configure`, including selecting the entity's gateway.
- LIFX uses device-side LAN transitions with an **estimated stop**, not a native stop command.
- All four actions accept standard HA targets: one entity, multiple entities, devices or areas. Existing `data.entity_id` calls remain supported.
- A step now cancels an earlier simulated ramp. LIFX completed transitions expire, and switching to simulation clears the active estimate so a later stop cannot restore it.

## Validation and limits

The local suite passes **363 tests** on Home Assistant **2026.2.3**, including command payloads, routing, cancellation, target resolution and the real aiohue 4.8.0 client request path. Network/device responses are mocked in this suite; it does not establish physical latency or fixture compatibility. See [TESTING.md](TESTING.md) for the recorded ZHA, Matter and WiZ hardware campaigns and other test procedures.

**Shelly, Hue, deCONZ and LIFX have not been validated on physical devices in this project.** Their automated coverage is useful but does not replace testing model/firmware behavior. Treat these paths as initial support and report device model, firmware, HA version and the failing action when filing issues. `backend: simulated` bypasses them for comparison.

LIFX stop estimates brightness from the starting state and elapsed time. Packet loss, concurrent commands, stale state and device timing can make it jump. Hue and deCONZ compute relative moves from cached brightness, so stale state can affect sweep duration and endpoint. Native fades have platform-specific floors and timing resolution; use `light.turn_off` when an off state is required. ZHA color-temperature reporting can lag after a fade even when the fixture has changed.

Requires Home Assistant 2026.2 or later. The test version above is the version actually exercised, not a claim of testing every later HA release.

## Install or upgrade

Add `nohat/dynamic_dimming` as a custom Integration repository in HACS if needed, download **0.7.0**, and restart Home Assistant. Existing users keep their configured integration and automations. New users then add Dynamic Dimming under Settings → Devices & Services.

Try one light first: hold calls `dynamic_dimming.move`, release calls `dynamic_dimming.stop`. Broader adoption examples will refer to this release rather than unreleased branches.
