"""measure_pr68.py - what PR #68 actually places, offline, $0.

Run on the laptop from the repo folder, on the PR #68 branch:

    git fetch origin claude/project-thread-ld0j2w
    git checkout claude/project-thread-ld0j2w
    python measure_pr68.py

Reads only the saved title_discovery reports already on this machine. No county
request, no reading, no spending, no writes. Prints counts only, no names or
addresses, so the output is safe to paste back.
"""
import json, re, sys
import case_review
import document_walk as W
import miami_title_discovery as TD
import miami_title_parties as TP


def evidence_for(report):
    """The same (rows, models) pair refresh_saved_report() feeds the matcher."""
    rows, seeds = TD.stored_evidence(report['case'])
    models = list(seeds or [])
    known = {W.key_of(m.get('reC_BOOK'), m.get('reC_PAGE')) for m in models}
    for records in (report.get('private_search_results') or {}).values():
        for record in records or []:
            key = W.key_of(record.get('reC_BOOK'), record.get('reC_PAGE'))
            if key not in known:
                known.add(key)
                models.append(record)
    return list(rows or []), models


def main():
    folder = case_review.output_path('title_discovery')
    # Only files named for a Miami case are investigations. The same folder holds
    # records_qs.json, sunbiz-cache.json, the budget snapshots and the nightly status,
    # and counting those as unreadable reports would bury the numbers this measures.
    paths = sorted(p for p in folder.glob('*.json')
                   if re.fullmatch(r'20\d{2}-\d{6}-(?:CA|CC)-\d{2}', p.stem))
    if not paths:
        print('No saved title_discovery reports in %s' % folder)
        return 1
    n = {'reports': 0, 'unreadable': 0, 'matcher_crashed': 0, 'owner_before': 0, 'owner_after': 0,
         'owner_gained': 0, 'owner_changed': 0, 'owner_lost': 0,
         'deeds_placed': 0, 'cases_with_a_placed_deed': 0,
         'needs_person': 0, 'differs': 0, 'still_unplaced': 0,
         'possibly_conveyed_later': 0}
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            old = report.get('title_parties') or {}
            docket = {'parties': [{'partyName': p['name'], 'partyTypeDesc': 'DEFENDANT'}
                                  for p in old.get('defendants', [])]}
            rows, models = evidence_for(report)
        except Exception as exc:              # an unreadable saved report must not stop the count
            n['unreadable'] += 1
            print('SKIP %s: %s: %s' % (path.name, type(exc).__name__, exc), file=sys.stderr)
            continue
        try:
            new = TP.build_title_parties(models, rows, docket, report['folio'])
        except Exception as exc:              # the matcher under measurement crashed: say so loudly
            n['matcher_crashed'] += 1
            print('MATCHER CRASH %s: %s: %s' % (path.name, type(exc).__name__, exc), file=sys.stderr)
            continue
        n['reports'] += 1
        before = (old.get('current_deed_candidate') or {}).get('book_page')
        after = (new.get('current_deed_candidate') or {}).get('book_page')
        n['owner_before'] += bool(before)
        n['owner_after'] += bool(after)
        if after and not before:
            n['owner_gained'] += 1
        elif before and not after:
            n['owner_lost'] += 1
        elif before and after and before != after:
            n['owner_changed'] += 1
        placed = new.get('legal_matched_deeds') or []
        n['deeds_placed'] += len(placed)
        n['cases_with_a_placed_deed'] += bool(placed)
        if new.get('current_deed_status') == 'possibly_conveyed_later':
            n['possibly_conveyed_later'] += 1
        for deed in new.get('unanchored_deeds') or []:
            status = deed.get('status')
            if status == 'legal_description_match_required':
                n['needs_person'] += 1
            elif status == 'legal_description_differs':
                n['differs'] += 1
            else:
                n['still_unplaced'] += 1
    print(json.dumps(n, indent=2, sort_keys=True))
    if n['matcher_crashed']:
        print('\nmatcher_crashed is not zero: the matcher raised on a real saved report. '
              'Do not merge on these numbers; send the MATCHER CRASH lines back.')
    print('\nowner_changed and owner_lost are the ones to look at: #68 changes who the board '
          'reports as owner only in those cases. owner_gained is the win.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
