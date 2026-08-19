"""County Property-Appraiser VALUE fallback — the second key when the statewide cadastral misses.

WHY THIS EXISTS
The 2026-08-16 door-ready census pulled 368 leads from Carlos's roster solely on 'VALUE
UNVERIFIED - no cadastral match'. Measured against the live lead files (2026-08-17), that miss
population is NOT what the folio-based enrichment assumes:

    BROWARD     134 warn rows =  86 with NO folio at all (the auction listing never carried one)
                               + 47 with a 10-digit CRIPPLED folio (to_slim strips \\D, and Broward
                                 condo folios carry letters: '494123BM2140' -> '4941232140')
                               +  1 clean 12-digit folio the cadastral genuinely lacks (new plat)
    PALM BEACH   41 warn rows = all with NO folio

So the real job is FOLIO RECOVERY, not value scraping. Each county's own search endpoint is
keyless and resolves an address to the TRUE parcel id:

    Broward     POST web.bcpa.net/BcpaClient/search.aspx/getData   (the BCPA SPA's own JSON API)
                -> folioNumber WITH the condo letters. Verified live: '5860 NW 44 ST 804' ->
                '494123BM2140', whose digits equal the crippled folio in the lead file exactly —
                that digit-match is the strongest possible corroboration for the 47 condo rows.
    Palm Beach  POST pbcpao.gov/AutoComplete/SearchAutoComplete  (propertyType 'RE')
                -> 'P:'-prefixed 17-digit PCN. Verified live on 3/3 warn addresses.

With the true folio in hand, fl_cadastral answers after all (verified: 4/4 recovered folios HIT
with full owner/value/homestead records — the cadastral stores lettered condo ids). Only parcels
the annual roll genuinely lacks (new plats, brand-new condos) fall through to parsing the county
PA page itself:

    Broward     bcpa.net/RecInfo.asp?URL_Folio=<folio>       (plain HTML, keyless, lettered OK)
    Palm Beach  pbcpao.gov/Property/Details?parcelId=<PCN>   (server-rendered, keyless)

RULES THIS MODULE LIVES BY
 * A parse failure must NEVER read as a value (the Bill-of-Rights lesson from ownership_gate):
   a PA page is only trusted when it shows the value table AND echoes the folio's digits.
 * A WRONG parcel is worse than no parcel: address resolutions are corroborated by street
   number, unit token, or crippled-folio digit match — ambiguous stays warned.
 * The warn clears only when a real (>$0) value lands; misses are cached with a TTL so a dead
   address is not re-fetched every night forever, but a new plat gets retried in 14 days.
 * Polite: one live hit per 1.2s, capped per process.

Run:  python pa_values.py --backfill              # patch broward/palmbeach_leads.json in place
      python pa_values.py --backfill --refresh    # retry cached misses too
      python pa_values.py --probe --county broward --folio 494123BM2140
      python pa_values.py --probe --county "palm beach" --addr "13052 COMPTON RD, LOXAHATCHEE"
"""
import argparse
import datetime
import json
import os
import re
import time

import requests

import fl_cadastral

MISS_TTL_DAYS = 14
THROTTLE = 1.2
MAX_LIVE = 300          # per-process live-fetch ceiling; the cache makes reruns cheap

HERE = os.path.dirname(os.path.abspath(__file__))
# Gitignored: carries owner names for people in foreclosure and this repo is PUBLIC.
CACHE_PATH = os.path.join(HERE, 'pa_values_cache.json')

BW_SEARCH = 'https://web.bcpa.net/BcpaClient/search.aspx/getData'
BW_PAGE = 'https://bcpa.net/RecInfo.asp?URL_Folio=%s'
PB_SEARCH = 'https://pbcpao.gov/AutoComplete/SearchAutoComplete'
PB_PAGE = 'https://pbcpao.gov/Property/Details?parcelId=%s'

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/126.0 Safari/537.36')
_S = requests.Session()
_S.headers.update({'User-Agent': _UA})

_cache = None
_last_hit = [0.0]
_live_count = [0]
_max_live = [MAX_LIVE]     # backfill raises this to cover its whole target list in one run
_capped_said = [False]


def _polite():
    wait = THROTTLE - (time.time() - _last_hit[0])
    if wait > 0:
        time.sleep(wait)
    _last_hit[0] = time.time()


def _live_ok():
    if _live_count[0] >= _max_live[0]:
        if not _capped_said[0]:
            print('pa_values: live-fetch cap (%d) reached this run — remaining misses stay '
                  'warned until the next run' % _max_live[0])
            _capped_said[0] = True
        return False
    _live_count[0] += 1
    return True


def _capped():
    return _live_count[0] >= _max_live[0]


def _get(url, referer=None):
    _polite()
    h = {'Referer': referer} if referer else {}
    last = None
    for i in range(2):
        try:
            r = _S.get(url, headers=h, timeout=40)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _flat(html):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'&nbsp;|&amp;', ' ', html)))


def _money(s):
    try:
        return int(str(s).replace(',', '').replace('$', '').strip() or 0)
    except Exception:
        return 0


def _load_cache():
    global _cache
    if _cache is None:
        try:
            _cache = json.load(open(CACHE_PATH, encoding='utf-8'))
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    tmp = CACHE_PATH + '.tmp'
    try:
        json.dump(_cache, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------
# address pieces
# ---------------------------------------------------------------------------------------------
_ST_TYPE = (r'ST|STREET|AVE?|AVENUE|BLVD|DR|DRIVE|RD|ROAD|CT|COURT|TER|TERR?ACE|LN|LANE|WAY|'
            r'PL|PLACE|CIR|CIRCLE|PKWY|HWY|TRL|TRAIL|MNR|BND|ROW|ISLE?|PT|GLN|CV|PATH|WALK|CRES')


def _street_unit(addr):
    """Lead 'addr' ('5860 NW 44 ST APT 804, LAUDERHILL, 33319') -> (street, number, unit).
    The unit is an APT/UNIT/STE token (whole word — 'STERLING' must not read as STE+RLING),
    a '#'-prefixed token ('# 863' — no \\b exists between space and '#'), or a bare token
    trailing the street type ('600 S OCEAN BLVD 507', '14701 CUMBERLAND DR 208A')."""
    street = (addr or '').split(',')[0].upper().strip()
    num = (re.match(r'(\d+)', street) or [None, ''])[1]
    unit = ''
    m = re.search(r'(?:\b(?:APT|UNIT|STE)\b\s*\.?|#)\s*([A-Z0-9-]+)', street)
    if m:
        unit = m.group(1)
        street = (street[:m.start()] + ' ' + street[m.end():]).strip()
    else:
        m = re.search(r'\b(?:%s)\b\s+([A-Z]?\d+[A-Z]?)$' % _ST_TYPE, street)
        if m:
            unit = m.group(1)
            street = street[:m.start(1)].strip()
    return re.sub(r'\s{2,}', ' ', street), num, unit


def _deord(s):
    """'2170 NE 3RD AVE' -> '2170 NE 3 AVE' — BCPA sitenames drop the ordinal suffix."""
    return re.sub(r'\b(\d+)(?:ST|ND|RD|TH)\b', r'\1', s)


def _variants(street, unit):
    """Query variants, most specific first. Covers the observed misses: ordinal suffixes
    ('3RD AVE' vs '3 AVE'), duplex ranges ('7165-7167 PEMBROKE RD' -> '7165'), and spelled-out
    suffix words the county sitename abbreviates ('CIRCLE WEST' -> 'CIR W')."""
    forms = [street]
    rng = re.sub(r'^(\d+)-\d+\b', r'\1', street)
    if rng != street:
        forms.append(rng)
    abbr = street
    for full, short in (('CIRCLE', 'CIR'), ('AVENUE', 'AVE'), ('STREET', 'ST'),
                        ('ROAD', 'RD'), ('DRIVE', 'DR'), ('BOULEVARD', 'BLVD'),
                        ('PLACE', 'PL'), ('TERRACE', 'TER'), ('COURT', 'CT'), ('LANE', 'LN')):
        abbr = re.sub(r'\b%s\b' % full, short, abbr)
    abbr = re.sub(r'\b(NORTH|SOUTH|EAST|WEST)$', lambda m: m.group(1)[0], abbr)
    if abbr not in forms:
        forms.append(abbr)
    # WRONG-SUFFIX CLASS (71 SAUSALITO PL, 2026-08-19): the court caption said "SAUSALITO PL";
    # the county's sitename is "Sausalito Dr". Autocomplete matches literally, so every variant
    # carrying the wrong suffix returned 0 and the lead sat value-less. Add a suffix-STRIPPED form —
    # number + street name only — as the LAST variant: less specific, so the earlier exact forms
    # still win when they can, and the number+unit corroboration downstream keeps a stripped query
    # from grabbing a wrong parcel.
    stripped = re.sub(r'\s+(CIR|AVE|ST|RD|DR|BLVD|PL|TER|CT|LN|WAY|PKWY|TRL|HWY)'
                      r'(\s+[NSEW])?$', '', abbr)
    if stripped not in forms:
        forms.append(stripped)
    out = []
    for f in forms:
        for base in ((f + ' ' + unit).strip(), f) if unit else (f,):
            for q in (base, _deord(base)):
                if q not in out:
                    out.append(q)
    return out


def _num_of(s):
    return (re.match(r'\s*(\d+)', s or '') or [None, ''])[1]


# ---------------------------------------------------------------------------------------------
# folio resolvers — address -> the TRUE county parcel id, corroborated or nothing
# ---------------------------------------------------------------------------------------------
def _bw_resolve(addr, crippled=''):
    street, num, unit = _street_unit(addr)
    if not num:
        return ''
    for q in _variants(street, unit):
        if not _live_ok():
            return ''
        _polite()
        try:
            # pageCount 1500: a big condo tower must return EVERY unit, or the crippled-folio
            # digit match below can miss the right one past a small page ('1950 S OCEAN DR').
            r = _S.post(BW_SEARCH, json={'value': q, 'cities': '', 'orderBy': 'NAME',
                                         'pageNumber': '1', 'pageCount': '1500',
                                         'arrayOfValues': '', 'selectedFromList': 'false',
                                         'totalCount': 'Y'},
                        headers={'Referer': 'https://web.bcpa.net/BcpaClient/'}, timeout=60)
            items = ((r.json().get('d') or {}).get('resultListk__BackingField')) or []
        except Exception:
            continue
        cands = [c for c in items
                 if _num_of(c.get('siteAddress1', '')) == num and c.get('folioNumber')]
        if not cands:
            continue
        if crippled:
            # strongest corroboration: the lettered folio's digits equal the crippled one
            for c in cands:
                if re.sub(r'\D', '', c['folioNumber']) == crippled:
                    return c['folioNumber'].strip()
        if len(cands) == 1:
            return cands[0]['folioNumber'].strip()
        if unit:
            uu = [c for c in cands
                  if re.search(r'(?:#|\b)%s\b' % re.escape(unit), c.get('siteAddress1', '').upper())]
            if len(uu) == 1:
                return uu[0]['folioNumber'].strip()
            if not uu:
                # DIGIT-1 / LETTER-L MANGLE (CACE-25-003554, 2026-08-19). The court caption said
                # unit "107"; BCPA's building had NO 107 — every unit there is letter-prefixed
                # (#L1..#L9, #PH..). The real unit was #L7: somewhere between the pleading and the
                # scrape, "L7" became "107". That single character produced a $0-value lead, a
                # skiptrace of the WRONG HUMAN (unit 201), and a Zillow attach of a THIRD unit.
                # When the literal unit matches nothing, try the 1<->L transforms — and accept a
                # transform ONLY when it matches exactly one unit. Ambiguity stays unresolved:
                # a wrong parcel is worse than no parcel.
                alts = set()
                if re.fullmatch(r'1\d+', unit):
                    alts.add('L' + unit[1:])                       # 107 -> L07
                    if len(unit) > 2 and unit[1] == '0':
                        alts.add('L' + unit[2:])                   # 107 -> L7
                if unit.startswith('L') and unit[1:].isdigit():
                    alts.add('1' + unit[1:])                       # L7 -> 17 (reverse direction)
                    alts.add('10' + unit[1:])                      # L7 -> 107
                # ONE decision across ALL transforms, not first-single-match-wins: iterating a
                # set and returning on the first hit makes the parcel depend on set ORDER when
                # two transforms each match a different unit (a building holding both #L7 and
                # #L07). Union the matches; accept only when they collapse to a single folio —
                # the same "wrong parcel is worse than no parcel" bar, actually enforced.
                found = {}
                for alt in alts:
                    for c in cands:
                        if re.search(r'(?:#|\b)%s\b' % re.escape(alt),
                                     c.get('siteAddress1', '').upper()):
                            found[c['folioNumber'].strip()] = True
                if len(found) == 1:
                    return next(iter(found))
    return ''


def _pb_resolve(addr):
    street, num, unit = _street_unit(addr)
    if not num:
        return ''
    for q in _variants(street, unit):
        if not _live_ok():
            return ''
        _polite()
        try:
            r = _S.post(PB_SEARCH, data={'propertyType': 'RE', 'searchText': q},
                        headers={'Referer': 'https://pbcpao.gov/'}, timeout=40)
            items = r.json() or []
        except Exception:
            continue
        cands = [c for c in items if _num_of(c.get('text', '')) == num and c.get('pcn')]
        pick = None
        if len(cands) == 1:
            pick = cands[0]
        elif unit:
            # PB PCNs pad the unit ('507' renders as '5070') — accept either form at the end
            uu = [c for c in cands
                  if re.search(r'\b%s0?$' % re.escape(unit), c.get('text', '').upper().strip())]
            if len(uu) == 1:
                pick = uu[0]
        if pick:
            pcn = re.sub(r'^P:', '', pick['pcn'].strip())
            if len(re.sub(r'\D', '', pcn)) >= 17:
                return pcn
            # R-prefixed record id = a parcel so new it has no PCN in the autocomplete yet
            # (new plats — one of the census miss classes). Its Details page renders anyway and
            # names the real PCN; corroborate the location address before trusting it.
            if re.match(r'^R\d+$', pcn) and _live_ok():
                try:
                    txt = _flat(_get(PB_PAGE % pcn))
                    loc = re.search(r'Location Address\s+(\d+)\s+([A-Z0-9 ]{3,40}?)\s+Municipality', txt)
                    real = re.search(r'Parcel Control Number\s+([\d-]{17,25})', txt)
                    if loc and real and loc.group(1) == num:
                        rd = re.sub(r'\D', '', real.group(1))
                        if len(rd) >= 17:
                            return rd
                except Exception:
                    pass
    return ''


# ---------------------------------------------------------------------------------------------
# PA page value parsers — last resort, for parcels the annual roll genuinely lacks
# ---------------------------------------------------------------------------------------------
def _shape(folio, src, market=0, assessed=0, owner='', homestead=False):
    """fl_cadastral._norm-shaped dict so to_slim's enrichment block consumes either source."""
    return {'parcel_id': folio, 'county_no': None, 'owner': owner, 'site_addr': '',
            'mail_addr': '', 'market_value': market, 'assessed_value': assessed,
            'land_value': 0, 'lot_sqft': 0, 'homestead': homestead, 'living_sqft': 0,
            'buildings': 0, 'year_built': 0, 'use_code': '', 'last_sale_price': 0,
            'last_sale_year': 0, 'legal': '', 'src': src}


# newest assessment row: '2026* $34,570 $305,230 $339,800 $273,070' (Land, Building, Just,
# Assessed; the current-roll year may carry no Tax column yet). Anchored right after the year so
# the header's '... Tax 2026 2026*' adjacency can't shift the groups.
_BW_ROW = re.compile(r'20\d{2}\*?\s+\$([\d,]+)\s+\$([\d,]+)\s+\$([\d,]+)\s+\$([\d,]+)')


def _bw_page(folio):
    html = _get(BW_PAGE % folio)
    txt = _flat(html)
    # Corroborate before trusting anything: the value table must be present and the page must
    # echo THIS folio's digits (a bad folio returns a generic page — the Bill-of-Rights class).
    i = txt.find('Just / Market Value')
    if i < 0 or re.sub(r'\D', '', folio) not in re.sub(r'\D', '', txt):
        return None
    market = assessed = 0
    for m in _BW_ROW.finditer(txt[i:i + 700]):
        market, assessed = _money(m.group(3)), _money(m.group(4))
        if market:
            break
    if not market:
        return None
    # owner — reuse ownership_gate's Bill-of-Rights-guarded regex rather than invent a new one.
    # Cut at 'Mailing Address' first: this module's whitespace-collapsed text removes the newline
    # that stops the gate's character class, so the label would otherwise bleed into the name.
    owner = ''
    try:
        import ownership_gate as _og
        om = _og._BW_OWNER_RX.search(txt.split('Mailing Address')[0])
        owner = re.sub(r'\s+', ' ', om.group(1)).strip(' ,') if om else ''
        if owner and _og._BW_NOT_OWNER.search(owner):
            owner = ''
    except Exception:
        pass
    # 'Homestead 100% $25,000' (or '0') in the by-Taxing-Authority block; 'Add. Homestead' is a
    # different exemption — the negative lookbehind keeps it from standing in for the real one.
    hs = False
    j = txt.find('by Taxing Authority')
    if j >= 0:
        hm = re.search(r'(?<!Add\. )(?<!Add )Homestead\s+(?:100%\s+|50%\s+)?\$?([\d,]+)',
                       txt[j:j + 900])
        hs = bool(hm and _money(hm.group(1)))
    return _shape(folio, 'bcpa-page', market, assessed, owner, hs)


def _pb_page(folio):
    html = _get(PB_PAGE % folio)
    txt = _flat(html)
    fdig = re.sub(r'\D', '', folio)
    if 'Total Market Value' not in txt or fdig not in re.sub(r'\D', '', txt):
        return None
    m = re.search(r'Total Market Value\s+\$([\d,]+)', txt)
    market = _money(m.group(1)) if m else 0
    if not market:
        return None
    a = re.search(r'Assessed & taxable values.{0,200}?Assessed Value\s+\$([\d,]+)', txt, re.I)
    assessed = _money(a.group(1)) if a else 0
    # owner block: 'Owner(s) Mailing Address Actions <NAMES> <street number...>' (same walk as
    # ownership_gate._fetch_palmbeach, applied to the already-fetched HTML)
    owner = ''
    i = html.upper().find('OWNER INFORMATION')
    if i >= 0:
        names = []
        for ln in re.sub(r'<[^>]+>', '\n', html[i:i + 1200]).replace('&amp;', '&').split('\n'):
            ln = re.sub(r'\s+', ' ', ln).strip()
            if not ln or ln.upper() in ('OWNER(S)', 'MAILING ADDRESS', 'ACTIONS',
                                        'OWNER INFORMATION', '-', '+'):
                continue
            if re.match(r'\d', ln) or 'CHANGE OF MAILING' in ln.upper():
                break
            if re.match(r"[A-Z][A-Z .,'&/-]{2,}$", ln):
                names.append(ln.rstrip(' &'))
            if len(names) >= 4:
                break
        owner = ' & '.join(names)
    # PBCPAO renders no applied-exemption block server-side -> no homestead claim off this page.
    # Nearly every PB lead resolves via the cadastral (real JV_HMSTD); the few that land here are
    # new parcels where a False default costs at most 10 score points, never a wrong value.
    return _shape(folio, 'pbcpao-page', market, assessed, owner, False)


# ---------------------------------------------------------------------------------------------
# the one entry point
# ---------------------------------------------------------------------------------------------
_FULL_LEN = {'BROWARD': 12, 'PALM BEACH': 17}


def lookup(county, folio='', addr=''):
    """Cache-first county fallback. Returns an fl_cadastral-shaped dict (+ 'src', and
    'parcel_id' = the RESOLVED folio, letters intact) or None. Never raises.

    Path: full folio (or address -> county resolver -> true folio) -> fl_cadastral by the raw
    folio -> only if the roll lacks the parcel, parse the county PA page itself."""
    county = (county or '').upper().strip()
    if county not in _FULL_LEN:
        return None
    folio = str(folio or '').strip().upper()
    street, num, _u = _street_unit(addr or '')
    ck = '%s|%s|%s' % (county, folio, street)
    cache = _load_cache()
    e = cache.get(ck)
    if e is not None:
        if not e.get('miss'):
            return dict(e)
        try:
            age = (datetime.date.today()
                   - datetime.date.fromisoformat(e.get('pulled', '2000-01-01'))).days
        except Exception:
            age = 0
        if age < MISS_TTL_DAYS:
            return None
    out = None
    try:
        full = folio if len(folio) >= _FULL_LEN[county] else ''
        if not full and addr:
            full = (_bw_resolve(addr, crippled=folio) if county == 'BROWARD'
                    else _pb_resolve(addr))
        if full:
            info = None
            if _live_ok():
                try:
                    info = fl_cadastral.enrich(parcel_id=full)
                except Exception:
                    info = None
            # corroborate an address-resolved parcel against the roll's own site address —
            # a wrong parcel's value is worse than no value
            if info and num and info.get('site_addr') and _num_of(info['site_addr']) != num:
                info = None
            if info and info.get('market_value'):
                info = dict(info)
                info['parcel_id'] = full
                info['src'] = 'cadastral-refetch'
                out = info
            elif _live_ok():
                out = _bw_page(full) if county == 'BROWARD' else _pb_page(full)
    except Exception:
        out = None
    today = datetime.date.today().isoformat()
    if out and out.get('market_value'):
        rec = dict(out)
        rec['pulled'] = today
        cache[ck] = rec
    else:
        out = None
        # a lookup that lost to the live-fetch cap is NOT evidence the parcel doesn't resolve —
        # caching it as a miss would block the retry for MISS_TTL_DAYS. Only real misses persist.
        if not _capped():
            cache[ck] = {'miss': True, 'pulled': today}
    _save_cache()
    return out


# ---------------------------------------------------------------------------------------------
# backfill — patch the EXISTING lead files so the board census moves without a full re-scrape
# ---------------------------------------------------------------------------------------------
def backfill(counties=('BROWARD', 'PALM BEACH'), limit=0, refresh=False):
    import county_leads as CL
    if refresh:
        cache = _load_cache()
        dead = [k for k, v in cache.items() if isinstance(v, dict) and v.get('miss')]
        for k in dead:
            del cache[k]
        if dead:
            print('refresh: dropped %d cached miss(es)' % len(dead))
    patched_total = 0
    for county in counties:
        cfg = CL.COUNTIES[county]
        path = os.path.join(HERE, county.lower().replace(' ', '') + '_leads.json')
        try:
            rows = json.load(open(path, encoding='utf-8'))
        except Exception as e:
            print('%s: cannot read %s (%s) — skipped' % (county, os.path.basename(path), e))
            continue
        targets = [r for r in rows if isinstance(r, dict)
                   and 'no cadastral match' in (r.get('warn') or '')]
        if limit:
            targets = targets[:limit]
        print('%s: %d lead(s) carry the no-cadastral-match warn' % (county, len(targets)))
        # one backfill run must be able to cover its whole target list (resolver variants +
        # cadastral + PA page can cost several live hits per lead)
        _max_live[0] = max(_max_live[0], _live_count[0] + 8 * len(targets))
        n = 0
        for r in targets:
            info = lookup(county, r.get('folio', ''), r.get('addr', ''))
            if not info or not info.get('market_value'):
                continue
            val = info['market_value']
            assv = info.get('assessed_value') or 0
            hs = bool(info.get('homestead'))
            from_roll = str(info.get('src', '')).startswith('cadastral')
            r['value'], r['assessed_value'], r['hs'] = val, assv, hs
            r['vsrc'] = info.get('src', '')
            rf = str(info.get('parcel_id') or '').strip()
            if rf and len(r.get('folio') or '') < _FULL_LEN[county]:
                r['folio'] = rf
                r['pa'] = cfg['pa'](rf)
                r['tax'] = cfg['tax'](rf)
            if from_roll:
                if not r.get('mail'):
                    r['mail'] = info.get('mail_addr', '')
                if not r.get('bprice'):
                    r['bprice'] = info.get('last_sale_price', 0)
                    r['bought'] = info.get('last_sale_year', 0)
                r['condo'] = (bool(re.search(r'CONDO', info.get('legal', ''), re.I))
                              or str(info.get('use_code', '')) in ('0400', '400', '04'))
                _uc = str(info.get('use_code', '') or '').strip()
                r['vac'] = ((_uc.lstrip('0') == '') and _uc != ''
                            or _uc in ('10', '1000', '40', '4000', '70', '7000'))
            owner = (info.get('owner') or '').strip()
            if owner and (not r.get('owners') or r.get('owners') == '(owner via title search)'):
                r['owners'] = owner
                r['oname'] = CL._clean_owner(owner)
                r['rname'] = CL._rec_name(owner)
                r['opart'] = CL._owner_partial(owner)
                r['co'] = bool(CL.COMPANY_RE.search(owner))
                links = CL._people_links(owner, r.get('addr', ''), r.get('mail', ''))
                r.update(links)
            try:
                days = (datetime.datetime.strptime(r.get('auction', ''), '%m/%d/%Y').date()
                        - datetime.date.today()).days
            except Exception:
                days = r.get('days', -1)
            r['days'] = days
            m = CL._value_metrics(r.get('st', 'FC'), r.get('judg', 0), val, hs, days,
                                  r.get('addr', ''), r.get('plaintiff', ''), r.get('ftype', ''))
            r.update(m)
            n += 1
        if n:
            tmp = path + '.tmp'
            json.dump(rows, open(tmp, 'w', encoding='utf-8'), indent=1)
            os.replace(tmp, path)
        left = sum(1 for r in rows if isinstance(r, dict)
                   and 'no cadastral match' in (r.get('warn') or ''))
        print('%s: patched %d, %d still warned -> %s'
              % (county, n, left, os.path.basename(path)))
        patched_total += n
    if patched_total:
        print('\nRebuild the board so the census moves:')
        print('  python -c "import json, foreclosure_leads as F; '
              'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return patched_total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--county', default='', help='limit backfill/probe to one county')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', action='store_true', help='retry cached misses')
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--folio', default='')
    ap.add_argument('--addr', default='')
    a = ap.parse_args()
    if a.probe:
        print(json.dumps(lookup(a.county or 'BROWARD', a.folio, a.addr), indent=1))
        return
    if a.backfill:
        counties = ([a.county.upper().strip()] if a.county else ['BROWARD', 'PALM BEACH'])
        backfill(counties, limit=a.limit, refresh=a.refresh)
        return
    ap.print_help()


if __name__ == '__main__':
    main()
