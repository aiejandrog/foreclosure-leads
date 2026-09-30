"""Call Mode "We already reinstated" button (2026-09-30). Container-safe: no browser, no board.

Lifts the guided-flow functions out of call_mode._PAGE and runs them under node with the real FLOW:
the button shows on the disarm / frame / q4 steps, routes to the reinstated step in EN and ES, and
logging from that path writes ONE dated line to n.note and nothing else (no status, no suppression
field). A path that never visited the step writes no note.
"""
import json
import os
import subprocess
import sys
import tempfile

import call_mode

P = call_mode._PAGE


def fn(name):
    i = P.index('function ' + name + '(')
    j = P.index('{', i)
    d = 0
    for k in range(j, len(P)):
        if P[k] == '{':
            d += 1
        elif P[k] == '}':
            d -= 1
            if d == 0:
                return P[i:k + 1]
    raise SystemExit('unterminated ' + name)


STUBS = r"""
var SCRIPT_ALL={flow:FLOW,obj:[],op:{}}; var cur={c:'2025-000001-CA-01',st:'FC'};
var notes={}; var _g={on:true,step:'greet',stack:[],ret:null};
var clicked=[]; var LANG='en';
function lang(){return LANG;} function langChips(){return '';}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function fillScript(s){return s;} function say(en,es){return '<div class="say">'+(LANG==='es'?es:en)+'</div>';}
function today(){return '2026-09-30';} function toast(m){clicked.push('toast:'+m);}
function renderSheet(){} var OUTCOMES=[{k:'notint',t:'Soft no'},{k:'dnc',t:'Hard no'}];
var document={querySelector:function(q){var m=q.match(/data-oc="(\w+)"/); return {click:function(){clicked.push(m[1]);}};}};
"""

CHECKS = r"""var ok=0,bad=0; function chk(c,m){ if(c){ok++;} else {bad++; console.log('FAIL '+m);} }
_g.step='disarm'; var h=renderGuided(cur,SCRIPT_ALL,true,false);
chk(h.indexOf('data-gto="reinstated">We already reinstated')>=0,'disarm has button');
_gGo('reinstated'); chk(_g.step==='reinstated','routes to reinstated');
h=renderGuided(cur,SCRIPT_ALL,true,false);
chk(h.indexOf('best way this can end')>=0,'EN line');
LANG='es'; h=renderGuided(cur,SCRIPT_ALL,true,false); chk(h.indexOf('mejor forma')>=0,'ES line'); LANG='en';
['end:notint','frame','end:dnc'].forEach(function(t){chk(h.indexOf('data-gto="'+t+'"')>=0,'branch '+t);});
_gGo('end:notint'); _gGo('log:notint');
chk(clicked[0]==='notint','clicked notint');
chk(notes[cur.c].note==='2026-09-30 owner says loan reinstated','note written: '+JSON.stringify(notes[cur.c]));
chk(notes[cur.c].status==='' && Object.keys(notes[cur.c]).length===2,'no other fields');
_gGo('log:notint'); chk(notes[cur.c].note.split('\n').length===1,'deduped');
// a path that never saw reinstated writes no note
notes={}; _g={on:true,step:'f15',stack:['greet','disarm'],ret:null}; _gGo('log:notint'); chk(!notes[cur.c],'no note off-path');
_g.step='frame'; chk(renderGuided(cur,SCRIPT_ALL,true,false).indexOf('data-gto="reinstated"')>=0,'frame button');
_g.step='q4'; chk(renderGuided(cur,SCRIPT_ALL,true,false).indexOf('data-gto="reinstated"')>=0,'q4 button');
console.log(ok+' ok, '+bad+' failed'); process.exit(bad?1:0);
"""


def main():
    src = '\n'.join(fn(n) for n in ['_gStep', '_gObj', '_gBtn', '_gTone', '_gListen', 'renderGuided', '_gGo'])
    js = 'var FLOW=' + json.dumps(call_mode.FLOW, ensure_ascii=False) + ';\n' + STUBS + src + '\n' + CHECKS
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
    try:
        r = subprocess.run(['node', f.name], capture_output=True, text=True)
    finally:
        os.unlink(f.name)
    sys.stdout.write(r.stdout + r.stderr)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
