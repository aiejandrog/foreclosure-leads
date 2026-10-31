"""Worker Stop versus a rejected in-flight request; fake clock/fetch only."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
result = subprocess.run(['node', str(root / 'ci_js' / 'worker_stop_rejection.js'),
                         str(root / 'tracker_template.html')], capture_output=True, text=True)
print(result.stdout.strip() or result.stderr.strip())
sys.exit(result.returncode)
