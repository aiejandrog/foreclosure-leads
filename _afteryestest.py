"""After the yes + the appointment equity gate + the /notes merge (2026-10-29).

Part 1, Call Mode JS (extracted from the built page, run under node):
  - ayApply: a stage past Appointment needs a REAL, non-past next-step date (CLOSED excepted); an
    equity-verified tick needs a source; Offer made / Under contract / Assigned / CLOSED are REFUSED
    unless the baked equity is a fact (ticking the box by hand does not unlock them); DO NOT CONTACT /
    Dead leads take nothing; keep-or-sell is stored only as keep / sell / undecided
  - ayBookedUnverified: Appointment on unverified equity still saves, forces next step "Verify equity"
    tomorrow, marks note.apptUnverified; never walks a later stage back
  - the panel renders, shows "Booked on unverified equity", and ayNeedsDate blocks leaving an open deal
  - logOutcome / afterCall are wired to those functions
Part 2 (the /notes merge) lives in _mergenotetest.py on claude/agents-mergenote (LEGAL REVIEW, D1).

Invented data only. Run: DEALFLOW_DIR=<temp> python _afteryestest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='afteryes_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


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


JS = ("var notes = {}; function $(i){return null;}\n"
      + html[html.index('var AY_STAGES'):html.index('\n', html.index('var AY_STAGES'))] + '\n'
      + fn('esc') + one('eqFact') + '\n'
      + ''.join(fn(n) for n in ('ayIsStage', 'ayDateOk', 'ayTomorrow', 'ayApply', 'ayBookedUnverified', 'ayNeedsDate',
                                'ayPanelHtml')))
TODAY, TOM = '2026-10-30', '2026-10-31'
NOW = 1793400000000   # 2026-10-30 local-ish


def run(body):
    path = os.path.join(_T, 't.js')
    io.open(path, 'w', encoding='utf-8').write(JS + '\n' + body)
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
    if p.returncode:
        return None, p.stderr[-300:]
    return json.loads(p.stdout), ''


print('\nSTAGES')
o, e = run('console.log(JSON.stringify(AY_STAGES));')
rec('stage list is the contract', o == ['Appointment', 'Offer made', 'Under contract', 'Assigned', 'CLOSED'], (o, e))

print('\nayApply')
body = """
var T='%s', N=%d, out={};
function t(k, n, f){ var err = ayApply(n, f, T, N); out[k] = {err: err, n: n}; }
t('okfact', {status:'Appointment'}, {stage:'Offer made', date:'2026-11-02', what:'send terms', eqOk:true, eqSource:'title search', keepOrSell:'sell', fact:true});
t('nodate', {status:'Appointment'}, {stage:'Appointment', date:'', fact:true});
t('baddate', {status:'Appointment'}, {stage:'Appointment', date:'2026-02-31', fact:true});
t('past', {status:'Appointment'}, {stage:'Appointment', date:'2026-10-01', fact:true});
t('nosource', {status:'Appointment'}, {stage:'Appointment', date:'2026-11-02', eqOk:true, eqSource:' ', fact:true});
t('offer_unverified', {status:'Appointment'}, {stage:'Offer made', date:'2026-11-02', eqOk:true, eqSource:'my gut', fact:false});
t('contract_unverified', {status:'Appointment'}, {stage:'Under contract', date:'2026-11-02', fact:false});
t('stay_appt_unverified', {status:'Appointment'}, {stage:'Appointment', date:'2026-11-02', fact:false});
t('already_offer', {status:'Offer made'}, {stage:'Offer made', date:'2026-11-02', fact:false});
t('dnc', {status:'DO NOT CONTACT'}, {stage:'Appointment', date:'2026-11-02', fact:true});
t('dead', {status:'Dead'}, {stage:'Appointment', date:'2026-11-02', fact:true});
t('closed_nodate', {status:'Assigned', nextStep:{date:'2026-11-02', what:'x'}}, {stage:'CLOSED', date:'', fact:true});
t('ks_junk', {status:'Appointment'}, {stage:'Appointment', date:'2026-11-02', keepOrSell:'rent', fact:true});
t('clears_flag', {status:'Appointment', apptUnverified:'2026-10-29'}, {stage:'Appointment', date:'2026-11-02', fact:true});
console.log(JSON.stringify(out));
""" % (TODAY, NOW)
o, e = run(body)
rec('node ran', o is not None, e)
o = o or {}
g = lambda k: o.get(k, {})
rec('good save writes the whole unit', g('okfact').get('err') == '' and g('okfact')['n'].get('status') == 'Offer made'
    and g('okfact')['n']['nextStep'] == {'date': '2026-11-02', 'what': 'send terms'}
    and g('okfact')['n']['next'] == '2026-11-02'
    and g('okfact')['n']['equityVerified'] == {'ok': True, 'source': 'title search', 'd': TODAY}
    and g('okfact')['n']['keepOrSell'] == 'sell' and g('okfact')['n']['afterYesAt'] == NOW
    and g('okfact')['n']['stageAt'] == {'Offer made': TODAY}, g('okfact'))
rec('no date refused, nothing written', 'date is required' in g('nodate').get('err', '') and 'nextStep' not in g('nodate')['n'], g('nodate'))
rec('impossible date refused', 'real date' in g('baddate').get('err', ''), g('baddate'))
rec('past date refused', 'past' in g('past').get('err', ''), g('past'))
rec('verified tick without a source refused', 'source' in g('nosource').get('err', ''), g('nosource'))
rec('Offer made refused on unverified equity, even with the box ticked',
    'not verified' in g('offer_unverified').get('err', '') and g('offer_unverified')['n']['status'] == 'Appointment', g('offer_unverified'))
rec('Under contract refused on unverified equity', 'not verified' in g('contract_unverified').get('err', ''))
rec('staying at Appointment is allowed without a fact', g('stay_appt_unverified').get('err') == '')
rec('a lead already at Offer made can still be re-dated', g('already_offer').get('err') == '')
rec('DO NOT CONTACT takes nothing', 'closed out' in g('dnc').get('err', '') and 'nextStep' not in g('dnc')['n'])
rec('Dead takes nothing', 'closed out' in g('dead').get('err', ''))
rec('CLOSED needs no date and drops the open next step', g('closed_nodate').get('err') == ''
    and 'nextStep' not in g('closed_nodate')['n'] and g('closed_nodate')['n']['status'] == 'CLOSED')
rec('keep-or-sell junk is not stored', 'keepOrSell' not in g('ks_junk')['n'])
rec('a verified save clears the unverified-booking flag', 'apptUnverified' not in g('clears_flag')['n'])

print('\nBOOKED ON UNVERIFIED EQUITY')
body = """
var out = {};
var a = {status:''}; ayBookedUnverified(a, '%s', %d); out.a = a;
var b = {status:'Offer made', nextStep:{date:'2026-12-01', what:'x'}}; ayBookedUnverified(b, '%s', %d); out.b = b;
out.tom = ayTomorrow(new Date(2026, 11, 31, 12).getTime());
console.log(JSON.stringify(out));
""" % (TODAY, NOW, TODAY, NOW)
o, e = run(body)
o = o or {}
a = o.get('a', {})
rec('saves as Appointment with next step Verify equity tomorrow',
    a.get('status') == 'Appointment' and a.get('nextStep', {}).get('what') == 'Verify equity'
    and a.get('nextStep', {}).get('date') == a.get('next') and a['next'] > TODAY and a.get('apptUnverified') == TODAY
    and a.get('equityVerified', {}).get('ok') is False and a.get('afterYesAt') == NOW, (a, e))
rec('never walks Offer made back', o.get('b', {}).get('status') == 'Offer made', o.get('b'))
rec('tomorrow crosses a year end', o.get('tom') == '2027-01-01', o.get('tom'))

print('\nTHE PANEL')
body = """
var out = {};
notes['U'] = {status:'Appointment', apptUnverified:'%s'};
notes['V'] = {status:'Contacted'};
notes['W'] = {status:'Appointment', nextStep:{date:'2026-11-02', what:'x'}};
out.unv = ayPanelHtml({c:'U', eqv:0});
out.talked = ayPanelHtml({c:'V'});
out.ver = ayPanelHtml({c:'U', eqc:{f:1}});
out.needU = ayNeedsDate(notes['U']); out.needV = ayNeedsDate(notes['V']); out.needW = ayNeedsDate(notes['W']);
console.log(JSON.stringify(out));
""" % TODAY
o, e = run(body)
o = o or {}
rec('unverified booking shows the warning', 'Booked on unverified equity' in o.get('unv', ''), (o.get('unv') or e)[:200])
rec('no panel before Appointment', o.get('talked') == '')
rec('verified equity drops the warning', 'Booked on unverified equity' not in o.get('ver', '') and 'id="ay_sv"' in o.get('ver', ''))
rec('panel has stage, date, source, keep-or-sell', all(x in o.get('unv', '') for x in ('id="ay_st"', 'id="ay_dt"', 'id="ay_es"', 'id="ay_ks"')))
rec('an open deal with no next step cannot be walked away from', o.get('needU') is True and o.get('needV') is False
    and o.get('needW') is False, o)

print('\nWIRING')
lo = html[html.index('function logOutcome('):]
lo = lo[:lo.index('\n}\n')]
rec("logOutcome: appt never walks a later stage back and books-unverified through the gate",
    "if(AY_STAGES.indexOf(n.status) < 1) n.status='Appointment';" in lo and 'if(!eqFact(r)) ayBookedUnverified(' in lo, lo[:200])
ac = html[html.index('function afterCall('):]
ac = ac[:ac.index('\n}\n')]
rec('afterCall renders the panel, the banner and gates leaving', 'ayPanelHtml(r)' in ac and 'Booked on unverified equity' in ac
    and 'ayNeedsDate(notes[r.c])' in ac and 'ayWire(r)' in ac)

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
