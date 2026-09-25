"""case_verdict — emit the per-case verdict a person has been writing by hand.

WHY THIS EXISTS
MIAMI-AUTOMATION-STATUS.md's five-case table carries a verdict column — supported, incomplete,
conflicted — and said of it, until the commit that added this module: "The verdict column is written
from the code's output by hand: no module emits supported / incomplete / conflicted yet." That
sentence was the last thing between the pipeline and the acceptance matrix's "Twelve-case review:
explicit review-policy acceptance and unattended evidence report", which is still marked not passed.
A human reading nine kinds of JSON and typing a word is not an unattended report, and it does not
scale past twelve cases.

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
PART_READ = NAMES['part_read']['document_coverage'] + NAMES['part_read']['miami_case_timeline']

# miami_case_timeline sets status kind 'unclear' for nine different reasons. Three are evidence
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

# miami_case_timeline's status kinds, minus the two this module handles on their own ('unclear' and
# 'active_pre_judgment'). An ALLOWLIST: a kind this module has never heard of holds the case, because
# the alternative is that a renamed or added kind silently turns every case into 'supported'.
SETTLED_KINDS = ('judgment_entered', 'sale_scheduled', 'sale_cancelled', 'stayed_by_bankruptcy',
                 'dismissed', 'satisfied_redeemed', 'sold')
# Postures where the docket is moving or has moved a sale. A stay in effect over any of them is a
# contradiction, not a note. The kinds are not enough on their own: when an unresolved stay is
# followed by sale activity, miami_case_timeline (:494) forces kind 'unclear', so this pair is close
# to unreachable in real output and the first version's stay-vs-sale rule was exercised only by a
# fixture shape the pipeline does not write. `_sale_on_the_docket` below reads the sale facts the
# producer actually saves.
SALE_KINDS = ('sale_scheduled', 'sold')
# status kinds that say a sale is over and settled, so a date in the past is not an open question.
# miami_case_timeline records sale_outcome when a scheduled date passes with no certificate.
UNSETTLED_SALE_OUTCOMES = ('unknown_no_certificate', 'held_no_certificate_yet')
# judgment_money's wording when the stated grand total has no matching printed total row on its own
# page: the document's own table disagrees with the figure it prints as the total.
TOTAL_ROW_DISAGREES = 'missing or disagreeing kind=total'

CAVEAT = ('Docket completeness is unproven: the clerk publishes no pagination cursor, so no run can '
          'show the entry list is whole. A verdict describes the evidence read, never the file.')
QUALIFICATION = (
    'Restates states other modules computed; reads and spends nothing. "supported" covers the '
    'controlling judgment, its posture and its amount - not every attachment on the docket. Not '
    'contact clearance (miami_ranking.qualify), not an equity input (equity_state).')


def _rows(timeline, key):
    value = timeline.get(key)
    return value if isinstance(value, list) else []


def _dict(timeline, key):
    """A block that must be a dict, or an empty one. Never assume a saved shape.

    `coverage` and `status` were hardened in the first review round and `judgments` and
    `amount_vision` were not, so a list or a string in either raised AttributeError mid-assess and
    took the whole unattended report down with it.
    """
    value = timeline.get(key)
    return value if isinstance(value, dict) else {}


def _amount_checks(timeline):
    """Every exact-cents check saved with this timeline, whichever path produced it."""
    vision = _dict(timeline, 'amount_vision')
    checks = [c for c in (vision.get('amount_checks') or []) if isinstance(c, dict)]
    return checks + [c for c in _rows(timeline, 'amount_checks') if isinstance(c, dict)]


def _controlling(timeline):
    judgments = _dict(timeline, 'judgments')
    return judgments.get('controlling_entry'), judgments


def _judgment_record(judgments, entry_id):
    """The controlling judgment's own row in reconcile_judgments' list, or None.

    Its `status` is not always 'operative': `operative` there includes 'partially_vacated'
    (miami_case_timeline :753), and `satisfaction` can be 'partially_satisfied'. A judgment that is
    partly vacated and partly satisfied was reading as a clean verified amount.
    """
    rows = judgments.get('judgments')
    if entry_id is None or not isinstance(rows, list):
        return None
    return next((r for r in rows if isinstance(r, dict) and r.get('entry_id') == entry_id), None)


def _sale_on_the_docket(timeline, status, kind):
    """-> a phrase naming the sale the docket is running, or None.

    Reads the facts the producer saves rather than the kind alone: `sale_held` (the clerk's bid and
    deposit entries) and the scheduled `sale_date` both survive a status the stay rewrote to
    'unclear' or 'stayed_by_bankruptcy'.
    """
    held = timeline.get('sale_held')
    if isinstance(held, dict) and held.get('date'):
        return 'the clerk posted sale-day bid and deposit entries on %s' % held['date']
    if held:
        return 'the clerk posted sale-day bid and deposit entries'
    if kind in SALE_KINDS:
        return 'the docket status is %r' % (kind,)
    if status.get('sale_date'):
        return 'a sale is on the calendar for %s' % status['sale_date']
    return None


def _judgment_amount(timeline, entry_id):
    """-> (verified, failed, rejected) checks for the controlling judgment.

    A check counts for this judgment only when it NAMES that entry and was read off a COURT
    document. Both halves are load-bearing, and the first version of this function got both wrong:

      - It accepted `entry_id: None` as "could be this judgment". `run_case_timeline.load_rows` sets
        `entry_ref` only for a `court:` source_ref, so every recorded-instrument row in the same
        pipeline carries None. A recorded mortgage's principal could then corroborate a judgment
        amount, which is verify-12 defect D5 exactly - the corroboration document_prioritizer was
        changed to refuse.
      - It used `check.get('ok')` as a truthiness test, so `ok: 1` or the string `'false'` from
        hand-edited or older saved JSON read as a to-the-cent verification.

    A check that names this judgment but cannot be trusted is `rejected` and reported, never
    silently dropped: a discarded check is indistinguishable from no check at all.
    """
    verified, failed, rejected = [], [], []
    for check in _amount_checks(timeline):
        if entry_id is None or check.get('entry_id') != entry_id:
            continue
        source = str(check.get('source_ref') or '')
        if not source.startswith('court:'):
            rejected.append('a check for this judgment was read off %s, not the court copy'
                            % (source or 'a document with no source recorded'))
            continue
        if check.get('ok') is True:
            if check.get('amount') is None:
                rejected.append('a check for this judgment verifies but records no amount')
            else:
                verified.append(check)
        elif check.get('ok') is False:
            failed.append(check)
        else:
            rejected.append('a check for this judgment records ok=%r, which is neither true nor '
                            'false' % (check.get('ok'),))
    return verified, failed, rejected


def _coverage_of(timeline, entry_id):
    """-> (coverage, rows for this entry). `coverage` may be any shape; callers must not assume."""
    coverage = timeline.get('coverage')
    if not isinstance(coverage, dict):
        coverage = {}
    attachments = coverage.get('attachments')
    rows = [r for r in attachments if isinstance(r, dict)] if isinstance(attachments, list) else []
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

    EVERY REQUIREMENT IS SHOWN, NOT ASSUMED. The first version of this function asked only whether
    anything contradicted "supported", so absent data produced it: a timeline with no coverage block,
    no coverage row for the controlling judgment, an unrecognised status kind, or `ok: 1` each read
    as supported. In this domain that is the one unacceptable direction, so each requirement below
    has to be positively satisfied by something saved, and anything else lands in `missing`.

    Every string in `conflicts`, `missing` and `supported_by` names where it came from, so a reader
    can go straight to the producing block instead of trusting this summary.
    """
    conflicts, missing, notes, supported_by = [], [], [], []
    status = timeline.get('status')
    if not isinstance(status, dict):
        status = {}
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
    elif kind not in SETTLED_KINDS:
        # An allowlist, not a denylist. miami_case_timeline owns this vocabulary; if it gains a kind
        # or renames one, every case must read incomplete until someone looks, rather than every
        # case silently reading supported.
        missing.append('docket status %r is not a posture this module can vouch for' % (kind,))
    if entry_id is None and reason not in (CONFLICTING_JUDGMENTS, NO_OPERATIVE_JUDGMENT):
        missing.append('no single controlling judgment')
    # reconcile_judgments (:681) reaches ONE operative judgment by inferring that an image-less or
    # login-walled same-day entry with the same docket code is the same judgment listed twice - its
    # own reason says "(inferred, not read)". The uniqueness of the controlling judgment is exactly
    # what "supported" is scoped to, so it cannot rest on a document nobody opened.
    duplicates = [d for d in (judgments.get('docket_duplicates_inferred') or []) if d]
    if duplicates and entry_id is not None:
        missing.append('one operative judgment only because same-day final judgment entr%s %s '
                       'was inferred to be a duplicate without being read'
                       % ('y' if len(duplicates) == 1 else 'ies', ', '.join(str(d) for d in duplicates)))
    # A scheduled sale date that has passed with no certificate: the producer's own reason says
    # "whether a sale occurred is unknown". That is not a posture to vouch for, whatever the kind.
    outcome = status.get('sale_outcome')
    if outcome in UNSETTLED_SALE_OUTCOMES:
        missing.append('the sale date has passed and the outcome is %s' % outcome)
    record = _judgment_record(judgments, entry_id)
    if entry_id is not None and record is None:
        missing.append('no record for the controlling judgment in the reconciliation, so nothing '
                       'says what became of it')
    elif record is not None:
        if record.get('status') != 'operative':
            missing.append('the controlling judgment is %s: %s'
                           % (record.get('status') or 'in an unrecorded state',
                              str(record.get('reason') or 'no reason recorded')))
        if str(record.get('satisfaction') or 'no_satisfaction_found') != 'no_satisfaction_found':
            missing.append('the controlling judgment is %s; no satisfaction found is not proof of '
                           'an open balance, and a partial one is not proof of scope'
                           % record.get('satisfaction'))

    # --- the bankruptcy stay -------------------------------------------------------------------
    stay = timeline.get('stay_in_effect')
    history = _rows(timeline, 'stay_history')
    if stay is True:
        # A well-evidenced stay is not a contradiction, and the status table's 2023-020247 is
        # "supported" with one in effect. A stay in effect while the docket moves a sale IS one:
        # 2018-026274, stay #93 with no relief order, against an amended judgment and a sale notice
        # for the same month. 'sold' counts too - a stay over a completed sale is at least as
        # contradictory as one over a scheduled sale.
        sale = _sale_on_the_docket(timeline, status, kind)
        if sale:
            conflicts.append('a bankruptcy stay is in effect while the docket shows a sale going '
                             'ahead (%s); no relief order was identified' % sale)
        else:
            notes.append('a bankruptcy stay is in effect; nothing here clears anyone to be '
                         'contacted')
    elif stay is None and history:
        missing.append('stay state unknown')

    # --- the amount ----------------------------------------------------------------------------
    verified, failed, rejected = _judgment_amount(timeline, entry_id)
    missing.extend(rejected)
    for check in failed:
        why = str(check.get('reason') or '')
        # A printed subtotal its own rows do not reproduce is the document disagreeing with itself.
        # The status table calls this "amount incomplete"; it is recorded here as a contradiction
        # too, because a figure that cannot be made to add up is not merely unread.
        if check.get('disagreeing_subtotals'):
            conflicts.append("the judgment's own printed subtotal does not equal its printed rows: "
                             '%s' % _subtotal_gap(check))
        elif why == AMBIGUOUS_RUN:
            conflicts.append('two different runs of printed rows both reach %s; the document '
                             'supports more than one reading' % _money(check.get('amount')))
        elif why == TOTAL_ROW_DISAGREES:
            missing.append("the judgment prints %s as its total but no printed total row on that "
                           'page matches it (%s)' % (_money(check.get('amount')), why))
        elif 'disagreeing_subtotals' not in check:
            # run_case_timeline started saving that key in the commit that added this module, and
            # keep_cached_amounts (:258) copies an older check verbatim for any document a later
            # pass does not re-read. So on evidence saved before then, the difference between "the
            # pages were not all read" and "the document cannot add up" - 2018-026274's $0.60 - is
            # not in the file at all. Say that, rather than let it degrade silently to "not read".
            missing.append('a total for the controlling judgment does not verify (%s) and the run '
                           'that saved it kept no subtotal detail, so a self-contradiction cannot '
                           'be ruled out here; re-run the timeline for this case' % why)
    amounts = sorted({round(float(c['amount']), 2) for c in verified
                      if isinstance(c.get('amount'), (int, float))})
    if len(amounts) > 1:
        # verify_document returns one check per stated grand total (judgment_money :457) and
        # document_vision appends one grand total PER PAGE (:263), so a judgment printing a
        # different total on two pages yields two checks that both verify. Two verified figures
        # that are not the same number is the document disagreeing with itself, and the first
        # version reported it as "amount verified to the cent" without printing either figure.
        conflicts.append('two different totals on the controlling judgment each verify to the '
                         'cent: %s' % ', '.join(_money(a) for a in amounts))
    if verified:
        supported_by.append('amount %s verified to the cent on %s' % (
            ', '.join(_money(a) for a in amounts) or 'recorded with no figure',
            ', '.join(sorted({str(c.get('source_ref') or c.get('pages') or '?') for c in verified}))))
    else:
        missing.append('no total for the controlling judgment verifies to the cent'
                       + (' (%d check(s) failed)' % len(failed) if failed else ''))
    if verified and failed:
        # A judgment can print several totals (verify_document returns one check per grand total).
        # One verifying does not excuse another that the document cannot reproduce, and reporting
        # only the good one is how "the amount verified" gets said about a document that disagrees
        # with itself.
        missing.append('%d other total(s) on the controlling judgment do not verify: %s'
                       % (len(failed), '; '.join(sorted({_money(c.get('amount')) for c in failed}))))

    # --- what was read for THIS judgment -------------------------------------------------------
    coverage, mine = _coverage_of(timeline, entry_id)
    if entry_id is not None and not mine:
        # Absence is not evidence of reading. A timeline saved before `coverage` existed, a partial
        # write, or an attachment list that simply has no row for this entry all land here.
        missing.append("no coverage row for the controlling judgment, so nothing shows its filing "
                       "was read")
    for row in mine:
        state = row.get('state')
        if state in LOGIN_WALLED:
            missing.append("the controlling judgment's filing is behind the clerk's login (%s)" % state)
        elif state in NO_IMAGE:
            missing.append("the controlling judgment's filing has no document to read (%s)" % state)
        elif state in PART_READ:
            missing.append("the controlling judgment's filing was only partly read (%s)" % state)
        elif state != 'read':
            missing.append("the controlling judgment's filing is %s" % (state or 'in an unrecorded state'))

    # --- everything else, reported and never a blocker (see the module docstring) ---------------
    # Identity, not value: two attachments can be equal dicts, and excluding by == would then drop
    # a second row that is a different filing.
    others = [r for r in (coverage.get('attachments') or [])
              if isinstance(r, dict) and not any(r is m for m in mine)] if isinstance(
                  coverage.get('attachments'), list) else []
    counts = _state_counts(others)
    elsewhere = {k: v for k, v in counts.items() if k not in ('read', 'county_no_document')}
    if elsewhere:
        notes.append('elsewhere on the docket, attachments not read: '
                     + ', '.join('%s %d' % (k, v) for k, v in sorted(elsewhere.items())))
    walled = sum(v for k, v in counts.items() if k in LOGIN_WALLED)
    if walled:
        notes.append('%d filing(s) behind the clerk\'s login; a login is a decision, not a bug' % walled)
    if dossier:
        gaps = [g for g in (dossier.get('open_gaps') or [])]
        for gap in gaps[:20]:
            notes.append('dossier: %s' % gap)
        if len(gaps) > 20:
            notes.append('dossier: %d more open gap(s) not listed here' % (len(gaps) - 20))

    verdict = 'conflicted' if conflicts else ('incomplete' if missing else 'supported')
    return {'case': timeline.get('case'), 'as_of': timeline.get('as_of'),
            'verdict': verdict,
            'controlling_judgment': entry_id,
            # The figure itself, not just "verified": the status table's whole point is the amount,
            # and a report that cannot print it cannot replace the hand-written column. None when
            # nothing verifies; a list when the document verifies two different totals.
            'judgment_amount': amounts[0] if len(amounts) == 1 else (amounts or None),
            'docket_status': kind,
            'stay_in_effect': stay,
            'conflicts': conflicts, 'missing': missing, 'notes': notes,
            'supported_by': supported_by,
            'caveat': CAVEAT, 'qualification': QUALIFICATION}


def _subtotal_gap(check):
    """judgment_money's disagreeing_subtotals, in a sentence a person reads.

    `difference` is signed (rows above minus the printed subtotal, so 2018-026274's is -0.60), and
    `_nearest_rows_above` omits both `rows_above_sum` and `difference` when the row above the
    subtotal is not additive. Printing the raw values gave "off by $-0.60" and "vs None above it".
    """
    out = []
    for row in (check.get('disagreeing_subtotals') or []):
        if not isinstance(row, dict):
            continue
        label = row.get('label') or 'subtotal'
        total = row.get('rows_above_sum')
        if total is None:
            out.append('%s %s, with no additive rows above it to compare'
                       % (label, _money(row.get('amount'))))
            continue
        gap = row.get('difference')
        out.append('%s %s vs %s in the rows above it%s' % (
            label, _money(row.get('amount')), _money(total),
            (' (off by %s)' % _money(abs(gap))) if isinstance(gap, (int, float)) else ''))
    return '; '.join(out) or 'no detail recorded'


def _money(value):
    # Thousands separators, because the figure this report exists to carry is the status table's
    # "$1,746,032.70" and a reader compares it against the judgment by eye.
    try:
        return '${:,.2f}'.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _amount_word(value):
    # The column the status table is for. A list means the document verified two different totals,
    # which is a conflict the row's Why already names; both figures still print.
    if isinstance(value, list):
        return ' / '.join(_money(v) for v in value) or 'not verified'
    return _money(value) if isinstance(value, (int, float)) else 'not verified'


def _stay_word(value):
    return {True: 'IN EFFECT', False: 'no', None: 'unknown'}.get(value, str(value))


def render_markdown(rows):
    # The stay column is not decoration. The first version of this report rendered only
    # conflicts/missing/supported_by, so 2023-020247 - supported, with a bankruptcy stay in effect -
    # printed as a clean row with nothing anywhere on the page saying a stay was in force.
    out = ['# Case verdicts', '',
           '| Case | Verdict | Judgment | Amount | Bankruptcy stay | Why |',
           '|---|---|---|---|---|---|']
    for row in sorted(rows, key=lambda r: (REVIEW_ORDER.index(r['verdict']), str(r.get('case')))):
        why = (row['conflicts'] + row['missing'] + row['supported_by']) or ['-']
        out.append('| %s | %s | %s | %s | %s | %s |' % (
            row.get('case') or '?', row['verdict'], row.get('controlling_judgment') or '-',
            _amount_word(row.get('judgment_amount')), _stay_word(row.get('stay_in_effect')),
            '; '.join(w.replace('|', '/') for w in why[:3])))
    noted = [r for r in rows if r.get('notes')]
    if noted:
        out += ['', '## Also on the record', '']
        for row in sorted(noted, key=lambda r: str(r.get('case'))):
            out.append('- **%s**' % (row.get('case') or '?'))
            for note in row['notes']:
                out.append('  - %s' % note)
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
        # This module's own output lands in the same directory, so a rerun would read
        # case-verdicts.json as a dossier and print it as skipped every time.
        paths += sorted(p for p in Path(args.dossiers).glob('*.json')
                        if not p.stem.endswith('-timeline')
                        and not p.stem.startswith('case-verdicts'))
    if not paths:
        parser.error('give --dossiers DIR or --case FILE')

    rows, skipped, broken = [], [], []
    for path in paths:
        # An unattended report must survive one malformed file. Before this, a saved timeline whose
        # `judgments` was a list raised mid-assess and no report was written at all, so the good
        # cases' verdicts were lost with the bad one.
        try:
            row = for_saved_case(path)
        except Exception as exc:                      # noqa: BLE001 - named in the report, never swallowed
            broken.append('%s: %s: %s' % (path, type(exc).__name__, str(exc)[:200]))
            continue
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
    for note in broken:
        print('  UNREADABLE (saved evidence does not load or does not parse): %s' % note)

    if rows:
        # Every other writer in this chain routes through case_review.output_path, which refuses a
        # path outside paths.DEALFLOW_DIR and anything under OneDrive - CLAUDE.md's Known Folder
        # Move rule. A raw --out here was the one unguarded write path in the chain.
        if args.out:
            import case_review
            try:
                out = Path(case_review.output_path(args.out))
            except ValueError as exc:
                # A refused path is the guard working. Say so in one line instead of a traceback.
                print('  refused --out: %s' % exc)
                return 2
        else:
            out = Path(paths[0]).parent / 'case-verdicts.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
        md = out.with_suffix('.md')
        md.write_text(render_markdown(rows), encoding='utf-8')
        print('  report: %s' % out)
        print('  report: %s' % md)
    return 0 if rows else 1


if __name__ == '__main__':
    raise SystemExit(main())
