"""judgment_pass — the $0 whole-case docket pass over every Miami lead, then a priced read plan.

Why this exists (2026-09-25): a judgment amount is only trusted when the court's own copy was read
and its line items reproduce the printed total (judgment_money's exact-cents check). On the 09-24
sweep that held for 30 of 349 Miami leads. The 09-23 backfill went through the recorded-instrument
route, which needs a cached owner-search token, and 284 cases had none. The whole-case timeline
(#53, run_case_timeline) reaches the final judgment through the court docket instead: no token, no
captcha. Its only paid step is the vision read of amount pages.

So this does two things, in order, and neither spends:

  1. PASS: run_case_timeline.timeline_case(case, collect=True/False) with NO vision budget, soonest
     sale first. That collects the full OCS docket, downloads the accessible filings, OCRs them for
     free, and reuses any hash-bound vision evidence already on disk. Resumable: a case whose
     timeline was written in the last 20 hours is skipped.
  2. PLAN: for every case, from files on disk only, whether its controlling (or latest) judgment
     already has an exact-cents verified amount, and if not, how many amount pages the paid reader
     would have to send to reach it, priced at the measured average of the evidence already bought.

It never creates an API client, never mints a token, never writes equity. Amounts it reports are
arithmetic agreement on one filing (see run_case_timeline.read_amounts), never an equity input.

    python judgment_pass.py --collect             # the free pass, then the plan
    python judgment_pass.py --collect --days 30   # only sales in the next 30 days
    python judgment_pass.py --report-only         # the plan from what is already on disk
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

CASE_RE = re.compile(r'\d{4}-\d{6}-(?:CA-01|CC-\d{2})')   # run_case_timeline.validate_case's
# Measured 2026-09-22 on the desktop: $0.045 per judgment once page selection sent two pages.
# Used only when no bought evidence is on disk to average; the report says which it used.
FALLBACK_USD_PER_PAGE = 0.0225


def _auction(entry):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(entry.get('auction_date') or ''), fmt).date()
        except ValueError:
            continue
    return None


def _with_cached_ocr(row, base):
    """The row as the paid reader sees it: plus the free 300-DPI OCR the pass already cached
    (miami_timeline_ocr's hash-keyed file). Cache only: supplement() itself would OCR, and off
    Windows it would write ocr_failed into the cache a real pass later trusts."""
    import copy
    manifest = row.get('manifest') or {}
    path = manifest.get('path') or manifest.get('pdf_path') or row.get('path')
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        saved = json.loads((Path(base) / 'timeline-ocr' / (digest + '-ocr300-v1.json'))
                           .read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError):
        return row
    row = copy.deepcopy(row)
    for page in (row.get('reading') or {}).get('pages', []):
        got = (saved.get('pages') or {}).get(str(page.get('page')))
        if got:
            page['supplemental_ocr'] = got
    return row


def load_entries(runner, leads_file=None):
    import document_backfill
    rows = json.loads(Path(leads_file or runner.LEADS).read_text(encoding='utf-8'))
    return document_backfill.select_cases(rows, runner._load(runner.CHAINS, {}))


def timeline_path(runner, case):
    path = Path(runner.dossier_path(runner.COUNTY, case))
    return path.with_name(path.stem + '-timeline.json')


FRESH_HOURS = 20


def built_recently(path, hours=FRESH_HOURS, now=None):
    """A timeline written in the last `hours` is skipped on a re-run. By file age, not the
    timeline's as_of date: a pass that crosses local midnight would otherwise rebuild every case
    the previous day's half already built."""
    try:
        age = (now or time.time()) - Path(path).stat().st_mtime
    except OSError:
        return False
    return 0 <= age < hours * 3600


def evidence_rate(root):
    """Average dollars per amount page over the vision evidence already bought, or None."""
    usd = pages = 0
    for path in Path(root).glob('*/amount-vision-*.json'):
        try:
            detail = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        # Only pages that were actually billed: a page cut off by the cap, an errored page or a
        # ledger-cache hit at $0 is not a price sample.
        for result in (detail.get('pages') or {}).values():
            cost = float((result or {}).get('usd') or 0)
            if cost > 0:
                usd += cost
                pages += 1
    return (usd / pages, pages) if pages else (None, 0)


def _target(recon, controlling):
    """-> (entry_id or None, reason). The controlling entry when the timeline established one;
    else the latest OPERATIVE judgment of record in the docket's own reconciliation. A vacated,
    satisfied, supplemental or duplicate entry is never the target: its amount proves nothing
    about the current debt."""
    if controlling:
        return controlling, 'controlling'
    judgments = (recon or {}).get('judgments') or []
    operative = [j for j in judgments if j.get('status') in ('operative', 'partially_vacated')
                 and j.get('role') not in ('supplemental', 'docket_duplicate') and j.get('date')]
    if operative:
        best = max(operative, key=lambda j: (j['date'], _entry_order(j['entry_id'])))
        return str(best['entry_id']), 'latest_operative'
    # The reconciliation could not tie them together (two same-day entries, a vacatur naming no
    # judgment): the latest unclear one is still the one to read, and the basis says so.
    unclear = [j for j in judgments if j.get('status') == 'unclear'
               and j.get('role') not in ('supplemental', 'docket_duplicate') and j.get('date')]
    if unclear:
        best = max(unclear, key=lambda j: (j['date'], _entry_order(j['entry_id'])))
        return str(best['entry_id']), 'latest_unclear'
    return None, ('no_operative_judgment' if judgments else 'no_judgment_on_docket')


def _entry_order(entry_id):
    text = str(entry_id)
    return (0, int(text), '') if text.isdigit() else (1, 0, text)


def assess(case, base, timeline, as_of, timeline_mtime=None):
    """One case's state from disk. -> dict with 'state' and the page counts a paid read needs.

    Evidence is read through run_case_timeline.read_amounts with no budget: the same hash binding
    and the same exact-cents check the timeline itself uses, so the two cannot disagree."""
    import document_store as DS
    import document_prioritizer as DP
    import miami_timeline_amounts as amounts
    import run_case_timeline as RCT
    out = {'case': case, 'pages_to_judgment': 0, 'pages_all': 0}
    controlling = str(((timeline or {}).get('judgments') or {}).get('controlling_entry') or '')
    out['controlling_entry'] = controlling or None
    inventory = DS.pipeline_load(Path(base) / 'inventory.json')
    if inventory is None:
        out['state'] = 'no_docket'
        return out
    try:
        plan = DP.prioritize(case, inventory, as_of)
    except ValueError as exc:
        out.update(state='docket_incomplete', detail=str(exc)[:200])
        return out
    try:
        docket_mtime = (Path(base) / 'inventory.json').stat().st_mtime
    except OSError:
        docket_mtime = None
    if timeline is not None and timeline_mtime is not None and docket_mtime is not None \
            and timeline_mtime < docket_mtime:
        # The docket was refreshed after the timeline was built (a rebuild that failed part-way):
        # its controlling judgment and checks may predate a new vacatur or satisfaction.
        out.update(state='timeline_older_than_docket',
                   detail='re-run the pass for this case before trusting any verdict')
        return out
    # The timeline's reconciliation read the bodies (a vacatur citing its judgment's date, an
    # image-less same-day twin); the plan's is the docket index alone. Prefer the timeline's.
    recon = (timeline or {}).get('judgments') or plan.get('judgments')
    target, why = _target(recon, controlling)
    if target is None:
        out['state'] = why
        return out
    out.update(target_entry=target, target_basis=why)
    if why != 'controlling':
        out['detail'] = ('no controlling judgment established; target is the %s one'
                         % why.replace('latest_', 'latest '))
    rows = RCT.load_rows(base)
    bought = RCT.read_amounts(rows, base)
    if any(c.get('ok') and str(c.get('entry_id')) == target for c in bought['amount_checks']):
        out['state'] = 'verified'
        return out
    # Pages a re-run would actually bill: a page the reader never reached (cap, error). A page it
    # read and called unreadable is a ledger-cache hit at $0 with the same answer, so not priced.
    rebuy = {}
    stuck = set()
    for gap in bought['gaps']:
        reason = str(gap.get('reason') or '')
        # Only a page the cap or the reader's stop never reached is bought by paying again. An
        # unreadable page, a rejected render or an uncertain paid call repeats at $0: a person.
        if re.search(r'budget|cap or reader stop|paid_read_not_selected', reason):
            rebuy.setdefault(gap.get('source_ref'), set()).add(gap.get('page'))
        else:
            stuck.add(gap.get('source_ref'))
    done = {Path(f).name for f in bought['evidence_files']}
    # Only the rows the paid reader would walk get the (hash-keyed, whole-file) OCR lookup.
    order = [_with_cached_ocr(r, base) for r in DP.timeline_read_order(plan, rows)['order']]
    need = []          # (is_target, pages) for every amount-bearing row with pages left to buy
    target_amount_rows = 0
    for row in order:
        pages = amounts.amount_page_numbers(row.get('reading') or {})
        if not pages:
            continue
        is_target = str(row.get('entry_ref') or '') == target
        target_amount_rows += is_target
        key = 'amount-vision-' + hashlib.sha256(str(row.get('source_ref')).encode()).hexdigest() + '.json'
        if key in done:
            detail = DS.pipeline_load(Path(base) / key) or {}
            # Amount pages the free OCR found after the purchase are new pages to buy.
            fresh = set(pages) - set(detail.get('selected_pages') or pages)
            left = (rebuy.get(row.get('source_ref')) or set()) | fresh
            if left:
                need.append((is_target, len(left)))
            elif is_target and row.get('source_ref') in stuck:
                out['detail'] = 'a read of the judgment failed in a way paying again repeats'
            continue
        need.append((is_target, len(pages)))
    if any(t for t, _ in need):
        last = max(i for i, (t, _) in enumerate(need) if t)
        out.update(state='needs_paid_read', pages_to_judgment=sum(n for _, n in need[:last + 1]),
                   pages_all=sum(n for _, n in need))
    elif target_amount_rows:
        # Every amount page of the target was read in full and the figures still do not reproduce
        # the printed total to the cent: paying again buys the same answer.
        out['state'] = 'read_not_verified'
    else:
        doc = next((d for d in plan['documents'] if str(d['entry_id']) == target), {})
        fetched = any(str(r.get('entry_ref') or '') == target for r in rows)
        if not fetched:
            out.update(state='judgment_not_fetched',
                       detail=','.join(doc.get('gaps') or []) or 'not_downloaded')
        elif not doc.get('eligible_for_acquisition'):
            # On disk, but the docket plan holds the entry (undated, no image count), so the paid
            # reader defers it. The fix is the docket entry, not another download.
            out.update(state='judgment_held_by_docket_plan',
                       detail=','.join(doc.get('gaps') or []) or 'not_eligible')
        else:
            # Fetched, but no page carries a printed dollar figure the free reads could see: a
            # paid read would have nothing selected. Named, never counted as verified.
            out['state'] = 'judgment_without_amount_page'
    return out


def run_pass(runner, entries, today, collect, log, limit=None, save=None):
    import run_case_timeline as RCT
    done = errors = skipped = 0
    for entry in entries:
        if limit is not None and done + errors >= limit:     # --limit counts cases worked, not cases skipped
            break
        case = entry['case']
        last = (log.get('built') or {}).get(case) or {}
        if (built_recently(timeline_path(runner, case))
                and (last.get('collect') or not collect)
                and time.time() - float(last.get('at') or 0) < FRESH_HOURS * 3600):
            skipped += 1
            continue
        try:
            RCT.timeline_case(case, today, collect=collect)   # shared=None: no API client, $0
            done += 1
            log.setdefault('built', {})[case] = {'at': time.time(), 'collect': bool(collect)}
            (log.get('errors') or {}).pop(case, None)
        except Exception as exc:                              # one case never stops the pass
            errors += 1
            log.setdefault('errors', {})[case] = '%s %s: %s' % (
                date.today().isoformat(), type(exc).__name__, str(exc)[:200])
        log['last'] = case
        if save:
            save()
    return done, skipped, errors


def plan(runner, entries, today, log):
    import document_store as DS
    root = Path(DS.pipeline_folder(runner.COUNTY, 'x')).parent
    rate, sample = evidence_rate(root)
    rows = []
    for entry in entries:
        case = entry['case']
        try:
            tpath = timeline_path(runner, case)
            timeline = DS.pipeline_load(tpath)
            row = assess(case, DS.pipeline_folder(runner.COUNTY, case), timeline, today.isoformat(),
                         tpath.stat().st_mtime if timeline is not None else None)
        except Exception as exc:                  # one unreadable case never costs the report
            row = {'case': case, 'state': 'unreadable_on_disk', 'pages_to_judgment': 0,
                   'pages_all': 0, 'detail': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
        auction = _auction(entry)
        row['sale'] = auction.isoformat() if auction else None
        if case in (log.get('errors') or {}):
            row['pass_error'] = log['errors'][case]
        rows.append(row)
    return rows, rate, sample


def render(rows, skipped_ids, rate, sample, today):
    per_page = rate if rate is not None else FALLBACK_USD_PER_PAGE
    basis = ('average of %d amount pages already bought ($%.4f/page)' % (sample, rate)
             if rate is not None else
             'no bought evidence on disk; the 09-22 measurement, $%.4f/page' % FALLBACK_USD_PER_PAGE)
    windows = [('next 7 days', 7), ('8-30 days', 30), ('31+ days', 10 ** 6)]
    def window(row):
        if not row['sale']:
            return 'no sale date'
        days = (date.fromisoformat(row['sale']) - today).days
        if days < 0:
            return 'sale passed'
        return next(name for name, limit in windows if days <= limit)
    names = [w[0] for w in windows] + ['sale passed', 'no sale date']
    states = ['verified', 'needs_paid_read', 'read_not_verified', 'judgment_not_fetched',
              'judgment_held_by_docket_plan', 'judgment_without_amount_page',
              'timeline_older_than_docket',
              'no_operative_judgment', 'no_judgment_on_docket', 'docket_incomplete', 'no_docket',
              'unreadable_on_disk']
    lines = ['# Miami judgment amounts: court-copy state and read plan (%s)' % today.isoformat(), '',
             'Built from files on disk after a $0 docket pass. Case numbers only; no names. '
             '"verified" means the court copy\'s line items reproduce its printed total to the cent '
             '(judgment_money); it is not an award, an open balance or an equity input.', '',
             '| State | ' + ' | '.join(names) + ' | Total |', '|---' * (len(names) + 2) + '|']
    for state in states:
        counts = [sum(1 for r in rows if r['state'] == state and window(r) == n) for n in names]
        lines.append('| %s | %s | %d |' % (state, ' | '.join(map(str, counts)), sum(counts)))
    guessed = sum(1 for r in rows if r['state'] == 'verified'
                  and r.get('target_basis') not in (None, 'controlling'))
    lines += ['', 'Of the verified, %d are on a judgment the docket did not establish as controlling '
              '(the latest operative or unclear one); their Note says which.' % guessed]
    lines += ['', 'Tax-deed IDs with no court docket, left out (all sale dates): %d' % len(skipped_ids), '']
    lines += ['## Price to read the rest', '', 'Rate: ' + basis + '.', '',
              '| Sales | Cases | Pages to the judgment | Cost | Pages, whole case | Cost |',
              '|---|---|---|---|---|---|']
    for name in names:
        need = [r for r in rows if r['state'] == 'needs_paid_read' and window(r) == name]
        if not need:
            continue
        pj = sum(r['pages_to_judgment'] for r in need)
        pa = sum(r['pages_all'] for r in need)
        lines.append('| %s | %d | %d | $%.2f | %d | $%.2f |'
                     % (name, len(need), pj, pj * per_page, pa, pa * per_page))
    lines += ['', '"read_not_verified": the judgment was already read in full and its figures do not '
              'reproduce its printed total to the cent; paying again buys the same answer, so it '
              'needs a person or the paid clerk copy, not another read.']
    lines += ['', '"Pages to the judgment" is what the paid reader sends in its own order '
              '(controlling orders first, then judgments newest first) until it reaches the '
              'judgment. "Whole case" is every amount page it would read with an unlimited share.',
              '', '## Every case', '', '| Case | Sale | State | Pages to judgment | Note |',
              '|---|---|---|---|---|']
    for r in sorted(rows, key=lambda r: (r['sale'] or '9999', r['case'])):
        lines.append('| %s | %s | %s | %s | %s |' % (
            r['case'], r['sale'] or '', r['state'],
            r['pages_to_judgment'] if r['state'] == 'needs_paid_read' else '',
            '; '.join(x for x in (r.get('detail'), r.get('pass_error') and
                                  'last pass error ' + r['pass_error']) if x)
            .replace('|', '/')[:200]))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--collect', action='store_true',
                        help='refresh each case\'s full OCS docket first (free)')
    parser.add_argument('--days', type=int, default=None,
                        help='only cases whose sale is within this many days')
    parser.add_argument('--limit', type=int, default=None, help='cases this pass (resumable)')
    parser.add_argument('--report-only', action='store_true', help='skip the pass; plan from disk')
    parser.add_argument('--leads-file', help='default leads_final.json')
    args = parser.parse_args(argv)
    import case_review
    import run_documents as runner
    today = date.today()
    entries = load_entries(runner, args.leads_file)
    skipped_ids = [e['case'] for e in entries if not CASE_RE.fullmatch(e['case'])]
    entries = [e for e in entries if CASE_RE.fullmatch(e['case'])]
    if args.days is not None:
        entries = [e for e in entries if _auction(e) and 0 <= (_auction(e) - today).days <= args.days]
    out_dir = Path(case_review.output_path('judgment-plan/.keep')).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / 'pass-log.json'      # one log across days: a pass may cross midnight
    try:
        log = json.loads(log_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log = {}
    print('judgment_pass: %d docketed Miami cases, %d tax-deed IDs left out; $0: no API client, '
          'no token mints, no captcha' % (len(entries), len(skipped_ids)))
    if not args.report_only:
        if os.name != 'nt':
            # The pass OCRs through Windows' own reader; anywhere else every page would be cached
            # as ocr_failed and a later Windows pass would trust that and never read it.
            parser.error('the pass runs on Windows only; use --report-only elsewhere')
        import document_store as DS
        def save():                              # after every case: a killed pass keeps progress
            DS._atomic_write_text(str(log_path), json.dumps(log, indent=2))
        done, skipped, errors = run_pass(runner, entries, today, args.collect, log, args.limit, save)
        print('  pass: %d built, %d built in the last 20h, %d errors (log %s)'
              % (done, skipped, errors, log_path))
    rows, rate, sample = plan(runner, entries, today, log)
    report = render(rows, skipped_ids, rate, sample, today)
    md = out_dir / ('JUDGMENT-PLAN-%s.md' % today.isoformat())
    md.write_text(report, encoding='utf-8')
    (out_dir / ('JUDGMENT-PLAN-%s.json' % today.isoformat())).write_text(
        json.dumps({'rows': rows, 'usd_per_page': rate, 'rate_sample_pages': sample}, indent=2),
        encoding='utf-8')
    print(report.split('\n## Every case')[0])
    print('  wrote %s' % md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
