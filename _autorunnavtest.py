"""Morning Worker: the moment after a /send answers (lane switch, Stop, Start). Supersedes #318.

Runs the real worker functions from tracker_template.html under node with a fake clock and a fake
bridge (ci_js/worker_nav_race.js). Fake leads, *.invalid addresses, no network, nothing sent.
  1. a lane switch right after an answer must not skip the new lane's lead 1;
  2. Stop right after an answer must not leave the run on the lead just emailed (Start would
     re-send it and the bridge's 409 would log a false skip).
Run: python _autorunnavtest.py"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
r = subprocess.run(['node', os.path.join(HERE, 'ci_js', 'worker_nav_race.js'),
                    os.path.join(HERE, 'tracker_template.html')], capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip())
sys.exit(1 if r.returncode else 0)
