# Dynamic Dimming

A Home Assistant custom integration that adds **hold-to-dim** control — start
dimming, stop when you let go — to lights that don't expose it natively.

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

## Status

Part of a broader effort to bring native move/stop dimming to the Home Assistant
ecosystem. Requires Home Assistant 2026.2 or newer.
