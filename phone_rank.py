#!/usr/bin/env python
"""phone_rank — decide WHICH number to dial first, and which not to dial at all.

THE PROBLEM THIS SOLVES
A lead can carry six phone numbers and the board prints them in whatever order the skip-trace returned.
So the first dial is a coin flip: it might be the owner's cell, a dead landline from 2009, or a number
the registry says not to call. At 11-15 dials a day that wastes the scarcest thing in the business —
the operator's willingness to pick up the phone again.

Every signal needed to rank them is ALREADY in skiptrace_results.json (number, type, carrier, dnc).
No API, no cost, no new data. It was just never used.

THE SCORE, and why each piece
  * DNC FLAG -> never a "call first". 1,660 of 5,392 numbers (31%) carry one. These are returned in a
    separate blocked list, not merely ranked low, because calling one is per-call statutory exposure —
    a ranking that buries it still lets a tired thumb hit it at 7pm.
  * MOBILE beats LANDLINE. A mobile is the person; a landline is the house, and on a property in
    foreclosure the house is exactly what they may have already left. Mobile is also the only one that
    can legally be texted.
  * VOIP / IP-PHONE carriers score DOWN. 4%+ of the file is Bandwidth/Twilio/Onvoy/Comcast-IP style
    carriers, which skew heavily toward disconnected numbers, Google-Voice forwards and spam traps.
  * BLANK CARRIER scores down slightly — 1,352 numbers have none, which usually means a thinner
    underlying record.
  * EARLIER IN THE LIST scores marginally higher: providers return their own best match first, so it
    is weak evidence, weighted weakly.

Deliberately NOT scored: area code. A 305/786 number is not more likely to be answered than a 407,
and half of Miami has kept an out-of-state cell for a decade. Guessing there would only add noise.

Run:  python phone_rank.py --case 2025-020389-CA-01     # ranking for one lead
      python phone_rank.py --stats                      # how the whole file scores
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

VOIP_RX = re.compile(r'VOIP|VOICE\s?OVER|IP\s?PHONE|BANDWIDTH|TWILIO|ONVOY|PEERLESS|INTELIQUENT|'
                     r'LEVEL\s?3|TELEPORT|SINCH|VONAGE|MAGICJACK|GOOGLE', re.I)


def _is_mobile(t):
    return str(t or '').strip().lower().startswith('mob')


def _is_landline(t):
    # the file carries BOTH "Land Line" and "Landline" (1,667 + 1,097) — normalise or half of them
    # silently fall through to the unknown-type branch and get mis-scored.
    return re.sub(r'[^a-z]', '', str(t or '').lower()) == 'landline'


def score_phone(p, idx=0):
    """(score, label, reasons[]) for one phone dict. Higher = dial this sooner."""
    if not isinstance(p, dict):
        return -999, 'bad', ['not a phone record']
    reasons, s = [], 0
    typ, car = p.get('type'), str(p.get('carrier') or '')

    if p.get('dnc'):
        return -999, 'DO NOT CALL', ['on the Do Not Call registry']

    if _is_mobile(typ):
        s += 40; reasons.append('mobile (the person, and textable)')
    elif _is_landline(typ):
        s += 15; reasons.append('landline (the house, not the person)')
    else:
        s += 5; reasons.append('type unknown')

    if VOIP_RX.search(car):
        s -= 25; reasons.append('VOIP/IP carrier — often dead or a forward')
    elif not car.strip():
        s -= 10; reasons.append('no carrier on file — thin record')

    s -= idx * 2                      # weak: providers return their best match first
    label = 'CALL FIRST' if s >= 35 else ('ok' if s >= 10 else 'last resort')
    return s, label, reasons


def rank(phones):
    """-> (callable_sorted, blocked). callable_sorted is best-first; blocked are DNC-flagged."""
    scored = []
    for i, p in enumerate(phones or []):
        s, label, why = score_phone(p, i)
        rec = {'number': (p or {}).get('number'), 'type': (p or {}).get('type'),
               'carrier': (p or {}).get('carrier'), 'score': s, 'label': label, 'why': why}
        scored.append(rec)
    blocked = [r for r in scored if r['score'] == -999]
    ok = sorted([r for r in scored if r['score'] != -999], key=lambda r: -r['score'])
    return ok, blocked


def best(phones):
    """The one number to dial first, or None if every number is blocked/absent."""
    ok, _ = rank(phones)
    return ok[0] if ok else None


def _fmt(n):
    d = re.sub(r'\D', '', str(n or ''))
    return '(%s) %s-%s' % (d[:3], d[3:6], d[6:]) if len(d) == 10 else str(n or '')


def _load():
    """-> list of records, each guaranteed to carry a 'case'.

    skiptrace_results.json is a DICT KEYED BY CASE NUMBER whose records hold no 'case' field of their
    own. Flattening with .values() therefore loses the case entirely, which made --case lookups always
    report 'not found' while the whole-file stats still worked (they only touch 'phones')."""
    st = json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
    if isinstance(st, dict):
        out = []
        for k, v in st.items():
            if isinstance(v, dict):
                r = dict(v)
                r.setdefault('case', k)
                out.append(r)
        return out
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()
    rows = _load()

    if a.case:
        hit = next((r for r in rows if isinstance(r, dict)
                    and str(r.get('case') or r.get('c') or '') == a.case), None)
        if not hit:
            raise SystemExit('case %s not in skiptrace_results.json' % a.case)
        ok, blocked = rank(hit.get('phones'))
        print('CASE %s' % a.case)
        for i, r in enumerate(ok, 1):
            print('  %d. %-16s %-10s %-12s  %s' % (i, _fmt(r['number']), r['type'] or '?',
                                                   r['label'], '; '.join(r['why'])))
        for r in blocked:
            print('  XX %-16s %-10s DO NOT CALL   on the Do Not Call registry' % (_fmt(r['number']), r['type'] or '?'))
        if not ok and not blocked:
            print('  (no phones on file)')
        return 0

    # whole-file stats
    tot = mob = land = voip = dnc = leads = with_callable = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        ph = r.get('phones') or []
        if not ph:
            continue
        leads += 1
        ok, blocked = rank(ph)
        if ok:
            with_callable += 1
        dnc += len(blocked)
        for p in ph:
            if not isinstance(p, dict):
                continue
            tot += 1
            if _is_mobile(p.get('type')):
                mob += 1
            elif _is_landline(p.get('type')):
                land += 1
            if VOIP_RX.search(str(p.get('carrier') or '')):
                voip += 1
    print('PHONE FILE')
    print('  leads with any phone        %d' % leads)
    print('  leads with a CALLABLE phone %d  (%d have only DNC-flagged numbers)'
          % (with_callable, leads - with_callable))
    print('  phones total                %d' % tot)
    print('    mobile                    %d (%.0f%%)' % (mob, 100.0 * mob / tot if tot else 0))
    print('    landline                  %d (%.0f%%)' % (land, 100.0 * land / tot if tot else 0))
    print('    VOIP/IP carrier           %d (%.0f%%)' % (voip, 100.0 * voip / tot if tot else 0))
    print('    DNC-FLAGGED (never dial)  %d (%.0f%%)' % (dnc, 100.0 * dnc / tot if tot else 0))
    print()
    print('Dial the CALL FIRST number. It is a mobile on a real carrier — the person, not the house.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
