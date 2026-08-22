#!/usr/bin/env python
"""diligence_list.py — Jesse's 3-5 minute deep-dive worklist. Which leads deserve a fine-tooth comb.

JESSE'S RULE (partner critique, 2026-08-14, after we hunted a non-owner for the SECOND time):
  "You only do this on pieces that warrant it... if there's an equity position there that you really
   believe, take 5 minutes, look at the OTHER cases filed under that person's name. And if you see the
   homeowners association listed as a CO-DEFENDANT on the foreclosure, that typically means there is an
   open lien or case with that association in the public record."

That is the cheapest early warning we have, and unlike the live-appraiser check in ownership_gate.py it
fires BEFORE the association's sale happens — while the deal is still savable. ownership_gate catches a
transfer that ALREADY occurred; this catches the one about to.

THE FILTER, and why each clause is there:
  * an HOA/association is a CO-DEFENDANT on our case  -> a second lien/case very likely exists
  * real equity (>=30%, not eq_fake)                  -> Jesse's "pieces that warrant it"; a plain
                                                         foreclosure we get paid to stop skips this
  * auction inside the window                         -> time still matters
Miami-Dade only today: leads_final carries a real 'defendants' string, while broward_leads/palmbeach_leads
hardcode defs='' (no party data at that source), so BW/PB cannot be screened this way yet — that gap is
the single highest-value data fix left, and it is why Milouse's HOA never appeared in any field we had.

Run:  python diligence_list.py                 # the worklist
      python diligence_list.py --days 90 --eq 20
"""
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# Association names in the wild: "FAIRHAVEN 11 MAINTENANCE CORP", "COMMODORE PLAZA CONDOMINIUM
# ASSOCIATION INC", "XYZ HOMEOWNERS ASSN", "L'HERMITAGE OWNERS ASSOCIATION INC", POA/master association.
HOA_RX = re.compile(
    r'\b(CONDOMINIUM|CONDO|HOMEOWNERS?|HOME\s*OWNERS?|MAINTENANCE\s+CORP|PROPERTY\s+OWNERS?|'
    r'MASTER\s+ASSOCIATION|OWNERS\s+ASSOCIATION|TOWNHOMES?|VILLAGE|CIVIC\s+ASSOCIATION|'
    r'\bPOA\b|\bHOA\b|ASSOCIATION|ASSN)\b', re.I)
# "BANK OF AMERICA, NATIONAL ASSOCIATION" is a bank, not an HOA.
BANK_RX = re.compile(r'NATIONAL\s+ASSOCIATION|\bN\.?\s?A\.?\s*$|BANK|MORTGAGE|TRUST\s+COMPANY', re.I)


def associations(defendants):
    """Association co-defendants on a case. '' / None safe.

    Prefers diligence_flags.hoa_parties() — the fuller detector built alongside this one, verified
    live on Acosta's case (returns COMMODORE PLAZA CONDOMINIUM ASSOCIATION INC). Keeping two HOA
    regexes in one repo guarantees they drift apart and start disagreeing about which leads are risky,
    so this defers to that module and only falls back to the local pattern if it is unavailable.
    """
    try:
        import diligence_flags as _DF
        if hasattr(_DF, 'hoa_parties'):
            got = _DF.hoa_parties({'defendants': defendants}) or []
            if isinstance(got, (list, tuple)):
                # hoa_parties returns dicts ({'name': ..., ...}); this list only ever needs the name,
                # so unwrap rather than printing the raw structure into the operator's worklist.
                out = []
                for x in got:
                    n = x.get('name') if isinstance(x, dict) else x
                    if n:
                        out.append(str(n))
                return out
    except Exception:
        pass
    out = []
    for part in str(defendants or '').split(';'):
        p = part.strip()
        if p and HOA_RX.search(p) and not BANK_RX.search(p):
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--eq', type=float, default=30.0)
    a = ap.parse_args()

    def _load(fn):
        try:
            d = json.load(open(os.path.join(HERE, fn), encoding='utf-8'))
            return d if isinstance(d, list) else list(d.values())
        except Exception:
            return []

    try:
        own = json.load(open(os.path.join(HERE, 'ownership.json'), encoding='utf-8'))
    except Exception:
        own = {}

    # (file, county, case_key, defendants_key, equity_key, days_key, owner_key)
    # Miami-Dade uses the fat leads_final shape; Broward/PB use the slim county shape. Broward/PB only
    # carry 'defs' once county_plaintiffs.py has resolved the case — until then they screen as empty,
    # which is reported below rather than silently looking like "no risk found".
    SRC = [('leads_final.json', 'MIAMI-DADE', 'Case #', 'defendants', 'equity_pct', 'days_to_auction', 'owners'),
           ('broward_leads.json', 'BROWARD', 'case', 'defs', 'eq', 'days', 'oname'),
           ('palmbeach_leads.json', 'PALM BEACH', 'case', 'defs', 'eq', 'days', 'oname')]

    rows, no_party = [], {}
    for fn, cty, ck, dk, ek, dyk, ok in SRC:
        leads = _load(fn)
        blind = 0
        for r in leads:
            if not isinstance(r, dict) or r.get('eq_fake'):
                continue
            eq = r.get(ek) or 0
            d = r.get(dyk)
            if eq < a.eq or not isinstance(d, (int, float)) or d < 0 or d > a.days:
                continue
            if not str(r.get(dk) or '').strip():
                blind += 1          # in-window, real equity, but we have no parties to screen
                continue
            hoas = associations(r.get(dk))
            if not hoas:
                continue
            case = r.get(ck)
            rows.append({'case': case, 'eq': eq, 'days': d, 'county': cty,
                         'owner': str(r.get(ok) or '')[:30], 'addr': r.get('Address') or r.get('addr', ''),
                         'hoa': hoas[0], 'plaintiff': str(r.get('plaintiff') or '')[:28],
                         'title': own.get(case, {}).get('title_status', 'not-scanned')})
        if blind:
            no_party[cty] = blind
    rows.sort(key=lambda x: (x['days'], -x['eq']))

    print('DEEP-DIVE LIST — association is a CO-DEFENDANT + equity >=%.0f%% + auction <=%dd' % (a.eq, a.days))
    print('%-4s %-5s %-20s %-30s %-36s %s' % ('EQ%', 'DAYS', 'CASE', 'OWNER', 'ASSOCIATION TO SEARCH', 'title'))
    print('-' * 132)
    for x in rows:
        print('%-4.0f %-5d %-20s %-30s %-36s %s'
              % (x['eq'], x['days'], x['case'], x['owner'], x['hoa'][:36], x['title']))
    print()
    print('%d lead(s) warrant the 3-5 minute check BEFORE anyone drives or dials.' % len(rows))
    if no_party:
        print()
        print('!! BLIND SPOT — these leads have real equity and a live clock but NO party data, so they')
        print('   cannot be screened for an association co-defendant at all:')
        for cty, n in sorted(no_party.items()):
            print('     %-12s %d lead(s)' % (cty, n))
        print('   Fix: run `python county_plaintiffs.py` to resolve parties for those counties.')
        print('   (This is the exact gap that hid the HOA case behind the Milouse lead.)')
    print()
    print('FOR EACH, BEFORE CONTACT:')
    print('  1. Search the ASSOCIATION name in the county clerk — does it have its own case on this owner?')
    print('  2. If yes: is there a Certificate of Title? Then the owner is already out — kill the lead.')
    print('  3. Check the property sale history: a recent sale on a distressed property is a red flag,')
    print('     not a comp.')
    print('  4. Only then hand it to the closer.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
