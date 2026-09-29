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
    ('PEREZ JOSE & GARCIA MARIA') is two people, not one who is Jose Garcia; a second name of one word
    ('PEREZ JOSE & MARIA') borrows only the first person's surname. A line with a company, trust or
    estate anywhere on it ('SMITH JOHN & MARY TR') names nobody. A lis pendens keyed by a name instead
    of a case number ('LP-<owner>') has no case number to check and gets nothing. A lead's owners are
    read from its raw owner line (`owners`) and nothing else: the pipeline also stores copies of that
    line (`owner_clean`, `oname`, `rname`) with the Jr / Sr, trust and company words taken out, or
    everything after an '&' dropped, or two people welded into one name, so reading them would undo
    every rule above.
  * Numbers are attributed by owner. REsimpli lists Phone_1..5 under the row's first owner and
    Phone_6..10 under the second. In the 2026-09-28 export every company-owned row carries phones
    only in 6..10 (the person found behind the company) and no single-owner row carries any there.
    A number goes to a lead only when that lead matches the owner the number is listed under; the
    other owner's numbers are counted (numbers_other_owner) and left out. This is read off the
    export, not documented by REsimpli.
  * A lead's row never gets pushed past phone_src.MAX_PHONES by this tool. The bake sorts a lead's
    skip-trace numbers (clean, mobile first), cuts them to that many, appends the lead's Whitepages
    numbers (whitepages_lookup.json) and cuts the row again, and the person-level opt-out checks read
    the cut row. So the numbers this tool adds must never push one the row already shows off it, a
    Whitepages number included: an entry is filled up to MAX_PHONES minus the numbers it already holds
    minus the Whitepages numbers it does not. Clean mobiles fill the room first; what does not fit is
    counted (numbers_over_cap) and not added. Every Whitepages number of the lead counts, including
    the person-search records the bake would drop, so this can only leave more room than needed.
  * Rows with no matching lead are counted, never added as leads. They have no case number, so no
    bankruptcy / stay check can run on them; a new lead source is a product decision.
  * Numbers are ADDED. An existing number, entry, or provider is never removed or replaced, and a
    number already in the cache is recognised however it is written ('13055550101' = '3055550101').
    A number REsimpli flags DNC is stored next to a callable number from the same export, or on a
    lead whose entry already has phones. On a lead with none, on its own, it would give a phone list
    of nothing dialable, which stops skiptrace.py from ever tracing it (it traces cases that have no
    entry, and retries a no-hit entry only while its phones are empty). On a lead that already has
    phones it costs nothing, and the bake then shows that number DNC where it would otherwise append
    a Whitepages copy of it as clean. A lead that gets its first number here is also not traced by
    Tracerfy afterwards, whatever that number's type.
  * Emails are not merged: they are unverified and would feed first-touch email.

DNC IS ABOUT THE NUMBER, AND IT ONLY TIGHTENS
A phone counts as clean only when REsimpli says so in plain words (DNC "No", status "[]", litigator
blank or No). Yes, blank, an unrecognised value, or any other status is DNC. A cell that is not a
single number (two numbers, or one with an extension or a note) is not merged, but when its row or
slot is flagged, every number written in it is flagged too. A row with `opt` set (Yes / True / Y / 1,
or Opted Out / Opt-Out / Unsubscribed / Stop / DNC) is opted out: its numbers are flagged and it is
not merged. So is a row, in any export this run reads, that names a person such a row names (same
surname and given name, either owner slot, either word order): opting out is about the person, so the
same person listed under another property with another number has not come back (opt_person_rows;
opt_person_cases lists the leads it kept a row off, opt_people_held how many people the hold was
built from). A namesake is held too; that is the safe side. `opt` blank or No / False / N / 0 is
not opted out; a value that is none of these refuses the file, since reading it as "no" lets an
opt-out through and reading it as "yes" flags every number in the list (1.0 and 0.0, what a
spreadsheet makes of 1 and 0, read as 1 and 0). A file with no `opt` column, or no _DNC, _status or
_IsLitigator column for a phone slot, is refused, not read as clean. A row with no `opt` cell at all
(a line cut short) is opted out.
A number any export flags is flagged DNC on every phone in the cache that has those digits, on this
lead or another, and a number the cache already holds as DNC on any lead is DNC on the lead it is
added to, even when REsimpli lists it clean, and so is a number dnc_scrub.json holds as registry-listed
(the bake makes it DNC whatever the cache says, so a lead whose only new number is one gets no entry).
Each flagged number is also recorded in dnc_scrub.json, the sidecar the board bake applies to every
cached skip-trace phone (foreclosure_leads.make_tracker).
That is what keeps the flag when skiptrace.py later replaces a lead's entry wholesale, or traces a
lead this tool skipped and its own modeled flag says clean. The bake looks a number up by its exact
stored string, so each one gets a key in the 10-digit spelling, in the 11-digit spelling a provider
can return, and as the cache spells it. Every record for a flagged number carries the REsimpli
marker (`resimpli`: the date), including one the registry lane already holds as listed. That marker
is what tracerfy_mcp.py's paid 30-day DNC re-scrub leaves alone: a registry miss must not replace a
REsimpli opt-out or litigator flag. Nothing here ever clears a flag, and an existing dnc_scrub.json
verdict is only tightened. A dnc_scrub.json that cannot be read is left exactly as found, and a run
that has any number to flag stops (exit 2): the sidecar is what keeps a flag when skiptrace.py replaces
an entry, and the only record of one on a lead with no entry or on no lead. The bake does not apply
the sidecar to phones it appends later from other sources (Whitepages numbers).

WHICH FILES
With no path: every SkipTrace_*.csv in Downloads / Desktop plus DEALFLOW_DIR/imports/resimpli/. With
paths: those files plus the copies in DEALFLOW_DIR/imports/resimpli/, because the hold on an opted-out
person and the DNC flags of every earlier export live in those copies, and naming one file must not
narrow them. All of them are processed, not the newest: the merge is add-only and dedupes by number,
so re-reading a file adds nothing (a lead sitting at its phone cap can, rarely, take one more number
on a second run, when an added number is also one of its Whitepages numbers and so no longer needs
room kept for it) and no file has to be picked.
A stray text file that has none of REsimpli's column names is skipped by name. An export that
cannot be read in full is REFUSED by name and stops the run, even when the other exports are fine:
the numbers it lists as DNC would be missing from the merge while an older export lists them clean.
That covers a file that is not UTF-8 (do not open and re-save it in Excel), one with a flag column
missing, one with any of REsimpli's own column names that is not a complete export (a re-save that
renamed the phone or address columns), one whose `opt` column holds a value this tool cannot read as
yes or no, one with REsimpli's column names that do not read as comma-separated columns on the first
line (a re-save with another delimiter, or with a 'sep=' line above the header), and a header line
csv cannot parse. Found files are copied into DEALFLOW_DIR/imports/resimpli/ (outside the repo and
outside OneDrive) after the data is written; the originals stay put.

OUTPUT
Counts on stdout. DEALFLOW_DIR/resimpli_sync_status.json holds the same counts plus the case numbers
of any unconfirmed leads (no names, no phone numbers). Exit 0 = done (or nothing to do), 1 = no
usable export, 2 = refused (an export, the cache, whitepages_lookup.json, the board leads, or a torn
dnc_scrub.json with numbers to flag; nothing written; a command-line usage error also exits 2), 3 =
the cache or dnc_scrub.json changed while this ran, or a write failed, 4 = an error nothing
anticipated (one line: its type and where, never its message; what was written before it is in the
lines above).
A missing cache stops the run (wrong machine) unless --create. Both files are written beside the
originals under a temp name of this tool's own (<file>.resimpli.<random>.tmp, so two runs never
share one; flushed to disk), the signatures of both are checked once more, and only then is a backup
of each taken (DEALFLOW_DIR/backups/) and the replace done: skiptrace.py, contact_trust.py --write
and tracerfy_mcp.py each rewrite a whole file from what they loaded at start, so do not run this
while one of them is running, or while a second copy of this tool is (the later replace would win
and the earlier run's numbers would come back on the next run). The sidecar is replaced first (it
only tightens), the cache last. A failed write says what already landed and leaves no temp file
behind.
Each run also counts the cached numbers tagged src=resimpli that today's rules would not attach
(resimpli_unconfirmed). They come from an earlier, looser merge; nothing is removed.
`traced` on a new entry is REsimpli's own skip-trace date. healthcheck.py reads max(traced) as the
last Tracerfy run, so a recent export can make a stalled Tracerfy nightly look fresh for a few days.
Nothing is committed, published or sent.

KNOWN LIMITS (reported here, not fixed)
  * A person match takes ONE shared given-name token among the owner's other names, so a relative who
    shares a middle name ('Elena Garcia' against 'GARCIA, MARIA ELENA') matches, and a Jr / Sr marker
    on one side only is not evidence. REsimpli's own skip trace can also return a namesake's or a
    relative's number for the right row: this checks the row's name against the lead, not the number
    against the person.
  * A registry verdict in dnc_scrub.json reaches the board bake, and makes a number DNC on the lead
    this tool adds it to, but is not copied onto other leads' cached phones. Readers of the raw cache
    (call_list.py, sheets_crm, bsg_daily_routes, _carlos_route, three_day.py, deal_desk.py) see only
    the cache's own `dnc` flag, as they already do for every registry hit the Tracerfy lane found.
  * REsimpli's `opt` becomes a number-level DNC flag and a person-level hold in this tool. It never
    reaches the opt-out ledger (optouts.json), which is another session's surface, and it does not
    touch what Tracerfy already cached for that person on her own lead. If `opt` means "do not contact
    this person", that is a larger gap and a policy call for Alejandro.
  * The person-level hold is rebuilt on every run from the exports the run reads (the ones passed plus
    the copies in DEALFLOW_DIR/imports/resimpli/). prep_desktop.py and make_transfer.py carry the phone
    cache and dnc_scrub.json but not that folder, and dnc_scrub.json holds the flagged numbers, not the
    people, so a machine set up from a transfer holds only the exports it has until the earlier ones
    are copied there. opt_people_held says how many people the hold was built from, and the NOTE about
    unconfirmed numbers fires when earlier exports are missing.
  * The bake appends a lead's Whitepages numbers with phdnc False and applies dnc_scrub.json to cached
    skip-trace phones only, so a number REsimpli flags that Whitepages also lists on a lead whose entry
    lacks it reaches the baked row as clean. flagged_also_whitepages counts those numbers; the seam is
    foreclosure_leads.make_tracker's, which this tool does not touch.
  * Nothing here clears a flag. To release a person held by mistake, the exports that opt them out (the
    copy in Downloads and the copy in DEALFLOW_DIR/imports/resimpli) have to be moved away and the flag
    fixed by hand in dnc_scrub.json and the cache; moving them forgets the hold for everyone on those
    exports. Left in place, the next run flags the person again.

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
import traceback
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import contact_trust as CT          # the repo's one definition of an entity, a placeholder, a role suffix
import phone_src as PS              # MAX_PHONES: how many numbers per lead the board bake keeps

REQUIRED = ('propertyStreetAddress', 'propertyZipCode', 'firstName', 'lastName', 'opt')
SLOT_FLAG_COLS = ('_DNC', '_status', '_IsLitigator')    # a phone slot without these cannot be read as clean
SLOTS_PER_OWNER = 5
OPT_NO = ('', 'no', 'false', 'n', '0')              # what the `opt` column says for a row that has not opted out
OPT_YES = ('yes', 'true', 'y', '1', 'opted out', 'opted-out', 'opt out', 'opt-out', 'optout', 'unsubscribed',
           'stop', 'dnc')                            # ... and for one that has; any other value is not guessed at
# a column name only REsimpli's export carries (lower-case): a file that shows any of them is one, however incomplete
# (its street-address names are caught by the sniff in read_export, before this list is read)
OURS = ('propertyzipcode', 'firstname2', 'lastname2', 'fullname2', 'skiptraceddate')
OWNER_COLS = (('firstName', 'lastName'), ('firstName2', 'lastName2'))
# the raw owner line only: owner_clean (Miami), oname and rname (county rolls, lis pendens) are copies of it with
# the Jr / Sr, trust and company words taken out, everything after an '&' dropped, or two people welded into one
OWNER_FIELDS = ('owners', 'owner', 'Owner')
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
# the county roll cuts an owner name at 30 characters (county_leads._owner_partial): the last part of
# a line that long may be a fragment, so it never borrows the first person's surname
NAME_CEILING = 30

COUNT_KEYS = ('rows', 'rows_with_phone', 'unmatched_rows_with_phone', 'unit_mismatch',
              'unit_conflict_same_person', 'entity_rows', 'flagged_lead_rows', 'owner_mismatch',
              'opt_rows', 'opt_person_rows', 'matched_rows', 'numbers_other_owner', 'leads_matched', 'leads_had_phone',
              'leads_new_phone', 'leads_dnc_only_skipped', 'entries_unusable', 'numbers_over_cap',
              'new_numbers', 'new_numbers_dnc', 'new_mobile_clean', 'numbers_unreadable',
              'flags_unexpected')
GLOBAL_KEYS = ('dnc_flagged_numbers', 'dnc_tightened', 'dnc_sidecar_added', 'dnc_sidecar_tightened',
               'dnc_sidecar_marked', 'opt_people_held', 'flagged_also_whitepages', 'cache_resimpli_numbers',
               'resimpli_confirmed', 'resimpli_unconfirmed', 'resimpli_unconfirmed_leads')


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


def name_words(s):
    """The upper-case words of a name in the order they were written, without role suffixes,
    particles and one-letter initials: 'PEREZ, JOSE L TRS' -> ['PEREZ', 'JOSE']. O'CONNOR reads as
    the county roll's OCONNOR."""
    s = CT.SUFFIX.sub(' ', fold(s)).replace("'", '')
    return [w for w in re.sub(r'[^A-Za-z ]', ' ', s).upper().split() if len(w) >= 2 and w not in NAME_STOP]


def person_tokens(s):
    """Upper-case name tokens of ONE person, or an empty set when the string is not a living
    person: a company, a placeholder, an estate or heirs. Role suffixes (TRS, LE, H/E ...) are
    dropped. A generation marker (JR, SR, II ...) rides along as an '@JR' token that person_matches
    compares and then ignores."""
    s = fold(s)
    if not s.strip() or CT.PLACEHOLDER.search(s) or CT.ENTITY.search(s) or NOT_A_PERSON.search(s):
        return set()
    gen = {'@' + m.group(1) for m in GENERATION.finditer(s.upper())}
    words = set(name_words(s))
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
    not one person who is Jose Garcia). A line with a company, trust, estate or placeholder anywhere
    on it is nobody: 'SMITH JOHN & MARY TR' is a trust, not John Smith. A second name of one word
    ('PEREZ JOSE & MARIA') borrows the first person's surname, which is that person's first word on
    a county roll, and nothing else: not their given names, and not into the fragment a line cut at
    the roll's 30-character ceiling ends with."""
    out = []
    for f in OWNER_FIELDS:
        for chunk in str(r.get(f) or '').split(';'):
            if not person_tokens(chunk):
                continue
            parts = CO_OWNER.split(chunk)
            cut = len(chunk.strip()) >= NAME_CEILING
            surname = ''
            for i, part in enumerate(parts):
                t = person_tokens(part)
                if not t:
                    continue
                if surname and len(_bare(t)) == 1 and not (cut and i == len(parts) - 1):
                    t = t | {surname}
                surname = surname or name_words(part)[0]
                if t not in out:
                    out.append(t)
    return out


def lead_is_dead(r):
    return any(r.get(k) for k in DEAD_FLAGS)


# ---------------------------------------------------------------- phones

def norm_number(v):
    """ASCII digits only (a fullwidth digit is folded to its ASCII form first, so it is the same key
    as the number written normally, and no other script's digit is a digit here), and an 11-digit US
    number loses its leading 1, so '13055550101' (which skiptrace.py keeps as returned) and
    '3055550101' are the same number."""
    d = re.sub(r'[^0-9]', '', unicodedata.normalize('NFKC', str(v or '')))
    return d[1:] if len(d) == 11 and d.startswith('1') else d


def parse_number(v):
    """The 10-digit US number in one CSV cell, else ''. '3055550101.0' (a column Excel turned into
    a float) is read; '3.05555E+09' (precision already lost), an extension, or a number that cannot
    be a US line is not guessed at: the caller counts it."""
    s = unicodedata.normalize('NFKC', str(v or '')).strip()
    if re.fullmatch(r'[0-9]+\.0+', s):
        s = s.split('.')[0]
    if re.fullmatch(r'[0-9](\.[0-9]+)?[eE][+-]?[0-9]+', s):
        return ''
    d = norm_number(s)
    return d if re.fullmatch(r'[2-9][0-9]{2}[2-9][0-9]{6}', d) else ''


# a US number written anywhere in a cell of text: '305-555-0101 x22', '(305) 555-0101', '+1 305 555 0101'
NUMBER_IN_TEXT = re.compile(r'(?<![0-9])(?:\+?1[ .-]?)?\(?([2-9][0-9]{2})\)?[ .-]?([2-9][0-9]{2})[ .-]?([0-9]{4})(?![0-9])')


def numbers_in(v):
    """Every US number written anywhere in one cell, as ten digits. parse_number reads a cell that IS
    a number; a cell with two numbers, or one with an extension or a note, is not read as a phone (it
    is counted and never merged), but a flag on its row still holds for the numbers it shows."""
    return {''.join(m) for m in NUMBER_IN_TEXT.findall(unicodedata.normalize('NFKC', str(v or '')))}


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


def opt_word(v):
    """The `opt` cell as a lower-case word; 1.0 and 0.0, what a spreadsheet makes of 1 and 0, are 1 and 0."""
    return re.sub(r'^([01])\.0+$', r'\1', str(v).strip().lower())


def opted_out(x):
    """The row's own `opt` flag. A row with no `opt` cell at all (a line cut short) is not read as "no"."""
    v = x.get('opt')
    return v is None or opt_word(v) not in OPT_NO


class OptHold(list):
    """The people some export has opted out, as (given tokens, surname tokens): each person once, and
    indexed by name token, so a row is compared only with the people it shares a name with. Compared
    with every person, once per pass, a few hundred opt-outs on a few thousand rows took minutes."""

    def __init__(self, people=()):
        super().__init__(dict.fromkeys((frozenset(f), frozenset(l)) for f, l in people))
        self.by_token = {}
        for p in self:
            for t in p[0] | p[1]:
                self.by_token.setdefault(t, []).append(p)

    def near(self, tokens):
        """The held people who share a name token with `tokens` (a match needs a surname in common)."""
        out = {}
        for t in tokens:
            for p in self.by_token.get(t, ()):
                out[id(p)] = p
        return list(out.values())


def opt_people_of(exports):
    """(given tokens, surname tokens) of every person named on a row REsimpli marks opted out, in any
    export. The flag is on the row, not on one of its owners, so both owners of the row count."""
    return OptHold((f, l) for _, _, rows in exports for x in rows if opted_out(x) for _, f, l in row_people(x))


def row_opted_out(x, people, opt_people):
    """The row is opted out: its own `opt` is set, or it names a person another row (in any export)
    has opted out. The same person listed under another property, with another number, has not come
    back: opting out is about the person, whatever REsimpli means by the flag."""
    if opted_out(x):
        return True
    indexed = isinstance(opt_people, OptHold)
    return any(person_matches(f, l, of | ol) for _, f, l in people
               for of, ol in (opt_people.near(f | l) if indexed else opt_people))


def slots_of(rows):
    """Phone slot numbers in the export, read off the header (every row of a DictReader has it; a
    trailing delimiter adds a None key, which is not a column)."""
    return sorted(int(m.group(1)) for k in (rows[0] if rows else ()) if isinstance(k, str)
                  for m in [re.fullmatch(r'Phone_(\d+)', k)] if m)


def owner_of(slot):
    return (slot - 1) // SLOTS_PER_OWNER + 1


def parse_cells(x, slots, n=None, opt=None):
    """Every readable phone on the row. A cell that holds something but no number is counted, and
    never guessed at. `opt`: the row is opted out (row_opted_out), which flags every number on it;
    when not given, the row's own `opt` column decides."""
    out = []
    opt = opted_out(x) if opt is None else opt
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


def flagged_numbers(rows, opt_people=()):
    """Every number any of these rows flags DNC (or lists under an opt-out), matched or not. DNC
    belongs to the number: main() makes it DNC on every cached phone with those digits, on any lead,
    and records it in dnc_scrub.json. The bake applies that sidecar to cached skip-trace phones only,
    not to the Whitepages numbers it appends later."""
    slots = slots_of(rows)
    out = set()
    for x in rows:
        opt = row_opted_out(x, row_people(x), opt_people)
        out.update(c['number'] for c in parse_cells(x, slots, opt=opt) if c['dnc'])
        for i in slots:
            raw = str(x.get('Phone_%d' % i) or '').strip()
            if raw and not parse_number(raw) and (opt or read_flags(x, i)[0]):
                out |= numbers_in(raw)      # not one number, but the numbers written in it are flagged with it
    return out


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


def registry_digits(side):
    """Digits of every number dnc_scrub.json holds as listed on a registry (national or state). The
    board bake flips a cached phone with such a number to DNC whatever the cache says, so a lead whose
    only new number is one of these has nothing dialable to add."""
    return {d for k, v in (side or {}).items() if isinstance(v, dict) and (v.get('national_dnc') or v.get('state_dnc'))
            for d in [norm_number(k)] if len(d) == 10}


def _list(v):
    return v if isinstance(v, list) else []


def wp_numbers(rec):
    """Digits of every number a Whitepages cache record (whitepages_lookup.json, one lead) could put
    on that lead's board row: the deed owners', the residents' and every person-search record's. The
    bake appends these after the lead's skip-trace numbers and cuts the row to MAX_PHONES again, and
    it drops a person-search record that is not the owner; this counts every one, so it can only
    over-count."""
    out = set()
    if not isinstance(rec, dict):
        return out
    res = rec.get('result') if isinstance(rec.get('result'), dict) else {}
    info = res.get('ownership_info') if isinstance(res.get('ownership_info'), dict) else {}
    people = _list(info.get('person_owners')) + _list(res.get('residents'))
    for pr in _list(rec.get('_person')):
        if isinstance(pr, dict):
            people += _list(pr.get('response'))
    for o in people:
        for p in _list(o.get('phones') if isinstance(o, dict) else None):
            d = norm_number(p.get('number') if isinstance(p, dict) else None)
            if len(d) == 10:
                out.add(d)
    return out


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


def merge(rows, idx, results, case_of, addr_of, today=None, flagged=(), pairs=None, state=None,
          registry=(), wp=None, opt_people=()):
    """Add matching rows' phones into `results` in place. Returns counts (no names, no numbers).

    flagged:  numbers to treat as DNC wherever they appear (flagged_numbers over every export). The
              numbers `results` already holds as DNC, on any lead, are added to it here.
    pairs:    optional set collecting every (case, number) this run attributes to a lead (audit()).
    state:    optional dict shared across files, so a lead two files both match counts once.
    registry: numbers dnc_scrub.json holds as registry-listed (registry_digits): DNC on the lead they
              are added to, because that is what the bake will make them, whatever REsimpli says.
    wp:       whitepages_lookup.json, {case: record}: the numbers the bake will append to a lead's
              row after its skip-trace numbers, which the entry has to leave room for.
    opt_people: the people any export has opted out (opt_people_of): a row that names one of them is
              held like a row that is opted out itself."""
    today = today or datetime.date.today().isoformat()
    flagged = set(flagged) | cached_dnc(results) | set(registry)
    st = state if state is not None else {}
    touched = st.setdefault('touched', set())
    dnc_only = st.setdefault('dnc_only', set())
    held_cases = st.setdefault('opt_person_cases', set())
    slots = slots_of(rows)
    n = dict.fromkeys(COUNT_KEYS, 0)
    n['rows'] = len(rows)
    for x in rows:
        people = row_people(x)
        opt = row_opted_out(x, people, opt_people)
        cells = parse_cells(x, slots, n, opt)
        if not cells:
            continue
        n['rows_with_phone'] += 1
        street, row_unit = row_address(x)
        hits = idx.get((street, str(x.get('propertyZipCode') or '').strip()[:5])) or []
        if opt:
            if opted_out(x):
                n['opt_rows'] += 1
            else:
                n['opt_person_rows'] += 1
                # the leads a person-level hold kept a row off, by case number: a namesake can be reviewed
                held_cases.update(case_of(r) for r, u in hits if u == row_unit and not lead_is_dead(r)
                                  and any(person_matches(f, l, t) for g, f, l in people for t in owner_people(r)))
            continue
        if not hits:
            n['unmatched_rows_with_phone'] += 1
            continue
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
            # The board bake sorts a lead's skip-trace numbers (clean, mobile first), cuts them to
            # MAX_PHONES, appends the lead's Whitepages numbers, and cuts the row again. The person-level
            # opt-out checks read that cut row, so a number this run adds must never push one the row
            # already shows off it, a Whitepages number included: the entry is filled up to what is left
            # of MAX_PHONES after the cache's own numbers and the Whitepages numbers it does not hold,
            # and no further. Clean mobiles first, DNC numbers last.
            added.sort(key=lambda p: (p['dnc'], p['type'] != 'Mobile'))
            wp_extra = len(wp_numbers((wp or {}).get(case)) - have)
            room = max(0, PS.MAX_PHONES - len((ent or {}).get('phones') or []) - wp_extra)
            if len(added) > room:
                n['numbers_over_cap'] += len(added) - room
                added = added[:room]
            if not added:
                continue
            if all(p['dnc'] for p in added) and not had:
                # Nothing dialable to add, and nothing on the lead yet. The flag is still recorded
                # (tighten + dnc_scrub.json); the lead gets no entry and no phones, so skiptrace.py can
                # still trace it. A lead that already has phones takes the number as DNC: that costs it
                # nothing, and the bake then shows it DNC where it would append a Whitepages copy as clean.
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


def load_sidecar(path):
    """(dnc_scrub.json as a dict, 'ok'); ({}, 'ok') when there is none; (None, 'unreadable') when it
    exists and cannot be read as a JSON object. A torn sidecar is never replaced by a fresh one."""
    try:
        with open(path, encoding='utf-8') as f:
            cur = json.load(f)
    except FileNotFoundError:
        return {}, 'ok'
    except (OSError, ValueError):
        return None, 'unreadable'
    return (cur, 'ok') if isinstance(cur, dict) else (None, 'unreadable')


def sidecar_plan(cur, flagged, results, today):
    """dnc_scrub.json (`cur`, as load_sidecar read it) with REsimpli's flagged numbers merged in.
    -> (doc or None, added, tightened, marked). make_tracker looks a cached number up by its exact
    stored string, so each flagged number gets a key in the 10-digit spelling, in the 11-digit
    spelling a provider can return, and in the spelling of every cached phone that has those digits.
    Every record for a flagged number carries the REsimpli marker, including one the registry lane
    already holds as listed: tracerfy_mcp's paid 30-day re-scrub leaves a marked, listed record
    alone, so a later registry miss cannot replace an opt-out or litigator flag. A registry verdict
    is only ever tightened. doc is None when there is nothing to write."""
    keys = set(flagged) | {'1' + n for n in flagged}
    for ent in results.values():
        for p in phones_of(ent):
            if norm_number(p.get('number')) in flagged:
                keys.add(str(p.get('number')))
    added = tightened = marked = 0
    for k in sorted(keys):
        v = cur.get(k)
        if isinstance(v, dict):
            if not (v.get('national_dnc') or v.get('state_dnc')):
                v['national_dnc'] = True
                v['resimpli'] = today
                v['source'] = 'resimpli'
                tightened += 1
            elif not v.get('resimpli'):
                v['resimpli'] = today               # the verdict stays the registry's; only the marker is added
                marked += 1
        else:
            # REsimpli says DNC without naming the registry; the seam needs one of the two flags
            cur[k] = {'national_dnc': True, 'state_dnc': False, 'states': [], 'case': '',
                      'checked': today, 'source': 'resimpli', 'resimpli': today}
            added += 1
    return (cur if added or tightened or marked else None), added, tightened, marked


# ---------------------------------------------------------------- files

def read_export(path):
    """The rows of one REsimpli skip-trace export. A file whose header says it is not one (a stray
    SkipTrace_*.csv) raises a skippable SyncError. A file that is one but cannot be read in full (not
    UTF-8, a flag column missing, a read error) raises a hard one: skipping it would drop its DNC
    flags while the other exports still merge their numbers as clean."""
    name = os.path.basename(path)
    try:
        with open(path, 'rb') as fb:
            raw = fb.readline(1 << 20)
            sniff = (raw + fb.read(1 << 16)).lower()
    except OSError as e:
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__))
    not_utf8 = SyncError('%s is not UTF-8 text (was it re-saved in Excel? download it from REsimpli again)' % name)
    head = re.split(rb'[\r\n]', raw, maxsplit=1)[0]        # the header line, whatever the line endings
    try:
        first = head.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise not_utf8
    if '\x00' in first:                                     # UTF-16 without a byte-order mark
        raise not_utf8
    try:
        fields = next(csv.reader(io.StringIO(first)), [])
    except csv.Error as e:
        raise SyncError('%s has a header line that cannot be read as CSV (%s)' % (name, e))
    slots = [int(m.group(1)) for c in fields for m in [re.fullmatch(r'Phone_(\d+)', c)] if m]
    if not slots and 'propertyStreetAddress' not in fields:
        if b'propertystreetaddress' in sniff or b'phone_1' in sniff:
            # its own column names, but not as comma-separated columns on the first line: re-saved with
            # ';' or tab as the delimiter, or with a 'sep=,' line above the header. Its DNC flags would
            # be missing from the merge, so it is not "some other CSV".
            raise SyncError('%s has REsimpli\'s column names but its header does not read as comma-separated '
                            'columns (re-saved in Excel with another delimiter, or a line above the header? '
                            'download it from REsimpli again)' % name)
        low = [c.strip().lower() for c in fields]
        if not any(c in OURS or re.match(r'phone[ _-]?[0-9]', c) for c in low):
            raise SyncError('%s is not a REsimpli skip-trace export' % name, skippable=True)
        # some of REsimpli's own column names, but not the phone or address ones (renamed by a re-save or a
        # format change): it falls through to the missing-columns refusal below rather than being skipped,
        # since its DNC flags would be missing while an older export lists the same numbers clean
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
            rows = list(csv.DictReader(f))
    except UnicodeDecodeError:
        raise not_utf8
    except (OSError, csv.Error) as e:
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__))
    # `opt` decides whether a whole row (and, through opt_people_of, a person) is held. A value this
    # tool does not know how to read is not guessed at: 'None' on every row would flag every number in
    # the file as DNC across the whole cache, and a value read as "no" would let an opt-out through.
    kinds = {str(x['opt']).strip() for x in rows if x.get('opt') is not None}
    odd = sorted(v for v in kinds if opt_word(v) not in OPT_NO + OPT_YES)
    if odd:
        shown = ', '.join(repr('<number>' if re.search(r'\d', v) else v[:12]) for v in odd[:3])
        raise SyncError('%s has %d kind%s of value in its `opt` column that this tool cannot read as yes or no '
                        '(%s%s). Nothing was read: a guess would either opt out the whole list or let an opt-out '
                        'through. If REsimpli really writes that word, tell Claude and it will be added. To go on '
                        'now, change those cells to Yes (opted out) or No in a copy of the file and run that copy; '
                        'if the cells look like a corrupt download, download the export from REsimpli again.'
                        % (name, len(odd), '' if len(odd) == 1 else 's', shown, ', ...' if len(odd) > 3 else ''))
    return rows


def discover(import_dir):
    home = os.path.expanduser('~')
    pats = [os.path.join(glob.escape(os.path.join(home, d)), 'SkipTrace_*.csv')
            for d in ('Downloads', 'Desktop', os.path.join('OneDrive', 'Desktop'))]
    pats.append(os.path.join(glob.escape(import_dir), '*.csv'))
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


def load_wp(path):
    """whitepages_lookup.json as {case: record}; {} when there is none. It decides how many numbers a
    lead's row still has room for, so one that exists and cannot be read stops the run."""
    try:
        with open(path, encoding='utf-8') as f:
            wp = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise SyncError('whitepages_lookup.json at %s cannot be read (%s); it decides how many numbers a lead '
                        'can take, so nothing was written. An interrupted whitepages_lookup.py run can leave it '
                        'torn: restore a copy of it, or run whitepages_lookup.py again for the leads it lost, '
                        'then run this again.' % (path, type(e).__name__))
    if not isinstance(wp, dict):
        raise SyncError('whitepages_lookup.json at %s is not a JSON object; nothing written' % path)
    return wp


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
    """Write `obj` beside `path` under a temp name of this tool's own (tracerfy_mcp.py and skiptrace.py
    use '.tmp', so those never write one file with this) that no other run of it shares, and flush it to
    disk before anything replaces `path`. A write that fails part-way removes what it wrote: a
    half-written phone cache is not left behind."""
    tmp = '%s.resimpli.%s.tmp' % (path, os.urandom(4).hex())
    fh = open(tmp, 'x', encoding='utf-8')       # a name another run already holds fails here and touches nothing
    try:
        with fh:
            json.dump(obj, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        drop([tmp])
        raise
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
    named = {os.path.abspath(f) for f in a.files}
    if a.files:
        # The copies kept of earlier exports hold their opt-outs and their DNC flags: naming one file must
        # not narrow who is held, so they are read too (and audit() sees the numbers they attached).
        kept = sorted(glob.glob(os.path.join(glob.escape(import_dir), '*.csv')))
        files = list(a.files) + [f for f in kept if os.path.abspath(f) not in named]
    else:
        files = discover(import_dir)
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
            if os.path.abspath(f) in named or not err.skippable:    # a file named on the command line, or one that
                print('REFUSED:', err)                              # is an export we cannot read in full
                if os.path.abspath(f) not in named:
                    print('That file is in %s. Its DNC flags would be missing from the merge, so nothing was read '
                          'further. Move or delete that file (or download it again from REsimpli), then run again.'
                          % os.path.dirname(os.path.abspath(f)))
                return 2
            print('SKIPPED:', err)          # a stray SkipTrace_*.csv must not block the real exports
            skipped.append(os.path.basename(f))
    if not exports:
        print('no usable REsimpli export found')
        return 1

    res_path = S.RESULTS
    here = os.path.dirname(os.path.abspath(res_path))
    side_path = os.path.join(here, 'dnc_scrub.json')
    wp_path = os.path.join(here, 'whitepages_lookup.json')
    bdir = os.path.join(P.DEALFLOW_DIR, 'backups')
    sig0, side_sig0 = cache_sig(res_path), cache_sig(side_path)         # each taken before its file is read
    try:
        results = load_cache(res_path, a.create)
        wp = load_wp(wp_path)
    except SyncError as e:
        print('REFUSED:', e)
        return 2
    side_cur, side_state = load_sidecar(side_path)
    today = datetime.date.today().isoformat()
    try:
        leads = S.load_all_leads()
    except (OSError, ValueError) as e:
        print('REFUSED: the board leads cannot be read (%s); nothing written. Run this from the repo folder '
              'that holds leads_final.json.' % type(e).__name__)
        return 2
    idx = build_index(leads, S._case, S._propaddr)

    opt_people = opt_people_of(exports)
    flagged = set()
    for f, h, rows in exports:
        flagged |= flagged_numbers(rows, opt_people)
    registry = registry_digits(side_cur)
    wp_all = set().union(*(wp_numbers(rec) for rec in wp.values()))
    print('opt-out hold: %d people, from %d exports (%d files found)' % (len(opt_people), len(exports), len(files)))

    status = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'dry_run': a.dry_run,
              'board_leads_indexed': sum(len(v) for v in idx.values()), 'whitepages_cases': len(wp),
              'skipped_files': skipped, 'files': []}
    total = dict.fromkeys(COUNT_KEYS, 0)
    state, pairs = {}, set()
    for f, h, rows in exports:
        n = merge(rows, idx, results, S._case, S._propaddr, today, flagged, pairs, state, registry, wp,
                  opt_people)
        status['files'].append({'file': os.path.basename(f), 'sha256': h[:16], **n})
        for k in COUNT_KEYS:
            total[k] += n[k]
        print('%s: %d rows, %d with phones, %d leads matched, %d new numbers (%d DNC-flagged), '
              '%d leads had no phone'
              % (os.path.basename(f), n['rows'], n['rows_with_phone'], n['leads_matched'],
                 n['new_numbers'], n['new_numbers_dnc'], n['leads_new_phone']))
    tightened = tighten(results, flagged)
    aud, bad_cases = audit(results, pairs)
    side_doc, side_added, side_tight, side_marked = None, 0, 0, 0
    if side_state == 'unreadable':
        # The sidecar is what keeps a flag when skiptrace.py later replaces a lead's entry wholesale, and
        # the only record of one on a lead with no entry (DNC-only) or on no lead at all (an unmatched
        # row). With it unreadable none of that can be written, so a run that has anything to flag stops.
        if flagged:
            print('REFUSED: dnc_scrub.json exists but cannot be read, and this run has %d numbers to flag as DNC '
                  'that would then be recorded only in the phone cache, which skiptrace.py can replace. Nothing '
                  'was written. Restore it from a copy (DEALFLOW\\backups holds dnc_scrub.pre-resimpli-*.json only '
                  'after an earlier run of this tool, so there may be none), then run again. Do not just move it '
                  'aside: a new sidecar would hold only REsimpli\'s flags, and every registry verdict the Tracerfy '
                  'DNC lane recorded in the old one would be gone without a word. Until it is fixed the board bake '
                  'cannot apply registry verdicts at all.' % len(flagged))
            return 2
    else:
        side_doc, side_added, side_tight, side_marked = sidecar_plan(side_cur, flagged, results, today)
    total.update(dnc_flagged_numbers=len(flagged), dnc_tightened=tightened, dnc_sidecar_added=side_added,
                 dnc_sidecar_tightened=side_tight, dnc_sidecar_marked=side_marked, opt_people_held=len(opt_people),
                 flagged_also_whitepages=len(flagged & wp_all), **aud)
    status['total'] = total
    status['dnc_scrub_json'] = side_state
    status['resimpli_unconfirmed_cases'] = bad_cases
    status['opt_person_cases'] = sorted(c for c in state.get('opt_person_cases', ()) if real_case(c))
    print('TOTAL')
    for k in COUNT_KEYS + GLOBAL_KEYS:
        print('  %-28s %d' % (k, total[k]))
    if side_state == 'unreadable':
        print('WARNING: dnc_scrub.json exists but cannot be read; left untouched. The board bake cannot '
              'apply registry verdicts until it is fixed. This run had nothing to flag.')
    if aud['resimpli_unconfirmed']:
        print('NOTE: %d cached REsimpli numbers on %d leads are not attached by any export this run read (an '
              'earlier, looser merge, or an earlier export that is not in %s). They are still in the cache and '
              'on the board; nothing was removed. The hold on opted-out people is built from the exports read '
              'only.' % (aud['resimpli_unconfirmed'], aud['resimpli_unconfirmed_leads'], import_dir))
    if total['flagged_also_whitepages']:
        print('NOTE: %d numbers REsimpli flags are also Whitepages numbers of some lead. The board bake appends '
              'a Whitepages number with no DNC flag unless the lead\'s own entry holds it.'
              % total['flagged_also_whitepages'])

    if a.dry_run:
        print('DRY RUN - nothing written')
        return 0

    cache_changed = bool(total['new_numbers'] or tightened)
    wrote, tmps = [], []
    try:
        # 1. prepare: both files are written beside the originals; nothing is replaced yet. Each temp
        #    is listed as soon as it exists, so a failure part-way leaves none behind.
        side_tmp = cache_tmp = None
        if side_doc is not None:
            side_tmp = write_tmp(side_path, side_doc)
            tmps.append(side_tmp)
        if cache_changed:
            cache_tmp = write_tmp(res_path, results)
            tmps.append(cache_tmp)
        # 2. check that no other tool wrote either file while this ran (skiptrace.py, contact_trust.py
        #    --write and tracerfy_mcp.py each rewrite a file whole from what they loaded at start),
        #    then back up what is about to be replaced. A refused run leaves no backup behind.
        if tmps and (cache_sig(res_path) != sig0 or cache_sig(side_path) != side_sig0):
            print('CHANGED: %s or dnc_scrub.json was modified while this ran (skiptrace.py, contact_trust.py '
                  'or tracerfy_mcp.py?). Nothing was replaced. Wait for it to finish and run again.'
                  % os.path.basename(res_path))
            drop(tmps)
            return 3
        if side_tmp:
            backup(side_path, 'dnc_scrub', bdir)
        bak = backup(res_path, 'skiptrace_results', bdir) if cache_tmp else None
        # 3. replace: the sidecar first (it only ever tightens), then the cache
        if side_tmp:
            os.replace(side_tmp, side_path)
            wrote.append('dnc_scrub.json')
            print('dnc_scrub.json updated (%d entries added, %d tightened, %d marked)'
                  % (side_added, side_tight, side_marked))
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
    except BaseException:
        drop(tmps)
        raise
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


def cli(argv=None):
    """main() with one last net: an error nothing above anticipated ends as one line and exit 4, naming
    the error type and where it happened but never its message (a KeyError's message can be a name).
    What main() already printed says what was written before it."""
    try:
        return main(argv)
    except Exception as e:                  # a SystemExit is not an Exception and passes through
        tb = traceback.extract_tb(e.__traceback__)
        where = '%s:%s in %s' % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].name) if tb else '?'
        print('FAILED: unexpected error (%s at %s). The lines above say what, if anything, was written; '
              'send them back.' % (type(e).__name__, where))
        return 4


if __name__ == '__main__':
    sys.exit(cli())
