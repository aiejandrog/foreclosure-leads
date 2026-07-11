"""Local, on-demand skip-tracing via the BatchData API.

Pulls owner phone numbers + emails for leads and stores them LOCALLY (skiptrace_results.json,
gitignored). Phones are personal data: they only reach the shared site through the encrypted gate
(see make_tracker), never in plaintext on the public web.

Setup once:
  - Get a BatchData API key (paid, ~$0.20/lookup) at batchdata.io.
  - Put it in a file next to this script named  batchdata.key   (gitignored), OR
    set the environment variable  BATCHDATA_API_KEY.

Usage:
  python skiptrace.py --dry-run          # show who WOULD be traced + est cost, make no call
  python skiptrace.py                     # trace Tier-A human owners not already cached
  python skiptrace.py --tier B            # trace Tier B instead
  python skiptrace.py --all               # every human-owner lead with an address
  python skiptrace.py --case 2025-014835-CA-01   # one specific case
  python skiptrace.py --refresh           # re-trace even if already cached
  python skiptrace.py --limit 10          # cap how many you spend on this run
  python skiptrace.py --raw               # print the raw API response (to confirm/adjust schema)

Compliance: BatchData filters TCPA-restricted numbers by default (we keep that default). Still,
dial MANUALLY, scrub against the federal DNC list, and never autodial/text these owners (FL FTSA + TCPA).
"""
import json, os, re, sys, time, argparse
from datetime import date
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
RESULTS = os.path.join(HERE, 'skiptrace_results.json')
KEYFILE = os.path.join(HERE, 'batchdata.key')
API_URL = 'https://api.batchdata.com/api/v1/property/skip-trace'
COST_PER = 0.20  # approx $/lookup, for the pre-spend estimate
UA = 'foreclosure-leads-skiptrace/1.0'

COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE)\b', re.I)

def load_key():
    k = os.environ.get('BATCHDATA_API_KEY', '').strip()
    if k: return k
    if os.path.exists(KEYFILE):
        k = open(KEYFILE, encoding='utf-8').read().strip()
        if k: return k
    return ''

def is_company(owner):
    return bool(COMPANY_RE.search(owner or ''))

def parse_addr(s):
    """PA mailing/site addresses are comma-joined 'Street[, Unit], City, State, Zip'. Parse positionally."""
    parts = [p.strip() for p in (s or '').split(',') if p.strip()]
    if not parts:
        return None
    zc = ''
    m = re.search(r'(\d{5})(?:-\d{4})?$', parts[-1])
    if m: zc = m.group(1)
    state = parts[-2] if len(parts) >= 2 and re.fullmatch(r'[A-Za-z]{2}', parts[-2]) else ''
    # if the token before zip wasn't a 2-letter state, the format is off — bail to keep matches clean
    if not (zc and state):
        return None
    city = parts[-3] if len(parts) >= 3 else ''
    street = ', '.join(parts[:-3]) if len(parts) > 3 else ''
    if not (street and city):
        return None
    return {'street': street, 'city': city, 'state': state.upper(), 'zip': zc}

def address_for(lead):
    # prefer the mailing address (where the owner actually is, incl. absentee owners), fall back to the property
    return parse_addr(lead.get('mailing_address', '')) or parse_addr(lead.get('Address', ''))

def select(leads, args):
    out = []
    for r in leads:
        if args.case:
            if (r.get('Case #', '') or '') != args.case:
                continue
        elif not args.all:
            if (r.get('tier', '') or '') != args.tier:
                continue
        owner = (r.get('owners', '') or '')
        if not owner or is_company(owner.split(';')[0]):
            continue
        if not address_for(r):
            continue
        out.append(r)
    return out

def extract(data):
    """Defensive parse of the BatchData response -> normalized phones/emails."""
    phones, emails = [], []
    persons = ((data or {}).get('results') or {}).get('persons') or []
    for pr in persons:
        for ph in (pr.get('phoneNumbers') or []):
            num = re.sub(r'\D', '', str(ph.get('number', '')))
            if len(num) >= 10:
                phones.append({
                    'number': num, 'type': ph.get('type', ''), 'carrier': ph.get('carrier', ''),
                    'reachable': ph.get('reachable'), 'score': ph.get('score'),
                    'dnc': bool(ph.get('dnc') or ph.get('tcpa')),
                })
        for em in (pr.get('emails') or []):
            e = em.get('email') if isinstance(em, dict) else em
            if e: emails.append(e)
    # de-dupe phones by number, keep highest score first
    seen, dedup = set(), []
    for p in sorted(phones, key=lambda x: -(x.get('score') or 0)):
        if p['number'] in seen: continue
        seen.add(p['number']); dedup.append(p)
    return dedup, sorted(set(emails))

def trace_one(session, key, lead, raw=False):
    body = {'requests': [{'propertyAddress': address_for(lead)}]}
    r = session.post(API_URL, json=body, timeout=30,
                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                              'User-Agent': UA})
    if raw:
        print('--- RAW', lead.get('Case #', ''), r.status_code, '---')
        print(r.text[:2000])
    r.raise_for_status()
    return extract(r.json())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='A')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--case', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--raw', action='store_true')
    args = ap.parse_args()

    leads = json.load(open(LEADS, encoding='utf-8'))
    results = json.load(open(RESULTS, encoding='utf-8')) if os.path.exists(RESULTS) else {}

    picked = select(leads, args)
    todo = [r for r in picked if args.refresh or (r.get('Case #', '') not in results)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(picked)} eligible lead(s); {len(todo)} to trace "
          f"({len(picked)-len(todo)} already cached). Est. cost: ${len(todo)*COST_PER:.2f}")
    if args.dry_run:
        for r in todo[:20]:
            a = address_for(r)
            print(f"  would trace: {(r.get('owners','') or '')[:28]:28} {a['street']}, {a['city']} {a['zip']}")
        print("(dry run — no API calls made)")
        return

    if not todo:
        print("nothing to trace."); return

    key = load_key()
    if not key:
        print("NO API KEY. Put your BatchData key in batchdata.key or set BATCHDATA_API_KEY. Aborting.")
        sys.exit(1)

    s = requests.Session()
    ok = 0
    for i, r in enumerate(todo, 1):
        case = r.get('Case #', '') or (r.get('Folio', '') or f'row{i}')
        try:
            phones, emails = trace_one(s, key, r, raw=args.raw)
            results[case] = {
                'name': (r.get('owners', '') or '').split(';')[0].strip(),
                'address': r.get('mailing_address', '') or r.get('Address', ''),
                'phones': phones, 'emails': emails, 'traced': f"{date.today():%Y-%m-%d}",
            }
            if phones: ok += 1
            print(f"  [{i}/{len(todo)}] {case}: {len(phones)} phone(s), {len(emails)} email(s)")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {case}: ERROR {str(e)[:120]}")
        time.sleep(0.3)
        json.dump(results, open(RESULTS, 'w', encoding='utf-8'), indent=1)  # save as we go

    print(f"\nDONE: {ok}/{len(todo)} leads got a phone. Results -> skiptrace_results.json (local, gitignored).")
    print("Reminder: MANUAL dial only, scrub the federal DNC list, no autodial/SMS (FL FTSA + TCPA).")

if __name__ == '__main__':
    main()
