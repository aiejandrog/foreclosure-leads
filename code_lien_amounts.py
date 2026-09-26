#!/usr/bin/env python3
"""code_lien_amounts.py -- C2: what a recorded Miami-Dade code-enforcement lien SAYS, and an
ESTIMATE of what it has grown to. Never a payoff. $0.

WHY
code_liens.py flags a recorded code lien from the county's CCVIOL layer (LN_RECBOOK/LN_RECPAGE),
and that layer has NO amount field. The board has shown "CODE LIEN" with no dollar figure, which
is the junior lien that turns a 90%-equity lead into a loss. The amount is on the recorded lien
itself: the county records an order/lien that prints the fine, the daily rate, and when the daily
fine starts. This reads that document and does the arithmetic.

WHAT IT DOES, cheapest first, spending nothing
  1. Every recorded lien in code_liens.json (folio -> hits with lienRef 'BOOK/PAGE').
  2. Book/page -> a fetchable Official Records record. The image endpoint needs the recording's
     CFN, and book/page alone answers "No pages found" (checked 2026-09-26). So the CFN comes
     from records_index.json (document_walk.RecordIndex), which records_liens' nightly owner
     searches now feed (index_models, below): the owner's own search returns the code lien with
     its CFN, for free. A lien not in the index yet is a NAMED gap, never a guess.
  3. The recorded PDF is fetched from the anonymous OR image endpoint (the same
     document_collectors.MiamiCollector path the document stage uses; no login, no captcha, no
     Advanced Search, no units) and read: embedded text first, then free local OCR when available.
  4. parse_lien_text() finds the recorded amount, the daily rate and the accrual start;
     estimate() computes the accrual to today.

WHAT IT NEVER DOES
  - It never produces a payoff. Every row says kind='ESTIMATE' and is_payoff=False, and the label
    says so in words. A real balance needs the county payoff/estoppel letter ($78/folio) or lien
    research ($312/folio), which are per-deal spends for Alejandro to approve.
  - It never touches equity. The estimate rides next to the CODE LIEN chip as a warning; the equity
    number stays built from recorded instruments only (equity_state, CLAUDE.md).
  - It never spends: no paid search, no captcha solve, no vision call. OCR is the free local one.

OUTPUT  code_lien_amounts.json (gitignored; county records keyed by book/page, no contact data).
        The text of each lien is NOT kept here, only the short matched snippets and the numbers.

Run:  python code_lien_amounts.py                 # read up to --limit new liens, re-estimate all
      python code_lien_amounts.py --limit 10
      python code_lien_amounts.py --no-fetch      # re-estimate what is cached, no county request
      python code_lien_amounts.py --plan          # what it would fetch; no network
"""
import datetime as dt
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_LIENS = os.path.join(HERE, 'code_liens.json')
OUT = os.path.join(HERE, 'code_lien_amounts.json')
COUNTY = 'MIAMI-DADE'
DEFAULT_LIMIT = 25          # documents fetched per run; the county image endpoint is free but shared
PAUSE_S = 1.0               # between fetches
PARSER_VERSION = 1          # bump when parse_lien_text changes, so cached parses are redone

LABEL = ('ESTIMATE, not a payoff: computed from the recorded lien (amount + daily fine x days). '
         'Fines stop when compliance is certified and the county can reduce or settle them, so the '
         'real balance can be lower or higher. Order the county payoff/estoppel letter ($78/folio) '
         'before relying on it.')

# ---------------------------------------------------------------------------------------------
# Parsing. Pure: text in, numbers out. Covered by _codelienamounttest.py.
# ---------------------------------------------------------------------------------------------
_MONEY = r'\$\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)'
_MONTHS = ('january|february|march|april|may|june|july|august|september|october|november|'
           'december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec')
_DATE = r'(\d{1,2}/\d{1,2}/\d{2,4}|(?:%s)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})' % _MONTHS
_PER_DAY = r'(?:\s*\([^)]{0,40}\))?\s*(?:per|a|each|for\s+each|for\s+every)\s+(?:calendar\s+)?day'

_DAILY_RES = [
    re.compile(_MONEY + _PER_DAY, re.I),
    re.compile(r'(?:daily|per\s+diem)\s+(?:fine|penalty|rate|amount|civil\s+penalty)s?'
               r'\s*(?:of|in\s+the\s+amount\s+of|at|:)?\s*' + _MONEY, re.I),
]
# A labelled TOTAL beats any other figure; a fine/penalty figure is a candidate.
_TOTAL_RES = [
    re.compile(r'(?:total\s+(?:amount\s+)?(?:due|owed|of\s+(?:the\s+)?lien|lien\s+amount)?'
               r'|lien\s+amount|amount\s+of\s+(?:the\s+)?lien|sum\s+total)\s*(?:of|is|:)?\s*' + _MONEY, re.I),
    re.compile(r'(?:lien|claim\s+of\s+lien)\s+(?:is\s+)?(?:hereby\s+)?(?:imposed\s+)?(?:in\s+the\s+)?'
               r'(?:total\s+)?(?:amount|sum)\s+of\s+' + _MONEY, re.I),
]
_AMOUNT_RES = [
    re.compile(r'(?:in\s+the\s+(?:total\s+|principal\s+)?(?:amount|sum)\s+of)\s*' + _MONEY, re.I),
    re.compile(r'(?:civil\s+penalt(?:y|ies)|fines?|penalt(?:y|ies)|administrative\s+(?:costs?|fees?))'
               r'\s+(?:totall?ing|of|in\s+the\s+amount\s+of|:)\s*' + _MONEY, re.I),
]
_START_RE = re.compile(r'(?:commenc\w*|beginning|begin|starting|start|effective|accru\w*'
                       r'(?:\s+(?:from|as\s+of|beginning|on))?|from)\s+(?:on\s+)?(?:and\s+after\s+)?'
                       + _DATE, re.I)


def _money(s):
    try:
        return round(float(str(s).replace(',', '')), 2)
    except (TypeError, ValueError):
        return None


def parse_date(s):
    """'03/14/2025', '3/14/25', 'March 14, 2025', 'Mar. 14th 2025' -> date, or None."""
    s = re.sub(r'(\d)(st|nd|rd|th)\b', r'\1', str(s or '').strip().replace('.', ''), flags=re.I)
    s = re.sub(r'\s+', ' ', s).replace(' ,', ',')
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y'):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    if s.lower().startswith('sept'):
        return parse_date('Sep' + s[4:])
    return None


def _snip(text, m, width=70):
    a, b = max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)
    return re.sub(r'\s+', ' ', text[a:b]).strip()


def _is_per_day(text, m):
    """True when a money match is immediately a per-day figure (so it is not a total)."""
    return bool(re.match(_PER_DAY, text[m.end():m.end() + 60], re.I))


def parse_lien_text(text):
    """-> dict(recorded_amount, daily_rate, accrual_start, candidates, basis, ambiguous).

    Conservative on purpose: a figure is used only when a phrase labels it. Several different
    unlabelled figures and no TOTAL is reported as ambiguous with every candidate listed, and the
    largest is used (too much debt, never too little -- the same rule records_liens follows)."""
    t = str(text or '')
    out = {'recorded_amount': None, 'daily_rate': None, 'accrual_start': None,
           'candidates': [], 'basis': [], 'ambiguous': False}
    daily = []
    for rx in _DAILY_RES:
        for m in rx.finditer(t):
            v = _money(m.group(1))
            if v and v > 0:
                daily.append((m.start(), v, m))
    if daily:
        daily.sort(key=lambda x: x[0])
        rates = sorted({v for _, v, _ in daily})
        # Two different daily figures (e.g. $250 first violation, $500 repeat) -> the larger, flagged.
        out['daily_rate'] = rates[-1]
        if len(rates) > 1:
            out['ambiguous'] = True
            out['basis'].append('several daily rates printed %s; the largest is used' % rates)
        out['basis'].append('daily rate: "%s"' % _snip(t, daily[0][2]))
        # The start date is the one printed NEAR the daily rate, if any.
        near = t[daily[0][0]: daily[0][0] + 300]
        sm = _START_RE.search(near) or _START_RE.search(t[max(0, daily[0][0] - 200): daily[0][0]])
        if sm:
            d = parse_date(sm.group(1))
            if d:
                out['accrual_start'] = d.isoformat()
                out['basis'].append('accrual start: "%s"' % _snip(near if _START_RE.search(near) else t, sm))
    # A figure already read as the daily rate is never also a total ("a daily fine of $100 shall
    # accrue" is one figure, not two).
    daily_at = {m.start(1) for _, _, m in daily}
    totals = []
    for rx in _TOTAL_RES:
        for m in rx.finditer(t):
            v = _money(m.group(1))
            if v and v > 0 and not _is_per_day(t, m) and m.start(1) not in daily_at:
                totals.append((v, m))
    cands = []
    for rx in _AMOUNT_RES:
        for m in rx.finditer(t):
            v = _money(m.group(1))
            if v and v > 0 and not _is_per_day(t, m) and m.start(1) not in daily_at:
                cands.append((v, m))
    out['candidates'] = sorted({v for v, _ in totals + cands})
    if totals:
        v, m = max(totals, key=lambda x: x[0])
        out['recorded_amount'] = v
        out['basis'].append('recorded total: "%s"' % _snip(t, m))
    elif cands:
        v, m = max(cands, key=lambda x: x[0])
        out['recorded_amount'] = v
        if len({c for c, _ in cands}) > 1:
            out['ambiguous'] = True
            out['basis'].append('no labelled total; several figures %s, the largest is used'
                                % sorted({c for c, _ in cands}))
        out['basis'].append('recorded amount: "%s"' % _snip(t, m))
    return out


def estimate(parsed, rec_date=None, as_of=None):
    """The accrual arithmetic. -> dict with status, estimate_total and how it was computed.

    status: 'estimated'             daily rate found; total = recorded amount + rate x days
            'recorded_amount_only'  an amount but no daily rate; the recorded figure is a FLOOR
            'no_amount_found'       nothing labelled in the text
    Days run from the stated accrual start, or, when the lien prints a total (which already counts
    the fines up to the order), from the recording date. Never negative."""
    as_of = as_of or dt.date.today()
    rec = parse_date(rec_date) if isinstance(rec_date, str) else rec_date
    amt, rate = parsed.get('recorded_amount'), parsed.get('daily_rate')
    start = parse_date(parsed['accrual_start']) if parsed.get('accrual_start') else None
    row = {'kind': 'ESTIMATE', 'is_payoff': False, 'label': LABEL, 'as_of': as_of.isoformat(),
           'recorded_amount': amt, 'daily_rate': rate, 'accrual_from': None, 'days': None,
           'accrued': None, 'estimate_total': None, 'how': '', 'ambiguous': bool(parsed.get('ambiguous'))}
    if rate:
        if amt and rec:
            frm, how = rec, 'recorded amount + daily rate x days since recording'
            if start and start > rec:
                frm, how = start, 'recorded amount + daily rate x days since the stated start'
        elif start:
            frm, how = start, 'daily rate x days since the stated start'
        elif rec:
            frm, how = rec, ('daily rate x days since recording (no start date printed; '
                             'understates if the fines began earlier)')
        else:
            frm, how = None, ''
        if frm is not None:
            days = max(0, (as_of - frm).days)
            accrued = round(rate * days, 2)
            row.update(status='estimated', accrual_from=frm.isoformat(), days=days, accrued=accrued,
                       estimate_total=round((amt or 0) + accrued, 2), how=how)
            return row
    if amt:
        row.update(status='recorded_amount_only', estimate_total=amt,
                   how='recorded amount only; no daily rate printed, so this is a floor')
        return row
    row.update(status='no_amount_found', how='no labelled amount or daily rate in the lien text')
    return row


# ---------------------------------------------------------------------------------------------
# The index hook records_liens calls. Recording keys only (county metadata), never lead data.
# ---------------------------------------------------------------------------------------------
_INDEX = None


def index_models(models, save=True):
    """Add an OR search's recordingModels to records_index.json so a later book/page lookup has a
    CFN. Never raises: a failed index write must not cost records_liens its case."""
    global _INDEX
    try:
        import document_walk as W
        if _INDEX is None:
            _INDEX = W.RecordIndex()
        added = _INDEX.add_models(models or [])
        if save and added:
            _INDEX.save()
        return added
    except Exception:
        return 0


# ---------------------------------------------------------------------------------------------
# Fetch + read. Network lives here and only here.
# ---------------------------------------------------------------------------------------------
def recorded_liens(code_liens):
    """code_liens.json -> [(folio, code_case, book, page)], one per recorded instrument."""
    seen, out = set(), []
    for folio, hits in (code_liens or {}).items():
        for h in hits or []:
            ref = str(h.get('lienRef') or '').strip()
            if not h.get('lien') or '/' not in ref:
                continue
            book, page = [x.strip() for x in ref.split('/', 1)]
            if not (book.isdigit() and page.isdigit()):
                continue
            key = '%s/%s' % (int(book), int(page))
            if key in seen:
                continue
            seen.add(key)
            out.append((folio, h.get('case') or '', str(int(book)), str(int(page))))
    return out


def _default_ocr():
    """Free local OCR only. Windows' built-in OCR via the PowerShell bridge document_store already
    uses; None elsewhere, or when DEALFLOW_CODELIEN_OCR=0. A page OCR cannot read stays a named
    'needs_ocr' gap in the reading -- it is never read as 'no amount'."""
    if os.environ.get('DEALFLOW_CODELIEN_OCR', '1') == '0' or os.name != 'nt':
        return None
    import document_store as DS
    return DS.winocr


def read_lien(folio, book, page, index, collector=None, ocr=None):
    """-> (text, meta). meta.status is 'read' or a named gap; text is '' on a gap."""
    import document_walk as W
    hit = index.get(book, page)
    if not hit:
        return '', {'status': 'not_in_index',
                    'reason': ('book/page %s/%s is not in records_index.json yet, so its CFN is '
                               'unknown and the image endpoint cannot address it. It is indexed the '
                               'next time the owner\'s nightly records_liens search returns it.'
                               % (book, page))}
    import miami_judgment as MJ
    rows = MJ.collect_recorded('CODELIEN-' + str(folio), [dict(hit)], collector=collector,
                               county=COUNTY, ocr=ocr)
    row = rows[0] if rows else {}
    if row.get('status') != 'stored':
        return '', {'status': 'fetch_' + str(row.get('status') or 'missing'),
                    'reason': str(row.get('reason') or 'no document row')[:200]}
    reading = row.get('reading') or {}
    pages = reading.get('pages') or []
    text = '\n'.join(p.get('text') or '' for p in pages if p.get('outcome') in ('text', 'ocr_text'))
    unread = [p.get('page') for p in pages if p.get('outcome') not in ('text', 'ocr_text', 'read_as_label')]
    return text, {'status': 'read' if text.strip() else 'unreadable',
                  'pages': len(pages), 'pages_unread': unread,
                  'doc_type': hit.get('doC_TYPE'), 'rec_date': (hit.get('reC_DATE') or '')[:10],
                  'reason': '' if text.strip() else 'no readable text (OCR unavailable or failed)'}


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def run(limit=DEFAULT_LIMIT, fetch=True, as_of=None, code_liens=None, out_path=OUT,
        collector=None, ocr='default', index=None, pause=PAUSE_S, log=print):
    """One pass. Returns the result dict that was written."""
    as_of = as_of or dt.date.today()
    code_liens = _load(CODE_LIENS, {}) if code_liens is None else code_liens
    prior = _load(out_path, {})
    rows = dict(prior.get('liens') or {}) if isinstance(prior, dict) else {}
    liens = recorded_liens(code_liens)
    if index is None:
        import document_walk as W
        index = W.RecordIndex()
    if ocr == 'default':
        ocr = _default_ocr()
    fetched = 0
    for folio, case, book, page in liens:
        key = '%s/%s' % (book, page)
        old = rows.get(key) or {}
        done = old.get('parser_version') == PARSER_VERSION and old.get('read_status') == 'read'
        if not done and fetch and fetched < limit and (old.get('read_status') != 'read'):
            text, meta = read_lien(folio, book, page, index, collector=collector, ocr=ocr)
            if meta['status'] != 'not_in_index':
                fetched += 1
                if pause:
                    time.sleep(pause)
            old = {'folio': folio, 'code_case': case, 'book': book, 'page': page,
                   'read_status': meta['status'], 'read_reason': meta.get('reason') or '',
                   'rec_date': meta.get('rec_date') or old.get('rec_date') or '',
                   'doc_type': meta.get('doc_type') or '', 'pages_unread': meta.get('pages_unread') or [],
                   'parser_version': PARSER_VERSION, 'read_on': as_of.isoformat(),
                   'parsed': parse_lien_text(text) if text else None}
        old.setdefault('folio', folio)
        old.setdefault('code_case', case)
        old.setdefault('book', book)
        old.setdefault('page', page)
        old.setdefault('read_status', 'not_read_yet')
        if old.get('parsed'):
            old['estimate'] = estimate(old['parsed'], old.get('rec_date') or None, as_of)
        else:
            old['estimate'] = None
        rows[key] = old
    counts = {}
    for r in rows.values():
        k = (r.get('estimate') or {}).get('status') or r.get('read_status')
        counts[k] = counts.get(k, 0) + 1
    result = {'_about': 'C2 code-lien amounts. Every figure is an ESTIMATE, never a payoff. '
                        'Built by code_lien_amounts.py; see its docstring.',
              'as_of': as_of.isoformat(), 'recorded_liens': len(liens), 'fetched_this_run': fetched,
              'counts': counts, 'liens': rows}
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, indent=1)
    os.replace(tmp, out_path)
    log('code_lien_amounts: %d recorded lien(s), %d fetched this run, %s'
        % (len(liens), fetched, ', '.join('%s %d' % kv for kv in sorted(counts.items())) or 'none'))
    return result


def for_board(result):
    """book/page -> the small dict the board chip shows. Only real estimates ride; a gap does not."""
    out = {}
    for key, r in ((result or {}).get('liens') or {}).items():
        e = r.get('estimate') or {}
        if e.get('status') in ('estimated', 'recorded_amount_only') and e.get('estimate_total'):
            out[key] = {'est': round(e['estimate_total']), 'estKind': 'ESTIMATE',
                        'estHow': e.get('how') or '', 'estAsOf': e.get('as_of') or '',
                        'estFloor': e['status'] == 'recorded_amount_only'}
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    limit = DEFAULT_LIMIT
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1])
    if '--plan' in args:
        liens = recorded_liens(_load(CODE_LIENS, {}))
        print('code_lien_amounts --plan: %d recorded code lien(s) in code_liens.json; would fetch up '
              'to %d not yet read, from the anonymous OR image endpoint, at $0. No network now.'
              % (len(liens), limit))
        return 0
    run(limit=limit, fetch='--no-fetch' not in args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
