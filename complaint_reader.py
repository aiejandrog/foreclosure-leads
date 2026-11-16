#!/usr/bin/env python3
"""complaint_reader.py -- read the foreclosure COMPLAINT on a lis pendens lead, every page. $0.

WHY (2026-11-16, Alex: "see if it's picking up on the complaint documents on the prospect's
dockets ... I want to see if it's reading the whole complaint PDF")
It was not. A lis pendens row came from the recorder's INDEX (date, case, parties, instrument) and
nothing opened the court file, so a fresh filing showed "Owed $0" while the complaint sitting on
the docket states the principal the lender claims, the date the borrower stopped paying, and every
other party the lender had to name because it holds a lien (the "hidden liens": a second mortgage,
an association, the IRS, a judgment creditor).

WHAT IS READ, PER COUNTY (each route measured live 2026-11-16)
    Miami-Dade   the complaint off the court docket (document_collectors.MiamiCollector: plain
                 requests, no login, no captcha). Runs unattended.
    Palm Beach   the complaint off eCaseView. The court site rejects a scripted browser at the
                 guest login ("reCAPTCHA failed") and admits a person's own Chrome with no
                 challenge, so the PDF is fetched in the operator's Chrome and posted to
                 pb_court_inbox.py; this module reads what landed in the inbox.
    Broward      the court docket (browardclerk.org) refused the case search in a driven browser
                 ("Your request could not be completed", Cloudflare check never cleared) and has
                 never answered a script. What IS free is the recorded LIS PENDENS notice itself
                 (the instrument number is on the lead): it names the mortgage being foreclosed.
                 That is read and tied to the recorded chain. kind='lp_notice', never 'complaint'.

EVERY PAGE. document_store.read_pages gives each page an outcome; `pages` and `pages_read` are
stored and a figure is only taken from pages that were read. Exhibits are read too: the note's
rate and original amount are usually on Exhibit A.

WHAT IS NEVER DONE
A principal balance is what the lender PLEADS on the filing date. It is not a payoff: interest,
escrow advances, late charges and fees ride on top. It is stamped as `cmp.p` and labelled
"complaint claims", and no equity gate reads it.

    python complaint_reader.py --limit 15            Miami complaints + Broward notices + PB inbox
    python complaint_reader.py --county miami --case 2026-003288-CA-01
    python complaint_reader.py --dry-run             count the work, read nothing

Output is counts only. complaints.json holds party names and stays out of the repo (gitignored).
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'complaints.json')
LP_LEADS = os.path.join(HERE, 'lp_leads.json')
PARSER_VERSION = 1
DEFAULT_LIMIT = 15
PAUSE_S = 1.5
RETRY_GAP_DAYS = 7

_MONEY = r'\$\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)'
_MONTHS = ('january|february|march|april|may|june|july|august|september|october|november|december|'
           'jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec')
_DATE = r'((?:%s)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})' % _MONTHS

# A balance the lender says is OWED today. "principal amount of $X" alone describes the original
# loan (it is how a complaint recites the note), so it is never read as the balance claimed.
_PRINCIPAL_RES = [re.compile(p, re.I | re.S) for p in (
    r'(?:unpaid|outstanding|remaining|current)\s+principal(?:\s+(?:balance|sum|amount))?[^$]{0,160}?' + _MONEY,
    r'principal\s+balance[^$]{0,200}?' + _MONEY,
    r'' + _MONEY + r'[^.$]{0,60}?(?:that\s+is\s+due\s+(?:on|in|as)\s+principal|in\s+principal|of\s+principal|as\s+principal)',
    r'(?:owes|owed|is\s+due|due\s+and\s+owing|is\s+owed)[^$]{0,160}?' + _MONEY + r'[^.$]{0,80}?principal',
    r'(?:owes|owed|due\s+and\s+owing|is\s+owed)[^$.]{0,120}?principal[^$.]{0,80}?' + _MONEY,
)]
_DEFAULT_RES = [re.compile(p, re.I | re.S) for p in (
    r'(?:payment|installment)s?\s+(?:that\s+(?:was|were|came)\s+)?due\s+(?:on|for)?\s*' + _DATE,
    r'default(?:ed)?[^.]{0,220}?' + _DATE,
    r'(?:since|from)\s+' + _DATE + r'[^.]{0,80}?(?:no|not)\s+(?:payment|been\s+paid)',
)]
_NOTE_RES = [re.compile(p, re.I | re.S) for p in (
    r'(?:promissory\s+)?note[^.]{0,160}?(?:dated|on)\s+' + _DATE,
    r'on\s+(?:or\s+about\s+)?' + _DATE + r'[^.]{0,200}?executed\s+and\s+delivered[^.]{0,80}?note',
)]
_ORIG_RES = [re.compile(p, re.I | re.S) for p in (
    r'(?:note|mortgage|loan)[^.$]{0,160}?in\s+the\s+(?:original\s+)?(?:principal\s+)?(?:amount|sum)\s+of\s+' + _MONEY,
    r'(?:original|initial)\s+principal\s+(?:amount|sum|balance)[^$]{0,60}?' + _MONEY,
)]
_RATE_RE = re.compile(r'(?:interest|yearly\s+rate|annual\s+rate|rate\s+of)[^.%]{0,80}?(\d{1,2}(?:\.\d{1,4})?)\s*%', re.I | re.S)
_BOOKPAGE_RE = re.compile(r'(?:official\s+records?\s+|O\.?\s*R\.?\s+)?book\s+(\d{3,6})\s*,?\s*(?:at\s+)?pages?\s+(\d{1,5})', re.I)
# the MORTGAGE's recording reference: Official Records only (a plat book is 'Plat Book 136, Page 82')
_MTG_BOOKPAGE_RE = re.compile(r'mortgage[^.]{0,400}?(?:official\s+records?|O\.?\s*R\.?)\s+book\s+(\d{3,6})\s*,?\s*(?:at\s+)?pages?\s+(\d{1,5})', re.I | re.S)
_OR_BOOKPAGE_RE = re.compile(r'(?:official\s+records?|O\.?\s*R\.?)\s+book\s+(\d{3,6})\s*,?\s*(?:at\s+)?pages?\s+(\d{1,5})', re.I)
_INSTR_RE = re.compile(r'(?:instrument|clerk\'?s?\s+file|c\.?f\.?n\.?)\s*(?:no\.?|number|#)?\s*:?\s*(\d{8,14})', re.I)
# "Defendant X may claim some interest in the property by virtue of a mortgage recorded in ..."
_JUNIOR_RE = re.compile(r'\bmay\s+(?:have\s+or\s+)?claim\s+(?:some|an|any)?\s*'
                        r'(?:right,?\s+title,?\s+(?:or|and)\s+)?(?:interest|lien)([^.]{0,420})\.', re.I | re.S)
_BASIS = (
    ('mortgage', r'\bmortgage\b|home\s+equity|line\s+of\s+credit|\bHELOC\b'),
    ('irs', r'internal\s+revenue|united\s+states|federal\s+tax'),
    ('association', r'association|declaration|assessments?|condominium|homeowners'),
    ('judgment', r'judgment'),
    ('lien', r'\blien\b|code\s+enforcement|\bcity\s+of\b|\bcounty\b'),
    ('tenant', r'tenant|possession|occupan'),
    ('spouse', r'spouse|marital|homestead'),
)


def _money(s):
    try:
        return float(str(s).replace(',', ''))
    except ValueError:
        return 0.0


def parse_date(s):
    s = re.sub(r'(\d)(?:st|nd|rd|th)\b', r'\1', str(s or '')).replace('.', '').strip()
    for fmt in ('%B %d, %Y', '%B %d %Y', '%b %d, %Y', '%b %d %Y', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.datetime.strptime(' '.join(s.split()), fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r'(sept)\s+(.*)', s, re.I)
    return parse_date('Sep ' + m.group(2)) if m else None


def _first(res, text, conv, lo=None, hi=None):
    for rx in res:
        for m in rx.finditer(text):
            v = conv(m.group(1))
            if v and (lo is None or v >= lo) and (hi is None or v <= hi):
                return v, m
    return None, None


def _basis_of(text):
    low = text.lower()
    return next((kind for kind, pat in _BASIS if re.search(pat, low)), 'unknown')


def _party_of(lead_in):
    """The defendant named just before 'may claim an interest', as written. Private file only."""
    s = ' '.join(lead_in.split())
    hits = list(re.finditer(r'\bdefendants?\b,?\s+', s, re.I))
    if hits:
        return s[hits[-1].end():].strip(' ,')[:90]
    m = None
    return (m.group(1).strip(' ,') if m else s[-90:].strip(' ,'))[:90]


def parse_complaint(pages):
    """pages: [text of page 1, text of page 2, ...] ('' for a page that was not read).

    -> {'principal', 'default_date', 'note_date', 'orig_amount', 'rate', 'mtg_bp', 'mtg_inst',
        'juniors': [{'who', 'basis', 'bp', 'amt'}], 'is_complaint'}
    Every value is None / [] when the text does not state it: nothing is inferred."""
    text = '\n'.join(str(p or '') for p in pages)
    flat = re.sub(r'[ \t\r\f]+', ' ', text)
    out = {'principal': None, 'default_date': None, 'note_date': None, 'orig_amount': None,
           'rate': None, 'mtg_bp': None, 'mtg_inst': None, 'juniors': [],
           'is_complaint': bool(re.search(r'\bcomplaint\b', flat[:6000], re.I))}
    v, _m = _first(_PRINCIPAL_RES, flat, _money, lo=1000, hi=50_000_000)
    out['principal'] = round(v, 2) if v else None
    d, _m = _first(_DEFAULT_RES, flat, parse_date)
    out['default_date'] = d
    d, _m = _first(_NOTE_RES, flat, parse_date)
    out['note_date'] = d
    v, _m = _first(_ORIG_RES, flat, _money, lo=1000, hi=50_000_000)
    out['orig_amount'] = round(v, 2) if v else None
    m = _RATE_RE.search(flat)
    if m:
        try:
            r = float(m.group(1))
            out['rate'] = r if 0 < r < 25 else None
        except ValueError:
            pass
    m = _MTG_BOOKPAGE_RE.search(flat) or _OR_BOOKPAGE_RE.search(flat)
    if m:
        out['mtg_bp'] = '%d/%d' % (int(m.group(1)), int(m.group(2)))
    m = _INSTR_RE.search(flat)
    if m:
        out['mtg_inst'] = m.group(1)
    seen = set()
    for m in _JUNIOR_RE.finditer(flat):
        # the name may carry periods ('N.A.', 'INC.'), so the lead-in is read back from the text
        lead_in, tail = flat[max(0, m.start() - 220):m.start()], m.group(1)
        who = _party_of(lead_in)
        if re.search(r'unknown\s+(?:tenant|spouse|part)|plaintiff\b', who, re.I) and not re.search(r'mortgage|judgment|lien', tail, re.I):
            basis = 'tenant' if re.search(r'tenant|possession', who + tail, re.I) else 'spouse'
        else:
            basis = _basis_of(tail + ' ' + who)
        key = (who.upper(), basis)
        if key in seen:
            continue
        seen.add(key)
        bp = _BOOKPAGE_RE.search(tail)
        amt = re.search(_MONEY, tail)
        out['juniors'].append({'who': who, 'basis': basis,
                               'bp': '%d/%d' % (int(bp.group(1)), int(bp.group(2))) if bp else None,
                               'amt': round(_money(amt.group(1))) if amt else None})
    return out


def lien_juniors(parsed):
    """The named parties that hold a claim on the property (not a tenant or a spouse)."""
    return [j for j in parsed.get('juniors') or [] if j.get('basis') not in ('tenant', 'spouse', 'unknown')]


# ---- reading bytes ----------------------------------------------------------------------------------
def _ocr():
    if os.name != 'nt':
        return None
    import document_store as DS
    return DS.winocr


def read_pdf(content):
    """-> (page_texts, pages, pages_read). Unread pages are '' in page_texts, never dropped."""
    import document_store as DS
    r = DS.read_pages(content, ocr=_ocr())
    texts, read = [], 0
    for p in r.get('pages') or []:
        ok = p.get('outcome') in ('text', 'ocr_text')
        texts.append((p.get('text') or '') if ok else '')
        read += 1 if ok else 0
    return texts, len(texts), read


def entry_from(content, kind, today, extra=None):
    texts, pages, read = read_pdf(content)
    parsed = parse_complaint(texts)
    e = {'status': 'read' if read == pages and pages else ('partial' if read else 'unreadable'),
         'kind': kind, 'pages': pages, 'pages_read': read, 'read': today.isoformat(), 'v': PARSER_VERSION}
    e.update(parsed)
    e.update(extra or {})
    return e


# ---- transports ---------------------------------------------------------------------------------------
_COMPLAINT_DESC = re.compile(r'\bcomplaint\b', re.I)
_NOT_COMPLAINT_DESC = re.compile(r'answer|motion|response|reply|summons|order\b|notice\s+of|affidavit|'
                                 r'certificate|cover\s+sheet|exhibit\s+to|objection|default', re.I)


def pick_complaint(entries, desc=lambda e: ''):
    """The operative complaint among docket entries: the latest amended one, else the first."""
    hits = [e for e in entries if _COMPLAINT_DESC.search(desc(e)) and not _NOT_COMPLAINT_DESC.search(desc(e))]
    if not hits:
        return None
    amended = [e for e in hits if re.search(r'amended', desc(e), re.I)]
    return amended[-1] if amended else hits[0]


def read_miami(case, today, collector=None):
    import document_collectors as DC
    col = collector or DC.MiamiCollector()
    try:
        inv = col.enumerate_documents(case)
    except DC.AccessGap as exc:
        return {'status': 'gap_docket', 'why': str(exc)[:120], 'read': today.isoformat(), 'v': PARSER_VERSION}
    desc = lambda e: str((e.get('metadata') or {}).get('docketDescrition')
                         or (e.get('metadata') or {}).get('docketDescription') or '')
    ents = sorted(inv.get('entries') or [], key=lambda e: str((e.get('metadata') or {}).get('eventDate') or ''))
    pick = pick_complaint(ents, desc)
    if pick is None:
        return {'status': 'gap_no_complaint_entry', 'docket_entries': len(ents),
                'read': today.isoformat(), 'v': PARSER_VERSION}
    try:
        atts = col.attachments(case, pick['metadata'])
        parts = [col.retrieve_document(a) for a in atts]
    except DC.AccessGap as exc:
        return {'status': 'gap_image', 'why': str(exc)[:120], 'read': today.isoformat(), 'v': PARSER_VERSION}
    blobs = [p['content'] for p in parts if p.get('content')]
    if not blobs:
        return {'status': 'gap_image', 'why': 'no document bytes', 'read': today.isoformat(), 'v': PARSER_VERSION}
    if len(blobs) > 1:
        import document_store as DS
        content = DS.merge_pages(blobs)
    else:
        content = blobs[0]
    return entry_from(content, 'complaint', today,
                      {'filed': str(pick['metadata'].get('eventDate') or '')[:10], 'docket_entries': len(ents)})


def read_broward_notice(case, instrument, today, session=None, chain=None):
    """The recorded lis pendens notice. Names the mortgage being foreclosed; no balance is pleaded."""
    from fl_lp import broward_pin as pin
    inst = re.sub(r'\D', '', str(instrument or ''))
    if not inst.strip('0'):
        return {'status': 'gap_no_instrument', 'read': today.isoformat(), 'v': PARSER_VERSION}
    html, detail = pin._details_html(inst)
    if not detail:
        return {'status': 'gap_details', 'read': today.isoformat(), 'v': PARSER_VERSION}
    with tempfile.TemporaryDirectory(prefix='lpnotice-') as work:
        pdf = pin._pdf_of(html, inst, work)
        if not pdf:
            return {'status': 'gap_pdf', 'read': today.isoformat(), 'v': PARSER_VERSION}
        with open(pdf, 'rb') as f:
            content = f.read()
    e = entry_from(content, 'lp_notice', today)
    e['principal'] = None          # a notice pleads no balance; never let a stray figure stand in
    e['face'] = foreclosed_face(e, chain)
    return e


def foreclosed_face(entry, chain):
    """The recorded face of the mortgage the notice names, when the chain holds that instrument."""
    if not isinstance(chain, dict):
        return None
    inst, bp = str(entry.get('mtg_inst') or ''), str(entry.get('mtg_bp') or '')
    for l in chain.get('liens') or []:
        if not isinstance(l, dict) or not l.get('amt'):
            continue
        lbp = re.sub(r'\s', '', str(l.get('bp') or ''))
        if (inst and str(l.get('inst') or '') == inst) or (bp and lbp and lbp == bp):
            return int(round(float(l['amt'])))
    return None


def pb_inbox_files(folder=None):
    """{case UCN: path} for complaints the court tab posted to pb_court_inbox."""
    if folder is None:
        import pb_court_inbox
        folder = pb_court_inbox.inbox_dir()
    best = {}
    for path in glob.glob(os.path.join(folder, '*_DIN*_*COMPLAINT*.pdf')):
        m = re.match(r'(50\d{4}(?:CA|CC)\d{6}[A-Z0-9]{6})_DIN(\d+)_', os.path.basename(path))
        if m and int(m.group(2)) >= best.get(m.group(1), (-1, ''))[0]:
            best[m.group(1)] = (int(m.group(2)), path)     # a later (amended) docket number wins
    return {case: path for case, (_din, path) in best.items()}


def pb_key(case):
    """Palm Beach case numbers are stored in several shapes; the first 14 characters identify one."""
    v = re.sub(r'[^A-Z0-9]', '', str(case or '').upper())
    return v[:14] if re.match(r'50\d{4}(?:CA|CC)\d{6}', v) else ''


# ---- run ------------------------------------------------------------------------------------------------
def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, separators=(',', ':'), ensure_ascii=False)
    os.replace(tmp, path)


def _due(entry, today):
    if not isinstance(entry, dict) or entry.get('v') != PARSER_VERSION:
        return True
    if entry.get('status') in ('read', 'partial'):
        return False
    try:
        return (today - datetime.date.fromisoformat(str(entry.get('read'))[:10])).days >= RETRY_GAP_DAYS
    except ValueError:
        return True


def _newest_first(rows):
    def key(r):
        try:
            return datetime.datetime.strptime(str(r.get('filed') or ''), '%m/%d/%Y').date().toordinal()
        except ValueError:
            return 0
    return sorted(rows, key=key, reverse=True)


def run(counties, limit=DEFAULT_LIMIT, only_case=None, dry_run=False, here=HERE, today=None, pause=PAUSE_S):
    today = today or datetime.date.today()
    out = _load(os.path.join(here, 'complaints.json'), {})
    leads = [r for r in _load(os.path.join(here, 'lp_leads.json'), []) if isinstance(r, dict) and r.get('case')]
    if only_case:
        leads = [r for r in leads if str(r['case']).upper() == only_case.upper()]
    rep = {c: {'leads': 0, 'queued': 0, 'read': 0, 'partial': 0, 'gaps': 0, 'with_principal': 0,
               'with_lienholders': 0} for c in counties}
    n = 0

    def note(county, e):
        st = e.get('status')
        rep[county]['read' if st == 'read' else 'partial' if st == 'partial' else 'gaps'] += 1
        rep[county]['with_principal'] += 1 if e.get('principal') or e.get('face') else 0
        rep[county]['with_lienholders'] += 1 if lien_juniors(e) else 0

    if 'MIAMI-DADE' in counties:
        md = _newest_first([r for r in leads if r.get('county') == 'MIAMI-DADE'])
        todo = [r for r in md if _due(out.get(r['case']), today)]
        rep['MIAMI-DADE'].update(leads=len(md), queued=len(todo))
        collector = None
        for r in ([] if dry_run else todo):
            if n >= limit:
                break
            if collector is None:
                import document_collectors as DC
                collector = DC.MiamiCollector()
            try:
                e = read_miami(r['case'], today, collector)
            except Exception as exc:
                e = {'status': 'gap_error', 'why': type(exc).__name__, 'read': today.isoformat(), 'v': PARSER_VERSION}
            e['county'] = 'MIAMI-DADE'
            out[r['case']] = e
            note('MIAMI-DADE', e)
            n += 1
            _save(os.path.join(here, 'complaints.json'), out)
            time.sleep(pause)

    if 'PALM BEACH' in counties:
        pb = [r for r in leads if r.get('county') == 'PALM BEACH']
        by_key = {pb_key(r['case']): r['case'] for r in pb if pb_key(r['case'])}
        files = {} if dry_run else pb_inbox_files()
        rep['PALM BEACH'].update(leads=len(pb), queued=len(files), inbox=len(files))
        for ucn, path in files.items():
            case = by_key.get(ucn[:14], ucn)          # a filing not on the board yet keeps its UCN
            mtime = int(os.path.getmtime(path))
            if (out.get(case) or {}).get('src_mtime') == mtime and (out.get(case) or {}).get('v') == PARSER_VERSION:
                continue
            with open(path, 'rb') as f:
                content = f.read()
            e = entry_from(content, 'complaint', today, {'src_mtime': mtime, 'county': 'PALM BEACH'})
            out[case] = e
            note('PALM BEACH', e)
            _save(os.path.join(here, 'complaints.json'), out)

    if 'BROWARD' in counties:
        lis = {str(x.get('case') or '').upper(): x for x in _load(os.path.join(here, 'lis_pendens.json'), [])
               if isinstance(x, dict) and x.get('county') == 'BROWARD'}
        chains = _load(os.path.join(here, 'broward_liens.json'), {})
        bw = _newest_first([r for r in leads if r.get('county') == 'BROWARD'])
        todo = [r for r in bw if _due(out.get(r['case']), today)]
        rep['BROWARD'].update(leads=len(bw), queued=len(todo))
        session = None
        for r in ([] if dry_run else todo):
            if n >= limit:
                break
            inst = (lis.get(str(r['case']).upper()) or {}).get('instrument')
            if session is None:
                from fl_lp import broward_pin as pin
                session = pin.BL.start_session() or False
            if not session:
                rep['BROWARD']['session'] = 'blocked'
                break
            try:
                e = read_broward_notice(r['case'], inst, today, session, chains.get(r['case']))
            except Exception as exc:
                e = {'status': 'gap_error', 'why': type(exc).__name__, 'read': today.isoformat(), 'v': PARSER_VERSION}
            e['county'] = 'BROWARD'
            out[r['case']] = e
            note('BROWARD', e)
            n += 1
            _save(os.path.join(here, 'complaints.json'), out)
            time.sleep(pause)
    return rep


def for_board(entry):
    """The few fields a board row carries. No party names: the row ships in the published payload."""
    if not isinstance(entry, dict) or entry.get('status') not in ('read', 'partial'):
        return None
    js = lien_juniors(entry)
    b = {'k': entry.get('kind'), 'pg': entry.get('pages') or 0, 'rd': entry.get('pages_read') or 0}
    if entry.get('principal'):
        b['p'] = int(round(entry['principal']))
    if entry.get('face'):
        b['face'] = int(entry['face'])
    if entry.get('default_date'):
        b['dd'] = entry['default_date']
    if entry.get('rate'):
        b['r'] = entry['rate']
    if js:
        b['jr'] = len(js)
        b['jrk'] = sorted({j['basis'] for j in js})
    return b


def stamp_rows(rows, here=HERE):
    """Bake `cmp` onto lis pendens board rows. -> rows stamped. Never raises."""
    try:
        data = _load(os.path.join(here, 'complaints.json'), {})
        if not data:
            return 0
        pbk = {pb_key(k): v for k, v in data.items() if pb_key(k)}
        n = 0
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            case = str(r.get('case') or '')
            b = for_board(data.get(case) or pbk.get(pb_key(case)))
            if b:
                r['cmp'] = b
                n += 1
        return n
    except Exception:
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read the complaint on lis pendens leads, every page.')
    ap.add_argument('--county', default='all', choices=['all', 'miami', 'broward', 'pb'])
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--case', default='')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    counties = {'all': ['MIAMI-DADE', 'PALM BEACH', 'BROWARD'], 'miami': ['MIAMI-DADE'],
                'broward': ['BROWARD'], 'pb': ['PALM BEACH']}[a.county]
    rep = run(counties, limit=a.limit, only_case=a.case or None, dry_run=a.dry_run)
    for c in counties:
        v = rep[c]
        print('complaint_reader %-11s leads %d, queued %d, read in full %d, partly read %d, gaps %d, '
              'debt figure %d, lienholders named %d'
              % (c, v['leads'], v['queued'], v['read'], v['partial'], v['gaps'],
                 v['with_principal'], v['with_lienholders']))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
