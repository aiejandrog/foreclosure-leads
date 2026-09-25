"""Re-check every saved judgment and claim amount with the one exact-cents check. $0, no network.

    python -u replay_money_check.py --all
    python -u replay_money_check.py --case 2025-023462-CA-01 --case ...
    python -u replay_money_check.py --all --grep "AMENDED TOTAL"

This is the acceptance run for priority 2 of the Miami automation goal: the pilot's 2024-009959 and
2022-012065 totals were verified by an agent transcribing the figures by hand. Here the same saved
evidence goes through judgment_money with no transcription, and the report shows, per total:

  - whether one run of printed rows ending at it adds up to the cent, and across which pages
  - the rows it counted, the credits it subtracted, the rates it kept out, each subtotal with the
    rows it covers
  - what the retired subset search said about the same figure, so a change of verdict is visible

It reads only what is already on disk under DEALFLOW_DIR:
  - stored documents and their saved page text (document_store.stored_documents)
  - the vision sidecar a paid read left beside a document (vision.json)
  - the whole-case timeline's saved amount reads (document_pipeline/.../amount-vision-*.json)
It builds no client, fetches nothing, claims no queue job, and writes one report under
DEALFLOW_DIR/reports. A total that does not verify is reported with the reason, never retried.
"""
import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

import case_review
import document_store as DS
import judgment_money as JM
import miami_judgment as MJ

COUNTY = 'MIAMI-DADE'


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def stored_cases():
    root = DS.case_dir(COUNTY, 'x').parent
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _text_of(reading):
    return '\n'.join(str(p.get('text') or '') for p in reading.get('pages') or [])


def _brief(check):
    return {k: check.get(k) for k in ('ok', 'reason', 'run', 'pages', 'components', 'credits',
                                      'rates', 'subtotals', 'excluded_before_run',
                                      'disagreeing_subtotals')}


def _candidate_brief(c):
    return {'ok': c.get('sum_check'), 'reason': c.get('sum_check_reason'),
            'run': c.get('sum_check_run'), 'pages': c.get('sum_check_pages'),
            'components': c.get('sum_check_components'), 'credits': c.get('sum_check_credits'),
            'rates': c.get('sum_check_rates'), 'subtotals': c.get('sum_check_subtotals'),
            'disagreeing_subtotals': c.get('sum_check_disagreeing_subtotals')}


def check_document(manifest, reading):
    """-> {'text_totals': [...], 'vision_totals': [...]} for one stored document."""
    out = {'source_ref': manifest.get('source_ref'), 'doc_name': manifest.get('doc_name'),
           'text_totals': [], 'vision_totals': []}
    pool = MJ.document_money(reading)
    for c in MJ.judgment_amount_candidates(reading):
        if c.get('composed'):
            continue
        legacy = MJ.sum_check(pool, c['amount'])
        out['text_totals'].append(dict(amount=c['amount'], page=c['page'], match=c['match'],
                                       text_source=c.get('text_source'),
                                       retired_subset_search=legacy['ok'],
                                       **_candidate_brief(c)))
    stem = (manifest.get('document_key') or '')[:16]
    sidecar = None
    if manifest.get('meta_path'):
        sidecar = _json(Path(manifest['meta_path']).parent / (stem + '-text') / 'vision.json')
    if sidecar:
        pages = {JM._page_no(p) for p in (sidecar.get('pages') or {})}
        for total, check in zip(sidecar.get('grand_totals') or [],
                                JM.verify_document(sidecar.get('figures') or [],
                                                   sidecar.get('grand_totals') or [], pages)):
            if sidecar.get('errors'):
                check = dict(check, ok=False, reason='saved read has errors: %s'
                             % sorted(sidecar['errors']))
            out['vision_totals'].append(dict(amount=total.get('amount'), page=total.get('page'),
                                             read_at=sidecar.get('read_at'), **_brief(check)))
    return out


def check_timeline(case):
    out = []
    base = DS.pipeline_folder(COUNTY, case)
    for path in sorted(Path(base).glob('amount-vision-*.json')):
        detail = _json(path) or {}
        pages = {JM._page_no(p) for p in (detail.get('pages') or {})}
        for total, check in zip(detail.get('grand_totals') or [],
                                JM.verify_document(detail.get('figures') or [],
                                                   detail.get('grand_totals') or [], pages)):
            if detail.get('gaps') or detail.get('errors'):
                check = dict(check, ok=False, reason='saved read has gaps or errors')
            out.append(dict(source_ref=detail.get('source_ref'), entry_id=detail.get('entry_id'),
                            amount=total.get('amount'), page=total.get('page'), **_brief(check)))
    return out


def replay(cases, patterns=()):
    report = {'cases': [], 'network_requests': 0, 'api_requests': 0}
    for case in cases:
        documents = []
        for manifest, reading in DS.stored_documents(COUNTY, case):
            if patterns and not any(re.search(p, _text_of(reading), re.I) for p in patterns):
                continue
            documents.append(check_document(manifest, reading))
        timeline = [] if patterns else check_timeline(case)
        if documents or timeline:
            report['cases'].append({'case': case, 'documents': documents, 'timeline': timeline})
    totals = [t for c in report['cases'] for d in c['documents']
              for t in d['text_totals'] + d['vision_totals']] + \
             [t for c in report['cases'] for t in c['timeline']]
    report['summary'] = {
        'totals': len(totals),
        'verified': sum(1 for t in totals if t['ok']),
        'retired_subset_said_yes_now_no': sum(1 for c in report['cases'] for d in c['documents']
                                              for t in d['text_totals']
                                              if t['retired_subset_search'] and not t['ok']),
        'across_pages': sum(1 for t in totals if t['ok'] and len(t.get('pages') or []) > 1)}
    return report


def _print_disagreeing(t):
    for d in t.get('disagreeing_subtotals') or ():
        if 'rows_above_sum' in d:
            print('      printed subtotal $%s p%s %r: its %d row(s) above add to $%s (off by $%s)' % (
                '{:,.2f}'.format(d['amount']), d['page'], d['label'], d['rows_above'],
                '{:,.2f}'.format(d['rows_above_sum']), '{:,.2f}'.format(d['difference'])))
        else:
            print('      printed subtotal $%s p%s %r: no rows above it' % (
                '{:,.2f}'.format(d['amount']), d['page'], d['label']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--all', action='store_true', help='every Miami case in the store')
    parser.add_argument('--grep', action='append', default=[],
                        help='only documents whose saved text matches (e.g. a party name)')
    args = parser.parse_args(argv)
    cases = list(dict.fromkeys(args.case + (stored_cases() if args.all else [])))
    if not cases:
        parser.error('name --case or --all')
    result = replay(cases, args.grep)
    # One file per run: the --grep run and the --all run on one day used to overwrite each other.
    target = case_review.output_path('reports/money-check-replay-%s-%s.json'
                                     % (date.today(), datetime.now().strftime('%H%M%S')))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    DS._atomic_write_text(str(target), json.dumps(result, indent=2) + '\n')
    s = result['summary']
    print('money check: %d total(s) in %d case(s); %d verify, %d across a page break; $0 spent, '
          'no requests' % (s['totals'], len(result['cases']), s['verified'], s['across_pages']))
    print('  the retired subset search said yes and the exact check says no: %d'
          % s['retired_subset_said_yes_now_no'])
    for case in result['cases']:
        for doc in case['documents']:
            for kind in ('text_totals', 'vision_totals'):
                for t in doc[kind]:
                    print('  %s  %s  $%s p%s  %s  %s' % (
                        case['case'], doc['source_ref'], '{:,.2f}'.format(t['amount'] or 0),
                        t['page'], 'VERIFIED' if t['ok'] else 'not verified',
                        t['run'] if t['ok'] else t['reason']))
                    _print_disagreeing(t)
        for t in case['timeline']:
            print('  %s  %s  $%s p%s  %s  %s' % (
                case['case'], t['source_ref'], '{:,.2f}'.format(t['amount'] or 0), t['page'],
                'VERIFIED' if t['ok'] else 'not verified', t['run'] if t['ok'] else t['reason']))
            _print_disagreeing(t)
    print('  report: %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
