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

`--token-budget N` unblocks asking for a case by name at all, and also defaults to 0. Every step
above needs the owner's cached Official Records token, and `gen_records_qs.py` fills that cache
40 owners a night in `leads_final.json` file order. So an owner low in that order simply has no
token yet, and the stage skipped the case rather than spending ~$0.003 uninvited. That is a
throughput limit, not a case-type one: nothing in the filler looks at whether a case is -CA- or
-CC-, and `records_liens._lien_kind` reads -CC- as HOA deliberately.

WHAT IT NEVER DOES
No publish, no board write, no lead write, no suppression surface, and no captcha spend
unless --name-budget or --token-budget is set above 0. Dossiers go
to DEALFLOW_DIR (outside the repo, outside OneDrive) through case_review.output_path. The equity
verdict it reports is equity_state's existing one, computed from the recorded chain exactly as it
always was — reading a document does not move a lead into a FACT state.

THE NIGHTLY LINE is refresh-dealflow.bat's [2e/5] stage, after the [2b/5] records step:

    if "%DEALFLOW_DOCS%"=="1" python -u run_documents.py --limit 25 --vision --vision-max-spend 1.00 --token-budget 0 --max-minutes 20 >> "%LOG%" 2>&1

It does nothing until DEALFLOW_DOCS=1 is set; setting it is the decision to spend up to $1.00 a
night on vision reads. --token-budget stays 0 (no paid owner-search tokens). #53 now routes token
minting through PaidCutoffSolver, so raising it is possible, but it is a separate one-line change
that must add --captcha-max-spend and needs the owner's go on the spend. --max-minutes 20 starts
no new case after twenty minutes, so a slow clerk cannot hold the board rebuild behind it. --limit 25 with oldest-dossier-first ordering cycles every
live Miami lead. Each night also writes dossiers/MIAMI-DADE/_nightly.json: cases, skipped for no
token, read, judgments found, judgments SATISFIED, and the commonest open gaps.

--vision needs an Anthropic API key the SCHEDULED TASK can see: put it in anthropic.key beside the
code (gitignored by *.key, read like captcha.key). Do NOT set a User or Machine ANTHROPIC_API_KEY:
that makes every Claude Code session on the machine bill the API instead of the subscription. An
ANTHROPIC_API_KEY already in the environment still wins, as TWOCAPTCHA_KEY does over captcha.key.
Without either this stage exits 2 having spent nothing and read nothing; with --vision dropped it
still runs, and Miami scans simply stay unread where the watermark crosses the figures.

Never pull a change to that .bat while a refresh is running: cmd reads a batch file by byte offset
WHILE it runs, so changing a live publish path mid-flight corrupts the running night.
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import case_dossier
import case_review
import document_store as DS
import miami_judgment as MJ
import paths as P
from document_queue import DocumentQueue

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
QS_CACHE = P.records_qs()
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
    # No dossier first, then the OLDEST dossier. Sorting on existence alone meant that once every
    # case had one, the same file-order head was re-read every night and a case low in the file
    # was never revisited — and a revisit is how a satisfaction recorded after the judgment is
    # ever seen.
    def _age(r):
        path = dossier_path(COUNTY, r['case'])
        try:
            return (1, path.stat().st_mtime)
        except OSError:
            return (0, 0.0)
    out.sort(key=_age)
    return out[:limit] if limit else out


def summarize(dossiers):
    """One line per case, and the counts a person reads first. Written beside the dossiers so the
    night's coverage is a file, not a scrollback."""
    rows, gaps = [], {}
    for d in dossiers:
        c = d.get('c_documents') or {}
        j = c.get('judgment') or {}
        skipped = [r for r in (c.get('documents') or []) if r.get('status') == 'skipped']
        rows.append({'case': d.get('case'),
                     'skipped': skipped[0].get('reason') if skipped else None,
                     'fully_read': c.get('fully_read') or 0,
                     'judgment_found': bool(j.get('operative') or j.get('candidates')),
                     'judgment_satisfied': bool(j.get('satisfied_by')),
                     'open_gaps': len(d.get('open_gaps') or [])})
        for g in d.get('open_gaps') or []:
            key = str(g).split(':', 1)[-1].strip()[:80]
            gaps[key] = gaps.get(key, 0) + 1
    return {'written_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'county': COUNTY, 'cases': len(rows),
            'skipped_no_token': sum(1 for r in rows if r['skipped']),
            'with_a_document_read': sum(1 for r in rows if r['fully_read']),
            'judgment_found': sum(1 for r in rows if r['judgment_found']),
            'judgment_satisfied': sum(1 for r in rows if r['judgment_satisfied']),
            'complete': sum(1 for r in rows if not r['open_gaps']),
            'top_gaps': sorted(gaps.items(), key=lambda kv: -kv[1])[:10],
            'per_case': rows}


def mint_token(owner, qs_cache, ladder=None):
    """Mint one Official Records search token for `owner` and cache it. Returns (token, reason).

    `ladder` is run_owner_tokens.TokenLadder over a captcha_cost_cutoff.PaidCutoffSolver: cached
    token, then the free browser session, and only then ONE paid solve, under the 300-submission
    ceiling and the real-balance cutoff. main() always supplies it when --token-budget is above 0.
    Until 2026-09-23 this called gen_records_qs.mint_qs with the default solver and three tries,
    so the nightly's paid captcha path had no balance check and retried a solve whose charge was
    unknown. A CutoffStopped from the ladder is NOT swallowed here: it ends paid minting for the
    whole run, which is run_case's job to record.

    WHY THIS IS A FLAG AND NOT THE DEFAULT
    `gen_records_qs.py` fills `records_qs.json` nightly, 40 owners a run under an 8-minute
    deadline (`refresh-dealflow.bat:182`), walking `leads_final.json` in file order and skipping
    anyone already cached. Nothing in it filters by case type — `records_liens._lien_kind` reads
    `-CC-` as HOA on purpose — so which owners have a token is decided by POSITION and budget,
    not by what kind of case it is. An owner low in that order may never be reached, and asking
    for that case by name is exactly when you want to pay the ~$0.003 rather than wait a week.

    It stays off by default because it is the only place in this stage that spends money without
    being asked, and a nightly that silently minted a token per uncached owner would turn a
    350-lead backlog into a bill.

    The >100-record rule is gen_records_qs's and is kept here deliberately: an owner returning
    more than MAX_HITS is a common-name over-match, and caching that token would point every
    later run at a stranger's recorded documents.
    """
    import gen_records_qs as G
    import records_liens as R
    split = R.split_owner((owner or '').strip())
    if not split:
        return None, 'no cached token, and the owner name could not be split for a search'
    from captcha_cost_cutoff import CutoffStopped
    try:
        if ladder is not None:
            token, hits = ladder.search_token(owner, split)
        else:
            token, hits = G.mint_qs(split, tries=1)
    except CutoffStopped:
        raise
    except Exception as exc:
        return None, 'token mint failed: %s: %s' % (type(exc).__name__, str(exc)[:140])
    if not token:
        return None, 'token mint returned nothing (no captcha solve, or the clerk refused)'
    if hits <= 0:
        return None, 'token minted but the county has no records under this name'
    if hits > G.MAX_HITS:
        return None, ('token minted but the name matched %d records (over %d), which is a '
                      'common-name over-match, so it was not cached' % (hits, G.MAX_HITS))
    qs_cache[owner] = token
    try:
        with open(QS_CACHE, 'w', encoding='utf-8') as fh:
            json.dump(qs_cache, fh, indent=1, sort_keys=True)
    except OSError as exc:
        # The token still works for THIS run; it just will not be free next time.
        return token, 'token minted but not cached (%s)' % exc
    return token, ''


def run_case(entry, qs_cache, queue=None, ocr=None, keep_images=False, interpreter=None,
             gray_cutoff=DS.WATERMARK_GRAY_CUTOFF,
             budget=None, vision_budget=None, walk_depth=0, walk_budget=0, name_budget=0,
             token_budget=None, resume=True, reuse_done=False):
    """One case through all seven steps. Returns its dossier."""
    case, owner = entry['case'], entry['owner']
    token = qs_cache.get(owner)
    minted = None
    if not token and token_budget is not None and token_budget.get('left', 0) > 0:
        from captcha_cost_cutoff import CutoffStopped
        try:
            token, minted = mint_token(owner, qs_cache, ladder=token_budget.get('ladder'))
            token_budget['left'] -= 1
        except CutoffStopped as stop:
            # The cutoff, the 300 ceiling or an unsettled charge: no further paid minting this run.
            token, minted = None, 'captcha cutoff stopped paid minting for this run: %s' % stop
            token_budget['left'] = 0
            token_budget['stopped'] = str(stop)
        token_budget['spent'] = token_budget.get('spent', 0) + 1
    report, rows = None, []
    inventory = None
    models = []
    walk_report = None
    # This case's share of the vision cap. With a CaseAllocator no case can spend a share another
    # still-pending case has not used (document_case_budget); a plain Budget is passed through.
    case_budget = (vision_budget.for_case(case) if hasattr(vision_budget, 'for_case')
                   else vision_budget)
    if token:
        import records_liens
        models = records_liens.records_by_qs(token) or []
        try:
            # Nightly runs keep done jobs; explicit --case runs deliberately re-read.
            report = MJ.run(case, models, queue=queue, ocr=ocr, judgments_only=True,
                            keep_images=keep_images, gray_cutoff=gray_cutoff, resume=resume,
                            vision_budget=case_budget, reuse_done=reuse_done)
            rows = report['documents']
            inventory = report.get('_inventory')
        except Exception as exc:
            # One bad case must not end the night. The dossier records why it is thin.
            rows = [{'source_ref': 'run', 'status': 'gap',
                     'reason': '%s: %s' % (type(exc).__name__, str(exc)[:200])}]
    else:
        rows = [{'source_ref': 'owner_search', 'status': 'skipped',
                 'reason': minted or ('no cached search token for this owner, and --token-budget '
                                      'is 0 so this stage did not mint one')}]

    case_dossier.classify_documents(rows, case=case)
    # THE WALK. It runs after classification because it needs `cited_instruments`, which
    # classify_documents attaches, and before interpretation because a walked document deserves
    # the same reading as one we fetched directly.
    if walk_depth and rows:
        import document_walk
        folio = entry.get('folio') or ''
        # Anchor the parcel from the records themselves when no chain was traced. Without this
        # the stage inherits an empty subdivision, and the whole deed half of the candidate list
        # silently matches nothing — which is what produced one candidate and 0 on-parcel hits
        # out of 500 records on 2024-014334-CA-01.
        subdivision = ((entry.get('chain') or {}).get('subdiv')
                       or document_walk.anchor_of(models, folio)['subdivision'])
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
    if hasattr(vision_budget, 'finish'):
        # Paid reading for this case is over: what it left unspent is now borrowable by the cases
        # still pending, and not before.
        vision_budget.finish(case)
    if interpreter is not None and budget is not None:
        _interpret(rows, interpreter, budget)
    dossier = case_dossier.build(case, COUNTY, inventory=inventory, chain=entry.get('chain'),
                                 documents=rows, walk=walk_report)
    if report:
        # The nightly is where corroborated scan figures will actually come from, and until
        # 2026-09-23 only the pilot CLI logged them, so the review count toward
        # MJ.REVIEW_THRESHOLD could never move on its own. Logging is a local write; a failure to
        # log never costs the case.
        try:
            MJ.log_corroboration(report)
        except Exception as exc:
            print('    review log not written: %s: %s' % (type(exc).__name__, str(exc)[:120]))
        MJ.strip_readings(report)
        dossier['judgment_amount_usable'] = report.get('judgment_amount_usable')
        dossier['judgment_amount_usable_with_ocr'] = report.get('judgment_amount_usable_with_ocr')
    return dossier


def _print_case(dossier):
    """The per-case detail, on the console, where a person running this can see it.

    The 2026-09-22 run printed one conclusion line and nothing else: no documents, no citations,
    no name searches, no amounts. Every fact in that run had to be dug out of the dossier JSON
    afterwards, including the one that mattered — that the document read belonged to a different
    lawsuit. A stage whose output is only legible by reading its output file is a stage nobody
    checks.
    """
    c = dossier.get('c_documents') or {}
    for row in c.get('documents') or []:
        bits = ['%s  %s' % (row.get('source_ref'), row.get('is') or 'unknown')]
        if row.get('confidence'):
            bits.append('confidence %s' % row['confidence'])
        if row.get('read_status') and row['read_status'] != 'read':
            bits.append(row['read_status'])
        print('    DOC %s' % ', '.join(bits))
        for amount in row.get('amounts') or []:
            print('      $%s (page %s, %s, %s)'
                  % ('{:,.2f}'.format(amount['amount']), amount.get('page'),
                     amount.get('text_source') or 'embedded',
                     'composed from parts, NOT a stated total' if amount.get('composed')
                     else ('line items check out' if amount.get('sum_check')
                           else 'line items do not add up')))
        second = row.get('second_reader')
        if second:
            print('      second reader: %d figure(s) transcribed -> %s'
                  % (len(second['figures']), second.get('saved_to') or 'not saved'))
    judgment = c.get('judgment') or {}
    if judgment.get('certain'):
        print('    JUDGMENT %s%s' % (judgment['operative'],
                                     (' $' + '{:,.2f}'.format(judgment['amount']))
                                     if judgment.get('amount') else ''))
    elif judgment.get('candidates'):
        print('    JUDGMENT unsettled: %d candidates (%s)'
              % (len(judgment['candidates']), ', '.join(str(r) for r in judgment['candidates'])))
    for row in c.get('other_actions') or []:
        print('    NOT THIS CASE  %s belongs to %s'
              % (row['source_ref'], ', '.join(row['belongs_to']) or '?'))
    for cite in c.get('cited_but_not_fetched') or []:
        print('    CITES %s/%s (page %s) not fetched'
              % (cite.get('book'), cite.get('page_no'), cite.get('cited_on_page')))
    walk = c.get('walk') or {}
    if walk:
        print('    walk: %d document(s) fetched, %d citation(s) unresolved%s'
              % (walk.get('documents_fetched') or 0, len(walk.get('unresolved') or []),
                 ('; stopped: ' + walk['stopped_because']) if walk.get('stopped_because') else ''))
    plan = walk.get('name_search') or {}
    if plan:
        anchor = plan.get('anchor') or {}
        if not anchor.get('anchored'):
            print('    NOT ANCHORED to the parcel, so every on-parcel count below is 0 by '
                  'construction, not by finding')
        print('    names: %d candidate(s), %d searched, %d unsearched'
              % (len(plan.get('candidates') or []), len(plan.get('planned') or []),
                 plan.get('skipped') or 0))
    for row in (walk.get('names') or {}).get('searched') or []:
        print('      %s -> %s (%s record(s), %s on this parcel)'
              % (row.get('name'), row.get('outcome'), row.get('records'), row.get('on_parcel')))
    for gap in dossier.get('open_gaps') or []:
        print('    GAP %s' % gap)


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
    parser.add_argument('--case', default='', help='one case number; re-read even completed documents')
    parser.add_argument('--backfill', action='store_true',
                        help='resumable pass over EVERY Miami lead; requires explicit vision cap')
    parser.add_argument('--leads-file', help='backfill input snapshot (default leads_final.json)')
    parser.add_argument('--timeline', action='store_true',
                        help='backfill: after each case\'s documents, build its whole-case docket '
                             'timeline (run_case_timeline) in the same checkpoint and the same '
                             'per-case vision share')
    parser.add_argument('--collect-dockets', action='store_true',
                        help='backfill --timeline: refresh each case\'s full OCS docket first '
                             '(free; without it a case with no saved docket is a named gap)')
    parser.add_argument('--retry-gaps', action='store_true',
                        help='backfill: retry finished attempts with outstanding gaps')
    parser.add_argument('--no-ocr', action='store_true', help='do not OCR scanned pages')
    parser.add_argument('--keep-images', action='store_true',
                        help='keep the 300-DPI render of each OCR page for a visual check')
    parser.add_argument('--interpret', action='store_true',
                        help='page-cited extraction through document_interpreter (needs --max-spend)')
    parser.add_argument('--max-spend', type=float, default=0.0,
                        help='hard dollar cap for --interpret. No cap, no interpretation.')
    parser.add_argument('--vision', action='store_true',
                        help='read page images through the Claude API when OCR\'s figures do not '
                             'add up (needs --vision-max-spend and anthropic.key)')
    parser.add_argument('--vision-max-spend', type=float, default=None,
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
    parser.add_argument('--token-budget', type=int, default=0,
                        help='Official Records search tokens this run may MINT for owners who '
                             'have none, at ~$0.003 each. Default 0. The nightly filler walks '
                             'leads_final.json in file order, 40 owners a run, so an owner low '
                             'in that order has no token yet — which is what blocks asking for '
                             'a named case on demand. Nothing here filters by case type.')
    parser.add_argument('--max-minutes', type=float, default=0,
                        help='start no new case after this many minutes (0 = no limit). The '
                             'nightly line sets it so a slow clerk cannot push the board rebuild '
                             'and publish that run after this stage back by hours')
    parser.add_argument('--captcha-max-spend', type=float, default=None,
                        help='required with --token-budget: the real-balance captcha cutoff in '
                             'dollars, at most 1.50, enforced by captcha_cost_cutoff against the '
                             'account balance. It is cumulative on its ledger and cannot be raised.')
    parser.add_argument('--captcha-state', default='captcha/run_documents-captcha.json',
                        help='captcha ledger under DEALFLOW_DIR (default %(default)s)')
    parser.add_argument('--dry-run', action='store_true', help='list the cases and stop')
    args = parser.parse_args(argv)
    if args.backfill:
        if (args.vision_max_spend is None or not math.isfinite(args.vision_max_spend)
                or args.vision_max_spend <= 0):
            parser.error('--backfill requires an explicit finite positive --vision-max-spend')
        if args.case or args.interpret or args.token_budget or args.name_budget:
            parser.error('--backfill excludes --case, --interpret and paid token/name searches')
        import document_backfill
        try:
            return document_backfill.run(args, sys.modules[__name__])
        except (OSError, ValueError, RuntimeError) as exc:
            parser.exit(2, 'backfill stopped: %s\n' % type(exc).__name__)
    if args.leads_file or args.retry_gaps or args.timeline or args.collect_dockets:
        parser.error('--leads-file, --retry-gaps, --timeline and --collect-dockets require --backfill')
    if args.vision_max_spend is None:
        args.vision_max_spend = 1.00  # Preserve the existing nightly default.
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
        interpreter = document_interpreter.build('api')
        budget = document_interpreter.Budget(args.max_spend)
    vision_budget = None
    if args.vision:
        # Same rule as --interpret, and for the same reason: validated here, before a single
        # record is loaded, so a machine that cannot pay finds out before it does the work.
        if args.vision_max_spend <= 0:
            parser.exit(2, '--vision needs a positive --vision-max-spend\n')
        import document_interpreter
        import document_vision
        # Split into per-case shares below, once the roster is known.
        vision_budget = document_interpreter.Budget(args.vision_max_spend)
        try:
            document_vision.VisionReader().client()
        except document_interpreter.NotConfigured as gap:
            parser.exit(2, '--vision cannot run here: %s\n' % gap)

    # PREFLIGHT, before any data loads. On 2026-09-22 the desktop batch ran from a fresh worktree:
    # captcha.key is gitignored, so it was not there, and the first three cases each burned a mint
    # attempt before anyone noticed. A missing input that makes a whole night useless is an exit
    # here, not a per-case reason in 349 dossiers.
    if args.token_budget > 0:          # gen_records_qs.mint_qs solves through 2Captcha only
        cap = args.captcha_max_spend
        if cap is None or not math.isfinite(cap) or not 0 < cap <= 1.50:
            parser.exit(2, '--token-budget needs --captcha-max-spend (dollars, above 0, at most '
                           '1.50). Paid captcha runs only under the real-balance cutoff.\n')
        import captcha_solver
        if not captcha_solver.has_key():
            parser.exit(2, '--token-budget needs a 2Captcha key (captcha.key in '
                           'this folder, or TWOCAPTCHA_KEY / CAPTCHA_KEY). A fresh worktree does '
                           'not have it: it is gitignored. Copy it in, or run with budgets at 0.\n')
    if not os.path.exists(QS_CACHE) and not args.token_budget:
        print('run_documents: WARNING %s is missing and --token-budget is 0, so every case will be '
              'skipped for no search token. Copy it in from the machine that has one.' % QS_CACHE)
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

    if vision_budget is not None:
        from document_backfill import PersistentBudget
        from document_case_budget import CaseAllocator, MemoryState
        vision_budget = CaseAllocator(PersistentBudget(args.vision_max_spend, MemoryState()),
                                      [entry['case'] for entry in picked])
    ocr = None if args.no_ocr else DS.winocr
    token_budget = {'left': args.token_budget, 'spent': 0} if args.token_budget else None
    captcha_state = solver = None
    if token_budget:
        import captcha_solver
        from captcha_cost_cutoff import PaidCutoffSolver
        from document_backfill import State
        from run_owner_tokens import TokenLadder
        ledger = case_review.output_path(args.captcha_state)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        captcha_state = State(ledger).__enter__()
        try:
            solver = PaidCutoffSolver(captcha_state, args.captcha_max_spend, captcha_solver._key())
        except ValueError as exc:
            captcha_state.__exit__(None, None, None)
            parser.exit(2, 'captcha cutoff refused: %s\n' % exc)
        token_budget['ladder'] = TokenLadder(qs_cache, solver)
    queue = DocumentQueue()
    written = read_ok = 0
    dossiers = []
    started = time.monotonic()
    try:
        for entry in picked:
            if args.max_minutes and time.monotonic() - started > args.max_minutes * 60:
                print('run_documents: --max-minutes %g reached; %d case(s) left for the next run.'
                      % (args.max_minutes, len(picked) - written))
                break
            dossier = run_case(entry, qs_cache, queue=queue, ocr=ocr,
                               keep_images=args.keep_images, interpreter=interpreter,
                               budget=budget, vision_budget=vision_budget,
                               walk_depth=args.walk_depth if args.walk_cites else 0,
                               walk_budget=args.walk_budget,
                               name_budget=args.name_budget if args.walk_cites else 0,
                               token_budget=token_budget, resume=not bool(args.case))
            target = dossier_path(COUNTY, entry['case'])
            target.parent.mkdir(parents=True, exist_ok=True)
            DS._atomic_write_text(str(target), json.dumps(dossier, indent=2) + '\n')
            written += 1
            dossiers.append(dossier)
            read_ok += dossier['c_documents'].get('fully_read') or 0
            print('  %s  %s' % (entry['case'], dossier['conclusion']))
            _print_case(dossier)
    finally:
        queue.close()
        if captcha_state is not None:
            from captcha_cost_cutoff import CutoffStopped
            token_budget['ladder'].close()
            try:
                token_budget['captcha_balance'] = solver.finish()
            except CutoffStopped as stop:
                token_budget['captcha_balance'] = {'balance_check_failed': True,
                                                   'reason': str(stop)}
            captcha_state.__exit__(None, None, None)
    print('run_documents: %d dossier(s) written, %d document(s) actually read.'
          % (written, read_ok))
    if dossiers:
        summary = summarize(dossiers)
        target = dossier_path(COUNTY, '_nightly')
        target.parent.mkdir(parents=True, exist_ok=True)
        DS._atomic_write_text(str(target), json.dumps(summary, indent=2) + '\n')
        print('  coverage: %(cases)d case(s), %(skipped_no_token)d skipped for no search token, '
              '%(with_a_document_read)d with a document read, %(judgment_found)d judgment(s) '
              'found, %(judgment_satisfied)d satisfied, %(complete)d with no open gap' % summary)
        print('  summary: %s' % target)
    if budget:
        print('  interpretation spend: $%.4f of $%.2f' % (budget.spent, budget.limit))
    if vision_budget:
        print('  second-reader spend: $%.4f of $%.2f (%d case share(s) of $%.4f)'
              % (vision_budget.budget.spent, vision_budget.budget.limit,
                 len(vision_budget.batch['roster']), vision_budget.batch['share']))
    if token_budget:
        print('  search tokens attempted: %d of %d allowed; captcha %s%s'
              % (token_budget['spent'], args.token_budget,
                 json.dumps(token_budget.get('captcha_balance')),
                 ('; STOPPED: ' + token_budget['stopped']) if token_budget.get('stopped') else ''))
    if written and not read_ok:
        print('  NOTE: no document was read. On Miami scans that means OCR did not run or did '
              'not return text — every dossier section c is honestly empty.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
