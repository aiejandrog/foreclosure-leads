#!/usr/bin/env python
"""analyst.py -- the standing business analyst. Weekly judge, not a daily do-list.

morning_planner.py answers "what do I do today". THIS answers "is the business working" --
the question nobody asked for three weeks while 524 commits shipped and 8 calls were dialed.
Deterministic, no LLM: every number is recomputed from the ledgers on every run, so the
scorecard cannot drift from reality the way a narrative can.

Reads (all repo root):
    leads_final.json + *_leads.json + skiptrace_results.json   (via morning_planner loaders)
    mail_sent.json / bounced_emails.json / replies.json / optouts.json
    worker_notes.json   <- the tracker's localStorage pushed by send_server.py POST /notes.
                           If missing/stale the scorecard says so in red instead of silently
                           reporting zero dials as if that were measured truth.

Writes:
    ~/DEALFLOW/DealFlow-Scorecard/YYYY-MM-DD_scorecard.html   (the judge's verdict)
    ~/DEALFLOW/DealFlow-Scorecard/YYYY-MM-DD_scorecard.json   (machine-readable; the
                           next run reads the previous file for week-over-week deltas)

Run:    python analyst.py            # writes + opens the scorecard
        python analyst.py --no-open  # scheduled/silent
Schedule: "DealFlow Weekly Analyst" (setup-analyst-automation.ps1), Sundays 7:30 AM.
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
import webbrowser
from email.utils import parsedate_to_datetime

import morning_planner as MP
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(P.DEALFLOW_DIR, 'DealFlow-Scorecard')

# ---------------------------------------------------------------- model assumptions (EDIT HERE)
# Revenue model B = attorney-routed stop-sale fees (FS 501.1377 exemption). Chosen by the
# 2026-08-02 business review as the PRIMARY model: shorter cycle, provable demand, every
# contactable lead is worth the floor fee. Model A (assignment) stays as upside, not plan.
FEE_PER_DEAL = 2500            # $ per closed stop-sale engagement (Jose's quoted 2000-2800 mid)
DEALS_TO_10K = 4               # ceil(10000 / FEE_PER_DEAL)
CONVO_TO_DEAL = 0.10           # ASSUMPTION: 10% of live conversations become paid engagements.
                               # Jose claims 80% of inbound CALLERS close -- that is for callers
                               # responding to mail, not outbound convos. 10% is the conservative
                               # planning number until 20+ conversations exist to measure it.
# Weekly activity floors (the RESTRUCTURED targets, not the 30-40/day playbook fantasy):
TARGET_DIALS_WK = 25           # Alejandro 5 warm+reply dials/day x 5 days
TARGET_EMAILS_WK = 200         # 40-50/day cap x ~4-5 send days
TARGET_DOORS_WK = 5            # Carlos field visits (playbook: 5-10/wk)

# Dimensions scored from a full strategist review, not measurable weekly. Carried until the
# next deep review updates them. (source: DealFlow Business Review 2026-08-02)
STATIC_SCORES = {
    'team_leverage':  {'score': 3.0, 'asof': '2026-08-02'},
    'tooling_roi':    {'score': 2.5, 'asof': '2026-08-02'},
    'founder_focus':  {'score': 4.0, 'asof': '2026-08-02'},
}
WEIGHTS = {  # must sum to 1.0 -- the review rubric
    'data_quality': 0.15, 'outreach': 0.25, 'followup': 0.20,
    'team_leverage': 0.10, 'compliance': 0.10, 'tooling_roi': 0.10, 'founder_focus': 0.10,
}

OUTREACH_CH = {'call', 'text', 'email', 'letter', 'door'}


# ---------------------------------------------------------------- helpers
def _now():
    return dt.datetime.now()


def _days_ago(iso_or_dt, now=None):
    now = now or _now()
    if isinstance(iso_or_dt, str):
        try:
            iso_or_dt = dt.datetime.fromisoformat(iso_or_dt.replace('Z', '+00:00'))
        except Exception:
            return None
    if iso_or_dt.tzinfo:
        iso_or_dt = iso_or_dt.astimezone().replace(tzinfo=None)
    return (now - iso_or_dt).total_seconds() / 86400.0


def _real_sends(mail):
    return [e for e in mail if e.get('ch') == 'email' and not e.get('test_mode')
            and not e.get('error') and e.get('message_id')]


def _touches(wnotes):
    """Flatten worker_notes notes[case].touches -> [(case, touch), ...]."""
    out = []
    for c, n in (wnotes.get('notes') or {}).items():
        for t in (n.get('touches') or []):
            out.append((c, t))
    return out


def _t_dt(t):
    """Best-effort datetime for a touch: epoch ms tsu, else the d date."""
    if t.get('tsu'):
        try:
            return dt.datetime.fromtimestamp(int(t['tsu']) / 1000.0)
        except Exception:
            pass
    try:
        return dt.datetime.fromisoformat(str(t.get('d') or '')[:10])
    except Exception:
        return None


def _in_last(days, when, now=None):
    d = _days_ago(when, now) if not isinstance(when, (int, float)) else when
    return d is not None and 0 <= d <= days


# ---------------------------------------------------------------- metric computation
def compute(now=None):
    now = now or _now()
    leads = MP._load_leads()
    mail = MP._load_json('mail_sent.json', [])
    bounced = MP._load_json('bounced_emails.json', {})
    replies = MP._load_json('replies.json', {})
    wn = MP._load_json('worker_notes.json', {})

    m = {'generated': now.strftime('%Y-%m-%d %H:%M'), 'date': now.strftime('%Y-%m-%d')}

    # -- board + reachability --------------------------------------------------------------
    m['leads_total'] = len(leads)
    m['leads_phone'] = sum(1 for r in leads if MP._has_phone(r))
    m['leads_email'] = sum(1 for r in leads if MP._has_email(r))
    m['leads_unreachable'] = sum(1 for r in leads if not MP._has_phone(r) and not MP._has_email(r))
    m['urgent_reachable'] = sum(1 for r in leads
                                if (MP._days(r) is not None and MP._days(r) <= 7)
                                and (MP._has_phone(r) or MP._has_email(r)))

    # -- email channel (ledger truth) ------------------------------------------------------
    sends = _real_sends(mail)
    bounced_set = {a.lower() for a in bounced} if isinstance(bounced, dict) else set()
    m['emails_ever'] = len(sends)
    m['emails_delivered_ever'] = sum(1 for e in sends if str(e.get('to', '')).lower() not in bounced_set)
    wk = [e for e in sends if _in_last(7, e.get('ts_utc') or '', now)]
    m['emails_7d'] = len(wk)
    m['emails_bounced_7d'] = sum(1 for e in wk if str(e.get('to', '')).lower() in bounced_set)
    m['bounce_rate_7d'] = round(100.0 * m['emails_bounced_7d'] / m['emails_7d'], 1) if m['emails_7d'] else 0.0

    # -- worker notes: dials / doors / texts / conversations -------------------------------
    m['worker_notes_present'] = bool(wn.get('notes'))
    m['worker_notes_age_d'] = None
    if wn.get('received_utc'):
        m['worker_notes_age_d'] = round(_days_ago(wn['received_utc'], now) or 0, 1)
    tch = _touches(wn)
    for ch in ('call', 'text', 'door', 'letter'):
        allc = [(c, t) for c, t in tch if t.get('ch') == ch]
        m[f'{ch}s_ever'] = len(allc)
        m[f'{ch}s_7d'] = sum(1 for c, t in allc if (d := _t_dt(t)) and _in_last(7, _days_ago(d, now), now))
    # 'talked' exact-match missed every disposition the worker writes ("Talked", "Talked — real
    # conversation", "Talked (queue panel)") — the scorecard said 0 conversations while dispositions
    # sat in the ledger. Substring match on 'talk' catches all writers, past and present.
    def _is_talk(t):
        return 'talk' in str(t.get('out') or '').lower()
    m['convos_ever'] = sum(1 for c, t in tch if _is_talk(t))
    m['convos_7d'] = sum(1 for c, t in tch
                         if _is_talk(t) and (d := _t_dt(t)) and _in_last(7, _days_ago(d, now), now))
    dial_days = {t.get('d') for c, t in tch
                 if t.get('ch') == 'call' and (d := _t_dt(t)) and _in_last(7, _days_ago(d, now), now)}
    m['dial_days_7d'] = len(dial_days)

    # -- DIAL-THROUGH: the 0.5% leak the 2026-08-09 audit found ---------------------------------
    # 192 leads were queued to call in 6 days and one dial came out, and no number anywhere showed
    # it. workerLog is the durable ledger: 'callq' = queued, 'callout' = a logged disposition
    # (legacy dials wrote kind 'call' with detail 'call outcome: ...' — count those too).
    wlog = wn.get('workerLog') or []
    def _wl_days_ago(e):
        try:
            return (now.date() - dt.date.fromisoformat(str(e.get('d') or ''))).days
        except Exception:
            return 9999
    _wl7 = [e for e in wlog if isinstance(e, dict) and _wl_days_ago(e) <= 7]
    m['callq_7d'] = sum(1 for e in _wl7 if e.get('a') == 'callq')
    m['dials_disposed_7d'] = sum(1 for e in _wl7 if e.get('a') == 'callout'
                                 or (e.get('a') == 'call' and str(e.get('detail','')).startswith('call outcome:')))
    m['dial_through_pct'] = (round(100.0 * m['dials_disposed_7d'] / m['callq_7d'], 1)
                             if m['callq_7d'] else None)


    # -- pipeline stages from statuses -----------------------------------------------------
    stat = {}
    for c, n in (wn.get('notes') or {}).items():
        s = str(n.get('status') or '').strip()
        if s:
            stat[s] = stat.get(s, 0) + 1
    m['statuses'] = stat
    m['appointments'] = stat.get('Appointment', 0)
    m['offers'] = stat.get('Offer made', 0)
    m['contracts'] = stat.get('Under contract', 0) + stat.get('Assigned', 0)
    m['closes'] = sum(v for k, v in stat.items() if k.upper().startswith('CLOSED'))

    # -- replies + the SLA that killed the Capri lead --------------------------------------
    rep_cases = {k: v for k, v in replies.items()
                 if not str(k).startswith('@') and not str(k).startswith('_') and isinstance(v, dict)}
    m['replies_total'] = len(rep_cases)
    m['replies_hot'] = sum(1 for v in rep_cases.values() if not v.get('stop'))
    sla = []
    for case, v in rep_cases.items():
        if v.get('stop'):
            continue
        try:
            when = parsedate_to_datetime(v['when']).astimezone().replace(tzinfo=None)
        except Exception:
            continue
        answered = False
        for c, t in tch:
            if c != case or t.get('ch') not in ('call', 'text'):
                continue
            td = _t_dt(t)
            if td and td >= when:
                answered = True
                break
        if not answered:
            sla.append({'case': case, 'email': v.get('email', ''),
                        'days_waiting': round(_days_ago(when, now) or 0, 1),
                        'excerpt': (v.get('excerpt') or '')[:90]})
    m['reply_sla_violations'] = sla
    replies_checked = next((v.get('checked') for v in replies.values()
                            if isinstance(v, dict) and v.get('checked')), None)
    m['replies_checked_age_d'] = round(_days_ago(replies_checked, now) or 99, 1) if replies_checked else None

    # -- compliance flags ------------------------------------------------------------------
    flags = []
    #  touches after a recorded opt-out (the cure-trail check the board runs per-device)
    for c, n in (wn.get('notes') or {}).items():
        if not n.get('optout'):
            continue
        try:
            od = dt.datetime.fromisoformat(str(n['optout'])[:10])
        except Exception:
            continue
        for t in (n.get('touches') or []):
            td = _t_dt(t)
            if td and td > od and t.get('ch') in OUTREACH_CH:
                flags.append(f'OPT-OUT VIOLATION: {c} touched ({t.get("ch")}) after opt-out {n["optout"]}')
    #  >3 telephonic touches on one lead in one day (FTSA 3-in-24h)
    percase_day = {}
    for c, t in tch:
        if t.get('ch') in ('call', 'text'):
            percase_day[(c, t.get('d'))] = percase_day.get((c, t.get('d')), 0) + 1
    for (c, d0), n0 in percase_day.items():
        if n0 > 3:
            flags.append(f'FTSA TOUCH CAP: {c} had {n0} telephonic touches on {d0} (max 3/24h)')
    if m['bounce_rate_7d'] > 5:
        flags.append(f'SPAM-REPUTATION RISK: {m["bounce_rate_7d"]}% bounce rate this week '
                     f'(provider tolerance ~2%) on the personal Gmail')
    m['compliance_flags'] = flags

    # -- scores ----------------------------------------------------------------------------
    sc = {}
    sc['data_quality'] = round(10 * (0.6 * m['leads_phone'] + 0.4 * m['leads_email']) / max(1, m['leads_total']), 1)
    dial_part = min(1.0, m['calls_7d'] / TARGET_DIALS_WK)
    mail_part = min(1.0, m['emails_7d'] / TARGET_EMAILS_WK)
    door_part = min(1.0, m['doors_7d'] / TARGET_DOORS_WK)
    sc['outreach'] = round(10 * (0.5 * dial_part + 0.3 * mail_part + 0.2 * door_part), 1)
    if m['replies_hot']:
        sc['followup'] = round(max(0.0, 10.0 * (1 - len(sla) / m['replies_hot'])), 1)
    else:
        sc['followup'] = 5.0  # nothing to follow up = neutral, not good
    comp = 10.0
    for f in flags:
        comp -= 4 if 'VIOLATION' in f else 2
    sc['compliance'] = round(max(0.0, comp), 1)
    for k, v in STATIC_SCORES.items():
        sc[k] = v['score']
    if not m['worker_notes_present']:
        # without the notes bridge nothing activity-based is measured -- refuse to flatter
        sc['outreach'] = min(sc['outreach'], round(10 * 0.3 * mail_part, 1))
    m['scores'] = sc
    m['overall'] = round(sum(sc[k] * WEIGHTS[k] for k in WEIGHTS), 1)
    m['grade'] = ('A' if m['overall'] >= 8.5 else 'B' if m['overall'] >= 7 else
                  'C' if m['overall'] >= 5.5 else 'D' if m['overall'] >= 4 else 'F')

    # -- pace to $10k ----------------------------------------------------------------------
    m['fee_model'] = f'${FEE_PER_DEAL:,}/deal (attorney stop-sale) -> {DEALS_TO_10K} deals to $10k'
    if m['convos_7d'] > 0:
        wk_deals = m['convos_7d'] * CONVO_TO_DEAL
        m['pace_weeks_to_10k'] = round(DEALS_TO_10K / wk_deals, 1)
        m['pace_note'] = (f'{m["convos_7d"]} conversations/wk x {int(CONVO_TO_DEAL*100)}% '
                          f'(assumed) = {wk_deals:.2f} deals/wk -> ~{m["pace_weeks_to_10k"]:.0f} weeks to $10k')
    else:
        m['pace_weeks_to_10k'] = None
        m['pace_note'] = ('ZERO live conversations this week -> no pace exists. '
                          'At 0 conversations the projected date to $10k is NEVER.')

    # -- suggestions (rule-ranked, top 3) --------------------------------------------------
    sug = []
    # Cuban's red line (2026-08-09 re-judge): a measured 0.0% nobody is forced to look at becomes
    # wallpaper in two weeks. Under 25% dial-through on a meaningful queue outranks everything
    # except an unanswered reply — a call list nobody dials is the whole business failing quietly.
    if m.get('callq_7d', 0) >= 10 and (m.get('dial_through_pct') or 0.0) < 25.0:
        sug.append(('P0', f'THE CALL QUEUE IS NOT BEING DIALED: {m["callq_7d"]} leads queued in 7 days, '
                          f'{m["dials_disposed_7d"]} disposed ({m["dial_through_pct"] or 0.0}%). '
                          f'Every one of these asked for a phone call by having no email. Block out one '
                          f'5:30-6:00 PM window and clear ten today.'))
    for v in sla:
        sug.append(('P0', f'CALL THE REPLY BACK: {v["email"]} (case {v["case"]}) has waited '
                          f'{v["days_waiting"]:.0f} days since writing back. A reply is the warmest '
                          f'signal the system produces — this is deal #1 leaking.'))
    if not m['worker_notes_present'] or (99 if m['worker_notes_age_d'] is None else m['worker_notes_age_d']) > 7:
        sug.append(('P0', 'Open the board once with the send bridge running — the call-history '
                          'backup (worker_notes.json) is missing/stale, so this scorecard is '
                          'blind to dials and dispositions.'))
    _rage = 99 if m['replies_checked_age_d'] is None else m['replies_checked_age_d']
    if _rage > 2:
        sug.append(('P1', f'Inbox unchecked for {_rage:.0f} days — run replies.py '
                          f'(or verify the daily task): replies may be sitting unseen.'))
    if m['calls_7d'] < TARGET_DIALS_WK:
        sug.append(('P1', f'{m["calls_7d"]}/{TARGET_DIALS_WK} weekly dials. Protect the 30-minute '
                          f'golden window: warm + reply-driven calls only, phone in hand, timer on.'))
    if m['doors_7d'] < TARGET_DOORS_WK:
        sug.append(('P1', f'{m["doors_7d"]}/{TARGET_DOORS_WK} Carlos door visits logged this week. '
                          f'His door produced the best contact the business has (Spong). Feed him a field sheet.'))
    if m['bounce_rate_7d'] > 5:
        sug.append(('P2', f'{m["bounce_rate_7d"]}% bounce rate — verify addresses before sending '
                          f'(MX check) or the Gmail account gets burned.'))
    if m['urgent_reachable'] and m['calls_7d'] == 0:
        sug.append(('P2', f'{m["urgent_reachable"]} URGENT leads (sale <=7d) are reachable right now '
                          f'and nobody has dialed this week.'))
    m['suggestions'] = sug[:3]

    # -- questions for the Jose call -------------------------------------------------------
    q = []
    if m['offers'] == 0 and m['contracts'] == 0:
        q.append('Zero offers and zero contracts on the board. What specifically closes this month, '
                 'and who owns each next action?')
    q.append(f'{m["convos_7d"]} live conversations this week against {m["leads_phone"]} leads with '
             f'phones. Whose job is volume now that the tool is built — and does the 33/5 split '
             f'still match who does what?')
    if m['replies_hot'] and sla:
        q.append('A seller who replied in writing was never called. What is our reply SLA — and who '
                 'is accountable for it?')
    q.append('The $2,500 stop-fee only works through the attorney (FS 501.1377). Is the attorney '
             'engagement letter actually in place to route fees, yes or no?')
    m['jose_questions'] = q[:4]
    return m


# ---------------------------------------------------------------- week-over-week
def _prev_data(today_str):
    files = sorted(glob.glob(os.path.join(OUT_DIR, '*_scorecard.json')))
    files = [f for f in files if today_str not in os.path.basename(f)]
    if not files:
        return None
    try:
        return json.load(open(files[-1], encoding='utf-8'))
    except Exception:
        return None


def _delta(cur, prev, key):
    if not prev or key not in prev or not isinstance(prev.get(key), (int, float)):
        return ''
    d = cur.get(key, 0) - prev[key]
    if d == 0:
        return '<span class="d flat">±0</span>'
    cls, sign = ('up', '+') if d > 0 else ('down', '')
    return f'<span class="d {cls}">{sign}{round(d,1)}</span>'


# ---------------------------------------------------------------- render
DIM_LABELS = {
    'data_quality': ('Lead data & acquisition', 'auto: phone/email coverage'),
    'outreach': ('Outreach execution', 'auto: dials + emails + doors vs weekly floors'),
    'followup': ('Conversion & follow-up', 'auto: reply-SLA compliance'),
    'team_leverage': ('Team & role leverage', 'from deep review'),
    'compliance': ('Compliance & risk', 'auto: violations, caps, bounce'),
    'tooling_roi': ('Tooling & automation ROI', 'from deep review'),
    'founder_focus': ('Founder focus', 'from deep review'),
}


def render(m, prev):
    def bar(v):
        pct = int(v * 10)
        col = '#1e7a3c' if v >= 7 else '#b58a1f' if v >= 5 else '#b3372f'
        return (f'<div class="bar"><div class="fill" style="width:{pct}%;background:{col}"></div>'
                f'<span>{v:.1f}</span></div>')

    rows = ''
    for k in WEIGHTS:
        lab, src = DIM_LABELS[k]
        asof = STATIC_SCORES.get(k, {}).get('asof')
        rows += (f'<tr><td>{lab}<div class="src">{src}{" · " + asof if asof else ""}</div></td>'
                 f'<td class="w">{int(WEIGHTS[k]*100)}%</td><td>{bar(m["scores"][k])}</td></tr>')

    fun = [
        ('Leads on board', m['leads_total'], 'leads_total'),
        ('&nbsp;&nbsp;reachable (phone)', m['leads_phone'], 'leads_phone'),
        ('Emails delivered · 7d', m['emails_7d'] - m['emails_bounced_7d'], None),
        ('Dials · 7d', m['calls_7d'], 'calls_7d'),
        ('Doors · 7d', m['doors_7d'], 'doors_7d'),
        ('Live conversations · 7d', m['convos_7d'], 'convos_7d'),
        ('Call queue added · 7d', m.get('callq_7d', 0), 'callq_7d'),
        ('Dials disposed · 7d', m.get('dials_disposed_7d', 0), 'dials_disposed_7d'),
        ('Dial-through %', (str(m['dial_through_pct']) + '%') if m.get('dial_through_pct') is not None else '—', None),
        ('Replies (all-time hot)', m['replies_hot'], 'replies_hot'),
        ('Appointments', m['appointments'], 'appointments'),
        ('Offers out', m['offers'], 'offers'),
        ('Contracts', m['contracts'], 'contracts'),
        ('CLOSED', m['closes'], 'closes'),
    ]
    frows = ''.join(f'<tr><td>{n}</td><td class="n">{v}</td><td>{_delta(m, prev, k) if k else ""}</td></tr>'
                    for n, v, k in fun)

    warn = ''
    if not m['worker_notes_present']:
        warn = ('<div class="alert">worker_notes.json is missing — dial/door/status numbers below are '
                'BLIND, not zero-by-measurement. Open the board once with the send bridge running.</div>')
    elif (m['worker_notes_age_d'] or 0) > 7:
        warn = (f'<div class="alert">Call-history backup is {m["worker_notes_age_d"]:.0f} days stale — '
                f'activity numbers may be undercounted.</div>')

    flags = ''.join(f'<li>{f}</li>' for f in m['compliance_flags']) or '<li class="ok">No violations detected in the ledgers.</li>'
    sugs = ''.join(f'<li><b class="{p.lower()}">{p}</b> {s}</li>' for p, s in m['suggestions']) \
           or '<li class="ok">All weekly floors met.</li>'
    qs = ''.join(f'<li>{q}</li>' for q in m['jose_questions'])
    sla_rows = ''.join(f'<li><b>{v["email"]}</b> · waiting {v["days_waiting"]:.0f}d · “{v["excerpt"]}”</li>'
                       for v in m['reply_sla_violations'])

    return f'''<!doctype html><html><head><meta charset="utf-8">
<title>DealFlow Scorecard — {m['date']}</title>
<style>
 body{{font:14px/1.5 'Segoe UI',Arial,sans-serif;color:#1b2437;background:#f7f8fb;margin:0;padding:28px}}
 .wrap{{max-width:880px;margin:0 auto}}
 h1{{font-size:21px;letter-spacing:.06em;margin:0 0 2px;color:#122048}}
 h1 span{{color:#b58a1f}}
 .sub{{color:#6b7488;font-size:12.5px;margin-bottom:20px}}
 .grade{{float:right;text-align:center;background:#122048;color:#fff;border-radius:12px;padding:14px 22px}}
 .grade b{{font-size:34px;display:block}}
 .grade i{{font-style:normal;font-size:11px;color:#c9b26a;letter-spacing:.15em}}
 h2{{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#b58a1f;margin:26px 0 8px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e4e7ef;border-radius:10px;overflow:hidden}}
 td{{padding:8px 12px;border-top:1px solid #eef0f5;vertical-align:middle}}
 tr:first-child td{{border-top:0}}
 .src{{font-size:10.5px;color:#8b93a7}}
 .w{{color:#8b93a7;width:44px}}
 .n{{font-weight:700;width:60px;text-align:right}}
 .bar{{position:relative;background:#edeff4;height:16px;border-radius:8px;width:230px}}
 .fill{{height:16px;border-radius:8px}}
 .bar span{{position:absolute;right:6px;top:0;font-size:11px;font-weight:700;line-height:16px}}
 .d{{font-size:11.5px;font-weight:700}} .d.up{{color:#1e7a3c}} .d.down{{color:#b3372f}} .d.flat{{color:#8b93a7}}
 .alert{{background:#fdecea;border:1px solid #e7b3ae;color:#8f2b23;padding:10px 14px;border-radius:9px;margin:14px 0;font-weight:600}}
 .pace{{background:#fff;border:1px solid #e4e7ef;border-left:4px solid #b58a1f;border-radius:10px;padding:12px 16px}}
 ul{{background:#fff;border:1px solid #e4e7ef;border-radius:10px;margin:0;padding:10px 14px 10px 30px}}
 li{{margin:7px 0}} li.ok{{color:#1e7a3c}}
 b.p0{{color:#b3372f}} b.p1{{color:#b58a1f}} b.p2{{color:#4a5878}}
 .foot{{color:#9aa1b3;font-size:11px;margin-top:26px}}
</style></head><body><div class="wrap">
<div class="grade"><b>{m['grade']}</b><i>{m['overall']}/10</i></div>
<h1>DEALFLOW <span>WEEKLY SCORECARD</span></h1>
<div class="sub">Generated {m['generated']} · every number recomputed from ledgers · {m['fee_model']}</div>
{warn}
<h2>Pace to $10,000</h2>
<div class="pace">{m['pace_note']}</div>
<h2>Score — weighted {m['overall']}/10 ({m['grade']})</h2>
<table>{rows}</table>
<h2>Funnel · last 7 days (Δ vs previous scorecard)</h2>
<table>{frows}</table>
{('<h2>Unanswered replies — the money leak</h2><ul>' + sla_rows + '</ul>') if sla_rows else ''}
<h2>Top actions this week</h2>
<ul>{sugs}</ul>
<h2>Compliance</h2>
<ul>{flags}</ul>
<h2>Questions for the Jose call</h2>
<ul>{qs}</ul>
<div class="foot">analyst.py · deterministic · assumptions marked in-line · deep-review baselines dated where used</div>
</div></body></html>'''


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--no-open', action='store_true', help='write files without opening the browser')
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    m = compute()
    prev = _prev_data(m['date'])
    html = render(m, prev)

    hp = os.path.join(OUT_DIR, f'{m["date"]}_scorecard.html')
    jp = os.path.join(OUT_DIR, f'{m["date"]}_scorecard.json')
    with open(hp, 'w', encoding='utf-8') as f:
        f.write(html)
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(m, f, indent=1, ensure_ascii=False)

    print(f'scorecard: {hp}')
    print(f'  overall {m["overall"]}/10 ({m["grade"]}) | dials 7d: {m["calls_7d"]} | '
          f'convos 7d: {m["convos_7d"]} | offers: {m["offers"]} | sla violations: {len(m["reply_sla_violations"])}')
    if not args.no_open:
        webbrowser.open('file:///' + hp.replace('\\', '/'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
