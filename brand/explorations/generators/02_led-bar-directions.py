import base64
A="#E0912A"; BR="#F3A63C"     # incandescent / filament
def led_bar(cx, top, bot, w, n, lit, r=None):
    """vertical stack of n rounded segments, bottom-up; `lit` filled bright, rest faint."""
    r = r if r else w*0.42
    gap = w*0.34
    seg = (bot-top - (n-1)*gap)/n
    out=[]
    for i in range(n):            # i=0 bottom
        y = bot - seg - i*(seg+gap)
        if i < lit:
            col = BR if i==lit-1 else A      # top-lit segment = brightest (current level)
            op = 1.0
        else:
            col = A; op = 0.20
        out.append(f'<rect x="{cx-w/2:.1f}" y="{y:.1f}" width="{w:.1f}" height="{seg:.1f}" rx="{r:.1f}" fill="{col}" fill-opacity="{op}"/>')
    return "\n    ".join(out)

def chevron(cx, cy, w, h, up, sw, col=A):
    if up:
        d=f"M {cx-w/2:.1f} {cy+h/2:.1f} L {cx:.1f} {cy-h/2:.1f} L {cx+w/2:.1f} {cy+h/2:.1f}"
    else:
        d=f"M {cx-w/2:.1f} {cy-h/2:.1f} L {cx:.1f} {cy+h/2:.1f} L {cx+w/2:.1f} {cy-h/2:.1f}"
    return f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>'

DEFS='''<defs>
    <radialGradient id="g" cx="50%" cy="60%" r="55%">
      <stop offset="0%" stop-color="#F3A63C" stop-opacity="0.40"/>
      <stop offset="70%" stop-color="#E0912A" stop-opacity="0.08"/>
      <stop offset="100%" stop-color="#E0912A" stop-opacity="0"/>
    </radialGradient>
  </defs>'''

def wrap(inner):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">\n  {DEFS}\n  {inner}\n</svg>\n'

# ---- Variant A: paddle (rocker) + side LED bar  (Inovelli / Zooz / Martin Jerry) ----
A_inner = f'''<circle cx="150" cy="256" r="180" fill="url(#g)"/>
  {led_bar(cx=118, top=70, bot=442, w=78, n=6, lit=4)}
  <rect x="212" y="70" width="248" height="372" rx="60" fill="{A}" fill-opacity="0.10" stroke="{A}" stroke-opacity="0.55" stroke-width="7"/>
  <line x1="212" y1="256" x2="460" y2="256" stroke="{A}" stroke-opacity="0.32" stroke-width="6"/>'''
open('/tmp/vA.svg','w').write(wrap(A_inner))

# ---- Variant B: up/down buttons + LED bar  (Pico / two-button) ----
B_inner = f'''<circle cx="326" cy="256" r="180" fill="url(#g)"/>
  {led_bar(cx=120, top=76, bot=436, w=80, n=6, lit=4)}
  <rect x="238" y="76" width="216" height="168" rx="46" fill="{A}" fill-opacity="0.10" stroke="{A}" stroke-opacity="0.55" stroke-width="7"/>
  {chevron(346, 150, 96, 60, up=True, sw=30)}
  <rect x="238" y="268" width="216" height="168" rx="46" fill="{A}" fill-opacity="0.10" stroke="{A}" stroke-opacity="0.55" stroke-width="7"/>
  {chevron(346, 354, 96, 60, up=False, sw=30)}'''
open('/tmp/vB.svg','w').write(wrap(B_inner))

# ---- Variant C: bold LED level bar + chevrons, no frame (minimal) ----
C_inner = f'''<circle cx="256" cy="256" r="190" fill="url(#g)"/>
  {chevron(256, 74, 150, 66, up=True, sw=34, col=A)}
  {led_bar(cx=256, top=150, bot=362, w=150, n=4, lit=3, r=30)}
  {chevron(256, 438, 150, 66, up=False, sw=34, col=A)}'''
open('/tmp/vC.svg','w').write(wrap(C_inner))
print("wrote vA vB vC")
