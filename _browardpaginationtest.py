"""Broward LP pagination regression: synthetic responses only, no network or sessions."""
import ast
import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

def sweep_with(pages):
    source = Path(__file__).with_name('fl_lp').joinpath('broward.py').read_text(encoding='utf-8')
    fn = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'sweep')
    transport = Mock(side_effect=['search accepted'] + pages)
    env = dict(datetime=datetime, json=json, sys=SimpleNamespace(stderr=None),
               time=SimpleNamespace(sleep=lambda _: None),
               B=SimpleNamespace(start_session=lambda: {'booktypes': 'all'},
                                 _curl=transport, BASE='https://example.invalid'),
               _lp_code=lambda _: 'synthetic', _date=lambda _: '2099-01-01',
               SESSION_RETRY_WAITS=())
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'broward-sweep', 'exec'), env)
    return env['sweep']()

def page(count, total):
    return json.dumps({'data': [{'DirectName': 'SYNTHETIC LENDER',
                                'IndirectName': 'SYNTHETIC OWNER',
                                'InstrumentNumber': str(i)} for i in range(count)],
                       'total': total})

class PaginationTests(unittest.TestCase):
    def test_failed_later_page_is_not_a_completed_sweep(self):
        self.assertTrue(sweep_with([page(400, 401), '<html>unavailable</html>']) is None,
                        'a failed later page must mark the whole sweep incomplete')

    def test_failed_first_page_still_fails(self):
        self.assertIsNone(sweep_with(['<html>unavailable</html>']))

    def test_complete_grid_still_returns_every_row(self):
        self.assertEqual(len(sweep_with([page(400, 401), page(1, 401)])), 401)

if __name__ == '__main__':
    unittest.main()
