#!/usr/bin/env python
"""export_to_propstream — board leads as a CSV for PropStream's Import List.

WHAT IT WRITES
PropStream's own Import List template, verbatim (supplied 2026-10-19): Address, Unit#, City,
State, Zip, County, FIPS, APN#. Nothing else: no owner, no phones, no emails, no case data.
PropStream matches on the address and skip-traces on its own side. Unit# is split off the street
line; FIPS is the county code (Miami-Dade 12086, Broward 12011, Palm Beach 12099); APN# is the
board's folio / parcel number. Headers live in propstream_map.json.

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
--scope send: for leads PropStream will MAIL or TEXT. The default gates PLUS the bankruptcy / stay
  check the send bridge and reply_send.py use (stay_gate.check and bk_lookup.send_hold: Miami docket
  stay, Broward / Palm Beach federal check, never_contact, PACER), and the whole file is refused
  unless the opt-out ledger is readable, fresh and synced today (the same preflight as /send). Any
  gate that cannot be evaluated holds the lead. No phone needed.
--scope callable: the exact Call Mode gates of export_to_resimpli (needs a dialable number, adds
  diligence and the equity floor). Use it when the PropStream list will be CALLED.

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
FIPS = {'MIAMIDADE': '12086', 'BROWARD': '12011', 'PALMBEACH': '12099'}
_UNIT = set(RL.UNIT.values())
# a bare trailing number after one of these is a unit ("777 SW 9 AVE 101"). Roads and highways are
# left out: a number after them is the route ("STATE RD 826"), not a unit.
_UNIT_AFTER = set(RL.SUFFIX.values()) - {'RD', 'HWY', 'PKWY', 'TPKE', 'CSWY', 'EXPY', 'LOOP', 'PASS', 'PATH', 'RUN', 'ROW', 'WALK'}
_BARE_UNIT = re.compile(r'^(?:\d{1,5}[A-Z]?|[A-Z]\d{1,5})$')


def default_send_hold(case):
    """(held, why). Fails closed: a gate that cannot be evaluated holds the lead."""
    try:
        v = EX.default_stay_check(case)
        if not v.get('ok'):
            return True, 'stay_gate:%s' % (v.get('code') or 'unknown')
        import bk_lookup
        held, _why = bk_lookup.send_hold(case)
        return (True, 'bankruptcy_hold') if held else (False, '')
    except Exception as e:
        return True, 'gate_unevaluable:%s' % type(e).__name__


def build_all(rows, optouts, opt_cases, deads, notes, list_name, county=None, never_contact=None,
              send_hold=None):
    """-> (leads, summary). Suppression gates, plus send_hold(case) -> (held, why) when given."""
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
        if send_hold is not None:
            sh, why = send_hold(case)
            if sh:
                hold(why or 'send_hold')
                continue
        ld = RL.from_board_row(row, [], [], list_name=list_name)
        ld.folio = re.sub(r'[^0-9A-Za-z]', '', str(row.get('folio') or '')).upper()
        if not ld.property_address:
            hold('no_address')
            continue
        leads.append(ld)
    leads, dupes = RL.dedupe(leads)
    return leads, {'rows_in': len(rows), 'held': dict(sorted(held.items(), key=lambda kv: -kv[1])),
                   'dupes_dropped': dupes, 'rows_written': len(leads),
                   'written_without_zip': sum(1 for ld in leads if not ld.zip),
                   'written_with_mailing_address': sum(1 for ld in leads if ld.mailing_address)}


def load_map(path):
    """propstream_map.json -> columns. 'field' is a Lead field or one of unit/street/fips/apn."""
    try:
        cols = json.load(open(path, encoding='utf-8'))['columns']
    except Exception as e:
        raise ExportError('%s does not load (%s)' % (os.path.basename(path), e))
    ok = set(RL.Lead.__dataclass_fields__) | {'unit', 'street', 'fips', 'apn'}
    for c in cols:
        if not c.get('header') or c.get('field') not in ok:
            raise ExportError('bad column in %s: %r' % (os.path.basename(path), c))
    return cols


_DIRECTIONAL = {'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'}
_UNIT_WORDS = _UNIT | {'UNITS', 'APTS', 'STES'}
_LETTER_UNIT = re.compile(r'^[A-Z]$')


def split_unit(street):
    """'842 NW 9TH ST APT 4' -> ('842 NW 9TH ST', '4'). No unit -> (street, '').

    Also splits a bare trailing unit after a street suffix ('777 SW 9 AVE 101', '2881 NE 33 CT 4C',
    '5110 NESTING WAY C', 'POLO RD W 103') and 'PH-7'. A lone letter after a plain street name
    ('187 SAXONY D') is NOT split: it cannot be told from the street's own name."""
    toks = str(street or '').split()
    for i, t in enumerate(toks):
        if i > 0 and t in _UNIT_WORDS:
            return ' '.join(toks[:i]), ' '.join(toks[i + 1:])
    if len(toks) >= 4:
        last, prev = toks[-1], toks[-2]
        if prev in _UNIT_AFTER and (_BARE_UNIT.match(last) or re.match(r'^PH-?\d+$', last)
                                    or (_LETTER_UNIT.match(last) and last not in _DIRECTIONAL)):
            return ' '.join(toks[:-1]), last
        if (prev in _DIRECTIONAL and len(toks) >= 5 and toks[-3] in set(RL.SUFFIX.values())
                and _BARE_UNIT.match(last)):
            return ' '.join(toks[:-1]), last
    return ' '.join(toks), ''


def propstream_row(ld, cols):
    """One CSV row. Fields: unit, street, fips, apn are derived here; the rest are Lead fields."""
    street, unit = split_unit(ld.property_address)
    d = ld.to_dict()
    d.update({'street': street, 'unit': unit, 'fips': FIPS.get(EX.norm_county(ld.county), ''),
              'apn': getattr(ld, 'folio', ''), 'county': ld.county.title()})
    return [EX._safe(EX._cell(d.get(c['field']))) for c in cols]


def write_csv(leads, cols, path):
    import csv
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([c['header'] for c in cols])
        for ld in leads:
            w.writerow(propstream_row(ld, cols))
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--scope', choices=('all', 'send', 'callable'), default='all')
    ap.add_argument('--list-name', default='')
    ap.add_argument('--county', default='', help='MIAMI-DADE, BROWARD or PALM BEACH (default: all)')
    ap.add_argument('--out-dir', default='')
    ap.add_argument('--twin', default='')
    ap.add_argument('--map', default=MAP_FILE)
    ap.add_argument('--limit', type=int, default=0,
                    help='write only the first N leads (soonest auction first), complete rows only '
                         '(zip, city and APN present). For a capped PropStream plan.')
    ap.add_argument('--skip', type=int, default=0,
                    help='with --limit: skip the first N of that same ordering, so a second run does '
                         'not repeat the first batch')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    today = datetime.date.today()
    try:
        cols = load_map(a.map)
        if a.scope in ('callable', 'send'):
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
                                       county=a.county or None,
                                       send_hold=default_send_hold if a.scope == 'send' else None)
        if a.limit:
            full = [ld for ld in leads if ld.zip and ld.city and getattr(ld, 'folio', '')]
            summary['complete_rows_available'] = len(full)
            leads = full[a.skip:a.skip + a.limit]
            summary['rows_written'] = len(leads)
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
    base = 'propstream_%s_%s%s' % (EX._slug(list_name), today.isoformat(),
                                   ('_first%d_skip%d' % (a.limit, a.skip)) if a.limit else '')
    csv_path = os.path.join(out_dir, base + '.csv')
    write_csv(leads, cols, csv_path)
    with open(os.path.join(out_dir, base + '.summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1)
    print('wrote %d row(s) -> %s' % (len(leads), csv_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
