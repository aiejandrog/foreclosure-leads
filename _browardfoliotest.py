"""_browardfoliotest.py — a Broward CONDO folio keeps its letters through to_slim.

WHY THIS EXISTS
A Broward condo folio carries letters ('484210HC0120'). `re.sub(r'\\D', '', folio)` does not blank
it — it returns '4842100120', a DIFFERENT folio that looks perfectly real. Everything downstream
then asks the county about a parcel that is not this lead's:
  * fl_cadastral.enrich queries raw-then-digits precisely because the cadastral stores the letters
    (fl_cadastral.py:87 — '4/4 condo folios MISS digits-only, 4/4 HIT raw'), so a pre-stripped
    folio throws that away before enrich ever sees it.
  * wp_contacts.wp_parcel_verdict compares the Whitepages apn to the lead's folio with only
    separators dropped (wp_contacts.py:300), so a digits-only folio graded every lettered-apn
    record 'mismatch' / 'unverified' (#326).

FAILS ON MAIN: 'condo folio keeps its letters (broward.to_slim)' and the county_leads twin — on
main to_slim returns '4842100120' for both, so the letters assertion fails. The URL checks and the
numeric-folio check pass on main too; they are here to pin that this change is non-regressive.

Invented data only: fake case numbers, a fake street, a fake folio. No network: fl_cadastral.enrich
and the Zillow/status helpers are stubbed, so nothing is looked up or billed.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fl_cadastral

fl_cadastral.enrich = lambda *a, **k: None          # NO live lookup, ever, in this test

import broward            # noqa: E402
import county_leads       # noqa: E402

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail else ''))


CONDO = '484210HC0120'      # invented, shaped like a real Broward condo folio
PLAIN = '504212340010'      # invented, all digits
DASHED = '4842-10-HC-0120'  # invented, separators the feed sometimes carries


def bw_row(folio):
    return {'Case #': 'XXXX-00-000000', 'Address': '1 EXAMPLE ST, SAMPLE CITY, FL 33301',
            'AuctionDate': '12/01/2026', 'Folio': folio,
            'Final Judgment Amount': '$100,000.00', 'Plaintiff': 'SAMPLE BANK NA'}


def bw_slim(folio):
    out = broward.to_slim([bw_row(folio)])
    return out[0] if out else {}


# ---- broward.to_slim ------------------------------------------------------------------------
c = bw_slim(CONDO)
rec('condo folio keeps its letters (broward.to_slim)', c.get('folio') == CONDO, c.get('folio'))
rec('condo folio is NOT silently renumbered to a different parcel',
    c.get('folio') != re.sub(r'\D', '', CONDO), c.get('folio'))

p = bw_slim(PLAIN)
rec('an all-digit folio is unchanged (broward.to_slim)', p.get('folio') == PLAIN, p.get('folio'))

d = bw_slim(DASHED)
rec('separators are still dropped and case folded', d.get('folio') == CONDO, d.get('folio'))

rec('tax URL still carries the digits-only folio (non-regressive)',
    c.get('tax', '').endswith(re.sub(r'\D', '', CONDO)), c.get('tax'))
rec('tax URL for an all-digit folio is unchanged',
    p.get('tax', '').endswith(PLAIN), p.get('tax'))

# the cadastral must be asked with the LETTERS, not the stripped form: that is the whole point
asked = []
fl_cadastral.enrich = lambda *a, **k: asked.append(k.get('parcel_id') or (a[0] if a else None))
bw_slim(CONDO)
fl_cadastral.enrich = lambda *a, **k: None
rec('fl_cadastral.enrich is asked with the raw lettered folio', asked == [CONDO], asked)

# ---- county_leads.to_slim ------------------------------------------------------------------
# Same bug, same line, in the generic county path. Driven through the real BROWARD config so the
# URL builders are the real ones.
cfg = county_leads.COUNTIES['BROWARD'] if hasattr(county_leads, 'COUNTIES') else None
if cfg is None:
    rec('county_leads exposes a BROWARD config to drive', False, 'no COUNTIES map found')
else:
    cl = county_leads.to_slim('BROWARD', cfg, '', [bw_row(CONDO)])
    row = cl[0] if cl else {}
    rec('condo folio keeps its letters (county_leads.to_slim)', row.get('folio') == CONDO,
        row.get('folio'))
    rec('county_leads pa URL still uses digits (non-regressive)',
        re.sub(r'\D', '', CONDO) in (row.get('pa') or '') or not (row.get('pa') or ''),
        row.get('pa'))

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
