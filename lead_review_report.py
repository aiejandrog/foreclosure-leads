"""Pure review-only adapter for shadow lead categories and follow-up recommendations.

The adapter consumes existing rows/notes in memory and returns a report object.  It performs no
file I/O, persistence, scheduling, outreach, ranking, or eligibility changes.
"""
from __future__ import annotations

from datetime import date
import json

import followup_shadow
import lead_categories


RULE_VERSION = "lead-review-report-v1"


def build_report(rows, notes=None, today=None):
    """Return review items and count summaries without mutating ``rows`` or ``notes``."""
    notes = notes or {}
    today = today or date.today()
    items = []
    for row in rows or []:
        row = row if isinstance(row, dict) else {}
        case = str(row.get("case") or "")
        note = notes.get(case, {}) if isinstance(notes, dict) else {}
        category = lead_categories.classify_lead(row)
        followup = followup_shadow.classify(row, note, today)
        items.append({
            "case": case,
            "county": category["county"] or followup["county"],
            "bucket": category["bucket"],
            "reasons": category["reasons"],
            "held": category["bucket"] in {"EXCLUDED", "RESEARCH_HOLD"} or followup["held"],
            "hold_reason": followup["compliance_hold"] if followup["held"] else None,
            "next_action": category["next_action"],
            "followup_action": followup["action"],
            "review_by": category["review_by"],
            "owner": followup["owner"],
            "next_date": followup["next_date"],
            "next_what": followup["next_what"],
            "auction_days": followup["auction_days"],
            "evidence": {
                "category": category["evidence"],
                "followup": followup["evidence"],
            },
            "category_evidence": category["evidence"],
            "category_rule_version": category["rule_version"],
            "followup_source_snapshot": followup["source_snapshot"],
            "followup_generated_at": followup["generated_at"],
            "followup_rule_version": followup["rule_version"],
            "review_only": True,
            "outreach_eligible": False,
            "rule_versions": {
                "report": RULE_VERSION,
                "category": category["rule_version"],
                "followup": followup["rule_version"],
            },
        })
    counts = {}
    held = 0
    for item in items:
        counts[item["bucket"]] = counts.get(item["bucket"], 0) + 1
        held += int(item["held"])
    return {
        "generated_for": today.isoformat(),
        "rule_version": RULE_VERSION,
        "review_only": True,
        "outreach_eligible": False,
        "total": len(items),
        "held": held,
        "counts": counts,
        "items": items,
    }


def serialize_review(rows, notes=None, today=None):
    """Serialize the in-memory review report without writing it or enabling outreach."""
    return json.dumps(build_report(rows, notes, today), sort_keys=True, separators=(",", ":"),
                      default=str)
