#!/usr/bin/env python
"""optout_sync — every detected STOP becomes a ledgered opt-out. Automatically. Same night.

THE GAP THIS CLOSES (found live 2026-08-13)
replies.py already detects a STOP word and writes `stop: true` into replies.json. Nothing then
carried that into optouts.json, which is the ledger the board and the send path actually consult.
So the pipeline *knew* and still stayed armed:

  * Norma Hendy (CACE-25-005200) reached the ledger only because a human typed it in.
  * gil_sosa@hotmail.com wrote "I am not Virginia. This is my personal email. Please stop sending
    emails" on 08-08. replies.py flagged it 08-12. It was STILL not in optouts.json on 08-13 —
    five days armed, against a person who was never even the right contact.

A detected opt-out that never reaches the ledger is worse than no detection: it manufactures a
record showing we were told to stop and kept going. That is the FTSA/TCPA/FDUTPA fact pattern, and
it is also just wrong.

SAFETY POSTURE — ONE WAY, ALWAYS
This only ever ADDS. It never clears, never downgrades, never rewrites an existing entry's status
(same rule as the server-side opt-out merge). Re-running is a no-op. An entry already in the ledger
is left exactly as a human left it.

BOTH KEYS, BECAUSE THE LEDGER IS CASE-KEYED AND REPLIES ARE EMAIL-KEYED
replies.json holds a lead twice: once under the case, once under '@address'. Norma had both; Gil had
only the email (his case had already left the board). Suppressing one key and not the other is how a
"handled" opt-out comes back. So every stop writes EVERY key it appears under, plus the raw address
onto the hard-suppression list send_server enforces.

Run:  python optout_sync.py            # sync, write, report
      python optout_sync.py --dry-run  # show what it WOULD ledger, write nothing
"""
import argparse
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPLIES = os.path.join(HERE, 'replies.json')
OPTOUTS = os.path.join(HERE, 'optouts.json')
SUPPRESS = os.path.join(HERE, 'bounced_emails.json')


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _truthy(v):
    # replies.py has written both a real bool and the STRING "True" (seen live on gil_sosa).
    # Treating the string as falsey is exactly how an opt-out silently fails to register.
    return str(v).strip().lower() in ('true', '1', 'yes')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    rep = _load(REPLIES, {})
    opt = _load(OPTOUTS, {}) or {}
    notes = opt.setdefault('notes', {})
    sup = _load(SUPPRESS, {})
    if not isinstance(sup, dict):
        sup = {}

    stops = {k: v for k, v in rep.items() if isinstance(v, dict) and _truthy(v.get('stop'))}
    added, already, addrs = [], [], []
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    for key, v in stops.items():
        email = (v.get('email') or (key[1:] if key.startswith('@') else '')).strip().lower()
        if email:
            addrs.append(email)
        if key in notes:
            already.append(key)
            continue
        when = str(v.get('when') or v.get('checked') or '')[:32]
        excerpt = str(v.get('excerpt') or '').strip()[:200]
        added.append(key)
        if a.dry_run:
            continue
        notes[key] = {
            'status': 'DO NOT CONTACT',
            'optout': str(v.get('checked') or now)[:10],
            'note': ('AUTO-LEDGERED by optout_sync from replies.json. Reply said STOP: %r. '
                     'Covers ALL channels: no email, no call, no text, no door. Subject: %s'
                     % (excerpt, str(v.get('subject') or '')[:90])),
            'optlog': [{'ts': when or now, 'act': 'opted-out', 'src': 'reply STOP word (replies.json)'},
                       {'ts': now, 'act': 'ledgered', 'src': 'optout_sync'}],
        }

    # the address itself onto the hard-suppression list the bridge enforces
    sup_new = [e for e in set(addrs) if e and e not in sup]
    if not a.dry_run:
        for e in sup_new:
            sup[e] = {'type': 'optout', 'when': now[:10], 'why': 'reply STOP word'}
        if added:
            tmp = OPTOUTS + '.tmp'
            json.dump(opt, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, OPTOUTS)
        if sup_new:
            json.dump(sup, open(SUPPRESS, 'w', encoding='utf-8'), indent=0, ensure_ascii=False)

    print('%d STOP-flagged reply key(s) in replies.json' % len(stops))
    print('  already ledgered : %d' % len(already))
    print('  %s: %d %s' % ('WOULD ledger' if a.dry_run else 'newly ledgered', len(added),
                           added or ''))
    print('  %s onto suppression list: %d %s'
          % ('would add' if a.dry_run else 'added', len(sup_new), sup_new or ''))
    if added and not a.dry_run:
        print('\n!! %d opt-out(s) were detected but NOT suppressed until this run. If any send went '
              'out to them in between, that is a real compliance event — check the Sent label.'
              % len(added))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
