"""Call Mode: no voicemail-sale script on a number that is not the owner's.

The voicemail says "Public records show a court sale scheduled on your property at <street>".
Whoever hears it learns about the filing. The call script's own wrong-person rule is to never
say "foreclosure" to a non-owner, and the after-call text is already withheld on household and
name-only numbers. The "Voicemail - show the script" button and the sheet's "IF NO ANSWER" block
were built for every number. Wording is NOT touched here; only whether the script is offered.

Pins: _vmHold(r, i) (run under node) and that both voicemail surfaces consult it.
  held   : ps hh / nm / wpm, pw l (likely, unconfirmed) / h / w (Whitepages says someone else's)
  offered: owner (ps ''), Whitepages-confirmed owner (pw 'o'), st with no contradiction,
           pw '-' / 'n' on an st number, no pw array at all, and every number on a lead with no
           Whitepages record
Run: python _callvmownertest.py
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callvm_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


PAGE = call_mode._PAGE
a = PAGE.find('function _vmHold(')
rec('function present: _vmHold', a >= 0)
if a >= 0:
    fn = PAGE[a:PAGE.index('\n}\n', a) + 3]
    cases = [
        ('hh', 'h', True), ('hh', 'o', True), ('nm', 'n', True), ('wpm', '', True), ('', 'l', True), ('hh', 'l', True),
        ('', 'h', True), ('', 'w', True), ('hh', 'w', True),
        ('', '', False), ('', 'o', False), ('', '-', False), ('', 'n', False), ('wp', 'o', False),
    ]
    js = fn + "\nvar cs=%s;console.log(JSON.stringify(cs.map(function(c){return !!_vmHold({ps:[c[0]],pw:c[1]},0);})));" % json.dumps(cases)
    js += "\nconsole.log(JSON.stringify([!!_vmHold({ps:null,pw:null},0), !!_vmHold({p:['3055550101']},0), !!_vmHold({ps:['','hh'],pw:'o-'},0), !!_vmHold({ps:['','hh'],pw:'o-'},1)]));"
    p = os.path.join(_T, 'vm.js')
    io.open(p, 'w', encoding='utf-8').write(js)
    q = subprocess.run([os.environ.get('NODE_BIN', 'node'), p], capture_output=True, text=True, timeout=60)
    out = q.stdout.strip().splitlines()
    rec('node ran _vmHold', q.returncode == 0 and len(out) == 2, q.stderr[-200:])
    if len(out) == 2:
        got = json.loads(out[0])
        for (ps, pw, want), g in zip(cases, got):
            rec('ps=%r pw=%r -> %s' % (ps, pw, 'held' if want else 'offered'), g == want, g)
        g2 = json.loads(out[1])
        rec('no ps/pw arrays: offered', g2[0] is False and g2[1] is False, g2)
        rec('the index picks the number: owner at 0 offered, household at 1 held', g2[2] is False and g2[3] is True, g2)

btn = PAGE.index('<button class="vmbtn" id="vmbtn">')
ctx = PAGE[btn - 400:btn]
rec('outcome-screen voicemail button sits behind _vmHold', '_vmHold(r, phIdx)' in ctx, ctx[-250:])
rec('the onclick is guarded for the absent button', "if($('vmbtn'))" in PAGE or "var _vb = $('vmbtn')" in PAGE)
blk = PAGE.index('IF NO ANSWER &mdash; LEAVE THIS')
rec('sheet IF NO ANSWER script sits behind _vmHold', '_vmHold(r, phIdx)' in PAGE[blk:blk + 1800], PAGE[blk:blk + 300])
# wording is untouched
rec('voicemail wording unchanged', call_mode.VM_SALE['en']['days'] == "Public records show a court sale scheduled on your property at {st1} in about {n} days.")
print('\n%d failed' % len(fails) if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
