"""run_documents — the nightly document stage: collect, read, classify, dossier. OFF by default.

WHAT IT DOES, IN ORDER, PER CASE
  1. pull the court docket                          -> dossier section a
  2. pull the owner's recorded rows with a CACHED    (free: no browser, no 2Captcha. A case whose
     search token, and shortlist judgment candidates  owner has no cached token is SKIPPED, not
                                                      paid for.)
  3. fetch each candidate, verify its page count
     against the recording index, store it
  4. read every page, OCR the scans
  5. classify each document from its own text       -> dossier section c
  6. join with the cached recorded chain            -> dossier sections b and d
  7. write the dossier under DEALFLOW_DIR

WHY IT IS STILL OFF BY DEFAULT — and what actually changed on 2026-09-22
This paragraph used to say the gate stays shut until the grey cutoff reads the four figures the
watermark spoiled. That condition was MET, by something else, and leaving the old wording here
would have the file state a test that is not the one this stage passed.

What happened: OCR read all five pages of the pilot judgment and got the $14,698.60 grand total
right, but misread 6,796.61 as 5,796.61 and dropped 317.05 and 31.19 — every error where the
clerk's diagonal watermark crosses a figure. Whitening the render above WATERMARK_GRAY_CUTOFF was
then swept at 160, 200, 130 and 220 and produced the SAME four misses at every cutoff. Identical
output at four thresholds means the watermark sits ON those digits, so thresholding cannot
separate them and the cutoff is not the route. The whitening stays because it is cheap and sound;
it is not what made the pilot pass.

document_vision read them. The page image goes to the Claude API, only on a page carrying a total
label and only when OCR's own figures did not add up, and its total goes through the SAME sum
check. Measured that day: $14,698.60, line items reproduce it, $0.0675 per judgment over three
pages (~$0.045 once page selection stopped buying a prose page).

So the gate is shut for a different reason now, and it is a cost reason rather than a correctness
one: with --vision this stage spends real money per judgment, and without it Miami scans stay
partly unread wherever the watermark crosses a figure. Either way every figure read off a scan is
still refused by miami_judgment.judgment_for_analyze — OCR's and vision's alike — until twelve
corroborated cases have been eyeballed against their kept page images. Turn it on with `--enable`
or DEALFLOW_DOCS=1; nothing else in the pipeline calls it.

--walk-cites: FOLLOWING WHAT A DOCUMENT NAMES
Off by default, and the reason it exists is the 2026-09-22 coverage map. Recorded liens are
reached by an OWNER-NAME search, so a mortgage the county recorded against a prior owner, a trust
or a misspelling is never in the result set — and no better filtering finds it, because filtering
only narrows. A read document does name it: a satisfaction recites the mortgage it kills, a
judgment recites the mortgage it forecloses. `--walk-cites` fetches those.

    python -u run_documents.py --limit 10 --walk-cites --walk-depth 2 --walk-budget 12

It costs NO captcha spend on its own: a cited instrument resolves out of `records_index.json`, the
book/page index that fills itself from every recorded search the pipeline already runs. A citation
it cannot address is reported as an unresolved citation with the reason, never dropped. Whether
the clerk endpoint also accepts a direct book/page search is unknown and is `records_probe.py`'s
question; until a probe on a machine with clerk access answers it, the walk uses the index alone
and says so.

`--name-budget N` is the other half and defaults to 0: it searches N of the parties named on the
parcel's own deeds and on the docket, which is the route that reaches a prior owner's open
mortgage. Each name is a Camoufox run or a 2Captcha solve, so a nightly does not spend it by
accident; with the budget at 0 the dossier still lists the names it declined to search.

WHAT IT NEVER DOES
No publish, no board write, no lead write, no suppression surface, and no captcha spend
unless --name-budget is set above 0. Dossiers go
to DEALFLOW_DIR (outside the repo, outside OneDrive) through case_review.output_path. The equity
verdict it reports is equity_state's existing one, computed from the recorded chain exactly as it
always was — reading a document does not move a lead into a FACT state.

THE LINE FOR refresh-dealflow.bat, to paste AFTER the [2b/5] records step:

    echo [2e/5] Reading Miami court documents (off unless DEALFLOW_DOCS=1)...
    python -u run_documents.py --limit 10 --vision --vision-max-spend 1.00 >> "%LOG%" 2>&1

--vision needs ANTHROPIC_API_KEY in the environment the SCHEDULED TASK runs in, which means a
User (or Machine) environment variable on the machine that runs the night, not one typed into a
shell. Without it this stage exits 2 having spent nothing and read nothing; with --vision dropped
it still runs, and Miami scans simply stay unread where the watermark crosses the figures.

Deliberately not added to the .bat here: cmd reads a batch file by byte offset WHILE it runs, so
editing a live publish path mid-flight corrupts the running night. Paste it when nothing is running.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import case_dossier
import case_review
import document_store as DS
import miami_judgment as MJ
from document_queue import DocumentQueue

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
QS_CACHE = os.path.join(HERE, 'records_qs.json')
CHAINS = os.path.join(HERE, 'records_liens.json')
COUNTY = 'MIAMI-DADE'


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def dossier_path(county, case):
    return case_review.output_path(os.path.join('dossiers', DS._slug(county),
                                                DS._slug(case) + '.json'))


LP_LEADS = os.path.join(HERE, 'lp_leads.json')


def _lead_rows(only=None):
    """The lead files a case may be selected from.

    leads_final.json is the AUCTION board and it is a LIVE file: a case that has been killed as a
    lead is dropped from it. On 2026-09-22 `--case 2026-020206-CC-25` — the pilot case, the one
    case in the project with documents already on disk — printed "no Miami case matched" and
    exited 0, because it had moved to deads.json. An explicit --case naming a real case must not
    look identical to a typo.
    """
    rows = _load(LEADS, []) or []
    if not only:
        return rows
    have = {str(r.get('Case #') or '') for r in rows if isinstance(r, dict)}
    if only in have:
        return rows
    # Only when the named case is absent: fold in the lis-pendens board, whose rows use different
    # key names. Same translation records_liens.main does, and for the same reason.
    for row in _load(LP_LEADS, []) or []:
        if not isinstance(row, dict) or str(row.get('case') or '') != only:
            continue
        owner = str(row.get('oname') or '').strip()
        if ',' in owner:                          # "LAST, FIRST" -> "FIRST LAST"; order is
            last, rest = owner.split(',', 1)      # load-bearing, see records_liens.main
            owner = '%s %s' % (rest.strip(), last.strip())
        rows = rows + [{'Case #': only, 'owner_clean': owner or str(row.get('owners') or ''),
                        'Folio': str(row.get('folio') or ''), 'county': 'MIAMI-DADE',
                        '_src': 'lp_leads'}]
        break
    return rows


def pick_cases(leads, chains, limit, only=None):
    """Miami cases with an owner we can search for free. Newest gap first: a case with no dossier
    yet comes before one we have already written."""
    out = []
    for row in leads:
        if not isinstance(row, dict):
            continue
        case = str(row.get('Case #') or '').strip()
        owner = str(row.get('owner_clean') or '').strip()
        if not case or not owner:
            continue
        if only and case != only:
            continue
        county = str(row.get('county') or COUNTY).upper()
        if county.replace(' ', '-') != COUNTY:
            continue
        out.append({'case': case, 'owner': owner,
                    'folio': row.get('Folio') or row.get('year_folio') or '',
                    'chain': (chains or {}).get(case)})
    out.sort(key=lambda r: dossier_path(COUNTY, r['case']).exists())
    return out[:limit] if limit else out


def run_case(entry, qs_cache, queue=None, ocr=None, keep_images=False, interpreter=None,
             gray_cutoff=DS.WATERMARK_GRAY_CUTOFF,
             budget=None, vision_budget=None, walk_depth=0, walk_budget=0, name_budget=0):
    """One case through all seven steps. Returns its dossier."""
    case, owner = entry['case'], entry['owner']
    token = qs_cache.get(owner)
    report, rows = None, []
    inventory = None
    models = []
    walk_report = None
    if token:
        import records_liens
        models = records_liens.records_by_qs(token) or []
        try:
            # resume=True: the nightly stage's job is the day's backlog, not re-reading
            # yesterday's documents. The pilot CLI defaults the other way on purpose.
            report = MJ.run(case, models, queue=queue, ocr=ocr, judgments_only=True,
                            keep_images=keep_images, gray_cutoff=gray_cutoff, resume=True,
                            vision_budget=vision_budget)
            rows = report['documents']
            inventory = report.get('_inventory')
        except Exception as exc:
            # One bad case must not end the night. The dossier records why it is thin.
            rows = [{'source_ref': 'run', 'status': 'gap',
                     'reason': '%s: %s' % (type(exc).__name__, str(exc)[:200])}]
    else:
        rows = [{'source_ref': 'owner_search', 'status': 'skipped',
                 'reason': 'no cached search token for this owner; not minting one in this stage'}]

    case_dossier.classify_documents(rows)
    # THE WALK. It runs after classification because it needs `cited_instruments`, which
    # classify_documents attaches, and before interpretation because a walked document deserves
    # the same reading as one we fetched directly.
    if walk_depth and rows:
        import document_walk
        folio = entry.get('folio') or ''
        subdivision = (entry.get('chain') or {}).get('subdiv') or ''
        plan = document_walk.name_search_plan(
            models, folio, subdivision=subdivision,
            docket=(inventory or {}).get('raw'), owner=owner, limit=name_budget)
        # The searcher is built ONLY when there is a budget to spend, so a run with --name-budget 0
        # cannot launch a browser or reach 2Captcha even by accident.
        name_searcher = None
        if plan['planned']:
            name_searcher = document_walk.NameSearcher(qs_cache=qs_cache)
        try:
            walked, walk_report = document_walk.walk(
                case, rows, models=models, queue=queue, ocr=ocr, county=COUNTY,
                depth=walk_depth, budget=walk_budget or document_walk.DEFAULT_BUDGET,
                gray_cutoff=gray_cutoff, keep_images=keep_images,
                name_plan=plan['planned'], name_searcher=name_searcher,
                folio=folio, subdivision=subdivision)
            rows.extend(walked)
        except Exception as exc:
            # A walk is an extension, never a reason to lose the documents already read. Its
            # failure is recorded where a reader will see it, not swallowed.
            walk_report = {'followed': [], 'unresolved': [],
                           'stopped_because': '%s: %s' % (type(exc).__name__, str(exc)[:200])}
        finally:
            if name_searcher is not None:
                name_searcher.close()
        walk_report['name_search'] = plan
    if interpreter is not None and budget is not None:
        _interpret(rows, interpreter, budget)
    dossier = case_dossier.build(case, COUNTY, inventory=inventory, chain=entry.get('chain'),
                                 documents=rows, walk=walk_report)
    if report:
        MJ.strip_readings(report)
        dossier['judgment_amount_usable'] = report.get('judgment_amount_usable')
        dossier['judgment_amount_usable_with_ocr'] = report.get('judgment_amount_usable_with_ocr')
    return dossier


def _interpret(rows, interpreter, budget):
    """Page-cited extraction over the pages that actually carry text. Budget-capped; a run that
    hits its cap stops interpreting and says so, rather than failing the case."""
    for row in rows:
        reading = row.get('reading')
        if not reading or reading.get('read_status') not in ('read', 'partial'):
            continue
        try:
            row['interpretation'] = interpreter.interpret(reading['pages'], budget)
        except Exception as exc:
            row['interpretation'] = {'findings': [], 'unresolved': [str(exc)[:200]]}
            if type(exc).__name__ == 'BudgetExhausted':
                return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--enable', action='store_true',
                        help='actually run (or set DEALFLOW_DOCS=1). Off by default.')
    parser.add_argument('--limit', type=int, default=10, help='cases this run (default 10)')
    parser.add_argument('--case', default='', help='one case number, ignoring the lead file order')
    parser.add_argument('--no-ocr', action='store_true', help='do not OCR scanned pages')
    parser.add_argument('--keep-images', action='store_true',
                        help='keep the 300-DPI render of each OCR page for a visual check')
    parser.add_argument('--interpret', action='store_true',
                        help='page-cited extraction through document_interpreter (needs --max-spend)')
    parser.add_argument('--max-spend', type=float, default=0.0,
                        help='hard dollar cap for --interpret. No cap, no interpretation.')
    parser.add_argument('--vision', action='store_true',
                        help='read page images through the Claude API when OCR\'s figures do not '
                             'add up (needs --vision-max-spend and ANTHROPIC_API_KEY)')
    parser.add_argument('--vision-max-spend', type=float, default=1.00,
                        help='hard dollar cap for --vision ACROSS THE WHOLE RUN. One measured '
                             'judgment cost $0.0675 on claude-opus-5 (2026-09-22, three pages, '
                             'one of which should not have been sent); the default covers a '
                             'nightly --limit 10 with room to spare.')
    parser.add_argument('--walk-cites', action='store_true',
                        help='follow the book/page references a read document makes, so a lien '
                             'recorded under a name we never searched can still be reached')
    parser.add_argument('--walk-depth', type=int, default=2,
                        help='how many citation hops to follow (default 2)')
    parser.add_argument('--walk-budget', type=int, default=12,
                        help='documents the walk may fetch per case (default 12)')
    parser.add_argument('--name-budget', type=int, default=0,
                        help='extra Official Records NAME searches to RUN per case, over the '
                             'parties on the parcel deeds and the docket. This is the only route '
                             'that widens past the current owner (the book/page search shapes were '
                             'probed on 2026-09-22 and none was confirmed). Each search costs a '
                             'Camoufox run or a 2Captcha solve, so the default is 0 and the plan '
                             'is reported unspent.')
    parser.add_argument('--dry-run', action='store_true', help='list the cases and stop')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    if not (args.enable or os.environ.get('DEALFLOW_DOCS') == '1'):
        # Exit 0 on purpose: a nightly line calling this must be a clean no-op, not a stage
        # failure that turns the whole run red.
        print('run_documents: OFF. Watermark removal before OCR has not been proven against a '
              'real clerk scan yet, so figures read off one could be wrong and unflagged.')
        print('  Turn it on with --enable, or DEALFLOW_DOCS=1 in the environment.')
        return 0

    # Validate arguments BEFORE touching data: --interpret with no cap is an error whether or
    # not there happen to be leads to run it over.
    interpreter = budget = None
    if args.interpret:
        if args.max_spend <= 0:
            parser.exit(2, '--interpret needs --max-spend; there is no uncapped mode\n')
        import document_interpreter
        interpreter = document_interpreter.build()
        budget = document_interpreter.Budget(args.max_spend)
    vision_budget = None
    if args.vision:
        # Same rule as --interpret, and for the same reason: validated here, before a single
        # record is loaded, so a machine that cannot pay finds out before it does the work.
        if args.vision_max_spend <= 0:
            parser.exit(2, '--vision needs a positive --vision-max-spend\n')
        import document_interpreter
        import document_vision
        vision_budget = document_interpreter.Budget(args.vision_max_spend)
        try:
            document_vision.VisionReader().client()
        except document_interpreter.NotConfigured as gap:
            parser.exit(2, '--vision cannot run here: %s\n' % gap)

    leads = _lead_rows(only=args.case or None)
    if not leads:
        print('run_documents: %s is missing or empty; nothing to do.' % LEADS)
        # Same rule as an unmatched --case below: a named case is a request, and a request that
        # could not even be looked up does not exit 0.
        return 3 if args.case else 0
    qs_cache = _load(QS_CACHE, {})
    chains = _load(CHAINS, {})
    picked = pick_cases(leads, chains, args.limit, only=args.case or None)
    if not picked:
        if args.case:
            # Exit NON-ZERO and say which file was searched. A named case that matches nothing is
            # a failed request, not a clean no-op, and exiting 0 with one vague line is how the
            # pilot case looked identical to a typo for an afternoon.
            print('run_documents: %s is not in leads_final.json or lp_leads.json. Those are LIVE '
                  'files; a case killed as a lead is dropped from them, so --case cannot reach '
                  'it here.' % args.case)
            print('  For an off-list case whose documents you already have, use the pilot CLI, '
                  'which takes the recorded rows directly:')
            print('    python -u miami_judgment.py %s --records or_rows.json --keep-images'
                  % args.case)
            print('  then follow its citations with:')
            print('    python -u document_walk.py --case %s --depth 2 --budget 12' % args.case)
            return 3
        print('run_documents: no Miami case matched.')
        return 0
    print('run_documents: %d case(s); %d have a cached search token'
          % (len(picked), sum(1 for p in picked if p['owner'] in qs_cache)))
    if args.dry_run:
        for entry in picked:
            print('  %s  %s  token=%s  chain=%s'
                  % (entry['case'], entry['owner'][:28],
                     'yes' if entry['owner'] in qs_cache else 'NO',
                     'yes' if entry['chain'] else 'no'))
        return 0

    ocr = None if args.no_ocr else DS.winocr
    queue = DocumentQueue()
    written = read_ok = 0
    try:
        for entry in picked:
            dossier = run_case(entry, qs_cache, queue=queue, ocr=ocr,
                               keep_images=args.keep_images, interpreter=interpreter,
                               budget=budget, vision_budget=vision_budget,
                               walk_depth=args.walk_depth if args.walk_cites else 0,
                               walk_budget=args.walk_budget,
                               name_budget=args.name_budget if args.walk_cites else 0)
            target = dossier_path(COUNTY, entry['case'])
            target.parent.mkdir(parents=True, exist_ok=True)
            DS._atomic_write_text(str(target), json.dumps(dossier, indent=2) + '\n')
            written += 1
            read_ok += dossier['c_documents'].get('fully_read') or 0
            print('  %s  %s' % (entry['case'], dossier['conclusion']))
    finally:
        queue.close()
    print('run_documents: %d dossier(s) written, %d document(s) actually read.'
          % (written, read_ok))
    if budget:
        print('  interpretation spend: $%.4f of $%.2f' % (budget.spent, budget.limit))
    if vision_budget:
        print('  second-reader spend: $%.4f of $%.2f' % (vision_budget.spent,
                                                         vision_budget.limit))
    if written and not read_ok:
        print('  NOTE: no document was read. On Miami scans that means OCR did not run or did '
              'not return text — every dossier section c is honestly empty.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
