<p align="center">
  <img src="https://raw.githubusercontent.com/nohat/dynamic_dimming/main/brand/icon.png" width="128" alt="Dynamic Dimming">
</p>

<h1 align="center">Dynamic Dimming</h1>

<p align="center">
  <strong>Hold-to-dim for Home Assistant lights that don't support it natively.</strong>
</p>

<p align="center">
  <a href="https://hacs.xyz/"><img src="https://img.shields.io/badge/HACS-Custom-e0912a.svg?style=flat-square" alt="HACS Custom"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.2%2B-e0912a.svg?style=flat-square" alt="Home Assistant 2026.2+">
  <img src="https://img.shields.io/badge/version-v0.1a-e0912a.svg?style=flat-square" alt="v0.1a">
</p>

---

Home Assistant's `light.turn_on` only sets a target brightness; there's no
"start dimming / stop dimming" the way a wall dimmer works. Dynamic Dimming adds
its own `move` / `stop` / `step` services and, for **v0.1a**, implements them by
*simulation*: it steps `light.turn_on` at a fixed rate on a timer until you stop
it or it reaches the end. That works with any dimmable light entity today. Native
protocol backends (Zigbee2MQTT move/stop, etc.) are planned for later versions.

> **v0.1a scope:** simulation only. Higher rates take bigger steps (not more
> commands), so a hold doesn't flood your mesh. Linear ramp; perceptual curves
> and native backends come later.

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
  step_pct: 5          # optional, default 5 (% of full scale)
```

A typical hold-to-dim binding calls `move` on button-hold and `stop` on
button-release.

## Native backends

On platforms whose protocol already has move/stop commands, the integration sends those commands instead of simulating the ramp — one message to start, one to stop, and the device dims itself.

| Platform | How it is driven | Notes |
|---|---|---|
| Zigbee2MQTT | `brightness_move` / `brightness_step` published to the device's `/set` topic | Rate profiles map directly to Z2M's units-per-second. Plain `brightness_move` is used (never `brightness_move_onoff`), so dimming down stops at the lowest on-level. The base topic is configurable in the integration's options if yours is not `zigbee2mqtt`. |
| Tasmota | `Dimmer >` / `Dimmer <` / `Dimmer !` on the device's command topic for move/stop, `Dimmer +` / `Dimmer -` for step | Ramp speed and step size are the device's own `Speed`, `Fade`, and `DimmerStep` settings; the `rate` and `step_pct` fields are ignored on this path, and `Fade 1` must be enabled on the device for a visible ramp. |
| Everything else | Stepped simulation (unchanged from v0.1a) | |

Selection is automatic. The `move` and `step` services also accept an optional `backend` field (`auto`, `native`, `simulated`): `simulated` forces the stepped path on a natively-supported light, which is useful for comparing behavior, and `native` fails loudly if no native backend supports the light.

## Brand

The mark is a US **Decora rocker dimmer** with a vertical LED level bar, in
incandescent amber (`#E0912A` / `#F3A63C`). Source and exports live in
[`brand/`](brand/); the working directions and their generators are in
[`brand/explorations/`](brand/explorations/). Original artwork; not derived from
Home Assistant branding.

The current icon is a **placeholder** — flat SVG shading doesn't read
convincingly as 3-D. Rendering it from a lit 3-D model is tracked in
[#1](https://github.com/nohat/dynamic_dimming/issues/1).

## Status

Part of a broader effort to bring native move/stop dimming to the Home Assistant
ecosystem. Requires Home Assistant 2026.2 or newer.
