"""Call Mode queue views (call workflow spec section 3 / matrix Q01-Q10, 2026-10-08).

Untouched is the default across all lanes; Replies, Retries and History unknown are explicit
choices. The JS is extracted by name from call_mode.py and run under node, like _freshfirsttest.py.
A view only NARROWS the list the gates produced, and uncontacted rows with unverified history are
History unknown, never untouched.
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra else ''))
    if not ok:
        fails.append(name)


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


FNS = ('var OUTREACH_CH = {call:1, text:1, email:1, letter:1, door:1};\n'
       'function esc(s){return String(s==null?"":s);}\n'
       + ''.join(grab_fn(n) for n in ('lastCall', '_inbound', 'lastOutreach', '_replyOpen', '_contactTier',
                                      '_histWhy', '_viewOf', 'viewEmptyHtml')))
HARNESS = r"""
var store = {};
var localStorage = {getItem:function(k){return k in store ? store[k] : null;}, setItem:function(k,v){store[k]=String(v);}};
var _NOTESBAD = false, QVIEW = 'untouched', _CBDUE = Object.create(null);
var _VIEWN = {untouched:0, replies:0, retries:0, history_unknown:0};
var HISTCOV = {ledgers_ok:true, why:'', capped:false, total:10, shipped:10};
""" + FNS + r"""
var notes = {
  CALLED: {touches:[{ch:'call', ts:'2026-09-27T10:00:00', out:'noanswer'}]},
  EMAILED: {touches:[{ch:'email', ts:'2026-09-27T10:00:00'}]},
  REPLY:  {replied:'2026-10-07T10:00:00'},
  SIBCALL:{}, SIB2:{touches:[{ch:'call', ts:'2026-09-27T10:00:00', out:'noanswer'}]},
  PENDING:{dials:[{tsu:1759000000000, oc:'pending', by:'x'}]},
  INBOUND:{touches:[{ch:'text', ts:'2026-09-27T10:00:00', out:'THEY REPLIED (inbound)'}]}
};
var R = function(c, extra){ var o = {c:c}; for(var k in (extra||{})) o[k] = extra[k]; return o; };
var rows = {fresh:R('FRESH'), called:R('CALLED'), emailed:R('EMAILED'), reply:R('REPLY'),
  sib:R('SIBCALL', {pcs:['SIB2']}), ledger:R('LEDG', {le:1758000000000}), pending:R('PENDING'), inbound:R('INBOUND')};
function views(){ var o = {}; for(var k in rows) o[k] = _viewOf(rows[k]); return o; }
var out = {};
out.base = views();
HISTCOV.ledgers_ok = false; HISTCOV.why = 'text_sent.json not found on the build machine';
out.noledger = views(); out.noledgerWhy = _histWhy();
HISTCOV.ledgers_ok = true; HISTCOV.why = '';
_NOTESBAD = true; out.badnotes = _viewOf(rows.fresh); _NOTESBAD = false;
store.fcTeamKey = 'abcdefgh'; out.syncNoPull = _viewOf(rows.fresh);
var _stamp = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,19).replace('T',' ');
function _keyFp(k){ let h = 5381; k = String(k||''); for(let n = 0; n < k.length; n++) h = ((h * 33) ^ k.charCodeAt(n)) >>> 0; return h.toString(36); }
store.fcLastPull = _stamp; store.fcPullStat = JSON.stringify({ok:true, kf:_keyFp('abcdefgh'), devices:1, failed:0, ts:_stamp});
out.syncPulled = _viewOf(rows.fresh);
delete store.fcTeamKey;
HISTCOV = null; out.noHistcov = _viewOf(rows.fresh); HISTCOV = {ledgers_ok:true, why:'', capped:true, total:783, shipped:400};
_VIEWN = {untouched:0, replies:1, retries:8, history_unknown:2};
out.emptyUntouched = viewEmptyHtml();
QVIEW = 'retries'; out.emptyRetries = viewEmptyHtml(); QVIEW = 'untouched';
console.log(JSON.stringify(out));
"""
rc, so, se = node(HARNESS)
rec('harness runs', rc == 0, se)
res = json.loads(so.strip().splitlines()[-1]) if rc == 0 else {}
b = res.get('base', {})
rec('Q02 an untouched row with complete history is untouched', b.get('fresh') == 'untouched', b)
rec('a called row and an emailed row are retries', b.get('called') == 'retries' and b.get('emailed') == 'retries', b)
rec('Q04 a baked server-ledger send (r.le) is contacted, not untouched', b.get('ledger') == 'retries', b)
rec('a pending (abandoned) dial leaves Untouched and never returns to it', b.get('pending') == 'retries', b)
rec('Q07 an unanswered inbound reply goes to Replies', b.get('reply') == 'replies', b)
rec('an inbound-only touch is not our outreach: still untouched', b.get('inbound') == 'untouched', b)
rec('a sibling case that was called makes the row a retry (conservative)', b.get('sib') == 'retries', b)
n = res.get('noledger', {})
rec('Q03 unreadable server ledger: uncontacted rows are History unknown, no verified zero',
    n.get('fresh') == 'history_unknown' and n.get('inbound') == 'history_unknown', n)
rec('contacted rows stay contacted when history is incomplete', n.get('called') == 'retries' and n.get('reply') == 'replies', n)
rec('the reason is surfaced', 'text_sent.json' in res.get('noledgerWhy', ''), res.get('noledgerWhy'))
rec('unparseable local notes -> History unknown', res.get('badnotes') == 'history_unknown', res)
rec('team sync on with no pull yet -> History unknown', res.get('syncNoPull') == 'history_unknown', res)
rec('team sync on and pulled -> untouched', res.get('syncPulled') == 'untouched', res)
rec('no coverage manifest at all -> History unknown', res.get('noHistcov') == 'history_unknown', res)
e = res.get('emptyUntouched', '')
rec('Q02 empty Untouched says so and counts what waits elsewhere, without opening it',
    'No untouched leads in the checked inventory' in e and '8 retr' in e and '1 reply' in e and '2 with history unknown' in e, e)
rec('Q09 a capped build says the inventory is partial', 'Partial inventory' in e and '400 of 783' in e, e)
rec('empty Retries does not fall through to anything', 'No retries due' in res.get('emptyRetries', ''), res.get('emptyRetries'))

# ---- structure: the view narrows AFTER the gates, defaults are untouched/all, no old restore ----
pool = SRC[SRC.find('function pool()'):SRC.find('\n}\n', SRC.find('function pool()'))]
rec('view filter runs after supReason, seat and claim filters, before ordering',
    pool.find('supReason(r, lane)') < pool.find('_clmOwner(r.c)') < pool.find('if(QVIEW) keep = keep.filter') < pool.find('_freshFirst(keep, lane)'))
rec('the view filter only removes rows (filter, no push into keep)', pool.count('keep.push') == 0)
st = SRC[SRC.find('function start()'):SRC.find('\n}\n', SRC.find('function start()'))]
rec('Q10 start() opens on Untouched across all lanes', "QVIEW = 'untouched'; lane = 'all';" in st)
rec('Q10 a position saved without a view (the old mixed queue) is never restored', 'if(s.v !== QVIEW) return false;' in SRC)
allk = SRC[SRC.find("{k:'all',"):SRC.find("hide0:false}\n];", SRC.find("{k:'all',"))]
rec('the all-lanes entry has no channel, so no email-lane exemption', "ch:'" not in allk, allk)
rec('a lead opened on purpose is a one-off: the first move off it restores the view',
    all(('function %s(){\n  _qvRestore();' % f) in SRC or ('function %s(workedC, nextC){\n  var _wl = lane, _rest = _qvRestore();' % f) in SRC
        for f in ('advance', 'navBack', 'navNext')) and '_QVBACK = {v:_qvWas' in SRC)
rec('supReason/suppressed/hardSuppressed do not read the view',
    not any(t in SRC[SRC.find('function supReason('):SRC.find('function suppressed(')] for t in ('QVIEW', '_viewOf', 'HISTCOV')))

# ---- python half: build-time coverage ----
sys.path.insert(0, HERE)
import call_mode, shutil
orig_here = call_mode.HERE
tmp = tempfile.mkdtemp()
try:
    call_mode.HERE = tmp
    c = json.loads(call_mode.history_coverage(10, 10))
    rec('no ledgers on the build machine -> ledgers_ok false with a reason', c['ledgers_ok'] is False and c['why'], c)
    json.dump([], open(os.path.join(tmp, 'mail_sent.json'), 'w'))
    c = json.loads(call_mode.history_coverage(10, 10))
    rec('one ledger missing is still not ok', c['ledgers_ok'] is False, c)
    json.dump([], open(os.path.join(tmp, 'text_sent.json'), 'w'))
    c = json.loads(call_mode.history_coverage(783, 400))
    rec('both readable -> ok; total above shipped -> capped', c['ledgers_ok'] is True and c['capped'] is True, c)
    open(os.path.join(tmp, 'text_sent.json'), 'w').write('{torn')
    c = json.loads(call_mode.history_coverage(5, 5))
    rec('a torn ledger is not ok', c['ledgers_ok'] is False and 'unreadable' in c['why'], c)
finally:
    call_mode.HERE = orig_here
    shutil.rmtree(tmp, ignore_errors=True)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
