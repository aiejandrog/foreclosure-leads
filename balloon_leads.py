#!/usr/bin/env python
"""balloon_leads — the hard-money BALLOON lane as board rows (balloon_leads.json).

WHY (2026-09-08). Jose/Jesse's refi lane lived in one HTML book (hardmoney_balloon.py) that nothing
dialed from and nothing emailed from: 232 candidates in balloon_refi.json, 1,442 enriched LLCs, 250
Sunbiz officer pulls — and zero phones, zero emails, zero tel: links. A lane that exists only as a
report is not a lane. This file turns hardmoney_balloon.candidates() into rows shaped like every
other <county>_leads.json, so the ONE merge in foreclosure_leads.py picks it up unchanged and the
Morning Worker (BALLOON lane), Call Mode (Balloon lane) and the send bridge (wl:'balloon' ->
biscaynesolutionsgroup.com) all work the same list the one-pager shows.

WHAT MAKES A ROW CONTACTABLE — and where that comes from. The public-record joins give a NAME and
an ADDRESS (Sunbiz officer, BCPA site). A phone or an email comes from exactly one place:

    balloon_contacts.json   { "<LLC name>" | "<BAL-case>": {"phones":[{"number":"3055551212",
                              "dnc":false,"type":"Mobile"}], "emails":["j@firm.com"],
                              "name":"First Last", "title":"Manager", "src":"getleads|tracerfy"} }

That file is written by the enrichment run (GetLeads decision-maker lookup on the LLC name, or a
Tracerfy skiptrace on the officer) — a PAID step that is the operator's call, never automatic here.
Rows without a phone still ship to the board (the address and the human are useful) but Call Mode
drops them (no number to dial) and the worker skips them (no mailbox). So the lane's size on the
phone is EXACTLY the size of balloon_contacts.json. The build log says so out loud.

MATURITY IS A PROXY. The recorded index carries the origin date, not the term. 24 months is the
outer wall Jesse uses (1-2 yr balloon); `days` can go negative (past the wall = hottest). Every
surface that shows it says "est." — the card, the chip, the script ("ask the real maturity").

Run:  python balloon_leads.py            # -> balloon_leads.json (+ a one-line count)
      python balloon_leads.py --dry-run  # print what would ship, write nothing
The nightly (foreclosure_leads.py) calls build() right before its county merge.
"""
import argparse
import datetime
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'balloon_leads.json')
CONTACTS = os.path.join(HERE, 'balloon_contacts.json')
TERM_MO = 24          # the proxy wall — see docstring
NO_SALE = 9999        # the board's "no auction date" sentinel (LP rows use the same one)
# THE BOOK, NOT THE UNIVERSE. The 24-month sweep returns ~2,150 mortgages; hardmoney_balloon's own
# one-pager shows the 8-30 month ZONE (past the 1-year balloon, at or approaching the 2-year wall),
# a few hundred rows — Jesse's actual book. Shipping the universe would add ~3 MB of uncontactable
# rows to a 10 MB board. Ship the zone, soonest maturity first, capped; a row WITH a contact always
# ships, uncapped — those rows ARE the lane.
ZONE_LO, ZONE_HI, CAP = 8, 30, 300
LAST_UNIVERSE = 0     # set by rows(): how many the sweep returned before the zone cut (for the log)


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _case_id(h):
    """Stable id from the same tuple hardmoney_balloon dedupes on. BAL- prefix so every consumer
    (notes, ledgers, the sheet) can tell it from a court case at a glance."""
    key = '|'.join(str(h.get(k) or '') for k in ('owner', 'lender', 'origin', 'amt'))
    return 'BAL-' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:8].upper()


def _first_last(name):
    """Sunbiz prints officers 'LAST, FIRST' as often as 'First Last'. Greet in First Last order."""
    s = re.sub(r'\s+', ' ', str(name or '')).strip()
    if ',' in s:
        last, first = s.split(',', 1)
        s = (first.strip() + ' ' + last.strip()).strip()
    return s


def _maturity(origin_iso):
    try:
        o = datetime.date.fromisoformat(origin_iso[:10])
    except Exception:
        return '', None
    m = o.month - 1 + TERM_MO
    mat = datetime.date(o.year + m // 12, m % 12 + 1, min(o.day, 28))
    return mat.isoformat(), (mat - datetime.date.today()).days


def _contacts_for(h, case, contacts):
    return contacts.get(case) or contacts.get(h.get('owner') or '') or {}


def rows(hits=None, contacts=None):
    """hardmoney_balloon hits -> board rows (the <county>_leads.json contract, see county_leads.to_slim)."""
    if hits is None:
        import hardmoney_balloon as HB
        hits = HB.candidates(report=False)
    contacts = contacts if contacts is not None else _load(CONTACTS, {})
    out = []
    for h in hits:
        case = _case_id(h)
        c = _contacts_for(h, case, contacts)
        mat, days = _maturity(str(h.get('origin') or ''))
        # reg. agent is a law firm / CT Corp far more often than a person — a name to ASK for, not to greet
        is_agent = '(reg. agent)' in str(h.get('agent') or '') and not c.get('name')
        human = _first_last(c.get('name') or (h.get('agent') or '').replace(' (reg. agent)', ''))
        phones = [p for p in (c.get('phones') or []) if isinstance(p, dict) and p.get('number')]
        if not phones and h.get('agent_ph'):
            phones = [{'number': h['agent_ph'], 'dnc': False, 'type': ''}]
        emails = [e for e in (c.get('emails') or []) if e and '@' in str(e)]
        fits = bool(h.get('fits'))
        ltv = h.get('ltv') or 0
        score = 80 if fits else (60 if ltv else 45)
        out.append({
            'county': h.get('county') or 'BROWARD', 'tier': 'A' if fits else ('B' if ltv else 'C'), 'score': score,
            'auction': '', 'days': NO_SALE,
            'case': case, 'owners': h.get('owner') or '', 'oname': ('' if is_agent else human),
            'rname': h.get('owner') or '',
            'addr': h.get('addr') or '', 'mail': h.get('agent_addr') or '',
            'value': h.get('jv') or h.get('value') or 0, 'assessed_value': 0, 'judg': 0, 'eq': None, 'eqfake': False,
            'hs': False, 'condo': False, 'vac': False, 'co': True, 'opart': '', 'vsrc': 'hardmoney',
            'st': 'BAL', 'obid': 0, 'folio': h.get('folio') or '', 'zillow': '', 'pa': '', 'tax': '', 'auc': '',
            'people': '', 'peopleaddr': '', 'cyberbg': '', 'cyberbgaddr': '',
            'ctype': 'Hard-money balloon', 'ftype': '', 'plaintiff': '', 'defs': '', 'named': [],
            'docket': '', 'records': '', 'cases': '', 'cstatus': '', 'mr': False, 'ip': False, 'ju': False,
            'bought': 0, 'bprice': 0, 'filed': 0, 'etax': 0, 'warn': '', 'recqs': '', 'ocsqs': '', 'cert': '',
            'phones': [p['number'] for p in phones][:4],
            'phdnc': [bool(p.get('dnc')) for p in phones][:4],
            'phtype': [str(p.get('type') or '') for p in phones][:4],
            'emails': emails[:4],
            'llcppl': ([{'n': human, 't': c.get('title') or h.get('agent_title') or ''}] if human else []),
            'llcra': ((h.get('agent') or '').replace(' (reg. agent)', '') if is_agent else ''),
            'llcraaddr': h.get('agent_addr') or '', 'llcstat': '',
            'bal': {
                'lender': h.get('lender') or '', 'amt': int(h.get('amt') or 0), 'origin': h.get('origin') or '',
                'age_mo': h.get('age_mo'), 'maturity': mat, 'days': days, 'term_mo': TERM_MO,
                'ltv': ltv, 'verdict': h.get('verdict') or '', 'why': h.get('verdict_why') or '',
                'fits': fits, 'jv': h.get('jv') or 0, 'source': h.get('source') or 'book',
                'contact_src': c.get('src') or ('sunbiz' if h.get('agent_ph') else ''),
            },
        })
    # soonest maturity first (negative = past the wall = first); then fundable; then size
    out.sort(key=lambda r: ((r['bal']['days'] if r['bal']['days'] is not None else 10 ** 6),
                            not r['bal']['fits'], -r['bal']['amt']))
    global LAST_UNIVERSE
    LAST_UNIVERSE = len(out)
    zone = [r for r in out if r['bal']['age_mo'] is not None and ZONE_LO <= float(r['bal']['age_mo']) <= ZONE_HI]
    keep = {id(r) for r in out if r['phones'] or r['emails']} | {id(r) for r in zone[:CAP]}
    return [r for r in out if id(r) in keep]


def build(write=True):
    contacts = _load(CONTACTS, {})
    rs = rows(contacts=contacts)
    n_ph = sum(1 for r in rs if r['phones'])
    n_em = sum(1 for r in rs if r['emails'])
    print('balloon lane: %d of %d in the 24-month sweep ship (%d-%d month zone, cap %d) -> %d with a phone, '
          '%d with an email (balloon_contacts.json: %d entr%s)'
          % (len(rs), LAST_UNIVERSE, ZONE_LO, ZONE_HI, CAP, n_ph, n_em, len(contacts),
             'y' if len(contacts) == 1 else 'ies'))
    if not n_ph and not n_em:
        print('  !! balloon lane ships NO contactable row. The lane exists on the board; the phone and '
              'the worker will show it empty until balloon_contacts.json is populated (GetLeads / skiptrace).')
    if write:
        tmp = OUT + '.tmp'
        json.dump(rs, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, OUT)
    return rs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    rs = build(write=not a.dry_run)
    for r in rs[:25]:
        b = r['bal']
        print('  %-12s %6s d | $%9s | %-28s | %-30s | %s' % (
            r['case'], b['days'], format(b['amt'], ','), b['lender'][:28], r['owners'][:30],
            ('PH' if r['phones'] else '  ') + ('EM' if r['emails'] else '  ')))


if __name__ == '__main__':
    main()
