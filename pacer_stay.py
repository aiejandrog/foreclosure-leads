#!/usr/bin/env python
"""pacer_stay.py -- federal bankruptcy (§362 stay) lookup for board leads through the official PACER
Case Locator (PCL) API, written where stay_gate.py (the /send gate, #72) reads it.

WHY (2026-09-26). The send gate refuses every lead it cannot prove is clear of an automatic stay.
Its only durable source was sale_history_cache.json: the Miami-Dade state docket. So every Broward
and Palm Beach lead -- and every Miami-Dade lis pendens row that carries another county's case
number -- was refused as stay_case_unresolvable, and a Miami-Dade petition was only seen once a
suggestion of bankruptcy reached the state docket. A bankruptcy is a FEDERAL filing; PACER is where
it exists first. This module searches it by owner name and writes a verdict per lead.

ENDPOINTS (verified against PACER's public guides, 2026-09-26: "PACER Case Locator (PCL) API User
Guide", August 2026, and "PACER Authentication API User Guide", v3 May 2025):
    auth    POST https://{pacer.login | qa-login}.uscourts.gov/services/cso-auth
            {"loginId","password", optional "clientCode", "otpCode", "redactFlag"}
            -> {"loginResult": "0", "nextGenCSO": <token>, "errorDescription": ""}
            loginResult "0" can still carry "...you will not have PACER search privileges" (a
            required client code is missing, or the account is disabled): treated as a failure.
    logout  POST .../services/cso-logout {"nextGenCSO": <token>}  (always, in a finally)
    search  POST https://{pcl | qa-pcl}.uscourts.gov/pcl-public-api/rest/parties/find?page=N
            header X-NEXT-GEN-CSO: <token> (a re-issued token comes back in the same response
            header and replaces ours); optional X-CLIENT-CODE.
            body  {"lastName", "firstName", "exactNameMatch": false,
                   "courtCase": {"jurisdictionType": "bk", "dateFiledFrom": "YYYY-MM-DD"}}
            -> {"receipt": {"billablePages", "searchFee"}, "pageInfo": {"totalPages", ...},
                "content": [party rows with courtId, caseNumberFull, bankruptcyChapter, dateFiled,
                            dateTermed, dateDismissed, dateDischarged, dateReopened, partyRole,
                            courtCase {..., effectiveDateClosed}]}
            54 rows a page; each page retrieved is billed, and a search that finds nothing still
            bills one page (PACER pricing page).
    QA: PACER_ENV=qa uses qa-login / qa-pcl with a separate QA account; searches are not billable,
    are not debited anywhere, and are written to pacer_stay_cache_qa.json, which the gate never reads.

WHAT IS SEARCHED, AND WHY NATIONALLY. All three counties file in the Southern District of Florida
(flsbk), but venue follows the debtor's domicile (28 U.S.C. 1408), so an absentee owner can file in
their home state and the stay still covers the Florida house. A PCL party search with no court
filter is the nationwide index at the SAME price per page as a one-court search, so the search is
national. The cost of that choice is ambiguity, not dollars: a common name returns more rows. That
is handled in the verdict (below), never by quietly narrowing the search. --region fl limits it to
Florida's three bankruptcy courts for anyone who decides otherwise.

LOOKBACK: cases filed in the last 8 years (--lookback-years). Only an OPEN case stays a sale, and a
case older than that is essentially never still open: chapter 13 plans run at most 5 years
(11 U.S.C. 1322(d)), plus the months to discharge and close; chapter 7 cases close in months. 8 years
also matches the chapter 7 re-discharge bar (11 U.S.C. 727(a)(8)), so the serial-filer history the
docket reads care about is inside the window. A longer window only adds closed cases -- more rows
and more "too many results" -- without adding stays.

NAMES. Each lead's owner string is split into people (co-owners on '&' / ';') in the order its
source writes names: Miami-Dade appraiser rows are FIRST [MIDDLE] LAST, Broward/Palm Beach FDOR rows
and recorder lis pendens rows are LAST[,] FIRST [MIDDLE]. A three- or four-word name without a comma
is ambiguous (FIRST MIDDLE LAST vs FIRST LAST1 LAST2 -- common in Miami), so each plausible surname
is searched; PCL's name search is "starts with", so searching GARCIA also returns GARCIA LOPEZ and
GARCIA-LOPEZ. A co-owner written as a bare first name (SMITH JOHN & MARY) shares the surname before
it. Estates, trusts, government owners, placeholders and one-word names are never searched: there is
no single debtor name to search, so the lead is 'unverifiable' at no cost. More than 4 people or 6
searches on one lead: unverifiable.

VERDICT per lead (never "clear" on doubt):
    active        a debtor row whose name matches exactly (first + surname, compatible middle) on an
                  OPEN bankruptcy (not closed, not dismissed, or reopened) in a FLORIDA bankruptcy court.
                  PCL has no docket entries, so relief from stay cannot be seen: open = stayed.
                  A discharge on a case that is still open counts as open (the stay on estate
                  property runs until the case closes, 11 U.S.C. 362(c)(1)).
    unverifiable  any of: an open case for the same name outside Florida (could be a namesake);
                  an open case for a SIMILAR name (initial only, nickname-length prefix, extra or
                  missing surname, different middle initial); a case dismissed within the last 30
                  days and not yet closed (reinstatement window); more matching rows than
                  --max-pages pages (a common name -- the unread pages could hold an open case);
                  an owner that cannot be searched; an API error before the lead finished.
    clear         every search completed, and every matching bankruptcy is closed or long dismissed.
Rows that are not bankruptcy petitions (adversary proceedings, civil cases) and rows whose role is
clearly not the debtor (creditor, trustee, attorney, plaintiff/defendant in an adversary) are ignored.

CACHE: pacer_stay_cache.json beside sale_history_cache.json (the gate finds it there), gitignored,
keyed by stay_gate.pacer_key(case). Per entry: verdict, a/bd/sl (sale_history's field names), src
'pacer_pcl', env, q (query time, ISO with offset), t (epoch), county, searches, pages, cost, why, and
cases: the matched bankruptcy case numbers with court, chapter and dates. No owner names, no case
titles (a bankruptcy caption is the debtor's name). An API error never overwrites an earlier verdict
(it is noted as err/tried); a clear therefore simply ages out at the gate.

COST. $0.10 a page, and PACER waives the bill for a quarter that accrues $30 or less -- one cent more
and the WHOLE quarter bills. So three ceilings, all fail closed:
    quarter  PACER_QUARTER_CAP (default $25: $5 of headroom under $30 for any manual PACER use),
             in DEALFLOW_DIR/pacer_quarter_ledger.json, calendar quarters. Reserve before each page,
             settle to the receipt's searchFee after.
    month    #74's shared paid-reads cap (paid_reads.debit / adjust, spender 'pacer_stay'), so PACER
             counts against the same $50/month as every other paid read.
    run      --max-spend, default 'auto' = what is left of the quarter / nights left in it (at most
             PACER_RUN_MAX, default $2.00): spreads the free allowance over the quarter instead of
             spending it on night one, and uses what is left at the end of a quarter.
A lead is only started if all its searches fit; a page that errors with no receipt keeps its
reservation (the ledger runs high, never low), except 400/401/404/406/429, which PCL does not bill.

ORDER: (0) auction within 14 days, re-checked every 3 days -- a petition is likeliest right before a
sale; (1) never checked, soonest auction first, then newest lis pendens; (2) stale: clear after 12
days (inside the gate's 14-day expiry), active after 7 (to see a dismissal), unverifiable after 30
(the same search gives the same ambiguity), an errored lead the next day. Within each tier a lead whose
case number has no Miami-Dade stem (Broward, Palm Beach, an LP row carrying another county's number)
goes first: for it PACER is the only stay source the gate has, while a Miami-Dade case already has
the docket read (and PACER can only add a block to it, never a clear).

CREDENTIALS come ONLY from the environment: PACER_USERNAME, PACER_PASSWORD, optional
PACER_OTP_SECRET (base32 MFA secret; a TOTP is computed per login), PACER_CLIENT_CODE,
PACER_REDACT_FLAG=1 (filer accounts only), PACER_ENV=prod|qa. Missing username/password: the
lookup is OFF, says so once, exits 0 (the nightly refresh carries on). Nothing is ever printed about
the password, the token or owner names.

    python pacer_stay.py                 # nightly: plan, log in, search what the budget allows
    python pacer_stay.py --plan          # no network, no credentials: what it would search and cost
    python pacer_stay.py --case CACE-24-012345   # just this lead (still under every cap)
    python pacer_stay.py --status        # quarter + month spend
"""
import argparse
import base64
import datetime as dt
import glob
import hashlib
import hmac
import json
import math
import os
import re
import struct
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_NAME = 'pacer_stay_cache.json'
QA_CACHE_NAME = 'pacer_stay_cache_qa.json'
QUARTER_LEDGER_NAME = 'pacer_quarter_ledger.json'
ENV_QUARTER_LEDGER = 'PACER_QUARTER_LEDGER'
ENV_QUARTER_CAP = 'PACER_QUARTER_CAP'
ENV_RUN_MAX = 'PACER_RUN_MAX'
SPENDER = 'pacer_stay'

HOSTS = {
    'prod': {'auth': 'https://pacer.login.uscourts.gov', 'pcl': 'https://pcl.uscourts.gov'},
    'qa': {'auth': 'https://qa-login.uscourts.gov', 'pcl': 'https://qa-pcl.uscourts.gov'},
}
AUTH_PATH = '/services/cso-auth'
LOGOUT_PATH = '/services/cso-logout'
PARTY_FIND = '/pcl-public-api/rest/parties/find'

PAGE_USD = 0.10
PAGE_SIZE = 54
FREE_QUARTER_USD = 30.0
DEFAULT_QUARTER_CAP = 25.0
DEFAULT_RUN_MAX = 2.0
LOOKBACK_YEARS = 8
FL_BK_COURTS = ('flsbk', 'flmbk', 'flnbk')
NEAR_DAYS = 14
RECHECK = {'near': 3, 'clear': 12, 'active': 7, 'unverifiable': 30, 'error': 1}
DISMISS_GRACE_DAYS = 30
MAX_PEOPLE = 4
MAX_SEARCHES_PER_PERSON = 3
MAX_SEARCHES_PER_LEAD = 6
MAX_LEAD_ERRORS = 3           # consecutive API failures in one run: stop (PCL is down or refusing us)
NOT_DEBTOR_ROLES = {'cr', 'crd', 'cred', 'creditor', 'tr', 'trs', 'trustee', 'ust', 'aty', 'atty',
                    'attorney', 'intp', 'intv', 'int', 'pla', 'pl', 'plaintiff', 'dft', 'df',
                    'defendant', 'mv', 'movant'}


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------------------------------
def credentials(env=None):
    env = os.environ if env is None else env
    user = str(env.get('PACER_USERNAME') or '').strip()
    pw = str(env.get('PACER_PASSWORD') or '')
    if not user or not pw:
        return None
    return {'user': user, 'pw': pw,
            'otp_secret': str(env.get('PACER_OTP_SECRET') or '').strip(),
            'client': str(env.get('PACER_CLIENT_CODE') or '').strip(),
            'redact': str(env.get('PACER_REDACT_FLAG') or '').strip() == '1'}


def environment(env=None):
    """'prod' | 'qa' | '' (a setting that is neither: refuse to run rather than guess)."""
    env = os.environ if env is None else env
    v = str(env.get('PACER_ENV') or 'prod').strip().lower()
    if v in ('prod', 'production', 'live'):
        return 'prod'
    if v in ('qa', 'test'):
        return 'qa'
    return ''


def _money_setting(name, default, env=None):
    """(dollars, problem). A bad value is 0 (fail closed) with a problem string."""
    env = os.environ if env is None else env
    raw = str(env.get(name) or '').strip()
    if not raw:
        return default, ''
    try:
        v = float(raw)
    except ValueError:
        return 0.0, '%s=%r is not a number' % (name, raw)
    if not math.isfinite(v) or v < 0:
        return 0.0, '%s=%r is not a dollar amount >= 0' % (name, raw)
    return v, ''


def totp(secret, now=None, step=30, digits=6):
    """RFC 6238 TOTP (HMAC-SHA1, 30 s, 6 digits) -- what PACER's MFA and its sample code use."""
    s = re.sub(r'[\s-]', '', str(secret or '')).upper()
    key = base64.b32decode(s + '=' * (-len(s) % 8))
    counter = int((time.time() if now is None else now) // step)
    h = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# --------------------------------------------------------------------------------------------------
# the quarter ledger (PACER's $30 waiver is per calendar quarter)
# --------------------------------------------------------------------------------------------------
def quarter_of(day):
    return '%d-Q%d' % (day.year, (day.month - 1) // 3 + 1)


def quarter_days_left(day):
    q = (day.month - 1) // 3
    end_month = q * 3 + 3
    if end_month == 12:
        end = dt.date(day.year, 12, 31)
    else:
        end = dt.date(day.year, end_month + 1, 1) - dt.timedelta(days=1)
    return (end - day).days + 1


class QuarterLedger:
    """{"2026-Q3": {"total": 1.2, "pages": 12, "searches": 11}} -- dollars and counts only."""

    def __init__(self, path=None, env=None, today=None):
        env = os.environ if env is None else env
        p = str(env.get(ENV_QUARTER_LEDGER) or '').strip()
        if not p and not path:
            import paths as P
            p = os.path.join(P.DEALFLOW_DIR, QUARTER_LEDGER_NAME)
        self.path = path or p
        self.cap, self.cap_problem = _money_setting(ENV_QUARTER_CAP, DEFAULT_QUARTER_CAP, env)
        self.today = today or dt.date.today()
        self.q = quarter_of(self.today)
        self.broken = ''

    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
        except FileNotFoundError:
            return {}, ''
        except Exception as e:
            return None, 'PACER quarter ledger %s unreadable (%s)' % (self.path, str(e)[:80])
        if not isinstance(d, dict):
            return None, 'PACER quarter ledger %s is not a JSON object' % self.path
        return d, ''

    def status(self):
        d, problem = self._read()
        out = {'quarter': self.q, 'cap': self.cap, 'spent': 0.0, 'remaining': 0.0, 'pages': 0,
               'ok': False, 'why': ''}
        if problem or self.broken:
            out['why'] = problem or self.broken
            return out
        ent = d.get(self.q) if isinstance(d.get(self.q), dict) else {}
        try:
            out['spent'] = round(float(ent.get('total', 0) or 0), 4)
            out['pages'] = int(ent.get('pages', 0) or 0)
        except (TypeError, ValueError):
            out['why'] = 'PACER quarter ledger %s malformed' % self.q
            return out
        out['remaining'] = round(max(0.0, self.cap - out['spent']), 4)
        if self.cap_problem:
            out['why'] = 'PACER quarter cap setting invalid: %s' % self.cap_problem
        elif out['spent'] + 1e-9 >= self.cap:
            out['why'] = 'PACER quarter cap reached: $%.2f of $%.2f in %s' % (out['spent'], self.cap, self.q)
        else:
            out['ok'] = True
        return out

    def _write(self, usd, pages, searches, enforce):
        import paid_reads
        why = ''
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        except OSError:
            pass
        with paid_reads._FileLock(self.path) as lk:
            if not lk.held:
                self.broken = why = 'PACER quarter ledger %s is locked by another writer' % self.path
                return False, why
            d, problem = self._read()
            if problem:
                self.broken = problem
                return False, problem
            ent = d.get(self.q) if isinstance(d.get(self.q), dict) else {}
            total = float(ent.get('total', 0) or 0)
            if enforce:
                if self.cap_problem:
                    return False, 'PACER quarter cap setting invalid: %s' % self.cap_problem
                if total + usd > self.cap + 1e-9:
                    return False, ('PACER quarter cap: $%.2f left of $%.2f in %s'
                                   % (max(0.0, self.cap - total), self.cap, self.q))
            ent['total'] = max(0.0, round(total + usd, 6))
            ent['pages'] = max(0, int(ent.get('pages', 0) or 0) + pages)
            ent['searches'] = max(0, int(ent.get('searches', 0) or 0) + searches)
            d[self.q] = ent
            tmp = self.path + '.tmp'
            for i in range(6):
                try:
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(d, f, indent=1, sort_keys=True)
                    os.replace(tmp, self.path)
                    return True, ''
                except OSError:
                    time.sleep(0.2 * (i + 1))
            self.broken = why = 'PACER quarter ledger %s could not be written' % self.path
            return False, why

    def debit(self, usd, searches=0):
        return self._write(float(usd), 1, searches, True)

    def adjust(self, delta, pages=0):
        if abs(delta) < 1e-12 and not pages:
            return True
        return self._write(float(delta), pages, 0, False)[0]


class Budget:
    """Every paid page: per-run room, then the quarter, then #74's month -- reserve, call, settle."""

    def __init__(self, run_cap, quarter, billable, paid=None):
        self.run_cap = float(run_cap)
        self.quarter = quarter
        self.billable = billable
        self.spent = 0.0
        self.pages = 0
        self.refused = ''
        if paid is None and billable:
            import paid_reads as paid
        self.paid = paid

    def fits(self, usd):
        if not self.billable:
            return True, ''
        if self.spent + usd > self.run_cap + 1e-9:
            return False, 'per-run cap $%.2f (spent $%.2f)' % (self.run_cap, self.spent)
        qs = self.quarter.status()
        if not qs['ok'] or qs['remaining'] + 1e-9 < usd:
            return False, qs['why'] or 'PACER quarter cap: $%.2f left' % qs['remaining']
        if self.paid.remaining(SPENDER) + 1e-9 < usd:
            return False, 'monthly paid-reads cap (paid_reads.py): $%.2f left' % self.paid.remaining()
        return True, ''

    def reserve(self, usd, new_search):
        if not self.billable:
            self.pages += 1
            return True, ''
        if self.spent + usd > self.run_cap + 1e-9:
            self.refused = 'per-run cap $%.2f reached' % self.run_cap
            return False, self.refused
        ok, why = self.quarter.debit(usd, 1 if new_search else 0)
        if not ok:
            self.refused = why
            return False, why
        ok, why = self.paid.debit(usd, SPENDER)
        if not ok:
            self.quarter.adjust(-usd, pages=-1)
            self.refused = why or 'monthly paid-reads cap refused'
            return False, self.refused
        self.spent += usd
        self.pages += 1
        return True, ''

    def settle(self, reserved, actual, retrieved=True):
        """Settle a reservation to what PCL billed. retrieved=False: no page came back (it is taken
        off the page count; the dollars follow `actual`, which is 0 only when PCL does not bill)."""
        if not retrieved:
            self.pages -= 1
        if not self.billable:
            return
        delta = round(float(actual) - float(reserved), 6)
        if abs(delta) < 1e-9 and retrieved:
            return
        self.quarter.adjust(delta, pages=(0 if retrieved else -1))
        if abs(delta) >= 1e-9:
            self.paid.adjust(delta, SPENDER)
            self.spent += delta


def auto_run_cap(quarter_status, today, ceiling):
    """What tonight may spend when --max-spend is 'auto': an even share of what is left of the quarter."""
    left = max(0.0, float(quarter_status.get('remaining') or 0))
    if left < PAGE_USD or not quarter_status.get('ok'):
        return 0.0
    share = left / max(1, quarter_days_left(today))
    cap = math.floor(min(ceiling, share) / PAGE_USD + 1e-9) * PAGE_USD
    return round(max(PAGE_USD, cap), 2)


# --------------------------------------------------------------------------------------------------
# the PCL client
# --------------------------------------------------------------------------------------------------
class PacerError(Exception):
    pass


class AuthError(PacerError):
    pass


class RateLimited(PacerError):
    pass


def _hdr(resp, name):
    h = getattr(resp, 'headers', None) or {}
    try:
        v = h.get(name)
        if v is None:
            for k in h:
                if str(k).lower() == name.lower():
                    return h[k]
        return v
    except Exception:
        return None


class PCL:
    def __init__(self, env_name, creds, session=None, now=None):
        self.env = env_name
        self.hosts = HOSTS[env_name]
        self.creds = creds
        if session is None:
            import requests
            session = requests.Session()
        self.s = session
        self.token = ''
        self.now = now

    def _json_headers(self):
        return {'Content-Type': 'application/json', 'Accept': 'application/json'}

    def login(self):
        c = self.creds
        body = {'loginId': c['user'], 'password': c['pw']}
        if c.get('client'):
            body['clientCode'] = c['client']
        if c.get('otp_secret'):
            try:
                body['otpCode'] = totp(c['otp_secret'], now=self.now)
            except Exception:
                raise AuthError('PACER_OTP_SECRET is not a valid base32 secret')
        if c.get('redact'):
            body['redactFlag'] = '1'
        try:
            r = self.s.post(self.hosts['auth'] + AUTH_PATH, json=body, headers=self._json_headers(),
                            timeout=30)
        except Exception as e:
            raise AuthError('PACER login request failed (%s)' % type(e).__name__)
        if r.status_code != 200:
            raise AuthError('PACER login HTTP %s' % r.status_code)
        try:
            d = r.json()
        except Exception:
            raise AuthError('PACER login answered with something that is not JSON')
        if not isinstance(d, dict):
            raise AuthError('PACER login answered with an unexpected shape')
        res = str(d.get('loginResult', '')).strip()
        tok = str(d.get('nextGenCSO') or '').strip()
        desc = ' '.join(str(d.get('errorDescription') or '').split())
        if res != '0' or not tok:
            raise AuthError('PACER login refused (loginResult %s: %s)' % (res or '?', desc[:160] or 'no description'))
        self.token = tok
        if 'search privileges' in desc.lower():
            raise AuthError('PACER login worked but this account cannot search: %s' % desc[:220])
        if desc:
            log('  PACER: login note: %s' % desc[:200])

    def logout(self):
        if not self.token:
            return True
        tok, self.token = self.token, ''
        try:
            r = self.s.post(self.hosts['auth'] + LOGOUT_PATH, json={'nextGenCSO': tok},
                            headers=self._json_headers(), timeout=20)
            if r.status_code != 200:
                return False
            try:
                d = r.json()
            except Exception:
                return True                           # the v1 guide said logout returns a plain string
            return str((d or {}).get('loginResult', '0')).strip() == '0'
        except Exception:
            return False

    def find_parties(self, body, page):
        headers = self._json_headers()
        headers['X-NEXT-GEN-CSO'] = self.token
        if self.creds.get('client'):
            headers['X-CLIENT-CODE'] = self.creds['client']
        r = self.s.post('%s%s?page=%d' % (self.hosts['pcl'], PARTY_FIND, page), json=body,
                        headers=headers, timeout=45)
        new = _hdr(r, 'X-NEXT-GEN-CSO')
        if new and str(new).strip():
            self.token = str(new).strip()
        return r


def receipt_fee(receipt):
    """Dollars PCL says it billed for this page; None when the receipt does not say."""
    if not isinstance(receipt, dict):
        return None
    sf = receipt.get('searchFee')
    if sf is not None and str(sf).strip() != '':
        try:
            v = float(str(sf).strip().lstrip('$'))
            if math.isfinite(v) and v >= 0:
                return round(v, 4)
        except ValueError:
            pass
    bp = receipt.get('billablePages')
    try:
        return round(int(bp) * PAGE_USD, 4)
    except (TypeError, ValueError):
        return None


def search_body(q, date_from, region):
    cc = {'jurisdictionType': 'bk', 'dateFiledFrom': date_from}
    if region == 'fl':
        cc['courtId'] = list(FL_BK_COURTS)
    body = {'lastName': q['last'], 'exactNameMatch': False, 'courtCase': cc}
    if q.get('first'):
        body['firstName'] = q['first']
    return body


def run_search(pcl, budget, q, date_from, region, max_pages):
    """One name search, paged, every page reserved then settled.
    -> {'rows', 'pages', 'overflow', 'error', 'refused'}; raises RateLimited / AuthError."""
    out = {'rows': [], 'pages': 0, 'overflow': False, 'error': '', 'refused': ''}
    body = search_body(q, date_from, region)
    page, reauthed = 0, False
    while True:
        ok, why = budget.reserve(PAGE_USD, new_search=(page == 0 and not reauthed))
        if not ok:
            out['refused'] = why
            return out
        try:
            r = pcl.find_parties(body, page)
        except Exception as e:                        # timeout / connection: billing unknown, keep it
            budget.settle(PAGE_USD, PAGE_USD, retrieved=False)
            out['error'] = 'PCL request failed (%s)' % type(e).__name__
            return out
        code = getattr(r, 'status_code', 0)
        if code == 401 and not reauthed:
            budget.settle(PAGE_USD, 0.0, retrieved=False)
            reauthed = True
            pcl.login()                               # AuthError propagates: the run stops
            continue
        if code != 200:
            budget.settle(PAGE_USD, 0.0 if code in (400, 401, 404, 406, 429) else PAGE_USD, retrieved=False)
            if code == 429:
                raise RateLimited('PCL rate limit (HTTP 429)')
            out['error'] = 'PCL HTTP %s' % code
            return out
        try:
            d = r.json()
        except Exception:
            out['error'] = 'PCL answered with something that is not JSON'
            return out
        if not isinstance(d, dict):
            out['error'] = 'PCL answered with an unexpected shape'
            return out
        fee = receipt_fee(d.get('receipt'))
        budget.settle(PAGE_USD, PAGE_USD if fee is None else fee)
        out['pages'] += 1
        content = d.get('content')
        if content is None:
            content = []
        if not isinstance(content, list):
            out['error'] = 'PCL content is not a list'
            return out
        out['rows'].extend(x for x in content if isinstance(x, dict))
        pi = d.get('pageInfo') if isinstance(d.get('pageInfo'), dict) else {}
        try:
            total_pages = int(pi.get('totalPages') or 0)
        except (TypeError, ValueError):
            total_pages = 0
        if pi:
            more = (page + 1 < total_pages) or (pi.get('last') is False)
        else:
            more = len(content) >= PAGE_SIZE          # no page info: a full page may have a next one
        if not more:
            return out
        if page + 1 >= max_pages:
            out['overflow'] = True
            return out
        page += 1


# --------------------------------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------------------------------
GENERATIONS = {'JR', 'SR', 'II', 'III', 'IV'}
NOISE_RE = re.compile(r'\b(?:H/?W|H/?E|W/?H|ET\s*UX|ET\s*VIR|ET\s*AL|ETAL|ETUX|ETVIR|L/?E|REM|AKA|A/K/A|'
                      r'FKA|F/K/A|NKA|N/K/A|UNKNOWN\s+SPOUSE\s+OF)\b')
ENTITY_RE = re.compile(r'\b(?:LLC|L\s?L\s?C|INC|INCORPORATED|CORP|CORPORATION|COMPANY|LTD|LP|LLP|PLLC|'
                       r'HOLDINGS?|GROUP|PARTNERS(?:HIP)?|VENTURES?|INVESTMENTS?|PROPERT(?:Y|IES)|'
                       r'REALTY|MANAGEMENT|BANK|ASSOCIATION|ASSN|ASSOC|CONDOMINIUM|CONDO|FUND|'
                       r'CAPITAL|ENTERPRISES?|CHURCH|MINISTRIES|FOUNDATION|DEVELOPMENT)\b')
ENTITY_TAIL = {'LLC', 'L', 'C', 'INC', 'INCORPORATED', 'CORP', 'CORPORATION', 'CO', 'COMPANY', 'LTD',
               'LP', 'LLP', 'PLLC', 'PA', 'THE'}
TRUST_RE = re.compile(r'\b(?:TR|TRS|TRUST|TRUSTS|TRUSTEE|TRUSTEES|REVOCABLE|IRREVOCABLE)\b')
ESTATE_RE = re.compile(r'\b(?:EST|ESTATE|DECEASED|DECD|HEIRS?|DEVISEES|BENEFICIARIES|UNKNOWN|'
                       r'PERSONAL\s+REPRESENTATIVE)\b')
GOV_RE = re.compile(r'\b(?:CITY\s+OF|COUNTY|STATE\s+OF|UNITED\s+STATES|SECRETARY\s+OF|HUD|'
                    r'DEPARTMENT\s+OF|HOUSING\s+AUTHORITY)\b')


def _ascii_upper(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return s.upper()


def name_tokens(s):
    """Upper-case alphanumeric tokens: accents dropped, apostrophes joined (O'NEIL -> ONEIL),
    hyphens split (GARCIA-LOPEZ -> GARCIA LOPEZ)."""
    s = _ascii_upper(s).replace("'", '').replace('\u2019', '')
    return re.findall(r'[A-Z0-9]+', s)


class Person:
    kind = 'person'

    def __init__(self, interps):
        # interps: list of (first, [surnames], [middles]); surnames[0] is the one searched
        seen, keep = set(), []
        for f, sn, mi in interps:
            k = (f, tuple(sn), tuple(mi))
            if k not in seen:
                seen.add(k)
                keep.append((f, list(sn), list(mi)))
        self.interps = keep

    def queries(self):
        out = []
        for f, sn, _ in self.interps:
            q = {'last': sn[0], 'first': f}
            if q not in out:
                out.append(q)
        return out

    def key(self):
        return ('P', tuple(sorted((f, tuple(sn), tuple(mi)) for f, sn, mi in self.interps)))


class Entity:
    kind = 'entity'

    def __init__(self, core):
        self.core = list(core)

    def queries(self):
        return [{'last': ' '.join(self.core)[:200], 'first': ''}]

    def key(self):
        return ('E', tuple(self.core))


def _interps(toks, order):
    """Plausible (first, surnames, middles) readings of one person's name tokens. A one-letter token
    is only ever a middle initial. [] when the name cannot be read."""
    n = len(toks)
    out = []
    if order == 'first_last':
        if n == 2:
            out = [(toks[0], [toks[1]], [])]
        elif n == 3:
            out = [(toks[0], [toks[2]], [toks[1]]), (toks[0], [toks[1], toks[2]], [])]
        elif n == 4:
            out = [(toks[0], [toks[3]], toks[1:3]), (toks[0], toks[2:4], [toks[1]])]
    else:
        if n == 2:
            out = [(toks[1], [toks[0]], [])]
        elif n == 3:
            out = [(toks[1], [toks[0]], [toks[2]]), (toks[2], toks[0:2], [])]
        elif n == 4:
            out = [(toks[1], [toks[0]], toks[2:4]), (toks[2], toks[0:2], [toks[3]])]
    return [(f, sn, mi) for f, sn, mi in out if len(f) > 1 and len(sn[0]) > 1]


def _strip_person(part):
    part = NOISE_RE.sub(' ', part)
    toks = [t for t in name_tokens(part) if t not in GENERATIONS]
    return toks


def parse_owner(raw, order):
    """(subjects, problem). subjects: Person / Entity objects to search. problem: why this owner
    cannot be searched at all ('' when it can). order: 'first_last' | 'last_first'."""
    s = _ascii_upper(raw).strip()
    s = re.sub(r'&\s*[WH]\b\.?', '& ', s)             # MD appraiser "&W" / "&H" = wife / husband
    # "UNKNOWN SPOUSE OF X" / "UNKNOWN TENANT" are pleading placeholders, not owners: drop the chunk
    s = re.sub(r'UNKNOWN\s+(?:SPOUSE|TENANTS?|PARTIES|HEIRS)\b[^;&]*', ' ', s)
    s = re.sub(r'^[\s;&]+|[\s;&]+$', '', s)
    if not s or '(' in s or 'TITLE SEARCH' in s:
        return [], 'no owner name on the lead'
    if ESTATE_RE.search(s):
        return [], 'owner is an estate or heirs -- no single debtor name to search'
    if TRUST_RE.search(s):
        return [], 'owner is a trust -- the person who could file is not named'
    if GOV_RE.search(s):
        return [], 'government owner'
    subjects = []
    for chunk in [c for c in s.split(';') if c.strip()]:
        if ENTITY_RE.search(chunk):
            core = name_tokens(chunk)
            while core and core[-1] in ENTITY_TAIL:
                core.pop()
            while core and core[0] == 'THE':
                core.pop(0)
            if not core:
                return [], 'an entity name with nothing searchable in it'
            subjects.append(Entity(core))
            continue
        prev_surnames = []
        for i, part in enumerate(re.split(r'\s*&\s*|\s+AND\s+', chunk)):
            if not part.strip():
                continue
            if ',' in part and order in ('last_first', 'first_last'):
                last, _, rest = part.partition(',')
                lt, rt = _strip_person(last), _strip_person(rest)
                if not lt or not rt or len(rt[0]) < 2 or len(lt[0]) < 2:
                    return [], 'an owner name that cannot be split into first and last'
                interps = [(rt[0], lt, rt[1:])]
            else:
                toks = _strip_person(part)
                if not toks:
                    continue
                if len(toks) == 1 or (len(toks) == 2 and len(toks[1]) == 1 and order == 'first_last'):
                    # a bare first name after '&' shares the surname before it
                    if i > 0 and prev_surnames and len(toks[0]) > 1:
                        interps = [(toks[0], [sn], toks[1:]) for sn in prev_surnames]
                    else:
                        return [], 'a one-word owner name'
                else:
                    if len(toks) > 4:
                        return [], 'an owner name with more than four words (cannot tell first from last)'
                    interps = _interps(toks, order)
                    if not interps:
                        return [], 'an owner name that is only initials'
                    if i > 0 and prev_surnames:
                        # "SMITH JOHN & MARY ANN": the co-owner may be MARY ANN SMITH, not ANN MARY
                        for sn in prev_surnames:
                            if all(sn not in s2 for _, s2, _ in interps):
                                interps.append((toks[0], [sn], toks[1:]))
            p = Person(interps)
            if len(p.queries()) > MAX_SEARCHES_PER_PERSON:
                return [], 'too many ways to read one owner name'
            subjects.append(p)
            prev_surnames = []
            for _, sn, _ in p.interps:
                if sn[0] not in prev_surnames:
                    prev_surnames.append(sn[0])
    uniq, seen = [], set()
    for sj in subjects:
        if sj.key() not in seen:
            seen.add(sj.key())
            uniq.append(sj)
    if not uniq:
        return [], 'no owner name on the lead'
    if len(uniq) > MAX_PEOPLE:
        return [], 'more than %d owners on one lead' % MAX_PEOPLE
    return uniq, ''


def lead_subjects(owner_specs):
    """[(owner string, order), ...] -> (subjects, problem, n_searches)."""
    subjects, seen = [], set()
    for raw, order in owner_specs:
        sj, problem = parse_owner(raw, order)
        if problem:
            return [], problem, 0
        for x in sj:
            if x.key() not in seen:
                seen.add(x.key())
                subjects.append(x)
    if not subjects:
        return [], 'no owner name on the lead', 0
    if len(subjects) > MAX_PEOPLE:
        return [], 'more than %d owners on one lead' % MAX_PEOPLE, 0
    queries = []
    for x in subjects:
        for q in x.queries():
            if q not in queries:
                queries.append(q)
    if len(queries) > MAX_SEARCHES_PER_LEAD:
        return [], 'more than %d searches for one lead' % MAX_SEARCHES_PER_LEAD, 0
    return subjects, '', len(queries)


def match_row(row, subject):
    """'strong' | 'weak' | None: does this PCL party row name this subject?"""
    last = name_tokens(row.get('lastName'))
    first = name_tokens(row.get('firstName'))
    mid = name_tokens(row.get('middleName'))
    if not last:
        return None
    if subject.kind == 'entity':
        rec = [t for t in last + first if t not in ENTITY_TAIL]
        while rec and rec[0] == 'THE':
            rec.pop(0)
        core = subject.core
        if rec == core:
            return 'strong'
        n = min(len(rec), len(core))
        if n and rec[:n] == core[:n]:
            return 'weak'
        return None
    if not first:
        allrec = set(last + mid)
        for f, sn, mi in subject.interps:
            if {f, sn[0]} <= allrec:
                return 'weak'
        return None
    rf, rmid = first[0], first[1:] + mid
    best = None
    for f, sn, mi in subject.interps:
        if last[0] not in sn:
            continue
        if rf == f:
            fq = 'exact'
        elif (len(rf) == 1 and f.startswith(rf)) or rf.startswith(f) or f.startswith(rf):
            fq = 'loose'
        else:
            continue
        ours = set(sn) | set(mi)
        extra = [t for t in last if t not in ours]
        others = mi + sn[1:]

        def _compat(t):
            return any(o == t or (len(t) == 1 and o.startswith(t)) or (len(o) == 1 and t.startswith(o))
                       for o in others)
        mid_ok = all(_compat(t) for t in rmid)
        if fq == 'exact' and not extra and mid_ok and last[0] == sn[0]:
            return 'strong'
        best = 'weak'
    return best


# --------------------------------------------------------------------------------------------------
# cases and verdicts
# --------------------------------------------------------------------------------------------------
def _date(v):
    s = str(v or '').strip()[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def case_view(row):
    cc = row.get('courtCase') if isinstance(row.get('courtCase'), dict) else {}

    def g(k):
        v = row.get(k)
        return v if v not in (None, '', ' ') else cc.get(k)
    court = str(g('courtId') or '').strip().lower()
    juris = str(g('jurisdictionType') or '').strip().lower()
    ctype = str(g('caseType') or '').strip().lower()
    return {
        'court': court, 'no': str(g('caseNumberFull') or '').strip(),
        'ch': str(g('bankruptcyChapter') or '').strip(),
        'filed': _date(g('dateFiled')), 'termed': _date(g('dateTermed')),
        'closed_eff': _date(cc.get('effectiveDateClosed') or row.get('effectiveDateClosed')),
        'reopened': _date(g('dateReopened')), 'dismissed': _date(g('dateDismissed')),
        'discharged': _date(g('dateDischarged')),
        'is_bk': (('bank' in juris) or juris == 'bk' or court.endswith('bk')) and ctype != 'ap',
        'role': str(row.get('partyRole') or row.get('role') or '').strip().lower(),
    }


def case_status(cv, today):
    """'closed' | 'dismissed' | 'dismissed_recent' | 'open'."""
    closes = [d for d in (cv['termed'], cv['closed_eff']) if d]
    last_close = max(closes) if closes else None
    if last_close and not (cv['reopened'] and cv['reopened'] > last_close):
        return 'closed'
    if cv['dismissed'] and not (cv['reopened'] and cv['reopened'] > cv['dismissed']):
        return 'dismissed' if (today - cv['dismissed']).days > DISMISS_GRACE_DAYS else 'dismissed_recent'
    return 'open'


def decide(subjects, results, today, date_from):
    """results: list of (subject_index, search_result). -> (verdict, why, cases, bd)."""
    reasons, evidence, cases, seen = [], [], [], set()
    lb = _date(date_from)
    for si, res in results:
        if res.get('overflow'):
            reasons.append('more than %d page(s) of results for one owner name (a common name)'
                           % max(1, res.get('pages') or 1))
        sj = subjects[si]
        for row in res.get('rows') or []:
            cv = case_view(row)
            if not cv['is_bk'] or cv['role'] in NOT_DEBTOR_ROLES:
                continue
            if lb and cv['filed'] and cv['filed'] < lb:
                continue
            q = match_row(row, sj)
            if not q:
                continue
            st = case_status(cv, today)
            k = (cv['court'], cv['no'])
            if k not in seen:
                seen.add(k)
                cases.append({'court': cv['court'], 'no': cv['no'], 'ch': cv['ch'],
                              'filed': str(cv['filed'] or ''), 'closed': str(cv['termed'] or cv['closed_eff'] or ''),
                              'dismissed': str(cv['dismissed'] or ''), 'discharged': str(cv['discharged'] or ''),
                              'reopened': str(cv['reopened'] or ''), 'status': st, 'match': q,
                              'open': st == 'open'})
            fl = cv['court'] in FL_BK_COURTS
            if st == 'open' and q == 'strong' and fl:
                evidence.append(cv)
            elif st == 'open' and q == 'strong':
                reasons.append('an open bankruptcy for the same name in %s (cannot tell if it is this owner)'
                               % (cv['court'] or 'another court'))
            elif st == 'open':
                reasons.append('an open bankruptcy for a similar name in %s' % (cv['court'] or 'a court'))
            elif st == 'dismissed_recent':
                reasons.append('a bankruptcy dismissed %s and not yet closed in %s (reinstatement window)'
                               % (cv['dismissed'], cv['court'] or 'a court'))
    cases.sort(key=lambda c: c['filed'], reverse=True)     # newest first ...
    cases.sort(key=lambda c: not c['open'])                # ... open cases ahead of closed ones
    if evidence:
        bd = str(max((cv['filed'] for cv in evidence if cv['filed']), default='') or '')
        return 'active', ('open bankruptcy in %s for the owner' % ', '.join(sorted({cv['court'] for cv in evidence}))), cases[:10], bd
    if reasons:
        return 'unverifiable', '; '.join(dict.fromkeys(reasons))[:300], cases[:10], ''
    return 'clear', 'no open bankruptcy for any owner name since %s' % date_from, cases[:10], ''


# --------------------------------------------------------------------------------------------------
# leads
# --------------------------------------------------------------------------------------------------
def _load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _day(v):
    s = str(v or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def load_leads(here=HERE):
    """{key: lead} from the files the board is built from, plus skip counts.
    lead = {'key','case','county','owners': [(str, order)], 'auction': date|None, 'filed': date|None,
            'src': set()}"""
    import stay_gate
    leads, skipped = {}, {}

    def add(case, county, owner, order, auction, filed, src):
        key = stay_gate.pacer_key(case)
        if not key:
            skipped['no usable case number'] = skipped.get('no usable case number', 0) + 1
            return
        ld = leads.get(key)
        if ld is None:
            ld = leads[key] = {'key': key, 'case': str(case).strip(), 'county': county, 'owners': [],
                               'auction': None, 'filed': None, 'src': set()}
        ld['src'].add(src)
        o = str(owner or '').strip()
        if o and (o, order) not in ld['owners']:
            ld['owners'].append((o, order))
        if auction and (ld['auction'] is None or auction < ld['auction']):
            ld['auction'] = auction
        if filed and (ld['filed'] is None or filed > ld['filed']):
            ld['filed'] = filed
        if county and not ld['county']:
            ld['county'] = county

    rows = _load_json(os.path.join(here, 'leads_final.json'))
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        owners = r.get('owners') or r.get('owner_clean') or ''
        add(r.get('Case #') or r.get('case'), 'MIAMI-DADE', owners, 'first_last', _day(r.get('AuctionDate')),
            None, 'auction')
    for f in sorted(glob.glob(os.path.join(here, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_final.json', 'leads_raw.json', 'lp_leads.json', 'balloon_leads.json') or bn.startswith('_'):
            continue
        rows = _load_json(f)
        for d in rows if isinstance(rows, list) else []:
            if not isinstance(d, dict) or d.get('st') == 'BAL':
                continue
            add(d.get('case'), str(d.get('county') or '').upper(), d.get('owners'), 'last_first',
                _day(d.get('auction')), None, 'auction')
    lp = _load_json(os.path.join(here, 'lp_leads.json'))
    if isinstance(lp, list):
        for d in lp:
            if not isinstance(d, dict) or d.get('lpDismissed') or d.get('lpClosed'):
                continue
            add(d.get('case'), str(d.get('county') or 'MIAMI-DADE').upper(), d.get('owners'), 'last_first',
                None, _day(d.get('filedDate') or d.get('filed')), 'lp')
    else:
        feed = _load_json(os.path.join(here, 'lis_pendens.json'))
        seen = set()
        for d in feed if isinstance(feed, list) else []:     # lp_leads.build's dedupe, verbatim
            if not isinstance(d, dict):
                continue
            case, owner = str(d.get('case') or '').strip(), str(d.get('owner') or '').strip()
            if not owner:
                continue
            k = case or (owner + str(d.get('date', '')))
            if k in seen:
                continue
            seen.add(k)
            add(case, str(d.get('county') or 'MIAMI-DADE').upper(), owner, 'last_first', None,
                _day(d.get('date')), 'lp')
    return leads, skipped


def recheck_days(ent, near):
    if ent.get('err'):
        return RECHECK['error']
    v = ent.get('verdict')
    if v == 'unverifiable':
        return RECHECK['unverifiable']
    if v == 'active':
        return RECHECK['active']
    return RECHECK['near'] if near else RECHECK['clear']


def plan(leads, cache, today, now_ts, only_case=None):
    """Due leads in the order they are searched. Each item: (lead, tier, near)."""
    import stay_gate
    want = stay_gate.pacer_key(only_case) if only_case else ''
    items = []
    for key, ld in leads.items():
        if want and key != want:
            continue
        a = ld.get('auction')
        days = (a - today).days if a else None
        if days is not None and days < -1:
            continue                                   # the sale has happened
        near = days is not None and days <= NEAR_DAYS
        ent = cache.get(key) if isinstance(cache.get(key), dict) else None
        if ent is not None and not want:
            last = max(float(ent.get('t') or 0), float(ent.get('tried') or 0))
            age = (now_ts - last) / 86400.0
            if age < recheck_days(ent, near):
                continue
        tier = 0 if near else (1 if ent is None else 2)
        # no Miami-Dade stem = PACER is the gate's ONLY stay source for this lead: search it first
        crank = 1 if stay_gate.case_stem(key) else 0
        if tier == 0:
            sub = (days,)
        elif tier == 1:
            sub = ((days if days is not None else 10 ** 6), -(ld['filed'].toordinal() if ld.get('filed') else 0))
        else:
            sub = (max(float(ent.get('t') or 0), float(ent.get('tried') or 0)),)
        items.append(((tier, crank) + sub + (key,), ld, tier, near))
    items.sort(key=lambda x: x[0])
    return [(ld, tier, near) for _, ld, tier, near in items]


def cache_path(here, env_name):
    return os.path.join(here, QA_CACHE_NAME if env_name == 'qa' else CACHE_NAME)


def load_cache(path):
    d = _load_json(path)
    return d if isinstance(d, dict) else {}


def save_cache(path, cache):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def lookback_from(today, years):
    try:
        return today.replace(year=today.year - years).isoformat()
    except ValueError:                                 # Feb 29
        return today.replace(year=today.year - years, day=28).isoformat()


def check_lead(pcl, budget, ld, subjects, today, date_from, region, max_pages):
    """-> (entry fields or None, status). status: 'done' | 'error' | 'refused'."""
    queries = []
    for si, sj in enumerate(subjects):
        for q in sj.queries():
            queries.append((si, q))
    results, errors, pages, cost0 = [], [], 0, budget.spent
    by_query = {}
    for si, q in queries:
        qk = (q['last'], q['first'])
        if qk in by_query:
            results.append((si, by_query[qk]))
            continue
        res = run_search(pcl, budget, q, date_from, region, max_pages)
        pages += res['pages']
        if res['refused']:
            return {'pages': pages}, 'refused', res['refused']
        if res['error']:
            errors.append(res['error'])
            continue
        by_query[qk] = res
        results.append((si, res))
    verdict, why, cases, bd = decide(subjects, results, today, date_from)
    info = {'pages': pages, 'cost': round(budget.spent - cost0, 4), 'searches': len(by_query)}
    if errors and verdict != 'active':
        return dict(info, verdict='unverifiable', why='PCL error: ' + errors[0], cases=cases, bd=''), 'error', errors[0]
    return dict(info, verdict=verdict, why=why, cases=cases, bd=bd), 'done', ''


def _entry(fields, ld, env_name, region, date_from, now_ts):
    return {
        'verdict': fields['verdict'], 'a': fields['verdict'] == 'active', 'bd': fields.get('bd') or '',
        'sl': '', 'src': 'pacer_pcl', 'env': env_name,
        'q': dt.datetime.fromtimestamp(now_ts).astimezone().isoformat(timespec='seconds'),
        't': round(now_ts, 1), 'county': ld.get('county') or '', 'searches': fields.get('searches', 0),
        'pages': fields.get('pages', 0), 'cost': fields.get('cost', 0.0), 'why': fields.get('why', ''),
        'cases': fields.get('cases') or [], 'lookback_from': date_from, 'region': region, 'v': 1,
    }


def summarize_plan(leads, queue, skipped, today):
    by = {}
    for ld in leads.values():
        c = ld.get('county') or '?'
        b = by.setdefault(c, {'leads': 0, 'searchable': 0, 'searches': 0, 'unsearchable': 0, 'near': 0, 'due': 0})
        b['leads'] += 1
        _, problem, n = lead_subjects(ld['owners'])
        if problem:
            b['unsearchable'] += 1
        else:
            b['searchable'] += 1
            b['searches'] += n
        a = ld.get('auction')
        if a and 0 <= (a - today).days <= NEAR_DAYS:
            b['near'] += 1
    for ld, _, _ in queue:
        by.setdefault(ld.get('county') or '?', {}).setdefault('due', 0)
        by[ld.get('county') or '?']['due'] += 1
    return by


def main(argv=None, session=None, here=HERE, env=None, now=None, paid=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description='Federal bankruptcy (PACER PCL) stay lookup for board leads')
    ap.add_argument('--max-spend', default='auto',
                    help="per-run dollar cap; 'auto' = quarter remaining / nights left (<= PACER_RUN_MAX)")
    ap.add_argument('--limit', type=int, default=0, help='max leads searched this run (0 = budget decides)')
    ap.add_argument('--case', default='', help='search only this lead (still under every cap)')
    ap.add_argument('--plan', action='store_true', help='no network: print what would be searched and its cost')
    ap.add_argument('--status', action='store_true', help='print quarter and month spend, then exit')
    ap.add_argument('--max-pages', type=int, default=1,
                    help='pages read per name before calling it a common name (unverifiable)')
    ap.add_argument('--lookback-years', type=int, default=LOOKBACK_YEARS)
    ap.add_argument('--region', choices=('national', 'fl'), default='national')
    a = ap.parse_args(argv)
    now_ts = time.time() if now is None else now
    today = dt.date.fromtimestamp(now_ts)

    env_name = environment(env)
    if not env_name:
        log('PACER: PACER_ENV=%r is neither prod nor qa -- federal bankruptcy lookup not run' % env.get('PACER_ENV'))
        return 3
    quarter = QuarterLedger(env=env, today=today)
    if a.status:
        import paid_reads
        qs, ms = quarter.status(), paid_reads.status()
        log('PACER quarter %s: $%.2f of $%.2f (%d pages)%s' % (qs['quarter'], qs['spent'], qs['cap'], qs['pages'],
                                                             '' if qs['ok'] else ' -- ' + qs['why']))
        log('paid reads %s: $%.2f of $%.2f, pacer_stay $%.2f' % (ms['month'], ms['spent'], ms['cap'],
                                                                float(ms['by'].get(SPENDER, 0) or 0)))
        return 0

    leads, skipped = load_leads(here)
    cpath = cache_path(here, env_name)
    cache = load_cache(cpath)
    queue = plan(leads, cache, today, now_ts, a.case or None)
    date_from = lookback_from(today, a.lookback_years)
    by = summarize_plan(leads, queue, skipped, today)
    log('PACER: %d lead(s) with a usable case number (%s), %d due tonight%s' % (
        len(leads), ', '.join('%s %d' % (c, b.get('leads', 0)) for c, b in sorted(by.items())), len(queue),
        ('; skipped: ' + ', '.join('%s %d' % kv for kv in skipped.items())) if skipped else ''))
    if a.plan:
        for c, b in sorted(by.items()):
            log('  %-11s leads %4d  searchable %4d (%d searches, ~$%.2f)  not searchable %4d  near-sale %3d  due %4d'
                % (c, b.get('leads', 0), b.get('searchable', 0), b.get('searches', 0), b.get('searches', 0) * PAGE_USD,
                   b.get('unsearchable', 0), b.get('near', 0), b.get('due', 0)))
        return 0

    creds = credentials(env)
    if not creds:
        log('PACER: PACER_USERNAME / PACER_PASSWORD not set -- federal bankruptcy lookup is OFF (nothing '
            'searched, nothing spent). Broward / Palm Beach sends stay refused by the stay gate.')
        return 0
    if not queue:
        log('PACER: nothing due tonight')
        return 0

    billable = env_name == 'prod'
    if billable:
        qs = quarter.status()
        if paid is None:
            import paid_reads as paid
        ms_left = paid.remaining(SPENDER)
        if not qs['ok']:
            log('PACER: %s -- no paid searches tonight' % qs['why'])
            return 0
        if qs['cap'] >= FREE_QUARTER_USD:
            log('PACER: WARNING PACER_QUARTER_CAP $%.2f is at or above the $30 waiver -- this quarter will be billed'
                % qs['cap'])
        run_max, prob = _money_setting(ENV_RUN_MAX, DEFAULT_RUN_MAX, env)
        if prob:
            log('PACER: %s -- no paid searches tonight' % prob)
            return 0
        if str(a.max_spend).lower() == 'auto':
            run_cap = auto_run_cap(qs, today, run_max)
        else:
            try:
                run_cap = max(0.0, float(a.max_spend))
            except ValueError:
                log('PACER: --max-spend %r is not a number -- no paid searches' % a.max_spend)
                return 3
        run_cap = min(run_cap, qs['remaining'], ms_left)
        if run_cap + 1e-9 < PAGE_USD:
            log('PACER: $%.2f available tonight (quarter $%.2f left, month $%.2f left) -- no paid searches'
                % (run_cap, qs['remaining'], ms_left))
            return 0
        log('PACER: tonight up to $%.2f (quarter %s $%.2f of $%.2f spent; month $%.2f left)'
            % (run_cap, qs['quarter'], qs['spent'], qs['cap'], ms_left))
    else:
        run_cap = 0.0
        log('PACER: QA environment -- searches are not billable; verdicts go to %s, which the gate never reads'
            % QA_CACHE_NAME)
    budget = Budget(run_cap, quarter, billable, paid=paid)

    pcl = PCL(env_name, creds, session=session, now=now)
    counts = {}
    active_keys = []
    rc = 0
    try:
        try:
            pcl.login()
        except AuthError as e:
            log('PACER: %s -- no searches tonight' % e)
            return 3
        searched = errs_in_row = 0
        for ld, tier, near in queue:
            if a.limit and searched >= a.limit:
                break
            c = counts.setdefault(ld.get('county') or '?', {'clear': 0, 'active': 0, 'unverifiable': 0,
                                                            'error': 0, 'deferred': 0})
            subjects, problem, nsearch = lead_subjects(ld['owners'])
            key = ld['key']
            if problem:
                prev = cache.get(key)
                if not (isinstance(prev, dict) and prev.get('verdict') == 'unverifiable' and not prev.get('err')
                        and prev.get('why') == problem):
                    cache[key] = _entry({'verdict': 'unverifiable', 'why': problem}, ld, env_name, a.region,
                                        date_from, now_ts)
                    save_cache(cpath, cache)
                c['unverifiable'] += 1
                continue
            ok, why = budget.fits(nsearch * PAGE_USD)
            if not ok:
                c['deferred'] += 1
                if budget.billable and budget.spent + PAGE_USD > budget.run_cap + 1e-9:
                    break
                continue
            try:
                fields, status, why = check_lead(pcl, budget, ld, subjects, today, date_from, a.region, a.max_pages)
            except RateLimited as e:
                log('PACER: %s -- stopping tonight' % e)
                rc = 3
                break
            except AuthError as e:
                log('PACER: re-login failed (%s) -- stopping tonight' % e)
                rc = 3
                break
            searched += 1
            if status == 'refused':
                c['deferred'] += 1
                log('PACER: budget refused mid-lead (%s) -- stopping tonight' % why)
                break
            prev = cache.get(key) if isinstance(cache.get(key), dict) else None
            if status == 'error':
                c['error'] += 1
                errs_in_row += 1
                if prev:
                    prev['err'] = fields['why'][:160]
                    prev['tried'] = round(now_ts, 1)
                else:
                    ent = _entry(fields, ld, env_name, a.region, date_from, now_ts)
                    ent['err'] = fields['why'][:160]
                    cache[key] = ent
                save_cache(cpath, cache)
                if errs_in_row >= MAX_LEAD_ERRORS:
                    log('PACER: %d leads in a row failed (%s) -- stopping tonight' % (errs_in_row, why))
                    rc = 3
                    break
                continue
            errs_in_row = 0
            cache[key] = _entry(fields, ld, env_name, a.region, date_from, now_ts)
            save_cache(cpath, cache)
            c[fields['verdict']] += 1
            if fields['verdict'] == 'active':
                active_keys.append(key)
    finally:
        out = pcl.logout()
        if pcl.creds and not out:
            log('PACER: logout did not confirm (the token expires on its own)')
    for cty, c in sorted(counts.items()):
        log('  PACER %-11s clear %3d  active %3d  unverifiable %3d  error %3d  deferred %3d'
            % (cty, c['clear'], c['active'], c['unverifiable'], c['error'], c['deferred']))
    log('PACER: %d page(s), $%.2f this run%s' % (budget.pages, budget.spent if billable else 0.0,
                                                 '' if billable else ' (QA, not billed)'))
    if active_keys:
        log('PACER: OPEN federal bankruptcy found on %d lead(s): %s' % (len(active_keys), ', '.join(active_keys[:20])))
    return rc


if __name__ == '__main__':
    sys.exit(main())
