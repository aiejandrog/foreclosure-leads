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

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
