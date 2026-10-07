"""text_hold.py - may texting go out right now? (held, why).

REPLACES the Quo inbound-scan text hold (2026-10-07, Alejandro: "remove Quo from operations completely").
Texts leave as plain SMS from the phone's own Messages app (an sms: link with the body pre-filled),
so there is no vendor inbox left to scan for STOP replies. Replies land on the phone. A "stop",
"no" or "wrong number" there is marked Do Not Contact in Call Mode or the board, and that note is
ledgered into optouts.json through optout_sync.ledger_from_notes (send_server /notes).

So the one thing texting can still be held on is the ledger itself: the same do-not-contact list
email is held on. Missing, unreadable, or older than DEALFLOW_OPTOUT_MAX_AGE_DAYS (default 2, the
same variable send_server's /send gate reads) holds texting. Fail closed: a list we cannot read is
not an empty list.

Callers: send_server (/health text_hold and the POST /text refusal), call_mode (the baked hold the
phone trusts while it cannot reach the bridge), export_to_resimpli (REsimpli texts).
"""
import datetime
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OPTOUT_FILE = os.path.join(HERE, 'optouts.json')
try:
    MAX_AGE_DAYS = float(os.environ.get('DEALFLOW_OPTOUT_MAX_AGE_DAYS', '2'))
except ValueError:
    MAX_AGE_DAYS = float('nan')   # status() holds on it rather than failing to import

_FIX = 'Run python ledger_sync.py on this computer. Email is held by the same list.'


def status(path=None, now=None, max_age_days=None):
    """{'held', 'why', 'ok', 'ts', 'maxAgeH'} for the opt-out ledger at `path`.

    ts is the ledger's modified time (UTC ISO) when it is readable, so a page that baked this can
    re-check the age itself once it can no longer ask the bridge."""
    path = path or OPTOUT_FILE
    try:
        max_age_days = MAX_AGE_DAYS if max_age_days is None else float(max_age_days)
    except (TypeError, ValueError):
        max_age_days = float('nan')
    out = {'held': True, 'why': '', 'ok': False, 'ts': '', 'maxAgeH': 0.0}
    # Fail closed on a nonsense limit: nan compares False against every age, inf never expires,
    # and 0 or less can never be met, so none of them is a usable window.
    if not math.isfinite(max_age_days) or max_age_days <= 0:
        out['why'] = ('HOLD texting: DEALFLOW_OPTOUT_MAX_AGE_DAYS is not a positive number (%r). '
                      'Fix or remove that setting.' % max_age_days)
        return out
    out['maxAgeH'] = max_age_days * 24
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        out['why'] = 'HOLD texting: the do-not-contact list is missing. ' + _FIX
        return out
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
    except Exception:
        d = None
    if not isinstance(d, (dict, list)):
        out['why'] = 'HOLD texting: the do-not-contact list cannot be read. ' + _FIX
        return out
    when = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age_d = (now - when).total_seconds() / 86400.0
    out['ts'] = when.isoformat().replace('+00:00', 'Z')
    if age_d > max_age_days:
        out['why'] = ('HOLD texting: the do-not-contact list is %.1f days old (max %g). %s'
                      % (age_d, max_age_days, _FIX))
        return out
    out.update(held=False, ok=True)
    return out


def text_hold(path=None, now=None, max_age_days=None):
    """(held, why). Texting only; email has its own gate on the same ledger in send_server."""
    s = status(path, now, max_age_days)
    return bool(s['held']), (s['why'] if s['held'] else '')


if __name__ == '__main__':
    held, why = text_hold()
    print(why if held else 'texting not held: the do-not-contact list is fresh')
    raise SystemExit(1 if held else 0)
