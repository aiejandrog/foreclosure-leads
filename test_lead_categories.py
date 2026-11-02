import unittest

import lead_categories as C


def lead(**kw):
    base = {
        "case": "CACE-26-000001", "county": "BROWARD", "addr": "1 Main St",
        "days": 10, "equity": 250000, "equity_pct": 45, "equity_state": "fact",
        "owners": "DOE, JANE", "phones": ["9545550101"], "equity_source": "title",
        "equity_checked_at": "2026-11-01",
    }
    base.update(kw)
    return base


class CategoryTests(unittest.TestCase):
    def test_gold_is_immediate_high_equity_deadline(self):
        out = C.classify_lead(lead(vac=True))
        self.assertEqual(out["bucket"], "GOLD")
        self.assertEqual(out["next_action"], "closer_circle_drain")
        self.assertEqual(out["evidence"], {"source": "title", "observed_at": "2026-11-01"})

    def test_silver_is_usable_equity_within_thirty_days(self):
        self.assertEqual(C.classify_lead(lead(days=30, equity=100000))["bucket"], "SILVER")

    def test_silver_secondary_distress_can_prioritize_longer_deadline(self):
        out = C.classify_lead(lead(days=75, equity=150000, vacant=True))
        self.assertEqual(out["bucket"], "SILVER")
        self.assertIn("secondary_distress_factor", out["reasons"])

    def test_bronze_is_valid_lower_priority_nurture(self):
        self.assertEqual(C.classify_lead(lead(days=90, equity=50000))["bucket"], "BRONZE")

    def test_hard_hold_always_excludes(self):
        out = C.classify_lead(lead(dnc=True))
        self.assertEqual(out["bucket"], "EXCLUDED")
        self.assertEqual(out["next_action"], "no_contact")

    def test_all_existing_hold_aliases_cannot_reach_gold(self):
        for key, value in (
            ("ddhold", True), ("sibclaimed", True), ("lpDismissed", True),
            ("unanchored", True), ("bkWhy", "bankruptcy review"),
        ):
            with self.subTest(key=key):
                self.assertEqual(C.classify_lead(lead(**{key: value}))["bucket"], "EXCLUDED")

    def test_false_or_empty_hold_aliases_do_not_hold(self):
        out = C.classify_lead(lead(ddhold=False, sibclaimed="", lpDismissed=0,
                                   unanchored="false", bkWhy=""))
        self.assertEqual(out["bucket"], "GOLD")

    def test_unknown_equity_is_not_promoted_by_a_large_number(self):
        out = C.classify_lead(lead(equity_state="unknown", judgment_unknown=True))
        self.assertEqual(out["bucket"], "RESEARCH_HOLD")

    def test_missing_identity_is_research_hold(self):
        out = C.classify_lead(lead(case="", addr=""))
        self.assertEqual(out["bucket"], "RESEARCH_HOLD")
        self.assertIn("missing_case", out["reasons"])

    def test_output_is_non_mutating_and_has_rule_metadata(self):
        row = lead()
        before = dict(row)
        out = C.classify_lead(row)
        self.assertEqual(row, before)
        self.assertEqual(out["rule_version"], C.RULE_VERSION)


if __name__ == "__main__":
    unittest.main()
