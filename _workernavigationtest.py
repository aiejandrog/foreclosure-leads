"""Worker delayed navigation after a reply: synthetic Node regression, no sends."""
import pathlib
import subprocess
import sys
here = pathlib.Path(__file__).resolve().parent
sys.exit(subprocess.run(['node', str(here / 'ci_js' / 'worker_navigation_race.js'),
                         str(here / 'tracker_template.html')]).returncode)
