"""Per-case shares of one cumulative vision cap. No case can spend another pending case's share.

WHY
The 2026-09-23 five-case pilot ran every case against ONE shared vision budget, and the first
case's reader walked its documents in the order the owner-name search returned them. It spent the
whole approval on historical and name-search candidates before it reached that case's current
judgment, and the four cases behind it got nothing. A shared cap is a cap, but it is not an
allocation: whoever goes first can take everything.

THE RULE
A batch has a FIXED roster, set when the batch starts and persisted with the ledger. Each case's
base share is the batch's starting headroom divided by the roster size. When a case asks to spend,
its allowance is

    headroom at batch start
    - everything the batch has charged or reserved so far
    - what every OTHER still-pending case has left of its base share

so a case always has its own share, can borrow what FINISHED cases left unspent, and can never
touch a share a pending case has not used yet. A finished case's leftover becomes borrowable the
moment it is marked finished; before that it is protected.

The underlying PersistentBudget (document_backfill) still enforces the global cap and still writes
the worst-case reservation to disk BEFORE the paid call. This module adds the per-case split on top
and, in the same ledger, which page each open reservation was for.

UNCERTAIN CALLS ARE NOT RETRIED
A reservation still open when a run starts means a paid call was made (or may have been) and its
outcome was never recorded: a crash, a kill, a transport error after the request left. The money
stays counted, and the page that reservation was for is NOT asked for again automatically. It
becomes a named gap (`uncertain_paid_call`), because a second request could be a second bill for
the same page. Clearing it is a person's decision, made by looking at the account's usage, not a
retry loop's.

WHAT A ROSTER OR CAP CHANGE DOES
A run whose roster or cap differs from the stored one starts a NEW batch over the cap's remaining
headroom. Charges are never reset and never refunded by a roster change; only the split of what is
left is recomputed. Deleting the ledger to get a fresh split is the one thing that would reset
money, and document_backfill.snapshot fails closed on a damaged ledger for exactly that reason.
"""
import hashlib
import json
import math

from document_interpreter import BudgetExhausted


class UncertainPaidCall(RuntimeError):
    """A paid request for this page may already have been billed; it is not repeated."""


class MemoryState:
    """An in-memory stand-in for document_backfill.State, for runs with no checkpoint (the
    nightly). Same data shape, so the same allocator and the same tests cover both."""

    def __init__(self):
        self.data = {'version': 1, 'cases': {}, 'actual_usd': 0.0, 'reserved': {}}

    def save(self):
        pass


def roster_id(roster):
    return hashlib.sha256(json.dumps(sorted(roster)).encode()).hexdigest()[:16]


def _charged(entry):
    return float(entry.get('actual_usd', 0.0)) + sum(float(v) for v in
                                                     (entry.get('reserved') or {}).values())


class CaseAllocator:
    """Splits a PersistentBudget across a fixed roster of cases."""

    def __init__(self, budget, roster):
        roster = list(dict.fromkeys(str(c) for c in roster))
        if not roster:
            raise ValueError('A per-case allocation needs at least one case')
        self.budget = budget
        self.state = budget.state
        data = self.state.data
        # The cap is part of the batch identity: raising it is an explicit authorization of a
        # larger total, so the split is recomputed over the new headroom instead of ignored.
        ident = roster_id(roster + ['cap=%r' % float(budget.limit)])
        batch = data.get('allocation')
        # Reservations left open by an earlier run are uncertain calls. Their page keys are read
        # BEFORE a new batch replaces the old one, so a roster change cannot forget them.
        self.uncertain = {}
        if isinstance(batch, dict):
            for rid, meta in (batch.get('reservation_keys') or {}).items():
                if rid in data['reserved'] and isinstance(meta, dict) and meta.get('key'):
                    self.uncertain[meta['key']] = dict(meta, reservation=rid)
        if not isinstance(batch, dict) or batch.get('roster_id') != ident:
            charged = data['actual_usd'] + sum(data['reserved'].values())
            headroom = max(0.0, budget.limit - charged)
            old_keys = {rid: meta for rid, meta in
                        ((batch or {}).get('reservation_keys') or {}).items()
                        if rid in data['reserved']}
            batch = {'roster_id': ident, 'roster': roster,
                     'headroom_at_start': headroom,
                     'share': headroom / len(roster),
                     'cases': {c: {'actual_usd': 0.0, 'reserved': {}, 'finished': False}
                               for c in roster},
                     'reservation_keys': old_keys}
            data['allocation'] = batch
            self.state.save()
        self.batch = batch
        for value in (batch['headroom_at_start'], batch['share']):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Invalid per-case allocation in the ledger')

    # ---- the arithmetic -------------------------------------------------------------------------
    def allowance(self, case):
        """What `case` may still charge, in dollars, right now."""
        cases = self.batch['cases']
        if case not in cases:
            return 0.0
        share = self.batch['share']
        spent_by_batch = sum(_charged(e) for e in cases.values())
        protected = sum(max(0.0, share - _charged(e)) for c, e in cases.items()
                        if c != case and not e.get('finished'))
        # The global cap still governs: a batch never gets more than the ledger has left.
        data = self.state.data
        global_left = self.budget.limit - data['actual_usd'] - sum(data['reserved'].values())
        return max(0.0, min(self.batch['headroom_at_start'] - spent_by_batch - protected,
                            global_left - protected))

    def finish(self, case):
        """Release `case`'s unspent share to the cases still pending."""
        entry = self.batch['cases'].get(case)
        if entry is not None and not entry.get('finished'):
            entry['finished'] = True
            self.state.save()

    def for_case(self, case):
        if case not in self.batch['cases']:
            raise ValueError('%s is not on this batch roster; it has no share' % case)
        return CaseBudget(self, case)

    def report(self, case=None):
        cases = self.batch['cases']
        out = {'roster': len(cases), 'share_usd': round(self.batch['share'], 6),
               'headroom_at_start_usd': round(self.batch['headroom_at_start'], 6),
               'uncertain_paid_calls': len(self.uncertain)}
        if case is not None:
            entry = cases.get(case, {})
            out.update(case=case, charged_usd=round(_charged(entry), 6),
                       allowance_usd=round(self.allowance(case), 6),
                       finished=bool(entry.get('finished')))
        return out


class CaseBudget:
    """One case's view of the allocator. Quacks like document_interpreter.Budget, plus the
    cached_read hook PersistentBudget has, so VisionReader.read_page uses it unchanged."""

    def __init__(self, allocator, case):
        self.allocator = allocator
        self.case = case
        self.budget = allocator.budget
        self.limit = self.budget.limit
        self.model = self.budget.model
        self.calls = 0
        self._exhausted = False
        self._key = None
        self._reservation = None

    @property
    def exhausted(self):
        """This case's share is spent, or the cumulative cap itself is."""
        return self._exhausted or bool(getattr(self.budget, 'exhausted', False))

    @property
    def entry(self):
        return self.allocator.batch['cases'][self.case]

    @property
    def spent(self):
        return _charged(self.entry)

    def price(self, input_tokens, output_tokens):
        return self.budget.price(input_tokens, output_tokens)

    def cached_read(self, key):
        """VisionReader calls this with the page key before it builds a client. A cached answer
        costs nothing; an UNCERTAIN earlier call for the same key stops here, before any request."""
        cached = self.budget.cached_read(key)
        if cached is not None:
            return cached
        if key in self.allocator.uncertain:
            raise UncertainPaidCall(
                'a paid read of this page was started on an earlier run and never settled; it is '
                'not repeated automatically (reservation %s)'
                % self.allocator.uncertain[key]['reservation'])
        self._key = key
        return None

    def check(self, input_tokens, max_output_tokens):
        worst = self.price(input_tokens, max_output_tokens)
        allowance = self.allocator.allowance(self.case)
        if worst > allowance:
            self._exhausted = True
            raise BudgetExhausted(
                'budget_exhausted: next page could cost $%.4f and %s has $%.4f left of its share; '
                'other pending cases\' shares are protected' % (worst, self.case, allowance))
        # Global reservation first (it saves the ledger), then the per-case entry and the page key,
        # saved again — both writes land before the paid call is made.
        self.budget.check(input_tokens, max_output_tokens)
        rid = self.budget.reservation
        self.entry['reserved'][rid] = worst
        self.allocator.batch['reservation_keys'][rid] = {'case': self.case, 'key': self._key}
        self.allocator.state.save()
        self._reservation = rid
        return worst

    def _settle(self, input_tokens, output_tokens):
        rid = self._reservation
        if rid is None:
            raise RuntimeError('Cannot record an unreserved API call')
        self.entry['reserved'].pop(rid, None)
        self.allocator.batch['reservation_keys'].pop(rid, None)
        self.entry['actual_usd'] = float(self.entry.get('actual_usd', 0.0)) + self.price(
            input_tokens, output_tokens)
        self._reservation = None
        self._key = None
        self.calls += 1

    def record(self, input_tokens, output_tokens):
        self._settle(input_tokens, output_tokens)
        self.budget.record(input_tokens, output_tokens)       # the one durable save
        return self.spent

    def record_read(self, input_tokens, output_tokens, key, result):
        self._settle(input_tokens, output_tokens)
        self.budget.record_read(input_tokens, output_tokens, key, result)   # one durable save
        return self.spent

    def report(self):
        return dict(self.allocator.report(self.case), model=self.model,
                    limit_usd=round(self.limit, 4), spent_usd=round(self.spent, 6),
                    calls=self.calls)
