"""Cached Whitepages evidence states survive Call Mode without changing eligibility."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import call_mode as C


def evidence_leads():
    rows = []
    for county, area in [('MIAMI-DADE', '305'), ('BROWARD', '954'), ('PALM BEACH', '561')]:
        for i in range(12):
            rows.append(dict(case=f'SYNTHETIC-WP-STATE-{area}-{i:02}', county=county,
                             st='LP', days=9999, owners='EXAMPLE, TAYLOR',
                             oname='Taylor Example', addr=f'{100+i} Example Street, Example City, FL',
                             phones=[area + str(5550100+i*3+j) for j in range(3)],
                             phsrc=['st']*3, phdnc=[False, False, True],
                             phtype=['mobile']*3,
                             wpKey=('mismatch', 'unverified', 'none', 'ok')[i % 4]))
    return rows


def call_rows(leads):
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'DEALFLOW_DIR': temp}):
        return C.call_rows(leads)[0]


def baked_evidence_leads():
    """Execute only the production cache merge against fabricated private-cache shapes."""
    import phone_src as PS
    from wp_contacts import wp_parcel_verdict
    source = Path(__file__).with_name('foreclosure_leads.py').read_text(encoding='utf-8')
    block = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Try) and n.body
                 and isinstance(n.body[0], ast.Assign)
                 and isinstance(n.body[0].targets[0], ast.Name)
                 and n.body[0].targets[0].id == '_wp')
    leads, cache = evidence_leads(), {}
    for lead in leads:
        state = lead.pop('wpKey')
        lead['folio'] = '' if state == 'unverified' else '9999999999991'
        owners = [{'name':'Taylor Example', 'phones':[]}] if state == 'ok' else []
        cache[lead['case']] = {'result':{
            'apn':'9999999999992' if state == 'mismatch' else '9999999999991',
            'ownership_info':{'person_owners':owners}}}
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'whitepages_lookup.json'
        path.write_text(json.dumps(cache), encoding='utf-8')
        env = dict(os=os, json=json, HERE=temp, _wpf=str(path), _wp_ids={},
                   _wp_norm_id=lambda x:x, wp_parcel_verdict=wp_parcel_verdict,
                   slim=leads, fit_phones=PS.fit_phones, MAX_PHONES=PS.MAX_PHONES,
                   PHSRC_TRACE=PS.PHSRC_TRACE, PHSRC_HOUSEHOLD=PS.PHSRC_HOUSEHOLD,
                   PHSRC_NAME=PS.PHSRC_NAME, PHSRC_WP=PS.PHSRC_WP)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            exec(compile(ast.Module(body=[block], type_ignores=[]), 'wp-status-bake', 'exec'), env)
        if 'skipped' in log.getvalue():
            raise AssertionError(log.getvalue())
    return leads


def numbers_html(row):
    source = C._PAGE
    functions = source[source.index('function _srcChip('):source.index('\nfunction _wireNumList(')]
    script = ('var notes={},phIdx=0;function badSet(){return{}};'
              'function fmt(x){return x};function _wpChip(){return ""};' + functions
              + '\nconsole.log(JSON.stringify(_numList(' + json.dumps(row) + ')));')
    result = subprocess.run([os.environ.get('NODE_BIN', 'node'), '-e', script],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


class WhitepagesEvidenceStatusTests(unittest.TestCase):
    def lead(self, **extra):
        lead = evidence_leads()[0]
        lead.update(extra)
        return lead

    def row(self, **extra):
        return call_rows([self.lead(**extra)])[0]

    def test_parcel_rejection_is_not_reported_as_unchecked(self):
        html = numbers_html(self.row(wpKey='mismatch'))
        self.assertIn('parcel mismatch', html.lower())
        self.assertNotIn('Whitepages not checked', html)

    def test_unverified_and_checked_miss_have_distinct_reasons(self):
        for state, reason in [('unverified', 'parcel not verified'),
                              ('none', 'no accepted contacts'),
                              ('ok', 'no accepted phone evidence')]:
            with self.subTest(state=state):
                html = numbers_html(self.row(wpKey=state))
                self.assertIn(reason, html.lower())
                self.assertNotIn('Whitepages not checked', html)

    def test_unknown_or_missing_state_keeps_unchecked_label(self):
        for state in (None, '', 'unexpected'):
            with self.subTest(state=state):
                row = self.row(wpKey=state)
                self.assertNotIn('ws', row)
                self.assertIn('Whitepages not checked', numbers_html(row))

    def test_allowlisted_status_does_not_change_number_metadata(self):
        baseline = self.row(wpKey=None)
        for state in ('ok', 'none', 'unverified', 'mismatch'):
            with self.subTest(state=state):
                row = self.row(wpKey=state)
                self.assertEqual(row.get('ws'), state)
                self.assertEqual({k:v for k,v in row.items() if k != 'ws'}, baseline)

    def test_one_phone_still_explains_rejected_evidence(self):
        row = self.row(phones=['3055550101'], phsrc=['st'], phdnc=[False], phtype=['mobile'])
        self.assertIn('parcel mismatch', numbers_html(row).lower())

    def test_accepted_number_evidence_still_shows_confirmation_count(self):
        row = self.row(wpKey='ok', wpAllPhones=[{'n':'3055550101', 'source':'property'}])
        html = numbers_html(row)
        self.assertIn('1 confirmed', html)
        self.assertNotIn('no accepted phone evidence', html)

    def test_36_payloads_keep_rejection_reason_and_dnc_separate(self):
        leads = baked_evidence_leads()
        rows = call_rows(leads)
        self.assertEqual(len(rows), 36)
        by_case = {r['c']:r for r in rows}
        for lead in leads:
            with self.subTest(case=lead['case']):
                row = by_case[lead['case']]
                self.assertEqual(row.get('ws'), lead['wpKey'])
                self.assertEqual(row['p'], lead['phones'][:2])
                self.assertEqual(row['dn'][0][0], lead['phones'][2])


if __name__ == '__main__':
    unittest.main()
