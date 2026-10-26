"""Morning Worker call-only leads reach Call Mode, split between the two seats (2026-10-26).

Alex: "the leads that get sent into call only from morning worker gets added and sent to call mode
and gets distributed to me and Carlos." What carries them already existed: the worker's callq stamps
the synced note flag `.wq`, and each seat page's workerQ() reads it. This suite pins what was added:

  - replied_cases(): owners who wrote back, from replies.json (minus STOP), a `replied` note, or an
    email touch logged as a reply. No '#phone' / '@email' identity keys.
  - call_rows(replied=...) puts a replied owner's whole household on seat 0 (Alejandro) and marks the
    row rp=1. Everyone else keeps the household hash split, so one lead is on exactly one phone.
  - the page sorts replied first, then worker hand-offs (wq newer than any wqx tombstone), then the
    old never-contacted order, and tags the card.
  - a do-not-call-only lead the worker queued still never becomes a dial row.

Container-safe: no board data, the JS runs under node.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='workerroute_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds

import call_mode as CM  # noqa: E402

SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
FL = io.open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


print('\nREPLIED CASES')
rp = CM.replied_cases(
    notes={'A': {'replied': '2026-10-20'},
           'B': {'touches': [{'ch': 'email', 'out': 'THEY REPLIED'}]},
           'C': {'touches': [{'ch': 'email', 'out': 'sent'}]},
           'D': {'touches': [{'ch': 'text', 'out': 'replied'}]},
           '#3055550101': {'replied': '2026-10-20'}},
    replies={'E': {'when': '2026-10-21'}, 'F': {'stop': True}, '@x@y.com': {'when': '2026-10-21'}})
rec('note flag, email reply touch and replies.json are replies', {'A', 'B', 'E'} <= rp, rp)
rec('a plain send, a text touch, a STOP reply and identity keys are not', not ({'C', 'D', 'F', '#3055550101', '@x@y.com'} & rp), rp)
rec('missing inputs are an empty set', CM.replied_cases(None, None) == set())

print('\nSEAT PIN')
cs = ['2026-%06d-CA-01' % i for i in range(1, 80)]
odd = [c for c in cs if CM._seat_bucket(c) % 2 == 1]
a, b, c3 = odd[0], odd[1], odd[2]
rows = [{'c': a, 'sb': CM._seat_bucket(a), 'pk': 'P1'}, {'c': b, 'sb': CM._seat_bucket(b), 'pk': 'P1'},
        {'c': c3, 'sb': CM._seat_bucket(c3), 'pk': 'P3'}]
CM._seat_by_household(rows, pin={b})
rec('a replied case moves its whole household to seat 0', rows[0]['sb'] == rows[1]['sb'] == 0, rows)
rec('an unrelated lead keeps its own bucket', rows[2]['sb'] == CM._seat_bucket(c3), rows)
single = [{'c': c3, 'sb': CM._seat_bucket(c3)}]
CM._seat_by_household(single, pin={c3})
rec('a replied singleton goes to seat 0 too', single[0]['sb'] == 0)
rec('bucket 0 is Alejandro for every seat count', all(0 % n == 0 for n in (2, 3, 4, 6))
    and CM.CALL_SEATS[0][1] == 0 and CM.CALL_SEATS[0][2] == 'Alejandro')
plain = [{'c': c3, 'sb': CM._seat_bucket(c3)}]
CM._seat_by_household(plain)
rec('no pin, no change', plain[0]['sb'] == CM._seat_bucket(c3))


def lead(case, **kw):
    d = {'case': case, 'county': 'MIAMI-DADE', 'st': 'FC', 'auction': '11/20/2026', 'days': 29,
         'tier': 'B', 'score': 50, 'value': 400000, 'owners': 'TEST OWNER ' + case,
         'oname': 'Test Owner', 'addr': '%d NW 1 ST, MIAMI, FL 33101' % (100 + cs.index(case)),
         'pkey': 'K' + case, 'phones': ['30555%05d' % cs.index(case)], 'phdnc': [False], 'phsrc': ['st'],
         'phtype': ['mobile']}
    d.update(kw)
    return d


print('\nCALL_ROWS END TO END')
dnc = odd[3]
SLIM = [lead(c) for c in odd[:3]] + [lead(dnc, phdnc=[True])]
r0, t0 = CM.call_rows(SLIM, None, None)
r1, t1 = CM.call_rows(SLIM, None, None, replied={odd[1], dnc})
by = {r['c']: r for r in r1}
rec('same leads with and without the pin', sorted(x['c'] for x in r0) == sorted(by) and t0 == t1)
rec('replied lead: seat 0 and rp=1', by[odd[1]]['sb'] == 0 and by[odd[1]].get('rp') == 1, by.get(odd[1]))
rec('other leads: their own odd bucket (Carlos), no rp',
    all(by[c]['sb'] % 2 == 1 and 'rp' not in by[c] for c in (odd[0], odd[2])), r1)
rec('a do-not-call-only lead is never a dial row, replied or queued', dnc not in by)
seat0 = [r['c'] for r in CM.seat_rows(r1, 2, 0)]
seat1 = [r['c'] for r in CM.seat_rows(r1, 2, 1)]
rec('each lead on exactly one page', sorted(seat0 + seat1) == sorted(by) and not (set(seat0) & set(seat1)))
rec('the replied lead is on Alejandro\'s page', odd[1] in seat0 and odd[1] not in seat1)
lk = []
CM.call_rows([lead(odd[4], phones=[], phdnc=[], phsrc=[], phtype=[])], None, None, lookup=lk, replied={odd[4]})
rec('a replied no-number lead is looked up on Alejandro\'s seat', lk and lk[0]['sb'] == 0 and lk[0].get('rp') == 1, lk)
# reply logged on a sibling case of the same person (not itself a dial row): the dial row pins too
sib_dial, sib_lp = odd[5], odd[6]
lk2 = []
r2, _ = CM.call_rows([lead(sib_dial, pkey='PX'), lead(sib_lp, pkey='PX', phones=[], phdnc=[], phsrc=[], phtype=[])],
                     None, None, lookup=lk2, replied={sib_lp})
b2 = {r['c']: r for r in r2}
rec('a reply on a sibling case pins the person\'s dial row to seat 0',
    b2.get(sib_dial, {}).get('sb') == 0 and b2.get(sib_dial, {}).get('rp') == 1, r2)
cov = [{'c': sib_lp}]
CM.mark_lookup(cov, lk2, crew_sb={'PX': 7})
rec('mark_lookup keeps a replied lookup lead on seat 0 and carries rp', cov[0].get('sb') == 0 and cov[0].get('rp') == 1, cov)
rec('a promoted lookup keeps rp on the phone', "'ne','st','rp']" in SRC)
rec('a bad note cannot cost both phone pages', "_cm_rp = set()" in FL)

print('\nBUILD WIRING')
rec('the build reads worker_notes.json and the reply table into replied_cases',
    "call_mode.replied_cases(_cm_wn if isinstance(_cm_wn, dict) else {}, _replies)" in FL)
rec('the build passes replied= to the one shared call_rows()', 'replied=_cm_rp)' in FL)
rec('_replies is defined before Call Mode is built', 0 < FL.find('_replies, _rp_unresolved = {}, 0') < FL.find('replied=_cm_rp'))


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 and j > 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


print('\nPAGE ORDER AND TAG')
FNS = 'var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1}, _WQSET=null;\n' + ''.join(grab_fn(n) for n in (
    'lastCall', '_inbound', 'lastOutreach', '_filedMs', '_contactTier', 'workerQ', 'wqSet', 'mailHistCM',
    '_isReplied', '_wkTier', '_wkChips', '_freshFirst'))
HARNESS = FNS + r"""
var notes = {
  Q: {wq: '2026-10-26'},                                     // worker hand-off today
  T: {wq: '2026-10-25', wqx: '2026-10-26'},                  // retired by a call outcome: tombstone wins
  R: {touches: [{ch: 'email', out: 'THEY REPLIED'}]},        // owner wrote back
  K: {touches: [{ch: 'call', ts: '2026-10-25T10:00:00', out: 'noanswer'}], wq: '2026-10-26'}
};
var rows = [{c:'F'}, {c:'T'}, {c:'Q'}, {c:'K'}, {c:'P', rp:1}, {c:'R'}];
var out = {};
out.order = _freshFirst(rows, 'soon').map(function(r){ return r.c; }).join('');
_WQSET = null;
out.chipQ = _wkChips({c:'Q'}); out.chipR = _wkChips({c:'R'}); out.chipF = _wkChips({c:'F'}); out.chipT = _wkChips({c:'T'});
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('replied first (baked rp, then notes), worker hand-offs next, then fresh, retired worker lead not lifted',
    res.get('order') == 'PRQKFT', res)
rec('worker card says where it came from', 'from Morning Worker' in res.get('chipQ', ''), res)
rec('replied card says the owner replied', 'owner replied' in res.get('chipR', ''), res)
rec('an ordinary lead and a retired worker lead carry no tag', res.get('chipF') == '' and res.get('chipT') == '', res)
rec('the card renders the tag on both the normal and the balloon card', SRC.count('who += _wkChips(r)') == 2)
rec('pool() still filters before it orders', SRC.find('supReason(r, lane)') < SRC.find('var _ff = _freshFirst(keep, lane);'))
rec('the order never reads a hiding rule',
    '_wkTier' not in SRC[SRC.find('function supReason('):SRC.find('\n}\n', SRC.find('function supReason('))])

print('\nLIVE HAND-OFF BANNER (workerWatch)')
W = 'var _WQSET=null, _WK_SEEN="fcWkSeen:/call/", _WKT=null, SCREEN="lead", lane="soon", QVIEW="untouched", BLANE=null, i=0;\n' \
    + ''.join(grab_fn(n) for n in ('workerQ', 'wqSet', 'workerWatch'))
H2 = W + r'''
var store = {}; var localStorage = {getItem:function(k){ return store[k]||null; }, setItem:function(k,v){ store[k]=v; }};
var BAN = {innerHTML:'', offsetWidth:1, classList:{on:0, add:function(){ this.on=1; }, remove:function(){ this.on=0; }}};
var GO = {onclick:null}, X = {onclick:null};
function $(id){ return id==='wkban' ? BAN : (id==='wkx' ? X : GO); }
function _seatMine(r){ return r.c !== 'OTHER'; }
function supReason(r, k){ return {k: r.c === 'DNC' ? 'hard' : ''}; }
function _clmOwner(c){ return c === 'CLAIMED' ? 'Carlos' : ''; }
function render(){} function toast(){}
var ROWS = [{c:'A'}, {c:'B'}, {c:'OTHER'}, {c:'DNC'}, {c:'PLAIN'}, {c:'CLAIMED'}];
var notes = {A:{wq:'2026-10-26'}, B:{wq:'2026-10-26'}, OTHER:{wq:'2026-10-26'}, DNC:{wq:'2026-10-26'}, CLAIMED:{wq:'2026-10-26'}};
var out = {};
out.first = workerWatch(); out.on = BAN.classList.on; out.txt = BAN.innerHTML;
out.again = workerWatch();
notes.B.wq = '2026-10-27'; out.requeue = workerWatch();
GO.onclick(); out.lane = lane; out.qv = QVIEW;
console.log(JSON.stringify(out));
'''
rc, so, se = node(H2)
rec('workerWatch harness runs', rc == 0, se)
w = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
rec('announces only this phone\'s worker leads the Worker lane would list (no suppressed, no claimed)', w.get('first') == 2 and w.get('on') == 1, w)
rec('banner says how many arrived', '2 new from Morning Worker' in w.get('txt', ''), w)
rec('each hand-off announces once', w.get('again') == 0, w)
rec('a lead handed off again on a later day announces again', w.get('requeue') == 1, w)
rec('Open goes to the Worker lane with every view', w.get('lane') == 'worker' and w.get('qv') is None, w)
SRCT = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
_hb = SRCT[SRCT.find('if(d.extra && d.extra.hand){'):SRCT.find('_docLog(r.case, \'worker-callq\'')]
rec('a button hand-off only stamps .wq: no fcCallQueue (tomorrow\'s text batch), no per-lead log',
    '.wq = _today()' in _hb and 'fcCallQueue' not in _hb.split('return;')[0].split('*/')[-1] and 'return;' in _hb)
rec('the tick runs on a timer and after the return-to-page pull',
    "workerWatch(); }catch(e){ logErr(e, 'workerWatch'); } }, 20000)" in SRC and "try{ workerWatch(); }catch(e){}\n    if(touched)" in SRC)

print('\n%s (%d failed)' % ('ALL PASS' if not FAILS else 'FAILED', len(FAILS)))
sys.exit(1 if FAILS else 0)
