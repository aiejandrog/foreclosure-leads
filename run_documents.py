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

WHY IT IS STILL OFF BY DEFAULT
OCR now works: on 2026-09-22 the desktop read all five pages of the pilot judgment and got its
$14,698.60 grand total right. It also misread 6,796.61 as 5,796.61 and dropped two line items,
everywhere the clerk's diagonal watermark crosses a figure. The watermark is now stripped from the
render before OCR (document_store.WATERMARK_GRAY_CUTOFF) — and THAT is what is unproven: the grey
cutoff has never run against a real clerk scan.

So the gate stays shut until one desktop run shows the four figures the watermark spoiled reading
correctly and the line items summing to the total. Until then this stage would write dossiers
carrying numbers nobody has checked, which is worse than writing none. It turns on with `--enable`
or DEALFLOW_DOCS=1, and nothing else in the pipeline calls it.

WHAT IT NEVER DOES
No publish, no board write, no lead write, no suppression surface, no captcha spend. Dossiers go
to DEALFLOW_DIR (outside the repo, outside OneDrive) through case_review.output_path. The equity
verdict it reports is equity_state's existing one, computed from the recorded chain exactly as it
always was — reading a document does not move a lead into a FACT state.

THE LINE FOR refresh-dealflow.bat, to paste AFTER the [2b/5] records step:

    echo [2e/5] Reading Miami court documents (off unless DEALFLOW_DOCS=1)...
    python -u run_documents.py --limit 10 >> "%LOG%" 2>&1

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
             budget=None):
    """One case through all seven steps. Returns its dossier."""
    case, owner = entry['case'], entry['owner']
    token = qs_cache.get(owner)
    report, rows = None, []
    inventory = None
    if token:
        import records_liens
        models = records_liens.records_by_qs(token) or []
        try:
            # resume=True: the nightly stage's job is the day's backlog, not re-reading
            # yesterday's documents. The pilot CLI defaults the other way on purpose.
            report = MJ.run(case, models, queue=queue, ocr=ocr, judgments_only=True,
                            keep_images=keep_images, gray_cutoff=gray_cutoff, resume=True)
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
    if interpreter is not None and budget is not None:
        _interpret(rows, interpreter, budget)
    dossier = case_dossier.build(case, COUNTY, inventory=inventory, chain=entry.get('chain'),
                                 documents=rows)
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

    leads = _load(LEADS, [])
    if not leads:
        print('run_documents: %s is missing or empty; nothing to do.' % LEADS)
        return 0
    qs_cache = _load(QS_CACHE, {})
    chains = _load(CHAINS, {})
    picked = pick_cases(leads, chains, args.limit, only=args.case or None)
    if not picked:
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
                               budget=budget)
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
    if written and not read_ok:
        print('  NOTE: no document was read. On Miami scans that means OCR did not run or did '
              'not return text — every dossier section c is honestly empty.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
