#!/usr/bin/env python
"""postscan -- read the Doral virtual mailbox (PostScan Mail) from code.

WHY THIS EXISTS
The Doral box at 8400 NW 33rd St Ste 310 is the return address on outbound Lob mail, so the
undeliverable pieces come back HERE. Email already has bounces.py closing that loop; this is the
physical-mail half of it. A returned letter is the strongest possible "this address is wrong"
signal -- stronger than any bounce heuristic -- and today it would just sit in a mailbox unseen.

SECURITY (non-negotiable, same rule as lob.key / quo.key)
The key is read from a gitignored `postscan.key` file and NEVER hardcoded. This repo is PUBLIC.
`*.key` is covered by .gitignore line 242. Do not add the key to sender.json, a .bat, or a comment.
Rotate it in the PostScan dashboard if it is ever pasted anywhere shared -- keys are shown once at
creation and can be revoked instantly.

🪤 THE EMPTY-MAILBOX TRAP -- the whole reason this file is careful
An empty mailbox does NOT return an empty list. It returns:

    {"status": 1, "data": "No items are received yet."}

`data` is a STRING when empty and a LIST when mail exists. So the obvious code --

    for item in resp['data']:        # <-- WRONG
        handle(item)

-- iterates the 26 CHARACTERS of that sentence, calls handle() 26 times on 'N', 'o', ' ', ...
and reports success. It "succeeds while doing nothing," which is the exact failure shape that has
bitten this repo before. items() below normalises to a real list and NOTHING ELSE should parse the
raw payload.

READ-ONLY BY DESIGN
The API also exposes /discard and /shred. This module deliberately does NOT implement them. Mail
that gets shredded is gone, and no automated loop in this repo should be able to destroy physical
mail. If that is ever wanted it goes in its own file, behind its own --i-mean-it flag.

Run:  python postscan.py            # summary of what is in the box
      python postscan.py --json     # raw normalised items, for piping
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'postscan.key')
BASE = 'https://api.postscanmail.com/api/account-docs/v2'

# The sentinel the API returns instead of an empty list. Compared case-insensitively on a prefix
# rather than for equality, because a wording change ("No items received yet.") must not silently
# turn back into a 26-iteration character loop.
_EMPTY_PREFIX = 'no items'


class PostScanError(RuntimeError):
    pass


def load_key(path=None):
    """Fail LOUD. A missing key must never degrade into 'zero mail today'."""
    path = path or KEY_FILE
    if not os.path.exists(path):
        raise PostScanError(
            'postscan: no key at %s. Create it with the API key from the PostScan dashboard '
            '(Settings -> API). It is gitignored by the *.key rule.' % path)
    key = io.open(path, encoding='utf-8').read().strip()
    if not key:
        raise PostScanError('postscan: %s is empty.' % path)
    return key


def _get(path, key=None):
    key = key or load_key()
    req = urllib.request.Request(BASE + path,
                                 headers={'x-api-key': key, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        if e.code in (401, 403):
            raise PostScanError('postscan: key rejected (HTTP %d). Rotate/re-copy it from the '
                                'dashboard. %s' % (e.code, detail))
        raise PostScanError('postscan: HTTP %d on %s. %s' % (e.code, path, detail))


def items(key=None):
    """Every mail item in the box, ALWAYS as a list.

    Returns [] for an empty mailbox -- never the sentinel string, never None. Callers may iterate
    the result directly without re-checking its type; that guarantee is this function's whole job.
    """
    resp = _get('/items', key=key)
    data = resp.get('data')

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Defensive: a paginated variant would nest the rows. Take the first list-valued key.
        for v in data.values():
            if isinstance(v, list):
                return v
        return []
    if isinstance(data, str):
        if data.strip().lower().startswith(_EMPTY_PREFIX):
            return []
        # A string we do NOT recognise is not an empty mailbox -- it is an unknown state, and
        # returning [] here would be the silent failure this module exists to prevent.
        raise PostScanError('postscan: unexpected string payload from /items: %r. Refusing to '
                            'treat it as an empty mailbox.' % data[:120])
    if data is None:
        return []
    raise PostScanError('postscan: /items returned %s, expected list/str/dict.' % type(data).__name__)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read the Doral virtual mailbox.')
    ap.add_argument('--json', action='store_true', help='print normalised items as JSON')
    a = ap.parse_args(argv)

    try:
        rows = items()
    except PostScanError as e:
        print(str(e), file=sys.stderr)
        return 1

    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print('postscan: mailbox is empty (0 items).')
        return 0

    print('postscan: %d item(s) in the box' % len(rows))
    for r in rows:
        if not isinstance(r, dict):
            print('  ?? non-dict row: %r' % (r,))
            continue
        who = r.get('recipient') or r.get('to') or r.get('recipient_name') or '?'
        frm = r.get('sender') or r.get('from') or r.get('sender_name') or '?'
        when = r.get('created_at') or r.get('received_date') or r.get('date') or '?'
        print('  %-12s from %-28s to %-24s' % (str(when)[:12], str(frm)[:28], str(who)[:24]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
