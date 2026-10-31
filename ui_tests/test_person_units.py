"""Production Person bake -> encrypted Call Mode -> Chromium, with no outbound requests."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import test_numbers_card as base
from test_wp_person_units import baked_unit_leads


class PersonUnitsUI(base.NumbersCardUI):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'DEALFLOW_DIR': data}):
            import call_mode as C
            import foreclosure_leads as F
            rows, total = C.call_rows(baked_unit_leads())
        payload = F._encrypt_multi(json.dumps(rows), [('Synthetic', base.FIXTURE_CODE)])
        template = (Path(C.HERE) / 'tracker_template.html').read_text(encoding='utf-8')
        html = C.build_html(rows, total, payload, dt.date.today().isoformat(),
                            'synthetic-unit-test', 'synthetic-unit-test',
                            funnel_js=C.extract_funnel_js(template),
                            text_js=C.extract_text_js(template),
                            histcov=json.dumps({'ledgers_ok': True, 'why': '', 'capped': False,
                                                'total': total, 'shipped': len(rows)}))
        (Path(cls.tmp.name) / 'call.html').write_text(html, encoding='utf-8')

    def unlock(self):
        self.page.goto(self.base + '/call.html')
        self.page.locator('#code').fill(base.FIXTURE_CODE)
        self.page.locator('#go').click()
        self.page.wait_for_function('ROWS && ROWS.length === 36')
        self.page.evaluate("lane='lp'; i=0; render()")
        self.page.locator('#dial').wait_for()
        self.page.wait_for_selector('.numlist')

    def test_attribution_and_navigation_across_all_36_leads(self):
        seen = set()
        for _ in range(36):
            row = self.page.evaluate('({c:cur.c,p:cur.p,pw:cur.pw,ps:cur.ps,dn:cur.dn})')
            self.assertNotIn(row['c'], seen)
            seen.add(row['c'])
            self.assertEqual(row['pw'], 'o-n')
            self.assertEqual(row['ps'], ['', '', 'nm'])
            self.assertEqual(self.page.locator('.numlist button.numrow').count(), 3)
            for k in range(3):
                self.page.locator('.numlist button.numrow').nth(k).click()
                self.assertEqual(base.digits(self.page.locator('#dial').inner_text()), row['p'][k])
                self.assertNotEqual(row['p'][k], row['dn'][0][0])
            text = self.page.locator('.numlist button.numrow').nth(2).inner_text()
            self.assertIn('name only', text.lower())
            # View-only Back/Next never invokes the dial button or logs an outcome.
            if len(seen) < 36:
                self.page.evaluate('navNext()')
                self.page.evaluate('navBack()')
                self.assertEqual(self.page.evaluate('cur.c'), row['c'])
            self.page.evaluate('navNext()')
        self.assertEqual(len(seen), 36)
        self.assertIn('Queue clear', self.page.locator('#app').inner_text())
        self.assertEqual(self.page.evaluate('Object.keys(notes).length'), 0)
        self.assertEqual(self.errors, [])
