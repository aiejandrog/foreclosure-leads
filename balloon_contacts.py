#!/usr/bin/env python
"""balloon_contacts — the missing writer of balloon_contacts.json (the balloon lane's phones).

WHY (2026-10-22). balloon_leads.py reads phones for the BALLOON lane from exactly one file,
balloon_contacts.json, and its docstring names the step that fills it ("GetLeads / skiptrace, a
paid step, the operator's call"). That step was never written: no module in the repo creates the
file, Sunbiz publishes no phone numbers, so every balloon row reached the board and Call Mode
with no number. Call Mode drops a row with no number, so the lane was empty on the phone.

WHAT IT DOES. For balloon rows that have a Sunbiz OFFICER (a person, not a registered agent
firm) with a parseable address, it runs the same skiptrace call skiptrace.py makes for the officer
behind an LLC-owned foreclosure lead (skiptrace.trace_one: officer address, same provider, same
dollar caps, same SKIPTRACE_RUN_CAP / SKIPTRACE_DAY_CAP call counter), and writes the phones under
the row's BAL- id. A miss is recorded too, so the next run does not pay for the same officer again.

WHAT IT DOES NOT DO.
  * Rows tied to a foreclosure case (the "book" half of hardmoney_balloon) are skipped. Their
    owner is a foreclosure defendant, and the stay, opt-out and never-contact gates are keyed on
    that foreclosure case, not on the BAL- id. Copying phones onto a BAL row would let a held
    owner be dialed from the balloon lane. Only countywide-sweep rows (no foreclosure case) go.
  * No emails are written. An email on a BAL row puts it in the Morning Worker's send queue;
    whether LLC officers get email is a separate decision.
  * Not wired into any .bat. Dry run by default; --trace spends.

Run:  python balloon_contacts.py                      # dry run: who would be traced, est. cost
      python balloon_contacts.py --trace --limit 10   # spend, at most --max-spend dollars
Then python balloon_leads.py (or the 5:30 refresh) bakes the phones into the board.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import balloon_leads as BL  # noqa: E402

OUT = BL.CONTACTS
SRC = 'skiptrace-officer'


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except FileNotFoundError:
        return default


def _traced_recent(entry, days):
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(str((entry or {}).get('traced') or ''))).days
    except (TypeError, ValueError):
        return False
    return 0 <= age < days


def select(hits, contacts, parse_addr, dedupe_days=90):
    """Balloon hits worth one officer lookup, in the board's order. Returns lead-shaped dicts
    that skiptrace.trace_one accepts (_trace_addr / _trace_name / _trace_entity) plus the BAL id."""
    picked, why = [], {'foreclosure_case': 0, 'no_officer': 0, 'reg_agent': 0, 'no_addr': 0,
                       'have_contact': 0, 'traced_recent': 0, 'out_of_zone': 0}
    for h in hits:
        bal = BL._case_id(h)
        if str(h.get('case') or '').strip():
            why['foreclosure_case'] += 1
            continue
        try:
            age = float(h.get('age_mo'))
        except (TypeError, ValueError):
            age = None
        if age is None or not (BL.ZONE_LO <= age <= BL.ZONE_HI):
            why['out_of_zone'] += 1
            continue
        agent = str(h.get('agent') or '').strip()
        if not agent:
            why['no_officer'] += 1
            continue
        if '(reg. agent)' in agent:
            why['reg_agent'] += 1
            continue
        prior = contacts.get(bal) or contacts.get(h.get('owner') or '')
        if prior and (prior.get('phones') or prior.get('emails')):
            why['have_contact'] += 1
            continue
        if prior and _traced_recent(prior, dedupe_days):
            why['traced_recent'] += 1
            continue
        a = parse_addr(h.get('agent_addr') or '')
        if not a:
            why['no_addr'] += 1
            continue
        _mat, days = BL._maturity(str(h.get('origin') or ''))
        picked.append({
            'case': bal, 'county': h.get('county') or 'BROWARD',
            '_trace_addr': a, '_trace_name': BL._first_last(agent), '_trace_entity': h.get('owner') or '',
            '_title': h.get('agent_title') or '',
            '_sort': ((days if days is not None else 10 ** 6), not bool(h.get('fits')), -int(h.get('amt') or 0)),
        })
    picked.sort(key=lambda r: r['_sort'])
    return picked, why


def entry(lead, phones, provider, outcome=''):
    row = {'phones': [p for p in (phones or []) if isinstance(p, dict) and p.get('number')],
           'emails': [], 'name': lead['_trace_name'], 'title': lead.get('_title') or '',
           'src': SRC, 'provider': provider, 'traced': datetime.date.today().isoformat()}
    if outcome:
        row['outcome'] = outcome
    return row


def save(contacts):
    tmp = OUT + '.tmp'
    json.dump(contacts, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--trace', action='store_true', help='call the provider (spends). Default is a dry run.')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--max-spend', type=float, default=2.0)
    ap.add_argument('--provider', default='')
    ap.add_argument('--dedupe-days', type=int, default=90, help='a recorded miss is not paid for again inside this window')
    a = ap.parse_args()

    import hardmoney_balloon as HB
    import skiptrace as ST
    try:
        contacts = _load(OUT, {})
    except Exception as e:
        print('balloon_contacts.json is unreadable (%s). Nothing traced; fix or move the file.' % e)
        sys.exit(1)
    if not isinstance(contacts, dict):
        print('balloon_contacts.json is not an object. Nothing traced.')
        sys.exit(1)

    picked, why = select(HB.candidates(report=False), contacts, ST.parse_addr, a.dedupe_days)
    provider = ST.pick_provider(a.provider)
    cost = ST.PROVIDERS[provider]['cost']
    todo = picked[:max(0, a.limit)]
    print('balloon contacts: %d officer(s) traceable, %d queued (limit %d). provider %s, est. $%.2f'
          % (len(picked), len(todo), a.limit, provider, len(todo) * cost))
    print('  skipped: ' + ', '.join('%s %d' % (k, v) for k, v in why.items() if v))
    if not a.trace:
        print('(dry run, no API call. Add --trace to spend.)')
        return
    if not todo:
        print('nothing to trace.')
        return
    if len(todo) * cost > a.max_spend:
        print('ABORT: ~$%.2f is over --max-spend $%.2f. Nothing was traced.' % (len(todo) * cost, a.max_spend))
        sys.exit(4)
    key = ST.load_key(provider)
    if not key:
        print("NO API KEY for '%s'. Nothing was traced." % provider)
        sys.exit(1)

    import requests
    ST._RUN_CALLS = 0
    s = requests.Session()
    got = done = 0
    try:
        for lead in todo:
            try:
                phones, _emails = ST.trace_one(s, provider, key, lead)
                contacts[lead['case']] = entry(lead, phones, provider)
                done += 1
                got += 1 if contacts[lead['case']]['phones'] else 0
            except ST.TraceSkip as e:
                contacts[lead['case']] = entry(lead, [], provider, outcome='skip')
                print('  skip %s: %s' % (lead['case'], str(e)[:80]))
            save(contacts)
    except ST.bd_budget.BudgetExhausted as e:
        print('stopped at a cap: %s' % str(e)[:160])
    except ST.TraceAborted as e:
        print('provider refused: %s' % str(e)[:160])
        sys.exit(2)
    except ST.TraceTransient as e:
        print('provider error: %s' % str(e)[:160])
        sys.exit(3)
    finally:
        print('balloon contacts: %d traced, %d with a phone. Rebuild with python balloon_leads.py.' % (done, got))


if __name__ == '__main__':
    main()
