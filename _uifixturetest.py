"""Synthetic UI artifact contract; no browser, live data, or network."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class UIFixtureTests(unittest.TestCase):
    def test_clean_checkout_builds_both_pages_without_private_inputs(self):
        import ui_fixture as U
        with tempfile.TemporaryDirectory() as d:
            board, call = U.build(d)
            self.assertIn('SYNTHETIC-UI-1', board.read_text())
            self.assertNotIn('__DATA__', board.read_text())
            self.assertIn('Call Mode', call.read_text())
            self.assertEqual({p.name for p in Path(d).iterdir()}, {'board.html', 'call.html'})
            self.assertTrue(all(not row['emails'] for row in U.leads()))

if __name__ == '__main__':
    unittest.main()
