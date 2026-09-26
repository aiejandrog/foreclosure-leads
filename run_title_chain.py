"""run_title_chain.py — F2: title discovery and owner tokens on the nightly schedule. OFF by default.

WHAT IT RUNS, IN ORDER
  1. owner tokens (run_owner_tokens.py) -- PAID, and a second opt-in on top of the first:
     only when DEALFLOW_TITLE_TOKENS=1 (or --tokens N). A token is one 2Captcha solve (~$0.003)
     and lets every later step search that owner's Official Records rows for free. The month's
     attempts are capped at TOKENS_PER_NIGHT x day-of-month in one per-month state file, so an
     owner that failed is not paid for again the same month, and run_owner_tokens' own $1.50
     cutoff is per month here too. Its dollar cap is cut to what the shared monthly paid-reads cap
     (paid_reads.py, $50/month) has left; nothing is paid when the month is spent or unreadable.
  2. title discovery (miami_title_discovery.py) -- FREE. Its searcher uses cached tokens and
     free Camoufox mints only (the paid rung is blocked in miami_search_budget.CappedNameSearcher)
     and new documents are read with local OCR, never vision. The CLI still demands positive caps,
     so the chain passes a nominal one and its OWN ledger file (--captcha-ledger), leaving the
     manual runs' ledger alone.

WHICH CASES
Miami-Dade auction leads with an owner, sale within the next 45 days first (document_backfill's
order), skipping any case whose saved discovery report is younger than FRESH_DAYS and any case
whose sale has passed. At most --limit cases a night, and no new case starts after --deadline
seconds.

WHAT IT NEVER DOES
No board write, no lead write, no publish, no suppression surface, no email. Reports go where
miami_title_discovery already writes them (DEALFLOW_DIR/title_discovery, outside the repo). The
status file it writes there holds counts and dollars only, no names or case numbers.

THE LINE IN refresh-dealflow.bat (stage [2e/5], after the [2b/5] records step):
    python -u run_title_chain.py >> "%LOG%" 2>&1
It exits 0 at once unless DEALFLOW_TITLE=1, and it is never fatal.

    python run_title_chain.py --plan      # which cases tonight would take, no network, no spend
"""
import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, 'leads_final.json')
FRESH_DAYS = 14            # a discovery report younger than this is not redone
TOKENS_PER_NIGHT = 10      # paid owner-token attempts per night, averaged over the month
TOKEN_RUN_CAP_USD = 1.50   # run_owner_tokens refuses more than this; per month in this chain
NOMINAL_CAPTCHA_USD = 0.01 # discovery never pays; its CLI still needs a positive cap
NIGHTLY_LEDGER = 'nightly-captcha-budget.json'


def _on(env, name):
    return str(env.get(name) or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _date(s):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(str(s or '').strip(), fmt).date()
        except ValueError:
            continue
    return None


def plan(rows, reports_dir, today, limit, fresh_days=FRESH_DAYS, now=None):
    """Case numbers for tonight, in order. Pure apart from reading report mtimes."""
    from document_backfill import select_cases
    import re
    now = now if now is not None else time.time()
    try:
        entries = select_cases(rows, {}, today=today)
    except ValueError as exc:
        print('title chain: lead file refused (%s)' % str(exc)[:120])
        return []
    out = []
    for e in entries:
        case = e['case']
        if not e.get('owner') or not re.fullmatch(r'20\d{2}-\d{6}-(?:CA|CC)-\d{2}', case):
            continue
        sale = _date(e.get('auction_date'))
        if sale is not None and sale < today:
            continue
        rp = os.path.join(str(reports_dir), case + '.json') if reports_dir else None
        if rp and os.path.exists(rp) and (now - os.path.getmtime(rp)) < fresh_days * 86400:
            continue
        out.append(case)
        if len(out) >= limit:
            break
    return out


def token_args(today, private_dir, leads, per_night, requested_cap, remaining_fn):
    """The run_owner_tokens argv for tonight, or (None, why)."""
    cap = min(float(requested_cap), TOKEN_RUN_CAP_USD, float(remaining_fn()))
    if cap <= 0:
        return None, 'monthly paid-reads cap spent or unreadable'
    budget = int(per_night) * today.day
    state = os.path.join(str(private_dir), 'owner-tokens-%s.json' % today.strftime('%Y-%m'))
    return ['--leads-file', leads, '--state', state, '--token-budget', str(budget),
            '--captcha-max-spend', '%.4f' % cap], ''


def run(argv=None, env=None, today=None, discover=None, tokens=None, remaining=None, private_dir=None,
        rows=None, clock=time.monotonic):
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--enable', action='store_true', help='run even without DEALFLOW_TITLE=1')
    ap.add_argument('--plan', action='store_true', help='print tonight\'s cases and stop (no network, no spend)')
    ap.add_argument('--limit', type=int, default=3)
    ap.add_argument('--deadline', type=int, default=900, help='seconds; no new case starts after this')
    ap.add_argument('--tokens', type=int, default=None, help='paid owner-token attempts per night (default 0 '
                    'unless DEALFLOW_TITLE_TOKENS=1, then %d)' % TOKENS_PER_NIGHT)
    ap.add_argument('--token-max-spend', type=float, default=0.50)
    a = ap.parse_args(argv)
    if not (a.enable or a.plan or _on(env, 'DEALFLOW_TITLE')):
        print('title chain: off (set DEALFLOW_TITLE=1 to schedule title discovery)')
        return 0
    today = today or datetime.date.today()
    if private_dir is None:
        import case_review
        private_dir = case_review.output_path('title_discovery')
    os.makedirs(str(private_dir), exist_ok=True)
    if rows is None:
        try:
            rows = json.load(open(LEADS, encoding='utf-8'))
        except Exception as exc:
            print('title chain: no lead file (%s)' % str(exc)[:80])
            return 0
    per_night = a.tokens if a.tokens is not None else (TOKENS_PER_NIGHT if _on(env, 'DEALFLOW_TITLE_TOKENS') else 0)
    status = {'date': today.isoformat(), 'tokens': 'off', 'token_exit': None, 'cases_planned': 0,
              'cases_done': 0, 'cases_failed': 0, 'stopped': None, 'discovery_spend_usd': 0.0}

    cases = plan(rows, private_dir, today, max(0, a.limit))
    status['cases_planned'] = len(cases)
    if a.plan:
        print(json.dumps({'cases': cases, 'tokens_per_night': per_night}))
        return 0

    # 1. owner tokens (paid, second opt-in)
    if per_night > 0:
        if remaining is None:
            import paid_reads
            remaining = lambda: paid_reads.remaining('run_title_chain tokens')
        targs, why = token_args(today, private_dir, LEADS, per_night, a.token_max_spend, remaining)
        if targs is None:
            status['tokens'] = 'skipped: ' + why
            print('title chain: owner tokens skipped (%s)' % why)
        else:
            if tokens is None:
                import run_owner_tokens
                tokens = run_owner_tokens.main
            try:
                status['token_exit'] = tokens(targs)
                status['tokens'] = 'ran'
            except SystemExit as exc:
                status['token_exit'], status['tokens'] = exc.code, 'refused'
            except Exception as exc:
                status['token_exit'], status['tokens'] = None, 'error: ' + type(exc).__name__
            print('title chain: owner tokens %s (exit %s)' % (status['tokens'], status['token_exit']))

    # 2. title discovery (free), one case per call so one bad case cannot stop the rest
    if discover is None:
        import miami_title_discovery
        discover = miami_title_discovery.main
    t0 = clock()
    for case in cases:
        if clock() - t0 > a.deadline:
            status['stopped'] = 'deadline'
            break
        try:
            rc = discover(['--case', case, '--captcha-max-spend', str(NOMINAL_CAPTCHA_USD),
                           '--vision-max-spend', str(NOMINAL_CAPTCHA_USD), '--captcha-ledger', NIGHTLY_LEDGER])
            ok = rc in (0, None)
        except SystemExit as exc:
            ok = exc.code in (0, None)
        except Exception as exc:
            print('title chain: one case failed (%s)' % type(exc).__name__)
            ok = False
        status['cases_done' if ok else 'cases_failed'] += 1
    try:
        with open(os.path.join(str(private_dir), 'nightly-status.json'), 'w', encoding='utf-8') as f:
            json.dump(status, f, indent=1)
    except OSError:
        pass
    print('title chain: %d planned, %d done, %d failed%s; tokens %s' % (
        status['cases_planned'], status['cases_done'], status['cases_failed'],
        ' (stopped: %s)' % status['stopped'] if status['stopped'] else '', status['tokens']))
    return 0


def main(argv=None):
    try:
        return run(argv)
    except Exception as exc:          # never fatal to the refresh
        print('title chain: skipped (%s)' % str(exc)[:120])
        return 0


if __name__ == '__main__':
    sys.exit(main())
