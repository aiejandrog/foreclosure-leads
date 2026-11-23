#!/usr/bin/env python3
"""equity_batch_in.py - hand a combined browser batch to the equity queue.

WHY (2026-11-22). The Broward worker copies one county name search per lead. Chrome's
multiple-download guard swallows files after about five automatic downloads from one page, and the
browser tool cuts an answer at ~1,000 characters, so per-lead files and chat-sized chunks both
stall. The page now stashes every search and writes ONE file per batch:

    { "<case>_<search index>": [header, chunk 0, chunk 1, ...], ... }

Each value is exactly what `equity_queue.py rows --file` takes (header from the county's own
results call, chunks with the same checksum the queue verifies). This splits the file and calls
the queue's own accept_search for each, so every existing check still decides what is stored:
checksums, row count equals the county's total, rows belong to the search asked for.

    python equity_batch_in.py <batch.json> [--keep]

Counts only on stdout. The batch file holds homeowner names: it is deleted after reading unless
--keep, and it must live outside the repo (Downloads is fine).
"""
import json
import os
import sys

import equity_queue as Q


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    try:
        with open(path, encoding='utf-8') as fh:
            batch = json.load(fh)
    except Exception as e:
        print('refused: could not read the batch (%s)' % str(e)[:80])
        return 1
    if not isinstance(batch, dict):
        print('refused: the batch must be an object of "<case>_<index>": payload')
        return 1
    ok = bad = 0
    why = {}
    for key, payload in batch.items():
        case, _, idx = key.rpartition('_')
        try:
            idx = int(idx)
        except ValueError:
            case, idx = key, 0
        good, msg = Q.accept_search(case, payload, idx)
        if good:
            ok += 1
        else:
            bad += 1
            k = msg.split(':')[0][:60]
            why[k] = why.get(k, 0) + 1
    print('stored %d search(es), refused %d' % (ok, bad))
    for k, n in sorted(why.items(), key=lambda kv: -kv[1]):
        print('  refused x%d: %s' % (n, k))
    if '--keep' not in argv:
        try:
            os.remove(path)
        except OSError:
            pass
    return 0 if ok or not bad else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
