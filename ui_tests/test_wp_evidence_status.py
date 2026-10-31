"""Production cached evidence -> Call Mode payload -> isolated real Chromium card."""
import datetime as dt
import json
from pathlib import Path

import test_numbers_card as base
from test_call_wp_status import baked_evidence_leads, call_rows


class WhitepagesEvidenceUI(base.NumbersCardUI):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import call_mode as C
        import foreclosure_leads as F
        rows = call_rows(baked_evidence_leads())
        payload = F._encrypt_multi(json.dumps(rows), [('Synthetic', base.FIXTURE_CODE)])
        template = (Path(C.HERE) / 'tracker_template.html').read_text(encoding='utf-8')
        html = C.build_html(rows, len(rows), payload, dt.date.today().isoformat(),
                            'synthetic-wp-status', 'synthetic-wp-status',
                            funnel_js=C.extract_funnel_js(template),
                            text_js=C.extract_text_js(template),
                            histcov=json.dumps({'ledgers_ok':True, 'why':'', 'capped':False,
                                                'total':len(rows), 'shipped':len(rows)}))
        (Path(cls.tmp.name) / 'call.html').write_text(html, encoding='utf-8')

    def unlock(self):
        self.page.goto(self.base + '/call.html')
        self.page.locator('#code').fill(base.FIXTURE_CODE)
        self.page.locator('#go').click()
        self.page.wait_for_function('ROWS && ROWS.length === 36')
        self.page.evaluate("lane='lp';i=0;render()")
        self.page.locator('#dial').wait_for()

    def test_cached_reasons_remain_visible_across_36_leads(self):
        expected = {'mismatch':'parcel mismatch', 'unverified':'parcel not verified',
                    'none':'no accepted contacts', 'ok':'no accepted phone evidence'}
        seen = set()
        for _ in range(36):
            row = self.page.evaluate('({c:cur.c,p:cur.p,dn:cur.dn,ws:cur.ws})')
            self.assertNotIn(row['c'], seen)
            seen.add(row['c'])
            text = self.page.locator('.numlist').inner_text().lower()
            self.assertIn(expected[row['ws']], text)
            self.assertNotIn('whitepages not checked', text)
            self.assertIn('cache', text)
            for k in range(2):
                self.page.locator('.numlist button.numrow').nth(k).click()
                self.assertEqual(base.digits(self.page.locator('#dial').inner_text()), row['p'][k])
                self.assertNotEqual(row['p'][k], row['dn'][0][0])
            if len(seen) < 36:
                self.page.evaluate('navNext();navBack()')
                self.assertEqual(self.page.evaluate('cur.c'), row['c'])
            self.page.evaluate('navNext()')
        self.assertEqual(len(seen), 36)
        self.assertIn('Queue clear', self.page.locator('#app').inner_text())
        self.assertEqual(self.page.evaluate('Object.keys(notes).length'), 0)
        self.page.evaluate("lane='urgent';i=0;render()")
        self.assertEqual(self.page.evaluate('pool().length'), 0)
        self.assertEqual(self.page.locator('#dial').count(), 0)
        self.page.evaluate("lane='lp';i=0;render()")
        self.assertEqual(self.page.evaluate('pool().length'), 36)
        self.page.locator('#dial').wait_for()
        self.assertEqual(self.errors, [])
