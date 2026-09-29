#!/usr/bin/env python
"""bk_lookup.py — federal bankruptcy check for board leads.

CourtListener (Free Law Project) REST API v4 is the provider. PACER registration is not
available. A finished CourtListener search with no open match is not, by itself, a release
for a Broward or Palm Beach lead: that result stays held (clear_unconfirmed) until the
owner sets DEALFLOW_BK_ALLOW_CL_CLEAR=1, which is a business decision. Miami-Dade keeps the
state-docket stay gate (stay_gate.py, #72). This lookup only ADDS a hold there, and only
when it finds an open match. A lifted or closed Miami bankruptcy is not an extra hold.
An active stay is cross-checked with one page of that day's FLSB/FLMB filings; a miss
leaves the docket verdict as it was.

A PACER Case Locator provider can be dropped in later behind BankruptcyProvider. The parked
PCL client is still pacer_stay.py; it is not called from here. Set DEALFLOW_BK_PROVIDER=pacer
only after that class implements the search methods — until then it reports unavailable and
every lead that needs this check stays held.

AUTH. COURTLISTENER_TOKEN in the environment (a Windows user env var on the laptop). Sent as
`Authorization: Token <token>`. The token is never printed, logged, or written to a status
file. No token means the check is unavailable and the lead stays held.

RATE BUDGET (free account, rolling windows): 5/minute, 50/hour, 125/day. A full minute
or hour window waits until a slot frees (the clock is injectable). Only the daily cap, or
a 429 that survives the bounded retries, stops the run. BK_MAX_RUNTIME_S (default 900)
caps one nightly run so the 5:30 refresh is not held up; the pull cursor is saved after
every page and the next run resumes. A cut-off run writes status reason time_budget and
still exits 0.

TWO READS.
  1. Nightly: new bankruptcy filings in the Southern District of Florida (court id flsb)
     since the last successful pull. Search API type=d, court + filed_after, paginated.
     Cached under DEALFLOW_DIR (not the repo). Matched locally against every lead.
  2. Once per lead: a party-name search of federal bankruptcy courts (court ids ending
     in b) for cases still open (no date terminated / closed), filed within
     BK_FILED_AFTER_YEARS (default 10), newest first. CourtListener keeps a single
     `court` parameter, so the id list is space-separated in that one value. A result
     set that still overflows is searched again with the same name tokens, limited to
     the Florida bankruptcy courts and a shorter filed-after window, with more pages.
     A city, ZIP, or exact phrase is not put in `party:`. Cached 14 days. Re-checked
     before a first touch older than that. The 5:30 run stops at BK_MAX_RUNTIME_S.
     The 21:00 evening check runs a second pass so the leftover daily budget is used
     after the hour window frees.

MATCHING. Names are folded (case, accents, punctuation). Middle initials, Hispanic double
surnames, LLC/TRUST owners, and joint owners are all read. An exact open-case match in
flsb, flmb, or flnb is a hard hold. An exact name match in any other bankruptcy court,
with no address or county evidence, is only `possible`. Any weaker plausible match is a
hold whose reason is `possible bankruptcy: <case number>` and is never auto-cleared. A
closed case is not a hold. A CourtListener clear does not release a Broward or Palm Beach
lead unless DEALFLOW_BK_ALLOW_CL_CLEAR=1. The Miami docket date is cross-checked only while
that stay is active, and only as one FLSB/FLMB page for that day. A miss does not add a
hold. A lifted or closed docket bankruptcy leaves the docket verdict as it was unless an
open CourtListener match was found.
A manual override file (DEALFLOW_DIR/bk_overrides.json) drops one bankruptcy case number
for one lead id. Another lead matching that case stays held.

Nothing written here is committed. Filings carry debtor names and stay in DEALFLOW_DIR.
The lead cache and the status file carry case numbers and counts only.

    python bk_lookup.py                 # nightly pull + local match + new-lead searches
    python bk_lookup.py --status        # counts only
    python bk_lookup.py --case CACE-99-000123 [--case ...]
"""
import argparse
import datetime as dt
import glob
import http.client
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

SEARCH_URL = 'https://www.courtlistener.com/api/rest/v4/search/'
PROVIDER_COURTLISTENER = 'courtlistener'
PROVIDER_PACER = 'pacer_pcl'
FLSB = 'flsb'
RECHECK_DAYS = 14.0
ENV_MAX_AGE = 'DEALFLOW_BK_MAX_AGE_DAYS'
ENV_TOKEN = 'COURTLISTENER_TOKEN'
ENV_PROVIDER = 'DEALFLOW_BK_PROVIDER'
ENV_MAX_RUNTIME = 'BK_MAX_RUNTIME_S'
ENV_FILED_YEARS = 'BK_FILED_AFTER_YEARS'
# A CourtListener clear is not a PACER clear. Broward and Palm Beach stay held unless
# the owner sets this to 1. That is a business decision, not the default.
ENV_ALLOW_CL_CLEAR = 'DEALFLOW_BK_ALLOW_CL_CLEAR'
VERDICT_CLEAR_UNCONFIRMED = 'clear_unconfirmed'
DOCKET_WINDOW_DAYS = 3
# One page of the docket day. Shoulders and extra pages would spend the 125/day budget
# on a check the docket stay already decides.
DOCKET_PAGE_CAP = 1
# Miami docket dates are checked against these two courts, not the national list.
DOCKET_COURTS = ('flsb', 'flmb')
CLEAR_UNCONFIRMED_WHY = ('CourtListener found no open match — not a confirmed clear, '
                         'lead stays held')
DEFAULT_MAX_RUNTIME = 900.0
DEFAULT_FILED_YEARS = 10
PRESEND_MAX_WAIT = 3.0
FIRST_PULL_DAYS = 14
PULL_PAGE_CAP = 20
PARTY_PAGE_CAP = 2
PRIORITY_PAGE_CAP = 6
CLI_PAGE_CAP = 8
# The second pass follows more pages than the default lead cap.
NARROW_PAGE_CAP = 8
NIGHTLY_PRESEND_RESERVE = 10
RETRY_429 = 3
RETRY_SLEEP = (2.0, 4.0, 8.0)
MAX_SLEEP = 30.0

CAP_MINUTE = 5
CAP_HOUR = 50
CAP_DAY = 125
WIN_MINUTE = 60.0
WIN_HOUR = 3600.0
WIN_DAY = 86400.0

CACHE_NAME = 'bk_lead_cache.json'
FILINGS_NAME = 'bk_filings.json'
OVERRIDES_NAME = 'bk_overrides.json'
STATUS_NAME = 'bk_lookup_status.json'
BUDGET_NAME = 'bk_budget.json'
PULL_STATE_NAME = 'bk_pull_state.json'
FILING_KEEP_DAYS = 400

GEN = {'JR', 'SR', 'II', 'III', 'IV', 'V'}
TAILS = {'LLC', 'INC', 'CORP', 'CORPORATION', 'TRUST', 'TRUSTEE', 'LP', 'LLP', 'PLLC',
         'LTD', 'LIMITED', 'COMPANY', 'CO', 'NA', 'ASSOC', 'ASSOCIATION', 'FOUNDATION',
         'PARTNERSHIP', 'LLLP'}
_BK_COURT = re.compile(r'^[a-z]{2,12}b$')
# CourtListener jurisdiction=FB, 2026-09-27. Every id ends in b. Party search sends this
# list as one space-separated court value so a common name is not paged across district
# courts. nebraskab and tennesseeb are ids the courts API actually returns.
BK_COURT_IDS = (
    'almb', 'alnb', 'alsb', 'akb', 'arb', 'areb', 'arwb', 'cacb', 'caeb', 'canb', 'casb',
    'cob', 'ctb', 'deb', 'dcb', 'flmb', 'flnb', 'flsb', 'gamb', 'ganb', 'gasb', 'hib', 'idb',
    'ilcb', 'ilnb', 'ilsb', 'innb', 'insb', 'ianb', 'iasb', 'ksb', 'kyeb', 'kywb', 'laeb',
    'lamb', 'lawb', 'meb', 'mdb', 'mab', 'mieb', 'miwb', 'mnb', 'msnb', 'mssb', 'moeb',
    'mowb', 'mtb', 'nebraskab', 'nvb', 'nhb', 'njb', 'nmb', 'nyeb', 'nynb', 'nysb', 'nywb',
    'nceb', 'ncmb', 'ncwb', 'ndb', 'ohnb', 'ohsb', 'okeb', 'oknb', 'okwb', 'orb', 'paeb',
    'pamb', 'pawb', 'rib', 'scb', 'sdb', 'tneb', 'tnmb', 'tnwb', 'tennesseeb', 'txeb',
    'txnb', 'txsb', 'txwb', 'utb', 'vtb', 'vaeb', 'vawb', 'waeb', 'wawb', 'wvnb', 'wvsb',
    'wieb', 'wiwb', 'wyb', 'gub', 'nmib', 'prb', 'vib',
)
# Florida bankruptcy courts. The narrow pass searches only these.
FL_BK_COURT_IDS = ('flsb', 'flmb', 'flnb')
TRUNC_WHY = 'CourtListener party search truncated — lead stays held'
_IN_RE = re.compile(r'^(?:IN\s+RE|IN\s+THE\s+MATTER\s+OF)\s+', re.I)


class ProviderError(Exception):
    pass


class BudgetExhausted(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


class TimeBudget(Exception):
    """The nightly run hit BK_MAX_RUNTIME_S. Progress is saved. Not a hard failure."""


class NetworkError(ProviderError):
    """The request never got an HTTP status back (timeout, reset, DNS, a cut-off body)."""


# A nightly run stops its per-lead searches after this many network failures. One slow
# search should not end the night; a dead connection should not burn BK_MAX_RUNTIME_S.
NIGHTLY_NET_ERROR_LIMIT = 3


def log(msg):
    print(msg, flush=True)


def scrub(text, token):
    """Drop a credential if one ever lands in an error body. Not for logging the token."""
    s = str(text or '')
    if token and token in s:
        s = s.replace(token, '[redacted]')
    return s[:180]


# --------------------------------------------------------------------------------------------------
# provider interface
# --------------------------------------------------------------------------------------------------
class BankruptcyProvider:
    """Federal bankruptcy search. CourtListener implements it. A PACER Case Locator provider
    implements the same three methods and nothing else in the gate has to change."""

    name = ''

    def available(self, env=None):
        """(ok, reason). reason is safe to print: it never contains a credential."""
        return False, 'no bankruptcy provider is configured'

    def filings_request(self, court, filed_after):
        """Absolute URL for one page of new filings. Pagination follows the response `next`."""
        raise ProviderError('%s has no filings request' % (self.name or 'provider'))

    def filings_window_request(self, courts, filed_after, filed_before):
        """Absolute URL for filings in a date window. No party query."""
        raise ProviderError('%s has no filings window' % (self.name or 'provider'))

    def party_request(self, query, filed_after=None, courts=None):
        """Absolute URL for one party-name search. `query` is provider-specific and contains
        no lead id. `courts` is one space-joined court parameter, not repeated keys."""
        raise ProviderError('%s has no party request' % (self.name or 'provider'))

    def parse_search(self, payload):
        """{results: [normalized row], next: url or ''}. A row is court_id, no, filed,
        terminated, parties (strings), open (bool). No other fields are kept."""
        raise ProviderError('%s has no parser' % (self.name or 'provider'))

    def auth_headers(self, env=None):
        return {}


class CourtListenerProvider(BankruptcyProvider):
    name = PROVIDER_COURTLISTENER

    def __init__(self, env=None):
        self.env = env

    def available(self, env=None):
        env = self.env if env is None else env
        env = os.environ if env is None else env
        if str(env.get(ENV_TOKEN) or '').strip():
            return True, ''
        return False, ('CourtListener token missing (set COURTLISTENER_TOKEN) — federal bankruptcy '
                       'check unavailable, lead stays held')

    def auth_headers(self, env=None):
        env = self.env if env is None else env
        env = os.environ if env is None else env
        token = str(env.get(ENV_TOKEN) or '').strip()
        if not token:
            return {}
        return {'Authorization': 'Token ' + token}

    def filings_request(self, court, filed_after):
        q = urllib.parse.urlencode({
            'type': 'd',
            'court': court,
            'filed_after': filed_after,
            'order_by': 'dateFiled desc',
        })
        return SEARCH_URL + '?' + q

    def party_request(self, query, filed_after=None, courts=None):
        """Bankruptcy courts only, newest first, with a filed-after bound.

        CourtListener accepts one `court` value and splits it on spaces. Repeated
        `court=` keys are not a list: the server keeps the last key only, so a loop
        that appends every court id searches `vib` and nothing else."""
        filed = filed_after or default_filed_after(self.env)
        ids = tuple(courts) if courts else BK_COURT_IDS
        params = [
            ('type', 'd'),
            ('q', query),
            ('filed_after', filed),
            ('order_by', 'dateFiled desc'),
            ('court', ' '.join(ids)),
        ]
        return SEARCH_URL + '?' + urllib.parse.urlencode(params)

    def filings_window_request(self, courts, filed_after, filed_before):
        """Docket list for a date window. No party query. Both dates are inclusive.
        `courts` is one space-joined court value."""
        params = [
            ('type', 'd'),
            ('court', ' '.join(courts)),
            ('filed_after', filed_after),
            ('filed_before', filed_before),
            ('order_by', 'dateFiled desc'),
        ]
        return SEARCH_URL + '?' + urllib.parse.urlencode(params)

    def parse_search(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise ProviderError('CourtListener search response is not a listing')
        rows = []
        for row in payload.get('results') or []:
            if not isinstance(row, dict):
                continue
            norm = _normalize_recap(row)
            if norm['no']:
                rows.append(norm)
        nxt = payload.get('next') or ''
        if not _same_host(nxt):
            nxt = ''
        return {'results': rows, 'next': nxt}


class PacerCaseLocatorProvider(BankruptcyProvider):
    """Slot for the PACER Case Locator. Not implemented: registration is not available, and
    pacer_stay.py remains the parked PCL client the stay gate already knows how to read.
    available() is false so selecting this provider holds every lead that needs a check."""

    name = PROVIDER_PACER

    def available(self, env=None):
        return False, ('PACER Case Locator is not registered — CourtListener is the federal '
                       'bankruptcy provider, and this lead stays held')


def get_provider(name=None, env=None):
    env = os.environ if env is None else env
    name = (name if name is not None else env.get(ENV_PROVIDER) or PROVIDER_COURTLISTENER)
    name = str(name or '').strip().lower()
    if name in ('pacer', 'pacer_pcl', 'pcl'):
        return PacerCaseLocatorProvider()
    return CourtListenerProvider(env)


def _same_host(url):
    if not url:
        return False
    try:
        host = (urllib.parse.urlparse(url).hostname or '').lower()
    except Exception:
        return False
    return host == 'www.courtlistener.com' or host == 'courtlistener.com'


def _date_field(row, *keys):
    for k in keys:
        if k not in row:
            continue
        v = row.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in ('none', 'null'):
            return s[:10]
    return ''


def _party_strings(row):
    out = []
    party = row.get('party')
    if isinstance(party, list):
        out.extend(str(x) for x in party if str(x or '').strip())
    elif isinstance(party, str) and party.strip():
        out.append(party)
    for k in ('caseName', 'case_name', 'caseNameFull'):
        if row.get(k):
            out.append(str(row.get(k)))
    return out


def _normalize_recap(row):
    court_id = str(row.get('court_id') or row.get('courtId') or '').strip().lower()
    court = str(row.get('court') or '').strip()
    terminated = _date_field(row, 'dateTerminated', 'date_terminated', 'dateClosed', 'date_closed')
    return {
        'court_id': court_id,
        'court': court,
        'no': str(row.get('docketNumber') or row.get('docket_number') or '').strip(),
        'filed': _date_field(row, 'dateFiled', 'date_filed'),
        'terminated': terminated,
        'parties': _party_strings(row),
        'open': not bool(terminated),
    }


def is_bk_court(court_id, court_name=''):
    cid = str(court_id or '').lower()
    if _BK_COURT.match(cid):
        return True
    name = str(court_name or '').lower()
    return 'bankruptcy' in name and 'appellate' not in name


# --------------------------------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------------------------------
def tokens(raw):
    s = unicodedata.normalize('NFKD', str(raw or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = s.upper().replace("'", '').replace('\u2019', '').replace('`', '')
    return re.findall(r'[A-Z0-9]+', s)


def is_entity_text(raw):
    return any(t in TAILS for t in tokens(raw))


def entity_core(raw):
    return [t for t in tokens(raw) if t not in TAILS and t != 'THE']


def _strip_caption(raw):
    return _IN_RE.sub('', str(raw or '').strip())


def person_readings(raw, order):
    """Plausible {first, surnames, middle} readings. [] when it is not a person's name."""
    raw = _strip_caption(raw)
    if not raw or is_entity_text(raw):
        return []
    readings = []
    if ',' in raw:
        last, _, rest = raw.partition(',')
        lt = [t for t in tokens(last) if t not in GEN]
        rt = [t for t in tokens(rest) if t not in GEN]
        if lt and rt and len(rt[0]) > 1 and all(len(t) > 1 for t in lt):
            readings.append({'first': rt[0], 'surnames': lt, 'middle': rt[1:]})
        return readings
    toks = [t for t in tokens(raw) if t not in GEN]
    if len(toks) < 2 or len(toks) > 4:
        return []
    if order == 'first_last':
        if len(toks) == 2:
            readings.append({'first': toks[0], 'surnames': [toks[1]], 'middle': []})
        elif len(toks) == 3:
            readings.append({'first': toks[0], 'surnames': [toks[2]], 'middle': [toks[1]]})
            readings.append({'first': toks[0], 'surnames': [toks[1], toks[2]], 'middle': []})
        else:
            readings.append({'first': toks[0], 'surnames': [toks[2], toks[3]], 'middle': [toks[1]]})
            readings.append({'first': toks[0], 'surnames': [toks[3]], 'middle': toks[1:3]})
    else:
        if len(toks) == 2:
            readings.append({'first': toks[1], 'surnames': [toks[0]], 'middle': []})
        elif len(toks) == 3:
            readings.append({'first': toks[2], 'surnames': [toks[0], toks[1]], 'middle': []})
            readings.append({'first': toks[1], 'surnames': [toks[0]], 'middle': [toks[2]]})
        else:
            readings.append({'first': toks[2], 'surnames': [toks[0], toks[1]], 'middle': [toks[3]]})
            readings.append({'first': toks[3], 'surnames': [toks[0], toks[1]], 'middle': [toks[2]]})
    return [r for r in readings if len(r['first']) > 1 and all(len(s) > 1 for s in r['surnames'])]


def _mid_rank(a, b):
    """'exact' when middles are absent or compatible, else 'plausible'."""
    if not a or not b:
        return 'exact'
    if a == b:
        return 'exact'
    af, bf = a[0], b[0]
    if af[0] == bf[0] and (len(af) == 1 or len(bf) == 1 or af.startswith(bf) or bf.startswith(af)):
        return 'exact'
    return 'plausible'


def match_readings(owners, parties):
    best = None
    for o in owners:
        for p in parties:
            if o['first'] == p['first']:
                first = 'exact'
            elif (len(o['first']) == 1 and p['first'].startswith(o['first'])) or (
                    len(p['first']) == 1 and o['first'].startswith(p['first'])):
                first = 'plausible'
            elif o['first'].startswith(p['first']) or p['first'].startswith(o['first']):
                first = 'plausible'
            else:
                continue
            osn, psn = set(o['surnames']), set(p['surnames'])
            if not (osn & psn):
                continue
            sur = 'exact' if osn == psn else 'plausible'
            mid = _mid_rank(o['middle'], p['middle'])
            if first == 'exact' and sur == 'exact' and mid == 'exact':
                return 'exact'
            best = 'plausible'
    return best


def match_entity(a, b):
    if a and a == b:
        return 'exact'
    if not a or not b:
        return None
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if set(short) <= set(long_) and any(len(t) >= 4 for t in short):
        return 'plausible'
    return None


def owners_from(raw, order):
    """People and entities on one owner string. Joint owners split on &, AND, ; and /."""
    text = str(raw or '').strip()
    if not text:
        return []
    owners = []
    for chunk in re.split(r'\s*;\s*', text):
        prev = None
        for part in re.split(r'\s*(?:&|\bAND\b|/)\s*', chunk, flags=re.I):
            part = part.strip()
            if not part:
                continue
            if is_entity_text(part):
                core = entity_core(part)
                if core:
                    owners.append({'kind': 'entity', 'tokens': core, 'readings': []})
                prev = None
                continue
            readings = person_readings(part, order)
            if not readings:
                one = [t for t in tokens(part) if t not in GEN]
                if len(one) == 1 and len(one[0]) > 1 and prev and prev['kind'] == 'person':
                    readings = [{'first': one[0], 'surnames': list(r['surnames']), 'middle': []}
                                for r in prev['readings']]
            if not readings:
                continue
            person = {'kind': 'person', 'tokens': [], 'readings': readings}
            owners.append(person)
            prev = person
    return owners


def party_readings(party):
    party = _strip_caption(party)
    if ',' in party:
        return person_readings(party, 'last_first')
    return None


def match_owner_party(owner, party):
    party = _strip_caption(party)
    if owner['kind'] == 'entity':
        core = entity_core(party) or [t for t in tokens(party) if t not in GEN]
        return match_entity(owner['tokens'], core)
    comma = party_readings(party)
    if comma is not None:
        return match_readings(owner['readings'], comma)
    best = None
    for order in ('first_last', 'last_first'):
        got = match_readings(owner['readings'], person_readings(party, order))
        if got == 'exact':
            return 'exact'
        if got:
            best = 'plausible'
    return best


def match_owners(owners, parties):
    """'exact' | 'plausible' | None against any party string."""
    best = None
    for owner in owners:
        for party in parties or []:
            got = match_owner_party(owner, party)
            if got == 'exact':
                return 'exact'
            if got:
                best = 'plausible'
    return best


def query_for(owner):
    """One CourtListener `q` value. Empty when the name is too thin to search (fail closed)."""
    if owner['kind'] == 'entity':
        toks = [t for t in owner['tokens'] if len(t) > 1][:4]
    else:
        reading = max(owner['readings'], key=lambda r: (len(r['surnames']), len(r['first'])))
        toks = [t for t in list(reading['surnames']) + [reading['first']] if len(t) > 1][:4]
    if owner['kind'] == 'person' and len(toks) < 2:
        return ''
    if not toks:
        return ''
    return 'party:(' + ' AND '.join(toks) + ')'


def case_no_key(value):
    s = ' '.join(str(value or '').split()).upper()
    return re.sub(r'\s*-\s*', '-', s)


# --------------------------------------------------------------------------------------------------
# state files (DEALFLOW_DIR — never the repo)
# --------------------------------------------------------------------------------------------------
def state_dir():
    d = str(os.environ.get('DEALFLOW_DIR') or '').strip()
    if not d:
        import paths as P
        d = P.DEALFLOW_DIR
    os.makedirs(d, exist_ok=True)
    return d


def _path(name, env_key):
    override = str(os.environ.get(env_key) or '').strip()
    if override:
        parent = os.path.dirname(override)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return override
    return os.path.join(state_dir(), name)


def cache_path():
    return _path(CACHE_NAME, 'DEALFLOW_BK_CACHE')


def filings_path():
    return _path(FILINGS_NAME, 'DEALFLOW_BK_FILINGS')


def overrides_path():
    return _path(OVERRIDES_NAME, 'DEALFLOW_BK_OVERRIDES')


def status_path():
    return _path(STATUS_NAME, 'DEALFLOW_BK_STATUS')


def budget_path():
    return _path(BUDGET_NAME, 'DEALFLOW_BK_BUDGET')


def pull_state_path():
    return _path(PULL_STATE_NAME, 'DEALFLOW_BK_PULL_STATE')


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, False
    except Exception:
        return None, True
    return data, True


def _dump(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def load_overrides():
    """{lead_key: set(case_no_key)}. Accepts {lead: [cases]} or {clears: [{lead_id, case_number}]}."""
    data, exists = _load(overrides_path())
    out = {}
    if not exists or not isinstance(data, dict):
        return out

    def add(lead, number):
        import stay_gate
        key = stay_gate.pacer_key(lead)
        no = case_no_key(number)
        if key and no:
            out.setdefault(key, set()).add(no)

    clears = data.get('clears')
    if isinstance(clears, list):
        for row in clears:
            if isinstance(row, dict):
                add(row.get('lead_id') or row.get('lead') or row.get('case'),
                    row.get('case_number') or row.get('case') or row.get('no'))
    for k, v in data.items():
        if k == 'clears' or str(k).startswith('_'):
            continue
        if isinstance(v, list):
            for n in v:
                add(k, n)
        elif isinstance(v, str):
            add(k, v)
    return out


class Budget:
    def __init__(self, hits, now):
        now = float(now)
        self.hits = sorted(float(t) for t in hits if now - float(t) < WIN_DAY)
        self.now = now

    def counts(self, now=None):
        now = self.now if now is None else float(now)
        self.hits = [t for t in self.hits if now - t < WIN_DAY]
        return {
            'minute': sum(1 for t in self.hits if now - t < WIN_MINUTE),
            'hour': sum(1 for t in self.hits if now - t < WIN_HOUR),
            'day': len(self.hits),
        }

    def room(self, now=None, reserve=0):
        c = self.counts(now)
        if c['minute'] >= CAP_MINUTE or c['hour'] >= CAP_HOUR:
            return False
        return c['day'] + int(reserve) < CAP_DAY

    def record(self, now):
        now = float(now)
        self.hits.append(now)
        self.now = now


def load_budget(now):
    """A budget file that exists but cannot be parsed is a full day: fail closed, do not reset."""
    data, exists = _load(budget_path())
    if exists and not isinstance(data, dict):
        b = Budget([float(now)] * CAP_DAY, now)
        b.unreadable = True
        return b
    hits = []
    if isinstance(data, dict):
        hits = data.get('hits') or []
    if not isinstance(hits, list):
        # The file parsed but the hit list is garbage. Same fail-closed rule.
        b = Budget([float(now)] * CAP_DAY, now)
        b.unreadable = True
        return b
    b = Budget(hits, now)
    b.unreadable = False
    return b


def save_budget(budget):
    _dump(budget_path(), {'hits': [round(t, 3) for t in budget.hits]})


class Clock:
    """Wall time for request timestamps.

    A simulated clock (now= or wall=) advances when sleep() is called and when advance()
    is called for time spent waiting on HTTP. The real clock is time.time(), which already
    moves during urlopen, so a request is stamped when the response arrives rather than at
    the last sleep. Tests inject wall= or now=."""

    def __init__(self, now=None, sleeper=None, wall=None):
        self._sleeper = time.sleep if sleeper is None else sleeper
        self._real = wall is None and now is None
        if wall is not None:
            self._wall = wall
        elif now is not None:
            self._box = [float(now)]
            self._wall = lambda: self._box[0]
        else:
            self._wall = time.time
        self._extra = 0.0

    def time(self):
        if self._real:
            return float(self._wall())
        return float(self._wall()) + self._extra

    @property
    def now(self):
        return self.time()

    @now.setter
    def now(self, value):
        if self._real:
            return
        self._extra = float(value) - float(self._wall())

    def advance(self, seconds):
        """Count time that passed outside sleep, such as an HTTP round trip."""
        if self._real:
            return
        self._extra += float(seconds)

    def sleep(self, seconds):
        seconds = max(0.0, min(float(seconds), MAX_SLEEP))
        if seconds:
            self._sleeper(seconds)
        if not self._real:
            self._extra += seconds


def urllib_transport(url, headers, timeout=45):
    req = urllib.request.Request(url, headers=dict(headers or {}), method='GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(getattr(e, 'headers', {}) or {}), e.read() or b''


def _retry_after(headers):
    raw = ''
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == 'retry-after':
                raw = str(v)
                break
    try:
        return max(0.0, min(float(raw), MAX_SLEEP))
    except (TypeError, ValueError):
        return 0.0


def _filed_years(env=None):
    env = os.environ if env is None else env
    raw = str((env or {}).get(ENV_FILED_YEARS) or '').strip()
    try:
        years = int(raw) if raw else DEFAULT_FILED_YEARS
    except ValueError:
        years = DEFAULT_FILED_YEARS
    if years < 1:
        years = DEFAULT_FILED_YEARS
    return years


def _years_ago(years, today=None):
    today = dt.date.today() if today is None else today
    try:
        return today.replace(year=today.year - int(years)).isoformat()
    except ValueError:
        return (today - dt.timedelta(days=365 * int(years))).isoformat()


def default_filed_after(env=None, today=None):
    """ISO date. Party search ignores dockets older than this. BK_FILED_AFTER_YEARS, default 10."""
    return _years_ago(_filed_years(env), today)


def narrow_filed_after(env=None, today=None):
    """A shorter window than default_filed_after. Half the configured years, at least 1.
    A finished search at this date cannot clear: older open cases would be invisible."""
    years = _filed_years(env)
    tight = max(1, years // 2)
    if tight >= years and years > 1:
        tight = years - 1
    return _years_ago(tight, today)


def narrow_bounds(env=None, today=None):
    """(courts, filed_after) for the second pass, or None when it would not be narrower.

    Same name tokens. Florida bankruptcy courts, and a shorter filed-after when the
    configured window is longer than a year. No city, ZIP, or quoted phrase."""
    courts = FL_BK_COURT_IDS
    filed = narrow_filed_after(env, today)
    eligible = default_filed_after(env, today)
    tighter_courts = set(courts) < set(BK_COURT_IDS)
    tighter_date = filed > eligible
    if not tighter_courts and not tighter_date:
        return None
    return courts, filed


def scope_can_clear(courts, filed_after, env=None, today=None):
    """True when a finished search of this scope covered every court and year the lead
    is eligible for. A Florida-only search or a shorter window can hold. It cannot clear."""
    if set(BK_COURT_IDS) - set(courts or ()):
        return False
    return str(filed_after or '9999') <= default_filed_after(env, today)


def narrow_page_cap(cap):
    return max(int(cap), NARROW_PAGE_CAP)


def cl_clear_allowed(env=None):
    """True only when the owner has decided a CourtListener clear may release a lead."""
    env = os.environ if env is None else env
    return str((env or {}).get(ENV_ALLOW_CL_CLEAR) or '').strip() == '1'


PACER_ROOT = None   # folder holding pacer_stay_cache.json; None = beside stay_gate.py (tests override)


def pacer_confirmed(key, now=None):
    """True only when PACER itself says this lead is clear: a fresh production per-lead
    'clear' in pacer_stay_cache.json (stay_gate.pacer_verdict) and no blocking new-filer hit.

    This is the confirmed clear Broward and Palm Beach have been waiting on. A missing,
    unreadable, stale, QA, 'unverifiable' or 'active' entry is False, and so is any error:
    the lead stays held. It never overrides a CourtListener hold; callers only ask it about
    leads CourtListener did not flag. The files live beside sale_history_cache.json, where
    pacer_stay.py writes them and stay_gate.check() reads them."""
    try:
        import stay_gate
        if stay_gate.never_contact(key):
            return False                               # contacted during a bankruptcy: never released
        root = PACER_ROOT or os.path.dirname(os.path.abspath(stay_gate.__file__))
        idx, err, exists = stay_gate._load_pacer(os.path.join(root, stay_gate.PACER_NAME))
        if not exists or err or not idx:
            return False
        ent = idx[0].get(key)
        if stay_gate.pacer_verdict(ent, now)[0] != stay_gate.CLEAR:
            return False
        hidx, herr, hexists = stay_gate._load_hits(os.path.join(root, stay_gate.HITS_NAME))
        if herr:
            return False
        hit = hidx[0].get(key) if (hexists and hidx) else None
        if hit is not None and stay_gate.hit_blocks(hit, ent, now):
            return False
        return True
    except Exception:
        return False


def max_runtime_s(env=None):
    env = os.environ if env is None else env
    raw = str(env.get(ENV_MAX_RUNTIME) or '').strip()
    try:
        v = float(raw) if raw else DEFAULT_MAX_RUNTIME
    except ValueError:
        return DEFAULT_MAX_RUNTIME
    return v if v > 0 else DEFAULT_MAX_RUNTIME


def _slot_wait(budget, now, reserve=0):
    """Seconds until a minute/hour slot frees. None when the day cap (plus reserve) is spent."""
    c = budget.counts(now)
    if c['day'] + int(reserve) >= CAP_DAY:
        return None
    wait = 0.0
    if c['minute'] >= CAP_MINUTE:
        oldest = min(t for t in budget.hits if now - t < WIN_MINUTE)
        wait = max(wait, WIN_MINUTE - (now - oldest) + 0.05)
    if c['hour'] >= CAP_HOUR:
        oldest = min(t for t in budget.hits if now - t < WIN_HOUR)
        wait = max(wait, WIN_HOUR - (now - oldest) + 0.05)
    return wait


def wait_for_slot(budget, clock, reserve=0, max_wait=None, deadline=None):
    """Block until minute and hour room exists.

    The day cap raises BudgetExhausted and is not waited out. max_wait caps how long a
    caller (the send bridge) is willing to sleep; a longer wait raises BudgetExhausted
    without sleeping it. deadline is the nightly runtime limit."""
    if getattr(budget, 'unreadable', False):
        raise BudgetExhausted('CourtListener rate budget file is unreadable — lead stays held')
    slept = 0.0
    while True:
        now = clock.time()
        if deadline is not None and now >= float(deadline):
            raise TimeBudget('CourtListener nightly run hit its time limit')
        wait = _slot_wait(budget, now, reserve=reserve)
        if wait is None:
            raise BudgetExhausted('CourtListener daily rate budget exhausted — lead stays held')
        if wait <= 0:
            return
        if deadline is not None and now + wait > float(deadline):
            raise TimeBudget('CourtListener nightly run hit its time limit')
        if max_wait is not None and slept + wait > float(max_wait) + 1e-9:
            raise BudgetExhausted('CourtListener rate budget is tight — lead stays held')
        before = clock.time()
        clock.sleep(wait)
        slept += clock.time() - before


def http_get(url, provider, transport, budget, clock, reserve=0, max_wait=None, deadline=None):
    """(status, payload_dict). Records every attempt. Raises BudgetExhausted, RateLimited, or TimeBudget.
    The token is not included in any exception text. A full minute or hour window waits.
    max_wait bounds every sleep in this call, including 429 backoff. A longer wait raises
    instead of sleeping it."""
    headers = provider.auth_headers()
    token = ''
    auth = headers.get('Authorization') or ''
    if auth.lower().startswith('token '):
        token = auth.split(' ', 1)[1]
    attempt = 0
    slept = 0.0
    while True:
        remaining = None if max_wait is None else max(0.0, float(max_wait) - slept)
        before = clock.time()
        wait_for_slot(budget, clock, reserve=reserve, max_wait=remaining, deadline=deadline)
        slept += clock.time() - before
        try:
            status, hdrs, body = transport(url, headers)
        except (OSError, http.client.HTTPException) as e:
            # Count it: the request may have reached CourtListener before the connection died.
            # The exception text is not kept -- only its class, which carries no name or token.
            budget.record(clock.time())
            if not getattr(budget, 'unreadable', False):
                save_budget(budget)
            raise NetworkError('CourtListener network error (%s)' % type(e).__name__)
        budget.record(clock.time())
        if not getattr(budget, 'unreadable', False):
            save_budget(budget)
        if status == 429 and attempt < RETRY_429:
            wait = _retry_after(hdrs) or RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)]
            attempt += 1
            if deadline is not None and clock.time() + wait > float(deadline):
                raise TimeBudget('CourtListener nightly run hit its time limit')
            if max_wait is not None and slept + wait > float(max_wait) + 1e-9:
                raise RateLimited(
                    'CourtListener returned 429 until the retry budget was spent — lead stays held')
            before = clock.time()
            clock.sleep(wait)
            slept += clock.time() - before
            continue
        if status == 429:
            raise RateLimited('CourtListener returned 429 until the retry budget was spent — lead stays held')
        if status != 200:
            # Touch the body only to redact a token if the server echoed one. The text is not logged.
            raw = body.decode('utf-8', 'replace') if isinstance(body, (bytes, bytearray)) else body
            scrub(raw, token)
            raise ProviderError('CourtListener HTTP %s' % status)
        try:
            payload = json.loads(body.decode('utf-8') if isinstance(body, (bytes, bytearray)) else body)
        except Exception:
            raise ProviderError('CourtListener response was not JSON')
        return status, payload


def paginate(url, provider, transport, budget, clock, page_cap, reserve=0, on_page=None,
             max_wait=None, deadline=None):
    """(rows, truncated). on_page(page_rows, next_url) runs after each page so a cursor can be saved."""
    rows = []
    seen = 0
    truncated = False
    while url and seen < page_cap:
        if not _same_host(url) and seen:
            break
        _status, payload = http_get(url, provider, transport, budget, clock, reserve=reserve,
                                    max_wait=max_wait, deadline=deadline)
        page = provider.parse_search(payload)
        rows.extend(page['results'])
        seen += 1
        nxt = page.get('next') or ''
        if on_page:
            on_page(page['results'], nxt)
        url = nxt
        if url and seen >= page_cap:
            truncated = True
            url = ''
    if url:
        truncated = True
    return rows, truncated


# --------------------------------------------------------------------------------------------------
# verdicts
# --------------------------------------------------------------------------------------------------
def max_age_days():
    raw = str(os.environ.get(ENV_MAX_AGE) or '').strip()
    try:
        v = float(raw) if raw else RECHECK_DAYS
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def _public_case(row, match):
    return {
        'no': str(row.get('no') or '')[:40],
        'court': str(row.get('court_id') or row.get('court') or '')[:12],
        'open': bool(row.get('open')),
        'match': match if match in ('exact', 'plausible') else '',
        'filed': str(row.get('filed') or '')[:10],
    }


def merge_cases(old, new):
    """Keep prior open matches unless a newer row shows that case number closed.
    A plausible match is never dropped just because a later search did not repeat it."""
    by = {}
    for c in old or []:
        if isinstance(c, dict) and case_no_key(c.get('no')):
            by[case_no_key(c.get('no'))] = dict(c)
    for c in new or []:
        if not isinstance(c, dict):
            continue
        k = case_no_key(c.get('no'))
        if not k:
            continue
        if not c.get('open'):
            by.pop(k, None)
            continue
        prev = by.get(k)
        row = dict(c)
        if prev and prev.get('match') == 'exact':
            row['match'] = 'exact'
        by[k] = row
    return list(by.values())


def _place_evidence(hit, county, place):
    """True when the hit text carries this lead's county or place. Name-only is not evidence."""
    parts = [str(p) for p in (hit.get('parties') or [])]
    for k in ('address', 'addresses', 'county', 'city', 'zip'):
        v = hit.get(k)
        if v:
            parts.append(str(v))
    blob = ' '.join(parts).upper()
    raw_county = ' '.join(str(county or '').upper().split())
    if raw_county and len(raw_county) >= 4 and raw_county in blob:
        return True
    token = str(place or '').upper().strip()
    if token and len(token) >= 4 and token in blob:
        return True
    return False


def cases_from_hits(owners, hits, county='', place=''):
    """Normalized public cases (no party names) for bankruptcy hits that name an owner.

    An exact match outside flsb/flmb/flnb with no address or county evidence is stored as
    plausible, so it is a possible hold and not a hard hold. Florida exact matches stay exact."""
    fl = set(FL_BK_COURT_IDS)
    found = []
    for hit in hits or []:
        if not is_bk_court(hit.get('court_id'), hit.get('court')):
            continue
        level = match_owners(owners, hit.get('parties') or [])
        if not level:
            continue
        court_id = str(hit.get('court_id') or '').lower()
        if level == 'exact' and court_id not in fl and not _place_evidence(hit, county, place):
            level = 'plausible'
        if not hit.get('open'):
            row = _public_case(hit, level)
            row['open'] = False
            row['match'] = level
            found.append(row)
            continue
        found.append(_public_case(hit, level))
    return found


def verdict_of(cases, searched, ok_check, err_why=''):
    exact = [c for c in cases if c.get('open') and c.get('match') == 'exact' and c.get('no')]
    plaus = [c for c in cases if c.get('open') and c.get('match') == 'plausible' and c.get('no')]
    if exact:
        nos = ', '.join(c['no'] for c in exact[:3])
        return 'active', 'open federal bankruptcy %s (exact match)' % nos
    if plaus:
        nos = ', '.join(c['no'] for c in plaus[:3])
        return 'possible', 'possible bankruptcy: %s' % nos
    if searched and ok_check and not err_why:
        return 'clear', 'no open federal bankruptcy matched this owner'
    return 'unavailable', err_why or 'federal bankruptcy check has not run for this lead'


def apply_override(cases, lead_key, overrides):
    drop = overrides.get(lead_key) or set()
    if not drop:
        return list(cases or [])
    return [c for c in (cases or []) if case_no_key(c.get('no')) not in drop]


def entry_opinion(key, ent, overrides, now):
    """What the stay gate should do with one cache entry. None when there is no entry."""
    if not isinstance(ent, dict):
        return None
    cases = apply_override(ent.get('cases') or [], key, overrides)
    had_cases = bool(ent.get('cases'))
    overridden_all = had_cases and not cases and bool(overrides.get(key))
    verdict, why = verdict_of(cases, bool(ent.get('searched')), bool(ent.get('ok_check')),
                              '' if overridden_all else str(ent.get('err') or ''))
    if overridden_all and verdict == 'unavailable' and not ent.get('err'):
        # The only matches were the false positive this lead's override names.
        if ent.get('searched') or ent.get('verdict') in ('active', 'possible'):
            verdict, why = 'clear', 'no open federal bankruptcy matched this owner'
    age = None
    try:
        age = (float(now) - float(ent.get('t') or 0)) / 86400.0
    except (TypeError, ValueError):
        age = None
    limit = max_age_days()
    bd = ''
    opens = [c for c in cases if c.get('open') and c.get('filed')]
    if opens:
        bd = max(str(c.get('filed') or '') for c in opens)
    if verdict == 'active':
        return {'blocks': True, 'ok': False, 'code': 'stay_active', 'why': why,
                'bk_need': False, 'bd': bd, 'src': 'courtlistener'}
    if verdict == 'possible':
        return {'blocks': True, 'ok': False, 'code': 'stay_unverified', 'why': why,
                'bk_need': False, 'bd': bd, 'src': 'courtlistener'}
    if verdict == 'clear':
        # A missed docket-date cross-check is not a hold. The Miami stay gate already
        # holds an active stay, and a lifted or closed one must stay clear unless an
        # open match was stored in `cases` (that recomputes as active above).
        fresh = age is not None and limit > 0 and -1 <= age <= limit
        if not fresh:
            return {'blocks': False, 'ok': False, 'code': 'stay_unverified', 'bk_need': True, 'bd': '',
                    'why': 'federal bankruptcy check is older than %g days — re-check before contact' % (limit or 0),
                    'src': 'courtlistener'}
        try:
            import stay_gate
            stem = bool(stay_gate.case_stem(key))
        except Exception:
            stem = False
        if not stem and not cl_clear_allowed() and not pacer_confirmed(key, now):
            # A CourtListener clear alone does not release Broward or Palm Beach. A fresh
            # PACER per-lead clear does (pacer_confirmed).
            return {'blocks': True, 'ok': False, 'code': VERDICT_CLEAR_UNCONFIRMED,
                    'why': CLEAR_UNCONFIRMED_WHY, 'bk_need': False, 'bd': '', 'src': 'courtlistener'}
        return {'blocks': False, 'ok': True, 'code': 'clear', 'why': why,
                'bk_need': False, 'bd': '', 'src': 'courtlistener'}
    return {'blocks': True, 'ok': False, 'code': 'stay_unverified', 'why': why,
            'bk_need': True, 'bd': '', 'src': 'courtlistener'}


def gate_opinion(case, now=None):
    """stay_gate's view of this case. None when no CourtListener cache exists yet
    (the PACER path is unchanged). Never raises."""
    try:
        import stay_gate
        key = stay_gate.pacer_key(case)
    except Exception:
        return None
    if not key:
        return None
    data, exists = _load(cache_path())
    if not exists:
        return None
    if not isinstance(data, dict):
        return {'blocks': True, 'ok': False, 'code': 'stay_unverified', 'bk_need': True, 'bd': '',
                'why': 'federal bankruptcy cache is unreadable — lead stays held',
                'src': 'courtlistener'}
    now = time.time() if now is None else now
    ent = data.get(key)
    if not isinstance(ent, dict):
        return {'blocks': False, 'ok': False, 'code': '', 'why': '', 'bk_need': True, 'bd': '',
                'src': 'courtlistener'}
    op = entry_opinion(key, ent, load_overrides(), now)
    # Release direction only: a Broward / Palm Beach lead CourtListener could not confirm is
    # cleared when PACER has a fresh clear. Everything else is returned exactly as before.
    if op and not op.get('ok') and not stay_gate.case_stem(key):
        op = _pacer_release(key, op, now) or op
    return op


def contact_blocked_reason(case, here=None):
    """(held, why) using the same stay_gate verdict the send bridge uses."""
    try:
        import stay_gate
        root = here or os.path.dirname(os.path.abspath(stay_gate.__file__))
        v = stay_gate.check(case, os.path.join(root, 'sale_history_cache.json'))
    except Exception:
        return True, 'federal bankruptcy check unavailable — lead stays held'
    if v.get('ok'):
        return False, ''
    return True, v.get('why') or 'federal bankruptcy check has not run for this lead'


def _pacer_release(key, op, now):
    """The clear opinion for a Broward / Palm Beach lead when PACER has a fresh clear and
    CourtListener found nothing against the owner; None otherwise.

    CourtListener evidence always wins: an open exact match (stay_active), a plausible match
    (possible: blocks with no further search wanted), and an unreadable cache all return None.
    Only a CourtListener result that failed to CONFIRM a clear (clear_unconfirmed, stale, not
    run, truncated, errored) can be completed by PACER's national per-lead search."""
    if op:
        code = op.get('code')
        if code == 'stay_active':
            return None
        if op.get('blocks') and not op.get('bk_need') and code != VERDICT_CLEAR_UNCONFIRMED:
            return None
    if not pacer_confirmed(key, now):
        return None
    return {'blocks': False, 'ok': True, 'code': 'clear', 'bk_need': False, 'bd': '',
            'why': 'no open federal bankruptcy for the owner in PACER', 'src': 'pacer_pcl'}


def _non_stem_opinion(key, op, now):
    """A Broward / Palm Beach lead CourtListener did not clear: held unless _pacer_release."""
    rel = _pacer_release(key, op, now)
    if rel:
        return rel
    op = dict(op)
    op['blocks'] = True
    if not op.get('why'):
        op['why'] = 'federal bankruptcy check has not run for this lead'
    return op


class HoldIndex:
    """One read of the CourtListener cache, reused for every row of a dial queue."""

    def __init__(self, data, exists, overrides, now):
        self.unreadable = bool(exists and not isinstance(data, dict))
        self.cache = data if isinstance(data, dict) else {}
        self.exists = bool(exists and isinstance(data, dict))
        self.overrides = overrides or {}
        self.now = now
        self._memo = {}

    def hold(self, case):
        """(held, why). Miami is held only when CourtListener flagged it. A keyable
        non-stem lead is held once a cache file exists and it has no fresh clear.
        No cache file yet: not a hold (the board bake still stamps saleBkAct)."""
        key = str(case or '')
        if key in self._memo:
            return self._memo[key]
        try:
            import stay_gate
        except Exception:
            out = (True, 'federal bankruptcy check unavailable — lead stays held')
            self._memo[key] = out
            return out
        pk = stay_gate.pacer_key(case)
        if not pk:
            out = (False, '')
        elif self.unreadable and not stay_gate.case_stem(pk):
            out = (True, 'federal bankruptcy cache is unreadable — lead stays held')
        elif not self.exists and not self.unreadable:
            out = (False, '')
        else:
            ent = self.cache.get(pk)
            if isinstance(ent, dict):
                op = entry_opinion(pk, ent, self.overrides, self.now)
                if op and not stay_gate.case_stem(pk) and not op.get('ok'):
                    op = _non_stem_opinion(pk, op, self.now)
                if op and op.get('blocks'):
                    out = (True, op.get('why') or 'federal bankruptcy check has not run for this lead')
                else:
                    out = (False, '')
            elif not stay_gate.case_stem(pk) and not pacer_confirmed(pk, self.now):
                out = (True, 'federal bankruptcy check has not run for this lead')
            else:
                out = (False, '')
        out = _apply_clerk_hold(case, out, self.now)
        self._memo[key] = out
        return out


_HOLD_MEMO = None  # (signature, HoldIndex) so a planner loop does not re-read the file


def _cache_sig():
    path = cache_path()
    try:
        st = os.stat(path)
        base = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        base = (path, None, None)
    extra = None
    if str(os.environ.get('DEALFLOW_CLERK_BK') or '').strip() == '1':
        try:
            import clerk_bk
            extra = (clerk_bk.cache_sig(), clerk_bk.county_sig())
        except Exception:
            extra = ('clerk-unreadable',)
    return (base, extra)


def _apply_clerk_hold(case, out, now=None):
    """(held, why). With DEALFLOW_CLERK_BK off this is `out` unchanged. An active clerk stay
    replaces a weaker answer. Any other clerk hold replaces a clear only, including a lead
    recorded as Broward or Palm Beach whose number is not a clerk-readable civil case. A
    throw while the flag is on holds. `now` is the HoldIndex clock."""
    held, why = out
    if str(os.environ.get('DEALFLOW_CLERK_BK') or '').strip() != '1':
        return out
    try:
        import clerk_bk
        op = clerk_bk.gate_opinion(case, now)
    except Exception:
        return True, 'clerk docket check failed. Lead stays held.'
    if not isinstance(op, dict) or not op.get('blocks'):
        return out
    if op.get('code') == 'stay_active' or not held:
        return True, (op.get('why') or why)
    return out


def federal_hold_index(now=None):
    """Load the CourtListener cache once. call_rows keeps the returned index for the whole call."""
    global _HOLD_MEMO
    sig = _cache_sig()
    now = time.time() if now is None else now
    if _HOLD_MEMO and _HOLD_MEMO[0] == sig:
        _HOLD_MEMO[1].now = now
        return _HOLD_MEMO[1]
    data, exists = _load(cache_path())
    overrides = load_overrides() if exists else {}
    idx = HoldIndex(data, exists, overrides, now)
    _HOLD_MEMO = (sig, idx)
    return idx


def federal_hold(case, here=None, index=None):
    """(held, why) for Call Mode and the knock planner.

    Miami-Dade leads that are not docket-clear (stay_unverified lis pendens included) stay
    callable unless this cache flags them. A Broward or Palm Beach lead is dropped once the
    cache file exists and the lead has no fresh clear. With DEALFLOW_CLERK_BK=1, a lead
    recorded as Broward or Palm Beach is also dropped unless it is a Broward civil number
    with a fresh full clerk read of no active stay, and an unreadable lead list drops every
    case that has no Miami-Dade stem. `here` is accepted so older callers
    keep working; the cache does not live beside the sale-history file. Pass `index` from
    federal_hold_index() so a queue does not re-read the file per row."""
    nc = _never_contact_hold(case)
    if nc:
        return nc
    if index is None:
        index = federal_hold_index()
    return index.hold(case)


def _never_contact_hold(case):
    """(True, why) for a case on stay_gate.NEVER_CONTACT, or when that list cannot be read;
    None otherwise. Every contact path holds these, whatever a docket or federal check says."""
    try:
        import stay_gate
        held = stay_gate.never_contact(case)
    except Exception as e:
        return True, 'never-contact list unreadable (%s) -- lead stays held' % str(e)[:80]
    if held:
        return True, 'contacted during its bankruptcy -- never contacted again'
    return None


def send_hold(case, here=None):
    """(held, why) for stamping saleBkAct and for the letter queue.

    Stricter than federal_hold: a keyable non-stem lead is held even before the cache file
    exists, because email and letters are sends. Miami is held only when CourtListener
    blocks. A missing docket clear is not, by itself, a federal hold."""
    nc = _never_contact_hold(case)
    if nc:
        return nc
    try:
        import stay_gate
    except Exception:
        return True, 'federal bankruptcy check unavailable — lead stays held'
    if not stay_gate.pacer_key(case):
        return False, ''
    try:
        root = here or os.path.dirname(os.path.abspath(stay_gate.__file__))
        v = stay_gate.check(case, os.path.join(root, 'sale_history_cache.json'))
    except Exception:
        if stay_gate.case_stem(case):
            return False, ''
        return True, 'federal bankruptcy check unavailable — lead stays held'
    if v.get('ok'):
        return False, ''
    if v.get('src') == 'courtlistener' or not stay_gate.case_stem(case):
        return True, v.get('why') or 'federal bankruptcy check has not run for this lead'
    return False, ''


def flags_for_cases(cases, now=None):
    """{pacer_key: {hold, why, hard}} for the board bake. Counts and case numbers only.

    With DEALFLOW_CLERK_BK=1, a lead recorded as Broward or Palm Beach is a hold unless it
    is a Broward civil number with a fresh full clerk read of no active stay. An unreadable
    lead list holds every case that has no Miami-Dade stem."""
    try:
        import stay_gate
    except Exception:
        return {}
    now = time.time() if now is None else now
    data, exists = _load(cache_path())
    if exists and not isinstance(data, dict):
        data, exists = {}, True
    overrides = load_overrides() if exists else {}
    out = {}
    for raw in cases or []:
        key = stay_gate.pacer_key(raw)
        if not key:
            continue
        ent = data.get(key) if isinstance(data, dict) else None
        if isinstance(ent, dict):
            op = entry_opinion(key, ent, overrides, now)
            # A stale clear is not a pass. Broward and Palm Beach stay held until a fresh
            # search says no open match. Miami-Dade is not held for a stale or missing check.
            if op and not stay_gate.case_stem(key) and not op.get('ok'):
                op = _non_stem_opinion(key, op, now)
        elif not stay_gate.case_stem(key) and not pacer_confirmed(key, now):
            op = {'blocks': True, 'code': 'stay_unverified',
                  'why': 'federal bankruptcy check has not run for this lead'}
        else:
            op = None
        if op and op.get('blocks'):
            out[key] = {'hold': True, 'why': str(op.get('why') or '')[:180],
                        'hard': op.get('code') == 'stay_active'}
        try:
            import clerk_bk
            cop = clerk_bk.gate_opinion(key, now)
        except Exception:
            cop = ({'blocks': True, 'code': 'stay_unverified',
                    'why': 'clerk docket check failed. Lead stays held.'}
                   if str(os.environ.get('DEALFLOW_CLERK_BK') or '').strip() == '1' else None)
        if isinstance(cop, dict) and cop.get('blocks'):
            if cop.get('code') == 'stay_active' or key not in out:
                out[key] = {'hold': True, 'why': str(cop.get('why') or '')[:180],
                            'hard': cop.get('code') == 'stay_active'}
    return out


def health_counts():
    data, exists = _load(cache_path())
    if not exists or not isinstance(data, dict):
        return {'ok': False, 'cases': 0, 'holds': 0, 'clear': 0}
    holds = clear = 0
    overrides = load_overrides()
    now = time.time()
    for k, ent in data.items():
        if str(k).startswith('_') or not isinstance(ent, dict):
            continue
        op = entry_opinion(k, ent, overrides, now)
        if not op:
            continue
        if op.get('blocks'):
            holds += 1
        elif op.get('ok'):
            clear += 1
    return {'ok': True, 'cases': holds + clear, 'holds': holds, 'clear': clear}


# --------------------------------------------------------------------------------------------------
# leads and searches
# --------------------------------------------------------------------------------------------------
def _day(value):
    s = str(value or '').strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _read_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _note_contact(ld, row):
    """Remember how soon this lead is contacted, and by which channel. Soonest days win."""
    raw_days = row.get('days')
    if raw_days is None:
        raw_days = row.get('days_to_auction')
    try:
        days = int(float(raw_days))
    except (TypeError, ValueError):
        days = None
    if days is not None and days >= 0:
        prev = ld.get('days')
        if prev is None or days < prev:
            ld['days'] = days
    emails = row.get('emails') if isinstance(row.get('emails'), list) else []
    if str(row.get('email') or '').strip() or any(str(e or '').strip() for e in emails):
        ld['email'] = True
    phones = row.get('phones') if isinstance(row.get('phones'), list) else []
    if str(row.get('phone') or '').strip() or any(str(p or '').strip() for p in phones):
        ld['phone'] = True
    if str(row.get('addr') or row.get('Address') or row.get('mail') or '').strip():
        ld['mail'] = True
    place = _place_token(row)
    if place and not ld.get('place'):
        ld['place'] = place


def _place_token(row):
    """ZIP if the row has one, otherwise a city token. Stored on the lead only.
    CourtListener `party:` is names, so this is never added to a search query."""
    for key in ('zip', 'Zip', 'postal'):
        z = re.search(r'\b(\d{5})\b', str(row.get(key) or ''))
        if z:
            return z.group(1)
    blob = ' '.join(str(row.get(k) or '') for k in ('addr', 'Address', 'mail', 'city'))
    z = re.search(r'\b(\d{5})\b', blob)
    if z:
        return z.group(1)
    addr = str(row.get('addr') or row.get('Address') or row.get('mail') or '')
    parts = [p.strip() for p in addr.split(',') if p.strip()]
    if len(parts) >= 2:
        tail = parts[-1]
        city = parts[-2] if re.search(r'\b[A-Z]{2}\b', tail, re.I) else parts[-1]
        city = re.sub(r'\d', '', city)
        city = re.sub(r'[^A-Za-z ]', '', city).strip()
        if len(city) >= 3:
            return city.upper()
    return ''


def party_page_cap(ld, cli=False):
    """CLI and leads next to be contacted may follow more pages. Everyone else stays at 2."""
    if cli:
        return CLI_PAGE_CAP
    if ld.get('email') or ld.get('phone'):
        return PRIORITY_PAGE_CAP
    return PARTY_PAGE_CAP


def contact_rank(ld):
    """Sort key. Lower is sooner. Email, a phone, and a letter address pull a lead forward.
    Miami sits in the same list as every other county."""
    try:
        days = int(ld.get('days'))
    except (TypeError, ValueError):
        days = 9999
    if days < 0:
        days = 9999
    boost = 0
    if ld.get('email'):
        boost -= 3000
    if ld.get('phone'):
        boost -= 2000
    if ld.get('mail'):
        boost -= 1000
    return (days + boost, str(ld.get('key') or ''))


def lead_paths(here=HERE):
    """The files load_leads reads, in that order.

    leads_final.json and lp_leads.json are always listed, even when they are missing, so a
    county map keyed on mtimes notices when one appears. The middle paths are the county
    *_leads.json files this function already keeps."""
    paths = [os.path.join(here, 'leads_final.json')]
    skip = ('leads_final.json', 'leads_raw.json', 'lp_leads.json', 'balloon_leads.json')
    for f in sorted(glob.glob(os.path.join(here, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in skip or bn.startswith('_'):
            continue
        paths.append(f)
    paths.append(os.path.join(here, 'lp_leads.json'))
    return paths


def load_leads(here=HERE):
    import stay_gate
    leads = {}

    def add(case, county, owner, order, row=None):
        key = stay_gate.pacer_key(case)
        if not key:
            return
        ld = leads.get(key)
        if ld is None:
            ld = leads[key] = {'key': key, 'case': str(case).strip(), 'county': str(county or '').upper(),
                               'owners_raw': []}
        raw = str(owner or '').strip()
        pair = (raw, order)
        if raw and pair not in ld['owners_raw']:
            ld['owners_raw'].append(pair)
        if county and not ld['county']:
            ld['county'] = str(county).upper()
        _note_contact(ld, row or {})

    paths = lead_paths(here)
    rows = _read_json(paths[0])
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict):
            add(r.get('Case #') or r.get('case'), 'MIAMI-DADE', r.get('owners') or r.get('owner_clean'),
                'first_last', r)
    for f in paths[1:-1]:
        rows = _read_json(f)
        for d in rows if isinstance(rows, list) else []:
            if isinstance(d, dict) and d.get('st') != 'BAL':
                add(d.get('case'), d.get('county'), d.get('owners'), 'last_first', d)
    lp = _read_json(paths[-1])
    for d in lp if isinstance(lp, list) else []:
        if isinstance(d, dict) and not d.get('lpDismissed'):
            add(d.get('case'), d.get('county') or 'MIAMI-DADE', d.get('owners'), 'last_first', d)
    return leads


def lead_owners(ld):
    owners = []
    for raw, order in (ld.get('owners_raw') or []):
        owners.extend(owners_from(raw, order))
    return owners


def _stamp():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def load_cache():
    data, exists = _load(cache_path())
    if not exists or not isinstance(data, dict):
        return {}
    return data


def save_cache(data):
    global _HOLD_MEMO
    _dump(cache_path(), data)
    _HOLD_MEMO = None


def put_entry(cache, key, cases, searched, ok_check, err_why, now):
    prev = cache.get(key) if isinstance(cache.get(key), dict) else {}
    merged = merge_cases(prev.get('cases') or [], cases)
    if not ok_check:
        # A failed search must not wipe a prior match, and must not become a clear.
        merged = merge_cases(prev.get('cases') or [], cases)
        searched_flag = bool(prev.get('searched'))
        ok_flag = False
    else:
        searched_flag = bool(searched or prev.get('searched'))
        ok_flag = True
    verdict, why = verdict_of(merged, searched_flag, ok_flag, '' if ok_flag else err_why)
    if not ok_flag and err_why and verdict not in ('active', 'possible'):
        why = err_why
        verdict = 'unavailable'
    exact = [c for c in merged if c.get('open') and c.get('match') == 'exact']
    cache[key] = {
        'verdict': verdict,
        'why': why[:200],
        'env': 'prod',
        'src': 'courtlistener',
        't': round(float(now), 1),
        'q': _stamp(),
        'searched': searched_flag if ok_flag else bool(prev.get('searched')),
        'ok_check': bool(ok_flag and searched_flag and verdict != 'unavailable'),
        'err': '' if ok_flag else err_why[:160],
        'a': bool(exact),
        'bd': max((str(c.get('filed') or '') for c in exact), default=''),
        'cases': merged[:12],
    }
    _relabel_clear(cache[key], key)
    return cache[key]


def _has_miami_stem(key):
    try:
        import stay_gate
        return bool(stay_gate.case_stem(key))
    except Exception:
        return False


def _relabel_clear(entry, key):
    """A finished CourtListener clear is not a release for Broward or Palm Beach."""
    if not isinstance(entry, dict) or entry.get('verdict') != 'clear':
        return entry
    if _has_miami_stem(key) or cl_clear_allowed():
        return entry
    entry['verdict'] = VERDICT_CLEAR_UNCONFIRMED
    entry['why'] = CLEAR_UNCONFIRMED_WHY
    return entry


def miami_docket_bd(case, here):
    """Bankruptcy filing date from an ACTIVE Miami docket stay, or '' otherwise.

    A lifted or closed entry (`entry_stay_active` false) does not count, even when it
    still carries a `bd`. Using that date marked a clear lead `docket_bk_unconfirmed`."""
    if not here:
        return ''
    try:
        import stay_gate
    except Exception:
        return ''
    if not stay_gate.case_stem(case):
        return ''
    path = os.path.join(here, stay_gate.CACHE_NAME)
    if not os.path.isfile(path):
        return ''
    idx, err = stay_gate._load(path)
    if err or not idx:
        return ''
    stem = stay_gate.case_stem(case)
    for _k, v in idx.get(stem) or []:
        if not isinstance(v, dict) or not stay_gate.entry_stay_active(v):
            continue
        bd = str(v.get('bd') or '').strip()[:10]
        if bd and _day(bd):
            return bd
    return ''


def _docket_windows(bd):
    """(start, end) inclusive slices: the docket day, then the three days on each side."""
    day = _day(bd)
    if not day:
        return []
    return [
        (day, day),
        (day - dt.timedelta(days=DOCKET_WINDOW_DAYS), day - dt.timedelta(days=1)),
        (day + dt.timedelta(days=1), day + dt.timedelta(days=DOCKET_WINDOW_DAYS)),
    ]


def _scan_docket_window(provider, transport, budget, clock, start, end, owners, county, place,
                        max_wait, deadline):
    """(rows, matched, truncated). Stops early once an open name match is in hand."""
    url = provider.filings_window_request(DOCKET_COURTS, start.isoformat(), end.isoformat())
    rows = []
    seen = 0
    while url and seen < DOCKET_PAGE_CAP:
        if seen and not _same_host(url):
            break
        _status, payload = http_get(url, provider, transport, budget, clock, reserve=0,
                                    max_wait=max_wait, deadline=deadline)
        page = provider.parse_search(payload)
        rows.extend(page['results'])
        seen += 1
        if _open_match(cases_from_hits(owners, page['results'], county=county, place=place)):
            return rows, True, False
        url = page.get('next') or ''
        if url and seen >= DOCKET_PAGE_CAP:
            return rows, False, True
    return rows, False, False


def _florida_exact(cases):
    fl = set(FL_BK_COURT_IDS)
    return any(c.get('open') and c.get('match') == 'exact' and str(c.get('court') or '') in fl
               for c in (cases or []))


def _confirm_docket(cache, entry, ld, owners, here, provider, transport, budget, clock,
                    max_wait, deadline):
    """When the Miami docket stay is active, look at one page of FLSB/FLMB filings that day.

    A lifted or closed bankruptcy is not checked and cannot add a hold. A miss does not
    add one either: the docket stay is already the hold, and the stay-check verdict stays
    whatever the docket says unless this page contains an open name match. One window and
    one page, so the 125/day budget still belongs to the party search."""
    if not isinstance(entry, dict):
        return entry
    if entry.get('verdict') not in ('clear', VERDICT_CLEAR_UNCONFIRMED):
        # Already a hold (or not a finished search). Another page cannot change that.
        return entry
    bd = miami_docket_bd(ld.get('case') or ld.get('key'), here)
    if not bd or _florida_exact(entry.get('cases')):
        return entry
    windows = _docket_windows(bd)[:1]
    if not windows:
        return entry
    start, end = windows[0]
    try:
        rows, matched, _cut = _scan_docket_window(
            provider, transport, budget, clock, start, end, owners,
            ld.get('county') or '', ld.get('place') or '', max_wait, deadline)
    except (TimeBudget, BudgetExhausted, RateLimited, ProviderError):
        # The party-search result stands. Do not invent a hold the docket did not ask for.
        return entry
    if not matched or not rows:
        return entry
    more = cases_from_hits(owners, rows, county=ld.get('county') or '', place=ld.get('place') or '')
    return put_entry(cache, ld['key'], more, True, True, '', clock.time())


def public_status():
    """Counts-only slice pipeline_alerts publishes. No names, no case numbers, no token."""
    data, exists = _load(status_path())
    if not exists or not isinstance(data, dict):
        return {'readable': False}
    try:
        pull_t = float(data.get('pull_t') or 0)
    except (TypeError, ValueError):
        pull_t = 0.0
    age_h = None
    if pull_t > 0:
        age_h = round((time.time() - pull_t) / 3600.0, 2)
    def num(k):
        try:
            return int(data.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    return {
        'readable': True,
        'pull_ok': bool(data.get('pull_ok')),
        'pull_age_h': age_h,
        'filings': num('filings'),
        'leads_checked': num('leads_checked'),
        'holds': num('holds'),
        'errors': num('errors'),
        'requests_used': num('requests_used'),
        'truncated': num('truncated'),
        'provider': 'courtlistener' if data.get('provider') in (None, '', 'courtlistener') else 'other',
        'reason': re.sub(r'[^a-z0-9_:-]', '', str(data.get('reason') or '').lower())[:40],
    }


def write_status(**fields):
    cur, exists = _load(status_path())
    if not isinstance(cur, dict):
        cur = {}
    # Drop anything that is not a count, a bool, or a short safe label.
    allowed = ('pull_ok', 'pull_t', 'pull_ts', 'filings', 'leads_checked', 'holds', 'errors',
               'requests_used', 'requests_minute', 'requests_hour', 'requests_day', 'truncated',
               'provider', 'reason')
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == 'reason':
            v = re.sub(r'[^a-z0-9_:-]', '', str(v or '').lower())[:40]
        elif k == 'provider':
            v = 'courtlistener' if v == 'courtlistener' else 'other'
        elif k == 'pull_ts':
            v = str(v)[:40]
        elif k == 'pull_ok':
            v = bool(v)
        elif k == 'pull_t':
            v = round(float(v), 1)
        else:
            v = int(v)
        cur[k] = v
    _dump(status_path(), cur)
    return cur


def _budget_fields(budget, now):
    c = budget.counts(now)
    return {'requests_used': c['day'], 'requests_minute': c['minute'],
            'requests_hour': c['hour'], 'requests_day': c['day']}


def load_filings():
    data, exists = _load(filings_path())
    if not exists or not isinstance(data, dict):
        return {}
    return data


def save_filings(rows, now):
    """rows are normalized hits WITH parties. Stored only under DEALFLOW_DIR."""
    cur = load_filings()
    items = cur.get('items') if isinstance(cur.get('items'), dict) else {}
    for row in rows:
        k = case_no_key(row.get('no')) + '|' + str(row.get('court_id') or '')
        if not row.get('no'):
            continue
        items[k] = {
            'no': row.get('no'),
            'court_id': row.get('court_id') or '',
            'filed': row.get('filed') or '',
            'terminated': row.get('terminated') or '',
            'open': bool(row.get('open')),
            'parties': [str(p)[:120] for p in (row.get('parties') or [])[:8]],
        }
    cutoff = (dt.date.today() - dt.timedelta(days=FILING_KEEP_DAYS)).isoformat()
    kept = {k: v for k, v in items.items()
            if isinstance(v, dict) and str(v.get('filed') or '9999') >= cutoff}
    _dump(filings_path(), {'updated': round(float(now), 1), 'items': kept})
    return kept


def match_filings_to_leads(leads, items, cache, now):
    """Local match. Open filing + name match writes a hold. Does not clear a lead by itself."""
    holds = 0
    for ld in leads.values():
        owners = lead_owners(ld)
        if not owners:
            continue
        found = []
        for item in items.values():
            if not isinstance(item, dict):
                continue
            found.extend(cases_from_hits(owners, [item], county=ld.get('county') or '',
                                     place=ld.get('place') or ''))
        if not found:
            continue
        entry = put_entry(cache, ld['key'], found, searched=False, ok_check=True, err_why='', now=now)
        # Index evidence alone is not a completed party search, so it cannot clear.
        if entry['verdict'] == 'clear':
            entry['verdict'] = 'unavailable'
            entry['ok_check'] = False
            entry['searched'] = False
            entry['why'] = 'federal bankruptcy check has not run for this lead'
        if entry['verdict'] in ('active', 'possible'):
            holds += 1
    return holds


def _collect_queries(queries, provider, transport, budget, clock, page_cap, max_wait, deadline,
                     filed_after=None, courts=None):
    """(rows, truncated). Stops paging a query once it overflows; the caller may narrow."""
    collected = []
    truncated = False
    for q in queries:
        url = provider.party_request(q, filed_after=filed_after, courts=courts)
        rows, cut = paginate(url, provider, transport, budget, clock, page_cap, reserve=0,
                             max_wait=max_wait, deadline=deadline)
        collected.extend(rows)
        if cut:
            truncated = True
    return collected, truncated


def _open_match(cases):
    return any(c.get('open') and c.get('match') in ('exact', 'plausible') and c.get('no')
               for c in (cases or []))


def _store_search(cache, key, owners, rows, clock, conclusive, narrow_overflow=False,
                  county='', place=''):
    """Combine every hit seen. A match holds. A clear requires a finished eligible search.
    Anything else stays truncated and held, including a narrow pass that overflowed or
    could not be built."""
    cases = cases_from_hits(owners, rows, county=county, place=place)
    matched = _open_match(cases)
    if conclusive:
        return put_entry(cache, key, cases, True, True, '', clock.time())
    if matched and not narrow_overflow:
        return put_entry(cache, key, cases, True, True, '', clock.time())
    return put_entry(cache, key, cases, False, False, TRUNC_WHY, clock.time())


def search_lead(ld, provider, transport, budget, clock, cache, max_wait=None, deadline=None,
                page_cap=None, here=None):
    """One lead's party search. Fail closed on any error.

    The eligible search is every bankruptcy court and the configured filed-after window.
    It can clear only when that search finishes inside the page cap. If it overflows, the
    same name query is run again for the Florida bankruptcy courts over a shorter window,
    with more pages. Hits from both passes are kept. A match in either holds the lead.
    The narrower pass cannot clear. An active Miami docket stay is checked against one
    page of FLSB/FLMB filings on that date. A miss does not add a hold. A lifted or
    closed docket bankruptcy is not checked.
    TimeBudget propagates so a cut-off search is not stored as a clear."""
    owners = lead_owners(ld)
    if not owners:
        put_entry(cache, ld['key'], [], False, False, 'no owner name on the lead — check cannot run', clock.time())
        return cache[ld['key']]
    queries = []
    for owner in owners[:4]:
        q = query_for(owner)
        if q and q not in queries:
            queries.append(q)
    if not queries or len(owners) > 4:
        put_entry(cache, ld['key'], [], False, False,
                  'owner name cannot be searched — lead stays held', clock.time())
        return cache[ld['key']]
    cap = PARTY_PAGE_CAP if page_cap is None else int(page_cap)
    env = getattr(provider, 'env', None)
    eligible = default_filed_after(env)
    county = ld.get('county') or ''
    place = ld.get('place') or ''
    collected = []

    def finish(entry):
        return _confirm_docket(cache, entry, ld, owners, here, provider, transport, budget, clock,
                               max_wait, deadline)

    try:
        wide, wide_cut = _collect_queries(
            queries, provider, transport, budget, clock, cap, max_wait, deadline,
            filed_after=eligible, courts=BK_COURT_IDS)
        collected.extend(wide)
        if not wide_cut and scope_can_clear(BK_COURT_IDS, eligible, env):
            return finish(_store_search(cache, ld['key'], owners, collected, clock, True,
                                        county=county, place=place))
        bounds = narrow_bounds(env)
        if not bounds:
            return finish(_store_search(cache, ld['key'], owners, collected, clock, False,
                                        narrow_overflow=True, county=county, place=place))
        n_courts, n_filed = bounds
        # The send bridge passes a few-second max_wait. Extra pages there would spend
        # the minute budget and come back as a rate hold. Nightly and the CLI, which
        # pass no max_wait, follow the longer narrow cap.
        ncap = narrow_page_cap(cap) if max_wait is None else cap
        narrow_rows, narrow_cut = _collect_queries(
            queries, provider, transport, budget, clock, ncap, max_wait, deadline,
            filed_after=n_filed, courts=n_courts)
        collected.extend(narrow_rows)
        # The narrow scope is not the full window. Finishing it cannot clear.
        can_clear = (not narrow_cut) and scope_can_clear(n_courts, n_filed, env)
        return finish(_store_search(cache, ld['key'], owners, collected, clock, can_clear,
                                    narrow_overflow=bool(narrow_cut), county=county, place=place))
    except TimeBudget:
        raise
    except BudgetExhausted as e:
        entry = put_entry(cache, ld['key'], cases_from_hits(owners, collected, county=county, place=place),
                          False, False, str(e), clock.time())
        return finish(entry)
    except (RateLimited, ProviderError) as e:
        entry = put_entry(cache, ld['key'], cases_from_hits(owners, collected, county=county, place=place),
                          False, False, str(e), clock.time())
        return finish(entry)


def _needs_party_search(ent, now):
    if not isinstance(ent, dict):
        return True
    if ent.get('err'):
        try:
            age = (float(now) - float(ent.get('t') or 0)) / 86400.0
        except (TypeError, ValueError):
            return True
        return age >= 1.0
    if not ent.get('searched') or not ent.get('ok_check'):
        return True
    try:
        age = (float(now) - float(ent.get('t') or 0)) / 86400.0
    except (TypeError, ValueError):
        return True
    limit = max_age_days()
    return limit <= 0 or age < -1 or age > limit


def _pull_caught_up(state):
    """True when the flsb cursor is empty and today's window already completed."""
    if not isinstance(state, dict):
        return False
    if str(state.get('cursor') or '').strip():
        return False
    return str(state.get('last_success_date') or '')[:10] == dt.date.today().isoformat()


def pull_filings(provider, transport, budget, clock, deadline=None, max_wait=None):
    """Pull flsb filings. The next-page cursor is saved after every page so a cut-off
    run resumes instead of restarting the lookback. Returns (items, caught_up, n_rows)."""
    state, _exists = _load(pull_state_path())
    if not isinstance(state, dict):
        state = {}
    cursor = str(state.get('cursor') or '').strip()
    if cursor and not _same_host(cursor):
        cursor = ''
        state['cursor'] = ''
    if not cursor and _pull_caught_up(state):
        items = load_filings().get('items') or {}
        return (items if isinstance(items, dict) else {}), True, 0
    if cursor:
        url = cursor
    else:
        last = str(state.get('last_success_date') or '')[:10]
        try:
            start = dt.date.fromisoformat(last) - dt.timedelta(days=1)
        except ValueError:
            start = dt.date.today() - dt.timedelta(days=FIRST_PULL_DAYS)
        state['filed_after'] = start.isoformat()
        state['cursor'] = ''
        _dump(pull_state_path(), state)
        url = provider.filings_request(FLSB, start.isoformat())
    kept = {}
    n_rows = [0]

    def on_page(page_rows, nxt):
        n_rows[0] += len(page_rows or [])
        saved = save_filings(page_rows or [], clock.time())
        kept.clear()
        kept.update(saved)
        nxt = nxt if nxt and _same_host(nxt) else ''
        state['cursor'] = nxt
        state['updated'] = round(clock.time(), 1)
        if not nxt:
            state['last_success_date'] = dt.date.today().isoformat()
            state['last_success_t'] = round(clock.time(), 1)
        _dump(pull_state_path(), state)

    _rows, truncated = paginate(url, provider, transport, budget, clock, PULL_PAGE_CAP,
                                reserve=NIGHTLY_PRESEND_RESERVE, on_page=on_page,
                                max_wait=max_wait, deadline=deadline)
    if not kept:
        items = load_filings().get('items') or {}
        kept = items if isinstance(items, dict) else {}
    caught = (not truncated) and _pull_caught_up(state)
    return kept, caught, n_rows[0]


def run_nightly(here=HERE, env=None, transport=None, clock=None, provider=None, progress=None):
    """progress, when given, is a dict this run keeps current (stage, budget, filings, checked,
    errors, truncated) so main() can write real counters if the run dies part way."""
    prog = progress if isinstance(progress, dict) else {}
    prog['stage'] = 'start'
    env = os.environ if env is None else env
    provider = provider or get_provider(env=env)
    clock = clock or Clock()
    transport = transport or urllib_transport
    budget = load_budget(clock.time())
    prog['budget'] = budget
    deadline = clock.time() + max_runtime_s(env)
    ok_av, why = provider.available(env)
    leads = load_leads(here)
    cache = load_cache()
    errors = 0
    pull_ok = False
    n_rows = 0
    time_stopped = False
    reason = 'ok'
    items = {}
    if not ok_av:
        items = load_filings().get('items') or {}
        if not isinstance(items, dict):
            items = {}
        reason = 'provider_unavailable'
        if provider.name == PROVIDER_COURTLISTENER and not str(env.get(ENV_TOKEN) or '').strip():
            reason = 'token_missing'
        log('CourtListener: check unavailable (%s). Leads that need it stay held.' % reason)
    else:
        prog['stage'] = 'pull'
        try:
            items, pull_ok, n_rows = pull_filings(
                provider, transport, budget, clock, deadline=deadline)
            log('CourtListener: pulled %d filing row(s), cache %d.' % (
                n_rows, len(items) if isinstance(items, dict) else 0))
            if not pull_ok:
                reason = 'pull_truncated'
                log('CourtListener: pull paused before the last page. The next run resumes.')
        except TimeBudget:
            items = load_filings().get('items') or {}
            st, _exists = _load(pull_state_path())
            pull_ok = _pull_caught_up(st)
            reason = 'time_budget'
            time_stopped = True
            log('CourtListener: nightly run hit its time limit. Progress saved.')
        except BudgetExhausted:
            items = load_filings().get('items') or {}
            st, _exists = _load(pull_state_path())
            pull_ok = _pull_caught_up(st)
            reason = 'budget'
            errors += 1
            time_stopped = True
            log('CourtListener: nightly pull stopped, daily rate budget exhausted. Remaining leads stay held.')
        except (RateLimited, ProviderError):
            items = load_filings().get('items') or {}
            pull_ok = False
            reason = 'pull_failed'
            errors += 1
            log('CourtListener: nightly pull failed. Leads stay held.')
    if not isinstance(items, dict):
        items = {}
    prog.update(filings=len(items), pull_ok=bool(pull_ok), errors=errors, stage='match')
    match_filings_to_leads(leads, items, cache, clock.time())
    checked = 0
    truncated_n = 0
    net_errors = 0
    prog.update(checked=0, truncated=0, stage='search')
    if ok_av and not time_stopped and clock.time() < deadline:
        # Soonest contact first: first-touch email, then a phone, then a letter.
        # Miami is in this same order, not after every other county.
        order = sorted(leads.values(), key=contact_rank)
        for ld in order:
            if clock.time() >= deadline:
                reason = 'time_budget'
                log('CourtListener: nightly run hit its time limit. Progress saved.')
                break
            ent = cache.get(ld['key'])
            if not _needs_party_search(ent, clock.time()):
                continue
            if _slot_wait(budget, clock.time(), reserve=NIGHTLY_PRESEND_RESERVE) is None:
                if reason in ('ok', 'pull_truncated'):
                    reason = 'budget'
                log('CourtListener: left the rest of the new-lead searches for later (budget). They stay held.')
                break
            before = budget.counts(clock.time())['day']
            try:
                entry = search_lead(ld, provider, transport, budget, clock, cache, deadline=deadline,
                                    page_cap=party_page_cap(ld), here=here)
            except TimeBudget:
                reason = 'time_budget'
                log('CourtListener: nightly run hit its time limit. Progress saved.')
                break
            if budget.counts(clock.time())['day'] != before or entry.get('searched'):
                checked += 1
            err = entry.get('err') or ''
            if 'truncated' in err:
                # A common-name overflow is not an HTTP failure. It is counted on its own.
                truncated_n += 1
            elif err:
                errors += 1
                if 'network error' in err:
                    net_errors += 1
                if '429' in err or 'budget' in err:
                    if reason in ('ok', 'pull_truncated'):
                        reason = 'rate_limit' if '429' in err else 'budget'
                    prog.update(checked=checked, errors=errors, truncated=truncated_n)
                    break
                if net_errors >= NIGHTLY_NET_ERROR_LIMIT:
                    if reason in ('ok', 'pull_truncated'):
                        reason = 'network'
                    log('CourtListener: %d network failures, left the rest of the new-lead searches '
                        'for later. They stay held.' % net_errors)
                    prog.update(checked=checked, errors=errors, truncated=truncated_n)
                    break
            prog.update(checked=checked, errors=errors, truncated=truncated_n)
    prog['stage'] = 'save'
    save_cache(cache)
    fields = _budget_fields(budget, clock.time())
    prog['stage'] = 'holds'
    overrides = load_overrides()
    hold_n = 0
    for k, ent in cache.items():
        if isinstance(ent, dict):
            op = entry_opinion(k, ent, overrides, clock.time())
            if op and op.get('blocks'):
                hold_n += 1
    write_status(pull_ok=bool(pull_ok), pull_t=clock.time(), pull_ts=_stamp(),
                 filings=len(items) if isinstance(items, dict) else 0,
                 leads_checked=checked, holds=hold_n, errors=errors, truncated=truncated_n,
                 provider='courtlistener' if provider.name == PROVIDER_COURTLISTENER else 'other',
                 reason=reason, **fields)
    log('CourtListener: status filings=%d checked=%d holds=%d errors=%d truncated=%d requests=%d' % (
        len(items) if isinstance(items, dict) else 0, checked, hold_n, errors, truncated_n,
        fields['requests_used']))
    return {'pull_ok': bool(pull_ok), 'checked': checked, 'holds': hold_n, 'errors': errors,
            'truncated': truncated_n, 'reason': reason}


def presend_check(case, here=HERE, env=None, transport=None, clock=None, provider=None,
                  max_wait=PRESEND_MAX_WAIT, page_cap=None):
    """One lead, just before a send. Returns {status, why, verdict}. Does not raise.

    max_wait defaults to a few seconds so the send bridge never sleeps out a minute
    window. Pass None to pace until the search finishes (the CLI does that)."""
    env = os.environ if env is None else env
    provider = provider or get_provider(env=env)
    clock = clock or Clock()
    transport = transport or urllib_transport
    try:
        import stay_gate
        key = stay_gate.pacer_key(case)
    except Exception:
        return {'status': 'error', 'why': 'stay gate could not key this case', 'verdict': ''}
    if not key:
        return {'status': 'unsearchable', 'why': 'case number cannot be keyed', 'verdict': ''}
    cache = load_cache()
    ent = cache.get(key)
    if isinstance(ent, dict) and not _needs_party_search(ent, clock.time()):
        return {'status': 'cached', 'why': '', 'verdict': ent.get('verdict') or ''}
    ok_av, why = provider.available(env)
    if not ok_av:
        write_status(reason='token_missing' if 'token missing' in why else 'provider_unavailable',
                     provider=provider.name if provider.name == PROVIDER_COURTLISTENER else 'other')
        return {'status': 'unavailable', 'why': why, 'verdict': ''}
    leads = load_leads(here)
    ld = leads.get(key)
    if ld is None:
        ld = {'key': key, 'case': str(case), 'county': '', 'owners_raw': []}
        # No owner on file: do not clear.
        put_entry(cache, key, [], False, False, 'no owner name on the lead — check cannot run', clock.time())
        save_cache(cache)
        return {'status': 'unsearchable', 'why': cache[key]['why'], 'verdict': cache[key]['verdict']}
    budget = load_budget(clock.time())
    try:
        entry = search_lead(ld, provider, transport, budget, clock, cache, max_wait=max_wait,
                            page_cap=page_cap, here=here)
    except TimeBudget as e:
        entry = {'err': str(e), 'why': str(e), 'verdict': 'unavailable'}
    save_cache(cache)
    fields = _budget_fields(budget, clock.time())
    err = entry.get('err') or ''
    cur = public_status()
    extra = {}
    if 'truncated' in err:
        extra['truncated'] = int(cur.get('truncated') or 0) + 1
    elif err:
        extra['errors'] = int(cur.get('errors') or 0) + 1
    write_status(leads_checked=int(cur.get('leads_checked') or 0) + 1, **extra, **fields)
    if entry.get('err'):
        err = entry['err']
        if '429' in err:
            status = 'rate_limit'
        elif 'budget' in err:
            status = 'budget'
        else:
            status = 'error'
        return {'status': status, 'why': entry['why'], 'verdict': entry.get('verdict') or ''}
    return {'status': 'searched', 'why': entry.get('why') or '', 'verdict': entry.get('verdict') or ''}


def resolve_lead(case, here=HERE):
    """The lead row for a case number. A Miami stem without the -CA-01 suffix matches that lead.
    None when nothing on file matches. Does not write the cache."""
    import stay_gate
    leads = load_leads(here)
    raw = str(case or '').strip()
    key = stay_gate.pacer_key(raw)
    if key and key in leads:
        return leads[key]
    stem = stay_gate.case_stem(raw)
    if not stem:
        return None
    matches = [ld for ld in leads.values() if stay_gate.case_stem(ld.get('case') or '') == stem]
    if not matches:
        return None
    ca = [ld for ld in matches if str(ld.get('case') or '').upper().endswith('-CA-01')]
    if len(ca) == 1:
        return ca[0]
    if len(matches) == 1:
        return matches[0]
    return ca[0] if ca else matches[0]


def case_report(case):
    """One public line: flagged, match type, bankruptcy case number. No party names."""
    try:
        import stay_gate
        key = stay_gate.pacer_key(case)
    except Exception:
        key = ''
    ent = load_cache().get(key) if key else None
    cases = []
    if isinstance(ent, dict):
        cases = [c for c in (ent.get('cases') or [])
                 if isinstance(c, dict) and c.get('open') and c.get('no')
                 and c.get('match') in ('exact', 'plausible')]
    match = 'none'
    if any(c.get('match') == 'exact' for c in cases):
        match = 'exact'
    elif cases:
        match = 'plausible'
    flagged = 'yes' if match != 'none' else 'no'
    nos = []
    for c in cases:
        if c.get('match') == match and c.get('no') not in nos:
            nos.append(str(c.get('no'))[:40])
    bk = ','.join(nos[:3]) if nos else '-'
    return '%s flagged=%s match=%s bk=%s' % (str(case).strip()[:40], flagged, match, bk)


def _crash_site(exc):
    """'file.py:123 func' for the innermost frame of exc. No values, no message."""
    tb = getattr(exc, '__traceback__', None)
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return 'unknown'
    code = last.tb_frame.f_code
    return '%s:%d %s' % (os.path.basename(code.co_filename), last.tb_lineno, code.co_name)


def _crash_status(progress, exc):
    """Status fields for a nightly run that raised. The counters are this run's, not the last
    good run's: a crash that left yesterday's numbers in place read as a run that made no calls.
    pull_ok stays False so the bk-lookup alert still fires. holds is not recounted."""
    prog = progress if isinstance(progress, dict) else {}
    fields = {'pull_ok': False, 'pull_t': time.time(), 'pull_ts': _stamp(),
              'provider': 'courtlistener',
              'reason': 'crash:' + type(exc).__name__,
              'errors': int(prog.get('errors') or 0) + 1,
              'leads_checked': int(prog.get('checked') or 0),
              'truncated': int(prog.get('truncated') or 0)}
    if 'filings' in prog:
        fields['filings'] = int(prog.get('filings') or 0)
    else:
        items = load_filings().get('items') or {}
        fields['filings'] = len(items) if isinstance(items, dict) else 0
    budget = prog.get('budget')
    if budget is not None:
        try:
            fields.update(_budget_fields(budget, time.time()))
        except Exception:
            pass
    return fields


def main(argv=None, clock=None, transport=None, here=None):
    ap = argparse.ArgumentParser(description='Federal bankruptcy check (CourtListener)')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--case', action='append', default=[])
    args = ap.parse_args(argv)
    if args.status:
        st = public_status()
        log('CourtListener status: ' + json.dumps(st, sort_keys=True))
        return 0
    cases = [str(c).strip() for c in (args.case or []) if str(c or '').strip()]
    if cases:
        # Pace the whole search and follow more pages than the send bridge will.
        root = here or HERE
        for case in cases:
            ld = resolve_lead(case, root)
            if ld is None:
                log('no lead for this case')
                continue
            presend_check(ld.get('case') or case, here=root, max_wait=None, clock=clock,
                          transport=transport, page_cap=CLI_PAGE_CAP)
            log(case_report(ld.get('case') or case))
        return 0
    progress = {}
    try:
        run_nightly(here=here or HERE, clock=clock, transport=transport, progress=progress)
    except TimeBudget:
        log('CourtListener: nightly run hit its time limit. Progress saved.')
        try:
            st, _exists = _load(pull_state_path())
            write_status(pull_ok=_pull_caught_up(st), pull_t=time.time(), pull_ts=_stamp(),
                         reason='time_budget', provider='courtlistener')
        except Exception:
            pass
    except Exception as e:
        # The class, the stage and the line say where it died. The exception text does not go
        # out: it can carry an owner name from a lead row or a filing.
        stage = str(progress.get('stage') or 'start')
        log('CourtListener: nightly run failed during %s (%s at %s). Leads that need the check '
            'stay held.' % (stage, type(e).__name__, _crash_site(e)))
        try:
            write_status(**_crash_status(progress, e))
        except Exception:
            pass
    _maybe_clerk_docket()
    return 0


def _maybe_clerk_docket():
    """The 5:30 refresh already runs this file. The clerk docket check rides along only when
    DEALFLOW_CLERK_BK=1. A failure here does not change this process's exit code."""
    try:
        import clerk_bk
        if not clerk_bk.enabled():
            return
        clerk_bk.run_nightly(here=HERE)
    except Exception:
        log('Clerk docket check failed. Broward and Palm Beach stay held while that check is on.')


if __name__ == '__main__':
    sys.exit(main())
