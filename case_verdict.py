"""case_verdict — emit the per-case verdict a person has been writing by hand.

WHY THIS EXISTS
MIAMI-AUTOMATION-STATUS.md's five-case table carries a verdict column — supported, incomplete,
conflicted — and says of it: "The verdict column is written from the code's output by hand: no
module emits supported / incomplete / conflicted yet." That sentence is the last thing between the
pipeline and the acceptance matrix's "Twelve-case review: explicit review-policy acceptance and
unattended evidence report", which is still marked not passed. A human reading nine kinds of JSON
and typing a word is not an unattended report, and it does not scale past twelve cases.

WHAT THIS IS ALLOWED TO DO
Restate, in one word plus its reasons, states that other modules already computed. It reads nothing,
fetches nothing, spends nothing, and decides nothing on its own:

    conflicted   two pieces of evidence disagree, and no read document resolves which is right
    incomplete   something needed was not read, not reached, or not verified
    supported    the controlling judgment, the posture and the amount each rest on a read document

It never upgrades a state. Every reason it prints is the producing module's own string, so the
vocabularies stay comparable (the same fact has up to five names across the pipeline — see the
NAMES table below). A verdict is not contact clearance, not an equity input, and not a board field:
`miami_ranking.qualify` remains the gate for who may be called, and equity_state's FACT states are
still reached only through recorded-instrument evidence.

THE SCOPE DECISION, STATED BECAUSE IT WILL BE QUESTIONED
"supported" is scoped to the CONTROLLING JUDGMENT, not to the whole docket. Coverage gaps on
unrelated attachments are listed as notes and do not block it. Two reasons:

  1. It is what the hand-written verdicts mean. 2024-014878 is "supported" in the status table with
     the $1,746,032.70 verified on the judgment's own pages, while other attachments on that docket
     were never read.
  2. Requiring whole-docket completeness makes every case incomplete forever and the word useless.
     `case_dossier`'s `complete` is `not open_gaps`, and one gap is appended whenever
     `pagination_verified` is false — which case_dossier itself documents as "False, always",
     because the clerk publishes no pagination cursor. So a blanket completeness gate is not a
     high standard, it is a constant.

Docket completeness stays exactly what the status file says it is: unproven, and printed as a
standing caveat on every verdict rather than hidden or quietly treated as satisfied.
"""
import argparse
import json
import os
from pathlib import Path

VERDICTS = ('supported', 'incomplete', 'conflicted')
# Reports are read from the top, and a contradiction is what a person needs to see first.
REVIEW_ORDER = ('conflicted', 'incomplete', 'supported')

# The same fact, in each module's own words. Kept as data so a reader can see the drift instead of
# rediscovering it, and so this module can accept either spelling without inventing a third.
NAMES = {
    'login_walled': {'miami_case_timeline': ('login_required', 'login_required_likely'),
                     'document_coverage': ('restricted', 'restricted_likely')},
    'no_image': {'miami_case_timeline': ('no_image_indexed', 'not_fetched', 'missing_attachments'),
                 'document_coverage': ('county_no_document', 'not_enumerated')},
    'part_read': {'miami_case_timeline': ('unreadable_pages', 'unassessed_pages'),
                  'document_coverage': ('read_partial',)},
}
LOGIN_WALLED = NAMES['login_walled']['document_coverage'] + NAMES['login_walled']['miami_case_timeline']
NO_IMAGE = NAMES['no_image']['document_coverage'] + NAMES['no_image']['miami_case_timeline']

# miami_case_timeline sets status kind 'unclear' for eight different reasons. Three are evidence
# CONTRADICTING evidence; the rest are evidence MISSING. The distinction is the whole point of this
# module, so the strings are matched literally and anything unrecognised counts as missing, which is
# the side that cannot overstate what is known.
CONFLICT_REASONS = (
    'Order vacating the judgment, certificate or sale found; subsequent case posture requires '
    'explicit evidence.',
    'Later foreclosure activity conflicts with an unresolved bankruptcy stay; no relief identified.',
    'Conflicting same-date entries; reliable within-day order is unavailable.',
)
CONFLICTING_JUDGMENTS = 'judgments conflict or could not be linked; review required'
NO_OPERATIVE_JUDGMENT = 'no operative judgment'
# judgment_money's own wording when two different runs of printed rows both reach the total. The
# document supports two readings and nothing in it says which table the figure belongs to.
AMBIGUOUS_RUN = ('more than one run of rows adds up to the total; where the table starts is '
                 'ambiguous, review required')

CAVEAT = ('Docket completeness is unproven: the clerk publishes no pagination cursor, so no run can '
          'show the entry list is whole. A verdict describes the evidence read, never the file.')
QUALIFICATION = (
    'Restates states other modules computed; reads and spends nothing. "supported" covers the '
    'controlling judgment, its posture and its amount - not every attachment on the docket. Not '
    'contact clearance (miami_ranking.qualify), not an equity input (equity_state).')


def _rows(timeline, key):
    value = timeline.get(key)
    return value if isinstance(value, list) else []


def _amount_checks(timeline):
    """Every exact-cents check saved with this timeline, whichever path produced it."""
    vision = timeline.get('amount_vision') or {}
    checks = [c for c in (vision.get('amount_checks') or []) if isinstance(c, dict)]
    return checks + [c for c in _rows(timeline, 'amount_checks') if isinstance(c, dict)]


def _controlling(timeline):
    judgments = timeline.get('judgments') or {}
    return judgments.get('controlling_entry'), judgments


def _judgment_amount(timeline, entry_id):
    """The verified total for the controlling judgment, if one verifies.

    A check is tied to a judgment through its source_ref/entry_id when the producer recorded one.
    An amount that verifies on a document belonging to some other entry is NOT this judgment's
    amount: that is verify-12 defect D5, and document_prioritizer already refuses to let another
    case's instrument corroborate this one.
    """
    verified, failed = [], []
    for check in _amount_checks(timeline):
        if entry_id is not None and check.get('entry_id') not in (None, entry_id):
            continue
        (verified if check.get('ok') else failed).append(check)
    return verified, failed


def _coverage_of(timeline, entry_id):
    coverage = timeline.get('coverage') or {}
    rows = [r for r in (coverage.get('attachments') or []) if isinstance(r, dict)]
    return coverage, [r for r in rows if r.get('entry_id') == entry_id]


def _state_counts(rows):
    counts = {}
    for row in rows:
        state = row.get('state')
        if state:
            counts[state] = counts.get(state, 0) + 1
    return counts


def assess(timeline, dossier=None):
    """-> the verdict dict for one case. Pure; takes the saved timeline (and optionally its dossier).

    Every string in `conflicts`, `missing` and `supported_by` names where it came from, so a reader
    can go straight to the producing block instead of trusting this summary.
    """
    conflicts, missing, notes, supported_by = [], [], [], []
    status = timeline.get('status') or {}
    kind = status.get('kind')
    entry_id, judgments = _controlling(timeline)

    # --- posture -------------------------------------------------------------------------------
    reason = str(judgments.get('controlling_reason') or '')
    if reason == CONFLICTING_JUDGMENTS:
        conflicts.append('judgments: %s' % reason)
    elif reason == NO_OPERATIVE_JUDGMENT:
        missing.append('judgments: %s' % reason)
    if kind in (None, 'unclear'):
        text = str(status.get('reason') or 'no reason recorded')
        (conflicts if text in CONFLICT_REASONS else missing).append('docket status unclear: %s' % text)
    elif kind == 'active_pre_judgment':
        missing.append('no judgment entered yet')
    if entry_id is None and reason not in (CONFLICTING_JUDGMENTS, NO_OPERATIVE_JUDGMENT):
        missing.append('no single controlling judgment')

    # --- the bankruptcy stay -------------------------------------------------------------------
    stay = timeline.get('stay_in_effect')
    history = _rows(timeline, 'stay_history')
    if stay is True:
        # A stay in effect is not by itself a contradiction; a stay in effect WHILE the docket runs
        # a sale is. 2018-026274: stay #93 with no relief order, against an amended judgment and a
        # sale notice for the same month.
        if kind == 'sale_scheduled' or timeline.get('sale_held'):
            conflicts.append('a bankruptcy stay is in effect while the docket shows a sale going '
                             'ahead; no relief order was identified')
        else:
            notes.append('a bankruptcy stay is in effect')
    elif stay is None and history:
        missing.append('stay state unknown')

    # --- the amount ----------------------------------------------------------------------------
    verified, failed = _judgment_amount(timeline, entry_id)
    for check in failed:
        # A printed subtotal its own rows do not reproduce is the document disagreeing with itself.
        # The status table calls this "amount incomplete"; it is recorded here as a contradiction
        # too, because a figure that cannot be made to add up is not merely unread.
        if check.get('disagreeing_subtotals'):
            conflicts.append("the judgment's own printed subtotal does not equal its printed rows: "
                             '%s' % _subtotal_gap(check))
        elif str(check.get('reason') or '') == AMBIGUOUS_RUN:
            conflicts.append('two different runs of printed rows both reach %s; the document '
                             'supports more than one reading' % _money(check.get('amount')))
    if verified:
        supported_by.append('amount verified to the cent on %s' % ', '.join(
            sorted({str(c.get('source_ref') or c.get('pages') or '?') for c in verified})))
    else:
        missing.append('no total for the controlling judgment verifies to the cent'
                       + (' (%d check(s) failed)' % len(failed) if failed else ''))

    # --- what was read for THIS judgment -------------------------------------------------------
    coverage, mine = _coverage_of(timeline, entry_id)
    for row in mine:
        state = row.get('state')
        if state in LOGIN_WALLED:
            missing.append("the controlling judgment's filing is behind the clerk's login (%s)" % state)
        elif state in NO_IMAGE:
            missing.append("the controlling judgment's filing has no document to read (%s)" % state)
        elif state and state != 'read':
            missing.append("the controlling judgment's filing is %s" % state)

    # --- everything else, reported and never a blocker (see the module docstring) ---------------
    counts = _state_counts([r for r in (coverage.get('attachments') or []) if isinstance(r, dict)])
    elsewhere = {k: v for k, v in counts.items() if k not in ('read', 'county_no_document')}
    if elsewhere:
        notes.append('elsewhere on the docket, attachments not read: '
                     + ', '.join('%s %d' % (k, v) for k, v in sorted(elsewhere.items())))
    walled = sum(v for k, v in counts.items() if k in LOGIN_WALLED)
    if walled:
        notes.append('%d filing(s) behind the clerk\'s login; a login is a decision, not a bug' % walled)
    if dossier:
        for gap in (dossier.get('open_gaps') or [])[:20]:
            notes.append('dossier: %s' % gap)

    verdict = 'conflicted' if conflicts else ('incomplete' if missing else 'supported')
    return {'case': timeline.get('case'), 'as_of': timeline.get('as_of'),
            'verdict': verdict,
            'controlling_judgment': entry_id,
            'docket_status': kind,
            'stay_in_effect': stay,
            'conflicts': conflicts, 'missing': missing, 'notes': notes,
            'supported_by': supported_by,
            'caveat': CAVEAT, 'qualification': QUALIFICATION}


def _subtotal_gap(check):
    out = []
    for row in (check.get('disagreeing_subtotals') or []):
        if not isinstance(row, dict):
            continue
        out.append('%s %s vs %s above it%s' % (
            row.get('label') or 'subtotal', _money(row.get('amount')),
            _money(row.get('rows_above_sum')),
            (' (off by %s)' % _money(row.get('difference'))) if row.get('difference') is not None else ''))
    return '; '.join(out) or 'no detail recorded'


def _money(value):
    try:
        return '$%.2f' % float(value)
    except (TypeError, ValueError):
        return str(value)


def render_markdown(rows):
    out = ['# Case verdicts', '',
           '| Case | Verdict | Controlling judgment | Why |', '|---|---|---|---|']
    for row in sorted(rows, key=lambda r: (REVIEW_ORDER.index(r['verdict']), str(r.get('case')))):
        why = (row['conflicts'] + row['missing'] + row['supported_by']) or ['-']
        out.append('| %s | %s | %s | %s |' % (
            row.get('case') or '?', row['verdict'], row.get('controlling_judgment') or '-',
            '; '.join(w.replace('|', '/') for w in why[:3])))
    out += ['', '## Counted', '']
    for name in REVIEW_ORDER:
        out.append('- %s: %d' % (name, sum(1 for r in rows if r['verdict'] == name)))
    out += ['', CAVEAT, '', QUALIFICATION, '']
    return '\n'.join(out)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def for_saved_case(dossier_path):
    """-> the verdict for one saved dossier, or None when it has no whole-case timeline yet."""
    path = Path(dossier_path)
    dossier = _load(path)
    timeline = _load(path.with_name(path.stem + '-timeline.json'))
    if not isinstance(timeline, dict):
        return None
    return assess(timeline, dossier if isinstance(dossier, dict) else None)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Emit the supported / incomplete / conflicted verdict for saved Miami cases. '
                    'Reads saved evidence only: no requests, no spending.')
    parser.add_argument('--dossiers', help='directory of saved <case>.json dossiers')
    parser.add_argument('--case', action='append', default=[],
                        help='one saved dossier path; repeatable')
    parser.add_argument('--out', help='write the report here (default: alongside the dossiers)')
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.case]
    if args.dossiers:
        paths += sorted(p for p in Path(args.dossiers).glob('*.json')
                        if not p.stem.endswith('-timeline'))
    if not paths:
        parser.error('give --dossiers DIR or --case FILE')

    rows, skipped = [], []
    for path in paths:
        row = for_saved_case(path)
        (rows.append(row) if row else skipped.append(str(path)))
    rows.sort(key=lambda r: (REVIEW_ORDER.index(r['verdict']), str(r.get('case'))))

    for row in rows:
        why = (row['conflicts'] or row['missing'] or row['supported_by'] or ['-'])[0]
        print('  %-24s %-11s %s' % (row.get('case') or '?', row['verdict'], why))
    print('case verdicts: %d case(s); %s; $0 spent, no requests' % (
        len(rows), ', '.join('%s %d' % (n, sum(1 for r in rows if r['verdict'] == n))
                             for n in REVIEW_ORDER)))
    for path in skipped:
        print('  SKIPPED (no whole-case timeline saved): %s' % path)

    if rows:
        out = Path(args.out) if args.out else Path(paths[0]).parent / 'case-verdicts.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
        md = out.with_suffix('.md')
        md.write_text(render_markdown(rows), encoding='utf-8')
        print('  report: %s' % out)
        print('  report: %s' % md)
    return 0 if rows else 1


if __name__ == '__main__':
    raise SystemExit(main())
