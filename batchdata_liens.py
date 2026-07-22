#!/usr/bin/env python3
"""batchdata_liens.py — the second lien source: BatchData Property API (no captcha, all counties).

The 2Captcha->Turnstile path (records_liens.py) reads Miami-Dade Official Records directly — the
richest source (exact satisfactions + assignments). BatchData covers what that can't: **Palm Beach
and Broward** (their own clerk walls), plus it returns two things public records don't hand you
cleanly — an AVM **estimated value** and a **current estimated balance** per open lien (payoff-ish,
not just the recorded doc-stamp amount).

We already pay BatchData for phones (skiptrace.py). Its `/property/lookup/all-attributes` endpoint
returns `openLien.mortgages[]` (lender, loanAmount, currentEstimatedBalance, recordingDate, HELOC
flag), `mortgageHistory[]`, `valuation` (estimatedValue, equityPercent), and `foreclosure`. This
normalizes `openLien` into the SAME shape records_liens.json uses (`liens[]`, `orsurv`, `orjuniors`,
`orsurvfirst`, `orconf`, `orftype`) so it flows through make_tracker and the Call Sheet debt stack
with zero UI change — plus `bd_value`/`bd_eqpct` and per-lien `bal` (current estimate).

Run:  python batchdata_liens.py [--all | --tier A | --case CASE] [--limit N] [--county "palm beach"]
Writes batchdata_liens.json (Case # -> normalized result), committed like records_liens.json.
"""
import argparse
import json
import os
import re
import time

import requests

import skiptrace as SK   # reuse parse_addr / _mailaddr / _propaddr / is_company

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'batchdata_liens.json')
API = 'https://api.batchdata.com/api/v1/property/lookup/all-attributes'
LENDER_HINT = re.compile(r'\b(BANK|MORTG|LOAN|LENDING|FINANC|CAPITAL|CREDIT|FUND|FSB|N\.?A\.?|TRUST|SERVICING)\b', re.I)


def _key():
    k = (os.environ.get('BATCHDATA_API_KEY') or '').strip()
    if k:
        return k
    p = os.path.join(HERE, 'batchdata.key')
    return open(p, encoding='utf-8').read().strip() if os.path.exists(p) else ''


def _iso(s):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', str(s or ''))
    return m.group(0) if m else ''


class BalanceExhausted(Exception):
    """BatchData returned 403 Insufficient balance — stop the whole run, don't churn 600 leads."""


def lookup(session, key, addr):
    """One property, all attributes. Returns the property dict or None.
    Raises BalanceExhausted on a 403 insufficient-balance so the caller can bail immediately
    instead of hammering the API with hundreds of guaranteed-failing calls."""
    try:
        r = session.post(API, json={'requests': [{'address': addr}]},
                         headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                         timeout=35)
        body = r.json() or {}
        if r.status_code == 403 or (body.get('status') or {}).get('code') == 403:
            msg = ((body.get('status') or {}).get('message') or 'Forbidden')
            if 'balance' in msg.lower():
                raise BalanceExhausted(msg)
        props = (body.get('results') or {}).get('properties') or []
        return props[0] if props else None
    except BalanceExhausted:
        raise
    except Exception:
        return None


def normalize(p, judg=0, ftype=''):
    """BatchData property -> records_liens.json shape. Uses the OPEN-lien determination BatchData
    already computed (it accounts for assignments/refis better than a raw name scrape), and carries
    the current estimated balance alongside the recorded amount."""
    ol = p.get('openLien') or {}
    open_mtgs = ol.get('mortgages') or []
    hist = p.get('mortgageHistory') or []
    val = p.get('valuation') or {}

    # build the chain: open liens (from openLien) + the rest of mortgageHistory marked satisfied
    liens = []
    open_keys = set()
    for m in open_mtgs:
        d = _iso(m.get('recordingDate'))
        amt = round(float(m.get('loanAmount') or 0))
        bal = round(float(m.get('currentEstimatedBalance') or amt))
        liens.append({'d': d, 'amt': amt, 'bal': bal, 'party': (m.get('lenderName') or 'NOT AVAILABLE').strip(),
                      'st': 'OPEN', '_dt': d, 'heloc': bool(m.get('equityCreditLine'))})
        open_keys.add((d, amt))
    for m in hist:
        d = _iso(m.get('recordingDate'))
        amt = round(float(m.get('loanAmount') or 0))
        if (d, amt) in open_keys:
            continue                                  # already counted as open
        liens.append({'d': d, 'amt': amt, 'party': (m.get('lenderName') or 'NOT AVAILABLE').strip(),
                      'st': 'SATISFIED', '_dt': d})
    liens.sort(key=lambda x: x.get('_dt') or '')

    open_liens = [l for l in liens if l['st'] == 'OPEN']
    # foreclosing loan = the open lien whose amount is closest to the judgment (same heuristic the
    # records path uses); everything senior to it survives, everything junior is a payoff/wipe.
    fore = None
    if open_liens and judg > 0:
        fore = min(open_liens, key=lambda l: abs((l.get('amt') or 0) - judg))
    surv = surv_first = juniors = 0
    if fore:
        fdate = fore.get('_dt') or ''
        seniors = [l for l in open_liens if (l.get('_dt') or '') < fdate]
        jrs = [l for l in open_liens if (l.get('_dt') or '') > fdate]
        surv = sum(l.get('bal') or l.get('amt') or 0 for l in seniors)
        surv_first = max([l.get('bal') or l.get('amt') or 0 for l in seniors], default=0)
        juniors = sum(l.get('bal') or l.get('amt') or 0 for l in jrs)
    elif ftype == 'HOA' and open_liens:
        # HOA sale: the whole mortgage stack survives
        surv = sum(l.get('bal') or l.get('amt') or 0 for l in open_liens)
        surv_first = max([l.get('bal') or l.get('amt') or 0 for l in open_liens], default=0)

    return {
        'liens': liens, 'open_count': len(open_liens),
        'junior': juniors, 'surv': surv, 'surv_first': surv_first, 'juniors_post': juniors,
        'hoa_open': 0, 'code_open': 0, 'irs_open': 0,
        'ftype': ftype or 'MORTGAGE', 'conf': 'bd',        # 'bd' = BatchData-sourced (distinct from ok/low)
        'bd_value': round(float(val.get('estimatedValue') or 0)),
        'bd_eqpct': val.get('equityPercent'),
        'bd_open_balance': round(float(ol.get('totalOpenLienBalance') or 0)),
        'traced': time.strftime('%Y-%m-%d'), 'source': 'batchdata',
    }


def _fc_type(case):
    c = (case or '').upper()
    if '-CA-' in c or c.startswith('CACE'):
        return 'MORTGAGE'
    if '-CC-' in c or c.startswith(('COCE', 'CONO', 'COWE', 'COSO')):
        return 'HOA'
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--tier', default='')
    ap.add_argument('--case', default='')
    ap.add_argument('--county', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true', help='re-pull even if already in the cache')
    a = ap.parse_args()

    key = _key()
    if not key:
        print('no batchdata.key / BATCHDATA_API_KEY'); return

    leads = []
    for fn, ck in (('leads_final.json', 'Case #'), ('broward_leads.json', 'case'), ('palmbeach_leads.json', 'case')):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            for r in json.load(open(p, encoding='utf-8')):
                leads.append((ck, r))

    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    session = requests.Session()
    picked, seen = [], set()
    for ck, r in leads:
        case = str(r.get(ck) or '')
        if not case or case in seen:
            continue
        seen.add(case)
        if a.case and case != a.case:
            continue
        if a.tier and (r.get('tier') or '') != a.tier:
            continue
        if a.county and (r.get('county') or 'MIAMI-DADE').upper() != a.county.upper():
            continue
        if case in out and not a.refresh and not a.case:
            continue
        picked.append((case, r))
    if a.limit:
        picked = picked[:a.limit]

    print(f'{len(picked)} leads to look up via BatchData property API')
    done = hits = 0
    for case, r in picked:
        addr = SK.parse_addr(SK._mailaddr(r)) or SK.parse_addr(SK._propaddr(r))
        if not addr:
            print(f'  --  {case:24} (no address)'); continue
        try:
            p = lookup(session, key, addr)
        except BalanceExhausted as e:
            print(f'\n!! BatchData balance exhausted ({e}). Stopping after {done} lookups.')
            print('   Top up at https://batchdata.com to resume Palm Beach / fallback coverage.')
            break
        if not p:
            print(f'  --  {case:24} (no BatchData match)'); continue
        judg = 0
        try:
            judg = float(r.get('judgment') or r.get('judg') or 0)
        except Exception:
            judg = 0
        res = normalize(p, judg=judg, ftype=_fc_type(case))
        out[case] = res
        done += 1
        if res['surv'] > 0:
            hits += 1
        tag = f"  surv ${res['surv']:,}" if res['surv'] else ''
        print(f"  ok  {case:24} {res['open_count']} open · value ${res['bd_value']:,}{tag}")
        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
        time.sleep(0.2)
    print(f'\nDONE: {done} looked up, {hits} with a surviving senior. -> batchdata_liens.json')


if __name__ == '__main__':
    main()
