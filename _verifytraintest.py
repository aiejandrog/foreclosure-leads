"""_verifytraintest.py - end-to-end proof for the 2026-10-30 merge train (equity gate, after-the-yes,
_merge_note, board stage counts + Overdue order). Own invented fixtures, temp DEALFLOW_DIR, no network.
Run: DEALFLOW_DIR=<temp> python _verifytraintest.py"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='verifytrain_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


NODE = os.environ.get('NODE_BIN', 'node')
tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')


def fn(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n}\n', a) + 3]


def one(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n', a)]


def node(js):
    path = os.path.join(_T, 'run.js')
    io.open(path, 'w', encoding='utf-8').write(js)
    p = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    if p.returncode:
        return None, p.stderr[-400:]
    return json.loads(p.stdout), ''


GUESS = {'state': 'none', 'label': 'GUESS - never searched', 'fact': False, 'judgment': None,
         'judgment_source': 'not posted', 'liens': [], 'basis': 400000.0, 'basis_source': 'county',
         'net': 123456.0, 'may_quote': False, 'why': 'x'}
FACT = {'state': 'clear', 'label': 'VERIFIED CLEAR', 'fact': True, 'judgment': 100000.0, 'judgment_source': 'docket',
        'liens': [], 'basis': 400000.0, 'basis_source': 'county', 'net': 300000.0, 'may_quote': True, 'why': 'x'}


def lead(case, eqc):
    return {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/20/2026', 'days': 20, 'tier': 'B',
            'score': 50, 'value': 400000, 'phones': ['3055550101'], 'phtype': ['mobile'], 'phsrc': ['st'],
            'phdnc': [False], 'phrank': ['ok'], 'phbest': 0, 'eqc': eqc}


rows = {k: call_mode.call_rows([lead(k, c)], None, None)[0][0] for k, c in (('G', GUESS), ('F', FACT))}

print('\nTASK 2: equity gate on a GUESS lead (call_rows -> built page -> node)')
JS = ("var notes={}; function $(i){return null;}\n" + html[html.index('var AY_STAGES'):html.index('\n', html.index('var AY_STAGES'))] + '\n'
      + fn('esc') + one('eqFact') + '\n' + one('eqMay') + '\n' + one('_eqUsd') + '\n' + fn('eqCardHtml')
      + ''.join(fn(n) for n in ('ayIsStage', 'ayDateOk', 'ayTomorrow', 'ayApply', 'ayBookedUnverified', 'ayNeedsDate', 'ayPanelHtml')))
body = JS + """
var R=%s, T='2026-10-30', N=1793400000000, out={};
['G','F'].forEach(function(k){
  var r=R[k], fact=!!eqFact(r), o={fact:fact, card:eqCardHtml(r)};
  ['Offer made','Under contract'].forEach(function(st){
    var n={status:'Appointment'}; o[st]=ayApply(n,{stage:st,date:'2026-11-05',what:'x',eqOk:true,eqSource:'gut feel',fact:fact},T,N); o[st+'_status']=n.status;
  });
  var a={status:''}; if(!fact) ayBookedUnverified(a,T,N); o.appt=a;
  out[k]=o;
});
console.log(JSON.stringify(out));
""" % json.dumps(rows)
o, e = node(body)
rec('node ran', o is not None, e)
o = o or {'G': {}, 'F': {}}
g, f = o['G'], o['F']
W = 'Equity not verified -- do not quote a number'
rec('GUESS: eqFact false', g.get('fact') is False)
rec('GUESS: warning words shown', W in g.get('card', ''), g.get('card'))
rec('GUESS: no Net equity line and the fixture net (123,456) never prints',
    'Net equity' not in g.get('card', '') and '123,456' not in g.get('card', ''), g.get('card'))
rec('GUESS: Offer made refused (box ticked + source typed does not unlock it)',
    'not verified' in (g.get('Offer made') or '') and g.get('Offer made_status') == 'Appointment', g)
rec('GUESS: Under contract refused', 'not verified' in (g.get('Under contract') or ''), g)
a = g.get('appt', {})
rec('GUESS: Appointment forces next step "Verify equity" dated tomorrow',
    a.get('status') == 'Appointment' and a.get('nextStep') == {'date': '2026-10-31', 'what': 'Verify equity'}
    and a.get('apptUnverified') == '2026-10-30', a)
rec('control FACT: Offer made allowed, net shown', f.get('Offer made') == '' and 'Net equity' in f.get('card', ''), f)

print('\nTASK 3: after-the-yes panel save')
body = JS + """
var T='2026-10-30', N=1793400000000, out={};
var n1={status:'Appointment'}; out.nodate=[ayApply(n1,{stage:'Appointment',date:'',fact:true},T,N), Object.keys(n1)];
var n2={status:'Appointment'}; out.past=ayApply(n2,{stage:'Appointment',date:'2026-10-29',fact:true},T,N);
var n3={status:'Appointment'}; out.err=ayApply(n3,{stage:'Offer made',date:'2026-11-04',what:'send terms',eqOk:true,eqSource:'title search',keepOrSell:'sell',fact:true},T,N); out.n=n3;
console.log(JSON.stringify(out));
"""
o, e = node(body)
rec('node ran', o is not None, e)
o = o or {}
rec('no date refused and nothing written', 'date is required' in o.get('nodate', ['', 0])[0] and o['nodate'][1] == ['status'], o.get('nodate'))
rec('past date refused', 'past' in o.get('past', ''), o.get('past'))
n = o.get('n', {})
rec('valid save payload', o.get('err') == '' and n.get('status') == 'Offer made' and n.get('stageAt') == {'Offer made': '2026-10-30'}
    and n.get('nextStep') == {'date': '2026-11-04', 'what': 'send terms'}
    and n.get('equityVerified') == {'ok': True, 'source': 'title search', 'd': '2026-10-30'}
    and n.get('keepOrSell') == 'sell' and n.get('afterYesAt') == 1793400000000, n)
for k in ('status', 'stageAt', 'nextStep', 'equityVerified', 'keepOrSell', 'afterYesAt'):
    rec('payload field present: ' + k, k in n)

print('\nTASK 3b: send_server._merge_note')
import send_server as S  # noqa: E402
M = S._merge_note
old = {'status': 'Appointment', 'afterYesAt': 100, 'nextStep': {'date': '2026-11-01', 'what': 'a'}, 'next': '2026-11-01'}
new = {'status': 'Offer made', 'afterYesAt': 200, 'nextStep': {'date': '2026-11-09', 'what': 'b'}, 'next': '2026-11-09'}
out, _ = M(old, new)
rec('newer save wins', out['status'] == 'Offer made' and out['nextStep']['what'] == 'b' and out['afterYesAt'] == 200, out)
out, _ = M(new, old)
rec('older push does not win', out['status'] == 'Offer made' and out['nextStep']['what'] == 'b', out)
for hard in ('DO NOT CONTACT', 'Dead'):
    out, _ = M({'status': hard, 'afterYesAt': 1}, new)
    rec('stored %s not overridden, no next step' % hard, out['status'] == hard and 'nextStep' not in out, out)
    out, _ = M(old, {'status': hard, 'afterYesAt': 0})
    rec('incoming %s wins' % hard, out['status'] == hard, out)

print('\nTASK 4: board _funnelStats + Overdue order (functions extracted from tracker_template.html)')
S4 = tpl


def tfn(name):
    i = S4.index('function %s(' % name)
    j = S4.index('{', i)
    d = 0
    for k in range(j, len(S4)):
        if S4[k] == '{':
            d += 1
        elif S4[k] == '}':
            d -= 1
            if d == 0:
                return S4[i:k + 1]


def tline(p):
    i = S4.index(p)
    return S4[i:S4.index('\n', i)]


blk = '\n'.join([tline('const esc = s =>'), tfn('_today'), tfn('_effOptout'), tfn('_isWrongOwner'),
                 S4[S4.index('const _AY_OPEN'):S4.index('function _nowTS()')]])
NOTES = {
    'V-1': {'status': 'Appointment', 'stageAt': {'Appointment': '2026-10-20'}, 'nextStep': {'date': '2026-10-27', 'what': 'x'}},
    'V-2': {'status': 'Offer made', 'stageAt': {'Appointment': '2026-10-01', 'Offer made': '2026-10-05'},
            'nextStep': {'date': '2026-10-10', 'what': 'y'}},
    'V-3': {'status': 'Under contract', 'stageAt': {'Under contract': '2026-10-28'}, 'nextStep': {'date': '2026-11-15', 'what': 'z'}},
    'V-4': {'status': 'CLOSED', 'closedAmt': 7000, 'stageAt': {'Appointment': '2026-09-01'}},
    'V-5': {'status': 'Called - talked', 'next': '2026-10-20'},
}
body = blk + "\nvar notes=%s;\nconsole.log(JSON.stringify({s:_funnelStats('2026-10-29'),o:_overdueFollowups(notes,'2026-10-29')}));" % json.dumps(NOTES)
o, e = node(body)
rec('node ran', o is not None, e)
o = o or {'s': {}, 'o': []}
s = o['s']
rec('stage counts appt/offers/contracts/closes/revenue = 4/3/2/1/7000',
    (s.get('appointments'), s.get('offers'), s.get('contracts'), s.get('closes'), s.get('revenue')) == (4, 3, 2, 1, 7000), s)
rec('overdue sorted oldest first', [(x['c'], x['days']) for x in o['o']] == [('V-2', 19), ('V-5', 9), ('V-1', 2)], o['o'])

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
