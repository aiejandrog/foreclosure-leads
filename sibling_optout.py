"""Same owner, other case, for the paths that read the raw lead files (no person key on them).

The opt-out ledger is keyed by case, so a stop on one of an owner's cases did not reach the others
unless they shared the exact ledgered address. Raw lead rows carry no pkey, so the link is derived
from what they do carry: a lead whose own case or email is on the ledger puts its emails and phones
in a set, and any other lead sharing one is the same person. Over-suppressing is the correct
direction for a do-not-contact list. An email or number on more than `max_shared` leads is an
institution (a lender, a law firm), not a person, and is skipped, the same guard
foreclosure_leads._person_keys uses.

Pure function: no file reads, no writes, no ledger access. It can only ADD suppressions.
"""
import re


def _phones(r):
    out = set()
    for p in (r.get('phones') or []):
        d = re.sub(r'\D', '', str(p.get('number') if isinstance(p, dict) else p))
        if len(d) == 11 and d[0] == '1':
            d = d[1:]
        if len(d) == 10:
            out.add(d)
    return out


def _emails(r):
    return {str(e or '').strip().lower() for e in (r.get('emails') or []) if str(e or '').strip()}


def ledger_phone_keys(optouts_path, notes_keys=()):
    """10-digit numbers the ledger holds as '#digits' keys (a text STOP, a wrong number, a number
    marked Do Not Contact), plus any '#digits' in `notes_keys` (rep-logged DNC). outreach_email.
    _load_optouts drops these on purpose, so the email gates never saw a phone-only stop. Read-only;
    an unreadable file gives an empty set (the callers' own ledger gate already holds the send)."""
    import json
    out = set()
    keys = list(notes_keys or ())
    try:
        d = json.load(open(optouts_path, encoding='utf-8'))
        n = d.get('notes') if isinstance(d, dict) else None
        if isinstance(n, dict):
            keys += [k for k, v in n.items() if v]
    except Exception:
        pass
    for k in keys:
        k = str(k).strip()
        if k[:1] != '#':
            continue
        d = re.sub(r'\D', '', k)
        if len(d) == 11 and d[0] == '1':
            d = d[1:]
        if len(d) == 10:
            out.add(d)
    return out


def sibling_cases(leads, ledger_keys, case_fn, max_shared=8, phone_keys=()):
    """Lowercased case numbers of leads that are NOT themselves on the ledger but share an email or
    phone with a lead that is. `ledger_keys` is the lowercased key set with '@' stripped (what
    outreach_email._load_optouts returns)."""
    keys = {str(k).strip().lower().lstrip('@') for k in (ledger_keys or ())}
    pkeys = set(phone_keys or ())
    if not keys and not pkeys:
        return set()
    em_c, ph_c = {}, {}      # distinct CASES per identity: the same lead repeated across files counts once
    for r in leads:
        cs = str(case_fn(r) or '').strip().lower()
        for e in _emails(r):
            em_c.setdefault(e, set()).add(cs)
        for d in _phones(r):
            ph_c.setdefault(d, set()).add(cs)
    em_n = {k: len(v) for k, v in em_c.items()}
    ph_n = {k: len(v) for k, v in ph_c.items()}
    out_em, out_ph = set(), set()
    for r in leads:
        case = str(case_fn(r) or '').strip().lower()
        if (case and case in keys) or (_emails(r) & keys) or (_phones(r) & pkeys):
            out_em |= {e for e in _emails(r) if em_n.get(e, 0) <= max_shared}
            out_ph |= {d for d in _phones(r) if ph_n.get(d, 0) <= max_shared}
    hits = set()
    for r in leads:
        case = str(case_fn(r) or '').strip().lower()
        if case and case not in keys and ((_phones(r) & pkeys) or (_emails(r) & out_em) or (_phones(r) & out_ph)):
            hits.add(case)
    return hits
