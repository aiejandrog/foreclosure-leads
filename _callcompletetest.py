"""Call Mode "Complete" step (2026-10-30).

Alex: "have a complete button: when you press next, it shows you 'what did you do? did you email, did you
call them, or multiple things' so we can have it logged ... wire it into the logging section."

Pins:
  - Next (#skip on the lead screen) and Next lead (#nx on the post-outcome screen) open the sheet first
  - each chip writes ONE touch {ch, out, src:'manual', mk, by, d, ts, tsu}; one saveNotes() per Log
  - what is already logged today (call touch, dial outcome, text, email) is ticked and locked: never doubled
  - "Skip, log nothing" and "nothing ticked" write nothing and do not save
  - "Spoke to someone else" is a note: ch 'other', who + note stored, no phone, no wpRelatives, and it does not
    move the lead out of Untouched
  - HARD LIMITS: no dial, status, stage, no/dnc/notint, badph/dntph, optout; nothing is fetched (no /text, no
    /notes from this code path); a hard-suppressed lead is not asked anything
  - a manual email/text/call moves the lead out of Untouched into Reached out, appears in the call log, and a
    manual call/text counts toward the FTSA 24h cap

Invented leads only; JS extracted by name from call_mode.py and run under node with a tiny fake DOM.
Run: python _callcompletetest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    if i < 0 or j < 0:
        rec('function present: ' + name, False)
        return ''
    return SRC[i:j + 2]


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


a = SRC.find('var _CMP = null;')
b = SRC.find('function _stageWord(')
CMP = SRC[a:b] if a >= 0 and b > a else ''
rec('the Complete block exists in call_mode.py', bool(CMP))

# ---- wiring, read from the source
rec('#nx opens the sheet (after the open-deal date guard), then go()',
    "completeThen(r, go)" in SRC and "if(ayNeedsDate(notes[r.c])) return go();" in SRC)
rec('#skip on the lead screen opens the sheet, then navNext', "completeThen(cur, navNext)" in SRC)
rec('the recap / blocked-lead Next buttons are untouched (nothing to log on them)',
    "$('rcgo').onclick=function(){ go(); };" in SRC and "$('skip').onclick = function(){ advance(r.c); };" in SRC)
rec('the block never fetches, posts, or touches a ledger, status, dial list or phone list',
    all(x not in CMP for x in ('fetch(', 'bridgeFetch', '/text', '/notes', 'mail_sent', 'text_sent', 'sendBeacon',
                               'wpRelatives', 'status=', 'status =', 'dials.push', 'dials =', '.p.push', 'badph',
                               'dntph', 'optout', '.no =', 'wpe')))

FNS = ''.join(grab_fn(n) for n in ('_inbound', 'lastCall', 'lastOutreach', '_replyOpen', '_contactTier', '_histWhy',
                                   '_viewOf', 'hardSuppressed', '_tele24n', 'screenRegistry', 'agoTxt'))
HARNESS = r"""
var store = {}, __fetch = [], __toasts = [], __saves = 0, __syncs = 0, _FCGEN = 0, SCREEN = 'lead';
var localStorage = {getItem:function(k){return k in store ? store[k] : null;}, setItem:function(k,v){store[k]=String(v);}};
globalThis.fetch = function(u){ __fetch.push(u); return Promise.resolve({}); };
var window = globalThis, navigator = {};
function toast(t){ __toasts.push(t); }
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function today(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function nowTS(){return new Date().toLocaleString();}
function caller(){ return 'Alex'; }
function propertyLabel(r){ return r.a || ''; }
function ownerLabel(r){ return r.o || ''; }
function optPhones(){ return {}; }
function head(){ return ''; }
function render(){}
var QVIEW = 'untouched', lane = 'all', _NOTESBAD = false, _CBDUE = Object.create(null);
var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1};
var HISTCOV = {ledgers_ok:true, why:'', capped:false, total:10, shipped:10};
var ROWS = [];
var notes = {};
function saveNotes(){ __saves++; }
function queueSync(){ __syncs++; }
// ---- tiny fake DOM: innerHTML is scanned for ids and for the Complete buttons
var __els = {};
function mk(id){ var e = {id:id, style:{}, cls:{}, attrs:{}, onclick:null, oninput:null, value:'', disabled:false,
  classList:{add:function(c){e.cls[c]=1;}, remove:function(c){delete e.cls[c];}, toggle:function(c,on){ if(on) e.cls[c]=1; else delete e.cls[c]; }, contains:function(c){return !!e.cls[c];}},
  setAttribute:function(k,v){ e.attrs[k]=v; }, getAttribute:function(k){ return e.attrs[k]; }, innerHTML:''}; return e; }
var __app = mk('app'), __sheet = mk('sheet'); __els.app = __app; __els.sheet = __sheet;
var __chips = [], __whos = [];
function __paint(html){
  __chips = []; __whos = [];
  ['cmpwhobox','cmpnotebox','cmpnote','cmplog','cmpskip','regback'].forEach(function(i){ __els[i] = mk(i); });
  var re = /<button type="button" class="(cmpchip|cmpwho)([^"]*)" data-([kw])="([^"]+)"([^>]*)>/g, m;
  while((m = re.exec(html))){
    var e = mk(''); e.attrs['data-' + m[3]] = m[4]; e.disabled = /disabled/.test(m[5]); e.lockedClass = /lock/.test(m[2]); e.onHtml = / on/.test(m[2]);
    if(m[1] === 'cmpchip') __chips.push(e); else __whos.push(e);
  }
  ['cmpwhobox','cmpnotebox'].forEach(function(i){ __els[i].style.display = (new RegExp('id="' + i + '" style="display:([a-z]+)').exec(html) || [0,''])[1]; });
  __els.cmpnote.value = (/id="cmpnote"[^>]*value="([^"]*)"/.exec(html) || [0,''])[1];
}
function $(id){ return __els[id] || null; }
var document = {
  getElementById: function(id){ return __els[id] || null; },
  querySelectorAll: function(sel){ return sel === '.cmpchip' ? __chips : sel === '.cmpwho' ? __whos : []; }
};
Object.defineProperty(__app, 'innerHTML', {set:function(h){ __app._h = h; __paint(h); }, get:function(){ return __app._h || ''; }});
var PENDING_HOLD_H = 6, COOL_DEFAULT_H = 72;
""" + FNS + CMP + r"""
function chip(k){ return __chips.filter(function(c){ return c.attrs['data-k'] === k; })[0]; }
function who(w){ return __whos.filter(function(c){ return c.attrs['data-w'] === w; })[0]; }
function open(r, after){ __after = 0; completeThen(r, function(){ __after++; }); }
var __after = 0, out = {};
var T = today();
function base(extra){ var o = {status:'', note:''}; for(var k in (extra||{})) o[k] = extra[k]; return o; }
function snap(c){ var n = JSON.parse(JSON.stringify(notes[c] || {})); return n; }
function strip(n){ var o = JSON.parse(JSON.stringify(n)); delete o.touches; return o; }

// 1. sheet appears, nothing written yet
var R1 = {c:'C1', a:'1 Test St', o:'A OWNER', p:['3055550101'], r:['ok']};
notes.C1 = base({dials:[], cooldownH:24});
open(R1);
out.painted = {screen:SCREEN, title:/What did you do on this lead\?/.test(__app.innerHTML), chips:__chips.length, after:__after,
  skipBtn:/Skip, log nothing/.test(__app.innerHTML), saves:__saves, whoHidden:__els.cmpwhobox.style.display,
  logLabel:/Next lead/.test(__app.innerHTML) || true};
out.labels = __chips.map(function(c){ return c.attrs['data-k']; });

// 2. each chip, one at a time, writes exactly one manual touch and one save
var per = {};
['call','text','email','vm','letter','door','other'].forEach(function(k){
  var c = 'P_' + k; notes[c] = base(); var r = {c:c, a:'x', o:'y', p:['3055550102']};
  __saves = 0; __after = 0; open(r); chip(k).onclick(); if(k === 'other'){ __els.cmpnote.value = 'left a flyer'; __els.cmpnote.oninput(); }
  __els.cmplog.onclick();
  var t = (notes[c].touches || []);
  per[k] = {n:t.length, ch:t[0] && t[0].ch, src:t[0] && t[0].src, mk:t[0] && t[0].mk, by:t[0] && t[0].by, out:t[0] && t[0].out,
    d:t[0] && t[0].d, tsu:typeof (t[0] && t[0].tsu), saves:__saves, after:__after, dials:(notes[c].dials || []).length, status:notes[c].status};
});
out.per = per;

// 3. several at once: one save, three touches
notes.M = base(); __saves = 0; __after = 0; open({c:'M', a:'x', o:'y', p:['3055550103']});
chip('call').onclick(); chip('email').onclick(); chip('door').onclick(); __els.cmplog.onclick();
out.multi = {n:notes.M.touches.length, chs:notes.M.touches.map(function(t){return t.ch;}), saves:__saves, after:__after,
  label:null};

// 4. pre-ticked and locked: a call touch, a dial outcome, a text touch and an email touch from today
notes.L = base({touches:[{d:T, ts:'x', tsu:Date.now(), ch:'call', out:'Talked', by:'Alex'},
                         {d:T, ts:'x', tsu:Date.now(), ch:'text', out:'Text sent - x', by:'Alex'},
                         {d:T, ts:'x', tsu:Date.now(), ch:'email', out:'Email sent - x', by:'Alex'}],
                dials:[{d:T, tsu:Date.now(), ph4:'0101', oc:'voicemail', by:'Alex'}]});
__saves = 0; __after = 0; open({c:'L', a:'x', o:'y', p:['3055550101']});
var lk = {}; ['call','text','email','vm','letter','door'].forEach(function(k){ lk[k] = chip(k).disabled && chip(k).onHtml; });
out.locked = lk;
chip('call').onclick(); chip('text').onclick(); chip('email').onclick(); chip('vm').onclick();   // locked: no effect
chip('letter').onclick();
var before = notes.L.touches.length;
__els.cmplog.onclick();
out.lockedLog = {added:notes.L.touches.length - before, ch:notes.L.touches[notes.L.touches.length-1].ch, saves:__saves, after:__after};
// the same lead again: the letter is now locked too, nothing doubled
open({c:'L', a:'x', o:'y', p:['3055550101']});
out.relocked = chip('letter').disabled;
__saves = 0; __els.cmplog.onclick();
out.relockedLog = {n:notes.L.touches.length, saves:__saves};
// a pending dial alone does not lock Called
notes.PD = base({dials:[{d:T, tsu:Date.now(), ph4:'0101', oc:'pending', by:'Alex'}]});
open({c:'PD', a:'x', o:'y', p:['3055550101']}); out.pendingNotLocked = !chip('call').disabled;

// 5. skip logs nothing; empty Log logs nothing
notes.S = base(); __saves = 0; __after = 0; open({c:'S', a:'x', o:'y', p:['3055550101']}); chip('call').onclick(); __els.cmpskip.onclick();
out.skip = {touches:(notes.S.touches||[]).length, saves:__saves, after:__after};
notes.E = base(); __saves = 0; __after = 0; var __t0 = __toasts.length; open({c:'E', a:'x', o:'y', p:['3055550101']}); __els.cmplog.onclick();
out.empty = {touches:(notes.E.touches||[]).length, saves:__saves, after:__after, toasts:__toasts.length - __t0};

// 6. spoke to someone else
notes.W = base({wpRelatives:undefined}); delete notes.W.wpRelatives;
var wbefore = JSON.stringify(strip(notes.W)); __saves = 0;
var RW = {c:'W', a:'x', o:'y', p:['3055550101']};
open(RW); chip('spoke').onclick();
out.spokeBoxes = {who:__els.cmpwhobox.style.display, note:__els.cmpnotebox.style.display};
who('son').onclick(); __els.cmpnote.value = 'says call back Friday 3055559999'; __els.cmpnote.oninput(); __els.cmplog.onclick();
var sw = notes.W.touches[0];
out.spoke = {n:notes.W.touches.length, ch:sw.ch, who:sw.who, src:sw.src, mk:sw.mk, out:sw.out, saves:__saves,
  noteKeysSame:JSON.stringify(strip(notes.W)) === wbefore, keys:Object.keys(notes.W).sort(),
  view:_viewOf(RW), tele:_tele24n(notes.W)};

// 7. hard-suppressed lead: no sheet, no log, straight on
notes.H = base({status:'DO NOT CONTACT'}); __saves = 0; __after = 0; SCREEN = 'lead';
open({c:'H', a:'x', o:'y', p:['3055550101']});
out.hard = {screen:SCREEN, after:__after, saves:__saves, touches:(notes.H.touches||[]).length};

// 8. Reached out, call log, FTSA
var tv = {};
['call','text','email','letter','door'].forEach(function(k){
  var c = 'V_' + k; notes[c] = base(); var r = {c:c, a:'x', o:'y', p:['3055550101']};
  tv[k] = {before:_viewOf(r)}; open(r); chip(k).onclick(); __els.cmplog.onclick(); tv[k].after = _viewOf(r);
});
out.view = tv;
notes.RG = base(); ROWS = [{c:'RG', o:'Reg Owner', a:'9 Log Ave'}]; open({c:'RG', a:'x', o:'y', p:['3055550101']});
chip('email').onclick(); __els.cmplog.onclick();
notes.RS = base(); ROWS.push({c:'RS', o:'Son Owner', a:'8 Log Ave'}); open({c:'RS', a:'x', o:'y', p:['3055550101']});
chip('spoke').onclick(); who('daughter').onclick(); __els.cmplog.onclick();
screenRegistry();
out.reg = {has:/Reg Owner/.test(__app.innerHTML) && /Emailed \(logged by hand\)/.test(__app.innerHTML),
  spoke:/Son Owner/.test(__app.innerHTML) && /Spoke to daughter/.test(__app.innerHTML)};
notes.F = base(); open({c:'F', a:'x', o:'y', p:['3055550101']}); chip('call').onclick(); chip('text').onclick(); __els.cmplog.onclick();
out.ftsa = _tele24n(notes.F);
notes.F2 = base(); open({c:'F2', a:'x', o:'y', p:['3055550101']}); chip('email').onclick(); chip('letter').onclick(); __els.cmplog.onclick();
out.ftsaEmail = _tele24n(notes.F2);

out.fetched = __fetch.length;
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se[:600])
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}

p = res.get('painted', {})
rec('the sheet paints on Next with the question, 8 chips and a Skip button, and has not advanced yet',
    p.get('screen') == 'complete' and p.get('title') and p.get('chips') == 8 and p.get('skipBtn') and p.get('after') == 0, p)
rec('chips are Called / Texted / Emailed / voicemail / letter / door / spoke / other',
    res.get('labels') == ['call', 'text', 'email', 'vm', 'letter', 'door', 'spoke', 'other'], res.get('labels'))
rec('the who? row and note stay hidden until "Spoke to someone else" is ticked', p.get('whoHidden') == 'none', p)

per = res.get('per', {})
want = {'call': 'call', 'text': 'text', 'email': 'email', 'vm': 'call', 'letter': 'letter', 'door': 'door', 'other': 'other'}
for k, ch in want.items():
    x = per.get(k, {})
    rec('chip %s writes exactly one manual %s touch, one save, then advances' % (k, ch),
        x.get('n') == 1 and x.get('ch') == ch and x.get('src') == 'manual' and x.get('mk') == k and x.get('by') == 'Alex'
        and x.get('tsu') == 'number' and x.get('saves') == 1 and x.get('after') == 1 and 'logged by hand' in (x.get('out') or ''), x)
    rec('chip %s adds no dial and sets no status' % k, x.get('dials') == 0 and x.get('status') == '', x)
rec('the voicemail chip reads "Left a voicemail"', 'Left a voicemail' in (per.get('vm', {}).get('out') or ''), per.get('vm'))
rec('Other carries its note', 'left a flyer' in (per.get('other', {}).get('out') or ''), per.get('other'))

m = res.get('multi', {})
rec('several chips at once: three touches, ONE save, one advance',
    m.get('n') == 3 and sorted(m.get('chs', [])) == ['call', 'door', 'email'] and m.get('saves') == 1 and m.get('after') == 1, m)

lk = res.get('locked', {})
rec('already-logged call / text / email / voicemail are ticked and locked; letter and door are free',
    lk.get('call') and lk.get('text') and lk.get('email') and lk.get('vm') and not lk.get('letter') and not lk.get('door'), lk)
rec('tapping locked chips and Log writes only the new letter, once',
    res.get('lockedLog') == {'added': 1, 'ch': 'letter', 'saves': 1, 'after': 1}, res.get('lockedLog'))
rec('reopening: the letter is now locked and logging again adds nothing, no save',
    res.get('relocked') is True and res.get('relockedLog', {}).get('saves') == 0 and res.get('relockedLog', {}).get('n') == 4,
    [res.get('relocked'), res.get('relockedLog')])
rec('a pending (tapped, never tagged) dial does not lock Called', res.get('pendingNotLocked') is True)

rec('Skip, log nothing: no touch, no save, advances',
    res.get('skip') == {'touches': 0, 'saves': 0, 'after': 1}, res.get('skip'))
e = res.get('empty', {})
rec('nothing ticked then Next lead: no touch, no save, no toast, advances',
    e.get('touches') == 0 and e.get('saves') == 0 and e.get('after') == 1 and e.get('toasts') == 0, e)

sp = res.get('spoke', {})
rec('Spoke to someone else: ch other, who stored, note in the text, src manual',
    sp.get('n') == 1 and sp.get('ch') == 'other' and sp.get('who') == 'son' and sp.get('src') == 'manual'
    and 'Spoke to son' in (sp.get('out') or '') and sp.get('saves') == 1, sp)
rec('the who? row and note box open for it', res.get('spokeBoxes') == {'who': 'block', 'note': 'block'}, res.get('spokeBoxes'))
rec('it touches nothing on the note but touches: no phone, no wpRelatives, no status, no dial',
    sp.get('noteKeysSame') is True and 'wpRelatives' not in sp.get('keys', []) and 'dials' not in sp.get('keys', [])
    and 'wpe' not in sp.get('keys', []), sp)
rec('it does not move the lead out of Untouched and does not count toward the FTSA cap',
    sp.get('view') == 'untouched' and sp.get('tele') == 0, sp)

h = res.get('hard', {})
rec('a do-not-contact lead is not asked anything: no sheet, no log, advances',
    h.get('screen') == 'lead' and h.get('after') == 1 and h.get('saves') == 0 and h.get('touches') == 0, h)

tv = res.get('view', {})
for k in ('call', 'text', 'email'):
    rec('manual %s moves the lead from Untouched to Reached out' % k,
        tv.get(k, {}).get('before') == 'untouched' and tv.get(k, {}).get('after') == 'retries', tv.get(k))
for k in ('letter', 'door'):
    rec('manual %s also leaves Untouched (the page already reads these channels)' % k,
        tv.get(k, {}).get('before') == 'untouched' and tv.get(k, {}).get('after') == 'retries', tv.get(k))
rg = res.get('reg', {})
rec('the call log lists a hand-logged email and a hand-logged "spoke to" note', rg.get('has') and rg.get('spoke'), rg)
rec('manual call + text count toward the FTSA 24h cap (safe direction)', (res.get('ftsa') or 0) >= 1, res.get('ftsa'))
rec('manual email / letter do not count toward the telephonic cap', res.get('ftsaEmail') == 0, res.get('ftsaEmail'))
rec('nothing was fetched (no /text, no /notes, no bridge call) by any of it', res.get('fetched') == 0, res.get('fetched'))

print('\n%d failed' % len(fails))
sys.exit(1 if fails else 0)
