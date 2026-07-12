# amber value ramp (top-left light), monochrome
HI="#FBC470"; LIT="#F2A94A"; BASE="#DB8C28"; SH="#B06E17"; DEEP="#7E4E0D"; GLOW="#F3A63C"
def led(cx, top, bot, w, n, lit):
    r=w*0.40; gap=w*0.30; seg=(bot-top-(n-1)*gap)/n; out=[]
    for i in range(n):
        y=bot-seg-i*(seg+gap)
        col, op = (HI if i==lit-1 else LIT, 1.0) if i<lit else (DEEP, 0.55)
        out.append(f'<rect x="{cx-w/2:.1f}" y="{y:.1f}" width="{w:.1f}" height="{seg:.1f}" rx="{r:.1f}" fill="{col}" fill-opacity="{op}"/>')
    return "\n    ".join(out)

# ---------- V1: frontal, Inovelli-style soft-shadow beveled rockers ----------
V1=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <filter id="blur" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>
    <radialGradient id="amb" cx="34%" cy="30%" r="85%"><stop offset="0" stop-color="{HI}"/><stop offset="55%" stop-color="{BASE}"/><stop offset="100%" stop-color="{SH}"/></radialGradient>
    <linearGradient id="up" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{HI}"/><stop offset="0.5" stop-color="{LIT}"/><stop offset="1" stop-color="{SH}"/></linearGradient>
    <linearGradient id="dn" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{LIT}"/><stop offset="0.55" stop-color="{BASE}"/><stop offset="1" stop-color="{SH}"/></linearGradient>
    <radialGradient id="gl" cx="50%" cy="55%" r="60%"><stop offset="0" stop-color="{GLOW}" stop-opacity="0.35"/><stop offset="100%" stop-color="{GLOW}" stop-opacity="0"/></radialGradient>
  </defs>
  <circle cx="256" cy="270" r="220" fill="url(#gl)"/>
  <!-- device body / faceplate -->
  <rect x="60" y="48" width="392" height="416" rx="70" fill="url(#amb)"/>
  <rect x="60" y="48" width="392" height="416" rx="70" fill="none" stroke="{DEEP}" stroke-opacity="0.35" stroke-width="3"/>
  <!-- LED channel (recessed) on the right -->
  <rect x="356" y="104" width="60" height="304" rx="30" fill="{DEEP}" fill-opacity="0.55"/>
  {led(386,116,396,40,6,4)}
  <!-- paddle: two beveled rockers, top-lit, with soft seam shadow -->
  <!-- seam shadow (soft) -->
  <rect x="96" y="250" width="228" height="26" rx="13" fill="{DEEP}" fill-opacity="0.55" filter="url(#blur)"/>
  <!-- top rocker -->
  <rect x="96" y="86" width="228" height="158" rx="30" fill="url(#up)"/>
  <rect x="104" y="92" width="212" height="20" rx="10" fill="{HI}" fill-opacity="0.55" filter="url(#blur)"/>
  <!-- bottom rocker -->
  <rect x="96" y="272" width="228" height="158" rx="30" fill="url(#dn)"/>
  <rect x="104" y="276" width="212" height="18" rx="9" fill="{HI}" fill-opacity="0.7" filter="url(#blur)"/>
  <rect x="104" y="408" width="212" height="18" rx="9" fill="{DEEP}" fill-opacity="0.5" filter="url(#blur)"/>
</svg>
'''
open('/tmp/rk1.svg','w').write(V1)

# ---------- V2: slight side-profile (oblique) revealing rocker thickness ----------
# front face is a subtle trapezoid tilted toward viewer; a right/bottom side face shows thickness
V2=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <filter id="b2" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="7"/></filter>
    <linearGradient id="face" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{HI}"/><stop offset="0.5" stop-color="{LIT}"/><stop offset="1" stop-color="{BASE}"/></linearGradient>
    <linearGradient id="side" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{SH}"/><stop offset="1" stop-color="{DEEP}"/></linearGradient>
    <radialGradient id="gl2" cx="45%" cy="55%" r="62%"><stop offset="0" stop-color="{GLOW}" stop-opacity="0.32"/><stop offset="100%" stop-color="{GLOW}" stop-opacity="0"/></radialGradient>
  </defs>
  <circle cx="250" cy="266" r="216" fill="url(#gl2)"/>
  <!-- extruded thickness (right + bottom side faces), offset down-right -->
  <path d="M 150 92 Q 150 60 182 60 L 356 60 Q 388 60 388 92 L 388 452 Q 388 484 356 484 L 182 484 Q 150 484 150 452 Z"
        transform="translate(20,16)" fill="url(#side)"/>
  <!-- front face (rocker paddle), tilted: top edge slightly narrower (recedes), bottom wider (toward viewer) -->
  <path d="M 168 64 L 348 64 Q 380 64 380 96 L 388 448 Q 388 480 356 480 L 176 480 Q 140 480 144 448 L 150 96 Q 150 64 168 64 Z"
        fill="url(#face)"/>
  <!-- seam shadow across the tilted face -->
  <path d="M 150 262 L 384 262 L 386 296 L 148 296 Z" fill="{DEEP}" fill-opacity="0.5" filter="url(#b2)"/>
  <!-- top-edge highlight and lower-rocker top highlight -->
  <rect x="168" y="74" width="196" height="16" rx="8" fill="{HI}" fill-opacity="0.6" filter="url(#b2)"/>
  <rect x="160" y="298" width="210" height="16" rx="8" fill="{HI}" fill-opacity="0.6" filter="url(#b2)"/>
  <!-- LED bar inset near right edge of the face -->
  <rect x="330" y="120" width="44" height="270" rx="22" fill="{DEEP}" fill-opacity="0.5"/>
  {led(352,130,380,32,6,4)}
</svg>
'''
open('/tmp/rk2.svg','w').write(V2)
print("wrote rk1 rk2")
