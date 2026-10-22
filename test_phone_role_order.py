"""Synthetic owner-first regressions; no network or contact policy changes."""
import unittest
from pathlib import Path
import ast
import contextlib
import io
import phone_rank as P


class RoleOrderTests(unittest.TestCase):
    def test_owner_beats_household_despite_voip_and_position(self):
        household = {'number': '3055550100', 'type': 'mobile', 'carrier': 'T-Mobile', 'src': 'hh'}
        owner = {'number': '3055550101', 'type': 'landline', 'carrier': 'Twilio', 'src': 'wp'}
        self.assertEqual(P.best([household, owner])['number'], owner['number'])

    def test_roles_then_quality(self):
        phones = [{'number': str(i), 'type': typ, 'carrier': 'T-Mobile', 'src': src}
                  for i, (src, typ) in enumerate([('nm', 'mobile'), ('hh', 'mobile'),
                                                  ('wp', 'landline'), ('st', 'mobile')])]
        self.assertEqual([r['number'] for r in P.rank(phones)[0]], ['3', '2', '1', '0'])

    def test_dnc_and_agent_stay_unavailable_as_best(self):
        self.assertIsNone(P.best([{'number': '1', 'src': 'wp', 'dnc': True},
                                 {'number': '2', 'src': 'ag'}]))

    def test_malformed_records_are_blocked_without_crashing(self):
        ok, blocked = P.rank([None, '3055550100'])
        self.assertEqual(ok, [])
        self.assertEqual(len(blocked), 2)

    def test_mixed_contact_bake_does_not_reuse_stale_best(self):
        source = Path(__file__).with_name('foreclosure_leads.py').read_text(encoding='utf-8')
        # Execute the bake's real final rank pass on a row that previously looked fully ranked.
        tree = ast.parse(source)
        block = next(n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.body
                     and isinstance(n.body[0], ast.Import)
                     and n.body[0].names[0].name == 'phone_rank')
        row = {'phones': ['3055550100', '3055550101'], 'phtype': ['mobile', 'landline'],
               'phsrc': ['hh', 'wp'], 'phdnc': [False, False],
               'phrank': ['ok', 'last resort'], 'phbest': 0}
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(ast.Module(body=[block], type_ignores=[]), 'rank-bake', 'exec'),
                 {'slim': [row], 'PHSRC_TRACE': 'st'})
        self.assertEqual(row['phbest'], 1)


if __name__ == '__main__':
    unittest.main()
