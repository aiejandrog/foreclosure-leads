#!/usr/bin/env python
"""call_list — the dial-ready sheet. Safe numbers only, best number first, in the order to call them.

WHY THIS EXISTS
The instinct was to buy an autodialer. The actual bottleneck is not dialing SPEED, it is list QUALITY:
31% of the phones on file carry a DNC flag, 67 leads have nothing BUT flagged numbers, and some leads
belong to people who no longer own the property. An autodialer would just make it faster to call
numbers that should never be called — in a state where that is $500-$1,500 per call.

So this does the choosing beforehand. Every lead that reaches the page has:
  * at least one phone with NO DNC flag on file          (flagged numbers never print, at all)
  * a live auction clock, soonest first                  (urgency decides the order, not a hunch)
  * survived the ownership gate                          (title_status != 'transferred' -> the person
                                                          still owns the house; see ownership_gate.py)
  * a BEST number chosen by phone_rank                   (mobile on a real carrier beats a dead landline)

The operator never picks a number. That is the point: the decision that creates legal exposure is made
once, here, against the data — not forty times a day by a tired thumb at 7pm.

🔴 HONEST LIMIT ON THE WORD "SCRUBBED". The DNC flag here comes from the skip-trace provider, not from
the federal registry. It is a real signal and it is what we have, but it is NOT an official FTC registry
scrub — that needs the entity + EIN, and it is a separate task. So this sheet says "no DNC flag on file",
never "scrubbed". Do not upgrade that wording until the registry pull actually exists.

Run:  python call_list.py                 # today's sheet
      python call_list.py --days 30 --max 40
"""
import argparse
import datetime
import html as H
import json
import os
import re
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(fn, d):
    try:
        x = json.load(open(os.path.join(HERE, fn), encoding='utf-8'))
        return x if x else d
    except Exception:
        return d


def _fmt(n):
    d = re.sub(r'\D', '', str(n or ''))
    return '(%s) %s-%s' % (d[:3], d[3:6], d[6:]) if len(d) == 10 else str(n or '')


def _money(v):
    try:
        return '$%s' % format(int(float(v)), ',d')
    except Exception:
        return ''


def collect(days_window, cap, lp=False):
    import phone_rank as PR
    import diligence_gate as DG
    _dg = DG.Tally()
    # skiptrace_results.json is a DICT KEYED BY CASE NUMBER, and the records inside carry no 'case'
    # field of their own ({name, address, phones, emails, traced, source}). Reading .values() and
    # looking for r['case'] silently produces an empty map — every lead then looks like it has no
    # phone, and the sheet comes out blank rather than erroring.
    st = _load('skiptrace_results.json', {})
    phones_by_case = {}
    if isinstance(st, dict):
        for k, v in st.items():
            if isinstance(v, dict):
                phones_by_case[str(k)] = v.get('phones') or []
    else:
        for r in st:
            if isinstance(r, dict) and (r.get('case') or r.get('c')):
                phones_by_case[str(r.get('case') or r.get('c'))] = r.get('phones') or []

    own = _load('ownership.json', {})
    notes = (_load('worker_notes.json', {}) or {}).get('notes') or {}
    optouts = _load('optouts.json', {})
    opt = set()
    if isinstance(optouts, dict):
        opt = {str(k).upper() for k in optouts}
    elif isinstance(optouts, list):
        opt = {str(k).upper() for k in optouts}

    rows = []
    if lp:
        # FRESH-FILING MODE. A lis-pendens lead has no auction date at all — lp_leads.py stamps
        # days=9999 — so the auction-window filter below would drop every one of the 758 of them.
        # That is why the freshest pool in the business was invisible to this sheet. Here the sort
        # key is FILING RECENCY instead: being first to a case filed this morning is the entire
        # edge, and it decays fast once every other investor's list catches up.
        for r in _load('lp_leads.json', []):
            if not isinstance(r, dict):
                continue
            case = str(r.get('case') or '')
            if not case or r.get('lpDismissed') or r.get('lpClosed'):
                continue
            if (own.get(case) or {}).get('title_status') == 'transferred':
                continue
            # DILIGENCE GATE — on the SOURCE row `r`, never on the display dict built below, which
            # drops every field the rules read (no defendants, no sale year, no judgment).
            if _dg.check(r)['hold']:
                continue
            owner = str(r.get('oname') or r.get('owners') or '').strip()
            if owner.upper() in opt:
                continue
            n = notes.get(case) or {}
            if n.get('dead') or n.get('optout'):
                continue
            ok_ph, blocked = PR.rank(phones_by_case.get(case) or [])
            if not ok_ph:
                continue
            best = ok_ph[0]
            filed = str(r.get('filedDate') or r.get('filed') or '')
            try:
                _v = float(str(r.get('value') or 0).replace('$', '').replace(',', ''))
            except Exception:
                _v = 0.0
            rows.append({
                'case': case, 'owner': owner[:30], 'addr': str(r.get('addr') or '')[:40],
                'auction': filed, 'days': 0, 'filed': filed,
                'phone': _fmt(best.get('number')),
                'ptype': 'mobile' if str(best.get('type') or '').lower().startswith('mob') else 'landline',
                'alt': _fmt(ok_ph[1].get('number')) if len(ok_ph) > 1 else '',
                'blocked': len(blocked),
                # A lis pendens is the START of a case — no judgment has been entered yet, so judg is
                # 0 and there is no equity percentage to compute. lp_leads' 'eq' is NOT a percent on
                # this path (it renders as e.g. "558805%"), so it is deliberately dropped. Printing
                # "$0 judgment / 0% equity" would state as fact something simply not decided yet.
                'judg': '', 'val': _money(r.get('value')) if _v > 0 else '',
                'eq': None,
                'title': (own.get(case) or {}).get('title_status', ''),
            })

        def _fkey(x):
            for f in ('%m/%d/%Y', '%Y-%m-%d'):
                try:
                    return datetime.datetime.strptime(x['filed'], f).date()
                except Exception:
                    pass
            return datetime.date(1900, 1, 1)
        rows.sort(key=_fkey, reverse=True)      # newest filing first — freshness IS the edge
        _dg.report('call list (LP)', indent='')
        return rows[:cap]

    srcs = [('leads_final.json', 'Case #', 'owners', 'Address', 'AuctionDate', 'days_to_auction',
             'judgment', 'market_value', 'equity_pct'),
            ('broward_leads.json', 'case', 'oname', 'addr', 'auction', 'days', 'judg', 'value', 'eq'),
            ('palmbeach_leads.json', 'case', 'oname', 'addr', 'auction', 'days', 'judg', 'value', 'eq')]
    for fn, ck, ok, ak, auk, dk, jk, vk, ek in srcs:
        data = _load(fn, [])
        data = data if isinstance(data, list) else list(data.values())
        for r in data:
            if not isinstance(r, dict):
                continue
            case = str(r.get(ck) or '')
            if not case:
                continue
            d = r.get(dk)
            if not isinstance(d, (int, float)) or d < 0 or d > days_window:
                continue
            # OWNERSHIP GATE — never dial someone who no longer owns the property.
            if (own.get(case) or {}).get('title_status') == 'transferred':
                continue
            # DILIGENCE GATE — same placement rule as the LP branch: the SOURCE row, before the
            # display dict. These are RAW county rows with no title_status of their own; the gate
            # merges ownership.json itself so an already-cleared lead is not held twice.
            if _dg.check(r)['hold']:
                continue
            owner = str(r.get(ok) or '').strip()
            if owner.upper() in opt:
                continue
            # already worked / retired on this device's ledger
            n = notes.get(case) or {}
            if n.get('dead') or n.get('optout'):
                continue
            ok_ph, blocked = PR.rank(phones_by_case.get(case) or [])
            if not ok_ph:
                continue                    # no callable number -> not a call, it's a skip-trace task
            best = ok_ph[0]
            # EQUITY MUST NOT LIE. When the property has no value on file, equity_pct computes to 0 —
            # and "0%" on a call sheet reads as "verified: no equity" when the truth is "we don't know".
            # Those are opposite instructions to the person dialling. Unknown prints as "?" and sorts
            # after the knowns inside the same day bucket, so a real number always outranks a guess.
            try:
                _v = float(str(r.get(vk) or 0).replace('$', '').replace(',', ''))
            except Exception:
                _v = 0.0
            eq_known = _v > 0
            rows.append({
                'case': case, 'owner': owner[:30],
                'addr': str(r.get(ak) or '')[:40],
                'auction': str(r.get(auk) or ''), 'days': int(d),
                'phone': _fmt(best.get('number')),
                'ptype': 'mobile' if str(best.get('type') or '').lower().startswith('mob') else 'landline',
                'alt': _fmt(ok_ph[1].get('number')) if len(ok_ph) > 1 else '',
                'blocked': len(blocked),
                'judg': _money(r.get(jk)), 'val': _money(r.get(vk)) if eq_known else '',
                'eq': (r.get(ek) or 0) if eq_known else None,
                'title': (own.get(case) or {}).get('title_status', ''),
            })
    # sort: soonest sale, then known equity high-to-low, then the unknowns
    rows.sort(key=lambda x: (x['days'], 0 if x['eq'] is not None else 1, -(x['eq'] or 0)))
    _dg.report('call list', indent='')
    return rows[:cap]


CSS = """
@page{size:letter landscape;margin:9mm 8mm}
*{box-sizing:border-box;margin:0;padding:0}
body{font:10.5px/1.3 "Segoe UI",Arial,sans-serif;color:#111827}
.hd{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid #0B1730;padding-bottom:5px;margin-bottom:6px}
h1{font-size:17px;color:#0B1730}
h1 span{font-weight:400;font-size:11px;color:#6b7280}
.who{font:600 10px Arial;color:#374151;text-align:right}
.who .bl{display:inline-block;border-bottom:1px solid #6b7280;min-width:105px;height:12px;margin-left:4px}
.rule{background:#0B1730;color:#fff;border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:10.5px}
.rule b{color:#F4E5A7}
table{width:100%;border-collapse:collapse}
th{background:#0B1730;color:#fff;font:700 8.5px Arial;text-transform:uppercase;letter-spacing:.03em;
   padding:5px 4px;border:1px solid #0B1730;text-align:left;vertical-align:bottom}
th small{display:block;font-weight:400;color:#c7cfdb;letter-spacing:0;text-transform:none;font-size:7.5px}
td{border:1px solid #cbd5e1;height:30px;padding:3px 5px;vertical-align:top;font-size:9.5px}
tr:nth-child(even) td{background:#f8fafc}
.n{color:#b58a1f;font-weight:800;width:20px;text-align:center}
.ph{font:700 12px monospace;color:#065f46;white-space:nowrap}
.alt{font:10px monospace;color:#6b7280;white-space:nowrap}
.mob{background:#dcfce7;color:#065f46;font:700 7.5px Arial;padding:1px 4px;border-radius:6px}
.land{background:#e5e7eb;color:#374151;font:700 7.5px Arial;padding:1px 4px;border-radius:6px}
.urg{background:#fee2e2;color:#991b1b;font:700 9px Arial;padding:1px 5px;border-radius:6px}
.meta{font-size:8.5px;color:#6b7280}
.foot{margin-top:7px;font-size:8.5px;color:#4b5563;line-height:1.4}
.foot b{color:#7f1d1d}
"""


def build(rows, days_window, lp=False):
    today = datetime.date.today()
    head = ('<tr><th style="width:3%">#</th>'
            '<th style="width:15%">Owner</th>'
            '<th style="width:22%">Property</th>'
            + ('<th style="width:8%">Filed<small>date recorded</small></th>' if lp
               else '<th style="width:7%">Sale<small>days out</small></th>')
            + '<th style="width:13%">CALL THIS NUMBER<small>best of the ones on file</small></th>'
              '<th style="width:10%">Backup</th>'
            + ('<th style="width:10%">Stage<small>no judgment yet</small></th>' if lp
               else '<th style="width:10%">Equity<small>after interest</small></th>')
            + '<th style="width:20%">Outcome + best time to call back</th></tr>')
    tr = []
    for i, r in enumerate(rows, 1):
        if lp:
            urg = '<span class="urg">JUST FILED</span>'
        else:
            urg = '<span class="urg">%dd</span>' % r['days'] if r['days'] <= 7 else '%dd' % r['days']
        tag = '<span class="mob">MOBILE</span>' if r['ptype'] == 'mobile' else '<span class="land">LANDLINE</span>'
        tr.append(
            '<tr><td class="n">%d</td>'
            '<td><b>%s</b><div class="meta">%s</div></td>'
            '<td>%s<div class="meta">%s</div></td>'
            '<td>%s<div class="meta">%s</div></td>'
            '<td><span class="ph">%s</span><div>%s</div></td>'
            '<td class="alt">%s</td>'
            '<td>%s</td>'
            '<td></td></tr>'
            % (i, H.escape(r['owner']), H.escape(r['case']), H.escape(r['addr']),
               (('value %s' % r['val']) if r['val'] else 'value not on file') if lp
               else ('judgment %s &middot; value %s' % (r['judg'], r['val'] or 'unknown')),
               urg, H.escape(r['auction']),
               H.escape(r['phone']), tag, H.escape(r['alt']),
               ('<span class="meta">case just filed</span>' if lp
                else (('%d%%' % int(r['eq'])) if r['eq'] is not None
                      else '<span class="meta">not known</span>'))))
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Call List</title>'
            '<style>%s</style></head><body>'
            '<div class="hd"><h1>Call List <span>&mdash; dial straight down the page, top to bottom</span></h1>'
            '<div class="who">DATE <span class="bl"></span> &nbsp; NAME <span class="bl"></span></div></div>'
            '<div class="rule"><b>Every number on this page has NO do-not-call flag on file and the owner '
            'still owns the property.</b> Numbers that were flagged are not printed here at all &mdash; '
            'if it is on this page, you may dial it. <b>8AM&ndash;8PM their time. Stop means stop, on every '
            'channel, forever.</b></div>'
            '<table>%s%s</table>'
            '<div class="foot"><b>You are selling ONE thing:</b> the free five-minute call with the senior '
            'advisor. Not the deal, not the solution. Ask <i>&ldquo;what happened with the house?&rdquo;</i> '
            'then stop talking &mdash; their answer is the whole call. If they push for detail: '
            '<i>&ldquo;I don&rsquo;t want to guess and give you wrong information.&rdquo;</i><br>'
            '<b>Never</b> say you can stop the foreclosure or save the house. <b>Never</b> discuss a loan '
            'modification, short sale or bankruptcy &mdash; that is their attorney or a HUD counselor. '
            '<b>Never</b> ask for money or a signature.<br>'
            'Do-not-call status here comes from our skip-trace data, not the federal registry &mdash; the '
            'registry pull needs the entity and EIN. Write the outcome as you go: a log written tonight '
            'from memory is not a record. Sale dates move; %d-day window as of %s.</div>'
            '</body></html>'
            % (CSS, head, ''.join(tr), days_window, today.strftime('%b %d, %Y')))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=45)
    ap.add_argument('--max', type=int, default=30)
    ap.add_argument('--lp', action='store_true',
                    help='FRESH FILINGS instead of auction-soon: lis pendens, newest filing first')
    a = ap.parse_args()
    # Collect UNCAPPED first so the sheet can say how many it is hiding. `collect` sorts before it
    # slices, so taking [:max] here is identical to passing the cap down — but it keeps the true
    # total, and the true total is the thing the operator was missing.
    rows_all = collect(a.days, 10 ** 9, lp=a.lp)
    total = len(rows_all)
    rows = rows_all[:a.max]
    if not rows:
        print('no callable leads in that window.')
        return 1
    doc = build(rows, a.days, lp=a.lp)
    today = datetime.date.today().isoformat()
    hp = os.path.join(HERE, 'BSG_Call_List_%s%s.html' % ('LP_' if a.lp else '', today))
    open(hp, 'w', encoding='utf-8').write(doc)
    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'BSG_Call_List_%s%s.pdf' % ('LP_' if a.lp else '', today))]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('BSG_Call_List_%s%s.pdf' % ('LP_' if a.lp else '', today)))
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/')); pg.wait_for_timeout(350)
        pdf = pg.pdf(width='11in', height='8.5in', print_background=True, landscape=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    mob = sum(1 for r in rows if r['ptype'] == 'mobile')
    blk = sum(r['blocked'] for r in rows)
    # NEVER print the capped count alone. It reads as "this is everything callable" — and it was
    # read that way: the sheet said "30 callable leads" for weeks while 466 actually qualified,
    # because --max defaults to 30. A day's sheet SHOULD be bounded; it just has to say so, or the
    # cap silently becomes the operator's picture of the whole pipeline.
    print('\n%d callable leads · %d mobile · soonest sale %dd out' % (len(rows), mob, rows[0]['days']))
    if total > len(rows):
        print('SHOWING %d of %d that qualify — capped by --max %d (soonest sale first). '
              'Raise it with --max %d to see them all.' % (len(rows), total, a.max, total))
    print('%d DNC-flagged number(s) were withheld from this sheet.' % blk)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
