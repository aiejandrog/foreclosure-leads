#!/usr/bin/env python
"""export_to_propstream — board leads as a CSV for PropStream's Import List.

WHAT IT WRITES
Property address, owner name, mailing address, county, case number, auction date, tags. NO phones
and NO emails: PropStream matches on the address and skip-traces on its own side, and a number or
address never leaves this repo's gates by accident. Headers come from propstream_map.json, which
is a placeholder until it is compared with PropStream's own template (see that file's _note).

WHICH LEADS
Default (--scope all): every lead on the board EXCEPT the ones that must never be contacted again:
  opt-out ledger (case, email and phone keys), dead ledger, rep notes (hard no, opted out, DNC,
  soft no, wrong owner, dead), never_contact.json, no address, vacant land.
  A held lead is COUNTED, never written. An unreadable ledger / notes / never-contact list
  stops the run. No phone is needed, so leads Call Mode cannot dial are in: that is the point of
  a skip-trace list.
  Bankruptcy-stay holds (Broward / Palm Beach awaiting a federal check) are NOT applied in this
  scope, because uploading an address contacts nobody. Do not send, call or text from PropStream
  to those leads: PropStream's dialer and mailers sit outside this repo's gates.
--scope callable: the exact Call Mode gates of export_to_resimpli (stay, bankruptcy, diligence,
  equity floor, send-hold preflight). Use it when the PropStream list will be worked, not traced.

Output goes to <DEALFLOW_DIR>/exports/ (outside the repo, per CLAUDE.md). Counts-only summary
JSON beside it.

RUN
  python export_to_propstream.py --dry-run
  python export_to_propstream.py --county MIAMI-DADE --list-name "Miami-Dade 2026-10"
  python export_to_propstream.py --scope callable
"""
import argparse
import datetime
import json
import os
import re
import sys

import export_to_resimpli as EX
import resimpli_lead as RL

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(HERE, 'propstream_map.json')
ExportError = EX.ExportError


def build_all(rows, optouts, opt_cases, deads, notes, list_name, county=None, never_contact=None):
    """-> (leads, summary). Suppression-only gates; see module docstring."""
    if never_contact is None:
        import stay_gate
        never_contact = stay_gate.never_contact
    all_rows = rows
    if county:
        want = EX.norm_county(county)
        present = {EX.norm_county(r.get('county')): str(r.get('county')).upper()
                   for r in rows if r.get('county')}
        if want not in present:
            raise ExportError('--county %r matches no lead. Counties on the board: %s'
                              % (county, ', '.join(sorted(present.values())) or 'none'))
        rows = [r for r in rows if EX.norm_county(r.get('county')) == want]
    rows = sorted(rows, key=EX._sort_key)
    person_cases = {}
    for r in all_rows:                       # whole board: a hard no on one case holds the person
        if r.get('pkey') and r.get('case'):
            person_cases.setdefault(r['pkey'], []).append(str(r['case']))
    person_optout = EX._person_optout_fn(optouts)
    held = {}

    def hold(why):
        held[why] = held.get(why, 0) + 1

    leads = []
    for row in rows:
        case = str(row.get('case') or '')
        if not case:
            hold('no_case_number')
            continue
        if case.upper() in opt_cases or case in optouts or person_optout(row):
            hold('opted_out')
            continue
        if case in deads:
            hold('dead_ledger')
            continue
        nwhy, _blocked = EX.notes_verdict(case, person_cases.get(row.get('pkey')) or [], notes)
        if nwhy:
            hold(nwhy)
            continue
        try:
            if never_contact(case):
                hold('never_contact')
                continue
        except Exception as e:
            raise ExportError('never_contact list unreadable (%s)' % str(e)[:80])
        if row.get('vac'):
            hold('vacant_land')
            continue
        ld = RL.from_board_row(row, [], [], list_name=list_name)
        if not ld.property_address:
            hold('no_address')
            continue
        leads.append(ld)
    leads, dupes = RL.dedupe(leads)
    return leads, {'rows_in': len(rows), 'held': dict(sorted(held.items(), key=lambda kv: -kv[1])),
                   'dupes_dropped': dupes, 'rows_written': len(leads),
                   'written_without_zip': sum(1 for ld in leads if not ld.zip),
                   'written_with_mailing_address': sum(1 for ld in leads if ld.mailing_address)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scope', choices=('all', 'callable'), default='all')
    ap.add_argument('--list-name', default='')
    ap.add_argument('--county', default='', help='MIAMI-DADE, BROWARD or PALM BEACH (default: all)')
    ap.add_argument('--out-dir', default='')
    ap.add_argument('--twin', default='')
    ap.add_argument('--map', default=MAP_FILE)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    today = datetime.date.today()
    try:
        cols = EX.load_map(a.map)
        if a.scope == 'callable':
            holds = EX.preflight()
            if holds:
                print('EXPORT HELD — nothing written:')
                for h in holds:
                    print('  - ' + h)
                return 3
        rows = EX.read_twin(a.twin or None)
        optouts, opt_cases, opt_emails = EX.load_suppression()
        deads = EX.load_deads()
        notes = EX.load_notes()
        list_name = a.list_name or EX.default_list_name(
            [r for r in rows if not a.county or EX.norm_county(r.get('county')) == EX.norm_county(a.county)], today)
        if a.scope == 'callable':
            leads, summary = EX.build(rows, optouts, opt_cases, opt_emails, deads, EX.load_bounced(),
                                      list_name, county=a.county or None, notes=notes)
            for ld in leads:                 # PropStream gets no contact data in either scope
                ld.phones, ld.emails = [], []
        else:
            leads, summary = build_all(rows, optouts, opt_cases, deads, notes, list_name,
                                       county=a.county or None)
    except ExportError as e:
        print('EXPORT FAILED — nothing written: %s' % e)
        return 2
    if not os.path.exists(EX.NOTES_FILE):
        summary['notes_warning'] = 'worker_notes.json not found: rep notes were NOT applied'
        print('WARNING: ' + summary['notes_warning'])
    summary.update({'scope': a.scope, 'list_name': list_name, 'date': today.isoformat(),
                    'county': a.county or 'ALL', 'dry_run': bool(a.dry_run)})
    print(json.dumps(summary, indent=1))
    if a.dry_run:
        print('dry run: no files written')
        return 0
    import paths as P
    out_dir = a.out_dir or P.out('exports')
    os.makedirs(out_dir, exist_ok=True)
    base = 'propstream_%s_%s' % (EX._slug(list_name), today.isoformat())
    csv_path = os.path.join(out_dir, base + '.csv')
    EX.write_csv(leads, cols, csv_path)
    with open(os.path.join(out_dir, base + '.summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1)
    print('wrote %d row(s) -> %s' % (len(leads), csv_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
