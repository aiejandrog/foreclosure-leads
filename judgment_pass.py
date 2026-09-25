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


def _vision_key(source_ref):
    # run_case_timeline.read_amounts names its evidence file amount-vision-<this>.json.
    return hashlib.sha256(str(source_ref).encode()).hexdigest()


def _bought(path, row):
    """The saved amount read for this exact document, or None. Bound to the document hash the way
    read_amounts binds it: a re-downloaded filing is not already bought."""
    import document_store as DS
    detail = DS.pipeline_load(path) if Path(path).exists() else None
    current = ((row.get('manifest') or {}).get('source_sha256') or
               (row.get('manifest') or {}).get('sha256'))
    if not detail or not current or detail.get('document_hash') != current:
        return None
    return detail


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


def _target(plan, controlling):
    """The judgment whose amount counts: the controlling entry when the timeline established one,
    else the latest dated final judgment on the docket. Never 'any judgment': an older, amended or
    vacated one verifying proves nothing about the current debt."""
    import run_case_timeline as RCT
    if controlling:
        return controlling
    dated = sorted((d['date'], str(d['entry_id'])) for d in plan['documents']
                   if d['kind'] in RCT._AMOUNT_KINDS and d.get('date')
                   and 'future_entry_not_current_evidence' not in (d.get('gaps') or []))
    return dated[-1][1] if dated else None


def assess(case, base, timeline, as_of):
    """One case's state from disk. -> dict with 'state' and the page counts a paid read needs."""
    import document_store as DS
    import document_prioritizer as DP
    import miami_timeline_amounts as amounts
    import run_case_timeline as RCT
    out = {'case': case, 'pages_to_judgment': 0, 'pages_all': 0}
    controlling = str(((timeline or {}).get('judgments') or {}).get('controlling_entry') or '')
    out['controlling_entry'] = controlling or None
    checks = ((timeline or {}).get('amount_vision') or {}).get('amount_checks') or []
    inventory = DS.pipeline_load(Path(base) / 'inventory.json')
    if inventory is None:
        out['state'] = 'no_docket'
        return out
    try:
        plan = DP.prioritize(case, inventory, as_of)
    except ValueError as exc:
        out.update(state='docket_incomplete', detail=str(exc)[:200])
        return out
    target = _target(plan, controlling)
    if target is None:
        out['state'] = 'no_judgment_on_docket'
        return out
    out['target_entry'] = target
    if any(c.get('ok') and str(c.get('entry_id')) == target for c in checks):
        out['state'] = 'verified'
        return out
    rows = RCT.load_rows(base)
    order = DP.timeline_read_order(plan, rows)['order']
    reached = bought_unverified = False
    for row in order:
        pages = amounts.amount_page_numbers(row.get('reading') or {})
        if not pages:
            continue
        is_target = str(row.get('entry_ref') or '') == target
        detail = _bought(Path(base) / ('amount-vision-' + _vision_key(row.get('source_ref')) + '.json'),
                         row)
        if detail is not None and not detail.get('gaps'):
            if is_target and not reached:
                # Read in full and still no exact-cents agreement: paying again buys the same answer.
                reached = bought_unverified = True
            continue
        out['pages_all'] += len(pages)
        if not reached:
            out['pages_to_judgment'] += len(pages)
            reached = is_target
    if bought_unverified:
        out['state'] = 'read_not_verified'
        out['pages_to_judgment'] = 0
    elif reached:
        out['state'] = 'needs_paid_read'
    else:
        doc = next((d for d in plan['documents'] if str(d['entry_id']) == target), {})
        fetched = any(str(r.get('entry_ref') or '') == target for r in rows)
        if not fetched or not doc.get('eligible_for_acquisition'):
            out.update(state='judgment_not_fetched',
                       detail=','.join(doc.get('gaps') or []) or 'not_downloaded')
        else:
            # Fetched, but no page carries a printed dollar figure the free OCR could see: a paid
            # read would have nothing selected. Named, never counted as verified.
            out['state'] = 'judgment_without_amount_page'
    if out['state'] != 'needs_paid_read':
        out['pages_all'] = 0
    return out


def run_pass(runner, entries, today, collect, log, limit=None):
    import run_case_timeline as RCT
    done = errors = skipped = 0
    for entry in entries:
        if limit and done + errors >= limit:     # --limit counts cases worked, not cases skipped
            break
        case = entry['case']
        if built_recently(timeline_path(runner, case)):
            skipped += 1
            (log.get('errors') or {}).pop(case, None)
            continue
        try:
            RCT.timeline_case(case, today, collect=collect)   # shared=None: no API client, $0
            done += 1
            (log.get('errors') or {}).pop(case, None)
        except Exception as exc:                              # one case never stops the pass
            errors += 1
            log.setdefault('errors', {})[case] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
        log['last'] = case
    return done, skipped, errors


def plan(runner, entries, today, log):
    import document_store as DS
    root = Path(DS.pipeline_folder(runner.COUNTY, 'x')).parent
    rate, sample = evidence_rate(root)
    rows = []
    for entry in entries:
        case = entry['case']
        try:
            timeline = DS.pipeline_load(timeline_path(runner, case))
            row = assess(case, DS.pipeline_folder(runner.COUNTY, case), timeline, today.isoformat())
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
              'judgment_without_amount_page', 'no_judgment_on_docket', 'docket_incomplete',
              'no_docket', 'unreadable_on_disk']
    lines = ['# Miami judgment amounts: court-copy state and read plan (%s)' % today.isoformat(), '',
             'Built from files on disk after a $0 docket pass. Case numbers only; no names. '
             '"verified" means the court copy\'s line items reproduce its printed total to the cent '
             '(judgment_money); it is not an award, an open balance or an equity input.', '',
             '| State | ' + ' | '.join(names) + ' | Total |', '|---' * (len(names) + 2) + '|']
    for state in states:
        counts = [sum(1 for r in rows if r['state'] == state and window(r) == n) for n in names]
        lines.append('| %s | %s | %d |' % (state, ' | '.join(map(str, counts)), sum(counts)))
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
            (r.get('pass_error') or r.get('detail') or '').replace('|', '/')[:120]))
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
    log_path = out_dir / ('pass-%s.json' % today.isoformat())
    try:
        log = json.loads(log_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log = {}
    print('judgment_pass: %d docketed Miami cases, %d tax-deed IDs left out; $0: no API client, '
          'no token mints, no captcha' % (len(entries), len(skipped_ids)))
    if not args.report_only:
        try:
            done, skipped, errors = run_pass(runner, entries, today, args.collect, log, args.limit)
        finally:
            log_path.write_text(json.dumps(log, indent=2), encoding='utf-8')
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
