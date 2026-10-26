"""Morning Worker "Send to Call Mode" button (2026-10-26): runs ci_js/worker_send_callmode.js under node
against the real worker functions in tracker_template.html. Fake leads, no network, nothing sent.
    python _workersendcallmodetest.py
"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
r = subprocess.run(['node', os.path.join(HERE, 'ci_js', 'worker_send_callmode.js'),
                    os.path.join(HERE, 'tracker_template.html')], capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip())
sys.exit(1 if r.returncode else 0)
