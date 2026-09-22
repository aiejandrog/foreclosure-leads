"""Create an offline, evidence-first review manifest from docket.py raw JSON.

Example: python case_review.py --input raw.json --case CASE --output CASE-review.json
Relative output names resolve under paths.DEALFLOW_DIR. Documents are NOT fetched or read
by this tool. Every docket entry requires document/access reconciliation by a reviewer.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

import paths as P


SIGNALS = {
    "death_or_estate": r"\b(deceased|death|died|decedent|estate of|suggestion of death)\b",
    "probate": r"\b(probate|personal representative|letters of administration|heirs?)\b",
    "bankruptcy": r"\b(bankrupt\w*|automatic stay|relief from stay)\b",
    "judgment": r"\b(judgment|judgement)\b",
    "amendment_or_vacatur": r"\b(amended|vacat\w*|set aside|reconsideration)\b",
    "attorney_change": r"\b(appearance|withdraw\w*|substitution of counsel|counsel|attorney)\b",
    "title_or_sale": r"\b(certificate of title|certificate of sale|sale|auction|deed|mortgage|assignment|satisfaction|lis pendens)\b",
}


def select_case(payload, case=None):
    """Accept one raw OCS object or docket.py's case-keyed export; reject errors."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Input must be a nonempty raw docket object or case-keyed object")
    if "dockets" in payload or "parties" in payload:
        record = payload
        identity = record.get("caseNumber") or record.get("caseNo")
        if case and identity and str(identity) != case:
            raise ValueError("Requested case differs from the raw record case identifier")
        case = case or identity
    else:
        if case is None:
            if len(payload) != 1:
                raise ValueError("--case is required for an export containing multiple cases")
            case = next(iter(payload))
        if case not in payload:
            raise ValueError("Requested case is absent from input")
        record = payload[case]
    if not isinstance(case, str) or not case.strip():
        raise ValueError("A nonempty case identifier is required")
    if not isinstance(record, dict):
        raise ValueError("Case record must be an object")
    identity = record.get("caseNumber") or record.get("caseNo")
    if identity and str(identity) != case:
        raise ValueError("Export key differs from the raw record case identifier")
    for field in ("dockets", "parties"):
        if field not in record or not isinstance(record[field], list):
            raise ValueError(f"Case record requires a {field} array; missing/null is not an empty result")
        if any(not isinstance(item, dict) for item in record[field]):
            raise ValueError(f"Every {field} item must be an object")
    return case, record


def _signals(value, reference):
    text = json.dumps(value, ensure_ascii=False)
    return [{"type": name, "status": "unverified_review_signal", "source_ref": reference,
             "matched_terms": sorted({m.group(0) for m in re.finditer(pattern, text, re.I)}),
             "requires": "Read original document, identify subject and negation, reconcile later filings"}
            for name, pattern in SIGNALS.items() if re.search(pattern, text, re.I)]


def build_review(payload, case=None):
    case, record = select_case(payload, case)
    raw = deepcopy(record)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    # Bind positional task references to this exact source revision. A later docket may insert
    # entries at the beginning; reusing its predecessor's IDs could attach a review to the wrong filing.
    prefix = hashlib.sha256(case.encode("utf-8")).hexdigest()[:16] + ":" + digest[:16]
    tasks, signals = [], []
    for index, entry in enumerate(raw["dockets"], 1):
        ref = f"dockets/{index - 1}"
        tasks.append({"task_id": f"{prefix}:entry:{index}", "source_ref": ref,
                      "status": "pending", "owner": None, "document_read": False,
                      "action": "Reconcile this entry with the official docket; enumerate and read every attachment, or record verified no-document/restricted access",
                      "evidence": [], "blocker": None})
        # Preserve unknown fields in raw_case; recognized attachment arrays get explicit
        # work items as well. An absent array never establishes that no document exists.
        for field in ("documents", "attachments"):
            attachments = entry.get(field)
            if isinstance(attachments, list):
                for attachment_index, _ in enumerate(attachments, 1):
                    tasks.append({"task_id": f"{prefix}:entry:{index}:{field}:{attachment_index}",
                                  "source_ref": f"{ref}/{field}/{attachment_index - 1}",
                                  "status": "pending", "owner": None, "document_read": False,
                                  "action": "Retrieve and read every page of this attachment; retain page evidence or an explicit access blocker",
                                  "evidence": [], "blocker": None})
        signals.extend(_signals(entry, ref))
    for index, party in enumerate(raw["parties"]):
        signals.extend(_signals(party, f"parties/{index}"))
    return {
        "schema_version": 1, "case": case,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"kind": "user_supplied_docket_json", "record_sha256": digest,
                   "official_source_identity_verified": False},
        "status": "incomplete",
        "coverage": {"entries_received": len(raw["dockets"]), "parties_received": len(raw["parties"]),
                     "docket_pagination_verified": False, "document_inventory_complete": False,
                     "documents_read": 0, "official_records_searched": False,
                     "related_cases_searched": False},
        "raw_case": raw, "document_tasks": tasks, "review_signals": signals,
        "findings": [],
        "review_requirements": [
            "Verify county, case identity and all docket pages against the official portal",
            "Account for every entry and attachment, including unavailable, sealed and unreadable documents",
            "Read complete documents and retain URL/instrument, retrieval time, file hash, page and verbatim supporting passage",
            "Reconcile parties and attorney appearances, substitutions and withdrawals chronologically",
            "Read judgment plus all amendments, vacatur, stays and subsequent orders; a title alone is not a finding",
            "Verify death/probate/bankruptcy hints against the correct person and case; a name match or keyword is insufficient",
            "Review linked official records, legal descriptions, assignments, satisfactions and sale/title events",
            "Record contradictions and unresolved access gaps; require independent review before any downstream decision",
        ],
        "limitations": ["Metadata received does not prove the entire docket was fetched",
                        "No document content has been read by this tool",
                        "Signals include negated and historical mentions and establish no legal or personal status",
                        "This manifest does not change outreach eligibility or any suppression state"],
    }


def output_path(value):
    """Constrain PII output to the configured local DealFlow folder, outside repositories/sync."""
    root = Path(P.DEALFLOW_DIR).expanduser().resolve()
    candidate = Path(value).expanduser()
    candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
    sync_roots = [Path(v).resolve() for k, v in os.environ.items()
                  if k.lower().startswith("onedrive") and v]
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("Output must be a file inside paths.DEALFLOW_DIR")
    if any(part.lower().startswith("onedrive") for part in candidate.parts):
        raise ValueError("Output cannot be inside OneDrive")
    if any(candidate.is_relative_to(sync) for sync in sync_roots):
        raise ValueError("Output cannot be inside a configured OneDrive root")
    if any((parent / ".git").exists() for parent in candidate.parents):
        raise ValueError("Output cannot be inside a Git repository")
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--case")
    parser.add_argument("--output", required=True, help="New JSON file inside paths.DEALFLOW_DIR")
    args = parser.parse_args(argv)
    try:
        with open(args.input, encoding="utf-8-sig") as source:
            report = build_review(json.load(source), args.case)
        target = output_path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Review manifest failed: {exc}\n")
    print(f"Incomplete review manifest created: {len(report['document_tasks'])} pending entry reviews; 0 documents read")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
