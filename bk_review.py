"""bk_review.py - a review sheet for leads held as "possible bankruptcy" (plausible CourtListener match).

A plausible match is the same name as the owner in a federal bankruptcy court with no address or
county on the hit, so many are namesakes. The stay gate never auto-clears one; a person has to
compare the filing to the homeowner and say it is a false positive (bk_overrides.json).

    python bk_review.py              # write bk_review.csv to DEALFLOW_DIR; counts only on screen
    python bk_review.py --apply F    # add rows a person marked  decision=clear  to bk_overrides.json

The sheet has the owner name, county, case number, court, filed date and a CourtListener search
link per open plausible case. It lives in DEALFLOW_DIR (outside OneDrive), never in git, and the
screen output carries counts only. --apply is add-only, writes only (lead, case) pairs that are
still a plausible open hold right now, ignores every other row, and leaves an override file it
cannot parse exactly as it is. It spends nothing and calls no API.
"""
import csv
import json
import os
import sys
import urllib.parse

import bk_lookup as BK
import stay_gate

OUT_NAME = 'bk_review.csv'
COLS = ['decision', 'lead_id', 'county', 'owners', 'case_number', 'court', 'filed', 'search_link']


def _out_path():
    return os.path.join(BK.state_dir(), OUT_NAME)


def possible_rows(leads=None, cache=None, overrides=None):
    leads = BK.load_leads() if leads is None else leads
    cache = BK.load_cache() if cache is None else cache
    overrides = BK.load_overrides() if overrides is None else overrides
    rows = []
    for key, ent in sorted(cache.items()):
        if not isinstance(ent, dict):
            continue
        cases = BK.apply_override(ent.get('cases') or [], key, overrides)
        if BK.verdict_of(cases, bool(ent.get('searched')), bool(ent.get('ok_check')),
                         str(ent.get('err') or ''))[0] != 'possible':
            continue
        ld = leads.get(key) or {}
        owners = ' / '.join(sorted({r for r, _o in ld.get('owners_raw') or [] if r}))
        for c in cases:
            if c.get('open') and c.get('match') == 'plausible' and c.get('no'):
                q = urllib.parse.urlencode({'q': c['no'], 'type': 'r'})
                rows.append({'decision': '', 'lead_id': key, 'county': ld.get('county', ''),
                             'owners': owners, 'case_number': c['no'], 'court': c.get('court', ''),
                             'filed': c.get('filed', ''),
                             'search_link': 'https://www.courtlistener.com/?' + q})
    return rows


def write_sheet(rows, path=None):
    path = path or _out_path()
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def apply_sheet(sheet, rows, path=None):
    """Add rows marked decision=clear to bk_overrides.json. Returns (added, skipped, error)."""
    path = path or BK.overrides_path()
    live = {(r['lead_id'], BK.case_no_key(r['case_number'])) for r in rows}
    marked = []
    with open(sheet, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if str(r.get('decision') or '').strip().lower() != 'clear':
                continue
            marked.append((str(r.get('lead_id') or '').strip(), str(r.get('case_number') or '').strip()))
    if not marked:
        return 0, 0, ''
    data, exists = BK._load(path)
    if exists and not isinstance(data, dict):
        return 0, len(marked), 'bk_overrides.json does not parse, left as is'
    data = data if isinstance(data, dict) else {}
    clears = data.get('clears')
    if clears is not None and not isinstance(clears, list):
        return 0, len(marked), 'bk_overrides.json clears is not a list, left as is'
    clears = list(clears or [])
    have = {(stay_gate.pacer_key(c.get('lead_id') or c.get('lead') or c.get('case')) or '',
             BK.case_no_key(c.get('case_number') or c.get('case') or c.get('no')))
            for c in clears if isinstance(c, dict)}
    added = skipped = 0
    for lead, no in marked:
        k = (lead, BK.case_no_key(no))
        if k not in live or k in have:
            skipped += 1
            continue
        clears.append({'lead_id': lead, 'case_number': no})
        have.add(k)
        added += 1
    if added:
        data['clears'] = clears
        BK._dump(path, data)
    return added, skipped, ''


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    rows = possible_rows()
    if '--apply' in argv:
        i = argv.index('--apply')
        sheet = argv[i + 1] if i + 1 < len(argv) else _out_path()
        added, skipped, err = apply_sheet(sheet, rows)
        print('BK-REVIEW apply: %d cleared, %d skipped (not a live plausible hold, or already cleared)%s'
              % (added, skipped, (' - ERROR ' + err) if err else ''))
        return 1 if err else 0
    p = write_sheet(rows)
    print('BK-REVIEW: %d open plausible cases across %d leads written to %s'
          % (len(rows), len({r['lead_id'] for r in rows}), p))
    print('Mark decision=clear on each false positive, save, then: python bk_review.py --apply "%s"' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
