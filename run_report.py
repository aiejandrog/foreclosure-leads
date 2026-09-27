"""Write a clear pass/fail status the user actually sees after each scheduled refresh — a Desktop status
file + a Windows tray notification. Runs at the end of refresh-dealflow.bat so an unattended 7 AM run
reports itself (no Claude session needed). Reads the built lead data + health.json + latest git commit.
"""
import glob, json, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# Count-only status file; the Desktop location is paths.py's call, not a hardcoded OneDrive path.
try:
    import paths as _P
    DESKTOP = _P.DESKTOP
except Exception:
    DESKTOP = os.path.expanduser(r'~\Desktop')
STATUS = os.path.join(DESKTOP, 'DEALFLOW-STATUS.txt')


def _counts():
    """Current lead population by the county of EACH ROW, and the phones that belong to it.

    THREE COUNTING BUGS, all in what this printed every morning (audit 2026-09-21, defect 11):

    1. A whole FILE was attributed to its first row's county. leads_final.json is mixed -- it
       carries the Miami-Dade auction rows AND the three-county lis-pendens lane -- so all 1,361
       of its rows were reported as Miami-Dade.
    2. `by[county] = ...` OVERWROTE. leads_final.json was counted as MIAMI-DADE and then the
       Miami-Dade auction file replaced that entry, or the reverse, depending on glob order. One
       of the two numbers was always thrown away; nothing said which.
    3. Phones came from the whole skiptrace CACHE, which is add-only and outlives the leads it
       was pulled for. Broward showed 507 phones against 267 leads. A ratio over 100% is the
       tell, and it was on the status file for weeks.

    Rows are deduplicated on (county, case) across files first, because the same case appears in
    both the county file and leads_final.json. Phones are then intersected with THAT population,
    so a phone only counts when the lead it belongs to is still on the board.
    """
    pop = {}                       # (county, case) -> county, the deduplicated population
    for f in [os.path.join(HERE, 'leads_final.json')] + sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_raw.json',) or bn.startswith('_'):
            continue
        try:
            leads = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(leads, list):
            continue
        # a file name is the fallback county ONLY for rows that carry none of their own
        fallback = 'MIAMI-DADE' if bn == 'leads_final.json' else bn.replace('_leads.json', '').upper()
        for n, r in enumerate(leads):
            if not isinstance(r, dict):
                continue
            cty = str(r.get('county') or fallback).upper()
            case = str(r.get('Case #') or r.get('case') or '').strip()
            # no case number is nothing to dedupe on, so such a row gets a key unique to its
            # position in its file: counted once, never merged with another caseless row.
            pop[(cty, case or '\x00%s#%d' % (bn, n))] = True

    by = {}
    for (cty, _case) in pop:
        by.setdefault(cty, {'leads': 0, 'phones': 0})
        by[cty]['leads'] += 1

    # phones: only for cases that are still in the population above
    try:
        st = json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
    except Exception:
        st = {}
    live = {case: cty for (cty, case) in pop if not case.startswith('\x00')}
    for case, v in (st.items() if isinstance(st, dict) else []):
        if not isinstance(v, dict) or not v.get('phones'):
            continue
        cty = live.get(str(case).strip())
        if cty and cty in by:
            by[cty]['phones'] += 1
    return dict(sorted(by.items()))


def _git_last():
    try:
        out = subprocess.run(['git', '-C', HERE, 'log', '-1', '--format=%cd|%s', '--date=format:%Y-%m-%d %H:%M'],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return out
    except Exception:
        return ''


def _health():
    """(status, payload). Status is '' ONLY when health.json could not be read at all.

    '' used to mean both "no file" and "file with no status field", and the verdict tested
    `!= 'FAIL'`, so every non-FAIL string on earth -- 'DOWN', 'UNKNOWN', '' -- passed as healthy
    (audit 2026-09-21, defect 11). A missing healthcheck is the LEAST evidence of health there is.
    """
    try:
        h = json.load(open(os.path.join(HERE, 'health.json'), encoding='utf-8'))
    except Exception:
        return '', {}
    return (h.get('status') or h.get('overall') or ('FAIL' if h.get('fail') else 'OK')), h


def _lp_sweep():
    """The structured outcome lis_pendens.py now writes. (None, []) when it never ran."""
    try:
        d = json.load(open(os.path.join(HERE, 'lp_sweep_status.json'), encoding='utf-8'))
        return d.get('ran'), [c for c in (d.get('failed') or [])]
    except Exception:
        return None, []


def _toast(title, msg):
    """Best-effort Windows tray balloon — never fatal if it fails."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.BalloonTipTitle='{title}';$n.BalloonTipText='{msg}';"
        "$n.ShowBalloonTip(15000);Start-Sleep -Seconds 6;$n.Dispose()"
    )
    try:
        subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception:
        pass


MIN_LEADS = 400


def verdict_of(total, hstatus, lp_ran, lp_failed):
    """-> ('HEALTHY'|'DEGRADED'|'FAILED'|'UNKNOWN', why).

    FOUR STATES, NOT TWO (audit 2026-09-21, defect 11). The old verdict was one expression,
    `total >= 400 and len(by) >= 1 and hstatus.upper() != 'FAIL'`, which made OK the default for
    everything that was not an outright compliance failure -- a healthcheck reporting DOWN, and a
    healthcheck that had never run at all, both passed. An unattended report whose happy path is
    also its unknown path is not a signal; it is a green light wired to nothing.
    """
    if not hstatus:
        return 'UNKNOWN', 'no health.json — the healthcheck did not run, so nothing here is graded'
    if hstatus.upper() == 'FAIL':
        return 'FAILED', f'healthcheck says {hstatus}'
    if total < MIN_LEADS:
        return 'FAILED', f'only {total} leads on file'
    if hstatus.upper() not in ('OK', 'PASS'):
        return 'DEGRADED', f'healthcheck says {hstatus}'
    if lp_failed:
        return 'DEGRADED', 'LP sweep did not run for ' + ', '.join(lp_failed)
    if lp_ran is None:
        return 'DEGRADED', 'no LP sweep outcome on file'
    return 'HEALTHY', ''


def main():
    by = _counts()
    total = sum(v['leads'] for v in by.values())
    total_ph = sum(v['phones'] for v in by.values())
    hstatus, _ = _health()
    gitlast = _git_last()
    today = datetime.now().strftime('%Y-%m-%d')
    # A LOCAL commit dated today. This is NOT evidence of a push, and never was: `git log -1`
    # reads this machine's own history and knows nothing about origin or about Pages. It is
    # reported as what it is and it does not enter the verdict (audit 2026-09-21, defect 11).
    commit_today = today in gitlast
    lp_ran, lp_failed = _lp_sweep()

    state, why = verdict_of(total, hstatus, lp_ran, lp_failed)
    ok = state == 'HEALTHY'
    verdict = {'HEALTHY': 'OK', 'DEGRADED': 'DEGRADED', 'FAILED': 'CHECK', 'UNKNOWN': 'UNKNOWN'}[state]

    lines = [
        f"DEALFLOW refresh — {verdict}",
        f"  when   : {datetime.now().strftime('%a %Y-%m-%d %H:%M')}",
        f"  total  : {total} leads ({total_ph} with phones)",
    ]
    for c, v in by.items():
        lines.append(f"    {c:11}: {v['leads']:>4} leads, {v['phones']:>3} phones")
    lines += [
        f"  health : {hstatus or 'NOT RUN'}",
        f"  LP     : " + ('did not run' if lp_ran is None else
                          ('ran ' + str(lp_ran)[:16] + (' — FAILED: ' + ', '.join(lp_failed) if lp_failed else ''))),
        f"  git    : {gitlast or 'n/a'}" + ('   (local commit today)' if commit_today else '   (no local commit today)'),
        "  note   : the line above is this machine's own git log. It does not show whether anything",
        "           reached origin or whether the live site republished — publish_verify does that.",
    ]
    if why:
        lines.append(f"  why    : {why}")
    lines += [
        "",
        "OK = data present, healthcheck clean, every LP county swept.",
        "DEGRADED = the board is usable but part of the night did not run.",
        "UNKNOWN = nothing was graded. CHECK = look at leads-run.log.",
    ]
    report = "\n".join(lines) + "\n"
    try:
        open(STATUS, 'w', encoding='utf-8').write(report)
    except Exception as e:
        print("status write failed:", e)
    print(report)

    counties = ', '.join(f"{c.split()[0]} {v['leads']}" for c, v in by.items())
    _toast(f"DEALFLOW refresh {verdict}", f"{total} leads / {total_ph} phones. {counties}. See DEALFLOW-STATUS.txt")
    # 0 healthy | 1 failed | 4 degraded | 5 ungraded. The batch treats any non-zero as worth
    # reporting; splitting them lets "part of the night did not run" be told from "no data".
    sys.exit({'HEALTHY': 0, 'FAILED': 1, 'DEGRADED': 4, 'UNKNOWN': 5}[state])


if __name__ == '__main__':
    main()
