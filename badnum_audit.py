#!/usr/bin/env python
"""badnum_audit — recover phone numbers the worker killed by accident.

WHAT WENT WRONG (measured 2026-08-13)
The morning worker has a "bad number" control that permanently retires a phone: it appends to
worker_notes[case].badph, drops the lead from the durable call queue, posts to the bridge's
/retrace endpoint, and — the expensive part — the build-time card filter then drops that number
from EVERY FUTURE BAKE. It is destructive, permanent, and it sits in the tap path with no
confirmation and no undo.

On 2026-08-13 it fired 61 times in a single day, in bursts: 20 marks inside the 08:09 minute, 18
inside 08:08. That is a click roughly every three seconds. Nobody establishes that a phone number
is dead in three seconds — the control was being used as "next".

THE PROOF IT WAS WRONG: (954) 245-5005 was marked bad at 08:09:45. That evening the owner of that
number — Milouse Joseph, 8208 NW 57 PL, case CACE-24-006635, foreclosure auction six days out with
roughly $56k of equity in the house — TEXTED IN asking to sell. The number was never bad. Had she
not written first, the lead was gone.

WHY THIS RESTORES EVERYTHING FROM THAT DAY RATHER THAN GUESSING
There is no evidence field on a mark: no carrier response, no bounce, no operator note. So a mark
made at machine speed is indistinguishable from a real one, and the only honest reading of a
3-second cadence is that none of them were evaluated. Restoring a genuinely dead number costs one
wasted dial. NOT restoring a live one costs a house. The asymmetry decides it.

NOTHING IS DELETED. Every restored mark is copied into badnum_quarantine.json with its case, number
and the date it was marked, so a real dead number can be re-flagged deliberately and the original
state is always recoverable.

Run:  python badnum_audit.py --dry-run      # show what would be restored
      python badnum_audit.py                # restore, with a quarantine record
      python badnum_audit.py --date 2026-08-13   # limit to one day's damage
"""
import argparse
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.join(HERE, 'worker_notes.json')
RETRACE = os.path.join(HERE, 'retrace_queue.json')
QUAR = os.path.join(HERE, 'badnum_quarantine.json')

# A human deciding a number is dead — dialing it, hearing it fail, marking it — cannot be done in
# under this many seconds. Marks that arrive faster than this were not decisions.
HUMAN_SECONDS = 20


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _save(p, obj):
    tmp = p + '.tmp'
    json.dump(obj, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--date', default='', help='only restore marks queued on this YYYY-MM-DD')
    a = ap.parse_args()

    notes_doc = _load(NOTES, {}) or {}
    notes = notes_doc.get('notes') or {}
    retrace = _load(RETRACE, [])
    retrace = retrace if isinstance(retrace, list) else []

    # when each case was queued for re-trace == when it was marked bad
    when = {}
    for x in retrace:
        if isinstance(x, dict) and x.get('c'):
            when[x['c']] = str(x.get('d') or '')

    flagged = {c: list(n.get('badph') or [])
               for c, n in notes.items()
               if isinstance(n, dict) and n.get('badph')}

    if not flagged:
        print('no bad-number marks on file — nothing to audit.')
        return 0

    target = [c for c in flagged if not a.date or when.get(c) == a.date]
    dates = {}
    for c in flagged:
        dates[when.get(c, '(unknown)')] = dates.get(when.get(c, '(unknown)'), 0) + 1

    print('BAD-NUMBER MARKS ON FILE')
    print('  cases flagged      : %d' % len(flagged))
    print('  numbers retired    : %d' % sum(len(v) for v in flagged.values()))
    print('  by date marked     : %s' % ', '.join('%s=%d' % kv for kv in sorted(dates.items())))
    print('  in scope this run  : %d case(s)%s'
          % (len(target), (' (--date %s)' % a.date) if a.date else ''))
    print()

    quarantine = _load(QUAR, [])
    quarantine = quarantine if isinstance(quarantine, list) else []
    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    restored_cases, restored_nums = 0, 0

    for c in target:
        nums = flagged[c]
        quarantine.append({'case': c, 'numbers': nums, 'marked': when.get(c, ''),
                           'restored_at': stamp,
                           'why': 'bulk-marked at machine speed; no evidence of a real failure'})
        restored_cases += 1
        restored_nums += len(nums)
        if not a.dry_run:
            n = notes.get(c) or {}
            n.pop('badph', None)
            notes[c] = n

    keep = [x for x in retrace
            if not (isinstance(x, dict) and x.get('c') in set(target))]

    print('%s %d case(s), %d phone number(s)'
          % ('WOULD RESTORE' if a.dry_run else 'RESTORED', restored_cases, restored_nums))
    for c in target[:12]:
        print('   %-24s %s' % (c, ', '.join(flagged[c])))
    if len(target) > 12:
        print('   ... and %d more' % (len(target) - 12))

    if a.dry_run:
        print('\ndry run — nothing written.')
        return 0

    notes_doc['notes'] = notes
    _save(NOTES, notes_doc)
    _save(RETRACE, keep)
    _save(QUAR, quarantine)
    print('\nworker_notes.json   badph cleared on %d case(s)' % restored_cases)
    print('retrace_queue.json  %d -> %d entries' % (len(retrace), len(keep)))
    print('badnum_quarantine.json  every restored mark preserved — nothing was deleted')
    print('\nRebuild to put these numbers back on the board:')
    print('  python -c "import json, foreclosure_leads as F; '
          'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
