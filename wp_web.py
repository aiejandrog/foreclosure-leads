#!/usr/bin/env python
"""wp_web.py -- owner/resident phones from a LOGGED-IN whitepages.com session, on the laptop.

Alejandro, 2026-10-22, in writing: "build the pipeline to scrape thru regular whitepages we take
on the risks dont stress it." This is that path. It is NOT the Pro API (whitepages_lookup.py, still
the default and untouched); it reads the consumer site www.whitepages.com in a real browser that is
already signed into Alejandro's account, and writes the SAME whitepages_lookup.json cache shape that
wp_contacts.py, the Call Mode card (#334) and the board bake already read. Nothing downstream changes.

RISK, STATED ONCE. whitepages.com's terms forbid using the site for commercial purposes and forbid
automated access; doing this can get the account suspended or banned. Alejandro accepted that. The
throttle and per-run cap below are to look human, not to make it allowed. The Pro API stays wired as
the clean alternative the moment a working key exists.

WHERE IT RUNS. The laptop only — the login lives there. It reuses the Playwright persistent profile
in browser-profile/ (gitignored, same jar login-setup.py uses for RealForeclose). Log in by hand
once with `--login`; nothing here ever reads, types or stores a password, and no cookie is committed.

CALIBRATION. This build environment cannot reach whitepages.com (blocked) and has no login, so the
page extractor was written against the exact visible-text layout Alejandro pasted on 10-22 (an
address page's "Owners & Residents" block: a name, an optional ", age N", a role line "Owner" or
"Resident", then "Cell Phone" / "Landline" / "Email Address" label-and-value pairs, "—" for none).
It parses the page's VISIBLE TEXT, not CSS selectors, so a cosmetic DOM change does not break it.
Before trusting a batch, capture one real page with `--save-text` / `--save-html` and eyeball the
parsed counts; _wpwebtest.py pins the parser against a synthetic copy of that layout.

LEAD SELECTION. Same gate the nightly deep search uses: whitepages_lookup.pick_callable — person-
owned, has a street address, no contact hold (stay / never-contact / opt-out / dismissed), a sale
inside the window or an undated lis pendens, fewest phones first. Or --case for named leads. A lead
that already carries a real lookup is skipped unless --refresh.

    python wp_web.py --login                       # open the browser, sign in by hand, press Enter
    python wp_web.py --plan                         # which leads it would scrape, no browser
    python wp_web.py --pick-callable 15             # scrape up to 15 board leads, human-paced
    python wp_web.py --compare 25                    # leads that already have skip-trace numbers
    python wp_web.py --case CACE-25-019745          # one named lead
    python wp_web.py --case C --from-file page.txt   # no browser: a page you copied by hand
    python wp_web.py --compare 25 --assist           # NO automation: opens each page in YOUR browser,
                                                     # you Ctrl+A / Ctrl+C, press Enter, it caches
    python wp_web.py --case C --save-text out.txt    # capture the visible text for calibration
    python wp_web.py --pick-callable 15 --refresh    # re-scrape even cached leads

Env:
    WP_WEB_MAX_LEADS      per-run lead cap (default 15)
    WP_WEB_THROTTLE_S     base delay between leads (default 9.0; a random 0-6s is added)
    WP_WEB_PROFILE        browser profile dir (default browser-profile/)
    WP_WEB_BASE           site base (default https://www.whitepages.com)

Counts only to the console: lead count, per-lead owner/resident/phone counts, never a name or
number. The cache file itself holds homeowner data and is gitignored, exactly like the API cache.
"""
import argparse
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, os.environ.get('WP_WEB_PROFILE', 'browser-profile'))
BASE = os.environ.get('WP_WEB_BASE', 'https://www.whitepages.com').rstrip('/')
MAX_LEADS = int(os.environ.get('WP_WEB_MAX_LEADS', '15'))
THROTTLE_S = float(os.environ.get('WP_WEB_THROTTLE_S', '9.0'))
# The browser the persistent profile runs in. 'chrome' = the real installed Google Chrome (what
# Alejandro browses with); '' = Playwright's bundled Chromium. Same profile either way.
CHANNEL = os.environ.get('WP_WEB_CHANNEL', 'chrome').strip() or None
_BLOCK_RE = re.compile(r'temporarily restricted|access (is )?denied|unusual traffic|verify you are human|captcha', re.I)

_LABELS = {'cell phone': 'mobile', 'mobile': 'mobile', 'landline': 'landline',
           'phone': '', 'email address': 'email', 'email': 'email'}
_ROLE = {'owner': 'owner', 'resident': 'resident', 'owners & residents': None}
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .''\-]+$")
_PHONE_RE = re.compile(r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}')
_EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


def _digits(v):
    d = re.sub(r'\D', '', str(v or ''))
    return d[1:] if len(d) == 11 and d.startswith('1') else d


def _is_name(line):
    if not _NAME_RE.match(line or ''):
        return False
    if line.strip().lower() in _LABELS or line.strip().lower() in _ROLE:
        return False
    return len([t for t in line.split() if t]) >= 2


def parse_people_text(text):
    """Visible page text -> [{name, role, age, phones:[{number,type}], emails:[...]}].

    Segments on the role lines ("Owner" / "Resident"): each person's name is the nearest name-like
    line above its role, and its phones/emails are the label-and-value pairs between that role line
    and the next. Works on the pasted layout; see CALIBRATION in the module docstring."""
    lines = [l.strip() for l in str(text or '').replace('\r', '').split('\n')]
    lines = [l for l in lines if l != '']
    roles = [i for i, l in enumerate(lines) if l.lower() in ('owner', 'resident')]
    people = []
    for n, ri in enumerate(roles):
        name = ''
        for j in range(ri - 1, max(-1, ri - 6), -1):
            if _is_name(lines[j]):
                name = lines[j]
                break
        age = None
        if 0 <= ri - 1 < len(lines):
            m = re.match(r',?\s*age\s+(\d{1,3})', lines[ri - 1], re.I) or \
                (re.match(r',?\s*age\s+(\d{1,3})', lines[ri - 2], re.I) if ri - 2 >= 0 else None)
            if m:
                age = int(m.group(1))
        end = roles[n + 1] if n + 1 < len(roles) else len(lines)
        # back up `end` so the next person's name line is not read as this person's value
        if end < len(lines):
            for j in range(end - 1, ri, -1):
                if _is_name(lines[j]):
                    end = j
                else:
                    break
        phones, emails, label = [], [], ''
        seen = set()
        for l in lines[ri + 1:end]:
            low = l.lower()
            if low in _LABELS:
                label = _LABELS[low]
                continue
            if l == '—' or l == '-' or l == '–':
                label = ''
                continue
            if label == 'email':
                m = _EMAIL_RE.search(l)
                if m and m.group(0).lower() not in seen:
                    seen.add(m.group(0).lower())
                    emails.append(m.group(0))
                continue
            m = _PHONE_RE.search(l)
            if m:
                d = _digits(m.group(0))
                if len(d) == 10 and d not in seen:
                    seen.add(d)
                    phones.append({'number': d, 'type': label if label in ('mobile', 'landline') else ''})
                label = ''
        people.append({'name': name, 'role': lines[ri].lower(), 'age': age,
                       'phones': phones, 'emails': emails})
    # keep only people who have a name AND at least one contact point
    return [p for p in people if p['name'] and (p['phones'] or p['emails'])]


def to_cache_entry(people, address):
    """people (parse_people_text) + the searched address string -> a whitepages_lookup.json entry.

    Owners land in ownership_info.person_owners, residents in residents, the same two lists the Pro
    API /property response fills, so wp_contacts.lead_contacts / parcel_owners and the bake grade a
    scraped record exactly like an API one. No `apn` key is set: wp_parcel_verdict treats an absent
    apn as 'ok' (no parcel claim to contradict), and the owner-name bar still decides owner vs
    household, so a wrong-address page cannot turn a stranger into the owner."""
    def rec(p):
        return {'name': p['name'],
                'phones': [dict(x) for x in p['phones']],
                'emails': list(p['emails']),
                'current_addresses': [{'full_address': address}] if address else []}
    owners = [rec(p) for p in people if p['role'] == 'owner']
    residents = [rec(p) for p in people if p['role'] != 'owner']
    return {'result': {'property_address': {'full_address': address},
                       'ownership_info': {'person_owners': owners},
                       'residents': residents},
            '_http': 200, '_ts': int(time.time()), '_src': 'wpweb'}


def _counts(entry):
    r = entry.get('result') or {}
    ow = (r.get('ownership_info') or {}).get('person_owners') or []
    res = r.get('residents') or []
    ph = sum(len(o.get('phones') or []) for o in ow) + sum(len(o.get('phones') or []) for o in res)
    return len(ow), len(res), ph


# ---- the browser half (laptop only; import guarded so --plan needs no Playwright) -----------------

def _addr_query(street, city, state):
    """whitepages.com address URL: /address/<house-street>/<City>-<ST>. UNVERIFIED from here (the
    site is unreachable from the build environment); if it lands on a search page instead of the
    address page, change this one function. Hand-assisted mode still works from any page you copy."""
    import urllib.parse
    def slug(t):
        return '-'.join(re.sub(r"[^A-Za-z0-9 ]", ' ', t).split())
    return '%s/address/%s/%s' % (BASE, urllib.parse.quote(slug(street)),
                                 urllib.parse.quote('%s-%s' % (slug(city), state)))


def _scrape_one(page, lead):
    """Navigate to the lead's address page and return (entry, text). Never raises for a miss."""
    import whitepages_lookup as WL
    parts = WL._split_addr(WL._lead_addr(lead))
    if not parts:
        return None, ''
    street, city, state = parts
    page.goto(_addr_query(street, city, state), wait_until='domcontentloaded', timeout=45000)
    try:
        page.wait_for_selector('text=/Owners?\\s*&\\s*Residents?|Cell Phone|Landline/i', timeout=12000)
    except Exception:
        pass
    text = page.inner_text('body')
    people = parse_people_text(text)
    addr = WL._lead_addr(lead)
    return (to_cache_entry(people, addr) if people else None), text


def _login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, channel=CHANNEL,
                                                    viewport={'width': 1280, 'height': 920})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE + '/dashboard', wait_until='domcontentloaded')
        try:
            input('Sign into whitepages.com in the browser (your name at the top-right = good), '
                  'then press Enter HERE -> ')
        except EOFError:
            print('No console — close the browser window when signed in.')
            try:
                page.wait_for_event('close', timeout=0)
            except Exception:
                pass
        ctx.close()
    print('Session saved to %s' % PROFILE)


def _run(todo, save_text=None, save_html=None, refresh=False):
    import whitepages_lookup as WL
    from playwright.sync_api import sync_playwright
    cache = _load_cache()
    scraped = miss = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=False, channel=CHANNEL,
                                                    viewport={'width': 1280, 'height': 920})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for idx, lead in enumerate(todo):
            case = WL._lead_key(lead)
            if not refresh and WL._cache_looked(cache.get(case)):
                print('  %s: already looked up, skipping (use --refresh)' % case[-4:], flush=True)
                continue
            try:
                entry, text = _scrape_one(page, lead)
            except Exception as e:
                print('  ...%s: error %s' % (case[-4:], e.__class__.__name__), flush=True)
                entry, text = None, ''
            if text and _BLOCK_RE.search(text) and not entry:
                # The site is refusing this session. Do NOT retry or push on: more requests while
                # blocked is what turns a pause into a ban. Stop the run and say how to continue.
                print('  ...%s: whitepages.com is blocking this session ("%s"). Stopping the run; '
                      'nothing was changed. Wait, then try again, or use --from-file with a page you '
                      'copied yourself.' % (case[-4:], _BLOCK_RE.search(text).group(0)), flush=True)
                if save_text:
                    open(save_text, 'w', encoding='utf-8').write(text)
                miss += 1
                break
            if save_text and text:
                open(save_text, 'w', encoding='utf-8').write(text)
                print('  wrote visible text to %s (%d chars)' % (save_text, len(text)))
            if save_html:
                open(save_html, 'w', encoding='utf-8').write(page.content())
                print('  wrote page HTML to %s' % save_html)
            if entry:
                old = cache.get(case) or {}
                for k in ('_person', '_prop_id'):           # keep any API layer already present
                    if old.get(k) is not None:
                        entry[k] = old[k]
                cache[case] = entry
                ow, res, ph = _counts(entry)
                print('  ...%s: %d owner(s), %d resident(s), %d phone(s)' % (case[-4:], ow, res, ph), flush=True)
                scraped += 1
            else:
                why = ''
                low = (text or '').lower()
                if text and re.search(r'\b(log in|sign in)\b', low) and not re.search(r'sign out|log out|my account', low):
                    why = ' (page looks SIGNED OUT: run --login again and finish signing in)'
                elif text and re.search(r'verify you are human|captcha|unusual traffic|access denied', low):
                    why = ' (page shows a bot check: solve it in the window, or wait and retry)'
                print('  ...%s: no owners/residents parsed%s' % (case[-4:], why), flush=True)
                miss += 1
            _save_cache(cache)
            if idx < len(todo) - 1:
                time.sleep(THROTTLE_S + random.uniform(0, 6))
        ctx.close()
    print('scraped %d lead(s), %d with nothing parsed. cache: %s' % (scraped, miss, CACHE))


# cache helpers — reuse whitepages_lookup's file and writer so there is one cache
import whitepages_lookup as _WL  # noqa: E402
CACHE = _WL.CACHE


def _load_cache():
    import json
    if not os.path.exists(CACHE):
        return {}
    try:
        return json.load(open(CACHE, encoding='utf-8')) or {}
    except Exception:
        return {}


def _save_cache(cache):
    _WL._save_cache(cache)


def _clipboard_text():
    """The Windows clipboard as text (PowerShell Get-Clipboard); '' if it cannot be read."""
    import subprocess
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw'],
                             capture_output=True, timeout=20)
        return out.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def assist(todo, refresh=False):
    """HUMAN-IN-THE-LOOP, no automation. For each lead: open its address page in Alejandro's own
    default browser (already signed in, nothing driving it); he presses Ctrl+A, Ctrl+C on the page
    and Enter here; the copied text is parsed into the cache. The site sees an ordinary person on an
    ordinary browser, so there is no bot check to fail. About 15 seconds a lead.
    Answers: Enter = take the clipboard, s = skip this lead, q = quit."""
    import webbrowser
    cache = _load_cache()
    ok = skipped = 0
    for n, lead in enumerate(todo, 1):
        case = _WL._lead_key(lead)
        parts = _WL._split_addr(_WL._lead_addr(lead))
        if not parts or (not refresh and _WL._cache_looked(cache.get(case))):
            continue
        webbrowser.open(_addr_query(*parts))
        try:
            ans = input('[%d/%d] ...%s: page opened. Ctrl+A, Ctrl+C on it, then Enter here '
                        '(s = skip, q = quit) -> ' % (n, len(todo), case[-4:])).strip().lower()
        except EOFError:
            break
        if ans == 'q':
            break
        if ans == 's':
            skipped += 1
            continue
        text = _clipboard_text()
        if _BLOCK_RE.search(text or '') and not parse_people_text(text):
            print('  ...%s: the copied page is a block / verification page, not the address page'
                  % case[-4:])
            skipped += 1
            continue
        people = parse_people_text(text)
        if not people:
            print('  ...%s: nothing parsed from the clipboard (copy the whole page: Ctrl+A, Ctrl+C)'
                  % case[-4:])
            skipped += 1
            continue
        entry = to_cache_entry(people, _WL._lead_addr(lead))
        entry['_src'] = 'wpweb-hand'
        old = cache.get(case) or {}
        for k in ('_person', '_prop_id'):
            if old.get(k) is not None:
                entry[k] = old[k]
        cache[case] = entry
        _save_cache(cache)
        o, r, ph = _counts(entry)
        print('  ...%s: %d owner(s), %d resident(s), %d phone(s) cached' % (case[-4:], o, r, ph), flush=True)
        ok += 1
    print('cached %d lead(s), skipped %d. cache: %s' % (ok, skipped, CACHE))
    return ok


def ingest_file(case, path):
    """No browser at all: parse the visible text of an address page that Alejandro copied from his
    own browser (Ctrl+A, Ctrl+C, saved to a text file) and cache it for `case`. The fallback when the
    site blocks the automated session, and the way to calibrate the parser on a real page."""
    leads = {_WL._lead_key(r): r for r in _WL.load_all_leads()}
    if case not in leads:
        print('case %s not on the board' % case[-6:])
        return 1
    why = _WL.contact_hold(leads[case])
    if why:
        print('...%s: %s, not caching' % (case[-4:], why))
        return 1
    text = open(path, encoding='utf-8', errors='replace').read()
    people = parse_people_text(text)
    if not people:
        print('...%s: no owners/residents parsed from %s' % (case[-4:], path))
        return 1
    entry = to_cache_entry(people, _WL._lead_addr(leads[case]))
    entry['_src'] = 'wpweb-file'
    cache = _load_cache()
    old = cache.get(case) or {}
    for k in ('_person', '_prop_id'):
        if old.get(k) is not None:
            entry[k] = old[k]
    cache[case] = entry
    _save_cache(cache)
    ow, res, ph = _counts(entry)
    print('...%s: %d owner(s), %d resident(s), %d phone(s) cached' % (case[-4:], ow, res, ph))
    return 0


def _select(args):
    leads = _WL.load_all_leads()
    cache = _load_cache()
    skip = _WL.load_skiptrace()
    if args.case:
        by = {_WL._lead_key(r): r for r in leads}
        out = []
        for c in args.case:
            if c not in by:
                sys.exit('case %s not on the board' % c)
            why = _WL.contact_hold(by[c])
            if why:
                print('  SKIP %s: %s' % (c[-4:], why))
                continue
            out.append(by[c])
        return out
    if getattr(args, 'compare', 0):
        # MEASUREMENT BATCH: leads that already carry skip-trace numbers, so each scraped lead can be
        # lined up against what the board has. Same eligibility gates as pick_callable; soonest sale
        # first (pick_callable sorts fewest-phones-first, the opposite of what a comparison wants).
        pool = _WL.pick_callable(leads, cache, 100000, skip)
        pool = [r for r in pool if _WL._lead_phone_count(r, skip) > 0]
        pool.sort(key=lambda r: (_WL._days_to_sale(r), _WL._lead_key(r)))
        return pool[:min(args.compare, MAX_LEADS)]
    n = args.pick_callable or MAX_LEADS
    return _WL.pick_callable(leads, cache, min(n, MAX_LEADS), skip)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--login', action='store_true', help='open the browser to sign in by hand')
    ap.add_argument('--case', action='append', help='lead id (repeatable)')
    ap.add_argument('--pick-callable', type=int, default=0, metavar='N', help='scrape up to N board leads')
    ap.add_argument('--compare', type=int, default=0, metavar='N',
                    help='scrape up to N board leads that already have skip-trace numbers (measurement)')
    ap.add_argument('--plan', action='store_true', help='list the leads it would scrape; no browser')
    ap.add_argument('--refresh', action='store_true', help='re-scrape even cached leads')
    ap.add_argument('--assist', action='store_true',
                    help='hand-assisted, NO automation: opens each page in your own browser, you copy it, Enter')
    ap.add_argument('--from-file', metavar='FILE',
                    help='with ONE --case: cache a page you copied by hand (Ctrl+A, Ctrl+C into a .txt); no browser')
    ap.add_argument('--save-text', metavar='FILE', help='write one lead\'s visible page text (calibration)')
    ap.add_argument('--save-html', metavar='FILE', help='write one lead\'s page HTML (calibration)')
    a = ap.parse_args(argv)

    if a.login:
        _login()
        return 0
    if a.from_file:
        if not a.case or len(a.case) != 1:
            print('--from-file needs exactly one --case')
            return 2
        return ingest_file(a.case[0], a.from_file)
    todo = _select(a)
    if a.assist and not a.plan:
        print('%d lead(s) selected for hand-assisted copy' % len(todo))
        if todo:
            assist(todo, refresh=a.refresh)
        return 0
    print('%d lead(s) selected (cap %d/run, ~%.0fs+jitter between leads)' % (len(todo), MAX_LEADS, THROTTLE_S))
    for r in todo:
        print('  %s  %s' % (_WL._lead_key(r)[-6:], 'has address' if _WL._split_addr(_WL._lead_addr(r)) else 'NO ADDRESS'))
    if a.plan:
        return 0
    if not todo:
        return 0
    _run(todo, save_text=a.save_text, save_html=a.save_html, refresh=a.refresh)
    return 0


if __name__ == '__main__':
    sys.exit(main())
