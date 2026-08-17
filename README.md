<p align="center">
  <img src="https://raw.githubusercontent.com/nohat/dynamic_dimming/main/brand/icon.png" width="128" alt="Dynamic Dimming">
</p>

<h1 align="center">Dynamic Dimming</h1>

<p align="center">
  <strong>Hold-to-dim for Home Assistant, using the command your light already speaks.</strong>
</p>

<p align="center">
  <a href="https://hacs.xyz/"><img src="https://img.shields.io/badge/HACS-Custom-e0912a.svg?style=flat-square" alt="HACS Custom"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.2%2B-e0912a.svg?style=flat-square" alt="Home Assistant 2026.2+">
  <img src="https://img.shields.io/badge/version-v0.6.1-e0912a.svg?style=flat-square" alt="v0.6.1">
</p>

---

Most lighting protocols have had a hold-to-dim primitive for years: Zigbee's
Level Control `Move`/`Stop`, Z-Wave's `StartLevelChange`/`StopLevelChange`, the
same pair inherited by Matter. Home Assistant has no way to ask for it —
`light.turn_on` sets a target brightness and that is the whole vocabulary — so
the capability sits unused in hardware you already own.

Dynamic Dimming adds the missing verbs: `move` / `stop` / `step` / `fade`. On
**Zigbee2MQTT, Tasmota, Matter, ZHA and Z-Wave JS** it sends the protocol's own
command — one message starts the ramp, one stops it, and the device does the
dimming itself, with no stream of brightness writes crossing your mesh. **WiZ**
has no such command, so it gets a native *transport* instead: still stepped, but
over direct fire-and-forget UDP rather than through `light.turn_on`. Everything
else falls back to stepped simulation on a perceptual curve. Every dimmable
light works — the ones whose protocol supports it work best.

> **v0.6.0 scope:** the six native backends listed below, with stepped
> simulation as the fallback everywhere else. Simulated ramps travel a
> perceptual curve by default, and a higher rate takes bigger steps rather than
> more of them — so even the fallback doesn't flood your mesh. Shelly and Hue
> come next.

## Dimming curve

A ramp that adds a fixed number of brightness units per tick does not *look*
steady. Perceived lightness goes roughly as the cube root of luminance, so a
linear ramp races through the bottom of the range — where a few units are a big
visible change — and then crawls through the top, where they are barely
distinguishable. Held from off to full it reads as "flash, then not much for two
seconds".

Stepped ramps therefore advance at a constant rate through *perceived* lightness,
and let the brightness step vary: tiny near the bottom, large near the top. A hold
takes exactly as long end to end as it used to; the time is just spent where it
is visible. `step` moves by a percentage of perceptual travel too, so one tap is
the same apparent change at either end of the range.

| Setting | Effect |
|---|---|
| `perceptual` (default) | Constant apparent rate. Gamma 3.0, which tracks CIE L\* closely. |
| `linear` | The pre-0.3.0 behavior: constant brightness rate. |
| a number, 1.0–6.0 | Raw gamma, if you want to tune it by eye. |

Set the default in the integration's options, or override per call with the
`curve` field on `move`, `step` and `fade`. It applies only where this
integration steps the ramp itself — the simulation and WiZ paths. Backends that
hand the ramp to device firmware (Zigbee2MQTT, Tasmota, Matter, ZHA, Z-Wave JS)
ignore it: the device's own curve applies, and this integration does not mutate
device config.

### Minimum brightness

The curve travels between a configurable **minimum brightness** and full, rather
than between zero and full. On a device with coarse resolution this matters more
than the curve does: WiZ bulbs accept only 100 discrete levels and report a
minimum usable level of 10 (`minDimLevel` in `getModelConfig`), so a curve
anchored at zero would spend roughly its first third of travel below the point
where the bulb does anything.

If the bottom of a hold looks dead, raise **minimum brightness** to the device's
real floor — 26 on the 0–255 scale is WiZ's declared 10%. The default is 1, which
preserves the full nominal range.

## Installation

Install via [HACS](https://hacs.xyz/) as a custom repository: add
`https://github.com/nohat/dynamic_dimming` as an **Integration** custom
repository, install "Dynamic Dimming", restart Home Assistant, then add the
integration under **Settings → Devices & Services → Add Integration → Dynamic
Dimming**.

## Services

Wire these to a remote's press-and-release events (or call them from Developer
Tools → Actions).

**`dynamic_dimming.move`** — start dimming and keep going until stopped:

```yaml
service: dynamic_dimming.move
data:
  entity_id: light.living_room
  direction: up        # up | down
  rate: medium         # optional: slow | medium | fast, or a number (brightness units/sec)
```

Dimming down bottoms out at the lowest on-level and stays lit (Zigbee "Move"
semantics) — it won't turn the light off. Use `light.turn_off` for that.

**`dynamic_dimming.stop`** — stop an in-progress move, holding the current level:

```yaml
service: dynamic_dimming.stop
data:
  entity_id: light.living_room
```

**`dynamic_dimming.step`** — one relative nudge:

```yaml
service: dynamic_dimming.step
data:
  entity_id: light.living_room
  direction: up        # up | down
  step_pct: 5          # optional, default 5 (% of perceptual travel)
```

**`dynamic_dimming.fade`** — go to an absolute level over a fixed duration:

```yaml
service: dynamic_dimming.fade
data:
  entity_id: light.living_room
  brightness_pct: 40   # 0-100
  duration: 3          # seconds, 0.1-120
  color_temp_kelvin: 2700   # optional; asserted from the first write, not faded
```

Unlike `move`, which is relative and open-ended, `fade` promises a level at a
time — what a scene transition needs. It exists for lights whose platform can't
honor `light.turn_on`'s `transition`, notably WiZ. Backends that can't fade fall
back to simulation, which writes absolute values and so always lands on target.

Every service except `stop` also takes an optional `curve`, and a `backend`
override (`auto`, `native`, `simulated`).

A typical hold-to-dim binding calls `move` on button-hold and `stop` on
button-release.

## Native backends

On platforms whose protocol already has move/stop commands, the integration sends those commands instead of simulating the ramp — one message to start, one to stop, and the device dims itself.

| Platform | How it is driven | Notes |
|---|---|---|
| Zigbee2MQTT | `brightness_move` / `brightness_step` published to the device's `/set` topic | Rate profiles map directly to Z2M's units-per-second. Plain `brightness_move` is used (never `brightness_move_onoff`), so dimming down stops at the lowest on-level. The base topic is configurable in the integration's options if yours is not `zigbee2mqtt`. |
| Tasmota | `Dimmer >` / `Dimmer <` / `Dimmer !` on the device's command topic for move/stop, `Dimmer +` / `Dimmer -` for step | Ramp speed and step size are the device's own `Speed`, `Fade`, and `DimmerStep` settings; the `rate` and `step_pct` fields are ignored on this path, and `Fade 1` must be enabled on the device for a visible ramp. |
| Matter | Level Control cluster `Move` / `Stop` / `Step`, sent as `device_command` calls over the integration's **own websocket** to the Matter server | Home Assistant's Matter integration surfaces no move/stop anywhere — not on the light platform, not as a service, not in its websocket API — so this backend opens its own connection to the same server the Matter config entry points at. Rate profiles map directly to Matter's level-units-per-second. Plain `Move`/`Step` are used (never the `WithOnOff` variants), so dimming down stops at the lowest on-level; the flip side, per the spec's Options handling, is that a `move` on a light that is **off** does nothing. `fade` is native too, as one `MoveToLevelWithOnOff` with a transition time — on Thread that is one command instead of forty. |
| ZHA | Level Control cluster `Move` / `Stop` / `Step`, issued through ZHA's own `zha.issue_zigbee_cluster_command` service | The same cluster the Zigbee2MQTT path drives, so it makes the same choices: rate profiles map directly to level-units-per-second, and plain `Move`/`Step` are used (never the `WithOnOff` variants), so dimming down stops at the lowest on-level and a `move` on a light that is **off** does nothing. No extra connection is needed — ZHA publishes a service that reaches any cluster on any node, so this backend is a service call. `fade` is native too, as one `MoveToLevelWithOnOff` with a transition time. Group lights are not claimed (they need a group command) and fall back to simulation. |
| Z-Wave JS | Multilevel Switch CC `StartLevelChange` / `StopLevelChange`, invoked through the `zwave_js.invoke_cc_api` service | Z-Wave carries no rate — only the time a full-scale sweep should take — so the rate profiles become durations of 6 s, 3 s and 2 s. The encoding is whole seconds, which is coarse, but the device still runs the ramp. Targeting the service by entity is what makes a multi-channel dimmer address its own channel rather than endpoint 0. The command class has **no** relative step, so `step` falls back to a single absolute write, which costs exactly what a native step would have. `fade` also falls back: `Set` with a duration can only express whole seconds, and the fade service promises an exact level at an exact time. |
| WiZ | A stream of absolute `setPilot` datagrams straight to the bulb's IP on UDP 38899, sent fire-and-forget at the tick rate | WiZ firmware has no ramp command and the HA integration doesn't advertise `TRANSITION`, so the ramp still has to be stepped — but not through `light.turn_on`. An acknowledged `setPilot` round-trip measures 38–476 ms (median ~160 ms), which a 20 Hz ramp cannot wait on; the same datagram sent unacknowledged costs ~0.3 ms. Every tick carries an absolute level, so a dropped datagram self-corrects on the next one. A light **group** whose members are all WiZ bulbs is claimed too, and driven from a single tick so the bulbs stay visibly in step. |
| Everything else | Stepped simulation | |

Because the WiZ path bypasses `light.turn_on`, Home Assistant's state machine goes stale while a bulb is moving; `stop` and `step` re-assert the final level through the light entity to put the two back in agreement. The Matter, ZHA and Z-Wave JS paths need no such reconciliation: the device reports its own level and each integration's existing subscription feeds that straight back into Home Assistant.

Selection is automatic. `move`, `step` and `fade` also accept an optional `backend` field (`auto`, `native`, `simulated`): `simulated` forces the stepped path on a natively-supported light, which is useful for comparing behavior, and `native` fails loudly if no native backend supports the light.

## Status

Part of a broader effort to bring native move/stop dimming to the Home Assistant
ecosystem. Requires Home Assistant 2026.2 or newer.
