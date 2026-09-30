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
    minus the Whitepages numbers it does not. Within a row, mobiles take the room first; the numbers
    REsimpli flags DNC go in last, after every callable number of the run (settle()); what does not fit
    is counted (numbers_over_cap) and not added. A number Whitepages also lists for the lead takes the
    place its Whitepages copy would have had, so it costs the row nothing (a row already over the
    limit takes nothing more, that number included). Every Whitepages number of
    the lead counts, including the person-search records the bake would drop, so this can only leave
    more room than needed. A callable number is placed as its row is read, so when two rows or two
    files match the same lead and the room is short for both, the one read first takes it (a landline
    read first can beat a mobile read later); nothing already on the lead is displaced by it.
  * Rows with no matching lead are counted, never added as leads. They have no case number, so no
    bankruptcy / stay check can run on them; a new lead source is a product decision.
  * Numbers are ADDED. An existing number, entry, or provider is never removed or replaced, and a
    number already in the cache is recognised however it is written ('13055550101' = '3055550101').
    A number REsimpli flags DNC is added last (settle()): after every callable number of the run has
    been placed, only to a lead that has phones by then, and only into the room that is left, in a
    fixed order. Which flagged numbers a lead takes therefore does not depend on the order the rows or
    the files come in, and a flagged number never takes the room a callable one wants. On a lead with
    no phone, a flagged number on its own would give a phone list of nothing dialable, which stops
    skiptrace.py from ever tracing it (it traces cases that have no entry, and retries a no-hit entry
    only while its phones are empty), so that lead gets no entry (leads_dnc_only_skipped; the flag is
    still recorded in dnc_scrub.json). On a lead that has phones the number is stored as DNC, and the
    bake then shows it DNC where it would otherwise append a Whitepages copy of it as clean; a number
    Whitepages also lists for the lead takes the place that copy would have had, so it adds nothing to
    the row and is taken unless the row is already over the limit. A lead that gets its first number here is also not
    traced by Tracerfy afterwards, whatever that number's type.
  * Emails are not merged: they are unverified and would feed first-touch email.

DNC IS ABOUT THE NUMBER, AND IT ONLY TIGHTENS
A phone counts as clean only when REsimpli says so in plain words (DNC "No", status "[]", litigator
blank or No). Yes, blank, an unrecognised value, or any other status is DNC. A cell that is not a
single number (two numbers, or one with an extension or a note) is not merged, but when its row or
slot is flagged, every number written in it is flagged too, whatever is written between its digit
groups or around them (numbers_in: dashes of every kind, a slash, a comma, an underscore, letters,
spaces inside the brackets, a tab, a zero-width space, digits of another script, an extension after
it, two numbers to a cell); a flag that is lost is a homeowner called. Digits with nothing between
them are one group: twelve of them run together are not searched for a number, and only the first 60
groups of a cell are read (a number after the 60th is not flagged). A flagged cell that holds no
number numbers_in can find is counted (flag_cells_unread) and said: its flag lands on nothing. A row
with `opt` set (Yes / True / Y / 1, or Opted Out / Opt-Out / Unsubscribed / Stop / DNC) is opted out:
its numbers are flagged and it is not merged. So is a row, in any export this run reads, that names a
person such a row names (same surname and given name, either owner slot, either word order): opting
out is about the person, so the same person listed under another property with another number has not
come back (opt_person_rows; opt_person_cases lists the leads it kept a row off, opt_people_held how
many people the hold was built from). A namesake is held too; that is the safe side. An opted-out row
with an owner (either one) this tool cannot read as a person (a company, an initial for a first name,
a name in one column only) is counted (opt_rows_unnamed) and said: its own numbers are flagged, but
that owner cannot be held on other rows or in other exports. `opt` blank or No / False / N / 0 is
not opted out; a value that is none of these refuses the file, since reading it as "no" lets an
opt-out through and reading it as "yes" flags every number in the list (1.0 and 0.0, what a
spreadsheet makes of 1 and 0, read as 1 and 0). A file with no `opt` column, or no _DNC, _status or
_IsLitigator column for a phone slot, is refused, not read as clean. A file with a row that is short of
cells is refused (see below); opted_out() itself still reads a missing `opt` cell as opted out.
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
verdict is only tightened. A dnc_scrub.json that cannot be read (or opened: another program has it
open, and the advice then is to close it, not to restore a copy) is left exactly as found and stops
every run (exit 2, a dry run included), whether or not the run has a number to flag: it holds the
registry's verdicts, which every number this run adds is checked against, and it is what keeps a flag
when skiptrace.py replaces an entry and the only record of one on a lead with no entry or on no lead.
The bake does not apply the sidecar to phones it appends later from other sources (Whitepages numbers).

WHICH FILES
With no path: every SkipTrace_*.csv in Downloads / Desktop plus DEALFLOW_DIR/imports/resimpli/. With
paths: those files plus the copies in DEALFLOW_DIR/imports/resimpli/, because the hold on an opted-out
person and the DNC flags of every earlier export live in those copies, and naming one file must not
narrow them. All of them are processed, not the newest: the merge is add-only and dedupes by number,
so re-reading a file adds nothing and no file has to be picked.
A stray text file that has none of REsimpli's column names is skipped by name. An export that
cannot be read in full is REFUSED by name and stops the run, even when the other exports are fine:
the numbers it lists as DNC would be missing from the merge while an older export lists them clean.
That covers a file that is not UTF-8 (do not open and re-save it in Excel), one with a flag column
missing, one with any of REsimpli's own column names that is not a complete export (a re-save that
renamed the phone or address columns), one whose `opt` column holds a value this tool cannot read as
yes or no, one with REsimpli's column names that do not read as comma-separated columns on the first
line (a re-save with another delimiter, or with a 'sep=' line above the header), a header line
csv cannot parse, a quote that is never closed or has text right after it (the csv is read strictly:
one stray quote would otherwise swallow the rows below it, an opt-out and a DNC flag with them), and a
row with more or fewer cells than the header has columns, an empty extra one included (a comma put in
a cell, or taken out, moves every column after it, so a number's flags and the `opt` cell are read
from a neighbour's place, and a download cut off in the middle of a row is short too, unless it stops
inside that row's last cell; a comma at the end of a row cannot be told from that, and a spreadsheet
that saved the file again may leave rows longer or shorter: use the file as REsimpli downloaded it). The
refusal says not to delete the line it names: the person on it would lose their opt-out and flags
without a word, and a file cut short is missing the rows after the cut as well; download the export
again, and if the new file is refused the same way, change nothing and send Claude the REFUSED line. The
refusals for a file that is not UTF-8 text and for a header that does not read as columns say the same,
and so do the ones a hand edit cannot mend: the missing-column one, a file that cannot be opened, and a
dnc_scrub.json that cannot be read or opened. Found files are copied into
DEALFLOW_DIR/imports/resimpli/ (outside the repo and outside OneDrive) after the data is written; the
originals stay put.

OUTPUT
Counts on stdout. DEALFLOW_DIR/resimpli_sync_status.json holds the same counts (per file: what that file
added that is callable; the flagged numbers are placed after every file has been read, so they are in
the totals only) plus the case numbers of any unconfirmed leads (unconfirmed_cases) and of the leads a
person-level hold kept a row off (opt_person_cases), and how many other exports a one-file run did not
read (unread_files). No names, no phone numbers. Exit 0 = done (or nothing to do), 1 = no
usable export, 2 = refused (an export, the cache, whitepages_lookup.json, the board leads, or a torn
or unopenable dnc_scrub.json; nothing written; a command-line usage error also exits 2), 3 =
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
  * A line that joins two people with a comma, ' Y ', a slash, a plus, W/ or C/O (Broward's PA-page
    fallback keeps ',' and '/') is read as one person, so a name made of one owner's given name and the
    other's surname can match; the address still has to match. Only the role suffixes in contact_trust.py's
    SUFFIX (TRS, TRUSTEE, LE, H/E ...) are dropped, so the person they follow still matches (TR, a trust,
    is nobody). TTEE and PERS REP are not in that list: they stay on the name as extra words, which does
    not stop that person from matching, but a second name that ends with one ('PEREZ JOSE & MARIA TTEE')
    keeps its own words and no longer borrows the first person's surname, so a Maria Perez row does not
    match it (a missed match, the safe side). JUNIOR, SENIOR and 2ND are not read as generation markers.
  * The person-level opt-out checks read the row the bake cuts to MAX_PHONES. tighten() turns a cached
    number DNC, the bake sorts it last, and on an entry that holds more than MAX_PHONES numbers
    (skiptrace.py's _collect never caps) that can cut it off the row and pull a different number on,
    so an identity the ledger holds as '#<number>' (an inbound STOP) may no longer be found on the row.
    It predates this tool (the Tracerfy DNC lane does the same) and belongs to the bake and the
    suppression owner: reported, not fixed. Nothing here adds a number past the limit.
  * A header that names a column twice (csv keeps the last one, so a flag or an opt-out in the first is
    lost) or spells a phone NUMBER column differently (`phone_1`, `Phone_01`, `Phone_1 `) is not caught:
    no number is read from that column, so a flag on it lands on nothing. A misspelled `opt` column, or
    a misspelled `_DNC` / `_status` / `_IsLitigator` column of a slot whose number column is spelled
    right, is refused as missing. A downloaded export has none of this; a hand-edited one could.
  * A quote that is never closed, or that has text right after it, is refused (the csv is read strictly),
    and so is a row with more or fewer cells than the header has columns. A quote that opens on one line
    and is closed by another on a later line is refused when the two are in different columns (the merged
    row then has more or fewer cells than the header) but not when both are in the same column: the rows
    between them read as one cell, opt-outs and flags included. Only a hand-edited or damaged export does
    any of this.
  * A download that stopped exactly at the end of a line, or inside the last cell of a row, reads as a
    complete, shorter export: the rows after the stop, their opt-outs and flags with them, are not there and
    nothing says so (a browser keeps a .crdownload or .part file until the download finishes, so a file
    named SkipTrace_*.csv is normally whole). The last column of REsimpli's 09-28 export is listPurchaseSource,
    not `opt` or a flag, so a stop inside that cell loses nothing this tool reads beyond the rows after it;
    a file whose last column were `opt` or a flag would lose that value too. A line of only spaces, or a
    Ctrl-Z end-of-file mark on a line of its own, is a one-cell row and is refused; a Ctrl-Z joined to the
    end of the last row becomes part of its last cell and reads; only a line with nothing on it is skipped.
  * A file a spreadsheet saved again is no longer what REsimpli sent, and the cell count cannot tell: a
    spreadsheet that pads every row to the widest one hides a shifted row (it reads as the right width), and
    one that trims trailing empty cells turns every such row into a refusal. Neither has been tried against
    the real export here. The refusals say to use the file as downloaded, and the `opt` refusal says to edit
    in a text editor, not in Excel.
  * tracerfy_mcp.py reads a dnc_scrub.json it cannot parse as empty, and its DNC lane then rewrites the
    whole file, dropping every record, REsimpli's included. This tool refuses to run on a torn sidecar;
    tracerfy_mcp.py is another script's.
  * A cached number stored as a float (3055550101.0, not a string) is not recognised: it is not
    tightened or keyed, and the same number can be added again. skiptrace.py stores strings.
  * REsimpli's `opt` becomes a number-level DNC flag and a person-level hold in this tool. It never
    reaches the opt-out ledger (optouts.json), which is another session's surface, and it does not
    touch what Tracerfy already cached for that person on her own lead. If `opt` means "do not contact
    this person", that is a larger gap and a policy call for Alejandro.
  * The person-level hold is rebuilt on every run from the exports the run reads (the ones passed plus
    the copies in DEALFLOW_DIR/imports/resimpli/). Neither prep_desktop.py (it carries the gitignored
    files at the repo root: the phone cache, dnc_scrub.json) nor make_transfer.py (the phone cache; its
    lists do not name dnc_scrub.json) carries that folder, and dnc_scrub.json holds the flagged numbers,
    not the people, so a machine set up from a transfer holds only the exports it has until the earlier
    ones are copied there. opt_people_held says how many people the hold was built from. The NOTE about
    unconfirmed numbers fires only when the cache holds numbers those earlier exports attached.
  * A run that is killed between writing its temp files and replacing the cache or the sidecar leaves
    <file>.resimpli.<random>.tmp behind. They are gitignored and no later run removes them: delete them
    by hand. Two runs at the same time are caught only by the changed-file check, which can miss two
    that finish together (the later replace wins and both exit 0); a lock file would close that.
  * The bake appends a lead's Whitepages numbers with phdnc False and applies dnc_scrub.json to cached
    skip-trace phones only, so a number REsimpli flags that Whitepages also lists on a lead whose entry
    lacks it reaches the baked row as clean. flagged_also_whitepages counts those numbers; the seam is
    foreclosure_leads.make_tracker's, which this tool does not touch.
  * A cell that starts with an apostrophe (Excel's text marker) or holds a number in another script's digits
    is not a number to add (it is counted in numbers_unreadable); when its row or slot is flagged, the
    number written in it is still flagged. A callable number written that way on a clean slot is lost
    to the merge, which is the safe side.
  * The NOTE that counts exports a one-file run did not read counts files, not distinct exports: the same
    export saved in Desktop and OneDrive\\Desktop counts twice, and anything it cannot open counts as not read.
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
OWNER_FULL = ('fullName', 'fullName2')       # the same owners' whole names: never read as a name, only to see that an owner is there
# the raw owner line only: owner_clean (Miami), oname and rname (county rolls, lis pendens) are copies of it with
# the Jr / Sr, trust and company words taken out, everything after an '&' dropped, or two people welded into one
OWNER_FIELDS = ('owners', 'owner', 'Owner')
DEAD_FLAGS = ('ownerMismatch', 'lpDismissed', 'lpClosed')
# What a refusal says when the fault is in the file's shape, which no edit by hand can mend without a person losing their
# opt-out. Both halves go together: the first stops a new download saved beside the old file (both are read, and the old one
# keeps refusing), the second is the way out when the new download is refused the same way, so the advice never ends in a loop.
REDOWNLOAD = ('Download the export from REsimpli again and put the new file over this one (a copy beside it would be read '
              'too, and this one would keep refusing)')
STUCK = 'If the new file is refused the same way, change nothing and send Claude this line (it has no homeowner names or numbers)'

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
SETTLE_KEYS = ('new_numbers_dnc', 'leads_dnc_only_skipped')     # counted by settle() once the run is read, not per file
GLOBAL_KEYS = ('dnc_flagged_numbers', 'dnc_tightened', 'dnc_sidecar_added', 'dnc_sidecar_tightened',
               'dnc_sidecar_marked', 'opt_people_held', 'flagged_also_whitepages', 'cache_resimpli_numbers',
               'resimpli_confirmed', 'resimpli_unconfirmed', 'resimpli_unconfirmed_leads', 'flag_cells_unread',
               'opt_rows_unnamed')


class SyncError(Exception):
    """skippable: a stray file that is plainly not a REsimpli export. Anything else that stops a file
    from being read in full is not skippable: its DNC flags would be silently missing from the merge.
    kind: 'io' when the file could not be opened or read at all (open in Excel, gone): the refusal
    then says to close it, not that its content is wrong. 'opt' when its `opt` column holds a word
    this tool cannot read: the refusal itself says how to fix those cells in that file, so the note
    after it does not tell the operator to move or replace the file. 'shape' when the file does not
    read as a whole REsimpli export (not UTF-8 text, a header that is not comma-separated columns, a
    quote csv cannot parse, a row with the wrong number of cells): there is no fix by hand that keeps
    every person, and deleting the line the refusal names drops that person's opt-out and flags, so
    the refusal says to download the export again (and what to do if that is refused too) and the
    note says to replace the file."""
    def __init__(self, msg, skippable=False, kind=''):
        super().__init__(msg)
        self.skippable = skippable
        self.kind = kind


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


def unread_owners(x):
    """How many of the row's owner slots hold a name this tool cannot read as one person (a company, an
    initial for a first name, a name in one column only, a whole name with neither part). An empty slot
    is not one."""
    named = {g for g, _, _ in row_people(x)}
    return sum(1 for g, cols in enumerate(OWNER_COLS, 1)
               if g not in named and any((x.get(k) or '').strip() for k in cols + (OWNER_FULL[g - 1],)))


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
    a float) is read. Anything that is not digits and phone punctuation (spaces, + ( ) - .) is not
    guessed at, and the caller counts it: '3.05555E+09' (precision already lost), an extension, a
    note, a letter, another script's digit (stripped, it would leave a different number that still
    has ten digits), and a number that cannot be a US line."""
    s = unicodedata.normalize('NFKC', str(v or '')).strip()
    if re.fullmatch(r'[0-9]+\.0+', s):
        s = s.split('.')[0]
    if re.search(r'[^0-9 ()+.\-]', s):
        return ''
    d = norm_number(s)
    return d if re.fullmatch(r'[2-9][0-9]{2}[2-9][0-9]{6}', d) else ''


# A US number written anywhere in a cell: '305-555-0101 x22', '(305) 555-0101', '+1 305 555 0101', '305x555x0101'.
# This reader decides what gets FLAGGED, and a flag has to hold however the number was typed; parse_number,
# which decides what gets ADDED, is the strict one.
NANP = re.compile(r'[2-9][0-9]{2}[2-9][0-9]{6}')
MAX_GROUPS = 60             # digit groups read from one cell: a phone cell has a handful, a longer one is not a phone cell


def numbers_in(v):
    """Every US number written in one cell, as ten digits. parse_number reads a cell that IS a number
    and decides what gets ADDED; a cell with two numbers, an extension, a note or punctuation it does
    not know is never added, but a flag on its row or slot still holds for the numbers it shows, and
    this decides which. So it is deliberately generous: a number is ten digits (eleven with a leading
    1) that make up consecutive digit groups of the cell, whatever is written between the groups and
    whatever comes before or after them: '305-555-0101 x12', '( 305 ) - 555 - 0101 ext 2', '305x555x0101',
    '305 -- 555 -- 0101 / 305-555-0102', '1 (305) 555-0101', '30 55 55 01 01'. Another script's digits
    are read as the digits they are (parse_number refuses them). A flag that lands on a digit string
    nobody has is harmless; a flag that is lost is a homeowner called. Digits with nothing between
    them are one group and are read only as a whole number, so twelve of them run together are not
    searched for one, and only the first MAX_GROUPS groups of a cell are read."""
    s = unicodedata.normalize('NFKC', str(v or ''))
    s = ''.join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in s)
    groups = re.findall(r'[0-9]+', s)[:MAX_GROUPS]
    out = set()
    for i in range(len(groups)):
        d = ''
        for g in groups[i:i + 11]:
            d += g
            if len(d) == 10 and NANP.fullmatch(d):
                out.add(d)
            elif len(d) == 11 and d[0] == '1' and NANP.fullmatch(d[1:]):
                out.add(d[1:])
    return out


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


def flagged_numbers(rows, opt_people=(), n=None):
    """Every number any of these rows flags DNC (or lists under an opt-out), matched or not. DNC
    belongs to the number: main() makes it DNC on every cached phone with those digits, on any lead,
    and records it in dnc_scrub.json. The bake applies that sidecar to cached skip-trace phones only,
    not to the Whitepages numbers it appends later. `n` (a dict): flag_cells_unread counts the flagged
    cells that hold no number numbers_in can find, whose flag therefore lands on nothing."""
    slots = slots_of(rows)
    out = set()
    for x in rows:
        opt = row_opted_out(x, row_people(x), opt_people)
        out.update(c['number'] for c in parse_cells(x, slots, opt=opt) if c['dnc'])
        for i in slots:
            raw = str(x.get('Phone_%d' % i) or '').strip()
            if raw and not parse_number(raw) and (opt or read_flags(x, i)[0]):
                found = numbers_in(raw)     # not one number, but the numbers written in it are flagged with it
                out |= found
                if not found and n is not None:
                    n['flag_cells_unread'] += 1
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


def fit(count, wp_left, cands):
    """Which of `cands` (phone dicts, in the order they are wanted) one entry can take, and which it
    cannot. The entry holds `count` numbers now; the bake will append the `wp_left` Whitepages numbers
    it does not hold and cut the row to MAX_PHONES, and nothing this tool adds may push a number off
    that row. A number takes one place on the row, except one Whitepages also lists for the lead: it
    takes the place its Whitepages copy would have had, so it adds nothing and is taken as long as the
    row is not already over the limit. -> (taken, refused)"""
    wp_left = set(wp_left)
    taken, refused = [], []
    for p in cands:
        room = PS.MAX_PHONES - count - len(taken) - len(wp_left)
        if room + (p['number'] in wp_left) < 1:
            refused.append(p)
            continue
        taken.append(p)
        wp_left.discard(p['number'])
    return taken, refused


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
    state:    optional dict shared across files, so a lead two files both match counts once, and the
              numbers REsimpli flags DNC (state['dnc_held'], case -> numbers) wait there for settle().
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
    held_cases = st.setdefault('opt_person_cases', set())
    dnc_held = st.setdefault('dnc_held', {})
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
                p = {'number': c['number'], 'type': c['type'], 'carrier': '', 'dnc': c['dnc'], 'src': 'resimpli'}
                # A number REsimpli flags DNC is not added here. It is set aside for settle(), which places
                # it after every callable number of the run: a flagged number never takes the room a callable
                # one wants, and which flagged numbers a lead takes does not depend on the order the rows and
                # the files come in.
                (dnc_held.setdefault(case, []) if c['dnc'] else added).append(p)
            # The board bake sorts a lead's skip-trace numbers (clean, mobile first), cuts them to
            # MAX_PHONES, appends the lead's Whitepages numbers, and cuts the row again. The person-level
            # opt-out checks read that cut row, so a number this run adds must never push one the row
            # already shows off it, a Whitepages number included: fit() fills the entry up to what is
            # left of MAX_PHONES after the cache's own numbers and the Whitepages numbers it does not
            # hold, and no further. Mobiles first within a row. Between rows and files that match the
            # same lead the first one read takes the room when it is short.
            added.sort(key=lambda p: p['type'] != 'Mobile')
            added, refused = fit(len((ent or {}).get('phones') or []), wp_numbers((wp or {}).get(case)) - have, added)
            n['numbers_over_cap'] += len(refused)
            if not added:
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
            n['new_mobile_clean'] += sum(1 for p in added if p['type'] == 'Mobile')
    return n


def settle(results, state, wp=None, today=None):
    """Add the DNC-flagged numbers merge() set aside (state['dnc_held']), once every export has been read.

    They go after every callable number of the run, so a flagged number never takes the room a callable
    one wants, and in a fixed order (mobiles first, then by number), so the order the rows and the
    files come in decides nothing about which of them a lead takes. A lead that has phones by now
    takes them, within what is left of MAX_PHONES (fit()): the bake shows a stored number DNC where
    it would otherwise append a Whitepages copy of it as clean. A lead with nothing dialable gets
    nothing (the flag is in dnc_scrub.json and skiptrace.py can still trace it).
    -> the counts the totals take from this step."""
    today = today or datetime.date.today().isoformat()
    out = dict.fromkeys(('new_numbers', 'numbers_over_cap') + SETTLE_KEYS, 0)
    for case, held in state.get('dnc_held', {}).items():
        ent = results.get(case)
        if not phones_of(ent):
            out['leads_dnc_only_skipped'] += 1
            continue
        have = {norm_number(p.get('number')) for p in phones_of(ent)}
        wp_left = wp_numbers((wp or {}).get(case)) - have           # what the bake would append after the entry
        pending, seen = [], set()
        for p in sorted(held, key=lambda p: (p['type'] != 'Mobile', p['number'], p['type'])):
            if p['number'] not in have and p['number'] not in seen:
                seen.add(p['number'])
                pending.append(p)
        taken, refused = fit(len(ent['phones']), wp_left, pending)
        out['numbers_over_cap'] += len(refused)
        if taken:
            ent['phones'].extend(taken)
            ent['resimpli'] = today
            out['new_numbers'] += len(taken)
            out['new_numbers_dnc'] += len(taken)
    return out


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
    exists and cannot be read as a JSON object; (None, 'unopenable') when it exists and cannot be
    opened (another program has it open, or it is not a file). A sidecar that is not readable is never
    replaced by a fresh one."""
    try:
        with open(path, encoding='utf-8') as f:
            cur = json.load(f)
    except FileNotFoundError:
        return {}, 'ok'
    except OSError:
        return None, 'unopenable'
    except ValueError:
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
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__), kind='io')
    not_utf8 = SyncError('%s is not UTF-8 text (was it re-saved in Excel?). %s. %s' % (name, REDOWNLOAD, STUCK),
                         kind='shape')
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
        raise SyncError('%s has a header line that cannot be read as CSV (%s). %s. %s' % (name, e, REDOWNLOAD, STUCK),
                        kind='shape')
    slots = [int(m.group(1)) for c in fields for m in [re.fullmatch(r'Phone_(\d+)', c)] if m]
    if not slots and 'propertyStreetAddress' not in fields:
        if b'propertystreetaddress' in sniff or b'phone_1' in sniff:
            # its own column names, but not as comma-separated columns on the first line: re-saved with
            # ';' or tab as the delimiter, or with a 'sep=,' line above the header. Its DNC flags would
            # be missing from the merge, so it is not "some other CSV".
            raise SyncError('%s has REsimpli\'s column names but its header does not read as comma-separated '
                            'columns (re-saved in Excel with another delimiter, or a line above the header?). %s. %s'
                            % (name, REDOWNLOAD, STUCK), kind='shape')
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
        raise SyncError('%s is not a complete REsimpli skip-trace export (missing %s). If a column is there under '
                        'another name, rename it back to REsimpli\'s spelling in that file itself, in Notepad or another '
                        'text editor and saved as UTF-8, not in Excel (a spreadsheet can change the rows it saves, and a '
                        'changed row is refused); do not add an empty one: an empty `opt` '
                        'reads every row as not opted out, an empty _IsLitigator reads every number as not a litigator '
                        '(the real column\'s flags would be dropped), and an empty _DNC or _status reads every number as '
                        'DNC, which this tool never clears. If a new download from REsimpli is missing these columns, '
                        'change nothing and send Claude this line (it has no homeowner names or numbers)'
                        % (name, ', '.join(missing[:6])))
    rows, ragged = [], []
    try:
        with open(path, encoding='utf-8-sig', newline='') as f:
            rd = csv.DictReader(f, strict=True)             # strict: a stray or unclosed quote is an error, not a guess
            for x in rd:
                if x.get(None) or any(v is None for v in x.values()):   # a cell past the header's last column (an empty one too) or one short
                    ragged.append(rd.line_num)                          # of it: a comma put in or taken out moves every column after it
                rows.append(x)
    except UnicodeDecodeError:
        raise not_utf8
    except OSError as e:
        raise SyncError('%s could not be read (%s)' % (name, type(e).__name__), kind='io')
    except csv.Error:
        raise SyncError('%s could not be read as CSV: a cell is longer than %d characters, a quote is never closed '
                        'or has text right after it (a stray quote would otherwise swallow the rows below it, opt-outs '
                        'and DNC flags included), or, on Python before 3.11, the file has a NUL byte. Do not delete lines '
                        'to get past it: the people on them would lose their opt-outs and flags without a word. %s. %s'
                        % (name, csv.field_size_limit(), REDOWNLOAD, STUCK), kind='shape')
    if ragged:
        raise SyncError('%s has %d row%s with a different number of cells than its %d header columns (line%s %s%s). A comma '
                        'typed into a cell, one taken out, or a file cut short moves every column after it, so a number\'s '
                        'flags and the `opt` cell can be read from the wrong place, and a comma at the end of a row cannot '
                        'be told from that. Do not delete the row to get past this: the person on it would lose their '
                        'opt-out and flags without a word, and a file cut short is missing the rows after the cut too. '
                        '%s. A file saved again in a spreadsheet may not be what REsimpli sent: use the copy REsimpli '
                        'downloaded. %s. Nothing was read.'
                        % (name, len(ragged), '' if len(ragged) == 1 else 's', len(rd.fieldnames),
                           '' if len(ragged) == 1 else 's', ', '.join(str(n) for n in ragged[:3]),
                           ', ...' if len(ragged) > 3 else '', REDOWNLOAD, STUCK), kind='shape')
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
                        'now, change those cells to Yes (opted out) or No in that file itself, in Notepad or another text '
                        'editor and saved as UTF-8, not in Excel (a spreadsheet can change the rows it saves, and a changed '
                        'row is refused), and run again; if you are not sure what a cell meant, put Yes, which opts that '
                        'person out. If the '
                        'cells look like a corrupt download, download the export from REsimpli again and put the new file over this '
                        'one (a copy beside it would be read too, and this one would keep refusing).'
                        % (name, len(odd), '' if len(odd) == 1 else 's', shown, ', ...' if len(odd) > 3 else ''),
                        kind='opt')
    return rows


def refusal_note(err, path, named, import_dir):
    """What to do about a file that stopped the run, said for why it stopped and where the file is."""
    folder = os.path.dirname(os.path.abspath(path))
    if err.kind == 'io':
        return ('That file is in %s. If another program has it open (Excel keeps a lock on a file it has open), close '
                'it and run again; otherwise check that it is still there and can be read. If neither helps, leave the '
                'file as it is and send Claude the REFUSED line above (it has no homeowner names or numbers). Nothing was '
                'read further: its opt-outs and DNC flags would be missing from the merge.' % folder)
    kept = folder == os.path.abspath(import_dir)
    if err.kind == 'opt':
        # the line above already says how to fix the cells in that file itself; a replacement download would have the
        # same words, and moving or deleting the file would drop its opt-outs
        if kept:
            return ('That file is in %s, where this tool keeps a copy of every export it has read. Deleting it forgets '
                    'its opt-outs: change the cells in that copy itself, as the line above says, then run again.' % folder)
        return '' if named else 'That file is in %s.' % folder
    if err.kind == 'shape':
        # the line above already says that a line cannot be deleted to get past this (deleting it, or the cut-off last
        # one, reads fine afterwards and drops a person's opt-out without a word), to download again, and what to do when
        # that is refused too; saying it twice only lengthens it. A kept copy can also be one that was changed after it was kept.
        fix = 'Replace it as the line above says'
        if kept:
            fix += ' (a new download, or the original export if this copy was changed after it was kept)'
    else:
        fix = ('Fix what the line above says in that file itself, or put the original export back over it (download it '
               'from REsimpli again if you no longer have it)')
    if kept:
        return ('That file is in %s, where this tool keeps a copy of every export it has read. This tool cannot read '
                'it in full, so its opt-outs and DNC flags would be missing from the merge and nothing was read '
                'further. Deleting it forgets them. %s, then run again.' % (folder, fix))
    if not named:
        # a real export must not be told to get out of the way: moving it aside drops its opt-outs from the next run
        return ('That file is in %s. If it is a REsimpli export, do not move or delete it: its opt-outs and DNC flags '
                'would be missing from the merge, and nothing was read further. %s, then run again. If it is '
                'something else, such as another vendor\'s skip-trace file or a list of ours, rename it so it no '
                'longer starts with SkipTrace_, then run again.' % (folder, fix))
    return ''


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
                        'torn: restore a copy of it (whitepages_lookup.py cannot read it either, so running that '
                        'first does not help), then run this again.' % (path, type(e).__name__))
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
            err = SyncError('%s could not be read (%s)' % (os.path.basename(f), type(e).__name__), kind='io')
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
                note = refusal_note(err, f, os.path.abspath(f) in named, import_dir)
                if note:
                    print(note)
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
    if side_state == 'unopenable':
        print('REFUSED: dnc_scrub.json exists but cannot be opened or read through (another program may have it open, it may '
              'not be a file, or the disk gave a read error). '
              'It holds the registry\'s DNC verdicts, which every number this run adds is checked against, and it is where '
              'this run\'s own flags are recorded. Nothing was written. Close whatever has it open and run again. If '
              'nothing has it open, do not move or delete it either: send Claude this line (it has no homeowner names or '
              'numbers). Do not put an old copy over it: if it is a good file, that would drop every registry verdict and '
              'every flag recorded since. Until it can be opened the board bake cannot apply registry verdicts either.')
        return 2
    if side_state == 'unreadable':
        # It holds the registry's verdicts, which every number this run adds is checked against, and it is where this
        # run's flags are kept (the phone cache alone loses them when skiptrace.py replaces a lead's entry).
        print('REFUSED: dnc_scrub.json exists but cannot be read. It holds the registry\'s DNC verdicts, which every number '
              'this run adds is checked against, and it is where this run\'s own flags are recorded. Nothing was '
              'written. Restore it from a copy (DEALFLOW\\backups holds dnc_scrub.pre-resimpli-*.json only after an '
              'earlier run of this tool, so there may be none, and a copy is only as new as its date: a verdict the '
              'Tracerfy DNC lane recorded after it is missing from it, and that number bakes as clean until the lane '
              'checks it again), then run again. Do not just move it aside: a new '
              'sidecar would hold only REsimpli\'s flags, and every registry verdict the Tracerfy DNC lane recorded in '
              'the old one would be gone without a word. If there is no copy to restore, do not delete it either: send '
              'Claude this line (it has no homeowner names or numbers). Until it is fixed the board bake cannot apply '
              'registry verdicts at all.')
        return 2
    today = datetime.date.today().isoformat()
    try:
        leads = S.load_all_leads()
    except (OSError, ValueError) as e:
        print('REFUSED: the board leads cannot be read (%s); nothing written. %s'
              % (type(e).__name__,
                 'leads_final.json is read from %s (next to skiptrace.py, whatever folder this is run from) and is '
                 'missing there or cannot be opened: run this on the machine that builds the board, and check that a '
                 'refresh is not rewriting it, then run again.' % os.path.dirname(os.path.abspath(S.LEADS))
                 if isinstance(e, OSError)
                 else 'leads_final.json is half-written or damaged (a refresh may still be running): wait for it to '
                      'finish, or rebuild it, then run again.'))
        return 2
    idx = build_index(leads, S._case, S._propaddr)

    opt_people = opt_people_of(exports)
    flagged, cells = set(), {'flag_cells_unread': 0}
    for f, h, rows in exports:
        flagged |= flagged_numbers(rows, opt_people, cells)
    registry = registry_digits(side_cur)
    wp_all = set().union(*(wp_numbers(rec) for rec in wp.values()))
    print('opt-out hold: %d people, from %d exports (%d files found)' % (len(opt_people), len(exports), len(files)))
    unnamed = sum(1 for _, _, rows in exports for x in rows if opted_out(x) and unread_owners(x))
    if unnamed:
        print('NOTE: %d opted-out rows have an owner this tool cannot read as a person (a company, an initial for a first '
              'name, a name in one column only), so that owner cannot be held on their other rows or in other exports. '
              'The numbers on the rows themselves are still flagged.' % unnamed)
    if cells['flag_cells_unread']:
        print('NOTE: %d flagged phone cells (the row is opted out, or the number is marked DNC) hold no number this tool can '
              'read, so that flag could not be applied to any number. Nothing was guessed.' % cells['flag_cells_unread'])
    unread = 0                  # exports waiting in Downloads / Desktop that this run did not read (naming a file reads
    for f in discover(import_dir):                                  # that file and the kept copies, nothing else)
        try:
            unread += sha256(f) not in seen
        except OSError:
            unread += 1
    if unread:
        print('NOTE: %d other SkipTrace_*.csv file%s in Downloads / Desktop %s not read: naming a file reads that '
              'file and the copies kept in %s, nothing else. An opt-out or DNC flag in %s is not in force in this '
              'run. Run without a path to read every export.'
              % (unread, '' if unread == 1 else 's', 'was' if unread == 1 else 'were', import_dir,
                 'it' if unread == 1 else 'them'))

    status = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'dry_run': a.dry_run,
              'board_leads_indexed': sum(len(v) for v in idx.values()), 'whitepages_cases': len(wp),
              'unread_files': unread, 'skipped_files': skipped, 'files': []}
    total = dict.fromkeys(COUNT_KEYS, 0)
    state, pairs = {}, set()
    for f, h, rows in exports:
        n = merge(rows, idx, results, S._case, S._propaddr, today, flagged, pairs, state, registry, wp,
                  opt_people)
        status['files'].append({'file': os.path.basename(f), 'sha256': h[:16],
                                **{k: v for k, v in n.items() if k not in SETTLE_KEYS}})
        for k in COUNT_KEYS:
            total[k] += n[k]
        print('%s: %d rows, %d with phones, %d leads matched, %d new callable numbers, %d leads had no phone'
              % (os.path.basename(f), n['rows'], n['rows_with_phone'], n['leads_matched'],
                 n['new_numbers'], n['leads_new_phone']))
    settled = settle(results, state, wp, today)
    for k in ('new_numbers', 'numbers_over_cap') + SETTLE_KEYS:
        total[k] += settled[k]                 # the flagged numbers, placed after every callable one
    tightened = tighten(results, flagged)
    aud, bad_cases = audit(results, pairs)
    side_doc, side_added, side_tight, side_marked = sidecar_plan(side_cur, flagged, results, today)
    total.update(dnc_flagged_numbers=len(flagged), dnc_tightened=tightened, dnc_sidecar_added=side_added,
                 dnc_sidecar_tightened=side_tight, dnc_sidecar_marked=side_marked, opt_people_held=len(opt_people),
                 flagged_also_whitepages=len(flagged & wp_all), flag_cells_unread=cells['flag_cells_unread'],
                 opt_rows_unnamed=unnamed, **aud)
    status['total'] = total
    status['dnc_scrub_json'] = side_state
    status['resimpli_unconfirmed_cases'] = bad_cases
    status['opt_person_cases'] = sorted(c for c in state.get('opt_person_cases', ()) if real_case(c))
    print('TOTAL')
    for k in COUNT_KEYS + GLOBAL_KEYS:
        print('  %-28s %d' % (k, total[k]))
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
