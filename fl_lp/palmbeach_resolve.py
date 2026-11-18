#!/usr/bin/env python
"""palmbeach_resolve.py -- the property behind a Palm Beach filing, from the owner's NAME. $0.

WHY (2026-11-16). Palm Beach filings now come from the court's case search (pb_court_filings.py),
and a court row is a case number, a plaintiff and a defendant's name: no parcel, no legal
description. The chain's Palm Beach address step keys on the recorder's legal text, so all 555
court filings landed on the board with no address and no value. Broward had the same hole and
fl_lp/broward_resolve.py closed it with a name ladder against the property appraiser. This is the
same ladder for Palm Beach against the Florida DOR statewide roll (fl_cadastral, CO_NO=60), which
answers an owner-name query for free with no key, no login and no captcha.

THE RULE (identical to Broward's, and for the same reason: a wrong house is worse than no house)
  * the query only controls RECALL; a parcel counts only when lp_resolve2.owner_agrees says the
    owner of record carries EVERY token of the defendant's name;
  * exactly one such parcel + homestead on the roll  -> 'high'   (the owner lives there)
  * exactly one such parcel, no homestead            -> 'medium' (needsHuman; one-parcel match)
  * several parcels                                   -> 'low' with the candidates listed
  * none                                              -> 'none'
  * a last-name query that returns a full page is too common to trust and is not used.

Writes lp_addresses.json entries in the shape broward_resolve writes ('rung': 'pb-name'). A case
already in the file is never overwritten.

v2 (2026-11-18), Palm Beach rungs asked BEFORE the name:
  * 'pb-pcn'   the parcel number a recorded lis pendens prints ('PCN: 00-42-...'): high when the roll
               has it with a street address; ownerMismatch when the owner of record differs.
  * 'pb-legal' the recorded legal ('B: 4 L: 8 SUB: ...') on the county's PARCEL_INFO layer: high only
               when the owner of record carries the filing's name; one parcel and no name = medium;
               a name hit on a different parcel than the legal's is never taken.
  And for both counties: a non-owner party in the owner slot (association, lender, unknown heirs) is not
  searched; the person inside an unknown-heirs caption is; on a recorded filing with the association in
  the owner slot the other party is, when that one is a person; both name orders are tried; a 'none'
  written by v1 is asked again at once.

    python fl_lp/palmbeach_resolve.py              resolve every unresolved Palm Beach row
    python fl_lp/palmbeach_resolve.py --limit 50
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import fl_cadastral as FC                              # noqa: E402
from lp_resolve2 import owner_agrees, toks             # noqa: E402  (the wrong-Garcia guard)

SRC = os.path.join(REPO, 'lis_pendens.json')
OUT = os.path.join(REPO, 'lp_addresses.json')
CO_NO = 60                # Palm Beach; the default for every function below
# County numbers on the FL DOR roll. Miami-Dade was added 2026-11-16 for the case-walk rows (md_case_walk.py),
# which carry a defendant name and no legal description, exactly like Palm Beach court filings.
COUNTIES = {'PALM BEACH': (60, 'pb-name'), 'MIAMI-DADE': (53, 'md-name')}
RUNGS = ('pb-name', 'md-name', 'pb-pcn', 'pb-legal')
# v2 (2026-11-18): the parcel number printed in a recorded lis pendens, non-owner parties skipped, and
# both name orders tried. A 'none' written by v1 is asked again at once instead of after RETRY_NONE_DAYS.
RESOLVER_V = 2
PAGE = 60                 # rows asked per query; a full page on a bare last name = too common
PAUSE_S = 0.35
ENTITY_RE = re.compile(r'\b(LLC|INC|CORP|TRUST|ASSN|ASSOC|CO|LP|LTD|DMCC|PLLC|BANK|ESTATE)\b')
RETRY_NONE_DAYS = 30      # a name the roll did not carry is asked again after this (new deeds)
SUFFIX = ('JR', 'SR', 'II', 'III', 'IV')
# A party who is not the owner of record: the association suing, a lender, a government body, a tenant.
# MEASURED 2026-11-18: 199 Palm Beach rows carried one of these in `owner` (an association in most of
# them, "UNKNOWN HEIRS ..." in the rest), so the roll was searched for the plaintiff's own name.
NONOWNER_RE = re.compile(r'\b(ASSOCIATION|ASSN|ASSOC|HOMEOWNERS?|CONDOMINIUM|CONDO|MASTER|PROPERTY OWNERS|'
                         r'COMMUNITY DEVELOPMENT|BANK|MORTGAGE|LOAN|SAVINGS|CREDIT UNION|SERVICING|TRUSTEE|'
                         r'SECRETARY|UNITED STATES|STATE OF FLORIDA|CITY OF|COUNTY|TENANT|SPOUSE|'
                         r'UNKNOWN|HEIRS|DEVISEES|GRANTEES|LIENORS|CREDITORS|BENEFICIARIES)\b')
PCN_RE = re.compile(r'PCN:\s*(\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{3}-\d{4})')


def agrees(lp_owner, roll_owner):
    """owner_agrees, plus one thing the court makes necessary: the court writes a middle NAME
    ('SMITH, JOHN MICHAEL') where the roll writes an INITIAL ('SMITH JOHN M'). A middle name whose
    initial stands as a lone letter on the roll counts as carried. Last and first name must still
    both be there in full, so a shared surname is never agreement."""
    if owner_agrees(lp_owner, roll_owner, ''):
        return True
    o = ' '.join(str(lp_owner or '').upper().split())
    if ',' not in o or ENTITY_RE.search(o):
        return False
    last, given = o.split(',', 1)
    given = re.sub(r'[^A-Z ]', ' ', given).split()
    last_t = toks(last)
    if not last_t or not given or len(given[0]) < 3:
        return False
    roll_words = re.sub(r'[^A-Z ]', ' ', str(roll_owner or '').upper()).split()
    roll_t = toks(roll_owner)
    if not last_t.issubset(roll_t) or given[0] not in roll_t:
        return False
    initials = {w for w in roll_words if len(w) == 1}
    for mid in given[1:]:
        if len(mid) <= 2:
            continue                                   # an initial or a suffix on the court side
        if mid not in roll_t and mid[0] not in initials:
            return False
    return True


def _sql(s):
    return re.sub(r'[^A-Z0-9 &\-]', ' ', str(s or '').upper()).strip().replace("'", "''")


def queries(owner):
    """Roll-shaped LIKE prefixes, widest recall first-name-first. The DOR roll writes people as
    'LAST FIRST M' with no comma.
      'MARTIN, SHAWN E'    -> ['MARTIN SHAWN', 'MARTIN']
      'PAULA ANNE DRALUCK' -> ['DRALUCK PAULA', 'DRALUCK']
      'K HOLDING LLC'      -> ['K HOLDING LLC', 'K HOLDING']"""
    o = ' '.join(str(owner or '').upper().split())
    if not o:
        return []
    if ENTITY_RE.search(o):
        base = re.sub(r'\b(LLC|INC|CORP|LTD|PLLC)\.?\s*$', '', o).strip(' ,')
        qs = [o.replace(',', ' '), base.replace(',', ' ')]
    elif ',' in o:
        last, given = o.split(',', 1)
        first = (given.split() or [''])[0]
        qs = [('%s %s' % (last.strip(), first)).strip(), last.strip()]
    else:
        parts = o.split()
        while len(parts) > 2 and parts[-1].strip('.') in SUFFIX:
            parts = parts[:-1]                         # 'JOHN SMITH JR': the surname is SMITH, not JR
        # FIRST LAST is the usual court order, but some filings write LAST FIRST with no comma, so
        # the name as written is asked second, before the bare surname.
        qs = (['%s %s' % (parts[-1], parts[0]), ' '.join(parts[:2]), parts[-1]]
              if len(parts) > 1 else [o])
    out = []
    for q in qs:
        q = ' '.join(_sql(q).split())
        if len(q) >= 3 and q not in out:
            out.append(q)
    return out


def is_nonowner(name):
    """True for a party who is not the owner of record (association, lender, unknown heirs ...)."""
    return bool(NONOWNER_RE.search(' '.join(str(name or '').upper().split())))


def named_in_blob(name):
    """'UNKNOWN HEIRS ... OF THE ESTATE OF PAT Q TESTER, DECEASED' -> 'PAT Q TESTER', else ''.
    The person whose heirs are sued owned the parcel; that is the name the roll still carries."""
    o = ' '.join(str(name or '').upper().replace(',', ' ').split())
    if not re.search(r'\b(UNKNOWN|HEIRS|ESTATE)\b', o) or ' OF ' not in ' %s ' % o:
        return ''
    tail = re.split(r'\bOF\b', o)[-1]
    tail = re.sub(r'\b(THE|ESTATE|DECEASED|A/K/A|AKA|ET AL|AL)\b', ' ', tail)
    tail = ' '.join(re.sub(r'[^A-Z &\-]', ' ', tail).split())
    if len(toks(tail)) < 2 or is_nonowner(tail) or ENTITY_RE.search(tail):
        return ''
    return tail


def candidate_owners(rec):
    """Names to look up on the roll, best first: the owner field unless it is a non-owner party; the
    person named inside an unknown-heirs caption; and, on a RECORDED lis pendens whose owner slot holds
    the association or lender, the other party when that one is a person (the recorder's grantor and
    grantee columns come through swapped on those rows)."""
    owner = ' '.join(str(rec.get('owner') or '').split())
    out = []
    if owner and not is_nonowner(owner):
        out.append(owner)
    blob = named_in_blob(owner)
    if blob:
        out.append(blob)
    other = ' '.join(str(rec.get('plaintiff') or '').split())
    if (owner and is_nonowner(owner) and rec.get('src') != 'court' and other
            and not is_nonowner(other) and not ENTITY_RE.search(other.upper()) and len(toks(other)) >= 2):
        out.append(other)
    seen, uniq = set(), []
    for n in out:
        if n.upper() not in seen:
            seen.add(n.upper())
            uniq.append(n)
    return uniq


def pcn_of(legal):
    """The one parcel number a recorded Palm Beach lis pendens prints ('PCN: 00-42-47-17-19-000-1730'),
    as the roll's 17-digit PARCEL_ID, or '' when it prints none or more than one."""
    got = {re.sub(r'\D', '', m) for m in PCN_RE.findall(str(legal or ''))}
    return got.pop() if len(got) == 1 else ''


# ---- the legal description, against the county's own parcel layer (2026-11-18) --------------------
# The DOR roll cannot be searched by legal (a LIKE on S_LEGAL times out after 2 minutes, no index), so
# the 346 recorded Palm Beach filings that carry "B: 4 L: 8 SUB: SAMPLE ESTATES PCN:" but no parcel
# number fell through to the owner name and mostly found nothing. The county's PARCEL_INFO layer answers
# a subdivision-name query in seconds, keyless, and the Palm Beach parcel number itself encodes the
# block and lot: CC-RR-TT-SS-DD-BBB-LLLL, lot 173 -> LLLL '1730', block 4 -> BBB '004' (checked live on a
# filing that printed both its legal and its parcel number: the pattern returned that parcel and no other).
PB_GIS = 'https://gis.pbcgov.org/arcgis/rest/services/Parcels/PARCEL_INFO/MapServer/4/query'
PB_GIS_FIELDS = ('PARCEL_NUMBER,SUBDIV_NAME,OWNER_NAME1,OWNER_NAME2,SITE_ADDR_STR,CITYNAME,ZIP1,'
                 'HMSTD_FLG,TOTAL_MARKET,PADDR1,PADDR2,STATE')
_NUMWORD = {'ONE': '1', 'TWO': '2', 'THREE': '3', 'FOUR': '4', 'FIVE': '5', 'SIX': '6', 'SEVEN': '7',
            'EIGHT': '8', 'NINE': '9', 'TEN': '10', 'FIRST': '1', 'SECOND': '2', 'THIRD': '3'}
_SUB_NOISE = {'NO', 'THE', 'OF', 'AT', 'A', 'AN', 'SUB', 'SUBDIVISION', 'PLAT', 'PL', 'REPLAT', 'PUD', 'POD',
              'SECTION', 'SEC', 'PHASE', 'PH', 'CONDOMINIUM', 'CONDO', 'COND', 'UNIT', 'IN', 'ADD', 'ADDITION'}


def parse_pb_legal(legal):
    """Every 'B: x L: y U: z SUB: name PCN:' group in a recorded Palm Beach legal ->
    [{'sub', 'blk', 'lot', 'unit'}]. The blank template ('L: BLK: U: SUB: PH: ...') is skipped."""
    text = ' '.join(str(legal or '').upper().split())
    out, prev = [], 0
    for m in re.finditer(r'\bSUB:\s*(.+?)\s+PCN:', text):
        sub = m.group(1).strip()
        pre = text[prev:m.start()]
        prev = m.end()
        if not sub or re.match(r'^(PH|BLDG|SEC):', sub):
            continue
        f = {}
        for k, v in re.findall(r'(?<![A-Z])(B|L|U):\s*([A-Z0-9\-]+)(?=\s|$)', pre):
            f[k] = v
        out.append({'sub': sub, 'blk': f.get('B', ''), 'lot': f.get('L', ''), 'unit': f.get('U', '')})
    return out


def _sub_words(sub):
    w = re.sub(r'[^A-Z0-9 ]', ' ', str(sub or '').upper().replace('#', ' NO ')).split()
    return [_NUMWORD.get(t, t.lstrip('0') or '0') if t.isdigit() or t in _NUMWORD else t for t in w]


def _sub_numbers(sub):
    return {t for t in _sub_words(sub) if t.isdigit()}


def pcn_pattern(g):
    """The PARCEL_NUMBER LIKE pattern a legal group implies, or '' when its lot / unit is not a plain
    number the parcel number can carry."""
    lot = g.get('lot') or g.get('unit') or ''
    blk = g.get('blk') or ''
    if not lot.isdigit():
        return ''
    n = int(lot)
    if n >= 10000:
        return ''
    tail = ('%03d_' % n) if n <= 999 else '%04d' % n
    if n <= 999 and blk.isdigit() and int(blk) <= 999:
        return '_' * 10 + '%03d' % int(blk) + tail
    return '_' * (17 - len(tail)) + tail


def _gis_query(where, gis=None):
    """County parcel layer -> list of attribute dicts, or None when it did not answer."""
    if gis is not None:
        return gis(where)
    import requests
    for attempt in range(2):
        try:
            r = requests.get(PB_GIS, params={'where': where, 'outFields': PB_GIS_FIELDS, 'returnGeometry': 'false',
                                             'resultRecordCount': 50, 'f': 'json'}, timeout=40)
            j = r.json()
            if 'error' in j:
                return None
            return [f.get('attributes') or {} for f in j.get('features') or []]
        except Exception:
            time.sleep(2 + 2 * attempt)
    return None


def resolve_legal(rec, gis=None):
    """The parcel a recorded Palm Beach legal describes, confirmed by the owner name when there is one.
      one parcel, owner agrees            -> high
      one parcel, no owner name to compare -> medium (needsHuman)
      one parcel, owner disagrees          -> low with the candidate (sold since, or the wrong plat)
      several, exactly one owner agrees    -> high;  several otherwise -> low with candidates
    None when the legal cannot be turned into a query or the layer finds nothing (the name rung runs)."""
    groups = [g for g in parse_pb_legal(rec.get('legal')) if pcn_pattern(g)]
    if len(groups) != 1:
        return None
    g = groups[0]
    words = [t for t in _sub_words(g['sub']) if t not in _SUB_NOISE and not t.isdigit()]
    if not words:
        return None
    prefix = _sql(' '.join(words[:2]))
    got = _gis_query("SUBDIV_NAME LIKE '%s%%' AND PARCEL_NUMBER LIKE '%s'" % (prefix, pcn_pattern(g)), gis)
    if not got:
        return None
    want_nums = _sub_numbers(g['sub'])
    seen, hits = set(), []
    for h in got:
        pid = re.sub(r'\D', '', str(h.get('PARCEL_NUMBER') or ''))
        if not pid or pid in seen:
            continue
        if want_nums and not want_nums.issubset(_sub_numbers(h.get('SUBDIV_NAME'))):
            continue                                   # '# 02' never matches plat 3 of the same name
        seen.add(pid)
        hits.append(dict(h, PARCEL_NUMBER=pid))
    if not hits:
        return None
    names = candidate_owners(rec)

    def owner_of(h):
        return ' '.join(x for x in (h.get('OWNER_NAME1'), h.get('OWNER_NAME2')) if x)

    agree = [h for h in hits if names and any(agrees(n, owner_of(h)) or agrees(n, h.get('OWNER_NAME1') or '')
                                              for n in names)]
    ev = 'county parcel layer: %s, block %s lot/unit %s' % (g['sub'], g['blk'] or '-', g['lot'] or g['unit'])

    def cand(h):
        return {'folio': h['PARCEL_NUMBER'], 'addr': ' '.join(str(h.get('SITE_ADDR_STR') or '').split()),
                'city': str(h.get('CITYNAME') or '').strip().title(), 'owner': owner_of(h).strip()}

    if len(agree) == 1:
        pick, conf, why = agree[0], 'high', ev + ', and the owner of record carries the filing\'s name'
    elif len(hits) == 1 and not names:
        pick, conf, why = hits[0], 'medium', ev + ' (one parcel; no owner name on the filing to confirm it)'
    else:
        return {'confidence': 'low', 'needsHuman': True, 'rung': 'pb-legal', 'candidates': [cand(h) for h in hits[:8]],
                'evidence': ev + (': %d parcels fit' % len(hits) if len(hits) > 1
                                  else ': one parcel fits but its owner of record is not the filing\'s')}
    a1 = ' '.join(str(pick.get('SITE_ADDR_STR') or '').split())
    row = {'folio': pick['PARCEL_NUMBER'], 'addr': a1, 'city': str(pick.get('CITYNAME') or '').strip().title(),
           'zip': str(pick.get('ZIP1') or '').strip()[:5], 'paOwner': owner_of(pick).strip(), 'candidates': [],
           'ownerMismatch': False, 'value': int(float(pick.get('TOTAL_MARKET') or 0)),
           'hs': str(pick.get('HMSTD_FLG') or '').upper() == 'Y', 'rung': 'pb-legal'}
    mail = ' '.join(' '.join(str(pick.get(k) or '') for k in ('PADDR1', 'PADDR2', 'STATE')).split())
    if mail:
        row['mailAddr'] = mail
    if not a1:
        row.update(confidence='low', needsHuman=True, evidence=why + ', but the layer lists no street address')
    else:
        row.update(confidence=conf, needsHuman=conf != 'high', evidence=why)
    return row


def roll_name(prefix, fetch=None, co_no=CO_NO):
    """Parcels in Palm Beach whose owner of record starts with `prefix`. None = the roll did not
    answer (unknown, never 'no such owner')."""
    fetch = fetch or FC._q
    try:
        got = fetch("CO_NO=%d AND OWN_NAME LIKE '%s%%'" % (co_no, prefix), PAGE)
    except Exception:
        return None
    return got if isinstance(got, list) else None


def _site(h):
    return (' '.join(str(h.get('PHY_ADDR1') or '').split()),
            str(h.get('PHY_CITY') or '').strip().title(), str(h.get('PHY_ZIPCD') or '').strip()[:5])


def resolve_pcn(rec, pcn, fetch=None, co_no=CO_NO):
    """The parcel the recorded lis pendens itself names. 'high' when the roll has it with a street
    address: the filing put it on the record, so no name has to pick it. ownerMismatch only when a
    usable owner name was there to compare and the roll owner carries none of them (sold since)."""
    fetch = fetch or FC._q
    try:
        got = fetch("CO_NO=%d AND PARCEL_ID='%s'" % (co_no, pcn), 2)
    except Exception:
        return None
    hits = [h for h in (got or []) if re.sub(r'\D', '', str(h.get('PARCEL_ID') or '')) == pcn]
    if len(hits) != 1:
        return None
    h = hits[0]
    cad = FC._norm(h)
    a1, city, zc = _site(h)
    names = candidate_owners(rec)
    roll_owner = (h.get('OWN_NAME') or '').strip()
    mismatch = bool(names) and not any(agrees(n, roll_owner) for n in names)
    row = {'folio': str(h.get('PARCEL_ID') or '').strip(), 'addr': a1, 'city': city, 'zip': zc,
           'paOwner': roll_owner, 'candidates': [], 'ownerMismatch': mismatch,
           'value': int(cad.get('market_value') or 0), 'hs': bool(cad.get('homestead')), 'rung': 'pb-pcn'}
    mail = ' '.join(str(cad.get('mail_addr') or '').split())
    if mail:
        row['mailAddr'] = mail
    ev = 'recorded lis pendens prints parcel %s' % pcn
    if not a1:
        row.update(confidence='low', needsHuman=True, evidence=ev + ', but the roll lists no street address for it')
    else:
        row.update(confidence='high', needsHuman=False,
                   evidence=ev + ('; the owner of record differs from the filing' if mismatch else ''))
    return row


def resolve_row(rec, fetch=None, pause=PAUSE_S, co_no=CO_NO, rung='pb-name', gis=None):
    """Palm Beach: the parcel number printed in the filing, then its legal description on the county
    layer; then each candidate owner name on the state roll (both counties)."""
    legal_low = None
    if co_no == 60:
        pcn = pcn_of(rec.get('legal'))
        if pcn:
            row = resolve_pcn(rec, pcn, fetch, co_no)
            if row:
                return row
        if rec.get('legal') and (gis is not None or fetch is None):
            row = resolve_legal(rec, gis)
            if row and row.get('confidence') in ('high', 'medium'):
                return row
            legal_low = row
    names = candidate_owners(rec)
    if not names:
        return legal_low or {'confidence': 'none', 'candidates': [],
                             'evidence': 'no owner name to search: the filing names only non-owner parties'}
    best = None
    legal_folios = {c.get('folio') for c in ((legal_low or {}).get('candidates') or [])}
    for n in names:
        res = _resolve_name(dict(rec, owner=n), fetch, pause, co_no, rung)
        if res.get('confidence') != 'none':
            # The filing's legal already points at a parcel. A name hit somewhere else may be the owner's
            # home, not the house being foreclosed: it stands only when it is one of the legal's parcels.
            if legal_folios and res.get('folio') not in legal_folios:
                return dict(legal_low, evidence=legal_low.get('evidence', '') + '; the owner name points at a '
                            'different parcel (%s), so neither is taken' % (res.get('folio') or 'several'))
            return res
        if best is None or res.get('unreachable'):
            best = res
    return legal_low or best


def _resolve_name(rec, fetch=None, pause=PAUSE_S, co_no=CO_NO, rung='pb-name'):
    owner = rec.get('owner') or ''
    qs = queries(owner)
    hits, tried, unreachable = [], [], True
    for i, q in enumerate(qs):
        got = roll_name(q, fetch, co_no)
        tried.append(q)
        if got is None:
            continue
        unreachable = False
        if i > 0 and len(got) >= PAGE:
            # a bare last name that fills the page: the one right parcel may be on a page not read
            if i < len(qs) - 1:
                continue                               # a fuller query is still to come
            return {'confidence': 'none', 'candidates': [],
                    'evidence': 'state roll: %r is too common to search by last name alone' % q}
        if got:
            hits = got
            break
        if pause:
            time.sleep(pause)
    if unreachable:
        return {'confidence': 'none', 'evidence': 'state roll unreachable', 'candidates': [],
                'unreachable': True}
    seen, matches = set(), []
    for h in hits:
        if agrees(owner, h.get('OWN_NAME', '')):
            f = str(h.get('PARCEL_ID') or '').strip()
            if f and f not in seen:
                seen.add(f)
                matches.append(h)
    if not matches:
        return {'confidence': 'none', 'candidates': [],
                'evidence': 'state roll: %d parcel(s) under %s, none carrying every name token'
                            % (len(hits), tried)}
    if len(matches) > 1:
        return {'confidence': 'low', 'needsHuman': True,
                'evidence': 'state roll: %d parcels in the county carry the full name; the name cannot '
                            'pick one' % len(matches),
                'candidates': [{'folio': str(h.get('PARCEL_ID') or ''), 'addr': _site(h)[0],
                                'city': _site(h)[1], 'owner': (h.get('OWN_NAME') or '').strip()}
                               for h in matches[:8]]}
    h = matches[0]
    cad = FC._norm(h)
    a1, city, zc = _site(h)
    row = {'folio': str(h.get('PARCEL_ID') or '').strip(), 'addr': a1, 'city': city, 'zip': zc,
           'paOwner': (h.get('OWN_NAME') or '').strip(), 'candidates': [], 'ownerMismatch': False,
           'value': int(cad.get('market_value') or 0), 'hs': bool(cad.get('homestead')),
           'rung': rung}
    mail = ' '.join(str(cad.get('mail_addr') or '').split())
    if mail:
        row['mailAddr'] = mail
    ev = 'state roll: exactly one parcel in the county carries every token of %r' % owner
    if not a1:
        row.update(confidence='low', needsHuman=True,
                   evidence=ev + ', but the roll lists no street address for it (vacant land?)')
        return row
    if row['hs']:
        row.update(confidence='high', needsHuman=False,
                   evidence=ev + ' and it is HOMESTEAD (the owner lives at the parcel)')
    else:
        row.update(confidence='medium', needsHuman=True,
                   evidence=ev + ' (no homestead: absentee or second home)')
    return row


def _due(prev_entry, today):
    """A Palm Beach case is (re)asked when it has no entry, or a 'none' older than the retry wait."""
    if not isinstance(prev_entry, dict):
        return True
    if prev_entry.get('rung') not in RUNGS and prev_entry.get('confidence') in ('high', 'medium', 'low'):
        return False                                   # another rung settled it: never overwritten
    if prev_entry.get('confidence') != 'none':
        return False
    if prev_entry.get('unreachable') or int(prev_entry.get('v') or 1) < RESOLVER_V:
        return True
    try:
        import datetime
        age = (today - datetime.date.fromisoformat(str(prev_entry.get('asked') or '')[:10])).days
    except ValueError:
        return True
    return age >= RETRY_NONE_DAYS


def run(limit=0, src=SRC, out=OUT, fetch=None, pause=PAUSE_S, today=None, say=print, county='PALM BEACH'):
    import datetime
    today = today or datetime.date.today()
    co_no, rung = COUNTIES[county]
    recs = [r for r in json.load(open(src, encoding='utf-8'))
            if isinstance(r, dict) and (r.get('county') or 'MIAMI-DADE').upper() == county
            and r.get('case') and r.get('owner')
            # Miami-Dade rows that came off the recorder carry a legal description and are placed by
            # lp_resolve; only the case-walk rows (no legal, src court-walk) are this resolver's
            and (county != 'MIAMI-DADE' or (r.get('src') == 'court-walk' and not r.get('legal')))]
    prev = {}
    if os.path.exists(out):
        try:
            prev = json.load(open(out, encoding='utf-8'))
        except Exception:
            prev = {}
    todo = [r for r in recs if _due(prev.get(r['case']), today)]
    if limit:
        todo = todo[:limit]
    say('%d %s LP rows, %d to resolve by owner name' % (len(recs), county, len(todo)))
    counts, dead = {}, 0
    for i, rec in enumerate(todo, 1):
        res = resolve_row(rec, fetch=fetch, pause=pause, co_no=co_no, rung=rung)
        if res.get('unreachable'):
            dead += 1
            if dead >= 8:
                say('  state roll unreachable 8 times in a row: stopping, nothing guessed')
                break
        else:
            dead = 0
        res.update({'case': rec['case'], 'lpOwner': rec.get('owner') or '',
                    'legal': rec.get('legal') or '', 'county': county,
                    'asked': today.isoformat(), 'v': RESOLVER_V})
        prev[rec['case']] = res
        counts[res['confidence']] = counts.get(res['confidence'], 0) + 1
        if i % 25 == 0 or i == len(todo):
            say('  [%d/%d] %s' % (i, len(todo), counts))
            tmp = out + '.tmp'
            json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, out)
        if pause:
            time.sleep(pause)
    tmp = out + '.tmp'
    json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, out)
    say('DONE: %s -> lp_addresses.json (merged)' % counts)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--county', default='palm-beach', choices=['palm-beach', 'miami'])
    a = ap.parse_args()
    run(limit=a.limit, county={'palm-beach': 'PALM BEACH', 'miami': 'MIAMI-DADE'}[a.county])
    return 0


if __name__ == '__main__':
    sys.exit(main())
