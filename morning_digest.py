#!/usr/bin/env python
"""morning_digest.py -- one screen: did the machine run last night, and what CHANGED.

WHY THIS EXISTS, AND WHY IT IS NOT THE THREE THINGS THAT ALREADY EXIST
The systems all work and their outputs are scattered across a board, a dozen JSON files and a bat
log, so the only way to know what happened overnight is to already know where to look. That is
backwards, and it is why a working machine can still feel like no machine at all.

    morning_planner.py   "what do I knock today"      per-lead, meeting-shaped, HTML agenda
    analyst.py           "is the business working"    weekly, trend-shaped
    healthcheck.py       "is the infrastructure ok"   pass/fail rules on internals
    THIS                 "did it run, what changed"   daily, one screen, deltas

TWO RULES THIS FILE IS BUILT AROUND, both learned the hard way in this repo:

1. A COUNT IS NOT NEWS. "373 leads" is wallpaper -- it is the same number every morning and the
   eye slides off it. "+6 new, 2 tier A" is the only part worth reading. So every headline metric
   carries a delta against yesterday, and metrics that cannot produce one say so rather than
   printing a bare number that looks like it means something.

2. MISSING MUST NEVER RENDER AS ZERO. The recurring failure mode here is a stage that does not run
   and reports success: sale_history stamps vanished and the board published 0 active bankruptcy
   stays while the cache held 97; a scraper returned nothing and "0 new leads" read as a quiet
   night. Every loader below distinguishes ABSENT from EMPTY, and an absent input is an ALERT line
   at the top, never a 0 in the body.

THE SAME-DAY RERUN TRAP
The baseline is keyed by DATE, not by run. Running this twice before lunch must show the same
deltas both times -- if the second run rebaselined against the first, every delta would collapse to
zero and the digest would report a quiet morning on the busiest day. roll_baseline() only advances
when the stored date differs from today.

CONTRACT: read-only, never raises, no network. It runs at the end of the nightly bat where a
traceback would mask the exit code of everything before it.

Usage:
    python morning_digest.py                # print to stdout, write the Desktop copy
    python morning_digest.py --no-save      # stdout only
    python morning_digest.py --json         # machine-readable, for wiring into anything else
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'digest_state.json')

# Desktop, because that is where deliverables land and because a file nobody opens is not a digest.
DESK = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop')
if not os.path.isdir(DESK):
    DESK = os.path.join(os.path.expanduser('~'), 'Desktop')

ABSENT = object()          # distinct from [] and from 0 -- see rule 2 in the docstring
ALERTS = []


def _load(fn):
    """ABSENT when the file is not there or cannot be parsed. Never {} as a consolation prize."""
    p = fn if os.path.isabs(fn) else os.path.join(HERE, fn)
    if not os.path.exists(p):
        ALERTS.append('%s is MISSING -- every number derived from it is omitted, not zeroed' % fn)
        return ABSENT
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception as e:
        ALERTS.append('%s is UNREADABLE (%s) -- omitted, not zeroed' % (fn, str(e)[:60]))
        return ABSENT


def _age_hours(fn):
    p = fn if os.path.isabs(fn) else os.path.join(HERE, fn)
    if not os.path.exists(p):
        return None
    return (dt.datetime.now() - dt.datetime.fromtimestamp(os.path.getmtime(p))).total_seconds() / 3600.0


def _num(v):
    try:
        if isinstance(v, bool):
            return 0.0
        return float(str(v).replace('$', '').replace(',', '').strip() or 0)
    except Exception:
        return 0.0


# ---- metrics ---------------------------------------------------------------------------------
def collect():
    """Every number the digest reports, as a flat dict. Missing inputs simply omit their keys."""
    m = {}
    leads = _load('leads_final.json')
    if leads is not ABSENT and isinstance(leads, list):
        m['leads'] = len(leads)
        m['auc7'] = sum(1 for r in leads if 0 <= _num(r.get('days_to_auction')) <= 7)
        m['auc30'] = sum(1 for r in leads if 0 <= _num(r.get('days_to_auction')) <= 30)
        m['tierA'] = sum(1 for r in leads if str(r.get('tier')) == 'A')
        m['bk'] = sum(1 for r in leads if str(r.get('sale_bk_active')) == 'True')
        m['eqfake'] = sum(1 for r in leads if r.get('eq_fake'))
        # CONTACTABILITY -- the growth lever, and the number that predicts a dead calling day.
        # It does NOT live on the lead row. leads_final.json has no phones/emails fields at all;
        # contacts are in skiptrace_results.json keyed by 'Case #'. Reading r.get('phones') here
        # returned None for every row and reported 319 of 373 contactless, which would have read
        # as a collapsed pipeline rather than as a join that was never made.
        # `emails` is already the LIVE list -- bounced addresses are held separately in
        # emails_dead -- so a row whose only addresses are dead correctly counts as unreachable.
        sk = _load('skiptrace_results.json')
        if sk is not ABSENT and isinstance(sk, dict):
            reach = 0
            for r in leads:
                ent = sk.get(str(r.get('Case #') or ''))
                if isinstance(ent, dict) and (ent.get('phones') or ent.get('emails')):
                    reach += 1
            m['reachable'] = reach
            m['contactless'] = len(leads) - reach
            m['traced'] = sum(1 for r in leads if str(r.get('Case #') or '') in sk)
        # the reset counter -- our only proprietary column, so it gets watched
        m['reset1'] = sum(1 for r in leads if int(_num(r.get('sale_survived'))) >= 1)
        m['reset3'] = sum(1 for r in leads if int(_num(r.get('sale_survived'))) >= 3)

    for name, fn in (('lp', 'lp_leads.json'), ('pb', 'palmbeach_leads.json')):
        d = _load(fn)
        if d is not ABSENT:
            m[name] = len(d)

    opt = _load('optouts.json')
    if opt is not ABSENT:
        m['optouts'] = len(opt)
    rep = _load('replies.json')
    if rep is not ABSENT:
        m['replies'] = len(rep)
    mail = _load('mail_sent.json')
    if mail is not ABSENT:
        m['mailed'] = len(mail)
    return m


def buyboxes():
    """Every standing buy-box, by verdict. This is the part that is FOR somebody by name."""
    out = []
    try:
        import buybox as BB
    except Exception:
        return out
    for key, box in getattr(BB, 'BOXES', {}).items():
        d = _load('buybox_%s.json' % key)
        if d is ABSENT:
            out.append({'key': key, 'label': box.get('label', key), 'for': box.get('for', ''),
                        'missing': True})
            continue
        rows = d if isinstance(d, list) else (d.get('rows') or d.get('matches') or [])
        c = {'CONFIRMED': 0, 'UNKNOWN': 0, 'UNDERWATER': 0}
        for r in rows:
            st = str(r.get('eq_state') or r.get('eqstate') or 'UNKNOWN')
            if st in c:
                c[st] += 1
        out.append({'key': key, 'label': box.get('label', key), 'for': box.get('for', ''),
                    'n': len(rows), 'confirmed': c['CONFIRMED'], 'unknown': c['UNKNOWN'],
                    'underwater': c['UNDERWATER'], 'missing': False})
    return out


def freshness():
    """Fail-loud staleness. A digest built on a three-day-old board is a lie told confidently."""
    rows = []
    for fn, limit in (('leads_final.json', 30), ('docs/index.html', 30),
                      ('sale_history_cache.json', 30), ('skiptrace_results.json', 72)):
        h = _age_hours(fn)
        rows.append({'file': fn, 'hours': h, 'limit': limit,
                     'stale': (h is None or h > limit)})
        if h is None:
            ALERTS.append('%s does not exist -- the nightly did not produce it' % fn)
        elif h > limit:
            ALERTS.append('%s is %.0fh old (limit %dh) -- THE NIGHTLY DID NOT RUN CLEAN'
                          % (fn, h, limit))
    return rows


# ---- baseline --------------------------------------------------------------------------------
def _read_state():
    """{'current': {date, metrics}, 'previous': {date, metrics}}. Two slots, deliberately.

    ONE SLOT IS NOT ENOUGH, and the single-slot version failed its own test. With one slot the
    first run of a new day writes TODAY into it, so every later run that day compares today
    against itself and every delta collapses to zero -- the digest would report a quiet morning
    precisely on the day something moved, and it would look completely normal doing it.
    """
    d = _load(STATE) if os.path.exists(STATE) else ABSENT
    if d is ABSENT or not isinstance(d, dict):
        return {}
    if 'current' in d or 'previous' in d:
        return d
    # migrate the old flat shape rather than silently discarding a real baseline
    return {'current': {'date': d.get('date') or '', 'metrics': d.get('metrics') or {}}}


def read_baseline():
    """The metrics to diff against: yesterday's, and yesterday's ALL DAY."""
    s = _read_state()
    cur, prev = s.get('current') or {}, s.get('previous') or {}
    today = dt.date.today().isoformat()
    # on a same-day rerun `current` IS today, so the honest comparison is the slot behind it
    use = prev if (cur.get('date') == today) else cur
    return (use.get('metrics') or {}), (use.get('date') or '')


def roll_baseline(metrics, today):
    """Refresh today's slot; push the old one back only when the day actually changed."""
    s = _read_state()
    cur = s.get('current') or {}
    if cur.get('date') == today:
        s['current'] = {'date': today, 'metrics': metrics}      # same day: refresh, keep previous
    else:
        if cur.get('date'):
            s['previous'] = cur                                  # new day: yesterday steps back
        s['current'] = {'date': today, 'metrics': metrics}
    try:
        json.dump(s, open(STATE, 'w', encoding='utf-8'), indent=1)
        return True
    except Exception:
        return False


def _d(cur, base, key):
    """Delta string, or '' when there is no comparable baseline. Never invents a zero."""
    if key not in cur or key not in base:
        return ''
    diff = cur[key] - base[key]
    if diff == 0:
        return '  (no change)'
    return '  (%+d)' % diff


# ---- render ----------------------------------------------------------------------------------
def render(m, base, boxes, fresh, today, prev_date):
    L = []
    W = 66
    L.append('=' * W)
    L.append('  BSG MORNING DIGEST   %s' % today)
    L.append('=' * W)

    if ALERTS:
        L.append('')
        L.append('  !! ATTENTION -- %d problem(s) with the overnight run' % len(ALERTS))
        for a in ALERTS:
            L.append('     - %s' % a)

    if not base:
        L.append('')
        L.append('  (first run -- no baseline yet, so no deltas. Tomorrow this column fills in.)')
    elif prev_date:
        L.append('')
        L.append('  changes measured against %s' % prev_date)

    def line(label, key, suffix=''):
        if key not in m:
            L.append('  %-26s  --  not available' % label)
        else:
            L.append('  %-26s %5d%s%s' % (label, m[key], suffix, _d(m, base, key)))

    L.append('')
    L.append('  BOARD')
    line('leads on the board', 'leads')
    line('auction within 7 days', 'auc7')
    line('auction within 30 days', 'auc30')
    line('tier A', 'tierA')
    L.append('')
    L.append('  PIPELINE UPSTREAM')
    line('lis pendens (Miami-Dade)', 'lp')
    line('Palm Beach leads', 'pb')
    L.append('')
    L.append('  SUPPRESSED -- did NOT contact, on purpose')
    line('bankruptcy stay (sec 362)', 'bk')
    line('opt-out ledger', 'optouts')
    L.append('')
    L.append('  DATA QUALITY')
    line('equity flagged not-real', 'eqfake')
    line('reachable (phone or email)', 'reachable')
    line('no phone and no email', 'contactless')
    line('survived >=1 auction', 'reset1')
    line('survived >=3 auctions', 'reset3')
    L.append('')
    L.append('  INBOUND')
    line('replies logged', 'replies')
    line('mail pieces sent (total)', 'mailed')

    L.append('')
    L.append('  BUY-BOXES')
    if not boxes:
        L.append('    (none configured -- add one to BOXES in buybox.py)')
    for b in boxes:
        who = (' -- %s' % b['for']) if b.get('for') else ''
        L.append('    %s%s' % (b['label'], who))
        if b.get('missing'):
            L.append('      !! buybox_%s.json MISSING -- the nightly scan did not run' % b['key'])
            continue
        L.append('      %d match(es):  %d confirmed   %d unknown   %d underwater'
                 % (b['n'], b['confirmed'], b['unknown'], b['underwater']))
        if b['confirmed'] == 0 and b['unknown'] > 0:
            L.append('      -> nothing with verified equity. Next action: pull the mortgage on '
                     'the %d unknown.' % b['unknown'])

    L.append('')
    L.append('  FRESHNESS')
    for f in fresh:
        if f['hours'] is None:
            L.append('    %-26s  MISSING' % f['file'])
        else:
            L.append('    %-26s %5.1fh %s' % (f['file'], f['hours'],
                                              '  <-- STALE' if f['stale'] else ''))
    L.append('')
    L.append('=' * W)
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-save', action='store_true', help='stdout only, no Desktop copy')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    a = ap.parse_args()

    today = dt.date.today().isoformat()
    m = collect()
    boxes = buyboxes()
    fresh = freshness()
    base, prev_date = read_baseline()

    if a.json:
        print(json.dumps({'date': today, 'metrics': m, 'baseline': base,
                          'baseline_date': prev_date, 'boxes': boxes,
                          'freshness': [{k: v for k, v in f.items()} for f in fresh],
                          'alerts': ALERTS}, indent=1))
    else:
        txt = render(m, base, boxes, fresh, today, prev_date)
        try:
            print(txt)
        except UnicodeEncodeError:                      # cp1252 consoles in the nightly bat
            print(txt.encode('ascii', 'replace').decode('ascii'))
        if not a.no_save:
            try:
                p = os.path.join(DESK, 'DEALFLOW-MORNING-DIGEST.txt')
                open(p, 'w', encoding='utf-8').write(txt)
                print('\nsaved: %s' % p)
            except Exception as e:
                print('\n(could not write the Desktop copy: %s)' % e)

    roll_baseline(m, today)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        # Runs last in the nightly bat. A traceback here would bury the exit status of every
        # stage before it, so it degrades to a one-line complaint and a clean exit.
        print('morning_digest failed: %s' % e)
        sys.exit(0)
