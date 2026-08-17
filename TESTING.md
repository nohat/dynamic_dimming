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
| ZHA | Dining room pendant (CA instance) |
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

## Native-path addendum (v0.1b)

The Zigbee2MQTT and Tasmota entries in the fleet now classify as native: `move` sends one protocol command and the device ramps itself. On each of those entries:

| # | Step | Expected |
|---|---|---|
| N1 | Re-run P2–P4 and P8 with `backend:` omitted | One MQTT command per action in the broker logs, not a 20 Hz stream; the ramp is the device's own; dimming down still floors at the lowest on-level and never turns off |
| N2 | Re-run P2 with `backend: simulated` | The v0.1a tick-loop behavior returns — this is the comparison baseline |
| N3 | `move` with `backend: native` on a light no backend claims | Fails with an error naming the entity; nothing moves |
| N4 | Tasmota only: observe ramp speed across rate profiles | Identical — speed comes from the device's own `Speed`/`Fade` settings; the `rate` field is documented as ignored on this path |

S7 (the mesh-rate measurement) is now the native-vs-simulated comparison it was designed to be: run N1 and N2 back-to-back on the Zigbee2MQTT entry and compare command counts in the Zigbee2MQTT logs.

### Matter addendum (v0.6.0)

The Matter entry classifies as native too, but over a websocket this integration opens itself rather than over MQTT, so it needs its own checks. N1–N3 apply as written; add:

| # | Step | Expected |
|---|---|---|
| M1 | Hold-to-dim with the Matter server's log at debug | Exactly two `device_command` calls per gesture — `Move` on press, `Stop` on release — and no stream of `MoveToLevel` writes |
| M2 | Let a `move` run to the bottom of the range | The light floors at its minimum on-level and stays lit; it never switches off |
| M3 | `move` up on a Matter light that is **off** | Nothing happens. Plain `Move` leaves ExecuteIfOff clear, which is the spec-correct behavior and the same trade the Zigbee2MQTT path makes |
| M4 | Release the button, then check the HA state | Brightness converges on its own within a second or so — the device reports `CurrentLevel` and the Matter integration's subscription carries it back. There is no resync call to look for |
| M5 | `fade` to an absolute level over 5 s, with and without `color_temp_kelvin` | One `MoveToLevelWithOnOff` carrying `transitionTime`, preceded by a zero-transition `MoveToColorTemperature` when a color was asked for; the device runs the whole ramp |
| M6 | Stop the Matter server add-on, then hold to dim | Nothing moves and one warning is logged — not one per press. Restart the add-on and hold again: it reconnects without reloading the integration |
| M7 | Disable the Matter integration, then hold to dim a Matter light | Falls back to stepped simulation through `light.turn_on` rather than going dead |

### ZHA addendum (v0.6.0)

ZHA is the first backend that drives another integration's **public service**
rather than a transport this integration owns. Nothing is sent on a socket we
opened; every command is a `zha.issue_zigbee_cluster_command` call whose `params`
dict is handed straight to zigpy's command schema. So the failure modes are not
about the mesh — they are about that contract, and none of it has run against
real hardware. The field names (`move_mode`/`rate`, `step_mode`/`step_size`/
`transition_time`) were read out of zigpy 2.1.0's `LevelControl.ServerCommandDefs`
and never sent. **Z1 and Z4 are the two steps that could invalidate the backend;
run them first.**

Fleet entry for this pass: the **dining room pendant** (CA instance).

| # | Step | Expected |
|---|---|---|
| Z1 | Pre-flight. From the ZHA device page record make/model and IEEE; from **download diagnostics** record the light entity's `unique_id` and its endpoint | The backend takes the IEEE from the device registry and the endpoint from the segment after it in the `unique_id`. If that string is not `<ieee>-<endpoint>`, **stop and report it** — address parsing is the piece with no live coverage, and a quirked or multi-endpoint device is where it would break |
| Z2 | `move` up with `backend: native` | Succeeds and the pendant ramps. Doubles as the classification probe: `native` raises a `ServiceValidationError` naming the entity when nothing claimed it, so a quiet success proves `ZhaBackend` owns this light |
| Z3 | Add `zigpy.zcl: debug` to `logger:`, then one press-and-release hold | Exactly **two** outbound frames per gesture — `move` on press, `stop` on release. A stream of `move_to_level` writes means it silently fell through to simulation |
| Z4 | In the same log, confirm the command was accepted, not rejected | The highest-risk item. A `TypeError`, `KeyError` or schema complaint from `zha`/`zigpy` means the installed zigpy names these fields differently than 2.1.0 does. Capture the exact traceback — it names the expected fields |
| Z5 | Re-run P2–P4 and P8 with `backend:` omitted | Same results as the Zigbee2MQTT entry: one command per action, the device's own ramp, direction reverses cleanly with exactly one job alive |
| Z6 | `move` down and let it run out | Floors at the lowest on-level and **stays lit**. Record the landing brightness |
| Z7 | `move` up on a pendant that is **off** | Nothing happens. Plain `Move` leaves ExecuteIfOff clear — spec-correct, and the same trade the Zigbee2MQTT and Matter paths make |
| Z8 | `stop` while nothing is moving | Silent no-op. Also the only live check that `Stop` is accepted with an **empty** `params` dict; its schema is all-optional, which no test covers |
| Z9 | `step` up 5%, `step` down 5% | Two discrete nudges, no drift. `transition_time` is 0 so it snaps — compare against the Z2M entry, which puts the same 0 on the wire. They should feel identical; if they don't, the two Zigbee paths have diverged |
| Z10 | Repeat Z5 at `rate: slow` and `rate: fast` | Roughly 6.4 s / 1.6 s full-range. The device applies its own curve, so it need not feel linear — note whether `fast` outruns the pendant |
| Z11 | `fade` to an absolute level over 5 s, with and without `color_temp_kelvin` | One `move_to_level_with_on_off` carrying `transition_time` in **tenths** of a second. With a color asked for, a zero-transition `move_to_color_temp` lands **first**, carrying ExecuteIfOff so a fade up from off arrives at the right white rather than flashing the last one |
| Z12 | `fade` with `color_temp_kelvin` on a light with no Color Control cluster, if the fleet has one | The color command fails, exactly one warning is logged, and **the ramp still runs**. This is the bounded-timeout path and it has only synthetic coverage |
| Z13 | Release a hold, then watch the HA state | Brightness converges on its own within a second or so, from the device's own `current_level` report. There is no resync call to look for — unlike WiZ |
| Z14 | Disable the ZHA integration, then hold to dim | Falls back to stepped simulation through `light.turn_on` rather than going dead. The backend treats the service's absence as the liveness check |
| Z15 | `move` on a ZHA **group** light | Not claimed — falls back to simulation. Groups need `issue_zigbee_group_command`, which this backend does not send |
| Z16 | Trigger `move` from an automation owned by a **non-admin** user | Still dims. `issue_zigbee_cluster_command` is an admin service and this backend deliberately passes no context, so the admin check sees no user id. If this fails, the backend is unusable from user-facing automations |

S7's mesh-rate measurement now has a third arm: run Z3 against the Z2M entry's
N1 and the same light under `backend: simulated`, and compare frame counts for
an identical gesture.

## Recording results

One device report per fleet entry, filed through the repo's own issue form, marked as the author's. Aggregate outcomes go in the README capability table once the fleet is done. Raw notes (log excerpts, timings) can live in the report's free-text field; exact model numbers always.
