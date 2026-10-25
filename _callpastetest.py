"""Builds the Call Mode page from SOURCE and runs _callpaste_run.js against it (paste a Whitepages number and call it)."""
import os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode  # noqa: E402

tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
_wpe = re.search(r"const _wpe = \(nn\.wpe\|\|\[\]\).*?Whitepages, pasted", tpl, re.S)
if not _wpe or 'mailto:' in _wpe.group(0):
    sys.exit('board pasted-email row must show the address as text, not a mailto link')
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), "test"),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 4, "shipped": 4}')
fd, path = tempfile.mkstemp(suffix='.html')
os.close(fd)
open(path, 'w', encoding='utf-8').write(html)
try:
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), os.path.join(HERE, '_callpaste_run.js'), path],
                       capture_output=True, text=True, timeout=120)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    sys.exit(p.returncode)
finally:
    os.remove(path)
