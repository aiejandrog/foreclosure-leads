#!/usr/bin/env python
"""phone_validate — ask the phone network whether a number is still connected, before he dials it.

WHY (2026-10-19). Alejandro's Call Mode day: 34 dials, 3 answered, 24 no answer, 7 wrong or
disconnected. The skip trace hands back numbers that were good once; nothing in this pipeline checks
whether they still are, so a dead number costs a dial, a ring-out and a minute of his day every
time it comes round in the queue. phone_rank already orders numbers by what the skip trace SAYS
they are (mobile beats landline, VoIP scores down). This file adds what the NETWORK says.

WHAT IT CALLS. Twilio Lookup v2, `Fields=line_status`:
    GET https://lookups.twilio.com/v2/PhoneNumbers/+1XXXXXXXXXX?Fields=line_status
    -> {"line_status": {"status": "active|reachable|unreachable|inactive|unknown", "error_code": ..}}
  inactive     not assigned to any subscriber (disconnected / never issued). Call Mode drops it.
  unreachable  assigned, but the handset is off or out of reach right now. Call Mode dials it last.
  active / reachable / unknown   no change.
Line Status is a Twilio PUBLIC BETA and is described in mobile-network terms; US landlines may come
back `unknown`, which changes nothing. `--with-type` also asks for line_type_intelligence and caches
the type (mobile / landline / VoIP), which is stored but not yet used by any build.

PRICE (twilio.com/en-us/lookup/pricing, read 2026-10-19): Line Status $0.007 per request (first
100,000), Line Type Intelligence $0.008. Every request that reaches Twilio is counted at that price
BEFORE the call, so the ledger runs high, never low. TWILIO_VALIDATE_DAILY_CAP (default $2.00) caps
the calendar day across every run on this machine; --max-spend caps one run. At $0.007 a $2 day is
285 numbers.

WHICH NUMBERS, each checked at most once per TWILIO_VALIDATE_MAX_AGE_DAYS (60). By default only
the Call Mode dial queue, in queue order: the numbers someone is actually about to dial. On 10-20
the wider set was 13,477 numbers (~$94), most of them on leads nobody will call, and Alejandro chose
queue only. If the queue cannot be built, nothing is checked. TWILIO_VALIDATE_SCOPE=all adds, after
the queue:
  - every other number on the board twin
  - numbers in skiptrace_results.json that have not reached the board yet
DNC-flagged numbers are never looked up: they are never dialled, so there is nothing to learn.

CREDENTIALS. DEALFLOW_DIR/twilio.key (or TWILIO_KEY_FILE): Account SID on line 1, Auth Token on
line 2. Never printed, never logged, never written anywhere else. No key file = skipped, exit 0.

PRIVACY. The cache (DEALFLOW_DIR/phone_validation.json) never holds a phone number: entries are keyed
by a SHA-256 of the 10 digits. That keeps plaintext numbers out of the file; it is not secrecy (ten
digits can be brute-forced), which is why the file stays in DEALFLOW_DIR and is never committed.
Status file DEALFLOW_DIR/phone_validate_status.json carries counts and dollars only.

    python phone_validate.py                 # nightly: check what is due, inside the caps
    python phone_validate.py --dry-run       # how many would be checked and what it would cost
    python phone_validate.py --stats         # what the cache knows, no network
    python phone_validate.py --check         # prove twilio.key with a free request
    python phone_validate.py --number 3055550100   # one number (still capped and cached)

Tests: _phonevalidatetest.py (no network, no key).
"""
import argparse
import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

LINE_STATUS_USD = 0.007
LINE_TYPE_USD = 0.008
BASE = 'https://lookups.twilio.com/v2/PhoneNumbers/'

# The verdicts Call Mode acts on. Everything else (active, reachable, unknown, an error) is no change.
DEAD = 'inactive'
UNREACHABLE = 'unreachable'
# "Handset off right now" is a moment, not a fact about the number, and the nightly check runs at
# dawn when phones are off. Rechecked after this many days so one dawn check cannot bury a good
# mobile at the end of its card for two months.
UNREACHABLE_AGE_DAYS = 3


def _float_env(name, default):
    try:
        v = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return 0.0                                   # a garbled cap spends nothing
    return v if v == v and v >= 0 else 0.0


def daily_cap():
    return _float_env('TWILIO_VALIDATE_DAILY_CAP', '2.00')


def max_age_days():
    try:
        return max(1, int(os.environ.get('TWILIO_VALIDATE_MAX_AGE_DAYS', '60')))
    except ValueError:
        return 60


def scope():
    """'queue' (default) or 'all'. Anything else reads as 'queue', the cheaper one."""
    return 'all' if (os.environ.get('TWILIO_VALIDATE_SCOPE') or '').strip().lower() == 'all' else 'queue'


def cache_path():
    return os.environ.get('TWILIO_VALIDATE_CACHE') or os.path.join(P.DEALFLOW_DIR, 'phone_validation.json')


def status_path():
    return os.path.join(os.path.dirname(cache_path()), 'phone_validate_status.json')


def key_path():
    if os.environ.get('TWILIO_KEY_FILE'):
        return os.environ['TWILIO_KEY_FILE']
    p = os.path.join(P.DEALFLOW_DIR, 'twilio.key')
    return p if os.path.exists(p) else os.path.join(HERE, 'twilio.key')


def ph10(v):
    d = ''.join(c for c in str(v or '') if c.isdigit())
    if len(d) == 11 and d[0] == '1':
        d = d[1:]
    return d if len(d) == 10 else ''


def phone_key(v):
    d = ph10(v)
    return hashlib.sha256(('dealflow-phone:' + d).encode()).hexdigest()[:24] if d else ''


# ---- cache -----------------------------------------------------------------------------------

def load_cache(path=None):
    """-> dict, or None when the file exists but cannot be read. A torn cache is never overwritten
    by an empty one: the run stops instead, so paid answers are not thrown away."""
    path = path or cache_path()
    if not os.path.exists(path):
        return {'numbers': {}, 'spend': {}}
    try:
        c = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(c, dict):
        return None
    c.setdefault('numbers', {})
    c.setdefault('spend', {})
    if not isinstance(c['numbers'], dict) or not isinstance(c['spend'], dict):
        return None
    return c


def save_cache(c, path=None):
    path = path or cache_path()
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(c, f, separators=(',', ':'))
    os.replace(tmp, path)


_VERDICTS = {'mtime': None, 'map': {}}


def verdicts():
    """{phone_key: line status} from the cache, memoized on its mtime. Missing or unreadable cache
    -> {} (no number is dropped on evidence nobody has). Used by call_mode.call_rows.
    An `inactive` verdict older than TWILIO_VALIDATE_MAX_AGE_DAYS is left out, so the number goes
    back into the Call Mode queue and, being in the queue and stale, is checked again: a number
    that was reconnected is not kept out for good."""
    path = cache_path()
    try:
        m = (os.path.getmtime(path), today())
    except OSError:
        return {}
    if _VERDICTS['mtime'] != m:
        c = load_cache(path) or {'numbers': {}}
        old = time.time() - max_age_days() * 86400
        _VERDICTS['map'] = {k: v.get('ls') for k, v in c['numbers'].items()
                            if isinstance(v, dict) and v.get('ls')
                            and not (v.get('ls') == DEAD and float(v.get('ts') or 0) < old)}
        _VERDICTS['mtime'] = m
    return _VERDICTS['map']


def line_status(number, vmap=None):
    """'inactive' / 'unreachable' / 'active' / ... / '' (never checked) for one number."""
    k = phone_key(number)
    return (verdicts() if vmap is None else vmap).get(k, '') if k else ''


# ---- spend -----------------------------------------------------------------------------------

def today():
    return dt.date.today().isoformat()


def spent_today(c):
    try:
        return float(c['spend'].get(today(), 0) or 0)
    except (TypeError, ValueError):
        return daily_cap()                           # unreadable day total reads as spent


def unit_price(with_type):
    return LINE_STATUS_USD + (LINE_TYPE_USD if with_type else 0)


# ---- which numbers ---------------------------------------------------------------------------

def _queue_numbers():
    """Call Mode's dial queue, in queue order. Empty if the board twin cannot be read."""
    try:
        import callmode_repeats as CR
        import call_mode as CM
        slim = CR.board_rows()
        rows, _ = CM.call_rows(slim, optouts=CR._optouts(), deads=CR._deads(), cap=10 ** 6)
    except BaseException as e:                       # SystemExit from board_rows included
        print('phone_validate: Call Mode queue unreadable (%s)%s' % (
            e.__class__.__name__, '; nothing checked' if scope() == 'queue' else '; falling back to the board'))
        return [], []
    out = []
    for r in rows:
        out.extend(r.get('p') or [])
    return out, slim


def _board_numbers(slim):
    out = []
    for r in slim or []:
        dnc = r.get('phdnc') or []
        for i, p in enumerate(r.get('phones') or []):
            if not (dnc[i] if i < len(dnc) else False):
                out.append(p)
    return out


def _skiptrace_numbers():
    try:
        st = json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
    except (OSError, ValueError):
        return []
    recs = st.values() if isinstance(st, dict) else (st if isinstance(st, list) else [])
    out = []
    for v in recs:
        for p in (v.get('phones') or []) if isinstance(v, dict) else []:
            if isinstance(p, dict):
                if not p.get('dnc'):
                    out.append(p.get('number'))
            else:
                out.append(p)
    return out


def candidates(c, now=None, sources=None):
    """-> ordered list of 10-digit numbers that are due: never checked first, then stale, each
    group in source order (queue, then board and skip trace under scope 'all'). `sources` is for
    the tests."""
    now = now or time.time()
    if sources is None:
        q, slim = _queue_numbers()
        if scope() == 'queue':
            sources = q
        else:
            if not slim:
                try:
                    import callmode_repeats as CR
                    slim = CR.board_rows()
                except BaseException:
                    slim = []
            sources = q + _board_numbers(slim) + _skiptrace_numbers()
    max_age = max_age_days() * 86400
    seen, fresh, stale = set(), [], []
    for n in sources:
        d = ph10(n)
        if not d or d in seen:
            continue
        seen.add(d)
        e = c['numbers'].get(phone_key(d))
        if not isinstance(e, dict) or not e.get('ts'):
            fresh.append(d)
        elif now - float(e.get('ts') or 0) > (UNREACHABLE_AGE_DAYS * 86400 if e.get('ls') == UNREACHABLE
                                               else max_age):
            stale.append(d)
    return fresh + stale


# ---- Twilio ----------------------------------------------------------------------------------

class AuthError(Exception):
    pass


class StopRun(Exception):
    pass


def load_key(path=None):
    path = path or key_path()
    try:
        lines = [l.strip() for l in open(path, encoding='utf-8-sig').read().splitlines() if l.strip()]
    except OSError:
        return None
    if len(lines) < 2 or not lines[0].startswith('AC') or len(lines[1]) < 20:
        raise SystemExit('phone_validate: %s must hold the Account SID (AC...) on line 1 and the '
                         'Auth Token on line 2' % os.path.basename(path))
    return lines[0], lines[1]


def lookup(number10, creds, with_type=False, opener=None, retries=3, on_retry=None):
    """-> dict parsed from Twilio. Raises AuthError on 401/403, StopRun on a request Twilio refuses
    for the whole account (400 on the Fields, i.e. Line Status not enabled)."""
    fields = 'line_status' + (',line_type_intelligence' if with_type else '')
    url = '%s%%2B1%s?Fields=%s' % (BASE, number10, fields)
    tok = base64.b64encode(('%s:%s' % creds).encode()).decode()
    req = urllib.request.Request(url, headers={'Authorization': 'Basic ' + tok,
                                               'User-Agent': 'dealflow-phone-validate'})
    opener = opener or urllib.request.urlopen
    for attempt in range(retries + 1):
        try:
            with opener(req, timeout=20) as r:
                return json.loads(r.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError('Twilio refused the credentials (HTTP %d)' % e.code)
            if e.code == 404:
                # v2 answers a bad number with 200 and valid:false; a 404 is OUR request being
                # wrong. Recording it as dead would empty the queue one night at a time.
                raise StopRun('Twilio HTTP 404 (request path wrong?) - nothing recorded')
            if e.code == 429 and attempt < retries:
                if on_retry:
                    on_retry()
                time.sleep(2 ** attempt)
                continue
            if e.code == 400:
                raise StopRun('Twilio HTTP 400 on Fields=%s (is Line Status enabled on this account?)' % fields)
            raise StopRun('Twilio HTTP %d' % e.code)
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            if attempt < retries:
                if on_retry:
                    on_retry()                       # the failed try may still have billed
                time.sleep(2 ** attempt)
                continue
            raise StopRun('network: %s' % e.__class__.__name__)


def _entry(resp, with_type):
    ls = (resp.get('line_status') or {}) if isinstance(resp, dict) else {}
    st = str(ls.get('status') or '').strip().lower()
    if resp.get('valid') is False:
        st = DEAD                                    # not a dialable number at all
    e = {'ls': st or 'unknown', 'ts': int(time.time())}
    if ls.get('error_code'):
        e['err'] = ls.get('error_code')
    if with_type:
        lt = (resp.get('line_type_intelligence') or {})
        if lt.get('type'):
            e['lt'] = str(lt['type'])
    return e


# ---- run -------------------------------------------------------------------------------------

class _Lock:
    """One run at a time on this machine (the 05:30 refresh and the 06:00 phones run can overlap).
    A lock older than two hours is taken over: a crashed run must not stop validation for good."""

    def __init__(self, path):
        self.path, self.held = path + '.lock', False

    def __enter__(self):
        try:
            if os.path.exists(self.path) and time.time() - os.path.getmtime(self.path) > 7200:
                os.remove(self.path)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
        except OSError:
            self.held = False
        return self

    def touch(self):
        """A live run refreshes the lock each number, so only a dead run is ever taken over."""
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def __exit__(self, *a):
        if self.held:
            try:
                if open(self.path).read().strip() == str(os.getpid()):   # never remove a taker's lock
                    os.remove(self.path)
            except OSError:
                pass


def _write_status(d):
    try:
        os.makedirs(os.path.dirname(status_path()) or '.', exist_ok=True)
        with open(status_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f, indent=1)
    except OSError:
        pass


def run(numbers=None, max_spend=None, with_type=False, dry_run=False, opener=None, creds=None,
        limit=None):
    """-> status dict. Spends only inside the day cap and max_spend; writes the cache after every
    answer, so a crash keeps what was already paid for."""
    st = {'ts': dt.datetime.now().isoformat(timespec='seconds'), 'checked': 0, 'inactive': 0,
          'unreachable': 0, 'usd': 0.0, 'due': 0, 'stopped': ''}
    cpath = cache_path()
    c = load_cache(cpath)
    if c is None:
        st['stopped'] = 'cache unreadable - not overwritten, nothing spent'
        print('phone_validate: %s' % st['stopped'])
        return st
    due = candidates(c, sources=numbers) if numbers is not None else candidates(c)
    if limit:
        due = due[:limit]
    st['due'] = len(due)
    unit = unit_price(with_type)
    cap = daily_cap()
    left_day = max(0.0, cap - spent_today(c))
    budget = left_day if max_spend is None else min(left_day, max(0.0, max_spend))
    affordable = int(budget / unit + 1e-9)
    print('phone_validate: scope %s' % (scope() if numbers is None else 'given numbers'))
    print('phone_validate: %d number(s) due, $%.3f each, $%.2f left today of $%.2f -> %d affordable'
          % (len(due), unit, left_day, cap, min(affordable, len(due))))
    if dry_run:
        st['stopped'] = 'dry run'
        return st
    if not due:
        _write_status(st)
        return st
    if creds is None:
        creds = load_key()
    if not creds:
        st['stopped'] = 'no twilio.key - skipped'
        print('phone_validate: no %s - skipped, nothing spent' % key_path())
        _write_status(st)
        return st
    with _Lock(cpath) as lk:
        if not lk.held:
            st['stopped'] = 'another run holds the lock'
            print('phone_validate: %s' % st['stopped'])
            return st
        def _charge_retry():
            # A retry is a new paid request: the same two caps apply before it goes out.
            cc = load_cache(cpath)
            if cc is None:
                raise StopRun('cache became unreadable')
            if spent_today(cc) + unit > cap + 1e-9:
                raise StopRun('daily cap $%.2f reached' % cap)
            if max_spend is not None and st['usd'] + unit > max_spend + 1e-9:
                raise StopRun('run cap $%.3f reached' % max_spend)
            cc['spend'][today()] = round(spent_today(cc) + unit, 4)
            save_cache(cc, cpath)
            st['usd'] = round(st['usd'] + unit, 4)

        for d in due:
            lk.touch()
            c = load_cache(cpath)                    # re-read: the day total is the shared counter
            if c is None:
                st['stopped'] = 'cache became unreadable'
                break
            if spent_today(c) + unit > cap + 1e-9:
                st['stopped'] = 'daily cap $%.2f reached' % cap
                break
            if max_spend is not None and st['usd'] + unit > max_spend + 1e-9:
                st['stopped'] = 'run cap $%.3f reached' % max_spend
                break
            # COUNT BEFORE THE CALL: a request that reaches Twilio may bill even if we never read
            # the answer, and a crash between the call and the write must not forget it.
            c['spend'][today()] = round(spent_today(c) + unit, 4)
            save_cache(c, cpath)
            st['usd'] = round(st['usd'] + unit, 4)
            try:
                resp = lookup(d, creds, with_type=with_type, opener=opener, on_retry=_charge_retry)
            except AuthError as e:
                st['stopped'] = str(e)
                break
            except StopRun as e:
                st['stopped'] = str(e)
                break
            e = _entry(resp, with_type)
            c = load_cache(cpath) or c
            c['numbers'][phone_key(d)] = e
            save_cache(c, cpath)
            st['checked'] += 1
            if e['ls'] == DEAD:
                st['inactive'] += 1
            elif e['ls'] == UNREACHABLE:
                st['unreachable'] += 1
    print('phone_validate: checked %d, disconnected %d, unreachable %d, spent $%.3f%s'
          % (st['checked'], st['inactive'], st['unreachable'], st['usd'],
             (' - stopped: ' + st['stopped']) if st['stopped'] else ''))
    _write_status(st)
    return st


def check_key():
    """Free request (no Fields: formatting and validation only, $0 on Twilio's price list) against
    Twilio's own example number, so a new key can be proven without spending. -> exit code."""
    creds = load_key()
    if not creds:
        print('phone_validate: no %s' % key_path())
        return 1
    tok = base64.b64encode(('%s:%s' % creds).encode()).decode()
    req = urllib.request.Request(BASE + '%2B15017122661', headers={'Authorization': 'Basic ' + tok})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = 'phone_number' in json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        print('phone_validate: key check FAILED, Twilio HTTP %d' % e.code)
        return 2
    except (urllib.error.URLError, OSError, ValueError) as e:
        print('phone_validate: key check could not reach Twilio (%s)' % e.__class__.__name__)
        return 1
    print('phone_validate: key OK' if ok else 'phone_validate: unexpected answer from Twilio')
    return 0 if ok else 1


def stats():
    c = load_cache()
    if c is None:
        print('phone_validate: cache unreadable')
        return
    by = {}
    for v in c['numbers'].values():
        if isinstance(v, dict):
            by[v.get('ls') or '?'] = by.get(v.get('ls') or '?', 0) + 1
    print('phone_validate cache: %d number(s) checked' % sum(by.values()))
    for k in sorted(by, key=lambda k: -by[k]):
        print('  %-12s %d' % (k, by[k]))
    print('spent today $%.3f of $%.2f; last 7 days $%.3f'
          % (spent_today(c), daily_cap(),
             sum(float(v or 0) for k, v in c['spend'].items()
                 if k >= (dt.date.today() - dt.timedelta(days=6)).isoformat())))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--max-spend', type=float, default=None, help='dollar cap for this run')
    ap.add_argument('--limit', type=int, default=None, help='check at most N numbers')
    ap.add_argument('--with-type', action='store_true', help='also buy line type ($0.008 more)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--check', action='store_true', help='prove the key with a free request')
    ap.add_argument('--number', action='append', default=None, help='check this number (repeatable)')
    a = ap.parse_args(argv)
    if a.check:
        return check_key()
    if a.stats:
        stats()
        return 0
    st = run(numbers=a.number, max_spend=a.max_spend, with_type=a.with_type, dry_run=a.dry_run,
             limit=a.limit)
    # Exit 0 on every soft stop so the nightly .bat keeps going; 2 only when the credentials are bad,
    # which nobody will notice otherwise.
    return 2 if 'credentials' in (st.get('stopped') or '') else 0


if __name__ == '__main__':
    sys.exit(main())
