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
     timeline was already built today is skipped.
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
from datetime import date, datetime
from pathlib import Path

CASE_RE = re.compile(r'\d{4}-\d{6}-(?:CA-01|CC-\d{2})')
JUDGMENT_KINDS = ('final_judgment', 'judgment')
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
    return hashlib.sha256(str(source_ref).encode()).hexdigest()


def load_entries(runner, leads_file=None):
    import document_backfill
    rows = json.loads(Path(leads_file or runner.LEADS).read_text(encoding='utf-8'))
    return document_backfill.select_cases(rows, runner._load(runner.CHAINS, {}))


def timeline_path(runner, case):
    path = Path(runner.dossier_path(runner.COUNTY, case))
    return path.with_name(path.stem + '-timeline.json')


def built_today(runner, case, today):
    import document_store as DS
    saved = DS.pipeline_load(timeline_path(runner, case)) or {}
    return str(saved.get('as_of') or '')[:10] == today.isoformat()


def evidence_rate(root):
    """Average dollars per amount page over the vision evidence already bought, or None."""
    usd = pages = 0
    for path in Path(root).glob('*/amount-vision-*.json'):
        try:
            detail = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        sent = len(detail.get('selected_pages') or [])
        if sent and detail.get('usd'):
            usd += float(detail['usd'])
            pages += sent
    return (usd / pages, pages) if pages else (None, 0)


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
    kinds = {str(d['entry_id']): d['kind'] for d in plan['documents']}
    judgment_entries = {e for e, k in kinds.items() if k in JUDGMENT_KINDS}
    target = {controlling} if controlling else judgment_entries
    verified = [c for c in checks if c.get('ok') and str(c.get('entry_id')) in target]
    if verified:
        out.update(state='verified', amount_entry=str(verified[0]['entry_id']))
        return out
    if not judgment_entries:
        out['state'] = 'no_judgment_on_docket'
        return out
    rows = RCT.load_rows(base)
    fetched = {str(r.get('entry_ref') or '') for r in rows}
    walled = [d for d in plan['documents'] if d['kind'] in JUDGMENT_KINDS
              and (not d['eligible_for_acquisition'] or str(d['entry_id']) not in fetched)]
    order = DP.timeline_read_order(plan, rows)['order']
    reached = False
    for row in order:
        pages = amounts.amount_page_numbers(row.get('reading') or {})
        if not pages:
            continue
        cached = Path(base) / ('amount-vision-' + _vision_key(row.get('source_ref')) + '.json')
        if cached.exists():
            continue
        out['pages_all'] += len(pages)
        if not reached:
            out['pages_to_judgment'] += len(pages)
            if str(row.get('entry_ref') or '') in target:
                reached = True
    if reached:
        out['state'] = 'needs_paid_read'
    elif walled:
        out.update(state='judgment_not_fetched',
                   detail=','.join(sorted({g for d in walled for g in (d['gaps'] or ['not_downloaded'])})))
    else:
        # A judgment was fetched but no page of it carries a printed dollar figure the free OCR
        # could see: a paid read would have nothing selected. Named, never counted as verified.
        out['state'] = 'judgment_without_amount_page'
    return out


def run_pass(runner, entries, today, collect, log):
    import run_case_timeline as RCT
    done = errors = skipped = 0
    for entry in entries:
        case = entry['case']
        if built_today(runner, case, today):
            skipped += 1
            continue
        try:
            RCT.timeline_case(case, today, collect=collect)   # shared=None: no API client, $0
            done += 1
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
        timeline = DS.pipeline_load(timeline_path(runner, case))
        row = assess(case, DS.pipeline_folder(runner.COUNTY, case), timeline, today.isoformat())
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
    states = ['verified', 'needs_paid_read', 'judgment_not_fetched', 'judgment_without_amount_page',
              'no_judgment_on_docket', 'docket_incomplete', 'no_docket']
    lines = ['# Miami judgment amounts: court-copy state and read plan (%s)' % today.isoformat(), '',
             'Built from files on disk after a $0 docket pass. Case numbers only; no names. '
             '"verified" means the court copy\'s line items reproduce its printed total to the cent '
             '(judgment_money); it is not an award, an open balance or an equity input.', '',
             '| State | ' + ' | '.join(names) + ' | Total |', '|---' * (len(names) + 2) + '|']
    for state in states:
        counts = [sum(1 for r in rows if r['state'] == state and window(r) == n) for n in names]
        lines.append('| %s | %s | %d |' % (state, ' | '.join(map(str, counts)), sum(counts)))
    lines += ['', 'Tax-deed IDs with no court docket, left out: %d' % len(skipped_ids), '']
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
        todo = entries[:args.limit] if args.limit else entries
        try:
            done, skipped, errors = run_pass(runner, todo, today, args.collect, log)
        finally:
            log_path.write_text(json.dumps(log, indent=2), encoding='utf-8')
        print('  pass: %d built, %d already built today, %d errors (log %s)'
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
