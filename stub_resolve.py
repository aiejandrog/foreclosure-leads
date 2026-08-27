"""stub_resolve.py — case -> PARCEL for the auction stubs that publish NOTHING.

THE POPULATION (measured 2026-08-27): 242 board leads carry 'parcel not linked - verify property
& value via the docket' — RealAuction items with no address, no folio, not even a folio in the
appraiser link's href (swept live 08-17; the display text is just 'Property Appraiser'). They are
mostly association/junior foreclosures listed early. The board can't value them, Carlos can't
drive to them, and every census run counts them as VALUE UNVERIFIED forever.

WHAT THIS MODULE KNOWS THAT THE AUCTION PAGE DOESN'T
The COURT knows the defendant, and the APPRAISER knows what the defendant owns. So:

    defendants ('Last, First M; ...' from the clerk)  ->  county owner-name search
        Miami-Dade   PaServicesProxy GetOwners (keyless; params from the app bundle:
                     Operation=GetOwners&from=1&to=200&ownerName=LAST FIRST)
        Broward      web.bcpa.net getData (the same SPA JSON API pa_values uses — it
                     searches owner names as well as addresses)
        Palm Beach   pbcpao SearchAutoComplete (placeholder: "Owner Name (Last Name first)")
    ->  candidate parcels, then CORROBORATION before anything is believed:
        * the defendant's name tokens must appear in the parcel's owner-of-record, AND
        * the ASSOCIATION in the case (plaintiff or co-defendant: 'CALUSA CLUB VILLAGE
          CONDOMINIUM...') must match the parcel's subdivision/legal ('CALUSA CLUB VILLAGE'),
          OR the defendant owns exactly ONE parcel in that county.
    A person can own two properties and be foreclosed on the OTHER one (the Milouse class) —
    ambiguity stays unresolved, never guessed.

Broward defendants are usually missing from the lead row (the auction hides parties); when
captcha.key exists this module resolves the case style via broward_plaintiff.resolve()
(Broward Clerk, 2Captcha, ~$0.003/case) — capped by --limit so a run can't overspend.

Cache: stub_folios.json (case -> folio + how it was corroborated). Pure public-record linkage,
no owner PII — safe to commit, and the nightly hooks read it:
  * county_leads.to_slim consults it before the pa_values fallback,
  * foreclosure_leads fills blank MD Folio fields from it before enrichment,
so a case resolved ONCE stays resolved through every future scrape.

Run:  python stub_resolve.py --sweep [--limit 25] [--county broward]   # resolve + patch files
      python stub_resolve.py --case 2024-000195-CA-01                  # one case, verbose
"""
import argparse
import datetime
import json
import os
import re
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, 'stub_folios.json')
MISS_TTL_DAYS = 7            # stubs self-heal as sale dates approach — retry weekly
MD_PROXY = 'https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx'
BW_SEARCH = 'https://web.bcpa.net/BcpaClient/search.aspx/getData'
PB_SEARCH = 'https://pbcpao.gov/AutoComplete/SearchAutoComplete'

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/126.0 Safari/537.36')
_S = requests.Session()
_S.headers.update({'User-Agent': _UA, 'Accept': 'application/json, text/plain, */*'})
_last = [0.0]

# party-name furniture that is never a resolvable person
_SKIP_PARTY = re.compile(
    r'UNKNOWN|TENANT|SPOUSE|DEPARTMENT|REVENUE|UNITED STATES|CLERK|CITY OF|COUNTY|STATE OF|'
    r'MORTGAGE|BANK|FINANC|LLC|CORP|INC\b|TRUST\b|ASSN|ASSOC|CONDO|HOMEOWNER|HOA\b|BOARD|'
    r'COMMISSION|HOUSING|CREDIT UNION|CAPITAL|FUND|PARTNERS|HOLDINGS|ET AL', re.I)
_ASSOC = re.compile(r'ASSN|ASSOC|CONDO|HOMEOWNERS|HOA\b|VILLAS?\b|MASTER|COMMUNITY|MAINTENANCE', re.I)
# words that carry no identity inside an association name
_ASSOC_NOISE = {'CONDOMINIUM', 'CONDO', 'ASSOCIATION', 'ASSN', 'ASSOC', 'INC', 'INCORPORATED',
                'HOMEOWNERS', 'HOMEOWNER', 'HOA', 'MASTER', 'COMMUNITY', 'MAINTENANCE', 'THE',
                'OF', 'AT', 'NO', 'NORTH', 'SOUTH', 'EAST', 'WEST', 'PHASE', 'SECTION', 'BLDG',
                'BUILDING', 'VILLAGE', 'VILLAGES', 'PROPERTY', 'OWNERS', 'RECREATION', 'CLUB'}


def _polite(sec=1.0):
    wait = sec - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()


def _load_cache():
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(c):
    tmp = CACHE_PATH + '.tmp'
    json.dump(c, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def folio_for(case):
    """Nightly-pipeline hook: the resolved folio for a case, or ''. Never fetches."""
    e = _load_cache().get(str(case) or '')
    return (e.get('folio') or '') if isinstance(e, dict) and not e.get('miss') else ''


def people_from(defs):
    """'Ferrer, Zayre; Tempo Condominium Association Inc; ...' -> [('FERRER','ZAYRE'), ...]"""
    out = []
    for party in (defs or '').split(';'):
        party = party.strip()
        if not party or _SKIP_PARTY.search(party):
            continue
        if ',' in party:
            last, _, first = party.partition(',')
        else:
            toks = party.split()
            if len(toks) < 2:
                continue
            last, first = toks[-1], ' '.join(toks[:-1])   # 'Lloyd Edwards' -> EDWARDS, LLOYD
        last = re.sub(r'[^A-Z ]', '', last.upper()).strip()
        first = re.sub(r'[^A-Z ]', '', first.upper()).strip().split(' ')[0]
        if len(last) >= 2 and len(first) >= 2:
            out.append((last, first))
    # dedupe, keep order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def assoc_tokens(plaintiff, defs):
    """Distinctive words of any association named in the case — the strongest corroborator,
    because an association forecloses units inside its OWN complex."""
    toks = set()
    for party in ([plaintiff or ''] + (defs or '').split(';')):
        party = party.strip().upper()
        if party and _ASSOC.search(party):
            for w in re.findall(r'[A-Z]{3,}', party):
                if w not in _ASSOC_NOISE:
                    toks.add(w)
    return toks


def _name_ok(last, first, owner_txt):
    o = ' ' + re.sub(r'[^A-Z ]', ' ', (owner_txt or '').upper()) + ' '
    return (' %s ' % last) in o and (' %s ' % first) in o


def _assoc_ok(toks, legal_txt):
    if not toks:
        return False
    L = ' ' + re.sub(r'[^A-Z ]', ' ', (legal_txt or '').upper()) + ' '
    hits = sum(1 for t in toks if (' %s ' % t) in L)
    return hits >= 1 and hits >= min(2, len(toks))    # every distinctive token when there are <=2


# ---------------------------------------------------------------------------------------------
# per-county candidate searches -> [{folio, owner, addr, legal}]
# ---------------------------------------------------------------------------------------------
def _md_candidates(last, first):
    _polite(0.9)
    try:
        r = _S.get(MD_PROXY, params={'Operation': 'GetOwners', 'clientAppName': 'PropertySearch',
                                     'from': 1, 'ownerName': '%s %s' % (last, first), 'to': 200},
                   headers={'Referer': 'https://apps.miamidadepa.gov/PropertySearch/'}, timeout=40)
        infos = r.json().get('MinimumPropertyInfos') or []
    except Exception:
        return []
    out = []
    for i in infos:
        owner = ' '.join(str(i.get(k) or '') for k in ('Owner1', 'Owner2', 'Owner3'))
        out.append({'folio': re.sub(r'\D', '', str(i.get('Strap') or '')),
                    'owner': owner, 'addr': str(i.get('SiteAddress') or '').strip(),
                    'legal': str(i.get('SubdivisionDescription') or '')})
    return out


def _bw_candidates(last, first):
    _polite(1.2)
    try:
        r = _S.post(BW_SEARCH, json={'value': '%s, %s' % (last, first), 'cities': '',
                                     'orderBy': 'NAME', 'pageNumber': '1', 'pageCount': '200',
                                     'arrayOfValues': '', 'selectedFromList': 'false',
                                     'totalCount': 'Y'},
                    headers={'Referer': 'https://web.bcpa.net/BcpaClient/'}, timeout=40)
        items = ((r.json().get('d') or {}).get('resultListk__BackingField')) or []
    except Exception:
        return []
    out = []
    for c in items:
        out.append({'folio': str(c.get('folioNumber') or '').strip(),
                    'owner': '%s %s' % (c.get('ownerName1') or '', c.get('ownerName2') or ''),
                    'addr': str(c.get('siteAddress1') or '').strip(), 'legal': ''})
    return out


def _pb_candidates(last, first):
    _polite(1.2)
    try:
        r = _S.post(PB_SEARCH, data={'propertyType': 'RE', 'searchText': '%s %s' % (last, first)},
                    headers={'Referer': 'https://pbcpao.gov/'}, timeout=40)
        items = r.json() or []
    except Exception:
        return []
    out = []
    for c in items:
        pcn = re.sub(r'^P:', '', str(c.get('pcn') or '').strip())
        if re.sub(r'\D', '', pcn):
            out.append({'folio': pcn, 'owner': str(c.get('text') or ''),
                        'addr': '', 'legal': ''})
    return out


def _md_value(folio):
    """Miami-Dade values from the county's OWN proxy (the cadastral misses some MD folios —
    verified on 0420350641250, which the proxy prices at $192,984 while FDOR returns nothing).
    Shaped like fl_cadastral._norm so every caller downstream reads one shape."""
    _polite(0.9)
    try:
        j = _S.get(MD_PROXY, params={'Operation': 'GetPropertySearchByFolio',
                                     'clientAppName': 'PropertySearch',
                                     'folioNumber': re.sub(r'\D', '', str(folio))},
                   headers={'Referer': 'https://apps.miamidadepa.gov/PropertySearch/'},
                   timeout=40).json()
    except Exception:
        return None
    cur = ((j.get('Assessment') or {}).get('AssessmentInfos') or [{}])[0]
    market = int(cur.get('TotalValue') or 0)
    if not market:
        return None
    hs = any('HOMESTEAD' in str(b.get('Description') or '').upper()
             for b in ((j.get('Benefit') or {}).get('BenefitInfos') or []))
    owner = '; '.join(o.get('Name') for o in (j.get('OwnerInfos') or []) if o.get('Name'))
    site = str((((j.get('SiteAddress') or [{}])[0]) or {}).get('Address') or '').strip()
    ma = j.get('MailingAddress') or {}
    mail = ', '.join(p for p in (str(ma.get('Address1') or '').strip(),
                                 str(ma.get('City') or '').strip(),
                                 (str(ma.get('State') or '').strip() + ' '
                                  + str(ma.get('ZipCode') or '').strip()).strip()) if p)
    return {'parcel_id': re.sub(r'\D', '', str(folio)), 'county_no': 53, 'owner': owner,
            'site_addr': site, 'mail_addr': mail, 'market_value': market,
            'assessed_value': int(cur.get('AssessedValue') or 0), 'land_value': 0,
            'lot_sqft': 0, 'homestead': hs, 'living_sqft': 0, 'buildings': 0,
            'year_built': 0, 'use_code': '', 'last_sale_price': 0, 'last_sale_year': 0,
            'legal': str((j.get('LegalDescription') or {}).get('Description') or '')}


def _value_info(county, folio):
    """Full parcel record for the resolved folio — MD via its own proxy first, everyone via
    the statewide cadastral otherwise."""
    if county == 'MIAMI-DADE':
        info = _md_value(folio)
        if info:
            return info
    import fl_cadastral
    try:
        return fl_cadastral.enrich(parcel_id=folio)
    except Exception:
        return None


def _legal_of(county, folio):
    """Parcel legal/subdivision text for association corroboration."""
    info = _value_info(county, folio)
    return (info or {}).get('legal', ''), info


def resolve_case(county, case, plaintiff, defs, verbose=False):
    """-> {folio, how, owner, addr, info} or None. Corroborated or nothing."""
    persons = people_from(defs)
    toks = assoc_tokens(plaintiff, defs)
    if verbose:
        print('  %s: persons=%s assoc=%s' % (case, persons[:3], sorted(toks)[:6]))
    search = {'MIAMI-DADE': _md_candidates, 'BROWARD': _bw_candidates,
              'PALM BEACH': _pb_candidates}[county]
    for last, first in persons[:3]:
        cands = [c for c in search(last, first) if c['folio'] and _name_ok(last, first, c['owner'])]
        # one parcel per folio
        uniq = {}
        for c in cands:
            uniq.setdefault(c['folio'], c)
        cands = list(uniq.values())
        if not cands:
            continue
        # association corroboration first (checks the parcel's OWN legal when the search
        # result didn't carry one); fall back to the single-parcel-owner rule
        picked, how = None, ''
        if toks:
            hits = []
            for c in cands[:6]:
                legal = c['legal']
                info = None
                if not legal:
                    legal, info = _legal_of(county, c['folio'])
                    c['_info'] = info
                if _assoc_ok(toks, legal):
                    hits.append(c)
            if len(hits) == 1:
                picked, how = hits[0], 'owner+assoc-legal'
        if not picked and len(cands) == 1:
            picked, how = cands[0], 'owner-unique'
        if picked:
            info = picked.get('_info')
            if info is None:
                _, info = _legal_of(county, picked['folio'])
            if verbose:
                print('    -> %s (%s) %s' % (picked['folio'], how, picked['addr'][:40]))
            return {'folio': picked['folio'], 'how': how, 'owner': picked['owner'].strip(),
                    'addr': picked['addr'], 'info': info}
    return None


# ---------------------------------------------------------------------------------------------
# the sweep: resolve every parcel-not-linked lead, patch the files, feed the nightly hooks
# ---------------------------------------------------------------------------------------------
def _fresh_miss(e):
    try:
        age = (datetime.date.today()
               - datetime.date.fromisoformat(e.get('when', '2000-01-01'))).days
    except Exception:
        age = 999
    return age < MISS_TTL_DAYS


def _patch_county(county, path, cache, clerk_budget, verbose):
    import county_leads as CL
    import pa_values
    try:
        rows = json.load(open(path, encoding='utf-8'))
    except Exception:
        return 0
    cfg = CL.COUNTIES[county]
    n = 0
    for r in rows:
        if 'parcel not linked' not in (r.get('warn') or ''):
            continue
        case = r.get('case') or ''
        if not case:
            continue
        e = cache.get(case)
        if isinstance(e, dict) and e.get('miss') and _fresh_miss(e):
            continue
        res = None
        if isinstance(e, dict) and e.get('folio'):
            _, info = _legal_of(county, e['folio'])
            res = {'folio': e['folio'], 'how': e.get('how', 'cache'), 'info': info,
                   'owner': '', 'addr': ''}
        else:
            defs = r.get('defs') or ''
            # Broward hides parties on the auction — buy the case style from the clerk
            # (2Captcha, ~$0.003) inside the run's budget, then resolve off that name.
            if county == 'BROWARD' and not people_from(defs) and clerk_budget[0] > 0:
                try:
                    import broward_plaintiff
                    clerk_budget[0] -= 1
                    cs = broward_plaintiff.resolve(case, verbose=False) or {}
                    if cs.get('defendant'):
                        defs = cs['defendant']
                        r['defs'] = r.get('defs') or defs
                        if cs.get('plaintiff') and not r.get('plaintiff'):
                            r['plaintiff'] = cs['plaintiff']
                except Exception:
                    pass
            res = resolve_case(county, case, r.get('plaintiff') or '', defs, verbose)
            if res:
                cache[case] = {'folio': res['folio'], 'county': county, 'how': res['how'],
                               'when': datetime.date.today().isoformat()}
            else:
                cache[case] = {'miss': True, 'county': county,
                               'when': datetime.date.today().isoformat()}
            _save_cache(cache)
        info = (res or {}).get('info')
        if not res or not info or not info.get('market_value'):
            continue
        # apply exactly as the pa_values backfill does — one derivation, no drift
        r['value'] = info['market_value']
        r['assessed_value'] = info.get('assessed_value') or 0
        r['hs'] = bool(info.get('homestead'))
        r['vsrc'] = 'stub-resolve:' + res['how']
        r['folio'] = res['folio']
        r['pa'] = cfg['pa'](res['folio'])
        r['tax'] = cfg['tax'](res['folio'])
        if not (r.get('addr') or '').strip() and info.get('site_addr'):
            r['addr'] = info['site_addr']
        if not r.get('mail'):
            r['mail'] = info.get('mail_addr', '')
        owner = (info.get('owner') or '').strip()
        if owner and (not r.get('owners') or r.get('owners') == '(owner via title search)'):
            r['owners'] = owner
            r['oname'] = CL._clean_owner(owner)
            r['rname'] = CL._rec_name(owner)
            r['opart'] = CL._owner_partial(owner)
            r['co'] = bool(CL.COMPANY_RE.search(owner))
            r.update(CL._people_links(owner, r.get('addr', ''), r.get('mail', '')))
        try:
            days = (datetime.datetime.strptime(r.get('auction', ''), '%m/%d/%Y').date()
                    - datetime.date.today()).days
        except Exception:
            days = r.get('days', -1)
        r['days'] = days
        r.update(CL._value_metrics(r.get('st', 'FC'), r.get('judg', 0), r['value'], r['hs'],
                                   days, r.get('addr', ''), r.get('plaintiff', ''),
                                   r.get('ftype', '')))
        n += 1
    if n:
        tmp = path + '.tmp'
        json.dump(rows, open(tmp, 'w', encoding='utf-8'), indent=1)
        os.replace(tmp, path)
    left = sum(1 for r in rows if 'parcel not linked' in (r.get('warn') or ''))
    print('%s: resolved %d, %d stub(s) left -> %s' % (county, n, left, os.path.basename(path)))
    return n


def _patch_md(cache, verbose):
    """Miami-Dade rows live in leads_final.json (its own shape). Patch the fields the board
    mapping reads (market_value/warning/Address/...), leave tier/score for the nightly to
    recompute properly off the hooked Folio — an under-scored lead is honest, an over-scored
    one is not."""
    path = os.path.join(HERE, 'leads_final.json')
    try:
        rows = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        print('MIAMI-DADE: leads_final.json unreadable (%s) — skipped' % e)
        return 0
    n = 0
    for r in rows:
        if 'parcel not linked' not in (r.get('warning') or ''):
            continue
        case = r.get('Case #') or ''
        if not case:
            continue
        e = cache.get(case)
        if isinstance(e, dict) and e.get('miss') and _fresh_miss(e):
            continue
        if isinstance(e, dict) and e.get('folio'):
            res = {'folio': e['folio'], 'how': e.get('how', 'cache'), 'addr': ''}
            _, info = _legal_of('MIAMI-DADE', e['folio'])
            res['info'] = info
        else:
            res = resolve_case('MIAMI-DADE', case, r.get('plaintiff') or '',
                               r.get('defendants') or '', verbose)
            cache[case] = ({'folio': res['folio'], 'county': 'MIAMI-DADE', 'how': res['how'],
                            'when': datetime.date.today().isoformat()} if res
                           else {'miss': True, 'county': 'MIAMI-DADE',
                                 'when': datetime.date.today().isoformat()})
            _save_cache(cache)
        info = (res or {}).get('info')
        if not res or not info or not info.get('market_value'):
            continue
        fol = re.sub(r'\D', '', res['folio'])
        r['Folio'] = fol
        r['market_value'] = info['market_value']
        r['homestead'] = bool(info.get('homestead'))
        judg = float(r.get('judgment') or 0)
        r['equity'] = round(info['market_value'] - judg)
        r['equity_pct'] = round((info['market_value'] - judg) / info['market_value'] * 100) if info['market_value'] else 0
        r['warning'] = ''
        r['pa_url'] = 'https://apps.miamidadepa.gov/PropertySearch/#/?folio=' + fol
        if not (r.get('Address') or '').strip():
            addr = res.get('addr') or info.get('site_addr') or ''
            if addr:
                r['Address'] = addr
                r['zillow_url'] = ('https://www.zillow.com/homes/'
                                   + requests.utils.quote(addr.replace(',', ' ')) + '_rb/')
        r['stub_resolved'] = res['how']
        n += 1
    if n:
        tmp = path + '.tmp'
        json.dump(rows, open(tmp, 'w', encoding='utf-8'), indent=1)
        os.replace(tmp, path)
    left = sum(1 for r in rows if 'parcel not linked' in (r.get('warning') or ''))
    print('MIAMI-DADE: resolved %d, %d stub(s) left -> leads_final.json' % (n, left))
    return n


def sweep(county_filter='', clerk_limit=25, verbose=False):
    cache = _load_cache()
    clerk_budget = [clerk_limit if os.path.exists(os.path.join(HERE, 'captcha.key')) else 0]
    total = 0
    if not county_filter or 'MIAMI' in county_filter.upper():
        total += _patch_md(cache, verbose)
    for county, fname in (('BROWARD', 'broward_leads.json'),
                          ('PALM BEACH', 'palmbeach_leads.json')):
        if county_filter and county_filter.upper() not in county:
            continue
        total += _patch_county(county, os.path.join(HERE, fname), cache, clerk_budget, verbose)
    if clerk_limit and not clerk_budget[0]:
        print('clerk budget spent this run — remaining Broward styles resolve on later runs')
    print('sweep: %d lead(s) resolved -> stub_folios.json (%d cached)' % (total, len(cache)))
    if total:
        print('rebuild the board:  python -c "import json, foreclosure_leads as F; '
              'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--county', default='')
    ap.add_argument('--limit', type=int, default=25,
                    help='max Broward clerk lookups (2Captcha spend cap) per run')
    ap.add_argument('--case', default='', help='resolve one case verbosely (no patch)')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    if a.case:
        for f, county, ck, dk, pk in (('leads_final.json', 'MIAMI-DADE', 'Case #', 'defendants', 'plaintiff'),
                                      ('broward_leads.json', 'BROWARD', 'case', 'defs', 'plaintiff'),
                                      ('palmbeach_leads.json', 'PALM BEACH', 'case', 'defs', 'plaintiff')):
            try:
                rows = json.load(open(os.path.join(HERE, f), encoding='utf-8'))
            except Exception:
                continue
            for r in rows:
                if r.get(ck) == a.case:
                    print(json.dumps(resolve_case(county, a.case, r.get(pk) or '',
                                                  r.get(dk) or '', verbose=True), indent=1, default=str)[:1500])
                    return
        print('case not found in any lead file')
        return
    if a.sweep:
        sweep(a.county, clerk_limit=a.limit, verbose=a.verbose)
        return
    ap.print_help()


if __name__ == '__main__':
    main()
