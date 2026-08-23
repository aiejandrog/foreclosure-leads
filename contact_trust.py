#!/usr/bin/env python3
"""contact_trust — flag skip-traced contacts that belong to a DIFFERENT HUMAN than the owner.

WHY THIS EXISTS (2026-08-23)
Two failures in one week, both the same shape and both nearly worked a stranger as if he were the
seller:

  * 1400 SAINT CHARLES PL "#107" (CACE-25-003554). The court's unit label does not exist on the
    county roll — the building is lettered L1..L8 and the real unit is #L7. The address-keyed trace
    therefore returned the occupant of a DIFFERENT unit: four phones and three emails belonging to
    JOHN CARDENAS of #201, sitting on a card whose owner is the ESTATE OF BARBARA COONEY.
  * 1343 PONCE DE LEON DR. The trace supplied `wamlong@gmail.com`, an address that has never
    existed — it hard-bounced 550 the moment it was used.

Jesse's standing critique is exactly this: "TWICE we hunted a non-owner." A wrong number is a
wasted dial; a wrong PERSON is a stranger being told their home is in foreclosure.

WHAT IT DOES NOT FLAG — and this is most of the work
A naive surname comparison calls 30% of the book a mismatch and is useless. Three legitimate
patterns dominate and every one of them is CORRECT data:
  * entity -> principal:  "RIM GROUP INVESTMENTS CORP" -> "GORRIN, RAUL A".  Resolving an LLC to a
    human is the single most valuable thing a trace does. Never flag it.
  * role suffixes:        "ROSA M MARTINEZ" -> "ROSA M MARTINEZ LE" (life estate), "ELLIOT LEVY" ->
    "ELLIOT LEVY TRS" (trustee), "... EST" (estate).  Same human.
  * spouse joins:         "VYACHESLAV MIKHAILOV" -> "VYACHESLAV MIKHAILOV &W NE...".  Same human.

So the flag fires only when BOTH sides are human, suffixes are stripped, and the surnames still
disagree — plus the separate, cheap check that a co-owner surname does not cover it.

Usage:
    python contact_trust.py                 # report only
    python contact_trust.py --write         # stamp suspect leads in skiptrace_results.json
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import paths as P
    TWIN = P.TWIN
except Exception:
    TWIN = os.path.join(os.path.expanduser('~'), 'DEALFLOW', 'Foreclosure Lead Tracker.html')

# an owner string containing any of these is an ENTITY; a human trace result is the desired answer
ENTITY = re.compile(r'\b(LLC|L\.L\.C|INC|CORP|CO|COMPANY|TRUST|TR|LTD|LP|LLP|PA|PLLC|ASSOC(?:IATION)?|'
                    r'BANK|HOLDINGS?|PROPERTIES|INVESTMENTS?|GROUP|ENTERPRISES|VENTURES?|REALTY|'
                    r'MANAGEMENT|PARTNERS|FUND|CHURCH|MINISTRIES|CONDOMINIUM|UNITED\s+STATES|'
                    r'DEPARTMENT|COUNTY|CITY\s+OF|STATE\s+OF|HOMES?|BUILDERS?|CAPITAL|EQUITY)\b', re.I)

# Not a name at all — the pipeline's own placeholder for "we never resolved the owner". Comparing
# it to anything produces a guaranteed false mismatch, and it accounted for most of the first
# run's noise.
PLACEHOLDER = re.compile(r'owner\s+via\s+title|unknown|not\s+available|^n/?a$|title\s+search', re.I)

# role / status noise that rides along with the SAME human's name on the county roll
SUFFIX = re.compile(r'\b(TRS|TRUSTEE|TR|LE|LIFE\s+EST(?:ATE)?|EST(?:ATE)?|H/E|W/E|&\s*W|&\s*H|'
                    r'ET\s*AL|ETAL|JR|SR|II|III|IV|MD|ESQ|DECD|DECEASED|REV(?:OCABLE)?)\b', re.I)


def _people(s):
    """Surname TOKENS in an owner string, entity/suffix/placeholder noise removed; empty when the
    string is an entity or a placeholder.

    Tokens, not whole surnames, because compound surnames are the norm here and comparing them as
    strings manufactures mismatches: 'PAOLA ALEXANDRA OROZCO A' vs 'OROZCO AGUIRRE,PAOLA ALE' is
    ONE woman, and 'LICIA H/E SAINT AMOUR' vs 'SAINT AMOUR,LICIA H/E' is one man. Any shared token
    means same family.
    """
    s = str(s or '')
    if not s.strip() or PLACEHOLDER.search(s) or ENTITY.search(s):
        return set()
    s = SUFFIX.sub(' ', s)
    s = re.sub(r'[^A-Za-z,& ]', ' ', s)
    out = set()
    # county rolls write "LAST,FIRST" ~a third of the time, and people write "First Last"
    for chunk in re.split(r'&|\band\b', s, flags=re.I):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p for p in chunk.split() if len(p) > 1]
        if not parts:
            continue
        if ',' in chunk:
            surname_part = chunk.split(',')[0]
            cands = surname_part.split()
        else:
            # WORD ORDER IS AMBIGUOUS WITHOUT A COMMA and this is not a corner case: the same human
            # appears as 'SALAZAR, ANFRES EDUARDO' on the roll and 'SALAZAR ANFRES EDUARDO' from the
            # tracer. Assuming Western order made every one of those read as a different person.
            # Both readings are admitted — leading token (LAST FIRST) and trailing pair (First Last,
            # incl. compound surnames). Over-admitting merges two real strangers only when they
            # share a token, which is the same bar the comma case uses.
            cands = parts[:1] + parts[-2:]
        for tok in cands:
            if len(tok) > 2:
                out.add(tok.upper())
    # first names are weak evidence but they rescue reversed orderings; keep them out of the
    # match set deliberately — two unrelated Marias must not read as the same household.
    return out


def audit(rows, st):
    """Returns (name-mismatch suspects, comparable count, UNVERIFIABLE list).

    The second list is the one that actually covers the Cooney failure. There, the owner was never
    resolved — the card said '(owner via title search)' — so NO name comparison is possible, and
    that is precisely the condition under which an address-keyed trace wanders: with a unit label
    the county does not recognise, the tracer happily returns whoever does live at that building.
    Those leads are not "clean"; they are UNCHECKABLE, and a card cannot be allowed to present
    unowned contacts with the same confidence as verified ones.
    """
    suspect, checked, unverifiable = [], 0, []
    for r in rows:
        case = r.get('case')
        e = st.get(case)
        if not isinstance(e, dict):
            continue
        traced_name = e.get('name') or ''
        owner = r.get('oname') or r.get('owners') or ''
        contacts = len(r.get('phones') or []) + len(r.get('emails') or [])
        owner_unknown = (not owner) or bool(PLACEHOLDER.search(owner))
        if owner_unknown and contacts:
            unverifiable.append({
                'case': case, 'addr': r.get('addr') or '', 'traced': traced_name,
                'phones': len(r.get('phones') or []), 'emails': len(r.get('emails') or []),
                'unit': bool(re.search(r'(?:#|\bAPT\b|\bUNIT\b|\bSTE\b)\s*[\w-]+|\b\d+\s*$',
                                       (r.get('addr') or '').split(',')[0], re.I)),
            })
        if not traced_name or not owner:
            continue
        own_sn = _people(owner)
        trc_sn = _people(traced_name)
        # entity on either side -> resolving to a human is the POINT, not an error
        if not own_sn or not trc_sn:
            checked += 1
            continue
        checked += 1
        if own_sn & trc_sn:
            continue                                  # a surname in common: same family
        # co-defendants sometimes carry the real surname (spouse with a different last name)
        defs_sn = _people(r.get('defs') or '')
        if defs_sn & trc_sn:
            continue
        suspect.append({
            'case': case,
            'addr': r.get('addr') or '',
            'owner': owner,
            'traced': traced_name,
            'phones': len(r.get('phones') or []),
            'emails': len(r.get('emails') or []),
            'unit': bool(re.search(r'(?:#|\bAPT\b|\bUNIT\b|\bSTE\b)\s*[\w-]+', r.get('addr') or '', re.I)),
        })
    return suspect, checked, unverifiable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true', help='stamp suspect leads in skiptrace_results.json')
    a = ap.parse_args()

    # NO TWIN IS A SKIP, NOT A CRASH. The cloud runner points DEALFLOW_DIR at a throwaway tmp path
    # and never writes a Desktop twin, so raising here would print a traceback into the nightly log
    # every single run and exit non-zero on a step that is purely advisory. Say what is missing and
    # leave with 0 — an absent input must never read as a broken check.
    if not os.path.exists(TWIN):
        print('contact_trust: no board twin at %s — nothing to audit (skipping, not an error)' % TWIN)
        return 0
    stp = os.path.join(HERE, 'skiptrace_results.json')
    if not os.path.exists(stp):
        print('contact_trust: no skiptrace_results.json — nothing to audit (skipping)')
        return 0
    h = open(TWIN, encoding='utf-8').read()
    i = h.find('RAW = ')
    if i < 0:
        print('contact_trust: twin carries no RAW payload — skipping')
        return 0
    rows, _ = json.JSONDecoder().raw_decode(h, i + len('RAW = '))
    st = json.load(open(stp, encoding='utf-8'))

    suspect, checked, unver = audit(rows, st)
    reach = sum(s['phones'] + s['emails'] for s in suspect)
    print('comparable leads (owner + traced name both present): %d' % checked)
    print('A. WRONG PERSON — traced name is a different human: %d  (%.1f%%)'
          % (len(suspect), 100 * len(suspect) / max(checked, 1)))
    print('   contacts riding on them: %d phone/email' % reach)
    for s in sorted(suspect, key=lambda x: -(x['phones'] + x['emails']))[:12]:
        print('     %-38s owner=%-22s traced=%-22s %dp/%de'
              % (s['addr'][:38], s['owner'][:22], s['traced'][:22], s['phones'], s['emails']))
    uu = [u for u in unver if u['unit']]
    print()
    print('B. UNVERIFIABLE — owner never resolved, so no name check is possible: %d' % len(unver))
    print('   of those at a MULTI-UNIT address: %d  <- the Cooney failure mode lives here'
          % len(uu))
    print('   contacts that cannot be attributed: %d phone/email'
          % sum(u['phones'] + u['emails'] for u in unver))
    for u in sorted(uu, key=lambda x: -(x['phones'] + x['emails']))[:8]:
        print('     %-46s %dp/%de  traced=%s'
              % (u['addr'][:46], u['phones'], u['emails'], (u['traced'] or '-')[:22]))

    if a.write:
        n = u_n = 0
        for s in suspect:
            e = st.get(s['case'])
            if isinstance(e, dict):
                e['contact_trust'] = ('SUSPECT: traced %r does not match owner %r — verify before '
                                      'treating these contacts as the owner'
                                      % (s['traced'][:40], s['owner'][:40]))
                n += 1
        for u in unver:
            e = st.get(u['case'])
            if isinstance(e, dict) and not e.get('contact_trust'):
                e['contact_trust'] = ('UNVERIFIED: owner never resolved, so these contacts cannot '
                                      'be attributed to anyone. Confirm the owner before dialling'
                                      + (' — multi-unit address, the trace may be a neighbour.'
                                         if u['unit'] else '.'))
                u_n += 1
        json.dump(st, open(stp, 'w', encoding='utf-8'), indent=1)
        print('\nstamped %d SUSPECT + %d UNVERIFIED lead(s)' % (n, u_n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
