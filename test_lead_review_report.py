import unittest
from datetime import date

import lead_review_report as R


class ReviewReportTests(unittest.TestCase):
    def base(self):
        return {
            "case": "CACE-26-000001", "county": "BROWARD", "addr": "1 Main St",
            "days": 10, "equity": 250000, "equity_pct": 45, "equity_state": "fact",
            "owners": "DOE, JANE", "phones": ["9545550101"],
            "equity_source": "title", "equity_checked_at": "2026-11-02",
        }

    def test_combines_category_and_followup_metadata(self):
        row = self.base()
        notes = {"CACE-26-000001": {"status": "Contacted", "owner": "LM",
                                    "nextStep": {"date": "2026-11-03", "what": "call"}}}
        out = R.build_report([row], notes, date(2026, 11, 2))
        item = out["items"][0]
        self.assertEqual(item["bucket"], "GOLD")
        self.assertEqual(item["owner"], "LM")
        self.assertEqual(item["next_date"], "2026-11-03")
        self.assertEqual(item["evidence"]["category"]["source"], "title")
        self.assertIn("category", item["rule_versions"])
        self.assertEqual(item["category_rule_version"], "lead-category-v1")
        self.assertEqual(item["followup_rule_version"], "shadow-followup-v1")
        self.assertEqual(item["followup_generated_at"], "2026-11-02")
        self.assertIn("sha256", item["followup_source_snapshot"])
        self.assertTrue(item["review_only"])
        self.assertFalse(item["outreach_eligible"])

    def test_hold_is_visible_and_not_reclassified_as_actionable(self):
        row = self.base()
        row["ddhold"] = True
        out = R.build_report([row], {}, date(2026, 11, 2))
        item = out["items"][0]
        self.assertEqual(item["bucket"], "EXCLUDED")
        self.assertTrue(item["held"])
        self.assertEqual(out["held"], 1)

    def test_report_does_not_mutate_inputs(self):
        row = self.base()
        before = dict(row)
        notes = {row["case"]: {"status": "New"}}
        R.build_report([row], notes, date(2026, 11, 2))
        self.assertEqual(row, before)
        self.assertEqual(notes[row["case"]]["status"], "New")

    def test_serialized_report_keeps_review_only_contract(self):
        import json
        payload = json.loads(R.serialize_review([self.base()], {}, date(2026, 11, 2)))
        self.assertEqual(payload["rule_version"], "lead-review-report-v1")
        self.assertTrue(payload["review_only"])
        self.assertFalse(payload["outreach_eligible"])


if __name__ == "__main__":
    unittest.main()
