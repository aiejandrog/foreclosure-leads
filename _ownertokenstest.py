import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from document_backfill import State, select_cases
import run_owner_tokens as T


class TokenTests(unittest.TestCase):
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
