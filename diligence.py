#!/usr/bin/env python3
"""diligence.py — Deep Diligence v1 for Dealflow (Capri-H brief quality when data exists).

Compose existing county enrichers (do not rewrite lien chains). Writes:
  diligence/{safe_case}.json
  diligence/{safe_case}.md
  diligence_cache.json   (keyed by case — make_tracker bakes r.diligence)

  python diligence.py --case CASE [--headed]

--headed: Palm Beach only — after 2Captcha fails, open a visible browser for one checkbox click.
Live dig always tries 2Captcha first when ShowCaptcha=True (see palmbeach_liens.py).

API / Call Sheet: diligence_server.py exposes run(case) on 127.0.0.1:8765.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'diligence')
CACHE = os.path.join(HERE, 'diligence_cache.json')

# Live OR scrapes can include 2Captcha polls — allow several minutes.
_LIVE_TIMEOUT = 480

# Reuse plaintiff classifiers already battle-tested on the board.
try:
    from broward_liens import _fc_type_plaintiff
except Exception:  # pragma: no cover
    def _fc_type_plaintiff(plaintiff):
        p = (plaintiff or '').upper()
        if re.search(r'\bBANK\b|\bMORTGAGE\b|\bN\.?\s?A\.?\b|NATIONAL\s+ASS', p):
            return 'MORTGAGE'
        if re.search(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|(?<!NATIONAL\s)\bASS(?:N|OC)', p):
            return 'HOA'
        return ''

try:
    from foreclosure_leads import _fc_type as _case_prefix_ftype, _senior_surviving
except Exception:  # pragma: no cover
    def _case_prefix_ftype(case):
        c = (case or '').upper()
        if '-CC-' in c or 'CC' in c[4:8]:
            return 'HOA'
        if '-CA-' in c or c.startswith('CACE'):
            return 'MORTGAGE'
        return ''

    def _senior_surviving(h):
        surv = float(h.get('surv') or 0)
        if (h.get('source') or '').lower() == 'batchdata':
            return int(round(surv))
        return int(round(max(0.0, surv - float(h.get('juniors_post') or 0))))


_LADY_BIRD_RE = re.compile(
    r'LADY\s*BIRD|ENHANCED\s+LIFE\s+ESTATE|LIFE\s+ESTATE|REMAINDER(?:MAN)?|'
    r'LIFE\s+TENANT|RESERV(?:ED|ING)\s+(?:A\s+)?LIFE',
    re.I,
)
_HOA_PL_RE = re.compile(
    r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|'
    r'PROPERTY\s+OWNERS?|TOWNHO|MAINTENANCE|(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b',
    re.I,
)
_TAX_DEED_RE = re.compile(r'TAX\s*DEED|TAX\s*COLLECTOR|CERTIFICATE', re.I)


# ---------------------------------------------------------------------------
# Verified Capri-H seed (do not re-scrape — captcha + known DD 2026-07-27)
# ---------------------------------------------------------------------------
CAPRI_CASE = '502025CC016197XXXAMB'
CAPRI_SEED = {
    'case': CAPRI_CASE,
    'county': 'PALM BEACH',
    'addr': '343 CAPRI H, DELRAY BEACH, FL 33484',
    'folio': '00424623060083430',
    'verdict': 'CONDITIONAL',
    'foreclosure_type': 'HOA',
    'plaintiff': 'CAPRI H ASSOCIATION INC',
    'judgment': 15320.86,
    'sale': '08/17/2026',
    'mortgages': [],
    'liens': [
        {'label': 'Claim of lien (Capri H Association Inc)', 'date': '07/10/2025',
         'bp': 'O 35867/1172', 'amt': None},
        {'label': 'Lis Pendens', 'date': '10/09/2025', 'bp': 'O 36054/105', 'amt': None},
        {'label': 'Second Capri lien (w/ Krasker)', 'date': '03/31/2026',
         'bp': 'O 36414/993', 'amt': None},
        {'label': 'Final Judgment (recorded)', 'date': '05/26/2026',
         'bp': 'O 36541/1833', 'amt': 15320.86},
        {'label': 'Palm Beach County tax / cert lien (OR)', 'date': '01/12/2026',
         'bp': 'O 36246/1286', 'amt': None},
    ],
    'taxes': {'status': 'delinquent', 'certs': 3724, 'due': 3724, 'url': ''},
    'title': {
        'deed_type': 'Lady Bird / enhanced life estate',
        'lady_bird': True,
        'life_estate_holder': 'Mattie G. St. Juste',
        'remainder': 'Shurod L. White',
        'bp': 'O 34698/1315',
    },
    'killer_issues': [
        'Lady Bird deed O 34698/1315: Mattie G. St. Juste keeps the life estate; '
        'White only gets fee on her death. Case names White only — if Mattie is alive '
        'and was not joined, auction may buy a remainder interest (usable equity ~$0).',
        'Tax certs ~$3,724 survive the HOA sale — redeem path must be locked before wire.',
    ],
    'money_math': {
        'value': 66407,
        'judg': 15320.86,
        'surviving_senior': 0,
        'tax_certs': 3724,
        'list_price': 59900,
        'net_equity_est': 40855,
        'notes': (
            'list ~$59.9k − $15.3k FJ − $3.7k taxes ≈ ~$41k before rehab / $616/mo HOA / '
            '55+ Kings Point — only if you get fee simple. If Mattie is alive and wasn’t '
            'joined, auction may buy a remainder interest → usable equity can go to ~$0.'
        ),
    },
    'timeline': [
        {'label': 'Claim of lien', 'date': '07/10/2025', 'bp': 'O 35867/1172'},
        {'label': 'Lis Pendens', 'date': '10/09/2025', 'bp': 'O 36054/105'},
        {'label': 'County tax/cert lien', 'date': '01/12/2026', 'bp': 'O 36246/1286'},
        {'label': 'Second Capri lien', 'date': '03/31/2026', 'bp': 'O 36414/993'},
        {'label': 'Final Judgment recorded', 'date': '05/26/2026', 'bp': 'O 36541/1833'},
        {'label': 'Auction sale', 'date': '08/17/2026', 'bp': ''},
    ],
    'outreach_notes': (
        'Do not wire / do not treat as clear title until Mattie is confirmed deceased '
        '(or joined/bought out) + tax redeem path is locked. Cash outreach to White is '
        'fine as a pre-auction options talk; auction bidding is a trap until the Lady Bird '
        'is cleared.'
    ),
    'citations': [
        {'label': 'Lady Bird deed', 'url_or_bp': 'O 34698/1315'},
        {'label': 'Claim of lien', 'url_or_bp': 'O 35867/1172'},
        {'label': 'Lis Pendens', 'url_or_bp': 'O 36054/105'},
        {'label': 'Second Capri lien', 'url_or_bp': 'O 36414/993'},
        {'label': 'FJ recorded', 'url_or_bp': 'O 36541/1833'},
        {'label': 'PAO parcel',
         'url_or_bp': 'https://pbcpao.gov/Property/Details?parcelId=00424623060083430'},
        {'label': 'Auction preview',
         'url_or_bp': 'https://palmbeach.realforeclose.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=08/17/2026#AITEM_1503351'},
    ],
    'sources': {
        'lead': 'palmbeach_leads.json',
        'or': 'verified Capri DD 2026-07-27 (seed — palmbeach_liens captcha-blocked)',
        'batchdata': 'batchdata_liens.json (0 open mortgages)',
        'note': 'surviving file + OR parcel/name pulls',
    },
    'traced': '2026-07-27',
}


def safe_case(case: str) -> str:
    return re.sub(r'[^\w.\-]+', '_', (case or '').strip()) or 'unknown'


def _load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def _lead_case_key(r):
    return (r.get('case') or r.get('Case #') or '').strip()


def find_lead(case: str):
    """Return (lead_dict, county_tag, source_file) or (None, '', '')."""
    case = (case or '').strip()
    # Palm Beach / Broward slim shape
    for fname, tag in (('palmbeach_leads.json', 'PALM BEACH'),
                       ('broward_leads.json', 'BROWARD')):
        path = os.path.join(HERE, fname)
        rows = _load_json(path, [])
        if not isinstance(rows, list):
            continue
        for r in rows:
            if _lead_case_key(r) == case:
                return r, tag, fname
    # Miami-Dade fat shape
    path = os.path.join(HERE, 'leads_final.json')
    rows = _load_json(path, [])
    if isinstance(rows, list):
        for r in rows:
            if _lead_case_key(r) == case:
                return r, 'MIAMI-DADE', 'leads_final.json'
    return None, '', ''


def _norm_lead(r, county):
    """Normalize MD fat keys + county slim keys into one shape."""
    return {
        'case': _lead_case_key(r),
        'county': (r.get('county') or county or '').upper(),
        'addr': r.get('addr') or r.get('Address') or '',
        'folio': r.get('folio') or r.get('Folio') or '',
        'owners': r.get('owners') or '',
        'plaintiff': r.get('plaintiff') or '',
        'judgment': float(r.get('judg') or r.get('judgment') or 0) or 0,
        'sale': r.get('auction') or r.get('AuctionDate') or '',
        'value': float(r.get('value') or r.get('market_value') or 0) or 0,
        'ctype': r.get('ctype') or r.get('case_type') or '',
        'ftype': r.get('ftype') or '',
        'st': r.get('st') or r.get('sale_type') or 'FC',
        'tax_url': r.get('tax') or r.get('tax_url') or '',
        # The ANNUAL tax off the roll. Carried under all three names the pipeline uses (`etax` on the
        # baked board, `est_annual_tax` on the Miami-Dade fat record). Without this the brief had no
        # tax number of ANY kind to show, so "taxes" read as blank on every card — and the money math
        # then quietly treated the unknown as zero.
        'est_annual_tax': float(r.get('etax') or r.get('est_annual_tax') or 0) or 0,
        'etaxest': bool(r.get('etaxest')),      # True = modelled off the roll, not a billed figure
        'hs': bool(r.get('hs') or r.get('homestead')),
        'pa': r.get('pa') or r.get('pa_url') or '',
        'auc': r.get('auc') or r.get('auction_url') or '',
        'docket': r.get('docket') or r.get('docket_url') or '',
        'records': r.get('records') or '',
        'zprice': float(r.get('zprice') or 0) or 0,
        'eq': float(r.get('eq') or r.get('equity_pct') or 0) or 0,
        'saleSurv': r.get('saleSurv') if 'saleSurv' in r else r.get('sale_survived'),
        'saleSched': r.get('saleSched') if 'saleSched' in r else r.get('sale_scheduled'),
        'saleBK': r.get('saleBK') if 'saleBK' in r else r.get('sale_bk'),
        'saleBkAct': bool(r.get('saleBkAct') if 'saleBkAct' in r else r.get('sale_bk_active')),
    }


def load_or_chain(county: str, case: str, headed: bool = False, force_live: bool = False):
    """Return (chain_dict_or_None, source_label, or_missing:bool).

    Default is cache-only so Call Sheet opens with lead-backed numbers in <1s.
    Live county enrichers run only when force_live=True (CLI --live / Refresh Diligence).
    Never invent liens if the scrape fails.
    """
    county = (county or '').upper()
    case = (case or '').strip()

    if county == 'MIAMI-DADE':
        path = os.path.join(HERE, 'records_liens.json')
        rl = _load_json(path, {})
        h = rl.get(case)
        if force_live:
            _invoke_live('MIAMI-DADE', case, headed=False)
            rl = _load_json(path, {})
            h = rl.get(case)
            if h and (h.get('liens') is not None or h.get('conf')):
                return h, 'records_liens.json (live 2Captcha/Turnstile)', False
        if h and (h.get('liens') is not None or h.get('conf')):
            return h, 'records_liens.json', False
        return None, 'records_liens.json (missing)', True

    if county == 'BROWARD':
        path = os.path.join(HERE, 'broward_liens.json')
        bl = _load_json(path, {})
        h = bl.get(case)
        if force_live:
            _invoke_live('BROWARD', case, headed=False)
            bl = _load_json(path, {})
            h = bl.get(case)
            if h and (h.get('liens') is not None or h.get('conf')):
                return h, 'broward_liens.json (live AcclaimWeb)', False
        if h and (h.get('liens') is not None or h.get('conf')):
            return h, 'broward_liens.json', False
        return None, 'broward_liens.json (missing)', True

    if county == 'PALM BEACH':
        path = os.path.join(HERE, 'palmbeach_liens.json')
        pl = _load_json(path, {})
        h = pl.get(case)
        cached_ok = h and (h.get('liens') is not None or h.get('conf') not in (None, 'none', ''))
        if force_live:
            # Default: 2Captcha. --headed only as fallback inside palmbeach_liens.
            _invoke_live('PALM BEACH', case, headed=headed)
            pl = _load_json(path, {})
            h = pl.get(case)
            if h and (h.get('liens') is not None or h.get('conf') not in (None, 'none', '')):
                cap = h.get('captcha') or '2captcha'
                return h, f'palmbeach_liens.json (live {cap})', False
            cached_ok = h and (h.get('liens') is not None or h.get('conf') not in (None, 'none', ''))
        if cached_ok:
            return h, 'palmbeach_liens.json', False
        bd = _load_json(os.path.join(HERE, 'batchdata_liens.json'), {})
        h = bd.get(case)
        if h is not None:
            # BatchData is a property API, not Official Records — treat OR as missing for
            # Lady Bird / deed text, but still use mortgage open-balance signal.
            return h, 'batchdata_liens.json (OR missing)', True
        return None, 'palmbeach_liens + batchdata missing', True

    return None, f'unknown county {county}', True


def apply_lead_backed_numbers(d: dict, lead) -> dict:
    """Guarantee Capri-style money table is never blank when the lead already has numbers.

    Always overlays judgment / sale / money_math.value|judg|net_equity_est from the lead
    (lead wins when dig left them empty/zero). Plaintiff / foreclosure_type filled when blank.
    """
    if not isinstance(d, dict):
        return d
    if isinstance(lead, dict) and 'judgment' not in lead and ('judg' in lead or 'Case #' in lead or 'auction' in lead):
        lead = _norm_lead(lead, d.get('county') or lead.get('county') or '')
    lead = lead or {}

    judg = float(lead.get('judgment') or 0) or 0
    sale = (lead.get('sale') or '') or ''
    value = float(lead.get('value') or 0) or 0
    plaintiff = (lead.get('plaintiff') or '').strip()
    ftype_lead = (lead.get('ftype') or '').strip().upper()

    if judg and not (d.get('judgment') or 0):
        d['judgment'] = judg
    elif judg and float(d.get('judgment') or 0) == 0:
        d['judgment'] = judg
    # Prefer lead judgment when dig somehow lost it (never blank a known FJ)
    if judg and not d.get('judgment'):
        d['judgment'] = judg

    if sale and not (d.get('sale') or '').strip():
        d['sale'] = sale

    if plaintiff and not (d.get('plaintiff') or '').strip():
        d['plaintiff'] = plaintiff

    if ftype_lead and ftype_lead != 'UNKNOWN':
        if not (d.get('foreclosure_type') or '').strip() or d.get('foreclosure_type') == 'UNKNOWN':
            d['foreclosure_type'] = ftype_lead

    mm = dict(d.get('money_math') or {})
    if value and not (mm.get('value') or 0):
        mm['value'] = int(round(value))
    dig_judg = float(d.get('judgment') or mm.get('judg') or 0) or 0
    use_judg = dig_judg or judg
    if use_judg and not (mm.get('judg') or 0):
        mm['judg'] = use_judg
    # Recompute net equity whenever value + judg known and net is missing
    mm_value = float(mm.get('value') or 0) or 0
    mm_judg = float(mm.get('judg') or 0) or 0
    if mm.get('net_equity_est') is None and mm_value:
        senior = float(mm.get('surviving_senior') or 0) or 0
        # AN UNPULLED TAX IS NOT A ZERO TAX. `tax_certs or 0` subtracted nothing and thereby asserted
        # "no delinquent taxes" on 29 of 30 cached briefs. In Florida a delinquent bill becomes a tax
        # certificate that SURVIVES the sale (FS 197) and is senior to everything — so a silent zero
        # here overstates the net by the one lien that cannot be negotiated away.
        _tc = mm.get('tax_certs')
        tax_known = _tc is not None and str(_tc) != ''
        tax = float(_tc or 0) if tax_known else 0.0
        mm['net_equity_est'] = int(round(mm_value - mm_judg - senior - tax))
        mm['tax_checked'] = bool(tax_known)
        # what we DO know even when certificates were never pulled: the annual bill off the tax roll.
        _ann = float(lead.get('est_annual_tax') or 0) or 0
        if _ann:
            mm['tax_annual'] = int(round(_ann))
        if not tax_known:
            mm['net_provisional'] = True
            mm['notes'] = ('PROVISIONAL — delinquent taxes NOT pulled, so nothing is subtracted for them. '
                           + (f'Annual bill is ~${int(round(_ann)):,}; ' if _ann else '')
                           + 'unpaid years become certificates that survive the sale and outrank every '
                           + 'other lien. Pull the tax bill before you treat this net as real.')
        elif not mm.get('notes'):
            mm['notes'] = 'Net equity from lead value − FJ − senior − tax certs. OR thin — confirm before wire.'
    d['money_math'] = mm

    # Tax URL from lead when taxes block lacks it
    tax_url = lead.get('tax_url') or ''
    if tax_url:
        taxes = dict(d.get('taxes') or {})
        if not taxes.get('url'):
            taxes['url'] = tax_url
            taxes.setdefault('status', taxes.get('status') or 'unknown')
            # certs stays None when nobody pulled the bill. Defaulting it to 0 made "never checked"
            # indistinguishable from "checked, none owed" — and the money math then trusted the 0.
            if 'certs' not in taxes:
                taxes['certs'] = None
            taxes['checked'] = taxes.get('status') not in (None, '', 'unknown')
            _ann = float(lead.get('est_annual_tax') or 0) or 0
            if _ann and not taxes.get('annual'):
                taxes['annual'] = int(round(_ann))
            d['taxes'] = taxes

    # Auction on timeline if sale known and timeline empty of auction row
    if sale:
        tl = list(d.get('timeline') or [])
        if not any('auction' in (t.get('label') or '').lower() or 'sale' in (t.get('label') or '').lower()
                   for t in tl):
            tl.insert(0, {'label': 'Auction sale', 'date': sale, 'bp': ''})
            d['timeline'] = tl

    src = dict(d.get('sources') or {})
    src['lead_backed'] = True
    d['sources'] = src
    return d


def _invoke_live(county: str, case: str, headed: bool = False):
    """Best-effort live OR scrape for one case. Logs progress; never raises."""
    county = (county or '').upper()
    scripts = {
        'MIAMI-DADE': ['records_liens.py', '--case', case],
        'BROWARD': ['broward_liens.py', '--case', case, '--refresh'],
        'PALM BEACH': ['palmbeach_liens.py', '--case', case, '--refresh'],
    }
    args = scripts.get(county)
    if not args:
        return
    if county == 'PALM BEACH' and headed:
        args = args + ['--headed']
    cmd = [sys.executable, os.path.join(HERE, args[0])] + args[1:]
    print(f'  live scrape: {" ".join(args)} (timeout {_LIVE_TIMEOUT}s)…')
    try:
        subprocess.run(cmd, cwd=HERE, check=False, timeout=_LIVE_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f'  live scrape timed out after {_LIVE_TIMEOUT}s')
    except Exception as e:
        print(f'  live scrape failed: {e}')


def _merge_capri_seed(live: dict, seed: dict) -> dict:
    """After a live Capri dig: keep live OR/money where present; fill Lady Bird + known
    lien labels from verified seed only when live cannot (no deed text / empty liens).

    Document every seed fill in sources['seed_merge'].
    """
    d = json.loads(json.dumps(live))  # deep copy
    fills = []
    src = d.setdefault('sources', {})
    live_title = d.get('title') or {}
    seed_title = seed.get('title') or {}

    # Lady Bird / life-estate parties — live scanner rarely has full deed OCR
    need_lb = live_title.get('lady_bird') is not True
    if need_lb and seed_title.get('lady_bird'):
        d['title'] = {
            'deed_type': seed_title.get('deed_type') or live_title.get('deed_type') or '',
            'lady_bird': True,
            'life_estate_holder': seed_title.get('life_estate_holder') or '',
            'remainder': seed_title.get('remainder') or '',
            'bp': seed_title.get('bp') or live_title.get('bp') or '',
        }
        fills.append('title Lady Bird (Mattie / White / O 34698/1315) — live lacked deed text')
        killers = list(d.get('killer_issues') or [])
        seed_killers = [k for k in (seed.get('killer_issues') or []) if 'lady bird' in (k or '').lower()
                        or 'life estate' in (k or '').lower() or 'mattie' in (k or '').lower()]
        for k in seed_killers:
            if k not in killers:
                killers.insert(0, k)
        # Drop generic VERIFY lady-bird unknown now that we know
        killers = [k for k in killers if not re.search(r'VERIFY:.*Lady Bird', k or '', re.I)]
        d['killer_issues'] = killers
        d['verdict'] = 'CONDITIONAL'
        src['verdict_reason'] = 'Lady Bird / life estate on title (seed + live)'

    live_taxes = d.get('taxes') or {}
    seed_taxes = seed.get('taxes') or {}
    if (live_taxes.get('status') in (None, '', 'unknown') or not live_taxes.get('certs')) and seed_taxes.get('certs'):
        d['taxes'] = dict(seed_taxes)
        if live_taxes.get('url'):
            d['taxes']['url'] = live_taxes['url']
        fills.append('tax certs from verified Capri seed')

    if not (d.get('liens') or []) and (seed.get('liens') or []):
        d['liens'] = seed['liens']
        fills.append('lien labels from verified Capri OR pull (live empty)')

    if not (d.get('timeline') or []) and (seed.get('timeline') or []):
        d['timeline'] = seed['timeline']
        fills.append('timeline from verified Capri seed')

    # Seed citations for Lady Bird deed + known BPs when live citations lack them
    cites = list(d.get('citations') or [])
    have = {(c.get('label') or '').lower() for c in cites}
    for c in (seed.get('citations') or []):
        lab = (c.get('label') or '').lower()
        if lab and lab not in have:
            cites.append(c)
            have.add(lab)
    d['citations'] = cites

    if seed.get('outreach_notes') and (
            'lady bird' in (seed.get('outreach_notes') or '').lower()
            and 'lady bird' not in (d.get('outreach_notes') or '').lower()):
        d['outreach_notes'] = seed['outreach_notes']
        fills.append('outreach Lady Bird warning from seed')

    mm = d.get('money_math') or {}
    sm = seed.get('money_math') or {}
    if sm.get('tax_certs') and not mm.get('tax_certs'):
        mm['tax_certs'] = sm['tax_certs']
        if mm.get('value') and mm.get('judg') is not None:
            mm['net_equity_est'] = int(round(
                float(mm['value']) - float(mm.get('judg') or 0)
                - float(mm.get('surviving_senior') or 0) - float(mm['tax_certs'] or 0)
            ))
        fills.append('money_math tax_certs from seed')
    if sm.get('notes') and (not mm.get('notes') or 'lady bird' in (sm['notes'] or '').lower()):
        if 'fee simple' in (sm.get('notes') or '').lower() or 'mattie' in (sm.get('notes') or '').lower():
            mm['notes'] = sm['notes']
            fills.append('money_math Lady Bird equity caveat from seed')
    d['money_math'] = mm

    if fills:
        src['seed_merge'] = fills
        src['or'] = (src.get('or') or '') + ' + Capri seed fill for deed text / known labels'
        src['note'] = (
            'Live scrape tried first; seed used only for fields live could not fill '
            '(Lady Bird deed text, known OR labels).'
        )
    else:
        src['seed_merge'] = 'none — live filled Capri fields'
    d['sources'] = src
    return d


def classify_foreclosure_type(lead, chain=None):
    """HOA association language beats ctype Bank/Mortgage. Chain ftype next, then prefix."""
    plaintiff = (lead.get('plaintiff') or '').strip()
    st = (lead.get('st') or '').upper()
    if st == 'TD' or _TAX_DEED_RE.search(plaintiff):
        return 'TAX_DEED', plaintiff

    from_pl = _fc_type_plaintiff(plaintiff)
    if from_pl == 'HOA' or (_HOA_PL_RE.search(plaintiff) and from_pl != 'MORTGAGE'):
        return 'HOA', plaintiff

    if chain and chain.get('ftype') in ('HOA', 'MORTGAGE'):
        # Plaintiff HOA language already handled; chain can still correct a blank plaintiff.
        if from_pl:
            return from_pl, plaintiff
        return chain['ftype'], plaintiff

    if from_pl:
        return from_pl, plaintiff

    # County-civil prefix is a strong HOA/junior signal (not senior mortgage).
    pref = (lead.get('ftype') or _case_prefix_ftype(lead.get('case') or '') or '').upper()
    if pref == 'HOA':
        return 'HOA', plaintiff
    if pref == 'MORTGAGE':
        return 'MORTGAGE', plaintiff

    ctype = (lead.get('ctype') or '').upper()
    if 'HOA' in ctype or 'ASSOC' in ctype:
        return 'HOA', plaintiff
    if 'BANK' in ctype or 'MORT' in ctype:
        return 'MORTGAGE', plaintiff
    if not plaintiff:
        return 'UNKNOWN', plaintiff
    return 'UNKNOWN', plaintiff


def _scan_text_blob(*parts):
    return ' | '.join(str(p) for p in parts if p)


def detect_lady_bird(chain, lead, extra_texts=None):
    """Return (lady_bird: bool|None, holder, remainder, deed_type, bp, evidence_str).

    None = unknown (no deed text / OR doc strings available). Never invent.
    """
    blobs = []
    bps = []
    if chain:
        for L in (chain.get('liens') or []):
            blobs.append(_scan_text_blob(
                L.get('party'), L.get('type'), L.get('doctype'), L.get('DocTypeDescription'),
                L.get('st'), L.get('bp'), L.get('label'),
            ))
            if L.get('bp'):
                bps.append(L['bp'])
        for k in ('deeded', 'second_fc'):
            v = chain.get(k)
            if isinstance(v, dict):
                blobs.append(json.dumps(v))
            elif v:
                blobs.append(str(v))
    if extra_texts:
        blobs.extend(extra_texts)
    # Lead-level crumbs (rare)
    blobs.append(_scan_text_blob(lead.get('owners'), lead.get('plaintiff'), lead.get('ctype')))

    joined = '\n'.join(blobs)
    if not joined.strip() or (chain is None and not extra_texts):
        # No OR party/doc strings to scan
        return None, '', '', '', '', ''

    if not _LADY_BIRD_RE.search(joined):
        # We had some text but no life-estate language → treat as not detected (False),
        # not unknown — only when the chain actually carried deed/party strings.
        had_docs = bool(chain and (chain.get('liens') or chain.get('nrec')))
        if had_docs and (chain.get('source') or '') != 'batchdata':
            return False, '', '', '', '', ''
        # BatchData / empty party strings can't prove absence of Lady Bird
        if (chain or {}).get('source') == 'batchdata' or not had_docs:
            return None, '', '', '', '', ''
        return False, '', '', '', '', ''

    # Detected — extract what we can without inventing parties
    m = re.search(
        r'(?:LIFE\s+ESTATE[^|]{0,80}|LADY\s*BIRD[^|]{0,80}|REMAINDER[^|]{0,80})',
        joined, re.I,
    )
    evidence = (m.group(0).strip() if m else 'life estate / Lady Bird language in OR strings')
    bp = ''
    for b in bps:
        if b:
            bp = b
            break
    deed_type = 'Lady Bird / life estate' if re.search(r'LADY\s*BIRD', joined, re.I) else 'Life estate'
    return True, '', '', deed_type, bp, evidence


def mortgages_from_chain(chain):
    out = []
    if not chain:
        return out
    for L in (chain.get('liens') or []):
        st = (L.get('st') or '').upper()
        party = L.get('party') or L.get('lender') or ''
        # Skip obvious non-mortgage rows when typed
        dt = (L.get('type') or L.get('doctype') or '').upper()
        if dt and not re.search(r'MORT|MTG|DEED\s+OF\s+TRUST|HELOC', dt) and re.search(
                r'LIEN|JUDGMENT|NOTICE|SATISF|RELEASE|DEED(?!\s+OF\s+TRUST)', dt):
            continue
        if not party and not L.get('amt'):
            continue
        # Prefer open mortgages; include satisfied for chain context with st flag
        if dt and re.search(r'MORT|MTG|HELOC|DEED\s+OF\s+TRUST', dt):
            pass
        elif st not in ('OPEN', 'SATISFIED', ''):
            continue
        elif not L.get('amt') and st != 'OPEN':
            continue
        out.append({
            'party': party,
            'amt': L.get('bal') or L.get('amt') or 0,
            'recorded_amt': L.get('amt') or 0,
            'bp': L.get('bp') or '',
            'date': L.get('d') or L.get('_dt') or '',
            'st': st or 'OPEN',
            'heloc': bool(L.get('heloc')),
        })
    # If chain had no typed mortgages but open_count/surv_first, surface a synthetic open row
    if not out and chain.get('surv_first'):
        out.append({
            'party': '(open mortgage — party not in cache)',
            'amt': chain.get('surv_first') or chain.get('surv') or 0,
            'recorded_amt': chain.get('surv_first') or 0,
            'bp': '', 'date': '', 'st': 'OPEN', 'heloc': False,
        })
    return out


def open_mortgage_total(mortgages, chain, ftype):
    if chain:
        try:
            return _senior_surviving(chain) if ftype == 'HOA' else int(round(float(chain.get('surv') or 0)))
        except Exception:
            pass
        if ftype == 'HOA':
            return int(round(float(chain.get('surv') or chain.get('surv_first') or 0)))
    opens = [m for m in mortgages if (m.get('st') or '').upper() == 'OPEN']
    return int(round(sum(float(m.get('amt') or 0) for m in opens)))


def decide_verdict(ftype, lead, chain, or_missing, lady_bird, surviving_senior, tax_certs, killers):
    """Conservative verdict rules (product bar)."""
    value = float(lead.get('value') or 0)
    judg = float(lead.get('judgment') or 0)
    equity = max(0.0, value - judg - surviving_senior - (tax_certs or 0)) if value else 0.0

    # Broken fee title with no path
    if any('no path' in (k or '').lower() for k in killers):
        return 'PASS', 'fee title clearly broken with no path'

    if surviving_senior and value:
        if surviving_senior > max(equity, value * 0.5) and surviving_senior > judg * 2:
            return 'PASS', f'surviving senior ${surviving_senior:,.0f} >> equity'

    if or_missing and lady_bird is None:
        # Capri-style: OR missing is VERIFY unless we already have a title killer from seed
        if not any('lady bird' in (k or '').lower() or 'life estate' in (k or '').lower() for k in killers):
            return 'VERIFY', 'Official Records chain missing for this county — pull OR before wiring'

    if lady_bird is True:
        return 'CONDITIONAL', 'Lady Bird / life estate on title'
    if lady_bird is None and any('verify' in (k or '').lower() and 'lady' in (k or '').lower() for k in killers):
        return 'CONDITIONAL', 'Lady Bird / life estate unknown — verify deed'
    if surviving_senior == 0 and ftype in ('HOA', 'JUNIOR') and or_missing is False and chain is not None:
        # mortgages known-empty from real OR
        pass
    elif surviving_senior == 0 and or_missing:
        # unknown mortgage because OR missing
        if ftype in ('HOA', 'JUNIOR', 'UNKNOWN'):
            return 'CONDITIONAL', 'mortgage stack unknown (OR missing)'

    if (tax_certs or 0) >= max(3000, judg * 0.15 if judg else 3000):
        return 'CONDITIONAL', f'material tax certs ~${tax_certs:,.0f}'

    if not (lead.get('plaintiff') or '').strip() or ftype == 'UNKNOWN':
        return 'CONDITIONAL', 'plaintiff / foreclosure type unresolved'

    if killers:
        return 'CONDITIONAL', 'killer issues present'

    # Soft BUY: HOA/junior, no senior found on real OR, no title killer, taxes known/small
    taxes_ok = tax_certs is not None and (tax_certs or 0) < max(2000, judg * 0.1 if judg else 2000)
    if (ftype in ('HOA', 'JUNIOR') and surviving_senior == 0 and not or_missing
            and lady_bird is False and taxes_ok and not killers):
        return 'BUY', 'soft BUY — HOA/junior, no senior on OR, no title killer, taxes small (still confirm deed + estoppel)'

    if ftype == 'MORTGAGE' and surviving_senior == 0 and not or_missing and lady_bird is False:
        return 'CONDITIONAL', 'mortgage FC — confirm payoff / surplus math'

    if or_missing:
        return 'VERIFY', 'OR chain missing'

    return 'CONDITIONAL', 'default conservative — confirm OR + taxes + deed'


def build_diligence(lead_raw, county, source_file, headed=False, seed=None, force_live=False):
    lead = _norm_lead(lead_raw, county)
    case = lead['case']

    if seed and not force_live:
        # Verified seed wins only when we are NOT forcing a live dig (offline bake path).
        d = json.loads(json.dumps(seed))  # deep copy
        d['case'] = case
        d['county'] = lead['county'] or d.get('county') or county
        d['addr'] = lead['addr'] or d.get('addr') or ''
        d['folio'] = lead['folio'] or d.get('folio') or ''
        if lead['plaintiff']:
            d['plaintiff'] = lead['plaintiff']
        if lead['judgment']:
            d['judgment'] = lead['judgment']
        if lead['sale']:
            d['sale'] = lead['sale']
        if lead['tax_url']:
            d.setdefault('taxes', {})['url'] = lead['tax_url']
        # Keep plaintiff-derived type aligned with patched lead
        ftype, pl = classify_foreclosure_type(lead, None)
        d['foreclosure_type'] = ftype if ftype != 'UNKNOWN' else d.get('foreclosure_type') or ftype
        if pl:
            d['plaintiff'] = pl
        d['traced'] = d.get('traced') or date.today().isoformat()
        d.setdefault('sources', {})['lead'] = source_file
        return apply_lead_backed_numbers(d, lead)

    chain, chain_src, or_missing = load_or_chain(
        lead['county'], case, headed=headed, force_live=force_live,
    )
    ftype, plaintiff = classify_foreclosure_type(lead, chain)
    # Junior heuristic: small judgment vs value on mortgage-labeled CC
    if ftype == 'MORTGAGE' and lead['value'] and lead['judgment']:
        if (lead['judgment'] / lead['value']) < 0.20 and lead['eq'] >= 40:
            ftype = 'JUNIOR'

    mortgages = mortgages_from_chain(chain)
    surviving = open_mortgage_total(mortgages, chain, ftype)
    open_mtgs = [m for m in mortgages if (m.get('st') or '').upper() == 'OPEN']

    lady_bird, holder, remainder, deed_type, lb_bp, lb_ev = detect_lady_bird(chain, lead)
    killers = []
    if lady_bird is True:
        killers.append(
            f"{deed_type or 'Life estate'} detected in OR/party strings"
            + (f' ({lb_bp})' if lb_bp else '')
            + (f': {lb_ev}' if lb_ev else '')
            + '. Confirm life-estate holder joined or deceased before treating fee as clear.'
        )
    elif lady_bird is None:
        killers.append(
            'VERIFY: Lady Bird / life estate unknown — Official Records deed text not available '
            'in cache; pull the latest deed before wiring.'
        )

    if chain and chain.get('deeded'):
        killers.append(f"Property already deeded / taken: {chain.get('deeded')}")
    if chain and chain.get('second_fc'):
        killers.append(f"Second foreclosure on chain: {chain.get('second_fc')}")
    if lead.get('saleBkAct'):
        killers.append('ACTIVE bankruptcy stay — collection contact is a federal violation until lifted.')

    # Taxes — we do not invent delinquency; unknown unless a seed/cert amount exists on chain
    tax_certs = 0
    tax_status = 'unknown'
    tax_due = None
    if chain:
        # Some caches put code/irs open amounts — not the same as tax certs, but signal
        code = float(chain.get('code_open') or 0)
        if code:
            tax_certs = int(round(code))
            tax_status = 'delinquent'
            tax_due = tax_certs
    taxes = {
        'status': tax_status,
        'certs': tax_certs,
        'due': tax_due,
        'url': lead.get('tax_url') or '',
    }

    # Timeline from sale + open mtgs + live OR events (PB GetSearchResults / cached or_events)
    timeline = []
    sh = _load_json(os.path.join(HERE, 'sale_history_cache.json'), {})
    ent = sh.get(case) or {}
    if lead.get('sale'):
        timeline.append({'label': 'Auction sale', 'date': lead['sale'], 'bp': ''})
    if lead.get('saleSurv') is not None:
        timeline.append({
            'label': f"Sale resets survived ({lead.get('saleSurv')})",
            'date': '', 'bp': '',
        })
    for m in open_mtgs[:6]:
        timeline.append({
            'label': f"Open mtg — {m.get('party') or '?'}",
            'date': m.get('date') or '',
            'bp': m.get('bp') or '',
        })
    # Live Official Records events (never invent — only what enricher stored)
    if chain and (chain.get('or_events') or []):
        have_bp = {(t.get('bp') or '').replace(' ', '').upper() for t in timeline}
        for ev in chain.get('or_events') or []:
            bp = (ev.get('bp') or '').strip()
            if bp and bp.replace(' ', '').upper() in have_bp:
                continue
            timeline.append({
                'label': ev.get('label') or 'OR record',
                'date': ev.get('date') or '',
                'bp': bp,
            })
            if bp:
                have_bp.add(bp.replace(' ', '').upper())

    value = lead['value']
    list_price = lead['zprice'] or None
    judg = lead['judgment']
    net = None
    if value:
        net = int(round(value - judg - surviving - (tax_certs or 0)))
    notes = ''
    if ftype == 'HOA' and surviving:
        notes = f'HOA sale — senior mortgage stack ~${surviving:,.0f} survives.'
    elif ftype == 'HOA' and not surviving and not or_missing:
        notes = 'HOA — no open mortgage found on OR cache.'
    elif or_missing:
        notes = 'OR chain missing/incomplete — mortgage & deed calls are provisional.'

    money = {
        'value': int(round(value)) if value else 0,
        'judg': judg,
        'surviving_senior': int(round(surviving)),
        'tax_certs': int(round(tax_certs or 0)),
        'list_price': int(round(list_price)) if list_price else None,
        'net_equity_est': net,
        'notes': notes,
    }

    verdict, vreason = decide_verdict(
        ftype, lead, chain, or_missing, lady_bird, surviving, tax_certs, killers,
    )
    if vreason and verdict == 'BUY':
        killers = killers  # keep
        money['notes'] = (money.get('notes') or '') + f' Soft BUY note: {vreason}'

    citations = []
    if lead.get('pa'):
        citations.append({'label': 'Property appraiser', 'url_or_bp': lead['pa']})
    if lead.get('tax_url'):
        citations.append({'label': 'Tax collector', 'url_or_bp': lead['tax_url']})
    if lead.get('auc'):
        citations.append({'label': 'Auction', 'url_or_bp': lead['auc']})
    if lead.get('docket'):
        citations.append({'label': 'Case docket', 'url_or_bp': lead['docket']})
    if lead.get('records'):
        citations.append({'label': 'Official Records', 'url_or_bp': lead['records']})
    for m in open_mtgs[:5]:
        if m.get('bp'):
            citations.append({'label': f"Mortgage {m.get('party') or ''}".strip(),
                              'url_or_bp': m['bp']})

    # Non-mortgage liens from chain (HOA/code amounts + labeled OR events without inventing)
    liens = []
    if chain:
        for key, label in (('hoa_open', 'HOA open (cache)'), ('code_open', 'Code/muni open'),
                           ('irs_open', 'IRS open')):
            amt = chain.get(key) or 0
            if amt:
                liens.append({'label': label, 'date': '', 'bp': '', 'amt': amt})
        # Surface LIEN / LIS PENDENS / JUDGMENT book-pages from live OR events (amt unknown OK)
        for ev in (chain.get('or_events') or []):
            lab = (ev.get('label') or '')
            if not re.search(r'LIEN|LIS\s*PEND|JUDGMENT|CLAIM', lab, re.I):
                continue
            liens.append({
                'label': lab,
                'date': ev.get('date') or '',
                'bp': ev.get('bp') or '',
                'amt': None,
            })

    title = {
        'deed_type': deed_type or '',
        'lady_bird': lady_bird,
        'life_estate_holder': holder or '',
        'remainder': remainder or '',
        'bp': lb_bp or '',
    }

    outreach = ''
    if verdict == 'PASS':
        outreach = 'PASS — do not wire; senior stack or broken title kills the deal.'
    elif verdict == 'VERIFY':
        outreach = 'VERIFY — pull Official Records + latest deed before outreach commits capital.'
    elif verdict == 'CONDITIONAL':
        outreach = 'CONDITIONAL — outreach OK as options talk; do not treat title as clear until killers cleared.'
    elif verdict == 'BUY':
        outreach = 'Soft BUY — still confirm deed, estoppel, and tax status before wire.'

    d = {
        'case': case,
        'county': lead['county'],
        'addr': lead['addr'],
        'folio': lead['folio'],
        'verdict': verdict,
        'foreclosure_type': ftype,
        'plaintiff': plaintiff or lead.get('plaintiff') or '',
        'judgment': judg,
        'sale': lead.get('sale') or '',
        'mortgages': mortgages,
        'liens': liens,
        'taxes': taxes,
        'title': title,
        'killer_issues': killers,
        'money_math': money,
        'timeline': timeline,
        'outreach_notes': outreach,
        'citations': citations,
        'sources': {
            'lead': source_file,
            'or': chain_src,
            'or_missing': or_missing,
            'conf': (chain or {}).get('conf', ''),
            'verdict_reason': vreason,
        },
        'traced': date.today().isoformat(),
    }
    return apply_lead_backed_numbers(d, lead)


def render_markdown(d):
    """Capri-style markdown brief."""
    mm = d.get('money_math') or {}
    tx = d.get('taxes') or {}
    title = d.get('title') or {}
    lines = []
    v = d.get('verdict') or 'VERIFY'
    reason = (d.get('sources') or {}).get('verdict_reason') or ''
    head = f"Verdict: **{v}**"
    if reason:
        head += f" — {reason}"
    lines.append(head)
    lines.append(
        f"Foreclosure type: **{d.get('foreclosure_type') or 'UNKNOWN'}** — "
        f"{d.get('plaintiff') or '(plaintiff unknown)'}"
    )
    lines.append('')
    lines.append('| | |')
    lines.append('|---|---|')
    lines.append(f"| Case | `{d.get('case')}` |")
    lines.append(f"| Judgment | ${_fmt_money(d.get('judgment'))} |")
    lines.append(f"| Sale | {d.get('sale') or '—'} |")
    lines.append(f"| County / folio | {d.get('county') or '—'} / `{d.get('folio') or '—'}` |")
    lines.append(f"| Address | {d.get('addr') or '—'} |")

    mtgs = d.get('mortgages') or []
    opens = [m for m in mtgs if (m.get('st') or '').upper() == 'OPEN']
    if not opens:
        lines.append('| First mortgage | **None found** (in available OR/BatchData cache) |')
    else:
        for i, m in enumerate(opens[:3]):
            label = 'First mortgage' if i == 0 else f'Open mortgage {i+1}'
            bp = m.get('bp') or 'bp n/a'
            lines.append(
                f"| {label} | {_fmt_money(m.get('amt'))} — {m.get('party') or '?'} ({bp}, {m.get('date') or 'n/d'}) |"
            )

    certs = tx.get('certs')
    lines.append(
        f"| Tax certs | {tx.get('status') or 'unknown'}"
        + (f" · ~${_fmt_money(certs)}" if certs else '')
        + ' |'
    )
    lines.append('')

    if title.get('lady_bird') is True or title.get('deed_type'):
        lines.append('### Title')
        if title.get('lady_bird') is True:
            lines.append(
                f"- **Lady Bird / life estate** {title.get('bp') or ''}: "
                f"holder `{title.get('life_estate_holder') or '?'}` · "
                f"remainder `{title.get('remainder') or '?'}`"
            )
        elif title.get('lady_bird') is None:
            lines.append('- Lady Bird / life estate: **unknown** (deed text not in cache)')
        else:
            lines.append(f"- Deed: {title.get('deed_type') or 'checked — no life estate language in cache'}")
        lines.append('')

    killers = d.get('killer_issues') or []
    if killers:
        lines.append('### Killer issues')
        for k in killers:
            lines.append(f'- {k}')
        lines.append('')

    lines.append('### Money math')
    lines.append(
        f"- Value ${_fmt_money(mm.get('value'))} · Judgment ${_fmt_money(mm.get('judg'))} · "
        f"Surviving senior ${_fmt_money(mm.get('surviving_senior'))} · "
        f"Tax certs ${_fmt_money(mm.get('tax_certs'))}"
    )
    if mm.get('list_price') is not None:
        lines.append(f"- List / asking ${_fmt_money(mm.get('list_price'))}")
    if mm.get('net_equity_est') is not None:
        lines.append(f"- Net equity est **${_fmt_money(mm.get('net_equity_est'))}**")
    if mm.get('notes'):
        lines.append(f"- {mm['notes']}")
    lines.append('')

    tl = d.get('timeline') or []
    if tl:
        lines.append('### Timeline (recorded / sale)')
        for t in tl:
            lines.append(
                f"- **{t.get('label') or '?'}** — {t.get('date') or 'n/d'}"
                + (f" · {t['bp']}" if t.get('bp') else '')
            )
        lines.append('')

    if d.get('outreach_notes'):
        lines.append('### Outreach')
        lines.append(d['outreach_notes'])
        lines.append('')

    cites = d.get('citations') or []
    if cites:
        lines.append('### Citations')
        for c in cites:
            lines.append(f"- {c.get('label') or 'cite'}: `{c.get('url_or_bp') or ''}`")
        lines.append('')

    lines.append(f"_traced {d.get('traced') or ''} · sources: {json.dumps(d.get('sources') or {})}_")
    return '\n'.join(lines) + '\n'


def _fmt_money(n):
    try:
        if n is None or n == '':
            return '—'
        return f"{float(n):,.2f}".rstrip('0').rstrip('.') if float(n) % 1 else f"{int(round(float(n))):,}"
    except Exception:
        return str(n)


def write_outputs(d):
    os.makedirs(OUT_DIR, exist_ok=True)
    sc = safe_case(d['case'])
    jpath = os.path.join(OUT_DIR, f'{sc}.json')
    mpath = os.path.join(OUT_DIR, f'{sc}.md')
    json.dump(d, open(jpath, 'w', encoding='utf-8'), indent=2)
    open(mpath, 'w', encoding='utf-8').write(render_markdown(d))

    cache = _load_json(CACHE, {})
    if not isinstance(cache, dict):
        cache = {}
    cache[d['case']] = d
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), indent=2)
    return jpath, mpath


def run(case: str, headed: bool = False, force_live: bool = False) -> dict:
    """Build + write a Capri-quality diligence brief. Callable from diligence_server.

    Default: cache/compose only — lead-backed judgment/sale/money always fill in <1s.
    Pass force_live=True (CLI --live / Refresh Diligence) to re-hit county OR.
    Capri: merge verified seed when OR thin; live only when force_live=True.
    """
    case = (case or '').strip()
    if not case:
        raise ValueError('pass case number')

    lead, county, src = find_lead(case)
    if not lead:
        raise ValueError(
            f'case not found in palmbeach_leads.json / broward_leads.json / leads_final.json: {case}'
        )

    is_capri = case == CAPRI_CASE
    force = bool(force_live)
    print(f'{case}: {county} via {src}' + (
        (' — Capri live dig (seed fill after)' if is_capri else ' — force live OR') if force else
        (' — Capri compose + seed' if is_capri else ' — cache/compose (lead-backed)')
    ))

    d = build_diligence(
        lead, county, src, headed=headed, seed=None, force_live=force,
    )

    if is_capri:
        # If OR still missing / thin, fall back to full seed then note it.
        or_missing = (d.get('sources') or {}).get('or_missing')
        if or_missing and not (d.get('liens') or []):
            print(f'{case}: OR empty — using Capri seed as base, tagged in sources')
            d = build_diligence(lead, county, src, headed=False, seed=CAPRI_SEED, force_live=False)
            d.setdefault('sources', {})['or'] = (
                'verified Capri seed 2026-07-27 (cache/live OR thin)'
            )
            d['sources']['seed_merge'] = 'full seed — OR chain missing'
        else:
            d = _merge_capri_seed(d, CAPRI_SEED)
        pl = (lead.get('plaintiff') or d.get('plaintiff') or '').strip()
        if pl:
            d['plaintiff'] = pl
        d['foreclosure_type'] = 'HOA'

    d = apply_lead_backed_numbers(d, _norm_lead(lead, county))
    jpath, mpath = write_outputs(d)
    print(f"verdict={d['verdict']} type={d['foreclosure_type']} plaintiff={d['plaintiff']}")
    print(f'wrote {jpath}')
    print(f'wrote {mpath}')
    print(f'merged {CACHE}')
    return d


# Back-compat alias for older callers / docs
run_case = run


def main():
    ap = argparse.ArgumentParser(description='Deep Diligence v1 — Capri-H quality brief per case')
    ap.add_argument('--case', required=True, help='Case number (exact board key)')
    ap.add_argument('--headed', action='store_true',
                    help='PB only: after 2Captcha fails, open headed browser for one checkbox')
    ap.add_argument('--live', action='store_true',
                    help='force live county Official Records scrape (ignore OR cache)')
    a = ap.parse_args()
    try:
        run(a.case, headed=a.headed, force_live=a.live)
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == '__main__':
    main()
