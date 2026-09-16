#!/usr/bin/env python
"""fl_lp.broward_pin — settle AMBIGUOUS Broward lis pendens addresses from the recorded mortgage itself.

WHY. A Broward lis pendens carries no legal description and no parcel, so fl_lp/broward_resolve.py finds
the property by the defendant's NAME — and when that name sits on several parcels it rightly grades the
row `low` (lp_resolve2.revoke_ambiguous() demotes the old coin-flip promotions to `pass2-revoked` the same
way). Those rows carry every parcel the defendant owns in `candidates[]` and nothing that picks one. ~196
rows, and many were already emailed about whichever parcel the coin flip chose.

THE RECORD PICKS IT. The lis pendens' AcclaimWeb details page links (DocLink) the mortgage it forecloses —
or, when the recorder left no link, the filing NAMES it in words ("...foreclose a note and mortgage ... at
Instrument Number 123456789"). A residential mortgage prints the parcel: the Fannie/Freddie Form 3010 header
carries "PIN: 123401-21-0310" / "A.P.N.: 1234-22-AB-0530" and "which currently has the address of ...",
usually on page 1-4 (cover sheets move it), and Exhibit A often repeats it. The scans have no text layer, so
pages are rendered with PyMuPDF and read by Windows' built-in OCR (fl_lp/winocr.ps1, Windows PowerShell 5.1 —
nothing to install), in batches that stop once a candidate's folio has been read.

THE BAR. Promote only when a parcel number read off the foreclosed mortgage EXACTLY equals one candidate's
folio (separators stripped; letters kept, so condo folios like 123434AB0170 compare exactly too). Anything
short of that stays `low`: no DocLink, no readable number, a number that is none of the candidates, two
candidates named, or page 3's property address naming a DIFFERENT candidate than the PIN. A promoted row
becomes `confidence: high, rung: mortgage-pin`, with evidence citing both instruments. Only rows whose
rung is `pass2-revoked`, or a broward_resolve `low`, are ever touched — never a human-verified row.

NETWORK. Reuses broward_liens.start_session()/_curl() — System32 curl is the only fingerprint Cloudflare
passes. A PDF download that comes back as a challenge page (no %PDF- header) is retried briefly and then
left for another run; nothing here tries to defeat a challenge. Results are cached per case
(_lp_pin_cache.json, gitignored): a transient failure is retried next run, a settled "no" after 30 days.

Run:  python fl_lp/broward_pin.py                  # up to 40 rows / 15 min (the nightly step)
      python fl_lp/broward_pin.py --limit 0 --max-seconds 0     # everything eligible
      python fl_lp/broward_pin.py --case CACE-26-000000 --dry-run
Exit: 0 = ran; 2 = could not run (no AcclaimWeb session, OCR unavailable, unexpected error) — lp_refresh
treats 2 as benign so an optional resolver never stops the lis pendens chain.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import broward_liens as BL                          # noqa: E402  (session, curl, details-page parser)

ADDR = os.path.join(REPO, 'lp_addresses.json')
SRC = os.path.join(REPO, 'lis_pendens.json')
CACHE = os.path.join(REPO, '_lp_pin_cache.json')
OCR_PS1 = os.path.join(HERE, 'winocr.ps1')
POWERSHELL = (r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
              if os.path.exists(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe') else 'powershell.exe')
RETRY_DAYS = 30
MAX_PAGES = 14           # OCR at most this many pages of one document: pages 1-6, the last 6, then 2 more. The PIN
                         # sits in the Form 3010 header (p1-4 behind cover sheets) or Exhibit A (near the end); a
                         # document with none there has none at all, and reading all 30 pages cost ~50 s a row
SETTLED = {'promoted', 'no-link', 'not-mortgage', 'no-pin', 'no-match', 'ambiguous', 'conflict'}
TOKEN_RE = re.compile(r'id="hdnTransactionItemId"\s+value=\'([^\']+)\'')
# "...foreclose a note and mortgage ... at Instrument Number 1 23456789." (OCR splits digits with spaces)
_INST_TEXT_RE = re.compile(r'INSTRUMENT\s*(?:NUMBER|NO\.?|#)?\s*[:#.]?\s*((?:\d\s?){7,8}\d)(?!\s?\d)')


class OcrUnavailable(RuntimeError):
    pass


# ---- which rows, and what a parcel number looks like ----------------------------------------------------
def _key(s):
    """Folio / PIN comparison key: separators out, letters kept. '123401-21-0310' -> '123401210310'."""
    return re.sub(r'[^0-9A-Z]', '', str(s or '').upper())


def eligible(row):
    """Only the rows this step exists for: an ambiguous BROWARD row with 2+ candidate parcels whose rung is
    `pass2-revoked` (lp_resolve2) or the broward_resolve name ladder's own `low`. Everything else — a human's
    records-verified row, any high/medium row, any other rung — is never touched."""
    if not isinstance(row, dict) or str(row.get('county') or '').upper() != 'BROWARD':
        return False
    if row.get('confidence') != 'low' or len(row.get('candidates') or []) < 2:
        return False
    rung = row.get('rung') or ''
    return rung == 'pass2-revoked' or (not rung and str(row.get('evidence') or '').startswith('BCPA:'))


_LABEL_RE = re.compile(
    r'(?<![A-Z0-9])(?:P\.?\s?I\.?\s?N\.?|A\.?\s?P\.?\s?N\.?|PARCEL(?:\s+(?:I\.?D\.?|IDENTIFICATION))?|FOLIO'
    r'|TAX\s+(?:I\.?D\.?|FOLIO|PARCEL)|PROPERTY\s+(?:I\.?D\.?|IDENTIFICATION))'
    r'(?:\s+(?:NUMBER|NO\.?|#))?\s*[:#.\-]*\s*(?=[0-9])(.*)$')


def _pin_after(tail):
    """The parcel number that follows a label: '123401-21-0310 DEFINITIONS' -> '123401210310',
    '1234 34 AB 0170' -> '123434AB0170'. A short letter block survives only between numeric groups."""
    toks = tail.split()
    kept = []
    for i, raw in enumerate(toks):
        t = raw.strip('.,;:()[]')
        if t and re.search(r'\d', t) and re.fullmatch(r'[0-9A-Z\-./]+', t):
            kept.append(t)
        elif re.fullmatch(r'[A-Z]{1,3}', t) and kept and i + 1 < len(toks) and re.search(r'\d', toks[i + 1]):
            kept.append(t)
        else:
            break
    return _key(''.join(kept)), ' '.join(kept)


def labeled_pins(text):
    """-> [(key, as printed)] for every PIN / APN / parcel / folio / tax-id label on the page."""
    out = []
    for line in str(text or '').upper().splitlines():
        m = _LABEL_RE.search(line)
        if not m:
            continue
        key, shown = _pin_after(m.group(1))
        if 10 <= len(key) <= 14 and sum(ch.isdigit() for ch in key) >= 8:
            out.append((key, shown))
    return out


def folio_printed(key, text):
    """Is this folio printed anywhere on the page as a whole token (separators tolerated, never inside a
    longer number such as the 18-digit MERS MIN)?"""
    if not key:
        return False
    pat = r'(?<![0-9A-Z])' + r'[ .\-]{0,2}'.join(map(re.escape, key)) + r'(?![0-9A-Z])'
    return re.search(pat, str(text or '').upper()) is not None


_PROP_ADDR_RE = re.compile(r'CURRENTLY\s+HAS\s+THE\s+ADDRESS\s+OF\s+(.{4,90})')


def property_address(text):
    """The Form 3010 'which currently has the address of ...' — the COLLATERAL, not the borrower's residence
    (page 1 also prints 'currently residing at', which is where the borrower lives)."""
    flat = ' '.join(str(text or '').upper().split())
    m = _PROP_ADDR_RE.search(flat)
    return m.group(1) if m else ''


def instruments_named(text, exclude=()):
    """Recorded instrument numbers a lis pendens NAMES in its text ('seeking to foreclose a note and mortgage
    ... at Instrument Number 123456789'). Many carry no DocLink but say it in words. The caller confirms each
    one is a mortgage on its own details page; the PIN rule still decides the parcel."""
    flat = ' '.join(str(text or '').upper().split())
    out = []
    for m in _INST_TEXT_RE.finditer(flat):
        inst = re.sub(r'\D', '', m.group(1))
        if inst not in exclude and inst not in out:
            out.append(inst)
    return out


def _addr_key(addr):
    """('100', 'SAMPLE') from '100 SAMPLE CT, SAMPLF CITY' — house number + first street word."""
    m = re.match(r'\s*(\d{1,6})\s+(?:[NSEW]{1,2}\s+)?([A-Z0-9]+)', str(addr or '').upper())
    return (m.group(1), m.group(2)) if m else None


def _near(a, b):
    """Same length and at most two differing characters: one parcel number read twice, once garbled."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 2


def decide(readings, candidates):
    """readings: [{'mtg': instrument, 'pages': {page_no: ocr_text}}]. -> dict with 'status' and, when
    promoted, 'cand' / 'mtg' / 'page' / 'how' / 'pin' / 'corroborated'."""
    cands = {}
    for c in candidates or []:
        k = _key(c.get('folio'))
        if k:
            cands[k] = c
    named, other, addr_named, addr_seen = {}, [], set(), ''
    for rd in readings:
        for pg in sorted(rd.get('pages') or {}):
            text = rd['pages'][pg] or ''
            for key, shown in labeled_pins(text):
                if key in cands:
                    if key not in named or named[key]['how'] != 'labeled':
                        named[key] = {'mtg': rd['mtg'], 'page': pg, 'how': 'labeled', 'pin': shown}
                else:
                    other.append(key)
            for key in cands:
                if key not in named and folio_printed(key, text):
                    named[key] = {'mtg': rd['mtg'], 'page': pg, 'how': 'printed', 'pin': key}
            a = property_address(text)
            if a:
                addr_seen = addr_seen or a
                ak = _addr_key(a)
                for key, c in cands.items():
                    if ak and ak == _addr_key(c.get('addr')):
                        addr_named.add(key)
    if len(named) > 1:
        return {'status': 'ambiguous', 'why': 'the mortgage names more than one of the defendant\'s parcels',
                'addr': addr_seen}
    if not named:
        if other:
            return {'status': 'no-match', 'why': 'its parcel number %s is none of the defendant\'s parcels' % other[0],
                    'addr': addr_seen}
        return {'status': 'no-pin', 'why': 'no parcel number readable', 'addr': addr_seen}
    key, hit = next(iter(named.items()))
    stray = [o for o in other if not _near(o, key)]
    if stray:
        return {'status': 'conflict', 'why': 'it also prints parcel number %s, which is none of the candidates'
                % stray[0], 'addr': addr_seen}
    if addr_named and key not in addr_named:
        return {'status': 'conflict', 'why': 'its PIN names %s but its property address names another candidate'
                % cands[key].get('addr'), 'addr': addr_seen}
    return dict(hit, status='promoted', cand=cands[key], corroborated=key in addr_named, addr=addr_seen)


def evidence(today, lp_insts, hit, n_cands):
    return ('MORTGAGE-PIN %s: lis pendens instr %s %s the mortgage it forecloses, instr %s; OCR of '
            'that mortgage (page %s) reads %s %s = folio %s, one of the %d parcels carrying the defendant\'s '
            'name%s.' % (today, '/'.join(lp_insts),
                         'names in its text' if hit.get('via') == 'text' else 'links (DocLink)',
                         hit['mtg'], hit['page'],
                         'PIN' if hit['how'] == 'labeled' else 'parcel number', hit['pin'],
                         str(hit['cand'].get('folio') or '').strip(), n_cands,
                         '; its page-3 property address names the same parcel' if hit.get('corroborated') else ''))


def promote(row, hit, lp_insts, today, zipc=''):
    c = hit['cand']
    row.update({'folio': str(c.get('folio') or '').strip(), 'addr': str(c.get('addr') or '').strip(),
                'city': str(c.get('city') or '').strip(), 'zip': zipc or '',
                'paOwner': str(c.get('owner') or '').strip(), 'ownerMismatch': False,
                'confidence': 'high', 'rung': 'mortgage-pin', 'needsHuman': False,
                'evidence': evidence(today, lp_insts, hit, len(row.get('candidates') or []))})
    for k in ('value', 'hs'):
        row.pop(k, None)                       # lp_values prices the parcel that is now named
    return row


def apply_promotions(path, promos, today):
    """Re-read the file right before writing and apply only to rows that are STILL eligible and still carry
    the chosen folio among their candidates — another process may have touched it meanwhile."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    applied = []
    for case, (hit, lp_insts, zipc) in promos.items():
        row = data.get(case)
        if not eligible(row):
            continue
        if _key(hit['cand'].get('folio')) not in {_key(c.get('folio')) for c in row.get('candidates') or []}:
            continue
        promote(row, hit, lp_insts, today, zipc)
        applied.append(case)
    if applied:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    return applied


# ---- network + OCR ---------------------------------------------------------------------------------------
def ocr_images(paths, timeout=600):
    """-> {path: text or None}. One Windows PowerShell per call; raises OcrUnavailable if it cannot run."""
    if not paths:
        return {}
    try:
        r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                            '-File', OCR_PS1] + list(paths), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OcrUnavailable(str(e)[:200])
    raw = r.stdout.decode('utf-8', 'replace').lstrip('\ufeff').strip()
    if r.returncode != 0 or not raw:
        raise OcrUnavailable('winocr exit %s: %s' % (r.returncode, r.stderr.decode('utf-8', 'replace')[:200]))
    try:
        return json.loads(raw.splitlines()[-1])
    except ValueError:
        raise OcrUnavailable('winocr printed no JSON: %s' % raw[:200])


def _details_html(inst, tries=3):
    for attempt in range(tries):
        h = BL._curl(BL.BASE + '/details/JumpToInstrumentNumber/27/%s' % inst)
        p = BL.parse_details(h)
        if p is not None and p['i'] == str(inst):
            return h, p
        if attempt < tries - 1:
            time.sleep(3 + 3 * attempt)
    return None, None


def _download_pdf(token, dest, tries=3):
    """DocumentPdfAllPages -> dest. A body without %PDF- is a challenge or error page: wait briefly and ask
    again, then give up for this run."""
    for attempt in range(tries):
        try:
            subprocess.run([BL.CURL, '-s', '-m', '120', '-A', BL.UA, '-c', BL.JAR, '-b', BL.JAR, '-o', dest,
                            BL.BASE + '/Image/DocumentPdfAllPages/%s' % token], capture_output=True, timeout=150)
            with open(dest, 'rb') as f:
                if f.read(5) == b'%PDF-':
                    return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        if attempt < tries - 1:
            time.sleep(5 + 5 * attempt)
    return False


def _ocr_pages(doc, idxs, workdir, tag, dpi=300):
    import fitz                                 # PyMuPDF
    paths = {}
    for i in idxs:
        p = os.path.join(workdir, '%s_p%d.png' % (tag, i + 1))
        doc[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).save(p)
        paths[p] = i + 1
    got = ocr_images(list(paths))
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
    return {pg: (got.get(p) or '') for p, pg in paths.items()}


def read_pdf(pdf, workdir, cand_keys=(), max_pages=MAX_PAGES):
    """OCR a recorded document in batches and stop once a candidate's folio has been read: pages 1-6 (cover
    sheets push the Form 3010 header and the property-address clause past page 3), then the last 6 (Exhibit
    A), then the rest in eights. With no cand_keys it reads everything up to max_pages. -> {page_no: text}."""
    import fitz
    doc = fitz.open(pdf)
    n = doc.page_count
    order = list(range(min(6, n)))
    order += [i for i in range(max(0, n - 6), n) if i not in order]
    order += [i for i in range(n) if i not in order]
    order = order[:max_pages]
    batches = [order[:6], order[6:12]] + [order[j:j + 8] for j in range(12, len(order), 8)]
    pages = {}
    try:
        for b in batches:
            if not b:
                continue
            got = _ocr_pages(doc, b, workdir, os.path.basename(pdf)[:-4])
            pages.update(got)
            if cand_keys and any(any(k in cand_keys for k, _ in labeled_pins(t))
                                 or any(folio_printed(k, t) for k in cand_keys) for t in got.values()):
                break
    finally:
        doc.close()
    return pages


def _pdf_of(html_, inst, workdir):
    """The document image for a details page already in hand -> local PDF path, or None (no token /
    the download stayed a challenge page)."""
    tok = TOKEN_RE.search(html_ or '')
    if not tok:
        return None
    BL._curl(BL.BASE + '/Image/StartImageRetrieval/%s/0' % tok.group(1))
    pdf = os.path.join(workdir, '%s.pdf' % inst)
    return pdf if _download_pdf(tok.group(1), pdf) else None


def resolve_case(row, lp_insts, today):
    """-> cache entry {'d', 'status', 'lp', 'mtg', ...}; status 'promoted' carries 'hit'."""
    entry = {'d': today, 'lp': sorted(lp_insts), 'mtg': []}
    parents, others, lp_html, via = [], [], {}, 'link'
    for lp in sorted(lp_insts):
        html_, page = _details_html(lp)
        if page is None:
            return dict(entry, status='unreadable', why='lis pendens %s details page unreadable' % lp)
        lp_html[lp] = html_
        for ref in page.get('p') or []:
            inst = next((k[2:] for k in ref.get('k') or [] if k.startswith('I:')), '')
            if inst and ref.get('t') in ('M', '') and inst not in parents:
                parents.append(inst)
            elif ref.get('t'):
                others.append('%s %s' % (ref['t'], inst or '/'.join(ref.get('k') or [])))
    cand_keys = [_key(c.get('folio')) for c in row.get('candidates') or [] if _key(c.get('folio'))]
    workdir = tempfile.mkdtemp(prefix='lp_pin_')

    def read_mortgages(insts):
        out = []
        for mtg in insts[:3]:
            html_, page = _details_html(mtg)
            if page is None:
                return None, dict(entry, status='unreadable', why='mortgage %s details page unreadable' % mtg)
            if not page['t'].upper().startswith('M'):
                continue                         # a lien, a declaration, a notice — not a mortgage
            pdf = _pdf_of(html_, mtg, workdir)
            if pdf is None:
                return None, dict(entry, status='unreadable',
                                  why='mortgage %s image did not download (challenge page)' % mtg)
            entry['mtg'].append(mtg)
            out.append({'mtg': mtg, 'pages': read_pdf(pdf, workdir, cand_keys)})
        return out, None
    try:
        readings, err = read_mortgages(parents) if parents else ([], None)
        if err:
            return err
        if not readings:
            # No DocLink to a MORTGAGE (none at all, or only a lien / notice): many lis pendens still NAME the
            # mortgage in words. Read the (1-3 page) filing.
            via, named = 'text', []
            for lp, html_ in lp_html.items():
                pdf = _pdf_of(html_, lp, workdir)
                if pdf is None:
                    return dict(entry, status='unreadable', why='lis pendens %s image did not download' % lp)
                text = '\n'.join(read_pdf(pdf, workdir, max_pages=4).values())
                for inst in instruments_named(text, exclude=lp_insts):
                    if inst not in named and inst not in parents:
                        named.append(inst)
            if not named and not parents:
                # an association / construction-lien lis pendens links its claim of lien [LIE], not a mortgage
                return dict(entry, status='no-link', why='the lis pendens links and names no mortgage%s'
                            % ((' (it links %s)' % ', '.join(others[:3])) if others else ''))
            readings, err = read_mortgages(named)
            if err:
                return err
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not readings:
        return dict(entry, status='not-mortgage', why='the lis pendens %s no mortgage-type document'
                    % ('names' if via == 'text' else 'links'))
    got = decide(readings, row.get('candidates') or [])
    entry.update({k: v for k, v in got.items() if k in ('status', 'why', 'addr')})
    entry['pages'] = sum(len(r['pages']) for r in readings)
    entry['via'] = via
    if got['status'] == 'promoted':
        got['via'] = via
        entry['hit'] = {k: got[k] for k in ('mtg', 'page', 'how', 'pin', 'corroborated', 'via')}
        entry['hit']['folio'] = str(got['cand'].get('folio') or '').strip()
        entry['_hit'] = got
    return entry


def _zip_of(folio):
    """ZIP from the FL cadastral site address (numeric folios only: its PARCEL_ID join strips letters)."""
    if not re.fullmatch(r'\d+', str(folio or '')):
        return ''
    try:
        import fl_cadastral as FC
        site = (FC.enrich(parcel_id=folio) or {}).get('site_addr') or ''
    except Exception:
        return ''
    m = re.search(r'(\d{5})(?:-\d{4})?\s*$', site)
    return m.group(1) if m else ''


def _hit_from_cache(row, h):
    """Rebuild a promotion from its cached decision (a run cut short after caching, before writing)."""
    cand = next((c for c in (row or {}).get('candidates') or [] if _key(c.get('folio')) == _key((h or {}).get('folio'))),
                None)
    return dict(h, cand=cand) if (cand and h) else None


def _due(entry, today, retry_days):
    if not entry:
        return True
    if entry.get('status') not in SETTLED:
        return True                              # transient: ask again next run
    try:
        age = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(entry.get('d') or '')).days
    except ValueError:
        return True
    return age > retry_days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=40, help='rows per run (0 = no limit)')
    ap.add_argument('--max-seconds', type=int, default=900, help='wall budget (0 = no limit)')
    ap.add_argument('--case', default='')
    ap.add_argument('--retry-days', type=int, default=RETRY_DAYS)
    ap.add_argument('--dry-run', action='store_true', help='decide and print; write nothing')
    a = ap.parse_args()
    today = time.strftime('%Y-%m-%d')
    started = time.time()

    with open(ADDR, encoding='utf-8') as f:
        data = json.load(f)
    lp_insts = {}
    with open(SRC, encoding='utf-8') as f:
        for r in json.load(f):
            if str(r.get('county') or '').upper() == 'BROWARD' and r.get('instrument') and r.get('case'):
                lp_insts.setdefault(r['case'], set()).add(str(r['instrument']).strip())
    try:
        with open(CACHE, encoding='utf-8') as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    # a decision cached as promoted but never written (the run was cut short): write it now, read nothing
    applied = []
    if not a.dry_run:
        pending = {}
        for c, entry in cache.items():
            hit = _hit_from_cache(data.get(c), entry.get('hit')) if entry.get('status') == 'promoted' else None
            if hit and eligible(data.get(c)) and lp_insts.get(c):
                pending[c] = (hit, sorted(lp_insts[c]), _zip_of(hit['cand'].get('folio')))
        if pending:
            applied += apply_promotions(ADDR, pending, today)
            print('applied %d promotion(s) decided by an earlier, interrupted run' % len(applied))
            with open(ADDR, encoding='utf-8') as f:
                data = json.load(f)
    rows = [r for c, r in data.items() if eligible(r) and (not a.case or c == a.case) and lp_insts.get(c)
            and (a.case or _due(cache.get(c), today, a.retry_days))]
    # rows that were CONTACTED about a coin-flip parcel first (pass2-revoked); circuit mortgage foreclosures
    # (CACE) before county-court association cases, whose lis pendens link a claim of lien, not a mortgage;
    # newest case first within each group
    rows.sort(key=lambda r: str(r.get('case')), reverse=True)
    rows.sort(key=lambda r: (r.get('rung') != 'pass2-revoked', not str(r.get('case')).startswith('CACE')))
    if a.limit:
        rows = rows[:a.limit]
    print('%d ambiguous Broward lis pendens row(s) to read from their mortgages' % len(rows))
    if not rows:
        return 0

    sess = None
    for attempt in range(3):
        sess = BL.start_session()
        if sess:
            break
        time.sleep(20 + 20 * attempt)
    if not sess:
        print('could not establish an AcclaimWeb session (Cloudflare / site down) - nothing read this run')
        return 2

    tally, streak = {}, 0
    for i, row in enumerate(rows, 1):
        if a.max_seconds and time.time() - started > a.max_seconds:
            print('time budget spent after %d row(s) - the rest wait for the next run' % (i - 1))
            break
        if streak >= 6:
            print('6 unreadable rows in a row (Cloudflare) - stopping; the rest wait for the next run')
            break
        case = row['case']
        try:
            entry = resolve_case(row, lp_insts[case], today)
        except OcrUnavailable as e:
            print('OCR unavailable (%s) - stopping' % e)
            break
        streak = streak + 1 if entry['status'] == 'unreadable' else 0
        hit = entry.pop('_hit', None)
        tally[entry['status']] = tally.get(entry['status'], 0) + 1
        if hit:
            print('  [%d/%d] PROMOTE %-16s -> %s (folio %s) mortgage %s p%s %s via %s%s' % (
                i, len(rows), case, hit['cand'].get('addr'), hit['cand'].get('folio'), hit['mtg'], hit['page'],
                hit['how'], hit.get('via'), ' +address' if hit.get('corroborated') else ''))
        else:
            print('  [%d/%d] low     %-16s %-11s %s' % (i, len(rows), case, entry['status'], entry.get('why', '')))
        if not a.dry_run:
            cache[case] = entry
            tmp = CACHE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=0)
            os.replace(tmp, CACHE)
            if hit:                              # written the moment it is decided — a cut-short run keeps it
                applied += apply_promotions(ADDR, {case: (hit, sorted(lp_insts[case]),
                                                          _zip_of(hit['cand'].get('folio')))}, today)
        time.sleep(1.0)

    print('\nDONE: %s | promoted %d%s -> lp_addresses.json' % (
        ', '.join('%s %d' % kv for kv in sorted(tally.items())), len(applied),
        ' (dry run: nothing written)' if a.dry_run else ''))
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        print('broward_pin: unexpected error - no rows were changed by this failure; the chain continues')
        code = 2
    raise SystemExit(code)
