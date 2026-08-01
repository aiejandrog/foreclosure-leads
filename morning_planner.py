#!/usr/bin/env python
"""morning_planner.py -- auto-generate the daily MSG standup agenda.

The blank meeting template was the wrong shape. Alejandro wants Jose and Carlos to join and
have the day's priorities already framed on the page, not have to fill in what matters each
morning. This reads the live DealFlow board and produces a dated HTML agenda with:

    * every URGENT lane lead (sale within 7 days) named individually, sorted soonest first
    * ACTIVE + EARLY lane counts and the top few in each
    * new REPLIES since yesterday (from replies.json)
    * portfolio owners -- one contact who owes you multiple deals worth ONE call
    * the day's OPT-IN / OPT-OUT pulse (from optouts.json)
    * blocker signal: how many leads sit with no traced phone or email
    * board-age warning if the last refresh is >= 2 days old
    * a standing 5-item agenda + editable Decisions / Action Items / Parking Lot

Usage:
    python morning_planner.py                             # today, saves to Desktop, opens browser
    python morning_planner.py --no-open                   # skip auto-open
    python morning_planner.py --date 2026-08-05           # generate for a specific date
    python morning_planner.py --focus "closing prep"      # pre-fill Today's Focus box
    python morning_planner.py --out X.html                # override output path

Output default: ~/OneDrive/Desktop/MSG-Meeting-Agendas/YYYY-MM-DD_standup.html

Cron-friendly: add to Windows Task Scheduler at 6:45 AM ET on weekdays; every morning the
day's agenda is on Alejandro's Desktop before he opens the laptop.
"""
import argparse
import datetime as dt
import glob
import html
import json
import os
import re
import sys
import webbrowser
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT_DIR = os.path.expanduser('~/OneDrive/Desktop/MSG-Meeting-Agendas')

_COMPANY_RE = re.compile(
    r'\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|TRUST|LP|LLP|COMPANY|CO\.|ASSOC|ASSOCIATION|BANK|'
    r'HOLDINGS|PROPERTY|PROPERTIES|REALTY|REAL ESTATE|INVESTMENTS?|CAPITAL|FUND|ENTERPRISES|'
    r'GROUP|VENTURES|PARTNERS|SERVICING|MORTGAGE|REO|MANAGEMENT)\b', re.I)


THEMES = {
    0: ('Monday', 'Week planning',
        'Set the week. Which leads to hit hard, which auctions land, mail/email budgets, target closings.'),
    1: ('Tuesday', 'Outreach + call block',
        "Yesterday's replies, who to call today, mail batches queued, who owns which follow-up."),
    2: ('Wednesday', 'Deal review',
        "Every live offer + every property under contract. Numbers, timelines, blockers. What's stuck?"),
    3: ('Thursday', 'Field / legal',
        'Door runs, walk-throughs, cash-for-keys tenants, signed papers, attorney escalations.'),
    4: ('Friday', 'Accountability + numbers',
        'Week-in-review. Doors knocked, letters mailed, calls made, offers out, dollars in. Miss the number? Why?'),
    5: ('Saturday', 'Weekend catch-up',
        'Optional. Field work, drive-bys, review of the week, plan next week.'),
    6: ('Sunday', 'Weekly reset',
        'Optional. Set the week ahead. Refresh the board. Prep Monday.'),
}


# ---------------------------------------------------------------- data loading
def _owner(r):
    raw = str(r.get('owners') or r.get('oname') or r.get('owner') or '').split(';')[0].strip()
    if ',' in raw:
        last, first = raw.split(',', 1)
        raw = f'{first.strip()} {last.strip()}'
    return raw.title() or '(no owner on record)'


def _addr_short(r):
    a = str(r.get('addr') or r.get('Address') or '')
    return (a.split(',')[0] or a).strip()


def _case(r):
    return str(r.get('case') or r.get('Case #') or r.get('cert') or '').strip()


def _days(r):
    try:
        d = r.get('days_to_auction') if r.get('days_to_auction') is not None else r.get('days')
        return int(d) if d is not None else None
    except Exception:
        return None


def _sale(r):
    return str(r.get('auction') or r.get('AuctionDate') or '').strip()


def _tier(r):
    return str(r.get('tier') or '').upper()


def _plaintiff(r):
    return str(r.get('plaintiff') or '').strip()


def _is_company(r):
    return bool(_COMPANY_RE.search(str(r.get('owners') or r.get('oname') or '')))


def _has_email(r):
    for e in (r.get('emails') or []):
        if e and '@' in str(e):
            return True
    return False


def _has_phone(r):
    for p in (r.get('phones') or []):
        if p:
            return True
    return False


def _load_leads():
    leads = []
    for path in [os.path.join(HERE, 'leads_final.json')] + sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        base = os.path.basename(path)
        if base.startswith('_') or 'raw' in base:
            continue
        try:
            leads += json.load(open(path, encoding='utf-8'))
        except Exception:
            pass
    # merge skiptrace so emails/phones show up
    st_path = os.path.join(HERE, 'skiptrace_results.json')
    if os.path.exists(st_path):
        try:
            st = json.load(open(st_path, encoding='utf-8'))
        except Exception:
            st = {}
        for r in leads:
            hit = st.get(_case(r)) or {}
            if hit:
                if not (r.get('emails') or []):
                    r['emails'] = list(hit.get('emails') or [])[:3]
                if not (r.get('phones') or []):
                    r['phones'] = list(hit.get('phones') or [])[:3]
    return leads


def _load_json(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def _board_age_days():
    """How stale is the built board? Reads docs/index.html's coverage stamp when possible."""
    p = os.path.join(HERE, 'docs', 'index.html')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8', errors='ignore') as f:
            head = f.read(200_000)
        m = re.search(r'"built":"(\d{4}-\d{2}-\d{2})T', head)
        if not m:
            return None
        d = dt.date.fromisoformat(m.group(1))
        return (dt.date.today() - d).days
    except Exception:
        return None


# ---------------------------------------------------------------- signals
def _urgent_leads(leads, limit=25):
    rows = []
    for r in leads:
        d = _days(r)
        if d is None or d < 0 or d > 7:
            continue
        rows.append(r)
    rows.sort(key=lambda r: (_days(r) or 0, {'A': 0, 'B': 1, 'C': 2}.get(_tier(r), 3)))
    return rows[:limit]


def _active_count(leads):
    return sum(1 for r in leads if (_days(r) is not None and 8 <= _days(r) <= 45))


def _early_count(leads):
    return sum(1 for r in leads if (r.get('st') == 'LP' or r.get('stage') == 'LP')
               or _days(r) is None)


def _portfolios(leads):
    """One primary email -> N leads. Same shape the worker uses."""
    by_email = defaultdict(list)
    for r in leads:
        em = ''
        for e in (r.get('emails') or []):
            if e and '@' in str(e):
                em = str(e).lower().strip()
                break
        if em:
            by_email[em].append(r)
    out = []
    for em, rs in by_email.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda r: _days(r) if _days(r) is not None else 99999)
        owner = _owner(rs[0])
        out.append({'email': em, 'owner': owner, 'count': len(rs), 'leads': rs})
    out.sort(key=lambda x: (-x['count'], _days(x['leads'][0]) if _days(x['leads'][0]) is not None else 99999))
    return out


def _blockers(leads):
    n = 0
    for r in leads:
        if _is_company(r):
            continue
        if not _has_phone(r) and not _has_email(r):
            n += 1
    return n


def _new_replies(replies_data, since_days=1):
    """replies.json shape varies by build; this handles the two we've seen."""
    if not replies_data:
        return []
    if isinstance(replies_data, dict):
        entries = list(replies_data.values())
    elif isinstance(replies_data, list):
        entries = replies_data
    else:
        return []
    cutoff = dt.date.today() - dt.timedelta(days=since_days)
    hits = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        w = str(e.get('when') or e.get('date') or e.get('d') or '')
        try:
            d = dt.date.fromisoformat(w[:10])
        except Exception:
            continue
        if d >= cutoff:
            hits.append(e)
    return hits


# ---------------------------------------------------------------- render
def _esc(s):
    return html.escape(str(s) if s is not None else '')


def render(day_dt, leads, replies, optouts, focus_override, mail_ledger):
    weekday = day_dt.weekday()
    day_name, theme_label, theme_hint = THEMES[weekday]

    urgent = _urgent_leads(leads, limit=25)
    active_n = _active_count(leads)
    early_n = _early_count(leads)
    portfolios = _portfolios(leads)
    blockers = _blockers(leads)
    board_age = _board_age_days()
    new_replies = _new_replies(replies, since_days=1)
    opt_count = 0
    if isinstance(optouts, dict):
        opt_count = sum(1 for v in optouts.values() if v)
    elif isinstance(optouts, list):
        opt_count = len(optouts)
    mail_today = 0
    if isinstance(mail_ledger, list):
        today_iso = dt.date.today().isoformat()
        mail_today = sum(1 for e in mail_ledger
                         if e.get('ch') == 'email' and str(e.get('d') or '') == today_iso)

    banner_bits = []
    if board_age is not None and board_age >= 2:
        banner_bits.append(f'<div class="warn">⚠ Board data is <b>{board_age} days old</b>. Run '
                           f'<code>python refresh.py</code> before making bids.</div>')
    if not urgent:
        banner_bits.append('<div class="ok">✓ No sales inside 7 days on the current board.</div>')
    banner = ''.join(banner_bits)

    urgent_rows = ''.join(
        f'<tr><td class="d">{_days(r)}d</td>'
        f'<td><b>{_esc(_owner(r))}</b><div class="sub">{_esc(_addr_short(r))}</div></td>'
        f'<td>{_esc(_sale(r) or "—")}</td>'
        f'<td>{_esc(_tier(r))}</td>'
        f'<td class="mono">{_esc(_case(r))}</td>'
        f'<td>{_esc(_plaintiff(r)[:26])}</td>'
        f'<td class="ck">{"📞" if _has_phone(r) else ""}{"✉" if _has_email(r) else ""}'
        f'{"" if _has_phone(r) or _has_email(r) else "🚫"}</td>'
        f'<td contenteditable="true" data-ph="—"></td></tr>'
        for r in urgent
    ) or '<tr><td colspan="8" class="empty">Nothing inside 7 days. Focus the meeting on ACTIVE + EARLY.</td></tr>'

    port_rows = ''.join(
        f'<tr><td><b>{_esc(p["owner"])}</b><div class="sub">{_esc(p["email"])}</div></td>'
        f'<td class="ck">{p["count"]} properties</td>'
        f'<td>{_esc(", ".join(_addr_short(r) for r in p["leads"][:3]))}'
        f'{" + " + str(len(p["leads"]) - 3) + " more" if len(p["leads"]) > 3 else ""}</td>'
        f'<td contenteditable="true" data-ph="Who is calling?"></td></tr>'
        for p in portfolios
    ) or '<tr><td colspan="4" class="empty">No portfolio owners on the current board.</td></tr>'

    replies_rows = ''.join(
        f'<tr><td>{_esc(e.get("when") or e.get("date") or "?")}</td>'
        f'<td>{_esc(e.get("who") or e.get("from") or e.get("owner") or "?")}</td>'
        f'<td>{_esc((e.get("preview") or e.get("body") or e.get("subject") or "")[:120])}</td>'
        f'<td contenteditable="true" data-ph="Who is following up?"></td></tr>'
        for e in new_replies[:10]
    ) or '<tr><td colspan="4" class="empty">No new replies since yesterday. If you sent yesterday, check your Gmail Sent folder — the worker composes, YOU click Send.</td></tr>'

    focus_body = _esc(focus_override) if focus_override else theme_hint

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MSG Standup — {day_dt.strftime("%A, %b %d %Y")}</title>
<style>
@page {{ size: Letter; margin: 0.45in; }}
*      {{ box-sizing: border-box; }}
html,body {{ margin:0; padding:0; background:#f4f6fa; color:#0e1b33;
            font: 13.5px/1.5 -apple-system,'Segoe UI',Roboto,Arial,sans-serif; }}
.sheet {{ max-width: 7.5in; margin: 18px auto; background:#fff; padding: 30px 36px 42px;
         box-shadow: 0 3px 18px rgba(11,23,48,.10); border-radius: 8px; }}
@media print {{
  html,body {{ background:#fff; }}
  .sheet {{ max-width: none; margin: 0; padding: 0; box-shadow: none; border-radius: 0; }}
  .noprint {{ display: none !important; }}
  table {{ break-inside: avoid; }}
}}

.head {{ display:flex; justify-content:space-between; align-items:flex-end;
        border-bottom: 2px solid #0B1730; padding-bottom: 10px; margin-bottom: 14px; }}
.head .brand {{ font: 900 20px/1.05 Georgia,serif; color:#0B1730; }}
.head .brand small {{ display:block; font: 700 9.5px/1.4 -apple-system,'Segoe UI',Arial,sans-serif;
                     letter-spacing: 3px; color:#5b6472; text-transform: uppercase; margin-top: 4px; }}
.head .meta   {{ text-align:right; font-size: 13px; color:#0e1b33; line-height: 1.55; }}

h2 {{ font: 800 11px/1 -apple-system,'Segoe UI',Arial,sans-serif; letter-spacing:.09em;
     text-transform: uppercase; color:#0B1730; margin: 20px 0 8px; padding-bottom:5px;
     border-bottom: 1px solid #e2e6ee; }}
.sub {{ font-size: 11.5px; color:#6b7280; margin: -3px 0 8px; }}

.warn {{ background:#fff2d7; border:1px solid #eadb9f; color:#7a5a10; padding:8px 12px;
        border-radius:6px; margin: 6px 0; font-size:12.5px; }}
.ok   {{ background:#eaf5ec; border:1px solid #bfe0c8; color:#286c34; padding:8px 12px;
        border-radius:6px; margin: 6px 0; font-size:12.5px; }}

.stats {{ display:flex; gap:10px; flex-wrap:wrap; margin: 6px 0 14px; }}
.stat  {{ background:#fbfaf6; border:1px solid #e2e6ee; border-radius:6px; padding:8px 12px;
         min-width:88px; text-align:center; }}
.stat .n {{ font:800 18px/1 -apple-system,'Segoe UI',Arial,sans-serif; color:#0e1b33; }}
.stat .l {{ font:700 9.5px/1.35 -apple-system,'Segoe UI',Arial,sans-serif; letter-spacing:.08em;
           text-transform:uppercase; color:#8a94a8; margin-top:3px; }}
.stat.urg .n {{ color:#8a1c1c; }}
.stat.warn .n {{ color:#7a5a10; }}
.stat.ok .n {{ color:#286c34; }}

table {{ width:100%; border-collapse: collapse; margin: 6px 0; font-size: 12.5px; }}
table th, table td {{ border: 1px solid #d5dae4; padding: 6px 8px; vertical-align: top;
                    text-align: left; }}
table th {{ background:#0B1730; color:#F4E5A7; font: 700 10px/1 -apple-system,'Segoe UI',Arial,sans-serif;
           letter-spacing: .09em; text-transform: uppercase; }}
table tr:nth-child(even) td {{ background:#fbfaf6; }}
.empty {{ text-align: center; color:#8a94a8; font-style: italic; padding: 10px; }}
.d     {{ text-align:right; width:40px; font-weight:800; color:#8a1c1c; }}
.ck    {{ text-align:center; width:80px; }}
.mono  {{ font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }}

[contenteditable="true"] {{ outline: none; }}
[contenteditable="true"]:focus {{ background:#fffdf0; box-shadow: inset 0 0 0 1px #C6A14B; }}
[contenteditable="true"]:empty::before {{
  content: attr(data-ph); color:#a2a9b6; font-style:italic;
}}

.att {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px 14px; margin: 6px 0; }}
.att .row {{ display:flex; align-items:center; gap: 8px; }}
.att .row input {{ width:15px; height:15px; }}
.att .row .name {{ font-weight:700; color:#0B1730; min-width:78px; }}
.att .row .role {{ color:#6b7280; font-size:12px; }}

.agenda ol {{ margin:4px 0 6px 22px; padding:0; }}
.agenda ol li {{ margin:6px 0; }}

.box {{ border:1px solid #d5dae4; border-radius:6px; padding:10px 12px; margin:6px 0;
       background:#fbfaf6; min-height: 56px; }}

.foot {{ margin-top: 26px; padding-top: 10px; border-top: 1px solid #d5dae4;
        display:flex; justify-content:space-between; font-size: 11.5px; color:#6b7280; }}

.tools {{ max-width: 7.5in; margin: 14px auto 0; text-align: right; }}
.tools button {{ padding: 7px 14px; margin-left: 6px; font: 700 12px Arial,sans-serif;
                background:#0B1730; color:#F4E5A7; border:none; border-radius: 5px; cursor:pointer; }}
</style>
</head>
<body>

<div class="tools noprint">
  <button onclick="window.print()">Print / Save as PDF</button>
</div>

<div class="sheet">

  <div class="head">
    <div class="brand">
      Morning Standup
      <small>Miami Solutions Group &middot; auto-planner</small>
    </div>
    <div class="meta">
      <div><b>{_esc(day_dt.strftime("%A, %B %d, %Y"))}</b></div>
      <div><b>Start:</b> 7:30 AM ET &nbsp;&nbsp;<b>Duration:</b> 30 min</div>
      <div><b>Chair:</b> Alejandro &nbsp;&nbsp;<b>Scribe:</b> rotates</div>
    </div>
  </div>

  {banner}

  <div class="stats">
    <div class="stat urg"><div class="n">{len(urgent)}</div><div class="l">Sale ≤ 7d</div></div>
    <div class="stat"><div class="n">{active_n}</div><div class="l">Sale 8–45d</div></div>
    <div class="stat"><div class="n">{early_n}</div><div class="l">Lis pendens / early</div></div>
    <div class="stat"><div class="n">{len(portfolios)}</div><div class="l">Portfolio owners</div></div>
    <div class="stat"><div class="n">{len(new_replies)}</div><div class="l">Replies since y'day</div></div>
    <div class="stat warn"><div class="n">{blockers}</div><div class="l">No phone or email</div></div>
    <div class="stat"><div class="n">{opt_count}</div><div class="l">Opt-outs on file</div></div>
    <div class="stat"><div class="n">{mail_today}</div><div class="l">Emails sent today</div></div>
  </div>

  <h2>Attendees</h2>
  <div class="att">
    <label class="row"><input type="checkbox" checked><span class="name">Alejandro</span> <span class="role">— DealFlow / strategy</span></label>
    <label class="row"><input type="checkbox"><span class="name">Jose</span> <span class="role">— field / signer / MSG</span></label>
    <label class="row"><input type="checkbox"><span class="name">Carlos</span> <span class="role">— door runs / mail</span></label>
    <label class="row"><input type="checkbox"><span class="name">Guest</span> <span class="role" contenteditable="true" data-ph="— name / role"></span></label>
  </div>

  <h2>Today&rsquo;s focus &mdash; {_esc(day_name)}: {_esc(theme_label)}</h2>
  <div class="sub">{focus_body}</div>
  <div class="box" contenteditable="true" data-ph="Specific items under today's theme — filled in the morning before the call."></div>

  <h2>Sale-in-7 sweep &mdash; every urgent lead, sorted soonest</h2>
  <div class="sub">Contact icons: 📞 phone traced &middot; ✉ email traced &middot; 🚫 no channel — this lead is a skip-trace or door task, not an outreach one.</div>
  <table>
    <thead><tr><th>Days</th><th>Owner / property</th><th>Sale</th><th>Tier</th><th>Case</th><th>Plaintiff</th><th>Reach</th><th>Assigned to</th></tr></thead>
    <tbody>{urgent_rows}</tbody>
  </table>

  <h2>Portfolio owners &mdash; one contact, several properties</h2>
  <div class="sub">These are single humans who owe you a single conversation about a stack of properties. Do NOT hit them twice — one consolidated call or email.</div>
  <table>
    <thead><tr><th>Owner</th><th>Count</th><th>Properties</th><th>Assigned to</th></tr></thead>
    <tbody>{port_rows}</tbody>
  </table>

  <h2>New replies since yesterday</h2>
  <table>
    <thead><tr><th>When</th><th>Who</th><th>Preview</th><th>Assigned to</th></tr></thead>
    <tbody>{replies_rows}</tbody>
  </table>

  <h2>Standing agenda &mdash; every day, in this order</h2>
  <div class="agenda">
    <ol>
      <li><b>Sale-in-7 sweep</b> — the table above. Anyone still un-worked? Blockers?</li>
      <li><b>Yesterday&rsquo;s replies + call-backs</b> — who wrote back, who we owe a call.</li>
      <li><b>Money in motion</b> — open offers, pending closings, cash-for-keys tenants, mail budget burn.</li>
      <li><b>Today&rsquo;s focus</b> ({_esc(theme_label)}) — the one item that only gets attention on {_esc(day_name)}.</li>
      <li><b>Blockers / one ask each</b> — each attendee names the one thing they need from someone else.</li>
    </ol>
  </div>

  <h2>Decisions</h2>
  <div class="box" contenteditable="true"
       data-ph="One line each. What did we decide? Who approved it? A decision without a name attached is a wish."></div>

  <h2>Action items</h2>
  <table>
    <thead><tr><th>What</th><th style="width:100px">Owner</th><th style="width:82px">Due</th><th style="width:84px">Status</th></tr></thead>
    <tbody>
      <tr><td contenteditable="true" data-ph="One clear action, verb first"></td><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true" data-ph="open"></td></tr>
      <tr><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td></tr>
      <tr><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td></tr>
      <tr><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td></tr>
      <tr><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td><td contenteditable="true"></td></tr>
    </tbody>
  </table>

  <h2>Notes / parking lot</h2>
  <div class="box" contenteditable="true"
       data-ph="Anything raised that we chose NOT to work on today. Comes back to Monday's agenda if still open."></div>

  <div class="foot">
    <div>Next meeting: <span contenteditable="true" data-ph="tomorrow, same time"></span></div>
    <div>Chair signed: <span contenteditable="true" data-ph="_____________"></span></div>
  </div>

</div>
</body>
</html>
'''


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--date', help='YYYY-MM-DD (default: today)', default=None)
    ap.add_argument('--focus', default='', help='override the day-theme hint under "Today\'s focus"')
    ap.add_argument('--out', default='', help='output path (default: Desktop/MSG-Meeting-Agendas/YYYY-MM-DD_standup.html)')
    ap.add_argument('--no-open', action='store_true', help='skip auto-opening in the default browser')
    args = ap.parse_args()

    day_dt = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    leads = _load_leads()
    replies = _load_json('replies.json', {})
    optouts = _load_json('optouts.json', {})
    mail_ledger = _load_json('mail_sent.json', [])

    html_out = render(day_dt, leads, replies, optouts, args.focus, mail_ledger)

    out_path = args.out
    if not out_path:
        os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
        out_path = os.path.join(DEFAULT_OUT_DIR, f'{day_dt.isoformat()}_standup.html')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_out)

    print(f'wrote {out_path}  ({os.path.getsize(out_path):,} bytes)')
    print(f'  leads loaded: {len(leads)}')
    print(f'  urgent (sale <= 7d): {sum(1 for r in leads if _days(r) is not None and 0<=_days(r)<=7)}')
    print(f'  portfolio owners: {sum(1 for _ in _portfolios(leads))}')
    print(f'  new replies since yesterday: {len(_new_replies(replies))}')

    if not args.no_open:
        try:
            webbrowser.open('file:///' + out_path.replace('\\', '/'))
        except Exception:
            pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
