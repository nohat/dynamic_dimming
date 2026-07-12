import math
S=512; cx=cy=256
R=228; sw=54            # ring fills the canvas: outer = R+sw/2 = 251 (~5px margin)
amber="#E0912A"; amber_bright="#F3A63C"
def pt(a,r=R):
    t=math.radians(a); return (cx+r*math.cos(t), cy+r*math.sin(t))
def f(v): return f"{v:.2f}"
# active level: from top (270 = -90) clockwise 62% of 360
start=270.0; frac=0.62; span=360*frac
end=start+span
sx,sy=pt(start); ex,ey=pt(end)
large=1 if span>180 else 0
svg=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}">
  <defs>
    <radialGradient id="glow" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="{amber_bright}" stop-opacity="0.45"/>
      <stop offset="65%" stop-color="{amber}" stop-opacity="0.10"/>
      <stop offset="100%" stop-color="{amber}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="bulb" cx="42%" cy="36%" r="72%">
      <stop offset="0%" stop-color="{amber_bright}"/>
      <stop offset="100%" stop-color="{amber}"/>
    </radialGradient>
  </defs>
  <circle cx="{cx}" cy="{cy}" r="150" fill="url(#glow)"/>
  <!-- full ring track -->
  <circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{amber}" stroke-opacity="0.26" stroke-width="{sw}"/>
  <!-- active level arc from top, clockwise -->
  <path d="M {f(sx)} {f(sy)} A {R} {R} 0 {large} 1 {f(ex)} {f(ey)}"
        fill="none" stroke="{amber}" stroke-width="{sw}" stroke-linecap="round"/>
  <!-- handle knob -->
  <circle cx="{f(ex)}" cy="{f(ey)}" r="{sw*0.60:.1f}" fill="{amber_bright}"/>
  <circle cx="{f(ex)}" cy="{f(ey)}" r="{sw*0.28:.1f}" fill="#FFFFFF" fill-opacity="0.92"/>
  <!-- central light -->
  <circle cx="{cx}" cy="{cy}" r="72" fill="url(#bulb)"/>
  <circle cx="{cx-22}" cy="{cy-24}" r="22" fill="#FFFFFF" fill-opacity="0.30"/>
</svg>
'''
open('/Users/nohat/iot/dimming/dynamic_dimming/brand/icon.svg','w').write(svg)
print("active end:", f(ex), f(ey), "large", large)
