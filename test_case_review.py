"""Synthetic fixtures only: no homeowner data or network access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import case_review as review


class CaseReviewTests(unittest.TestCase):
    def record(self):
        return {"dockets": [{"docketDescrition": "Filing", "comments": "x" * 5000}
                            for _ in range(57)],
                "parties": [{"partyName": f"Synthetic party {i}", "leadAttName": "Synthetic Counsel"}
                            for i in range(23)]}

    def test_full_evidence_preserved_and_never_claims_read(self):
        raw = self.record()
        result = review.build_review({"TEST-CASE": raw})
        self.assertEqual(result["raw_case"], raw)
        self.assertEqual(len(result["document_tasks"]), 57)
        self.assertEqual(len(result["raw_case"]["parties"]), 23)
        self.assertEqual(len({t["task_id"] for t in result["document_tasks"]}), 57)
        self.assertTrue(all(t["status"] == "pending" and not t["document_read"]
                            for t in result["document_tasks"]))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["coverage"]["docket_pagination_verified"])
        result["raw_case"]["dockets"][0]["comments"] = "changed"
        self.assertEqual(len(raw["dockets"][0]["comments"]), 5000)

    def test_negation_and_later_order_are_unverified(self):
        raw = {"dockets": [{"comments": "Defendant is NOT deceased. No bankruptcy filed."},
                           {"comments": "Amended final judgment vacated; withdrawal of counsel"}],
               "parties": []}
        result = review.build_review(raw, "TEST")
        types = {s["type"] for s in result["review_signals"]}
        self.assertTrue({"death_or_estate", "bankruptcy", "judgment", "amendment_or_vacatur", "attorney_change"} <= types)
        self.assertTrue(all(s["status"] == "unverified_review_signal" for s in result["review_signals"]))
        self.assertEqual(result["findings"], [])

    def test_reject_malformed_or_ambiguous(self):
        for raw in ([], {}, {"error": "denied"}, {"dockets": None, "parties": []},
                    {"dockets": ["bad"], "parties": []}, {"dockets": [], "parties": {}},
                    {"a": self.record(), "b": self.record()}):
            with self.subTest(raw=type(raw)), self.assertRaises(ValueError):
                review.build_review(raw)
        with self.assertRaises(ValueError):
            review.build_review({"caseNumber": "OTHER", "dockets": [], "parties": []}, "TEST")
        with self.assertRaises(ValueError):
            review.build_review({"TEST": {"caseNumber": "OTHER", "dockets": [], "parties": []}})

    def test_all_duplicate_attachments_have_separate_pending_tasks(self):
        raw = {"dockets": [{"documents": [{"title": "Order"}, {"title": "Order"}],
                            "attachments": [{"title": "Order"}]}], "parties": []}
        result = review.build_review(raw, "TEST")
        self.assertEqual(len(result["document_tasks"]), 4)
        self.assertEqual(len({t["task_id"] for t in result["document_tasks"]}), 4)
        self.assertEqual(result["raw_case"], raw)

    def test_selected_case_and_stable_ids(self):
        payload = {"A": self.record(), "B": self.record()}
        first = review.build_review(payload, "B")
        second = review.build_review(payload, "B")
        self.assertEqual(first["source"], second["source"])
        self.assertEqual(first["document_tasks"], second["document_tasks"])
        self.assertNotEqual(first["document_tasks"][0]["task_id"], review.build_review(payload, "A")["document_tasks"][0]["task_id"])
        payload["B"]["dockets"].insert(0, {"comments": "New filing inserted by portal"})
        updated = review.build_review(payload, "B")
        self.assertNotEqual(first["document_tasks"][0]["task_id"], updated["document_tasks"][0]["task_id"])

    def test_output_boundaries_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(review.P, "DEALFLOW_DIR", folder):
            root = Path(folder)
            self.assertEqual(review.output_path("review.json"), root / "review.json")
            for value in ("../escape.json", str(root), "OneDrive/review.json"):
                with self.assertRaises(ValueError):
                    review.output_path(value)
            (root / ".git").mkdir()
            with self.assertRaises(ValueError):
                review.output_path("review.json")
            (root / ".git").rmdir()
            source = root / "input.json"
            source.write_text(json.dumps({"TEST": self.record()}), encoding="utf-8")
            args = ["--input", str(source), "--output", "review.json"]
            self.assertEqual(review.main(args), 0)
            original = (root / "review.json").read_bytes()
            with self.assertRaises(SystemExit) as error:
                review.main(args)
            self.assertEqual(error.exception.code, 2)
            self.assertEqual((root / "review.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
