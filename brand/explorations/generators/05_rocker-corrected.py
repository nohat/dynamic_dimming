HI="#FBCB78"; EDGE="#FFE6B4"; LIT="#F0A544"; BASE="#DA8A26"; SH="#A5640F"; DEEP="#6E430A"; GLOW="#F3A63C"
def led(cx, top, bot, w, n, lit):
    r=w*0.40; gap=w*0.30; seg=(bot-top-(n-1)*gap)/n; out=[]
    for i in range(n):
        y=bot-seg-i*(seg+gap)
        col,op=(HI if i==lit-1 else LIT,1.0) if i<lit else (DEEP,0.6)
        out.append(f'<rect x="{cx-w/2:.1f}" y="{y:.1f}" width="{w:.1f}" height="{seg:.1f}" rx="{r:.1f}" fill="{col}" fill-opacity="{op}"/>')
    return "\n    ".join(out)

# Decora paddle ~ 1 : 2.03.  front face:
FX,FW = 150,196; FY,FH = 54,404; FR=26
seam=FY+FH/2                      # 256
tSeam=seam-9; bSeam=seam+9        # seam gap
DEFS=f'''<defs>
  <filter id="soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="5"/></filter>
  <clipPath id="ff"><rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="{FR}"/></clipPath>
  <linearGradient id="top" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{HI}"/><stop offset="0.62" stop-color="{LIT}"/><stop offset="1" stop-color="{SH}"/></linearGradient>
  <linearGradient id="bot" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{SH}"/><stop offset="0.38" stop-color="{LIT}"/><stop offset="1" stop-color="{HI}"/></linearGradient>
  <linearGradient id="sideg" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{DEEP}"/><stop offset="1" stop-color="{SH}"/></linearGradient>
  <radialGradient id="gl" cx="46%" cy="52%" r="60%"><stop offset="0" stop-color="{GLOW}" stop-opacity="0.32"/><stop offset="100%" stop-color="{GLOW}" stop-opacity="0"/></radialGradient>
  </defs>'''

def front_and_led(with_side_x_shift=0):
    fx=FX+with_side_x_shift
    s=f'''<g clip-path="url(#ff)" transform="translate({with_side_x_shift},0)">
    <rect x="{FX}" y="{FY}" width="{FW}" height="{FH/2}" fill="url(#top)"/>
    <rect x="{FX}" y="{seam}" width="{FW}" height="{FH/2}" fill="url(#bot)"/>
    <!-- crisp coplanar edge highlights (top & bottom, frontmost) -->
    <rect x="{FX}" y="{FY}" width="{FW}" height="10" fill="{EDGE}" fill-opacity="0.9"/>
    <rect x="{FX}" y="{FY+FH-10}" width="{FW}" height="10" fill="{EDGE}" fill-opacity="0.9"/>
    <!-- recessed centre seam shadow -->
    <rect x="{FX}" y="{tSeam-6}" width="{FW}" height="30" fill="{DEEP}" fill-opacity="0.75" filter="url(#soft)"/>
  </g>
  <rect x="{fx}" y="{FY}" width="{FW}" height="{FH}" rx="{FR}" fill="none" stroke="{DEEP}" stroke-opacity="0.5" stroke-width="4" transform="translate(0,0)"/>'''
    return s

# ---------- A: front view, corrected rocker shading ----------
A=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  {DEFS}
  <circle cx="248" cy="262" r="216" fill="url(#gl)"/>
  <g clip-path="url(#ff)">
    <rect x="{FX}" y="{FY}" width="{FW}" height="{FH/2}" fill="url(#top)"/>
    <rect x="{FX}" y="{seam}" width="{FW}" height="{FH/2}" fill="url(#bot)"/>
    <rect x="{FX}" y="{FY}" width="{FW}" height="11" fill="{EDGE}" fill-opacity="0.92"/>
    <rect x="{FX}" y="{FY+FH-11}" width="{FW}" height="11" fill="{EDGE}" fill-opacity="0.92"/>
    <rect x="{FX}" y="{tSeam-8}" width="{FW}" height="34" fill="{DEEP}" fill-opacity="0.78" filter="url(#soft)"/>
  </g>
  <rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="{FR}" fill="none" stroke="{DEEP}" stroke-opacity="0.45" stroke-width="4"/>
  <!-- LED level column, SEPARATE, to the right -->
  {led(410,120,396,50,6,4)}
</svg>
'''
open('/tmp/rA.svg','w').write(A)

# ---------- B: slight left side view; side silhouette shows out-in-out notch ----------
D=30  # side depth
# side strip left contour: (FX-D, FY) -> (FX, seam) -> (FX-D, FY+FH), inner = front left edge x=FX
side=f'''<path d="M {FX} {FY} L {FX-D} {FY+18} L {FX} {seam} L {FX-D} {FY+FH-18} L {FX} {FY+FH} Z" fill="url(#sideg)"/>
  <path d="M {FX} {FY} L {FX-D} {FY+18} L {FX} {seam} L {FX-D} {FY+FH-18} L {FX} {FY+FH}" fill="none" stroke="{DEEP}" stroke-opacity="0.6" stroke-width="3" stroke-linejoin="round"/>'''
B=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  {DEFS}
  <circle cx="256" cy="262" r="216" fill="url(#gl)"/>
  {side}
  <g clip-path="url(#ff)">
    <rect x="{FX}" y="{FY}" width="{FW}" height="{FH/2}" fill="url(#top)"/>
    <rect x="{FX}" y="{seam}" width="{FW}" height="{FH/2}" fill="url(#bot)"/>
    <rect x="{FX}" y="{FY}" width="{FW}" height="11" fill="{EDGE}" fill-opacity="0.92"/>
    <rect x="{FX}" y="{FY+FH-11}" width="{FW}" height="11" fill="{EDGE}" fill-opacity="0.92"/>
    <rect x="{FX}" y="{tSeam-8}" width="{FW}" height="34" fill="{DEEP}" fill-opacity="0.78" filter="url(#soft)"/>
  </g>
  <rect x="{FX}" y="{FY}" width="{FW}" height="{FH}" rx="{FR}" fill="none" stroke="{DEEP}" stroke-opacity="0.45" stroke-width="4"/>
  {led(414,120,396,50,6,4)}
</svg>
'''
open('/tmp/rB.svg','w').write(B)
print("wrote rA rB")
