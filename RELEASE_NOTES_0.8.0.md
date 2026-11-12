# Dynamic Dimming 0.8.0

This release adds no backends. It bounds the traffic the simulated path can generate, fixes several ways a multi-light call could misbehave, and corrects documentation that claimed more than had been shown. It follows an outside review of 0.7.0 that found the fallback path had no protection against overloading a mesh or bridge.

## What was wrong

A native `move` sends one command per light on press and one on release. The simulated fallback did not behave that way: it wrote an absolute level 20 times a second per light, without waiting for the previous write to be acknowledged and without regard to the transport underneath. The README said the fallback "doesn't flood your mesh." On a mesh it could.

Simulation is also reached more often than the backend list suggests: Home Assistant light groups (20 writes a second for every member), ZHA groups (20 Zigbee broadcasts a second), every `fade` on Zigbee2MQTT and Z-Wave JS, and any light a native backend cannot resolve.

## Changes

- **One write in flight.** A simulated ramp waits for each `light.turn_on` to return before sending the next, so a slow integration is written to slowly and no queue of stale levels keeps arriving after release.
- **Latest level wins**, and a level identical to the last one sent is not repeated.
- **Transport budgets.** Simulated writes to Zigbee (ZHA, Zigbee2MQTT, deCONZ), Matter and Hue lights share 5 writes a second per transport; Z-Wave JS shares 1. A light group is charged for every member, and a ZHA or Zigbee2MQTT group for a broadcast. Where the light supports `transition`, each budgeted write carries one.
- **Repeated hold events** no longer re-send a native move that is already running.
- **Concurrent fan-out.** A call naming several lights starts and stops them together. One unreachable device no longer delays the rest, and an error on one light no longer leaves the others ramping.
- **Group overlap.** A target that names a group and its own members drives each bulb once.
- **Zigbee2MQTT groups** are no longer claimed by the native backend, which addressed them at a topic that does not exist. They use budgeted simulation.
- **Input validation.** Infinite, NaN and out-of-range values for `rate`, `step_pct`, `curve`, `brightness_pct` and `duration` are rejected.
- A simulated `fade` stops if its light becomes unavailable, as `move` already did.

## Behavior changes to know about

- An unrecognized `rate` or `curve` name is now an error. It used to fall back to the default silently.
- `backend: native` now fails for `step` and `fade` on a backend with no native form of that verb. It used to simulate silently.
- After a release, a budgeted simulated light can finish the transition it was last given (at most the gap between writes).

## Validation and limits

The local suite passes **396 tests** on Home Assistant **2026.2.3**. Device and network responses are mocked. **No hardware tests were performed for this release**, and the budgets are defaults chosen from published limits, not measurements.

The README now opens with what has been tested on hardware (one ZHA bulb, one Matter bulb) and what has not: **Z-Wave JS, Shelly, Hue, deCONZ and LIFX have never been run on a device in this project.**

Known and not fixed here:

- Hue and deCONZ compute a move from cached brightness, which can be wrong during and just after a transition. A second hold soon after a release may move very little.
- A stop is not ordered against a move still in flight on a slow transport, and native stops are not retried.
- Groups are simulated rather than driven with one native group command.
- Dimming down a light that is off turns it on at minimum under simulation and WiZ.

Requires Home Assistant 2026.2 or later.

## Install or upgrade

In HACS, download **0.8.0** and restart Home Assistant. Existing configuration and automations carry over, subject to the behavior changes above.
