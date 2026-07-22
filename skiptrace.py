"""Local, on-demand skip-tracing. Two licensed providers, auto-selected by which key file exists:
  - Tracerfy  (tracerfy.key)  -> $0.02/credit, 5 credits/hit (~$0.10), 0 on a miss, no minimum deposit
  - BatchData (batchdata.key) -> ~$0.15/hit, $50 minimum balance

Pulls owner phone numbers + emails and stores them LOCALLY (skiptrace_results.json, gitignored).
Phones are personal data: they only reach the shared site through the encrypted gate (see
make_tracker), never in plaintext on the public web.

Setup once:
  - Make an account with ONE provider, generate an API key from its dashboard.
  - Save it next to this script as  tracerfy.key  or  batchdata.key  (both gitignored), OR
    set env  TRACERFY_API_KEY / BATCHDATA_API_KEY.

Usage:
  python skiptrace.py --dry-run          # show who WOULD be traced + est cost, make no call
  python skiptrace.py                     # trace Tier-A human owners not already cached
  python skiptrace.py --tier B            # trace Tier B instead
  python skiptrace.py --all               # every human-owner lead with an address
  python skiptrace.py --case 2025-014835-CA-01   # one specific case
  python skiptrace.py --refresh           # re-trace even if already cached
  python skiptrace.py --limit 10          # cap how many you spend on this run
  python skiptrace.py --provider tracerfy # force a provider (default: auto-detect by key file)
  python skiptrace.py --raw               # print the raw API response (to confirm/adjust schema)

Compliance: providers filter TCPA-restricted numbers by default (we keep that). Still, dial MANUALLY,
scrub against the federal DNC list, and never autodial/text these owners (FL FTSA + TCPA).
"""
import glob as _glob
import json, os, re, sys, time, argparse
from datetime import date
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
RESULTS = os.path.join(HERE, 'skiptrace_results.json')
UA = 'foreclosure-leads-skiptrace/1.1'

COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE)\b', re.I)


# ---- schema helpers: work across Miami-Dade (leads_final.json) AND county files (broward/palmbeach_leads.json)
def _case(r):  return (r.get('Case #', '') or r.get('case', '') or '').strip()
def _mailaddr(r): return r.get('mailing_address', '') or r.get('mail', '') or ''
def _propaddr(r): return r.get('Address', '') or r.get('addr', '') or ''

def load_all_leads():
    """Miami-Dade + every county <name>_leads.json (skip scratch/_-prefixed + the raw MD file), county-tagged."""
    leads = list(json.load(open(LEADS, encoding='utf-8')))
    for f in sorted(_glob.glob(os.path.join(HERE, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_final.json', 'leads_raw.json') or bn.startswith('_'):
            continue
        try: leads.extend(json.load(open(f, encoding='utf-8')))
        except Exception as e: print(f"skip {bn}: {e}")
    return leads


# ---- providers -------------------------------------------------------------------------------
def _body_tracerfy(a):
    return {'address': a['street'], 'city': a['city'], 'state': a['state'], 'zip': a['zip'], 'find_owner': True}

def _body_batchdata(a):
    return {'requests': [{'propertyAddress': a}]}

def _extract_tracerfy(data):
    # flat object; phones/emails nested inside persons[]; best phone = lowest rank (1 first)
    return _collect((data or {}).get('persons') or [], phone_field='phones', order=lambda p: (p.get('rank') or 9999))

def _extract_batchdata(data):
    persons = ((data or {}).get('results') or {}).get('persons') or []
    return _collect(persons, phone_field='phoneNumbers', order=lambda p: -(p.get('score') or 0))

PROVIDERS = {
    'tracerfy':  {'keyfile': 'tracerfy.key',  'env': 'TRACERFY_API_KEY',
                  'url': 'https://tracerfy.com/v1/api/trace/lookup/',
                  'body': _body_tracerfy, 'extract': _extract_tracerfy, 'cost': 0.10},
    'batchdata': {'keyfile': 'batchdata.key', 'env': 'BATCHDATA_API_KEY',
                  'url': 'https://api.batchdata.com/api/v1/property/skip-trace',
                  'body': _body_batchdata, 'extract': _extract_batchdata, 'cost': 0.15},
}

def pick_provider(forced=''):
    if forced:
        if forced not in PROVIDERS: sys.exit(f"unknown provider '{forced}'. Options: {', '.join(PROVIDERS)}")
        return forced
    # auto: prefer whichever key is actually present (env or file), tracerfy first
    for name in ('tracerfy', 'batchdata'):
        p = PROVIDERS[name]
        if os.environ.get(p['env'], '').strip() or os.path.exists(os.path.join(HERE, p['keyfile'])):
            return name
    return 'tracerfy'  # default target when nothing is set up yet (dry-run still works)

def load_key(provider):
    p = PROVIDERS[provider]
    k = os.environ.get(p['env'], '').strip()
    if k: return k
    kf = os.path.join(HERE, p['keyfile'])
    if os.path.exists(kf):
        k = open(kf, encoding='utf-8').read().strip()
        if k: return k
    return ''


# ---- shared parsing --------------------------------------------------------------------------
def _collect(persons, phone_field, order):
    phones, emails = [], []
    for pr in (persons or []):
        for ph in sorted(pr.get(phone_field) or [], key=order):
            num = re.sub(r'\D', '', str(ph.get('number', '')))
            if len(num) >= 10:
                phones.append({'number': num, 'type': ph.get('type', ''), 'carrier': ph.get('carrier', ''),
                               'dnc': bool(ph.get('dnc') or ph.get('tcpa'))})
        for em in (pr.get('emails') or []):
            e = em.get('email') if isinstance(em, dict) else em
            if e: emails.append(e)
    seen, dedup = set(), []
    for p in phones:
        if p['number'] in seen: continue
        seen.add(p['number']); dedup.append(p)
    return dedup, sorted(set(emails))


def is_company(owner):
    return bool(COMPANY_RE.search(owner or ''))

def parse_addr(s):
    """Parse a comma-joined address across all the formats we produce:
       Miami-Dade mailing  'Street, City, FL, 33184-2809'  (state as its own part)
       county mailing (new) 'Street, City, FL 33401'        (state+zip in the last part)
       county property      'Street, City, 33025'           (no state -> implied FL, all our counties are FL)
    """
    parts = [p.strip() for p in (s or '').split(',') if p.strip()]
    if len(parts) < 2:
        return None
    last = parts[-1]
    zm = re.search(r'(\d{5})(?:-\d{4})?$', last)
    if not zm:
        return None
    zc = zm.group(1)
    sm = re.search(r'\b([A-Za-z]{2})\b\s+\d{5}', last)                     # "FL 33401" in the last part
    if sm:
        state, city, street = sm.group(1).upper(), parts[-2], ', '.join(parts[:-2])
    elif len(parts) >= 3 and re.fullmatch(r'[A-Za-z]{2}', parts[-2]):      # "..., City, FL, 33184"
        state, city, street = parts[-2].upper(), parts[-3], ', '.join(parts[:-3])
    else:                                                                  # "..., City, 33025" -> FL implied
        state, city, street = 'FL', parts[-2], ', '.join(parts[:-2])
    if not (street and city):
        return None
    return {'street': street, 'city': city, 'state': state, 'zip': zc}

def address_for(lead):
    # prefer the mailing address (where the owner actually is, incl. absentee owners), fall back to the property
    return parse_addr(_mailaddr(lead)) or parse_addr(_propaddr(lead))

def _officer_target(case, llcs):
    """For a company-owned lead, the human to actually skip-trace: the first Sunbiz officer with a
    usable address, else the registered agent (llc_officers.py output). Returns (name, addr) or
    (None, None) when no human is resolved yet — in which case the row still carries its free
    People/CyberBG links, so nothing is lost, we just don't spend a lookup on a company shell."""
    lo = (llcs or {}).get(case) or {}
    for p in (lo.get('officers') or []):
        if p and p.get('n'):
            a = parse_addr(p.get('a') or '')
            if a:
                return p['n'], a
    if lo.get('ra') and lo.get('ra_addr'):
        a = parse_addr(lo.get('ra_addr'))
        if a:
            return lo['ra'], a
    return None, None


def select(leads, args, llcs=None):
    """Attach a trace target to every eligible lead. Human owners trace their own mailing address;
    company owners trace the Sunbiz officer/agent behind the LLC (r['_trace_*']), so a company-owned
    deal is a callable person, not a dead end."""
    out = []
    for r in leads:
        if args.case:
            if _case(r) != args.case:
                continue
        elif not args.all:
            if (r.get('tier', '') or '') != args.tier:
                continue
        owner = (r.get('owners', '') or '')
        if not owner:
            continue
        if is_company(owner.split(';')[0]):
            oname, oaddr = _officer_target(_case(r), llcs)
            if oaddr:
                r['_trace_addr'] = oaddr                   # LLC with a resolved Sunbiz officer — best target
                r['_trace_name'] = oname
                r['_trace_entity'] = owner.split(';')[0].strip()
            else:
                # No Sunbiz officer: a TRUST/ESTATE (the trustee is named in the owner string and gets
                # mail at the property address) or an LLC whose Sunbiz pull missed. The mailing address
                # is almost always the trustee's / manager's own home — trace it rather than skip.
                a = address_for(r)
                if not a:
                    continue
                r['_trace_addr'] = a
                r['_trace_name'] = owner.split(';')[0].strip()
                r['_trace_entity'] = owner.split(';')[0].strip()
        else:
            a = address_for(r)
            if not a:
                continue
            r['_trace_addr'] = a
            r['_trace_name'] = owner.split(';')[0].strip()
            r['_trace_entity'] = ''
        out.append(r)
    return out

def trace_one(session, prov, key, lead, raw=False):
    p = PROVIDERS[prov]
    addr = lead.get('_trace_addr') or address_for(lead)   # officer address for a company, else the owner's
    r = session.post(p['url'], json=p['body'](addr), timeout=30,
                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'User-Agent': UA})
    if raw:
        print('--- RAW', lead.get('Case #', ''), r.status_code, '---')
        print(r.text[:2000])
    r.raise_for_status()
    return p['extract'](r.json())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='A')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--case', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--raw', action='store_true')
    ap.add_argument('--provider', default='', help='tracerfy | batchdata (default: auto-detect by key file)')
    args = ap.parse_args()

    provider = pick_provider(args.provider)
    cost_per = PROVIDERS[provider]['cost']

    leads = load_all_leads()                                    # Miami-Dade + Broward + Palm Beach
    results = json.load(open(RESULTS, encoding='utf-8')) if os.path.exists(RESULTS) else {}
    _lof = os.path.join(HERE, 'llc_officers.json')              # the Sunbiz humans behind LLC owners
    llcs = json.load(open(_lof, encoding='utf-8')) if os.path.exists(_lof) else {}

    picked = select(leads, args, llcs)
    todo = [r for r in picked if args.refresh or (_case(r) not in results)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"provider: {provider}  |  {len(picked)} eligible lead(s); {len(todo)} to trace "
          f"({len(picked)-len(todo)} already cached). Est. cost: ${len(todo)*cost_per:.2f}")
    _comp = sum(1 for r in todo if r.get('_trace_entity'))
    if _comp:
        print(f"  (of those, {_comp} are LLC-owned -> tracing the Sunbiz officer/agent behind the company)")
    if args.dry_run:
        for r in todo[:20]:
            a = r.get('_trace_addr') or address_for(r)
            who = (r.get('_trace_name') or r.get('owners', '') or '')[:28]
            via = f"  [officer of {r['_trace_entity'][:22]}]" if r.get('_trace_entity') else ''
            print(f"  would trace: {who:28} {a['street']}, {a['city']} {a['zip']}{via}")
        print("(dry run — no API calls made)")
        return

    if not todo:
        print("nothing to trace."); return

    key = load_key(provider)
    if not key:
        kf = PROVIDERS[provider]['keyfile']
        print(f"NO API KEY for '{provider}'. Put your key in {kf} or set {PROVIDERS[provider]['env']}. Aborting.")
        sys.exit(1)

    s = requests.Session()
    ok = 0
    for i, r in enumerate(todo, 1):
        case = _case(r) or (r.get('Folio', '') or r.get('folio', '') or f'row{i}')
        try:
            phones, emails = trace_one(s, provider, key, r, raw=args.raw)
            _ta = r.get('_trace_addr') or {}
            results[case] = {
                'name': r.get('_trace_name') or (r.get('owners', '') or '').split(';')[0].strip(),
                'entity': r.get('_trace_entity', ''),          # the LLC, when the number belongs to its officer
                'address': (', '.join(v for v in (_ta.get('street'), _ta.get('city'), _ta.get('zip')) if v)
                            if _ta else (_mailaddr(r) or _propaddr(r))),
                'county': r.get('county', 'MIAMI-DADE'),
                'phones': phones, 'emails': emails, 'traced': f"{date.today():%Y-%m-%d}", 'source': provider,
            }
            if phones: ok += 1
            print(f"  [{i}/{len(todo)}] {case}: {len(phones)} phone(s), {len(emails)} email(s)")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {case}: ERROR {str(e)[:140]}")
        time.sleep(0.3)
        json.dump(results, open(RESULTS, 'w', encoding='utf-8'), indent=1)  # save as we go

    print(f"\nDONE: {ok}/{len(todo)} leads got a phone. Results -> skiptrace_results.json (local, gitignored).")
    print("Reminder: MANUAL dial only, scrub the federal DNC list, no autodial/SMS (FL FTSA + TCPA).")

if __name__ == '__main__':
    main()
