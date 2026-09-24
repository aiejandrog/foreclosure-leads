"""Miami whole-case timelines; free OCR and opt-in, cumulatively capped vision."""
import argparse
import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path
from contextlib import nullcontext
from collections import Counter

import document_collectors as DC
import document_queue as DQ
import document_store as DS

COUNTY = 'MIAMI-DADE'


def validate_case(value):
    if not re.fullmatch(r'\d{4}-\d{6}-(?:CA-01|CC-\d{2})', value):
        raise ValueError('Expected a Miami-Dade civil case number')
    return value


def budget_snapshot(path, cap):
    """Observe the shared ledger; never create/reset it or authorize paid reads."""
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError('Vision cap must be finite and positive')
    value = DS.pipeline_load(path)
    report = {'cap_usd': cap, 'paid_requests_this_command': 0,
              'scope': 'shared title-discovery vision ledger', 'path': str(path)}
    if value is None:
        return dict(report, status='unknown', remaining_usd=None,
                    reason='Existing spend ledger unavailable; no paid reading authorized')
    actual = float(value['actual_usd'])
    reserved = sum(float(v) for v in value['reserved'].values())
    if not all(math.isfinite(v) and v >= 0 for v in (actual, reserved)):
        raise ValueError('Invalid shared vision ledger')
    return dict(report, status='observed', actual_usd=actual, reserved_usd=reserved,
                remaining_usd=max(0.0, cap - actual - reserved))


def load_rows(base):
    rows = []
    for path in sorted(Path(base).glob('*.json')):
        if not re.fullmatch(r'[0-9a-f]{64}', path.stem):
            continue
        row = DS.pipeline_load(path)
        if not isinstance(row, dict) or 'manifest' not in row:
            continue
        ref = row.get('source_ref', '')
        if ref.startswith('court:'):
            row['entry_ref'] = ref.split(':', 2)[1]
        rows.append(row)
    return rows


def source_comparison(case, inventory, cache_path):
    """The compact board cache is comparison-only, never timeline evidence."""
    raw = inventory.get('raw') or {}
    entries = raw.get('dockets')
    if ((raw.get('caseNumber') or raw.get('caseNo')) != case or
            not isinstance(entries, list)):
        raise ValueError('Full OCS case payload required; refresh with --collect')
    if [e.get('metadata') for e in inventory.get('entries', [])] != entries:
        raise ValueError('Inventory differs from full OCS entries/comments; refresh with --collect')
    try:
        cache = DS.pipeline_load(cache_path) or {}
        cached = cache.get(case) or {}
        compact = cached.get('ents')
        count = len(compact) if isinstance(compact, list) else None
        cache_status = 'available' if count is not None else 'case_or_cache_missing'
    except (ValueError, OSError, AttributeError):
        cached, count, cache_status = {}, None, 'unreadable_cache'
    return {'timeline_source': 'full OCS GetSingleCaseResult payload',
            'full_ocs_entries': len(entries), 'timeline_entries': len(inventory['entries']),
            'cache_entries': count, 'cache_reported_total': cached.get('n'),
            'cache_path': str(cache_path), 'cache_status': cache_status,
            'cache_date': cached.get('ts'),
            'counts_differ': len(entries) != count if count is not None else None,
            'note': 'Cache is comparison-only; its reported total is not its retained entry count. OCS pagination completeness remains unproven.'}


def acquire(case, base=None, collect=False, docket_cache=None):
    """One complete queue sweep; download/read failures remain named queue gaps."""
    validate_case(case)
    base = Path(base) if base is not None else DS.pipeline_folder(COUNTY, case)
    if collect:
        DC.collect_case_documents(COUNTY, case)
    inventory = DS.pipeline_load(base / 'inventory.json')
    if inventory is None:
        raise ValueError('No existing docket inventory; use --collect to obtain it')
    inventory['source_comparison'] = source_comparison(
        case, inventory, docket_cache or Path(__file__).with_name('dockets.json'))
    with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        count = sum(job['kind'] == 'acquire' for job in queue.jobs(COUNTY, case))
    # The existing worker's default limit is ten. A whole-case pass must cover all jobs.
    DQ.resume_case_documents(COUNTY, case, limit=max(1, count), interpret=False)
    rows = load_rows(base)
    with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        jobs = queue.jobs(COUNTY, case)
    by_ref = {row.get('source_ref'): row for row in rows}
    for job in jobs:
        if job['kind'] != 'acquire' or job['status'] == 'done':
            continue
        row = by_ref.get(job['source_ref'])
        if row is None:
            ref = job['source_ref']
            row = {'source_ref': ref, 'entry_ref': ref.split(':', 2)[1] if ref.startswith('court:') else '',
                   'manifest': {}, 'reading': {'pages': []}}
            rows.append(row)
        row['acquisition_status'] = job['status']
        row['acquisition_gap'] = job.get('error') or 'Document acquisition ' + job['status']
    return inventory, rows


def write_timeline(dossier_path, timeline, markdown):
    path = Path(dossier_path)
    json_path = path.with_name(path.stem + '-timeline.json')
    md_path = path.with_name(path.stem + '-timeline.md')
    DS.pipeline_write(json_path, timeline)
    DS._atomic_write_text(md_path, markdown)
    dossier = DS.pipeline_load(path, {'case': timeline['case'], 'county': COUNTY, 'complete': False})
    dossier['whole_case_timeline'] = {'json': str(json_path), 'markdown': str(md_path),
                                    'status': timeline.get('status'),
                                    'entries': len(timeline.get('entries', [])),
                                    'gaps': len(timeline.get('gaps', []))}
    DS.pipeline_write(path, dossier)
    return {'json': str(json_path), 'markdown': str(md_path)}


def read_amounts(rows, base, budget=None, plan=None):
    """Reuse hash-bound private evidence; paid selection requires an explicit budget.

    Paid reads follow the docket plan (document_prioritizer.timeline_read_order), not the order the
    evidence files sort in on disk, and stop at this case's share of the cap: every document the
    share did not reach is a named `budget_exhausted` gap rather than a silent omission.
    """
    import judgment_money as JM
    import miami_timeline_amounts as amounts
    result = {'figures': [], 'gaps': [], 'evidence_files': [], 'amount_checks': []}
    if budget is not None:
        import document_prioritizer as DP
        selection = DP.timeline_read_order(plan, rows)
        result['selection'] = {'order': [r.get('source_ref') for r in selection['order']],
                               'deferred': selection['deferred']}
        result['gaps'].extend(dict(item, reason='paid_read_not_selected: ' + item['reason'])
                              for item in selection['deferred']
                              if amounts.amount_page_numbers(
                                  next((r.get('reading') or {} for r in rows
                                        if r.get('source_ref') == item['source_ref']), {})))
        rows = selection['order']
    for row in rows:
        if budget is not None and getattr(budget, 'exhausted', False):
            if amounts.amount_page_numbers(row.get('reading') or {}):
                result['gaps'].append({'source_ref': row.get('source_ref'),
                                       'reason': 'budget_exhausted: this case\'s vision share '
                                                 'was spent before this document'})
            continue
        current_hash = ((row.get('manifest') or {}).get('source_sha256') or
                        (row.get('manifest') or {}).get('sha256'))
        key = hashlib.sha256(str(row.get('source_ref')).encode()).hexdigest()
        path = Path(base) / ('amount-vision-' + key + '.json')
        if budget is None:
            detail = DS.pipeline_load(path)
            if (not detail or not current_hash or detail.get('document_hash') != current_hash or
                    detail.get('source_ref') != row.get('source_ref')):
                continue
            detail = dict(detail)
        else:
            if not amounts.amount_page_numbers(row.get('reading') or {}):
                continue
            detail = amounts.assess_amount_pages(row, budget)
        detail['source_ref'] = row.get('source_ref')
        detail['document_key'] = (row.get('manifest') or {}).get('document_key')
        detail['document_hash'] = current_hash
        detail['entry_id'] = row.get('entry_ref')
        if budget is not None:
            DS.pipeline_write(path, detail)
        result['evidence_files'].append(str(path))
        result['gaps'].extend(dict(gap, source_ref=row.get('source_ref')) for gap in detail.get('gaps', []))
        # The one exact-cents check (judgment_money): a figure inside a run of printed rows that
        # reproduces the document's own printed total is 'in_verified_table'. That is arithmetic
        # agreement on one filing, never an award, an open balance or an equity input.
        checks = JM.verify_document(detail.get('figures') or [], detail.get('grand_totals') or [],
                                    {JM._page_no(p) for p in (detail.get('pages') or {})})
        if detail.get('gaps') or detail.get('errors'):
            checks = [dict(c, ok=False, reason='amount pages have unresolved reading gaps')
                      for c in checks]
        in_table = {r['gid'] for c in checks if c['ok'] for r in c['component_rows']}
        result.setdefault('amount_checks', []).extend(
            {'source_ref': row.get('source_ref'), 'entry_id': detail['entry_id'],
             'amount': c['amount'], 'page': c['page'], 'ok': c['ok'], 'reason': c['reason'],
             'pages': c['pages'], 'run': c['run'], 'components': c['components'],
             'credits': c['credits'], 'rates': c['rates'], 'subtotals': c['subtotals']}
            for c in checks)
        result['figures'].extend(dict(figure, source_ref=row.get('source_ref'),
            document_key=detail['document_key'], document_hash=detail['document_hash'],
            entry_id=detail['entry_id'],
            verification_status=('in_verified_table'
                                 if 'p%d:%s' % (JM._page_no(figure.get('page')), figure.get('id'))
                                 in in_table else 'unverified'),
            interpretation='Vision-extracted amount; not an accepted judgment or equity input')
            for figure in detail.get('figures', []))
    return result


def summary_counts(rows, timeline):
    pages = [p for row in rows for p in (row.get('reading') or {}).get('pages', [])]
    return {'entries': len(timeline.get('entries', [])),
            'documents': sum(bool(row.get('manifest', {}).get('pages')) for row in rows),
            'pages': len(pages),
            'ocr_text_pages': sum(p.get('outcome') == 'ocr_text' or
                (p.get('supplemental_ocr') or {}).get('outcome') == 'ocr_text' for p in pages),
            'supplemental_ocr_pages': sum(bool(p.get('supplemental_ocr')) for p in pages),
            'unreadable_source_pages': sum(p.get('outcome') == 'unreadable_source' for p in pages),
            'gaps': len(timeline.get('gaps', [])),
            'pending_types': dict(Counter(p.get('type', 'unknown') for p in timeline.get('pending', [])))}


def recorded_copies(case):
    """Stored Official Records documents for this case, each with the kind its own text reads
    as and its recording date: the pool an unreadable court filing's public copy can come from."""
    import document_classify
    out = []
    for manifest, reading in DS.stored_documents(COUNTY, case):
        ref = str(manifest.get('source_ref') or '')
        if not ref.startswith('official_records/'):
            continue
        out.append({'source_ref': ref, 'reading': reading,
                    'recorded_date': (manifest.get('record_key') or {}).get('rec_date'),
                    'kind': document_classify.classify(reading).get('kind')})
    return out


def case_coverage(case, inventory, rows, timeline):
    import document_coverage
    return document_coverage.coverage(inventory, rows, timeline.get('entries'),
                                      recorded_copies(case), case)


def timeline_case(case, as_of, collect=False, docket_cache=None, shared=None, ledger=None,
                  cap=None):
    """One case's whole-case timeline: acquire, free reading, then (with `shared`, a
    document_case_budget.CaseAllocator) paid amount reads within this case's share.

    The one per-case body behind both this command and `run_documents --backfill --timeline`, so
    the orchestrated run and the standalone run cannot drift apart. -> (timeline, paths)
    """
    import miami_case_timeline
    import miami_timeline_ocr
    import document_prioritizer
    import run_documents
    as_of = as_of.isoformat() if hasattr(as_of, 'isoformat') else str(as_of)
    budget = shared.for_case(case) if shared is not None else None
    inventory, rows = acquire(case, collect=collect,
                              docket_cache=docket_cache or Path(__file__).with_name('dockets.json'))
    base = DS.pipeline_folder(COUNTY, case)
    rows = [miami_timeline_ocr.supplement(row, base / 'timeline-ocr') for row in rows]
    timeline = miami_case_timeline.build_timeline(case, inventory, rows, as_of=as_of)
    timeline['source_comparison'] = inventory.get('source_comparison')
    timeline['coverage'] = case_coverage(case, inventory, rows, timeline)
    # Preserve the free whole-case analysis even if a paid reader fails.
    if ledger is not None:
        timeline['vision_budget'] = budget_snapshot(ledger, cap)
    timeline['counts'] = summary_counts(rows, timeline)
    cached_amounts = read_amounts(rows, base)
    if cached_amounts['evidence_files']:
        timeline['amount_vision'] = cached_amounts
        timeline.setdefault('gaps', []).extend(cached_amounts['gaps'])
        timeline.setdefault('amounts', []).extend(cached_amounts['figures'])
        if cached_amounts['gaps']:
            timeline['coverage_complete'] = False
    write_timeline(run_documents.dossier_path(COUNTY, case), timeline,
                   miami_case_timeline.render_markdown(timeline))
    if budget is not None:
        # Rebuild before replacing cached figures so the paid refresh cannot duplicate them.
        timeline = miami_case_timeline.build_timeline(case, inventory, rows, as_of=as_of)
        timeline['source_comparison'] = inventory.get('source_comparison')
        timeline['coverage'] = case_coverage(case, inventory, rows, timeline)
        try:
            plan = document_prioritizer.prioritize(case, inventory, as_of)
        except ValueError:
            plan = None      # every row is then deferred as not_in_docket_plan
        amounts = read_amounts(rows, base, budget, plan=plan)
        shared.finish(case)
        timeline['amount_vision'] = amounts
        timeline.setdefault('gaps', []).extend(amounts['gaps'])
        timeline.setdefault('amounts', []).extend(amounts['figures'])
        if amounts['gaps']:
            timeline['coverage_complete'] = False
    spending = budget_snapshot(ledger, cap) if ledger is not None else {}
    if budget is not None:
        spending['paid_requests_this_command'] = budget.calls
        spending['case_share'] = shared.report(case)
    timeline['vision_budget'] = spending
    timeline['counts'] = summary_counts(rows, timeline)
    paths = write_timeline(run_documents.dossier_path(COUNTY, case), timeline,
                           miami_case_timeline.render_markdown(timeline))
    print(json.dumps({'case': case, 'status': timeline.get('status'), 'files': paths,
                      'counts': timeline['counts'], 'source_comparison': timeline.get('source_comparison'),
                      'vision_budget': spending}), flush=True)
    return timeline, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', required=True)
    parser.add_argument('--collect', action='store_true', help='Refresh using the existing county collector')
    parser.add_argument('--docket-cache', type=Path, default=Path(__file__).with_name('dockets.json'),
                        help='Compact cache to compare only; never used to build timeline')
    parser.add_argument('--as-of', default=date.today().isoformat(), type=date.fromisoformat)
    parser.add_argument('--vision-max-spend', required=True, type=float,
                        help='Existing shared cumulative cap, at most $1')
    parser.add_argument('--vision', action='store_true', help='Read only amount-bearing pages within shared cap')
    args = parser.parse_args(argv)
    import case_review
    try:
        cases = list(dict.fromkeys(validate_case(case) for case in args.case))
        ledger = case_review.output_path('title_discovery/vision-budget.json')
        spending = budget_snapshot(ledger, args.vision_max_spend)
        if args.vision_max_spend > 1:
            raise ValueError('This run is authorized for at most $1 cumulative vision spend')
        if args.vision and spending['status'] != 'observed':
            raise ValueError('Existing shared vision ledger required; refusing to reset cumulative spend')
    except ValueError as exc:
        parser.error(str(exc))
    from document_backfill import State, PersistentBudget
    from document_case_budget import CaseAllocator
    with (State(ledger) if args.vision else nullcontext()) as state:
        # One share per named case: the first case cannot spend what the others were given.
        shared = (CaseAllocator(PersistentBudget(args.vision_max_spend, state), cases)
                  if args.vision else None)
        for case in cases:
            timeline_case(case, args.as_of, collect=args.collect, docket_cache=args.docket_cache,
                          shared=shared, ledger=ledger, cap=args.vision_max_spend)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
