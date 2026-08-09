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
def _workable_leads(leads, min_days=13, max_days=45, limit=40):
    """The lane Alejandro actually meets on: sales far enough out that a letter can arrive, a
    call can land, an offer can be negotiated, and a title company can close. Sub-13 days is
    already too close to work realistically -- see _expiring_leads for that separate section."""
    rows = [r for r in leads
            if (_days(r) is not None and min_days <= _days(r) <= max_days)]
    rows.sort(key=lambda r: (_days(r) or 0, {'A': 0, 'B': 1, 'C': 2}.get(_tier(r), 3)))
    return rows[:limit]


def _expiring_leads(leads, max_days=12, limit=20):
    """Sales inside the min_days window. Named honestly: these are past the letter-arrival
    window; the meeting decision is call/door-knock TODAY or write them off. Not the focus."""
    rows = [r for r in leads
            if (_days(r) is not None and 0 <= _days(r) <= max_days)]
    rows.sort(key=lambda r: (_days(r) or 0, {'A': 0, 'B': 1, 'C': 2}.get(_tier(r), 3)))
    return rows[:limit]


def _late_count(leads, gt_days=45):
    return sum(1 for r in leads if (_days(r) is not None and _days(r) > gt_days))


# ---------------------------------------------------------------- Carlos cluster
MD_BBOX = {'la0': 25.13, 'la1': 25.99, 'lo0': -80.88, 'lo1': -80.11}


def _has_coords(r):
    try:
        lat, lng = float(r.get('lat')), float(r.get('lng'))
    except (TypeError, ValueError):
        return False
    return (MD_BBOX['la0'] <= lat <= MD_BBOX['la1']
            and MD_BBOX['lo0'] <= lng <= MD_BBOX['lo1'])


def _addr(r):
    """MD scrape stores 'Address' (capital A); tracker-processed rows use 'addr'. Try both."""
    return str(r.get('addr') or r.get('Address') or '')


def _routable_addr(r):
    """Same rule the tracker uses: address must start with a street number, else the geocode is
    a city centroid and Carlos ends up in downtown Miami instead of on the block."""
    import re
    return bool(re.match(r'^\s*\d+\s+\S', _addr(r)))


def _knock_eligible(r):
    if r.get('saleBkAct') and not r.get('saleLift'):
        return False
    if r.get('sibclaimed'):
        return False
    return True


def _zip_of(r):
    import re
    m = re.search(r'\b(3\d{4})\b', _addr(r))
    return m.group(1) if m else ''


def _carlos_cluster(leads, exclude_zips=('33172',), max_days=45, min_cluster=3, top_n=4):
    """Pick the ZIP (excluding worked-out ones) with the most workable leads clustered together.
    Returns (zip, city_hint, [top leads]) or (None, '', []) if nothing hits the threshold.

    Alejandro's constraint: Carlos already exhausted 33172; give him a FRESH tight cluster where
    3+ properties are within a few minutes of each other so he isn't burning gas between them.

    NOTE ON GEOMETRY: this uses the ZIP code as the cluster proxy, not lat/lng radius. The reason
    is honest -- raw leads_final.json has no coordinates; those are added at build time by
    make_tracker's geocoder. So a coord/bbox filter here would drop 100% of leads (which is what
    the first pass did). ZIP is a good-enough neighborhood; most Miami-Dade ZIPs are 2-4 miles
    across, so 3 leads in one ZIP genuinely are 'a few minutes apart' in Carlos's van."""
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in leads:
        if (r.get('county') or 'MIAMI-DADE') != 'MIAMI-DADE':
            continue
        if not _routable_addr(r):
            continue
        if not _knock_eligible(r):
            continue
        if _is_company(r):
            continue
        d = _days(r)
        if d is None or d < 0 or d > max_days:
            continue
        z = _zip_of(r)
        if not z or z in exclude_zips:
            continue
        buckets[z].append(r)
    # Dedupe by owner: a portfolio owner with 4 properties in one ZIP is ONE door to Carlos
    # (they live at one, not all four), and the Portfolio-owners section already surfaces the
    # multi-property picture. What Carlos needs here is 3+ DIFFERENT doors in the same ZIP.
    def _dedupe_by_owner(rows):
        seen, out = set(), []
        for r in rows:
            key = _owner(r).lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(r)
        return out

    ranked = []
    for z, rs in buckets.items():
        rs = _dedupe_by_owner(rs)
        if len(rs) < min_cluster:
            continue
        avg_days = sum(_days(x) for x in rs) / len(rs)
        rs.sort(key=lambda r: _days(r) or 99999)
        ranked.append((len(rs), avg_days, z, rs))
    if not ranked:
        return None, '', []
    ranked.sort(key=lambda x: (-x[0], x[1]))
    _, _, best_zip, rs = ranked[0]
    return best_zip, '', rs[:top_n]


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


def _reply_date(e):
    """Parse a reply's 'when'. replies.py stores the RFC-2822 header date verbatim
    ("Fri, 7 Aug 2026 18:51:40 -0400") — fromisoformat CANNOT parse that, and the old
    except/continue swallowed the error, so the agenda said "Zero new replies" every
    single morning while real owners waited (the oldest 13 days, with a phone number
    in the reply text). Parse RFC-2822 first, ISO as the fallback, and NEVER silently
    drop an entry that has any date at all — an unparseable date is still a reply."""
    w = str(e.get('when') or e.get('date') or e.get('d') or '')
    if not w.strip():
        return None
    try:
        import email.utils
        d = email.utils.parsedate_to_datetime(w)
        if d:
            return d.date()
    except Exception:
        pass
    try:
        return dt.date.fromisoformat(w[:10])
    except Exception:
        return None


def _new_replies(replies_data, since_days=1):
    """ALL unanswered reply threads, oldest first — not a 1-day window.

    replies.json double-keys every thread ('@address' + one entry per matched case #);
    dedupe by email address so one human is one row. since_days is kept for the
    console summary but the agenda now shows every thread still waiting, because a
    13-day-old reply is MORE urgent than a fresh one, not less."""
    if not replies_data:
        return []
    if isinstance(replies_data, dict):
        entries = list(replies_data.values())
    elif isinstance(replies_data, list):
        entries = replies_data
    else:
        return []
    seen, hits = set(), []
    today = dt.date.today()
    for e in entries:
        if not isinstance(e, dict):
            continue
        addr = str(e.get('email') or '').strip().lower()
        if addr and addr in seen:
            continue
        if addr:
            seen.add(addr)
        d = _reply_date(e)
        e = dict(e)
        e['_age_days'] = (today - d).days if d else None
        hits.append(e)
    hits.sort(key=lambda x: -(x['_age_days'] if x['_age_days'] is not None else 0))
    return hits


# ---------------------------------------------------------------- render
def _esc(s):
    return html.escape(str(s) if s is not None else '')


def render(day_dt, leads, replies, optouts, focus_override, mail_ledger,
           min_days=13, max_days=45,
           alej_call_target=50, alej_email_target=30, carlos_visits_target=3,
           exclude_zips=('33172',),
           meeting_time='7:30 AM ET', meeting_kind='Morning standup'):
    weekday = day_dt.weekday()
    day_name, theme_label, theme_hint = THEMES[weekday]

    workable = _workable_leads(leads, min_days=min_days, max_days=max_days, limit=40)
    expiring = _expiring_leads(leads, max_days=min_days - 1, limit=20)
    workable_n = sum(1 for r in leads
                     if (_days(r) is not None and min_days <= _days(r) <= max_days))
    expiring_n = sum(1 for r in leads
                     if (_days(r) is not None and 0 <= _days(r) <= min_days - 1))
    late_n = _late_count(leads, gt_days=max_days)
    early_n = _early_count(leads)
    cluster_zip, _, cluster_leads = _carlos_cluster(
        leads, exclude_zips=exclude_zips, max_days=max_days)
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
    if not workable:
        banner_bits.append(f'<div class="ok">✓ No sales in the workable window ({min_days}–{max_days}d). '
                           f'Focus on EARLY (lis pendens) instead.</div>')
    banner = ''.join(banner_bits)

    def _row(r):
        return (
            f'<tr><td class="d">{_days(r)}d</td>'
            f'<td><b>{_esc(_owner(r))}</b><div class="sub">{_esc(_addr_short(r))}</div></td>'
            f'<td>{_esc(_sale(r) or "—")}</td>'
            f'<td>{_esc(_tier(r))}</td>'
            f'<td class="mono">{_esc(_case(r))}</td>'
            f'<td>{_esc(_plaintiff(r)[:26])}</td>'
            f'<td class="ck">{"📞" if _has_phone(r) else ""}{"✉" if _has_email(r) else ""}'
            f'{"" if _has_phone(r) or _has_email(r) else "🚫"}</td>'
            f'<td contenteditable="true" data-ph="—"></td></tr>'
        )

    workable_rows = ''.join(_row(r) for r in workable) or (
        f'<tr><td colspan="8" class="empty">No leads in the workable window ({min_days}–{max_days}d). '
        f'Focus on EARLY (lis pendens) for pipeline; ignore the expiring section — too late to work.</td></tr>'
    )

    expiring_rows = ''.join(_row(r) for r in expiring) or (
        f'<tr><td colspan="8" class="empty">Nothing inside {min_days} days.</td></tr>'
    )

    # Carlos cluster rows
    cluster_rows = ''.join(
        f'<tr><td class="d">{_days(r)}d</td>'
        f'<td><b>{_esc(_owner(r))}</b><div class="sub">{_esc(_addr_short(r))}</div></td>'
        f'<td>{_esc(_sale(r) or "—")}</td>'
        f'<td>{_esc(_tier(r))}</td>'
        f'<td class="mono">{_esc(_case(r))}</td></tr>'
        for r in cluster_leads
    )

    # Talking points -- sentences to READ aloud, not just tables to glance at.
    workable_bit = (
        f"We have <b>{workable_n} workable leads</b> in the {min_days}-to-{max_days}-day window "
        f"tonight. That is our real pipeline. Expiring is at {expiring_n} — call today or write off, "
        f"do not let it eat the meeting."
    )
    portfolio_bit = (
        f"<b>{len(portfolios)} portfolio owners</b> on the board — one contact, several properties. "
        f"That is one conversation for a stack of parcels. Do NOT hit them twice."
        if portfolios else ""
    )
    reply_bit = (
        f"<b>{len(new_replies)} owner replies are WAITING</b> — oldest "
        f"{max((e.get('_age_days') or 0) for e in new_replies)}d. Every reply outranks every score on the board. "
        f"These get called back before anything else on this page."
        if new_replies else
        "Zero replies waiting. If we sent emails, the worker composes — you have to click Send in Gmail. Ground truth is the Sent folder, not the Proof Sheet."
    )
    blocker_bit = (
        f"<b>{blockers} leads sit with no traced phone AND no email</b> — that is a skip-trace TODO, "
        f"not an outreach task. If we do not fix that this week the pipeline stops growing."
    )
    cluster_bit = (
        f"Carlos's fresh cluster is <b>ZIP {cluster_zip}</b> — {len(cluster_leads)} properties, "
        f"soonest sale in {_days(cluster_leads[0])}d. Not 33172; the goal is a new tight cluster he "
        f"can hit in one drive."
        if cluster_zip else
        "No fresh cluster (>=3 properties in one ZIP outside 33172) on today's board. Carlos gets a "
        "solo assignment; see the Sale-in-7 / Workable tables for one-off targets."
    )
    focus_body = _esc(focus_override) if focus_override else theme_hint

    port_rows = ''.join(
        f'<tr><td><b>{_esc(p["owner"])}</b><div class="sub">{_esc(p["email"])}</div></td>'
        f'<td class="ck">{p["count"]} properties</td>'
        f'<td>{_esc(", ".join(_addr_short(r) for r in p["leads"][:3]))}'
        f'{" + " + str(len(p["leads"]) - 3) + " more" if len(p["leads"]) > 3 else ""}</td>'
        f'<td contenteditable="true" data-ph="Who is calling?"></td></tr>'
        for p in portfolios
    ) or '<tr><td colspan="4" class="empty">No portfolio owners on the current board.</td></tr>'

    def _reply_age_cell(e):
        a = e.get('_age_days')
        if a is None:
            return '?'
        # 2+ days waiting renders red — a reply is the warmest signal the system produces,
        # and the whole reason this table exists is the 13-day-old one nobody saw.
        return (f'<b style="color:#b3372f">{a}d waiting</b>' if a >= 2
                else ('today' if a == 0 else '1d waiting'))
    replies_rows = ''.join(
        f'<tr><td>{_reply_age_cell(e)}</td>'
        f'<td>{_esc(e.get("email") or e.get("who") or e.get("from") or e.get("owner") or "?")}</td>'
        f'<td>{_esc((e.get("excerpt") or e.get("preview") or e.get("body") or e.get("subject") or "")[:140])}</td>'
        f'<td contenteditable="true" data-ph="Who is calling this back TODAY?"></td></tr>'
        for e in new_replies[:15]
    ) or '<tr><td colspan="4" class="empty">No replies waiting. If you sent yesterday, check your Gmail Sent folder — the worker composes, YOU click Send.</td></tr>'

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
      {_esc(meeting_kind)}
      <small>Miami Solutions Group &middot; auto-planner</small>
    </div>
    <div class="meta">
      <div><b>{_esc(day_dt.strftime("%A, %B %d, %Y"))}</b></div>
      <div><b>Start:</b> {_esc(meeting_time)} &nbsp;&nbsp;<b>Duration:</b> 30 min</div>
      <div><b>Chair:</b> Alejandro &nbsp;&nbsp;<b>Scribe:</b> rotates</div>
    </div>
  </div>

  {banner}

  <div class="stats">
    <div class="stat ok"><div class="n">{workable_n}</div><div class="l">Workable · {min_days}–{max_days}d</div></div>
    <div class="stat warn"><div class="n">{expiring_n}</div><div class="l">Expiring · &lt;{min_days}d</div></div>
    <div class="stat"><div class="n">{early_n}</div><div class="l">Lis pendens / early</div></div>
    <div class="stat"><div class="n">{late_n}</div><div class="l">Sale &gt; {max_days}d out</div></div>
    <div class="stat"><div class="n">{len(portfolios)}</div><div class="l">Portfolio owners</div></div>
    <div class="stat"><div class="n">{len(new_replies)}</div><div class="l">Replies waiting</div></div>
    <div class="stat warn"><div class="n">{blockers}</div><div class="l">No phone or email</div></div>
    <div class="stat"><div class="n">{mail_today}</div><div class="l">Emails sent today</div></div>
  </div>

  <h2>What Alejandro says out loud</h2>
  <div class="sub">Talking points, spelled out as sentences. Read them if you have to. Do not skip &mdash; if you cannot say them, nobody in the room knows the state of the business.</div>
  <div class="box">
    <p>{workable_bit}</p>
    {"<p>" + portfolio_bit + "</p>" if portfolio_bit else ""}
    <p>{reply_bit}</p>
    <p>{blocker_bit}</p>
    <p>{cluster_bit}</p>
  </div>

  <h2>Attendees</h2>
  <div class="att">
    <label class="row"><input type="checkbox" checked><span class="name">Alejandro</span> <span class="role">— DealFlow / strategy</span></label>
    <label class="row"><input type="checkbox"><span class="name">Jose</span> <span class="role">— field / signer / MSG</span></label>
    <label class="row"><input type="checkbox"><span class="name">Carlos</span> <span class="role">— door runs / mail</span></label>
    <label class="row"><input type="checkbox"><span class="name">Guest</span> <span class="role" contenteditable="true" data-ph="— name / role"></span></label>
  </div>

  <h2>Daily targets &mdash; per person</h2>
  <div class="sub">Non-negotiable. If we do not hit these numbers, nothing else in the meeting matters.</div>
  <table>
    <thead><tr><th>Who</th><th>Target</th><th>Done today</th><th>Notes</th></tr></thead>
    <tbody>
      <tr><td><b>Alejandro</b></td>
          <td><b>{alej_call_target}</b> calls &middot; <b>{alej_email_target}</b> emails</td>
          <td contenteditable="true" data-ph="calls / emails so far"></td>
          <td contenteditable="true" data-ph="blockers?"></td></tr>
      <tr><td><b>Carlos</b></td>
          <td><b>{carlos_visits_target}</b> property visits (cluster below)</td>
          <td contenteditable="true" data-ph="visited so far"></td>
          <td contenteditable="true" data-ph="starts afternoon; packet ready"></td></tr>
      <tr><td><b>Jose</b></td>
          <td contenteditable="true" data-ph="e.g. sign 2 offers, review 3 title chains"></td>
          <td contenteditable="true" data-ph=""></td>
          <td contenteditable="true" data-ph=""></td></tr>
    </tbody>
  </table>

  <h2>Carlos&rsquo;s cluster of the day{" &mdash; ZIP " + _esc(cluster_zip) if cluster_zip else ""}</h2>
  <div class="sub">Fresh territory (not 33172, that's already worked out). Pick the packet from this cluster so his stops are all within a few minutes of each other and he burns less gas between doors. Alejandro to hand him this list before he starts driving in the afternoon.</div>
  {"<table><thead><tr><th>Days</th><th>Owner / property</th><th>Sale</th><th>Tier</th><th>Case</th></tr></thead><tbody>" + cluster_rows + "</tbody></table>" if cluster_zip else '<div class="box"><i>No fresh cluster with 3+ properties in a single ZIP outside 33172 tonight. Carlos gets solo assignments &mdash; pull them from the Sale-in-7 / Workable tables above.</i></div>'}

  <h2>Today&rsquo;s focus &mdash; {_esc(day_name)}: {_esc(theme_label)}</h2>
  <div class="sub">{focus_body}</div>
  <div class="box" contenteditable="true" data-ph="Specific items under today's theme — filled in the morning before the call."></div>

  <h2>Workable sweep &mdash; sales {min_days}–{max_days} days out</h2>
  <div class="sub">Primary focus. Far enough that a letter can arrive, a call can land, an offer can be negotiated, a title company can close.
  Contact icons: 📞 phone traced &middot; ✉ email traced &middot; 🚫 no channel — this lead is a skip-trace or door task, not an outreach one.</div>
  <table>
    <thead><tr><th>Days</th><th>Owner / property</th><th>Sale</th><th>Tier</th><th>Case</th><th>Plaintiff</th><th>Reach</th><th>Assigned to</th></tr></thead>
    <tbody>{workable_rows}</tbody>
  </table>

  <h2>Expiring &mdash; sales inside {min_days} days</h2>
  <div class="sub">Secondary. Past the letter-arrival window; decision is <b>call/door today</b> or <b>write them off</b>. Not the meeting focus — do NOT let the panic here drown out the workable pipeline above.</div>
  <table>
    <thead><tr><th>Days</th><th>Owner / property</th><th>Sale</th><th>Tier</th><th>Case</th><th>Plaintiff</th><th>Reach</th><th>Assigned to</th></tr></thead>
    <tbody>{expiring_rows}</tbody>
  </table>

  <h2>Portfolio owners &mdash; one contact, several properties</h2>
  <div class="sub">These are single humans who owe you a single conversation about a stack of properties. Do NOT hit them twice — one consolidated call or email.</div>
  <table>
    <thead><tr><th>Owner</th><th>Count</th><th>Properties</th><th>Assigned to</th></tr></thead>
    <tbody>{port_rows}</tbody>
  </table>

  <h2>Owner replies waiting for a call back &mdash; oldest first, these ARE the day</h2>
  <table>
    <thead><tr><th>Waiting</th><th>Who</th><th>They said</th><th>Assigned to</th></tr></thead>
    <tbody>{replies_rows}</tbody>
  </table>

  <h2>Standing agenda &mdash; every day, in this order</h2>
  <div class="agenda">
    <ol>
      <li><b>Workable sweep ({min_days}–{max_days}d)</b> — the primary table above. Anyone un-worked? Who owns each?</li>
      <li><b>Expiring (&lt;{min_days}d)</b> — call/door TODAY or write off. Do NOT overrun the meeting on these.</li>
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
    ap.add_argument('--min-days', type=int, default=13,
                    help='minimum days-to-auction for the "workable" section (default: 13). '
                         'Sub-13d sales bucket into "Expiring" and get de-emphasised.')
    ap.add_argument('--max-days', type=int, default=45,
                    help='maximum days-to-auction for the workable section (default: 45)')
    ap.add_argument('--meeting-time', default='7:30 AM ET',
                    help='what the header shows for start time (e.g. "10:30 PM ET" for a night sync)')
    ap.add_argument('--meeting-kind', default='Morning Standup',
                    help='header title (e.g. "Night sync", "Weekly review")')
    ap.add_argument('--calls',  type=int, default=50,
                    help="Alejandro's daily call target (default 50)")
    ap.add_argument('--emails', type=int, default=30,
                    help="Alejandro's daily email target (default 30)")
    ap.add_argument('--visits', type=int, default=3,
                    help="Carlos's daily property-visit target (default 3)")
    ap.add_argument('--exclude-zips', default='33172',
                    help='comma-separated ZIPs Carlos has already worked out (default 33172)')
    ap.add_argument('--out', default='', help='output path (default: Desktop/MSG-Meeting-Agendas/YYYY-MM-DD_standup.html)')
    ap.add_argument('--no-open', action='store_true', help='skip auto-opening in the default browser')
    args = ap.parse_args()

    day_dt = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    leads = _load_leads()
    replies = _load_json('replies.json', {})
    optouts = _load_json('optouts.json', {})
    mail_ledger = _load_json('mail_sent.json', [])

    excl = tuple(z.strip() for z in args.exclude_zips.split(',') if z.strip())
    html_out = render(day_dt, leads, replies, optouts, args.focus, mail_ledger,
                      min_days=args.min_days, max_days=args.max_days,
                      alej_call_target=args.calls, alej_email_target=args.emails,
                      carlos_visits_target=args.visits, exclude_zips=excl,
                      meeting_time=args.meeting_time, meeting_kind=args.meeting_kind)

    out_path = args.out
    if not out_path:
        os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
        out_path = os.path.join(DEFAULT_OUT_DIR, f'{day_dt.isoformat()}_standup.html')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html_out)

    print(f'wrote {out_path}  ({os.path.getsize(out_path):,} bytes)')
    print(f'  leads loaded: {len(leads)}')
    print(f'  workable ({args.min_days}-{args.max_days}d): '
          f'{sum(1 for r in leads if _days(r) is not None and args.min_days<=_days(r)<=args.max_days)}')
    print(f'  expiring (<{args.min_days}d): '
          f'{sum(1 for r in leads if _days(r) is not None and 0<=_days(r)<args.min_days)}')
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
