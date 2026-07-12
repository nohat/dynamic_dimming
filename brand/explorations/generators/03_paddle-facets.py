A="#E0912A"; BR="#F3A63C"; DK="#B4701A"   # incandescent / filament / shadow-amber
def led_bar(cx, top, bot, w, n, lit):
    r=w*0.42; gap=w*0.34; seg=(bot-top-(n-1)*gap)/n; out=[]
    for i in range(n):
        y=bot-seg-i*(seg+gap)
        if i<lit: col=BR if i==lit-1 else A; op=1.0
        else: col=A; op=0.20
        out.append(f'<rect x="{cx-w/2:.1f}" y="{y:.1f}" width="{w:.1f}" height="{seg:.1f}" rx="{r:.1f}" fill="{col}" fill-opacity="{op}"/>')
    return "\n  ".join(out)

# paddle geometry
PX,PY,PW,PH,PR = 212,70,248,372,58
cx = PX+PW/2                 # 336
split = PY+PH/2              # 256
L, R = PX+30, PX+PW-30       # crease leg x
apexTop=(cx,150); apexBot=(cx,362)
craeseTopY=205; creaseBotY=307

DEFS=f'''<defs>
    <radialGradient id="g" cx="30%" cy="55%" r="60%">
      <stop offset="0%" stop-color="{BR}" stop-opacity="0.38"/><stop offset="70%" stop-color="{A}" stop-opacity="0.07"/><stop offset="100%" stop-color="{A}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="tf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BR}" stop-opacity="0.60"/><stop offset="1" stop-color="{DK}" stop-opacity="0.42"/></linearGradient>
    <linearGradient id="bf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{DK}" stop-opacity="0.42"/><stop offset="1" stop-color="{BR}" stop-opacity="0.60"/></linearGradient>
    <clipPath id="pad"><rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="{PR}"/></clipPath>
  </defs>'''
def head(): return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">\n  {DEFS}\n  <circle cx="150" cy="256" r="184" fill="url(#g)"/>\n  {led_bar(118,70,442,78,6,4)}\n'
def foot(): return '</svg>\n'
outline = f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="{PR}" fill="none" stroke="{A}" stroke-opacity="0.7" stroke-width="8"/>'
splitline = f'<line x1="{PX+14}" y1="{split}" x2="{PX+PW-14}" y2="{split}" stroke="{A}" stroke-opacity="0.42" stroke-width="6"/>'
creaseTop = f'<path d="M {L} {craeseTopY} L {apexTop[0]} {apexTop[1]} L {R} {craeseTopY}" fill="none" stroke="{A}" stroke-opacity="0.42" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
creaseBot = f'<path d="M {L} {creaseBotY} L {apexBot[0]} {apexBot[1]} L {R} {creaseBotY}" fill="none" stroke="{A}" stroke-opacity="0.42" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'

# ---- 1) DEPTH / SHADING only: soft molded rocker via vertical gradients + ridge highlight, no crease lines ----
shade_fill = f'''<g clip-path="url(#pad)">
    <rect x="{PX}" y="{PY}" width="{PW}" height="{PH/2}" fill="url(#tf)"/>
    <rect x="{PX}" y="{split}" width="{PW}" height="{PH/2}" fill="url(#bf)"/>
    <rect x="{PX}" y="{split-4}" width="{PW}" height="8" fill="{BR}" fill-opacity="0.85"/>
  </g>'''
open('/tmp/pA1.svg','w').write(head()+shade_fill+outline+foot())

# ---- 2) FACETS only: flat light fill + crease lines forming the molded facets ----
flat = f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="{PR}" fill="{A}" fill-opacity="0.10"/>'
open('/tmp/pA2.svg','w').write(head()+flat+splitline+creaseTop+creaseBot+outline+foot())

# ---- 3) COMBINED: shaded facet polygons (each plane lit differently) + crease lines + outline ----
# facet polygons clipped to paddle; alternate light/dark so folds read as 3D
poly = f'''<g clip-path="url(#pad)">
    <polygon points="{PX-6},{PY-6} {PX+PW+6},{PY-6} {PX+PW+6},{craeseTopY} {R},{craeseTopY} {cx},{apexTop[1]} {L},{craeseTopY} {PX-6},{craeseTopY}" fill="{BR}" fill-opacity="0.52"/>
    <polygon points="{PX-6},{craeseTopY} {L},{craeseTopY} {cx},{apexTop[1]} {R},{craeseTopY} {PX+PW+6},{craeseTopY} {PX+PW+6},{split} {PX-6},{split}" fill="{DK}" fill-opacity="0.42"/>
    <polygon points="{PX-6},{split} {PX+PW+6},{split} {PX+PW+6},{creaseBotY} {R},{creaseBotY} {cx},{apexBot[1]} {L},{creaseBotY} {PX-6},{creaseBotY}" fill="{DK}" fill-opacity="0.42"/>
    <polygon points="{PX-6},{creaseBotY} {L},{creaseBotY} {cx},{apexBot[1]} {R},{creaseBotY} {PX+PW+6},{creaseBotY} {PX+PW+6},{PY+PH+6} {PX-6},{PY+PH+6}" fill="{BR}" fill-opacity="0.52"/>
    <rect x="{PX}" y="{split-4}" width="{PW}" height="8" fill="{BR}" fill-opacity="0.85"/>
  </g>'''
open('/tmp/pA3.svg','w').write(head()+poly+creaseTop+creaseBot+outline+foot())
print("wrote pA1 pA2 pA3")
