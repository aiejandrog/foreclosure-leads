"""Token-only Miami batch; never performs document reading or vision."""
import argparse
import hashlib
import json
import math
from datetime import date
from decimal import Decimal
from pathlib import Path
import document_store as DS
from document_backfill import State, select_cases
from miami_search_budget import search_name_parts
from document_walk import NameSearcher


class TokenLadder(NameSearcher):
    """Reuse the existing browser session; pay only after cache/browser access fail."""
    def __init__(self, cache, solver, mint=None):
        super().__init__(qs_cache=cache)
        import gen_records_qs as G
        self.solver, self.mint = solver, mint or G.mint_qs

    def search_token(self, owner, parts):
        token = self.qs_cache.get(owner)
        if token:
            try:
                rows = self.R.records_by_qs(token)
            except Exception:
                rows = None
            if rows is not None:
                return token, len(rows)
            self.qs_cache.pop(owner, None)
        try:
            browser = self._camoufox()
            token = self.R.camoufox_qs(browser, parts) if browser is not None else None
            rows = self.R.records_by_qs(token) if token else None
        except Exception:
            rows = None
        if rows is not None:
            # Empty/overmatched results are valid responses, not a reason to pay again.
            return token, len(rows)
        return self.mint(parts, tries=1, solver=self.solver)


def process(entries, qs_cache, token_budget, solver, state, cache_path, mint=None, lookup=None):
    import gen_records_qs as G
    mint = mint or G.mint_qs
    attempts = state.data.setdefault('owner_attempts', {})
    stopped = None
    for entry in entries:
        owner = entry['owner']
        if qs_cache.get(owner) and lookup is None:
            continue
        key = hashlib.sha256(owner.encode()).hexdigest()
        if key in attempts:
            continue
        if len(attempts) >= token_budget:
            stopped = 'token_budget_reached'
            break
        parts = search_name_parts(owner)
        attempts[key] = {'case':entry['case'], 'status':'running', 'cached':False}
        state.save()
        if not parts:
            attempts[key]['status'] = 'name_not_searchable'
            state.save()
            continue
        try:
            prior_token = qs_cache.get(owner)
            token, hits = lookup(owner, parts) if lookup else mint(parts, tries=1, solver=solver)
        except Exception as exc:
            attempts[key]['status'] = 'stopped_' + type(exc).__name__
            stopped = type(exc).__name__
            state.save()
            break
        valid = bool(token) and 0 < hits <= G.MAX_HITS
        if valid:
            qs_cache[owner] = token
        else:
            qs_cache.pop(owner, None)
        DS.pipeline_write(cache_path, qs_cache)
        attempts[key].update(cached=valid, minted=valid and token != prior_token, hits=hits,
            status='cached' if valid else 'overmatch' if hits > G.MAX_HITS else 'no_usable_token')
        state.save()
        print(json.dumps({'attempted_owners':len(attempts),
            'cases_with_token':sum(bool(qs_cache.get(e['owner'])) for e in entries),
            'captcha_usd':state.data['actual_usd']}), flush=True)
    report = {'cases':len(entries), 'attempted_owners':len(attempts),
        'tokens_minted':sum(a.get('minted', a.get('cached')) is True for a in attempts.values()),
        'cases_with_token':sum(bool(qs_cache.get(e['owner'])) for e in entries),
        'captcha_receipt_usd':state.data['actual_usd'], 'stopped':stopped,
        'captcha_reserved_usd':sum(state.data.get('reserved',{}).values()),
        'captcha_pending':state.data.get('captcha_pending'),
        'cutoff_policy':'Maximum 300 paid submissions; real account balance debit stop at $1.50',
        'vision_spent_usd':0,
        'projected_vision_usd':float(Decimal(len(entries))*Decimal('0.14'))}
    state.data['last_report'] = report
    state.save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--leads-file', type=Path, required=True)
    parser.add_argument('--qs-cache', type=Path, default=Path(__file__).with_name('records_qs.json'))
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--token-budget', type=int, required=True)
    parser.add_argument('--captcha-max-spend', type=float, required=True)
    parser.add_argument('--allow-one-request-overrun', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.token_budget <= 0 or not math.isfinite(args.captcha_max_spend) or not 0 < args.captcha_max_spend <= 1.50:
        parser.error('Positive token budget and finite CAPTCHA cutoff at most $1.50 required')
    rows = json.loads(args.leads_file.read_text(encoding='utf-8'))
    if not isinstance(rows, list): parser.error('Lead input must be an array')
    entries = select_cases(rows, {}, today=date.today())
    cache = DS.pipeline_load(args.qs_cache, {})
    if args.dry_run:
        print(json.dumps({'cases':len(entries), 'cases_with_token':sum(bool(cache.get(e['owner'])) for e in entries),
            'projected_vision_usd':float(Decimal(len(entries))*Decimal('0.14')), 'captcha_spent_usd':0, 'vision_spent_usd':0}))
        return 0
    from captcha_cost_cutoff import PaidCutoffSolver, CutoffStopped
    import captcha_solver
    key = args.key_file.read_text().strip() if args.key_file else captcha_solver._key()
    if not key: parser.error('CAPTCHA key unavailable')
    with State(args.state) as state:
        solver = PaidCutoffSolver(state, args.captcha_max_spend, key)
        ladder = TokenLadder(cache, solver)
        try:
            result = process(entries, cache, args.token_budget, solver, state, args.qs_cache,
                             lookup=ladder.search_token)
        finally:
            ladder.close()
            # Runs on interruption too; missing/failed balance stays unknown, never zero.
            try:
                balance = solver.finish()
            except CutoffStopped:
                def saved_money(name):
                    value = state.data.get(name)
                    return float(Decimal(value)) if value is not None else None
                balance = {
                    'balance_before_usd':saved_money('captcha_balance_before_decimal'),
                    'balance_after_usd':saved_money('captcha_balance_after_decimal'),
                    'balance_spend_usd':saved_money('captcha_balance_debit_decimal'),
                    'balance_check_failed':True,
                    'final_balance_unsettled':True,
                    'paid_attempts':state.data.get('captcha_paid_attempts'),
                }
            print(json.dumps({'captcha_balance':balance}), flush=True)
        result.update(balance)
        if balance.get('balance_check_failed') or state.data.get('captcha_halted'):
            result['stopped'] = state.data.get('captcha_halted') or 'final_balance_unavailable'
        state.data['last_report'] = result
        state.save()
        print(json.dumps(result), flush=True)
    return 2 if result['stopped'] else 0


if __name__ == '__main__': raise SystemExit(main())
