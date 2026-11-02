"""Pure, shadow-mode follow-up operating model.

This module deliberately does not write notes, schedule work, send messages, or change a lead.
It converts the existing board fields into a reviewable next-action/report shape so the team can
test a follow-up process before wiring it into the board or outreach paths.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
import hashlib
import json


TERMINAL = {"Dead", "DO NOT CONTACT", "CLOSED", "Lost - sold at auction"}
RULE_VERSION = "shadow-followup-v1"
STAGES = ["Appointment", "Offer made", "Under contract", "Assigned", "CLOSED"]
REASON = {
    "dnc": "do_not_contact",
    "stay": "bankruptcy_stay",
    "diligence": "diligence_hold",
    "suppressed": "suppressed_or_dismissed",
    "terminal": "terminal_status",
    "urgent": "auction_within_14_days",
    "appointment_unverified": "appointment_equity_unverified",
    "overdue": "next_step_overdue",
    "missing_next": "missing_next_step",
    "callable": "callable_follow_up",
    "unknown": "needs_manager_review",
}


def _parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def _next_step(note: dict[str, Any]) -> tuple[date | None, str]:
    value = note.get("nextStep") or note.get("next") or {}
    if isinstance(value, dict):
        return _parse_day(value.get("date")), str(value.get("what") or "").strip()
    return _parse_day(value), ""


def _hold_reason(row: dict[str, Any], note: dict[str, Any]) -> str | None:
    status = str(note.get("status") or row.get("status") or "").strip()
    if (status == "DO NOT CONTACT" or note.get("optout") or note.get("dnc")
            or note.get("never_contact") or note.get("no") in ("hard", "stop")
            or row.get("optout") or row.get("dnc") or row.get("never_contact")):
        return REASON["dnc"]
    if (row.get("saleBkAct") or row.get("bk") or row.get("bkWhy") or row.get("stay")
            or row.get("rfHold") or note.get("rfHold") or note.get("stay")):
        return REASON["stay"]
    if (row.get("ddhold") or row.get("unanchored") or row.get("contact_hold")
            or note.get("contact_hold")):
        return REASON["diligence"]
    if (row.get("sibclaimed") or row.get("lpDismissed") or row.get("suppressed")
            or row.get("suppression") or note.get("suppressed")):
        return REASON["suppressed"]
    if status in TERMINAL:
        return REASON["terminal"]
    return None


def _snapshot(row: dict[str, Any], note: dict[str, Any], evaluation_date: date | None = None) -> dict[str, Any]:
    """Stable, non-sensitive evidence snapshot for a review report."""
    fields = ("status", "auction", "days", "saleBkAct", "bk", "bkWhy", "stay", "rfHold", "ddhold",
              "unanchored", "contact_hold", "sibclaimed", "lpDismissed", "suppressed",
              "suppression", "dnc", "optout", "never_contact", "wpKey", "eqstate")
    values = {k: row.get(k) for k in fields if k in row}
    note_values = {k: note.get(k) for k in ("status", "optout", "dnc", "never_contact", "no",
                                             "contact_hold", "suppressed", "apptUnverified", "rfHold", "stay",
                                             "nextStep", "next") if k in note}
    next_date, next_what = _next_step(note)
    note_values["normalized_next_date"] = next_date.isoformat() if next_date else None
    note_values["normalized_next_what"] = next_what
    payload = {"evaluation_date": (evaluation_date or date.today()).isoformat(),
               "row": values, "note": note_values}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {"fields": payload, "sha256": hashlib.sha256(encoded).hexdigest()}


def classify(row: dict[str, Any], note: dict[str, Any] | None = None,
             today: date | None = None) -> dict[str, Any]:
    """Return a pure shadow recommendation for one row and its local note."""
    row = row or {}
    note = note or {}
    today = today or date.today()
    status = str(note.get("status") or row.get("status") or "New").strip()
    next_date, next_what = _next_step(note)
    hold = _hold_reason(row, note)
    auction = _parse_day(row.get("auction"))
    days = (auction - today).days if auction else row.get("days")
    urgent = isinstance(days, int) and 0 <= days <= 14
    overdue = bool(next_date and next_date < today)
    appointment_unverified = bool(note.get("apptUnverified"))

    if hold:
        action, reason = "hold", hold
    elif appointment_unverified:
        action, reason = "verify_equity", REASON["appointment_unverified"]
    elif overdue:
        action, reason = "complete_overdue_next_step", REASON["overdue"]
    elif urgent:
        action, reason = "prioritize_follow_up", REASON["urgent"]
    elif not next_date and status not in TERMINAL:
        action, reason = "assign_next_step", REASON["missing_next"]
    else:
        action, reason = "follow_up", REASON["callable"]

    return {
        "case": str(row.get("case") or ""),
        "county": str(row.get("county") or ""),
        "status": status,
        "stage": status if status in STAGES else None,
        "action": action,
        "reason": reason,
        "owner": str(note.get("nextActionOwner") or note.get("owner") or "unassigned"),
        "next_date": next_date.isoformat() if next_date else None,
        "next_what": next_what,
        "auction_days": days if isinstance(days, int) else None,
        "urgent_14d": urgent,
        "held": bool(hold),
        "compliance_hold": hold,
        "appointment_unverified": appointment_unverified,
        "rule_version": RULE_VERSION,
        "source_snapshot": _snapshot(row, note, today),
        "generated_at": today.isoformat(),
        "evidence": {
            "eq_state": row.get("eqstate"),
            "eq_source": note.get("eqSource") or row.get("eqSource"),
            "wp_status": row.get("wpKey"),
            "keep_or_sell": note.get("keepOrSell"),
        },
    }


def transition(note: dict[str, Any], requested: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a requested shadow status transition without mutating the input."""
    current = str((note or {}).get("status") or "New")
    requested = str(requested or "").strip()
    gate = _hold_reason(row or {}, note or {})
    allowed = {"New", "Verified", "Callable", "Contacted", "Conversation", "Appointment", *STAGES[1:], *TERMINAL}
    if requested not in allowed:
        return {"ok": False, "from": current, "to": requested, "reason": "invalid_status"}
    if current == "DO NOT CONTACT" and requested != "DO NOT CONTACT":
        return {"ok": False, "from": current, "to": requested, "reason": REASON["dnc"]}
    if gate and requested not in {current, "DO NOT CONTACT", "Dead"}:
        return {"ok": False, "from": current, "to": requested, "reason": gate}
    if current in {"Dead", "CLOSED"} and requested not in {current, "DO NOT CONTACT"}:
        return {"ok": False, "from": current, "to": requested, "reason": REASON["terminal"]}
    return {"ok": True, "from": current, "to": requested, "reason": None}


def report(rows: list[dict[str, Any]], notes: dict[str, dict[str, Any]] | None = None,
           today: date | None = None) -> dict[str, Any]:
    """Build count-only dashboard data; never persists or schedules anything."""
    notes = notes or {}
    items = [classify(row, notes.get(str(row.get("case") or ""), {}), today) for row in rows]
    counts = {}
    for item in items:
        counts[item["action"]] = counts.get(item["action"], 0) + 1
    return {"generated_for": (today or date.today()).isoformat(), "total": len(items),
            "counts": counts, "items": items}
