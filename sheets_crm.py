#!/usr/bin/env python
"""sheets_crm — the working-deals CRM, pushed to Google Sheets (and always to a CSV).

WHY (2026-08-17, Jose's ask): "when we share our information... it's not the same as being able to
point and click and having it at your fingertips, especially when we got a client calling in 20
minutes." The board is Alejandro's cockpit; Jose and Carlos live in Google. This exports the
WORKING set — not the whole 1,700-lead universe — to a sheet the three of them share.

WHAT QUALIFIES AS "WORKING"
  * a human status was set on the board (any status except Dead / DO NOT CONTACT — those are
    excluded entirely; a CRM must never resurface someone the ledger closed), OR
  * a HUMAN touch was logged: a call, a door visit, or a text that is not the automated morning
    batch ("morning batch" outs are machine sends, not work — see funnel-truth), OR
  * door-ready: cert-of-title not recorded, judgment known, value present, no cadastral warning,
    junior-lien equity only counts when the chain resolved, net equity >= $30k, no active BK stay.

LEAD SOURCE: the Desktop plaintext twin's RAW payload — the board's own merged rows (BatchData
chain conf, cadastral warns, cert flags all included), so the sheet always agrees with the board.
Falls back to the slim county JSONs if the twin is missing.

TWO OUTPUTS, EVERY RUN
  1. Desktop\DEALFLOW\BSG_CRM.csv — always written; importable into anything.
  2. Google Sheets push — ONLY when sheets_crm_webhook.url exists (gitignored). The webhook is a
     Google Apps Script bound to the team sheet, deployed by Alejandro once (sheets_crm_SETUP.txt).
     No OAuth lives in this repo; the script runs as his account and the URL is the only secret.
     The push REPLACES the "DealFlow" tab wholesale — the sheet is a VIEW of the pipeline, not a
     second source of truth. Team comments live on OTHER tabs, which the push never touches.

RUN
  python sheets_crm.py            # CSV + push (if webhook configured)
  python sheets_crm.py --csv-only
"""
import argparse
import csv
import json
import os
import re
import urllib.request
from datetime import datetime
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
DESK = P.DEALFLOW_DIR
TWIN = os.path.join(DESK, 'Foreclosure Lead Tracker.html')
WEBHOOK_F = os.path.join(HERE, 'sheets_crm_webhook.url')
DOOR_MIN_EQ = 30000

HEADERS = ['Status', 'Address', 'Owner', 'Best phone', 'More phones', 'County', 'Case',
           'Auction', 'Days', 'Value', 'Judgment owed', 'Net equity', 'Equity %',
           'Chain / risk notes', 'Last human touch', 'Touches', 'Follow-up', 'Board notes']


def _load(p, default):
    try:
        return json.load(open(os.path.join(HERE, p), encoding='utf-8'))
    except Exception:
        return default


def _leads():
    """Board rows, from the Desktop twin's RAW payload (the merged truth the board renders)."""
    try:
        h = open(TWIN, encoding='utf-8').read()
        i = h.find('RAW = ')
        if i >= 0:
            rows, _ = json.JSONDecoder().raw_decode(h, i + len('RAW = '))
            if isinstance(rows, list) and rows:
                return rows
    except Exception as e:
        print('twin unreadable (%s) - falling back to slim county files' % str(e)[:80])
    seen, out = set(), []
    for f in ('broward_leads.json', 'palmbeach_leads.json', 'lp_leads.json'):
        for r in _load(f, []):
            c = r.get('case') or ''
            if c and c not in seen:
                seen.add(c)
                out.append(r)
    return out


def _human_touches(n):
    """Calls, door visits, and hand-typed texts. Morning-batch sends are machine work."""
    out = []
    for t in (n.get('touches') or []):
        ch = t.get('ch') or ''
        if ch in ('call', 'door', 'visit'):
            out.append(t)
        elif ch in ('text', 'sms') and 'morning batch' not in (t.get('out') or ''):
            out.append(t)
    return out


def _phones(case, st, n):
    """Skiptrace phones (DNC-flagged excluded) with any hand-captured board phone first."""
    ph = []
    board = re.sub(r'\D', '', str(n.get('phone') or ''))
    if len(board) == 11 and board.startswith('1'):
        board = board[1:]
    if len(board) == 10:
        ph.append(board)
    for p in ((st.get(case) or {}).get('phones') or []):
        d = re.sub(r'\D', '', str(p.get('number') or ''))
        if len(d) == 10 and not p.get('dnc') and d not in ph:
            ph.append(d)
    return ph


def _fmt_phone(d):
    return '(%s) %s-%s' % (d[0:3], d[3:6], d[6:10]) if len(d) == 10 else d


# ---- per-prospect deep tabs -------------------------------------------------------------------
# "every prospect we decide to go on and consult" (Alejandro, 2026-08-17): when a lead's status
# says the team is ON it, the sheet grows a dedicated tab carrying the FULL workup — so he and
# Jesse have the whole file on their phones mid-consult instead of relaying numbers over the call.
# The trigger is the STATUS he already sets from Call Mode / the board — no new workflow.
CONSULT_STATUSES = {'APPOINTMENT', 'OFFER MADE', 'UNDER CONTRACT', 'CONTACTED', 'CALLED - TALKED'}


def _money(v):
    try:
        return '${:,.0f}'.format(float(v))
    except Exception:
        return ''


def _prospect_block(r, n, st, pbl, brl, mdl):
    case = r.get('case') or ''
    rows = []
    def sec(t): rows.append(['— ' + t + ' —', ''])
    def kv(k, v):
        if v not in (None, '', 0, '0'):
            rows.append([k, str(v)])
    sec('PROPERTY')
    kv('Address', r.get('addr'))
    kv('Owner(s)', r.get('owners'))
    kv('County', r.get('county'))
    kv('Case', case)
    kv('Folio', r.get('folio'))
    kv('Type', r.get('dor_desc') or r.get('ctype'))
    kv('Homestead', 'YES' if r.get('hs') else '')
    kv('Bought', ('%s for %s' % (r.get('bought'), _money(r.get('bprice')))) if r.get('bought') else '')
    kv('Listing status', r.get('zstatus'))
    sec('MONEY')
    kv('Market value', _money(r.get('value')))
    kv('Assessed value', _money(r.get('assessed_value')))
    kv('Est. annual tax', _money(r.get('etax')))
    kv('Judgment', _money(r.get('judg')) or ('UNKNOWN' if r.get('ju') else ''))
    if r.get('value') and r.get('judg'):
        kv('Net equity', _money(float(r['value']) - float(r['judg'])))
        kv('Equity %', str(r.get('eq') or round((float(r['value']) - float(r['judg'])) / float(r['value']) * 100)) + '%')
    kv('Opening bid', _money(r.get('obid')))
    kv('VALUE WARNING', r.get('warn'))
    sec('AUCTION')
    kv('Sale date', r.get('auction'))
    kv('Days out', r.get('days'))
    kv('Plaintiff', r.get('plaintiff'))
    kv('Case status', r.get('cstatus'))
    if r.get('saleBkAct'):
        kv('!! BANKRUPTCY STAY', 'ACTIVE - 11 USC 362 bars ALL contact')
    sec('LIEN CHAIN')
    kv('Summary', _chain_note(r, pbl))
    src = None
    for d in (pbl, brl, mdl):
        e = d.get(case) or {}
        if e.get('liens'):
            src = e
            break
    for li in ((src or {}).get('liens') or [])[:8]:
        kv((li.get('type') or 'lien'), ' '.join(str(x) for x in (
            li.get('holder') or li.get('grantee') or '', _money(li.get('bal') or li.get('amt')),
            li.get('rec') or li.get('date') or '') if x).strip())
    for li in (r.get('orliens') or [])[:8]:
        kv('BD ' + str(li.get('t') or 'mortgage'),
           ' '.join(str(x) for x in (li.get('h') or '', _money(li.get('bal')),
                                     ('SATISFIED ' + str(li.get('sat'))) if li.get('sat') else 'OPEN') if x).strip())
    sec('PHONES')
    for i, p in enumerate(_phones(case, st, n)[:6], 1):
        rows.append(['Phone %d%s' % (i, ' (best)' if i == 1 else ''), _fmt_phone(p)])
    for e in (r.get('emails') or [])[:3]:
        kv('Email', e)
    kv('Mailing addr', r.get('mail'))
    sec('TIMELINE')
    kv('Status', n.get('status'))
    kv('Follow-up', n.get('next'))
    for t in (n.get('touches') or [])[-8:]:
        rows.append([str(t.get('d') or (t.get('ts') or '')[:10]),
                     ('%s: %s' % (t.get('ch') or '', t.get('out') or '')).strip(': ')])
    if n.get('note'):
        rows.append(['Notes', str(n.get('note'))[:800]])
    sec('LINKS')
    kv('Appraiser', r.get('pa'))
    kv('Taxes', r.get('tax'))
    kv('Docket', r.get('docket'))
    kv('Aerial photo', r.get('aurl'))
    kv('Zillow', r.get('zillow'))
    tab = re.sub(r'[\\/?*\[\]:]', ' ', (r.get('addr') or case).split(',')[0]).strip()[:80] or case
    return {'tab': tab, 'case': case, 'rows': rows}


def build_prospects(rows_src, notes, st, pbl):
    brl = _load('broward_liens.json', {})
    mdl = _load('records_liens.json', {})
    out, seen_tabs = [], set()
    for r in rows_src:
        case = r.get('case') or ''
        n = notes.get(case) or {}
        if str(n.get('status') or '').strip().upper() not in CONSULT_STATUSES:
            continue
        b = _prospect_block(r, n, st, pbl, brl, mdl)
        while b['tab'] in seen_tabs:                      # two prospects on the same street name
            b['tab'] = (b['tab'] + ' ' + case[-4:])[:80]
        seen_tabs.add(b['tab'])
        out.append(b)
    return out


def _doorish(r):
    if r.get('title_status') == 'transferred':   # ownership flip: never send a rep to a stranger's door
        return False
    if r.get('vac'):                             # vacant land: no homeowner to help
        return False
    if r.get('cert'):                       # cert of title recorded = already sold
        return False
    if r.get('saleBkAct') or r.get('sale_bk_active'):
        return False
    if r.get('ju') or r.get('warn'):
        return False
    if (r.get('eqfake') or r.get('mr')) and r.get('orconf') not in ('ok', 'low'):
        return False
    val = float(r.get('value') or 0)
    judg = float(r.get('judg') or 0)
    return bool(val) and (val - judg) >= DOOR_MIN_EQ


def _chain_note(r, pbl):
    bits = []
    pb = pbl.get(r.get('case') or '') or {}
    if pb.get('chain_note'):
        bits.append(pb['chain_note'])
    elif r.get('orconf') == 'ok':
        bits.append('mortgage chain records-verified')
    elif r.get('orconf') == 'bd':
        bits.append('chain via BatchData (HOA/code/IRS not checked)')
    elif r.get('eqfake') or r.get('mr'):
        bits.append('JUNIOR-LIEN case - shown equity is gross, a senior mortgage may survive')
    if r.get('warn'):
        bits.append(str(r['warn']))
    if r.get('ju'):
        bits.append('judgment amount UNKNOWN')
    if r.get('cert'):
        bits.append('CERT OF TITLE RECORDED - property already sold')
    return ' | '.join(bits)


def _closed_cases():
    """Cases the ledgers closed: server opt-outs + dead ledger. A CRM must never resurface these
    (2026-08-18 audit: the sheet Jose dials from was carrying opted-out and dead leads)."""
    closed = set()
    for fn in ('optouts.json', 'deads.json'):
        d = _load(fn, {})
        inner = d.get('notes') if isinstance(d.get('notes'), dict) else d
        for k, v in (inner or {}).items():
            if k.startswith('_') or not isinstance(v, dict):
                continue
            closed.add(k)
    return closed


def build_rows(rows_src, notes, st, pbl):
    closed = _closed_cases()
    rows = []
    for r in rows_src:
        case = r.get('case') or ''
        if not case:
            continue
        n = notes.get(case) or {}
        status = str(n.get('status') or '').strip()
        if status.upper() in ('DEAD', 'DO NOT CONTACT'):
            continue
        if case in closed or n.get('optout') or n.get('wrongown'):
            continue
        if r.get('title_status') == 'transferred':      # live appraiser: no longer their house
            continue
        if r.get('saleBkAct') or r.get('sale_bk_active'):
            continue
        human = _human_touches(n)
        if not (status or human or _doorish(r)):
            continue
        val = float(r.get('value') or 0)
        judg = float(r.get('judg') or 0)
        neq = round(val - judg) if val and judg else (round(val) if val and not judg else '')
        eqp = r.get('eq') if r.get('eq') is not None else (round((val - judg) / val * 100) if val and judg else '')
        phones = _phones(case, st, n)
        last = human[-1] if human else None
        last_s = ('%s %s: %s' % (last.get('d') or (last.get('ts') or '')[:10],
                                 last.get('ch'), last.get('out') or '')).strip() if last else ''
        _d = r.get('days')
        _passed = isinstance(_d, (int, float)) and _d < 0
        rows.append([
            ('AUCTION PASSED - surplus only' if _passed else '') or status or ('Door-ready' if _doorish(r) else 'Worked'),
            r.get('addr') or '',
            (r.get('owners') or '').split(';')[0].strip(),
            _fmt_phone(phones[0]) if phones else '',
            ' / '.join(_fmt_phone(p) for p in phones[1:4]),
            r.get('county') or '',
            case,
            r.get('auction') or '',
            r.get('days') if r.get('days') is not None else '',
            round(val) if val else '',
            round(judg) if judg else '',
            neq,
            eqp if eqp != '' else '',
            _chain_note(r, pbl),
            last_s,
            len(human),
            n.get('next') or '',
            str(n.get('note') or '').replace('\n', ' | ')[:300],
        ])
    # hottest first: fewest days to auction, then biggest equity. PASSED auctions (days < 0) sort
    # LAST, not first — a held sale is a surplus conversation, never the top of the call sheet.
    def _key(x):
        d = x[8] if isinstance(x[8], (int, float)) else 9999
        return (1 if d < 0 else 0, d if d >= 0 else -d,
                -(x[11] if isinstance(x[11], (int, float)) else 0))
    rows.sort(key=_key)
    return rows


def push_sheet(rows, prospects=None):
    if not os.path.exists(WEBHOOK_F):
        print('sheets: no sheets_crm_webhook.url - CSV only. One-time setup: sheets_crm_SETUP.txt')
        return False
    url = open(WEBHOOK_F, encoding='utf-8').read().strip()
    if not url.startswith('https://script.google.com/'):
        print('sheets: webhook url does not look like an Apps Script deployment - not pushing')
        return False
    body = json.dumps({'headers': HEADERS, 'rows': rows, 'prospects': prospects or [],
                       'stamp': datetime.now().strftime('%Y-%m-%d %H:%M')}).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = resp.read().decode('utf-8', 'replace')[:200]
        print('sheets: pushed %d row(s) -> %s' % (len(rows), out))
        return True
    except Exception as e:
        # NEVER let a sheet outage break the refresh - the CSV is already on disk.
        print('sheets: push FAILED (%s) - CSV still written' % str(e)[:120])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv-only', action='store_true')
    a = ap.parse_args()
    rows_src = _leads()
    wn = _load('worker_notes.json', {})
    notes = wn.get('notes') if isinstance(wn.get('notes'), dict) else (wn if isinstance(wn, dict) else {})
    st = _load('skiptrace_results.json', {})
    pbl = _load('palmbeach_liens.json', {})
    rows = build_rows(rows_src, notes, st, pbl)
    prospects = build_prospects(rows_src, notes, st, pbl)
    os.makedirs(DESK, exist_ok=True)
    out = os.path.join(DESK, 'BSG_CRM.csv')
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:   # BOM so Excel/Sheets read accents
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)
    print('csv: %d working lead(s) -> %s' % (len(rows), out))
    print('prospects: %d consult tab(s): %s' % (len(prospects), ', '.join(p['tab'] for p in prospects)[:200]))
    if not a.csv_only:
        push_sheet(rows, prospects)


if __name__ == '__main__':
    main()
