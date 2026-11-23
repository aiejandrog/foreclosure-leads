"""_workerladdertest -- Morning Worker refusals that are about the LEAD'S DATA take the next channel, not a FAIL.

Run:  python _workerladdertest.py   (exit 0 = safe; invented rows, no network, nothing sent)

WHY (2026-11-22, the 8:00 AM run): of 41 email-lane leads, 12 were delivered and 29 were refused:
  20  mail_guard: 'the subject names the fallback "your property" instead of a street' (no street on the lead)
   7  'send refused - case X has no entry in sale_history_cache.json' (brand-new MD filing, nightly not there yet)
   2  'HELD - every address on file for this owner is known dead'
 plus 5 'no email to send (no reachable mailbox)' SKIPs that never reached the text/call queue.
The refused leads logged FAIL (!), burned their slot, came back identical the next morning, and never
reached the text batch or call list.

Pins:
  - _cardMail on a lead with an email and NO street returns no payload and mailWhy 'nostreet'; the same
    lead WITH a street still builds a message
  - the worker's regexes match the exact refusal strings from the log, and ONLY those
  - the template wires: attempt() falls through on an empty card and on a data refusal; the stay-cache
    lag is a HOLD (skip), never a FAIL
  - the text never says "at your property" when the street is missing
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _balloonlanetest as BL  # noqa: E402
import _stressboardtest as SB  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


tpl = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()

# ---- the regex the worker uses, lifted from the template and run against the real log lines
m = re.search(r'/(refusing to send to \.\*\(your property\|greeting is only\)\|every address on file for this owner is known dead)/i', tpl)
rec('the data-refusal regex is in the worker', m is not None)
rx = re.compile(m.group(1), re.I) if m else None
LOG = {
    'your property (subject)': 'refusing to send to a@example.invalid — the subject names the fallback "your property" instead of a street. Fix the lead data or the template; the message was NOT sent',
    'your property + Hi,': 'refusing to send to a@example.invalid — the subject names the fallback "your property" instead of a street; the greeting is only \'Hi,\' on an outreach subject. Fix the lead data',
    'body fallback': 'refusing to send to a@example.invalid — the subject names the fallback "your property" instead of a street; the body names the fallback "your property" instead of a street near \'about your property.\'.',
    'known dead': 'HELD — every address on file for this owner is known dead (dead: hard-bounced before (bounced_emails.json)). Nothing was sent.',
}
for k, v in LOG.items():
    rec('ladder: %s' % k, rx is not None and bool(rx.search(v)))
NOT = {
    'stay cache lag': 'send refused — case 2026-019205-CA-01 has no entry in sale_history_cache.json -- sale_history.py has not read its docket yet',
    'opt-out': 'refusing to send to a@example.invalid — this address opted out',
    'placeholder': 'refusing to send to a@example.invalid — the body still contains the placeholder [first name]',
}
for k, v in NOT.items():
    rec('NOT a ladder step: %s' % k, rx is not None and not rx.search(v))
rec('the stay-cache lag is said as a HOLD (skip), not a FAIL', "has no entry in sale_history_cache" in tpl and 'stay-cache-lag' in tpl)
rec('attempt() falls through on an empty card', 'no address the send bridge will accept for this lead' in tpl and "post(\"skip\", {qc:qc, why:\"unsendable-ladder\"" in tpl)
rec('doSend takes the fall-through as its second argument without changing its signature', "function doSend(r){'" in tpl and "var fall = arguments[1];" in tpl)

# ---- _cardMail on the board
EMAIL = 'owner@example.invalid'
withst = SB.fc(31, emails=[EMAIL], addr='123 NW 45TH ST, MIAMI, FL 33101')
nost = SB.fc(32, emails=[EMAIL], addr='')
nost2 = SB.fc(33, emails=[EMAIL], addr=', MIAMI, FL')
try:
    from playwright.sync_api import sync_playwright
    import pathlib, tempfile
    fake, preview_empty = BL.preview_constants()
    html = BL.build_html([withst, nost, nost2], preview_empty)
    path = os.path.join(tempfile.mkdtemp(prefix='ladder_'), 'board.html')
    open(path, 'w', encoding='utf-8').write(html)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
        ctx = b.new_context(viewport={'width': 1400, 'height': 900})
        ctx.route('**/*', lambda rt: rt.continue_() if rt.request.url.startswith('file:') else rt.abort())
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)[:200]))
        pg.goto(pathlib.Path(path).as_uri()); pg.wait_for_timeout(900)
        o = pg.evaluate("""(cs) => { const by = c => DATA.find(r => r.case === c); const out = {};
            cs.forEach(k => { const r = by(k[1]); const m = _cardMail(r); out[k[0]] = {why: m.mailWhy, hasBody: !!(m.subj && m.body && m.to), subj: m.subj || ''}; });
            return out; }""", [['with', withst['case']], ['none', nost['case']], ['comma', nost2['case']]])
        b.close()
    rec('a lead WITH a street is not stopped by the street check (its subject names the street)',
        o['with']['why'] != 'nostreet' and '123 NW 45TH ST' in o['with']['subj'], o['with'])
    rec('no street (blank): no payload and mailWhy nostreet', (not o['none']['hasBody']) and o['none']['why'] == 'nostreet', o['none'])
    rec('no street (only ", MIAMI, FL"): the same', (not o['comma']['hasBody']) and o['comma']['why'] == 'nostreet', o['comma'])
    rec('no page error', not errs, errs)
except ImportError:
    print('SKIP browser half (playwright missing)')

# ---- the text never says "at your property"
tb = re.search(r"var hasSt = !!String\(o\.stEN \|\| o\.stES \|\| ''\)\.trim\(\);", tpl)
rec('the notice text drops " at <street>" when there is no street', tb is not None and "(hasSt ? ' at ' + st : '')" in tpl)

# ---- ONE TEXT PER PERSON: textQAdd run in node against the real lines in the template
import json
import subprocess
lines = tpl.splitlines()
i0 = next(i for i, l in enumerate(lines) if l.strip() == "+   'function textQAdd(r){'")
body = []
for l in lines[i0:]:
    body.append(l)
    if l.strip() == "+   '}'":
        break
js_src = []
for l in body:
    mm = re.match(r"\s*\+\s*'(.*)'\s*$", l)
    if mm:
        js_src.append(mm.group(1).replace('\\\\', '\\').replace("\\'", "'"))
JS = ("var TEXTQ=[], LOG=[]; function addLog(k,w,m){ LOG.push(m); } function renderTextQ(){}\n" + "\n".join(js_src) + """
textQAdd({c:'A1', first:'Pat', phones:['(954) 555-0101'], pcs:['A2']});
textQAdd({c:'A2', first:'Pat', phones:['9545550101'], pcs:['A1']});
textQAdd({c:'B1', first:'Lee', phones:['9545550102'], pcs:['B2']});
textQAdd({c:'B2', first:'Lee', phones:['3055550199'], pcs:['B1']});
textQAdd({c:'C1', first:'Kim', phones:['9545550101']});
textQAdd({c:'D1', first:'Zed', phones:['9545550103']});
textQAdd({c:'D1', first:'Zed', phones:['9545550103']});
console.log(JSON.stringify({q: TEXTQ.map(function(x){return x.c;}), log: LOG.length}));
""")
r = subprocess.run(['node', '-e', JS], capture_output=True, text=True, timeout=30)
if r.returncode != 0:
    rec('textQAdd runs in node', False, r.stderr[:400])
else:
    o = json.loads(r.stdout)
    rec('one text per person: same phone, sibling case either way, same case twice', o['q'] == ['A1', 'B1', 'D1'], o)
    rec('...and each cross-case skip says so in the log (3 of them; a repeat of the same case is silent)', o['log'] == 3, o)

print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
