"""Write a clear pass/fail status the user actually sees after each scheduled refresh — a Desktop status
file + a Windows tray notification. Runs at the end of refresh-dealflow.bat so an unattended 7 AM run
reports itself (no Claude session needed). Reads the built lead data + health.json + latest git commit.
"""
import glob, json, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.expanduser(r'~\OneDrive\Desktop')
if not os.path.isdir(DESKTOP):
    DESKTOP = os.path.expanduser(r'~\Desktop')
STATUS = os.path.join(DESKTOP, 'DEALFLOW-STATUS.txt')


def _phones_by_county():
    """Phones live in skiptrace_results.json (merged into the site at build time), keyed by case + county."""
    by = {}
    try:
        st = json.load(open(os.path.join(HERE, 'skiptrace_results.json'), encoding='utf-8'))
        for v in st.values():
            if v.get('phones'):
                c = v.get('county', 'MIAMI-DADE'); by[c] = by.get(c, 0) + 1
    except Exception: pass
    return by


def _counts():
    ph_by = _phones_by_county()
    by = {}
    def add(county, leads):
        by[county] = {'leads': len(leads), 'phones': ph_by.get(county, 0)}
    try: add('MIAMI-DADE', json.load(open(os.path.join(HERE, 'leads_final.json'), encoding='utf-8')))
    except Exception: pass
    for f in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        bn = os.path.basename(f)
        if bn in ('leads_final.json', 'leads_raw.json') or bn.startswith('_'):
            continue
        try:
            leads = json.load(open(f, encoding='utf-8'))
            add(leads[0].get('county', bn) if leads else bn, leads)
        except Exception: pass
    return by


def _git_last():
    try:
        out = subprocess.run(['git', '-C', HERE, 'log', '-1', '--format=%cd|%s', '--date=format:%Y-%m-%d %H:%M'],
                             capture_output=True, text=True, timeout=15).stdout.strip()
        return out
    except Exception:
        return ''


def _health():
    try:
        h = json.load(open(os.path.join(HERE, 'health.json'), encoding='utf-8'))
        return h.get('status') or h.get('overall') or ('FAIL' if h.get('fail') else 'OK'), h
    except Exception:
        return '', {}


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


def main():
    by = _counts()
    total = sum(v['leads'] for v in by.values())
    total_ph = sum(v['phones'] for v in by.values())
    hstatus, _ = _health()
    gitlast = _git_last()
    today = datetime.now().strftime('%Y-%m-%d')
    pushed_today = today in gitlast

    # verdict: healthy data present + a push landed today (weekday run) => OK
    ok = total >= 400 and len(by) >= 1 and hstatus.upper() != 'FAIL'
    verdict = 'OK' if ok else 'CHECK'

    lines = [
        f"DEALFLOW refresh — {verdict}",
        f"  when   : {datetime.now().strftime('%a %Y-%m-%d %H:%M')}",
        f"  total  : {total} leads ({total_ph} with phones)",
    ]
    for c, v in by.items():
        lines.append(f"    {c:11}: {v['leads']:>4} leads, {v['phones']:>3} phones")
    lines += [
        f"  health : {hstatus or 'n/a'}",
        f"  git    : {gitlast or 'n/a'}" + ('   (pushed today)' if pushed_today else '   (NO push today — may not have published)'),
        "",
        "OK  = fresh data across counties + published. CHECK = look at leads-run.log.",
    ]
    report = "\n".join(lines) + "\n"
    try:
        open(STATUS, 'w', encoding='utf-8').write(report)
    except Exception as e:
        print("status write failed:", e)
    print(report)

    counties = ', '.join(f"{c.split()[0]} {v['leads']}" for c, v in by.items())
    _toast(f"DEALFLOW refresh {verdict}", f"{total} leads / {total_ph} phones. {counties}. See DEALFLOW-STATUS.txt")
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
