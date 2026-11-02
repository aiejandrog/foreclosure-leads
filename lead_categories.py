"""Shadow-mode deterministic lead categories.

This module is deliberately pure and is not imported by the production runner.  It turns an
existing DealFlow row into an explainable work bucket without changing eligibility, ranking,
messaging, paid lookups, or any persisted lead data.
"""
from __future__ import annotations

from datetime import date, datetime, timezone


RULE_VERSION = "lead-category-v1"
EQUITY_DOLLARS_GOLD = 200_000
EQUITY_PCT_GOLD = 40.0
_HARD_HOLD_KEYS = (
    "dnc", "optout", "never_contact", "suppressed", "saleBkAct", "bk", "stay",
    "rfHold", "contact_hold", "ddhold", "sibclaimed", "lpDismissed", "unanchored",
    "bkWhy",
)


def _first(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _number(value):
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(row, *keys):
    value = _first(row, *keys)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "verified"}
    return bool(value)


def _evidence(row):
    eqc = row.get("eqc") if isinstance(row.get("eqc"), dict) else {}
    source = _first(row, "equity_source", "eq_source", "vsrc", "source")
    source = source or _first(eqc, "source", "src", "basis_source")
    observed = _first(row, "equity_checked_at", "equity_ts", "checked", "updated_at", "ts")
    observed = observed or _first(eqc, "date", "d", "checked")
    return {
        "source": str(source) if source else "unknown",
        "observed_at": str(observed) if observed else None,
    }


def _equity(row):
    eqc = row.get("eqc") if isinstance(row.get("eqc"), dict) else {}
    state = _first(row, "equity_state", "eq_state") or _first(eqc, "state", "status")
    state = str(state or "").lower()
    if _bool(row, "equity_verified") or _bool(eqc, "verified", "fact"):
        state = "fact"
    if not state:
        state = "unknown" if _bool(row, "judgment_unknown") else "estimate"
    dollars = _number(_first(row, "equity_dollars", "equity", "eq"))
    pct = _number(_first(row, "equity_pct", "eq_pct"))
    fake = _bool(row, "eqfake", "eq_fake", "equity_fake")
    usable = state in {"fact", "priced", "verified"} and not fake and dollars is not None
    high = usable and (dollars >= EQUITY_DOLLARS_GOLD or (pct is not None and pct >= EQUITY_PCT_GOLD))
    return {"state": state, "dollars": dollars, "pct": pct, "usable": usable, "high": high}


def _hard_hold(row):
    for key in _HARD_HOLD_KEYS:
        value = row.get(key)
        if isinstance(value, str):
            if value.strip().lower() not in {"", "0", "false", "none", "no"}:
                return True
        elif value:
            return True
    return False


def _days(row):
    value = _first(row, "days_to_sale", "days_to_auction", "days")
    return _number(value)


def _contact_quality(row):
    phones = _first(row, "phones", "phone", "wpPhones")
    has_phone = bool(phones) if not isinstance(phones, str) else bool(phones.strip())
    has_owner = bool(_first(row, "owner", "owners", "oname", "name"))
    return "verified" if has_phone and has_owner else "name_only" if has_owner else "none"


def classify_lead(row, *, now=None):
    """Return an explainable shadow category for one existing lead row.

    No field is mutated.  ``now`` is accepted for deterministic callers and reserved for future
    freshness rules; current rules use the row's already-computed countdown fields.
    """
    row = row if isinstance(row, dict) else {}
    evidence = _evidence(row)
    eq = _equity(row)
    days = _days(row)
    county = str(row.get("county") or "").upper() or None
    contact = _contact_quality(row)
    reasons = []
    hard = _hard_hold(row)
    missing = []
    if not row.get("case"):
        missing.append("missing_case")
    if not _first(row, "addr", "address", "property_address"):
        missing.append("missing_property_address")
    if not county:
        missing.append("missing_county")
    if days is None:
        missing.append("missing_sale_countdown")
    if hard:
        bucket, next_action, review_by = "EXCLUDED", "no_contact", "compliance"
        reasons.append("hard_contact_or_legal_hold")
    elif missing:
        bucket, next_action, review_by = "RESEARCH_HOLD", "resolve_missing_identity", "research_coordinator"
        reasons.extend(missing)
    elif days < 0:
        bucket, next_action, review_by = "EXCLUDED", "retire_past_sale", "operations"
        reasons.append("sale_date_passed")
    elif eq["high"] and days <= 14 and contact == "verified":
        bucket, next_action, review_by = "GOLD", "closer_circle_drain", "human_closer"
        reasons.extend(["sale_within_14_days", "high_verified_equity", "owner_phone_available"])
    elif eq["usable"] and days <= 30 and contact != "none":
        bucket, next_action, review_by = "SILVER", "lead_manager_call_and_verify", "lead_manager"
        reasons.extend(["sale_within_30_days", "usable_equity", "owner_contact_path"])
    elif eq["usable"] and (_bool(row, "vac", "vacant", "vacancy_verified") or
                            _bool(row, "deceased", "deceased_or_probate", "probate", "estate")):
        bucket, next_action, review_by = "SILVER", "investigate_vacancy_or_estate", "lead_manager"
        reasons.append("secondary_distress_factor")
    elif eq["usable"] and contact != "none":
        bucket, next_action, review_by = "BRONZE", "low_cost_nurture", "lead_manager"
        reasons.extend(["valid_equity", "owner_contact_path"])
    else:
        bucket, next_action, review_by = "RESEARCH_HOLD", "verify_equity_or_contact", "research_coordinator"
        reasons.append("insufficient_verified_evidence")
    return {
        "bucket": bucket,
        "reasons": reasons,
        "next_action": next_action,
        "review_by": review_by,
        "rule_version": RULE_VERSION,
        "evidence": evidence,
        "county": county,
        "days_to_sale": days,
        "equity": eq,
        "contact_quality": contact,
    }
