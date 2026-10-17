#!/usr/bin/env python3
"""broward_or_feed.py — Broward foreclosure leads from the county's free Official Records daily files.

The recorder posts one set of pipe-delimited files per recording day (doc / nme / lnk).
Lis pendens rows are the filings. The party file supplies the plaintiff (D, "direct") and
the defendants (R, "reverse"). The files carry no parcel and no street address on a lis
pendens, so a parcel comes only from a name match against the local FDOR name roll.

This builds a lead list. It does not merge into lis_pendens.json, and it does not change
who can be called, mailed, or texted. Broward outreach stays exactly as it is today.
Every row here is marked outreach "held" so a later reader can see that.

Off unless DEALFLOW_BROWARD_OR_FEED=1. A manual run can pass --dir instead. The day's
files and the roll CSV stay outside this repo (the repo is public). Output is
~/DEALFLOW/broward_or_feed/ (or DEALFLOW_DIR), never a path inside the checkout.
foreclosure_leads.make_tracker globs *_leads.json in the checkout, so this writer
uses feed.json and refuses to write inside the repo at all.

    python broward_or_feed.py --dir /path/to/day-files --roll /path/to/NAL.csv
    python broward_or_feed.py                 # no-op unless DEALFLOW_BROWARD_OR_FEED=1

Days may arrive out of order. Every file in the folder is read, then closings are
applied. A final judgment (FJ, CFJ) or a release (RST) with the same normalized case
number drops the case. An RST whose link row cites the lis pendens instrument drops
that instrument. Filename order is not a date.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FLAG = 'DEALFLOW_BROWARD_OR_FEED'
ENV_DIR = 'DEALFLOW_BROWARD_OR_DIR'
ENV_ROLL = 'DEALFLOW_BROWARD_ROLL'
ENV_CONFIG = 'DEALFLOW_BROWARD_OR_CONFIG'
_ON = ('1', 'true', 'yes', 'on')

# Same columns the statewide cadastral client asks for (fl_cadastral.FLDS). That client
# looks up one parcel or one street over the network and has no owner-name index, which
# is what a day of lis pendens needs. The local NAL extract is that index.
ROLL_COLUMNS = ('PARCEL_ID', 'OWN_NAME', 'PHY_ADDR1', 'PHY_CITY', 'PHY_ZIPCD')

_NOT_CASE = {'', '-NOT SHOWN', 'NOT SHOWN', 'OTHER', 'UNKNOWN', 'NONE', 'N/A', 'NA', 'NULL'}
_DOC_RE = re.compile(r'^(\d{2})-(\d{2})-(\d{4})doc-ver\.txt$', re.I)
_NME_RE = re.compile(r'^(\d{2})-(\d{2})-(\d{4})nme-ver\.txt$', re.I)
_LNK_RE = re.compile(r'^(\d{2})-(\d{2})-(\d{4})lnk-ver\.txt$', re.I)
_SUFFIX_END = re.compile(
    r'(?i)(?:\s+(?:LIFE ESTATE|ET\s*AL|ET\s*UX|ET\s*VIR|TRUSTEE|TRS|TR|JR|SR|II|III|IV))+\s*$')

# Plaintiff class. Lender is the generic words plus servicers whose names do not contain
# those words, and a trust that carries a year ("2021-1 TRUST"). A bare TRUST is not a
# lender: "DOE FAMILY TRUST" is someone else. HUD and the Secretary are government, not lenders.
_LENDER_RE = re.compile(
    r'\b(?:BANK|MORTGAGE|MTG|LOAN|SERVICING|FUNDING|CREDIT UNION|'
    r'N\.A\.|NATIONAL ASSOCIATION|'
    r'WILMINGTON|WELLS FARGO|JPMORGAN|CITIBANK|NATIONSTAR|CARRINGTON|SELENE|'
    r'RUSHMORE|PENNYMAC|SHELLPOINT|NEWREZ|LAKEVIEW|FANNIE|FREDDIE|'
    r'LOANDEPOT|CALIBER|U\.S\. BANK|US BANK|DEUTSCHE|PHH|SPECIALIZED LOAN)\b|'
    r'\b(?:19|20)\d{2}(?:-\d+[A-Z]?)?\s+TRUST\b|'
    r'\bTRUST\s+(?:19|20)\d{2}\b', re.I)
_GOV_RE = re.compile(
    r'\b(?:UNITED STATES|U\.S\.A\.|USA|STATE OF FLORIDA|'
    r'FLORIDA DEPARTMENT|FLORIDA DEPT|DEPARTMENT OF|DEPT OF|'
    r'COUNTY OF|BROWARD COUNTY|CITY OF|TOWN OF|VILLAGE OF|'
    r'TAX COLLECTOR|SHERIFF|HOUSING AUTHORITY|SECRETARY OF HOUSING|'
    r'HUD|INTERNAL REVENUE|IRS)\b', re.I)
_HOA_RE = re.compile(
    r'\b(?:HOA|COA|POA|CONDOMINIUM|CONDO|HOMEOWNERS?|PROPERTY OWNERS?|'
    r'MASTER ASSOC\w*|COMMUNITY ASSOC\w*|ASSOC(?:IATION)?|ASSN)\b', re.I)
_ENTITY_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LTD|LLP|PLLC|COMPANY|'
    r'ASSOC|ASSOCIATION|ASSN|TRUST|BANK|MORTGAGE|COUNTY|CITY|STATE|'
    r'DEPARTMENT|CHURCH|HOSPITAL|AUTHORITY|CONDOMINIUM|HOMEOWNERS|HOA)\b', re.I)

_JUDGMENT = {'FJ', 'CFJ'}
_RELEASE = {'RST'}
_CANDIDATE_CAP = 8


def enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get(ENV_FLAG) or '').strip().lower() in _ON


def normalize_case(raw):
    """Court case number to AAAA-YY-NNNNNN. Empty when the field is not a case number.

    The daily file writes the same case as 'CACE-26-000001', 'CACE- 26-000001',
    'FMCE26-000123', 'COCE 26000014', and '-NOT SHOWN'. Spaces and a 4-digit year
    are folded. Anything else (OTHER, UNKNOWN, a truncated digit run) is empty,
    so two filings are joined only when both numbers actually parsed.
    """
    s = str(raw or '').upper().replace('\u2014', '-').replace('\u2013', '-').replace('/', '-')
    if 'NOT SHOWN' in s or s.strip() in _NOT_CASE:
        return ''
    compact = re.sub(r'\s+', '', s)
    four = re.fullmatch(r'([A-Z]{4})-?((?:19|20)\d{2})-?(\d{1,6})', compact)
    if four:
        prefix, year, num = four.group(1), four.group(2)[2:], four.group(3)
        return '%s-%s-%s' % (prefix, year, num.zfill(6))
    two = re.fullmatch(r'([A-Z]{4})-?(\d{2})-?(\d{1,6})', compact)
    if not two:
        return ''
    prefix, year, num = two.groups()
    return '%s-%s-%s' % (prefix, year, num.zfill(6))


def _iso_date(compact, slash):
    s = (compact or '').strip()
    if re.fullmatch(r'\d{8}', s):
        try:
            datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return ''
        return '%s-%s-%s' % (s[:4], s[4:6], s[6:8])
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{4})', (slash or '').strip())
    if not m:
        return ''
    try:
        got = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return ''
    return got.isoformat()


def _stop_words():
    """The same stop list lp_resolve2.toks uses, loaded on first match so this module
    stays importable without that module's network client."""
    if _stop_words.words is None:
        import lp_resolve2 as R2
        _stop_words.words = R2._STOP
    return _stop_words.words


_stop_words.words = None


def ordered_tokens(name):
    """Same token rule as lp_resolve2.toks, kept in order so the given name is the first one."""
    cleaned = _SUFFIX_END.sub('', str(name or ''))
    words = re.sub(r'[^A-Z ]', ' ', cleaned.upper()).split()
    stop = _stop_words()
    return [t for t in words if len(t) > 2 and t not in stop]


def _institution(name):
    return bool(_GOV_RE.search(name or '') or _LENDER_RE.search(name or '') or _HOA_RE.search(name or ''))


def person_parts(name):
    """(surnames, first given name, all given names) for a 'LAST, FIRST MIDDLE' person.

    Entities have no comma in this feed. A single initial is not a person: matching
    'DOE, J' would guess. Returns None when the name should not be searched on the roll.
    """
    if not name or _institution(name) or _ENTITY_RE.search(name):
        return None
    raw = _SUFFIX_END.sub('', ' '.join(str(name).split()))
    if ',' not in raw:
        return None
    last, _, rest = raw.partition(',')
    surnames = ordered_tokens(last)
    given = ordered_tokens(rest)
    if not surnames or not given:
        return None
    return set(surnames), given[0], set(given)


def owner_slots(owner):
    """Split a roll owner into people. 'DOE JANE & DOE JOHN' is two slots, not one blended name."""
    s = ' '.join(str(owner or '').upper().split()).replace('&W', ' & ').replace('&H', ' & ')
    out = []
    for part in re.split(r'\s*(?:&|;|\bAND\b)\s*', s):
        part = re.sub(r'^(?:WF|HUSBAND|WIFE|HUS|[WH])\b', '', part).strip(' ,')
        if part:
            out.append(part)
    return out


def _agreement(parts, slot):
    """exact / close / weak, or None when the surname and given name are not both there.

    exact: the slot says the same significant tokens.
    close: one extra token on the roll (a middle name the filing left off, or the reverse).
    weak: surname and given name hit, but the roll adds two or more tokens. Not a parcel.
    A different given name is None, not weak — that is a different person.
    """
    surnames, first, given = parts
    slot_tokens = set(ordered_tokens(slot))
    if not surnames.issubset(slot_tokens) or first not in slot_tokens:
        return None
    # A middle name on only one side is still the same person when this is the only
    # parcel. Two differences (Jane Ann vs Jane Marie) are a different person or a guess.
    drift = len(slot_tokens - surnames - given) + len(given - slot_tokens)
    if drift == 0:
        return 'exact'
    if drift == 1:
        return 'close'
    return 'weak'


def classify(case, plaintiffs):
    """lender-foreclosure / hoa-condo / family / government / other.

    FMCE is the family court. It wins over the plaintiff name: those lis pendens are
    divorce and partition, not foreclosures. Otherwise the plaintiff side is a lender,
    then a government, then an association. What is left (a person, a private company)
    stays other — quiet title and a private note cannot be told apart from this feed.
    """
    prefix = (case or '').split('-')[0]
    if prefix == 'FMCE':
        return 'family'
    text = plaintiffs or []
    if any(_LENDER_RE.search(p or '') for p in text):
        return 'lender-foreclosure'
    if any(_GOV_RE.search(p or '') for p in text):
        return 'government'
    if any(_HOA_RE.search(p or '') for p in text):
        return 'hoa-condo'
    return 'other'


def join_parties(rows):
    """Plaintiff and defendants for one instrument.

    D is the plaintiff and R the defendants, except when the sides were filed backwards:
    every direct name is a person and every reverse name that has a role is an
    institution (the association or the lender sits on R, and no person does). A bank
    plaintiff with the association listed as a defendant is the normal shape and is
    not flipped. Both sides people, or both sides institutions, are left as filed.
    """
    ordered = sorted(rows or [], key=lambda r: (r.get('seq') or 0, r.get('ord') or 0))
    direct = [r['name'] for r in ordered if r.get('side') == 'D']
    reverse = [r['name'] for r in ordered if r.get('side') == 'R']

    def roles(names):
        out = set()
        for n in names:
            if person_parts(n):
                out.add('person')
            elif _institution(n):
                out.add('institution')
        return out

    d_roles, r_roles = roles(direct), roles(reverse)
    flipped = ('person' in d_roles and 'institution' not in d_roles
               and 'institution' in r_roles and 'person' not in r_roles)
    if flipped:
        plaintiffs, defendants = reverse, direct
    else:
        plaintiffs, defendants = direct, reverse
    return {'plaintiffs': plaintiffs, 'defendants': defendants, 'flipped': flipped}


class StateRoll(object):
    """Owner-name index over FDOR roll rows. One parcel stays one parcel."""

    def __init__(self, rows):
        self.rows = []
        self.by_token = {}
        seen = {}
        for raw in rows or []:
            parcel = _norm_parcel(raw.get('parcel') or raw.get('PARCEL_ID'))
            owner = ' '.join(str(raw.get('owner') or raw.get('OWN_NAME') or '').split())
            if not parcel or not owner:
                continue
            addr = ' '.join(p for p in (
                str(raw.get('address') or raw.get('PHY_ADDR1') or '').strip(),
                str(raw.get('address2') or raw.get('PHY_ADDR2') or '').strip()) if p)
            city = ' '.join(str(raw.get('city') or raw.get('PHY_CITY') or '').split())
            zipc = ' '.join(str(raw.get('zip') or raw.get('PHY_ZIPCD') or '').split())
            if parcel not in seen:
                rec = {'parcel': parcel, 'owner': owner, 'address': addr, 'city': city,
                       'zip': zipc, 'slots': owner_slots(owner)}
                seen[parcel] = rec
                self.rows.append(rec)
                idx = len(self.rows) - 1
                for slot in rec['slots']:
                    for tok in ordered_tokens(slot):
                        self.by_token.setdefault(tok, []).append(idx)
            else:
                rec = seen[parcel]
                for slot in owner_slots(owner):
                    if slot not in rec['slots']:
                        rec['slots'].append(slot)
                        for tok in ordered_tokens(slot):
                            idx = self.rows.index(rec)
                            self.by_token.setdefault(tok, []).append(idx)

    def candidates(self, parts):
        surnames = parts[0]
        if not surnames:
            return []
        ids = []
        for tok in surnames:
            ids.extend(self.by_token.get(tok, []))
        out, have = [], set()
        for i in ids:
            if i not in have:
                have.add(i)
                out.append(self.rows[i])
        return out


def _norm_parcel(value):
    return re.sub(r'[\s-]+', '', str(value or '').strip().upper())


def match_names(defendants, roll):
    """Parcel for these defendants. clean only when one parcel agrees and the name is not weak.

    Two parcels, or one parcel whose roll name adds two or more tokens, is needs-review
    and the parcel field stays empty. A shared surname with a different given name is
    not a candidate. roll is None when no roll was configured: every lead is none,
    not a guess from a previous run.
    """
    empty = {'match': 'none', 'match_note': 'no-roll', 'parcel': '', 'address': '',
             'city': '', 'zip': '', 'roll_owner': '', 'candidates': [], 'candidate_count': 0}
    if roll is None:
        return empty
    people = []
    for name in defendants or []:
        parts = person_parts(name)
        if parts:
            people.append((name, parts))
    if not people:
        empty['match_note'] = 'no-person'
        return empty
    hits = {}
    rank = {'weak': 1, 'close': 2, 'exact': 3}
    for _name, parts in people:
        for rec in roll.candidates(parts):
            best, best_slot = None, ''
            for slot in rec['slots']:
                kind = _agreement(parts, slot)
                if kind and (best is None or rank[kind] > rank[best]):
                    best, best_slot = kind, slot
            if not best:
                continue
            prev = hits.get(rec['parcel'])
            if prev is None or rank[best] > rank[prev['strength']]:
                hits[rec['parcel']] = {
                    'parcel': rec['parcel'], 'address': rec['address'], 'city': rec['city'],
                    'zip': rec['zip'], 'owner': best_slot or rec['owner'], 'strength': best}
    if not hits:
        empty['match_note'] = 'no-name-hit'
        return empty
    chosen = list(hits.values())
    chosen.sort(key=lambda r: r['parcel'])
    if len(chosen) > 1 or chosen[0]['strength'] == 'weak':
        note = 'several-parcels' if len(chosen) > 1 else 'weak-name'
        shown = [{k: r[k] for k in ('parcel', 'address', 'city', 'zip', 'owner')}
                 for r in chosen[:_CANDIDATE_CAP]]
        return {'match': 'needs-review', 'match_note': note, 'parcel': '', 'address': '',
                'city': '', 'zip': '', 'roll_owner': '', 'candidates': shown,
                'candidate_count': len(chosen)}
    one = chosen[0]
    return {'match': 'clean', 'match_note': 'unique-parcel', 'parcel': one['parcel'],
            'address': one['address'], 'city': one['city'], 'zip': one['zip'],
            'roll_owner': one['owner'], 'candidates': [], 'candidate_count': 1}


def load_state_roll(path):
    """Read the local NAL CSV (latin-1, header row). PARCEL_ID and OWN_NAME are required.

    Address columns match fl_cadastral when they are present. A file that does not
    have those two headers raises, and the caller treats the roll as unreadable
    rather than matching against the wrong columns.
    """
    rows = []
    with open(path, encoding='latin-1', newline='') as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError('roll has no header')
        header = {}
        for h in reader.fieldnames:
            key = (h or '').strip().lstrip('\ufeff').upper()
            if key:
                header[key] = h
        parcel_col = header.get('PARCEL_ID') or header.get('PARCEL') or header.get('FOLIO')
        owner_col = header.get('OWN_NAME') or header.get('OWNER_NAME') or header.get('OWNER')
        if not parcel_col or not owner_col:
            raise ValueError('roll is missing PARCEL_ID or OWN_NAME')
        addr1 = header.get('PHY_ADDR1') or header.get('SITE_ADDR') or header.get('SITE_ADDRESS')
        addr2 = header.get('PHY_ADDR2')
        city = header.get('PHY_CITY') or header.get('SITE_CITY')
        zipc = header.get('PHY_ZIPCD') or header.get('PHY_ZIP') or header.get('SITE_ZIP')
        for rec in reader:
            rows.append({
                'parcel': rec.get(parcel_col) or '',
                'owner': rec.get(owner_col) or '',
                'address': (rec.get(addr1) or '') if addr1 else '',
                'address2': (rec.get(addr2) or '') if addr2 else '',
                'city': (rec.get(city) or '') if city else '',
                'zip': (rec.get(zipc) or '') if zipc else '',
            })
    return StateRoll(rows)


def read_pipe(path):
    """Rows of one daily file. latin-1, so a cp437-era byte never aborts the day. CRLF stripped."""
    out = []
    with open(path, encoding='latin-1', newline='') as fh:
        for line in fh:
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            out.append(line.split('|'))
    return out


def parse_doc(fields):
    if len(fields) < 19:
        return None
    inst = (fields[0] or '').strip()
    typ = (fields[4] or '').strip().upper()
    if not inst.isdigit() or not typ:
        return None
    case_raw = (fields[18] or '').strip() if len(fields) > 18 else ''
    return {
        'instrument': inst,
        'type': typ,
        'recorded': _iso_date((fields[1] or '').strip(), (fields[2] or '').strip() if len(fields) > 2 else ''),
        'case_raw': case_raw,
        'case': normalize_case(case_raw),
        'legal': fields[9] if len(fields) > 9 else '',
    }


def parse_name(fields, order):
    if len(fields) < 4:
        return None
    inst = (fields[0] or '').strip()
    name = ' '.join((fields[1] or '').split())
    side = (fields[2] or '').strip().upper()
    if not inst.isdigit() or not name or side not in ('D', 'R'):
        return None
    try:
        seq = int((fields[3] or '').strip() or '0')
    except ValueError:
        seq = 0
    return {'instrument': inst, 'name': name, 'side': side, 'seq': seq, 'ord': order}


def parse_link(fields):
    """Referenced instrument is field 5 only. The display text is not a join key."""
    if len(fields) < 6:
        return None
    inst = (fields[0] or '').strip()
    if not inst.isdigit():
        return None
    ref = (fields[5] or '').strip()
    if not ref.isdigit():
        ref = ''
    return {'instrument': inst, 'ref': ref}


def load_directory(directory):
    """Every doc, name, and link file in one folder. Order on disk does not matter."""
    docs, names, links = [], {}, []
    doc_seen = set()
    bad = 0
    files = 0
    try:
        listed = os.listdir(directory)
    except OSError:
        listed = []
    for name in listed:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        kind = ''
        if _DOC_RE.match(name):
            kind = 'doc'
        elif _NME_RE.match(name):
            kind = 'nme'
        elif _LNK_RE.match(name):
            kind = 'lnk'
        else:
            continue
        files += 1
        try:
            rows = read_pipe(path)
        except OSError:
            bad += 1
            continue
        if kind == 'doc':
            for fields in rows:
                rec = parse_doc(fields)
                if rec is None or rec['instrument'] in doc_seen:
                    bad += 1
                    continue
                doc_seen.add(rec['instrument'])
                docs.append(rec)
        elif kind == 'nme':
            for i, fields in enumerate(rows):
                rec = parse_name(fields, i)
                if rec is None:
                    bad += 1
                    continue
                names.setdefault(rec['instrument'], []).append(rec)
        else:
            for fields in rows:
                rec = parse_link(fields)
                if rec is None:
                    bad += 1
                    continue
                links.append(rec)
    return docs, names, links, {'files': files, 'bad_rows': bad}


def _closings(docs, links):
    judged, released_cases, release_instruments = set(), set(), set()
    for doc in docs:
        if doc['type'] in _JUDGMENT and doc['case']:
            judged.add(doc['case'])
        elif doc['type'] in _RELEASE:
            release_instruments.add(doc['instrument'])
            if doc['case']:
                released_cases.add(doc['case'])
    released_lps = set()
    for link in links:
        if link['instrument'] in release_instruments and link['ref']:
            released_lps.add(link['ref'])
    return judged, released_cases, released_lps


def _why_closed(lp, judged, released_cases, released_lps):
    if lp['case'] and lp['case'] in judged:
        return 'judgment'
    if lp['instrument'] in released_lps:
        return 'release'
    if lp['case'] and lp['case'] in released_cases:
        return 'release'
    return ''


def _lead_key(lp):
    if lp['case']:
        return ('case', lp['case'])
    return ('inst', lp['instrument'])


def _sort_lps(lps):
    def key(d):
        try:
            n = int(d['instrument'])
        except ValueError:
            n = 0
        return (d.get('recorded') or '', n)
    return sorted(lps, key=key, reverse=True)


def build(directory, roll=None):
    """Open lis pendens leads and a counts-only stats dict. roll is a StateRoll or None."""
    docs, names, links, loaded = load_directory(directory)
    judged, released_cases, released_lps = _closings(docs, links)
    lps = [d for d in docs if d['type'] == 'LP']
    groups = {}
    dropped_j = dropped_r = 0
    for lp in lps:
        key = _lead_key(lp)
        why = _why_closed(lp, judged, released_cases, released_lps)
        if why:
            groups.setdefault(key, {'open': [], 'why': set()})
            groups[key]['why'].add(why)
            continue
        groups.setdefault(key, {'open': [], 'why': set()})
        groups[key]['open'].append(lp)
    leads = []
    for _key, bucket in groups.items():
        if not bucket['open']:
            if 'judgment' in bucket['why']:
                dropped_j += 1
            else:
                dropped_r += 1
            continue
        ordered = _sort_lps(bucket['open'])
        primary = ordered[0]
        party = {'plaintiffs': [], 'defendants': [], 'flipped': False}
        for lp in ordered:
            rows = names.get(lp['instrument']) or []
            if rows:
                party = join_parties(rows)
                break
        kind = classify(primary['case'], party['plaintiffs'])
        matched = match_names(party['defendants'], roll)
        lead = {
            'county': 'BROWARD',
            'source': 'broward-or-feed',
            'outreach': 'held',
            'instrument': primary['instrument'],
            'instruments': [d['instrument'] for d in ordered],
            'recorded': primary['recorded'],
            'case': primary['case'],
            'case_raw': primary['case_raw'],
            'plaintiff': '; '.join(party['plaintiffs']),
            'defendants': list(party['defendants']),
            'kind': kind,
            'sides_flipped': bool(party['flipped']),
            'match': matched['match'],
            'match_note': matched['match_note'],
            'parcel': matched['parcel'],
            'address': matched['address'],
            'city': matched['city'],
            'zip': matched['zip'],
            'roll_owner': matched['roll_owner'],
            'candidates': matched['candidates'],
            'candidate_count': matched['candidate_count'],
        }
        leads.append(lead)
    leads.sort(key=lambda r: (r['recorded'], r['case'], r['instrument']))
    kinds = {}
    matches = {'clean': 0, 'needs-review': 0, 'none': 0}
    flipped = no_case = 0
    for lead in leads:
        kinds[lead['kind']] = kinds.get(lead['kind'], 0) + 1
        matches[lead['match']] = matches.get(lead['match'], 0) + 1
        flipped += 1 if lead['sides_flipped'] else 0
        no_case += 1 if not lead['case'] else 0
    dates = sorted({d['recorded'] for d in docs if d.get('recorded')})
    stats = {
        'ok': True,
        'source': 'broward-or-feed',
        'outreach': 'held',
        'directory': 'ok',
        'files': loaded['files'],
        'bad_rows': loaded['bad_rows'],
        'documents': len(docs),
        'lis_pendens': len(lps),
        'open_leads': len(leads),
        'dropped_judgment': dropped_j,
        'dropped_release': dropped_r,
        'no_case': no_case,
        'flipped': flipped,
        'by_kind': kinds,
        'match': matches,
        'recording_dates': dates,
        'roll': 'loaded' if roll is not None else 'missing',
        'roll_rows': len(roll.rows) if roll is not None else 0,
    }
    return leads, stats


def _assert_outside_repo(path):
    repo = os.path.normcase(os.path.abspath(HERE))
    dest = os.path.normcase(os.path.abspath(path))
    if dest == repo or dest.startswith(repo + os.sep):
        raise ValueError('refusing to write the Broward feed inside the repo')


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, path)


def _dealflow_dir(env):
    return (env.get('DEALFLOW_DIR') or '').strip() or os.path.join(os.path.expanduser('~'), 'DEALFLOW')


def _read_config(env):
    path = (env.get(ENV_CONFIG) or '').strip()
    if not path:
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_inputs(env, directory=None, roll_path=None):
    cfg = _read_config(env)
    directory = directory or (env.get(ENV_DIR) or '').strip() or str(cfg.get('dir') or '').strip()
    roll_path = roll_path or (env.get(ENV_ROLL) or '').strip() or str(cfg.get('roll') or '').strip()
    return directory, roll_path


def run(env=None, directory=None, roll_path=None, out_dir=None):
    """Read the folder, write feed.json and status.json. Returns counts only, no party names."""
    env = os.environ if env is None else env
    directory, roll_path = resolve_inputs(env, directory, roll_path)
    out_dir = out_dir or os.path.join(_dealflow_dir(env), 'broward_or_feed')
    _assert_outside_repo(out_dir)
    if not directory or not os.path.isdir(directory):
        print('BROWARD OR FEED: directory not set or not found')
        return {'ok': False, 'directory': 'missing', 'outreach': 'held', 'open_leads': 0,
                'source': 'broward-or-feed'}
    roll = None
    roll_state = 'missing'
    if roll_path:
        try:
            roll = load_state_roll(roll_path)
            roll_state = 'loaded'
        except (OSError, ValueError, csv.Error):
            roll = None
            roll_state = 'unreadable'
    leads, stats = build(directory, roll)
    stats['roll'] = roll_state
    if roll_state != 'loaded':
        stats['roll_rows'] = 0
    _write_json(os.path.join(out_dir, 'feed.json'), leads)
    _write_json(os.path.join(out_dir, 'status.json'), stats)
    m = stats['match']
    print('BROWARD OR FEED: %d open lead(s), dropped %d judgment / %d release, '
          'match clean %d / review %d / none %d'
          % (stats['open_leads'], stats['dropped_judgment'], stats['dropped_release'],
             m.get('clean', 0), m.get('needs-review', 0), m.get('none', 0)))
    return stats


def run_safe(env=None):
    """Collector hook. Counts only. Never raises into the lis pendens sweep."""
    try:
        if not enabled(env):
            return None
        stats = run(env=env)
        return {'ok': bool(stats.get('ok')), 'open_leads': int(stats.get('open_leads') or 0)}
    except Exception as exc:
        print('BROWARD OR FEED: not built (%s)' % type(exc).__name__)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build Broward leads from a folder of Official Records daily files.')
    ap.add_argument('--dir', default='', help='folder of MM-DD-YYYYdoc-ver.txt and the matching nme/lnk files')
    ap.add_argument('--roll', default='', help='FDOR NAL CSV (PARCEL_ID, OWN_NAME, PHY_ADDR1, PHY_CITY, PHY_ZIPCD)')
    ap.add_argument('--out', default='', help='output folder (default: DEALFLOW_DIR/broward_or_feed)')
    ap.add_argument('--enable', action='store_true', help='run even without DEALFLOW_BROWARD_OR_FEED=1')
    args = ap.parse_args(argv)
    env = os.environ
    if not (args.dir or args.enable or enabled(env)):
        print('Broward OR feed: off (set DEALFLOW_BROWARD_OR_FEED=1, or pass --dir)')
        return 0
    stats = run(env=env, directory=args.dir or None, roll_path=args.roll or None,
                out_dir=args.out or None)
    return 0 if stats.get('ok') else 2


if __name__ == '__main__':
    sys.exit(main())
