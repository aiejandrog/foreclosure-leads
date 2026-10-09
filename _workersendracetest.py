"""Morning Worker /send: Stop -> Start race, the Email button mid-send, and a bridge that never answers.

Runs the real worker functions extracted from tracker_template.html under node with a fake clock and
a fake bridge (ci_js/worker_send_race.js, ci_js/worker_send_timeout.js). Fake leads, *.invalid
addresses, no network, nothing sent.   python _workersendracetest.py
"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
tpl = os.path.join(HERE, 'tracker_template.html')
fails = 0
for js in ('worker_send_race.js', 'worker_send_timeout.js'):
    r = subprocess.run(['node', os.path.join(HERE, 'ci_js', js), tpl], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        fails += 1
print('PASS' if not fails else 'FAIL')
sys.exit(1 if fails else 0)
