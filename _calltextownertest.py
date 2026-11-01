"""Call Mode: no text composer on a number that is not the owner's (household or name-only).

The board's textablePhones() withholds 'hh' (household member at the address) and 'nm' (name-only
match, maybe a namesake): the text names the property and the case, so sending it to either
"discloses this homeowner's foreclosure to somebody who never asked". Call Mode's after-call panel
had no such gate. call_rows ships the same tags as r.ps, the card says "Do not discuss the case with
them", and the panel still offered the ladder text (and every situational text) on that number.

Pins, by running the REAL decision chain of afterCall() (extracted from call_mode._PAGE) under node:
  - ps 'hh' / 'nm': no text button, for the cold ladder and for every situational outcome
  - ps '' (owner), 'wp': the text button is still offered (nothing else changed)
  - the older holds keep their order and wording: suppressed, stale list, FTSA cap, bad number,
    do-not-text, likely owner (pw 'l', texted only after a call reaches them), pasted ('wpm')
  - the tags come from the real call_rows path (invented people, 555-01xx numbers, example.com)
Run: python _calltextownertest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='calltextowner_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


PAGE = call_mode._PAGE
a = PAGE.index("  var txt = '';")
b = PAGE.index('  /* NAME THE NUMBER.', a)
CHAIN = PAGE[a:b]
rec('the extracted chain is the text decision', "hardSuppressed(r)" in CHAIN and "id=\"tx\"" in CHAIN, CHAIN[:80])

JS = """
function esc(s){return String(s==null?'':s);}
function txLabel(k, st){return 'LABEL';}
var TEXT_SIT = {apptconfirm:{t:'Confirm appt'}, aftertalk:{t:'After talk'}, booklink:{t:'Booking link'},
                apptremind:{t:'Remind'}, noshow:{t:'No show'}, cbtext:{t:'Callback'}};
var cases = JSON.parse(process.argv[2]);
function run(c){
  var r = c.r, o = c.o, phIdx = 0, dnt = !!c.dnt, st = c.st || 'cold', miss = true;
  var fl = {ok: c.flok !== false, txt: '9:00 PM'};
  function hardSuppressed(){ return c.supp || ''; }
  function textingHeld(){ return !!c.held; }
  function textHoldWhy(){ return 'held'; }
  function _tele24(){ return c.tele || 0; }
  var sitMap = { appt:['apptconfirm','booklink','apptremind','noshow'], callback:['cbtext'],
                 talked:['aftertalk','booklink'], notint:['aftertalk'] };
  var sitKeys = sitMap[o.k] || [];
  var _txk = sitKeys.length ? sitKeys[0] : 'ladder';
  var _chips = '';
  %s
  return txt;
}
var out = cases.map(function(c){ var t = run(c); return {name: c.name, button: t.indexOf('id="tx"') >= 0, txt: t.slice(0, 160)}; });
console.log(JSON.stringify(out));
""" % CHAIN


def run(cases):
    p = os.path.join(_T, 'chain.js')
    io.open(p, 'w', encoding='utf-8').write(JS)
    q = subprocess.run([os.environ.get('NODE_BIN', 'node'), p, json.dumps(cases)],
                       capture_output=True, text=True, timeout=60)
    if q.returncode != 0:
        rec('node ran the chain', False, q.stderr[-300:])
        return {}
    return {x['name']: x for x in json.loads(q.stdout)}


def r(ps, pw=''):
    return {'p': ['3055550101'], 'ps': ps, 'pw': pw, 'on': 'Jose Garcia'}


cases = []
for outcome in ('noanswer', 'voicemail', 'talked', 'appt', 'callback', 'notint'):
    cases.append({'name': 'hh/' + outcome, 'r': r(['hh'], 'h'), 'o': {'k': outcome}})
    cases.append({'name': 'nm/' + outcome, 'r': r(['nm'], 'n'), 'o': {'k': outcome}})
cases += [
    {'name': 'hh-resident-owner', 'r': r(['hh'], 'o'), 'o': {'k': 'noanswer'}},
    {'name': 'owner/noanswer', 'r': r([''], 'o'), 'o': {'k': 'noanswer'}},
    {'name': 'owner/no-ps', 'r': {'p': ['3055550101'], 'on': 'Jose Garcia'}, 'o': {'k': 'noanswer'}},
    {'name': 'owner/appt', 'r': r([''], 'o'), 'o': {'k': 'appt'}},
    {'name': 'likely/noanswer', 'r': r(['hh'], 'l'), 'o': {'k': 'noanswer'}},
    {'name': 'likely/talked', 'r': r(['hh'], 'l'), 'o': {'k': 'talked'}},
    {'name': 'wpm/noanswer', 'r': r(['wpm']), 'o': {'k': 'noanswer'}},
    {'name': 'dnt+hh', 'r': r(['hh'], 'h'), 'o': {'k': 'noanswer'}, 'dnt': True},
    {'name': 'supp+owner', 'r': r(['']), 'o': {'k': 'noanswer'}, 'supp': 'opted out'},
    {'name': 'held+owner', 'r': r(['']), 'o': {'k': 'noanswer'}, 'held': True},
    {'name': 'ftsa+owner', 'r': r(['']), 'o': {'k': 'noanswer'}, 'tele': 3},
    {'name': 'badnum+owner', 'r': r(['']), 'o': {'k': 'badnum'}},
    {'name': 'late+owner', 'r': r(['']), 'o': {'k': 'noanswer'}, 'flok': False},
]
res = run(cases)

for outcome in ('noanswer', 'voicemail', 'talked', 'appt', 'callback', 'notint'):
    for tag in ('hh', 'nm'):
        x = res.get(tag + '/' + outcome, {})
        rec('%s number, %s: no text button' % (tag, outcome), x.get('button') is False, x)
rec('deed owner filed as a resident (ps hh) stays call-only, like the board', res.get('hh-resident-owner', {}).get('button') is False,
    res.get('hh-resident-owner'))
rec('an owner number still gets the ladder text', res.get('owner/noanswer', {}).get('button') is True, res.get('owner/noanswer'))
rec('a lead with no ps array still gets the ladder text', res.get('owner/no-ps', {}).get('button') is True, res.get('owner/no-ps'))
rec('an owner number still gets the situational text', res.get('owner/appt', {}).get('button') is True, res.get('owner/appt'))
rec('likely owner, no answer: held with its own wording', res.get('likely/noanswer', {}).get('button') is False
    and 'Likely owner per Whitepages' in res.get('likely/noanswer', {}).get('txt', ''), res.get('likely/noanswer'))
rec('likely owner, call reached them: the situational text is still offered (2026-10-22 rule kept)',
    res.get('likely/talked', {}).get('button') is True, res.get('likely/talked'))
rec('pasted number has no text button', res.get('wpm/noanswer', {}).get('button') is False, res.get('wpm/noanswer'))
rec('do-not-text still wins over the household note', 'do-not-text' in res.get('dnt+hh', {}).get('txt', ''), res.get('dnt+hh'))
rec('suppressed still blocks an owner number', 'suppressed' in res.get('supp+owner', {}).get('txt', ''), res.get('supp+owner'))
rec('stale list still blocks an owner number', res.get('held+owner', {}).get('button') is False, res.get('held+owner'))
rec('FTSA cap still blocks an owner number', 'FTSA cap' in res.get('ftsa+owner', {}).get('txt', ''), res.get('ftsa+owner'))
rec('bad number still blocks', 'Bad number' in res.get('badnum+owner', {}).get('txt', ''), res.get('badnum+owner'))
rec('hours window still applies to an owner number', res.get('late+owner', {}).get('button') is False, res.get('late+owner'))
rec('the household note names the reason', 'owner' in res.get('hh/noanswer', {}).get('txt', '').lower()
    and 'call only' in res.get('hh/noanswer', {}).get('txt', '').lower(), res.get('hh/noanswer'))

# ---- the tags come from the real call_rows path ------------------------------------------------
lead = {'case': '2026-000901-CA-01', 'owners': 'JOSE GARCIA', 'oname': 'JOSE GARCIA',
        'addr': '100 NW 1st Ave, Miami, FL 33130', 'folio': '0101010101010', 'days': 20,
        'auction': '2026-11-25', 'st': 'FC', 'pkey': 'pk901',
        'phones': ['3055550101', '3055550102', '3055550103'], 'phtype': ['mobile'] * 3,
        'phsrc': ['st', 'hh', 'nm'], 'phdnc': [False] * 3}
rows, _ = call_mode.call_rows([lead])
row = rows[0]
tags = dict(zip(row['p'], row.get('ps') or [''] * len(row['p'])))
rec('call_rows ships hh and nm for those numbers', tags.get('3055550102') == 'hh' and tags.get('3055550103') == 'nm'
    and tags.get('3055550101') == '', tags)
# the chain reads r.ps[phIdx] with phIdx 0, so rebuild a row per number
cases2 = []
for i, n in enumerate(row['p']):
    rr = dict(row)
    rr['p'] = [n]
    rr['ps'] = [(row.get('ps') or [''] * len(row['p']))[i]]
    cases2.append({'name': n, 'r': rr, 'o': {'k': 'noanswer'}})
res3 = run(cases2)
rec('real row: owner number textable, household and name-only not',
    res3.get('3055550101', {}).get('button') is True and res3.get('3055550102', {}).get('button') is False
    and res3.get('3055550103', {}).get('button') is False, res3)

print('\n%d failed' % len(fails) if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
