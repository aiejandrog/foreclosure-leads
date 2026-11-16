#!/usr/bin/env python3
"""lien_amounts.py -- what an OPEN recorded lien or judgment actually says it is for. All three
counties. Free: county image endpoints + local OCR. Never a payoff.

WHY (2026-11-16, Alex: "see if it's scraping up any hidden liens on the three counties")
The owner searches FIND the hidden liens. They almost never PRICE them, because they read the
county INDEX row and the index mostly publishes no amount:

    Broward     3,763 open non-mortgage liens/judgments with no amount, 121 priced
    Miami-Dade    390 with no amount, 1 priced
    Palm Beach     52 with no amount, 0 priced

An open claim with no amount keeps a chain from ever being a fact (equity_state.state_of), so those
leads sit at "ceiling only" forever. The amount is printed on the recorded document. This reads the
document, every page, and takes the figure off it.

WHERE THE DOCUMENT COMES FROM (each measured, none needs a captcha or a paid call)
    Broward      AcclaimWeb details page -> DocumentPdfAllPages (fl_lp.broward_pin, native curl)
    Miami-Dade   officialrecords DocumentImage (code_lien_amounts.read_lien, via records_index.json)
    Palm Beach   /Document/GetDocumentByBookPage (pb_recorded; the direct link is NOT search-gated)

WHAT COUNTS AS THE AMOUNT (parse_amount)
A figure a phrase labels as the total / the sum / the amount due wins. With no labelled figure the
LARGEST figure in the document is used and flagged `largest_of_n`: on a judgment the grand total is
the largest figure by construction, and too much debt costs a lead while too little reaches a
homeowner. A document with an unread page and no labelled total is NOT priced (the total may be on
the page that was not read). Per-day and per-month figures are never a total.

WHAT IT WRITES
    lien_amounts.json   (gitignored) county:key -> {amt, status, basis, pages, pages_unread, read}
    the county chain files: a priced row gets amt + amt_src='doc', its amount joins the same bucket
    a priced index row would have (irs_open / hoa_open / code_open), and other_open_unpriced drops.
other_liens.rows and records_liens consult lookup() so a re-trace keeps the amount.

    python lien_amounts.py --limit 60            read up to 60 documents, then apply
    python lien_amounts.py --county pb           one county (broward | miami | pb | all)
    python lien_amounts.py --apply-only          no network: re-apply the cache to the chain files
    python lien_amounts.py --dry-run             count the work, read nothing

Output is counts only. No names, addresses or passages are printed or stored.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'lien_amounts.json')
CHAIN_FILES = {'BROWARD': 'broward_liens.json', 'MIAMI-DADE': 'records_liens.json',
               'PALM BEACH': 'palmbeach_liens.json'}
PARSER_VERSION = 1
DEFAULT_LIMIT = 60
PAUSE_S = 1.2
RETRY_GAP_DAYS = 14          # a document the county would not serve is asked for again after this
RESOLVE_CASES = 60           # Broward name searches per run, to find a row's instrument number (443 cases waited on 11-16)
MIN_FIGURE = 100.0
MAX_FIGURE = 50_000_000.0

_MONEY = r'\$\s*((?:\d{1,3}(?:[,.]\d{3})+|\d+)(?:\.\d{2})?)(?![\d,])'
_MONEY_RE = re.compile(_MONEY)
_RATE_AFTER_RE = re.compile(r'^\s*(?:\([^)]{0,30}\)\s*)?(?:per|a|each|/)\s*(?:day|diem|month|year|annum)\b', re.I)
_RATE_BEFORE_RE = re.compile(r'(?:per\s+diem|daily\s+(?:rate|fine|interest)|per\s+day)[^$]{0,25}$', re.I)
_LABEL_RES = [re.compile(p, re.I) for p in (
    r'(?:grand\s+)?total(?:\s+(?:sum|amount|due|judgment|lien|claim|owed|indebtedness))?[^$\n]{0,60}' + _MONEY,
    r'(?:total\s+)?sum\s+of[^$\n]{0,30}' + _MONEY,
    r'in\s+the\s+(?:total\s+|principal\s+|aggregate\s+)?(?:amount|sum)\s+of[^$\n]{0,20}' + _MONEY,
    r'amount\s+(?:due|owed|owing|claimed|of\s+(?:the\s+)?(?:lien|claim|judgment))[^$\n]{0,50}' + _MONEY,
    r'unpaid\s+balance[^$\n]{0,60}' + _MONEY,
    r'(?:shall|do(?:es)?)\s+(?:have\s+and\s+)?recover[^$\n]{0,140}' + _MONEY,
    r'for\s+which\s+(?:sum\s+)?let\s+execution\s+issue[^$\n]{0,40}' + _MONEY,
)]


def _num(s):
    s = str(s or '')
    # OCR writes '21.890.90' for '21,890.90': a dot followed by exactly three digits is a separator
    s = re.sub(r'\.(?=\d{3}(?:[.,]|$))', ',', s)
    try:
        return float(s.replace(',', ''))
    except ValueError:
        return 0.0


def _is_rate(text, m):
    return bool(_RATE_AFTER_RE.match(text[m.end():m.end() + 40])
                or _RATE_BEFORE_RE.search(text[max(0, m.start() - 60):m.start()]))


def parse_amount(text, complete=True):
    """-> {'amt': int or None, 'status', 'basis', 'n_figures', 'ambiguous'}.

    status: 'priced' | 'no_figure' (read, nothing printed) | 'partial' (a page was not read and no
    labelled total was found, so the total may be on the unread page)."""
    t = str(text or '')
    figures = []
    for m in _MONEY_RE.finditer(t):
        v = _num(m.group(1))
        if MIN_FIGURE <= v <= MAX_FIGURE and not _is_rate(t, m):
            figures.append(v)
    labelled = []
    for rx in _LABEL_RES:
        for m in rx.finditer(t):
            v = _num(m.group(1))
            tail = t[m.end():m.end() + 40]
            if MIN_FIGURE <= v <= MAX_FIGURE and not _RATE_AFTER_RE.match(tail):
                labelled.append(v)
    out = {'amt': None, 'status': 'no_figure', 'basis': '', 'n_figures': len(set(figures)), 'ambiguous': False}
    if labelled:
        # a labelled figure smaller than another figure on the page (a subtotal labelled "total
        # costs") must not undercount: the largest figure still wins, the label only vouches for it
        best = max(max(labelled), max(figures) if figures else 0)
        out.update(amt=int(round(best)), status='priced',
                   basis='labelled' if best in labelled else 'largest_over_label',
                   ambiguous=best not in labelled)
        return out
    if not figures:
        out['status'] = 'no_figure' if complete else 'partial'
        return out
    if not complete:
        out['status'] = 'partial'
        return out
    out.update(amt=int(round(max(figures))), status='priced', basis='largest_of_n',
               ambiguous=len(set(figures)) > 1)
    return out


# ---- which rows, and how each is addressed ----------------------------------------------------
# records_liens.analyze defines these two inside the function; same patterns, kept in step by
# _lienamountstest (it reads the analyzer's source and compares).
_MD_CLAIM_DOC_RE = re.compile(r'\bLIEN\b|JUDGMENT|\bWARRANTS?\b', re.I)
_MD_NOT_CLAIM_DOC_RE = re.compile(r'WAIVER|CONTEST|SUBORDINAT|FINANCING STATEMENT|CERTIFICATE OF TITLE', re.I)


def _miami_counted(row):
    """Mirror of the counting filter at the end of records_liens.analyze: a title certificate, a
    financing statement or a waiver is shown and never counted, so it is never priced here either."""
    doc = str(row.get('doc') or '')
    if _MD_NOT_CLAIM_DOC_RE.search(doc):
        return False
    if row.get('kind') == 'other' and not _MD_CLAIM_DOC_RE.search(doc):
        return False
    return True


def wants(county, row):
    """An open claim the chain counted as 'amount not published'."""
    if not isinstance(row, dict) or row.get('amt') or str(row.get('st') or '').upper() != 'OPEN':
        return False
    if row.get('kind') == 'lis_pendens' or row.get('own_case') or row.get('lender_suit'):
        return False
    if county == 'MIAMI-DADE':
        return _miami_counted(row)
    return row.get('counted') == 'unpriced'


def _bp(value):
    m = re.search(r'(\d{1,6})\s*/\s*(\d{1,5})\s*$', str(value or ''))
    if not m or not int(m.group(1)) or not int(m.group(2)):
        return None
    return '%d/%d' % (int(m.group(1)), int(m.group(2)))


def row_key(county, row):
    """The cache key of a row's document, or None when the row cannot be addressed yet."""
    if county == 'BROWARD':
        inst = re.sub(r'\D', '', str(row.get('inst') or ''))
        return ('BROWARD:I:' + inst) if inst.strip('0') else None
    bp = _bp(row.get('bp'))
    return ('%s:BP:%s' % (county, bp)) if bp else None


def bucket(row):
    k = row.get('kind')
    return 'irs_open' if k in ('irs', 'state_tax') else 'hoa_open' if k == 'association' else 'code_open'


# ---- cache ------------------------------------------------------------------------------------
_CACHE = {'mtime': None, 'data': None}


def load_cache(path=None):
    path = path or CACHE
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return {}
    if path == CACHE and _CACHE['mtime'] == mt and _CACHE['data'] is not None:
        return _CACHE['data']
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    if path == CACHE:
        _CACHE.update(mtime=mt, data=data)
    return data


def save_cache(data, path=None):
    path = path or CACHE
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'), sort_keys=True)
    os.replace(tmp, path)
    if path == CACHE:
        _CACHE.update(mtime=None, data=None)


def lookup(county, row, cache=None):
    """The amount read off this row's document, or None. Never raises: an analyzer calls this."""
    try:
        key = row_key(county, row)
        if not key:
            return None
        e = (cache if cache is not None else load_cache()).get(key)
        if isinstance(e, dict) and e.get('status') == 'priced' and e.get('v') == PARSER_VERSION:
            a = e.get('amt')
            return int(a) if a and a > 0 else None
    except Exception:
        pass
    return None


def _due(entry, today):
    """Should this key be read (again)?"""
    if not isinstance(entry, dict):
        return True
    if entry.get('v') != PARSER_VERSION:
        return True
    if entry.get('status') in ('priced', 'no_figure'):
        return False
    try:
        age = (today - datetime.date.fromisoformat(str(entry.get('read') or '')[:10])).days
    except ValueError:
        return True
    return age >= RETRY_GAP_DAYS


# ---- apply to a stored chain --------------------------------------------------------------------
def apply_chain(county, chain, cache):
    """Price the rows the cache can price. Idempotent. -> rows priced on this call."""
    if not isinstance(chain, dict):
        return 0
    n = 0
    for row in chain.get('other') or []:
        if not wants(county, row):
            continue
        amt = lookup(county, row, cache)
        if not amt:
            continue
        row['amt'] = amt
        row['amt_src'] = 'doc'
        key = bucket(row)
        if county != 'MIAMI-DADE':
            row['counted'] = key
        chain[key] = (chain.get(key) or 0) + amt
        chain['other_open_unpriced'] = max(0, int(chain.get('other_open_unpriced') or 0) - 1)
        n += 1
    if n:
        chain['other_doc_priced'] = int(chain.get('other_doc_priced') or 0) + n
    return n


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'))
    os.replace(tmp, path)


def apply_files(cache, counties, here=HERE):
    """-> {county: {'rows': n, 'chains': n, 'cleared': chains with no unpriced claim left}}."""
    out = {}
    for county in counties:
        path = os.path.join(here, CHAIN_FILES[county])
        chains = _load_json(path, None)
        if not isinstance(chains, dict):
            continue
        rows = touched = cleared = 0
        for chain in chains.values():
            got = apply_chain(county, chain, cache)
            if got:
                rows += got
                touched += 1
                if not (chain.get('other_open_unpriced') or 0):
                    cleared += 1
        if rows:
            _save_json(path, chains)
        out[county] = {'rows': rows, 'chains': touched, 'cleared': cleared}
    return out


# ---- reading one document (network lives below this line) ----------------------------------------
def _ocr():
    if os.name != 'nt':
        return None
    import document_store as DS
    return DS.winocr


def _read_bytes(content):
    import document_store as DS
    r = DS.read_pages(content, ocr=_ocr())
    pages = r.get('pages') or []
    text = '\n'.join(p.get('text') or '' for p in pages if p.get('outcome') in ('text', 'ocr_text'))
    unread = [p.get('page') for p in pages if p.get('outcome') not in ('text', 'ocr_text')]
    return text, {'pages': len(pages), 'pages_unread': unread}


class Readers:
    """One reader per county; sessions are opened on first use and reused."""

    def __init__(self):
        self._brw = None
        self._index = None
        self._collector = None

    def broward(self, key, case):
        from fl_lp import broward_pin as pin
        inst = key.split(':')[-1]
        if self._brw is None:
            self._brw = pin.BL.start_session() or False
        if not self._brw:
            return '', {'status': 'gap_session', 'pages': 0, 'pages_unread': []}
        html, detail = pin._details_html(inst)
        if not detail:
            return '', {'status': 'gap_details', 'pages': 0, 'pages_unread': []}
        with tempfile.TemporaryDirectory(prefix='lienamt-') as work:
            pdf = pin._pdf_of(html, inst, work)
            if not pdf:
                return '', {'status': 'gap_pdf', 'pages': 0, 'pages_unread': []}
            with open(pdf, 'rb') as f:
                content = f.read()
        text, meta = _read_bytes(content)
        meta['status'] = 'read'
        return text, meta

    def palm_beach(self, key, case):
        import pb_recorded as PR
        book, page = key.split(':')[-1].split('/')
        try:
            content = PR.by_book_page(book, page)
        except PR.Unavailable:
            return '', {'status': 'gap_pdf', 'pages': 0, 'pages_unread': []}
        text, meta = _read_bytes(content)
        meta['status'] = 'read'
        return text, meta

    def miami(self, key, case):
        import code_lien_amounts as CLA
        import document_walk as W
        if self._index is None:
            self._index = W.RecordIndex()
        book, page = key.split(':')[-1].split('/')
        text, meta = CLA.read_lien(case, book, page, self._index, collector=self._collector, ocr=_ocr())
        st = meta.get('status')
        return text, {'status': 'read' if st == 'read' else 'gap_' + str(st),
                      'pages': meta.get('pages') or 0, 'pages_unread': meta.get('pages_unread') or []}

    def read(self, county, key, case):
        fn = {'BROWARD': self.broward, 'PALM BEACH': self.palm_beach, 'MIAMI-DADE': self.miami}[county]
        return fn(key, case)


def read_one(county, key, case, readers, today):
    try:
        text, meta = readers.read(county, key, case)
    except Exception as exc:                      # one bad document never stops the run
        return {'status': 'gap_error', 'why': type(exc).__name__, 'read': today.isoformat(), 'v': PARSER_VERSION}
    entry = {'read': today.isoformat(), 'v': PARSER_VERSION, 'pages': meta.get('pages') or 0,
             'pages_unread': meta.get('pages_unread') or []}
    if meta.get('status') != 'read':
        entry['status'] = meta.get('status') or 'gap'
        return entry
    if not text.strip():
        entry['status'] = 'gap_unreadable'
        return entry
    parsed = parse_amount(text, complete=not entry['pages_unread'])
    entry.update(status=parsed['status'], amt=parsed['amt'], basis=parsed['basis'],
                 n_figures=parsed['n_figures'], ambiguous=parsed['ambiguous'])
    return entry


# ---- the work list --------------------------------------------------------------------------------
def live_cases(here=HERE):
    """Cases on a board right now: lis pendens leads and auction leads."""
    cases = {}
    for r in _load_json(os.path.join(here, 'lp_leads.json'), []) or []:
        if isinstance(r, dict) and r.get('case'):
            cases[str(r['case']).strip().upper()] = r
    for r in _load_json(os.path.join(here, 'leads_final.json'), []) or []:
        if isinstance(r, dict):
            c = str(r.get('Case #') or r.get('case') or '').strip().upper()
            if c:
                cases.setdefault(c, r)
    return cases


def worklist(counties, cache, today, here=HERE, live=None):
    """-> (items, unaddressed). items: (county, case, key) in reading order, one per document;
    unaddressed: {county: [case, ...]} for rows whose document cannot be addressed yet."""
    live = live_cases(here) if live is None else live
    items, seen, unaddr = [], set(), {}
    for county in counties:
        chains = _load_json(os.path.join(here, CHAIN_FILES[county]), {})
        if not isinstance(chains, dict):
            continue
        for case, chain in chains.items():
            if not isinstance(chain, dict) or (live and str(case).strip().upper() not in live):
                continue
            rows = [r for r in chain.get('other') or [] if wants(county, r)]
            if not rows:
                continue
            rank = (0 if chain.get('conf') == 'ok' else 1, len(rows))
            for r in rows:
                key = row_key(county, r)
                if not key:
                    unaddr.setdefault(county, [])
                    if case not in unaddr[county]:
                        unaddr[county].append(case)
                    continue
                if key in seen or not _due(cache.get(key), today):
                    continue
                seen.add(key)
                items.append((rank, str(r.get('d') or ''), county, case, key))
    items.sort(key=lambda x: (x[0], [-ord(ch) for ch in x[1]]))
    return [(c, case, key) for _rank, _d, c, case, key in items], unaddr


def resolve_broward(cases, here=HERE, limit=RESOLVE_CASES, pause=PAUSE_S, search=None):
    """Chains traced before 2026-11-16 kept no instrument number on their lien rows, and Broward's
    image route needs one. One free name search per case finds it again. -> rows resolved."""
    import broward_liens as BL
    path = os.path.join(here, CHAIN_FILES['BROWARD'])
    chains = _load_json(path, {})
    live = live_cases(here)
    sess = None
    done = 0
    for case in cases[:limit]:
        chain, lead = chains.get(case), live.get(str(case).strip().upper())
        if not isinstance(chain, dict) or not lead:
            continue
        name = BL._search_name({'owners': lead.get('owners') or lead.get('Owner(s)') or chain.get('owner') or ''})
        if not name:
            continue
        if search is None:
            if sess is None:
                sess = BL.start_session() or False
            if not sess:
                break
            docs = BL.search_docs(sess, name)
            time.sleep(pause)
        else:
            docs = search(name)
        by = {}
        for d in docs or []:
            inst = str(d.get('InstrumentNumber') or '').strip()
            if not inst.strip('0'):
                continue
            k = (BL._jsdate(d.get('RecordDate')), (d.get('DocTypeDescription') or '').upper().strip()[:40],
                 (d.get('CrossPartyName') or '').strip()[:40])
            by.setdefault(k, set()).add(inst)
        for r in chain.get('other') or []:
            if r.get('inst') or not wants('BROWARD', r):
                continue
            hit = by.get((r.get('d') or '', r.get('doc') or '', r.get('party') or ''))
            if hit and len(hit) == 1:            # two instruments with one description: never guess
                r['inst'] = next(iter(hit))
                done += 1
    if done:
        _save_json(path, chains)
    return done


def run(counties, limit=DEFAULT_LIMIT, dry_run=False, apply_only=False, here=HERE, today=None,
        readers=None, pause=PAUSE_S):
    today = today or datetime.date.today()
    cache = dict(load_cache())
    report = {'read': 0, 'priced': 0, 'no_figure': 0, 'partial': 0, 'gaps': 0, 'by_county': {}}
    if not apply_only:
        items, unaddr = worklist(counties, cache, today, here)
        report['queued'] = len(items)
        report['unaddressed_cases'] = {c: len(v) for c, v in unaddr.items()}
        if not dry_run and unaddr.get('BROWARD'):
            report['broward_rows_resolved'] = resolve_broward(unaddr['BROWARD'], here)
            if report['broward_rows_resolved']:
                items, unaddr = worklist(counties, cache, today, here)
                report['queued'] = len(items)
        if not dry_run:
            readers = readers or Readers()
            for county, case, key in items:
                if report['read'] >= limit:
                    break
                entry = read_one(county, key, case, readers, today)
                cache[key] = entry
                if entry.get('status') == 'gap_not_in_index':
                    # no network was used: Miami's image route needs a CFN the owner search has not
                    # indexed yet. Counted apart so it never eats the run's document limit.
                    report['not_indexed'] = report.get('not_indexed', 0) + 1
                    continue
                report['read'] += 1
                st = entry.get('status')
                slot = st if st in ('priced', 'no_figure', 'partial') else 'gaps'
                report[slot] += 1
                bc = report['by_county'].setdefault(county, {'read': 0, 'priced': 0})
                bc['read'] += 1
                bc['priced'] += 1 if st == 'priced' else 0
                if report['read'] % 10 == 0:
                    save_cache(cache)
                time.sleep(pause)
            save_cache(cache)
    if not dry_run:
        report['applied'] = apply_files(cache, counties, here)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read the amount off open recorded liens and judgments.')
    ap.add_argument('--county', default='all', choices=['all', 'broward', 'miami', 'pb'])
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--apply-only', action='store_true')
    a = ap.parse_args(argv)
    counties = {'all': ['BROWARD', 'MIAMI-DADE', 'PALM BEACH'], 'broward': ['BROWARD'],
                'miami': ['MIAMI-DADE'], 'pb': ['PALM BEACH']}[a.county]
    rep = run(counties, limit=a.limit, dry_run=a.dry_run, apply_only=a.apply_only)
    print('lien_amounts: queued %s, read %d, priced %d, no figure %d, partial %d, gaps %d'
          % (rep.get('queued', '-'), rep['read'], rep['priced'], rep['no_figure'], rep['partial'], rep['gaps']))
    for c, v in sorted((rep.get('by_county') or {}).items()):
        print('  %-11s read %d, priced %d' % (c, v['read'], v['priced']))
    if rep.get('not_indexed'):
        print('  MIAMI-DADE  %d document(s) have no index entry yet (read after the owner search indexes them)'
              % rep['not_indexed'])
    for c, n in sorted((rep.get('unaddressed_cases') or {}).items()):
        print('  %-11s %d case(s) hold a lien row with no document reference yet' % (c, n))
    if 'broward_rows_resolved' in rep:
        print('  BROWARD     %d row(s) matched to an instrument number' % rep['broward_rows_resolved'])
    for c, v in sorted((rep.get('applied') or {}).items()):
        print('  %-11s applied: %d row(s) on %d chain(s); %d chain(s) now have every open claim priced'
              % (c, v['rows'], v['chains'], v['cleared']))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
