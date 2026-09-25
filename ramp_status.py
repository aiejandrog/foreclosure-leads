#!/usr/bin/env python
"""ramp_status.py -- what each sending alias ACTUALLY put on the wire today, across both ledgers.

WHY THIS EXISTS. Every cap in this project meters off `mail_sent.json` and nothing else:
`send_server._alias_sent_today()` counts rows in it, `_ramp_cap()` bounds that count, and
`cadence.py` and `outreach_email.py` both import those two functions so all three senders draw on
one budget. That is correct and it is deliberate.

But `warmup.py` does not write that ledger. It says so on purpose -- "it logs to
~/DEALFLOW/warmup_log.json, not to mail_sent.json, so the outreach ledger stays clean" -- and for
an outreach ledger that IS right: warm-up mail goes to company-owned mailboxes, it is not outreach,
and counting it as outreach would corrupt every reply-rate and bounce-rate number in the project.

The gap is that nothing then adds the two back together, and `warmup_log.json` is read by exactly
one module: warmup.py itself. So no surface anywhere shows an alias's real daily volume. Gmail does
not care which file a message was logged in. On a domain with no sending history, the number that
decides whether it gets throttled is the total.

Measured on 2026-09-22, the second day of the ramp: each warming alias is allowed 5 cold sends and
is separately sending 15 warm-up messages, so its true volume is up to 20 while every cap in the
system reads at most 5. That is not a bug in either half -- it is the sum nobody was printing.

READ-ONLY. This module opens two files and prints. It sends nothing, writes nothing, and imports
`send_server` only for its cap arithmetic (side-effect free: send_server binds a port only under
`if __name__ == '__main__'`, which is the same reason cadence.py imports it).

Run:  python ramp_status.py              today, per alias
      python ramp_status.py --days 14    plus what the caps do over the next 14 days
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))

import send_server as _SS
import warmup as _WU

SENT_LEDGER = _SS.SENT_LEDGER
WARMUP_LOG = _WU.LOG


def cold_today(alias, day):
    """Rows in mail_sent.json for this alias today, by _alias_sent_today's exact predicate.

    Returns None when the ledger is absent. That distinction is the point: this file is gitignored
    and HERE-rooted, so on any machine that is not the sender it does not exist -- and reporting a
    confident 0 for "we have no idea" is how you conclude a domain is quiet when it is not.
    """
    if not os.path.exists(SENT_LEDGER):
        return None
    try:
        rows = json.load(open(SENT_LEDGER, encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    iso = day.isoformat()
    return sum(1 for e in rows
               if e.get('d') == iso
               and str(e.get('from') or '').lower() == alias
               and e.get('message_id') and not e.get('test_mode') and not e.get('error'))


def warm_today(alias, day):
    """Messages warmup.py recorded for this alias today. None when the log is absent."""
    if not os.path.exists(WARMUP_LOG):
        return None
    try:
        log = json.load(open(WARMUP_LOG, encoding='utf-8'))
    except Exception:
        return None
    return len((log.get('days') or {}).get(day.isoformat(), {}).get(alias, []))


def _n(v):
    return '?' if v is None else str(v)


def main():
    ap = argparse.ArgumentParser(description='True per-alias daily send volume, both ledgers.')
    ap.add_argument('--days', type=int, default=0, metavar='N',
                    help='also project the caps N days forward')
    ap.add_argument('--date', default='', metavar='YYYY-MM-DD', help='report a past day instead')
    a = ap.parse_args()

    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    cfg = _SS._load_senders()
    main_addr = str((cfg.get('lanes') or {}).get('default') or '').lower()
    wu_day = _WU.day_number(day)
    wu_quota = _WU.quota(wu_day)

    try:
        ramp_day = (day - dt.date.fromisoformat(str(cfg.get('ramp_start')))).days + 1
    except Exception:
        ramp_day = 0

    print('DEALFLOW send volume - %s' % day)
    print('  senders.json ramp day %d (ramp_start %s) | warmup.py day %d, quota %d/alias'
          % (ramp_day, cfg.get('ramp_start'), wu_day, wu_quota))
    if not os.path.exists(SENT_LEDGER):
        print('  !! mail_sent.json not on this machine - cold counts unknown, shown as "?".')
    if not os.path.exists(WARMUP_LOG):
        print('  !! warmup_log.json not on this machine - warm-up counts unknown, shown as "?".')
    print()

    addrs = [main_addr] + [x for x in _WU.ALIASES if x != main_addr] if main_addr else list(_WU.ALIASES)
    for x in sorted(set((cfg.get('lanes') or {}).values())):
        if x and x.lower() not in addrs:
            addrs.append(x.lower())

    print('  %-38s %6s %6s %7s %6s' % ('alias', 'cold', 'warm', 'total', 'cap'))
    over = []
    for alias in addrs:
        cold = cold_today(alias, day)
        warm = warm_today(alias, day) if alias in _WU.ALIASES else 0
        cap = _SS._ramp_cap(cfg, alias, today=day)
        total = None if cold is None or warm is None else cold + warm
        print('  %-38s %6s %6s %7s %6d%s'
              % (alias, _n(cold), _n(warm) if alias in _WU.ALIASES else '-', _n(total), cap,
                 '   << over cap' if total is not None and total > cap else ''))
        # The headline is not today's actual count -- on most machines that is unknown. It is that
        # the SCHEDULED warm-up quota alone already exceeds the cold cap, which needs no ledger.
        if alias in _WU.ALIASES and wu_quota > cap:
            over.append((alias, cap, wu_quota))

    if over:
        print()
        print('  WARM-UP OUTWEIGHS THE COLD RAMP on %d alias(es), by schedule, ledgers aside:'
              % len(over))
        for alias, cap, q in over:
            print('    %-38s cold cap %-3d  warm-up %d/day  -> up to %d on the wire, metered as %d'
                  % (alias, cap, q, cap + q, cap))
        print('  Both halves are behaving as written. Nothing in the project adds them up but this.')

    if a.days > 0:
        print()
        print('  Next %d days - cold cap vs the warm-up quota running beside it:' % a.days)
        crossover = None
        for i in range(a.days + 1):
            d = day + dt.timedelta(days=i)
            wq = _WU.quota(_WU.day_number(d))
            caps = [(x, _SS._ramp_cap(cfg, x, today=d)) for x in _WU.ALIASES]
            lo = min(c for _, c in caps)
            if crossover is None and lo >= wq:
                crossover = d
            print('    %s  cold %-4s  warm-up %-3d  %s'
                  % (d, '/'.join(str(c) for _, c in caps), wq,
                     'cold ahead' if lo >= wq else 'warm-up ahead'))
        print()
        if crossover:
            print('  The cold ramp CAP first matches or passes the warm-up quota on %s.' % crossover)
            print('  That is a date on the calendar, not a measurement. The ramp climbs from')
            print('  ramp_start whether or not a single message was sent, so the cap reaching 15')
            print('  says nothing about real cold volume. Audit 2026-09-23 found no live cold path')
            print('  to these aliases at all - so stopping BSG Warmup on this date would have')
            print('  dropped each alias 15 -> 0, not 15 -> 20.')
            print('  Stop warm-up per alias only after the cold column above shows >= the warm-up')
            print('  quota in REAL sends for five days running, with bounce under 3%, then taper.')
        else:
            print('  The cold ramp does not reach the warm-up quota inside this window.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
