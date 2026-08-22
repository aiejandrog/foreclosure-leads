#!/usr/bin/env python3
"""lp_leads.py — turn the LIS PENDENS feed (lis_pendens.py -> lis_pendens.json) into board leads.

The LP feed is the FRONT of the funnel: owners whose foreclosure was just FILED, months before an
auction date exists. LP records carry the real court CASE NUMBER + the legal description but NO folio
(foliO_NUMBER is 0 on a lis pendens), so there's no cheap folio->appraiser address/value at this stage.
What we DO have is the homeowner name (the defendant) and the plaintiff — enough to People-search a
phone and be the first call. Emits `lp_leads.json` in the slim shape make_tracker glob-merges, tagged
st='LP' (days=9999, no auction) so the board's _playFor stamps it LP-EARLY.

ADDRESSES (added 2026-07-30) — lp_resolve.py closes the gap the docstring above describes.
It matches each LP legal description against the Miami-Dade parcel roll and writes lp_addresses.json.
We consume it here, but ONLY the rows it graded `high`. That restraint is the whole point:
  - `high`   = the legal-description key found exactly one parcel AND the owner of record corroborates.
               85 of 125 on the first run. 125/125 rows went through adversarial re-derivation and not
               one `high` row was refuted. These fill addr/folio for real, so skiptrace can run.
  - medium/low = every one of the three refutations landed here. They carry `needsHuman`, so the
               address goes into ADVISORY fields (addrGuess/addrWhy) that render as "verify before
               contact" and never feed outreach. A wrong address sends someone to a stranger's door.
  - ownerMismatch = the parcel is right but title moved after the filing (LLC/trust/REO). The address
               is usable; the NAME is not. Flagged so nobody greets a stranger by the defendant's name.

Run:  python lp_leads.py           # reads lis_pendens.json (+ lp_addresses.json) -> lp_leads.json
"""
import json
import os
import re

import requests

import foreclosure_leads as F   # people/cyberbg builders
import county_leads as C        # _people_name / _rec_name

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, 'lis_pendens.json')
ADDR = os.path.join(HERE, 'lp_addresses.json')
STATUS = os.path.join(HERE, '_lp_status_cache.json')
OUT = os.path.join(HERE, 'lp_leads.json')
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE|PROPERT|INVEST|GROUP|REALTY|CAPITAL|VENTURES|CONDO)\b', re.I)


def _addresses():
    """case -> resolved-address record from lp_resolve.py. Absent file is fine: no addresses, no crash."""
    if not os.path.exists(ADDR):
        return {}
    try:
        return json.load(open(ADDR, encoding='utf-8')) or {}
    except Exception as e:
        print(f'lp_addresses.json unreadable ({e}) — continuing without addresses')
        return {}


def _statuses():
    """case -> clerk case status from lp_status.py. A lis pendens stays in the Official Records
    forever, so nothing in the LP feed says the case DIED — the owner reinstated, refinanced, sold,
    or the bank withdrew. 8 of the current 125 are voluntarily dismissed. Without this they sit in
    the EARLY nurture lane looking fresh, and somebody calls a homeowner about a foreclosure that
    no longer exists."""
    if not os.path.exists(STATUS):
        return {}
    try:
        return json.load(open(STATUS, encoding='utf-8')) or {}
    except Exception:
        return {}


def _full_addr(a):
    """'430 NW 72 AVE, Miami, FL 33126' from the resolver's parts. Skiptrace parses this shape."""
    street = str(a.get('addr') or '').strip()
    if not street:
        return ''
    city = str(a.get('city') or '').strip()
    # the parcel roll says "Unincorporated County" where there is no municipality; that is not a city
    # a geocoder or a skip-trace provider can use, and mailing it would be undeliverable.
    if city.lower().startswith('unincorporated'):
        city = 'Miami'
    zc = str(a.get('zip') or '').strip()[:5]      # roll stores ZIP+4 as "33126-0000"
    return ', '.join(x for x in (street, city, 'FL ' + zc if zc else 'FL') if x)


def build():
    if not os.path.exists(IN):
        print('no lis_pendens.json — run lis_pendens.py first'); return []
    feed = json.load(open(IN, encoding='utf-8'))
    addrs = _addresses()
    stats = _statuses()
    nhigh = nadv = nmismatch = nvalued = ndismissed = nclosed = 0
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

        # ---- resolved address, gated on confidence -------------------------------------------
        a = addrs.get(case) or {}
        conf = str(a.get('confidence') or 'none')
        full = _full_addr(a)
        addr = folio = ''
        addr_guess = addr_why = ''
        if conf == 'high' and full:
            addr, folio = full, str(a.get('folio') or '')
            nhigh += 1
        elif full:
            # Deliberately NOT `addr`. Everything downstream — skiptrace, the door route, the letter
            # merge, the map — reads `addr` and would treat a guess as a fact. These land in advisory
            # fields the row renders as "verify before contact" and no outreach path consumes.
            addr_guess = full
            addr_why = '%s confidence — %s' % (conf, str(a.get('evidence') or '')[:180])
            nadv += 1
        mismatch = bool(a.get('ownerMismatch')) and bool(addr or addr_guess)
        if mismatch:
            nmismatch += 1

        # VALUE (lp_values.py, off the Property Appraiser). Gated on `high` for the same reason the
        # address is: pricing a parcel we are not sure we identified produces a confident number
        # about the wrong house. It is also the difference between the funnel working and not —
        # every outreach stage ranks by net equity, which derives from value, so a value-less lead
        # sorts dead last in the very list it was resolved to join.
        value = int(a.get('value') or 0) if conf == 'high' else 0
        if value:
            nvalued += 1

        # ---- is the case still alive? ------------------------------------------------------
        _s = stats.get(case) or {}
        lp_status = str(_s.get('status') or '')
        lp_dismissed = bool(_s.get('dismissed'))
        # terminal WITHOUT a dismissal docket: the case ended some other way (likely judgment).
        # Either way it is no longer a fresh filing, but the two are different stories and the
        # board says which rather than collapsing them.
        lp_closed = bool(_s.get('terminal')) and not lp_dismissed
        if lp_dismissed:
            ndismissed += 1
        elif lp_closed:
            nclosed += 1

        _cty = (lp.get('county') or 'MIAMI-DADE').upper()
        out.append({
            'county': _cty, 'st': 'LP', 'stage': 'LP',   # 'LP' -> _isLP()/lpOnly Fresh-filings lane
            'case': case or ('LP-' + re.sub(r'\W', '', owner)[:16]),
            'owners': owner, 'oname': C._rec_name(owner), 'rname': C._rec_name(owner),
            'addr': addr, 'mail': '', 'value': value, 'folio': folio,
            # advisory-only; never an outreach input. See the confidence gate above.
            'addrGuess': addr_guess, 'addrWhy': addr_why,
            # the parcel is right but the deed moved after the filing — use the address, not the name
            'paOwner': (a.get('paOwner') or '') if mismatch else '',
            'ownerMismatch': mismatch,
            # judg stays 0: a lis pendens has no final judgment yet, so equity here is the WHOLE
            # value against an unknown debt. eqfake marks that honestly rather than showing 100% equity.
            'judg': 0, 'eq': value, 'eqfake': bool(value),
            'hs': bool(a.get('hs')) if conf == 'high' else False,
            'beds': a.get('beds') or 0, 'baths': a.get('baths') or 0,
            'sqft': a.get('sqft') or 0, 'built': a.get('built') or 0,
            'dor_desc': a.get('dor') or '',
            'condo': ('CONDO' in owner.upper()) or ('CONDO' in str(a.get('dor') or '').upper()),
            'vac': False, 'co': is_co, 'plaintiff': lp.get('plaintiff', ''), 'defs': '', 'named': [],
            'mr': False, 'ip': False, 'tier': 'C', 'score': 0, 'auction': '', 'days': 9999,
            'filed': lp.get('date', ''), 'filedDate': lp.get('date', ''),   # the Fresh-filings sort keys on filedDate
            'lpkind': lp.get('kind', ''), 'legal': lp.get('legal', ''),
            'cstatus': lp_status, 'lpDismissed': lp_dismissed, 'lpClosed': lp_closed,
            'bookpage': lp.get('bookpage', ''),
            'zillow': '',
            # deep links are per-recorder — a Broward owner deep-linked into Miami-Dade's PA
            # search is worse than no link. BCPA/Broward clerk links land with the Phase-2 adapter.
            # DEEP-LINK WHEN WE HAVE THE FOLIO (2026-08-22). This emitted the county's generic
            # search page even on rows that carry a resolved folio, and emitted NO 'tax' key at
            # all — so 483 leads with a perfectly good parcel id fell through to the board's
            # search-engine fallback ("the tax link only takes me to a Google search"). Both URLs
            # are pure functions of folio + county; derive them here rather than throw the folio
            # away. The generic search page stays as the honest fallback when folio is empty.
            'pa': (F._pa_url_from_folio({'folio': folio, 'county': _cty})
                   or ('https://apps.miamidadepa.gov/PropertySearch/#/' if _cty == 'MIAMI-DADE'
                       else 'https://web.bcpa.net/BcpaClient/#/Record-Search' if _cty == 'BROWARD' else '')),
            # 'tax' — the SLIM board key. lp_leads emits already-slim rows (note 'pa'/'people'/
            # 'cases' beside it), so the fat-shape name 'tax_url' would be silently ignored.
            'tax': F._tax_url_from_folio({'folio': folio, 'county': _cty}),
            'people': people, 'peopleaddr': '', 'cyberbg': F.cyberbg_url(nm, '') if (nm and not is_co and not ent) else '',
            'cyberbgaddr': '',
            'records': ('https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch'
                        if _cty == 'MIAMI-DADE'
                        else 'https://officialrecords.broward.org/AcclaimWeb/' if _cty == 'BROWARD' else ''),
            'cases': ('https://www2.miamidadeclerk.gov/ocs/Search.aspx' if _cty == 'MIAMI-DADE'
                      else 'https://www.browardclerk.org/Web2' if _cty == 'BROWARD' else ''),
            'docket': (docket if _cty == 'MIAMI-DADE' else ''),
            'ctype': 'Bank/Mortgage', 'ftype': 'MORTGAGE',
        })
    json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
    print(f'DONE: {len(out)} LP leads -> lp_leads.json (st=LP -> board play LP-EARLY)')
    if addrs:
        print(f'  addresses: {nhigh} filled (high confidence, skiptrace-ready) · '
              f'{nadv} advisory only (needs human) · {nmismatch} owner-of-record changed since filing')
        print(f'  case:      {ndismissed} DISMISSED, {nclosed} closed without a dismissal docket'
              + (' — these drop out of the EARLY lane' if (ndismissed or nclosed) else ''))
        print(f'  values:    {nvalued} priced from the Property Appraiser'
              + ('' if nvalued else ' — run lp_values.py to rank these by equity'))
    else:
        print('  no lp_addresses.json — run lp_resolve.py to fill addresses (leads stay un-skiptraceable)')
    return out


if __name__ == '__main__':
    build()
