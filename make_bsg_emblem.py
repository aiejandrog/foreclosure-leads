#!/usr/bin/env python
"""Process the OFFICIAL BSG emblem into every production asset.

Source of truth: `bsg_emblem_source.svg` in this repo. Everything else is derived from it by this
script. Do not hand-edit the derivatives; drop a new SVG in and re-run.

WHY THIS IS SVG NOW (2026-08-23)
The previous emblem was a navy/gold *wordmark* whose artwork spelled MSG, traced out of a source
PNG by flood-fill and hue analysis. Renaming the company to Biscayne Solutions Group invalidated
the artwork outright, and it could not be recoloured or regenerated because the source PNG lived in
`~/OneDrive/Desktop/MSG-Legal-Pack-Drafts`, which had already been deleted. Vector source + a
letter-free mark means the next rename costs nothing.

What comes out:
  BSG_Emblem_Color.png       full colour, transparent background   -> default everywhere
  BSG_Emblem_Mono_Black.png  one-ink black field, white knockout   -> B&W printing and fax
  BSG_Emblem_Reversed.png    for navy / dark / photographic fields
  bsg_brand.py               the colour emblem as a base64 data URI, embedded

Three things this has to get right, and the reasons are recorded because they each ate a pass:

1. **Background must be ALPHA, never white.** A white slab baked into the asset swallows whatever
   the emblem is layered on. Chromium screenshots default to an opaque page, so the page body is
   forced transparent AND `omit_background=True` is passed — either one alone still yields white.
2. **The one-ink variant is a colour SUBSTITUTION, not a grayscale.** Navy and teal are both
   mid-luminance, so a grayscale threshold turns the shield into one gray mass and the knocked-out
   water and palms disappear. Swapping the gradient stops to black keeps the figure/ground pattern
   the brand guide already proved works at letterhead size.
3. **Derive width from height, never stretch.** Consumers call `mark_size()` / `mono_bw_size()`;
   those are generated from the ACTUAL rendered pixel size, so changing the SVG's aspect ratio
   cannot silently distort every letterhead.
"""
import base64
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.environ.get('BSG_EMBLEM_SRC') or os.path.join(HERE, 'bsg_emblem_source.svg')
OUT = os.environ.get('BSG_BRAND_OUT') or os.path.join(HERE, 'brand')

EMBED_H = 384        # covers the largest display use (88px) at 4.4x — past 300dpi print need
MASTER_H = 1400      # the PNGs written to OUT, for print and for dropping into other documents

# Sampled off the BSG shield in bsg-site/index.html — keep these in sync with that page's tokens.
TEAL, NAVY, WHITE = '#3BB0CC', '#0C2E4A', '#FFFFFF'

VARIANTS = {
    #                     __TOP__  __BOTTOM__  __KNOCK__
    'BSG_Emblem_Color':    (TEAL,   NAVY,      WHITE),
    'BSG_Emblem_Mono_Black': ('#000000', '#000000', WHITE),
    'BSG_Emblem_Reversed': (WHITE,  WHITE,     NAVY),
}


def svg_variant(src, top, bottom, knock):
    """Token substitution. Fails loudly rather than emitting an emblem with literal __TOKEN__ text."""
    out = (src.replace('__TOP__', top).replace('__BOTTOM__', bottom).replace('__KNOCK__', knock))
    left = re.findall(r'__[A-Z]+__', out)
    if left:
        sys.exit('unsubstituted token(s) in %s: %s' % (SOURCE, sorted(set(left))))
    return out


def render(svg, height):
    """SVG -> transparent PNG bytes, via the headless chromium this repo already runs.

    Playwright is already the render engine here (call_list.py, acosta_report.py, broward.py), so
    this adds no dependency. cairosvg would be lighter but is not installed and does not ship a
    wheel for this Python on Windows."""
    from playwright.sync_api import sync_playwright
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not m:
        sys.exit('no viewBox in %s — cannot derive aspect' % SOURCE)
    vw, vh = float(m.group(1)), float(m.group(2))
    w = int(round(height * vw / vh))
    svg = re.sub(r'\swidth="[\d.]+"\s+height="[\d.]+"', ' width="%d" height="%d"' % (w, height), svg, count=1)
    # Transparent on BOTH axes: a transparent body, and omit_background on the shot itself.
    page_html = ('<!doctype html><meta charset="utf-8">'
                 '<style>html,body{margin:0;padding:0;background:transparent}'
                 'svg{display:block}</style>' + svg)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': w, 'height': height}, device_scale_factor=1)
        pg.set_content(page_html, wait_until='load')
        png = pg.screenshot(omit_background=True, clip={'x': 0, 'y': 0, 'width': w, 'height': height})
        b.close()
    return png, w


def fit(png_bytes, height):
    """Downscale a master render to `height` with a high-quality filter, alpha preserved."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
    w = round(im.width * height / im.height)
    im = im.resize((w, height), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue(), w


def b64(png_bytes):
    return 'data:image/png;base64,' + base64.b64encode(png_bytes).decode('ascii')


HEADER = '''"""Biscayne Solutions Group brand assets, embedded.

The OFFICIAL emblem — the BSG shield: navy-to-teal field, bay water and two palms knocked out in
white. Mark only, and deliberately LETTER-FREE: the company name is not in the artwork and not in
the header, it is spelled out once as footer text at the bottom of the page. The emblem it replaced
was a wordmark spelling MSG, which the 2026-08-23 rename invalidated outright.

This file is GENERATED by `make_bsg_emblem.py` from `bsg_emblem_source.svg`.
Do not hand-edit the payload. Drop a new SVG in and re-run.

Colour is the default on every surface. MONO_BW is the same emblem rendered as a solid black field
with the same white knockout, used only where the output is genuinely one-ink: Lob letters are
submitted with `color:'false'`, and a straight grayscale of navy-on-teal collapses to one mid-gray
mass that loses the water and the palms entirely.

Native asset is %dx%d px. Derive width from height so it always scales, never stretches.
"""

# COLOR_B64 is the emblem proper. MONO_B64 is a legacy alias kept because make_bsg_forms.py and
# older callers import that name; it has ALWAYS carried the colour asset despite the name.
COLOR_B64 = "%s"
MONO_B64 = COLOR_B64
NATIVE_W, NATIVE_H = %d, %d


def mark_size(height_px):
    """Width for a given height, preserving the native aspect."""
    return round(height_px * NATIVE_W / NATIVE_H)


# One-ink rendition, for surfaces that are printed or transmitted in black and white only:
# Lob letters (submitted color:'false') and fax. Same emblem, the gradient substituted to solid
# black so the water and palms stay knocked out instead of collapsing into the field.
MONO_BW_B64 = "%s"
MONO_BW_W, MONO_BW_H = %d, %d


def mono_bw_size(height_px):
    """Width for a given height of the one-ink rendition."""
    return round(height_px * MONO_BW_W / MONO_BW_H)
'''


def main():
    if not os.path.exists(SOURCE):
        sys.exit('missing emblem source: %s' % SOURCE)
    src = io.open(SOURCE, encoding='utf-8').read()
    os.makedirs(OUT, exist_ok=True)

    masters = {}
    for name, (top, bottom, knock) in VARIANTS.items():
        png, w = render(svg_variant(src, top, bottom, knock), MASTER_H)
        open(os.path.join(OUT, name + '.png'), 'wb').write(png)
        masters[name] = png
        print('  %-24s <- %dx%d  (%d KB)' % (name + '.png', w, MASTER_H, len(png) // 1024))

    color, cw = fit(masters['BSG_Emblem_Color'], EMBED_H)
    mono, mw = fit(masters['BSG_Emblem_Mono_Black'], EMBED_H)
    src_out = HEADER % (cw, EMBED_H, b64(color), cw, EMBED_H, b64(mono), mw, EMBED_H)
    io.open(os.path.join(HERE, 'bsg_brand.py'), 'w', encoding='utf-8', newline='').write(src_out)
    print('  %-24s <- %dx%d colour + %dx%d one-ink' % ('bsg_brand.py', cw, EMBED_H, mw, EMBED_H))
    print('\nemblem aspect is now %d:%d — tracker_template.html _bsgHead() derives width from this.'
          % (cw, EMBED_H))


if __name__ == '__main__':
    main()
