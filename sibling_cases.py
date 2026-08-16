"""Sibling-case check — is this lead already CLAIMED through a case we don't track?

THE BLIND SPOT THIS CLOSES (found live, 2026-07-28): Carlos door-knocked 1101 SW 122 AVE 307
off the board's JPMorgan case (2026-003288-CA-01, sale 08/03) and was handed a filed motion
showing the SAME condo already sold at auction on 05/27 through a SIBLING case the board never
saw — the HOA's county-court case (2026-20206-CC-25). R&V Investors LLC took title 06/16 and
cash-for-keys'd the family for $19,600. Our lead was dead on arrival; the board had no idea.

Root cause: the scraper tracks ONE case per property (the RealForeclose auction we found).
A Florida condo/HOA property routinely carries TWO+ foreclosure cases (association's + bank's),
and whichever auctions FIRST decides who owns it. Any lead whose sibling case already produced
a Certificate of Title is claimed — routing Carlos there burns gas on a competitor's doorstep.

HOW IT WORKS (Miami-Dade only — OCS is an MD system):
  1. For each lead owner with a cached party-search token (cases_qs.json, minted by
     gen_cases_qs.py), pull their full case list: GetMultipleCaseResult?qs=<token>.
     No captcha on this GET — only the token MINT was captcha-walled, and it's cached.
  2. Keep foreclosure-type sibling cases (CA/CC, caseType contains 'Foreclosure'),
     excluding the lead's own case.
  3. Deep-pull each sibling via the NO-CAPTCHA per-case chain:
     GET /api/CaseInfo/encrypt/{case#} -> qs -> POST GetSingleCaseResult?qs= -> full record.
  4. SOLD SIGNATURE (verified against the Martin ground truth):
       - a party with partyTypeCode 'TPB' (Third Party Bidder) -> outside investor bid it
       - docket 'Certificate of Sale'         -> hammer fell (10-day objection window runs)
       - docket 'Certificate of Title'        -> ownership TRANSFERRED. Deal is gone.
       - docket 'Certificate of Disbursement' -> proceeds paid out
  5. FALSE-POSITIVE GUARD (common names): the party search matches by NAME, so 'Lazaro
     Ruiz' surfaces every Lazaro Ruiz in the county. A sibling only counts when the case's
     defendants overlap the lead's own parties: BOTH owners for multi-owner leads
     (conf 'high'), or the single owner for single-owner leads (conf 'med'). No overlap
     beyond the searched name on a multi-party lead -> conf 'low', never marked claimed.

  6. RECENCY RULE (the false-positive that almost shipped): a sibling whose Certificate of
     Title is from 2009 does NOT claim a 2024 lead — that old foreclosure completed, the
     property changed hands, and the CURRENT owner is in a NEW case. First sweep flagged 16
     "claimed"; 15 had titles from 1999-2015 predating the lead's own filing. The real
     signature is the Martin case's: title issued ON OR AFTER the lead case's filing year.
     Old completed siblings are history, not competition — they are dropped from the output.
     Likewise a CLOSED never-sold sibling is noise; only OPEN siblings make the '2 CASES' flag.

OUTPUT sibling_cases.json (committed — public court data, no PII beyond the public record):
  { lead_case: { checked: iso-ts, sibs: [ {case, type, status, plaintiff, filed,
                 sold, tpb, cert_sale, cert_title, conf} ], claimed: bool } }
  claimed = any KEPT sibling with cert_title (or TPB+cert_sale) dated >= the lead's filing
  year, at conf med/high.

Run:  python sibling_cases.py --all            # every MD lead with a cached token
      python sibling_cases.py --case 2026-003288-CA-01
      python sibling_cases.py --all --refresh  # re-check even cached entries
"""
import json, os, re, sys, time, datetime

import requests
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'sibling_cases.json')
H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
     'Referer': 'https://www2.miamidadeclerk.gov/ocs/'}
PAUSE = 0.35          # politeness between clerk calls
FC_TYPE = re.compile(r'foreclos', re.I)

def _load(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default

def _save(data):
    json.dump(data, open(OUT, 'w', encoding='utf-8'), indent=1)

def _multi(qs):
    r = requests.get('https://www2.miamidadeclerk.gov/ocs/api/CaseInfo/GetMultipleCaseResult?qs=' + qs,
                     headers=H, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get('caseListResult') or d.get('caseList') or []

def _single(case):
    r = requests.get(f'https://www2.miamidadeclerk.gov/ocs/api/CaseInfo/encrypt/{case}', headers=H, timeout=30)
    r.raise_for_status()
    qs = r.json()['qs']
    time.sleep(PAUSE)
    r2 = requests.post('https://www2.miamidadeclerk.gov/ocs/api/CaseInfo/GetSingleCaseResult?qs=' + qs,
                       headers={**H, 'Content-Type': 'application/json'}, data='""', timeout=30)
    r2.raise_for_status()
    return r2.json()

def _surname_first(nm):
    """'Martin, Milagros J.' -> 'MILAGROS J MARTIN'-comparable token set."""
    nm = re.sub(r'[.,]', ' ', str(nm or '')).upper()
    return {t for t in nm.split() if len(t) > 1}

def _lead_parties(lead):
    """Token-set per named human on the lead (owners + defendants)."""
    # 'defendants' is a SEMICOLON-DELIMITED STRING (foreclosure_leads.enrich_clerk:357), not a list.
    # This used to be ';'.join(str(x) for x in defendants) — iterating a string yields CHARACTERS, so it
    # built "S;m;i;t;h;,; ;J;o;h;n" and every single-char token was dropped by _surname_first's len>1
    # filter. Net effect: the co-defendant half of the sibling-name match NEVER contributed, which is
    # part of why a second case on the same owner (the Milouse pattern) went unlinked. Accept either shape.
    _defs = lead.get('defendants') or ''
    _defs = '; '.join(str(x) for x in _defs) if isinstance(_defs, (list, tuple)) else str(_defs)
    people = []
    for src in (lead.get('owners') or '', _defs):
        for nm in re.split(r'[;|]', src):
            nm = re.sub(r'&W\.?|ET UX|ET AL|A/K/A.*', '', nm, flags=re.I).strip()
            if nm and not re.search(r'ASSOCIATION|LLC|BANK|MORTGAGE|TENANT|UNKNOWN|CORP|INC\b', nm, re.I):
                toks = _surname_first(nm)
                if toks:
                    people.append(toks)
    return people

def _overlap(lead_people, sib_defendants):
    """How many of the LEAD's humans appear among the sibling case's defendants."""
    n = 0
    for person in lead_people:
        for d in sib_defendants:
            # a person matches when most of their name tokens appear in the defendant name
            if len(person & d) >= min(2, len(person)):
                n += 1
                break
    return n

def check_lead(lead, qs_cache):
    case = lead.get('case') or lead.get('Case #')
    owner = (lead.get('owner_clean') or '').upper().strip()
    qs = qs_cache.get(owner) or qs_cache.get((lead.get('owner_clean') or '').strip())
    if not qs:
        return None                                    # no cached search token -> can't discover siblings
    try:
        cases = _multi(qs)
    except Exception as e:
        print(f'  ! {case}: party search failed ({e})')
        return None
    time.sleep(PAUSE)
    sibs = []
    lead_people = _lead_parties(lead)
    for c in cases:
        cn = (c.get('caseNumber') or '').strip()
        ctype = c.get('caseType') or ''
        if not cn or cn == case or cn.replace('-', '') == str(case).replace('-', ''):
            continue
        if not FC_TYPE.search(ctype):
            continue
        # deep-pull: dockets + parties = the sold signature + the identity guard
        try:
            d = _single(cn)
        except Exception as e:
            print(f'  ! {case}: sibling {cn} pull failed ({e})')
            continue
        time.sleep(PAUSE)
        tpb = any((p.get('partyTypeCode') == 'TPB') for p in (d.get('parties') or []))
        tpb_name = next((p.get('partyName') for p in (d.get('parties') or []) if p.get('partyTypeCode') == 'TPB'), '')
        texts = [(str(x.get('docketDescrition') or ''), str(x.get('eventDate') or '')[:10])
                 for x in (d.get('dockets') or [])]
        def _has(frag):
            for t, dt in texts:
                if frag.lower() in t.lower():
                    return dt or True
            return False
        cert_sale = _has('Certificate of Sale')
        cert_title = _has('Certificate of Title')
        cert_disb = _has('Certificate of Disbursement')
        sib_defs = [_surname_first(p.get('partyName')) for p in (d.get('parties') or [])
                    if p.get('partyTypeCode') == 'DN']
        ov = _overlap(lead_people, sib_defs)
        conf = 'high' if ov >= 2 else ('med' if (ov >= 1 and len(lead_people) <= 1) or ov >= 1 else 'low')
        plaintiff = next((p.get('partyName') for p in (d.get('parties') or []) if p.get('partyTypeCode') == 'PN'), '')
        sold = bool(cert_title or (tpb and cert_sale))
        sibs.append({'case': cn, 'type': ctype, 'status': d.get('caseStatus') or '',
                     'plaintiff': plaintiff, 'filed': str(c.get('filingDate') or '')[:10],
                     'sold': sold, 'tpb': tpb_name if tpb else '', 'cert_sale': cert_sale or '',
                     'cert_title': cert_title or '', 'cert_disb': cert_disb or '', 'conf': conf})
    kept, claimed = _apply_rules(case, sibs)
    return {'checked': datetime.datetime.now().isoformat(timespec='minutes'),
            'sibs': kept, 'claimed': claimed}

def _lead_year(case):
    m = re.match(r'(\d{4})', str(case or ''))
    return int(m.group(1)) if m else 0

def _event_year(v):
    """cert dates arrive as 'MM/DD/YYYY' (or True when a docket matched with no date)."""
    m = re.search(r'(\d{4})', str(v or ''))
    return int(m.group(1)) if m else 0

def _apply_rules(lead_case, sibs):
    """RECENCY + RELEVANCE. Returns (kept_sibs, claimed).
    keep a sibling only when it is competition, not history:
      - sold with title/sale >= the lead's filing year  -> the claiming case
      - not sold and not CLOSED                         -> a live second clock
    """
    yr = _lead_year(lead_case)
    kept = []
    claimed = False
    for s in sibs:
        ev = max(_event_year(s.get('cert_title')), _event_year(s.get('cert_sale')))
        if s.get('sold'):
            if yr and ev and ev >= yr:
                kept.append(s)
                if s.get('conf') in ('high', 'med'):
                    claimed = True
            # sold long before our case existed = completed history; drop
        elif str(s.get('status') or '').upper() != 'CLOSED':
            kept.append(s)
    return kept, claimed

def main():
    args = sys.argv[1:]
    if '--recompute' in args:
        # re-derive kept-sibs + claimed from the cached raw pulls under the current rules —
        # no clerk traffic. Used when the rules change (e.g. the recency fix).
        out = _load('sibling_cases.json', {})
        n_claimed = 0
        for c, v in out.items():
            kept, claimed = _apply_rules(c, v.get('sibs') or [])
            v['sibs'], v['claimed'] = kept, claimed
            if claimed:
                n_claimed += 1
                s = next(x for x in kept if x['sold'])
                print(f'  CLAIMED  {c}  <- {s["case"]} title {s["cert_title"] or s["cert_sale"]} {s["tpb"] or ""} [{s["conf"]}]')
        _save(out)
        print(f'recomputed {len(out)} | CLAIMED {n_claimed}')
        return
    refresh = '--refresh' in args
    limit = 0
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1])
    one = None
    if '--case' in args:
        one = args[args.index('--case') + 1]

    leads = _load('leads_final.json', [])
    if isinstance(leads, dict):
        leads = leads.get('leads', [])
    qs_cache = _load('cases_qs.json', {})
    qs_cache = { (k or '').upper().strip(): v for k, v in qs_cache.items() }
    out = _load('sibling_cases.json', {})

    todo = []
    for r in leads:
        c = r.get('case') or r.get('Case #')
        if one and c != one:
            continue
        if not one and not refresh and c in out:
            continue
        todo.append(r)
    if limit:
        todo = todo[:limit]
    print(f'{len(todo)} lead(s) to check ({len(out)} cached, {len(qs_cache)} owner tokens)')

    done = skipped = flagged = 0
    for i, r in enumerate(todo, 1):
        c = r.get('case') or r.get('Case #')
        res = check_lead(r, qs_cache)
        if res is None:
            skipped += 1
            continue
        out[c] = res
        done += 1
        if res['claimed']:
            flagged += 1
            s = next(x for x in res['sibs'] if x['sold'])
            print(f'  CLAIMED  {c}  <- sibling {s["case"]} title {s["cert_title"] or "?"} '
                  f'bidder {s["tpb"] or "?"} [{s["conf"]}]')
        elif res['sibs']:
            print(f'  siblings {c}: {len(res["sibs"])} open/none-sold')
        if done % 10 == 0:
            _save(out)
            print(f'  ...{i}/{len(todo)} (saved)')
    _save(out)
    print(f'\nchecked {done} | no-token {skipped} | CLAIMED {flagged} | total cached {len(out)}')

if __name__ == '__main__':
    main()
