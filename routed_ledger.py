#!/usr/bin/env python
"""routed_ledger -- the durable memory of every door ever put on a route sheet.

WHY. "Remember all the ones we did" had no system of record: carlos_week_routes.py carries a
hardcoded EXCLUDE set frozen at 27 addresses on 08-12, carlos_deep_zones re-DERIVES last week's
assignment from live data (so a data refresh can silently re-route a door), and the only honest
answer on 2026-08-24 was scraping 785 addresses back out of old HTML artifacts. Meanwhile
worker_notes.json has zero door touches ever logged, so "issued" is the only signal that exists
for most doors. This file makes issuance itself the durable record, per normalized address:

    doors[ADDR] = { case, first_issued, last_issued, rest_until,
                    attempts: [ {d, who, class, out}, ... ] }

  who   : 'A' (Alejandro) | 'C' (Carlos) | '?' (seeded from an old artifact)
  class : 'wd-early' | 'wd-late' | 'sat' | 'unknown'   -- the time-slot of the attempt; the
          re-knock engine requires the NEXT attempt to be a different class, because knocking
          the same door at the same hour three times just measures the owner's commute.
  out   : 'issued' when we only know it went on a sheet; upgraded to the real outcome
          ('no-answer' / 'talked' / ...) when a door touch shows up in worker_notes.json
          (the tracker's door QUICKLOG buttons write ch:'door') -- see sync_outcomes().

The ledger is PII (homeowner street addresses) and is gitignored. It lives next to the other
local-state ledgers (worker_notes.json, optouts.json, mail_sent.json) and, like them, exists on
the armed machine only.

CLI:
    python routed_ledger.py --seed     # one-time backfill from prior route artifacts on disk
    python routed_ledger.py            # print a summary
"""
import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, 'routed_ledger.json')

MAX_ATTEMPTS = 3          # then the door rests -- mail/calls keep working it
COOLDOWN_D = 3            # days before a no-answer door may re-enter
REST_D = 30               # rest after the 3rd attempt, and for talked/answered doors

_ADDR_RE = re.compile(
    r'\b(\d{2,6}\s+(?:[NSEW]{1,2}\s+)?[0-9A-Z][0-9A-Z ]{1,26}?'
    r'(?:ST|AVE|AV|RD|DR|CT|TER|PL|WAY|BLVD|LN|CIR|HWY|PKWY|TRL))\b')


def norm(addr):
    """Street-line only, uppercased, whitespace-collapsed -- the ledger key."""
    a = re.sub(r'\s+', ' ', str(addr or '').upper()).strip()
    return a.split(',')[0].strip()


def load():
    if not os.path.exists(PATH):
        return {'version': 1, 'doors': {}}
    try:
        return json.load(open(PATH, encoding='utf-8'))
    except Exception:
        # A corrupt ledger must fail LOUD: silently starting fresh would re-issue every door
        # in the county tomorrow morning and the operator would never know why.
        raise SystemExit('routed_ledger.json exists but is unreadable -- fix or move it; '
                         'refusing to start an empty ledger over it.')


def save(led):
    tmp = PATH + '.tmp'
    json.dump(led, open(tmp, 'w', encoding='utf-8'), indent=1)
    os.replace(tmp, PATH)


def timeclass(when=None):
    """The attempt's slot: Saturday block, or early/late half of the weekday evening."""
    when = when or dt.datetime.now()
    if when.weekday() == 5:
        return 'sat'
    return 'wd-early' if when.hour < 18 else 'wd-late'


def record_issue(led, addr, case, who, klass, day=None):
    """Idempotent per (addr, day): re-running the generator the same morning must not
    double-count an attempt."""
    a = norm(addr)
    day = day or dt.date.today().isoformat()
    d = led['doors'].setdefault(a, {'case': case, 'first_issued': day,
                                    'last_issued': day, 'attempts': [], 'rest_until': None})
    if any(at.get('d') == day for at in d['attempts']):
        return False
    d['attempts'].append({'d': day, 'who': who, 'class': klass, 'out': 'issued'})
    d['last_issued'] = day
    d['case'] = d.get('case') or case
    # REAL attempts only — seeded print history never spends the budget (see _real)
    if len(_real(d['attempts'])) >= MAX_ATTEMPTS:
        d['rest_until'] = (dt.date.fromisoformat(day) + dt.timedelta(days=REST_D)).isoformat()
    return True


def _real(atts):
    """Attempts that represent an actual ROUTED-BY-THIS-SYSTEM knock opportunity.

    Seeded rows ('issued-preledger') are IMPORTED PRINT HISTORY, not attempts: seed() records one
    per artifact an address appeared in, so a lead that rode five old packets arrived carrying five
    'attempts' — and 89 doors were already past MAX_ATTEMPTS on day one (measured 2026-08-26). The
    cap check outlives rest_until, so those leads were banned from the door system permanently, for
    the crime of having been printed a lot. They were never even knocked: the door ledger has zero
    logged touches. Print history still earns the 30-day rest (so day one is not a re-flood) — it
    must not spend the attempt budget."""
    return [x for x in (atts or []) if x.get('out') != 'issued-preledger']


def state(led, addr, today=None):
    """-> (eligible, attempt_no, last_class, note) for a prospective NEW issue today."""
    a = norm(addr)
    today = today or dt.date.today()
    d = led['doors'].get(a)
    if not d:
        return True, 1, None, 'fresh'
    atts = _real(d.get('attempts') or [])
    n = len(atts)
    last = atts[-1] if atts else ((d.get('attempts') or [{}])[-1])
    if d.get('rest_until'):
        try:
            if dt.date.fromisoformat(d['rest_until']) > today:
                return False, n + 1, last.get('class'), f'resting until {d["rest_until"]}'
        except Exception:
            pass
    if n >= MAX_ATTEMPTS:
        return False, n + 1, last.get('class'), 'attempt cap reached'
    out = str(last.get('out') or '')
    if out.startswith('talked') or out.startswith('spoke'):
        return False, n + 1, last.get('class'), 'already talked -- calls own it now'
    # SAME-DAY RE-RUN = REPRINT, not a second batch. Without this, running the generator twice in
    # one morning cooldown-blocked the 13 doors already issued and quietly issued 12 DIFFERENT
    # ones (caught 2026-08-26): the printed sheet and the ledger described two different days.
    # A door issued today stays eligible AT ITS CURRENT attempt number; record_issue() already
    # no-ops on the duplicate, so the plan reproduces instead of multiplying.
    if last.get('d') == today.isoformat() and out == 'issued':
        return True, n, last.get('class'), 'issued today (reprint)'
    try:
        since = (today - dt.date.fromisoformat(last.get('d'))).days
    except Exception:
        since = 999
    if since < COOLDOWN_D:
        return False, n + 1, last.get('class'), f'cooldown ({since}d < {COOLDOWN_D}d)'
    return True, n + 1, last.get('class'), f'reknock #{n + 1}'


def sync_outcomes(led, notes):
    """Upgrade 'issued' attempts with real door outcomes from worker_notes touches (ch=='door').
    The tracker's QUICKLOG buttons are the only writers of those; zero exist as of 2026-08-24,
    so this is forward-looking wiring, not a backfill."""
    bycase = {}
    for a, d in led['doors'].items():
        if d.get('case'):
            bycase.setdefault(d['case'], a)
    n = 0
    for case, rec in (notes or {}).items():
        a = bycase.get(case)
        if not a:
            continue
        d = led['doors'][a]
        for t in (rec.get('touches') or []):
            if t.get('ch') != 'door':
                continue
            td = str(t.get('d') or '')[:10]
            for at in d['attempts']:
                if at['d'] == td and at['out'] == 'issued':
                    o = str(t.get('out') or '').lower()
                    at['out'] = 'talked' if ('spoke' in o or 'talk' in o) else 'no-answer'
                    if at['out'] == 'talked':
                        d['rest_until'] = (dt.date.fromisoformat(td)
                                           + dt.timedelta(days=REST_D)).isoformat()
                    n += 1
    return n


# ---------------------------------------------------------------- one-time seeding
def seed():
    """Backfill from every route artifact still on disk. Seeded attempts count as attempt #1
    with class 'unknown', so a seeded door is a legitimate re-knock candidate once its 30-day
    rest expires -- but it can never masquerade as fresh."""
    led = load()
    pats = ['Carlos_*.html', 'MSG_Door_*.html', 'BSG_Door_*.html', 'BSG_Miami_Doors_*.html',
            'Carlos_Week_Routes_*.html']
    files = sorted({f for p in pats for f in glob.glob(os.path.join(HERE, p))})
    added = 0
    for f in files:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(f))
        day = m.group(1) if m else '2026-08-01'
        txt = re.sub(r'<[^>]+>', ' ', open(f, encoding='utf-8', errors='replace').read())
        for hit in {mm.group(1).strip() for mm in _ADDR_RE.finditer(txt.upper())}:
            a = norm(hit)
            d = led['doors'].setdefault(a, {'case': None, 'first_issued': day,
                                            'last_issued': day, 'attempts': [], 'rest_until': None})
            if not any(at.get('d') == day for at in d['attempts']):
                d['attempts'].append({'d': day, 'who': '?', 'class': 'unknown',
                                      'out': 'issued-preledger'})
                d['last_issued'] = max(d['last_issued'], day)
                # the old sheets carried no re-knock discipline; rest them 30d from issue so the
                # new system does not open by re-flooding routes with every address ever printed
                d['rest_until'] = (dt.date.fromisoformat(day)
                                   + dt.timedelta(days=REST_D)).isoformat()
                added += 1
    save(led)
    print(f'seeded {added} issuance record(s) from {len(files)} artifact(s) '
          f'-> {len(led["doors"])} door(s) in the ledger')
    return 0


def migrate():
    """Collapse duplicated seeded rows: one 'issued-preledger' per door, keeping the LATEST print
    date (that is what the 30-day rest should count from). seed() wrote one row per artifact, so a
    much-printed address carried five — inflating nothing now that _real() ignores them for the
    cap, but still misleading on the card and in any future count. Also sorts attempts by date;
    glob order had them out of sequence, which made `attempts[-1]` (the 'last attempt') wrong."""
    led = load()
    fixed = 0
    for a, d in led['doors'].items():
        atts = sorted(d.get('attempts') or [], key=lambda x: str(x.get('d') or ''))
        seeded = [x for x in atts if x.get('out') == 'issued-preledger']
        real = [x for x in atts if x.get('out') != 'issued-preledger']
        if len(seeded) > 1:
            fixed += 1
        keep = ([seeded[-1]] if seeded else []) + real
        keep.sort(key=lambda x: str(x.get('d') or ''))
        if keep != atts:
            d['attempts'] = keep
        if keep:
            d['first_issued'] = keep[0]['d']
            d['last_issued'] = keep[-1]['d']
    save(led)
    print(f'migrated: collapsed duplicate seed rows on {fixed} door(s); attempts date-sorted')
    return 0


def main():
    if '--seed' in sys.argv[1:]:
        return seed()
    if '--migrate' in sys.argv[1:]:
        return migrate()
    led = load()
    doors = led['doors']
    today = dt.date.today()
    elig = sum(1 for a in doors if state(led, a, today)[0])
    print(f'{len(doors)} door(s) in the ledger | {elig} currently re-eligible')
    return 0


if __name__ == '__main__':
    sys.exit(main())
