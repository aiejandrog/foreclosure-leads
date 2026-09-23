import json
import tempfile
import unittest
from pathlib import Path
from document_backfill import State
from captcha_cost_cutoff import PaidCutoffSolver, CutoffStopped


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class Session:
    def __init__(self, results, state): self.results, self.state, self.calls = list(results), state, []
    def post(self, url, **kwargs):
        self.calls.append(url)
        assert self.state.path.exists()
        assert json.loads(self.state.path.read_text())['captcha_pending']
        result = self.results.pop(0)
        if isinstance(result, Exception): raise result
        return Response(result)


class CutoffTests(unittest.TestCase):
    def run_state(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = State(Path(directory.name) / 'ledger.json').__enter__()
        self.addCleanup(state.__exit__, None, None, None)
        return state

    def test_actual_cost_recorded_no_secrets_and_single_overrun_stops_next(self):
        state = self.run_state()
        state.data['actual_usd'] = 1.495
        session = Session([{'errorId': 0, 'taskId': 123}, {'errorId': 0, 'status': 'ready',
            'cost': '0.01000', 'solution': {'token': 'private-token'}}], state)
        solver = PaidCutoffSolver(state, 1.5, 'private-key', session=session, sleep=lambda _: None)
        self.assertEqual(solver('site', 'https://example.com'), 'private-token')
        self.assertEqual(state.data['captcha_actual_decimal'], '1.50500')
        with self.assertRaises(CutoffStopped): solver('site', 'https://example.com')
        self.assertEqual(len(session.calls), 2)
        saved = state.path.read_text()
        self.assertNotIn('private-key', saved)
        self.assertNotIn('private-token', saved)
        self.assertEqual(state.data['captcha_receipts'], [{'task_id': 123, 'cost': '0.01000'}])

    def test_remaining_below_conservative_reservation_does_not_submit(self):
        state = self.run_state(); state.data['actual_usd'] = 1.498
        session = Session([], state)
        solver = PaidCutoffSolver(state, 1.5, 'key', session=session)
        with self.assertRaises(CutoffStopped): solver('site', 'https://example.com')
        self.assertEqual(session.calls, [])

    def test_transport_ambiguity_persists_pending_and_blocks_restart(self):
        state = self.run_state(); session = Session([TimeoutError()], state)
        solver = PaidCutoffSolver(state, 1.5, 'key', session=session)
        with self.assertRaises(CutoffStopped): solver('site', 'https://example.com')
        self.assertTrue(state.data['captcha_pending'])
        with self.assertRaises(CutoffStopped):
            PaidCutoffSolver(state, 1.5, 'key', session=session)('site', 'https://example.com')
        self.assertEqual(len(session.calls), 1)

    def test_missing_cost_blocks_new_tasks_without_assuming_zero(self):
        state = self.run_state()
        session = Session([{'errorId':0, 'taskId':7}, {'errorId':0, 'status':'ready', 'solution':{'token':'x'}}], state)
        with self.assertRaises(CutoffStopped):
            PaidCutoffSolver(state, 1.5, 'key', session=session, sleep=lambda _:None)('s','https://example.com')
        self.assertEqual(state.data['captcha_pending']['task_id'], 7)
        self.assertEqual(state.data['actual_usd'], 0)
        self.assertTrue(state.data['reserved'])

    def test_cap_increase_refused_and_zero_nan_refused(self):
        state = self.run_state()
        PaidCutoffSolver(state, 1.5, 'key', session=Session([],state))
        for cap in (2, 0, float('nan')):
            with self.assertRaises(ValueError): PaidCutoffSolver(state, cap, 'key', session=Session([],state))

    def test_known_cost_without_token_is_still_charged_then_stops(self):
        state = self.run_state()
        session = Session([{'errorId':0,'taskId':8},{'errorId':0,'status':'ready','cost':'0.002','solution':{}}],state)
        with self.assertRaises(CutoffStopped):
            PaidCutoffSolver(state,1.5,'key',session=session,sleep=lambda _:None)('s','https://example.com')
        self.assertEqual(state.data['captcha_actual_decimal'],'0.002')
        self.assertTrue(state.data['captcha_halted'])

    def test_poll_timeout_retains_task_and_never_resubmits(self):
        state = self.run_state()
        session = Session([{'errorId':0,'taskId':9}] + [{'errorId':0,'status':'processing'}] * 60,state)
        solver = PaidCutoffSolver(state,1.5,'key',session=session,sleep=lambda _:None)
        with self.assertRaises(CutoffStopped): solver('s','https://example.com')
        self.assertEqual(state.data['captcha_pending']['task_id'],9)
        self.assertEqual(sum(url.endswith('createTask') for url in session.calls),1)

    def test_settlement_write_failure_prevents_another_request(self):
        state = self.run_state()
        session = Session([{'errorId':0,'taskId':10},{'errorId':0,'status':'ready','cost':'0.001','solution':{'token':'x'}}],state)
        solver = PaidCutoffSolver(state,1.5,'key',session=session,sleep=lambda _:None)
        original = state.save
        def failing_save():
            if state.data['captcha_receipts']: raise OSError('disk failure')
            original()
        state.save = failing_save
        with self.assertRaises(CutoffStopped): solver('s','https://example.com')
        with self.assertRaises(CutoffStopped): solver('s','https://example.com')
        self.assertEqual(len(session.calls),2)


if __name__ == '__main__': unittest.main()
