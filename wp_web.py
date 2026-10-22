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
    python wp_web.py --reparse                       # re-read every kept page with the current parser
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
           'phone': 'phone', 'email address': 'email', 'email': 'email'}
_ROLE = {'owner': 'owner', 'resident': 'resident', 'owners & residents': None}
# Letters in any script (José, Muñoz), so an accented name is parsed AND masked by --structure.
_NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\u2019\-])+$")
_PHONE_RE = re.compile(r'\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}')
# A phone VALUE line is the number and nothing else. A parcel number or a help-line sentence after
# the last person must never become that person's phone.
_PHONE_LINE_RE = re.compile(r'^(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}$')
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
            # Only directly under a phone label, and only a line that is just a number.
            if label in ('mobile', 'landline', 'phone') and _PHONE_LINE_RE.match(l):
                d = _digits(l)
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


_DIRS = {'n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw', 'north', 'south', 'east', 'west'}
_UNIT_RE = re.compile(r'(?:\b(?:apt|unit|ste|suite)\b\.?|#)\s*#?\s*([0-9A-Za-z\-]+)', re.I)
_SUFFIXES = {'st', 'ave', 'ct', 'ter', 'dr', 'rd', 'ln', 'pl', 'blvd', 'way', 'cir', 'pkwy', 'hwy',
             'trl', 'street', 'avenue', 'court', 'terrace', 'drive', 'road', 'lane', 'place', 'circle'}
_SUFFIX_SAME = {'st': {'street'}, 'street': {'st'}, 'ave': {'avenue', 'av'}, 'avenue': {'ave', 'av'},
                'ct': {'court'}, 'court': {'ct'}, 'ter': {'terrace'}, 'terrace': {'ter'},
                'dr': {'drive'}, 'drive': {'dr'}, 'rd': {'road'}, 'road': {'rd'}, 'ln': {'lane'},
                'lane': {'ln'}, 'pl': {'place'}, 'place': {'pl'}, 'cir': {'circle'}, 'circle': {'cir'}}
# A number after one of these is the road's own number ("State Road 7", "US Highway 1"): never an
# ordinal, never a unit, and it has to be on the page too.
_ROUTE_WORDS = {'road', 'rd', 'highway', 'hwy', 'sr', 'us', 'route', 'rte', 'cr'}
_DIR_WORDS = {'northwest': 'nw', 'northeast': 'ne', 'southwest': 'sw', 'southeast': 'se',
              'north': 'n', 'south': 's', 'east': 'e', 'west': 'w'}


def page_matches_address(text, street):
    """True when the copied page is about the lead's own street address: one line carries the
    house number, the first street-name word and, for a unit, the unit number. A page for another
    house (a wrong URL hit, or a late copy of the previous lead's page) is refused, so its people
    are never cached as this lead's household."""
    def norm(t):            # "9th" and "9" are the same street; case never matters
        t = re.sub(r'\b(\d+)(st|nd|rd|th)\b', r'\1', t.lower())
        return re.sub(r'\b(%s)\b' % '|'.join(_DIR_WORDS), lambda m: _DIR_WORDS[m.group(1)], t)
    toks = norm(re.sub(r'[^A-Za-z0-9 ]', ' ', _UNIT_RE.sub(' ', str(street or '')))).split()
    if not toks or not toks[0].isdigit():
        return False
    house = toks[0]
    name = next((t for t in toks[1:] if t.lower() not in _DIRS), '')
    suffix = ''
    if name in _SUFFIXES:
        # "1234 West Ave": the street's name IS the direction word; compare it AND the suffix.
        d = next((t for t in reversed(toks[1:toks.index(name)]) if t in _DIRS), '')
        if d:
            name, suffix = d, name
    m = _UNIT_RE.search(street or '')
    unit = m.group(1).lower().replace('-', ' ').strip() if m else ''
    route = next((t for i, t in enumerate(toks) if i > 1 and t.isdigit() and toks[i - 1] in _ROUTE_WORDS), '')
    for l in str(text or '').split('\n'):
        low = ' ' + norm(re.sub(r'[^a-z0-9]', ' ', l.lower())) + ' '
        if (' %s ' % house.lower()) not in low:
            continue
        if name and (' %s ' % name.lower()) not in low:
            continue
        if suffix and not ({suffix} | _SUFFIX_SAME.get(suffix, set())) & set(low.split()):
            continue
        if unit and (' %s ' % unit) not in low:
            continue
        if route and (' %s ' % route) not in low.replace(' %s ' % house.lower(), ' ', 1):
            continue
        return True
    return False


# Only short abbreviations survive masking: a full word (Lane, West, Court) can be a surname.
_KEEP_WORDS = {'apt', 'unit', 'ste', 'bldg', 'st', 'ave', 'ct', 'ter', 'rd', 'ln', 'pl',
               'blvd', 'cir', 'pkwy', 'hwy', 'trl', 'n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'}


def _shape(t):
    """An address with every number as 9s and every street-name word as W: safe to paste."""
    out = []
    for w in re.findall(r'[A-Za-z]+|\d+|[^A-Za-z\d\s]', str(t or '')):
        out.append(re.sub(r'\d', '9', w) if w[0].isdigit() else
                   (w if w.lower() in _KEEP_WORDS or not w.isalpha() else 'W'))
    return ' '.join(out)


def addr_why(text, street):
    """Why page_matches_address refused, plus masked shapes of the lead street and the page's
    address-looking lines. Counts and shapes only."""
    if page_matches_address(text, street):
        return 'match', _shape(street), []
    toks = re.sub(r'[^A-Za-z0-9 ]', ' ', _UNIT_RE.sub(' ', str(street or ''))).split()
    if not toks or not toks[0].isdigit():
        return 'lead street has no house number', _shape(street), []
    house = toks[0]
    lines = [l.strip() for l in str(text or '').split('\n')
             if re.search(r'(^|\D)%s(\D|$)' % re.escape(house), l) and len(l) < 160]
    why = 'house number never on the page' if not lines else 'house number found; street word or unit differs'
    # What the page says it IS: its own title lines, masked the same way.
    heads = [l.strip() for l in str(text or '').split('\n')
             if re.search(r'Property Details for|Looking for who (owns|lives at)|FAQs about|Who lives at', l, re.I)
             and len(l) < 200]
    # The address the page is about: its title, else its first short "<number> <words>" line.
    addr_lines = [l.strip() for l in str(text or '').split('\n')
                  if re.match(r'^\s*\d{1,6}\s+[A-Za-z]', l) and len(l.strip()) < 70
                  and not re.search(r'\b(bed|bath|sq|built|sold|acre|results?|properties)\b', l, re.I)]
    page_addr = heads[0] if heads else (addr_lines[0] if addr_lines else '')
    return why, _shape(street), sorted(set(_shape(l) for l in lines))[:4] + \
        ['(title) ' + _shape(h) for h in heads[:3]] + \
        (['(page is about) ' + _shape(page_addr), '(vs lead) ' + _part_diff(street, page_addr)]
         if page_addr else ['(page names no address)'])


def _part_diff(street, line):
    """Which part of the lead address the page's title line differs in: house / street / unit.
    Says same / DIFFERENT / missing per part, no values, so it can be pasted."""
    def toks(t):
        t = re.sub(r'\b(\d+)(st|nd|rd|th)\b', r'\1', t.lower())
        return re.sub(r'[^a-z0-9]', ' ', t).split()
    lt = toks(_UNIT_RE.sub(' ', street or ''))
    m = _UNIT_RE.search(street or '')
    lunit = m.group(1).lower().replace('-', ' ').strip() if m else ''
    pm = _UNIT_RE.search(line or '')
    punit = pm.group(1).lower().replace('-', ' ').strip() if pm else ''
    pt = toks(line)
    house = 'same' if lt and lt[0] in pt else 'DIFFERENT'
    name = next((t for t in lt[1:] if t not in _DIRS and t not in _DIR_WORDS), '')
    st = 'same' if name and name in pt else 'DIFFERENT'
    unit = ('same' if lunit == punit else 'DIFFERENT') if (lunit or punit) else 'none'
    if lunit and not punit:
        unit = 'lead has a unit, page has none'
    elif punit and not lunit:
        unit = 'page has a unit, lead has none'
    return 'house %s, street %s, unit %s' % (house, st, unit)


def _lead_street(lead):
    parts = _WL._split_addr(_WL._lead_addr(lead))
    return parts[0] if parts else ''


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

def _ordinal(n):
    n = int(n)
    suf = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return '%d%s' % (n, suf)


def _wp_street(street):
    """Our street as Whitepages writes it in its own links (10-22, from real result pages):
    "2929 NW 123 TER" -> "2929 NW 123rd Ter", "8600 NW 34 PL #B105" -> "8600 NW 34th Pl Apt B105",
    "9999 NE 99 AVE 999" (bare trailing unit) -> "... Ave Apt 999". A link in our own spelling
    (no ordinal) falls through to the city's property list instead of the house."""
    m = _UNIT_RE.search(street or '')
    unit = m.group(1) if m else ''
    base = _UNIT_RE.sub(' ', str(street or ''))
    toks = re.sub(r'[^A-Za-z0-9 ]', ' ', base).split()
    if (not unit and len(toks) >= 3 and toks[-1].isdigit() and toks[-2].lower() in _SUFFIXES
            and toks[-2].lower() not in _ROUTE_WORDS):
        unit = toks.pop()                       # "... AVE 999": a unit with no label
    out = []
    for i, t in enumerate(toks):
        low = t.lower()
        if i > 0 and t.isdigit() and toks[i - 1].lower() in _ROUTE_WORDS:
            out.append(t)                       # State Road 7, US Highway 1
        elif i > 0 and t.isdigit():
            out.append(_ordinal(t))             # street names are numbered: 34 -> 34th
        elif (low in _DIRS and len(low) <= 2) or low in ('us', 'sr', 'cr'):
            out.append(t.upper())
        else:
            out.append(t[:1].upper() + t[1:].lower())
    if unit:
        out += ['Apt', unit.upper()]
    return ' '.join(out)


def _addr_query(street, city, state):
    """whitepages.com address URL: /address/<house-street>/<City>-<ST>. UNVERIFIED from here (the
    site is unreachable from the build environment); if it lands on a search page instead of the
    address page, change this one function. Hand-assisted mode still works from any page you copy."""
    import urllib.parse
    def slug(t):
        return '-'.join(re.sub(r"[^A-Za-z0-9 ]", ' ', t).split())
    return '%s/address/%s/%s' % (BASE, urllib.parse.quote(slug(_wp_street(street))),
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
    people = parse_people_text(text) if page_matches_address(text, street) else []
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
                cache = _put(case, entry, cache)
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


def _put(case, entry, cache):
    """Write one entry onto the cache file AS IT IS NOW, not onto the copy loaded when the run
    started, so a lookup another process saved meanwhile is not erased by this run's save. An
    unreadable file falls back to the run's own copy rather than to an empty one."""
    fresh = _load_cache() if os.path.exists(CACHE) else {}
    base = fresh if (fresh or not os.path.exists(CACHE)) else cache
    base[case] = entry
    _save_cache(base)
    return base


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
# The city's property list (where a link Whitepages cannot resolve lands), not one house.
_CITY_LIST_RE = re.compile(r'Property Records\s*\n\s*[\d,]+ of [\d,]+ results', re.I)
ASSIST_WAIT_S = float(os.environ.get('WP_WEB_ASSIST_WAIT_S', '240'))


_WRONG_HOUSE = 'the copied page is for a different address'
_CITY_LIST = "Whitepages showed the city's property list, not the house"
_EMPTY_NOBODY = 'Whitepages has no one on file at this address'
_EMPTY_NO_PERSON = 'Whitepages lists no person owner or resident here (business-owned or vacant?)'


def _set_clipboard(t):
    """Put text on the Windows clipboard (clip.exe). False anywhere else or on failure."""
    try:
        import subprocess
        subprocess.run(['clip'], input=str(t).encode('utf-16-le'), check=True, timeout=10)
        return True
    except Exception:
        return False


def _why_empty(text):
    """One line on why a copied address page gave no person, counts only."""
    if _BLOCK_RE.search(text or ''):
        return 'the page is a block / verification page'
    if re.search(r'no people living at this property|no one currently lives at', text or '', re.I):
        return _EMPTY_NOBODY
    if _CITY_LIST_RE.search(text or ''):
        return _CITY_LIST
    if not _PAGE_MARK.search(text or ''):
        return 'that was not a Whitepages address page'
    if not re.search(r'^\s*(owner|resident)\s*$', text or '', re.I | re.M):
        return _EMPTY_NO_PERSON
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
            text, t0, hinted = '', time.time(), False
            while time.time() - t0 < wait_s:
                time.sleep(1.0)
                cur = _clipboard_text()
                if not cur or cur.strip() == (base or '').strip():
                    continue
                if hinted and not _PAGE_MARK.search(cur):
                    continue                # e.g. the address pasted into the search box, re-copied
                text = cur
                if hinted or not _PAGE_MARK.search(cur) or (
                        page_matches_address(cur, parts[0]) and not _CITY_LIST_RE.search(cur)):
                    break
                # Whitepages sent the address URL to some other property. Give one more try for this
                # lead: put its address on the clipboard (never printed, the terminal gets pasted
                # into chat) so it can be pasted into the site's own search box.
                hinted = True
                addr = _WL._lead_addr(lead)
                if _set_clipboard(addr):
                    base = addr
                    print('  ...%s: that page is not this house. This lead\'s address is now on '
                          'your clipboard: paste it into the Whitepages search box, open the property, '
                          'then Ctrl+A, Ctrl+C.' % case[-4:], flush=True)
                else:
                    break
            if not text:
                print('  ...%s: nothing copied in %ds, skipped' % (case[-4:], wait_s), flush=True)
                skipped += 1
                continue
            same = page_matches_address(text, parts[0]) and not _CITY_LIST_RE.search(text)
            if same and _PAGE_MARK.search(text):
                _keep_page(case, text)      # only this lead's own Whitepages page replaces its copy
            elif _PAGE_MARK.search(text):
                _keep_page(case, text, rejected=True)   # kept apart, for --addr-check only
            people = parse_people_text(text) if same and _PAGE_MARK.search(text) else []
            if not people:
                why = _why_empty(text) if same or not _PAGE_MARK.search(text) or _CITY_LIST_RE.search(text) \
                    else _WRONG_HOUSE
                reasons[why] = reasons.get(why, 0) + 1
                if why in (_WRONG_HOUSE, _CITY_LIST):
                    _note_attempt(case)     # only "Whitepages has no page for it" counts as a miss
                print('  ...%s: skipped: %s (%d chars copied)' % (case[-4:], why, len(text)), flush=True)
                skipped += 1
                continue
            entry = to_cache_entry(people, _WL._lead_addr(lead), page_text=text)
            entry['_src'] = 'wpweb-hand'
            old = cache.get(case) or {}
            for k in ('_person', '_prop_id'):
                if old.get(k) is not None:
                    entry[k] = old[k]
            cache = _put(case, entry, cache)
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


def _keep_page(case, text, rejected=False):
    """Every copied page is kept under DEALFLOW_DIR/wpweb_pages so a parse can be re-run or
    calibrated later without opening the site again. Homeowner data: never the repo."""
    try:
        d = _pages_dir()
        if rejected:                # a page that was NOT this lead's house: never read as its data
            d = os.path.join(d, 'rejected')
            os.makedirs(d, exist_ok=True)
        open(os.path.join(d, re.sub(r'[^A-Za-z0-9-]', '_', case) + '.txt'), 'w',
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


def ingest_file(case, path, refresh=False):
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
    cache = _load_cache()
    if not refresh and _WL._cache_looked(cache.get(case)):
        print('...%s: already looked up, not replacing it (use --refresh)' % case[-4:])
        return 1
    text = open(path, encoding='utf-8', errors='replace').read()
    if not page_matches_address(text, _lead_street(leads[case])):
        print('...%s: that page is for a different address, not caching' % case[-4:])
        return 1
    people = parse_people_text(text)
    if not people:
        print('...%s: no owners/residents parsed from %s' % (case[-4:], path))
        return 1
    _keep_page(case, text)
    entry = to_cache_entry(people, _WL._lead_addr(leads[case]), page_text=text)
    entry['_src'] = 'wpweb-file'
    old = cache.get(case) or {}
    for k in ('_person', '_prop_id'):
        if old.get(k) is not None:
            entry[k] = old[k]
    _put(case, entry, cache)
    ow, res, ph = _counts(entry)
    print('...%s: %d owner(s), %d resident(s), %d phone(s) cached' % (case[-4:], ow, res, ph))
    return 0


def reparse():
    """Re-read every page this tool cached (entries marked _src wpweb*) from the copies kept in
    DEALFLOW_DIR/wpweb_pages with the CURRENT parser and address check. An entry whose page fails
    the check, parses to nobody, or has no kept copy is removed (the lead reads "not looked up"
    again). Pro API entries are never touched, and an entry that also carries a paid Person layer
    (`_person` / `_prop_id`) keeps it: only the page half is dropped. An entry with no kept page, or
    whose lead is not in the lead files, is left as it is and counted. Counts only."""
    leads = {_WL._lead_key(r): r for r in _WL.load_all_leads()}
    if not leads:
        print('the lead files read as empty; not touching the cache')
        return 1
    cache = _load_cache()
    kept = rebuilt = dropped = left = 0
    why = {}
    for case in [c for c, e in cache.items() if str((e or {}).get('_src', '')).startswith('wpweb')]:
        old = cache[case]
        f = os.path.join(_pages_dir(), re.sub(r'[^A-Za-z0-9-]', '_', case) + '.txt')
        lead = leads.get(case)
        reason = ''
        if not os.path.exists(f) or lead is None:
            k = 'no kept copy of the page' if not os.path.exists(f) else 'lead not in the lead files'
            why['left as is, ' + k] = why.get('left as is, ' + k, 0) + 1
            left += 1
            continue
        else:
            text = open(f, encoding='utf-8', errors='replace').read()
            if not page_matches_address(text, _lead_street(lead)):
                reason = 'page is for a different address'
            else:
                people = parse_people_text(text)
                if not people:
                    reason = 'no person with a phone or email'
                else:
                    entry = to_cache_entry(people, _WL._lead_addr(lead), page_text=text)
                    entry['_src'] = old.get('_src')
                    for k in ('_person', '_prop_id', '_ts'):
                        if old.get(k) is not None:
                            entry[k] = old[k]
                    if _counts(entry) == _counts(old) and entry['result'] == old.get('result'):
                        kept += 1
                    else:
                        rebuilt += 1
                    cache[case] = entry
                    continue
        paid = {k: old[k] for k in ('_person', '_prop_id') if old.get(k) is not None}
        if paid:
            cache[case] = dict(paid, _ts=old.get('_ts'))   # keep what was paid for, drop the page half
        else:
            del cache[case]
        dropped += 1
        why['removed, ' + reason] = why.get('removed, ' + reason, 0) + 1
    _save_cache(cache)
    print('re-read %d page(s): %d unchanged, %d rebuilt, %d removed; %d left as is'
          % (kept + rebuilt + dropped, kept, rebuilt, dropped, left))
    for k, v in sorted(why.items(), key=lambda x: -x[1]):
        print('  %s: %d' % (k, v))
    return 0


ATTEMPT_DAYS = 7


def _attempts_path():
    return os.path.join(_pages_dir(), '_attempts.json')


def _attempts():
    import json
    try:
        return json.load(open(_attempts_path(), encoding='utf-8')) or {}
    except Exception:
        return {}


def _note_attempt(case):
    """A copy that gave nothing usable for this lead (wrong house, city list, not a page, timeout)."""
    import json
    a = _attempts()
    now = time.time()
    a[case] = [t for t in a.get(case, []) if now - t < ATTEMPT_DAYS * 86400] + [now]
    try:
        json.dump(a, open(_attempts_path(), 'w', encoding='utf-8'))
    except Exception:
        pass


def _tried_out(case, att):
    """Two misses in the last week: stop offering it (Whitepages has no page for that address)."""
    now = time.time()
    return len([t for t in att.get(case, []) if now - t < ATTEMPT_DAYS * 86400]) >= 2


def _fresh(pool, cache, refresh):
    """Leads a run should open: not already cached, not a known-empty page, not tried out."""
    if refresh:
        return pool
    att = _attempts()
    return [r for r in pool if not _WL._cache_looked(cache.get(_WL._lead_key(r)))
            and not _known_empty(r) and not _tried_out(_WL._lead_key(r), att)]


def _known_empty(lead):
    """True when a page already copied for this lead is its own address page and lists nobody with
    a phone or email (business-owned, vacant, nobody on file). Re-opening it would only cost another
    copy, so selection leaves it out unless --refresh."""
    f = os.path.join(_pages_dir(), re.sub(r'[^A-Za-z0-9-]', '_', _WL._lead_key(lead)) + '.txt')
    if not os.path.exists(f):
        return False
    try:
        text = open(f, encoding='utf-8', errors='replace').read()
    except Exception:
        return False
    # Only a page that lists NO ONE. A signed-out page still lists people (no phones showing) and
    # must stay selectable, or the lead would vanish from every later run.
    return (_why_empty(text) in (_EMPTY_NOBODY, _EMPTY_NO_PERSON)
            and page_matches_address(text, _lead_street(lead)) and not parse_people_text(text))


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
    if getattr(args, 'retry_misses', False):
        # Only the leads that came up "not this house" / city list: to see why (--addr-check after).
        att = _attempts()
        pool = [r for r in _WL.pick_callable(leads, cache, 100000, skip)
                if att.get(_WL._lead_key(r)) and not _WL._cache_looked(cache.get(_WL._lead_key(r)))]
        return pool[:MAX_LEADS]
    if getattr(args, 'compare', 0):
        # MEASUREMENT BATCH: leads that already carry skip-trace numbers, so each scraped lead can be
        # lined up against what the board has. Same eligibility gates as pick_callable; soonest sale
        # first (pick_callable sorts fewest-phones-first, the opposite of what a comparison wants).
        pool = _WL.pick_callable(leads, cache, 100000, skip)
        pool = [r for r in pool if _WL._lead_phone_count(r, skip) > 0]
        pool = _fresh(pool, cache, getattr(args, 'refresh', False))
        pool.sort(key=lambda r: (_WL._days_to_sale(r), _WL._lead_key(r)))
        return pool[:min(args.compare, MAX_LEADS)]
    n = args.pick_callable or MAX_LEADS
    pool = _fresh(_WL.pick_callable(leads, cache, 100000, skip), cache, getattr(args, 'refresh', False))
    return pool[:min(n, MAX_LEADS)]


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
    ap.add_argument('--retry-misses', action='store_true',
                    help='with --assist: reopen only the leads that came up "not this house"')
    ap.add_argument('--addr-check', action='store_true',
                    help='for every kept page, say why its address did or did not match (masked shapes)')
    ap.add_argument('--reparse', action='store_true',
                    help='re-read every page this tool cached with the current parser; no browser')
    ap.add_argument('--save-text', metavar='FILE', help='write one lead\'s visible page text (calibration)')
    ap.add_argument('--save-html', metavar='FILE', help='write one lead\'s page HTML (calibration)')
    a = ap.parse_args(argv)

    if a.login:
        _login()
        return 0
    if a.reparse:
        return reparse()
    if a.addr_check:
        leads = {_WL._lead_key(r): r for r in _WL.load_all_leads()}
        for d, tag in ((_pages_dir(), ''), (os.path.join(_pages_dir(), 'rejected'), ' [rejected copy]')):
            if not os.path.isdir(d):
                continue
            for f in sorted(x for x in os.listdir(d) if x.endswith('.txt')):
                lead = leads.get(f[:-4])
                if lead is None:
                    print('...%s:%s lead not in the lead files' % (f[-8:-4], tag))
                    continue
                why, ls, ps = addr_why(open(os.path.join(d, f), encoding='utf-8', errors='replace').read(),
                                       _lead_street(lead))
                if tag and why == 'match':
                    continue
                print('...%s:%s %s | lead: %s' % (f[-8:-4], tag, why, ls))
                for p in ps:
                    print('        page: %s' % p)
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
        return ingest_file(a.case[0], a.from_file, refresh=a.refresh)
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
