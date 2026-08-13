#!/usr/bin/env python
"""hardmoney_values — put a VALUE, an LTV, and a PROGRAM VERDICT on every balloon loan.

WHY THIS EXISTS
hardmoney_enrich.py answers "who is the human and where is the property?". It does not answer the
only question Jesse's son actually underwrites on: **what is the loan-to-value?** Without it the
balloon book is 733 names nobody can qualify, and every call opens with a guess.

Jesse's program sheet (2026-08-12) is a pure LTV ladder:

    INVESTOR RENTAL / DSCR   LTV 70-80   6-8%    30yr fixed   no income docs   ~2wk close
    HARD EQUITY              LTV 60-70   10-12%  3-5 pts      1-2yr straight   5-10 day close
    FORECLOSURE BAILOUT      LTV 50-60   same terms as hard equity

So LTV is the filter. A loan at 62% LTV is a DSCR client — they are paying 10-12% on a balloon and
he can put them at 6-8% fixed for thirty years. A loan at 105% LTV is not a client at any price.
Sorting the book by loan SIZE (what it did before) ranks by commission, not by who can close.

THE VALUE SOURCE — free, keyless, countywide.
The FL DOR statewide cadastral (fl_cadastral) returns JV (just/market value) by parcel id for all
67 counties. No key, no captcha, no per-county integration. One POST returns 40 parcels, so the
whole book prices in under a minute.

THREE THINGS THAT WOULD MAKE THIS LIE, AND WHAT WE DO ABOUT THEM
 1. VACANT LAND / NO STRUCTURE. A ground-up construction loan is written against the FINISHED
    value (ARV), not the dirt. Measured on the live book: the >100% LTV rows are led by DOR use
    code 000 with year-built 0. Scoring those as "upside down" would be flatly wrong and would put
    a false insult in Alejandro's mouth on the first call. They are labelled ARV and never scored.
 2. THE ROLL LAGS THE MARKET. JV is the annual certified roll, so it trails real market value —
    an 85% LTV against the roll can be ~75% against a real appraisal. We therefore do NOT hard-kill
    the 80-90% band; it is labelled TIGHT ("may still fit on appraisal"), and every verdict below
    is explicitly "vs the county roll", never "vs market".
 3. ONE PARCEL ONLY. hardmoney_enrich already refuses to guess which property a multi-parcel LLC
    mortgaged, so those rows carry no folio and simply stay unpriced. An LTV computed against the
    wrong parcel is worse than no LTV.

Run:  python hardmoney_values.py                 # price the balloon zone (8-30mo), cache, report
      python hardmoney_values.py --all           # every swept loan in the band, not just the zone
      python hardmoney_values.py --refresh       # re-query folios already cached
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SRC = os.path.join(HERE, 'broward_mortgages.json')
ENR = os.path.join(HERE, 'hardmoney_enriched.json')
OUT = os.path.join(HERE, 'hardmoney_values.json')

LAYER = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
         'Florida_Statewide_Cadastral/FeatureServer/0/query')
FLDS = ('PARCEL_ID,OWN_NAME,JV,LND_VAL,TOT_LVG_AR,ACT_YR_BLT,DOR_UC,'
        'SALE_PRC1,SALE_YR1,PHY_ADDR1,PHY_CITY,PHY_ZIPCD')

# FL DOR use codes whose "0" tier means VACANT: 000 residential, 010 commercial, 040 industrial,
# 070 institutional. A loan against one of these is construction/ARV paper by definition.
VACANT_UC = {'000', '0', '00', '010', '10', '040', '40', '070', '70'}

S = requests.Session()
S.headers.update({'User-Agent': 'foreclosure-leads/hardmoney-values',
                  'Referer': 'https://www.arcgis.com/'})


def _load(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _age_mo(iso):
    try:
        return round((datetime.date.today() - datetime.date.fromisoformat(str(iso)[:10])).days / 30.4, 1)
    except Exception:
        return 999.0


def _money(v):
    try:
        return round(float(v)) if v not in (None, '') else 0
    except Exception:
        return 0


def fetch_parcels(folios, chunk=40, tries=3):
    """{folio: attrs} for as many as the cadastral knows. Folios are sent RAW (letters intact) —
    Broward condo ids only match that way; see the fl_cadastral.enrich note."""
    out = {}
    todo = [f for f in folios if f]
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        where = 'PARCEL_ID IN (%s)' % ','.join("'%s'" % f.replace("'", "''") for f in part)
        for t in range(tries):
            try:
                r = S.post(LAYER, data={'where': where, 'outFields': FLDS, 'returnGeometry': 'false',
                                        'resultRecordCount': chunk + 20, 'f': 'json'}, timeout=60)
                j = r.json()
                if j.get('error'):
                    raise RuntimeError(str(j['error'])[:120])
                for feat in j.get('features', []):
                    a = feat.get('attributes') or {}
                    pid = str(a.get('PARCEL_ID') or '').strip()
                    if pid:
                        out[pid] = a
                break
            except Exception as e:
                if t == tries - 1:
                    print('  batch %d-%d failed: %s' % (i, i + len(part), str(e)[:80]))
                time.sleep(2 * (t + 1))       # the free ArcGIS host throttles bursts
        time.sleep(0.25)
    return out


def _norm(a):
    uc = str(a.get('DOR_UC') or '').strip()
    yb = int(a.get('ACT_YR_BLT') or 0)
    sqft = _money(a.get('TOT_LVG_AR'))
    return {
        'folio': str(a.get('PARCEL_ID') or '').strip(),
        'paOwner': (a.get('OWN_NAME') or '').strip(),
        'jv': _money(a.get('JV')),
        'land_val': _money(a.get('LND_VAL')),
        'sqft': sqft,
        'built': yb,
        'uc': uc,
        # No structure = vacant use code, or nothing built and no living area. Either way the loan
        # was underwritten on the finished product, not on what the roll sees today.
        'vacant': bool(uc in VACANT_UC or (yb == 0 and sqft == 0)),
        'last_sale': _money(a.get('SALE_PRC1')),
        'last_sale_yr': int(a.get('SALE_YR1') or 0),
        'addr': ' '.join(str(a.get('PHY_ADDR1') or '').split()),
        'city': (str(a.get('PHY_CITY') or '').strip().title()),
        'zip': str(a.get('PHY_ZIPCD') or '').strip()[:5],
        'pulled': datetime.date.today().isoformat(),
    }


# --- the verdict ------------------------------------------------------------------------------
# Labels map 1:1 onto Jesse's sheet so a row reads the same on the board as it does on the call.
VERDICTS = {
    'DSCR-STRONG': 'DSCR fits with room (30yr fixed 6-8%, no income docs)',
    'DSCR-EDGE':   'DSCR fits at the top of his 70-80 band — no cushion if value comes in low',
    'TIGHT':       'Over 80% of the county roll; the roll lags market, so an appraisal may still fit',
    'OVER':        'Loan exceeds the roll value — needs a paydown or a much higher appraisal',
    'ARV':         'Vacant / no structure — construction paper written against finished value, LTV vs roll is meaningless',
    'NOVALUE':     'No cadastral row for this parcel',
    'NOFOLIO':     'No parcel pinned to this LLC — cannot price',
    'DSCR-ANY':    'LLC owns several parcels; the loan clears 70% of EVERY one, so it qualifies whichever secures it',
    'MULTI':       'LLC owns several parcels — which one secures this loan decides the LTV; read the mortgage',
    'SOLD?':       'No parcel in this LLC\'s name today — likely already sold, or titled differently',
}


def verdict(amt, p):
    """(label, ltv_pct_or_0) for one loan against one parcel record."""
    if not p:
        return 'NOFOLIO', 0.0
    if p.get('vacant'):
        return 'ARV', 0.0
    jv = p.get('jv') or 0
    if not jv:
        return 'NOVALUE', 0.0
    ltv = round(amt / jv * 100, 1)
    if ltv <= 70:
        return 'DSCR-STRONG', ltv
    if ltv <= 80:
        return 'DSCR-EDGE', ltv
    if ltv <= 90:
        return 'TIGHT', ltv
    return 'OVER', ltv


def verdict_multi(amt, parcels):
    """Verdict for an LLC that owns SEVERAL parcels and whose mortgage we cannot pin to one.

    The tempting move — sum every parcel and divide — is wrong and dangerous. If the loan is
    secured by ONE of five houses, dividing by all five understates the LTV and hands Alejandro a
    'you qualify' that evaporates at underwriting. So we bound it instead, and only claim what is
    true under EVERY interpretation:

      * clears 70% of the SMALLEST parcel  -> qualifies no matter which one secures it   (DSCR-ANY)
      * exceeds the COMBINED value of all  -> fails no matter which one secures it       (OVER)
      * anything between                   -> genuinely unknown; the mortgage image decides (MULTI)

    Vacant parcels are excluded from the floor test — construction paper is scored ARV, not LTV.
    """
    live = [p for p in parcels if p and (p.get('jv') or 0) and not p.get('vacant')]
    if not live:
        return ('ARV', 0.0) if any((p or {}).get('vacant') for p in parcels) else ('NOVALUE', 0.0)
    lo = min(p['jv'] for p in live)          # worst case: the least valuable parcel secures it
    combined = sum(p['jv'] for p in live)    # best case: a blanket mortgage over all of them
    if amt / lo * 100 <= 70:
        return 'DSCR-ANY', round(amt / lo * 100, 1)
    if amt / combined * 100 > 90:
        return 'OVER', round(amt / combined * 100, 1)
    return 'MULTI', round(amt / combined * 100, 1)


def loan_rows(zone_only=True, min_amt=75000, max_amt=5000000):
    """The swept loans in scope, each joined to its enriched folio (or '')."""
    rows = _load(SRC, [])
    enr = _load(ENR, {})
    out = []
    for r in rows if isinstance(rows, list) else rows.values():
        amt = int(r.get('amt') or 0)
        if not (min_amt <= amt <= max_amt):
            continue
        age = _age_mo(r.get('origin'))
        if zone_only and not (8 <= age <= 30):
            continue
        b = (r.get('borrower') or '').strip()
        e = enr.get(b) or {}
        bc = e.get('bcpa') or {}
        folio = str(bc.get('folio') or '').strip()
        # A multi-parcel LLC has no single folio but DOES carry the candidate list hardmoney_enrich
        # refused to guess between. Pricing all of them lets verdict_multi bound the answer instead
        # of throwing the lead away — 466 loans in the balloon zone sit in exactly this state.
        cands = [str((c or {}).get('folio') or '').strip()
                 for c in (bc.get('candidates') or []) if (c or {}).get('folio')]
        out.append({'borrower': b, 'lender': (r.get('lender') or '').strip(), 'amt': amt,
                    'origin': str(r.get('origin') or '')[:10], 'age_mo': age, 'folio': folio,
                    'cands': cands, 'enriched': bool(e),
                    'bcpa_err': str(bc.get('err') or '')})
    return out


def all_folios(rows):
    """Every parcel id worth a value — pinned folios plus multi-parcel candidates."""
    s = set()
    for r in rows:
        if r['folio']:
            s.add(r['folio'])
        for c in r.get('cands') or []:
            s.add(c)
    return sorted(s)


def priced(zone_only=True):
    """Every in-scope loan with its cached parcel + verdict folded in. This is what the book and
    the board both consume — one definition of the number, computed in one place."""
    cache = _load(OUT, {})
    out = []
    for r in loan_rows(zone_only=zone_only):
        r = dict(r)
        p = cache.get(r['folio']) if r['folio'] else None
        if p:
            lab, ltv = verdict(r['amt'], p)
            for k in ('jv', 'addr', 'city', 'zip', 'built', 'sqft', 'uc', 'vacant',
                      'last_sale', 'last_sale_yr', 'paOwner'):
                r[k] = p.get(k)
        elif r.get('cands'):
            ps = [cache.get(c) for c in r['cands']]
            ps = [x for x in ps if x and not x.get('miss')]
            lab, ltv = verdict_multi(r['amt'], ps)
            r['parcels'] = len(ps)
            r['jv'] = sum((x.get('jv') or 0) for x in ps)
            r['addr'] = (ps[0].get('addr') if ps else '') or ''
            r['city'] = (ps[0].get('city') if ps else '') or ''
        elif 'no BCPA parcel' in (r.get('bcpa_err') or ''):
            lab, ltv = 'SOLD?', 0.0
        else:
            lab, ltv = 'NOFOLIO', 0.0
        r['ltv'] = ltv
        r['verdict'] = lab
        r['verdict_why'] = VERDICTS.get(lab, '')
        # One flag the book and the board both sort on: can Jesse's son fund this today?
        r['fits'] = lab in ('DSCR-STRONG', 'DSCR-EDGE', 'DSCR-ANY')
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='every banded loan, not just the 8-30mo zone')
    ap.add_argument('--refresh', action='store_true', help='re-query folios already cached')
    ap.add_argument('--limit', type=int, default=0, help='cap folios fetched this run (0 = no cap)')
    a = ap.parse_args()

    rows = loan_rows(zone_only=not a.all)
    cache = {} if a.refresh else _load(OUT, {})
    folios = all_folios(rows)
    todo = [f for f in folios if f not in cache]
    if a.limit:
        todo = todo[:a.limit]

    print('%d loan(s) in scope · %d parcel(s) to price (pinned + multi-parcel candidates) · '
          '%d need a value (%d cached)'
          % (len(rows), len(folios), len(todo), len(folios) - len(todo)))

    if todo:
        got = fetch_parcels(todo)
        for f in todo:
            a_ = got.get(f)
            # Cache MISSES too, as an explicit empty row: a parcel the roll does not carry should
            # not be re-queried every single night forever. --refresh is the way back in.
            cache[f] = _norm(a_) if a_ else {'folio': f, 'jv': 0, 'miss': True,
                                             'pulled': datetime.date.today().isoformat()}
        tmp = OUT + '.tmp'
        json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, OUT)
        hit = sum(1 for f in todo if not (cache[f] or {}).get('miss'))
        print('  fetched %d, %d carried a roll value' % (len(todo), hit))

    # ---- report ------------------------------------------------------------------------------
    res = priced(zone_only=not a.all)
    counts, ltvs = {}, []
    for r in res:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
        if r['ltv']:
            ltvs.append(r['ltv'])
    print('\nVERDICTS across %d loan(s):' % len(res))
    for k in ('DSCR-STRONG', 'DSCR-EDGE', 'DSCR-ANY', 'TIGHT', 'MULTI', 'OVER', 'ARV',
              'SOLD?', 'NOVALUE', 'NOFOLIO'):
        if counts.get(k):
            print('  %-12s %4d   %s' % (k, counts[k], VERDICTS[k]))
    if ltvs:
        ltvs.sort()
        print('  median LTV %.0f%% (vs county roll) across %d scored loans'
              % (ltvs[len(ltvs) // 2], len(ltvs)))

    fits = [r for r in res if r['fits']]
    fits.sort(key=lambda r: ((20 <= r['age_mo'] <= 30), r['amt']), reverse=True)
    if fits:
        vol = sum(r['amt'] for r in fits)
        print('\n*** %d LOANS FIT HIS DSCR BOX — $%.1fM. Refi 11%% -> 7%% saves them $%s/yr. ***'
              % (len(fits), vol / 1e6, format(int(vol * 0.04), ',')))
        print('Most urgent first:')
        for r in fits[:15]:
            print('   %5.1fmo  LTV %5.1f%%  $%9s  %-26s  %s'
                  % (r['age_mo'], r['ltv'], format(r['amt'], ','),
                     r['borrower'][:26], (r.get('addr') or '')[:30]))
    print('\n-> %s' % OUT)
    print('Rebuild the book:  python hardmoney_balloon.py --months 30')


if __name__ == '__main__':
    main()
