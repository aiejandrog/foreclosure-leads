import unittest
from datetime import date

import followup_shadow as F


TODAY = date(2026, 11, 1)


class FollowupShadowTests(unittest.TestCase):
    def row(self, **kw):
        out = {"case": "C-1", "county": "BROWARD", "auction": "11/20/2026", "days": 19}
        out.update(kw)
        return out

    def test_dnc_always_holds(self):
        got = F.classify(self.row(saleBkAct=True), {"status": "DO NOT CONTACT"}, TODAY)
        self.assertEqual((got["action"], got["reason"]), ("hold", "do_not_contact"))

    def test_stay_beats_urgency(self):
        got = F.classify(self.row(saleBkAct=True, auction="11/05/2026"), {}, TODAY)
        self.assertEqual(got["reason"], "bankruptcy_stay")
        self.assertFalse(got["action"] == "prioritize_follow_up")

    def test_urgent_lane_uses_auction_not_stale_days(self):
        got = F.classify(self.row(days=90, auction="11/10/2026"), {"status": "Contacted"}, TODAY)
        self.assertTrue(got["urgent_14d"])
        self.assertEqual(got["action"], "prioritize_follow_up")

    def test_missing_next_step_is_actionable(self):
        got = F.classify(self.row(), {"status": "Contacted"}, TODAY)
        self.assertEqual(got["action"], "assign_next_step")

    def test_overdue_next_step(self):
        got = F.classify(self.row(), {"status": "Appointment", "nextStep": {"date": "2026-10-30", "what": "call"}}, TODAY)
        self.assertEqual(got["action"], "complete_overdue_next_step")

    def test_unverified_appointment_requires_equity_review(self):
        got = F.classify(self.row(), {"status": "Appointment", "apptUnverified": "2026-10-31"}, TODAY)
        self.assertEqual(got["action"], "verify_equity")

    def test_transition_does_not_clear_dnc(self):
        got = F.transition({"status": "DO NOT CONTACT"}, "Contacted")
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "do_not_contact")

    def test_transition_rejects_invalid(self):
        self.assertFalse(F.transition({}, "Scheduled by automation")["ok"])

    def test_gate_aliases_hold_in_shadow(self):
        for field in ("optout", "never_contact", "contact_hold", "suppressed", "rfHold", "stay", "bk"):
            row = self.row(**{field: True})
            got = F.classify(row, {"status": "Contacted"}, TODAY)
            self.assertEqual(got["action"], "hold", field)

    def test_transition_cannot_clear_row_gate(self):
        got = F.transition({"status": "Contacted"}, "Appointment", {"rfHold": True})
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "bankruptcy_stay")

    def test_metadata_is_reproducible(self):
        got = F.classify(self.row(), {"status": "Contacted"}, TODAY)
        self.assertEqual(got["rule_version"], "shadow-followup-v1")
        self.assertEqual(got["generated_at"], "2026-11-01")
        self.assertEqual(got["source_snapshot"]["fields"]["row"]["auction"], "11/20/2026")
        self.assertEqual(len(got["source_snapshot"]["sha256"]), 64)

    def test_every_gate_alias_blocks_transition(self):
        for field in ("saleBkAct", "bk", "bkWhy", "stay", "rfHold", "ddhold", "unanchored",
                      "contact_hold", "sibclaimed", "lpDismissed", "suppressed"):
            got = F.transition({"status": "Contacted"}, "Appointment", {field: True})
            self.assertFalse(got["ok"], field)

    def test_every_row_gate_alias_is_visible_and_changes_snapshot(self):
        base = self.row()
        original = F.classify(base, {"status": "Contacted"}, TODAY)["source_snapshot"]
        for field in ("dnc", "optout", "never_contact", "suppressed", "suppression", "bk",
                      "clerk_stale", "source_stale", "stale", "judgment_unknown", "bkdeg"):
            changed = dict(base)
            changed[field] = True
            snap = F.classify(changed, {"status": "Contacted"}, TODAY)["source_snapshot"]
            self.assertIn(field, snap["fields"]["row"], field)
            self.assertNotEqual(snap["sha256"], original["sha256"], field)

    def test_status_and_note_gate_fields_are_snapshot_bound(self):
        row = self.row()
        base = F.classify(row, {"status": "Contacted"}, TODAY)["source_snapshot"]["sha256"]
        for note in ({"status": "DO NOT CONTACT"}, {"rfHold": True}, {"stay": True}):
            snap = F.classify(row, {"status": "Contacted", **note}, TODAY)["source_snapshot"]
            for field in note:
                bucket = "row" if field == "status" and field in row else "note"
                self.assertIn(field, snap["fields"][bucket])
            self.assertNotEqual(snap["sha256"], base)

    def test_next_step_shape_is_snapshot_bound(self):
        row = self.row()
        no_next = F.classify(row, {"status": "Contacted"}, TODAY)["source_snapshot"]
        overdue = F.classify(row, {"status": "Contacted", "nextStep": {"date": "2026-10-31", "what": "call"}}, TODAY)["source_snapshot"]
        future = F.classify(row, {"status": "Contacted", "next": "2026-11-05"}, TODAY)["source_snapshot"]
        self.assertNotEqual(no_next["sha256"], overdue["sha256"])
        self.assertNotEqual(overdue["sha256"], future["sha256"])
        self.assertEqual(overdue["fields"]["note"]["normalized_next_date"], "2026-10-31")
        self.assertEqual(future["fields"]["note"]["normalized_next_date"], "2026-11-05")

    def test_evaluation_date_is_snapshot_bound_and_changes_urgency(self):
        row = self.row(auction="11/10/2026")
        note = {"status": "Contacted", "nextStep": {"date": "2026-11-30", "what": "call"}}
        before = F.classify(row, note, date(2026, 11, 1))
        after = F.classify(row, note, date(2026, 11, 20))
        self.assertNotEqual(before["source_snapshot"]["sha256"], after["source_snapshot"]["sha256"])
        self.assertEqual(before["source_snapshot"]["fields"]["evaluation_date"], "2026-11-01")
        self.assertEqual(after["source_snapshot"]["fields"]["evaluation_date"], "2026-11-20")
        self.assertEqual(before["action"], "prioritize_follow_up")
        self.assertEqual(after["action"], "follow_up")

    def test_stale_or_unknown_sources_remain_research_not_outreach(self):
        for row in (self.row(clerk_stale=True), self.row(judgment_unknown=True), self.row(eqstate="unpriced"), self.row(bkdeg=1)):
            got = F.classify(row, {"status": "Contacted", "nextStep": {"date": "2026-11-30", "what": "call"}}, TODAY)
            self.assertEqual(got["action"], "research")

    def test_serialized_report_is_review_only(self):
        raw = F.report([self.row(case="A")], {"A": {"status": "Contacted"}}, TODAY)
        out = F.serialize_review(raw)
        self.assertTrue(out["review_only"])
        self.assertFalse(out["outreach_eligible"])
        self.assertEqual(out["items"][0]["case"], "A")

    def test_report_is_count_only_and_pure(self):
        rows = [self.row(case="A", auction="11/05/2026"), self.row(case="B", saleBkAct=True)]
        notes = {"A": {"status": "Contacted"}, "B": {"status": "Contacted"}}
        before = [dict(r) for r in rows]
        out = F.report(rows, notes, TODAY)
        self.assertEqual(out["total"], 2)
        self.assertEqual(rows, before)
        self.assertEqual(out["counts"]["hold"], 1)


if __name__ == "__main__":
    unittest.main()
