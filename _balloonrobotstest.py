"""F-sec-03: the PUBLISHED copy of the balloon book asks search engines not to index it.

The published copy (docs/hm-balloon-q7v3n8/index.html) is a plaintext page on the public site. The
board (tracker_template.html:3) and Call Mode (call_mode.py) both carry
<meta name="robots" content="noindex">; the book did not. Invented rows only, no network, nothing
is written: the helper is a pure string transform.
"""
import os
import tempfile
import unittest

os.environ.setdefault('DEALFLOW_DIR', tempfile.mkdtemp(prefix='balloonrobots-'))

import hardmoney_balloon as HB

ROBOTS = '<meta name="robots" content="noindex">'

ROW = ('<tr><td>2025-09-01</td><td class="r">14 mo</td><td>SYNTHETIC CAPITAL</td>'
       '<td class="r">$250,000</td><td class="r">61%</td><td><span class="v g">DSCR-STRONG</span></td>'
       '<td>ALPHA SYN LLC</td><td>1 Test St</td><td>DOE, JANE &middot; 555-0100</td></tr>')

DOC = ('<!doctype html><html><head><meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1">'
       '<title>Hard-Money Balloon Book</title>\n<style>\nbody{font:14px}\n</style></head><body>\n'
       '<h1>Hard-Money Balloon Book</h1>\n'
       '<table><thead><tr><th>Originated</th><th>Age</th><th>Lender</th><th>Loan</th><th>LTV</th>'
       '<th>Verdict</th><th>Owner (LLC)</th><th>Property</th><th>Human behind the LLC</th></tr></thead>\n'
       '<tbody>' + ROW + '</tbody></table>\n</body></html>')


class PublishedCopy(unittest.TestCase):
    def setUp(self):
        self.pub = HB._published_copy(DOC)

    def test_published_copy_carries_robots_noindex(self):
        self.assertEqual(self.pub.count(ROBOTS), 1)

    def test_robots_tag_sits_inside_head(self):
        self.assertLess(self.pub.index(ROBOTS), self.pub.index('</head>'))

    def test_human_column_is_still_stripped(self):
        self.assertNotIn('DOE, JANE', self.pub)
        self.assertNotIn('555-0100', self.pub)
        self.assertIn('<span class="mut">on file</span>', self.pub)
        self.assertNotIn('Human behind the LLC', self.pub)

    def test_corporate_columns_survive(self):
        for s in ('SYNTHETIC CAPITAL', '$250,000', 'ALPHA SYN LLC', 'DSCR-STRONG'):
            self.assertIn(s, self.pub)

    def test_helper_is_a_pure_transform(self):
        self.assertEqual(HB._published_copy(DOC), self.pub)
        self.assertNotIn(ROBOTS, DOC)

    def test_real_template_still_has_the_anchor_the_tag_hangs_on(self):
        # str.replace on a missing anchor is a silent no-op; fail here instead of on the live page.
        src = open(HB.__file__.replace('.pyc', '.py'), encoding='utf-8').read()
        self.assertIn('doc = """<!doctype html><html><head><meta charset="utf-8">', src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
