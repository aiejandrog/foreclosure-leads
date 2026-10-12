"""Scraper status line for the board header (2026-10-12).

Parses leads-run.log into a one-line health panel baked into the board header, so a silent
scraper failure OR a silent recovery is visible at a glance — the exact blind spot that nearly
cost a redundant 90-minute re-scrape on 2026-10-12 (the 5:30 run failed on DNS, the 12:21 run
recovered it, and nothing on the board said so).

Shows COUNTS AND TIMES ONLY — no names, no addresses — so it is safe in the public encrypted board.

FAIL-SAFE CONTRACT: scrape_status_html() must NEVER raise. Any parse/IO error returns a neutral
"status unavailable" span. A status widget is not allowed to break the build that ships the board.
"""
import datetime
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'leads-run.log')

# ==== REFRESH ENDED rc=3 Mon 10/12/2026  5:38:21.88 ====
_END = re.compile(r'REFRESH ENDED rc=(\d+)\s+\w{3}\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})')
# ==================== REFRESH Mon 10/12/2026 12:21:39.33 ====
_START = re.compile(r'={6,}\s*REFRESH\s+\w{3}\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})')
_COUNTY = re.compile(r'\b(MIAMI-DADE|BROWARD|PALM BEACH):\s*(\d+)\s*row')
_NETFAIL = re.compile(r'net-ready:\s*REFUSING')


def _dt(mm, dd, yyyy, h, mi, s):
    try:
        return datetime.datetime(int(yyyy), int(mm), int(dd), int(h), int(mi), int(s))
    except Exception:
        return None


def _parse_runs(text):
    """Walk the log into ordered runs: {start, end, rc, counties{}, netfail}. Latest last."""
    runs, cur = [], None
    for ln in text.splitlines():
        ms = _START.search(ln)
        if ms:
            if cur:
                runs.append(cur)
            cur = {'start': _dt(ms.group(1), ms.group(2), ms.group(3), ms.group(4), ms.group(5), ms.group(6)),
                   'end': None, 'rc': None, 'counties': {}, 'netfail': False}
            continue
        if cur is None:
            continue
        if _NETFAIL.search(ln):
            cur['netfail'] = True
        mc = _COUNTY.search(ln)
        if mc:
            cur['counties'][mc.group(1)] = int(mc.group(2))
        me = _END.search(ln)
        if me:
            cur['rc'] = int(me.group(1))
            cur['end'] = _dt(me.group(2), me.group(3), me.group(4), me.group(5), me.group(6), me.group(7))
            runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    return [r for r in runs if r.get('start')]


def _htime(dt):
    s = dt.strftime('%I:%M %p').lstrip('0')
    return s


def _rc_reason(run):
    if run.get('netfail') or run.get('rc') == 3:
        return 'network not up'
    rc = run.get('rc')
    return ('rc=%s' % rc) if rc not in (None, 0) else 'incomplete'


def status(now=None):
    """Structured status (dict) or None if the log cannot be read. Pure data, for tests."""
    if not os.path.exists(LOG):
        return None
    try:
        text = open(LOG, encoding='utf-8', errors='replace').read()
    except Exception:
        return None
    # tail is enough — today's runs are at the end
    text = '\n'.join(text.splitlines()[-8000:])
    runs = _parse_runs(text)
    if not runs:
        return None
    now = now or datetime.datetime.now()
    today = now.date()
    scraped = [r for r in runs if r['counties']]                 # runs that actually pulled counties
    last_good = scraped[-1] if scraped else None
    latest = runs[-1]
    today_runs = [r for r in runs if r['start'] and r['start'].date() == today]
    failed_today = [r for r in today_runs if (r.get('netfail') or (r.get('rc') not in (None, 0, 7)))
                    and not r['counties']]
    good_today = last_good is not None and last_good['start'].date() == today
    out = {
        'good_today': good_today,
        'last_good_when': last_good['start'] if last_good else None,
        'counties': last_good['counties'] if last_good else {},
        'recovered': good_today and bool(failed_today),
        'first_fail_today': failed_today[0] if failed_today else None,
        'stale_days': (today - last_good['start'].date()).days if last_good else None,
        'latest_rc': latest.get('rc'),
        'latest_reason': _rc_reason(latest),
    }
    return out


def scrape_status_html(now=None):
    """One-line HTML panel for the board header. NEVER raises — neutral span on any failure."""
    try:
        st = status(now=now)
        if st is None:
            return ('<div class="scrapestat" style="font:12px/1.4 system-ui,sans-serif;color:#9aa;'
                    'padding:4px 10px">Scraper status unavailable (no run log on this build host)</div>')
        order = ('BROWARD', 'MIAMI-DADE', 'PALM BEACH')
        label = {'BROWARD': 'Broward', 'MIAMI-DADE': 'Miami', 'PALM BEACH': 'Palm Beach'}
        cts = ' · '.join('%s %d' % (label[c], st['counties'][c]) for c in order if c in st['counties'])
        if st['good_today']:
            when = _htime(st['last_good_when'])
            msg = '&#x2705; Scraper: today %s — %s' % (when, cts or 'scraped')
            if st['recovered'] and st.get('first_fail_today') and st['first_fail_today'].get('start'):
                ft = _htime(st['first_fail_today']['start'])
                msg += ' <span style="opacity:.75">(%s run failed, auto-recovered)</span>' % ft
            color, bg = '#1a7f37', 'rgba(26,127,55,.10)'
        else:
            lastwhen = st['last_good_when'].strftime('%b %d') if st['last_good_when'] else 'unknown'
            days = st['stale_days']
            stale = ('%d day%s old' % (days, '' if days == 1 else 's')) if days is not None else 'stale'
            failt = _htime(st['first_fail_today']['start']) if st.get('first_fail_today') and st['first_fail_today'].get('start') else None
            at = (' at %s' % failt) if failt else ''
            msg = ('&#x274C; Scraper: FAILED%s (%s) — board %s, last good scrape %s'
                   % (at, st['latest_reason'], stale, lastwhen))
            color, bg = '#b42318', 'rgba(180,35,24,.10)'
        return ('<div class="scrapestat" style="font:12.5px/1.45 system-ui,sans-serif;font-weight:600;'
                'color:%s;background:%s;padding:5px 12px;border-radius:6px;margin:2px 0">%s</div>'
                % (color, bg, msg))
    except Exception:
        return ('<div class="scrapestat" style="font:12px/1.4 system-ui,sans-serif;color:#9aa;'
                'padding:4px 10px">Scraper status unavailable</div>')


if __name__ == '__main__':
    import json
    print(json.dumps(status(), default=str, indent=1))
    print(scrape_status_html())
