"""DealFlow Today — one page, three sections: Call / Mail / Knock.

The notes file is the record: 2,303 leads with status, touches and next date. The board
(leads_final.json) is a 367-row build stamped 11-04 and is used only to ENRICH a note with
address, owner, sale date and equity. A lead is never dropped because the board is stale —
it is listed and flagged.

Order inside every section: overdue first, then due today, then new. Nothing here sends,
mails, dials, prints or spends. It reads, sorts and renders.

Exclusions are enforced on every list, from the same verdicts the board and Call Mode use.
This module does not decide a suppression verdict — it reads one. The suppression surface
named in CLAUDE.md is not touched here.

Run:  python dealflow_today.py            counts per section
      python dealflow_today.py --json     the full structure
"""

import json
import os
import sys
from datetime import date, datetime

import dealflow_rules as R

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, "leads_final.json")
NOTES = os.path.join(HERE, "worker_notes.json")
CLIENTS = os.path.join(HERE, "clients.json")

# Statuses that retire a lead from every list. A hard no is never re-contacted.
RETIRED = {"dead", "do not contact", "dnc", "not interested", "client", "closed"}

# leads_final.json is a raw export with human column names. One map, one place.
COLMAP = {
    "case": "Case #", "addr": "Address", "mail": "mailing_address", "owners": "owners",
    "auction": "AuctionDate", "value": "market_value", "judg": "Final Judgment Amount",
    "equity": "equity", "eq": "equity_pct", "days": "days_to_auction", "score": "score",
    "st": "sale_type", "ctype": "case_type", "plaintiff": "plaintiff", "defs": "defendants",
    "hs": "homestead", "filed": "filing_year", "bprice": "last_sale_price",
    "parcel": "Parcel ID", "docket": "docket_url", "status": "case_status",
}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _pretty_owner(s):
    """'RAFAEL VERDE; MARIETTA VERDE' -> 'Rafael Verde'."""
    if not s:
        return ""
    first = str(s).split(";")[0].strip()
    return first.title() if first.isupper() else first


def norm_row(r):
    """Map one raw export row to the short keys the rest of this module reads."""
    if not isinstance(r, dict):
        return {}
    out = {}
    for short, long in COLMAP.items():
        if long in r:
            out[short] = r[long]
    for k, v in r.items():
        out.setdefault(k, v)
    out["case"] = str(out.get("case") or "").strip()
    out["oname"] = _pretty_owner(out.get("owners"))
    return out


def load_leads():
    d = _load(LEADS, [])
    if isinstance(d, dict):
        for k in ("rows", "leads", "data"):
            if isinstance(d.get(k), list):
                d = d[k]
                break
        else:
            d = []
    return [norm_row(r) for r in d] if isinstance(d, list) else []


def load_notes():
    d = _load(NOTES, {})
    n = d.get("notes") if isinstance(d, dict) else None
    return n if isinstance(n, dict) else {}


def load_clients():
    """clients.json — a client is never worked as a lead. Any shape, read defensively."""
    d = _load(CLIENTS, {})
    out = set()
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                out.add(str(k))
            elif isinstance(v, str):
                out.add(v)
    elif isinstance(d, list):
        for x in d:
            if isinstance(x, str):
                out.add(x)
            elif isinstance(x, dict):
                for k in ("case", "c", "id"):
                    if x.get(k):
                        out.add(str(x[k]))
    return out


def _d(v):
    """Parse a date out of a note or a row. None when absent or unreadable."""
    if not v:
        return None
    s = str(v)[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    return None


def exclusion(row, note, clients):
    """'' when the lead may be worked, else the reason it may not.

    Every verdict here is read, never produced. Mirrors the board's own gates by name so a
    future reader can trace each one: opt-out, Do Not Contact, bankruptcy stay, attorney,
    client, Dead, wrong owner, diligence hold.
    """
    c = str((row or {}).get("case") or "")
    n = note or {}
    st = str(n.get("status") or "").strip().lower()

    if c and c in clients:
        return "client"
    if n.get("optout") or n.get("optlog"):
        return "opted out"
    if st in RETIRED:
        return "retired: " + st
    if n.get("wrongown"):
        return "wrong owner"
    if n.get("atty"):
        return "attorney represented"
    if (row or {}).get("saleBkAct") or (row or {}).get("bk") or n.get("stay"):
        return "bankruptcy stay"
    if (row or {}).get("diligence") or (row or {}).get("hold") or n.get("hold"):
        return "diligence hold"
    if (row or {}).get("no") == "hard" or n.get("no") == "hard":
        return "hard no"
    return ""


def _sale_days(row, today):
    d = _d((row or {}).get("auction") or (row or {}).get("sale") or (row or {}).get("x"))
    if d:
        return (d - today).days
    v = (row or {}).get("days")
    return v if isinstance(v, int) else None


def _phones(row, note):
    """Owner numbers. leads_final.json carries no owner phone (only zagentphone, the agent),
    so the note is the primary source and the row is the fallback."""
    out = []
    n = note or {}
    if n.get("phone"):
        out.append(str(n["phone"]))
    for x in ((row or {}).get("phones") or []):
        if x and str(x) not in out:
            out.append(str(x))
    return out


def _knock_ok(row):
    """A door exists when there is an address and the lead is not a lis-pendens-only row."""
    r = row or {}
    return bool(r.get("addr") or r.get("a")) and str(r.get("st") or "") != "LP"


def _last_touch(note):
    ts = (note or {}).get("touches") or []
    if not ts:
        return None
    last = ts[-1]
    return {"d": last.get("d") or (last.get("ts") or "")[:10],
            "ch": last.get("ch"), "out": last.get("out")}


def _bucket(due, today):
    if due is None:
        return "new"
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    return None


def _letters_sent(note):
    return sum(1 for t in ((note or {}).get("touches") or []) if t.get("ch") == "letter")


def _no_answer_streak(note):
    """Consecutive no-answers with no conversation. Stops at the first real contact."""
    streak = 0
    for t in reversed((note or {}).get("touches") or []):
        out = str(t.get("out") or "").lower()
        if t.get("ch") == "call" and ("no answer" in out or "voicemail" in out or "noanswer" in out):
            streak += 1
        elif t.get("ch") in ("call", "door") or "spoke" in out or "talked" in out:
            break
    return streak


def build_today(today=None, leads=None, notes=None, clients=None):
    """-> {'header': {...}, 'call': [...], 'mail': [...], 'knock': [...], 'excluded': {...}}"""
    today = today or date.today()
    leads = leads if leads is not None else load_leads()
    notes = notes if notes is not None else load_notes()
    clients = clients if clients is not None else load_clients()

    by_case = {}
    for r in leads:
        c = str(r.get("case") or "")
        if c and c not in by_case:
            by_case[c] = r

    call, mail, knock = [], [], []
    excluded = {}

    for c, n in notes.items():
        if not isinstance(n, dict):
            continue
        row = by_case.get(c)
        why = exclusion(row, n, clients)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
            continue

        due = _d(n.get("next"))
        b = _bucket(due, today)
        if b is None:
            continue                       # dated in the future: not today's work

        sale_days = _sale_days(row, today)
        knock_ok = _knock_ok(row)
        phones = _phones(row, n)
        letters = _letters_sent(n)
        streak = _no_answer_streak(n)

        item = {
            "case": c,
            "name": (row or {}).get("oname") or (row or {}).get("owners") or n.get("name") or "",
            "addr": (row or {}).get("addr") or (row or {}).get("a") or "",
            "sale": (row or {}).get("auction") or "",
            "sale_days": sale_days,
            "due": due.isoformat() if due else None,
            "bucket": b,
            "status": n.get("status") or "",
            "note": n.get("note") or "",
            "last": _last_touch(n),
            "phones": phones,
            "knock_ok": knock_ok,
            "on_board": row is not None,
            "equity": "UNVERIFIED",
        }

        # CALL — a number to dial. The note's own phone is enough; the board is not required.
        if phones:
            call.append(dict(item, ch="call", prep={
                "number": phones[0],
                "script": "call",
                "no_answer_streak": streak,
                "letters": letters,
            }))

        # MAIL — first letter or follow-up, only outside the 7-day window.
        if (sale_days is None or sale_days >= R.MAIL_MIN_DAYS) and ((row or {}).get("mail") or item["addr"]):
            mail.append(dict(item, ch="letter", prep={
                "letter": "follow-up" if letters else "first",
                "batch": today.isoformat(),
                "mark": "mailed",
            }))

        # KNOCK — route order, door sheet, log outcome.
        if knock_ok and (sale_days is None or sale_days < R.MAIL_MIN_DAYS):
            city = item["addr"].split(",")[-1].strip() if "," in item["addr"] else "unrouted"
            knock.append(dict(item, ch="door", prep={
                "route": city, "sheet": "door", "log": "outcome",
            }))

    order = {"overdue": 0, "today": 1, "new": 2}
    for lst in (call, mail, knock):
        lst.sort(key=lambda x: (order[x["bucket"]],
                                x["sale_days"] if x["sale_days"] is not None else 9999))

    overdue = sum(1 for lst in (call, mail, knock) for x in lst if x["bucket"] == "overdue")
    return {
        "header": {"calls": len(call), "letters": len(mail), "doors": len(knock),
                   "overdue": overdue, "date": today.isoformat(),
                   "board_rows": len(leads), "notes": len(notes),
                   "off_board": sum(1 for x in call + mail + knock if not x["on_board"])},
        "call": call, "mail": mail, "knock": knock,
        "excluded": excluded,
    }


def main(argv):
    t = build_today()
    if "--json" in argv:
        print(json.dumps(t, indent=1, default=str))
        return 0
    h = t["header"]
    print("Board %d rows / %d notes  (%d work items off-board)"
          % (h["board_rows"], h["notes"], h["off_board"]))
    print("Today: %d calls, %d letters, %d doors, %d overdue  (%s)"
          % (h["calls"], h["letters"], h["doors"], h["overdue"], h["date"]))
    for name in ("call", "mail", "knock"):
        print("\n%s (%d)" % (name.upper(), len(t[name])))
        for x in t[name][:10]:
            print("  %-8s %-22s %-12s %s" % (x["bucket"], x["case"][:22], x["due"] or "-", x["name"][:26]))
        if len(t[name]) > 10:
            print("  ... %d more" % (len(t[name]) - 10))
    print("\nExcluded:", ", ".join("%s=%d" % kv for kv in sorted(t["excluded"].items())) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))