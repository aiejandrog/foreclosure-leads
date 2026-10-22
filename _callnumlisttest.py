"""Call Mode: every number on the card, Whitepages-confirmed owner numbers first (2026-10-22).

Alejandro, 10-22: Call Mode says "number 1 of 10" and shows one number; he wants all of them side
by side before he dials, with the ones Whitepages ties to the owner marked and put on the button.

Pins:
  - call_rows ships `pw` (what Whitepages says about each number) and `pt` (line type) aligned with `p`
  - a number Whitepages lists as the OWNER's goes first, even when the skip trace tagged it 'st'
    and phone_rank's phbest named a different number
  - a confirmed owner number phone_rank called a last resort does not jump ahead
  - household / name-only numbers never move ahead of the owner's, whatever Whitepages says
  - no Whitepages record: pw absent and the order is exactly what it was before
  - the card lists every number; picking one moves the button, never dials, never writes a note

Invented leads only. The JS is extracted by name from call_mode.py and run under node.
Run: python _callnumlisttest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='callnum_')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')   # no federal cache: no holds

import call_mode  # noqa: E402

SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


A, B, C, D, E = '3055550101', '3055550102', '3055550103', '3055550104', '3055550105'


def lead(case, **extra):
    d = {'case': case, 'county': 'BROWARD', 'st': 'FC', 'auction': '11/01/2026', 'days': 20,
         'tier': 'B', 'score': 50, 'value': 400000,
         'phones': [A, B, C, D], 'phtype': ['landline', 'mobile', 'mobile', 'mobile'],
         'phsrc': ['st', 'st', 'st', 'hh'], 'phdnc': [False] * 4,
         'phrank': ['ok', 'call first', 'ok', 'ok'], 'phbest': 1}
    d.update(extra)
    return d


def row(d):
    rows, _ = call_mode.call_rows([d], None, None)
    return rows[0] if rows else None


print('\nNO WHITEPAGES RECORD: ORDER UNCHANGED, NOTHING CLAIMED')
r = row(lead('NOWP'))
rec('phbest still leads', r['p'][0] == B, r['p'])
rec('household number still last', r['p'][-1] == D, r['p'])
rec('pw absent (card says "not checked")', 'pw' not in r, r.get('pw'))
rec('pt aligned with p', len(r['pt']) == len(r['p']) and r['pt'][0] == 'm'
    and r['pt'][r['p'].index(A)] == 'l', (r['p'], r['pt']))

print('\nA TRACE NUMBER WHITEPAGES LISTS AS THE OWNER\'S GOES FIRST')
wp = [{'n': C, 'type': 'mobile', 'resident': False, 'source': 'property'},
      {'n': D, 'type': 'mobile', 'resident': True, 'source': 'property'}]
r = row(lead('WP', wpAllPhones=wp, wpKey='ok'))
rec('confirmed owner number is on the button', r['p'][0] == C, r['p'])
rec('phbest comes right after it', r['p'][1] == B, r['p'])
rec('pw marks it o, the resident h, the rest -', r['pw'][0] == 'o' and r['pw'][r['p'].index(D)] == 'h'
    and r['pw'][r['p'].index(A)] == '-', (r['p'], r['pw']))
rec('household number still last even though Whitepages lists it', r['p'][-1] == D, r['p'])
rec('pw same length as p', len(r['pw']) == len(r['p']))

print('\nA CONFIRMED NUMBER RANKED LAST RESORT DOES NOT JUMP')
wpA = [{'n': A, 'type': 'landline', 'resident': False, 'source': 'property'}]
r = row(lead('WPL', wpAllPhones=wpA, phrank=['last resort', 'call first', 'ok', 'ok']))
rec('phbest keeps the button', r['p'][0] == B, r['p'])
rec('it is still marked confirmed', r['pw'][r['p'].index(A)] == 'o', r.get('pw'))

r = row(lead('WPLOK', wpAllPhones=wpA))
rec('a confirmed landline ranked ok does not take the button from a mobile', r['p'][0] == B, r['p'])
rec('so the lead is not flagged landline-first', r.get('_ll', 0) == 0, r.get('_ll'))

print('\nWHITEPAGES CANNOT PROMOTE A HOUSEHOLD OR NAME-ONLY NUMBER')
wpD = [{'n': D, 'type': 'mobile', 'resident': False, 'source': 'property'}]
r = row(lead('WPHH', wpAllPhones=wpD))
rec('an hh-tagged number stays behind the owner role', r['p'][-1] == D, r['p'])
nm = [{'n': B, 'type': 'mobile', 'resident': False, 'source': 'person'}]
r = row(lead('WPNM', wpAllPhones=nm, phbest=None))
rec('a name-only Whitepages match is n, not o', r['pw'][r['p'].index(B)] == 'n', r.get('pw'))
pv = [{'n': B, 'type': 'mobile', 'resident': False, 'source': 'person-verified'}]
r = row(lead('WPPV', wpAllPhones=pv, phbest=0))
rec('a Person record placing the owner at the address is o and leads', r['p'][0] == B and r['pw'][0] == 'o',
    (r['p'], r.get('pw')))
both = [{'n': C, 'resident': True, 'source': 'property'}, {'n': C, 'resident': False, 'source': 'property'}]
rec('a number listed twice keeps the strongest', call_mode._wp_phone_kinds({'wpAllPhones': both}) == {C: 'o'})
rec('11-digit Whitepages number matches', call_mode._wp_phone_kinds(
    {'wpAllPhones': [{'n': '1' + C, 'source': 'property'}]}) == {C: 'o'})

print('\nA RESIDENT WHO IS THE DEED OWNER (CACE-25-019745 SHAPE, INVENTED NAMES)')
# Whitepages files the defendant under Resident and a relative under Owner. Our deed owner is
# GARCIA, MARIA ELENA. Trace numbers: A (landline), B (mobile, phbest), C (mobile), D (hh).
res = [{'n': C, 'resident': True, 'source': 'property', 'owner': 'Maria Elena Garcia'},
       {'n': A, 'resident': True, 'source': 'property', 'owner': 'Jose Luis Garcia'},
       {'n': D, 'resident': True, 'source': 'property', 'owner': 'Maria Elena Garcia'}]
r = row(lead('RES', owners='GARCIA, MARIA ELENA', wpAllPhones=res))
rec('the owner listed as a resident is confirmed and leads', r['p'][0] == C and r['pw'][0] == 'o', (r['p'], r.get('pw')))
rec('a relative with the surname only stays h', r['pw'][r['p'].index(A)] == 'h', r.get('pw'))
rec('the named deed owner keeps owner confirmation despite the old household tag',
    r['pw'][r['p'].index(D)] == 'o', (r['p'], r.get('pw')))
rec('the original household source is retained for contact permissions',
    r['ps'][r['p'].index(D)] == 'hh', r.get('ps'))
resident_owner = [res[-1]]
r = row(lead('RES-ONLY', owners='GARCIA, MARIA ELENA', wpAllPhones=resident_owner))
rec('a verified resident-owner mobile beats the stale skiptrace best',
    r['p'][0] == D and r['pw'][0] == 'o', (r['p'], r.get('pw')))
import phone_rank
household_label = phone_rank.score_phone({'number': D, 'type': 'mobile', 'src': 'hh'}, 3)[1]
r = row(lead('RES-HOUSEHOLD-RANK', owners='GARCIA, MARIA ELENA', wpAllPhones=resident_owner,
             phrank=['ok', 'call first', 'ok', household_label]))
rec('the household score penalty cannot hide an independently matched owner mobile',
    household_label == 'last resort' and r['p'][0] == D)
r = row(lead('RES-DNC', owners='GARCIA, MARIA ELENA', wpAllPhones=resident_owner,
             phdnc=[False, False, False, True]))
rec('verified resident-owner DNC stays display only', D not in r['p'] and r['dn'][0][0] == D)

print('\nWHITEPAGES NAMES A DIFFERENT OWNER (10-22 SHAPE, INVENTED NAMES)')
# Our roll / court owner is ORLANDO; Whitepages labels MAYLEN the owner and someone else a resident.
wo_owners = [{'name': 'Maylen Perez Diaz', 'notOwner': True, 'phones': [{'n': D, 'type': 'mobile'}]}]
wo_all = [{'n': D, 'resident': True, 'source': 'property', 'owner': 'Maylen Perez Diaz'},
          {'n': A, 'resident': True, 'source': 'property', 'owner': 'Ana Rivera'}]
r = row(lead('WO', owners='MARTINEZ, ORLANDO', wpOwners=wo_owners, wpAllPhones=wo_all))
rec('the Whitepages owner name ships', r.get('wo') == ['Maylen Perez Diaz'], r.get('wo'))
rec('her number is marked w, not household', r['pw'][r['p'].index(D)] == 'w', r.get('pw'))
rec('a plain resident stays h', r['pw'][r['p'].index(A)] == 'h', r.get('pw'))
rec('the household tag that governs texting is unchanged', r['ps'][r['p'].index(D)] == 'hh', r.get('ps'))
co = wo_owners + [{'name': 'Orlando Martinez', 'notOwner': False, 'phones': [{'n': C}]}]
r = row(lead('WO-CO', owners='MARTINEZ, ORLANDO', wpOwners=co, wpAllPhones=wo_all + [{'n': C, 'source': 'property'}]))
rec('Whitepages listing our owner too is not a disagreement', r.get('wo') is None and 'w' not in r['pw'], (r.get('wo'), r['pw']))
r = row(lead('WO-NONE', owners='MARTINEZ, ORLANDO', wpAllPhones=wo_all))
rec('no Whitepages owners: nothing flagged', r.get('wo') is None and 'w' not in r['pw'], (r.get('wo'), r['pw']))

_pg2 = call_mode._PAGE


def _fn(name):
    a = _pg2.index('function %s(' % name)
    return _pg2[a:_pg2.index('\n}\n', a) + 3]


_nn = _pg2.index('var _NOTNAME')
_js2 = os.path.join(_T, 'wo.js')
open(_js2, 'w', encoding='utf-8').write(
    _pg2[_nn:_pg2.index(';', _pg2.index(']', _nn)) + 1] + '\n'
    + ''.join(_fn(n) for n in ('esc', 'has', 'firstName', '_wpChip', '_phSrcNote')) + """
var r = {on: 'Orlando Martinez', o: 'MARTINEZ, ORLANDO', f: '', wo: ['Maylen Perez Diaz'], pw: 'hw', ps: ['', 'hh']};
var r0 = {on: 'Orlando Martinez', o: 'MARTINEZ, ORLANDO', f: '', pw: 'h', ps: ['']};
console.log(JSON.stringify([firstName(r), firstName(r0), _wpChip(r, 1), _phSrcNote(r, 1), _phSrcNote(r, 0)]));
""")
_p4 = subprocess.run([os.environ.get('NODE_BIN', 'node'), _js2], capture_output=True, text=True, timeout=60)
_o4 = json.loads(_p4.stdout or 'null') if _p4.returncode == 0 else None
rec('card: no greeting by name when Whitepages names a different owner', bool(_o4) and _o4[0] == '' and _o4[1] == 'Orlando',
    (_o4, _p4.stderr[-300:]))
rec('card: her number says Whitepages owner, verify', bool(_o4) and 'listed OWNER Maylen Perez Diaz' in _o4[2] and 'verify' in _o4[2], _o4)
rec('card: dialing her number says who Whitepages says she is', bool(_o4) and 'Maylen Perez Diaz' in _o4[3]
    and 'Orlando Martinez' in _o4[3] and 'whether they own the house' in _o4[3], _o4)
rec('card: the warn bar names both', 'Whitepages lists a different owner' in _pg2 and 'r.wo.join' in _pg2)

print('\nDNC / AGENT NUMBERS STAY OUT')
r = row(lead('DNC', wpAllPhones=[{'n': B, 'source': 'property'}], phdnc=[False, True, False, False],
             phsrc=['st', 'st', 'ag', 'hh']))
rec('a DNC number Whitepages confirms is still dropped', B not in r['p'], r['p'])
rec('an agent number is still dropped', C not in r['p'], r['p'])
rec('pw/pt stay aligned after drops', len(r['pw']) == len(r['p']) == len(r['pt']), r)


print('\nDNC NUMBERS ARE VISIBLE ON THE CARD AND NEVER DIALABLE')
r = row(lead('SHOWDNC', phdnc=[False, True, False, False], phsrc=['st', 'st', 'st', 'hh']))
rec('the DNC number is not in the dial list', B not in r['p'], r['p'])
rec('it ships in dn, with whose and line type', r.get('dn') == [[B, 'o', 'm']], r.get('dn'))
rec('withheld count unchanged', r.get('k') == 1, r.get('k'))
rec('p/pw/pt/r still aligned', len(r['p']) == len(r['r']) == len(r['pt']))
r = row(lead('SHOWAG', phdnc=[False, True, False, False], phsrc=['st', 'ag', 'st', 'hh']))
rec('an agent number that is also DNC stays off the page', 'dn' not in r, r.get('dn'))
r = row(lead('HHDNC', phdnc=[False, False, False, True], phsrc=['st', 'st', 'st', 'hh']))
rec('a household DNC number is labeled household', r.get('dn') == [[D, 'h', 'm']], r.get('dn'))
r = row(lead('NODNC'))
rec('no DNC number: no dn key', 'dn' not in r)

print('\nTHE CARD')


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 and j > 0 else ''


rec('the lead card renders the list', '+   _numList(r)\n    +   _navRow()' in SRC)
rec('the list is wired on every lead paint', "  _wireNumList();\n  $('dial').addEventListener('click'" in SRC)
pick = SRC[SRC.find('function _wireNumList('):SRC.find('/* WHICH LINE THE CALL GOES OUT ON')]
for bad in ('saveNotes', 'queueSync', 'dials.push', 'logOutcome', 'tel:', 'dialHref'):
    rec('picking a number never touches ' + bad, bad not in pick)

FNS = ''.join(grab_fn(n) for n in ('esc', 'fmt', 'badSet', '_phSrcNote', '_wpChip', '_dncRows', '_numList'))
HARNESS = FNS + r"""
var phIdx = 1, notes = {X: {badph: ['3055550104'], dials: [{ph4: '0101', oc: 'noans'}, {ph4: '0101', oc: 'vm'}]}};
var r = {c: 'X', p: ['3055550103', '3055550101', '3055550102', '3055550104'], pw: 'o--h', pt: 'mlmm', ps: ['', '', '', 'hh']};
var h = _numList(r);
var withdnc = _numList({c: 'W', p: ['3055550101', '3055550102'], dn: [['3055550199', 'h', 'm']]});
var onlydnc = _numList({c: 'V', p: ['3055550101'], dn: [['3055550199', 'o', 'l']]});
var none = _numList({c: 'Y', p: ['3055550101', '3055550102']});
var one = _numList({c: 'Z', p: ['3055550101']});
var ownerHH = {c:'R', p:['3055550101','3055550102'], pw:'o-', ps:['hh',''], pt:'mm'};
console.log(JSON.stringify({h: h, none: none, one: one, withdnc: withdnc, onlydnc: onlydnc,
  ownerHH:_numList(ownerHH), ownerNote:_phSrcNote(ownerHH,0)}));
"""
fd, path = tempfile.mkstemp(suffix='.js')
os.close(fd)
io.open(path, 'w', encoding='utf-8').write(HARNESS)
try:
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
finally:
    os.remove(path)
rec('node ran', p.returncode == 0, p.stderr)
out = json.loads(p.stdout) if p.returncode == 0 else {'h': '', 'none': '', 'one': ''}
h = out['h']
rec('all four numbers listed', all(x in h for x in ('(305) 555-0103', '(305) 555-0101', '(305) 555-0102', '(305) 555-0104')))
rec('header counts the confirmed owner numbers', 'ALL 4 NUMBERS' in h and '1 confirmed the owner' in h, h[:300])
rec('Whitepages owner chip present once', h.count('Whitepages: owner') == 1)
rec('household chips', 'someone else at the address' in h and 'not the owner &middot; household' in h)
rec('not-on-Whitepages chip on unconfirmed numbers', h.count('not on Whitepages') == 2)
rec('line types shown', h.count('>mobile<') == 3 and h.count('>landline<') == 1)
rec('the selected number is marked on the button', h.count('on the button') == 1
    and h.index('on the button') > h.index('(305) 555-0101'))
rec('a bad number is disabled and labeled', 'data-k="3" disabled' in h and 'marked bad' in h)
rec('dial history counted by last four', 'rung 2&times;' in h)
rec('no Whitepages record says not checked', 'Whitepages not checked for this lead' in out['none']
    and 'not on Whitepages' not in out['none'])
rec('a one-number lead gets no list', out['one'] == '')
rec('a verified resident-owner card does not contradict its owner chip',
    'Whitepages: owner' in out.get('ownerHH', '')
    and 'not the owner' not in out.get('ownerHH', '') and out.get('ownerNote') == '')

wd = out.get('withdnc', '')
rec('DNC number is listed, struck through and labeled', '(305) 555-0199' in wd and 'do-not-call flag' in wd and 'line-through' in wd)
rec('the header counts it', 'ALL 3 NUMBERS (1 do-not-call)' in wd, wd[:200])
rec('a DNC row is not a button and carries no data-k', wd.count('data-k=') == 2 and wd.count('<button') == 2, wd)
rec('no tel/dial link anywhere in a DNC row', 'href' not in out.get('withdnc', '') and 'tel:' not in wd)
rec('DNC household / line labels shown', 'household' in wd)
rec('a one-number lead with a DNC number still shows the list', 'do-not-call flag' in out.get('onlydnc', ''))

# Whitepages address link on the card: Whitepages' own spelling (ordinals, "Apt"), as wp_web builds it.
_pg = call_mode._PAGE
_a = _pg.index('function fileLinks(')
_js = os.path.join(_T, 'wplinks.js')
_b = _pg.index('\n}\n', _a) + 3
open(_js, 'w', encoding='utf-8').write(_pg[_a:_b] + """
var streets = ['2929 NW 123 TER', '8600 NW 34 PL #B105', '9999 NE 99 AVE 999', '5000 US HIGHWAY 1'];
console.log(JSON.stringify(streets.map(function(s){ var L = fileLinks({a: s + ', MIAMI, FL 33101', o: 'X Y', ct: 'MI', fo: ''});
  return (L.filter(function(x){ return x[0] === 'Whitepages addr'; })[0] || ['', ''])[1]; })));
""")
_p2 = subprocess.run([os.environ.get('NODE_BIN', 'node'), _js], capture_output=True, text=True, timeout=60)
_got = json.loads(_p2.stdout or 'null') if _p2.returncode == 0 else None
import wp_web as _W
_want = ['https://www.whitepages.com/address/%s/MIAMI-FL' % '-'.join(_W._wp_street(x).split())
         for x in ('2929 NW 123 TER', '8600 NW 34 PL #B105', '9999 NE 99 AVE 999', '5000 US HIGHWAY 1')]
rec('card Whitepages address link spells streets like wp_web (the house, not the city list)',
    _got == _want, (_got, _want, _p2.stderr[-300:]))

# The Whitepages search-box stem, same rule as wp_web._search_stem.
_js5 = os.path.join(_T, 'stem.js')
_a5 = _pg.index('function _wpStem(')
open(_js5, 'w', encoding='utf-8').write(_pg[_a5:_pg.index('\n}\n', _a5) + 3] + """
console.log(JSON.stringify(['4809 NW 45 AVE', '2634 SE 19 CT #205C', '10442 SUNRISE LAKES BLVD 105',
  '4500 N STATE ROAD 7', '1234 US HIGHWAY 1', '1723 CANAL DR'].map(_wpStem)));
""")
_p5 = subprocess.run([os.environ.get('NODE_BIN', 'node'), _js5], capture_output=True, text=True, timeout=60)
_g5 = json.loads(_p5.stdout or 'null') if _p5.returncode == 0 else None
_w5 = [_W._search_stem(x) for x in ('4809 NW 45 AVE', '2634 SE 19 CT #205C', '10442 SUNRISE LAKES BLVD 105',
                                    '4500 N STATE ROAD 7', '1234 US HIGHWAY 1', '1723 CANAL DR')]
rec('card "Copy for Whitepages search" stem matches wp_web', _g5 == _w5 and _g5[0] == '4809 NW 45', (_g5, _w5, _p5.stderr[-300:]))
rec('the copy button is wired, never dials, writes no note', 'wpcopy' in _pg and "querySelectorAll('.wpcopy')" in _pg)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
