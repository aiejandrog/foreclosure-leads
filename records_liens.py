"""Pillar 1 — automate the recorded mortgage/lien pull so the equity number stops being a guess.

For each lead's owner, pull the Miami-Dade Official Records chain, match SATISFACTION docs to their
MORTGAGE, and surface the OPEN (unsatisfied) mortgages on the subject folio — i.e. the hidden 2nd that
made Hondroulis's "$655k equity" a fantasy. Output -> records_liens.json (gitignored), keyed by Case #.

Reliability trick: the gated part is only the *search* (standardsearch POST). The RESULTS fetch
(getStandardRecords GET) is NOT gated, so a valid search token (qs) is the entire cost of an owner.

TOKEN SOURCES, in the order tried (2026-08-22):

  1. CACHED qs      records_qs.json — plain requests, no browser, free and instant.
  2. CAMOUFOX       drives the real search UI; Turnstile runs invisible/managed here and hands an
                    anti-detect browser a token unprompted. FREE. Measured 4/4 (CAMOUFOX-EVAL.md);
                    vanilla headless chromium gets nothing on the identical flow, so this is not
                    "any browser works". ~17s per owner. The captured qs is written back to
                    records_qs.json, so each owner costs that once and lands on path 1 afterwards.
  3. 2CAPTCHA       fetch_via_turnstile — ~$0.003 and ~6s per solve. Faster than Camoufox but not
                    free. Kept as the fallback for the day the county stops being generous, and
                    reachable directly with --no-camoufox.
  4. mint_and_fetch legacy Playwright reCAPTCHA-v3 mint. Effectively dead — the site migrated off
                    the sitekey it was built for — and skipped silently when its JS template is gone.

So the trade is time for money: Camoufox is ~3x slower per uncached owner than a 2Captcha solve, but
costs nothing and compounds into the cache. On a backlog that matters; once records_qs.json is warm,
most owners never reach step 2 at all.

Usage:
  python records_liens.py --case 2024-023366-CA-01     # one lead (prove it)
  python records_liens.py --tier A                      # a tier
  python records_liens.py --all --cached-only           # everyone we already have a token for (fast, no browser)
  python records_liens.py --all                         # everyone; free Camoufox tokens for the rest
  python records_liens.py --all --no-camoufox           # skip Camoufox, buy tokens from 2Captcha
"""
import argparse, datetime, json, os, re, time, urllib.parse


def _parse_recd(s):
    """Parse a Miami-Dade recorded-date string into a real date.

    The clerk emits `M/D/YYYY` (with an occasional trailing time slice, e.g. '2/1/2002 1'), which
    means string comparison `'1/10/2006' >= '10/31/2006'` is True — the day-in-January reads as
    NEWER than the day-in-October and reversed classifications of every lien pair whose months
    started with different digits. Return None on unreadable input so a comparison with a real date
    is False either way; callers must guard on both operands existing before ordering.
    """
    parts = (s or '').split()
    s = parts[0].strip() if parts else ''
    if not s:
        return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
QS_CACHE = os.path.join(HERE, 'records_qs.json')      # owner_clean -> search token (from gen_records_qs.py)
OUT = os.path.join(HERE, 'records_liens.json')         # Case # -> lien result  (gitignored)
OR_BASE = 'https://onlineservices.miamidadeclerk.gov/officialrecords/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|UNITED STATES|COUNTY|CITY OF)\b', re.I)
SITE_KEY = '6LfI8ikaAAAAAH0qlQMApskMGd1U6EqDyniH5t0x'   # legacy reCAPTCHA (DEAD — site migrated)
# Miami-Dade Official Records migrated from reCAPTCHA v3 to Cloudflare TURNSTILE (running in
# reCAPTCHA-compat mode, so the token still rides the x-recaptcha-token header). This is the live
# Turnstile sitekey; 2Captcha solves it in ~6s and the search accepts it. Verified 2026-07-21.
TS_SITE_KEY = '0x4AAAAAAD1vWBs-1bsZ5Z5M'

S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept': 'application/json', 'Referer': OR_BASE})


def norm_folio(s):
    return re.sub(r'\D', '', str(s or '')).lstrip('0')

def num(x):
    try: return float(x or 0)
    except Exception: return 0

def split_owner(clean):
    # kimi: companies have no LAST/FIRST — pass the full name as one partyName token string
    # (the clerk search tokenizes it). analyze() isolates by folio+subdivision, never by name.
    if COMPANY_RE.search(clean or ''):
        return (clean.strip(), '')
    toks = [t for t in (clean or '').split() if len(t.strip('.')) > 1]
    if len(toks) < 2:
        return None
    # MD owner_clean is FIRST [MIDDLE] LAST. The clerk indexes SURNAME-FIRST, and its name search is
    # order-sensitive: for "MARIE FLORETTE FLEURIMOND" the old ' '.join(toks[1:]) made the surname
    # "FLORETTE FLEURIMOND" and returned ZERO — searching the true surname (last token) first
    # returned 120 docs. So: surname = LAST token, given = everything before it. (2-token names are
    # unchanged: "EDUARDO ECHEVERRI" -> surname ECHEVERRI, given EDUARDO.)
    return (toks[-1], ' '.join(toks[:-1]))   # (SURNAME, GIVEN)


# A government body or a bare street address is not a party whose mortgage chain means anything.
# Both still cost a mint attempt every run and both come back empty — measured on this board:
# CITY OF NORTH MIAMI BEACH FLORIDA, STATE OF FLORIDA'S DEPARTMENT OF REVENUE and
# UNITED STATES OF AMERICA - DEPARTMENT OF HOUSING all traced to 0 open mtg / conf=None, and
# '10867 NW 59TH ST DORAL FL 33178' never resolved at all.
#
# THE TRAP, and why this is deliberately narrow: most address-shaped owners on this board are REAL
# entities named after their address — 5838 ALTON ROAD LLC, 13925 OLD CUTLER ROAD LLC,
# 6828 NW 3 AVE LLC — and every one of the 20 owners containing digits is a live company. Companies
# are worth tracing: 63 of them have been traced and 8 came back carrying an open mortgage. So a
# company suffix ALWAYS wins; the address rule only fires on a name that has no entity marker at all.
COMPANY_SUFFIX_RE = re.compile(
    r'\b(LLC|L\.L\.C|CORP|INC|TRUST|ASSOC|ASSN|COMPANY|HOLDINGS|GROUP|PARTNERS|VENTURES'
    r'|INVESTMENTS|PROPERTIES|REALTY|MANAGEMENT|LP|LLP|LTD|PA|PLLC)\b', re.I)
GOV_RE = re.compile(
    r'(\bCITY OF\b|\bSTATE OF\b|\bCOUNTY OF\b|\bUNITED STATES\b|\bDEPARTMENT OF\b|\bDEPT OF\b'
    r'|\bCOUNTY\s*$|\bTAX COLLECTOR\b|\bCLERK OF\b|\bSHERIFF\b)', re.I)
STREET_RE = re.compile(
    r'\b(ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|BLVD|CT|COURT|LN|LANE|WAY|TER|TERR|PL|PLACE'
    r'|HWY|CIR|CIRCLE|PKWY|APT|UNIT|STE)\b', re.I)


def untraceable_owner(clean):
    """Reason string when this owner_clean is not worth a search token, else ''.

    Checked BEFORE a lead is picked, so junk never reaches a mint — free or paid.
    """
    s = (clean or '').strip()
    if not s:
        return 'empty'
    if COMPANY_SUFFIX_RE.search(s):
        return ''                                   # a company is a real party — always trace it
    if GOV_RE.search(s):
        return 'government body'
    # bare street address: starts with a house number AND carries a street word, no entity marker
    if re.match(r'^\d', s) and STREET_RE.search(s):
        return 'street address, not a name'
    return ''


# ---- fetch the owner's recorded documents -----------------------------------------------------
def records_by_qs(qs):
    try:
        r = S.get(OR_BASE + 'api/SearchResults/getStandardRecords?qs=' + qs, timeout=30)
        if r.status_code != 200:
            return None
        return (r.json() or {}).get('recordingModels') or []
    except Exception:
        return None


# ---- the paid-solve cap ------------------------------------------------------------------------
# 2026-09-25: Alex set $5 for Miami data reads, lien re-pulls first. `paid` below counted OWNERS that
# reached 2Captcha, but one owner can cost three solves (tries=3) and the defendant fallback adds two
# more owners, so it was never a spend figure and nothing stopped at a number. --max-spend now counts
# every submitted solve at the measured owner-search price and refuses the next one that would pass
# the cap; the account balance is re-read every 20 solves in case the price moved. --spend-ledger
# makes the cap a TOTAL across runs: a second run against the same ledger gets only what is left,
# and can lower the ledger's cap but never raise it.
PAID_SOLVE_USD = 0.0033          # measured per owner-search token, 2026-09 (2Captcha lists ~$0.003)
MAX_SPEND_CEILING = 5.00         # the most any one run may be given, whatever --max-spend says
_SPEND = {'cap': None, 'submits': 0, 'bal0': None, 'prior': 0.0, 'ledger': None, 'stopped': ''}


def _usd(x):
    return ('%.2f' % x) if abs(x * 100 - round(x * 100)) < 1e-9 else ('%.4f' % x)


def _balance():
    try:
        from captcha_solver import balance
        v = balance()
        return float(v) if v not in (None, '') else None
    except Exception:
        return None


def _may_submit():
    """True when one more paid solve fits under --max-spend. Sticky: once it says no, it stays no."""
    cap = _SPEND['cap']
    if cap is None:
        return True
    if _SPEND['stopped']:
        return False
    if _SPEND.get('lock'):
        if not _lock_mine():
            # asleep past the stale age and another run took the ledger over: its total is not ours
            _SPEND['stopped'] = 'another run took over the spend ledger'
            return False
    unit = _SPEND.get('unit') or PAID_SOLVE_USD
    if _SPEND['prior'] + (_SPEND['submits'] + 1) * unit > cap + 1e-9:
        _SPEND['stopped'] = 'counted solves reached the $%s cap' % _usd(cap)
        return False
    if _SPEND['submits'] and _SPEND['submits'] % 20 == 0:
        b = _balance()
        if b is None:
            _SPEND['stopped'] = '2Captcha balance could not be re-read'
            return False
        # the balance says what a solve really costs: count every later one at that price, so the
        # 19 solves before the next re-read cannot run past the cap on a stale estimate
        _SPEND['unit'] = unit = max(PAID_SOLVE_USD, (_SPEND['bal0'] - b) / _SPEND['submits'])
        if _SPEND['prior'] + max(_SPEND['bal0'] - b, _SPEND['submits'] * unit) + unit > cap + 1e-9:
            _SPEND['stopped'] = 'the account balance fell by the $%s cap' % _usd(cap)
            return False
    return True


def _parcel_in(models, folio):
    """Does a search result carry this folio at all? analyze()'s parcel_found, without the analysis."""
    fol = norm_folio(folio)
    return bool(models) and bool(fol) and any(norm_folio(r.get('foliO_NUMBER', '')) == fol for r in models)


def _mortgages_narrower(old, new):
    """Does `new` miss mortgage debt `old` recorded? An OPEN loan the new rows do not show open or
    released by a satisfaction that names its book/page, more unpriced loans before than now, or a
    lender's second foreclosure the new read does not see. Any of these means the new read is
    narrower (or its release rules guessed from a namesake's satisfaction), not news of a payoff."""
    now = {str(l.get('bp') or '') for l in (new.get('liens') or []) if isinstance(l, dict)
           and (str(l.get('st') or 'OPEN').upper() == 'OPEN' or l.get('sat_by') == 'book/page')}
    for l in old.get('liens') or []:
        if isinstance(l, dict) and str(l.get('st') or 'OPEN').upper() == 'OPEN':
            if not l.get('bp') or str(l['bp']) not in now:
                return True
    if (old.get('mtg_open_unpriced') or 0) > (new.get('mtg_open_unpriced') or 0):
        return True
    if (old.get('second_fc') or old.get('second_fc_unsure')) and not (new.get('second_fc') or new.get('second_fc_unsure')):
        return True
    return False


def _carry_lien_totals(old, new, out):
    """A lien total never goes down on a narrower re-read (a spouse's judgment the surname-only search
    found). But a chain the old analyzer wrote (no 'other' rows) summed this case's own judgment, the
    plaintiff association's own claim of lien and notices into these totals: the error this re-read
    exists to fix. When the re-read examined at least as many records as the old search said it did,
    its totals stand. Otherwise (narrower, or an old chain that never said) the larger figure is kept,
    less what the re-read shows was this case's own claim, and the chain says so."""
    out.pop('lien_totals_kept', None)
    out.pop('hoa_own_in', None)
    legacy = 'other' not in old
    if legacy and old.get('nrec') and (new.get('nrec') or 0) >= old['nrec']:
        for k in ('hoa_open', 'code_open', 'irs_open'):
            out[k] = new.get(k) or 0
        return
    own = {'hoa_open': 0, 'code_open': 0, 'irs_open': 0}
    claim_out = False                   # the plaintiff's own claim of lien was taken out of hoa_open
    if legacy:
        _tr = str(old.get('traced') or '')
        for o in new.get('other') or []:
            if isinstance(o, dict) and o.get('own_case') and o.get('amt'):
                # only a claim the old search could have seen: recorded on or before its trace
                _d = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', str(o.get('d') or ''))
                if _tr and _d and '%s-%02d-%02d' % (_d.group(3), int(_d.group(1)), int(_d.group(2))) > _tr:
                    continue
                k = o.get('old_bucket')
                if k not in own:
                    continue                                # the old analyzer never summed it
                own[k] += o.get('old_amt') or o['amt']       # the unrounded figure the old code summed
                if k == 'hoa_open' and re.search(r'\bLIEN\b', str(o.get('doc') or ''), re.I) \
                        and not re.search(r'JUDG|LIS PENDENS', str(o.get('doc') or ''), re.I):
                    claim_out = True
    for k in own:
        # subtracted only when the old total is at least the claim: a smaller total never held it,
        # and taking it out would erase a real lien the re-read did not reach
        _o = old.get(k) or 0
        _left = _o - own[k] if _o + 1 >= own[k] else _o         # a dollar of rounding is the same claim
        out[k] = max(round(_left, 2) if _left >= 1 else 0, new.get(k) or 0)
    if legacy and any(out[k] > (new.get(k) or 0) for k in own):
        out['lien_totals_kept'] = ('lien totals from the earlier, wider search (%s records) kept; they may '
                                   'include this case\'s own judgment' % (old.get('nrec') or '?'))
    if legacy and out['hoa_open'] > (new.get('hoa_open') or 0) and not (claim_out and own['hoa_open']):
        # the association total kept is the old analyzer's, which summed the plaintiff's own claim:
        # the board must keep netting the judgment against it. Not when the re-read found that claim:
        # either it was taken out above, or the old total was smaller than it and never held it.
        out['hoa_own_in'] = True


def _lay_lien_rows(old, new):
    """A narrower re-read (_mortgages_narrower) changes nothing the board counts. The old chain stands
    whole: its mortgages, its totals, its type and survival. What the new read found that the old chain
    does not hold rides along under 'other_seen' (its lien rows, and any open mortgage the old chain
    lacks), for a person to read and never summed, and the chain is flagged for a wider re-pull.
    'other' stays absent, so the chain is still picked by the next --reanalyze / --repull."""
    out = dict(old)
    _have = {str(l.get('bp') or '') for l in old.get('liens') or [] if isinstance(l, dict)}
    seen = [{k: v for k, v in o.items() if not k.startswith('_')}
            for o in new.get('other') or [] if isinstance(o, dict)]
    seen += [dict({k: v for k, v in l.items() if not k.startswith('_')}, kind='mortgage')
             for l in new.get('liens') or [] if isinstance(l, dict) and l.get('bp')
             and str(l['bp']) not in _have and str(l.get('st') or 'OPEN').upper() == 'OPEN']
    out['other_seen'] = seen
    _fc = new.get('second_fc') or new.get('second_fc_unsure')
    out['wider_repull'] = ("%s: a re-read searched as %s did not reach every mortgage the earlier search "
                           "found (searched as %s); the earlier chain is kept whole and the %d row(s) this "
                           "re-read found are listed, not counted%s"
                           % (time.strftime('%Y-%m-%d'), new.get('searched_as') or '?',
                              old.get('searched_as') or old.get('owner') or '?', len(seen),
                              ('; it also saw a lender\'s foreclosure (%s)'
                               % ((_fc.get('party') if isinstance(_fc, dict) else _fc) or '?')) if _fc else ''))
    return out


def _ledger_lock(path):
    """One paying run per ledger. A second run would read the same 'already spent' and both could
    spend what is left. A lock older than 12 hours is a crashed run's and is taken over."""
    lock = path + '.lock'
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            _SPEND['lock_id'] = '%d %s %s' % (os.getpid(), time.strftime('%Y-%m-%d %H:%M:%S'), os.urandom(4).hex())
            os.write(fd, _SPEND['lock_id'].encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 12 * 3600:
                    # take a crashed run's lock over by RENAMING it: only one of two runs starting
                    # together can move it, and the loser then meets the winner's fresh lock
                    stale = '%s.stale-%s' % (lock, os.urandom(4).hex())
                    os.rename(lock, stale)
                    try:
                        os.remove(stale)
                    except OSError:
                        pass
                    continue
            except OSError:
                continue                                        # someone else moved it: look again
            return None
    return None


def _ledger_open(path, cap):
    """(cap, already counted) from a --spend-ledger file; the smaller cap wins."""
    try:
        led = json.load(open(path, encoding='utf-8'))
    except FileNotFoundError:
        led = {}
    if not isinstance(led, dict):
        raise ValueError('spend ledger %s is not a JSON object' % path)
    cap = min(cap, float(led.get('cap', cap)))
    return cap, float(led.get('counted_usd', 0) or 0), led


def _lock_mine():
    """Does this run still hold the ledger lock (no other run took it over as stale)?"""
    try:
        return open(_SPEND['lock'], encoding='utf-8').read() == _SPEND.get('lock_id')
    except OSError:
        return False


def _lock_touch():
    """Keep the ledger lock fresh, so a long run is never taken for a crashed one."""
    if _SPEND.get('lock'):
        try:
            os.utime(_SPEND['lock'], None)
        except OSError:
            pass


def _ledger_save(charged=None, final=False):
    path = _SPEND['ledger']
    if not path:
        return
    if _SPEND.get('lock') and not _lock_mine():
        # another run took the ledger over while this one slept: its file holds that run's spend on
        # top of ours, and writing our stale copy back would erase it (and hand its money out again)
        _SPEND['stopped'] = 'another run took over the spend ledger'
        print('  ! spend ledger %s: another run holds it now; this run\'s record is left to it' % path)
        return
    _lock_touch()
    run = _SPEND['submits'] * (_SPEND.get('unit') or PAID_SOLVE_USD)
    total = _SPEND['prior'] + max(run, charged or 0)
    # the end-of-run balance drop is a price too: a run of under 20 solves never re-reads the
    # balance mid-run, and must still teach the next run what a solve really cost
    seen = (charged / _SPEND['submits']) if (charged and _SPEND['submits']) else 0
    led = dict(_SPEND.get('led') or {}, cap=_SPEND['cap'], counted_usd=round(total, 4),
               unit_usd=round(max(float((_SPEND.get('led') or {}).get('unit_usd') or 0),
                                  _SPEND.get('unit') or PAID_SOLVE_USD, seen), 6))
    if final:
        led['runs'] = list(led.get('runs') or []) + [{'at': time.strftime('%Y-%m-%d %H:%M'),
                                                     'solves': _SPEND['submits'], 'counted_usd': round(run, 4),
                                                     'charged_usd': None if charged is None else round(charged, 4)}]
        _SPEND['led'] = led
    tmp = path + '.tmp'
    for i in range(6):                     # Windows: an indexer or AV scan can hold the file a moment
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(led, f, indent=1)
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.25 * (i + 1))
    # the spend can no longer be written down, so no more of it happens
    _SPEND['stopped'] = 'the spend ledger %s could not be written' % path
    print('  ! %s' % _SPEND['stopped'])


def fetch_via_turnstile(owner_lf, tries=3):
    """THE UNLOCK — pull an owner's recorded docs by solving Cloudflare Turnstile (2Captcha), no
    browser. Solves the token, POSTs the same standardsearch the app uses with it in the
    x-recaptcha-token header, then the (ungated) getStandardRecords GET. Returns models or None.
    owner_lf = (LAST..., FIRST) or (COMPANY, '')."""
    try:
        from captcha_solver import solve_turnstile
    except Exception:
        return None
    # THE MD CLERK ACCEPTS ONE WORD. Any name with a space (`WHITE SHUROD`) or comma (`WHITE, SHUROD`)
    # returns `{isValidSearch:false, qs:null}` with the token accepted. Verified with a $12 wallet
    # and a fresh solve per test: only `WHITE` alone succeeded. That is why 41 of 43 leads couldn't
    # be pulled today. Submit the last name only; the downstream analyzer already isolates by folio,
    # so a broad last-name pool is safe. Companies stay whole (owner_lf[1] is empty for them).
    party = owner_lf[0].strip()
    url = (OR_BASE + 'api/home/standardsearch?partyName=' + urllib.parse.quote(party)
           + '&dateRangeFrom=&dateRangeTo=&documentType=&searchT=&firstQuery=y&searchtype='
           + urllib.parse.quote('Name/Document'))
    for _ in range(max(1, tries)):
        if not _may_submit():
            return None
        _SPEND['submits'] += 1                  # counted on submit: a failed solve may still bill
        _ledger_save()                          # before the solve, so a crash cannot forget it
        if _SPEND['stopped']:
            return None                         # the ledger could not be written: no unrecorded solve
        tok = solve_turnstile(TS_SITE_KEY, OR_BASE)
        if not tok:
            continue
        try:
            r = S.post(url, headers={'x-recaptcha-token': tok,
                                     'content-type': 'application/json; charset=utf-8'},
                       data='', timeout=30)
            j = r.json()
        except Exception:
            continue
        qs = j.get('qs') if isinstance(j, dict) else None
        if qs:
            return records_by_qs(qs)
        # isValidSearch:false with a fresh token = a bad solve; loop and re-solve
        time.sleep(1)
    return None

# ---- Camoufox: let the browser mint its own Turnstile token, for free ------------------------
# Measured 2026-08-22 (CAMOUFOX-EVAL.md): Turnstile runs invisible/managed on this site, so it hands
# a browser it considers legitimate a token with no interaction. Camoufox got one on 4/4 trials
# (666-688 chars) and pulled 42 records for HONDROULIS. Vanilla headless chromium got NOTHING on the
# same flow — twice the challenges.cloudflare.com traffic and an empty x-recaptcha-token — so this is
# specific to the anti-detect build, not "any browser works now".
#
# It has to drive the real UI. POSTing api/home/standardsearch cold does not work: Turnstile only
# executes as part of a search interaction, so on a freshly loaded page there is no widget and no
# token. The search box itself lives behind the sidebar's Standard Search -> Name/Document.
#
# We do not read the rendered results. We capture the `qs` off the app's OWN getStandardRecords
# request and hand it to records_by_qs(), so every existing parser downstream is untouched — and the
# qs gets cached, which puts the next run on the free plain-requests path.

CF_UNAVAILABLE = None          # set to a reason string once, so we do not retry a missing import


def camoufox_session():
    """One browser for a whole batch. Launching per-owner would dominate the runtime at --limit 60."""
    global CF_UNAVAILABLE
    if CF_UNAVAILABLE:
        return None, None
    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:
        CF_UNAVAILABLE = 'camoufox not installed (%s)' % type(e).__name__
        return None, None
    try:
        cm = Camoufox(headless=True, geoip=True, humanize=True)
        return cm, cm.__enter__()
    except Exception as e:
        CF_UNAVAILABLE = 'camoufox failed to launch: %s' % str(e)[:90]
        return None, None


def camoufox_qs(browser, owner_lf, settle=9000):
    """Run one search in the real UI and return the `qs` the county issued, or None.

    owner_lf is ``(LAST, FIRST)``.  The county exposes separate last/first inputs; filling both is
    essential because broad surnames are capped at 500 oldest records and can silently omit the
    current owner.  Companies have an empty FIRST and continue to use the last-name field alone.
    """
    page = browser.new_page()
    grabbed = {}

    def on_req(r):
        if 'getStandardRecords' in r.url and 'qs=' in r.url:
            grabbed.setdefault('qs', urllib.parse.unquote(r.url.split('qs=', 1)[1].split('&')[0]))

    page.on('request', on_req)
    try:
        page.goto(OR_BASE, wait_until='domcontentloaded', timeout=60000)
        try:
            page.wait_for_load_state('networkidle', timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        for sel in ('text=Name/Document', 'a:has-text("Name/Document")'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=8000)
                    break
            except Exception:
                continue
        page.wait_for_timeout(2500)

        box = None
        for sel in ('#lastName', 'input[name="lastName"]', 'input[placeholder*="Last" i]'):
            try:
                if page.locator(sel).count():
                    box = page.locator(sel).first
                    break
            except Exception:
                continue
        if box is None:
            return None
        last, first = ((owner_lf[0], owner_lf[1]) if not isinstance(owner_lf, str)
                       else (owner_lf, ''))
        box.fill(last.strip())
        if first and first.strip():
            for sel in ('#firstName', 'input[name="firstName"]', 'input[placeholder*="First" i]'):
                try:
                    loc = page.locator(sel).first
                    if loc.count():
                        loc.fill(first.strip())
                        break
                except Exception:
                    continue
        page.wait_for_timeout(600)

        for sel in ('button[type="submit"]', 'button:has-text("SEARCH")', 'button:has-text("Search")'):
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=8000)
                    break
            except Exception:
                continue

        # Poll rather than one long sleep — the qs usually lands in 3-5s and there is no reason to
        # pay the worst case on every owner.
        for _ in range(int(settle / 750)):
            if grabbed.get('qs'):
                break
            page.wait_for_timeout(750)
        return grabbed.get('qs')
    except Exception:
        return None
    finally:
        try:
            page.remove_listener('request', on_req)
            page.close()
        except Exception:
            pass


def mint_and_fetch(owner_lf, budget=70, persist=False):
    """Mint a fresh reCAPTCHA token in a browser, then fetch. Defaults to a bounded 3-try attempt.

    persist=True mode: 'never give up' — keeps trying with widening back-off (10s -> 20s -> 40s ->
    60s cap, capped at 25 attempts, ~15-20 min max). Each attempt spins up a fresh browser context
    so the site's per-context rate-limits reset. For a specific lead the operator has flagged as
    important enough to burn time on (Furs, Echeverri) — worth it. Do NOT use inside a batch loop.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
    js = re.search(r'JS = r"""(.*?)"""', src, re.S).group(1).replace('SITEKEY', SITE_KEY)
    if persist:
        # keep hammering until the captcha yields or the retry cap is hit
        attempt = 0
        while attempt < int(os.environ.get('MINT_ATTEMPTS', 25)):
            attempt += 1
            try:
                with sync_playwright() as p:
                    b = p.chromium.launch(headless=True)
                    pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded')
                    pg.wait_for_timeout(4000 + attempt * 500)   # give the site more settle each try
                    res = pg.evaluate(js, list(owner_lf))
                    b.close()
                    if res and res.get('success') and res.get('qs'):
                        print(f'  mint OK on attempt {attempt}')
                        return records_by_qs(res['qs'])
                    print(f'  mint attempt {attempt} failed — {res.get("error") if res else "no response"}')
            except Exception as e:
                print(f'  mint attempt {attempt} threw: {str(e)[:80]}')
            back = min(60, 10 * (1.4 ** min(attempt, 10)))
            print(f'  backing off {int(back)}s before attempt {attempt + 1}...')
            time.sleep(back)
        print(f'  gave up after {attempt} attempts (captcha remained hostile)')
        return None
    # legacy bounded behaviour (unchanged)
    t0 = time.time()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context(user_agent=UA, viewport={'width': 1400, 'height': 1000}).new_page()
            res = None
            for _ in range(3):
                if time.time() - t0 > budget: break
                try:
                    pg.goto(OR_BASE, timeout=40000, wait_until='domcontentloaded'); pg.wait_for_timeout(3000)
                    res = pg.evaluate(js, list(owner_lf))
                    if res and res.get('success') and res.get('qs'): break
                except Exception:
                    pg.wait_for_timeout(2500)
            b.close()
        if res and res.get('success') and res.get('qs'):
            return records_by_qs(res['qs'])
    except Exception:
        pass
    return None


# ---- parse the chain: open vs satisfied, isolate the surviving junior --------------------------
def _fc_type(case, case_type='', plaintiff=''):
    """HOA/county-court (whole 1st mortgage survives) vs circuit mortgage foreclosure. Miami-Dade case format
    uses -CA- (circuit) / -CC- (county); also handle the Broward-style CACE/COCE prefixes defensively.
    An association suing in CIRCUIT court (a large arrears claim) is still an association's case: the
    lead's own case_type says so, and the first mortgage survives its sale just the same. The case type
    alone is not enough: foreclosure_leads.classify types Fannie Mae, Ginnie Mae and "COMMUNITY" lenders
    'HOA/Condo', and a lender read as an association counts the loan it forecloses as a survivor. So a
    plaintiff that reads as a lender (_FC_LENDER_RE: MORTGAGE, FEDERAL, BANK, LOAN, SERVICING, ...) keeps its
    circuit case a mortgage foreclosure, unless it names homeowners or a condominium outright."""
    if str(case_type or '').upper().startswith('HOA'):
        p = str(plaintiff or '')
        if not _FC_LENDER_RE.search(p) or _FC_OWNERS_RE.search(p):
            return 'HOA'
    c = (case or '').upper()
    if '-CA-' in c or c.startswith('CACE'): return 'MORTGAGE'
    if '-CC-' in c or c.startswith(('COCE', 'CONO', 'COWE', 'COSO')): return 'HOA'
    return ''


def _inst(s):
    """Normalize a lender/institution name for satisfaction<->mortgage matching."""
    s = (s or '').upper()
    s = re.sub(r'\b(NA|N A|NATIONAL ASSN|NATIONAL ASSOCIATION|FSB|FA|INC|CORP|CO|LLC|LP|USA|'
               r'TRUST COMPANY|MTGE|MORTGAGE|GROUP|GRP|SVGS|SAVINGS|HOME LOANS?|FINANCIAL|SERVICES?|BANK)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


def has_duplicate_liens(res):
    """True when a CACHED result lists the same recorded mortgage twice (pre-dedupe trace)."""
    bps = [str(x.get('bp') or '').strip() for x in ((res or {}).get('liens') or []) if isinstance(x, dict)]
    bps = [b for b in bps if b.strip('0/ -')]
    return len(bps) != len(set(bps))


# Who forecloses a MORTGAGE. Deliberately wider than analyze()'s satisfaction-side _LENDER_RE: the
# plaintiff on a lis pendens is usually a servicer or a trustee, not a bank by name.
_FC_LENDER_RE = re.compile(r'BANK|MORTGAGE|MTGE|LOAN|FINANC|SAVING|CREDIT|FUNDING|SERVIC|FEDERAL|'
                           r'NATIONAL|TRUSTEE|\bTRUST\b|NATIONSTAR|NEWREZ|PENNYMAC|MR\.?\s*COOPER|'
                           r'LAKEVIEW|CARRINGTON|SHELLPOINT|ROCKET|QUICKEN|FANNIE|FREDDIE|'
                           r'HOUSING AND URBAN|SECRETARY OF HOUSING|\bHUD\b|LENDING', re.I)
_FC_ASSN_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|COMMUNITY|PROPERTY\s+OWNERS?|'
                         r'TOWNHO|MAINTENANCE|(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
# words only an owners' association uses (not COMMUNITY or MASTER, which banks use too)
_FC_OWNERS_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|PROPERTY\s+OWNERS?|TOWNHO', re.I)
BANK_FC_YEARS = 5


def bank_foreclosure(models, fol, subj_subdiv, today=None):
    """A lender's lis pendens on the SUBJECT parcel, or None (2026-09-23 audit, Salkey).

    Only asked of an HOA/condo lead. The association's small case is what reached the board, and
    the chain came back with no open mortgage, so the board printed VERIFIED CLEAR -- while a bank
    had its own foreclosure filed against the same unit. A lender foreclosing proves a mortgage is
    open, and under an association sale that whole mortgage survives. broward_liens has flagged
    this as `second_fc` since the Bloom / Tucker cases; Miami-Dade never looked.

    Parcel-anchored. A filing that carries a folio counts only when it is the subject folio. One
    with a BLANK folio is matched on the subdivision, which proves the parcel only when the owner
    has no other folio in that subdivision: the search is owner-wide, and a condo is one
    subdivision with many units, so a lender foreclosing the owner's OTHER unit would otherwise
    land on this one. When other units are seen the match comes back marked `unsure` -- analyze()
    keeps it off the board's 2ND FORECLOSURE flag, and equity_state still refuses to call the
    subject clear, because a lender foreclosing one of this owner's units in the building is not
    something an empty chain can rule out.

    Recent only (BANK_FC_YEARS): an unreleased lis pendens from a foreclosure a decade ago says
    nothing about today. Released ones (a release/discharge pointing at its book/page) are skipped.
    """
    today = today or datetime.date.today()
    released = set()
    for r in models:
        t = (r.get('doC_TYPE', '') or '').upper()
        if 'LIS PEND' in t and re.search(r'REL|DISCH|CANCEL|WITHDR|TERMIN', t):
            released.add((str(r.get('oriG_REC_BOOK', '')).strip(), str(r.get('oriG_REC_PAGE', '')).strip()))
    # the owner's OTHER parcels in the subject subdivision, as far as the results show them
    other_units = {norm_folio(r.get('foliO_NUMBER', '')) for r in models
                   if subj_subdiv and (r.get('subdiV_NAME', '') or '').strip().upper() == subj_subdiv
                   and norm_folio(r.get('foliO_NUMBER', '')) not in ('', fol)}
    best, unsure = None, None
    for r in models:
        t = (r.get('doC_TYPE', '') or '').upper()
        if 'LIS PEND' not in t or re.search(r'REL|DISCH|CANCEL|WITHDR|TERMIN', t):
            continue
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME', '') or '').strip().upper()
        if rf:
            if rf != fol:
                continue                               # another parcel's filing
            exact = True
        elif subj_subdiv and sd == subj_subdiv:
            exact = not other_units                    # subdivision proves the unit only if it is the only one
        else:
            continue
        if (str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip()) in released:
            continue
        d = _parse_recd(r.get('reC_DATE', ''))
        if not d or (today - d).days > BANK_FC_YEARS * 366:
            continue
        lender = ''
        for p in (r.get('firsT_PARTY', ''), r.get('seconD_PARTY', '')):
            if p and _FC_LENDER_RE.search(p) and not _FC_ASSN_RE.search(p):
                lender = p
                break
        if not lender:
            continue
        hit = (d, {'case': 'lis pendens %s recorded %s' % (r.get('reC_BOOKPAGE', '') or '',
                                                            d.strftime('%m/%d/%Y')),
                   'party': lender[:40], 'bp': r.get('reC_BOOKPAGE', ''), 'd': d.isoformat()})
        if exact:
            if best is None or d > best[0]:
                best = hit
        elif unsure is None or d > unsure[0]:
            unsure = hit
    if best:
        return best[1]
    return dict(unsure[1], unsure=True) if unsure else None


_UNPRICED_SKIP_RE = re.compile(r'MODIF|ASSUMP|SUBORD|SPREAD|AMEND|ASSIGN|RELEASE|SATIS|CORRECT', re.I)


def clear_undocumented(res, case=''):
    """True for a cached chain that would read CLEAR but carries no record of HOW it was searched
    (traced before `nrec` / `second_fc` / `mtg_open_unpriced` were written). equity_state already
    refuses to call it clear; this puts it at the front of the re-pull queue so it can earn it."""
    res = res or {}
    return (res.get('conf') == 'ok' and not res.get('liens')
            and not ('nrec' in res and 'second_fc' in res and 'mtg_open_unpriced' in res))


# words on a deed's party line that are not a person's given name
_NOT_GIVEN = frozenset(('AND', 'HW', 'WF', 'HUSB', 'WIFE', 'ET', 'AL', 'ETAL', 'UX', 'VIR', 'JR', 'SR', 'II', 'III',
                        'IV', 'TR', 'TRS', 'TRUSTEE', 'TRUSTEES', 'LE', 'EST', 'ESTATE', 'OF', 'THE', 'REM', 'LIFE',
                        'HEIRS', 'DEC', 'DECEASED', 'JT', 'JTRS', 'TEN', 'TENANTS', 'ENT', 'ENTIRETY', 'WROS',
                        'MR', 'MRS', 'MS', 'DR', 'REV', 'LIV', 'LIVING', 'TRUST', 'REVOCABLE', 'FAMILY'))


# THE OLD ANALYZER'S COUNTING, kept only to say where it summed a row (origin/main before #62): a
# re-read of a chain it wrote may take this case's own filing out of that total and no other.
_LEG_IRS = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
_LEG_CODE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|CODE\s+ENFORCEMENT|MUNICIPAL|MIAMI-?DADE|STATE OF FLORIDA|PACE|CLEAN ENERGY', re.I)
_LEG_HOA = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|TOWNHO|MAINTENANCE', re.I)
_LEG_ASSN = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
_LEG_DOC = re.compile(r'^(LIEN|JUDGMENT|NOTICE|CLAIM|CERT|FINANCING STATEMENT)', re.I)


def _legacy_norm(s):
    s = re.sub(r'\b(NA|N A|INC|CORP|CO|LLC|LP|USA|TRUST|COMPANY|OF|THE|AND|ASSN|ASSOC|ASSOCIATION)\b', '', (s or '').upper())
    return re.sub(r'[^A-Z]', '', s)


def _legacy_bucket(r, on_parcel, sats):
    """'irs_open' / 'hoa_open' / 'code_open' where the old analyzer summed this record, or None."""
    doc = (r.get('doC_TYPE', '') or '').upper().strip()
    if not _LEG_DOC.match(doc) or num(r.get('consideratioN_1')) <= 0:
        return None
    party = r.get('seconD_PARTY') or ''
    h = _legacy_norm(party)
    if h and h in sats:
        return None
    if _LEG_IRS.search(party):
        return 'irs_open'
    if _LEG_HOA.search(party) or _LEG_ASSN.search(party):
        return 'hoa_open' if on_parcel else None
    if _LEG_CODE.search(party):
        return 'code_open' if on_parcel else None
    return 'code_open' if 'JUDGMENT' in doc else None


def _surv_of(opens, ftype, judgment):
    """What survives the sale, from the OPEN priced mortgages. Kept apart from analyze() so a re-read
    that keeps an older search's mortgages can re-settle them when the case's type changed."""
    junior = first_amt = surv = surv_first = 0
    juniors_post = 0
    first_bp = ''
    if opens:
        if ftype == 'HOA':                             # HOA sale: the WHOLE first mortgage survives
            surv = sum(o['amt'] for o in opens)
            surv_first = max(o['amt'] for o in opens)
        else:
            anchor = (lambda o: abs(o['amt'] - judgment)) if (judgment and judgment > 0) else (lambda o: -o['amt'])
            fore = min(opens, key=anchor)              # the foreclosing 1st (closest to judgment, else largest)
            first_amt = fore['amt']
            first_bp = fore.get('bp') or ''
            junior = surv = sum(o['amt'] for o in opens if o is not fore)
            # DATES COMPARE AS DATES. `o['d']` is 'M/D/YYYY' straight from the clerk — '1/10/2006'
            # sorts lexically ABOVE '10/31/2006', so string comparison silently classified 1-Jan
            # loans as "recorded AFTER" 10-Oct loans and swapped seniors with juniors in the
            # reconciliation the browser trusts. Falling back to the raw string only when parsing
            # fails means an unreadable date can never claim to be newer than a real one.
            fd = _parse_recd(fore['d'])
            juniors_post = sum(o['amt'] for o in opens if o is not fore and _parse_recd(o['d']) and fd and _parse_recd(o['d']) >= fd)
    return {'junior': junior, 'first_est': first_amt, 'surv': surv, 'surv_first': surv_first,
            'juniors_post': juniors_post, 'first_bp': first_bp}


def _owner_words(owner):
    """The words a party string must carry to name the searched owner, or None when unknown.
    A person needs the surname and the first given name as whole words (the clerk writes
    'PEREZ JOHN A' or 'PEREZ, JOHN'); a company needs its whole name."""
    owner = re.sub(r'\s*\(defendant\)\s*$', '', owner or '', flags=re.I).strip()
    sp = split_owner(owner) if owner else None
    if not sp:
        return None
    if not sp[1]:
        return ('co', re.sub(r'[^A-Z0-9]', '', sp[0].upper()))
    given = re.findall(r'[A-Z0-9]+', sp[1].upper().replace("'", ''))    # MARIA-JOSE -> MARIA, as tokens split
    # split_owner drops single letters; the owner's own middle initial ('JOSE A PEREZ') is kept apart,
    # so 'PEREZ A' or 'PEREZ ANTONIO' may still be this owner, never a namesake
    inits = tuple(sorted({t for t in re.findall(r'[A-Z]+', owner.upper().replace("'", '')) if len(t) == 1}))
    return ('person', sp[0].upper().strip('.'), given[0], tuple(g for g in given if len(g) > 1), inits) if given else None


def _names_owner(party, owners, strict=False):
    """True when the party string names any of `owners` (a list of _owner_words results). strict: every
    given name the owner has (not just the first) must be there, for a match that releases a debt."""
    if not owners or not party:
        return False
    up = party.upper()
    toks = set(re.findall(r'[A-Z0-9]+', up.replace("'", '')))   # GARCIA-LOPEZ = GARCIA LOPEZ; O'BRIEN = OBRIEN
    for words in owners:
        if words[0] == 'co':
            if words[1] and words[1] in re.sub(r'[^A-Z0-9]', '', up):
                return True
        elif (all(w in toks for w in re.findall(r'[A-Z0-9]+', words[1].replace("'", '')))    # 'DE LA CRUZ'
              and (re.findall(r'[A-Z0-9]+', words[2].replace("'", '')) or [''])[0] in toks
              and (not strict or all(g in toks for g in (words[3] if len(words) > 3 else ())))):
            return True
    return False


def _surname_given(party, words):
    """(surname present?, the other name tokens) of a party string against one person's words."""
    toks = re.findall(r'[A-Z0-9]+', (party or '').upper().replace("'", ''))
    sn = re.findall(r'[A-Z0-9]+', words[1].replace("'", ''))
    if not sn or not all(w in toks for w in sn):
        return False, []
    return True, [t for t in toks if t not in sn]


def _maybe_owner(party, owners):
    """Could this be the owner, indexed short? The surname plus any given name, any initial, or no
    given name at all. ('SMITH J' may be JOHN SMITH; 'SMITH MARIA' is not.)"""
    for words in owners or ():
        if words[0] != 'person':
            continue
        ok, rest = _surname_given(party, words)
        given = set(re.findall(r'[A-Z0-9]+', words[2].replace("'", ''))) | set(words[3] if len(words) > 3 else ())
        inits = {t for t in rest if len(t) == 1}
        mids = set(words[4] if len(words) > 4 else ())
        if ok and mids and ({t[:1] for t in rest if t not in _NOT_GIVEN} & mids):
            return True                                     # the owner's middle initial, or a name it starts
        # any of the owner's given names (a middle name too: 'PEREZ ANTONIO' may be JOSE ANTONIO PEREZ)
        # or any of their initials
        if ok and (not rest or given & set(rest) or {g[:1] for g in given} & inits):
            return True
    return False


def _other_person(party, owners):
    """The owner's surname with a different given name only: a namesake, not the owner."""
    return any(w[0] == 'person' and _surname_given(party, w)[0] and _surname_given(party, w)[1]
               for w in owners or ()) and not _maybe_owner(party, owners)


def analyze(models, folio, judgment, ftype='', plaintiff='', owner='', case='', co_owners=()):
    """Open-mortgage picture for the SUBJECT parcel only. Precision > recall: without a folio to isolate
    by, we return nothing rather than risk a namesake's mortgages polluting the number.
    ftype='HOA' means the whole first mortgage survives the sale (surface `surv`), not just a 2nd.
    plaintiff = this case's plaintiff, so the case's OWN lis pendens and judgments (a vacated one
    included) are shown as this case and never counted as another claim.
    owner = the name that was searched: the clerk search is by SURNAME ONLY, so a person-wide row
    (tax warrant, money judgment) with no parcel anchor rides only when it names this owner.
    co_owners = the case's other named people, (LAST, FIRST): a spouse's tax lien or judgment
    attaches to their share, so it rides like the owner's own.
    case = this case's number: its own judgment and lis pendens cannot predate the year it was filed."""
    fol = norm_folio(folio)
    if not fol:
        return {'liens': [], 'open_count': 0, 'junior': 0, 'first_est': 0, 'surv': 0, 'surv_first': 0,
                'ftype': ftype, 'conf': 'none'}
    # ANCHOR the subject's subdivision from a record that DOES carry the subject folio (usually the deed).
    # folio is blank on most newer mortgages, but subdivision is consistent — so subdivision + owner-name
    # isolates the property, while folio alone would drop the very mortgages we need.
    subj_subdiv = ''
    parcel_found = any(norm_folio(r.get('foliO_NUMBER', '')) == fol for r in models)
    for r in models:
        if norm_folio(r.get('foliO_NUMBER', '')) == fol:
            sd = (r.get('subdiV_NAME', '') or '').strip().upper()
            if sd: subj_subdiv = sd; break
    # a MORTGAGE is satisfied if a SATISFACTION points at its book/page
    satisfied = set()
    for r in models:
        if 'SATISFACTION' in (r.get('doC_TYPE', '') or '').upper():
            satisfied.add((str(r.get('oriG_REC_BOOK', '')).strip(), str(r.get('oriG_REC_PAGE', '')).strip()))
    def sortkey(r):
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', (r.get('reC_DATE', '') or '').strip())
        return (m.group(3), m.group(1).zfill(2), m.group(2).zfill(2)) if m else ('0000', '00', '00')
    liens, opens = [], []
    seen_instruments = set()
    for r in sorted(models, key=sortkey):
        if not (r.get('doC_TYPE', '') or '').upper().startswith('MORTGAGE'):
            continue
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME', '') or '').strip().upper()
        if not ((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv)):
            continue                                   # subject parcel only (folio when present, else subdivision)
        it, cons = num(r.get('intangible')), num(r.get('consideratioN_1'))
        amt = round(it / 0.002) if it > 0 else round(cons)
        nopx = False
        if amt <= 0:
            # $0 doc = modification/piggyback placeholder, not a real balance -- usually. But a plain
            # MORTGAGE on the parcel with no stamp amount is a loan whose size the index does not
            # publish, and dropping it is how an unknown became "no surviving debt". It runs through
            # the same release rules as a priced loan, is never summed, and leaves as a COUNT:
            # equity_state reads mtg_open_unpriced as a CEILING, never a clear.
            if _UNPRICED_SKIP_RE.search(r.get('doC_TYPE', '') or '') or not (
                    str(r.get('reC_BOOK', '')).strip() and str(r.get('reC_PAGE', '')).strip()):
                continue
            nopx = True
        bp = (str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip())
        # ONE INSTRUMENT, ONE ROW. The owner search returns the same recording once per name it
        # matched (co-owners, AKA spellings), and every copy used to become its own lien: Elharrar
        # (accuracy audit 2026-09-23) showed two mortgages four times, $790,000 of face amount
        # where the county records $395,000. Book/page identifies a recording; the CFN is the
        # fallback when either half is blank. A row with neither is kept, never merged on a guess.
        ident = (('bp',) + bp) if all(bp) else (
            ('cfn', str(r.get('cfN_MASTER_ID') or '').strip()) if r.get('cfN_MASTER_ID') else None)
        if ident is not None:
            if ident in seen_instruments:
                continue
            seen_instruments.add(ident)
        is_open = bp not in satisfied
        row = {'d': (r.get('reC_DATE', '') or '')[:10], 'amt': amt, 'party': (r.get('seconD_PARTY', '') or '')[:40],
               'bp': r.get('reC_BOOKPAGE', ''), 'st': 'OPEN' if is_open else 'SATISFIED',
               '_dt': '-'.join(sortkey(r)), '_lend': _inst(r.get('seconD_PARTY'))}
        if not is_open:
            row['sat_by'] = 'book/page'       # a release that names this loan (the others below infer it)
        if nopx:
            row['_nopx'] = True
        liens.append(row)
        if is_open:
            opens.append(row)
    # --- kimi: layered fallback for still-open mortgages (MD) -------------------------------------
    # The book/page match is direct but blind when a satisfaction leaves oriG_* empty (common on
    # assignee-recorded releases). Layered: release by lender OR its recorded assignee; then any
    # LENDER-party release within 24 months; then refi-kill (newer different-lender mortgage
    # >=70% of balance within 36 months).
    _LENDER_RE = re.compile(r'BANK|MORTGAGE|MTGE|LOAN|FINANC|SAVING|CREDIT|FUNDING|SERVICING|FEDERAL|NATIONAL', re.I)
    def _months(a, b):
        try: return (int(b[:4]) - int(a[:4])) * 12 + (int(b[5:7]) - int(a[5:7]))
        except Exception: return 99
    sats2 = [r for r in models if re.search(r'SATISF|RELEASE', (r.get('doC_TYPE', '') or '').upper())]
    for r in sats2: r['_dt'] = '-'.join(sortkey(r))
    assigns = [r for r in models if 'ASSIGNMENT' in (r.get('doC_TYPE', '') or '').upper()]
    for r in assigns: r['_dt'] = '-'.join(sortkey(r))
    # CORRECTNESS: build a per-lender assignment GRAPH (from -> set(to)) and BFS the chain that starts
    # at the mortgage's own lender. Was: _assignees pooled EVERY assignee of every assignment together
    # and returned them regardless of the input `lend`, so a release by an unrelated bank's assignee
    # (chain B) could silently mark chain A's mortgage SATISFIED — the exact "phantom survivor -> false
    # equity" failure the tool exists to catch. Mirrors broward_liens.chain_of. Uses firsT_PARTY as the
    # assignor (which the MD Records API records as the current holder BEFORE the assignment) and
    # seconD_PARTY as the new holder.
    assigngraph = {}
    for a in assigns:
        fr = _inst(a.get('firsT_PARTY')); to = _inst(a.get('seconD_PARTY'))
        if fr and to and fr != to:
            assigngraph.setdefault(fr, set()).add(to)
    def chain_of(lend, after):
        if not lend: return {lend}
        out, frontier, seen = {lend}, [lend], set()
        while frontier:
            cur = frontier.pop()
            if cur in seen: continue
            seen.add(cur)
            for nxt in assigngraph.get(cur, ()):
                if nxt not in out:
                    out.add(nxt); frontier.append(nxt)
        return out
    for o in liens:
        if o['st'] == 'SATISFIED':
            continue
        chain = chain_of(o['_lend'], o['_dt'])
        for s in sats2:
            if not s['_dt'] or s['_dt'] < o['_dt']: continue
            si = _inst(s.get('seconD_PARTY'))
            if si and si in chain:
                o['st'] = 'SATISFIED'; o['sat_by'] = 'lender chain'; break
    opens = [o for o in liens if o['st'] == 'OPEN']
    # rule 2: a LENDER-party release kills the NEWEST still-open mortgage recorded 3-24 months
    # before it. The 3-month floor is the Echeverri guard: a release dated weeks after a loan was
    # written belongs to an OLDER loan in the chain, never to the new one — so same-year misfires
    # (Echeverri's real New Century senior) can't be killed by a different loan's satisfaction.
    for s in sorted(sats2, key=lambda x: x['_dt']):
        if not (s['_dt'] and _LENDER_RE.search(s.get('seconD_PARTY') or '')): continue
        # an unpriced row never absorbs a release here: it would free the priced loan the release
        # was really for, and rule 2 is a guess about WHICH loan, not proof
        prior = [o for o in opens if not o.get('_nopx') and o['_dt'] < s['_dt']
                 and 3 <= _months(o['_dt'], s['_dt'][:10]) <= 24]
        if prior:
            newest = max(prior, key=lambda o: o['_dt'])
            newest['st'] = 'SATISFIED'; newest['sat_by'] = 'lender release'
            opens = [o for o in opens if o is not newest]
    # rule 3: refi-kill ONLY in true-refi shape — newer different-lender mortgage >=90% of the
    # older balance within 24 months (a junior second is usually far smaller, so it can't pose as one)
    for o in liens:
        if o['st'] != 'OPEN' or o.get('_nopx'): continue     # refi shape needs a balance to compare
        chain3 = chain_of(o['_lend'], o['_dt'])
        for m2 in liens:
            if m2 is o or m2['_dt'] <= o['_dt']: continue
            if (_months(o['_dt'], m2['_dt']) <= 24 and m2['amt'] >= o['amt'] * 0.9
                    and m2['_lend'] != o['_lend'] and m2['_lend'] not in chain3):
                o['st'] = 'SATISFIED'; o['sat_by'] = 'refinance'; break
    # unpriced loans leave the list here: counted, never summed, never shown as a $0 lien
    unpriced_open = sum(1 for o in liens if o.get('_nopx') and o['st'] == 'OPEN')
    liens = [o for o in liens if not o.get('_nopx')]
    opens = [o for o in liens if o['st'] == 'OPEN']
    _sv = _surv_of(opens, ftype, judgment)
    junior, first_amt, surv, surv_first = _sv['junior'], _sv['first_est'], _sv['surv'], _sv['surv_first']
    juniors_post, first_bp = _sv['juniors_post'], _sv['first_bp']
    # --- open non-mortgage liens (kimi: feeds the deal-modal HOA / code / IRS prefills) ------------
    # Every lien, judgment, lis pendens and tax warrant the search returned on this parcel is a ROW
    # in `other`, priced or not, so a reader sees what was found rather than a total that silently
    # dropped it. 12-case verification 2026-09-24, defect 1: City of Miami and county liens that
    # were in the saved search never reached the chain, because
    #   * one City release anywhere released EVERY City lien (the match was on the holder's name),
    #   * a lien the index publishes no amount for was skipped instead of counted as unpriced,
    #   * the holder was read from seconD_PARTY only, and the index does not put the lienor on a
    #     fixed side, and
    #   * LIS PENDENS and tax WARRANT rows never matched the document filter.
    # A lien is RELEASED only by a satisfaction/release that points at its own book/page. Code and
    # association liens need the same parcel isolation the mortgages use; IRS, state tax warrants
    # and money judgments attach to the person and ride anyway. The case's own lis pendens and
    # judgments are this case, not another claim: shown, never summed (defect 7, 2024-014878's
    # vacated final judgment 34932/1256).
    _IRS_RE = re.compile(r'INTERNAL\s+REV|UNITED\s+STATES|\bIRS\b', re.I)
    _DOR_RE = re.compile(r'DEPARTMENT\s+OF\s+REVENUE|DEPT\.?\s+OF\s+REV|\bDOR\b', re.I)
    _CODE_RE = re.compile(r'\bCITY\s+OF\b|\bCOUNTY\b|CODE\s+ENFORCEMENT|MUNICIPAL|MIAMI-?DADE|STATE OF FLORIDA|\bPACE\b|CLEAN ENERGY|WATER\s+(?:AND|&)\s+SEWER|\bWASD\b', re.I)
    _HOA_DOC_RE = re.compile(r'HOMEOWNERS?|CONDOMINIUM|\bCONDO\b|\bMASTER\b|\bVILLAS?\b|COMMUNITY|PROPERTY\s+OWNERS?|TOWNHO|MAINTENANCE', re.I)
    _ASSN_DOC_RE = re.compile(r'(?<!NATIONAL\s)\bASS(?:N|OC(?:IATION)?)\b', re.I)
    _OTHER_DOC_RE = re.compile(r'\bLIEN\b|JUDGMENT|LIS PENDENS|\bWARRANTS?\b|^NOTICE|^CLAIM|^CERT|^FINANCING STATEMENT', re.I)
    _NOT_A_CLAIM_RE = re.compile(r'SATISF|RELEASE|TERMINAT|CANCEL|DISCHARGE|NOTICE OF COMMENCEMENT|^MORTGAGE', re.I)
    # A City's code lien is often recorded as a CERTIFIED COPY OF (FINAL) ORDER imposing a fine, so a
    # recognised creditor's row counts unless its document says it is NOT a claim; an unrecognised
    # creditor's counts only when the document says lien, judgment or warrant.
    _CLAIM_DOC_RE = re.compile(r'\bLIEN\b|JUDGMENT|\bWARRANTS?\b', re.I)
    _NOT_CLAIM_DOC_RE = re.compile(r'WAIVER|CONTEST|SUBORDINAT|FINANCING STATEMENT|CERTIFICATE OF TITLE', re.I)
    # a NOTICE from a recognized creditor (the IRS, a City, an association) still counts: Miami-Dade
    # files federal tax liens and code liens as 'NOTICE - NOT', and main summed them
    _CREDITOR_RE = re.compile('|'.join(x.pattern for x in (_IRS_RE, _DOR_RE, _CODE_RE, _HOA_DOC_RE, _ASSN_DOC_RE)), re.I)
    def _here(r):
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        return bool((rf and rf == fol) or (subj_subdiv and (r.get('subdiV_NAME') or '').strip().upper() == subj_subdiv))
    # A PARTIAL release frees one parcel of a lien that may cover several: it counts only when it
    # is indexed to this folio.
    released_bp = {(str(r.get('oriG_REC_BOOK', '')).strip(), str(r.get('oriG_REC_PAGE', '')).strip())
                   for r in models if re.search(r'SATISF|RELEASE', (r.get('doC_TYPE', '') or '').upper())
                   and ('PARTIAL' not in (r.get('doC_TYPE', '') or '').upper()
                        or norm_folio(r.get('foliO_NUMBER', '')) == fol)}
    released_bp.discard(('', ''))
    # "U.S. BANK NATIONAL ASSOCIATION, AS TRUSTEE FOR ..." and "U S BANK NATIONAL ASSN TR" agree
    # once the trustee clause is cut; "PNC BANK, N.A." and "PNC BANK NA" once punctuation is.
    _pnorm = lambda x: _inst(re.sub(r'\bASS(?:N|OC)\b', 'ASSOCIATION',
                                    re.sub(r'\s(?:AS\s+)?(?:TRUSTEE|TR)\b.*$', '', re.sub(r'[.,]', ' ', (x or '').upper()))))
    _pl = _pnorm(plaintiff)
    _ow = [w for w in [_owner_words(owner)] + [('person', l, f) for l, f in (co_owners or ())] if w] or None
    # The parcel's own deeds name the household too ('SMITH JOHN & HELEN'), and an LP lead carries no
    # defendants: every given name recorded beside the owner's surname on a deed to this folio is an owner.
    _o0 = _owner_words(owner)
    if _o0 and _o0[0] == 'person':
        _sn = re.findall(r'[A-Z0-9]+', _o0[1].replace("'", ''))
        for r in models:
            if 'DEED' not in (r.get('doC_TYPE', '') or '').upper() or norm_folio(r.get('foliO_NUMBER', '')) != fol:
                continue
            # the GRANTEE only: a same-surname seller deeding to the owner is not the household. And
            # never a suffix or a role word: 'GARCIA JOSE JR' must not make every GARCIA JR an owner,
            # whose release in the subdivision would then free this owner's lien.
            toks = re.findall(r'[A-Z0-9]+', (r.get('seconD_PARTY') or '').upper().replace("'", ''))
            if _sn and all(t in toks for t in _sn):
                for g in toks:
                    if len(g) > 1 and g not in _sn and g not in _NOT_GIVEN and not g.isdigit():
                        _ow.append(('person', _o0[1], g))
    _legacy_sats = {_legacy_norm(r.get('seconD_PARTY')) for r in models
                    if re.search(r'SATISFACTION|RELEASE', (r.get('doC_TYPE', '') or '').upper())}
    _cy = re.match(r'\s*(\d{4})-', case or '')
    _case_year = int(_cy.group(1)) if _cy else None
    def _is_plaintiff(*parties):
        # A name that normalizes short ("PNC BANK" -> "PNC") must match exactly; a containment
        # test on three letters would call every party with "PNC" in it the plaintiff. The INDEX
        # name may be the plaintiff's cut short, never longer: "SUNSET HOMEOWNERS ASSOCIATION PHASE II"
        # is a sibling of "SUNSET HOMEOWNERS ASSOCIATION", "VILLAGES OF KENDALL MASTER" only shares a
        # start with "... HOMEOWNERS", and "BANK OF AMERICA" (-> OFAMERICA) sits inside "UNITED
        # STATES OF AMERICA" without starting it.
        if len(_pl) < 2:
            return False
        for p in parties:
            q = _pnorm(p)
            if not q:
                continue
            # an index name that ENDS in a legal suffix is whole, not cut short: it must match exactly
            # ("SUNSET HOMEOWNERS ASSOCIATION INC" is not "... ASSOCIATION PHASE II INC")
            whole = re.search(r'\b(?:INC|LLC|CORP|CO|LTD|LP|ASSN|ASSOC|ASSOCIATION|NA|N A|FSB|TRUST)\.?\s*$',
                              re.sub(r'[.,]', ' ', p or '').upper().strip())
            if q == _pl or (not whole and len(_pl) >= 5 and len(q) >= 5 and _pl.startswith(q)):
                return True
        return False
    def _own_judgment(doc, amt, d):
        # the case's figure on a judgment recorded no earlier than the case
        return ('JUDGMENT' in doc and amt > 0 and judgment and judgment > 0
                and abs(amt - judgment) <= max(1.0, 0.01 * judgment) and not _before_case(d))
    def _before_case(d):
        # a recording from before the year this case was filed cannot be one of its own filings
        dt = _parse_recd(d)
        return bool(_case_year and dt and dt.year < _case_year)
    # A release that names no book/page. One City release used to release EVERY City lien by name;
    # now a lien falls only to a release that points at it, or, for releases that point nowhere, when
    # its holder has at least one such release on or after each of its liens (paired one to one).
    # Fewer releases than liens and none of them is released: which one was paid is not knowable.
    # Only a release that says it is of a lien, judgment or warrant (a plain SATISFACTION is how the
    # index files a mortgage payoff), never a partial one, and only one indexed to THIS parcel: a
    # release of the same City's lien on the owner's other house must not free this one.
    _rel_seen = set()
    def _first_copy(r):
        # co-owner and AKA copies of ONE release are one release, not one per copy
        k = ((str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip())
             if str(r.get('reC_BOOK', '')).strip() and str(r.get('reC_PAGE', '')).strip()
             else ('cfn', str(r.get('cfN_MASTER_ID') or '').strip()) if r.get('cfN_MASTER_ID') else None)
        if k is None:
            return True
        if k in _rel_seen:
            return False
        _rel_seen.add(k)
        return True
    def _rel_names_owner(r):
        # a release on ANOTHER folio is that property's (the owner's other house): never a person-wide one
        return (_ow is not None and not norm_folio(r.get('foliO_NUMBER', ''))
                and (_names_owner(r.get('firsT_PARTY'), _ow, strict=True)
                     or _names_owner(r.get('seconD_PARTY'), _ow, strict=True)))
    def _rel_here(r):
        # on this folio, or in its subdivision AND naming the owner: a neighbour's is not ours
        return (norm_folio(r.get('foliO_NUMBER', '')) == fol
                or (_here(r) and (_ow is None or _rel_names_owner(r))))
    # (date, parties, on this parcel?). One that is not on the parcel but names the owner strictly can
    # free only a PERSON-WIDE lien (see the pairing below): a paid tax lien carries no folio either.
    unref_rel = [((_parse_recd((r.get('reC_DATE', '') or '')[:10])),
                  {_pnorm(r.get('firsT_PARTY')), _pnorm(r.get('seconD_PARTY'))} - {''}, _rel_here(r))
                 for r in models if re.search(r'SATISF|RELEASE', (r.get('doC_TYPE', '') or '').upper())
                 and re.search(r'LIEN|JUDG|WARRANT', (r.get('doC_TYPE', '') or '').upper())
                 and not re.search(r'MORTGAGE|PARTIAL', (r.get('doC_TYPE', '') or '').upper())
                 and (_rel_here(r) or _rel_names_owner(r))
                 and not (str(r.get('oriG_REC_BOOK', '')).strip() and str(r.get('oriG_REC_PAGE', '')).strip())
                 and _first_copy(r)]
    other = []
    hoa_open = code_open = irs_open = 0
    other_unpriced = 0
    seen_other = set()
    for r in sorted(models, key=sortkey):
        doc = (r.get('doC_TYPE', '') or '').upper().strip()
        if not _OTHER_DOC_RE.search(doc) or _NOT_A_CLAIM_RE.search(doc):
            continue
        bp = (str(r.get('reC_BOOK', '')).strip(), str(r.get('reC_PAGE', '')).strip())
        p1, p2 = r.get('firsT_PARTY') or '', r.get('seconD_PARTY') or ''
        both = p1 + ' | ' + p2
        # The index puts the lienor on either side, and one side is the owner: read the creditor
        # from the OTHER side, or an owner named VILLA or COUNTY files their own debts as an
        # association's or a city's. Both sides only when neither names the owner (a prior owner).
        o1, o2 = _names_owner(p1, _ow), _names_owner(p2, _ow)
        cred = p2 if (o1 and not o2) else (p1 if (o2 and not o1) else both)
        rf = norm_folio(r.get('foliO_NUMBER', ''))
        sd = (r.get('subdiV_NAME') or '').strip().upper()
        on_parcel = bool((rf and rf == fol) or (subj_subdiv and sd == subj_subdiv))
        if 'LIS PENDENS' in doc:
            kind = 'lis_pendens'
        elif _IRS_RE.search(cred):
            kind = 'irs'
        elif _DOR_RE.search(cred) or re.search(r'\bWARRANTS?\b', doc):   # a tax warrant, never a WARRANTY deed
            kind = 'state_tax'
        elif _HOA_DOC_RE.search(cred) or _ASSN_DOC_RE.search(cred):
            kind = 'association'
        elif _CODE_RE.search(cred):
            kind = 'code'
        elif 'JUDGMENT' in doc:
            kind = 'judgment'
        else:
            kind = 'other'
        if 'JUDGMENT' in doc and not on_parcel and kind in ('association', 'code'):
            kind = 'judgment'           # COMMUNITY BANK's money judgment follows the person, not a parcel
        person_wide = kind in ('irs', 'state_tax', 'judgment')
        if not on_parcel:
            if not person_wide:
                continue                                    # another property of the same owner
            if _ow is not None and not (o1 or o2) and (_other_person(p1, _ow) or _other_person(p2, _ow)) \
                    and not (_maybe_owner(p1, _ow) or _maybe_owner(p2, _ow)):
                continue                                    # a namesake: the search is by surname only
        # One instrument, one row (co-owner copies). Marked seen only once a copy is KEPT: a copy
        # indexed to another folio must not hide this parcel's copy of the same recording.
        ident = (('bp',) + bp) if all(bp) else (
            ('cfn', str(r.get('cfN_MASTER_ID') or '').strip()) if r.get('cfN_MASTER_ID') else None)
        if ident is not None:
            if ident in seen_other:
                continue
            seen_other.add(ident)
        amt = num(r.get('consideratioN_1')) or num(r.get('amount'))
        # The year floor is for the filings the suit itself makes (its lis pendens and judgment). An
        # association's claim of lien is recorded BEFORE it sues and is the debt being foreclosed.
        # By DOCUMENT, not by who the creditor looks like: a plaintiff called SPACE COAST CREDIT UNION
        # or FIRST COUNTY BANK reads as 'code', and its own judgment is still this case. Its other
        # liens are claims unless they are an association's (the claim of lien it forecloses).
        _suit_doc = 'LIS PENDENS' in doc or 'JUDGMENT' in doc
        # A plaintiff-named JUDGMENT off this parcel with a different figure is the same bank's OTHER
        # suit (a credit card), not this one.
        _other_suit = ('JUDGMENT' in doc and not on_parcel and amt > 0 and judgment and judgment > 0
                       and not _own_judgment(doc, amt, r.get('reC_DATE', '')))
        # The figure alone names this case's judgment only on this parcel and only when the creditor
        # is not a City, an association or a tax authority: a City's $9,510 judgment is not the
        # association's $9,500 one. (The index filing the plaintiff under another name, a servicer,
        # is what this is for.)
        own_case = ((_is_plaintiff(p1, p2) and (kind in ('association', 'other') or _suit_doc)
                     and not (_suit_doc and _before_case(r.get('reC_DATE', ''))) and not _other_suit)
                    or (_own_judgment(doc, amt, r.get('reC_DATE', '')) and on_parcel
                        and kind in ('judgment', 'other')))
        released = all(bp) and bp in released_bp
        row = {'d': (r.get('reC_DATE', '') or '')[:10], 'doc': doc[:40], 'kind': kind,
               'party': (cred if cred is not both else
                         (p1 if _is_plaintiff(p1) or _CREDITOR_RE.search(p1) else p2))[:40],
               'parties': both[:90], 'bp': r.get('reC_BOOKPAGE', '') or '/'.join(bp),
               'amt': round(amt) if amt > 0 else None,
               'st': 'RELEASED' if released else 'OPEN',
               'anchor': 'folio' if (rf and rf == fol) else ('subdivision' if on_parcel else 'person')}
        if own_case:
            row['own_case'] = True                          # this foreclosure's own filing: never a claim
            _ob = _legacy_bucket(r, on_parcel, _legacy_sats)
            if _ob:
                row['old_bucket'] = _ob                     # where the pre-2026-09-25 analyzer summed it
                row['old_amt'] = num(r.get('consideratioN_1'))   # and the unrounded figure it summed
        row['_doc'] = doc                                   # untruncated, for the counting filter
        # untruncated creditor name; when neither side names the owner and neither reads as a
        # creditor, there is no holder to pair a release with (it could be the owner's own name)
        row['_holder'] = _pnorm(cred if cred is not both else
                                (p1 if _is_plaintiff(p1) or _CREDITOR_RE.search(p1) else
                                 p2 if _is_plaintiff(p2) or _CREDITOR_RE.search(p2) else ''))
        if (kind == 'judgment' and not own_case and cred is not both
                and not COMPANY_RE.search(cred) and not _CREDITOR_RE.search(cred)):
            # person against person: the index does not say who won, and a judgment the OWNER won is
            # money owed to them. Flagged for a reader, but still SUMMED: too much debt, never too little.
            row['direction_unknown'] = True
        other.append(row)
    # pair the releases that point nowhere with the liens of their own holder, one to one
    _used = set()                                           # a release frees one lien, whoever holds it
    for h in sorted({o['_holder'] for o in other if o['_holder']}):
        # only rows that would be COUNTED: a waiver, a financing statement or a notice that is no claim
        # never needs a release, and must not stop the holder's real lien from taking one
        _all = sorted((o for o in other if o['_holder'] == h and o['st'] == 'OPEN' and not o.get('own_case')
                       and o['kind'] != 'lis_pendens' and _parse_recd(o['d'])
                       and not _NOT_CLAIM_DOC_RE.search(o['_doc'])
                       and not (o['kind'] == 'other' and not _CLAIM_DOC_RE.search(o['_doc']))),
                      key=lambda o: _parse_recd(o['d']))
        # a lien on the parcel takes a release on the parcel; a person-wide one (a tax lien, a money
        # judgment, no folio) also takes a release that names the owner, wherever it is indexed
        _parcel = [o for o in _all if o['anchor'] != 'person']
        # a release on the parcel belongs to the parcel's liens first: when this holder has any there, a
        # person-wide lien takes only a folio-less release naming the owner, never the parcel's leftover
        for mine, ok in ((_parcel, lambda p: p),
                         ([o for o in _all if o['anchor'] == 'person'], (lambda p: not p) if _parcel else (lambda p: True))):
            rels = sorted((d, k) for k, (d, ps, here) in enumerate(unref_rel)
                          if d and h in ps and k not in _used and ok(here))
            if not mine or not rels:
                continue
            left, i, took = list(rels), 0, []
            for o in mine:                                  # oldest lien takes the oldest release on/after it
                j = next((k for k, (d, _) in enumerate(left) if d >= _parse_recd(o['d'])), None)
                if j is None:
                    break
                took.append(left.pop(j)[1]); i += 1
            if i == len(mine):
                _used.update(took)
                for o in mine:
                    o['st'] = 'RELEASED'; o['released_by'] = 'unreferenced release, one per lien'
            else:
                for o in mine:
                    o['release_unmatched'] = len(rels)      # releases seen, too few to say which lien
    for row in other:
        row.pop('_holder', None)
        _full = row.pop('_doc', row['doc'])
        kind = row['kind']
        if row['st'] == 'RELEASED' or row.get('own_case') or kind == 'lis_pendens':
            continue
        if _NOT_CLAIM_DOC_RE.search(_full) or (kind == 'other' and not _CLAIM_DOC_RE.search(_full)):
            continue                                        # a title certificate, a financing statement,
                                                            # a waiver: shown, never counted
        if not row['amt']:
            other_unpriced += 1                             # found, open, amount not published: a count
            continue
        if kind in ('irs', 'state_tax'):
            irs_open += row['amt']
        elif kind == 'association':
            hoa_open += row['amt']
        else:
            code_open += row['amt']                         # code/muni, money judgments, any other lienor
    # confidence: we must have isolated by a real anchor, sane count, and not a common-name over-match
    conf = 'ok'
    if not subj_subdiv: conf = 'low'                   # couldn't anchor the property (no folio-carrying record)
    if len(opens) > 4: conf = 'low'                    # one parcel rarely has >4 live mortgages
    if len(models) > 45: conf = 'low'                  # busy/common name -> results unreliable
    # always present (None = looked, found none), so a cached chain from before this check can be
    # told apart from one that passed it -- see clear_undocumented()
    _fc = bank_foreclosure(models, fol, subj_subdiv) if ftype == 'HOA' else None
    second_fc = _fc if _fc and not _fc.get('unsure') else None
    # a lender filing on one of this owner's units in the building, unit not established: never a
    # 2ND FORECLOSURE flag on this parcel, never a clear either (equity_state.coverage_documented)
    second_fc_unsure = _fc if _fc and _fc.get('unsure') else None
    # SEARCH COVERAGE, written down so a CLEAR can be checked rather than trusted (equity_state
    # .coverage_documented): how many records the search returned, what anchored the parcel, and
    # the mortgages whose amount the index does not publish.
    return {'second_fc': second_fc, 'second_fc_unsure': second_fc_unsure, 'nrec': len(models),
            'anchor': 'folio+subdivision' if subj_subdiv else '',
            'mtg_open_unpriced': unpriced_open,
            'liens': liens, 'open_count': len(opens), 'junior': junior, 'first_est': first_amt,
            'surv': surv, 'surv_first': surv_first, 'juniors_post': juniors_post,
            'hoa_open': hoa_open, 'code_open': code_open, 'irs_open': irs_open,
            'other': other, 'other_open_unpriced': other_unpriced,
            # the foreclosed first's RECORDED face, and the judgment it is foreclosed for: the debt
            # is the judgment, never the face (verification defect 2: 2024-006803 $417,000 face,
            # $1,022,358.91 judgment)
            'first_face': first_amt, 'first_bp': first_bp, 'judgment': judgment or 0,
            # the county search returns at most 500 records; a full page cannot prove what is not
            # on it (equity_state.coverage_documented), and a search that never returned the
            # subject folio may be a namesake's records (verification defect 3)
            'capped': len(models) >= 500, 'parcel_found': parcel_found,
            'ftype': ftype, 'conf': conf, 'subdiv': subj_subdiv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', default='')
    ap.add_argument('--tier', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--retries', type=int, default=40,
                    help='max previously-FAILED traces to retry this run (nightly default 40; '
                         'raise to clear a backlog — camoufox mints are free, 2Captcha ~$0.003)')
    ap.add_argument('--cached-only', action='store_true', help="only owners with a cached search token (fast, no browser)")
    ap.add_argument('--reanalyze', action='store_true',
                    help="$0: re-run the chain analysis on cached chains traced before the lien rows "
                         "existed (no 'other' key), using ONLY cached search tokens. Never mints, never "
                         "opens a browser, never pays; a dead token leaves the old chain as it was.")
    ap.add_argument('--repull', action='store_true',
                    help="the --reanalyze chains, but PAID: a chain with no cached token or an expired "
                         "one gets a fresh search (Camoufox first, free; then 2Captcha). Needs --max-spend.")
    ap.add_argument('--max-spend', type=float, default=None,
                    help="hard dollar cap on 2Captcha solves (at most $%.2f); each submitted solve counts "
                         "~$%s and the run stops paying at the cap. With --spend-ledger it is the TOTAL for "
                         "that ledger across runs, and the ledger keeps the lowest total ever given"
                         % (MAX_SPEND_CEILING, PAID_SOLVE_USD))
    ap.add_argument('--spend-ledger', default='',
                    help="JSON file that makes --max-spend a total across runs (what earlier runs "
                         "spent is subtracted; the total in the file can be lowered, never raised). "
                         "--repull also records each chain it paid to search, so none is paid for twice")
    ap.add_argument('--no-camoufox', action='store_true',
                    help="skip the free Camoufox token mint and go straight to 2Captcha "
                         "(escape hatch for the day the county stops issuing tokens to it)")
    ap.add_argument('--persist', action='store_true', help="never give up on the captcha — keep minting with back-off until it yields (per-lead cap via MINT_ATTEMPTS env, default 25). This is how we FIGURE OUT the surviving-senior for every lead no matter how hostile the wall is.")
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.max_spend is not None and not (0 < a.max_spend <= MAX_SPEND_CEILING):
        ap.error('--max-spend must be above 0 and at most %.2f' % MAX_SPEND_CEILING)
    if a.repull and (a.max_spend is None or not a.spend_ledger):
        ap.error('--repull pays for searches; give it a --max-spend and a --spend-ledger')
    if a.spend_ledger and a.max_spend is None:
        ap.error('--spend-ledger needs a --max-spend')
    if a.repull and a.cached_only:
        ap.error('--repull and --cached-only contradict each other')
    _SPEND.update(cap=a.max_spend, submits=0, bal0=None, prior=0.0, ledger=None, led=None, stopped='',
                  unit=PAID_SOLVE_USD)
    _lock = None
    if a.spend_ledger and not a.dry_run:
        _lock = _ledger_lock(a.spend_ledger)
        if not _lock:
            ap.error('another run holds %s.lock; wait for it (or delete the lock if no run is going)'
                     % a.spend_ledger)
    _SPEND['lock'] = _lock
    try:
        if a.spend_ledger:
            _SPEND['cap'], _SPEND['prior'], _SPEND['led'] = _ledger_open(a.spend_ledger, a.max_spend)
            _SPEND['ledger'] = a.spend_ledger
            # the dearest price an earlier run saw a solve really cost, so this run counts at it from
            # its first solve rather than learning it again 20 solves in
            _SPEND['unit'] = max(PAID_SOLVE_USD, float(_SPEND['led'].get('unit_usd') or 0))
        _run(a, ap)
    finally:
        _SPEND['lock'] = None
        if _lock:
            try:
                if open(_lock, encoding='utf-8').read() == _SPEND.get('lock_id'):
                    os.remove(_lock)                          # only our own: never a later run's
            except OSError:
                pass


def _run(a, ap):
    if a.repull:
        a.reanalyze = True                                    # same chains, same keep rule, but it may mint
    elif a.reanalyze:
        a.cached_only = True                                  # no mint, no browser, no captcha: $0

    leads = json.load(open(LEADS, encoding='utf-8'))
    # THE LP BOARD WAS NEVER PULLED. leads_final.json is the AUCTION board — 370 rows that already
    # have a judgment. lp_leads.json is 1,007 lis-pendens rows that by definition do NOT yet, and
    # nothing has ever pointed a mortgage pull at them. So every fresh filing sat at "debt unknown"
    # permanently, and a closer reading value-with-no-debt reads it as equity. That is exactly the
    # state that put an underwater owner (Sisavath, 4118 41st Way) on a live call: value $225,675,
    # no payoff on the row, board printing 91%.
    # Miami-Dade only here — this module speaks to the MDC recorder. Broward/PB LP rows are pulled
    # by broward_liens.py / palmbeach_liens.py against their own counties.
    # Keys are translated to the fat shape the loop below reads ('Case #', 'owner_clean', 'folio'),
    # and a case already present from leads_final wins, so nothing is double-pulled.
    try:
        _lp_path = os.path.join(HERE, 'lp_leads.json')
        if os.path.exists(_lp_path):
            _have = {(r.get('Case #') or '') for r in leads}
            _lp_added = 0
            for _r in json.load(open(_lp_path, encoding='utf-8')):
                if not isinstance(_r, dict):
                    continue
                _c = str(_r.get('case') or '')
                if not _c or _c in _have:
                    continue
                if str(_r.get('county') or '').upper() not in ('', 'MIAMI-DADE', 'MIAMI DADE'):
                    continue
                # NAME ORDER IS LOAD-BEARING AND IT FAILS SILENTLY. _name_parts() above takes the
                # LAST token as the surname because leads_final's owner_clean is "FIRST [MIDDLE]
                # LAST". The LP board is the other way round: oname is "LABISSIERE, FRANTZ" and
                # owners is "LABISSIERE FRANTZ". Handing either straight over searches surname
                # FRANTZ, given "LABISSIERE," — which returns ZERO documents and is then reported
                # as "ok  ...  0 open mtg". An empty search that prints like a clean title check is
                # the single most dangerous output this repo can produce, so flip it here.
                _own = str(_r.get('oname') or '').strip()
                if ',' in _own:                                   # "LAST, FIRST M" -> "FIRST M LAST"
                    _last, _rest = _own.split(',', 1)
                    _own = '%s %s' % (_rest.strip(), _last.strip())
                elif not _own:
                    _own = str(_r.get('owners') or '').strip()
                _own = ' '.join(_own.split())
                if not _own:
                    continue
                leads.append({'Case #': _c, 'owner_clean': _own,
                              # 'Folio' with a capital F — that is the key the loop reads at ~L684
                              # ('folio' lowercase silently yields no isolation, and analyze() then
                              # returns nothing, which prints as "0 open mtg" = a clean title check
                              # that never happened). Set both so neither spelling can lose.
                              'Folio': str(_r.get('folio') or ''),
                              'folio': str(_r.get('folio') or ''),
                              'Address': str(_r.get('addr') or ''),
                              'tier': str(_r.get('tier') or ''), '_src': 'lp'})
                _have.add(_c)
                _lp_added += 1
            if _lp_added:
                print('  + %d Miami-Dade LP lead(s) folded in from lp_leads.json '
                      '(never pulled before this)' % _lp_added)
    except Exception as _e:                                   # never let this kill a nightly run
        print('  ! LP fold-in skipped: %s' % _e)
    qs_cache = json.load(open(QS_CACHE, encoding='utf-8')) if os.path.exists(QS_CACHE) else {}
    out = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}

    picked = []
    md_retries = []          # previously-failed traces, retried within the run's budget
    no_token = []            # --reanalyze: pre-lien chains with no cached token (counted, never minted)
    skipped = {}
    for r in leads:
        case = r.get('Case #', '') or ''
        if a.case and case != a.case: continue
        if a.tier and (r.get('tier', '') or '') != a.tier: continue
        oc = (r.get('owner_clean', '') or '').strip()
        if not oc: continue                                        # kimi: companies traced too (folio-isolated)
        # Government bodies and bare street addresses cost a mint every run and return nothing.
        # --case overrides, so a human can still force one if they have a reason.
        if not a.case:
            why = untraceable_owner(oc)
            if why:
                skipped.setdefault(why, []).append(oc)
                continue
        if a.reanalyze:
            # 12-case verification 2026-09-24, defect 1: chains traced before the lien rows existed
            # dropped liens their own search returned. The token is cached, so re-reading the same
            # search and re-running analyze() costs a plain GET and nothing else. A chain with no
            # cached token is only counted: a fresh search needs a mint, which is Alex's call.
            _old = out.get(case) or {}
            if case in out and 'other' not in _old and _old.get('conf') in ('ok', 'low'):
                if a.repull and _old.get('repull_tried'):
                    skipped.setdefault('already re-searched by --repull on %s' % _old['repull_tried'], []).append(oc)
                    continue                                # paid for once; a second search finds the same
                (picked if oc in qs_cache or a.repull else no_token).append(r)
            continue
        if a.cached_only and oc not in qs_cache: continue
        if case in out and not a.case:
            # A FAILED trace is not a result. Miami-Dade cached conf 'none' FOREVER, so 259 of 370
            # MD leads were frozen as "equity unverified" and the tracer reported nothing left to
            # do — the same bug Broward fixed 2026-08-18 (broward_liens ~L474), which MD never got.
            # It matters more here than it did there: MD returns 'none' when there is no FOLIO to
            # isolate by (analyze(), ~L377), and stub_resolve.py now BACKFILLS folios — so a lead
            # that legitimately failed last week can succeed today with no other change.
            # Capped per run: a mint costs ~$0.003, so retries are bounded like fresh pulls.
            if (out.get(case) or {}).get('conf') == 'none':
                md_retries.append(r)
            elif has_duplicate_liens(out.get(case)) or clear_undocumented(out.get(case), case):
                # traced before the one-instrument rule / the coverage record existed: the cached
                # chain either double counts a mortgage or claims CLEAR with no record of how it
                # searched (and, on an association case, never asked whether a lender is
                # foreclosing the same unit). Re-pull it FIRST — a known-wrong answer outranks a
                # still-unknown one.
                md_retries.insert(0, r)
            continue
        picked.append(r)
    if a.repull:
        # $0 re-reads first; a chain flagged for a wider search pays, so it waits with the untokened
        picked.sort(key=lambda r: (r.get('owner_clean', '') or '').strip() not in qs_cache
                    or bool((out.get(r.get('Case #', '')) or {}).get('wider_repull')))
    if a.limit: picked = picked[:a.limit]
    # append retries AFTER the fresh cap so new leads always win the budget
    if md_retries:
        room = max(0, (a.limit or len(picked) + a.retries) - len(picked))
        take = md_retries[:min(room, a.retries)]
        if take:
            print(f"retrying {len(take)} previously-failed trace(s) of {len(md_retries)} "
                  f"(folios backfilled since; a cached failure is not a result)")
            picked += take

    cached = sum(1 for r in picked if (r.get('owner_clean','') or '').strip() in qs_cache)
    print(f"{len(picked)} lead(s) to pull ({cached} via cached token / requests, {len(picked)-cached} need a mint)")
    if a.repull:
        _need = len(picked) - cached
        print(f"  --repull: at most ${_usd(a.max_spend)} of 2Captcha; the {_need} without a token (and any "
              f"expired token) try Camoufox first, free")
    elif a.reanalyze:
        print(f"  --reanalyze: {len(no_token)} older chain(s) have no cached token and stay as they are "
              f"(a fresh search would need a mint, ~$0.0033 each; not done here)")
    # Say what was dropped and why. A silent filter reads as "there was nothing there".
    for why, names in sorted(skipped.items()):
        uniq = sorted(set(names))
        print(f"  skipped {len(names)} lead(s) — {why}: "
              + ', '.join(n[:34] for n in uniq[:3]) + (' ...' if len(uniq) > 3 else ''))
    if a.dry_run or not picked:
        for r in picked[:20]:
            oc=(r.get('owner_clean','') or '').strip()
            _w = a.repull and (out.get(r.get('Case #', '')) or {}).get('wider_repull')
            print(f"  {r.get('Case #',''):22} {oc:26} {'WIDER (paid)' if _w else 'cached' if oc in qs_cache else 'MINT'}")
        return

    # One Camoufox for the whole batch, opened only when there is actually something to mint.
    cf_cm = cf_browser = None
    # --repull: a cached token may have expired, and finding that out must not skip the free mint
    need_mint = a.repull or any((r.get('owner_clean', '') or '').strip() not in qs_cache for r in picked)
    if need_mint and not a.cached_only and not a.no_camoufox:
        cf_cm, cf_browser = camoufox_session()
        print('  camoufox: %s' % ('ready (free Turnstile tokens)' if cf_browser
                                  else 'UNAVAILABLE — %s; using 2Captcha' % CF_UNAVAILABLE))

    if _SPEND['cap'] is not None:
        _SPEND['bal0'] = _balance()
        if _SPEND['bal0'] is None:
            _SPEND['stopped'] = '2Captcha balance could not be read at the start'
            print('  2captcha: balance unreadable, so no paid solves this run (free paths only)')
        else:
            print(f"  2captcha: balance ${_SPEND['bal0']:.4f}; cap ${_usd(_SPEND['cap'])}"
                  + (f", ${_SPEND['prior']:.4f} already spent against {a.spend_ledger}" if a.spend_ledger else ''))

    done = hits = cf_free = paid = kept = capped = merged = 0
    try:
        for r in picked:
            _lock_touch()
            case = r.get('Case #', ''); oc = (r.get('owner_clean', '') or '').strip()
            folio = r.get('Folio', '') or r.get('year_folio', '')
            judg = num(r.get('judgment'))
            models = None
            _paid0 = paid
            _def_blocked = False
            try:
                import stub_resolve as _sr
                _co = _sr.people_from(r.get('defendants') or '')
            except Exception:
                _co = []
            _src = None                                       # which search produced `models`
            # a chain an earlier re-read found narrower is searched the widest way there is: the paid
            # surname-only search. The cached token and Camoufox (first AND last name) are what
            # came back narrower, so they are skipped for it.
            _wider = bool(a.repull and (out.get(case) or {}).get('wider_repull'))
            if oc in qs_cache and not _wider:
                models = records_by_qs(qs_cache[oc])          # free: reuse a still-valid cached token
                _src = 'cache'
                if a.repull and not _parcel_in(models, folio):
                    # an expired token can come back EMPTY rather than failing, and a chain first found
                    # through a defendant's name is not in the owner's results: either way the cached
                    # search cannot re-read this parcel, so search afresh (free first) instead of keeping
                    models = None
            if models is None and not a.cached_only:
                sp = split_owner(oc)
                if sp:
                    # 1) CAMOUFOX (2026-08-22): the browser mints its own Turnstile token, so this costs
                    #    nothing. Tried before 2Captcha for exactly that reason. A captured qs is written
                    #    back to records_qs.json, which puts the NEXT run for this owner on the free
                    #    plain-requests path above — the saving compounds instead of repeating.
                    #    Any failure just falls through to the paid path below; it never ends the run.
                    if cf_browser is not None and not _wider:
                        try:
                            qs = camoufox_qs(cf_browser, sp)
                        except Exception as e:
                            qs = None
                            print(f'  camoufox errored ({str(e)[:70]}) — falling back to 2Captcha')
                        if qs:
                            models = records_by_qs(qs)
                            if models is not None:
                                cf_free += 1
                                _src = 'camoufox'
                                qs_cache[oc] = qs
                                try:
                                    json.dump(qs_cache, open(QS_CACHE, 'w', encoding='utf-8'), indent=1)
                                except Exception:
                                    pass

                    # 2) 2CAPTCHA (2026-07-21): solve Turnstile for ~$0.003, no browser. This is what
                    # took the wall from ~15% coverage to near-total, and it stays as the fallback for
                    # the day Turnstile stops handing out free tokens. Browser mint is the last resort —
                    # skip it silently when the JS template is missing (site migrated away from the old
                    # reCAPTCHA v3 the mint code was built for), so a broken fallback never masks a real
                    # Turnstile failure. `--persist` on the batch is a no-op when the fallback is dead.
                    if models is None:
                        paid += 1
                        models = fetch_via_turnstile(sp)
                        _src = 'paid'
                    if models is None:
                        _src = 'mint'
                        src = open(os.path.join(HERE, 'gen_records_qs.py'), encoding='utf-8').read()
                        if 'JS = r"""' in src:
                            models = mint_and_fetch(sp, persist=a.persist)
            # DEFENDANT FALLBACK. Measured 2026-08-27: of 259 MD leads stuck at conf 'none',
            # 144 HAD a folio and still returned nothing — because the search key is the
            # APPRAISER's owner-of-record, which is often not how the CLERK indexes the party:
            # estates ('HAYDEE BETETA (ESTATE OF)'), entities, trusts, post-deed owners. The
            # court, however, names the defendant it is actually foreclosing. So when the owner
            # name finds nothing, search the DEFENDANTS. analyze() still isolates by folio +
            # subdivision, so a wrong-person hit cannot pollute the number — worst case is
            # another empty result, same as now.
            _searched = oc
            _owner_models = _owner_src = None
            if a.repull and models is not None and not _parcel_in(models, folio):
                # the owner's name does not reach this parcel (a chain first found through a
                # defendant): try the defendants too, and fall back to this result if they fail
                _owner_models, models = models, None
                _owner_src = _src
            if models is None and not a.cached_only:
                _sp0 = split_owner(oc)
                # the paid search asks for the SURNAME only, so a spouse's paid search after the
                # owner's is the same query; Camoufox fills the first name too, so it is not
                _paid_sn = {_sp0[0].upper()} if _sp0 and paid > _paid0 else set()
                _def_blocked = False                          # a defendant search the clerk never answered
                for _last, _first in _co[:2]:
                    _nm = '%s %s' % (_first, _last)          # split_owner wants FIRST ... LAST
                    if _nm.strip().upper() == oc.strip().upper():
                        continue                              # already tried as the owner
                    _sp = split_owner(_nm)
                    if not _sp:
                        continue
                    if cf_browser is not None and not _wider:
                        try:
                            _qs = camoufox_qs(cf_browser, _sp)
                        except Exception:
                            _qs = None
                        if _qs:
                            models = records_by_qs(_qs)
                            if models is not None:
                                cf_free += 1
                                _src = 'camoufox'
                    if models is None and _sp[0].upper() not in _paid_sn:
                        _paid_sn.add(_sp[0].upper())
                        paid += 1
                        models = fetch_via_turnstile(_sp)
                        _src = 'paid'
                        _def_blocked = _def_blocked or models is None
                    if models is not None and a.repull and not _parcel_in(models, folio):
                        models = None                         # not this parcel either; next defendant
                    if models is not None:
                        _searched = _nm + ' (defendant)'
                        break
            if models is None and _owner_models is not None:
                models, _src = _owner_models, _owner_src
            if models is None:
                if _SPEND['stopped'] and not a.cached_only:
                    capped += 1
                    print(f"  $$  {case:22} {oc:26} not pulled: spend cap ({_SPEND['stopped']})")
                else:
                    print(f"  --  {case:22} {oc:26} (no records / blocked)")
                    # never marked here: the owner's search was not answered (a 503, a dead browser, an
                    # unsolved captcha), so nothing was found out and a later run retries. A search the
                    # clerk answered with nothing on this parcel is marked below, as 'old chain kept'.
                continue
            res = analyze(models, folio, judg, ftype=_fc_type(case, r.get('case_type'), r.get('plaintiff') or ''), plaintiff=r.get('plaintiff') or '',
                          owner=_searched, case=case, co_owners=_co)
            res['searched_as'] = _searched
            res['case_type'] = r.get('case_type') or ''     # the lead's own reading of who is foreclosing
            if a.reanalyze:
                _old = out.get(case)
                if _old and not (res.get('nrec') and res.get('parcel_found')):
                    if a.repull and _SPEND['stopped']:
                        # the cap stopped the search part-way (the defendants were never asked):
                        # not a finding, and not marked, so a later run with budget can finish it
                        capped += 1
                        print(f"  $$  {case:22} {oc:26} not fully searched: spend cap ({_SPEND['stopped']})")
                        continue
                    # The cached token came back empty, or no longer carries this folio: that is a
                    # failed re-read, not news that the recorded mortgages went away. Keep the chain.
                    kept += 1
                    print(f"  ..  {case:22} {oc:26} re-read found nothing on this parcel; old chain kept")
                    if a.repull and not _def_blocked:
                        # the searches were answered and none reached this parcel: marked, never paid
                        # for again. A defendant the clerk never answered leaves it for a later run.
                        out[case]['repull_tried'] = time.strftime('%Y-%m-%d')
                        json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
                    continue
                if _old and _mortgages_narrower(_old, res):
                    # The old chain may have come from a wider search (a surname-only 2Captcha search,
                    # a defendant's name) than this re-read. It stands whole; the new lien rows are
                    # listed beside it, not counted, and the chain waits for a wider search.
                    out[case] = _lay_lien_rows(_old, res)
                    if a.repull and _src == 'paid':
                        # the widest search there is came back narrower: never paid for again. A
                        # free re-read (cached token, Camoufox) only flags it, and the next --repull
                        # goes straight to the paid surname search.
                        out[case]['repull_tried'] = time.strftime('%Y-%m-%d')
                    merged += 1
                    print(f"  ++  {case:22} {oc:26} narrower re-read: earlier chain kept, "
                          f"{len(out[case]['other_seen'])} lien row(s) listed, not counted")
                    json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
                    continue
                else:
                    _new = res
                    res = dict(_old or {}, **res)            # keep keys other steps wrote (chain_note)
                    for _k in ('other_seen', 'wider_repull'):
                        res.pop(_k, None)                    # an earlier narrower re-read's, now answered
                    if _old:
                        # the mortgages agree, but the new search may still be narrower on liens
                        _carry_lien_totals(_old, _new, res)
            res['traced'] = time.strftime('%Y-%m-%d'); res['folio'] = norm_folio(folio); res['owner'] = oc
            out[case] = res
            done += 1
            flag = ''
            if (res.get('open_count') or 0) >= 2:
                hits += 1; flag = f"  <-- OPEN 2ND ~${res.get('junior') or 0:,} (of {res['open_count']} open mtgs)"
            print(f"  ok  {case:22} {oc:26} {res.get('open_count') or 0} open mtg{flag}")
            json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
            time.sleep(0.4)
    finally:
        if cf_cm is not None:
            try:
                cf_cm.__exit__(None, None, None)
            except Exception:
                pass

    print(f"\nDONE: {done} traced, {hits} with a surviving 2nd mortgage. -> records_liens.json")
    if a.repull:
        print(f"     --repull: {done} chain(s) re-read, {merged} narrower than the earlier search (earlier "
              f"chain kept, needs a wider search), {kept} re-read(s) found nothing on the parcel "
              f"(old chain kept), {capped} not pulled because of the cap, "
              f"{len(picked) - done - merged - kept - capped} with no records or blocked")
    elif a.reanalyze:
        print(f"     --reanalyze: {done} chain(s) re-read, {merged} narrower than the earlier search (earlier "
              f"chain kept, lien rows listed not counted: a --repull candidate)")
        print(f"     --reanalyze: {len(picked) - done - merged - kept} cached token(s) had expired and {kept} re-read(s) "
              f"found nothing on the parcel; those chains, and the {len(no_token)} without a token, keep "
              f"their old lien picture until a paid re-pull")
    if cf_free or paid:
        # paid counts owners that reached fetch_via_turnstile; each of those is a 2Captcha solve
        # (~$0.003) that a free Camoufox token would have avoided.
        print(f"     token source: {cf_free} free (camoufox) / {paid} owner search(es) sent to 2captcha, "
              f"{_SPEND['submits']} solve(s) submitted  ~${_SPEND['submits'] * (_SPEND.get('unit') or PAID_SOLVE_USD):.3f} spent, "
              f"~${cf_free * PAID_SOLVE_USD:.3f} avoided")
    if _SPEND['cap'] is not None:
        b1 = _balance() if _SPEND['bal0'] is not None else None
        print(f"     2captcha cap ${_usd(_SPEND['cap'])}: {_SPEND['submits']} solve(s) submitted, "
              f"~${_SPEND['submits'] * (_SPEND.get('unit') or PAID_SOLVE_USD):.3f} counted")
        if _SPEND['ledger']:
            _ledger_save(charged=(_SPEND['bal0'] - b1) if b1 is not None else None, final=True)
            _cu = (_SPEND.get('led') or {}).get('counted_usd')
            print(f"     ledger {_SPEND['ledger']}: " + (f"${_cu:.4f} of ${_usd(_SPEND['cap'])} used" if _cu is not None
                                                        else "not written by this run (another run holds it)"))
        if b1 is not None:
            print(f"     ACTUAL CHARGE: balance ${_SPEND['bal0']:.4f} -> ${b1:.4f} = ${_SPEND['bal0'] - b1:.4f} "
                  f"(account-wide: anything else solving at the same time counts too)")
        if _SPEND['stopped']:
            print(f"     stopped paying: {_SPEND['stopped']}")


if __name__ == '__main__':
    main()
