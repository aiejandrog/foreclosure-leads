"""The card's QR must be the right SIZE, not merely decodable.

WHY THIS EXISTS
The QR shipped undersized and every check passed. segno emits width="29" height="29" and NO
viewBox, so `width:100%` stretched the ELEMENT while the drawing stayed locked at 29 user units
anchored top-left: the code painted at 0.257in in the corner of a 0.62in white chip, with dead
space to its right and below.

Decoding never caught it, because a small sharp code decodes fine at 300-600 DPI. It would have
failed on a phone at arm's length -- i.e. it worked in every test and nowhere real. Alejandro
caught it by LOOKING at the PDF.

So this asserts the thing the earlier checks did not:
    * the symbol's PAINTED WIDTH in inches, off the rasterised PDF
    * that it still decodes to the exact target URL
    * dark-on-light orientation (an inverted code decodes in almost no phone scanner)
    * the front carries NO code -- it is the mark alone, by design
    * all 10 codes survive the 10-up sheet

A QR is uniquely suited to shipping broken: it is unreadable to the author, so "it looks like a
QR" is the only signal a human gets, and that signal stays green while the code rots.

Run: python _cardtest.py     (needs pymupdf + opencv-python-headless + segno)
"""
import datetime as dt
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = 'https://bsgflorida.com'
# Chip is 0.62in with .035in padding -> 0.55in of code, of which the 25-module symbol is
# 25/29 = 0.474in. The detector measures the symbol, NOT the quiet zone. 0.44 leaves room for
# rasteriser rounding while still failing the 0.257in regression by a wide margin.
MIN_SYMBOL_IN = 0.44
R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    print(('  PASS ' if ok else '  FAIL ') + name + ((' | ' + detail) if detail else ''))


def _pdf_pages(path, dpi=400):
    import fitz
    import cv2
    import numpy as np
    doc = fitz.open(path)
    out = []
    for pg in doc:
        pix = pg.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        out.append(cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR))
    return out


print('=== business card — QR size, orientation, and placement ===\n')
try:
    import cv2
except ImportError:
    print('  SKIP — opencv-python-headless not installed')
    sys.exit(0)

today = dt.date.today().isoformat()
subprocess.run([sys.executable, 'bsg_cards.py', '--layout', 'e'], cwd=HERE,
               capture_output=True, text=True)
card = os.path.join(HERE, 'BSG_Card_E_%s.pdf' % today)
rec('the card PDF was generated today', os.path.exists(card), os.path.basename(card))
if not os.path.exists(card):
    print('\n%d/%d passed' % (sum(R), len(R)))
    sys.exit(1)

DPI = 400
pages = _pdf_pages(card, DPI)
det = cv2.QRCodeDetector()
rec('card PDF has a front and a back', len(pages) == 2, '%d page(s)' % len(pages))

# ---- front carries no code -------------------------------------------------------------------
ftxt, _, _ = det.detectAndDecode(pages[0])
rec('FRONT has no QR (it is the mark alone, by design)', not ftxt)

# ---- back: decode, target, and SIZE ----------------------------------------------------------
back = pages[1]
btxt, _, _ = det.detectAndDecode(back)
rec('BACK decodes', bool(btxt))
rec('BACK points at the right URL', btxt == TARGET, repr(btxt))

ok, pts = det.detect(back)
sym = 0.0
if ok and pts is not None:
    p = pts.reshape(-1, 2)
    sym = (max(p[:, 0]) - min(p[:, 0])) / DPI
rec('symbol is printed at a scannable size', sym >= MIN_SYMBOL_IN,
    '%.3f in (floor %.2f, the shipped regression was 0.257)' % (sym, MIN_SYMBOL_IN))
rec('symbol is square (viewBox preserves aspect)',
    ok and pts is not None and abs((max(p[:, 1]) - min(p[:, 1])) / DPI - sym) < 0.02)

# ---- orientation: dark modules on light, never inverted --------------------------------------
if ok and pts is not None:
    import numpy as np
    x0, y0 = int(min(p[:, 0])), int(min(p[:, 1]))
    x1, y1 = int(max(p[:, 0])), int(max(p[:, 1]))
    patch = cv2.cvtColor(back[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    # a correctly-oriented code is mostly light; an inverted one is mostly dark
    rec('code is DARK-ON-LIGHT, not inverted', float(np.mean(patch)) > 127,
        'mean luma %.0f' % float(np.mean(patch)))

# ---- the viewBox itself, so the root cause is asserted directly -------------------------------
sys.path.insert(0, HERE)
import bsg_cards as B                                              # noqa: E402
svg = B.qr_svg()
head = svg[:svg.index('>') + 1]
rec('emitted SVG carries a viewBox', 'viewBox' in head, head[:78])
rec('emitted SVG has no fixed width/height to fight the CSS', 'width=' not in head)
rec('emitted SVG has exactly one class attribute', head.count('class=') == 1)

# ---- the 10-up sheet -------------------------------------------------------------------------
subprocess.run([sys.executable, 'bsg_cards.py', '--sheet'], cwd=HERE,
               capture_output=True, text=True)
sheet = os.path.join(HERE, 'BSG_Cards_SHEET_E_%s.pdf' % today)
if os.path.exists(sheet):
    sp = _pdf_pages(sheet, 300)
    # decode each of the 10 back-page cards individually: detectAndDecodeMulti is unreliable
    # across a full page, which once read as "5 of 10 broken" when all 10 were fine.
    bp = sp[1]
    h, w = bp.shape[:2]
    good = 0
    for r_i in range(5):
        for c_i in range(2):
            cell = bp[int(h * (0.5 + r_i * 2.0) / 11.0):int(h * (0.5 + (r_i + 1) * 2.0) / 11.0),
                      int(w * (0.75 + c_i * 3.5) / 8.5):int(w * (0.75 + (c_i + 1) * 3.5) / 8.5)]
            t, _, _ = det.detectAndDecode(cell)
            good += (t == TARGET)
    rec('all 10 codes on the print sheet decode', good == 10, '%d/10' % good)
else:
    rec('sheet PDF generated', False, 'missing')

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
