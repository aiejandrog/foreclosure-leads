#!/usr/bin/env python
"""bsg_mark.py — the Biscayne Solutions Group mark as VECTOR.

WHY THIS EXISTS
The mark shipped as a 440x457 PNG of the OLD chrome "BSG FLORIDA" badge. The print review measured
what that costs: "FLORIDA" occupies 4.16% of the asset height, so it prints at a 1.50-2.55pt cap
height against a 6pt floor, in light cyan reversed out of navy INSIDE the bitmap at a 0.5-0.8pt
stroke -- it plugs solid under normal dot gain at every size a card uses. The asset also carries
10,090 semi-transparent edge pixels matted toward black, so on white stock it haloes.

A raster logo cannot be fixed by scaling it. This is the same mark drawn as geometry:
  * crisp at any size, because there is no resolution
  * mono() reverses to a single colour cleanly -- the old one inverted to "an unreadable white blob",
    which is why layout B needed a white chip to sit on, which is what threw the trim centring off
  * no alpha fringe, so it sits on white paper without a halo
  * the wordmark is REAL TYPE, so it stays legible at 0.3in instead of becoming mush

APPROXIMATION, STATED PLAINLY: this is drawn from the reference image, not traced from the source
vector. The tower, arc and wordmark are faithful; the BSG monogram is custom lettering and is set
here in a heavy geometric sans, which is close but not identical. If the original AI/EPS/SVG turns
up, prefer it -- bsg_cards.py --logo takes any file and overrides this.
"""

BLUE = '#1B5FAA'
CHAR = '#3A3F47'
GREY = '#8A8D91'


def svg(mono=''):
    """The mark. `mono` (e.g. '#ffffff') collapses every element to one colour for reversing."""
    blue = mono or BLUE
    char = mono or CHAR
    grey = mono or GREY
    rule = mono or '#B9BDC2'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 620" role="img"
     aria-label="Biscayne Solutions Group">
  <!-- skyline: left silver block, centre blue tower with the clipped top, right charcoal block -->
  <path d="M296 232 V150 h34 v82 z" fill="{grey}"/>
  <path d="M404 232 V150 h40 l-2 82 z" fill="{char}"/>
  <path d="M340 232 V96 l62 -34 v170 z" fill="{blue}"/>
  <path d="M352 232 V232 h18 v96 h-18 z" fill="{char}"/>
  <!-- the horizon arc under the skyline -->
  <path d="M214 262 Q380 196 546 262 Q380 226 214 262 z" fill="{blue}"/>
  <!-- BSG monogram -->
  <text x="380" y="452" text-anchor="middle"
        font-family="Segoe UI,Arial Black,Helvetica,sans-serif" font-weight="900"
        font-size="196" letter-spacing="-4" fill="{blue}">B<tspan fill="{char}">S</tspan>G</text>
  <!-- wordmark -->
  <text x="380" y="536" text-anchor="middle"
        font-family="Segoe UI,Helvetica,Arial,sans-serif" font-weight="700"
        font-size="72" letter-spacing="10" fill="{blue}">BISCAYNE</text>
  <!-- Flanking rules sit OUTSIDE the wordmark. At font-size 38/letter-spacing 7 the string runs
       ~420px centred on 380, i.e. x 170-590 -- rules at 176 and 524 landed INSIDE it and struck
       the type through. Tightened the setting and pushed the rules clear of it. -->
  <line x1="118" y1="578" x2="176" y2="578" stroke="{rule}" stroke-width="4"/>
  <line x1="584" y1="578" x2="642" y2="578" stroke="{rule}" stroke-width="4"/>
  <text x="380" y="590" text-anchor="middle"
        font-family="Segoe UI,Helvetica,Arial,sans-serif" font-weight="500"
        font-size="34" letter-spacing="5" fill="{grey}">SOLUTIONS GROUP</text>
</svg>'''


def data_uri(mono=''):
    import base64
    return 'data:image/svg+xml;base64,' + base64.b64encode(
        svg(mono).encode('utf-8')).decode('ascii')


if __name__ == '__main__':
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for name, m in (('BSG_Mark.svg', ''), ('BSG_Mark_White.svg', '#ffffff'),
                    ('BSG_Mark_Navy.svg', '#16294d')):
        open(os.path.join(here, name), 'w', encoding='utf-8').write(svg(m))
        print('wrote', name)
