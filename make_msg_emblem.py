#!/usr/bin/env python
"""Process the OFFICIAL MSG emblem into every production asset.

Source of truth: `MSG_Emblem_Source.png` in the brand folder — Alejandro's navy/gold shield, made
2026-08-08 and declared the official emblem. Everything else is derived from it by this script.
Do not hand-edit the derivatives; drop a new source in and re-run.

What comes out:
  MSG_Emblem_Color.png       full colour, transparent background   -> default everywhere
  MSG_Emblem_Mono_Black.png  navy->black, gold->white knockout     -> B&W printing and fax
  MSG_Emblem_Reversed.png    for navy / dark / photographic fields
  msg_brand.py               the colour emblem as a base64 data URI, embedded

Three things this has to get right, and the reasons are recorded because they each ate a pass:

1. **Background must fade to ALPHA, never to white.** A white slab baked into the asset swallows
   whatever the emblem is layered on — that lesson came from the previous brand pass and still holds.
2. **Flood fill from the border, do not threshold globally.** The emblem contains near-white gold
   highlights on the letterforms; a luminance cutoff deletes them and punches holes in the M and G.
   Filling inward from the edge only removes background that is actually connected to the outside.
3. **The mono variant is split by HUE, not brightness.** Navy and gold are both mid-luminance, so a
   grayscale threshold turns the whole thing into one gray mass. Blue-dominant pixels become the
   black field, warm pixels become the knocked-out letters — which is the figure/ground pattern the
   brand guide already proved works at letterhead size.
"""
import base64
import io
import os
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('MSG_BRAND_OUT') or r"C:\Users\olqbb\OneDrive\Desktop\MSG-Legal-Pack-Drafts"
SOURCE = os.path.join(OUT, 'MSG_Emblem_Source.png')

BG_TOL = 26          # per-channel distance from the sampled background that still counts as page
FEATHER = 1.1        # px of edge softening so the cutout does not look laser-cut against white
EMBED_H = 384        # 384 covers the largest display use (88px) at 4.4x — past 300dpi print need
MASTER_H = 1400


def _load(path):
    from PIL import Image
    return Image.open(path).convert('RGBA')


def strip_background(im):
    """Flood-fill the page away from every border pixel, leaving the emblem on transparency."""
    from PIL import Image, ImageFilter
    w, h = im.size
    px = im.load()
    # sample the background from the four corners rather than assuming pure white — the source is a
    # warm off-white (~246,248,250) and assuming #FFF leaves a visible halo ring.
    corners = [px[1, 1], px[w - 2, 1], px[1, h - 2], px[w - 2, h - 2]]
    bg = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))

    def is_bg(p):
        return all(abs(p[i] - bg[i]) <= BG_TOL for i in range(3))

    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if is_bg(px[x, y]):
                q.append((x, y)); seen[y * w + x] = 1
    for y in range(h):
        for x in (0, w - 1):
            if is_bg(px[x, y]):
                q.append((x, y)); seen[y * w + x] = 1
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and is_bg(px[nx, ny]):
                seen[ny * w + nx] = 1; q.append((nx, ny))

    keep = keep_largest_blob(bytearray(255 - 255 * b for b in seen), w, h)
    mask = Image.frombytes('L', (w, h), bytes(keep))
    mask = mask.filter(ImageFilter.GaussianBlur(FEATHER))
    im.putalpha(mask)
    return im.crop(im.getbbox())


def keep_largest_blob(mask, w, h, thresh=40):
    """Keep only the biggest connected opaque region.

    The generated source carries a "Made with AI" badge in a corner. Its text is dark, so background
    removal leaves it standing and the crop box then stretches to include it — which is how a
    portrait shield comes out landscape. Taking the largest blob drops the badge and any stray
    speckle generically, instead of hard-coding a corner that a future source might not use."""
    lab = [0] * (w * h)
    best_id, best_n, cur = 0, 0, 0
    for i in range(w * h):
        if mask[i] <= thresh or lab[i]:
            continue
        cur += 1
        n, q = 0, deque([i])
        lab[i] = cur
        while q:
            j = q.popleft(); n += 1
            x, y = j % w, j // w
            for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                if 0 <= nx < w and 0 <= ny < h:
                    k = ny * w + nx
                    if not lab[k] and mask[k] > thresh:
                        lab[k] = cur; q.append(k)
        if n > best_n:
            best_id, best_n = cur, n
    return bytearray(mask[i] if lab[i] == best_id else 0 for i in range(w * h))


def to_mono(im, inset=0.012):
    """Navy field -> black, gold/cream -> white. Split by hue; luminance cannot separate these.

    The knockout is restricted to the INTERIOR. Hue alone put the outer gold rim and its
    anti-aliased edge into the white bucket, and white-on-white paper meant the silhouette lost its
    outline and read as a torn/distressed edge. Eroding the alpha mask first forces the whole
    outside boundary black, so the shield keeps a clean hard edge at any size."""
    from PIL import Image, ImageFilter
    k = max(3, int(im.width * inset) | 1)          # MinFilter needs an odd kernel
    interior = im.getchannel('A').filter(ImageFilter.MinFilter(k))
    out = Image.new('RGBA', im.size, (0, 0, 0, 0))
    s, d, q = im.load(), out.load(), interior.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = s[x, y]
            if a < 8:
                continue
            # warm (gold, cream, highlight) = the mark; cool (navy) = the field it is knocked out of
            warm = r >= b + 12 and q[x, y] > 200
            d[x, y] = (255, 255, 255, a) if warm else (10, 10, 10, a)
    return out


def to_reversed(im):
    """For navy / dark / photographic fields: keep the gold, turn the navy field white."""
    from PIL import Image
    out = Image.new('RGBA', im.size, (0, 0, 0, 0))
    s, d = im.load(), out.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = s[x, y]
            if a < 8:
                continue
            d[x, y] = (r, g, b, a) if r >= b + 12 else (255, 255, 255, a)
    return out


def fit(im, height):
    from PIL import Image
    return im.resize((max(1, round(im.width * height / im.height)), height), Image.LANCZOS)


def write_msg_brand(color_im):
    """Embed the COLOUR emblem in msg_brand.py as a base64 PNG data URI.

    PNG, not SVG: the source is a raster with gradients and bevels, and Outlook's Word engine will
    not draw an SVG in an <img> anyway. Quantised to 128 colours — the gradients band slightly at
    100% zoom and not at all at the sizes this is ever displayed, and it roughly halves the payload
    that every copy of the board and every generated document has to carry."""
    from PIL import Image
    small = fit(color_im, EMBED_H).quantize(colors=128, method=Image.FASTOCTREE)
    buf = io.BytesIO()
    small.save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    w = small.width
    src = f'''"""Miami Solutions Group brand assets, embedded.

The OFFICIAL emblem — Alejandro's navy/gold MSG shield, declared official 2026-08-08. Mark only:
the company name is not in the artwork and not in the header, it is spelled out once as footer text
at the bottom of the page.

This file is GENERATED by `make_msg_emblem.py` from `MSG_Emblem_Source.png` in the brand folder.
Do not hand-edit the payload. Drop a new source in and re-run.

Colour is the default on every surface. MONO_BW is the same emblem rendered navy->black /
gold->white, used only where the output is genuinely one-ink: Lob letters are submitted with
`color:'false'`, and a straight grayscale of navy-on-gold collapses to one mid-gray mass.

Native asset is {w}x{EMBED_H} px. Derive width from height so it always scales, never stretches.
"""

MONO_B64 = "data:image/png;base64,{b64}"
NATIVE_W, NATIVE_H = {w}, {EMBED_H}


def mark_size(height_px):
    """Width for a given height, preserving the native aspect."""
    return round(height_px * NATIVE_W / NATIVE_H)
'''
    return src, len(b64), w


def main():
    if not os.path.exists(SOURCE):
        print(f'FATAL: official artwork missing at {SOURCE}'); return 1
    from PIL import Image
    im = strip_background(_load(SOURCE))
    print(f'  source cut out -> {im.width}x{im.height} (transparent)')

    color = fit(im, MASTER_H)
    mono = to_mono(color)
    rev = to_reversed(color)
    for name, img in (('MSG_Emblem_Color', color), ('MSG_Emblem_Mono_Black', mono),
                      ('MSG_Emblem_Reversed', rev)):
        p = os.path.join(OUT, name + '.png')
        img.save(p, optimize=True)
        print(f'  {name}.png  {img.width}x{img.height}  {os.path.getsize(p)//1024}KB')

    src, n, w = write_msg_brand(color)
    open(os.path.join(HERE, 'msg_brand.py'), 'w', encoding='utf-8').write(src)
    print(f'  msg_brand.py  <- {w}x{EMBED_H} colour ({n} b64 chars)')

    # the one-ink derivative the Lob path needs, embedded the same way
    small = fit(mono, EMBED_H).quantize(colors=16, method=Image.FASTOCTREE)
    buf = io.BytesIO(); small.save(buf, format='PNG', optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    with open(os.path.join(HERE, 'msg_brand.py'), 'a', encoding='utf-8') as f:
        f.write(f'''

# One-ink rendition, for surfaces that are printed or transmitted in black and white only:
# Lob letters (submitted color:'false') and fax. Same emblem, hue-split so the letterforms stay
# knocked out instead of collapsing into the field the way a plain grayscale does.
MONO_BW_B64 = "data:image/png;base64,{b64}"
MONO_BW_W, MONO_BW_H = {small.width}, {EMBED_H}


def mono_bw_size(height_px):
    """Width for a given height of the one-ink rendition."""
    return round(height_px * MONO_BW_W / MONO_BW_H)
''')
    print(f'  msg_brand.py  <- one-ink rendition ({len(b64)} b64 chars)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
