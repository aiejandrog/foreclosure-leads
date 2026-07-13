"""Free skip-tracing via TruePeopleSearch + curl_cffi (cookie-based).

Requires YOUR real browser cookies exported from Chrome. Uses curl_cffi to impersonate
Chrome's TLS fingerprint so TPS sees the same session. Writes to the same
skiptrace_results.json that the tracker reads — interchangeable with the paid BatchData path.

Setup once:
  1. Install "Get cookies.txt LOCALLY" Chrome extension
  2. Go to truepeoplesearch.com, do one manual search (proves you're human)
  3. Click extension icon → Export → save as tps_cookies.txt in THIS folder
  4. pip install curl-cffi beautifulsoup4  (if not already)

Usage:
  python skiptrace_free.py --dry-run          # show who WOULD be traced, no requests
  python skiptrace_free.py                     # trace Tier-A human owners not already cached
  python skiptrace_free.py --tier B            # trace Tier B
  python skiptrace_free.py --all               # every human-owner lead with an address
  python skiptrace_free.py --case 2025-014835-CA-01   # one specific case
  python skiptrace_free.py --refresh           # re-trace even if already cached
  python skiptrace_free.py --limit 10          # cap how many to trace this run
  python skiptrace_free.py --batch 10          # requests per session before pausing (default 10)

Cookie refresh:
  Cookies last days to weeks with moderate use (6-10 lookups/session, not 300 in a row).
  When the script reports CAPTCHA, re-export cookies and run again.

Compliance: same rules as BatchData — manual dial, DNC scrub, no autodial/SMS (FL FTSA + TCPA).
"""
import json, os, re, sys, time, argparse, random
from datetime import date
from pathlib import Path

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    sys.exit("curl_cffi not installed. Run:  pip install curl-cffi")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("beautifulsoup4 not installed. Run:  pip install beautifulsoup4")

HERE = Path(__file__).resolve().parent
LEADS = HERE / 'leads_final.json'
RESULTS = HERE / 'skiptrace_results.json'
COOKIES_FILE = HERE / 'tps_cookies.txt'

COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|TR|EST|ESTATE)\b', re.I)
PHONE_RE = re.compile(r'\((\d{3})\)\s*(\d{3})-(\d{4})')


def load_cookies():
    if not COOKIES_FILE.exists():
        sys.exit(
            "No tps_cookies.txt found.\n"
            "Export your TPS cookies:\n"
            "  1. Install 'Get cookies.txt LOCALLY' Chrome extension\n"
            "  2. Visit truepeoplesearch.com and do one search manually\n"
            "  3. Click extension → Export → save as tps_cookies.txt here"
        )
    cookies = {}
    for line in COOKIES_FILE.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or not line.strip():
            continue
        fields = line.split('\t')
        if len(fields) >= 7 and 'truepeoplesearch' in fields[0]:
            cookies[fields[5]] = fields[6]
    if not cookies:
        sys.exit("tps_cookies.txt has no TPS cookies. Re-export after visiting the site.")
    return cookies


def is_company(owner):
    return bool(COMPANY_RE.search(owner or ''))


def parse_addr(s):
    parts = [p.strip() for p in (s or '').split(',') if p.strip()]
    if not parts:
        return None
    zc = ''
    m = re.search(r'(\d{5})(?:-\d{4})?$', parts[-1])
    if m:
        zc = m.group(1)
    state = parts[-2] if len(parts) >= 2 and re.fullmatch(r'[A-Za-z]{2}', parts[-2]) else ''
    if not (zc and state):
        return None
    city = parts[-3] if len(parts) >= 3 else ''
    return {'city': city, 'state': state.upper(), 'zip': zc} if city else None


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
        if not parse_addr(r.get('mailing_address', '') or r.get('Address', '')):
            continue
        out.append(r)
    return out


def build_url(name, city, state):
    """Build the TPS search URL from an owner name + city/state."""
    name_clean = re.sub(r'\s+', ' ', name.split(';')[0].strip())
    location = f"{city}, {state}"
    return (
        f"https://www.truepeoplesearch.com/results?"
        f"name={requests_quote(name_clean)}&citystatezip={requests_quote(location)}"
    )


def requests_quote(s):
    return s.replace(' ', '%20').replace(',', '%2C')


def is_blocked(html, url):
    """Detect captcha/block pages. Returns a reason string or empty."""
    lower = html.lower()
    if '/internalcaptcha' in (url or '').lower():
        return 'InternalCaptcha redirect'
    if 'datadome' in lower and ('captcha' in lower or 'challenge' in lower):
        return 'DataDome challenge'
    if '<title>captcha</title>' in lower:
        return 'Captcha title'
    if 'geo.captcha-delivery.com' in lower:
        return 'DataDome captcha iframe'
    if len(html) < 500 and ('access denied' in lower or '403' in lower):
        return 'Access denied'
    return ''


def parse_phones(html):
    """Extract phone numbers from TPS results HTML.
    TPS puts phones in specific containers — we target those, not the whole page."""
    soup = BeautifulSoup(html, 'html.parser')
    phones = set()

    # TPS person cards use various class patterns; phones appear in spans/links with tel: or specific patterns
    for link in soup.find_all('a', href=re.compile(r'^tel:')):
        num = re.sub(r'\D', '', link.get('href', '').replace('tel:', ''))
        if len(num) == 10:
            phones.add(num)
        elif len(num) == 11 and num.startswith('1'):
            phones.add(num[1:])

    # Also grab formatted phones like (305) 555-1234 from phone-related containers
    for container in soup.find_all(['span', 'div', 'a'], string=PHONE_RE):
        m = PHONE_RE.search(container.get_text())
        if m:
            phones.add(m.group(1) + m.group(2) + m.group(3))

    # Fallback: regex on text nodes near "Phone" labels
    for el in soup.find_all(string=re.compile(r'phone', re.I)):
        parent = el.parent
        if parent:
            nearby = parent.get_text(' ', strip=True)
            for m in PHONE_RE.finditer(nearby):
                phones.add(m.group(1) + m.group(2) + m.group(3))

    return sorted(phones)


def has_results(html):
    """Check if TPS returned actual person results (not zero-results page)."""
    soup = BeautifulSoup(html, 'html.parser')
    # TPS shows "We could not find results" or similar
    text = soup.get_text(' ', strip=True).lower()
    if 'could not find' in text or 'no results' in text or 'did not return' in text:
        return False
    # Check for person-card links (TPS uses /find/person/ pattern)
    if soup.find('a', href=re.compile(r'/find/person/')):
        return True
    # Or phone links
    if soup.find('a', href=re.compile(r'^tel:')):
        return True
    return len(html) > 5000  # real results pages are large


def trace_one(session, name, city, state):
    """Fetch TPS, parse phones. Returns (phones, blocked_reason)."""
    url = build_url(name, city, state)
    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
    }
    resp = session.get(url, headers=headers, timeout=30, allow_redirects=True)
    blocked = is_blocked(resp.text, str(resp.url))
    if blocked:
        return [], blocked
    if not has_results(resp.text):
        return [], ''  # not blocked, just no matches
    phones = parse_phones(resp.text)
    return phones, ''


def main():
    ap = argparse.ArgumentParser(description='Free TPS skip-tracing with cookie auth')
    ap.add_argument('--tier', default='A')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--case', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--batch', type=int, default=10, help='requests per session before long pause')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    if not LEADS.exists():
        sys.exit(f"No leads file at {LEADS}. Run foreclosure_leads.py first.")

    leads = json.loads(LEADS.read_text(encoding='utf-8'))
    results = json.loads(RESULTS.read_text(encoding='utf-8')) if RESULTS.exists() else {}

    picked = select(leads, args)
    todo = [r for r in picked if args.refresh or (r.get('Case #', '') not in results)]
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(picked)} eligible lead(s); {len(todo)} to trace "
          f"({len(picked) - len(todo)} already cached). Cost: $0.00 (free)")
    if args.dry_run:
        for r in todo[:20]:
            addr = parse_addr(r.get('mailing_address', '') or r.get('Address', ''))
            name = (r.get('owners', '') or '').split(';')[0].strip()
            print(f"  would trace: {name[:28]:28} {addr['city']}, {addr['state']} {addr['zip']}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        print("(dry run — no requests made)")
        return

    if not todo:
        print("nothing to trace.")
        return

    cookies = load_cookies()
    # "chrome" = newest fingerprint curl_cffi ships (146 as of 0.15). The cookies are minted by the
    # user's real Chrome (150) — an old pinned fingerprint (chrome120) contradicts them and gets the
    # session flagged much faster. Keep this generic so library upgrades track new Chrome releases.
    session = cffi_requests.Session(impersonate="chrome")
    for k, v in cookies.items():
        session.cookies.set(k, v, domain=".truepeoplesearch.com")

    ok, blocked_count = 0, 0
    for i, r in enumerate(todo, 1):
        case = r.get('Case #', '') or r.get('Folio', '') or f'row{i}'
        name = (r.get('owners', '') or '').split(';')[0].strip()
        addr = parse_addr(r.get('mailing_address', '') or r.get('Address', ''))
        if not addr:
            print(f"  [{i}/{len(todo)}] {case}: no parseable address, skipping")
            continue

        phones, block_reason = trace_one(session, name, addr['city'], addr['state'])

        if block_reason:
            blocked_count += 1
            print(f"  [{i}/{len(todo)}] {case}: BLOCKED ({block_reason})")
            if blocked_count >= 2:
                print("\n  *** Hit captcha twice — cookies are stale. Re-export and try again. ***")
                break
            # one-off block might be a fluke, wait longer and retry next lead
            time.sleep(random.uniform(20, 35))
            continue

        blocked_count = 0  # reset consecutive block counter on success
        results[case] = {
            'name': name,
            'address': r.get('mailing_address', '') or r.get('Address', ''),
            'phones': [{'number': p, 'type': '', 'carrier': '', 'reachable': None, 'score': None, 'dnc': False} for p in phones],
            'emails': [],
            'traced': f"{date.today():%Y-%m-%d}",
            'source': 'tps',
        }
        if phones:
            ok += 1
        print(f"  [{i}/{len(todo)}] {case}: {len(phones)} phone(s) — {name[:30]}")

        # save after each successful lookup
        RESULTS.write_text(json.dumps(results, indent=1), encoding='utf-8')

        # rate limiting: vary 8-18s, with a longer pause every batch
        if i < len(todo):
            if i % args.batch == 0:
                pause = random.uniform(45, 75)
                print(f"  --- batch pause ({pause:.0f}s) ---")
            else:
                pause = random.uniform(8, 18)
            time.sleep(pause)

    print(f"\nDONE: {ok}/{len(todo)} leads got a phone. Results -> skiptrace_results.json")
    print("Reminder: MANUAL dial only, scrub DNC, no autodial/SMS (FL FTSA + TCPA).")


if __name__ == '__main__':
    main()
