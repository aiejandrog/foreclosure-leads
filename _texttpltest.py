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
rec('every tone x filing type has an English body',
    all(v.get('en') for k, v in _bodies), len(_bodies))
rec('no body renders the word undefined',
    not any('undefined' in (v.get('en', '') + v.get('es', '')) for k, v in _bodies))
# House voice rule, stated in the template above these bodies: no em dashes in a text, they read as
# machine-written. Worth a check because the bodies are edited by hand.
rec('no em dash in any body',
    not any('—' in (v['en'] + v['es']) for k, v in _bodies))
rec('a missing street falls back to words, never a blank',
    'at your property on' in MATRIX['_nostreet_es']['en'],
    MATRIX['_nostreet_es']['en'][:120])
rec('a missing first name still renders the notice',
    MATRIX['_nofirst']['en'].startswith('FINAL NOTICE from Biscayne Solutions Group: ')
    and 'for Current Resident at' in MATRIX['_nofirst']['en'],
    MATRIX['_nofirst']['en'][:80])

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

# ──────────────────────────────────────────── 3. the words he approved
# 2026-11-20 (later): FINAL NOTICE, English only, proper company caps, the prospect's own name.
# Character for character -- this is homeowner-facing copy.
APPROVED_EN = ("FINAL NOTICE from Biscayne Solutions Group: A priority FREE foreclosure-prevention "
               "consultation has been scheduled for HILL at 1887 NW 44 ST on the "
               "scheduled date at 9:00 am. This is our final attempt to reach you. "
               "Text C to confirm or R to reschedule."
               " If now's not a good time, just let me know and I won't text you again."
               " Msg&data rates may apply.")
rec('the English notice is the approved wording, character for character',
    MATRIX['cold|LP']['en'] == APPROVED_EN, MATRIX['cold|LP']['en'])
rec('the notice ships in ONE language -- Spanish comes back empty',
    MATRIX['cold|LP']['es'] == '', repr(MATRIX['cold|LP']['es']))
rec('every tone ships the SAME notice -- the ladder is retired',
    len(set(v['en'] for k, v in _bodies)) == 1)
SC = node(BLOCK + r'''
function st(x){ return _textBodies({first:'Juan', sender:'S', stEN:x, stES:x, tone:'cold'}); }
console.log(JSON.stringify({
  caps: st('6270 NW 18 ST').en, unit: st('100 N BISCAYNE BLVD APT 3B').en,
  apos: st("12 O'BRIEN CT").en, ph: st('[street]').en, none: st('').en,
  dt: _textBodies({first:'Juan', sender:'S', stEN:'1 MAIN ST', stES:'1 MAIN ST', date:'2026-11-25', time:'2:30 pm'}),
  nofirst: _textBodies({sender:'S', stEN:'1 MAIN ST', stES:'1 MAIN ST'}).en
}));
''')
rec('the street passes through exactly as the board holds it', '6270 NW 18 ST' in SC['caps'], SC['caps'][60:120])
rec('a unit number is left alone', '100 N BISCAYNE BLVD APT 3B' in SC['unit'], SC['unit'][60:130])
rec('a name with an apostrophe survives', "12 O'BRIEN CT" in SC['apos'], SC['apos'][60:120])
rec('a placeholder street passes through untouched', '[street]' in SC['ph'], SC['ph'][60:120])
rec('an empty street still falls back', 'at your property on' in SC['none'], SC['none'][60:130])
rec('a real appointment date and time replace the defaults',
    'on 2026-11-25 at 2:30 pm' in SC['dt']['en'], SC['dt']['en'][60:140])
rec('the prospect name replaces Current Resident', 'scheduled for Juan at' in SC['caps'], SC['caps'][:120])
rec('no first name falls back to Current Resident', 'for Current Resident at' in SC['nofirst'], SC['nofirst'][:120])

# ─────────────────────────────────────────────────────────── 4. nobody copied them
# The point of the whole change. One phrase, in no comment anywhere, English only.
for phrase, lang in (('FINAL NOTICE from ', 'EN'),):
    rec('the %s body exists exactly once, in the template' % lang,
        TPL_SRC.count(phrase) == 1 and strip_js_comments(CM_SRC).count(phrase) == 0,
        'template %d, call_mode %d' % (TPL_SRC.count(phrase), CM_SRC.count(phrase)))
rec('the board still routes its own text through the shared bodies',
    '_textBodies({' in extract(TPL_SRC, 'smsMsg'))
rec('the board tone rule is the shared one too',
    '_textTone(' in extract(TPL_SRC, '_defaultTextTone'))

# ─────────────────────────────────────────────────────────── 5. the page: adapter + wiring
# 2026-10-13 (Alejandro: "i want call mode to send this out what morning worker does for texts").
# The phone's ladder button sends the worker's message itself: the shared bodies, English then
# Spanish in one text, exactly smsMsg(r, 'both'). Balloon rows keep the page's own ladder.
PAGE = strip_js_comments(C._PAGE)
BTB = extract(C._PAGE, 'boardTextBody')
TB = extract(C._PAGE, 'textBody')
rec('the phone composes from the shared bodies, not from a local table',
    '_textBodies({' in BTB and '_textTone(' in BTB)
rec('the phone picks its rung from ITS OWN stage count', 'textStage(r)' in BTB)
rec('the phone sends ONE language, and the concat tolerates an empty es',
    "B.en + (B.es ? '\\n\\n' + B.es : '')" in BTB)
rec('the ladder button sends the worker message on every homeowner row',
    "if(!(r && r.st === 'BAL')) return boardTextBody(r);" in TB)
rec('the old two-chip choice is gone', 'data-sit="batch"' not in C._PAGE and "_txk==='batch'" not in C._PAGE)

# Every hard gate in afterCall's if/else chain must sit ABOVE the ladder send button.
after = C._PAGE[C._PAGE.index('function afterCall(r, o, nextC){'):]
after = after[:after.index("var go = function(){ advance(r.c, nextC); };")]
_btn = after.index("var lbl = txLabel('ladder', st);")
for gate, why in ((r"hardSuppressed\(r\)", 'suppression'),
                  (r"o\.k==='badnum'", 'bad number'),
                  (r"dnt\b", 'do-not-text'),
                  (r"!fl\.ok", 'FTSA hours'),
                  (r"st === 'retired'", 'retired ladder'),
                  (r"st === 'replied'", 'they already replied')):
    m = [x for x in re.finditer(gate, after) if x.start() < len(after)]
    rec('the text button sits below the %s gate' % why,
        bool(m) and m[0].start() < _btn, 'gate at %s, button at %d' % (m[0].start() if m else None, _btn))

rec('the send button routes the ladder through textBody',
    "(_txk==='ladder') ? textBody(r, st)" in C._PAGE)
tx = C._PAGE[C._PAGE.index("if($('tx')) $('tx').onclick = function(){"):]
tx = tx[:tx.index("$('txn').onclick")]
rec('a text spends ONE touch -- one write, one ledger',
    tx.count('touches.push(') == 1, tx.count('touches.push('))
rec('and it is still logged as an open, not a send, until he confirms',
    'n.textopen = today();' in tx and "$('txy').onclick" in tx)
rec('the log line says which script went out', 'txKind(_txk, st)' in tx)

# The words that actually leave the phone, run end to end through the page's own adapter.
PH = node(BLOCK + '\n' + BTB + '\n' + TB + r'''
var SENDER = {name:'Alejandro Gonzalez'}, _STAGE = 'cold';
function liveDays(r){ return r.lp ? null : r.d; }
function firstName(r){ return r.on || ''; }
function textStage(r){ return _STAGE; }
function lang(){ return 'en'; }
function fillScript(t){ return 'LADDER:' + t; }
var TEXT_T = {cold:'balloon cold'}, TEXT_T_ES = TEXT_T;
function _lcNames(t){ return t; }
var base = {on:'Juan', a:'3461 NW 171 ST, MIAMI GARDENS, FL 33056', x:'11/20/2026'};
function row(o){ return Object.assign({}, base, o); }
var out = {
  cold:   textBody(row({d: 40, st: ''}), 'cold'),
  urgent: textBody(row({d: 5, st: ''}), 'cold'),
  lp:     textBody(row({d: 9999, lp: 1}), 'cold'),
  td:     textBody(row({d: 40, td: 1}), 'cold'),
  bal:    textBody(row({d: 9999, st: 'BAL'}), 'cold'),
  nodate: textBody(row({d: 0, st: '', x: ''}), 'cold'),
  noaddr: textBody(row({d: 40, st: '', a: ''}), 'cold')
};
_STAGE = 'follow'; out.follow = textBody(row({d: 40, st: ''}), 'follow');
_STAGE = 'final';  out.last   = textBody(row({d: 40, st: ''}), 'final');
console.log(JSON.stringify(out));
''')
_OPT_EN = " If now's not a good time, just let me know and I won't text you again."
NOTICE_EN = ("FINAL NOTICE from Biscayne Solutions Group: A priority FREE foreclosure-prevention "
             "consultation has been scheduled for Juan at 3461 NW 171 ST on the scheduled date at "
             "9:00 am. This is our final attempt to reach you. Text C to confirm or R to reschedule."
             + _OPT_EN + " Msg&data rates may apply.")
rec('phone first text is the notice, word for word', PH['cold'] == NOTICE_EN, PH['cold'][:160])
rec('the notice goes out in English only', '\n\n' not in PH['cold'], PH['cold'][:160])
rec('every phone text is the SAME notice -- the ladder is retired',
    len(set(PH[k] for k in ('cold', 'urgent', 'lp', 'td', 'follow', 'last'))) == 1,
    len(set(PH[k] for k in ('cold', 'urgent', 'lp', 'td', 'follow', 'last'))))
rec('the phone row ships the tax-deed flag', "'td': 1 if (d.get('st') or d.get('sale_type')) == 'TD' else 0" in CM_SRC)
rec('a balloon row keeps the page ladder, not the notice', PH['bal'].startswith('LADDER:'), PH['bal'][:60])
rec('the phone reads the live day count, not the baked one', 'liveDays(r)' in BTB)
rec('a phone row with no auction date gets no countdown and no urgency',
    'is today' not in PH['nodate'] and 'es hoy' not in PH['nodate']
    and '3461 NW 171 ST' in PH['nodate'], PH['nodate'][:160])
rec('the phone picks the tone from the same live count it prints', '_textTone(textStage(r), dd,' in BTB)
rec('a missing street falls back to words, never a blank',
    'at your property on' in PH['noaddr'] and ' for .' not in PH['noaddr'], PH['noaddr'][:200])
rec('the notice carries the let-me-know line', PH['cold'].count(_OPT_EN) == 1)
rec('the board text reads the live sale days, not the baked r.days',
    'days: _saleDays(r)' in extract(TPL_SRC, 'smsMsg')
    and '_saleDays(r)' in extract(TPL_SRC, '_defaultTextTone'))
rec('every phone text carries the let-me-know line exactly once',
    all(PH[k].count(_OPT_EN) == 1
        for k in ('cold', 'urgent', 'lp', 'follow', 'last')))
rec('a balloon row still gets the page ladder, not foreclosure copy', PH['bal'] == 'LADDER:balloon cold', PH['bal'])

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
