"""Call Mode: their phone's AI answered (2026-10-22).

iPhone "Ask Reason for Calling" and Pixel Call Screen show the owner a transcript of what the caller
says. Alejandro asked for a pattern-interrupt line for that moment. This pins the copy law on it (no
"foreclosure" on a lock-screen transcript, the caller's own name, EN + ES, a no-name variant) and the
wiring (greet -> screen -> greet / vm / no answer), and runs the renderer's anonymous swap under node.

Run: python _screenertest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode as CM  # noqa: E402

SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def chk(ok, name):
    print(('ok   ' if ok else 'FAIL ') + name)
    if not ok:
        fails.append(name)


F = dict((s['id'], s) for s in CM.FLOW)
greet = dict((b[1], b[0]) for b in F['greet']['br'])
chk(greet.get('screen', '').startswith("Their phone's AI answered"), 'greet offers the screener branch')
chk('vm' in greet and 'disarm' in greet and 'who' in greet, 'greet keeps its other branches')

for sid in ('screen', 'screen2'):
    s = F.get(sid)
    chk(s is not None, sid + ' step exists')
    if not s:
        continue
    lines = [s['en'], s['es'], s['aen'], s['aes']]
    blob = ' '.join(lines).lower()
    chk('foreclos' not in blob and 'ejecuci' not in blob, sid + ': never says foreclosure (lock-screen transcript)')
    chk(all('{sender}' in t for t in lines), sid + ': every variant names the caller')
    chk(all('{st1}' in t for t in lines), sid + ': every variant names the street')
    chk('{first}' in s['en'] and '{first}' in s['es'], sid + ': named variant uses the first name')
    chk('{first}' not in s['aen'] and '{first}' not in s['aes'], sid + ': no-name variant has no {first}')
    chk(s['en'].startswith('Hey phone') and s['es'].startswith('Oiga teléfono'), sid + ': talks to the phone itself (the interrupt)')
    chk(not any(w in blob for w in ('cash', 'buy your', 'investor', 'stop the sale', 'fee')), sid + ': no spam or fee words')
    tg = dict((b[1], b[0]) for b in s['br'])
    chk(tg.get('greet') == 'They picked up' and 'vm' in tg and 'end:noanswer' in tg, sid + ': picked up / message / declined')
chk(dict((b[1], b[0]) for b in F['screen']['br']).get('screen2') is not None, 'screen -> screen2 for a repeat try')

# ---- renderer: a no-name row gets the anonymous screener line, a named row the named one --------
i = SRC.find('function renderGuided(')
j = SRC.find('\n}\n', i)
fn = SRC[i:j + 2]
chk(i > 0 and 'else if(!named && s.aen)' in fn, 'renderGuided swaps aen on non-greet steps')
chk("['Their phone\\'s AI answered','screen2']" in fn, 'warm callers can still reach the screener (2nd-try line)')
js = r'''
var out=[];
function esc(t){return String(t);}
function say(en,es,r){ return '<SAY>'+fillScript(en,r)+'</SAY>'; }
function fillScript(t,r){ return String(t).split('{first}').join(r.f||'').split('{sender}').join('Alejandro').split('{st1}').join('1 Oak St'); }
function _gStep(S,id){ for(var k=0;k<S.flow.length;k++){ if(S.flow[k].id===id) return S.flow[k]; } return null; }
function _gBtn(l,t){ return '['+l+']'; }
function _gTone(t){ return ''; } function _gListen(t){ return ''; }
function _vmWarnHtml(){ return ''; } function secondName(){ return ''; }
function lang(){ return 'en'; } function langChips(){ return ''; } function _gObj(){ return null; }
var es=false, OUTCOMES=[], _g={on:true,step:'screen',stack:[],ret:null};
''' + fn + r'''
var S={flow:FLOW, op:{}};
out.push(renderGuided({f:'Maria'}, S, true, false));
out.push(renderGuided({f:''}, S, false, false));
_g.step='greet'; out.push(renderGuided({f:'Maria'}, S, true, true));
console.log(JSON.stringify(out));
'''
js = 'var FLOW=' + json.dumps(CM.FLOW, ensure_ascii=False) + ';\n' + js
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
    fh.write(js)
try:
    p = subprocess.run(['node', fh.name], capture_output=True, text=True, timeout=30)
    res = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else None
    chk(res is not None, 'renderer runs under node' + ('' if res else ' | ' + p.stderr[:600]))
    if res:
        chk("tell Maria it's Alejandro" in res[0], 'named row: "tell Maria it\'s Alejandro"')
        chk('[Their phone\'s AI answered]' in res[2], 'warm greet renders the screener button')
        chk('tell the owner of 1 Oak St' in res[1] and 'tell  it' not in res[1], 'no-name row: "the owner of" line, no blank name')
finally:
    os.unlink(fh.name)

print('\n%d failure(s)' % len(fails) if fails else '\nall screener checks pass')
sys.exit(1 if fails else 0)
