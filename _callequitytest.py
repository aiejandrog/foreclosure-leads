"""Call Mode equity card (2026-10-29): a dollar equity is spoken only when the baked eqc says FACT.

Pins:
  - call_rows ships a slim `eqc` from the baked card (state, label, fact, may_quote, judgment + source,
    recorded liens, basis, net); no card = no key, and net is dropped unless may_quote
  - the card prints the state label, judgment + source, each recorded lien ("recorded amount, not a
    payoff") and the net, from eqc only
  - GUESS (none / unchecked) and CEILING (unpriced) print "Equity not verified -- do not quote a number"
    and NO dollar equity
  - a FACT with no judgment posted prints no net figure
  - rule 6 hole closed: the call screen's Money line no longer subtracts value - payoff/judgment itself;
    the old expression is gone and a dollar equity/UNDERWATER figure is read only from eqc.n
  - build_html builds a page that carries the card

Invented leads only; the JS is extracted by name from the built page and run under node.
Run: DEALFLOW_DIR=<temp> python _callequitytest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='calleq_')
os.environ.setdefault('DEALFLOW_DIR', _T)
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


A = '3055550101'
CARD_FACT = {'state': 'clear', 'label': 'VERIFIED CLEAR - chain traced, no surviving mortgage found', 'fact': True,
             'judgment': 100000.0, 'judgment_source': 'docket', 'liens': [], 'basis': 400000.0,
             'basis_source': 'county', 'net': 300000.0, 'may_quote': True, 'why': 'x'}
CARD_PRICED = dict(CARD_FACT, state='priced', label='VERIFIED PRICED',
                   liens=[{'type': 'mortgage', 'amount': 150000.0, 'recorded': '03/02/2019', 'source': 'official records'}],
                   net=150000.0)
CARD_CEIL = {'state': 'unpriced', 'label': 'CEILING - lien amount unread', 'fact': False, 'judgment': 100000.0,
             'judgment_source': 'docket', 'liens': [{'type': 'mortgage', 'amount': None, 'recorded': '03/02/2019',
                                                      'source': 'official records'}],
             'basis': 400000.0, 'basis_source': 'arv', 'net': None, 'may_quote': False, 'why': 'x'}
CARD_GUESS = {'state': 'none', 'label': 'GUESS - never searched', 'fact': False, 'judgment': None,
              'judgment_source': 'not posted', 'liens': [], 'basis': 400000.0, 'basis_source': 'county',
              'net': None, 'may_quote': False, 'why': 'x'}
CARD_NOJ = dict(CARD_FACT, judgment=None, judgment_source='not posted', net=None, may_quote=False)


def lead(case, eqc=None, **extra):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': 20, 'tier': 'B',
         'score': 50, 'value': 400000, 'phones': [A], 'phtype': ['mobile'], 'phsrc': ['st'], 'phdnc': [False],
         'phrank': ['ok'], 'phbest': 0}
    if eqc is not None:
        d['eqc'] = eqc
    d.update(extra)
    return d


def row(d):
    rows, _ = call_mode.call_rows([d], None, None)
    return rows[0] if rows else None


print('\nROW PAYLOAD')
r = row(lead('F1', CARD_PRICED))
e = r.get('eqc') or {}
rec('slim card shipped', e.get('st') == 'priced' and e.get('f') == 1 and e.get('q') == 1 and e.get('n') == 150000.0, e)
rec('judgment and source shipped', e.get('j') == 100000.0 and e.get('js') == 'docket', e)
rec('lien shipped as recorded amount', e.get('li') == [['mortgage', 150000.0, '03/02/2019', 'official records']], e)
r = row(lead('C1', CARD_CEIL))
e = r.get('eqc') or {}
rec('ceiling: not a fact, no net', e.get('f') == 0 and e.get('q') == 0 and 'n' not in e, e)
r = row(lead('G1', dict(CARD_GUESS, net=999.0)))
rec('a net on a card that may not be quoted is dropped', 'n' not in (r.get('eqc') or {}), r.get('eqc'))
r = row(lead('N1'))
rec('no card: no eqc key', 'eqc' not in r, list(r))

print('\nTHE CARD, RUN UNDER NODE')
tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
html = call_mode.build_html([], 0, {'ct': 'x', 'k': 'y'}, 'built', 'sig', 'bsig',
                            sync_js=call_mode.extract_sync_js(tpl), funnel_js=call_mode.extract_funnel_js(tpl),
                            text_js=call_mode.extract_text_js(tpl),
                            mail_js=call_mode.bake_mail_js(call_mode.extract_mail_js(tpl), 'test'),
                            histcov='{"ledgers_ok": true, "why": "", "capped": false, "total": 0, "shipped": 0}')
rec('page builds and carries the card', 'function eqCardHtml(' in html and "id=\"eqcard\"" in html)


def fn(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n}\n', a) + 3]


def one_line(name):
    a = html.index('function %s(' % name)
    return html[a:html.index('\n', a)]


cases = {
    'fact': row(lead('F2', CARD_PRICED)), 'ceil': row(lead('C2', CARD_CEIL)), 'guess': row(lead('G2', CARD_GUESS)),
    'noj': row(lead('J2', CARD_NOJ)), 'old': row(lead('O2')), 'underwater': row(lead('U2', dict(
        CARD_FACT, net=-25000.0))),
}
js = fn('esc') + one_line('eqFact') + '\n' + one_line('eqMay') + '\n' + one_line('_eqUsd') + '\n' + fn('eqCardHtml') + """
var R = %s;
var out = {};
Object.keys(R).forEach(function(k){ out[k] = eqCardHtml(R[k]); });
console.log(JSON.stringify(out));
""" % json.dumps(cases)
path = os.path.join(_T, 'card.js')
io.open(path, 'w', encoding='utf-8').write(js)
p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
rec('node ran', p.returncode == 0, p.stderr[-300:])
o = json.loads(p.stdout) if p.returncode == 0 else {}
WARN = 'Equity not verified -- do not quote a number'
rec('fact: label, judgment + source, lien, net', all(x in o.get('fact', '') for x in (
    'VERIFIED PRICED', 'PRICED', '$100,000', 'docket', 'Recorded mortgage: $150,000', '03/02/2019',
    'Net equity', '$150,000', '$400,000')), o.get('fact'))
rec('fact: no warning words', WARN not in o.get('fact', ''))
rec('lien is never called a payoff', 'recorded amount, not a payoff' in o.get('fact', '')
    and 'payoff:' not in o.get('fact', '').lower())
for k in ('ceil', 'guess', 'old'):
    rec(k + ': plain warning words', WARN in o.get(k, ''), o.get(k))
    rec(k + ': no dollar equity', 'Net equity' not in o.get(k, '') and '300,000' not in o.get(k, ''), o.get(k))
rec('ceiling says so', 'ceiling' in o.get('ceil', '').lower())
rec('guess says so', 'guess' in o.get('guess', '').lower())
rec('ceiling lien with no amount says so, prints no $0', 'amount not read' in o.get('ceil', '') and '$0' not in o.get('ceil', ''))
rec('fact without a posted judgment: no net, says why', 'Net equity' not in o.get('noj', '')
    and 'Judgment not posted' in o.get('noj', ''), o.get('noj'))
rec('underwater is stated, not hidden', 'UNDERWATER $25,000' in o.get('underwater', ''), o.get('underwater'))

print('\nRULE 6: THE CALL SCREEN MONEY LINE')
i = html.index("addRef('Money'")
blk = html[i - 900:i + 1100]
rec("old 'value minus owed' equity expression is gone", '(+r.v - (+r.py || +r.jg))' not in html)
rec('money line reads eqc.n via eqMay', 'eqMay(r) && r.eqc.n != null' in blk, blk[:400])
rec('money line falls back to the warning', WARN in blk)
rec('the card sits on the reference block and the sheet', html.count('eqCardHtml(r)') >= 2, html.count('eqCardHtml(r)'))

print('\n%s' % ('ALL PASS' if not fails else 'FAILS: %d -> %s' % (len(fails), fails)))
sys.exit(1 if fails else 0)
