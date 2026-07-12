# Manual test plan — v0.1a

The integration has so far been exercised casually, on a handful of lights. This plan makes the author's own testing systematic before asking anyone else for theirs — and it doubles as a guide for anyone who wants to test thoroughly before filing a device report. The author's test fleet, across two Home Assistant instances, covers seven integration paths: ESPHome on real dimmer loads, Tasmota (plug and bulb), Zigbee2MQTT, Matter, WiZ local UDP, Tuya cloud, and a Home Assistant light group. Each device runs the same per-device protocol; a set of system-level tests runs once per instance. Every completed device protocol ends by filing a device report through the repo's own report form — the author's reports are disclosed as such and seed the public matrix, and filing them exercises the funnel itself.

Reference numbers, so observations map to the implementation: the simulation ticks every **50 ms** (20 Hz); rate profiles are **slow = 40**, **medium = 90**, **fast = 160** brightness units (0–255) per second, so a full-range ramp takes roughly 6.4 s / 2.8 s / 1.6 s; dimming down floors at brightness 1 (the lowest on-level) and must never turn the light off.

## Test fleet

| Platform | Hardware |
|---|---|
| ESPHome | Martin Jerry MJ-SD01 ×2 (triac dimmers, real loads) |
| ESPHome | Athom E27 15W high-lumen bulb |
| Tasmota | Gosund WP6 plug |
| Tasmota | LB01-15W-E27 bulb |
| Zigbee2MQTT | Gledopto USB Mini LED Controller RGB+CCT |
| Matter | Leedarson Smart RGBTW bulb |
| WiZ (local UDP) | HALO HLB6099WZRGBWMWR wafer downlight ×2 |
| Tuya (cloud) | Tuya LED BULB W509Z1 |
| group | Home Assistant light group |

## Per-device protocol

Run in order on each fleet entry, from Developer Tools → Actions. About ten minutes per device. Record the outcome of each step; "what I expected, what I saw" beats a checkmark.

| # | Step | Expected |
|---|---|---|
| P1 | Set brightness to ~50% with plain `light.turn_on`; note baseline responsiveness | Establishes what "normal" latency looks like on this device |
| P2 | `move` `direction: up`, `rate: medium`; after ~2 s, `stop` | Visibly continuous rise; halts promptly on stop; level holds (no overshoot, no snap-back) |
| P3 | `move` down, `stop` after ~2 s | Same, descending |
| P4 | `move` down and let it run out | Settles at the lowest on-level and **stays on** — never turns off. Record the brightness it lands at |
| P5 | `move` up and let it run out | Tops out at full; the job ends (no continuing writes — confirm via logbook or integration debug logs) |
| P6 | `step` up 5%, `step` down 5% | Two discrete nudges, no drift |
| P7 | Repeat P2 at `rate: slow` and `rate: fast` | Note perceived smoothness at each rate; note whether fast overwhelms the device (queued commands, lag between release and stop, dropped steps) |
| P8 | `move` up, then immediately `move` down with no stop between | Direction reverses cleanly; exactly one job survives (no fighting, no flood) |
| P9 | `stop` while nothing is moving | Silent no-op, no error |
| P10 | Turn the light off, then `move` up | Record what actually happens — this defines the contract for moving an off light, which v0.1a has not pinned down |

After P10: file a device report via the repo's report form with the results.

## System tests — once per instance

| # | Test | Expected |
|---|---|---|
| S1 | Pull power on a device mid-`move` | Job cancels when the entity goes unavailable; no error spam in the log |
| S2 | Restart Home Assistant mid-`move` | Clean restart; no orphaned job, no startup errors from the integration |
| S3 | `move` on a light group entity | All members ramp; record how far they drift out of sync |
| S4 | `move` on a light Adaptive Lighting manages, while AL is active | Record who wins — does AL snap the level back during or after the move? This is the most likely real-world conflict |
| S5 | `move` on a Lightener-wrapped entity, then on its underlying light | Record whether the curve mapping distorts the ramp |
| S6 | Two simultaneous moves, different lights, different rates | Independent jobs; neither starves the other |
| S7 | Watch Zigbee2MQTT logs during a `fast` move on a Zigbee light | Command rate on the mesh, any timeouts or retries — this is the mesh-flooding measurement the simulation-vs-native argument rests on |
| S8 | Full protocol on a cloud-connected light with extra attention to latency | Likely the worst case: record command latency and any rate-limiting; an honest "did not work" here is a finding, not a failure |

## Recording results

One device report per fleet entry, filed through the repo's own issue form, marked as the author's. Aggregate outcomes go in the README capability table once the fleet is done. Raw notes (log excerpts, timings) can live in the report's free-text field; exact model numbers always.
