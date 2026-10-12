#!/usr/bin/env python
"""outcome_learn.py -- which board signals actually led to a conversation? Read-only, counts only.

The learning loop's first step is measurement, not re-ranking. Call Mode and the board already log
every dial outcome into worker_notes.json (notes[case].dials[] = {d, ph4, oc, by}). This joins each
logged dial to the lead's signals as they read on the plaintext board twin and prints, per signal
bucket: dials, leads dialed, a human answered, and a real conversation (talked / callback / appt).

Signals are read from today's board (current sale date, current phone order), not as they were on
the dial day, so a rescheduled sale or a re-ranked phone list moves past dials between buckets.

Nothing here changes queue order. When a bucket has enough conversations to trust, the ordering
change is a separate, reviewed step.

Reads:   worker_notes.json (repo root), P.TWIN (plaintext board), *_leads.json as a fallback for
         leads that have since dropped off the board (sold / aged out).
Writes:  P.out('outcome_learn.json') -- counts only, no names, numbers, addresses or case ids.
Run:     python outcome_learn.py            # table to the console
         python outcome_learn.py --days 30  # only dials in the last 30 days
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

# CALL_OUTCOMES keys (tracker_template.html / call_mode.py). 'pending' = dial started, no outcome.
CONVO = {'talked', 'callback', 'appt'}
HUMAN = CONVO | {'notint', 'dnc', 'gate'}        # someone picked up (gate = not the owner)
NO_PICKUP = {'noanswer', 'voicemail'}
BADNUM = {'badnum', 'wrong'}
MIN_TRUST = 20    # a bucket needs this many dials before its rate means anything


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def load_board_rows():
    try:
        from foreclosure_report import load_board
        return load_board(P.TWIN)
    except Exception as e:
        print('board twin unreadable (%s): signals come from the raw lead files only' % e.__class__.__name__)
        return []


def load_raw_leads():
    import glob
    out = []
    for p in [os.path.join(HERE, 'leads_final.json')] + sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        b = os.path.basename(p)
        if b.startswith('_') or 'raw' in b or b == 'balloon_leads.json':
            continue
        rows = _load(p, [])
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            if not r.get('case') and r.get('Case #'):      # leads_final.json: unslimmed Miami-Dade rows
                r = {'case': r['Case #'], 'auction': r.get('AuctionDate') or '', 'county': 'MIAMI-DADE'}
            out.append(r)
    return out


def _date(s, fmt):
    try:
        return dt.datetime.strptime(str(s or '')[:10], fmt).date()
    except ValueError:
        return None


def _digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def sale_band(lead, dial_day):
    sale = _date(lead.get('auction'), '%m/%d/%Y')
    if not lead.get('auction'):
        return 'no sale date'                 # lis pendens: filed, no sale set yet
    if not sale or not dial_day:
        return 'unknown'
    n = (sale - dial_day).days
    if n < 0:
        return 'after sale'
    for hi, lab in ((7, '0-7d'), (21, '8-21d'), (45, '22-45d'), (90, '46-90d')):
        if n <= hi:
            return lab
    return '90d+'


def phone_source(lead, ph4):
    phs = lead.get('phones') or []
    src = lead.get('phsrc') or []
    typ = lead.get('phtype') or []
    for i, p in enumerate(phs):
        num = p.get('number') if isinstance(p, dict) else p
        if ph4 and _digits(num).endswith(ph4):
            s = src[i] if i < len(src) else ''
            t = typ[i] if i < len(typ) else ''
            return (str(s) or 'unknown'), (str(t) or 'unknown'), i
    return 'not on lead', 'unknown', None


def signals(lead, d):
    if lead is None:
        return {'county': 'lead gone'}
    day = _date(d.get('d'), '%Y-%m-%d')
    src, typ, idx = phone_source(lead, str(d.get('ph4') or ''))
    eq = lead.get('eq')                   # a PERCENT of value, not dollars (tracker_template r.eq)
    try:
        e = float(eq)
        eqb = 'none/0%' if e <= 0 else ('<30%' if e < 30 else ('30-60%' if e < 60 else '60%+'))
    except (TypeError, ValueError):
        eqb = 'unknown'
    return {
        'county': lead.get('county') or 'unknown',
        'stage': 'lis pendens' if lead.get('st') == 'LP' else ('foreclosure' if lead.get('st') else 'unknown'),
        'days to sale': sale_band(lead, day),
        'equity state': lead.get('eqstate') or 'unknown',
        'equity %': eqb,
        'homestead': 'yes' if lead.get('hs') else 'no',
        'tier': lead.get('tier') or '?',
        'phone source': src,
        'phone type': typ,
        'phone position': 'first' if idx == 0 else ('2nd-3rd' if idx in (1, 2) else ('4th+' if idx else 'n/a')),
        'caller': d.get('by') or 'unstamped',
    }


def compute(days=None, today=None):
    today = today or dt.date.today()
    wn = _load(os.path.join(HERE, 'worker_notes.json'), {})
    notes = wn.get('notes') or {}
    leads = {}
    for r in load_raw_leads():
        if r.get('case'):
            leads[r['case']] = r
    for r in load_board_rows():          # the board's view wins: it carries eqstate, phsrc, phtype
        if r.get('case'):
            leads[r['case']] = r
    cut = today - dt.timedelta(days=days) if days else None
    tab = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.Counter()))
    tot = collections.Counter()
    oc_all = collections.Counter()
    leads_dialed, leads_convo = set(), set()
    for case, n in notes.items():
        if not isinstance(n, dict):
            continue
        for d in n.get('dials') or []:
            if not isinstance(d, dict):
                continue
            oc = d.get('oc') or ''
            day = _date(d.get('d'), '%Y-%m-%d')
            if cut and (not day or day < cut):
                continue
            if oc in ('pending', 'redial'):   # attempt markers; the real outcome is its own row
                tot['pending (no outcome tapped)'] += 1
                continue
            oc_all[oc or 'blank'] += 1
            leads_dialed.add(case)
            if oc in CONVO:
                leads_convo.add(case)
            for sig, val in signals(leads.get(case), d).items():
                c = tab[sig][val]
                c['dials'] += 1
                c['human'] += oc in HUMAN
                c['convo'] += oc in CONVO
                c['badnum'] += oc in BADNUM
    tot['dials'] = sum(oc_all.values())
    tot['leads dialed'] = len(leads_dialed)
    tot['leads with a conversation'] = len(leads_convo)
    return {
        'generated': dt.datetime.now().isoformat(timespec='seconds'),
        'window_days': days,
        'notes_received_utc': wn.get('received_utc'),
        'totals': dict(tot),
        'outcomes': dict(oc_all),
        'by_signal': {s: {v: dict(c) for v, c in vals.items()} for s, vals in tab.items()},
    }


def render(res):
    out = []
    t = res['totals']
    out.append('OUTCOME LEARN  %s  (worker_notes received %s)' % (res['generated'], res['notes_received_utc'] or 'unknown'))
    out.append('dials %d | leads dialed %d | leads with a conversation %d | pending, no outcome tapped %d'
               % (t.get('dials', 0), t.get('leads dialed', 0), t.get('leads with a conversation', 0),
                  t.get('pending (no outcome tapped)', 0)))
    out.append('outcomes: ' + ', '.join('%s %d' % kv for kv in sorted(res['outcomes'].items(), key=lambda x: -x[1])))
    convos = sum(v for k, v in res['outcomes'].items() if k in CONVO)
    if convos < MIN_TRUST:
        out.append('VERDICT: %d conversations logged. Under %d, no signal below can be trusted to re-rank '
                   'the queue; it shows where to look, not what to change.' % (convos, MIN_TRUST))
    for sig, vals in res['by_signal'].items():
        out.append('')
        out.append('%-16s %7s %7s %7s %7s %8s' % (sig.upper(), 'dials', 'human', 'convo', 'badnum', 'convo%'))
        for v, c in sorted(vals.items(), key=lambda x: -x[1]['dials']):
            rate = (100.0 * c['convo'] / c['dials']) if c['dials'] else 0
            flag = '' if c['dials'] >= MIN_TRUST else '  (thin)'
            out.append('%-16s %7d %7d %7d %7d %7.1f%%%s' % (str(v)[:16], c['dials'], c['human'], c['convo'],
                                                         c['badnum'], rate, flag))
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=None, help='only dials in the last N days')
    a = ap.parse_args(argv)
    res = compute(a.days)
    print(render(res))
    try:
        with open(P.out('outcome_learn.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=1)
    except Exception as e:
        print('could not write outcome_learn.json: %s' % e.__class__.__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
