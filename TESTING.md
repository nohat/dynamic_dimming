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
| M7 | Restart Home Assistant with the Matter **server** unreachable but the config entry still present, then hold to dim | The entity is unavailable, so `move` is dropped at the capability gate with a debug line and no traceback. Nothing hangs and nothing warns per press |
| M8 | With Matter healthy, `move` with `backend: simulated` on the same light | The 20 Hz `light.turn_on` path drives it instead. This is the fallback `claims()` guards, and the only way to exercise it deliberately |

**M7 replaces an earlier version that expected the wrong thing.** It used to read "disable the Matter integration, then hold to dim — falls back to stepped simulation." That cannot happen. Disabling the integration takes its entities with it: the entity goes unavailable, `classify` finds no `supported_color_modes`, returns `UNSUPPORTED`, and `move` is dropped before any backend is consulted. There is nothing left for simulation to drive.

The fallback `claims()` actually guards is narrower — Matter's *registry entries* outliving the loaded integration, which is a startup-ordering window rather than a state a user can sit in. M7 as rewritten covers the reachable half of that (unavailable entity, no crash), and M8 covers the simulation path the honest way, through the `backend` override.

#### Results — 2026-08-16, CA House

Run against **Stairs sconce bulb** (`light.mv_str_sconces`, Leedarson RGBTW, node 18 / endpoint 1) on **matter-server 1.4.0 (matter.js 0.17.9), schema 13**.

**Method, in two passes.** Worth repeating for any future backend, because the first pass costs nothing and catches the expensive class of bug.

*Pass 1 — protocol only, nothing installed.* A stdlib websocket client run from the SSH add-on, sending byte-for-byte the payloads the backend emits. `core-matter-server:5580` only resolves inside the box, so this has to run there, but it needs no deploy and no restart. M1, M2, M3 and M5 were confirmed against real hardware before the integration shipped anywhere — and this is the pass that found the `Step` defect.

*Pass 2 — through the integration.* v0.6.1 installed to `/config/custom_components`, HA restarted, then the real services driven over the websocket API. This is the only pass that can answer M4, because state convergence is a property of the integration, not the protocol.

Two practical notes for anyone repeating this. Read the target's `unique_id` out of the entity registry first (`config/entity_registry/get`) and check your address parser against it — that one string is the whole addressing scheme, and a format mismatch means silent degradation to simulation rather than an error. And drive the services with `entity_id` as a plain **string**; a list is rejected (see the defect note below).

| # | Result |
|---|---|
| M1 | **Pass.** Two commands per gesture. Level 40 → 133 after 1 s of `Move` (rate 90), 227 at `Stop`, still 227 two seconds later — the device ramped on its own and `Stop` held it |
| M2 | **Pass.** `Move` down to the rail floors at level **1** with `OnOff` still true; never switched off |
| M3 | **Pass.** `Move` up on an off light is accepted by the server and does nothing — level unchanged, light stays off. ExecuteIfOff clear behaves as the spec says |
| M5 | **Pass.** `MoveToColorTemperature` (370 mireds, ExecuteIfOff) then one `MoveToLevelWithOnOff(level=254, transitionTime=30)`; mid-fade 171, landed exactly on 254 in ~3 s |
| Step | **Failed, then fixed.** See below |

**The one real defect, and it only shows on hardware.** `transitionTime` is a *mandatory* field that happens to be nullable. Omitting the key relies on the server filling the default in — which python-matter-server does and **matter.js does not**: it answers `ValidationMandatoryFieldMissingError` and the step never reaches the device. A fake-server unit test could not have caught this, because the fake accepted whatever it was handed. Probing the four variants against the real server settled it:

| `transitionTime` | Server | Device |
|---|---|---|
| omitted | rejected | no change |
| `null` | accepted | level 100 → 113 |
| `0` | accepted | level 100 → 113 |
| `10` | accepted | level 100 → 113 |

Fixed in v0.6.1 by sending an explicit `null`, which both server implementations accept and which is the spec's way of saying "use the device's own `OnOffTransitionTime`". Re-ran the suite afterwards: **13/13**.

#### In-Home-Assistant results — v0.6.1 deployed to CA

v0.6.1 installed to `/config/custom_components` and HA restarted (2026.8.2). The integration loaded clean — no `dynamic_dimming` or `matter` entries in the error log — and registered all four services.

| # | Result |
|---|---|
| Claim | **Pass.** `move` with `backend: native` is accepted rather than raising, so the Matter backend claims the entity through the real registry |
| M4 | **Pass.** Brightness 100 → 255 during a 2 s hold, settled the instant `stop` landed and held across six samples. HA's state machine converged on its own — the resync the WiZ path needs is genuinely unnecessary here |
| Step | **Pass** through the deployed service: 255 → 230 |
| Fade | **Pass** through the deployed service: gradual (238 mid-fade), landed on 255 |

**M6, M7 and M8 remain unrun.** M6 and M7 both need the Matter server stopped, which takes all 96 Matter entities at this house — kitchen pendants included — offline for the duration, so they want a deliberate maintenance window. M8 is cheap and should be folded into the next pass.

M7's premise was found wrong by reading `capability.classify` while planning the run, not by running it — the table above carries the corrected version and the reasoning. It is still unverified either way.

#### Unrelated defect surfaced by this run

Driving the services through a generic client failed with `invalid_format - value should be a string for dictionary value @ data['entity_id']`. All four services declare `cv.entity_id`, which takes a single string, so any caller passing a list — an automation using `target:`, or most API wrappers — is rejected. Pre-existing, not Matter-specific, and already the subject of a workaround in the CA lighting generator (`build-mv-lighting.py`, "Learned the hard way, 2026-08-07"). Tracked separately.

## Recording results

One device report per fleet entry, filed through the repo's own issue form, marked as the author's. Aggregate outcomes go in the README capability table once the fleet is done. Raw notes (log excerpts, timings) can live in the report's free-text field; exact model numbers always.

## Proposal: let other people run this without reading this document

*Not built yet — a design, written down while the Matter run was fresh.*

Everything above assumes the tester is the author: SSH to the box, a hand-written websocket client, `docker stop` on an add-on. Nobody testing a Z-Wave dimmer for the first time is going to do that, and the reports that matter most come from hardware nobody here owns. Three pieces, in increasing order of effort, each useful alone.

### 1. `diagnostics.py` — the support bundle, for free

Home Assistant already has this: implement `async_get_config_entry_diagnostics` and a **Download diagnostics** button appears on the integration's page. No UI to build, no new service, and users already know the button from filing bugs against core integrations.

What it should dump, per light entity:

```
entity_id, platform, classification (NATIVE/SIMULATED/UNSUPPORTED),
claiming_backend, supported_color_modes, supported_features, brightness
```

plus, per backend, *why* an entity resolved or did not. That last part is the whole value. Today `claims()` returns a bare `False` and the user has no way to learn whether their Z-Wave dimmer was skipped because the service was missing, the platform did not match, or the `unique_id` carried no value id. Each backend should be able to answer "not mine, because —" in one string:

| Backend | Reports |
|---|---|
| Matter | node id, endpoint id, whether the config entry had a URL, whether the `unique_id` parsed |
| Z-Wave JS | whether `zwave_js.invoke_cc_api` is registered, whether the `unique_id` carries a value id |
| ZHA | whether `zha.issue_zigbee_cluster_command` is registered, IEEE found, endpoint parsed |
| Zigbee2MQTT | whether MQTT is loaded, whether a `zigbee2mqtt_` identifier was found |
| Tasmota | whether the discovery topic yielded a command prefix |
| WiZ | whether a host resolved; for a group, which member broke the all-or-nothing rule |

Redact addresses through `homeassistant.components.diagnostics.async_redact_data` — IEEE, IP, node id are all identifying.

### 2. `dynamic_dimming.diagnose` — run the protocol for them

A service taking one `entity_id` that performs the per-device protocol automatically and records what happened, rather than asking a human to eyeball it:

1. sample `brightness` every 100 ms throughout
2. `move` up 2 s → `stop` → settle 2 s
3. `move` down to the rail → confirm it floors above zero and stays on
4. `step` down, `step` up
5. `fade` to 50% over 3 s
6. return the light to where it started

Then score it against the same things the M-table checks by hand: did it move, did `stop` hold, did it floor above zero without switching off, did HA's state converge without a resync, was the ramp continuous or visibly stepped. Output a verdict plus the raw `(t, brightness)` trace.

This is also the honest way to measure the thing the whole project rests on — command count. A native backend should produce two commands per gesture and simulation forty; the trace shows which happened without anyone reading broker logs.

Emit the result as a persistent notification (so it is visible immediately) and write the full JSON next to the config so it can be attached.

### 3. The pre-filled report link

The notification ends with a link that opens the device report with the machine-knowable parts already filled.

**The constraint that shapes this:** GitHub issue-form prefill works for `input` and `textarea` fields only. `dropdown` and `checkboxes` are *not* prefillable ([community #5288](https://github.com/orgs/community/discussions/5288), [#32200](https://github.com/orgs/community/discussions/32200)) — and today's form uses a dropdown for `integration`, a dropdown for `result`, and checkboxes for `tried`. So "every field pre-filled" is not reachable with the form as written. Two changes make it reachable:

- Turn `integration` into an `input`. The backend knows the platform exactly; a dropdown only invites the user to get it wrong.
- Drop the `tried` checkboxes. `diagnose` tried *all* of them, and the trace says so more reliably than a human ticking boxes.

Keep `result` a dropdown. That one is a judgment call — "looked smooth to me" is information the trace does not carry, and it is the one thing worth making a person answer.

Add one `textarea` with id `diagnostics` for the generated block, and the URL becomes:

```
https://github.com/nohat/dynamic_dimming/issues/new
  ?template=device-report.yml
  &title=%5Bdevice%5D+Inovelli+LZW31-SN+%28Z-Wave+JS%29
  &integration=Z-Wave+JS
  &device=Inovelli+LZW31-SN
  &ha_version=2026.8.2
  &diagnostics=<urlencoded block>
```

with the block itself compact and readable, something like:

```yaml
dynamic_dimming: 0.6.1        home_assistant: 2026.8.2
entity: light.hall            platform: zwave_js
classification: NATIVE        backend: ZwaveJsBackend
verdict: moved=yes stop=held floor=1(on) converged=yes commands=2
trace: 100,118,141,167,196,228,254,254,254
notes: rate profile "medium" -> duration 3s (full-scale sweep)
```

Mind the length: GitHub answers `414 URI Too Long` past a few kilobytes, so the trace has to be decimated (every Nth sample, or just the inflection points) and the full JSON left as a manual attachment. The link carries enough to triage; the diagnostics download carries enough to debug.

### Why this order

Piece 1 alone would have shortened the Matter work — "why didn't it claim my light" is the first question every new backend raises, and it is currently unanswerable without a debugger. Piece 2 is what makes a report comparable across houses. Piece 3 is polish, and it is worth doing only after 1 and 2 exist, because a pre-filled link to a report with nothing in it is just a shorter way to file a vague issue.
