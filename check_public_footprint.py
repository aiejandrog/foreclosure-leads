#!/usr/bin/env python
"""check_public_footprint.py -- fetch the LIVE public pages and prove no personal identifier is in them.

Run it after the nightly rebuild publishes, and again after site_fresh.py:

    python check_public_footprint.py                  # board, /call/, /call/carlos/, hm-balloon
    python check_public_footprint.py --since 2026-10-26T05:30   # also require a build at or after that time
    python check_public_footprint.py --also "some string"       # extra literal(s) to look for (not printed)

It applies footprint.problems() -- the same rule the build and the publish step enforce: any consumer
mailbox, and any value from the untracked footprint.local.json (advisor address, retired defaults,
deny list), in plain, regex-escaped or oddly spaced form. Values are never printed, only masked.

--since matters: GitHub Pages serves a cached copy for several minutes, and a clean result from the
OLD page proves nothing. The board carries its build time in the DEALFLOW-COVERAGE line; a page built
before --since is reported STALE, not clean.

Exit 0 = every page clean (and fresh, if --since). Exit 1 = a hit or a stale page. Exit 2 = a page
could not be fetched.
"""
import argparse
import json
import re
import sys
import time

import requests

import board_url
import footprint as FP

PAGES = ('', 'call/', 'call/carlos/', 'hm-balloon-q7v3n8/')
MARK = re.compile(r'DEALFLOW-COVERAGE\s*(\{.*?\})')


def fetch(url):
    r = requests.get(url, params={'cb': int(time.time())}, timeout=120,
                     headers={'Cache-Control': 'no-cache', 'User-Agent': 'footprint-check/1'})
    r.raise_for_status()
    return r.text


def built_stamp(html):
    m = MARK.search(html[:4000])
    if not m:
        return ''
    try:
        return str(json.loads(m.group(1)).get('built') or '')
    except Exception:
        return ''


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--since', default='', help='require the board build stamp >= this (YYYY-MM-DDTHH:MM)')
    ap.add_argument('--also', action='append', default=[], help='extra literal to look for (repeatable)')
    ap.add_argument('--base', default=board_url.BOARD_URL, help='site root (default: board_url.BOARD_URL)')
    a = ap.parse_args()

    if not (FP.advisor_emails() or FP.retired()['email'] or FP.retired()['addr'] or FP._list('deny')):
        print('NOTE: no footprint.local.json on this machine -- only the generic consumer-mailbox rule is '
              'being checked. Create one (footprint.local.json.template) for the full check.\n')
    extra = [FP.needle_regex(x) for x in a.also if x.strip()]
    worst = 0
    for rel in PAGES:
        url = a.base + rel
        try:
            html = fetch(url)
        except Exception as e:
            print('FETCH FAILED  %-28s %s' % ('/' + rel, str(e)[:100]))
            worst = max(worst, 2)
            continue
        plain = re.sub(r'[A-Za-z0-9+/]{400,}={0,2}', '<enc>', html)
        hits = FP.problems(plain)
        for rx, lit in zip(extra, a.also):
            if rx is not None and rx.search(plain):
                hits.append('--also value #%d is in the page (%s***)' % (a.also.index(lit) + 1, lit[:2]))
        stamp = built_stamp(html) if rel == '' else ''
        stale = bool(a.since and rel == '' and (not stamp or stamp < a.since))
        status = 'HIT' if hits else ('STALE' if stale else 'clean')
        print('%-6s %-28s %9d bytes%s' % (status, '/' + rel, len(html), ('  built ' + stamp) if stamp else ''))
        for h in hits:
            print('         - ' + h)
        if stale:
            print('         - build stamp %r is older than --since %s: this is the OLD page (cache?), '
                  'so a clean scan proves nothing yet' % (stamp, a.since))
        if hits or stale:
            worst = max(worst, 1)
    print('\n%s' % ('ALL CLEAN' if worst == 0 else 'NOT CLEAN' if worst == 1 else 'INCOMPLETE'))
    return worst


if __name__ == '__main__':
    sys.exit(main())
