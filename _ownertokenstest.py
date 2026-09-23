import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from document_backfill import State, select_cases
import run_owner_tokens as T


class TokenTests(unittest.TestCase):
    def test_cli_final_balance_checked_after_interruption_without_new_solves(self):
        events=[]
        class Solver:
            def __init__(self,*args): pass
            def finish(self):
                events.append('balance')
                return {'balance_spend_usd':None}
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            (base/'leads.json').write_text('[]')
            (base/'key').write_text('synthetic-test-key')
            with patch('captcha_cost_cutoff.PaidCutoffSolver',Solver), patch.object(T,'process',side_effect=KeyboardInterrupt), patch.object(T.TokenLadder,'close',side_effect=lambda:events.append('close')):
                with self.assertRaises(KeyboardInterrupt):
                    T.main(['--leads-file',str(base/'leads.json'),'--state',str(base/'state.json'),
                            '--key-file',str(base/'key'),'--qs-cache',str(base/'cache.json'),
                            '--token-budget','5','--captcha-max-spend','1.50'])
            self.assertEqual(events,['close','balance'])

    def test_cache_then_free_then_paid_and_no_paid_for_empty_or_broad_results(self):
        import records_liens as R
        from document_walk import NameSearcher
        for cached, free, rows, expected in [
            ('cached',None,[{}],['cached']),
            ('expired','free',[{}],['expired','browser','free']),
            (None,'free',[],['browser','free']),
            (None,'free',[{}]*500,['browser','free']),
            (None,None,None,['browser','paid']),
        ]:
            events=[]
            def records(token):
                events.append(token)
                return None if token=='expired' else rows
            def browser_token(*args):
                events.append('browser'); return free
            def paid(*args,**kwargs):
                events.append('paid'); return ('paid-token',2)
            with patch.object(R,'records_by_qs',side_effect=records), patch.object(R,'camoufox_qs',side_effect=browser_token), patch.object(NameSearcher,'_camoufox',return_value=object()):
                ladder=T.TokenLadder({'Owner':cached} if cached else {},object(),paid)
                token,hits=ladder.search_token('Owner',('Owner',''))
                self.assertEqual(events,expected)
                self.assertEqual(hits,2 if expected[-1]=='paid' else len(rows))

    def test_cli_cannot_raise_authorized_cutoff(self):
        with self.assertRaises(SystemExit):
            T.main(['--leads-file','absent','--state','absent','--token-budget','284',
                    '--captcha-max-spend','2','--allow-one-request-overrun'])

    def test_unknown_charge_remains_visible_and_stops_next_owner(self):
        with tempfile.TemporaryDirectory() as td, State(Path(td)/'state.json') as state:
            def mint(*args,**kwargs):
                state.data['captcha_pending']={'stage':'submitting','task_id':None}
                state.data['reserved']['captcha_pending']=.003
                raise RuntimeError('uncertain charge')
            result=T.process([{'case':'A','owner':'Synthetic Alpha'}, {'case':'B','owner':'Synthetic Beta'}],
                {},2,object(),state,Path(td)/'cache.json',mint=mint)
            self.assertTrue(result['captcha_pending'])
            self.assertEqual(result['captcha_reserved_usd'],.003)
            self.assertEqual(result['attempted_owners'],1)

    def test_counts_success_not_attempt_and_reuses_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            cache=Path(td)/'qs.json'
            entries=[{'case':str(i),'owner':'Synthetic '+str(i)} for i in range(3)]
            def mint(parts, tries, solver):
                return [('safe-token',3),('broad-token',500),(None,0)][int(parts[0].split()[-1])]
            with State(Path(td)/'state.json') as state, patch.object(T,'search_name_parts',side_effect=lambda n:(n,'')):
                q={}
                result=T.process(entries,q,3,object(),state,cache,mint=mint)
                self.assertEqual(result['tokens_minted'],1)
                self.assertEqual(result['cases_with_token'],1)
                self.assertEqual(len(q),1)
                self.assertEqual(result['attempted_owners'],3)
                again=T.process(entries,q,3,object(),state,cache,mint=lambda *a,**k: self.fail('must resume without remint'))
                self.assertEqual(again['tokens_minted'],1)
                self.assertEqual(json.loads(cache.read_text()),{'Synthetic 0':'safe-token'})

    def test_budget_stops_before_next_owner_and_no_vision_projection_spend(self):
        with tempfile.TemporaryDirectory() as td, State(Path(td)/'state.json') as state:
            entries=[{'case':'A','owner':'Synthetic Alpha'},{'case':'B','owner':'Synthetic Beta'}]
            result=T.process(entries,{},1,object(),state,Path(td)/'cache.json',mint=lambda *a,**k:('token',2))
            self.assertEqual(result['attempted_owners'],1)
            self.assertEqual(result['cases_with_token'],1)
            self.assertEqual(result['vision_spent_usd'],0)
            self.assertEqual(result['projected_vision_usd'],.28)

    def test_minter_accepts_capped_solver_without_calling_default_paid_path(self):
        import gen_records_qs as G
        with patch('captcha_solver.solve_turnstile',side_effect=AssertionError('uncapped solver forbidden')):
            self.assertEqual(G.mint_qs(('SYNTHETIC','OWNER'),tries=1,solver=lambda *a:None),(None,0))


if __name__=='__main__':unittest.main()
