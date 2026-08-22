#!/usr/bin/env python
"""tracerfy_mcp — the Tracerfy lanes BatchData never had: registry DNC scrub + APN/parcel trace.

Talks to Tracerfy's MCP endpoint (URL with embedded auth in gitignored tracerfy_mcp.url).
skiptrace.py keeps owning ordinary skip-traces over REST (tracerfy.key, Bearer) — this module
adds only what that rail can't do, and reuses skiptrace's own lead loader + eligibility so the
two can never disagree about who is traceable.

EVERY paid call runs through bd_budget's shared daily-dollar ledger at $0.02/credit — same
ceiling as every other spender. Dry-run is the DEFAULT for paid lanes; nothing bills without
--live. Balance is checked first and a 0-credit account stops before the first call.

  python tracerfy_mcp.py balance             # free — credits remaining
  python tracerfy_mcp.py plan                # free — full projected spend, all lanes, no calls
  python tracerfy_mcp.py batchcsv            # free — CSV of untraced leads for the 1cr/hit web batch upload
  python tracerfy_mcp.py dnc [--limit N] [--live]     # registry-scrub dnc:false phones (1 cr each)
  python tracerfy_mcp.py parcel [--limit N] [--live]  # APN-trace company/estate dead ends (5 cr/hit)

Lanes:
  * dnc    — the board's send/call gate trusts the provider's MODELED dnc flag; the site footer
             admits it is "not a National DNC Registry scrub". This lane runs the phones that flag
             currently lets through (dnc:false) against the actual federal/state registries and
             writes dnc_scrub.json; the board bake overrides the modeled flag with the registry
             verdict, so the compliance gate becomes enforceable instead of advisory.
  * parcel — owners person-tracing can't reach: companies/estates with no Sunbiz officer and
             cached no-hit traces. parcel_lookup(APN, county) finds the human behind the parcel.
             Hits are written into skiptrace_results.json in skiptrace's own schema
             (source 'tracerfy-parcel') so the board bakes them with zero new code. Their phones
             carry NO dnc flag on purpose — the dnc lane must clear them before anyone dials.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import types

import requests

import bd_budget
import skiptrace as SK
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_URL_F = os.path.join(HERE, 'tracerfy_mcp.url')
DNC_F = os.path.join(HERE, 'dnc_scrub.json')          # gitignored sidecar {phone: verdict}
CR = 0.02                                             # dollars per credit
# dnc: measured live 2026-08-11 — the MCP per-call check bills FIVE credits (credits_deducted:5),
# not the 1cr the pricing page quotes for the web BATCH scrub product. Per-call is for small urgent
# sets only; route bulk scrubs through `dnccsv` + the web upload at the real 1cr/phone.
COST = {'dnc': 5 * CR, 'dnc_batch': 1 * CR, 'parcel': 5 * CR,
        'trace_instant': 5 * CR, 'trace_batch': 1 * CR}
DNC_TTL_DAYS = 30                                     # registry status is re-checkable after this

COUNTY_OF = {'MIAMI-DADE': 'Miami-Dade', 'BROWARD': 'Broward', 'PALM BEACH': 'Palm Beach'}


def _fmt_folio(folio, county):
    """Tracerfy's parcel_lookup 500s on undashed folios (verified live 2026-08-11: 3220240340060
    -> empty server error AND a mystery balance hit; 32-2024-034-0060 -> clean miss, 0 deducted).
    Dash per county convention; unknown shapes pass through untouched."""
    f = re.sub(r'[^0-9A-Za-z]', '', str(folio or ''))
    if county == 'Miami-Dade' and len(f) == 13 and f.isdigit():
        return '%s-%s-%s-%s' % (f[0:2], f[2:6], f[6:9], f[9:13])
    if county in ('Broward', 'Palm Beach') and len(f) == 12 and f.isdigit():
        return '%s-%s-%s-%s' % (f[0:4], f[4:6], f[6:8], f[8:12])
    return str(folio or '')


def _url():
    try:
        return open(MCP_URL_F, encoding='utf-8').read().strip()
    except OSError:
        sys.exit('tracerfy_mcp.url missing — paste the MCP URL there (gitignored).')


def call(tool, arguments=None, timeout=90):
    """One MCP tools/call. Fail-loud: a transport error or isError result raises, it never
    returns half-data — a scrub that silently skipped is a compliance hole, not a soft miss."""
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': tool, 'arguments': arguments or {}}}
    r = requests.post(_url(), json=body, timeout=timeout,
                      headers={'Accept': 'application/json, text/event-stream'})
    r.raise_for_status()
    data = None
    for line in r.text.splitlines():
        if line.startswith('data: '):
            data = json.loads(line[6:])
    if data is None:                                   # plain JSON (non-SSE) transport
        data = r.json()
    res = data.get('result') or {}
    if res.get('isError'):
        raise RuntimeError('%s error: %s' % (tool, json.dumps(res)[:300]))
    txt = ''.join(c.get('text', '') for c in res.get('content', []) if c.get('type') == 'text')
    try:
        return json.loads(txt)
    except ValueError:
        return {'_raw': txt}


def balance():
    return int((call('check_balance') or {}).get('credits') or 0)


# ---- shared eligibility (skiptrace's own logic, never a re-implementation) ---------------------
def _board():
    leads = SK.load_all_leads()
    args = types.SimpleNamespace(case='', all=True, tier='')
    llcs = None
    try:
        llcs = json.load(open(os.path.join(HERE, 'llc_officers.json'), encoding='utf-8'))
    except Exception:
        pass
    return SK.select(leads, args, llcs)


def _cache():
    try:
        return json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
    except Exception:
        return {}


def _dnc_scrubbed():
    try:
        return json.load(open(DNC_F, encoding='utf-8'))
    except Exception:
        return {}


def _dnc_targets():
    """Phones the modeled flag currently ALLOWS (dnc:false) and the registry hasn't confirmed
    inside the TTL — ordered most-urgent lead first (days to auction), so a budget-capped run
    covers the numbers the operator will actually dial this month before the long tail."""
    cache, done = _cache(), _dnc_scrubbed()
    cutoff = (datetime.date.today() - datetime.timedelta(days=DNC_TTL_DAYS)).isoformat()
    days_of = {}
    for r in _board():
        c = SK._case(r)
        try:
            d = int(r.get('days_to_auction') if r.get('days_to_auction') is not None else r.get('days'))
        except (TypeError, ValueError):
            d = 9999
        if c and (c not in days_of or d < days_of[c]):
            days_of[c] = d
    out = []
    for case, e in cache.items():
        for p in (e.get('phones') or []):
            n = re.sub(r'\D', '', str(p.get('number') or ''))
            if len(n) != 10 or p.get('dnc') is not False:
                continue
            d = done.get(n)
            if d and (d.get('checked') or '') >= cutoff:
                continue
            out.append((case, n))
    out.sort(key=lambda cn: days_of.get(cn[0], 9999))
    seen, uniq = set(), []
    for case, n in out:
        if n not in seen:
            seen.add(n)
            uniq.append((case, n))
    return uniq


def _parcel_targets():
    """Company/estate-owned leads with a folio where the person rail is a proven dead end:
    no cached trace, or a cached trace that returned zero phones."""
    cache = _cache()
    out = []
    for r in _board():
        case = SK._case(r)
        owner = (r.get('owners') or '').split(';')[0].strip()
        folio = re.sub(r'[^0-9A-Za-z-]', '', str(r.get('folio') or r.get('Folio') or ''))
        if not case or not folio or not owner:
            continue
        if not (SK.is_company(owner) or re.search(r'\bEST(ATE)?\b', owner.upper())):
            continue
        got = cache.get(case)
        if got and (got.get('phones') or []):
            continue
        if got and (got.get('source') or '') == 'tracerfy-parcel':
            continue                                   # already tried this rail — a miss stays a miss
        cty = COUNTY_OF.get((r.get('county') or 'MIAMI-DADE').upper())
        if not cty:
            continue
        out.append({'case': case, 'owner': owner, 'folio': folio, 'county': cty})
    return out


# ---- lanes -------------------------------------------------------------------------------------
def run_dnc(limit, live):
    targets = _dnc_targets()
    if limit:
        targets = targets[:limit]
    cost = len(targets) * COST['dnc']
    print('DNC scrub lane: %d phone(s) the modeled flag lets through -> %d credits (~$%.2f)'
          % (len(targets), len(targets), cost))
    if not live:
        print('(dry run — pass --live to spend)')
        return
    bal = balance()
    if bal < len(targets):
        print('balance %d credits < %d needed — running what fits' % (bal, len(targets)))
        targets = targets[:bal]
    done = _dnc_scrubbed()
    flagged = errs = 0
    for i, (case, n) in enumerate(targets, 1):
        bd_budget.require(COST['dnc'], 'tracerfy dnc')
        try:
            v = call('dnc_check', {'phone': n})
        except Exception as e:
            errs += 1
            print('  ERR %s: %s' % (n, str(e)[:100]))
            if errs >= 5:
                print('ABORT: 5 provider errors — stopping.')
                break
            time.sleep(1.0)
            continue
        bd_budget.charge(COST['dnc'], 'tracerfy-dnc')
        rec = {'national_dnc': bool(v.get('national_dnc')), 'state_dnc': bool(v.get('state_dnc')),
               'states': v.get('state_dnc_list') or [], 'case': case,
               'checked': datetime.date.today().isoformat()}
        done[n] = rec
        if rec['national_dnc'] or rec['state_dnc']:
            flagged += 1
        # checkpoint EVERY result — billed-but-unsaved is money lost (learned at 12 results, 08-11)
        tmp = DNC_F + '.tmp'
        json.dump(done, open(tmp, 'w', encoding='utf-8'), indent=1)
        os.replace(tmp, DNC_F)
        if i % 20 == 0 or i == len(targets):
            print('  [%d/%d] %d registry-listed, %d error(s)' % (i, len(targets), flagged, errs))
        time.sleep(0.3)
    print('DONE: %d scrubbed, %d ON a registry (modeled flag said safe), %d errors -> dnc_scrub.json; '
          'rebuild the board to enforce.' % (len(done), flagged, errs))


def run_dnccsv(limit=0):
    """CSV of still-unscrubbed dnc:false phones for the web BATCH DNC scrub (1 cr/phone — 5x cheaper
    than the per-call MCP check). Upload click is the operator's.

    --limit MATTERS HERE and used to be silently ignored. The full unscrubbed set is ~3,640 phones
    (~$72.80) against a balance that has been sitting near 1,076 credits, so an unbounded CSV is a
    file the operator cannot actually upload — and one they may upload PARTIALLY, in provider order,
    which is not the order that matters. _dnc_targets() already sorts most-urgent-first by days to
    auction, so a capped slice is exactly the numbers that get dialled soonest. Cap it and say plainly
    what was left out, rather than shipping an unaffordable file that looks complete."""
    targets = _dnc_targets()
    total = len(targets)
    if limit:
        targets = targets[:limit]
    out = P.out('Tracerfy_DNC_%s.csv' % datetime.date.today().isoformat())
    import csv
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['phone', 'case'])
        w.writerows([(n, case) for case, n in targets])
    print('%d unscrubbed phone(s) -> %s' % (len(targets), out))
    if limit and total > len(targets):
        print('CAPPED: %d of %d written (most-urgent-first by days to auction). '
              '%d NOT in this file and still unscrubbed.' % (len(targets), total, total - len(targets)))
    print('web batch scrub: %d cr (~$%.2f); the MCP per-call route would be %d cr (~$%.2f)'
          % (len(targets), len(targets) * COST['dnc_batch'],
             len(targets) * 5, len(targets) * COST['dnc']))
    if limit and total > len(targets):
        print('full set would be %d cr (~$%.2f) via batch.' % (total, total * COST['dnc_batch']))


def run_parcel(limit, live):
    targets = _parcel_targets()
    if limit:
        targets = targets[:limit]
    cost = len(targets) * COST['parcel']
    print('Parcel lane: %d company/estate dead end(s) with a folio -> worst case %d credits (~$%.2f; misses free)'
          % (len(targets), len(targets) * 5, cost))
    if not live:
        for t in targets[:10]:
            print('  would trace: %-32s folio %s (%s)' % (t['owner'][:32], t['folio'], t['county']))
        if len(targets) > 10:
            print('  ... +%d more' % (len(targets) - 10))
        print('(dry run — pass --live to spend)')
        return
    cache = _cache()
    hits = errs = 0
    for i, t in enumerate(targets, 1):
        bd_budget.require(COST['parcel'], 'tracerfy parcel')
        try:
            v = call('parcel_lookup', {'parcel_id': _fmt_folio(t['folio'], t['county']),
                                       'county': t['county'], 'state': 'FL'})
        except Exception as e:
            errs += 1
            print('  ERR %s (%s): %s' % (t['folio'], t['county'], str(e)[:100]))
            if errs >= 5:
                print('ABORT: 5 provider errors — stopping before more mystery deductions.')
                break
            time.sleep(1.0)
            continue
        persons = (v or {}).get('persons') or []
        phones, emails = SK._collect(persons, phone_field='phones',
                                     order=lambda p: (p.get('rank') or 9999))
        if phones or emails:
            bd_budget.charge(COST['parcel'], 'tracerfy-parcel')   # billed per HIT, misses free
            hits += 1
            # phones keep the provider's modeled dnc flag (same trust as every trace); any that
            # arrive dnc:false enter the registry-scrub lane's target set automatically.
            cache[t['case']] = {'name': t['owner'], 'address': '', 'phones': phones,
                                'emails': emails, 'traced': datetime.date.today().isoformat(),
                                'source': 'tracerfy-parcel'}
        else:
            cache.setdefault(t['case'], {'name': t['owner'], 'address': '', 'phones': [],
                                         'emails': [],
                                         'traced': datetime.date.today().isoformat(),
                                         'source': 'tracerfy-parcel'})
        # checkpoint EVERY result — a billed hit that dies before a batched save is money lost
        tmp = os.path.join(HERE, 'skiptrace_results.json.tmp')
        json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=1)
        os.replace(tmp, os.path.join(HERE, 'skiptrace_results.json'))
        if i % 10 == 0 or i == len(targets):
            print('  [%d/%d] %d hit(s), %d error(s)' % (i, len(targets), hits, errs))
        time.sleep(0.4)
    print('DONE: %d/%d parcels resolved to a human (%d errors) -> skiptrace_results.json; rebuild to bake.'
          % (hits, len(targets), errs))


def _addr_str(r):
    """select() attaches _trace_addr as skiptrace's parsed dict {street, city, state, zip}."""
    a = r.get('_trace_addr') or {}
    if not isinstance(a, dict):
        return str(a).strip()
    parts = [a.get('street') or '', a.get('city') or '', a.get('state') or '', a.get('zip') or '']
    return ', '.join(p for p in parts if p).strip(', ')


def run_batchcsv():
    """CSV for Tracerfy's web batch upload (1 cr/hit — 5x cheaper than instant). The upload click
    is the operator's; this just makes the file exactly one lane wide."""
    cache = _cache()
    rows = []
    for r in _board():
        case = SK._case(r)
        if not case or case in cache:
            continue
        a = r.get('_trace_addr') or {}
        raw = _addr_str(r)
        if not raw:
            continue
        rows.append({'case': case, 'name': r.get('_trace_name') or '',
                     'street': (a.get('street') or '') if isinstance(a, dict) else raw,
                     'city': (a.get('city') or '') if isinstance(a, dict) else '',
                     'state': (a.get('state') or 'FL') if isinstance(a, dict) else 'FL',
                     'zip': (a.get('zip') or '') if isinstance(a, dict) else ''})
    out = P.out('Tracerfy_Batch_%s.csv' % datetime.date.today().isoformat())
    import csv
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['case', 'name', 'street', 'city', 'state', 'zip'])
        w.writeheader()
        w.writerows(rows)
    print('%d untraced lead(s) -> %s' % (len(rows), out))
    print('batch product bills 1 cr/hit = ~$%.2f worst case (instant would be ~$%.2f)'
          % (len(rows) * COST['trace_batch'], len(rows) * COST['trace_instant']))


def run_plan():
    bal = balance()
    dnc_n = len(_dnc_targets())
    par_n = len(_parcel_targets())
    cache = _cache()
    untraced = sum(1 for r in _board() if SK._case(r) and SK._case(r) not in cache
                   and _addr_str(r))
    print('=== TRACERFY PROJECTED SPEND (no calls made, balance %d credits) ===' % bal)
    print('1. trace backlog : %4d untraced leads  -> %5d cr (~$%6.2f) via WEB BATCH upload (1cr/hit)'
          % (untraced, untraced, untraced * COST['trace_batch']))
    print('                                          %5d cr (~$%6.2f) if run through skiptrace.py instant instead'
          % (untraced * 5, untraced * COST['trace_instant']))
    print('2. DNC registry  : %4d dnc:false phones -> %5d cr (~$%6.2f)  [tracerfy_mcp.py dnc --live]'
          % (dnc_n, dnc_n, dnc_n * COST['dnc']))
    print('3. parcel lane   : %4d LLC/estate deads -> %5d cr (~$%6.2f) worst case, misses free  [tracerfy_mcp.py parcel --live]'
          % (par_n, par_n * 5, par_n * COST['parcel']))
    total = untraced + dnc_n + par_n * 5
    print('TOTAL (batch-first routing): ~%d credits (~$%.2f). Daily ceiling: bd_budget $%.2f/day '
          '— raise per-run with BATCHDATA_DAILY_CAP for the one-time catch-up.'
          % (total, total * CR, bd_budget.cap()))
    print('skiptrace.py stays the nightly incremental rail (auto-Tracerfy now that tracerfy.key exists).')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['balance', 'plan', 'dnc', 'dnccsv', 'parcel', 'batchcsv'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--live', action='store_true', help='actually spend credits (default: dry run)')
    a = ap.parse_args()
    if a.cmd == 'balance':
        print('credits:', balance())
    elif a.cmd == 'plan':
        run_plan()
    elif a.cmd == 'dnc':
        run_dnc(a.limit, a.live)
    elif a.cmd == 'dnccsv':
        run_dnccsv(a.limit)
    elif a.cmd == 'parcel':
        run_parcel(a.limit, a.live)
    elif a.cmd == 'batchcsv':
        run_batchcsv()


if __name__ == '__main__':
    main()
