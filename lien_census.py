"""Per-lien-type counts for Miami-Dade leads, for the board's plaintext coverage census.

make_tracker() writes `<!-- DEALFLOW-COVERAGE {...} -->` on line 1 of docs/index.html. The board
itself is encrypted and the lien detail files (records_liens.json, code_liens.json,
county_taxes.json) are gitignored, so that marker is the only place anyone -- a phone, the cloud
engine, a reviewer -- can see how much lien coverage a build carries. Before this module it said
only `liens: N` (a chain exists), which cannot tell "every Miami lead has its mortgages, HOA and
tax liens read" from "a few chains, nothing priced".

`md_counts(slim, chains)` returns FLAT INTEGER COUNTS ONLY:
  * no names, addresses, case numbers, folios, amounts or any other string -- the marker ships in
    the clear on the public page;
  * no nested objects -- every reader of the marker (publish_guard.MARK, healthcheck, engine_drift)
    extracts it with a NON-GREEDY `\\{.*?\\}`, so a nested `{}` would cut the JSON short and turn
    the whole census unreadable.
Counts are LEADS (rows), not instruments, except md_mo_n / md_ms_n, which count mortgages.

These keys are census-only: publish_guard.FIELDS does not list them, so they never gate a publish.

Keys (Miami-Dade rows: county missing or MIAMI-DADE; the hard-money balloon lane, st == BAL, is
not a court lead and is left out)
  md        Miami-Dade rows on the board        md_lp     of them, the lis pendens lane (st == LP)
  md_td     of them, tax-deed auction rows (st == TD; no foreclosing judgment exists)
  md_rd     rows with a recorded-lien read attached (records_liens chain on the row)
  md_fcj    foreclosure-auction rows with a posted foreclosing judgment amount
  md_fcj_u  foreclosure-auction rows whose judgment is not posted / unknown (debt unpriced)
  md_mo     rows with >=1 OPEN mortgage         md_mo_n   OPEN mortgages (instruments)
  md_ms     rows with >=1 SATISFIED mortgage    md_ms_n   SATISFIED mortgages (instruments)
  md_mo_u   rows with an OPEN mortgage whose amount the index does not publish
  md_hoa    rows with an open HOA/condo association lien          md_hoa_u  ...unpriced
  md_mj     rows with an open money judgment (not this case's own)  md_mj_u   ...unpriced
  md_fed    rows with an open federal tax lien (IRS / United States)
  md_st     rows with an open state tax lien / warrant (Dept. of Revenue)
  md_tl_u   rows with a federal or state tax lien whose amount is not published
  md_muni   rows with an open recorded municipal / county / code / PACE lien  md_muni_u ...unpriced
  md_util   rows with an open recorded utility lien (water & sewer, WASD)     md_util_u ...unpriced
  md_oth    rows with any other open recorded lien (other lienor)             md_oth_u  ...unpriced
  md_lpx    rows whose record search shows ANOTHER open lis pendens (not this case's own filing)
  md_taxd   rows with verified delinquent property taxes (county_taxes)
  md_taxc   rows where a tax certificate has been sold
  md_code   rows with a code-enforcement hit (code_liens: open case or recorded code lien)
  md_codel  of them, a recorded code lien per the code layer
  md_code_a code hits where the recorded chain prices an open municipal/code lien
  md_code_na code hits with no amount anywhere (the code layer publishes no dollar figure)
  md_unp    rows with at least one UNPRICED item of any type above (fcj, mortgage, recorded lien,
            code hit without an amount)

What is counted as an open recorded lien mirrors records_liens.analyze's summing loop: status OPEN
(not RELEASED), not the foreclosure's own filing (own_case), not a lis pendens, not a waiver /
financing statement / title certificate, and an 'other' row only when its document is a LIEN,
JUDGMENT or WARRANT. analyze keeps the untruncated document for that test; the chain keeps the
first 40 characters, which is where those words sit, so the census can differ from analyze's sum
only on a document type longer than that.
"""
import re

# same patterns as records_liens.analyze (_CLAIM_DOC_RE / _NOT_CLAIM_DOC_RE), which defines them
# locally; duplicated rather than imported so a census never pulls in requests/playwright
_CLAIM_DOC_RE = re.compile(r'\bLIEN\b|JUDGMENT|\bWARRANTS?\b', re.I)
_NOT_CLAIM_DOC_RE = re.compile(r'WAIVER|CONTEST|SUBORDINAT|FINANCING STATEMENT|CERTIFICATE OF TITLE', re.I)
# records_liens files water & sewer (WASD) liens under kind 'code'; split them out for the census
_UTIL_RE = re.compile(r'WATER|SEWER|\bWASD\b|UTILIT', re.I)

KEYS = ('md', 'md_lp', 'md_td', 'md_rd', 'md_fcj', 'md_fcj_u', 'md_mo', 'md_mo_n', 'md_ms', 'md_ms_n', 'md_mo_u',
        'md_hoa', 'md_hoa_u', 'md_mj', 'md_mj_u', 'md_fed', 'md_st', 'md_tl_u', 'md_muni', 'md_muni_u',
        'md_util', 'md_util_u', 'md_oth', 'md_oth_u', 'md_lpx', 'md_taxd', 'md_taxc', 'md_code',
        'md_codel', 'md_code_a', 'md_code_na', 'md_unp')


def is_md(row):
    return str(row.get('county') or 'MIAMI-DADE').strip().upper() == 'MIAMI-DADE'


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _bucket(o):
    """(bucket, priced) for one chain `other` row that analyze would count, else None."""
    if not isinstance(o, dict) or o.get('own_case') or str(o.get('st') or 'OPEN').upper() != 'OPEN':
        return None
    kind = o.get('kind') or 'other'
    doc = str(o.get('doc') or '')
    if kind == 'lis_pendens':
        return 'lpx', True
    if _NOT_CLAIM_DOC_RE.search(doc) or (kind == 'other' and not _CLAIM_DOC_RE.search(doc)):
        return None
    priced = _num(o.get('amt')) > 0
    if kind == 'association':
        return 'hoa', priced
    if kind == 'judgment':
        return 'mj', priced
    if kind == 'irs':
        return 'fed', priced
    if kind == 'state_tax':
        return 'st', priced
    if kind == 'code':
        blob = '%s %s' % (o.get('party') or '', doc)
        return ('util' if _UTIL_RE.search(blob) else 'muni'), priced
    return 'oth', priced


def md_counts(slim, chains=None):
    """Flat int counts for the Miami-Dade rows of `slim` (see module docstring).

    chains = records_liens.json as make_tracker loaded it ({case: chain}). A row's chain is used only
    when the board attached one to it (`orhoa` is stamped exactly when a chain was applied), so the
    census describes the board, not the cache.
    """
    chains = chains if isinstance(chains, dict) else {}
    c = dict.fromkeys(KEYS, 0)
    for d in slim or ():
        if not isinstance(d, dict) or not is_md(d):
            continue
        st = str(d.get('st') or '').upper()
        if st == 'BAL':
            continue
        c['md'] += 1
        unp = False
        if st == 'LP':
            c['md_lp'] += 1
        elif st == 'TD':
            c['md_td'] += 1
        else:
            if _num(d.get('judg')) > 0 and not d.get('ju'):
                c['md_fcj'] += 1
            else:
                c['md_fcj_u'] += 1; unp = True
        h = chains.get(str(d.get('case') or '')) if 'orhoa' in d or d.get('orliens') else None
        h = h if isinstance(h, dict) else {}
        if 'orhoa' in d or d.get('orliens'):
            c['md_rd'] += 1
        # mortgages: the chain the board shows (orliens), status per instrument
        ml = [m for m in (d.get('orliens') or []) if isinstance(m, dict)]
        mo = [m for m in ml if str(m.get('st') or '').upper() == 'OPEN']
        ms = [m for m in ml if str(m.get('st') or '').upper() == 'SATISFIED']
        c['md_mo_n'] += len(mo); c['md_ms_n'] += len(ms)
        c['md_mo'] += bool(mo); c['md_ms'] += bool(ms)
        if any(_num(m.get('amt')) <= 0 for m in mo) or _num(h.get('mtg_open_unpriced')) > 0:
            c['md_mo_u'] += 1; unp = True
        # non-mortgage recorded liens, by kind
        seen, seen_u = set(), set()
        for o in h.get('other') or ():
            b = _bucket(o)
            if not b:
                continue
            seen.add(b[0])
            if not b[1]:
                seen_u.add(b[0])
        for k in ('hoa', 'mj', 'muni', 'util', 'oth', 'fed', 'st', 'lpx'):
            if k in seen:
                c['md_' + k] += 1
        for k in ('hoa', 'mj', 'muni', 'util', 'oth'):
            if k in seen_u:
                c['md_%s_u' % k] += 1
        if seen_u & {'fed', 'st'}:
            c['md_tl_u'] += 1
        if seen_u - {'lpx'}:
            unp = True
        # property taxes (county_taxes join: only rows with money due carry taxDue)
        if _num(d.get('taxDue')) > 0:
            c['md_taxd'] += 1
        if d.get('taxCert'):
            c['md_taxc'] += 1
        # code enforcement (code_liens layer: status + a recorded-lien flag, never a dollar figure)
        hits = [x for x in (d.get('codeliens') or []) if isinstance(x, dict)]
        if hits:
            c['md_code'] += 1
            if any(x.get('lien') for x in hits):
                c['md_codel'] += 1
            priced_code = any(_bucket(o) == ('muni', True) for o in h.get('other') or ())
            if priced_code:
                c['md_code_a'] += 1
            else:
                c['md_code_na'] += 1; unp = True
        if unp:
            c['md_unp'] += 1
    return c
