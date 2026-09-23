"""Durable 300-submission ceiling and real account-balance spend checks.

Balance change is account-wide, so concurrent spending/refunds affect the report.
Unknown task costs retain their pending marker; no estimated dollar reservation.
"""
from decimal import Decimal, InvalidOperation
import threading
import time
from urllib.parse import urlsplit


class CutoffStopped(RuntimeError):
    """Caller must halt the paid run, never retry this error as a fresh task."""


def _money(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid cost value') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid cost value')
    return result


class PaidCutoffSolver:
    max_paid_attempts = 300

    def __init__(self, state, limit, key, session=None, sleep=time.sleep):
        if not getattr(state, 'lock', None) or state.lock.closed:
            raise ValueError('An entered exclusive State is required')
        self.state, self.limit = state, _money(limit)
        if self.limit <= 0 or self.limit > Decimal('1.50'):
            raise ValueError('Cutoff must be positive and at most 1.50')
        prior = state.data.get('captcha_cutoff_decimal')
        if prior is not None and self.limit > _money(prior):
            raise ValueError('Existing cutoff cannot be raised')
        if not isinstance(key, str) or not key.strip():
            raise ValueError('API credential missing')
        if session is None:
            import requests
            session = requests.Session()
            # Explicitly disable retries, including user-installed adapters.
            session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
        self.session, self.key, self.sleep = session, key, sleep
        self.guard = threading.Lock()
        state.data['captcha_cutoff_decimal'] = str(self.limit)
        state.data.setdefault('captcha_actual_decimal', str(_money(state.data['actual_usd'])))
        state.data.setdefault('captcha_receipts', [])
        derived = len(state.data['captcha_receipts']) + int(bool(state.data.get('captcha_pending')))
        count = state.data.get('captcha_paid_attempts', derived)
        if type(count) is not int or count < 0:
            raise ValueError('Invalid paid attempt count')
        state.data['captcha_paid_attempts'] = max(count, derived)
        state.save()

    def _balance(self):
        data = self.state.data
        baseline = data.get('captcha_balance_before_decimal')
        if baseline is None and (data['captcha_paid_attempts'] or _money(data['captcha_actual_decimal']) > 0):
            raise CutoffStopped('Historical paid run has no starting balance; cannot invent a baseline')
        try:
            result = self._post('getBalance', {})
            if result.get('errorId') != 0 or 'balance' not in result:
                raise ValueError('Invalid balance response')
            balance = _money(result['balance'])
        except (CutoffStopped, ValueError):
            data['captcha_halted'] = 'Account balance unavailable; paid work stopped'
            self.state.save()
            raise CutoffStopped(data['captcha_halted']) from None
        if baseline is None:
            baseline = str(balance)
            data['captcha_balance_before_decimal'] = baseline
        debit = _money(baseline) - balance
        data['captcha_balance_after_decimal'] = str(balance)
        data['captcha_balance_debit_decimal'] = str(debit)
        data['captcha_final_balance_unsettled'] = bool(data.get('captcha_pending'))
        if debit > self.limit:
            data['captcha_halted'] = 'Account balance drop exceeds approved cutoff'
        self.state.save()
        if debit > self.limit:
            raise CutoffStopped(data['captcha_halted'])
        return {'balance_before_usd': float(_money(baseline)),
                'balance_after_usd': float(balance), 'balance_spend_usd': float(debit),
                'paid_attempts': data['captcha_paid_attempts'],
                'final_balance_unsettled': data['captcha_final_balance_unsettled']}

    def finish(self):
        """Read and persist final balance, including when called after interruption."""
        if not self.guard.acquire(blocking=False):
            raise CutoffStopped('Cannot finalize while a paid request is running')
        try:
            return self._balance()
        finally:
            self.guard.release()

    def _post(self, method, payload):
        try:
            response = self.session.post('https://api.2captcha.com/' + method,
                json=dict(payload, clientKey=self.key), timeout=30, allow_redirects=False)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError('Invalid response')
            return result
        except Exception:
            # Never expose transport messages containing request bodies or credentials.
            raise CutoffStopped('Provider response uncertain; pending charge retained, run stopped') from None

    def __call__(self, site_key, page_url):
        parsed = urlsplit(page_url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('A credential-free HTTPS page URL is required')
        if not isinstance(site_key, str) or not site_key.strip():
            raise ValueError('Site key required')
        if not self.guard.acquire(blocking=False):
            raise CutoffStopped('Only one paid request may run at a time')
        try:
            return self._solve(site_key, page_url)
        finally:
            self.guard.release()

    def _solve(self, site_key, page_url):
        data = self.state.data
        if data.get('captcha_pending') or data.get('captcha_halted') or data.get('reserved'):
            raise CutoffStopped('Unresolved prior request or stop marker; no new paid task permitted')
        actual = _money(data['captcha_actual_decimal'])
        if data['captcha_paid_attempts'] >= self.max_paid_attempts:
            raise CutoffStopped('Hard ceiling of 300 paid submissions reached')
        self._balance()
        data['captcha_pending'] = {'stage': 'submitting', 'task_id': None}
        data['captcha_paid_attempts'] += 1
        self.state.save()  # MUST be durable before createTask can charge.
        created = self._post('createTask', {'task': {'type': 'TurnstileTaskProxyless',
                                                 'websiteURL': page_url, 'websiteKey': site_key}})
        task_id = created.get('taskId')
        if created.get('errorId') != 0 or type(task_id) is not int or task_id <= 0:
            raise CutoffStopped('Provider did not establish a task; unknown-charge marker retained')
        data['captcha_pending'] = {'stage': 'polling', 'task_id': task_id}
        self.state.save()
        for _ in range(60):
            self.sleep(5)
            result = self._post('getTaskResult', {'taskId': task_id})
            if result.get('errorId') != 0:
                raise CutoffStopped('Provider task error; pending charge requires reconciliation')
            if result.get('status') == 'processing':
                continue
            if result.get('status') != 'ready' or 'cost' not in result:
                raise CutoffStopped('Final cost unavailable; pending charge retained')
            try:
                cost = _money(result['cost'])
            except ValueError:
                raise CutoffStopped('Final cost invalid; pending charge retained') from None
            actual += cost
            data['captcha_actual_decimal'] = str(actual)
            data['actual_usd'] = float(actual)
            data['captcha_receipts'].append({'task_id': task_id, 'cost': str(cost)})
            data['captcha_pending'] = None
            data['reserved'].pop('captcha_pending', None)
            token = (result.get('solution') or {}).get('token') if isinstance(result.get('solution'), dict) else None
            if not isinstance(token, str) or not token:
                data['captcha_halted'] = 'Completed task had no token; actual cost recorded'
            try:
                self.state.save()  # Settle known cost before releasing token to caller.
            except Exception:
                data['captcha_halted'] = 'Cost settlement could not be persisted; stop for reconciliation'
                raise CutoffStopped(data['captcha_halted']) from None
            if data.get('captcha_halted'):
                raise CutoffStopped(data['captcha_halted'])
            self._balance()
            return token
        raise CutoffStopped('Polling deadline reached; pending charge retained')
