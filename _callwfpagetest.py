"""Builds the Call Mode page from SOURCE and runs _callwfpage_run.js against it (work timer + callbacks)."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode  # noqa: E402

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 4, "shipped": 4}')
fd, path = tempfile.mkstemp(suffix='.html')
os.close(fd)
open(path, 'w', encoding='utf-8').write(html)
try:
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), os.path.join(HERE, '_callwfpage_run.js'), path],
                       capture_output=True, text=True, timeout=120)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    sys.exit(p.returncode)
finally:
    os.remove(path)
