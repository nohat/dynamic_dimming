# Icon explorations

Working source for the Dynamic Dimming brand mark. The shipped icon (`../icon.svg`
→ `../icon.png` / `../icon@2x.png`) is exploration **A** (`svg/FINAL-A.svg`), a
front-view Decora rocker with a separate LED level column, in incandescent amber
(`#E0912A` / `#F3A63C`). It is a **placeholder**: the flat SVG shading doesn't
convincingly read as 3-D. See the tracking issue for rendering it from a lit 3-D
model instead.

## Generators (`generators/`)

Each is a standalone Python script that emits SVG(s); rasterize with
`rsvg-convert -w 512 -h 512 in.svg -o out.png`.

| File | What it explores |
|------|------------------|
| `01_dial.py` | First direction — a rotary dimmer dial + glow (rejected: dimmers here are paddles/buttons, not dials). |
| `02_led-bar-directions.py` | Vertical LED level bar with paddle / up-down buttons / minimal chevrons. |
| `03_paddle-facets.py` | Paddle with facet creases (shading, crease lines, combined). |
| `04_3d-rocker.py` | Filled 3-D rocker attempts (Inovelli-style soft shadows; side-profile). |
| `05_rocker-corrected.py` | Corrected rocker geometry — top/bottom edges coplanar frontmost, seam recessed; front (**A**, shipped) and side view (**B**). |

## Design constraints (for the 3-D redo)

- **Subject:** a US **Decora rocker dimmer** (~1 : 2 aspect), e.g. Inovelli / Zooz / Martin Jerry SD-01, with a **vertical LED level bar** as a **separate column beside the paddle** (not inset on the paddle face).
- **Rocker geometry:** at rest it's a shallow valley — the **top and bottom edges are the frontmost plane (coplanar)**, each half tilts *out toward its own edge*, and the **center seam is recessed** (in shadow). The top half must not slant the same direction as the bottom.
- **Palette:** monochrome incandescent amber, top-lit; must read on both light and dark backgrounds without a separate dark variant.
- **Output:** square (1:1), trimmed, `icon.png` 256×256 + `icon@2x.png` 512×512, transparent PNG, for the `home-assistant/brands` `custom_integrations/dynamic_dimming/` submission.
- **Legibility:** must hold at ~40 px (the HACS store list size).
