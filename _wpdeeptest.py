#!/usr/bin/env python
"""_wpdeeptest -- Whitepages numbers reach Call Mode in the right order, and the wrong parcel's don't.
Fixtures are fake (555 numbers, made-up names). No network, no key, no board.

Run:  python _wpdeeptest.py    (exit 0 = pass)

Pins five things found in the 2026-10-22 audit of the Whitepages path:
  1. phone_src.fit_phones: past MAX_PHONES the cut used to fall on Whitepages' numbers only (they
     are appended last). Owner numbers now outrank the skip trace's tail; household and name-only
     do not; DNC goes first.
  2. wp_contacts.parcel_owners: what the APN gate lets through for 'ok' / 'mismatch' / 'unverified'.
  3. wp_contacts address reading: Whitepages' dicts carry `street` as a NAME only; 'SW 8 ST' and
     'SW 8th St' are one street.
  4. call_mode.call_rows: after the best number, the rest follow phrank, not merge order.
  5. whitepages_lookup: a different parcel's owner names are never searched or paid for.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('DEALFLOW_BK_ALLOW_UNCHECKED', '0')

fails = []


def chk(name, cond, extra=''):
    print(('ok   ' if cond else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if extra and not cond else ''))
    if not cond:
        fails.append(name)


# --- 1. fit_phones --------------------------------------------------------------------------------
import phone_src as PS  # noqa: E402


def ent(n, src='st', dnc=False):
    return {'n': '30555500%02d' % n, 'type': '', 'dnc': dnc, 'src': src}


ten_st = [ent(i) for i in range(10)]
wp2 = [ent(20, 'wp'), ent(21, 'wp')]
out = PS.fit_phones(ten_st + wp2, cap=10)
nums = [e['n'] for e in out]
chk('fit: never more than cap', len(out) == 10)
chk('fit: both Whitepages owner numbers survive a full skip-trace list',
    ent(20)['n'] in nums and ent(21)['n'] in nums, nums)
chk('fit: the skip trace loses its TAIL, not its head',
    ent(0)['n'] in nums and ent(1)['n'] in nums and ent(9)['n'] not in nums and ent(8)['n'] not in nums, nums)
chk('fit: survivors keep merge order', nums == sorted(nums, key=lambda n: [e['n'] for e in ten_st + wp2].index(n)))
out = PS.fit_phones(ten_st + [ent(30, 'hh'), ent(31, 'nm')], cap=10)
chk('fit: household / name-only do not push out the owner trace', [e['n'] for e in out] == [e['n'] for e in ten_st])
mixed = [ent(0, dnc=True)] + [ent(i) for i in range(1, 10)] + [ent(30, 'hh')]
out = PS.fit_phones(mixed, cap=10)
chk('fit: a DNC-flagged number is the first to go', ent(0)['n'] not in [e['n'] for e in out]
    and ent(30)['n'] in [e['n'] for e in out])
chk('fit: under the cap nothing changes', PS.fit_phones(ten_st[:4] + wp2, cap=10) == ten_st[:4] + wp2)
out = PS.fit_phones([ent(i, 'wp') for i in range(12)], cap=10)
chk('fit: all-owner overflow still cuts the tail', [e['n'] for e in out] == [ent(i)['n'] for i in range(10)])

# --- 2. parcel_owners -----------------------------------------------------------------------------
import wp_contacts as W  # noqa: E402

LEAD_OWN, LEAD_ADDR = 'DOE, JANE MARIE', '123 SW 8 ST, Miami, FL 33130'
RES = {'apn': '01-0000-000-0001',
       'property_address': {'line1': '123 SW 8th St', 'house': '123', 'street': '8th', 'city': 'Miami'},
       'ownership_info': {'person_owners': [{'name': 'Jane M Doe', 'phones': [{'number': '3055550101'}]},
                                            {'name': 'Robert Stranger', 'phones': [{'number': '3055550102'}]}]}}
chk('parcel: ok -> every person owner', len(W.parcel_owners('ok', LEAD_OWN, LEAD_ADDR, RES)) == 2)
chk('parcel: mismatch -> nobody', W.parcel_owners('mismatch', LEAD_OWN, LEAD_ADDR, RES) == [])
u = W.parcel_owners('unverified', LEAD_OWN, LEAD_ADDR, RES)
chk('parcel: unverified, same street -> only the name-matched owner',
    [o['name'] for o in u] == ['Jane M Doe'], u)
chk('parcel: unverified, different street -> nobody',
    W.parcel_owners('unverified', LEAD_OWN, '125 SW 9 ST, Miami, FL', RES) == [])
chk('parcel: unverified, lead has no street -> nobody',
    W.parcel_owners('unverified', LEAD_OWN, '', RES) == [])
chk('verdict: apn digits vs folio digits', W.wp_parcel_verdict('01-0000-000-0001', '0100000000001') == 'ok'
    and W.wp_parcel_verdict('01-0000-000-0001', '0100000000002') == 'mismatch'
    and W.wp_parcel_verdict('01-0000-000-0001', '') == 'unverified')

# --- 3. address reading ---------------------------------------------------------------------------
chk("street: '8 ST' == '8th St'", W._street_key('123 SW 8 ST') == W._street_key('123 SW 8th St'))
chk('street: different house number differs', W._street_key('123 SW 8 ST') != W._street_key('125 SW 8 ST'))
person_rec = {'name': 'Jane M Doe',
              'current_addresses': [{'house': '123', 'street': '8th', 'line1': '123 SW 8th St',
                                     'city': 'Miami'}]}
chk('at_property reads line1 when `street` is a name only', W.at_property(person_rec, LEAD_ADDR))
chk('grade: name + our address -> wp', W.grade_person_record(LEAD_OWN, LEAD_ADDR, person_rec) == 'wp')
person_rec2 = {'name': 'Jane M Doe', 'current_addresses': [{'house': '123', 'street': '8th'}]}
chk('at_property rebuilds house + street when only parts exist', W.at_property(person_rec2, LEAD_ADDR))

# --- 4. Call Mode order ---------------------------------------------------------------------------
import call_mode as CM  # noqa: E402

lead = {'case': '2099-000777-CA-01', 'county': 'MIAMI-DADE', 'st': 'FC', 'stage': 'FC',
        'owners': 'DOE, JANE', 'oname': 'Jane Doe', 'addr': '123 SW 8 ST, Miami, FL 33130',
        'folio': '0100000000001', 'mail': '123 SW 8 ST, Miami, FL 33130', 'value': 400000,
        'judg': 100000, 'eq': 60, 'eqfake': False, 'days': 20, 'auction': '2099-01-20',
        # merge order: trace landline, trace landline, household mobile, Whitepages owner mobile
        'phones': ['3055550201', '3055550202', '3055550203', '3055550204'],
        'phdnc': [False, False, False, False], 'phtype': ['landline', 'landline', 'mobile', 'mobile'],
        'phsrc': ['st', 'st', 'hh', 'wp'],
        'phrank': ['last resort', 'last resort', 'last resort', 'ok'], 'phbest': 0,
        'emails': [], 'vac': False, 'warn': '', 'ctype': 'Bank/Mortgage', 'ftype': 'MORTGAGE'}
rows, _n = CM.call_rows([lead], max_days=60)
if not rows:
    chk('call_rows ships the fixture lead', False, 'no rows')
else:
    p = rows[0]['p']
    chk('call: the ranked-best number still leads', p[0] == '3055550201', p)
    chk("call: a Whitepages owner 'ok' comes before the trace's 'last resort' numbers",
        p.index('3055550204') < p.index('3055550202'), p)
    chk('call: same label keeps merge order (trace before household)',
        p.index('3055550202') < p.index('3055550203'), p)
    chk("call: 'ps' stays aligned with 'p'", rows[0].get('ps') is None or
        rows[0]['ps'][p.index('3055550203')] == 'hh', rows[0].get('ps'))

# --- 5. lookup side: a different parcel's owners are never searched -------------------------------
import whitepages_lookup as WL  # noqa: E402

WL.THROTTLE_S = 0
searched = []
WL.person_lookup = lambda name, city, state, key: (searched.append(name), [])[1]
L5 = {'Case #': '2099-000888-CA-01', 'owners': 'DOE, JANE', 'Address': '123 SW 8 ST, Miami',
      'Folio': '0100000000001'}
wrong = {'result': {'apn': '01-0000-000-0099',
                    'ownership_info': {'person_owners': [{'name': 'Robert Stranger', 'phones': []}]}}}
_recs, _add, ran = WL._run_person_layer(L5, 'Miami', wrong, {}, 'k' * 24, deep=False)
chk('lookup: a wrong-parcel record does not suppress the Person fallback', ran)
chk("lookup: the search is our owner's name, never the stranger's",
    searched and all('STRANGER' not in n.upper() for n in searched) and 'JANE' in searched[0].upper(), searched)
searched.clear()
right = {'result': {'apn': '01-0000-000-0001',
                    'ownership_info': {'person_owners': [{'name': 'Jane Doe', 'phones': []}]}}}
_r, _a, ran2 = WL._run_person_layer(L5, 'Miami', right, {}, 'k' * 24, deep=False)
chk('lookup: a matching record with owners still skips the paid Person call', not ran2 and not searched)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
