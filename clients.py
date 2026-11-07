"""clients.py -- existing clients are not leads.

WHY (2026-11-06, Alejandro): a homeowner who already works with us showed up on Call Mode as a
cold lead (dial card, Whitepages search button, mail/knock routing). A client is not a
do-not-contact: Alejandro talks to them directly. They just must never be worked as a lead by
either seat, the worker, a paid lookup, a letter or a door.

THE LIST. `clients.json` next to this file, gitignored like deads.json (it names our clients):

    {"<case number>": {"d": "YYYY-MM-DD", "by": "who added it", "note": "free text, never baked"}}

Keyed by CASE only, never by person or household: two people with the same surname are two
separate lanes until a human says otherwise. Add a client by adding a line; remove one by
deleting it. The page never sees `note` or `by`, only a `client: 1` flag on the row.

WHAT IT DOES. make_tracker stamps `client: 1` on the row; Call Mode drops it from every dial
lane (and so from mail/knock routes, which only route dial rows), from Find a number and from
the board-lane funnel; the board treats it like a killed lead (Worker, lanes, mail queue,
relatives) and paints a CLIENT banner on the card. The Python paid-lookup and outreach pickers
(Whitepages, skip trace, first-touch email, three-day, morning planner) skip it too.

WHAT IT DOES NOT DO. It writes no opt-out, ledger, or do-not-contact status, and it is not a
suppression verdict: nothing here changes what the opt-out surface decides.

FAILURE. An unreadable file is reported loudly and treated as empty. A client reappearing as a
lead is a nuisance, not a legal wall, and holding the whole book on a typo would be worse.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS_FILE = os.path.join(HERE, 'clients.json')

_cache = {'key': None, 'val': {}, 'err': ''}


def norm(case):
    """Case number -> comparison key: upper case, letters and digits only."""
    return re.sub(r'[^0-9A-Z]', '', str(case or '').upper())


def load(path=None):
    """-> {normalized case: {'d': ..., 'case': original}}. Memoized on the file's mtime."""
    p = path or CLIENTS_FILE
    try:
        st = os.stat(p)
    except OSError:
        _cache.update(key=None, val={}, err='')
        return {}
    key = (p, st.st_mtime, st.st_size)
    if _cache['key'] == key:
        return _cache['val']
    out, err = {}, ''
    try:
        raw = json.load(open(p, encoding='utf-8')) or {}
        if not isinstance(raw, dict):
            raise ValueError('top level is not an object')
        for c, v in raw.items():
            k = norm(c)
            if k:
                out[k] = {'case': str(c), 'd': str((v or {}).get('d') or '') if isinstance(v, dict) else ''}
    except Exception as e:
        err = '%s: %s' % (type(e).__name__, str(e)[:80])
        out = {}
        print('!! clients.json unreadable (%s) -- NO client is excluded from the lead lanes this run' % err)
    _cache.update(key=key, val=out, err=err)
    return out


def error():
    """'' or why the last load failed."""
    return _cache['err']


def is_client(row_or_case, path=None):
    """True when the row carries the baked flag or its case is on the list."""
    if isinstance(row_or_case, dict):
        if row_or_case.get('client'):
            return True
        case = row_or_case.get('case') or row_or_case.get('Case #') or row_or_case.get('c') or ''
    else:
        case = row_or_case
    k = norm(case)
    return bool(k) and k in load(path)


def stamp(rows, path=None):
    """Set client=1 on every row whose case is on the list. -> number stamped."""
    lst = load(path)
    n = 0
    if not lst:
        return 0
    for d in rows or ():
        if isinstance(d, dict) and norm(d.get('case')) in lst:
            d['client'] = 1
            n += 1
    return n
