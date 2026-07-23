#!/usr/bin/env python3
"""lp_leads.py — turn the LIS PENDENS feed (lis_pendens.py -> lis_pendens.json) into board leads.

The LP feed is the FRONT of the funnel: owners whose foreclosure was just FILED, months before an
auction date exists. LP records carry the real court CASE NUMBER + the legal description but NO folio
(foliO_NUMBER is 0 on a lis pendens), so there's no cheap folio->appraiser address/value at this stage.
What we DO have is the homeowner name (the defendant) and the plaintiff — enough to People-search a
phone and be the first call. Emits `lp_leads.json` in the slim shape make_tracker glob-merges, tagged
st='LP' (days=9999, no auction) so the board's _playFor stamps it LP-EARLY.

Run:  python lp_leads.py           # reads lis_pendens.json -> lp_leads.json
"""
import json
import os
import re

import requests

import foreclosure_leads as F   # people/cyberbg builders
import county_leads as C        # _people_name / _rec_name

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, 'lis_pendens.json')
OUT = os.path.join(HERE, 'lp_leads.json')
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE|PROPERT|INVEST|GROUP|REALTY|CAPITAL|VENTURES|CONDO)\b', re.I)


def build():
    if not os.path.exists(IN):
        print('no lis_pendens.json — run lis_pendens.py first'); return []
    feed = json.load(open(IN, encoding='utf-8'))
    out, seen = [], set()
    for lp in feed:
        case = str(lp.get('case') or '').strip()
        owner = str(lp.get('owner') or '').strip()
        if not owner:
            continue
        key = case or (owner + lp.get('date', ''))
        if key in seen:
            continue
        seen.add(key)
        is_co = bool(COMPANY_RE.search(owner.split(';')[0]))
        ent = bool(re.search(r'\b(TR|TRS|EST|ESTATE|FUND|PROPERT|REALTY|HOMES|GROUP|INVEST|LAND|ASSN|ASSOC|CONDO)\b', owner, re.I))
        nm = C._people_name(owner)
        people = ('https://www.truepeoplesearch.com/results?name=' + requests.utils.quote(nm)) if (nm and not is_co and not ent) else ''
        # case-number deep links (docket + records) — the LP has no auction/folio yet
        docket = ('https://www2.miamidadeclerk.gov/ocs/Search.aspx') if case else ''
        out.append({
            'county': 'MIAMI-DADE', 'st': 'LP', 'stage': 'LP',   # 'LP' -> _isLP()/lpOnly Fresh-filings lane
            'case': case or ('LP-' + re.sub(r'\W', '', owner)[:16]),
            'owners': owner, 'oname': C._rec_name(owner), 'rname': C._rec_name(owner),
            'addr': '', 'mail': '', 'value': 0, 'folio': '',
            'judg': 0, 'eq': 0, 'eqfake': False, 'hs': False, 'condo': 'CONDO' in owner.upper(),
            'vac': False, 'co': is_co, 'plaintiff': lp.get('plaintiff', ''), 'defs': '', 'named': [],
            'mr': False, 'ip': False, 'tier': 'C', 'score': 0, 'auction': '', 'days': 9999,
            'filed': lp.get('date', ''), 'filedDate': lp.get('date', ''),   # the Fresh-filings sort keys on filedDate
            'lpkind': lp.get('kind', ''), 'legal': lp.get('legal', ''),
            'bookpage': lp.get('bookpage', ''),
            'zillow': '', 'pa': 'https://apps.miamidadepa.gov/PropertySearch/#/',
            'people': people, 'peopleaddr': '', 'cyberbg': F.cyberbg_url(nm, '') if (nm and not is_co and not ent) else '',
            'cyberbgaddr': '',
            'records': 'https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch',
            'cases': 'https://www2.miamidadeclerk.gov/ocs/Search.aspx', 'docket': docket,
            'ctype': 'Bank/Mortgage', 'ftype': 'MORTGAGE',
        })
    json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
    print(f'DONE: {len(out)} LP leads -> lp_leads.json (st=LP -> board play LP-EARLY; People-search the owner for a phone)')
    return out


if __name__ == '__main__':
    build()
