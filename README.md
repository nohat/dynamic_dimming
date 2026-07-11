# Dynamic Dimming

A Home Assistant custom integration that provides smooth, continuous
dimming ("move to X over time") and step-dimming services for lights that
don't natively support them. v0.1a is simulation-only: it drives
brightness via repeated `light.turn_on` calls on a timer rather than any
native ramp protocol, so it works with any dimmable light entity today.

## Installation

Install via [HACS](https://hacs.xyz/) as a custom repository:
add `https://github.com/nohat/dynamic_dimming` as an "Integration" custom
repository, then install "Dynamic Dimming" and restart Home Assistant.
