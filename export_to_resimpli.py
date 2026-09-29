#!/usr/bin/env python
"""export_to_resimpli — the board's callable leads as a REsimpli List Stacking CSV.

WHAT IT DOES
Reads the board's own merged rows (the Desktop twin's RAW payload, the same source sheets_crm.py
uses), keeps only the leads Call Mode would let a person dial today, turns each into a
resimpli_lead.Lead, dedupes on the property, and writes a CSV whose headers come from
resimpli_map.json. Nothing is sent anywhere: the CSV is uploaded by hand on REsimpli's List
Stacking page.

WHY IT GATES EXACTLY LIKE CALL MODE
Anything in REsimpli can be called or texted from REsimpli, outside every gate in this repo. So a
row leaves only if it passes the same checks as a dial, and a held row is COUNTED, never written:

  whole file held (nothing written) when any of these is not clean:
    * opt-out ledger missing, unreadable or stale       cadence._ledger_send_block()
    * today's 07:15 opt-out sync not confirmed          cadence._sync_send_block()
    * bounced_emails.json unreadable                     cadence._bounce_send_block()
    * Quo inbound STOP scan failed or stale              quo_sync.text_hold()  (REsimpli texts)
  per row:
    * Call Mode's selection                              call_mode.call_rows(): case and person-level
      opt-outs, dead ledger, active stay flags, federal bankruptcy hold, title transferred,
      diligence hold, DNC numbers and not-the-owner numbers removed, no dialable number -> out
    * Call Mode's page-side note gates (worker_notes.json): hard/soft no and opt-out person-wide,
      wrong number, Dead; dead (badph) and do-not-text (dntph) numbers removed
    * the send bridge's stay verdict, cache only        stay_gate.check(): never_contact.json, the
      §362 stay cache, PACER/CourtListener caches. Unlike /send it never triggers a paid or rate-
      limited lookup; an unverified case is simply held.

Call Mode's scheduling choices are NOT applied: the 60-day auction window and the per-phone cap
are lifted (List Stacking wants the whole callable book). The equity floor stays, because it is
inside call_rows and this module does not reach into it.

These gates are read, never changed here (CLAUDE.md "OWNED SURFACE: suppression").

WHERE THE FILE GOES
<DEALFLOW_DIR>/exports/ by default, outside the repo, per CLAUDE.md's output-path rule (the repo
is public and homeowner data never lives in it). --out-dir overrides. A summary JSON with counts
only is written beside the CSV.

RUN
  python export_to_resimpli.py                         # all counties, default list name
  python export_to_resimpli.py --county MIAMI-DADE --list-name "Miami-Dade Pre-Foreclosure 2026-09"
  python export_to_resimpli.py --dry-run               # gates + summary, no files written
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys

import resimpli_lead as RL

HERE = os.path.dirname(os.path.abspath(__file__))
MAP_FILE = os.path.join(HERE, 'resimpli_map.json')
STAY_CACHE_FILE = os.path.join(HERE, 'sale_history_cache.json')
OPTOUTS_FILE = os.path.join(HERE, 'optouts.json')
DEADS_FILE = os.path.join(HERE, 'deads.json')
LEAD_FIELDS = set(RL.Lead.__dataclass_fields__)


class ExportError(Exception):
    pass


# ---------------------------------------------------------------- source

def read_twin(path=None):
    """The board's RAW rows from the plaintext Desktop twin. No fallback: the slim county files
    carry no phones and no baked stay flags, so exporting from them would be exporting less-gated
    data. A missing twin is an error."""
    if path is None:
        import paths as P
        path = P.TWIN
    try:
        h = open(path, encoding='utf-8').read()
    except OSError as e:
        raise ExportError('board twin not readable at %s (%s). Run the refresh first.' % (path, e))
    i = h.find('RAW = ')
    if i < 0:
        raise ExportError('board twin at %s carries no RAW payload' % path)
    try:
        rows, _ = json.JSONDecoder().raw_decode(h, i + len('RAW = '))
    except ValueError as e:
        raise ExportError('board twin RAW payload does not parse (%s)' % str(e)[:80])
    if not isinstance(rows, list) or not rows:
        raise ExportError('board twin RAW payload is empty')
    return rows


# ---------------------------------------------------------------- gates (read-only)

def preflight():
    """[] when the whole export may run; otherwise one reason per failed whole-file gate.
    Every check fails closed: a gate that cannot be evaluated is a hold."""
    holds = []
    try:
        import cadence as _C
        for fn in (_C._ledger_send_block, _C._sync_send_block, _C._bounce_send_block):
            try:
                why = fn()
            except Exception as e:
                why = '%s could not be evaluated (%s)' % (fn.__name__, str(e)[:80])
            if why:
                holds.append(why)
    except Exception as e:
        holds.append('the opt-out gates could not be imported (%s)' % str(e)[:80])
    try:
        import quo_sync
        held, why = quo_sync.text_hold()
        if held:
            holds.append(why or 'HOLD texting — inbound STOP scan not confirmed')
    except Exception as e:
        holds.append('the texting hold could not be evaluated (%s)' % str(e)[:80])
    return holds


def load_suppression(optouts_path=OPTOUTS_FILE, notes_keys=None):
    """-> (optouts, case_keys, emails).

    optouts: the dict call_rows expects (case keys, '@email' / '#digits' identity keys).
    Every ledger entry with a truthy value counts, which is outreach_email._load_optouts' rule and
    wider than the board bake's. REP-logged DNC notes (optout_sync.notes_dnc_keys) are unioned in.
    An unreadable ledger raises; preflight() should already have refused it."""
    try:
        with open(optouts_path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise ExportError('optouts.json is missing')
    except (OSError, ValueError) as e:
        raise ExportError('optouts.json is unreadable (%s)' % str(e)[:80])
    if isinstance(data, list):
        keys = [str(x) for x in data]
    elif isinstance(data, dict):
        notes = data.get('notes') if isinstance(data.get('notes'), dict) else data
        keys = [str(k) for k, v in notes.items() if v]
    else:
        raise ExportError('optouts.json is not an object or a list')
    if notes_keys is None:
        try:
            from optout_sync import notes_dnc_keys
            notes_keys = notes_dnc_keys()
        except Exception as e:
            raise ExportError('rep-logged DNC notes could not be read (%s)' % str(e)[:80])
    keys += [str(k) for k in notes_keys]
    optouts, cases, emails = {}, set(), set()
    for k in keys:
        k = k.strip()
        if not k:
            continue
        if k[0] == '@':
            emails.add(k[1:].strip().lower())
            optouts['@' + k[1:].strip().lower()] = {'optout': 1}
        elif k[0] == '#':
            d = re.sub(r'\D', '', k[1:])
            optouts['#' + d] = {'optout': 1}
            # '13055550101' and '3055550101' are one number (resimpli_sync.norm_number); key both
            # spellings so an 11-digit ledger entry still matches a 10-digit phone and back.
            if RL.digits10(d):
                optouts['#' + RL.digits10(d)] = {'optout': 1}
        elif '@' in k:                                # a bare address in a list-shaped ledger
            emails.add(k.lower())
            optouts['@' + k.lower()] = {'optout': 1}
        else:
            cases.add(k.upper())
            optouts[k] = optouts[k.upper()] = {'optout': 1}
    return optouts, cases, emails


def load_deads(path=DEADS_FILE):
    """Same statuses make_tracker retires. Missing file = none; unreadable = error."""
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding='utf-8')) or {}
    except Exception as e:
        raise ExportError('deads.json is unreadable (%s)' % str(e)[:80])
    return {c: {'status': 'Dead'} for c, n in raw.items()
            if isinstance(n, dict) and str(n.get('status') or '').upper()
            in ('DEAD', 'CLOSED', 'LOST - SOLD AT AUCTION')}


NOTES_FILE = os.path.join(HERE, 'worker_notes.json')


def load_notes(path=NOTES_FILE):
    """The rep notes Call Mode's page gates on (the bridge's backup of board + phone notes).
    Missing = none on this machine. A file that exists and does not parse is an error: those notes
    are where a wrong number, a soft no, a dead number or a do-not-text lives."""
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        raise ExportError('worker_notes.json is unreadable (%s)' % str(e)[:80])
    notes = d.get('notes') if isinstance(d, dict) and isinstance(d.get('notes'), dict) else d
    if not isinstance(notes, dict):
        raise ExportError('worker_notes.json is not a notes object')
    return notes


def _digits_set(xs):
    out = set()
    for x in xs or []:
        d = re.sub(r'\D', '', str(x or ''))
        out |= {d, RL.digits10(d)} - {''}
    return out


def notes_verdict(case, person_cases, notes):
    """-> (reason, blocked_numbers). reason '' = the notes do not hold this lead.

    Call Mode's page gates on these at dial time (call_mode.py hardSuppressed / noState /
    nextLivePh / dntph), not in call_rows, so the export applies them itself:
      person-wide over every case the person owns: hard no, opt-out, DO NOT CONTACT, soft no or
      'Not interested'. A soft no is RETIRED here outright: REsimpli would call it on its own
      schedule, so the page's one event-driven resurface cannot be honoured outside the page.
      this case: wrong number reported, status Dead.
      numbers: badph (dead) and dntph (do-not-text) on any of the person's cases are removed."""
    blocked = set()
    for c in [case] + [x for x in person_cases if x != case]:
        n = notes.get(c) or {}
        if not isinstance(n, dict):
            continue
        if n.get('no') == 'hard':
            return 'hard_no', blocked
        if n.get('optout') or str(n.get('status') or '').upper() in ('DO NOT CONTACT', 'OPTED OUT', 'DNC'):
            return 'opted_out_in_notes', blocked
        if n.get('no') == 'soft' or (not n.get('no') and n.get('status') == 'Not interested'):
            return 'soft_no_retired', blocked
        blocked |= _digits_set(n.get('badph')) | _digits_set(n.get('dntph'))
    n = notes.get(case) or {}
    if isinstance(n, dict):
        if n.get('wrongown'):
            return 'wrong_number_reported', blocked
        if str(n.get('status') or '').upper() == 'DEAD':
            return 'dead_in_notes', blocked
    return '', blocked


def load_bounced():
    try:
        import outreach_email as _oe
        return set(_oe._load_bounced())
    except Exception as e:
        raise ExportError('bounced_emails.json could not be read (%s)' % str(e)[:80])


def default_stay_check(case):
    import stay_gate
    return stay_gate.check(case, STAY_CACHE_FILE)


def _person_optout_fn(optouts):
    """Person-level opt-out test on EVERY email and phone on the row, DNC numbers included.
    Raw keys always; hashed keys too when foreclosure_leads._addr_key imports."""
    ident = {str(k) for k in optouts if str(k)[:1] in ('@', '#')}
    try:
        from foreclosure_leads import _addr_key as ak
    except Exception:
        ak = None

    def test(row):
        for e in (row.get('emails') or []):
            e = str(e or '').strip().lower()
            if e and (('@' + e) in ident or (ak and ('@' + ak(e)) in ident)):
                return True
        for p in (row.get('phones') or []):
            p = re.sub(r'\D', '', str(p or ''))
            # both spellings: a cache can hold '13055550101' (skiptrace.py keeps what it was sent)
            for q in {p, RL.digits10(p)} - {''}:
                if ('#' + q) in ident or (ak and ('#' + ak(q)) in ident):
                    return True
        return False
    return test


def _hold_reason_fn():
    """Label for a row call_rows did not return: the FIRST of call_rows' own checks it fails, in
    call_rows' order. This only names the reason for the summary; call_rows alone decides who is
    held. 'other' means none of these matched (for example two rows of one sale collapsed by
    call_rows' calendar dedupe), so a large 'other' says this labeller has drifted from call_rows."""
    idx, bk = None, None
    try:
        import bk_lookup as bk
        idx = bk.federal_hold_index()
    except Exception:
        idx = None
    try:
        import stay_gate as sg
    except Exception:
        sg = None
    try:
        import diligence_gate as dgm
        tally = dgm.Tally()
    except Exception:
        tally = None
    floor = float(os.environ.get('CALLMODE_EQ_FLOOR', '-25'))

    def federal(case):
        if idx is not None:
            try:
                return bool(bk.federal_hold(case, index=idx)[0])
            except Exception:
                pass
        if sg is not None:
            return not bool(sg.case_stem(case))
        return True

    def reason(d, deads):
        case = str(d.get('case') or '')
        is_bal = d.get('st') == 'BAL'
        if not case:
            return 'no_case_number'
        if case in deads:
            return 'dead_ledger'
        if d.get('sibclaimed'):
            return 'sibling_case_sold'
        if d.get('saleBkAct'):
            return 'bankruptcy_federal_flag' if d.get('bkWhy') else 'bankruptcy_stay_on_docket'
        if d.get('lpDismissed'):
            return 'lis_pendens_dismissed'
        if federal(case):
            return 'bankruptcy_federal_hold'
        if d.get('title_status') == 'transferred':
            return 'title_transferred'
        if tally is not None and not is_bal:
            try:
                if tally.check(d).get('hold'):
                    return 'diligence_hold'
            except Exception:
                return 'diligence_unevaluable'
        try:
            eq = float(d.get('eq'))
        except (TypeError, ValueError):
            eq = None
        if eq is not None and eq <= floor:
            return 'equity_below_floor'
        dnc, src = d.get('phdnc') or [], d.get('phsrc') or []
        if not any(RL.digits10(p) and not (dnc[i] if i < len(dnc) else False)
                   and (src[i] if i < len(src) else '') not in ('ag', 'xl')
                   for i, p in enumerate(d.get('phones') or [])):
            return 'no_dialable_phone'
        try:
            days = int(d.get('days'))
        except (TypeError, ValueError):
            days = 9999
        is_lp = d.get('st') == 'LP'
        if not is_lp and not is_bal and days < 0:
            return 'auction_passed'
        if is_lp and d.get('auction') and days < 0:
            return 'lis_pendens_sale_passed'
        if d.get('vac'):
            return 'vacant_land'
        if re.search(r'timeshare|parcel not linked', str(d.get('warn') or ''), re.I):
            return 'timeshare_or_unlinked_parcel'
        return 'other'
    return reason


def gate_rows(rows, optouts, opt_cases, deads, stay_check=default_stay_check, notes=None, all_rows=None):
    """-> (passed [(row, phones)], counts). Order follows `rows`."""
    import call_mode
    dial, _total = call_mode.call_rows(rows, optouts=optouts, deads=deads,
                                       max_days=10 ** 6, cap=10 ** 9)
    phones_by_case = {r['c']: list(r.get('p') or []) for r in dial}
    counts = {'held_optout': 0, 'held_call_mode': 0, 'held_call_mode_by_reason': {},
              'held_notes': 0, 'held_notes_by_reason': {}, 'held_stay_gate': 0, 'stay_codes': {}}
    notes = notes or {}
    person_cases = {}
    # the WHOLE board, not the --county slice: a hard no on the person's Broward case must still
    # hold their Miami-Dade case (the page walks r.pcs, built from every row)
    for r in (all_rows if all_rows is not None else rows):
        if r.get('pkey') and r.get('case'):
            person_cases.setdefault(r['pkey'], []).append(str(r['case']))
    person_optout = _person_optout_fn(optouts)
    reason_of = _hold_reason_fn()
    passed = []
    for row in rows:
        case = str(row.get('case') or '')
        # Belt to call_rows' own person-level check, which silently turns off when
        # foreclosure_leads cannot be imported (it needs that module's _addr_key even for raw keys).
        if case.upper() in opt_cases or person_optout(row):
            counts['held_optout'] += 1
            continue
        why = reason_of(row, deads)
        # call_rows answers per CASE (it collapses two calendar rows of one sale), so a case it
        # returned can still arrive here on a row it held. Re-check the row itself: any reason the
        # labeller finds on a row call_rows passed is a hold, never a pass.
        if case not in phones_by_case or why != 'other':
            counts['held_call_mode'] += 1
            by = counts['held_call_mode_by_reason']
            by[why] = by.get(why, 0) + 1
            continue
        nwhy, blocked = notes_verdict(case, person_cases.get(row.get('pkey')) or [], notes)
        phones = [p for p in phones_by_case[case] if not ({p, RL.digits10(p)} & blocked)]
        if not nwhy and not phones:
            nwhy = 'every_number_bad_or_do_not_text'
        if nwhy:
            counts['held_notes'] += 1
            nb = counts['held_notes_by_reason']
            nb[nwhy] = nb.get(nwhy, 0) + 1
            continue
        v = stay_check(case)
        if not v.get('ok'):
            counts['held_stay_gate'] += 1
            code = v.get('code') or 'unknown'
            counts['stay_codes'][code] = counts['stay_codes'].get(code, 0) + 1
            continue
        passed.append((row, phones))
    return passed, counts


# ---------------------------------------------------------------- mapping + CSV

def load_map(path=MAP_FILE):
    """resimpli_map.json -> list of column specs. Unknown fields and duplicate headers are errors,
    so a typo in the map fails the run instead of shipping an empty column."""
    try:
        m = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        raise ExportError('%s does not load (%s)' % (os.path.basename(path), e))
    cols = m.get('columns') if isinstance(m, dict) else None
    if not isinstance(cols, list) or not cols:
        raise ExportError('%s has no "columns" list' % os.path.basename(path))
    seen = set()
    for c in cols:
        h = c.get('header') if isinstance(c, dict) else None
        if not h:
            raise ExportError('a column in %s has no header' % os.path.basename(path))
        if h in seen:
            raise ExportError('header %r appears twice in %s' % (h, os.path.basename(path)))
        seen.add(h)
        if 'value' not in c and c.get('field') not in LEAD_FIELDS:
            raise ExportError('column %r maps unknown field %r' % (h, c.get('field')))
    return cols


_NUMERIC = re.compile(r'^-?\d+(\.\d+)?$')


def _safe(text):
    """A text cell Excel would read as a formula gets a leading apostrophe. Plain numbers
    (a negative equity percent) are left alone."""
    if text and text[0] in '=+-@\t\r' and not _NUMERIC.match(text):
        return "'" + text
    return text


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else ('%.2f' % v)
    return str(v)


def render_row(lead, cols):
    d = lead.to_dict()
    out = []
    for c in cols:
        if 'value' in c:
            out.append(_safe(_cell(c['value'])))
            continue
        v = d.get(c['field'])
        if isinstance(v, list):
            if 'index' in c:
                i = int(c['index'])
                v = v[i] if i < len(v) else ''
            else:
                v = c.get('join', ', ').join(str(x) for x in v)
        out.append(_safe(_cell(v)))
    return out


def write_csv(leads, cols, path):
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([c['header'] for c in cols])
        for ld in leads:
            w.writerow(render_row(ld, cols))
    os.replace(tmp, path)


# ---------------------------------------------------------------- build

def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower() or 'list'


def default_list_name(rows, today):
    counties = sorted({str(r.get('county') or '').upper() for r in rows if r.get('county')})
    where = counties[0].title() if len(counties) == 1 else 'South Florida'
    return '%s Pre-Foreclosure %s' % (where, today.strftime('%Y-%m'))


def norm_county(c):
    """Comparison key only: 'palm-beach', 'Palm_Beach', 'PALM BEACH' -> 'PALMBEACH';
    'MIAMI-DADE', 'miami dade' -> 'MIAMIDADE'."""
    return re.sub(r'[^A-Z]', '', str(c or '').upper())


def _sort_key(row):
    d = row.get('days')
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 9999
    return (d if d >= 0 else 9999, str(row.get('case') or ''))


def build(rows, optouts, opt_cases, opt_emails, deads, bounced, list_name,
          county=None, stay_check=default_stay_check, notes=None):
    """-> (leads, summary). Pure given its inputs; main() supplies the real ones."""
    all_rows = rows
    if county:
        want = norm_county(county)
        present = {norm_county(r.get('county')): str(r.get('county')).upper()
                   for r in rows if r.get('county')}
        if not want or want not in present:
            raise ExportError('--county %r matches no lead. Counties on the board: %s'
                              % (county, ', '.join(sorted(present.values())) or 'none'))
        rows = [r for r in rows if norm_county(r.get('county')) == want]
    rows = sorted(rows, key=_sort_key)
    passed, counts = gate_rows(rows, optouts, opt_cases, deads, stay_check=stay_check, notes=notes,
                              all_rows=all_rows)
    leads, no_addr = [], 0
    for row, phones in passed:
        emails = [e for e in (row.get('emails') or [])
                  if str(e or '').strip().lower() not in bounced
                  and str(e or '').strip().lower() not in opt_emails]
        ld = RL.from_board_row(row, phones, emails, list_name=list_name)
        if not ld.property_address:
            no_addr += 1
            continue
        leads.append(ld)
    leads, dupes = RL.dedupe(leads)
    summary = {
        'rows_in': len(rows),
        'held_optout': counts['held_optout'],
        'held_call_mode': counts['held_call_mode'],
        'held_call_mode_by_reason': dict(sorted(counts['held_call_mode_by_reason'].items(),
                                                key=lambda kv: -kv[1])),
        'held_notes': counts['held_notes'],
        'held_notes_by_reason': counts['held_notes_by_reason'],
        'held_stay_gate': counts['held_stay_gate'],
        'stay_codes': counts['stay_codes'],
        'dropped_missing_address': no_addr,
        'dupes_dropped': dupes,
        'rows_written': len(leads),
        'written_without_email': sum(1 for ld in leads if not ld.emails),
        'written_without_zip': sum(1 for ld in leads if not ld.zip),
        'written_equity_verified': sum(1 for ld in leads if ld.equity_verified),
        'equity_note': ('Est Equity % is the board percent. It is a guess unless Equity Verified '
                        'is Yes (the recorded mortgage chain was traced).'),
    }
    return leads, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--list-name', default='')
    ap.add_argument('--county', default='', help='MIAMI-DADE, BROWARD or PALM BEACH (default: all)')
    ap.add_argument('--out-dir', default='')
    ap.add_argument('--twin', default='', help='board twin path (default: paths.TWIN)')
    ap.add_argument('--map', default=MAP_FILE)
    ap.add_argument('--dry-run', action='store_true', help='run every gate and print the summary; write nothing')
    a = ap.parse_args(argv)
    today = datetime.date.today()
    try:
        cols = load_map(a.map)
        holds = preflight()
        if holds:
            print('EXPORT HELD — nothing written. Every row stays in DealFlow until these clear:')
            for h in holds:
                print('  - ' + h)
            return 3
        rows = read_twin(a.twin or None)
        optouts, opt_cases, opt_emails = load_suppression()
        deads = load_deads()
        bounced = load_bounced()
        notes = load_notes()
        list_name = a.list_name or default_list_name(
            [r for r in rows if not a.county or norm_county(r.get('county')) == norm_county(a.county)], today)
        leads, summary = build(rows, optouts, opt_cases, opt_emails, deads, bounced, list_name,
                               county=a.county or None, notes=notes)
    except ExportError as e:
        print('EXPORT FAILED — nothing written: %s' % e)
        return 2
    if not os.path.exists(NOTES_FILE):
        summary['notes_warning'] = ('worker_notes.json not found on this machine: rep notes '
                                    '(wrong number, soft no, bad / do-not-text numbers) were NOT applied')
        print('WARNING: ' + summary['notes_warning'])
    summary.update({'list_name': list_name, 'date': today.isoformat(), 'dry_run': bool(a.dry_run),
                    'county': a.county or 'ALL'})
    print(json.dumps(summary, indent=1))
    if a.dry_run:
        print('dry run: no files written')
        return 0
    if a.out_dir:
        out_dir = a.out_dir
    else:
        import paths as P
        out_dir = P.out('exports')
    os.makedirs(out_dir, exist_ok=True)
    base = 'resimpli_%s_%s' % (_slug(list_name), today.isoformat())
    csv_path = os.path.join(out_dir, base + '.csv')
    write_csv(leads, cols, csv_path)
    with open(os.path.join(out_dir, base + '.summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1)
    print('wrote %d row(s) -> %s' % (len(leads), csv_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
