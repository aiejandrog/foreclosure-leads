#!/usr/bin/env python
"""deal_desk.py — every YES that is rotting, ranked by how much it is bleeding.

WHY (2026-09-16 audit): the business does not lose deals at the pitch. It loses them AFTER the yes.
Every advanced-stage lead — Appointment, Offer made, Under contract, Callback, a verbal commit — had
the same shape: a yes, then no next action, then the sale date passed or the owner went cold. Five
appointments booked and abandoned; a retainer emailed to counsel and never chased; an offer that sat
until the sale date passed. There was no surface that said "Miranda said yes 26 days ago — what is the
next step and why hasn't it happened?". This is that surface.

WHAT IT IS. Reads worker_notes.json (the ledger of every human touch/dial/status) joined to the board
(value, debt, equity honesty, sale date) and skiptrace (phones). Emits, for every lead past mere
outreach, the ONE next action, days since anyone touched it, and days to the sale — ranked so the deal
closest to dying sits on top. Dead / DO NOT CONTACT / wrong-number / not-interested drop off; they are
decisions, not open work.

Run:  python deal_desk.py                 # write DEALFLOW\DEAL-DESK-<date>.md + deal_desk.json, print top
      python deal_desk.py --print         # ...and print the whole desk
      python deal_desk.py --email jesse    # ...and SMTP it to Jesse (celusa13@gmail.com)
"""
import argparse
import datetime
import json
import os
import re

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'deal_desk.json')
JESSE = 'celusa13@gmail.com'

# Stages that are OPEN WORK, with how committed the owner is (higher = closer to money) and the one
# next action that moves it. Anything not here (Dead, DO NOT CONTACT, Wrong number, Not interested,
# Called - no answer, Door - no answer) is a decision or a miss, not a live deal — it never shows.
STAGES = {
    'Under contract':  (100, 'PUSH TO CLOSE: confirm signature/court order, set the closing + collect the fee'),
    'Offer made':      (92,  'GET A DECISION: is the offer signed? yes/no today — the sale clock is running'),
    'Appointment':     (80,  'RUN/RECOVER THE CONSULT: did it happen? set the outcome and the next step'),
    'Callback':        (72,  'CALL BACK at the promised time — a callback you miss is a no'),
    'Gatekeeper':      (58,  'GET THE OWNER: work past the gatekeeper, pin a time the owner is there'),
    'Called - talked': (50,  'ADVANCE: verify the equity, then book the consult'),
    'Contacted':       (42,  'ADVANCE: get them talking — keep it or sell it — then book the consult'),
}
# a verbal yes hiding in a lower stage's note is really an Offer/Appointment — surface it as hot
_VERBAL_RE = re.compile(r'verbal|committed|retainer|TPA|signature|sign(ed|ing)?\b|stipulation|prefilled', re.I)


def _load(fn, default):
    try:
        with open(os.path.join(HERE, fn), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _parse_day(s):
    s = str(s or '')[:10]
    for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _last_touch(n):
    """(date, 'ch: out') of the most recent human touch OR dial. Morning-batch machine sends do not
    count as work — a lead the robot emailed is not a lead someone is working."""
    best = None
    for t in (n.get('touches') or []):
        if 'morning batch' in (t.get('out') or '').lower():
            continue
        d = _parse_day(t.get('d') or (t.get('ts') or '')[:10])
        if d and (best is None or d > best[0]):
            best = (d, '%s: %s' % (t.get('ch') or '?', (t.get('out') or '').strip()))
    for dl in (n.get('dials') or []):
        d = _parse_day(dl.get('d') or (dl.get('ts') or '')[:10])
        if d and (best is None or d > best[0]):
            best = (d, 'call: %s' % (dl.get('oc') or '').strip())
    return best or (None, '')


def _sale_date(r):
    return _parse_day(r.get('auction'))


def build(today=None):
    today = today or datetime.date.today()
    notes = (_load('worker_notes.json', {}).get('notes') or {})
    import sheets_crm as S
    rows = {r.get('case'): r for r in S._leads()}
    st = _load('skiptrace_results.json', {})
    desk = []
    for case, n in notes.items():
        status = (n.get('status') or '').strip()
        if status not in STAGES:
            continue
        if n.get('optout') or n.get('wrongown'):
            continue
        r = rows.get(case) or {}
        weight, action = STAGES[status]
        note = (n.get('note') or '')
        # a verbal commit noted under a soft stage is really hot — lift it and say so
        if status in ('Contacted', 'Called - talked', 'Callback') and _VERBAL_RE.search(note):
            weight = max(weight, 88)
            action = 'CHASE THE SIGNATURE: a commitment/retainer is in flight — get it signed, do not let it cool'
        ld, lout = _last_touch(n)
        days_since = (today - ld).days if ld else 999
        sd = _sale_date(r)
        d2s = (sd - today).days if sd else None
        nxt = _parse_day(n.get('next'))
        v = float(r.get('value') or 0)
        owed = float(r.get('payoff') or 0) or float(r.get('judg') or 0)
        eq = (v - owed) if (v and owed) else None
        # PRIORITY: how committed + how close to dying (sale clock) + how long neglected + a promised
        # callback that is overdue. A signed deal with a sale in 3 days that nobody touched in 10 = top.
        pri = weight
        if d2s is not None:
            if d2s < 0:
                pri -= 30                       # sale passed: surplus-only, still open but cooler
            elif d2s <= 3:
                pri += 70
            elif d2s <= 7:
                pri += 55
            elif d2s <= 14:
                pri += 35
            elif d2s <= 30:
                pri += 18
        pri += min(days_since, 30) * 2          # neglect compounds
        if nxt and nxt <= today:
            pri += 25                            # a next-step whose date has arrived or passed
        desk.append({
            'case': case, 'status': status, 'action': action,
            'owner': (r.get('owners') or '').split(';')[0].strip()[:34] or (n.get('owner') or ''),
            'addr': (r.get('addr') or '')[:40],
            'days_since': days_since, 'last': lout[:46],
            'sale': r.get('auction') or '', 'd2s': d2s,
            'next': n.get('next') or '', 'next_due': bool(nxt and nxt <= today),
            'eq': round(eq) if eq is not None else None, 'eqfake': bool(r.get('eqfake')),
            'phones': S._phones(case, st, n)[:2],
            'note': note[:140], 'pri': round(pri),
        })
    desk.sort(key=lambda x: -x['pri'])
    return desk, today


def write_md(desk, today):
    os.makedirs(P.DEALFLOW_DIR, exist_ok=True)
    path = os.path.join(P.DEALFLOW_DIR, 'DEAL-DESK-%s.md' % today.isoformat())
    hot = [d for d in desk if d['pri'] >= 120]
    L = ['# DEAL DESK — %s — every open yes, worst-bleeding first' % today.strftime('%a %m/%d/%Y'), '',
         '%d open deals. **%d are on fire** (committed and/or a sale bearing down and going stale). '
         'Work top-down. A yes with no next action is the thing this business keeps losing.' % (len(desk), len(hot)), '',
         '| # | Pri | Stage | Owner | Address | Sale | d→sale | Idle | Next action | Phone | Equity |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    for i, d in enumerate(desk, 1):
        eq = ('$%s%s' % (format(d['eq'], ','), ' ⚠unverified' if d['eqfake'] else '')) if d['eq'] is not None else '—'
        d2s = ('%dd' % d['d2s']) if d['d2s'] is not None else '—'
        if d['d2s'] is not None and d['d2s'] < 0:
            d2s = 'PASSED'
        L.append('| %d | %d | %s | %s | %s | %s | %s | %dd | %s | %s | %s |' % (
            i, d['pri'], d['status'], d['owner'], d['addr'], d['sale'], d2s, d['days_since'],
            d['action'], ' / '.join('(%s)%s-%s' % (p[:3], p[3:6], p[6:]) for p in d['phones']) or '—', eq))
    L += ['', '## Notes on the top deals', '']
    for d in desk[:12]:
        L.append('- **%s** (%s) — idle %dd, last: %s%s%s' % (
            d['owner'] or d['case'], d['status'], d['days_since'], d['last'] or 'never',
            ' · next: ' + d['next'] if d['next'] else '', ' · ' + d['note'] if d['note'] else ''))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    return path


def email_jesse(path, desk, today):
    import json as _j
    import outreach_email as O
    hot = [d for d in desk if d['pri'] >= 120]
    body = ['Deal desk %s — %d open deals, %d on fire. Full list attached below.' % (today.isoformat(), len(desk), len(hot)), '',
            'Top of the desk (work these first):']
    for d in desk[:10]:
        eq = ('$%s%s' % (format(d['eq'], ','), ' (UNVERIFIED equity)' if d['eqfake'] else '')) if d['eq'] is not None else 'equity unknown'
        d2s = ('sale in %dd' % d['d2s']) if (d['d2s'] is not None and d['d2s'] >= 0) else ('sale PASSED' if d['d2s'] is not None else 'no sale date')
        body.append('- %s (%s) — %s, idle %dd. %s. %s. Phone %s. %s' % (
            d['owner'] or d['case'], d['status'], d2s, d['days_since'], eq,
            d['action'], ' / '.join(d['phones']) or 'none on file', d['case']))
    body += ['', 'Rule: a yes with no next action is a lost deal. Nothing on this list sits.', '', 'Pulled from DealFlow ' + today.isoformat()]
    s = _j.loads(open(os.path.join(HERE, 'bsg_gmail.key'), encoding='utf-8').read())
    u, pw = s.split(':', 1)
    mid, resp = O._smtp_send(u.strip(), pw.strip(), 'Alejandro Gonzalez', JESSE,
                             'Deal desk %s — %d open deals, %d on fire' % (today.isoformat(), len(desk), len(hot)),
                             '\n'.join(body), from_addr=u.strip())
    return mid, resp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--print', action='store_true')
    ap.add_argument('--email', default='', help='"jesse" -> SMTP the desk to celusa13@gmail.com')
    a = ap.parse_args()
    desk, today = build()
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'built': today.isoformat(), 'count': len(desk), 'desk': desk}, f, indent=1)
    path = write_md(desk, today)
    hot = sum(1 for d in desk if d['pri'] >= 120)
    print('DEAL DESK: %d open deals, %d on fire -> %s' % (len(desk), hot, path))
    show = desk if a.print else desk[:12]
    for d in show:
        d2s = ('%+dd' % d['d2s']) if d['d2s'] is not None else '  -'
        print('  [%3d] %-14s %-30s idle %2dd sale %-10s %5s | %s' % (
            d['pri'], d['status'], (d['owner'] or d['case'])[:30], d['days_since'], d['sale'] or '-', d2s, d['action'][:48]))
    if a.email == 'jesse':
        try:
            mid, resp = email_jesse(path, desk, today)
            print('emailed Jesse (%s) -> %s' % (JESSE, mid))
        except Exception as e:
            print('email failed:', str(e)[:160])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
