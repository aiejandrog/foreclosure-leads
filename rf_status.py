#!/usr/bin/env python3
"""rf_status.py — what RealForeclose says happened to a sale we still list as ahead.

THE GAP (2026-10-16 probe, Alejandro's laptop). The nightly scrape reads only #Area_W (Waiting).
A sale that is cancelled, redeemed, dismissed or sent to bankruptcy moves to #Area_C (Closed /
Canceled) and simply stops being scraped, so the archive kept it as a sale "still ahead" and Call
Mode could keep dialing an owner whose case was already resolved. Of 22 archive cases with a future
sale date that had dropped off the Waiting list, all 22 were in Closed/Canceled with a status line
("Canceled per County", "Canceled per Order", "Canceled per Bankruptcy", "Redeemed", "Judgment
Vacated/Dismissed", "Rescheduled", ...). Same platform for Miami-Dade, Broward and Palm Beach, and
free: it is the public auction page for a date.

WHAT THIS DOES
  Reads #Area_C (and the case numbers in #Area_W) for every upcoming sale date we hold a case on,
  writes rf_status.json (gitignored): {case: {st, sale, county, ts}}. st is one of
    bk redeemed vacated cancelled rescheduled sold other
  make_tracker stamps `rf` on the matching row (same case AND same sale date) and `rfHold` for
  bk / redeemed / vacated. Call Mode drops rfHold rows.

WHAT THIS DOES NOT DO
  It only ever ADDS a hold. Nothing here releases a stay, a bankruptcy hold or an opt-out, and it
  touches none of the suppression surface in CLAUDE.md. A case that comes back in the Waiting list
  for the same date (rescheduled / reinstated) loses its entry on the next read, which is the only
  way a status goes away. The status text is reduced to a fixed label; no party name or bid amount
  is stored. Never fatal: a failed read costs the stamps, never the build.

Run:  python rf_status.py              # read the dates we hold, write rf_status.json
      python rf_status.py --report     # print counts from the current file, read nothing
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'rf_status.json')
ARCHIVE = os.path.join(HERE, 'auction_archive.json')

HOLD_ST = ('bk', 'redeemed', 'vacated')
KEEP_PAST_DAYS = 7
MAX_DATES = int(os.environ.get('DEALFLOW_RF_MAX_DATES', '90'))
HORIZON_DAYS = int(os.environ.get('DEALFLOW_AUCTION_HORIZON_DAYS', '120'))
SUBS = {'MIAMI-DADE': 'miamidade', 'BROWARD': 'broward', 'PALM BEACH': 'palmbeach'}

READ_JS = """
() => {
  const grab = (area) => {
    const out = [];
    document.querySelectorAll('#Area_' + area + ' .AUCTION_ITEM').forEach(item => {
      let c = '';
      item.querySelectorAll('.AUCTION_DETAILS tr').forEach(tr => {
        const t = tr.querySelectorAll('td,th');
        if (t.length > 1 && /^case/i.test((t[0].innerText || '').trim())) c = (t[1].innerText || '').trim();
      });
      const clone = item.cloneNode(true);
      clone.querySelectorAll('.AUCTION_DETAILS').forEach(n => n.remove());
      out.push({c: c, t: (clone.innerText || '').replace(/\\s+/g, ' ').trim()});
    });
    return out;
  };
  return JSON.stringify({closed: grab('C'), waiting: grab('W')});
}
"""


def classify(text):
    """Item text outside the details table -> st, or None when it names no status at all."""
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    m = re.search(r'auction\s*status\s*[:\-]?\s*(.+)', t, re.I)
    s = (m.group(1) if m else t).lower()
    if not s:
        return None
    if 'bankrupt' in s:
        return 'bk'                       # "Canceled per Bankruptcy": must be checked BEFORE 'cancel'
    if 'redeem' in s:
        return 'redeemed'
    if 'vacat' in s or 'dismiss' in s:
        return 'vacated'
    if 'resched' in s:
        return 'rescheduled'
    if 'cancel' in s:
        return 'cancelled'
    if 'sold' in s:
        return 'sold'
    return 'other'


def parse_page(raw, sale, county, ts):
    """One date's read -> ({case: entry}, {waiting cases}). Pure, so a fixture can test it."""
    got, waiting = {}, set()
    for it in raw.get('waiting') or []:
        c = str(it.get('c') or '').strip().upper()
        if c:
            waiting.add(c)
    for it in raw.get('closed') or []:
        c = str(it.get('c') or '').strip().upper()
        st = classify(it.get('t'))
        if c and st:
            got[c] = {'st': st, 'sale': sale, 'county': county, 'ts': ts}
    return got, waiting


def merge(old, got, waiting, sale_dates_read, today):
    """New file contents. A case in Waiting for the date we read it on loses its entry; entries for
    dates we did not read this run are kept; entries whose sale passed more than a week ago go."""
    out = {}
    for c, e in (old or {}).items():
        if not isinstance(e, dict) or not e.get('sale'):
            continue
        try:
            sd = datetime.date.fromisoformat(e['sale'])
        except ValueError:
            continue
        if (today - sd).days > KEEP_PAST_DAYS:
            continue
        if e['sale'] in sale_dates_read and c in waiting.get(e['sale'], ()):
            continue
        out[c] = e
    for c, e in got.items():
        if c in waiting.get(e['sale'], ()):
            continue
        prev = out.get(c)
        if (prev and prev.get('st') in HOLD_ST and prev.get('sale') == e['sale']
                and e['st'] not in HOLD_ST):
            continue                      # an unrecognised new status never lifts a hold
        out[c] = e
    return out


def dates_to_read(arc, today):
    """{(county, iso date)} for every archived case whose sale is still ahead, inside the horizon."""
    last = today + datetime.timedelta(days=HORIZON_DAYS)
    out = set()
    for r in (arc or {}).values():
        if not isinstance(r, dict):
            continue
        co = str(r.get('county') or '').upper()
        try:
            d = datetime.date.fromisoformat(str(r.get('auction') or ''))
        except ValueError:
            continue
        if co in SUBS and today <= d <= last:
            out.add((co, d.isoformat()))
    return sorted(out, key=lambda x: (x[1], x[0]))


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_for_board(rows, path=OUT, today=None):
    """Stamp `rf` (and `rfHold` for bk / redeemed / vacated) on rows whose case AND sale date match.
    Returns the number stamped. No age limit: a hold ends only when the date's Waiting list shows the
    case again, never because the read got old."""
    res = _load(path, {})
    if not isinstance(res, dict) or not res:
        return 0
    n = 0
    for r in rows:
        case = str(r.get('case') or '').strip().upper()
        e = res.get(case)
        if not isinstance(e, dict) or e.get('st') in (None, 'sold', 'other', 'rescheduled'):
            continue
        a = str(r.get('auction') or '').strip()
        iso = None
        for f in ('%m/%d/%Y', '%Y-%m-%d'):
            try:
                iso = datetime.datetime.strptime(a, f).date().isoformat()
                break
            except ValueError:
                pass
        if iso != e.get('sale'):
            continue
        r['rf'] = {'st': e['st'], 'sale': e['sale']}
        if e['st'] in HOLD_ST:
            r['rfHold'] = True
        n += 1
    return n


def read_all(dates, today):
    from playwright.sync_api import sync_playwright
    import foreclosure_leads as F
    got, waiting, read = {}, {}, set()
    ts = today.isoformat()
    with sync_playwright() as p:
        if os.path.isdir(F.PROFILE_DIR):
            ctx = p.chromium.launch_persistent_context(F.PROFILE_DIR, headless=True, user_agent=F.UA,
                                                       viewport={'width': 1400, 'height': 1000})
        else:
            ctx = p.chromium.launch(headless=True).new_context(user_agent=F.UA)
        page = ctx.new_page()
        for co, iso in dates[:MAX_DATES]:
            d = datetime.date.fromisoformat(iso)
            url = 'https://%s.realforeclose.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=%s' % (
                SUBS[co], d.strftime('%m/%d/%Y'))
            try:
                page.goto(url, timeout=45000)
                page.wait_for_selector('#Area_W .AUCTION_DETAILS tr, #Area_C .AUCTION_DETAILS tr',
                                       timeout=25000, state='attached')
                page.wait_for_timeout(1500)
                raw = json.loads(page.evaluate(READ_JS))
            except Exception as e:
                print('  rf_status %s %s: not read (%s)' % (co, iso, str(e)[:60]))
                continue
            g, w = parse_page(raw, iso, co, ts)
            got.update(g)
            waiting.setdefault(iso, set()).update(w)
            read.add(iso)
            print('  rf_status %s %s: %d closed/canceled, %d waiting' % (co, iso, len(g), len(w)))
        ctx.close()
    return got, waiting, read


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', action='store_true')
    a = ap.parse_args()
    today = datetime.date.today()
    cur = _load(OUT, {})
    if a.report:
        import collections
        c = collections.Counter(e.get('st') for e in cur.values() if isinstance(e, dict))
        print('rf_status: %d entries  %s' % (len(cur), dict(c)))
        return 0
    try:
        dates = dates_to_read(_load(ARCHIVE, {}), today)
        if not dates:
            print('rf_status: no upcoming sale dates in the archive')
            return 0
        got, waiting, read = read_all(dates, today)
        if not read:
            print('rf_status: no date could be read, file left as it was')
            return 0
        new = merge(cur, got, waiting, read, today)
        tmp = OUT + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(new, f, indent=1, sort_keys=True)
        os.replace(tmp, OUT)
        import collections
        c = collections.Counter(e['st'] for e in new.values())
        print('rf_status: %d dates read, %d entries  %s' % (len(read), len(new), dict(c)))
    except Exception as e:
        print('rf_status: skipped (%s)' % str(e)[:120])
    return 0


if __name__ == '__main__':
    sys.exit(main())
