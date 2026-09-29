#!/usr/bin/env python3
"""resimpli_sync.py - fold REsimpli skip-trace exports into the board's phone cache.

The inbound half of the REsimpli bridge (export_to_resimpli.py is the outbound half). REsimpli's
"Skip Trace" export is a CSV of properties, each with up to two named owners and ten phones. This
adds those phones to skiptrace_results.json, the same local cache skiptrace.py writes, so the next
board build bakes them into r.phones / r.phdnc / r.phtype and Call Mode applies every gate it
already applies to a traced number.

WHAT GETS IN
  * A row is used only when its property street, unit and ZIP match a board lead that has a case
    number AND one of the row's owners is one of that lead's owners: a surname in common and a
    given name in common, read from REsimpli's own first/last columns. A shared surname alone (a
    relative), a shared first name alone (two unrelated Marias), a Jr against a Sr, a company, an
    estate or heirs, and a lead flagged ownerMismatch / lpDismissed / lpClosed all skip. So does a
    different unit in the same building, or a unit on one side only: those numbers belong to someone
    else (the Cooney/Cardenas failure in contact_trust.py). A county-roll line that names two people
    ('PEREZ JOSE & GARCIA MARIA') is two people, not one who is Jose Garcia. A lis pendens keyed by
    a name instead of a case number ('LP-<owner>') has no case number to check and gets nothing.
  * Numbers are attributed by owner. REsimpli lists Phone_1..5 under the row's first owner and
    Phone_6..10 under the second. In the 2026-09-28 export every company-owned row carries phones
    only in 6..10 (the person found behind the company) and no single-owner row carries any there.
    A number goes to a lead only when that lead matches the owner the number is listed under; the
    other owner's numbers are counted (numbers_other_owner) and left out. This is read off the
    export, not documented by REsimpli.
  * A lead ends up with at most phone_src.MAX_PHONES numbers, the number the board bake keeps. The
    bake sorts a lead's numbers (clean, mobile first) and cuts the rest, and the person-level
    opt-out checks read the cut row, so numbers this tool appended must never push one the cache
    already holds off it. Clean mobiles fill the room first; what does not fit is counted
    (numbers_over_cap) and not added.
  * Rows with no matching lead are counted, never added as leads. They have no case number, so no
    bankruptcy / stay check can run on them; a new lead source is a product decision.
  * Numbers are ADDED. An existing number, entry, or provider is never removed or replaced, and a
    number already in the cache is recognised however it is written ('13055550101' = '3055550101').
    A number REsimpli flags DNC is stored only next to a callable number from the same export: on
    its own it would give a lead a phone list of nothing dialable, which stops skiptrace.py from
    ever tracing it (it only traces cases that have no entry). A lead that gets its first number
    here is also not traced by Tracerfy afterwards, whatever that number's type.
  * Emails are not merged: they are unverified and would feed first-touch email.

DNC IS ABOUT THE NUMBER, AND IT ONLY TIGHTENS
A phone counts as clean only when REsimpli says so in plain words (DNC "No", status "[]", litigator
blank or No). Yes, blank, an unrecognised value, or any other status is DNC. A row with a truthy
`opt` is treated as opted out: its numbers are flagged and it is not merged. A file with no `opt`
column, or no _DNC, _status or _IsLitigator column for a phone slot, is refused, not read as clean.
A number any export flags is flagged DNC on every phone in the cache that has those digits, on this
lead or another, and a number the cache already holds as DNC on any lead is DNC on the lead it is
added to, even when REsimpli lists it clean. Each flagged number is also recorded in dnc_scrub.json,
the sidecar the board bake applies to every cached skip-trace phone (foreclosure_leads.make_tracker).
That is what keeps the flag when skiptrace.py later replaces a lead's entry wholesale, or traces a
lead this tool skipped and its own modeled flag says clean. The bake looks a number up by its exact
stored string, so each one gets a key in the 10-digit spelling, in the 11-digit spelling a provider
can return, and as the cache spells it. The record carries source 'resimpli', which
tracerfy_mcp.py's paid 30-day DNC re-scrub leaves alone: a registry miss must not replace a
REsimpli opt-out or litigator flag. Nothing here ever clears a flag, and an existing dnc_scrub.json
verdict is only tightened. A dnc_scrub.json that cannot be read is left exactly as found and said so.
The bake does not apply the sidecar to phones it appends later from other sources.

WHICH FILES
Every CSV passed on the command line, or with none: every SkipTrace_*.csv in Downloads / Desktop
plus DEALFLOW_DIR/imports/resimpli/. All of them are processed, not the newest: the merge is
add-only and dedupes by number, so re-reading a file adds nothing and no file has to be picked.
A stray text file whose header is plainly not a REsimpli export is skipped by name. An export that
cannot be read in full (not UTF-8, so do not open and re-save it in Excel; a flag column missing) is
REFUSED by name and stops the run, even when the other exports are fine: the numbers it lists as
DNC would be missing from the merge while an older export lists them clean. Found files are copied
into DEALFLOW_DIR/imports/resimpli/ (outside the repo and outside OneDrive) after the data is
written; the originals stay put.

OUTPUT
Counts on stdout. DEALFLOW_DIR/resimpli_sync_status.json holds the same counts plus the case numbers
of any unconfirmed leads (no names, no phone numbers). Exit 0 = done (or nothing to do), 1 = no
usable export, 2 = refused (a file or the cache; nothing written), 3 = the cache or dnc_scrub.json
changed while this ran, or a write failed.
A missing cache stops the run (wrong machine) unless --create. Both files are written beside the
originals under this tool's own temp name (.resimpli.tmp, flushed to disk), a backup of each goes to
DEALFLOW_DIR/backups/, and the signatures of both are checked once more before anything is
replaced: skiptrace.py, contact_trust.py --write and tracerfy_mcp.py each rewrite a whole file from
what they loaded at start, so do not run this while one of them is running. The sidecar is replaced
first (it only tightens), the cache last. A failed write says what already landed.
Each run also counts the cached numbers tagged src=resimpli that today's rules would not attach
(resimpli_unconfirmed). They come from an earlier, looser merge; nothing is removed.
`traced` on a new entry is REsimpli's own skip-trace date. healthcheck.py reads max(traced) as the
last Tracerfy run, so a recent export can make a stalled Tracerfy nightly look fresh for a few days.
Nothing is committed, published or sent.

    python resimpli_sync.py --dry-run          # counts, writes nothing
    python resimpli_sync.py                    # merge every export found
    python resimpli_sync.py path\\to\\file.csv   # one file

The board is not rebuilt here. After a merge, rebuild and publish through the normal gates.
"""
import argparse
import csv
import datetime
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contact_trust as CT          # the repo's one definition of an entity, a placeholder, a role suffix
import phone_src as PS              # MAX_PHONES: how many numbers per lead the board bake keeps

REQUIRED = ('propertyStreetAddress', 'propertyZipCode', 'firstName', 'lastName', 'opt')
SLOT_FLAG_COLS = ('_DNC', '_status', '_IsLitigator')    # a phone slot without these cannot be read as clean
SLOTS_PER_OWNER = 5
OWNER_COLS = (('firstName', 'lastName'), ('firstName2', 'lastName2'))
OWNER_FIELDS = ('owners', 'owner', 'oname', 'rname', 'Owner', 'owner_clean')
DEAD_FLAGS = ('ownerMismatch', 'lpDismissed', 'lpClosed')

SUF = {'STREET': 'ST', 'AVENUE': 'AVE', 'AV': 'AVE', 'COURT': 'CT', 'ROAD': 'RD', 'DRIVE': 'DR',
       'TERRACE': 'TER', 'TERR': 'TER', 'PLACE': 'PL', 'LANE': 'LN', 'BOULEVARD': 'BLVD',
       'CIRCLE': 'CIR', 'HIGHWAY': 'HWY', 'PARKWAY': 'PKWY', 'NORTH': 'N', 'SOUTH': 'S',
       'EAST': 'E', 'WEST': 'W', 'NORTHWEST': 'NW', 'NORTHEAST': 'NE', 'SOUTHWEST': 'SW',
       'SOUTHEAST': 'SE'}
UNIT_MARKS = {'UNIT', 'APT', 'APARTMENT', 'STE', 'SUITE', 'BLDG', 'LOT', '#'}
UNIT_FILLER = {'NO', 'NUM', 'NUMBER'}
# particles and prefixes that many unrelated surnames share: 'DA COSTA' and 'DA SILVA' are not a
# surname in common
NAME_STOP = {'DE', 'DEL', 'LA', 'LAS', 'LOS', 'EL', 'DA', 'DAS', 'DO', 'DOS', 'DI', 'DU', 'DES',
             'VAN', 'VON', 'DER', 'DEN', 'SAN', 'SANTA', 'SANTO', 'SAINT', 'MC', 'MAC', 'THE',
             'AND', 'OF'}
# not a living owner to phone: the number belongs to someone else (an heir, a personal representative)
NOT_A_PERSON = re.compile(r'\b(ESTATE|EST|DECD|DECEASED|HEIRS?|UNKNOWN|UNK)\b', re.I)
# 'JOSE PEREZ JR' and 'JOSE PEREZ SR' at one address are two people. A marker on one side only is
# not evidence either way (most county rolls leave it off).
GENERATION = re.compile(r'\b(JR|SR|II|III|IV)\b')
# a county-roll line can name two people: 'PEREZ JOSE & GARCIA MARIA', 'PEREZ JOSE & MARIA'
CO_OWNER = re.compile(r'\s*&\s*|\s+AND\s+', re.I)

COUNT_KEYS = ('rows', 'rows_with_phone', 'unmatched_rows_with_phone', 'unit_mismatch',
              'unit_conflict_same_person', 'entity_rows', 'flagged_lead_rows', 'owner_mismatch',
              'opt_rows', 'matched_rows', 'numbers_other_owner', 'leads_matched', 'leads_had_phone',
              'leads_new_phone', 'leads_dnc_only_skipped', 'entries_unusable', 'numbers_over_cap',
              'new_numbers', 'new_numbers_dnc', 'new_mobile_clean', 'numbers_unreadable',
              'flags_unexpected')
GLOBAL_KEYS = ('dnc_flagged_numbers', 'dnc_tightened', 'dnc_sidecar_added', 'dnc_sidecar_tightened',
               'cache_resimpli_numbers', 'resimpli_confirmed', 'resimpli_unconfirmed',
               'resimpli_unconfirmed_leads')


class SyncError(Exception):
    """skippable: a stray file that is plainly not a REsimpli export. Anything else that stops a file
    from being read in full is not skippable: its DNC flags would be silently missing from the merge."""
    def __init__(self, msg, skippable=False):
        super().__init__(msg)
        self.skippable = skippable


# ---------------------------------------------------------------- addresses

def join_unit(tokens):
    """['1', '23'] -> '1-23', ['12', '3'] -> '12-3' (two units of one tower), ['4', 'B'] -> '4B'."""
    out = ''
    for t in tokens:
        if out and not (len(t) == 1 and t.isalpha() and out[-1].isdigit()):
            out += '-'
        out += t
    return out


def split_address(s):
    """'8425 Sw 152nd Ave Apt 405, Miami' -> ('8425 SW 152 AVE', '405'). The unit is part of the
    identity: two condos in one building are two homes, and a shared surname between them is not
    the same household. '#405', 'Unit 405' and 'Apt 405' all read as unit '405'."""
    raw = re.sub(r'[^A-Z0-9# ]', ' ', (s or '').upper().split(',')[0].replace('#', ' # '))
    street, unit, in_unit = [], [], False
    for w in raw.split():
        if w in UNIT_MARKS:
            in_unit = True
        elif in_unit:
            if w not in UNIT_FILLER:
                unit.append(w)
        else:
            street.append(re.sub(r'^(\d+)(ST|ND|RD|TH)$', r'\1', SUF.get(w, w)))
    return ' '.join(street), join_unit(unit)


def row_address(x):
    """A REsimpli row's (street, unit): the unit column when it has one, else one written inline."""
    street, unit = split_address(x.get('propertyStreetAddress'))
    unit2 = split_address('0 UNIT ' + (x.get('propertyStreetAddress2') or ''))[1]
    return street, (unit2 or unit)


def zip_of(s):
    m = re.findall(r'\b(\d{5})(?:-\d{4})?\b', s or '')
    return m[-1] if m else ''


# ---------------------------------------------------------------- names

def fold(s):
    """Accents off: 'JOSÉ' is 'JOSE', as the county roll writes it."""
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))


def person_tokens(s):
    """Upper-case name tokens of ONE person, or an empty set when the string is not a living
    person: a company, a placeholder, an estate or heirs. Role suffixes (TRS, LE, H/E ...) are
    dropped, and O'CONNOR reads as the county roll's OCONNOR. A generation marker (JR, SR, II ...)
    rides along as an '@JR' token that person_matches compares and then ignores."""
    s = fold(s)
    if not s.strip() or CT.PLACEHOLDER.search(s) or CT.ENTITY.search(s) or NOT_A_PERSON.search(s):
        return set()
    gen = {'@' + m.group(1) for m in GENERATION.finditer(s.upper())}
    s = CT.SUFFIX.sub(' ', s).replace("'", '')
    words = {w for w in re.sub(r'[^A-Za-z ]', ' ', s).upper().split() if len(w) >= 2 and w not in NAME_STOP}
    return words | gen if words else set()


def _bare(tokens):
    return {w for w in tokens if not w.startswith('@')}


def person_matches(first, last, owner):
    """The REsimpli person (given-name tokens, surname tokens) is this owner: a surname in common
    AND a given name in common among the owner's remaining tokens. Word order does not matter,
    which is what lets 'GARCIA, MARIA' and 'Maria Garcia' agree. A surname alone or a first name
    alone does not (contact_trust.py: two unrelated Marias must not read as one household), and
    a Jr against a Sr is two people."""
    gen_a, gen_b = set(first | last) - _bare(first | last), set(owner) - _bare(owner)
    if gen_a and gen_b and gen_a != gen_b:
        return False
    first, last, owner = _bare(first), _bare(last), _bare(owner)
    return bool(last & owner) and bool(first & (owner - last))


def row_people(x):
    """[(owner number, given tokens, surname tokens)] for the row's named human owners. A company,
    a placeholder, or an owner with only one of the two names yields nothing."""
    out = []
    for g, (fk, lk) in enumerate(OWNER_COLS, 1):
        first, last = x.get(fk) or '', x.get(lk) or ''
        if not person_tokens('%s %s' % (first, last)):
            continue
        f, l = person_tokens(first), person_tokens(last)
        if f and l:
            out.append((g, f, l))
    return out


def owner_people(r):
    """Token sets of the board lead's human owners: every ';'-separated owner in every owner field,
    and every person of a line that names two ('PEREZ JOSE & GARCIA MARIA' is two people, and is
    not one person who is Jose Garcia). A second name of one word ('PEREZ JOSE & MARIA') shares the
    first person's surname."""
    out = []
    for f in OWNER_FIELDS:
        for chunk in str(r.get(f) or '').split(';'):
            lead_tokens = None
            for part in CO_OWNER.split(chunk):
                t = person_tokens(part)
                if not t:
                    continue
                if lead_tokens is not None and len(_bare(t)) == 1:
                    t = t | _bare(lead_tokens)
                if lead_tokens is None:
                    lead_tokens = t
                if t not in out:
                    out.append(t)
    return out


def lead_is_dead(r):
    return any(r.get(k) for k in DEAD_FLAGS)


# ---------------------------------------------------------------- phones

def norm_number(v):
    """Digits only, and an 11-digit US number loses its leading 1, so '13055550101' (which
    skiptrace.py keeps as returned) and '3055550101' are the same number."""
    d = re.sub(r'\D', '', str(v or ''))
    return d[1:] if len(d) == 11 and d.startswith('1') else d


def parse_number(v):
    """The 10-digit US number in one CSV cell, else ''. '3055550101.0' (a column Excel turned into
    a float) is read; '3.05555E+09' (precision already lost), an extension, or a number that cannot
    be a US line is not guessed at: the caller counts it."""
    s = str(v or '').strip()
    if re.fullmatch(r'\d+\.0+', s):
        s = s.split('.')[0]
    if re.fullmatch(r'\d(\.\d+)?[eE][+-]?\d+', s):
        return ''
    d = norm_number(s)
    return d if re.fullmatch(r'[2-9]\d{2}[2-9]\d{6}', d) else ''


def norm_type(v):
    """Mobile / Landline / '' (unknown). The board reads anything that is not mobile as landline,
    which withholds Text and WhatsApp, so an unknown type fails closed."""
    t = str(v or '').strip().lower()
    return 'Mobile' if t.startswith('mob') else 'Landline' if t.startswith('land') else ''


def read_flags(x, i):
    """(dnc, unexpected) for Phone_i. Fail closed: a number is clean only when REsimpli says so in
    plain words. `unexpected` counts values outside what the export has ever held."""
    cell = str(x.get('Phone_%d_DNC' % i) or '').strip().lower()
    status = str(x.get('Phone_%d_status' % i) or '').replace(' ', '').upper()
    lit = str(x.get('Phone_%d_IsLitigator' % i) or '').strip().lower()
    dnc = odd = False
    if cell == 'yes':
        dnc = True
    elif cell != 'no':
        dnc = odd = True
    if status != '[]':                                   # blank is not "clean" either; every number in the export has one
        dnc = True
        odd = odd or status != '["DNC"]'
    if lit in ('yes', 'true', 'y', '1'):
        dnc = True
    elif lit not in ('', 'no', 'false', 'n', '0'):     # blank is every row of the real export
        dnc = odd = True
    return dnc, odd


def opted_out(x):
    return str(x.get('opt') or '').strip().lower() not in ('', 'no', 'false', 'n', '0')


def slots_of(rows):
    """Phone slot numbers in the export, read off the header (every row of a DictReader has it; a
    trailing delimiter adds a None key, which is not a column)."""
    return sorted(int(m.group(1)) for k in (rows[0] if rows else ()) if isinstance(k, str)
                  for m in [re.fullmatch(r'Phone_(\d+)', k)] if m)


def owner_of(slot):
    return (slot - 1) // SLOTS_PER_OWNER + 1


def parse_cells(x, slots, n=None):
    """Every readable phone on the row. A cell that holds something but no number is counted, and
    never guessed at."""
    out = []
    opt = opted_out(x)
    for i in slots:
        raw = str(x.get('Phone_%d' % i) or '').strip()
        if not raw:
            continue
        num = parse_number(raw)
        if not num:
            if n is not None:
                n['numbers_unreadable'] += 1
            continue
        dnc, odd = read_flags(x, i)
        if n is not None and odd:
            n['flags_unexpected'] += 1
        out.append({'slot': i, 'number': num, 'type': norm_type(x.get('Phone_%d_type' % i)),
                    'dnc': dnc or opt})
    return out


def flagged_numbers(rows):
    """Every number any of these rows flags DNC (or lists under an opt-out), matched or not. DNC
    belongs to the number, so it is applied wherever that number appears."""
    slots = slots_of(rows)
    return {c['number'] for x in rows for c in parse_cells(x, slots) if c['dnc']}


def row_traced(x, today):
    """REsimpli's own skip-trace date for the row (epoch ms in this export) as YYYY-MM-DD, never later
    than today (the laptop clock has run behind), or '' when the cell holds nothing readable."""
    v = str(x.get('skipTracedDate') or '').strip()
    try:
        if re.fullmatch(r'\d{12,13}', v):
            d = datetime.datetime.fromtimestamp(int(v) / 1000, datetime.timezone.utc).date()
        elif re.fullmatch(r'\d{9,10}', v):
            d = datetime.datetime.fromtimestamp(int(v), datetime.timezone.utc).date()
        else:
            d = datetime.date.fromisoformat(v[:10])
    except (ValueError, OverflowError, OSError):
        return ''
    return min(d, datetime.date.fromisoformat(today)).isoformat()


# ---------------------------------------------------------------- merge

def real_case(c):
    """A case number a stay or bankruptcy check can key on. lp_leads.py keys a lis pendens row that
    has none 'LP-<owner name>': that is not a case number, and it must neither receive phones nor
    end up, name and all, in the status file."""
    return bool(re.search(r'\d{5,}', str(c or '')))


def phones_of(ent):
    """The phone dicts of one cache entry; anything malformed reads as no phones."""
    ph = ent.get('phones') if isinstance(ent, dict) else None
    return [p for p in ph if isinstance(p, dict)] if isinstance(ph, list) else []


def cached_dnc(results):
    """Digits of every phone the cache already holds as DNC, on any lead. A number one lead's entry
    says never to contact is not clean on the next lead because REsimpli lists it there."""
    return {norm_number(p.get('number')) for ent in results.values() for p in phones_of(ent) if p.get('dnc')}


def build_index(leads, case_of, addr_of):
    """(street, zip) -> [(lead, unit)] for every lead that has a case number and a usable address."""
    idx = {}
    for r in leads:
        if not real_case(case_of(r)):
            continue
        a = addr_of(r)
        street, unit = split_address(a)
        k = (street, zip_of(a))
        if k[0] and k[1]:
            idx.setdefault(k, []).append((r, unit))
    return idx


def merge(rows, idx, results, case_of, addr_of, today=None, flagged=(), pairs=None, state=None):
    """Add matching rows' phones into `results` in place. Returns counts (no names, no numbers).

    flagged: numbers to treat as DNC wherever they appear (flagged_numbers over every export). The
             numbers `results` already holds as DNC, on any lead, are added to it here.
    pairs:   optional set collecting every (case, number) this run attributes to a lead (audit()).
    state:   optional dict shared across files, so a lead two files both match counts once."""
    today = today or datetime.date.today().isoformat()
    flagged = set(flagged) | cached_dnc(results)
    st = state if state is not None else {}
    touched = st.setdefault('touched', set())
    dnc_only = st.setdefault('dnc_only', set())
    slots = slots_of(rows)
    n = dict.fromkeys(COUNT_KEYS, 0)
    n['rows'] = len(rows)
    for x in rows:
        cells = parse_cells(x, slots, n)
        if not cells:
            continue
        n['rows_with_phone'] += 1
        if opted_out(x):
            n['opt_rows'] += 1
            continue
        street, row_unit = row_address(x)
        hits = idx.get((street, str(x.get('propertyZipCode') or '').strip()[:5])) or []
        if not hits:
            n['unmatched_rows_with_phone'] += 1
            continue
        people = row_people(x)
        same_unit = [r for r, u in hits if u == row_unit]
        if not same_unit:
            n['unit_mismatch'] += 1
            if any(person_matches(f, l, t) for g, f, l in people for r, u in hits for t in owner_people(r)):
                # Same building, same person, other unit: what a building-level match would have
                # attached to the wrong home.
                n['unit_conflict_same_person'] += 1
            continue
        if not people:
            n['entity_rows'] += 1
            continue
        live = [r for r in same_unit if not lead_is_dead(r)]
        if not live:
            n['flagged_lead_rows'] += 1
            continue
        matched = []
        for r in live:
            owners = owner_people(r)
            groups = {g for g, f, l in people if any(person_matches(f, l, t) for t in owners)}
            if groups:
                matched.append((r, groups))
        if not matched:
            n['owner_mismatch'] += 1
            continue
        n['matched_rows'] += 1
        by_owner = {}
        for c in cells:
            c['dnc'] = c['dnc'] or c['number'] in flagged
            by_owner.setdefault(owner_of(c['slot']), []).append(c)
        n['numbers_other_owner'] += sum(len(v) for g, v in by_owner.items()
                                        if not any(g in gs for _, gs in matched))
        traced = row_traced(x, today)
        for r, groups in matched:
            case = case_of(r)
            ent = results.get(case)
            if ent is not None and not (isinstance(ent, dict) and isinstance(ent.get('phones') or [], list)):
                n['entries_unusable'] += 1
                continue
            had = bool(ent and ent.get('phones'))
            if case not in touched:
                touched.add(case)
                n['leads_matched'] += 1
                n['leads_had_phone'] += had
            mine = [c for g in sorted(groups) for c in by_owner.get(g, [])]
            if pairs is not None:
                pairs.update((case, c['number']) for c in mine)
            have = {norm_number(p.get('number')) for p in phones_of(ent)}
            added, seen = [], set()
            for c in mine:
                if c['number'] in have or c['number'] in seen:
                    continue
                seen.add(c['number'])
                added.append({'number': c['number'], 'type': c['type'], 'carrier': '', 'dnc': c['dnc'],
                              'src': 'resimpli'})
            # The board bake sorts a lead's numbers (clean, mobile first) and keeps MAX_PHONES. Numbers
            # this run adds must never push one the cache already holds off the row, so the entry is
            # filled up to that many and no further: clean mobiles first, DNC numbers last.
            added.sort(key=lambda p: (p['dnc'], p['type'] != 'Mobile'))
            room = max(0, PS.MAX_PHONES - len((ent or {}).get('phones') or []))
            if len(added) > room:
                n['numbers_over_cap'] += len(added) - room
                added = added[:room]
            if not added:
                continue
            if all(p['dnc'] for p in added):
                # Nothing dialable to add. The flag is still recorded (tighten + dnc_scrub.json); the
                # lead gets no entry and no phones, so skiptrace.py can still trace it.
                if case not in dnc_only:
                    dnc_only.add(case)
                    n['leads_dnc_only_skipped'] += 1
                continue
            if ent is None:
                ent = {'name': (r.get('owners') or r.get('owner') or '').split(';')[0].strip(),
                       'entity': '', 'address': addr_of(r), 'county': r.get('county') or 'MIAMI-DADE',
                       'phones': [], 'emails': [], 'source': 'resimpli'}
                if traced:
                    ent['traced'] = traced
                results[case] = ent
            elif not ent.get('phones'):
                ent['phones'] = []
            if not had:
                n['leads_new_phone'] += 1
            ent['phones'].extend(added)
            ent['resimpli'] = today
            n['new_numbers'] += len(added)
            n['new_numbers_dnc'] += sum(1 for p in added if p['dnc'])
            n['new_mobile_clean'] += sum(1 for p in added if not p['dnc'] and p['type'] == 'Mobile')
    return n


def tighten(results, flagged):
    """One-way, like the registry override in make_tracker: every cached phone whose digits are in
    `flagged` becomes dnc=True. Nothing here ever clears a flag. Returns how many phones changed."""
    n = 0
    for ent in results.values():
        for p in phones_of(ent):
            if not p.get('dnc') and norm_number(p.get('number')) in flagged:
                p['dnc'] = True
                n += 1
    return n


def audit(results, pairs):
    """Cached numbers tagged src=resimpli that today's rules did not attach in this run: left by an
    earlier, looser merge. -> (counts, case numbers)"""
    held = {(case, norm_number(p.get('number')))
            for case, ent in results.items() for p in phones_of(ent) if p.get('src') == 'resimpli'}
    bad = {k for k in held if k not in pairs}
    return ({'cache_resimpli_numbers': len(held), 'resimpli_confirmed': len(held) - len(bad),
             'resimpli_unconfirmed': len(bad), 'resimpli_unconfirmed_leads': len({c for c, _ in bad})},
            sorted({c for c, _ in bad if real_case(c)}))


def sidecar_plan(path, flagged, results, today):
    """dnc_scrub.json with REsimpli's flagged numbers merged in. -> (doc or None, added, tightened,
    state). make_tracker looks a cached number up by its exact stored string, so each flagged number
    gets a key in the 10-digit spelling, in the 11-digit spelling a provider can return, and in the
    spelling of every cached phone that has those digits. doc is None when there is nothing to write
    or when the file exists and cannot be read: a torn sidecar is left as found."""
    try:
        with open(path, encoding='utf-8') as f:
            cur = json.load(f)
    except FileNotFoundError:
        cur = {}
    except (OSError, ValueError):
        return None, 0, 0, 'unreadable'
    if not isinstance(cur, dict):
        return None, 0, 0, 'unreadable'
    keys = set(flagged) | {'1' + n for n in flagged}
    for ent in results.values():
        for p in phones_of(ent):
            if norm_number(p.get('number')) in flagged:
                keys.add(str(p.get('number')))
    added = tightened = 0
    for k in sorted(keys):
        v = cur.get(k)
        if isinstance(v, dict):
            if not (v.get('national_dnc') or v.get('state_dnc')):
                v['national_dnc'] = True                # a registry verdict is only ever tightened
                v['resimpli'] = today
                v['source'] = 'resimpli'                # so tracerfy_mcp's 30-day re-scrub leaves it alone
                tightened += 1
        else:
            # REsimpli says DNC without naming the registry; the seam needs one of the two flags
            cur[k] = {'national_dnc': True, 'state_dnc': False, 'states': [], 'case': '',
                      'checked': today, 'source': 'resimpli'}
            added += 1
    return (cur if added or tightened else None), added, tightened, 'ok'


# ---------------------------------------------------------------- files

def read_export(path):
    """The rows of one REsimpli skip-trace export. A file whose header says it is not one (a stray
    SkipTrace_*.csv) raises a skippable SyncError. A file that is one but cannot be read in full (not
    UTF-8, a flag column missing, a read error) raises a hard one: skipping it would drop its DNC
    flags while the other exports still merge their numbers as clean."""
    name = os.path.basename(path)
    try:
        with open(path, 'rb') as fb:
            head = fb.readline(1 << 20)
    except OSError as e:
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__))
    not_utf8 = SyncError('%s is not UTF-8 text (was it re-saved in Excel? download it from REsimpli again)' % name)
    try:
        first = head.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise not_utf8
    if '\x00' in first:                                     # UTF-16 without a byte-order mark
        raise not_utf8
    fields = next(csv.reader(io.StringIO(first)), [])
    slots = [int(m.group(1)) for c in fields for m in [re.fullmatch(r'Phone_(\d+)', c)] if m]
    if not slots and 'propertyStreetAddress' not in fields:
        raise SyncError('%s is not a REsimpli skip-trace export' % name, skippable=True)
    missing = [c for c in REQUIRED if c not in fields]
    if not slots:
        missing.append('Phone_1')
    missing += ['Phone_%d%s' % (i, s) for i in sorted(slots) for s in SLOT_FLAG_COLS
                if 'Phone_%d%s' % (i, s) not in fields]
    if missing:
        raise SyncError('%s is not a complete REsimpli skip-trace export (missing %s)'
                        % (name, ', '.join(missing[:6])))
    try:
        with open(path, encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except UnicodeDecodeError:
        raise not_utf8
    except (OSError, csv.Error) as e:
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__))


def discover(import_dir):
    home = os.path.expanduser('~')
    pats = [os.path.join(home, d, 'SkipTrace_*.csv')
            for d in ('Downloads', 'Desktop', os.path.join('OneDrive', 'Desktop'))]
    pats.append(os.path.join(import_dir, '*.csv'))
    found = []
    for p in pats:
        found.extend(glob.glob(p))
    return sorted(set(found))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def load_cache(path, create):
    try:
        with open(path, encoding='utf-8') as f:
            results = json.load(f)
    except FileNotFoundError:
        if create:
            return {}
        raise SyncError('no phone cache at %s (wrong machine? --create starts an empty one)' % path)
    except (OSError, ValueError) as e:
        raise SyncError('the phone cache at %s cannot be read (%s); nothing written' % (path, type(e).__name__))
    if not isinstance(results, dict):
        raise SyncError('the phone cache at %s is not a JSON object; nothing written' % path)
    return results


def cache_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def backup(path, stem, bdir):
    """Copy `path` into bdir as <stem>.pre-resimpli-<time>.json; None when there is nothing to copy."""
    if not os.path.exists(path):
        return None
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, '%s.pre-resimpli-%s.json' % (stem, datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
    shutil.copy2(path, dst)
    return dst


def drop(paths):
    """Remove leftover temp files; a temp file that is already gone is fine."""
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def write_tmp(path, obj):
    """Write `obj` beside `path` under this tool's own temp name (tracerfy_mcp.py and skiptrace.py use
    '.tmp', so the two never write one file) and flush it to disk before anything replaces `path`."""
    tmp = path + '.resimpli.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    return tmp


def main(argv=None):
    try:
        sys.stdout.reconfigure(errors='replace')
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('files', nargs='*')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--create', action='store_true',
                    help='start an empty phone cache when there is none (otherwise that stops the run)')
    a = ap.parse_args(argv)

    import paths as P
    import skiptrace as S
    import_dir = os.path.join(P.DEALFLOW_DIR, 'imports', 'resimpli')
    files = a.files or discover(import_dir)
    if not files:
        print('no REsimpli export found (SkipTrace_*.csv in Downloads / Desktop, or %s)' % import_dir)
        return 1

    # one copy of each distinct file, by content: the same export downloaded twice is one file
    seen, exports, skipped = set(), [], []
    for f in files:
        try:
            h = sha256(f)
        except OSError as e:
            err = SyncError('%s could not be read (%s)' % (os.path.basename(f), type(e).__name__))
        else:
            if h in seen:
                continue
            seen.add(h)
            err = None
            try:
                exports.append((f, h, read_export(f)))
            except SyncError as e:
                err = e
        if err:
            if a.files or not err.skippable:    # a file named on the command line, or one that is an export
                print('REFUSED:', err)            # we cannot read in full, is worth stopping for
                if not a.files:
                    print('Its DNC flags would be missing from the merge, so nothing was read further. '
                          'Move or delete that file (or download it again from REsimpli), then run again.')
                return 2
            print('SKIPPED:', err)          # a stray SkipTrace_*.csv must not block the real exports
            skipped.append(os.path.basename(f))
    if not exports:
        print('no usable REsimpli export found')
        return 1

    res_path = S.RESULTS
    side_path = os.path.join(os.path.dirname(os.path.abspath(res_path)), 'dnc_scrub.json')
    bdir = os.path.join(P.DEALFLOW_DIR, 'backups')
    sig0 = cache_sig(res_path)
    try:
        results = load_cache(res_path, a.create)
    except SyncError as e:
        print('REFUSED:', e)
        return 2
    today = datetime.date.today().isoformat()
    leads = S.load_all_leads()
    idx = build_index(leads, S._case, S._propaddr)

    flagged = set()
    for f, h, rows in exports:
        flagged |= flagged_numbers(rows)

    status = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'dry_run': a.dry_run,
              'board_leads_indexed': sum(len(v) for v in idx.values()), 'skipped_files': skipped,
              'files': []}
    total = dict.fromkeys(COUNT_KEYS, 0)
    state, pairs = {}, set()
    for f, h, rows in exports:
        n = merge(rows, idx, results, S._case, S._propaddr, today, flagged, pairs, state)
        status['files'].append({'file': os.path.basename(f), 'sha256': h[:16], **n})
        for k in COUNT_KEYS:
            total[k] += n[k]
        print('%s: %d rows, %d with phones, %d leads matched, %d new numbers (%d DNC-flagged), '
              '%d leads had no phone'
              % (os.path.basename(f), n['rows'], n['rows_with_phone'], n['leads_matched'],
                 n['new_numbers'], n['new_numbers_dnc'], n['leads_new_phone']))
    tightened = tighten(results, flagged)
    aud, bad_cases = audit(results, pairs)
    side_sig0 = cache_sig(side_path)
    side_doc, side_added, side_tight, side_state = sidecar_plan(side_path, flagged, results, today)
    total.update(dnc_flagged_numbers=len(flagged), dnc_tightened=tightened,
                 dnc_sidecar_added=side_added, dnc_sidecar_tightened=side_tight, **aud)
    status['total'] = total
    status['dnc_scrub_json'] = side_state
    status['resimpli_unconfirmed_cases'] = bad_cases
    print('TOTAL')
    for k in COUNT_KEYS + GLOBAL_KEYS:
        print('  %-28s %d' % (k, total[k]))
    if side_state == 'unreadable':
        print('WARNING: dnc_scrub.json exists but cannot be read; left untouched. The board bake cannot '
              'apply registry verdicts until it is fixed. %d flagged numbers were not recorded there.'
              % len(flagged))
    if aud['resimpli_unconfirmed']:
        print('NOTE: %d cached REsimpli numbers on %d leads are not attached by today\'s rules (an earlier, '
              'looser merge). They are still in the cache and on the board; nothing was removed.'
              % (aud['resimpli_unconfirmed'], aud['resimpli_unconfirmed_leads']))

    if a.dry_run:
        print('DRY RUN - nothing written')
        return 0

    cache_changed = bool(total['new_numbers'] or tightened)
    wrote, tmps = [], []
    try:
        # 1. prepare: both files are written beside the originals; nothing is replaced yet
        side_tmp = write_tmp(side_path, side_doc) if side_doc is not None else None
        cache_tmp = write_tmp(res_path, results) if cache_changed else None
        tmps = [t for t in (side_tmp, cache_tmp) if t]
        # 2. back up what is about to be replaced, then check that no other tool wrote either file
        #    while this ran (skiptrace.py, contact_trust.py --write and tracerfy_mcp.py each rewrite a
        #    file whole from what they loaded at start)
        if side_tmp:
            backup(side_path, 'dnc_scrub', bdir)
        bak = backup(res_path, 'skiptrace_results', bdir) if cache_tmp else None
        if tmps and (cache_sig(res_path) != sig0 or cache_sig(side_path) != side_sig0):
            print('CHANGED: %s or dnc_scrub.json was modified while this ran (skiptrace.py, contact_trust.py '
                  'or tracerfy_mcp.py?). Nothing was replaced. Wait for it to finish and run again.'
                  % os.path.basename(res_path))
            drop(tmps)
            return 3
        # 3. replace: the sidecar first (it only ever tightens), then the cache
        if side_tmp:
            os.replace(side_tmp, side_path)
            wrote.append('dnc_scrub.json')
            print('dnc_scrub.json updated (%d entries added, %d tightened)' % (side_added, side_tight))
        if cache_tmp:
            if bak:
                print('backup ->', bak)
            os.replace(cache_tmp, res_path)
            wrote.append('skiptrace_results.json')
            print('skiptrace_results.json updated')
        else:
            print('nothing new - skiptrace_results.json unchanged')
    except OSError as e:
        drop(tmps)
        print('FAILED: could not write (%s). Written before the failure: %s.'
              % (type(e).__name__, ', '.join(wrote) or 'nothing'))
        return 3
    # 4. what stays behind: a copy of each export outside the repo and OneDrive, and the status file.
    #    The data is already written, so a failure here is a warning, not a failed run.
    try:
        os.makedirs(import_dir, exist_ok=True)
        for f, h, _ in exports:
            if os.path.dirname(os.path.abspath(f)) == os.path.abspath(import_dir):
                continue                    # already the imports copy
            dst = os.path.join(import_dir, '%s_%s' % (h[:8], os.path.basename(f)))
            if not os.path.exists(dst):
                shutil.copy2(f, dst)
        with open(os.path.join(P.DEALFLOW_DIR, 'resimpli_sync_status.json'), 'w', encoding='utf-8') as fh:
            json.dump(status, fh, indent=1)
    except OSError as e:
        print('WARNING: the phone data was written, but the export copies or the status file could not be '
              '(%s).' % type(e).__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
