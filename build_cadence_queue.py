# -*- coding: utf-8 -*-
"""Build cadence_queue.json for owners the Morning Worker emailed ONCE and who never replied.

WHY THIS EXISTS
outreach_email._eligible drops these with 'worker already emailed - follow-ups are the worker's
job'. That is correct for the batch CLI, but nothing else picked them up: cadence.py — the 4-touch
sequence built for exactly this — had never run (no cadence_queue.json, no cadence_state.json on
disk as of 2026-08-22), because enrolling is a manual per-lead click in the tracker. So 97 warm
leads sat at one touch with nothing scheduled behind them.

THREE THINGS THIS GETS RIGHT THAT A NAIVE EXPORT WOULD NOT

1. STEP 1, NOT STEP 0. cadence seeds `step: lead.get('step', 0)`. These owners have already had
   touch #1 from the worker, so enrolling at 0 re-sends the opening email. They enroll at step 1
   and resume at touch #2 of 4.

2. THE SUPPRESSION CHECKS _eligible NEVER REACHED. Its gate order returns 'worker already emailed'
   BEFORE it tests opt-out, bankruptcy or bounce, so that bucket is not pre-screened. Re-checking
   found 1 owner who OPTED OUT after the first touch — enrolling them in a 4-touch sequence is the
   FTSA/TCPA fact pattern — plus 18 previously-bounced addresses (the account has run 24-33% bounce
   against a ~2% provider tolerance before), 8 companies, 1 active stay, 3 with no address.
   97 in, 66 out.

3. STAGGERED DATES. cadence has no cap, no throttle and no --limit: one run sends every sequence
   whose `next` is due. Enrolling 66 past-due owners at once would fire 66 back-to-back SMTP sends
   and bypass the 50/day ceiling outreach_email enforces deliberately. `next` is spread PER_DAY at
   a time, soonest-auction first, so the run rate stays inside that norm.

Writes nothing but cadence_queue.json. Sending still requires `python cadence.py` WITHOUT --dry-run.

Run:  python build_cadence_queue.py            # write the queue
      python build_cadence_queue.py --show     # print the plan, write nothing
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import outreach_email as OE

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, 'cadence_queue.json')
PER_DAY = 25            # matches the spirit of outreach_email's 50/day cap, halved for follow-ups


def _suppressed():
    out = set()
    for f in ('bounced_emails.json', 'bounces.json'):
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        if isinstance(d, dict):
            for k, v in d.items():
                out.add(str(k).strip().lower())
                if isinstance(v, str):
                    out.add(v.strip().lower())
        elif isinstance(d, list):
            for k in d:
                out.add(str(k).strip().lower())
    return out


def collect():
    leads = [r for r in OE._load_leads() if OE._tier(r) in {'A', 'B'}]
    ledger, optouts = OE._load_ledger(), OE._load_optouts()
    supp = _suppressed()

    pool, dropped = [], {}
    for r in leads:
        ok, why = OE._eligible(r, ledger, optouts, 24)
        if ok or not why.startswith('worker already emailed'):
            continue
        em = OE._primary_email(r)
        case = OE._case(r)
        if not em:
            dropped['no email'] = dropped.get('no email', 0) + 1
            continue
        if em in optouts or case.strip().lower() in optouts:
            dropped['OPTED OUT since first touch'] = dropped.get('OPTED OUT since first touch', 0) + 1
            continue
        if OE._is_company_owner(r):
            dropped['company owner'] = dropped.get('company owner', 0) + 1
            continue
        if (r.get('saleBkAct') or r.get('sale_bk_active')) and not r.get('saleLift'):
            dropped['active bankruptcy stay'] = dropped.get('active bankruptcy stay', 0) + 1
            continue
        if em in supp:
            dropped['previously bounced'] = dropped.get('previously bounced', 0) + 1
            continue
        pool.append((r, em))
    return pool, dropped


def sale_key(r):
    """Sort soonest-auction first; undated leads go last rather than jumping the queue."""
    s = OE._sale_date(r) or ''
    parts = s.split('/')
    if len(parts) == 3:
        try:
            return (0, int(parts[2]), int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (1, 9999, 99, 99)


ap = argparse.ArgumentParser()
ap.add_argument('--show', action='store_true', help='print the plan, write nothing')
ap.add_argument('--per-day', type=int, default=PER_DAY)
a = ap.parse_args()

pool, dropped = collect()
pool.sort(key=lambda t: sale_key(t[0]))

# A 4-touch sequence spans 7 days. Enrolling an owner whose auction is 2 days out means touches 3
# and 4 arrive AFTER the house is sold, and touch 2 lands the day before — an email that reads as
# careless at the worst possible moment. These go to the phone instead; every one of them has a
# number on file, which is the whole reason the call list exists.
URGENT_DAYS = 3
urgent, sequenceable = [], []
for r, em in pool:
    s = OE._sale_date(r) or ''
    try:
        n = (date(*[int(x) for x in (s.split('/')[2], s.split('/')[0], s.split('/')[1])]) - date.today()).days
    except Exception:
        n = 999
    (urgent if n <= URGENT_DAYS else sequenceable).append((r, em, n))

if urgent:
    st = {}
    p_st = os.path.join(HERE, 'skiptrace_results.json')
    if os.path.exists(p_st):
        st = json.load(open(p_st, encoding='utf-8'))
    print('CALL TODAY — auction within %d days, too close to email (%d):' % (URGENT_DAYS, len(urgent)))
    for r, em, n in urgent:
        raw = (st.get(OE._case(r)) or {}).get('phones') or []
        # skiptrace stores phones as dicts ({number, rank, ...}) on some providers and bare strings
        # on others — normalise rather than assuming either shape.
        phones = [str(p.get('number') or p.get('phone') or '') if isinstance(p, dict) else str(p)
                  for p in raw]
        phones = [p for p in phones if p]
        print('   %-22s %-22s sale %s (%dd)  %s'
              % (OE._case(r)[:22], (OE._owner_name(r) or '')[:22], OE._sale_date(r), n,
                 ', '.join(phones[:2]) if phones else 'NO PHONE'))
    print()

pool = [(r, em) for r, em, n in sequenceable]
start = date.today() + timedelta(days=1)     # never same-day; gives a chance to review the queue
queue = []
for i, (r, em) in enumerate(pool):
    queue.append({
        'case': OE._case(r),
        'owner': OE._owner_name(r) or (r.get('owners') or ''),
        'addr': r.get('Address') or r.get('addr') or '',
        'email': em,
        'auction': OE._sale_date(r) or '',
        'step': 1,                                    # touch #1 already went out via the worker
        'next': str(start + timedelta(days=i // max(1, a.per_day))),
    })

snd = {}
p = os.path.join(HERE, 'sender.json')
if os.path.exists(p):
    raw = json.load(open(p, encoding='utf-8'))
    snd = {k: v for k, v in raw.items() if not k.startswith('_')}

print('worker-emailed, never replied : %d' % (len(urgent) + len(sequenceable) + sum(dropped.values())))
for k, v in sorted(dropped.items(), key=lambda kv: -kv[1]):
    print('   dropped %-30s %d' % (k, v))
print('   routed to CALL (auction <=%dd)  %d' % (URGENT_DAYS, len(urgent)))
print('ENROLLING                     : %d  at step 1 of 4' % len(queue))
print()
by_day = {}
for q in queue:
    by_day[q['next']] = by_day.get(q['next'], 0) + 1
for d in sorted(by_day):
    print('   %s   %d owner(s)' % (d, by_day[d]))
print()
print('  soonest auctions in the queue:')
for q in queue[:6]:
    print('     %-22s %-24s %-28s sale %s' % (q['case'][:22], (q['owner'] or '')[:24], q['email'][:28], q['auction']))

if a.show:
    print('\n--show: nothing written.')
else:
    json.dump({'sender': snd, 'queue': queue}, open(QUEUE, 'w', encoding='utf-8'), indent=1)
    print('\nwrote %s (%d entries)' % (QUEUE, len(queue)))
    print('INERT until someone runs `python cadence.py` WITHOUT --dry-run.')
