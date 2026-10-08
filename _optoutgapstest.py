"""Four opt-out gaps reported by the 10-03 Call Mode review and PR #159, closed 2026-10-08.

  1. A number DNC-flagged on one lead but not on another was still dialable and lookup-linked
     through the clean copy (phdnc is per record).
  2. The lookup screen's Text link ignored the text hold (textingHeld()).
  3. A case-keyed opt-out did not reach the same person's other cases (pkey), in call_rows, the
     lookup or the board's manual contact buttons.
  4. A phone opt-out saved with its country code ('#1XXXXXXXXXX') never matched the lead's
     10-digit number.

Runs the real Python and the real page JS (in node). Fake cases (FAKE-2099-*), fake 555 numbers,
no network, no contact.   python _optoutgapstest.py
"""
import json
import os
import re
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
bk = types.ModuleType('bk_lookup')
bk.federal_hold_index = lambda: object()
bk.federal_hold = lambda case, index=None, here=None: (False, '')
sys.modules['bk_lookup'] = bk
import call_mode as cm          # noqa: E402
import phone_src as PS          # noqa: E402
import morning_planner as MP    # noqa: E402

FAILS = []


def check(name, ok):
    print(('PASS ' if ok else 'FAIL ') + name)
    if not ok:
        FAILS.append(name)


def lead(case, nums, dnc=None, **kw):
    d = {'case': case, 'phones': list(nums), 'phdnc': list(dnc or [0] * len(nums)),
         'oname': 'FAKE OWNER', 'owners': 'FAKE OWNER', 'addr': '1 Fake St, Miami', 'days': 10,
         'value': 300000, 'judg': 100000, 'eq': 60}
    d.update(kw)
    return d


def node(js):
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def js_fn(src, name):
    m = re.search(r'\nfunction ' + name + r'\(.*?\n}\n', src, re.S)
    assert m, name
    return m.group(0)


CM_SRC = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
BOARD = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()

# ---- 1. DNC on one lead, clean on another -----------------------------------------------------
A, B = 'FAKE-2099-100001', 'FAKE-2099-100002'
slim = [lead(A, ['3055550101'], dnc=[1]), lead(B, ['3055550101', '3055550102'], dnc=[0, 0])]
rows, _ = cm.call_rows(slim, {}, {})
b_row = [r for r in rows if r.get('c') == B]
b_nums = (b_row[0].get('p') or []) if b_row else []
check('1a dial queue drops a number another lead flags DNC', '3055550101' not in b_nums)
check('1b the lead keeps its other clean number', '3055550102' in b_nums)
idx = cm.phone_index(slim, cm.lookup_hold_fn(slim, {}, {}))
check('1c lookup index does not carry a number another lead flags DNC', '3055550101' not in idx['d'])
check('1d lookup still identifies the lead by its clean number', '3055550102' in idx['d'])
bake = [lead(A, ['(305) 555-0101'], dnc=[1]),
        dict(lead(B, ['3055550101', '3055550102'], dnc=[0]), phrank=['CALL FIRST', ''], phbest=0)]
n_leads, n_nums = PS.propagate_dnc(bake) if hasattr(PS, 'propagate_dnc') else (0, 0)
check('1e bake flags the clean copy too', bake[1]['phdnc'] == [True, False] and (n_leads, n_nums) == (1, 1))
check('1f bake drops the stale ranking that pointed at it', bake[1]['phbest'] is None and bake[1]['phrank'] == [])
check('1g bake never clears a flag', bake[0]['phdnc'] == [1])
# a flagged number the MAX_PHONES cut dropped from lead A still flags lead B's copy
bake2 = [lead(B, ['3055550104'], dnc=[0])]
if hasattr(PS, 'propagate_dnc'):
    try:
        PS.propagate_dnc(bake2, extra=['+1 (305) 555-0104'])
    except TypeError:
        pass
check('1h bake flags a number known DNC from outside the rows', bake2[0]['phdnc'] == [True])

# ---- 2. lookup Text link obeys the text hold -------------------------------------------------
m = re.search(r"\+   \(\(function\(\)\{ var _hs = .*?\}\)\(\)\)", CM_SRC, re.S)
check('2a lookup link block found', bool(m))
if m:
    expr = m.group(0)[len('+   '):]
    js = ('function esc(s){return String(s);} function dialHref(n){return "tel:"+n;} '
          'function dialTarget(){return "";} function hardSuppressed(){return "";} var r=null;'
          'var HELD=true; function textingHeld(){return HELD;} '
          'function textHoldWhy(){return HELD?"Texting is held: test":"";}'
          'var h={c:"FAKE-2099-100003", num:"3055550103", h:""};'
          'var held=' + expr + '; HELD=false; var open=' + expr + ';'
          'console.log(JSON.stringify({held:held, open:open}));')
    v = node(js)
    check('2b held: no sms: link', 'sms:' not in v['held'] and 'Texting is held' in v['held'])
    check('2c held: Call back still offered', 'tel:' in v['held'])
    check('2d not held: Text link present', 'sms:3055550103' in v['open'])

# ---- 3. a case opt-out closes the same person's other cases ----------------------------------
P = 'Pabcdef0123'
O1, O2, O3 = 'FAKE-2099-100011', 'FAKE-2099-100012', 'FAKE-2099-100013'
slim3 = [lead(O1, ['3055550111'], pkey=P, pkn=2), lead(O2, ['3055550112'], pkey=P, pkn=2),
         lead(O3, ['3055550113'], pkey='C' + O3)]
oo3 = {O1: {'optout': '2099-01-01'}}
rows3, _ = cm.call_rows(slim3, oo3, {})
cases3 = {r.get('c') for r in rows3}
check('3a dial queue drops the sibling of an opted-out case', O2 not in cases3)
check('3b an unrelated lead stays', O3 in cases3)
hold = cm.lookup_hold_fn(slim3, oo3, {})
check('3c lookup holds the sibling', bool(hold(slim3[1])) and not hold(slim3[2]))
cov, _n = cm.coverage_rows(slim3, cases3, oo3, {})
check('3d coverage routes the sibling to OUT', any(r['c'] == O2 and r.get('oo') == 1 for r in cov))
idx3 = cm.phone_index(slim3, lambda d: '')
row2 = idx3['t'][idx3['d']['3055550112']]
check('3e lookup row carries the other cases', row2[8] == [O1])
check('3f a singleton carries none', idx3['t'][idx3['d']['3055550113']][8] is None)
# phone side, a no logged AFTER the build on the sibling: hardSuppressed walks pcs
js = ('var notes={"' + O1 + '":{no:"hard"}}, _OPTPH=null;' + js_fn(CM_SRC, 'optPhones')
      + js_fn(CM_SRC, 'hardSuppressed')
      + 'console.log(JSON.stringify(hardSuppressed({c:"' + O2 + '", p:["3055550112"], pcs:' + json.dumps(row2[8]) + '})));')
check('3g lookup hit with pcs is suppressed by a sibling hard no', bool(node(js)))
# board manual buttons
board_js = ('var notes={"' + O1 + '":{optout:"2099-01-01", status:"DO NOT CONTACT"}};'
            'var DATA=' + json.dumps([{'case': O1, 'pkey': P}, {'case': O2, 'pkey': P}, {'case': O3}]) + ';'
            'let _OOP=null,_OOPsrc=null; let _PGROUP=null;'
            'function _isOptedOut(n){return !!(n&&n.optout);} function _isWrongOwner(){return false;}'
            'function _addrKey(v){return "h"+v;}'
            + js_fn(BOARD, '_optedOutIdentities') + js_fn(BOARD, '_isOptedOutPerson')
            + js_fn(BOARD, '_pgroup') + js_fn(BOARD, '_personKey') + js_fn(BOARD, '_personCases')
            + js_fn(BOARD, '_boardWasLp') + js_fn(BOARD, '_boardNoState') + js_fn(BOARD, '_textContactBlocked')
            + 'console.log(JSON.stringify([_textContactBlocked({case:"' + O2 + '", pkey:"' + P + '"}),'
              '_textContactBlocked({case:"' + O3 + '"})]));')
v = node(board_js)
check('3h board blocks text/email/letter on the sibling', v[0] == 'optout')
check('3i board leaves an unrelated lead alone', v[1] == '')

# ---- 4. '#1XXXXXXXXXX' matches the 10-digit lead number ---------------------------------------
L4 = lead('FAKE-2099-100021', ['3055550121'])
oo4 = {'#13055550121': {'optout': '2099-01-01'}}
check('4a call_mode identity reader matches the 11-digit key', cm._identity_opted_fn([L4], oo4)(L4))
rows4, _ = cm.call_rows([L4], oo4, {})
check('4b dial queue drops it', not rows4)
check('4c morning planner matches it', MP._opted_out(L4, {'notes': oo4}))
check('4c2 morning planner matches a dict-shaped skip-trace phone',
      MP._opted_out({'case': 'FAKE-2099-100022', 'phones': [{'number': '3055550121', 'score': 97}]}, {'notes': oo4}))
check('4d morning planner still matches a 10-digit key', MP._opted_out(L4, {'notes': {'#3055550121': {}}}))
js = ('var notes={"#13055550121":{optout:"x"}}, _OPTPH=null;' + js_fn(CM_SRC, 'optPhones')
      + js_fn(CM_SRC, 'hardSuppressed')
      + 'console.log(JSON.stringify(hardSuppressed({c:"FAKE-2099-100021", p:["3055550121"]})));')
check('4e Call Mode phone matches a device-saved 11-digit key', bool(node(js)))
js = ('var notes={"#13055550121":{optout:"x"}}; let _OOP=null,_OOPsrc=null;'
      'function _addrKey(v){return "h"+v;}' + js_fn(BOARD, '_optedOutIdentities') + js_fn(BOARD, '_isOptedOutPerson')
      + 'console.log(JSON.stringify([_isOptedOutPerson({phones:["3055550121"]}),'
        '_isOptedOutPerson({phones:["13055550121"]}), _isOptedOutPerson({phones:["3055550199"]})]));')
v = node(js)
check('4f board matches an 11-digit key against a 10-digit lead', v[0] is True)
check('4g board matches an 11-digit lead number too', v[1] is True)
check('4h board does not match a different number', v[2] is False)
# the server ledger ships phone opt-outs as '#'+_addrKey(10 digits): the board must match those
js = ('let _OOP=null,_OOPsrc=null;' + js_fn(BOARD, '_addrKey') + 'var notes={};'
      'notes["#"+_addrKey("3055550122")]={optout:"x", status:"DO NOT CONTACT"};'
      + js_fn(BOARD, '_optedOutIdentities') + js_fn(BOARD, '_isOptedOutPerson')
      + 'console.log(JSON.stringify([_isOptedOutPerson({phones:["(305) 555-0122"]}),'
        '_isOptedOutPerson({phones:["3055550123"]})]));')
v = node(js)
check('4j board matches a hashed server-ledger phone opt-out', v[0] is True)
check('4k ...and only that number', v[1] is False)
fl = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
check('4i bake hashes the 10-digit form of an 11-digit key',
      "_optouts['#' + _addr_key(_raw[1:])]" in fl)

print('%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
