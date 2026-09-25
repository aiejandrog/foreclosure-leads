"""Replay paid-read selection and per-case shares over SAVED evidence. No network, no API, $0.

    python -u replay_paid_selection.py --case 2025-023462-CA-01 --case ... --cap 0.75

This is the acceptance run for priority 1 of the Miami automation goal: on the same five cases the
2026-09-23 pilot spent its $0.75 on, show which documents the new selection would pay for first,
which it refuses to pay for and why, and whether each case reaches its current judgment within its
own share of the same cap.

It reads only what is already on disk under DEALFLOW_DIR:
  - recorded documents and their saved page text (document_store.stored_documents)
  - the county index rows for their book/page (records_index.json) for the recording date
  - the full OCS docket inventory the timeline stage saved (document_pipeline/.../inventory.json)
  - the vision sidecars the pilot wrote (vision.json, amount-vision-*.json), for what each page
    actually cost when it was bought
It builds no client, claims no queue job, and writes one report under DEALFLOW_DIR/reports.

A document the pilot never bought has no measured price. Its pages are priced at
ESTIMATED_PAGE_USD and the report marks that figure as an estimate.
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path

import case_review
import document_prioritizer as DP
import document_store as DS

COUNTY = 'MIAMI-DADE'
# Measured 2026-09-22: $0.0675 for a three-page judgment read on the API. An estimate per page.
ESTIMATED_PAGE_USD = 0.0225


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def recorded_rows(case):
    """Stored Official Records documents with their saved reading and what vision cost on them."""
    rows = []
    for manifest, reading in DS.stored_documents(COUNTY, case):
        ref = manifest.get('source_ref') or ''
        if not ref.startswith('official_records/'):
            continue
        stem = (manifest.get('document_key') or '')[:16]
        sidecar = _json(Path(manifest['meta_path']).parent / (stem + '-text') / 'vision.json', {})
        rows.append({'source_ref': ref, 'status': 'stored', 'reading': reading,
                     'doc_type': manifest.get('doc_name') or '', 'path': manifest.get('path'),
                     'pilot_usd': float(sidecar.get('usd') or 0.0),
                     'pilot_read_at': sidecar.get('read_at'),
                     'pilot_pages': sorted(int(p) for p in (sidecar.get('pages') or {}))})
    return rows


def index_records(rows, index_path):
    index = _json(index_path, {}) or {}
    out = []
    for row in rows:
        book_page = row['source_ref'].split('/', 1)[1]
        book, _, page = book_page.partition('-')
        key = '%s/%s' % (book.lstrip('0') or '0', page.lstrip('0') or '0')
        if key in index:
            out.append(dict(index[key], reC_BOOK=book, reC_PAGE=page))
    return out


def find_inventory(case, inventory_dir=None):
    """The saved full OCS inventory: the timeline stage's own file, else one named for the case
    in --inventory-dir (the pilot saved its as five-case-pilot-20260923/<case>-fresh-inventory.json)."""
    inventory = _json(DS.pipeline_folder(COUNTY, case) / 'inventory.json')
    if inventory is None and inventory_dir:
        for path in sorted(Path(inventory_dir).glob('*%s*inventory*.json' % case)):
            inventory = _json(path)
            if inventory is not None:
                break
    return inventory


def docket_plan(case, as_of, inventory_dir=None):
    inventory = find_inventory(case, inventory_dir)
    if inventory is None:
        return None, 'no saved full OCS inventory for this case'
    try:
        return DP.prioritize(case, inventory, as_of), None
    except ValueError as exc:
        return None, str(exc)


def timeline_rows(case):
    """Whole-case timeline documents ('court:<entry>:...') and what their amount pages cost."""
    import hashlib
    import run_case_timeline
    base = DS.pipeline_folder(COUNTY, case)
    rows = []
    for row in run_case_timeline.load_rows(base):
        key = hashlib.sha256(str(row.get('source_ref')).encode()).hexdigest()
        detail = _json(Path(base) / ('amount-vision-' + key + '.json'), {}) or {}
        row['pilot_usd'] = float(detail.get('usd') or 0.0)
        rows.append(row)
    return rows


def page_cost(row):
    if row['pilot_usd'] > 0:
        return row['pilot_usd'], 'measured'
    pages = len([p for p in row['reading'].get('pages') or []
                 if p.get('outcome') in ('text', 'ocr_text')]) or 1
    return min(pages, 2) * ESTIMATED_PAGE_USD, 'estimate'


def replay(cases, cap, as_of, index_path, inventory_dir=None):
    share = cap / len(cases)
    released = 0.0            # what finished cases left unspent, borrowable by later cases
    report = {'as_of': as_of, 'cap_usd': cap, 'share_usd': round(share, 6), 'cases': [],
              'network_requests': 0, 'api_requests': 0}
    for case in cases:
        rows = recorded_rows(case)
        plan, plan_gap = docket_plan(case, as_of, inventory_dir)
        selection = DP.recorded_read_order(case, rows, index_records(rows, index_path), plan)
        allowance = share + released
        spent, would_read, not_reached = 0.0, [], []
        for item in selection['order']:
            usd, basis = page_cost(item['row'])
            if spent + usd > allowance:
                not_reached.append(item['source_ref'])
                continue
            spent += usd
            would_read.append({'source_ref': item['source_ref'], 'tier': item['tier'],
                               'usd': round(usd, 6), 'cost_basis': basis,
                               'docket_judgment_date': item['docket_judgment_date'],
                               'later_orders': item['later_orders']})
        # The whole-case timeline reader, same share: its amount pages in docket-plan order.
        timeline = timeline_rows(case)
        t_order = DP.timeline_read_order(plan, timeline)
        timeline_read = []
        for row in t_order['order']:
            if not row['pilot_usd'] and not any('$' in str(p.get('text') or '') for p in
                                                (row.get('reading') or {}).get('pages') or []):
                continue                    # no amount page: the reader would not buy it
            usd = row['pilot_usd'] or ESTIMATED_PAGE_USD
            if spent + usd > allowance:
                not_reached.append(row.get('source_ref'))
                continue
            spent += usd
            timeline_read.append({'source_ref': row.get('source_ref'), 'usd': round(usd, 6),
                                  'cost_basis': 'measured' if row['pilot_usd'] else 'estimate'})
        released = max(0.0, allowance - spent)
        deferred_refs = {d['source_ref'] for d in selection['deferred']}
        pilot_bought = sorted((r for r in rows if r['pilot_usd'] > 0),
                              key=lambda r: r['pilot_read_at'] or '')
        report['cases'].append({
            'case': case,
            'docket_judgment_dates': selection['docket_judgment_dates'],
            'docket_plan_gap': plan_gap,
            'selected_order': [{k: v for k, v in r.items() if k != 'row'}
                               for r in selection['order']],
            'refused': selection['deferred'],
            'would_read_within_share': would_read,
            'selected_but_share_spent': not_reached,
            'reaches_tier0_or_tier1': any(r['tier'] <= 1 for r in would_read),
            'own_judgment_in_recorded_store': bool(selection['order']),
            'first_court_read': timeline_read[0]['source_ref'] if timeline_read else None,
            'timeline_would_read': timeline_read,
            'timeline_refused': t_order['deferred'],
            'timeline_pilot_usd': round(sum(r['pilot_usd'] for r in timeline), 6),
            'pilot_bought': [{'source_ref': r['source_ref'], 'usd': r['pilot_usd'],
                              'read_at': r['pilot_read_at'],
                              'now_refused': r['source_ref'] in deferred_refs}
                             for r in pilot_bought],
            'pilot_usd_now_refused': round(sum(r['pilot_usd'] for r in pilot_bought
                                               if r['source_ref'] in deferred_refs), 6),
            'replay_usd': round(spent, 6)})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', action='append', required=True)
    parser.add_argument('--cap', type=float, default=0.75,
                        help='the cap to split, for the replay only; nothing is spent')
    parser.add_argument('--as-of', default=date.today().isoformat())
    parser.add_argument('--index', default=str(Path(__file__).with_name('records_index.json')))
    parser.add_argument('--inventory-dir', help='folder holding <case>*inventory*.json when the '
                                                'timeline stage has not saved one')
    args = parser.parse_args(argv)
    cases = list(dict.fromkeys(args.case))
    result = replay(cases, args.cap, args.as_of, args.index, args.inventory_dir)
    # One file per run: two runs on one day used to overwrite each other.
    target = case_review.output_path('reports/paid-selection-replay-%s-%s.json'
                                     % (args.as_of, datetime.now().strftime('%H%M%S')))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    DS._atomic_write_text(str(target), json.dumps(result, indent=2) + '\n')
    print('replay: %d case(s), $%.2f cap split into $%.4f shares; $0 spent, no requests'
          % (len(cases), args.cap, result['share_usd']))
    for row in result['cases']:
        first = (row['would_read_within_share'] or [{}])[0]
        print('  %s  docket judgments %s' % (row['case'], ', '.join(row['docket_judgment_dates'])
                                             or 'none found'))
        print('    first paid recorded read: %s (tier %s)  reaches current-judgment tier: %s'
              % (first.get('source_ref', 'nothing'), first.get('tier', '-'),
                 'YES' if row['reaches_tier0_or_tier1'] else
                 'NO' if row['own_judgment_in_recorded_store'] else
                 'n/a, no recorded copy tied to this case is stored'))
        print('    first paid court read: %s' % (row['first_court_read'] or 'nothing'))
        print('    refused %d; pilot money on now-refused documents $%.4f; replay spend $%.4f'
              % (len(row['refused']), row['pilot_usd_now_refused'], row['replay_usd']))
        if row['docket_plan_gap']:
            print('    GAP docket plan: %s' % row['docket_plan_gap'])
    print('  report: %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
