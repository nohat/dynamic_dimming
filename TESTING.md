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
| Matter | Orein bulb (second vendor — see the second-device pass) |
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
| S4 | `move` on a light Adaptive Lighting manages, while AL is active | Record who wins — does AL snap the level back during or after the move? This is the most likely real-world conflict. **Measured on the CA instance, 2026-08-16:** AL wins. Configured `interval: 90`, `transition: 45`, it re-applied `move_to_level_with_on_off(level=2)` about four seconds into a fade and overwrote it. Worse for testing, **turning the light off clears AL's `manual_control` claim** — so claiming manual control before a gesture does not survive an off/on cycle, and AL resumes the moment the light comes back on. Disable the AL switch outright for any run that cycles the light, or results are contaminated |
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

#### Second Matter device — the Orein bulb

The backend is no longer what is under test. The Leedarson run settled the
protocol, the addressing and the integration path, and the one real defect it
found is fixed. What a second vendor's bulb tests is **how much of that was the
Leedarson's behavior rather than Matter's** — every result above that came from
the device rather than the server is, until now, a sample of one.

So this pass is not a repeat. Run the standard per-device protocol P1–P10 and
N1–N3 for the device report, and add the table below. **One pass, through the
deployed integration** — the protocol-only client was worth building when the
backend was unproven, and is now only worth reaching for if something here looks
wrong and you need to separate device from integration.

Record before anything else: **model number, and whether it joined over Thread or
Wi-Fi.** Most inexpensive Matter bulbs are Wi-Fi, and the whole
one-command-instead-of-forty argument is worth far more on Thread than on Wi-Fi.
If both fleet bulbs turn out to be Wi-Fi, the mesh claim is still untested on
Matter, and that should be said plainly rather than assumed from the Zigbee runs.

| # | Step | Expected — and what varies by device |
|---|---|---|
| O1 | Record model, transport (Thread / Wi-Fi), node and endpoint, entity `unique_id`, and color capability | The `unique_id` is the whole addressing scheme. Check it against `parse_unique_id` before anything else: a format mismatch degrades to simulation silently rather than erroring |
| O2 | M2 — `move` down to the rail | **The most device-variable result in the suite.** The Leedarson floors at level 1 and stays lit. Record the exact landing level and whether it stays on. A ZHA bulb in this same fleet switches itself off at level 1 under `WithOnOff`, so "floors and stays lit" is a property of the device and the command variant together, not a guarantee |
| O3 | M3 — `move` up while off | Nothing happens. Spec-defined, but ExecuteIfOff handling is exactly the kind of thing vendors get wrong. If this bulb *does* ramp from off, that is a finding worth writing up |
| O4 | M1 — hold-to-dim, server log at debug | Two `device_command` calls per gesture. Then measure the rate the way the Leedarson was measured: sample the level one second into a `rate: 90` move and check it against `start + 90`. Record whether the device honors the commanded rate, clamps it, or ignores it |
| O5 | `step` up and down | The v0.6.1 fix sends `transitionTime` as an explicit `null`, which means "use the device's own `OnOffTransitionTime`". That attribute is the vendor's choice, so the step may look instant on one bulb and glide on another. Record which, and the step size actually applied |
| O6 | M5 — `fade` over 5 s, with and without `color_temp_kelvin` | One `MoveToLevelWithOnOff` carrying `transitionTime` in tenths, preceded by a zero-transition `MoveToColorTemperature` when a color was asked for. Check the landing level is exact, and note the mireds actually accepted — a CCT-only bulb has a narrower range than the RGBTW, and the backend clamps rather than rejects |
| O7 | **New: does HA's `color_temp_kelvin` follow the fade?** After O6's colored fade, read the entity's `color_temp_kelvin` back and compare against the device | Imported from the ZHA campaign, which found the answer is *no* on that path — the fixture went to the right white while HA reported the previous value for tens of seconds, because color is subscribed on far slower terms than brightness. Matter's subscription model is not Zigbee's attribute reporting, so this may well be fine here. **It has never been checked**, and M5 only ever verified the wire and the level |
| O8 | M4 — release a hold, watch state converge | Record the typical time **and the worst case**. The Leedarson settled the instant `stop` landed. The ZHA run measured 1–2 s typical with a 10 s tail, so sample repeatedly rather than once — a single fast observation says nothing about the tail |
| O9 | M8 — `move` with `backend: simulated` | The 20 Hz `light.turn_on` path drives it instead. Cheap, still unrun on any device, and this is the pass to fold it into |
| O10 | Two Matter lights moving at once, this bulb and the Leedarson, different rates | Independent jobs, neither starving the other, and both ramps smooth. This is S6 on the transport where it matters — two devices sharing one websocket and one fabric |

**M6 and M7 stay out of this pass.** Both need the Matter server stopped, which
takes all 96 Matter entities at the CA house offline, so they want a deliberate
maintenance window rather than a ride-along. They are unfinished business from the
Leedarson run and remain so.

Two carried-over cautions. Drive the services with `entity_id` as a plain
**string**, not a list — see the defect note below. And if this bulb shares an
Adaptive Lighting switch with anything, disable that switch outright for the
duration: S4 records why claiming manual control is not enough.

#### Unrelated defect surfaced by this run

Driving the services through a generic client failed with `invalid_format - value should be a string for dictionary value @ data['entity_id']`. All four services declare `cv.entity_id`, which takes a single string, so any caller passing a list — an automation using `target:`, or most API wrappers — is rejected. Pre-existing, not Matter-specific, and already the subject of a workaround in the CA lighting generator (`build-mv-lighting.py`, "Learned the hard way, 2026-08-07"). Tracked separately.

### WiZ addendum (v0.6.0)

WiZ is the odd one out and needs the most testing per line of code. Every other
native backend hands a ramp to firmware and gets to be small. This one has no
firmware ramp to hand off to — WiZ has no move command and the HA integration
advertises no `TRANSITION` — so it steps the ramp itself and only the
*transport* is native: raw `setPilot` datagrams to UDP 38899, fire-and-forget at
the 20 Hz tick.

That buys a smooth ramp and costs three things nothing else in this integration
has to deal with, and **none of them has ever run on hardware**:

- **A state machine that goes stale on purpose.** Writes bypass `light.turn_on`
  entirely, so Home Assistant believes the old brightness for the whole gesture.
  `async_stop` and `async_step` call `_resync` to re-assert the final level
  through the light entity afterwards. Nothing else here needs reconciling.
- **Group fan-out.** `_hosts` walks a group's members recursively and claims it
  only if *every* leaf is a WiZ bulb, then drives them all from one tick so a
  multi-bulb fixture stays visibly in step. A single non-WiZ member drops the
  whole group to simulation.
- **A hundred-step device.** WiZ `dimming` is 1–100 against Home Assistant's
  0–255, so `to_dimming` quantizes hard. At `rate: slow` the commanded value
  changes roughly sixteen times a second against twenty ticks — many ticks send a
  value identical to the last one. Whether that reads as smooth or as stepping is
  the question the whole perceptual-curve design is trying to answer, and this is
  the only fleet device coarse enough to show it.

Fleet entry: the two HALO HLB6099WZRGBWMWR downlights. **Check which instance
they are on first** — both campaigns so far ran on CA, and the deploy and logging
prerequisites have to be redone if these live on the other box.

#### Prerequisites beyond the usual

- **A packet capture.** `tcpdump -i any -n udp port 38899` on the HA host is the
  only way to see what this backend actually emits — the writes never touch the
  service log. W3 and W4 depend on it.
- **An acknowledged probe.** The backend deliberately discards replies, so it
  cannot tell you whether the firmware *accepted* a datagram. A ten-line script
  that sends one `setPilot` and reads the reply answers W8, which is otherwise
  unanswerable. Expect `{"result":{"success":true}}`.

| # | Step | Expected |
|---|---|---|
| W1 | Confirm both downlights' IPs from their WiZ config entries, and record firmware versions | `_host` reads `CONF_HOST` off the config entry. The docstring's latency figures were measured against SHRGB 1.37/1.38; note whether these match |
| W2 | `move` up with `backend: native`, then `stop` | Ramps and holds. As elsewhere, `native` raising would prove the backend did not claim it |
| W3 | Capture a full `move` gesture | ~20 datagrams per second per bulb, each carrying an **absolute** `dimming` and `"state": true`. Confirm the rate, and that no datagram carries a relative value |
| W4 | In the same capture, count **duplicate** consecutive `dimming` values at `rate: slow` | This is the quantization question. Roughly sixteen distinct values a second against twenty ticks means about a fifth of the datagrams are redundant. Record the real ratio — it bounds how much traffic could be saved by suppressing unchanged writes |
| W5 | Watch the entity's brightness in the UI **during** a move | It should sit visibly stale — that is the design, not a bug. Record how far it diverges by the end of a full-range gesture |
| W6 | Release, then watch it converge | `_resync` re-asserts the last commanded level through `light.turn_on`. Confirm HA catches up, and that the bulb does **not** visibly jump when it lands — the resync writes the value the bulb already has, so it should be invisible |
| W7 | `step` up and down 5% | UDP first for immediate visible change, then `_resync`. Confirm both halves happen and the round trip returns to the starting level |
| W8 | Probe an acknowledged `setPilot` carrying `temp` | Tests a firmware claim `async_fade`'s docstring makes and the backend structurally cannot check: that `temp` alone selects tunable-white mode, and that no key in the payload is unrecognized. **An unrecognized key makes the firmware reject the whole datagram, and a fire-and-forget write would never notice.** Confirm `success: true` for the exact payload the backend sends |
| W9 | `fade` over 5 s with `color_temp_kelvin` | `temp` rides in **every** datagram, not just the first — the opposite of the Zigbee and Matter paths, and for a stated reason: a lost packet is corrected 50 ms later. Confirm in the capture, then confirm the bulb lands on the right white with no flash of the stale one |
| W10 | `fade` to an exact level, then read the entity back | The fade's whole justification is that HA cannot fade a WiZ bulb at all. Confirm it lands **exactly** on target — this path writes absolute values, so unlike the firmware backends it has no excuse for missing |
| W11 | `move` down to the rail at the default `min_brightness: 1` | Record where it bottoms out and whether the bulb is still emitting light. The README claims WiZ's own `minDimLevel` is 10 of 100 — so the bottom third of the default range may be visually dead. This is the claim behind the whole minimum-brightness setting and it has never been checked against these bulbs |
| W12 | Repeat W11 with `min_brightness: 26` | The README's recommended floor. Confirm the bottom of the hold stops looking dead, and that the perceptual curve now spends its travel where the bulb responds |
| W13 | Same hold at `curve: perceptual` and `curve: linear` | WiZ is one of only two paths where `curve` does anything. With 100 levels the difference should be more visible here than anywhere else — linear should race the bottom and crawl the top |
| W14 | Make a light group of **both** downlights; `move` on the group | Claimed as native. One tick fans out to both IPs, so they stay visibly in step. Compare against `backend: simulated` on the same group, which drives per-entity — record how far apart the two bulbs drift |
| W15 | Add any non-WiZ light to that group, `move` again | **Not** claimed. All-or-nothing: one foreign member drops the whole group to simulation rather than driving half of it over UDP. Confirm via the capture that no datagrams go out |
| W16 | Verify `_hosts` actually reads what it thinks | It resolves members from the state attribute `entity_id`. Confirm the group exposes that attribute — if the group platform in use exposes members differently, the group path silently never claims |
| W17 | Power-cycle one bulb mid-`move` | `async_move` bails when `current_brightness` is None, and the tick self-cancels when the entity goes unavailable. Confirm the job ends rather than streaming into the void, and that nothing spams the log |
| W18 | `fade` with color, then `move` on the same bulb without stopping | The move pops `_last_temp`, so the move's resync must **not** re-assert the fade's color. Confirm the bulb keeps its white but the move's level wins |
| W19 | Long soak — three or four full-range gestures back to back | The socket drains at most 32 replies per tick and discards them. Confirm no socket errors, no growing latency, and that the receive buffer does not wedge |

**Traffic note for context.** `async_fade`'s docstring mentions this house has 24
WiZ lamps. Two bulbs at 20 Hz is 40 datagrams a second; a whole-house scene fade
across 24 would be ~480/s from one socket. That is not what this campaign tests,
but W3's measured per-bulb rate is what any such estimate has to be built on.

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

#### ZHA campaign results — 2026-08-16

First hardware run of the ZHA backend, on the CA instance. Home Assistant
2026.8.2, **zigpy 2.1.0**, EZSP coordinator. Pendant: Signify **LTA010** White
Ambiance, unquirked, IEEE `00:17:88:01:0b:20:91:df`, **endpoint 11**, entity
`light.dining_nook_pendant`.

**Ten of sixteen passed. Nothing failed.** Two were skipped for want of hardware
and two were not run for want of approval.

| Steps | Outcome |
|---|---|
| Z1–Z11, Z13 | **pass** (Z13 with a caveat, below) |
| Z12, Z15 | **skipped** — the fleet has no ZHA light without a Color Control cluster, and no ZHA group light |
| Z14, Z16 | **not run** — approval withheld |

What the run converted from assumption to fact, all of it previously mocked:

- The **zigpy parameter names are right**. `move(move_mode=MoveMode.Up, rate=90)`
  came back `DefaultResponse(command_id=1, status=SUCCESS)`; ZHA's coercion layer
  logged `Converted ZCL schema field(move_mode)`. No schema complaint anywhere in
  2922 log lines. Confirmed against zigpy 2.1.0 — the version the names were read
  from — so the contract holds, not its version-independence.
- **`Stop` is accepted with an empty `params` dict.** zigpy fills the optional
  fields, which is what keeps ExecuteIfOff clear.
- **Address parsing works**, and did real work: the endpoint was **11**, not 1,
  so a default would not have matched. All 58 frames targeted `[0x571C:11:…]`.
- **Two frames per gesture**, no `move_to_level` stream. The premise holds.
- **Rate is honored to the unit**: commanded 90, measured 128 → 218 in 1.0 s.
- **`fade` transition time in tenths**: 5 s → `transition_time=50`. With a color,
  `move_to_color_temp` landed 108–112 ms ahead of the level command with
  ExecuteIfOff on both mask and override, and a fade up from off arrived at
  6493 K rather than the stale 2202 K.
- **Z7 is the trade it is documented to be, not a dropped frame.** The `move` was
  delivered and ACKed `SUCCESS` while the off light stayed off. Worth knowing that
  a silent failure and a spec-correct refusal are indistinguishable from entity
  state alone — only the ACK separates them.

Three things to carry forward:

- **Z13's "within a second or so" is accurate on average and optimistic at the
  tail.** Convergence was typically 1–2 s, but one release sat stale for a full
  10 s. The bound is the device's own reporting cadence, not anything the backend
  does.
- **Color temperature does not converge on the same terms.** No Color Control
  attribute reports arrived during the entire campaign; HA kept reporting 2202 K
  while the bulb sat at 6493 K until an explicit read. ZHA configures
  `color_temperature` with a thirty-second minimum interval against
  `current_level`'s one second, and hardware that never binds the report at all
  is common. See `async_fade`'s docstring.
- **`move_to_level_with_on_off(level=1)` switched this bulb off.** Which is the
  argument for the backend's central choice: it is *because* the `WithOnOff`
  variant extinguishes at level 1 that Z6's plain-`Move` floor landing on level 1
  and staying lit is a real result rather than luck. The corollary is that a
  `fade` to zero percent means whatever the device decides.

**Z16 remains the consequential gap.** `_command` passes no context on the
reasoning that a fresh context carries no user id and so clears the admin check
on `issue_zigbee_cluster_command`. That is sound on paper and entirely
unexercised. If it is wrong, the backend does not work from user-owned
automations — the main way anyone would drive hold-to-dim. Z12, Z14 and Z15 also
remain untested on hardware.

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
