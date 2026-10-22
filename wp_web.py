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
                                                     # you Ctrl+A / Ctrl+C on it, it caches and moves on
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


_APN_RE = re.compile(r"Assessor'?s Parcel Number\s*\(APN\)\s*([0-9A-Za-z]+(?:-[0-9A-Za-z]+)*)", re.I)


def page_apn(text):
    """The parcel number the address page prints ("... Assessor's Parcel Number (APN) 74-43-...")."""
    m = _APN_RE.search(text or '')
    return m.group(1).strip() if m else ''


def to_cache_entry(people, address, page_text=''):
    """people (parse_people_text) + the searched address string -> a whitepages_lookup.json entry.

    Owners land in ownership_info.person_owners, residents in residents, the same two lists the Pro
    API /property response fills, so wp_contacts.lead_contacts / parcel_owners and the bake grade a
    scraped record exactly like an API one. The page's APN, when printed, goes in `apn` so the
    bake's parcel gate applies; without one, wp_parcel_verdict reads 'ok' and the owner-name bar
    still decides owner vs household, so a wrong-address page cannot turn a stranger into the owner."""
    def rec(p):
        return {'name': p['name'],
                'phones': [dict(x) for x in p['phones']],
                'emails': list(p['emails']),
                'current_addresses': [{'full_address': address}] if address else []}
    owners = [rec(p) for p in people if p['role'] == 'owner']
    residents = [rec(p) for p in people if p['role'] != 'owner']
    res = {'property_address': {'full_address': address}}
    apn = page_apn(page_text)
    if apn:
        # The page names its parcel. Carrying it lets the bake's APN gate (wp_parcel_verdict) drop
        # the property half if Whitepages resolved a different house than our lead's folio.
        res['apn'] = apn
    return {'result': dict(res, **{
                       'ownership_info': {'person_owners': owners},
                       'residents': residents}),
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
    return (to_cache_entry(people, addr, page_text=text) if people else None), text


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
    """The Windows clipboard as text, '' when empty or unreadable. Win32 API through ctypes (no
    process per read, so it can be polled); PowerShell Get-Clipboard as the fallback."""
    if os.name == 'nt':
        try:
            import ctypes
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
            u.GetClipboardData.restype = ctypes.c_void_p
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            if u.OpenClipboard(None):
                try:
                    h = u.GetClipboardData(13)            # CF_UNICODETEXT
                    if not h:
                        return ''
                    ptr = k.GlobalLock(h)
                    try:
                        return ctypes.wstring_at(ptr) if ptr else ''
                    finally:
                        k.GlobalUnlock(h)
                finally:
                    u.CloseClipboard()
        except Exception:
            pass
    import subprocess
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw'],
                             capture_output=True, timeout=20)
        return out.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


# A copied whitepages.com address page carries these; anything else copied is read as "skip".
_PAGE_MARK = re.compile(r'Owners\s*&\s*Residents|Reverse Address|Property Details for|'
                        r'who (owns|lives at) .{0,80}\d{5}', re.I)
ASSIST_WAIT_S = float(os.environ.get('WP_WEB_ASSIST_WAIT_S', '240'))


def _why_empty(text):
    """One line on why a copied address page gave no person, counts only."""
    if _BLOCK_RE.search(text or ''):
        return 'the page is a block / verification page'
    if re.search(r'no people living at this property|no one currently lives at', text or '', re.I):
        return 'Whitepages has no one on file at this address'
    if not _PAGE_MARK.search(text or ''):
        return 'that was not a Whitepages address page'
    if not re.search(r'^\s*(owner|resident)\s*$', text or '', re.I | re.M):
        return 'Whitepages lists no person owner or resident here (business-owned or vacant?)'
    return 'people listed but no phone or email showing (not signed in, or hidden on the page)'


def assist(todo, refresh=False, wait_s=None):
    """HUMAN-IN-THE-LOOP, no automation. For each lead it opens the address page in Alejandro's own
    default browser (already signed in, nothing driving it) and WATCHES THE CLIPBOARD. He presses
    Ctrl+A, Ctrl+C on the page and that is all: the copy is detected, parsed and cached, and the next
    page opens. Nothing is typed or pasted into this window (a paste here feeds the terminal, not
    the script). Copying anything that is not a Whitepages address page skips the lead; Ctrl+C in
    THIS window stops the run. The site sees an ordinary person on an ordinary browser."""
    import webbrowser
    wait_s = ASSIST_WAIT_S if wait_s is None else wait_s
    cache = _load_cache()
    ok = skipped = 0
    reasons = {}
    print('For each page: click it, Ctrl+A, Ctrl+C. Do not paste anything here. '
          'Ctrl+C in THIS window stops.', flush=True)
    try:
        for n, lead in enumerate(todo, 1):
            case = _WL._lead_key(lead)
            parts = _WL._split_addr(_WL._lead_addr(lead))
            if not parts or (not refresh and _WL._cache_looked(cache.get(case))):
                continue
            base = _clipboard_text()
            webbrowser.open(_addr_query(*parts))
            print('[%d/%d] ...%s: page opened, waiting for your copy...' % (n, len(todo), case[-4:]), flush=True)
            text, t0 = '', time.time()
            while time.time() - t0 < wait_s:
                time.sleep(1.0)
                cur = _clipboard_text()
                if cur and cur != base:
                    text = cur
                    break
            if not text:
                print('  ...%s: nothing copied in %ds, skipped' % (case[-4:], wait_s), flush=True)
                skipped += 1
                continue
            _keep_page(case, text)
            people = parse_people_text(text) if _PAGE_MARK.search(text) else []
            if not people:
                why = _why_empty(text)
                reasons[why] = reasons.get(why, 0) + 1
                print('  ...%s: skipped: %s (%d chars copied)' % (case[-4:], why, len(text)), flush=True)
                skipped += 1
                continue
            entry = to_cache_entry(people, _WL._lead_addr(lead), page_text=text)
            entry['_src'] = 'wpweb-hand'
            old = cache.get(case) or {}
            for k in ('_person', '_prop_id'):
                if old.get(k) is not None:
                    entry[k] = old[k]
            cache[case] = entry
            _save_cache(cache)
            o, r, ph = _counts(entry)
            print('  ...%s: %d owner(s), %d resident(s), %d phone(s) cached; parcel %s'
                  % (case[-4:], o, r, ph, _parcel(entry, lead)), flush=True)
            ok += 1
    except KeyboardInterrupt:
        print('\nstopped.')
    print('cached %d lead(s), skipped %d. cache: %s' % (ok, skipped, CACHE))
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        print('  skipped, %s: %d' % (k, v))
    return ok


def _pages_dir():
    import paths as P
    d = os.path.join(P.DEALFLOW_DIR, 'wpweb_pages')     # outside the repo and outside OneDrive
    os.makedirs(d, exist_ok=True)
    return d


def _keep_page(case, text):
    """Every copied page is kept under DEALFLOW_DIR/wpweb_pages so a parse can be re-run or
    calibrated later without opening the site again. Homeowner data: never the repo."""
    try:
        open(os.path.join(_pages_dir(), re.sub(r'[^A-Za-z0-9-]', '_', case) + '.txt'), 'w',
             encoding='utf-8').write(text)
    except Exception:
        pass


def structure(text):
    """The owners/residents part of a copied page with every name, number and email masked, so its
    LAYOUT can be pasted into chat. Names become NAME(n), digits 9, emails EMAIL."""
    lines = [l.strip() for l in str(text or '').replace('\r', '').split('\n')]
    starts = [i for i, l in enumerate(lines) if re.match(r'owners\s*&\s*residents$', l, re.I)]
    i0 = starts[-1] if starts else 0
    out = []
    for l in lines[i0:i0 + 120]:
        if re.match(r'(whitepages property intel|property financials for)', l, re.I):
            break
        m = l
        m = re.sub(r'\S+@\S+', 'EMAIL', m)
        m = re.sub(r'\d', '9', m)
        if _is_name(l):
            m = 'NAME(%d)' % len(l.split())
        out.append(m)
    return '\n'.join(out)


def _parcel(entry, lead):
    """The APN gate's verdict for a cached page against this lead's folio (wp_contacts)."""
    import wp_contacts as C
    apn = ((entry or {}).get('result') or {}).get('apn')
    return C.wp_parcel_verdict(apn, _WL._lead_folio(lead)) if apn else 'no APN on page'


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
    entry = to_cache_entry(people, _WL._lead_addr(leads[case]), page_text=text)
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
                    help='hand-assisted, NO automation: opens each page in your own browser; Ctrl+A, Ctrl+C on it')
    ap.add_argument('--from-file', metavar='FILE',
                    help='with ONE --case: cache a page you copied by hand (Ctrl+A, Ctrl+C into a .txt); no browser')
    ap.add_argument('--structure', metavar='CASE_OR_FILE',
                    help='print a copied page\'s owners/residents layout with names/numbers masked (safe to paste)')
    ap.add_argument('--save-text', metavar='FILE', help='write one lead\'s visible page text (calibration)')
    ap.add_argument('--save-html', metavar='FILE', help='write one lead\'s page HTML (calibration)')
    a = ap.parse_args(argv)

    if a.login:
        _login()
        return 0
    if a.structure == 'clip':
        # Wait for the NEXT copy of a Whitepages page: whatever is on the clipboard now (often the
        # command just pasted to run this) is ignored.
        base, t0 = _clipboard_text(), time.time()
        print('waiting: now go to the Whitepages page in Chrome, click it, Ctrl+A, Ctrl+C ...', flush=True)
        while time.time() - t0 < ASSIST_WAIT_S:
            time.sleep(1.0)
            cur = _clipboard_text()
            if cur and cur != base and _PAGE_MARK.search(cur):
                print(structure(cur))
                return 0
        print('no Whitepages page copied in %ds' % ASSIST_WAIT_S)
        return 1
    if a.structure:
        f = a.structure if os.path.exists(a.structure) else os.path.join(
            _pages_dir(), re.sub(r'[^A-Za-z0-9-]', '_', a.structure) + '.txt')
        if not os.path.exists(f):
            hits = [x for x in os.listdir(_pages_dir()) if x[:-4].endswith(a.structure)]
            f = os.path.join(_pages_dir(), hits[0]) if len(hits) == 1 else f
        if not os.path.exists(f):
            print('no saved page for %s' % a.structure)
            return 1
        print(structure(open(f, encoding='utf-8', errors='replace').read()))
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
