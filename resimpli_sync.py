#!/usr/bin/env python3
"""resimpli_sync.py - fold REsimpli skip-trace exports into the board's phone cache.

The inbound half of the REsimpli bridge (export_to_resimpli.py is the outbound half). REsimpli's
"Skip Trace" export is a CSV of properties with up to ten phones each. This adds those phones to
skiptrace_results.json, the same local cache skiptrace.py writes, so the next board build bakes
them into r.phones / r.phdnc / r.phtype and Call Mode applies every gate it already applies.

WHAT GETS IN
  * A row is used only when its property street + ZIP match a board lead that has a case number
    AND a REsimpli owner surname appears in that lead's owner names. Same house with a different
    owner on file is counted and skipped: those numbers belong to someone else.
  * Rows with no matching lead are counted, never added as leads. They have no case number, so
    no bankruptcy / stay check can run on them; a new lead source is a product decision.
  * Numbers are ADDED. An existing number, entry, or provider is never removed or replaced.
  * REsimpli's per-phone DNC flag (Phone_N_DNC = Yes, or "DNC" in Phone_N_status) and a litigator
    flag become dnc=True, which foreclosure_leads.make_tracker turns into phdnc and Call Mode
    withholds. Emails are not merged: they are unverified and would feed first-touch email.
  * A lead with no cache entry gets one only if at least one new number is not DNC-flagged, so an
    all-DNC row does not stop skiptrace.py from tracing that lead later.

WHICH FILES
Every CSV passed on the command line, or with none: every SkipTrace_*.csv in Downloads / Desktop
plus DEALFLOW_DIR/imports/resimpli/. All of them are processed, not the newest: the merge is
add-only and dedupes by number, so re-reading a file adds nothing and no file has to be picked.
A file whose header is not a REsimpli skip-trace export is refused by name. Found files are
copied into DEALFLOW_DIR/imports/resimpli/ (outside the repo and outside OneDrive); the originals
are left where they are.

OUTPUT
Counts only on stdout and in DEALFLOW_DIR/resimpli_sync_status.json. A backup of the cache is
written to DEALFLOW_DIR/backups/ before every write. Nothing is committed, published or sent.

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
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REQUIRED = ('propertyStreetAddress', 'propertyZipCode', 'Phone_1', 'Phone_1_DNC', 'fullName')
MAX_PHONES_PER_ROW = 10

SUF = {'STREET': 'ST', 'AVENUE': 'AVE', 'AV': 'AVE', 'COURT': 'CT', 'ROAD': 'RD', 'DRIVE': 'DR',
       'TERRACE': 'TER', 'TERR': 'TER', 'PLACE': 'PL', 'LANE': 'LN', 'BOULEVARD': 'BLVD',
       'CIRCLE': 'CIR', 'HIGHWAY': 'HWY', 'PARKWAY': 'PKWY', 'NORTH': 'N', 'SOUTH': 'S',
       'EAST': 'E', 'WEST': 'W', 'NORTHWEST': 'NW', 'NORTHEAST': 'NE', 'SOUTHWEST': 'SW',
       'SOUTHEAST': 'SE'}
UNIT_WORDS = {'UNIT', 'APT', 'STE', 'SUITE', 'BLDG'}
NAME_STOP = {'LLC', 'INC', 'TRUST', 'TR', 'TRUSTEE', 'ESTATE', 'OF', 'THE', 'AND', 'JR', 'SR',
             'II', 'III', 'LE', 'ET', 'AL', 'UNKNOWN', 'HEIRS', 'SPOUSE', 'TENANT', 'DE', 'DEL',
             'LA', 'LOS', 'Y'}
OWNER_FIELDS = ('owners', 'owner', 'oname', 'rname', 'Owner')
COUNT_KEYS = ('rows', 'rows_with_phone', 'addr_match', 'owner_mismatch', 'matched_rows',
              'leads_matched', 'leads_had_phone', 'leads_new_phone', 'new_numbers',
              'new_numbers_dnc', 'new_mobile_clean', 'skipped_all_dnc_new_lead',
              'unmatched_rows_with_phone')


class SyncError(Exception):
    pass


# ---------------------------------------------------------------- normalizing

def street_key(s):
    """'7164 Sw 163rd Ct' and '7164 SW 163 COURT, MIAMI, FL' -> '7164 SW 163 CT'. Units dropped:
    sources disagree on them, and the building plus the owner check is the key."""
    s = re.sub(r'[^A-Z0-9 ]', ' ', (s or '').upper().split(',')[0])
    out = []
    for w in s.split():
        if w in UNIT_WORDS:
            break
        w = re.sub(r'^(\d+)(ST|ND|RD|TH)$', r'\1', SUF.get(w, w))
        out.append(w)
    return ' '.join(out)


def zip_of(s):
    m = re.findall(r'\b(\d{5})(?:-\d{4})?\b', s or '')
    return m[-1] if m else ''


def name_tokens(s):
    return {w for w in re.sub(r'[^A-Z ]', ' ', (s or '').upper()).split()
            if len(w) > 2 and w not in NAME_STOP}


def row_phones(x):
    out = []
    for i in range(1, MAX_PHONES_PER_ROW + 1):
        num = re.sub(r'\D', '', x.get('Phone_%d' % i) or '')
        if len(num) == 11 and num.startswith('1'):
            num = num[1:]
        if len(num) != 10:
            continue
        dnc = ((x.get('Phone_%d_DNC' % i) or '').strip().lower() == 'yes'
               or 'DNC' in (x.get('Phone_%d_status' % i) or '').upper()
               or (x.get('Phone_%d_IsLitigator' % i) or '').strip().lower() in ('yes', 'true'))
        out.append({'number': num, 'type': (x.get('Phone_%d_type' % i) or '').strip(),
                    'carrier': '', 'dnc': dnc, 'src': 'resimpli'})
    return out


# ---------------------------------------------------------------- merge

def build_index(leads, case_of, addr_of):
    idx = {}
    for r in leads:
        if not case_of(r):
            continue
        a = addr_of(r)
        k = (street_key(a), zip_of(a))
        if k[0] and k[1]:
            idx.setdefault(k, []).append(r)
    return idx


def merge(rows, idx, results, case_of, addr_of, today=None):
    """Add matching rows' phones into `results` in place. Returns counts (no names, no numbers)."""
    today = today or datetime.date.today().isoformat()
    n = dict.fromkeys(COUNT_KEYS, 0)
    n['rows'] = len(rows)
    touched = set()
    for x in rows:
        phones = row_phones(x)
        if phones:
            n['rows_with_phone'] += 1
        k = (street_key(x.get('propertyStreetAddress')), (x.get('propertyZipCode') or '').strip()[:5])
        hits = idx.get(k) or []
        if not hits:
            if phones:
                n['unmatched_rows_with_phone'] += 1
            continue
        n['addr_match'] += 1
        rs = set()
        for f in ('fullName', 'fullName2', 'lastName', 'lastName2'):
            rs |= name_tokens(x.get(f))
        ok = [r for r in hits
              if rs & name_tokens(' '.join(str(r.get(f) or '') for f in OWNER_FIELDS))]
        if not ok:
            n['owner_mismatch'] += 1
            continue
        n['matched_rows'] += 1
        for r in ok:
            case = case_of(r)
            ent = results.get(case)
            had = bool(ent and ent.get('phones'))
            if case not in touched:
                n['leads_matched'] += 1
                n['leads_had_phone'] += had
            have = {p.get('number') for p in (ent or {}).get('phones') or []}
            added = [p for p in phones if p['number'] not in have]
            if not added:
                touched.add(case)
                continue
            if ent is None:
                if all(p['dnc'] for p in added):
                    n['skipped_all_dnc_new_lead'] += 1
                    touched.add(case)
                    continue
                ent = {'name': (r.get('owners') or r.get('owner') or '').split(';')[0].strip(),
                       'entity': '', 'address': addr_of(r), 'county': r.get('county', 'MIAMI-DADE'),
                       'phones': [], 'emails': [], 'traced': today, 'source': 'resimpli'}
                results[case] = ent
            if not had and case not in touched:
                n['leads_new_phone'] += 1
            ent.setdefault('phones', []).extend(added)
            ent['resimpli'] = today
            n['new_numbers'] += len(added)
            n['new_numbers_dnc'] += sum(1 for p in added if p['dnc'])
            n['new_mobile_clean'] += sum(1 for p in added
                                         if not p['dnc'] and p['type'].lower().startswith('mob'))
            touched.add(case)
    return n


# ---------------------------------------------------------------- files

def read_export(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (rd.fieldnames or [])]
        if missing:
            raise SyncError('%s is not a REsimpli skip-trace export (missing %s)'
                            % (os.path.basename(path), ', '.join(missing)))
        return list(rd)


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('files', nargs='*')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    import paths as P
    import skiptrace as S
    import_dir = os.path.join(P.DEALFLOW_DIR, 'imports', 'resimpli')
    files = a.files or discover(import_dir)
    if not files:
        print('no REsimpli export found (SkipTrace_*.csv in Downloads / Desktop, or %s)' % import_dir)
        return 1

    # one copy of each distinct file, by content: the same export downloaded twice is one file
    seen, exports = set(), []
    for f in files:
        h = sha256(f)
        if h in seen:
            continue
        seen.add(h)
        try:
            exports.append((f, h, read_export(f)))
        except SyncError as e:
            print('REFUSED:', e)
            return 2

    leads = S.load_all_leads()
    idx = build_index(leads, S._case, S._propaddr)
    res_path = S.RESULTS
    results = json.load(open(res_path, encoding='utf-8')) if os.path.exists(res_path) else {}

    status = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'dry_run': a.dry_run,
              'board_leads_indexed': sum(len(v) for v in idx.values()), 'files': []}
    total = dict.fromkeys(COUNT_KEYS, 0)
    for f, h, rows in exports:
        n = merge(rows, idx, results, S._case, S._propaddr)
        status['files'].append({'file': os.path.basename(f), 'sha256': h[:16], **n})
        for k in COUNT_KEYS:
            total[k] += n[k]
        print('%s: %d rows, %d leads matched, %d new numbers (%d DNC-flagged), %d leads had no phone'
              % (os.path.basename(f), n['rows'], n['leads_matched'], n['new_numbers'],
                 n['new_numbers_dnc'], n['leads_new_phone']))
    status['total'] = total
    print('TOTAL')
    for k in COUNT_KEYS:
        print('  %-26s %d' % (k, total[k]))

    if a.dry_run:
        print('DRY RUN - nothing written')
        return 0

    os.makedirs(import_dir, exist_ok=True)
    for f, h, _ in exports:
        dst = os.path.join(import_dir, os.path.basename(f))
        if os.path.abspath(f) != os.path.abspath(dst) and not os.path.exists(dst):
            shutil.copy2(f, dst)
    if total['new_numbers']:
        if os.path.exists(res_path):
            bdir = os.path.join(P.DEALFLOW_DIR, 'backups')
            os.makedirs(bdir, exist_ok=True)
            bak = os.path.join(bdir, 'skiptrace_results.pre-resimpli-%s.json'
                               % datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(res_path, bak)
            print('backup ->', bak)
        tmp = res_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(results, fh, indent=1)
        os.replace(tmp, res_path)
        print('skiptrace_results.json updated')
    else:
        print('nothing new - skiptrace_results.json unchanged')
    with open(os.path.join(P.DEALFLOW_DIR, 'resimpli_sync_status.json'), 'w', encoding='utf-8') as fh:
        json.dump(status, fh, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
