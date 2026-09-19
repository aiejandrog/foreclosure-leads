"""_texttpltest -- the text batch's script, and the phone's copy of it (there isn't one).

Run:  python _texttpltest.py   (exit 0 = safe; no network, no browser, no board, no real data)

WHY THIS EXISTS (2026-09-18, Alejandro: "the script on the text batch i need that text template
for the call mode py aswell thats an absolute best way to optimize the sms game. es and en").

The board's text batch composes every message with smsMsg(); Call Mode composed its own, from a
SECOND set of bodies (TEXT_T / TEXT_T_ES). So the same homeowner, on the same case, got one script
from the laptop and a different one from the phone -- and only the board's had been through the
compliance read. The fix is the rule this repo has already paid for twice (extract_sync_js,
extract_funnel_js): the bodies are ONE source, lifted out of tracker_template.html at build time by
call_mode.extract_text_js, and the phone runs those bytes.

WHAT THIS ASSERTS
  1. The extraction still works, and what comes across is COMPLETE -- every tone, both languages.
     A half-extracted block is silent: _textBodies would hand back {en: undefined} and the composer
     would open with the literal word "undefined" in a message to a homeowner.
  2. The two functions are PURE. The moment one reads a board row, the phone (whose rows are a
     different shape) cannot run it, and the extraction guard is what says so at build time.
  3. The words themselves are the ones Alejandro approved, character for character, EN and ES.
  4. NOBODY COPIED THEM. The bodies must appear exactly once in the repo -- in the template. A
     second copy in call_mode.py is the whole failure this change removes.
  5. The wiring: the batch script rides the SAME send path, the same touch, the same hard gates
     (suppression, do-not-text, FTSA hours) and the same 3-message lifetime ladder as the ladder
     text. A second script that skips a gate is worse than no second script.

No browser and no built board, on purpose: node evaluates the extracted block directly, so this
runs on any checkout rather than only on the armed machine.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TPL = os.path.join(HERE, 'tracker_template.html')

fails = []
checks = []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    if len(extra) > 200:
        extra = extra[:200] + ' ...'
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra) if extra else ''))
    if not ok:
        fails.append(name)


def node(js):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:3000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- the trick _textbatchtest.py uses."""
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s() is no longer in the source. It was renamed or '
                         'removed -- update this test with it.' % name)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise SystemExit('unbalanced braces extracting ' + name)


def strip_js_comments(src):
    """Commentary in these files quotes the bodies verbatim to explain them. Scan CODE."""
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
    return re.sub(r'(?m)//.*$', ' ', src)


TPL_SRC = open(TPL, encoding='utf-8').read()
CM_SRC = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()

import call_mode as C                                    # noqa: E402

# ─────────────────────────────────────────────────────────── 1. the extraction itself
BLOCK = C.extract_text_js(TPL_SRC)
rec('extract_text_js pulls the region out of the template', len(BLOCK) > 1000, '%d bytes' % len(BLOCK))
rec('both functions came across',
    'function _textTone' in BLOCK and 'function _textBodies' in BLOCK)
# PURITY IS THE CONTRACT. The board resolves the owner name and street from r.owners/r.addr, the
# phone from r.on/r.a -- neither row shape can travel, so the shared code must take neither.
rec('the block is pure -- it calls nothing it does not define',
    C.free_identifiers(BLOCK, ()) == [], C.free_identifiers(BLOCK, ()))

# ─────────────────────────────────────────────────────────── 2. every tone, both languages
MATRIX = node(BLOCK + '''
var out = {};
['cold','urgent','followup','t3'].forEach(function(t){
  [['LP',{lp:true}],['TD',{td:true}],['FC',{}]].forEach(function(kind){
    var o = {first:'HILL', sender:'Alejandro Gonzalez', stEN:'1887 NW 44 ST', stES:'1887 NW 44 ST',
             tone:t, lp:!!kind[1].lp, td:!!kind[1].td};
    out[t + '|' + kind[0]] = _textBodies(o);
  });
});
out['_nofirst'] = _textBodies({sender:'Alejandro Gonzalez', tone:'cold', lp:true});
out['_nostreet_es'] = _textBodies({first:'HILL', sender:'Alejandro Gonzalez', tone:'cold'});
out['_tone'] = {
  follow:  _textTone('follow', 9999, true),
  final:   _textTone('final', 9999, true),
  retired: _textTone('retired', 3, false),
  replied: _textTone('replied', 3, false),
  soon:    _textTone('cold', 9, false),
  soon_lp: _textTone('cold', 9, true),
  far:     _textTone('cold', 40, false),
  nodate:  _textTone('cold', 9999, false)
};
console.log(JSON.stringify(out));
''')

_bodies = [(k, v) for k, v in MATRIX.items() if not k.startswith('_')]
rec('every tone x filing type has a body in both languages',
    all(v.get('en') and v.get('es') for k, v in _bodies), len(_bodies))
rec('no body renders the word undefined',
    not any('undefined' in (v.get('en', '') + v.get('es', '')) for k, v in _bodies))
# House voice rule, stated in the template above these bodies: no em dashes in a text, they read as
# machine-written. Worth a check because the bodies are edited by hand.
rec('no em dash in any body',
    not any('—' in (v['en'] + v['es']) for k, v in _bodies))
rec('a missing street falls back in the RIGHT language',
    'su propiedad' in MATRIX['_nostreet_es']['es'] and 'your property' in MATRIX['_nostreet_es']['en'],
    MATRIX['_nostreet_es']['es'][:90])
rec('a missing first name does not leave a dangling greeting',
    MATRIX['_nofirst']['en'].startswith('Hi, ') and MATRIX['_nofirst']['es'].startswith('Hola, '),
    MATRIX['_nofirst']['en'][:40])

T = MATRIX['_tone']
rec('touch 2 selects the follow-up body', T['follow'] == 'followup', T['follow'])
rec('touch 3 selects the last-message body', T['final'] == 't3', T['final'])
rec('retired and replied pass through so the caller must refuse',
    T['retired'] == 'retired' and T['replied'] == 'replied')
rec('a sale inside 14 days is urgent', T['soon'] == 'urgent', T['soon'])
rec('a lis pendens is never urgent -- it has no sale date to be close to',
    T['soon_lp'] == 'cold', T['soon_lp'])
rec('a far sale and a dateless lead both open cold',
    T['far'] == 'cold' and T['nodate'] == 'cold')

# ─────────────────────────────────────────────────────────── 3. the words he approved
# Pasted by Alejandro 2026-09-18 with HILL and 1887 NW 44 ST standing in for the owner's first name
# and the street. Character for character: this is homeowner-facing copy that has been through the
# compliance read, and "improving" it in passing is how five versions of the disclaimer happened.
APPROVED_EN = ("Hi HILL, this is Alejandro Gonzalez with Biscayne Solutions Group. A case was just "
               "filed at the courthouse on 1887 NW 44 ST. Right now you have the most choices, and "
               "most take weeks to set up. Our senior advisor maps them free in 5 minutes. What do "
               "you want to do with the house?")
APPROVED_ES = ("Hola HILL, le escribe Alejandro Gonzalez de Biscayne Solutions Group. Acaban de "
               "abrir un caso en la corte sobre 1887 NW 44 ST. Hoy es cuando más opciones tiene, y "
               "toman semanas. Nuestro asesor principal se las explica gratis en 5 minutos. "
               "¿Qué quiere hacer con la casa?")
rec('the English just-filed body is the approved wording, character for character',
    MATRIX['cold|LP']['en'] == APPROVED_EN, MATRIX['cold|LP']['en'])
rec('the Spanish just-filed body is the approved wording, character for character',
    MATRIX['cold|LP']['es'] == APPROVED_ES, MATRIX['cold|LP']['es'])

# ─────────────────────────────────────────────────────────── 4. nobody copied them
# The point of the whole change. Two phrases that appear in no comment anywhere, one per language.
for phrase, lang in (('Right now you have the most choices', 'EN'),
                     ('Acaban de abrir un caso', 'ES')):
    rec('the %s body exists exactly once, in the template' % lang,
        TPL_SRC.count(phrase) == 1 and strip_js_comments(CM_SRC).count(phrase) == 0,
        'template %d, call_mode %d' % (TPL_SRC.count(phrase), CM_SRC.count(phrase)))
rec('the board still routes its own text through the shared bodies',
    '_textBodies({' in extract(TPL_SRC, 'smsMsg'))
rec('the board tone rule is the shared one too',
    '_textTone(' in extract(TPL_SRC, '_defaultTextTone'))

# ─────────────────────────────────────────────────────────── 5. the page: adapter + wiring
PAGE = strip_js_comments(C._PAGE)
BTB = extract(C._PAGE, 'boardTextBody')
rec('the phone composes from the shared bodies, not from a local table',
    '_textBodies({' in BTB and '_textTone(' in BTB)
rec('the phone picks its rung from ITS OWN stage count', 'textStage(r)' in BTB)
rec('the phone honours the EN|ES toggle rather than sending both after a call',
    "lang() === 'es'" in BTB and 'B.es' in BTB and 'B.en' in BTB)

# The chip is offered only where the cold ladder itself is offered. Everything above it in
# afterCall's if/else chain is a hard gate, and the batch script must sit BELOW all of them.
after = C._PAGE[C._PAGE.index('function afterCall(r, o, nextC){'):]
after = after[:after.index("var go = function(){ advance(r.c, nextC); };")]
_chip = after.index('data-sit="batch"')
for gate, why in ((r"hardSuppressed\(r\)", 'suppression'),
                  (r"o\.k==='badnum'", 'bad number'),
                  (r"dnt\b", 'do-not-text'),
                  (r"!fl\.ok", 'FTSA hours'),
                  (r"st === 'retired'", 'retired ladder'),
                  (r"st === 'replied'", 'they already replied')):
    m = [x for x in re.finditer(gate, after) if x.start() < len(after)]
    rec('the batch chip sits below the %s gate' % why,
        bool(m) and m[0].start() < _chip, 'gate at %s, chip at %d' % (m[0].start() if m else None, _chip))

rec('the batch chip is not offered on a balloon/investor row',
    "r.st === 'BAL'" in after[:_chip + 400])
rec('the send button routes the batch chip to the shared bodies',
    "(_txk==='batch')  ? boardTextBody(r)" in C._PAGE)
tx = C._PAGE[C._PAGE.index("if($('tx')) $('tx').onclick = function(){"):]
tx = tx[:tx.index("$('txn').onclick")]
rec('the batch text spends the SAME touch as the ladder -- one write, one ledger',
    tx.count('touches.push(') == 1, tx.count('touches.push('))
rec('and it is still logged as an open, not a send, until he confirms',
    'n.textopen = today();' in tx and "$('txy').onclick" in tx)
rec('the log line says which script went out', 'txKind(_txk, st)' in tx)

# ─────────────────────────────────────────────────────────── 6. the whole page still assembles
sync_js = C.extract_sync_js(TPL_SRC)
funnel_js = C.extract_funnel_js(TPL_SRC)
C._assert_page_provides(C._PAGE)
for _b in (sync_js, funnel_js, BLOCK):
    C._assert_no_dead_overrides(C._PAGE, _b)
html = C.build_html([], 0, {'ct': 'x', 'k': 'y'}, '2026-09-18T00:00', 'sig123456789',
                    'bsig12345678', sync_js, None, seat=None, funnel_js=funnel_js, text_js=BLOCK)
rec('the extracted block is actually injected into the page',
    'function _textBodies(' in html and '__TEXTTPLJS__' not in html)
_js = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
    f.write('\n'.join(_js))
    _p = f.name
_chk = subprocess.run(['node', '--check', _p], capture_output=True, text=True)
os.unlink(_p)
rec('the assembled Call Mode page parses under node', _chk.returncode == 0, (_chk.stderr or '')[:400])

print('\n%s -- %d check(s), %d failure(s)' % ('FAILED' if fails else 'PASSED',
                                              len(checks), len(fails)))
if fails:
    print('failed: ' + ', '.join(fails))
sys.exit(1 if fails else 0)
