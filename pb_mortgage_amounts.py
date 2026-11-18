#!/usr/bin/env python3
"""pb_mortgage_amounts.py -- what each open Palm Beach mortgage was recorded for, read off its own
document. Free: the documents come from pb_recorded (direct book/page link, no search, no captcha).

WHY (2026-11-18). Landmark's search grid has no dollar column, so broward_liens.analyze dropped every
Palm Beach mortgage and pb_chain_truth could only COUNT them: 186 chains traced that day, all
'unpriced', none ever verified. lien_amounts prices only the non-mortgage rows (`other`).

WHERE THE FIGURE COMES FROM (measured on live documents the same day). The clerk's recording stamp on
page 1 prints the amount and the two taxes paid on it:

    CFN 2023... OR BK 34096 PG 1146 RECORDED 1/31/2023 ... AMT $238,500.00  MTG DOC $834.75  INTANGIBLE $477.00

Florida's documentary stamp tax on a note is $0.35 per $100 (or part of $100) and the non-recurring
intangible tax is 2 mills, so both taxes restate the amount independently (590.00 / 0.002 = 295,000;
1,032.50 / 0.0035 = 295,000). The note's own definition ("... Dollars (U.S. $295,000.00)") is a third.

THE RULE: a mortgage gets an amount only when TWO independent readings agree within 1%. One reading,
or readings that disagree, is 'unconfirmed' and the chain stays a ceiling. OCR misreads a digit far more
often than it misreads two different printed figures the same way, and understating a debt is the one
error that reaches a homeowner.

Also kept (no names): the parcel numbers the first pages print, so palmbeach_liens can drop a loan
that names a different parcel (the owner's other property, found by the countywide name search).

Cache: lien_amounts.json, key 'PALM BEACH:MTG:<book>/<page>'. Counts only on the console.

    python pb_mortgage_amounts.py --limit 150     read up to 150 mortgages (the nightly step)
    python pb_mortgage_amounts.py --dry-run       show the work list size, read nothing
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lien_amounts as LA                          # noqa: E402  (cache, OCR reader, live cases)

MTG_V = 1
KEY_PREFIX = 'PALM BEACH:MTG:'
PAGES = 3                                           # stamp on 1, the note on 1-2, the address clause by 3
AGREE = 0.01
MIN_AMT, MAX_AMT = 1000.0, 50_000_000.0
DOC_RATE, INTANGIBLE_RATE = 0.0035, 0.002
TRANSIENT = ('gap_pdf', 'gap_error')
DEFAULT_LIMIT = 150

_MONEY = r'(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})'
_TOKEN_RE = re.compile(r'(?<![\d,.])' + _MONEY + r'(?![\d,])')
_AMT_RE = re.compile(r'\bAMT\b\.?\s*:?\s*\$?\s*' + _MONEY, re.I)
_DOC_RE = re.compile(r'\bDOC(?:UMENTARY)?\b\.?\s*(?:STAMPS?|STAMP\s+TAX|TAX)?\s*:?\s*\$?\s*' + _MONEY, re.I)
_INT_RE = re.compile(r'\bINTANG\w*\b\.?\s*(?:TAX)?\s*:?\s*\$?\s*' + _MONEY, re.I)
_NOTE_RES = (re.compile(r'U\.?\s?S\.?\s*\$\s*' + _MONEY, re.I),
             re.compile(r'U\.?\s?S\.?\s?S\s*' + _MONEY, re.I),                    # OCR: 'U.S.S 295,000.00'
             re.compile(r'principal\s+(?:sum|amount)\s+of\s+[^$]{0,80}\$\s*' + _MONEY, re.I))
_PCN_RE = re.compile(r'\b(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{3})\s*-\s*(\d{4})\b')


def clean(text):
    """OCR repair for figures only: a letter O beside a digit is a zero ('300,OOO.OO'), and spaces
    around a separator inside a number are dropped ('251 ,500.00', '295, 000.00')."""
    t = str(text or '')
    for _ in range(3):
        t = re.sub(r'(?<=[\d,.])[Oo]|[Oo](?=[\d,.]\d)', '0', t)
    t = re.sub(r'(\d)\s*([,.])\s*(\d)', r'\1\2\3', t)
    return t


def _f(s):
    try:
        v = float(str(s).replace(',', ''))
    except ValueError:
        return 0.0
    return v


def _near(a, b, tol=AGREE):
    return a > 0 and b > 0 and abs(a - b) <= tol * max(a, b)


def readings(text):
    """-> [(source, amount)] every independent reading of the principal on these pages."""
    t = clean(text)
    head = t[:2500]
    out = []
    m = _AMT_RE.search(head)
    if m and MIN_AMT <= _f(m.group(1)) <= MAX_AMT:
        out.append(('stamp', _f(m.group(1))))
    m = _INT_RE.search(head)
    if m and _f(m.group(1)) > 0:
        v = round(_f(m.group(1)) / INTANGIBLE_RATE, 2)
        if MIN_AMT <= v <= MAX_AMT:
            out.append(('intangible', v))
    m = _DOC_RE.search(head)
    if m and _f(m.group(1)) > 0:
        v = round(_f(m.group(1)) / DOC_RATE, 2)       # rounded UP to $100 by the statute: within 1% above $10k
        if MIN_AMT <= v <= MAX_AMT:
            out.append(('doc_stamp', v))
    # THE STAMP WITH ITS LABELS LOST (measured 2026-11-18): OCR sometimes keeps the three stamp figures
    # and drops the words ('344,389.00 1,205.40 688.78'). Position alone proves nothing, so a bare
    # figure counts only when a figure right after it is exactly its doc stamp or its intangible tax;
    # the arithmetic is the label.
    if not any(s == 'stamp' for s, _v in out):
        toks = [(_f(m.group(1)), m.start()) for m in _TOKEN_RE.finditer(head)]
        for j, (a, _pos) in enumerate(toks):
            if not MIN_AMT <= a <= MAX_AMT:
                continue
            hit = []
            for b, _p in toks[j + 1:j + 3]:
                if b <= 0:
                    continue
                if _near(b / INTANGIBLE_RATE, a, 0.002):
                    hit.append(('intangible', round(b / INTANGIBLE_RATE, 2)))
                elif a >= 10000 and _near(b / DOC_RATE, a, AGREE):
                    hit.append(('doc_stamp', round(b / DOC_RATE, 2)))
            if hit:
                out = [r for r in out if r[0] not in {h[0] for h in hit}]
                out.append(('stamp', a))
                out.extend(hit)
                break
    seen = set()
    for rx in _NOTE_RES:
        for m in rx.finditer(t):
            v = _f(m.group(1))
            if MIN_AMT <= v <= MAX_AMT and v not in seen:
                seen.add(v)
                out.append(('note', v))
                break
    return out


def decide(rs):
    """-> {'status': 'priced'|'unconfirmed', 'amt', 'basis', 'n_readings'}. Two sources must agree."""
    pairs = [(sa, va, sb, vb) for i, (sa, va) in enumerate(rs) for sb, vb in rs[i + 1:]
             if sa != sb and _near(va, vb)]
    # The clerk's printed amount is the figure itself; the doc stamp is rounded UP to the next $100 by
    # statute, so when the stamp is confirmed by anything, the stamp is the amount.
    stamped = [(v, {s1, s2}) for s1, v1, s2, v2 in pairs for v, s in ((v1, s1), (v2, s2)) if s == 'stamp']
    if stamped:
        amt = max(v for v, _src in stamped)
        srcs = set().union(*[src for v, src in stamped if v == amt])
        return {'status': 'priced', 'amt': int(round(amt)), 'basis': '+'.join(sorted(srcs)), 'n_readings': len(rs)}
    if pairs:
        sa, va, sb, vb = max(pairs, key=lambda p: max(p[1], p[3]))
        return {'status': 'priced', 'amt': int(round(max(va, vb))), 'basis': '+'.join(sorted({sa, sb})),
                'n_readings': len(rs)}
    return {'status': 'unconfirmed', 'amt': None, 'basis': '', 'n_readings': len(rs)}


def parcels(text):
    """Palm Beach parcel numbers printed on these pages, as 17-digit strings."""
    return sorted({''.join(m.groups()) for m in _PCN_RE.finditer(clean(text))})


def key_of(bp):
    v = LA._bp(bp)
    return (KEY_PREFIX + v) if v else None


def due(entry, today):
    if not isinstance(entry, dict) or entry.get('v') != MTG_V:
        return True
    if entry.get('status') not in TRANSIENT and entry.get('status') != 'gap_unreadable':
        return False
    try:
        age = (today - datetime.date.fromisoformat(str(entry.get('read') or '')[:10])).days
    except ValueError:
        return True
    return age >= (1 if entry.get('status') in TRANSIENT else 14)


def lookup(bp, cache=None):
    """The cache entry for a mortgage at this book/page when it is priced, else None. Never raises."""
    try:
        k = key_of(bp)
        e = (cache if cache is not None else LA.load_cache()).get(k) if k else None
        if isinstance(e, dict) and e.get('v') == MTG_V and e.get('status') == 'priced' and (e.get('amt') or 0) > 0:
            return e
    except Exception:
        pass
    return None


def worklist(store, cache, today, live=None):
    """Borrower-side mortgages on board cases that have no entry yet. Cases closest to fully priced
    first (fewest unread mortgages), newest mortgage first inside a case. -> [(case, key, bp)]."""
    live = LA.live_cases() if live is None else live
    per_case = []
    seen = set()
    for case, ctx in (store or {}).items():
        if live and str(case).strip().upper() not in live:
            continue
        rows = []
        for d in (ctx or {}).get('docs') or []:
            if not (d.get('DocTypeDescription') or '').upper().startswith('MORTGAGE'):
                continue
            if (d.get('Party') or '').strip().upper() != 'FROM':
                continue
            k = key_of(d.get('BookPage'))
            if not k or k in seen or not due(cache.get(k), today):
                continue
            seen.add(k)
            rows.append((str(d.get('RecordDate') or ''), case, k, d.get('BookPage')))
        if rows:
            rows.sort(reverse=True)
            per_case.append((len(rows), rows))
    per_case.sort(key=lambda x: x[0])
    return [(c, k, bp) for _n, rows in per_case for _d, c, k, bp in rows]


def first_pages(content, n=PAGES):
    import fitz
    doc = fitz.open(stream=bytes(content), filetype='pdf')
    try:
        if doc.page_count > n:
            doc.select(list(range(n)))
        return doc.tobytes()
    finally:
        doc.close()


def read_one(bp, today, fetch=None, ocr_read=None):
    """One mortgage -> cache entry. fetch(book, page) -> PDF bytes; ocr_read(bytes) -> (text, meta)."""
    entry = {'v': MTG_V, 'read': today.isoformat()}
    try:
        book, page = LA._bp(bp).split('/')
        if fetch is None:
            import pb_recorded as PR
            try:
                content = PR.by_book_page(book, page)
            except PR.Unavailable:
                entry['status'] = 'gap_pdf'
                return entry
        else:
            content = fetch(book, page)
            if content is None:
                entry['status'] = 'gap_pdf'
                return entry
        text, meta = (ocr_read or LA._read_bytes)(first_pages(content) if fetch is None else content)
    except Exception as exc:                        # one bad document never stops the run
        entry.update(status='gap_error', why=type(exc).__name__)
        return entry
    if not str(text or '').strip():
        entry['status'] = 'gap_unreadable'
        return entry
    entry.update(decide(readings(text)))
    entry['pcns'] = parcels(text)
    entry['pages'] = (meta or {}).get('pages') or 0
    return entry


def run(limit=DEFAULT_LIMIT, dry_run=False, today=None, store=None, cache=None, fetch=None, ocr_read=None,
        live=None, save=True, say=print):
    import palmbeach_liens as PBL
    today = today or datetime.date.today()
    store = PBL.load_docs_store() if store is None else store
    cache = dict(LA.load_cache()) if cache is None else cache
    items = worklist(store, cache, today, live)
    rep = {'queued': len(items), 'read': 0, 'priced': 0, 'unconfirmed': 0, 'gaps': 0}
    if dry_run:
        say('pb_mortgage_amounts: %d mortgage(s) to read (dry run)' % len(items))
        return rep
    for i, (_case, key, bp) in enumerate(items[:limit] if limit else items, 1):
        e = read_one(bp, today, fetch, ocr_read)
        cache[key] = e
        rep['read'] += 1
        st = e.get('status')
        rep['priced' if st == 'priced' else 'unconfirmed' if st == 'unconfirmed' else 'gaps'] += 1
        if save and i % 10 == 0:
            LA.save_cache(cache)
    if save and rep['read']:
        LA.save_cache(cache)
    say('pb_mortgage_amounts: queued %d, read %d, priced %d (two readings agree), unconfirmed %d, gaps %d'
        % (rep['queued'], rep['read'], rep['priced'], rep['unconfirmed'], rep['gaps']))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    run(limit=a.limit, dry_run=a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
