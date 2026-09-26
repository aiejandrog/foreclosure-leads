#!/usr/bin/env python
"""or_daily_file.py — nightly ingest of the Miami-Dade Clerk's paid Official Records daily file.

WHAT IT IS
The Clerk's Commercial Data Services "Records" folder drops one zip a night,
`dly_records_MMDDYYYY.zip`, around 9:30 PM ET. Inside is one caret-delimited
`.exp`: a 20-digit control line, then one row per party per recorded document
(42 fields; layout in the Clerk's FTP_Layout_Official_Records.pdf, February 2026).
This module downloads the files that are actually listed, loads them into a
SQLite database, and matches the recordings to the Miami-Dade foreclosure cases
and parcels this pipeline already tracks.

REPORT ONLY
Matched liens, judgments, lis pendens, satisfactions/releases and mortgages are
written as evidence under ~/DEALFLOW/or_daily (outside this repo, outside
OneDrive). Nothing here is read by send, callable, or hold. A match never
changes a lead, a gate, or a board.

HOW A RUN GOES
  1. Off unless DEALFLOW_OR_DAILY=1 (or --enable). Prints one line and exits 0.
  2. Credentials come only from CLERKDEV_USERNAME and CLERKDEV_PASSWORD. They
     are never printed, logged, or written. This is the CDS developers login,
     not the CLERK_USERNAME login clerk_session.py uses for OCS.
  3. GET the login page, POST Email + Password. No captcha, no antiforgery
     token. "Sign out" in the response means the session is in.
  4. Read the units balance on MyAccount. Read it again after listing and
     after every download. If it drops, or it cannot be read, stop. No further
     request is made.
  5. GET /Developers/FTP and take only `dly_records_MMDDYYYY` rows whose link
     is /Developers/FTP/FTP/DownloadFile/<digits>. The id is copied out and the
     URL is rebuilt; the raw href is never requested. The first page shows the
     5 newest files until someone clicks "View More". That endpoint was not
     part of the read-only probe, so this module does not call it. A night
     that runs gets that night's file; a first run backfills whatever is
     listed, not the older files hidden behind View More.
  6. Load each zip into SQLite. The 13-digit Key is the upsert identity.
     I inserts, U updates, D marks the row deleted. Applying the same file
     again changes nothing.
  7. Match, write evidence + a short markdown skim + status.json (counts and
     dates only, no party names in the status file).

FAIL SOFT
Site down, login rejected, subscription lapsed, balance unreadable: log a gap,
write status.json, exit 0. refresh-dealflow.bat keeps going. The units-drop
stop is the same shape — the ingest aborts, the refresh does not.

EXPIRY
The FTP page's "Expiration Date" / "Days Left" wins. If the page has neither,
OR_DAILY_EXPIRES or the purchase date 2026-10-26 is the fallback. Five days
out, the log line and healthcheck both warn. The warning does not block a publish.

NEVER REQUESTED
Purchase, extend, add-units, basket, AddToCart, ExtendPurchase, DownloadDirectory.
The client has an allowlist. Anything else, including a redirect, is refused
before it is sent.

    python or_daily_file.py            # no-op unless DEALFLOW_OR_DAILY=1
    python or_daily_file.py --plan     # no network; where data would go
    python or_daily_file.py --enable   # one manual run without the env flag
"""
import argparse
import datetime as dt
import glob
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import zipfile

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = 'https://www2.miamidadeclerk.gov'
LOGIN_URL = HOST + '/Developers/Account/Login'
ACCOUNT_URL = HOST + '/Developers/Home/MyAccount'
FTP_URL = HOST + '/Developers/FTP'
DOWNLOAD_URL = HOST + '/Developers/FTP/FTP/DownloadFile/'

ENV_ENABLE = 'DEALFLOW_OR_DAILY'
ENV_USER = 'CLERKDEV_USERNAME'
ENV_PASS = 'CLERKDEV_PASSWORD'
ENV_EXPIRES = 'OR_DAILY_EXPIRES'
# The month Alejandro bought. The FTP page's own date replaces this when it parses.
DEFAULT_EXPIRES = '2026-10-26'
WARN_DAYS = 5
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
MAX_ZIP_BYTES = 80 * 1024 * 1024

# PDF field order, 0-based. A line that does not split into exactly these 42
# fields is skipped and counted — a caret inside a legal description was not
# present in the 2026-09-25 file, and this parser does not try to repair one.
FIELDS = (
    'cfn_year', 'cfn_seq', 'group_id', 'rec_date_raw', 'rec_time',
    'book', 'page', 'book_type', 'doc_pages', 'append_pages',
    'doc_type', 'doc_desc', 'doc_date_raw', 'party_name', 'party_code',
    'cross_party', 'orig_cfn_year', 'orig_cfn_seq', 'orig_book', 'orig_page',
    'orig_misc', 'subdivision', 'folio', 'legal', 'section',
    'township', 'range_val', 'plat_book', 'plat_page', 'block',
    'case_number', 'consideration_1', 'consideration_2', 'deed_doc_tax',
    'single_family', 'surtax', 'intangible', 'doc_stamps', 'key',
    'txn', 'party_seq', 'modified_raw',
)
assert len(FIELDS) == 42

# Codes verified against the Clerk layout (MOR, and the codes counted on the
# 2026-09-25 file). SJU is NOT in this map: the probe counted 10 of them and
# the layout PDF does not define the code, so SJU counts only when its
# description itself says JUDGMENT. AMO (assignment) is deliberately not a mortgage.
CODE_CATEGORY = {
    'REL': 'release', 'SAT': 'release',
    'LIS': 'lis_pendens',
    'JUD': 'judgment',
    'LIE': 'lien',
    'MOR': 'mortgage',
}
# Seen on the sample day, and not one of the five evidence categories.
OTHER_CODES = {
    'DCP', 'DEE', 'AFF', 'SMO', 'NCO', 'CCP', 'NOT', 'QCD', 'AGR', 'AMO',
    'FST', 'ORD', 'BAN', 'ASM',
}
SURFACE = ('lien', 'judgment', 'lis_pendens', 'release', 'mortgage')
CASE_RE = re.compile(r'(\d{4}-\d{6}-[A-Za-z]{2}-\d{2})')
NAME_RE = re.compile(r'[A-Z0-9]+')
SUFFIXES = {'JR', 'SR', 'II', 'III', 'IV', 'ESQ'}
MIAMI_CASE_RE = re.compile(r'^\d{4}-\d{6}-(?:CA|CC)-\d{2}$')
FILE_RE = re.compile(r'^dly_records_(\d{8})$', re.I)

# Allowlist is the real gate. BLOCKED_SEGMENTS is the second one, so a later
# edit that widens the allowlist still cannot request a purchase path.
ALLOWED = [re.compile(p) for p in (
    r'^https://www2\.miamidadeclerk\.gov/Developers/Account/Login$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/?$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/Home/?$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/Home/Index$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/Home/MyAccount$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/FTP$',
    r'^https://www2\.miamidadeclerk\.gov/Developers/FTP/FTP/DownloadFile/[0-9]+$',
)]
BLOCKED_SEGMENTS = {
    'addtocart', 'extendpurchase', 'downloaddirectory', 'addtobasket',
    'basket', 'addunits', 'add-units', 'buyunits', 'buy', 'extend',
    'purchase', 'checkout', 'payment', 'addunit',
}
LAPSED_RE = re.compile(
    r'subscription has expired|subscription expired|access has expired|'
    r'access has lapsed|purchase has expired|this folder has expired|'
    r'no active subscription', re.I)
BALANCE_RE = re.compile(r'Balance.{0,200}?(\d+)\s+units', re.I | re.S)
EXPIRY_RE = re.compile(
    r'Expiration Date:\s*([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}|[A-Za-z]+\s+\d{1,2},\s+\d{4})',
    re.I)
DAYS_RE = re.compile(r'Days Left:\s*(\d+)', re.I)
TR_RE = re.compile(r'(?is)<tr\b[^>]*>.*?</tr>')
FILE_IN_ROW_RE = re.compile(r'dly_records_(\d{8})', re.I)
ID_IN_ROW_RE = re.compile(r'/Developers/FTP/FTP/DownloadFile/(\d+)', re.I)


class Gap(Exception):
    """The ingest cannot finish. The refresh continues."""


class SiteDown(Gap):
    pass


class LoginFailed(Gap):
    pass


class Denied(Gap):
    pass


def enabled(env):
    return str(env.get(ENV_ENABLE) or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _secrets(env):
    out = []
    for key in (ENV_USER, ENV_PASS):
        value = (env.get(key) or '').strip()
        if len(value) >= 3:
            out.append(value)
            out.append(urllib.parse.quote(value))
            out.append(urllib.parse.quote_plus(value))
    return sorted(set(out), key=len, reverse=True)


def scrub(text, env):
    s = str(text)
    for secret in _secrets(env):
        s = s.replace(secret, '***')
    return s


def say(msg, env):
    print(scrub(msg, env), flush=True)


def assert_allowed(url):
    """Refuse anything that is not a login, an account read, the FTP list, or one file download."""
    if not url or not url.startswith('https://www2.miamidadeclerk.gov/'):
        raise Denied('refused a non-download URL')
    parts = urllib.parse.urlsplit(url)
    if parts.query or parts.fragment:
        raise Denied('refused a non-download URL')
    segments = [s for s in parts.path.split('/') if s]
    for seg in segments:
        if seg.lower() in BLOCKED_SEGMENTS:
            raise Denied('refused a non-download URL')
    if not any(p.match(url) for p in ALLOWED):
        raise Denied('refused a non-download URL')
    return url


def download_url(file_id):
    """Rebuild the download URL from the numeric id. The page's href is never used."""
    if not re.fullmatch(r'\d+', str(file_id or '')):
        raise Denied('refused a non-download URL')
    return assert_allowed(DOWNLOAD_URL + str(file_id))


# ---------------------------------------------------------------------------------------------
# Dates, money, names, categories
# ---------------------------------------------------------------------------------------------
def parse_mmddyyyy(s):
    raw = (s or '').strip()
    if not re.fullmatch(r'\d{8}', raw) or raw == '00000000':
        return ''
    month, day, year = int(raw[0:2]), int(raw[2:4]), int(raw[4:8])
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return ''


def parse_long_date(s):
    text = (s or '').strip()
    for fmt in ('%A, %B %d, %Y', '%B %d, %Y', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_loose_date(s):
    text = (s or '').strip()
    if not text:
        return ''
    iso = parse_mmddyyyy(text)
    if iso:
        return iso
    found = parse_long_date(text)
    if found:
        return found.isoformat()
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if not m:
        return ''
    try:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return ''


def fallback_expiry(env):
    raw = (env.get(ENV_EXPIRES) or '').strip() or DEFAULT_EXPIRES
    return parse_long_date(raw) or parse_long_date(DEFAULT_EXPIRES)


def expiry_is_near(expires, page_days, today):
    """True from five days before the subscription ends, including the last day."""
    if page_days is not None and page_days <= WARN_DAYS:
        return True
    if expires is not None and (expires - today).days <= WARN_DAYS:
        return True
    return False


def norm_folio(s):
    digits = re.sub(r'\D', '', str(s or ''))
    if not digits or set(digits) <= {'0'}:
        return ''
    return digits.lstrip('0')


def norm_case(s):
    m = CASE_RE.search(s or '')
    return m.group(1).upper() if m else ''


def cfn_join(year, seq):
    y = re.sub(r'\D', '', year or '')
    s = re.sub(r'\D', '', seq or '')
    if not y or not s:
        return ''
    try:
        yi, si = int(y), int(s)
    except ValueError:
        return ''
    if yi <= 0 or si <= 0:
        return ''
    return '%04d%010d' % (yi, si)


def cfn_keys(cfn):
    """Forms of one CFN that are safe to compare for equality. No substrings."""
    digits = re.sub(r'\D', '', cfn or '')
    if not digits or set(digits) <= {'0'}:
        return set()
    keys = {digits, str(int(digits))}
    if len(digits) >= 8:
        keys.add(digits.zfill(14))
    return keys


def norm_bp(book, page):
    b = re.sub(r'\D', '', str(book or ''))
    p = re.sub(r'\D', '', str(page or ''))
    if not b or not p:
        return None
    try:
        bi, pi = int(b), int(p)
    except ValueError:
        return None
    if bi <= 0 or pi <= 0:
        return None
    return (str(bi), str(pi))


def parse_bp_text(s):
    m = re.search(r'(\d+)\s*/\s*(\d+)', str(s or ''))
    if not m:
        return None
    return norm_bp(m.group(1), m.group(2))


def bp_text(bp):
    if not bp:
        return ''
    return '%s/%s' % (bp[0], bp[1])


def money_amount(s):
    text = (s or '').strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value == 0:
        return None
    return value


def category_of(code, desc):
    c = (code or '').strip().upper()
    d = (desc or '').strip().upper()
    if c in CODE_CATEGORY:
        return CODE_CATEGORY[c]
    if c in OTHER_CODES:
        return ''
    if re.search(r'\bSATISFACTION\b|\bRELEASE\b', d):
        return 'release'
    if 'LIS PENDENS' in d:
        return 'lis_pendens'
    if re.search(r'\bJUDGMENT\b', d):
        return 'judgment'
    if re.search(r'\bLIEN\b', d):
        return 'lien'
    if re.search(r'\bMORTGAGE\b', d) and 'ASSIGNMENT' not in d:
        return 'mortgage'
    return ''


def _core_tokens(s):
    toks = NAME_RE.findall((s or '').upper().replace("'", ''))
    sig = [t for t in toks if len(t) > 1 and t not in SUFFIXES]
    initials = [t for t in toks if len(t) == 1]
    return sig, initials


def name_identity(s):
    """Significant tokens plus initials. None when the name is too thin to be unique."""
    sig, initials = _core_tokens(s)
    if len(sig) < 2:
        return None
    return (frozenset(sig), frozenset(initials))


def name_matches(owner, party):
    """Order-insensitive. Extra significant tokens on either side do not match.
    An initial the owner has must be on the party; an extra initial on the party is allowed."""
    left = name_identity(owner)
    if left is None:
        return False
    sig, initials = _core_tokens(party)
    if len(sig) < 2:
        return False
    if left[0] != frozenset(sig):
        return False
    if not left[1] <= frozenset(initials):
        return False
    return True


# ---------------------------------------------------------------------------------------------
# The .exp file and the database
# ---------------------------------------------------------------------------------------------
def parse_exp(text):
    """-> (records, bad_rows, control_skipped). records are raw 42-field dicts."""
    if text.startswith('\ufeff'):
        text = text[1:]
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    records, bad, control = [], 0, 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if '^' not in line:
            if control == 0 and not records and re.fullmatch(r'\d{20}', line.strip()):
                control += 1
                continue
            bad += 1
            continue
        parts = line.split('^')
        if len(parts) != 42:
            bad += 1
            continue
        records.append(dict(zip(FIELDS, parts)))
    return records, bad, control


def normalize_record(raw, source_file):
    key = (raw.get('key') or '').strip()
    if not key:
        return None
    txn = (raw.get('txn') or '').strip().upper()[:1]
    if txn not in ('I', 'U', 'D'):
        return None
    case_raw = raw.get('case_number') or ''
    misc = raw.get('orig_misc') or ''
    case = norm_case(case_raw) or norm_case(misc)
    folio_raw = (raw.get('folio') or '').strip()
    return {
        'key': key,
        'cfn': cfn_join(raw.get('cfn_year'), raw.get('cfn_seq')),
        'cfn_year': (raw.get('cfn_year') or '').strip(),
        'cfn_seq': (raw.get('cfn_seq') or '').strip(),
        'group_id': (raw.get('group_id') or '').strip(),
        'rec_date': parse_mmddyyyy(raw.get('rec_date_raw')),
        'rec_time': (raw.get('rec_time') or '').strip(),
        'book': (raw.get('book') or '').strip(),
        'page': (raw.get('page') or '').strip(),
        'book_type': (raw.get('book_type') or '').strip(),
        'doc_pages': (raw.get('doc_pages') or '').strip(),
        'append_pages': (raw.get('append_pages') or '').strip(),
        'doc_type': (raw.get('doc_type') or '').strip(),
        'doc_desc': (raw.get('doc_desc') or '').strip(),
        'doc_date': parse_mmddyyyy(raw.get('doc_date_raw')),
        'party_name': (raw.get('party_name') or '').strip(),
        'party_code': (raw.get('party_code') or '').strip().upper()[:1],
        'cross_party': (raw.get('cross_party') or '').strip(),
        'orig_cfn': cfn_join(raw.get('orig_cfn_year'), raw.get('orig_cfn_seq')),
        'orig_cfn_year': (raw.get('orig_cfn_year') or '').strip(),
        'orig_cfn_seq': (raw.get('orig_cfn_seq') or '').strip(),
        'orig_book': (raw.get('orig_book') or '').strip(),
        'orig_page': (raw.get('orig_page') or '').strip(),
        'orig_misc': misc.strip(),
        'subdivision': (raw.get('subdivision') or '').strip(),
        'folio': folio_raw,
        'folio_norm': norm_folio(folio_raw),
        'legal': (raw.get('legal') or '').strip(),
        'section': (raw.get('section') or '').strip(),
        'township': (raw.get('township') or '').strip(),
        'range_val': (raw.get('range_val') or '').strip(),
        'plat_book': (raw.get('plat_book') or '').strip(),
        'plat_page': (raw.get('plat_page') or '').strip(),
        'block': (raw.get('block') or '').strip(),
        'case_number': case_raw.strip(),
        'case_norm': case,
        'consideration_1': (raw.get('consideration_1') or '').strip(),
        'consideration_2': (raw.get('consideration_2') or '').strip(),
        'deed_doc_tax': (raw.get('deed_doc_tax') or '').strip(),
        'single_family': (raw.get('single_family') or '').strip(),
        'surtax': (raw.get('surtax') or '').strip(),
        'intangible': (raw.get('intangible') or '').strip(),
        'doc_stamps': (raw.get('doc_stamps') or '').strip(),
        'txn': txn,
        'party_seq': (raw.get('party_seq') or '').strip(),
        'modified': parse_mmddyyyy(raw.get('modified_raw')),
        'source_file': source_file,
        'deleted': 1 if txn == 'D' else 0,
    }


ROW_COLUMNS = (
    'key', 'cfn', 'cfn_year', 'cfn_seq', 'group_id', 'rec_date', 'rec_time',
    'book', 'page', 'book_type', 'doc_pages', 'append_pages', 'doc_type', 'doc_desc',
    'doc_date', 'party_name', 'party_code', 'cross_party', 'orig_cfn', 'orig_cfn_year',
    'orig_cfn_seq', 'orig_book', 'orig_page', 'orig_misc', 'subdivision', 'folio',
    'folio_norm', 'legal', 'section', 'township', 'range_val', 'plat_book', 'plat_page',
    'block', 'case_number', 'case_norm', 'consideration_1', 'consideration_2',
    'deed_doc_tax', 'single_family', 'surtax', 'intangible', 'doc_stamps', 'txn',
    'party_seq', 'modified', 'source_file', 'deleted', 'loaded_at',
)


def open_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rows (
            key TEXT PRIMARY KEY,
            cfn TEXT, cfn_year TEXT, cfn_seq TEXT, group_id TEXT,
            rec_date TEXT, rec_time TEXT, book TEXT, page TEXT, book_type TEXT,
            doc_pages TEXT, append_pages TEXT, doc_type TEXT, doc_desc TEXT, doc_date TEXT,
            party_name TEXT, party_code TEXT, cross_party TEXT,
            orig_cfn TEXT, orig_cfn_year TEXT, orig_cfn_seq TEXT,
            orig_book TEXT, orig_page TEXT, orig_misc TEXT,
            subdivision TEXT, folio TEXT, folio_norm TEXT, legal TEXT,
            section TEXT, township TEXT, range_val TEXT, plat_book TEXT, plat_page TEXT, block TEXT,
            case_number TEXT, case_norm TEXT,
            consideration_1 TEXT, consideration_2 TEXT, deed_doc_tax TEXT,
            single_family TEXT, surtax TEXT, intangible TEXT, doc_stamps TEXT,
            txn TEXT, party_seq TEXT, modified TEXT, source_file TEXT,
            deleted INTEGER NOT NULL DEFAULT 0, loaded_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applied (
            name TEXT PRIMARY KEY, applied_at TEXT, data_rows INTEGER, bad_rows INTEGER
        );
        CREATE TABLE IF NOT EXISTS downloads (
            name TEXT PRIMARY KEY, remote_id TEXT, bytes INTEGER, downloaded_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rows_cfn ON rows(cfn);
        CREATE INDEX IF NOT EXISTS idx_rows_case ON rows(case_norm);
        CREATE INDEX IF NOT EXISTS idx_rows_folio ON rows(folio_norm);
        CREATE INDEX IF NOT EXISTS idx_rows_deleted ON rows(deleted);
    """)
    return conn


def write_row(conn, record, loaded_at):
    """Insert, update, or tombstone one party row. The key is the identity, so a replay does not duplicate."""
    record = dict(record)
    record['loaded_at'] = loaded_at
    cols = ROW_COLUMNS
    placeholders = ','.join('?' for _ in cols)
    updates = ','.join('%s=excluded.%s' % (c, c) for c in cols if c != 'key')
    conn.execute(
        'INSERT INTO rows (%s) VALUES (%s) ON CONFLICT(key) DO UPDATE SET %s'
        % (','.join(cols), placeholders, updates),
        [record.get(c) for c in cols])


def apply_text(conn, name, text, loaded_at):
    """Apply one file if it has not been applied. Returns a small stats dict."""
    seen = conn.execute('SELECT 1 FROM applied WHERE name=?', (name,)).fetchone()
    if seen:
        return {'name': name, 'applied': False, 'data_rows': 0, 'bad_rows': 0, 'skipped_txn': 0}
    raws, bad, _control = parse_exp(text)
    written = 0
    skipped_txn = 0
    with conn:
        for raw in raws:
            rec = normalize_record(raw, name)
            if rec is None:
                skipped_txn += 1
                continue
            write_row(conn, rec, loaded_at)
            written += 1
        conn.execute(
            'INSERT INTO applied (name, applied_at, data_rows, bad_rows) VALUES (?,?,?,?)',
            (name, loaded_at, written, bad + skipped_txn))
    return {'name': name, 'applied': True, 'data_rows': written, 'bad_rows': bad,
            'skipped_txn': skipped_txn}


def read_zip_exp(blob):
    """The single .exp member, decoded. Raises Gap if the zip is not the file we expect."""
    if not blob or blob[:2] != b'PK':
        raise Gap('download was not a zip')
    if len(blob) > MAX_ZIP_BYTES:
        raise Gap('download was larger than the daily file')
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise Gap('download was not a zip') from exc
    names = [n for n in zf.namelist()
             if n.lower().endswith('.exp') and '..' not in n.replace('\\', '/')
             and not n.startswith('/') and not n.startswith('\\')]
    if len(names) != 1:
        raise Gap('zip did not contain one exp file')
    data = zf.read(names[0])
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252')


def file_stamp(name):
    m = FILE_RE.match(name or '')
    if not m:
        return ''
    return parse_mmddyyyy(m.group(1))


def apply_saved_zips(conn, files_dir, loaded_at):
    applied = []
    names = sorted(os.listdir(files_dir)) if os.path.isdir(files_dir) else []
    for fname in names:
        if not fname.lower().endswith('.zip'):
            continue
        stem = fname[:-4]
        if not FILE_RE.match(stem):
            continue
        path = os.path.join(files_dir, fname)
        try:
            blob = open(path, 'rb').read()
            text = read_zip_exp(blob)
        except (OSError, Gap):
            continue
        applied.append(apply_text(conn, stem, text, loaded_at))
    return applied


def live_party_rows(conn, source_file=None):
    if source_file:
        return conn.execute(
            'SELECT * FROM rows WHERE deleted=0 AND source_file=?', (source_file,)).fetchall()
    return conn.execute('SELECT * FROM rows WHERE deleted=0').fetchall()


def documents_from_rows(rows):
    """Collapse party rows into one document. Identity is CFN + group, or the row key when there is no CFN."""
    groups = {}
    order = []
    for row in rows:
        if row['cfn']:
            doc_id = '%s:%s' % (row['cfn'], row['group_id'] or '')
        else:
            doc_id = 'key:' + row['key']
        if doc_id not in groups:
            groups[doc_id] = []
            order.append(doc_id)
        groups[doc_id].append(row)
    docs = []
    for doc_id in order:
        parts = groups[doc_id]
        first = parts[0]
        cases, folios, parties, orig_cfns = [], [], [], []
        for part in parts:
            if part['case_norm'] and part['case_norm'] not in cases:
                cases.append(part['case_norm'])
            if part['folio_norm'] and part['folio_norm'] not in folios:
                folios.append(part['folio_norm'])
            if part['orig_cfn'] and part['orig_cfn'] not in orig_cfns:
                orig_cfns.append(part['orig_cfn'])
            parties.append({
                'name': part['party_name'], 'code': part['party_code'],
                'cross': part['cross_party'], 'seq': part['party_seq'],
            })
        docs.append({
            'doc_id': doc_id,
            'cfn': first['cfn'] or '',
            'book': (first['book'] or '').strip(),
            'page': (first['page'] or '').strip(),
            'bp': norm_bp(first['book'], first['page']),
            'orig_bp': norm_bp(first['orig_book'], first['orig_page']),
            'orig_cfns': orig_cfns,
            'doc_type': (first['doc_type'] or '').strip(),
            'doc_desc': (first['doc_desc'] or '').strip(),
            'category': category_of(first['doc_type'], first['doc_desc']),
            'rec_date': first['rec_date'] or '',
            'cases': cases,
            'folios': folios,
            'parties': parties,
            'amount': money_amount(first['consideration_1']),
            'source_file': first['source_file'] or '',
            'keys': [part['key'] for part in parts],
        })
    return docs


# ---------------------------------------------------------------------------------------------
# Cases the pipeline already tracks, and the match
# ---------------------------------------------------------------------------------------------
def _miami_county(value):
    text = re.sub(r'[^A-Z]', '', str(value or '').upper())
    return text in ('', 'MIAMIDADE', 'DADE', 'MD')


def _owner_from_lead(row):
    clean = str(row.get('owner_clean') or '').strip()
    if clean:
        return clean
    oname = str(row.get('oname') or '').strip()
    if ',' in oname:
        last, rest = oname.split(',', 1)
        return ('%s %s' % (rest.strip(), last.strip())).strip()
    if oname:
        return oname
    return str(row.get('owners') or '').strip()


def _consider_lead(cases, row):
    if not isinstance(row, dict):
        return
    case = norm_case(row.get('Case #') or row.get('case') or row.get('Case') or '')
    if not case or not MIAMI_CASE_RE.match(case):
        return
    if not _miami_county(row.get('county')):
        return
    ent = cases.setdefault(case, {'case': case, 'folios': set(), 'owners': set()})
    folio = norm_folio(row.get('folio') or row.get('Folio') or row.get('folioNumber') or '')
    if folio:
        ent['folios'].add(folio)
    owner = _owner_from_lead(row)
    if owner:
        ent['owners'].add(owner)


def load_tracked_cases(repo):
    """Miami-Dade cases from the lead files, plus a folio the stub resolver already corroborated.

    There is no separate case/parcel database. leads_final.json is the auction board,
    *_leads.json includes the lis pendens lane, and stub_folios.json is the case->folio
    cache. Broward and Palm Beach rows are not this county's recordings.
    """
    cases = {}
    if not repo or not os.path.isdir(repo):
        return cases
    paths = [os.path.join(repo, 'leads_final.json')]
    paths += sorted(glob.glob(os.path.join(repo, '*_leads.json')))
    for path in paths:
        base = os.path.basename(path)
        if base in ('leads_raw.json',) or base.startswith('_'):
            continue
        try:
            data = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            for row in data:
                _consider_lead(cases, row)
        elif isinstance(data, dict):
            for row in data.values():
                _consider_lead(cases, row)
    stub_path = os.path.join(repo, 'stub_folios.json')
    try:
        stub = json.load(open(stub_path, encoding='utf-8'))
    except (OSError, ValueError):
        stub = {}
    if isinstance(stub, dict):
        for case, ent in stub.items():
            key = norm_case(case)
            if key not in cases or not isinstance(ent, dict) or ent.get('miss'):
                continue
            folio = norm_folio(ent.get('folio'))
            if folio:
                cases[key]['folios'].add(folio)
            owner = str(ent.get('owner') or '').strip()
            if owner:
                cases[key]['owners'].add(owner)
    return cases


def load_chain_book_pages(path):
    """case -> set of (book, page) the owner-search chain already recorded."""
    try:
        data = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for case, chain in data.items():
        key = norm_case(case)
        if not key or not isinstance(chain, dict):
            continue
        pages = set()
        for bucket in ('liens', 'other'):
            for row in chain.get(bucket) or []:
                if isinstance(row, dict):
                    bp = parse_bp_text(row.get('bp'))
                    if bp:
                        pages.add(bp)
        if pages:
            out[key] = pages
    return out


def load_index_cfn_pages(path):
    """CFN key -> set of (book, page), from records_index.json. Equality only."""
    try:
        data = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for row in data.values():
        if not isinstance(row, dict):
            continue
        bp = norm_bp(row.get('reC_BOOK'), row.get('reC_PAGE'))
        if not bp:
            bp = parse_bp_text(row.get('bp'))
        cfn = str(row.get('cfN_MASTER_ID') or row.get('cfn') or '')
        if not bp or not cfn:
            continue
        for form in cfn_keys(cfn):
            out.setdefault(form, set()).add(bp)
    return out


def _add(found, case, via):
    found.setdefault(case, set()).add(via)


def match_documents(docs, cases, chain_pages=None, index_cfn_pages=None):
    """Match surfaced documents onto tracked cases.

    High confidence: the document's case number, its folio, its CFN, or an
    original-document reference (original CFN or original book/page) that lands
    on a recording already tied to the case.
    Low confidence: a lien with no folio and no case number whose party name
    equals exactly one tracked owner's name. Two cases sharing that name, a
    missing middle token, or a mortgage with no folio: no party match.
    """
    cases = cases or {}
    chain_pages = chain_pages or {}
    index_cfn_pages = index_cfn_pages or {}
    folio_to_cases = {}
    for case, ent in cases.items():
        for folio in ent.get('folios') or ():
            if folio:
                folio_to_cases.setdefault(folio, set()).add(case)
    # book/page -> cases, from the scraper chain and from documents we can already place
    bp_to_cases = {}
    for case, pages in chain_pages.items():
        if case not in cases:
            continue
        for bp in pages:
            bp_to_cases.setdefault(bp, set()).add(case)

    def remember(doc, case_ids):
        if doc.get('bp'):
            for case in case_ids:
                bp_to_cases.setdefault(doc['bp'], set()).add(case)
        for form in cfn_keys(doc.get('cfn')):
            for bp in index_cfn_pages.get(form) or ():
                for case in case_ids:
                    bp_to_cases.setdefault(bp, set()).add(case)

    placed = {}  # doc_id -> {case: set(via)}
    for doc in docs:
        if doc.get('category') not in SURFACE:
            continue
        found = {}
        for case in doc.get('cases') or []:
            if case in cases:
                _add(found, case, 'case')
        for folio in doc.get('folios') or []:
            for case in folio_to_cases.get(folio) or ():
                _add(found, case, 'folio')
        if found:
            placed[doc['doc_id']] = found
            remember(doc, found.keys())

    # CFN and original-document references, now that placed documents contribute book/pages.
    cfn_to_cases = {}
    for doc in docs:
        hit = placed.get(doc['doc_id'])
        if not hit or not doc.get('cfn'):
            continue
        for form in cfn_keys(doc['cfn']):
            cfn_to_cases.setdefault(form, set()).update(hit.keys())
    for form, pages in index_cfn_pages.items():
        owners = set()
        for bp in pages:
            owners |= bp_to_cases.get(bp) or set()
        if owners:
            cfn_to_cases.setdefault(form, set()).update(owners)

    for doc in docs:
        if doc.get('category') not in SURFACE:
            continue
        found = placed.setdefault(doc['doc_id'], {})
        if doc.get('bp'):
            for case in bp_to_cases.get(doc['bp']) or ():
                _add(found, case, 'book_page')
        if doc.get('orig_bp'):
            for case in bp_to_cases.get(doc['orig_bp']) or ():
                _add(found, case, 'orig_book_page')
        for cfn in [doc.get('cfn')] + list(doc.get('orig_cfns') or []):
            via = 'cfn' if cfn == doc.get('cfn') else 'orig_cfn'
            for form in cfn_keys(cfn):
                for case in cfn_to_cases.get(form) or ():
                    _add(found, case, via)
        if found:
            remember(doc, found.keys())

    # Party name, liens only, and only when the recording names neither a folio nor a case.
    for doc in docs:
        if doc.get('category') != 'lien':
            continue
        if placed.get(doc['doc_id']):
            continue
        if doc.get('folios') or doc.get('cases'):
            continue
        hits = set()
        ambiguous = False
        for party in doc.get('parties') or []:
            for side in (party.get('name'), party.get('cross')):
                if name_identity(side) is None:
                    continue
                owners = {case for case, ent in cases.items()
                          if any(name_matches(owner, side) for owner in (ent.get('owners') or ()))}
                if len(owners) > 1:
                    ambiguous = True
                    continue
                if len(owners) == 1:
                    hits |= owners
        if ambiguous or len(hits) != 1:
            continue
        placed.setdefault(doc['doc_id'], {})
        _add(placed[doc['doc_id']], next(iter(hits)), 'party')

    high = {'case', 'folio', 'cfn', 'orig_cfn', 'book_page', 'orig_book_page'}
    by_id = {doc['doc_id']: doc for doc in docs}
    matches = []
    for doc_id, found in placed.items():
        doc = by_id.get(doc_id)
        if not doc or not found:
            continue
        for case, vias in sorted(found.items()):
            if not vias:
                continue
            matches.append({
                'case': case,
                'category': doc['category'],
                'confidence': 'high' if vias & high else 'low',
                'via': sorted(vias),
                'cfn': doc.get('cfn') or '',
                'book': doc.get('book') or '',
                'page': doc.get('page') or '',
                'bp': bp_text(doc.get('bp')),
                'rec_date': doc.get('rec_date') or '',
                'doc_type': doc.get('doc_type') or '',
                'doc_desc': doc.get('doc_desc') or '',
                'folios': list(doc.get('folios') or []),
                'amount': doc.get('amount'),
                'source_file': doc.get('source_file') or '',
                'parties': list(doc.get('parties') or []),
                'doc_id': doc_id,
            })
    matches.sort(key=lambda m: (m['case'], m['category'], m['rec_date'], m['bp'], m['cfn']))
    return matches


# ---------------------------------------------------------------------------------------------
# HTTP. The transport is injectable so tests never touch the network.
# ---------------------------------------------------------------------------------------------
class HttpResp(object):
    def __init__(self, status, content, headers):
        self.status = int(status or 0)
        self.content = content or b''
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}

    @property
    def text(self):
        if self.content[:2] == b'PK':
            return ''
        return self.content.decode('utf-8', errors='replace')


class RequestsTransport(object):
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers['User-Agent'] = UA

    def __call__(self, method, url, data=None, timeout=45):
        import requests
        try:
            resp = self.s.request(method, url, data=data, timeout=timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise SiteDown('clerk site unreachable') from exc
        return HttpResp(resp.status_code, resp.content, resp.headers)


class Client(object):
    def __init__(self, transport, sleeper, pause):
        self.transport = transport
        self.sleeper = sleeper or (lambda _n: None)
        self.pause = pause
        self._last = 0.0

    def _wait(self):
        if self.pause:
            delay = self.pause - (time.time() - self._last)
            if delay > 0:
                self.sleeper(delay)
        self._last = time.time()

    def request(self, method, url, data=None):
        assert_allowed(url)
        self._wait()
        current, verb, payload = url, method, data
        for _hop in range(4):
            assert_allowed(current)
            try:
                resp = self.transport(verb, current, data=payload, timeout=45)
            except (Gap, AssertionError):
                raise
            except Exception:
                # The transport's own message can quote the request. Ours does not.
                raise SiteDown('clerk site unreachable')
            if resp.status in (301, 302, 303, 307, 308):
                loc = urllib.parse.urljoin(current, resp.headers.get('location') or '')
                assert_allowed(loc)  # raises before the next hop is sent
                verb = 'GET' if resp.status in (301, 302, 303) else verb
                payload = None if verb == 'GET' else payload
                current = loc
                continue
            if resp.status >= 500:
                raise SiteDown('clerk site returned %s' % resp.status)
            return resp
        raise SiteDown('clerk site redirected too many times')

    def login(self, username, password):
        self.request('GET', LOGIN_URL)
        resp = self.request('POST', LOGIN_URL, {'Email': username, 'Password': password})
        if resp.status in (401, 403):
            raise LoginFailed('login failed')
        if 'sign out' not in resp.text.lower():
            raise LoginFailed('login failed')
        return resp

    def units(self):
        resp = self.request('GET', ACCOUNT_URL)
        if resp.status in (401, 403):
            raise LoginFailed('login failed')
        return parse_units(resp.text)

    def ftp_page(self):
        resp = self.request('GET', FTP_URL)
        if resp.status in (401, 403):
            raise Gap('access lapsed')
        return resp.text

    def download(self, file_id):
        resp = self.request('GET', download_url(file_id))
        if resp.status in (401, 403):
            raise Gap('access lapsed')
        return resp.content


def parse_units(html):
    m = BALANCE_RE.search(html or '')
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_ftp_page(html):
    """Listed daily files, plus the Records folder's expiry if the page states it.

    Only table rows that name dly_records_MMDDYYYY and a numeric DownloadFile id.
    View More, basket, and every other folder's links are ignored.
    """
    files = []
    seen = set()
    for tr in TR_RE.findall(html or ''):
        name_m = FILE_IN_ROW_RE.search(tr)
        id_m = ID_IN_ROW_RE.search(tr)
        if not name_m or not id_m:
            continue
        name = 'dly_records_' + name_m.group(1)
        if not file_stamp(name) or name in seen:
            continue
        seen.add(name)
        files.append({'name': name, 'id': id_m.group(1), 'date': file_stamp(name)})
    files.sort(key=lambda item: item['date'])
    idx = (html or '').lower().find('dly_records_')
    window = (html or '')[max(0, idx - 2500):idx + 800] if idx >= 0 else (html or '')
    exp_m = EXPIRY_RE.search(window)
    days_m = DAYS_RE.search(window)
    expires = parse_long_date(exp_m.group(1)) if exp_m else None
    days = int(days_m.group(1)) if days_m else None
    return {'files': files, 'expires': expires, 'days_left': days,
            'lapsed_phrase': bool(LAPSED_RE.search(window))}


def access_lapsed(parsed, today):
    """Lapsed only from the Records folder's own date, day count, or expiry sentence.

    A "Not Verified" registration row on MyAccount is not a lapse — the probe
    still downloaded files with that row present. Phrases elsewhere on the
    page (other products, footers) are not this folder.
    """
    if parsed.get('lapsed_phrase'):
        return True
    if parsed.get('days_left') == 0:
        return True
    expires = parsed.get('expires')
    if expires is not None and expires < today:
        return True
    return False


# ---------------------------------------------------------------------------------------------
# Output. status.json has no party names. evidence.json does, and it stays outside the repo.
# ---------------------------------------------------------------------------------------------
def resolve_data_dir(root):
    if root:
        base = os.path.abspath(root)
    else:
        base = os.path.abspath(os.path.join(P.DEALFLOW_DIR, 'or_daily'))
    repo = os.path.abspath(HERE)
    if base == repo or base.startswith(repo + os.sep):
        raise Gap('data dir is inside the repo; refusing')
    if 'onedrive' in base.lower():
        raise Gap('data dir is inside OneDrive; refusing')
    os.makedirs(os.path.join(base, 'files'), exist_ok=True)
    return base


def _atomic_json(path, payload):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, path)


def write_status(data_dir, status, env):
    # Belt: a gap reason is one of our own strings, and scrub catches a credential anyway.
    blob = json.loads(scrub(json.dumps(status), env))
    _atomic_json(os.path.join(data_dir, 'status.json'), blob)


def render_markdown(today, status, matches):
    counts = {}
    for cat in SURFACE:
        counts[cat] = sum(1 for m in matches if m['category'] == cat)
    high = sum(1 for m in matches if m['confidence'] == 'high')
    low = sum(1 for m in matches if m['confidence'] == 'low')
    lines = [
        '# Official Records daily file — %s' % today.isoformat(),
        '',
        'Report only. This file does not change send, callable, or hold.',
        '',
        '- Files newly downloaded: %s' % ', '.join(status.get('downloaded') or []) or '(none)',
        '- Units: %s -> %s' % (status.get('units_before'), status.get('units_after')),
        '- Expires: %s (%s)' % (status.get('expires') or '?', status.get('expires_source') or '?'),
        '- Matches: %d (%d high, %d low)' % (len(matches), high, low),
    ]
    for cat in SURFACE:
        lines.append('- %s: %d' % (cat, counts[cat]))
    if status.get('expiry_warn'):
        lines.append('')
        lines.append('WARNING: subscription expires %s (%s days left).' % (
            status.get('expires'), status.get('days_left')))
    lines.append('')
    lines.append('## Matched recordings')
    lines.append('')
    if not matches:
        lines.append('(none)')
    else:
        for m in matches[:200]:
            who = '; '.join(
                p.get('name') or '' for p in (m.get('parties') or []) if p.get('name'))
            lines.append('- %s  %s  %s  %s  %s  via %s  %s' % (
                m['case'], m['category'], m.get('bp') or m.get('cfn') or '-',
                m.get('rec_date') or '', m['confidence'], ','.join(m.get('via') or []),
                who[:80]))
        if len(matches) > 200:
            lines.append('- ... %d more in evidence.json' % (len(matches) - 200))
    lines.append('')
    return '\n'.join(lines)


def write_evidence(data_dir, today, status, matches):
    counts = {cat: sum(1 for m in matches if m['category'] == cat) for cat in SURFACE}
    payload = {
        'generated': today.isoformat(),
        'report_only': True,
        'affects_send': False,
        'affects_callable': False,
        'affects_hold': False,
        'note': ('Report only. Not read by send, callable, or hold. '
                 'high = case number, folio, or a recording reference; '
                 'low = a lien with no folio and no case number whose party name '
                 'equals exactly one tracked owner.'),
        'counts': counts,
        'matches': matches,
    }
    _atomic_json(os.path.join(data_dir, 'evidence.json'), payload)
    md_path = os.path.join(data_dir, 'OR-DAILY-%s.md' % today.isoformat())
    tmp = md_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(render_markdown(today, status, matches))
    os.replace(tmp, md_path)


def base_status(today, expires, source, days_left, warn):
    return {
        'ran': today.isoformat(),
        'ok': False,
        'gap': '',
        'units_before': None,
        'units_after': None,
        'units_dropped': False,
        'expires': expires.isoformat() if expires else '',
        'expires_source': source,
        'days_left': days_left,
        'expiry_warn': bool(warn),
        'downloaded': [],
        'applied': [],
        'matched': 0,
        'tracked_cases': 0,
        'lapsed': False,
    }


def _days_left(expires, page_days, today):
    if page_days is not None:
        return page_days
    if expires is not None:
        return (expires - today).days
    return None


def match_and_store(data_dir, repo, today, status, env):
    db_path = os.path.join(data_dir, 'or_daily.sqlite')
    conn = open_db(db_path)
    try:
        apply_saved_zips(conn, os.path.join(data_dir, 'files'), today.isoformat())
        docs = documents_from_rows(live_party_rows(conn))
    finally:
        conn.close()
    cases = load_tracked_cases(repo)
    chain = load_chain_book_pages(os.path.join(repo, 'records_liens.json')) if repo else {}
    index = load_index_cfn_pages(os.path.join(repo, 'records_index.json')) if repo else {}
    matches = match_documents(docs, cases, chain, index)
    surfaced = [m for m in matches if m['category'] in SURFACE]
    status['matched'] = len(surfaced)
    status['tracked_cases'] = len(cases)
    status['documents'] = len(docs)
    write_evidence(data_dir, today, status, surfaced)
    return surfaced


def finish(data_dir, repo, today, status, env):
    """Match whatever is already on disk, then write status. A gap stays a gap."""
    try:
        files_dir = os.path.join(data_dir, 'files')
        has_files = os.path.isdir(files_dir) and any(
            name.lower().endswith('.zip') for name in os.listdir(files_dir))
        has_db = os.path.exists(os.path.join(data_dir, 'or_daily.sqlite'))
        if has_files or has_db:
            match_and_store(data_dir, repo, today, status, env)
    except Exception as exc:
        status['ok'] = False
        status['gap'] = status['gap'] or ('match failed: %s' % type(exc).__name__)
    status['ok'] = not status.get('gap')
    write_status(data_dir, status, env)
    return status


def run(env, transport=None, data_dir=None, repo_dir=None, today=None, pause=2.0, sleeper=None):
    """One ingest. Returns the status dict. Raises only on a refused local path, which main catches."""
    today = today or dt.date.today()
    env = env if env is not None else os.environ
    repo = repo_dir if repo_dir is not None else HERE
    data_dir = resolve_data_dir(data_dir)
    configured = fallback_expiry(env)
    status = base_status(today, configured, 'config',
                         (configured - today).days if configured else None,
                         expiry_is_near(configured, None, today))
    user = (env.get(ENV_USER) or '').strip()
    password = (env.get(ENV_PASS) or '').strip()
    if not user or not password:
        status['gap'] = 'credentials not set'
        return finish(data_dir, repo, today, status, env)
    client = Client(transport or RequestsTransport(), sleeper or time.sleep, pause)
    try:
        client.login(user, password)
        before = client.units()
        status['units_before'] = before
        if before is None:
            status['gap'] = 'units balance unreadable'
            return finish(data_dir, repo, today, status, env)
        html = client.ftp_page()
        parsed = parse_ftp_page(html)
        expires = parsed['expires'] or configured
        source = 'page' if parsed['expires'] else 'config'
        days = _days_left(expires, parsed['days_left'], today)
        status.update(expires=expires.isoformat() if expires else '',
                      expires_source=source, days_left=days,
                      expiry_warn=expiry_is_near(expires, parsed['days_left'], today))
        if access_lapsed(parsed, today):
            status['gap'] = 'access lapsed'
            status['lapsed'] = True
            return finish(data_dir, repo, today, status, env)
        if 'dly_records_' in html.lower() and not parsed['files']:
            status['gap'] = 'daily file list could not be read'
            return finish(data_dir, repo, today, status, env)
        after_list = client.units()
        status['units_after'] = after_list
        if after_list is None or after_list < before:
            status['units_dropped'] = after_list is not None and after_list < before
            status['gap'] = 'units dropped' if status['units_dropped'] else 'units balance unreadable'
            return finish(data_dir, repo, today, status, env)
        files_dir = os.path.join(data_dir, 'files')
        conn = open_db(os.path.join(data_dir, 'or_daily.sqlite'))
        try:
            have = {row['name'] for row in conn.execute('SELECT name FROM downloads')}
        finally:
            conn.close()
        for item in parsed['files']:
            if item['name'] in have and os.path.exists(os.path.join(files_dir, item['name'] + '.zip')):
                continue
            blob = client.download(item['id'])
            if not blob or blob[:2] != b'PK' or len(blob) > MAX_ZIP_BYTES:
                status['gap'] = 'download was not a zip'
                return finish(data_dir, repo, today, status, env)
            dest = os.path.join(files_dir, item['name'] + '.zip')
            tmp = dest + '.tmp'
            with open(tmp, 'wb') as fh:
                fh.write(blob)
            os.replace(tmp, dest)
            conn = open_db(os.path.join(data_dir, 'or_daily.sqlite'))
            try:
                with conn:
                    conn.execute(
                        'INSERT INTO downloads (name, remote_id, bytes, downloaded_at) VALUES (?,?,?,?) '
                        'ON CONFLICT(name) DO UPDATE SET remote_id=excluded.remote_id, bytes=excluded.bytes, '
                        'downloaded_at=excluded.downloaded_at',
                        (item['name'], item['id'], len(blob), today.isoformat()))
            finally:
                conn.close()
            status['downloaded'].append(item['name'])
            after = client.units()
            status['units_after'] = after
            if after is None or after < before:
                status['units_dropped'] = after is not None and after < before
                status['gap'] = 'units dropped' if status['units_dropped'] else 'units balance unreadable'
                break
        return finish(data_dir, repo, today, status, env)
    except Denied:
        status['gap'] = 'refused a blocked URL'
        return finish(data_dir, repo, today, status, env)
    except Gap as exc:
        status['gap'] = exc.args[0] if exc.args else 'gap'
        status['lapsed'] = status['gap'] == 'access lapsed' or status.get('lapsed')
        return finish(data_dir, repo, today, status, env)
    except AssertionError:
        raise
    except Exception:
        status['gap'] = 'clerk site unreachable'
        return finish(data_dir, repo, today, status, env)


def health_checks(status=None, today=None, env=None, path=None):
    """(level, name, detail) rows for healthcheck. Empty when this stage was never turned on."""
    today = today or dt.date.today()
    env = os.environ if env is None else env
    if status is None:
        path = path or os.path.join(P.DEALFLOW_DIR, 'or_daily', 'status.json')
        if not os.path.exists(path):
            if enabled(env):
                return [('WARN', 'official records daily',
                         'enabled but no ingest status on file')]
            return []
        try:
            status = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError):
            return [('WARN', 'official records daily', 'status file unreadable')]
    if not isinstance(status, dict):
        return [('WARN', 'official records daily', 'status file unreadable')]
    out = []
    expires = parse_long_date(status.get('expires') or '')
    if status.get('lapsed') or (expires is not None and expires < today):
        out.append(('WARN', 'official records subscription',
                    'expired %s — daily file not ingesting' % (status.get('expires') or '?')))
    elif expiry_is_near(expires, None, today):
        days = (expires - today).days if expires else status.get('days_left')
        out.append(('WARN', 'official records subscription',
                    'expires %s (%s days left)' % (status.get('expires') or '?', days)))
    if status.get('units_dropped'):
        out.append(('WARN', 'official records units',
                    'balance dropped %s -> %s; ingest aborted' % (
                        status.get('units_before'), status.get('units_after'))))
    gap = status.get('gap') or ''
    if gap and gap not in ('access lapsed', 'units dropped'):
        out.append(('WARN', 'official records daily',
                    'last run gap: %s (%s)' % (gap, status.get('ran') or '?')))
    if not out:
        out.append(('PASS', 'official records daily',
                    'last run ok, %s matched, units unchanged' % (status.get('matched') or 0)))
    return out


def _print_status(status, env):
    if status.get('gap'):
        say('OR daily: GAP %s — refresh continues' % status['gap'], env)
    else:
        say('OR daily: downloaded %d, matched %d, units %s -> %s, expires %s (%s days)' % (
            len(status.get('downloaded') or []), status.get('matched') or 0,
            status.get('units_before'), status.get('units_after'),
            status.get('expires') or '?', status.get('days_left')), env)
    if status.get('expiry_warn'):
        say('OR daily: WARNING subscription expires %s (%s days left)' % (
            status.get('expires') or '?', status.get('days_left')), env)


def main(argv=None, env=None, transport=None, data_dir=None, repo_dir=None, today=None,
         pause=2.0, sleeper=None):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description='Ingest the Miami-Dade Clerk Official Records daily file')
    ap.add_argument('--plan', action='store_true', help='print the plan; no network and no credentials used')
    ap.add_argument('--enable', action='store_true', help='run even without DEALFLOW_OR_DAILY=1')
    args = ap.parse_args(argv)
    if args.plan:
        user_set = bool((env.get(ENV_USER) or '').strip())
        pass_set = bool((env.get(ENV_PASS) or '').strip())
        exp = fallback_expiry(env)
        say('OR daily: plan', env)
        say('  enabled: %s' % (enabled(env) or args.enable), env)
        say('  credentials set: %s' % ('yes' if user_set and pass_set else 'no'), env)
        say('  expiry fallback: %s' % (exp.isoformat() if exp else '?'), env)
        say('  report only: send, callable, and hold are unchanged', env)
        say('  data dir: ~/DEALFLOW/or_daily (outside the repo)', env)
        return 0
    if not (enabled(env) or args.enable):
        say('OR daily: off (set DEALFLOW_OR_DAILY=1 to ingest the Clerk Records folder)', env)
        return 0
    try:
        status = run(env, transport=transport, data_dir=data_dir, repo_dir=repo_dir,
                     today=today, pause=pause, sleeper=sleeper)
    except Gap as exc:
        say('OR daily: GAP %s — refresh continues' % (exc.args[0] if exc.args else 'gap'), env)
        return 0
    _print_status(status, env)
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
