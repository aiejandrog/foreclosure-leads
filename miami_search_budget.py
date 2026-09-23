"""Private, cumulative CAPTCHA accounting and fail-closed Miami name searches.

The existing solver's $0.003 is an estimate, not a provider-enforced ceiling.
2Captcha getTaskResult reports cost *after* the task; createTask documents no
maximum-price parameter (official API docs checked 2026-09-23). Consequently
this adapter preserves cached-token and Camoufox searches but does not invoke
the unbounded paid solver. Each blocked name remains an explicit unknown gap.
No provider token, key, or homeowner name is stored in the money ledger.
"""
from decimal import Decimal, InvalidOperation
import uuid

from document_backfill import State
from document_walk import NameSearcher


class CaptchaCapReached(RuntimeError):
    pass


def search_name_parts(name):
    """Honor explicit LAST, FIRST without changing the legacy shared parser.

    Entity names keep commas and suffixes intact. For names without an explicit
    comma, the legacy FIRST LAST fallback remains a search heuristic: it cannot
    establish where an unmarked compound surname starts.
    """
    import records_liens as R
    clean = ' '.join(str(name or '').strip().split())
    if R.COMPANY_RE.search(clean) or R.COMPANY_SUFFIX_RE.search(clean):
        return (clean, '')
    if ',' in clean:
        surname, given = (part.strip() for part in clean.split(',', 1))
        if not surname or not given or ',' in given:
            return None
        return (surname, given)
    return R.split_owner(clean)


def _money(value, positive=False):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('A finite CAPTCHA dollar cap is required') from None
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError('A finite positive CAPTCHA dollar cap is required')
    return amount


class CaptchaBudget:
    """Hold the exclusive private ledger lease across both case searches.

    Reservations support a future provider-enforced ceiling. They are NOT a
    license to turn an assumed unit price into a claim of a hard cap. An
    interrupted request retains its entire reservation until a confirmed cost
    is available. Reopening this ledger may lower, but never raise, its cap.
    """
    def __init__(self, path, captcha_max_spend):
        self.limit = _money(captcha_max_spend, positive=True)
        self.state = State(path)

    def __enter__(self):
        self.state.__enter__()
        try:
            old = self.state.data.get('captcha_max_spend')
            if old is not None and self.limit > _money(old, positive=True):
                raise ValueError('Cannot increase this run CAPTCHA cap')
            self.state.data['captcha_max_spend'] = str(self.limit)
            self.state.save()
        except Exception:
            self.state.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        return self.state.__exit__(*args)

    @property
    def spent(self):
        return (_money(self.state.data['actual_usd']) +
                sum((_money(v) for v in self.state.data['reserved'].values()), Decimal(0)))

    def reserve(self, enforced_max_cost):
        amount = _money(enforced_max_cost, positive=True)
        if self.state.data.get('provider_ceiling_violated'):
            raise CaptchaCapReached('Provider ceiling violation; paid work is stopped')
        if self.spent + amount > self.limit:
            raise CaptchaCapReached('CAPTCHA cap reached; search remains unknown')
        ticket = uuid.uuid4().hex
        self.state.data['reserved'][ticket] = float(amount)
        self.state.save()  # Durable BEFORE any caller can submit a paid task.
        return ticket

    def settle(self, ticket, actual_cost):
        actual = _money(actual_cost)
        reserved = _money(self.state.data['reserved'][ticket])
        if actual > reserved:
            # A violated provider ceiling is not silently accepted/refunded.
            self.state.data['actual_usd'] = float(_money(self.state.data['actual_usd']) + actual)
            del self.state.data['reserved'][ticket]
            self.state.data['provider_ceiling_violated'] = True
            self.state.save()
            raise CaptchaCapReached('Provider cost exceeded its enforced ceiling')
        self.state.data['actual_usd'] = float(_money(self.state.data['actual_usd']) + actual)
        del self.state.data['reserved'][ticket]
        self.state.save()

    def report(self):
        return {'cap_usd': float(self.limit),
                'actual_usd': float(_money(self.state.data['actual_usd'])),
                'reserved_usd': float(sum((_money(v) for v in self.state.data['reserved'].values()), Decimal(0))),
                'paid_route': 'paid_cap_unenforceable'}


class CappedNameSearcher(NameSearcher):
    """Existing cache/browser ladder with a deliberately blocked unsafe last rung."""
    def __init__(self, budget, qs_cache=None, use_camoufox=True):
        if not isinstance(budget, CaptchaBudget):
            raise ValueError('An explicit shared CaptchaBudget is required')
        # Fail before creating a browser if caller did not enter the lease.
        budget.report()
        super().__init__(qs_cache=qs_cache, use_camoufox=use_camoufox)
        self.budget = budget
        self.gaps = []
        self.raw_results = {}
        self.routes = {}

    def search(self, name):
        if name in self.qs_cache:
            try:
                models = self.R.records_by_qs(self.qs_cache[name])
            except Exception:
                models = None
            if models is not None:
                self.raw_results[name] = models
                self.routes[name] = 'cached_qs'
                return models
        parts = search_name_parts(name)
        if not parts:
            self.gaps.append({'name': name, 'status': 'unknown', 'reason': 'name_not_searchable'})
            return None
        try:
            browser = self._camoufox()
            token = self.R.camoufox_qs(browser, parts) if browser is not None else None
            models = self.R.records_by_qs(token) if token else None
        except Exception:
            models = None
        if models is not None:
            self.qs_cache[name] = token
            self.spent_free += 1
            self.raw_results[name] = models
            self.routes[name] = 'camoufox'
            return models
        reason = ('captcha_cap_reached' if self.budget.spent >= self.budget.limit
                  else 'paid_cap_unenforceable')
        self.gaps.append({'name': name, 'status': 'unknown', 'reason': reason})
        self.routes[name] = reason
        return None
