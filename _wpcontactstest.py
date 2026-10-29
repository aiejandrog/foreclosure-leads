"""_wpcontactstest -- Whitepages owner bar, relatives field, and spend caps.

Run:  python _wpcontactstest.py     (exit 0 = safe; no network, no key, no board, no real data)

What it holds in place:
  1. wp_contacts.name_grade: a Whitepages name is the OWNER's only with two shared name tokens
     including a surname. Shared surname alone, a one-token subset, an entity: not the owner.
  2. Person-layer records: namesakes are dropped; name + at-the-property is 'wp'; name only is 'nm'.
  3. Relatives: only off records that cleared the owner bar, never a number already on the lead,
     never an owner, and the bake keeps them out of phones / emails / the plaintext board.
  4. The board renders relatives call-only (no sms:, WhatsApp, mailto), hides them while the owner
     has a number to try, and hides them outright under any hold.
  5. whitepages_lookup spend: the run cap and the day cap each stop the next request, in-flight
     reservations count, and a request that never reached billing is not charged.
  6. contact_hold: a never-contact case and a stay-flagged lead are not spent on.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='wpct-')
os.environ['DEALFLOW_DIR'] = TMP          # never-contact list lookups land in a scratch dir

import wp_contacts as W                    # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(name)


# ------------------------------------------------------------------ 1. the owner bar
TRUTH = [
    ('GARCIA, JOSE L', 'Jose L Garcia', 'owner'),
    ('PEREZ MARIA', 'Maria Perez', 'owner'),                      # county-roll order
    ('DE LA CRUZ, ANA; LOPEZ, PEDRO', 'Pedro Lopez', 'owner'),    # second owner on the deed
    ('GARCIA, JOSE', 'Maria Garcia', 'kin'),                      # shared surname only
    ('GARCIA', 'Jose Garcia', 'kin'),                             # one-token subset
    ('RODRIGUEZ, JOSE LUIS', 'Jose Luis Martinez', 'kin'),        # given names only
    ('SMITH, JOHN', 'Robert Jones', 'no'),
    ('ABC HOLDINGS LLC', 'Abc Holdings', 'no'),                   # entity
    ('', 'Jose Garcia', 'no'),
    ('(owner via title search)', 'Jose Garcia', 'no'),            # placeholder
]
for own, wp, want in TRUTH:
    got = W.name_grade(own, wp)
    rec('name_grade %r vs %r -> %s' % (own, wp, want), got == want, got)
rec('grade_property_owner: matching deed owner -> wp',
    W.grade_property_owner('GARCIA, JOSE L', 'Jose Garcia') == 'wp')
rec('grade_property_owner: someone else on the parcel -> hh',
    W.grade_property_owner('GARCIA, JOSE L', 'Maria Garcia') == 'hh')

# ------------------------------------------------------------------ 2. Person-layer grading
LEAD_OWN, LEAD_ADDR = 'GARCIA, JOSE L', '1234 SW 8th St, Miami, FL 33135'
AT_PROP = {'name': 'Jose L Garcia', 'phones': [{'number': '(305) 555-0101', 'type': 'Mobile'}],
           'current_addresses': [{'street_line_1': '1234 SW 8TH ST', 'city': 'Miami'}],
           'relatives': [{'name': 'Maria Garcia', 'relation': 'Spouse',
                          'phones': [{'number': '305-555-0202', 'type': 'mobile'},
                                     {'number': '305-555-0101'}]},          # owner's own number
                         {'name': 'Jose Luis Garcia', 'phones': [{'number': '305-555-0303'}]},  # the owner
                         {'name': 'Carlos Garcia'}]}
NAME_ONLY = {'name': 'Jose Garcia', 'phones': [{'number': '786-555-0404'}],
             'historical_addresses': [{'street_line_1': '99 NW 1ST AVE'}]}
NAMESAKE = {'name': 'Maria Garcia', 'phones': [{'number': '786-555-0505'}],
            'relatives': [{'name': 'Stranger Relative', 'phones': [{'number': '786-555-0606'}]}]}
rec('person record at the property -> wp', W.grade_person_record(LEAD_OWN, LEAD_ADDR, AT_PROP) == 'wp')
rec('person record, name only -> nm', W.grade_person_record(LEAD_OWN, LEAD_ADDR, NAME_ONLY) == 'nm')
rec('namesake person record -> dropped', W.grade_person_record(LEAD_OWN, LEAD_ADDR, NAMESAKE) is None)
rec('at_property ignores directionals (SW 8TH ST == 8TH ST)',
    W.at_property({'addresses': ['1234 8TH ST']}, LEAD_ADDR))
rec('at_property: different house number is not the property',
    not W.at_property({'addresses': ['1236 SW 8TH ST']}, LEAD_ADDR))

# ------------------------------------------------------------------ 3. relatives
PRS = [{'name': 'Jose Garcia', 'response': [AT_PROP, NAMESAKE, NAME_ONLY]}]
rels = W.extract_relatives(LEAD_OWN, LEAD_ADDR, PRS, owner_numbers=['3055550101'])
names = [r['name'] for r in rels]
nums = [p['n'] for r in rels for p in r['phones']]
rec('relatives: spouse kept with her number', 'Maria Garcia' in names and '3055550202' in nums, rels)
rec("relatives: owner's own number never relabelled as a relative's", '3055550101' not in nums)
rec('relatives: a relative who is the owner is skipped', 'Jose Luis Garcia' not in names)
rec("relatives: a namesake's relatives are not ours", 'Stranger Relative' not in names)
rec('relatives: name-only entry kept, sorted after the ones with numbers',
    names and names[-1] == 'Carlos Garcia' and rels[-1]['phones'] == [])
rec('relatives: nothing when no record clears the bar',
    W.extract_relatives(LEAD_OWN, LEAD_ADDR, [{'response': [NAMESAKE]}]) == [])

lead_ok = {'case': '2025-111111-CA-01'}
rec('relatives_allowed: clean lead', W.relatives_allowed(lead_ok, lambda c: False))
for k in ('saleBkAct', 'bkWhy', 'sibclaimed', 'lpDismissed', 'ddhold'):
    rec('relatives_allowed: %s strips them' % k, not W.relatives_allowed(dict(lead_ok, **{k: True}), lambda c: False))
rec('relatives_allowed: never-contact strips them', not W.relatives_allowed(lead_ok, lambda c: True))


def _boom(c):
    raise RuntimeError('unreadable')


rec('relatives_allowed: unreadable never-contact list fails closed', not W.relatives_allowed(lead_ok, _boom))

FL = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('bake: plaintext board strips wpRelatives',
    "if k not in ('phones', 'phdnc', 'phsrc', 'emails', 'wpRelatives')" in FL)
rec('bake: relatives are assigned only to r.wpRelatives',
    FL.count("_r['wpRelatives'] = _rels") == 1 and not re.search(r"_ph\.append\([^)]*_rels", FL))
rec('bake: namesake Person records are skipped', bool(re.search(r'if _grade is None:\s*\n\s*continue', FL)))
rec('bake: held leads lose relatives after every hold is set',
    FL.index('relatives gate: stripped') > FL.index('\n    _bk_held, _bk_degraded = stamp_federal_bk(slim)'))

# ------------------------------------------------------------------ 4. the board render
TPL = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()


def extract(src, name):
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s()' % name)
    j, depth = src.index('{', i), 0
    for k in range(j, len(src)):
        depth += {'{': 1, '}': -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]


if shutil.which('node'):
    js = '\n'.join(extract(TPL, n) for n in ('_ownerUnreachable', '_relativesHeld', '_wpRelatives')) + r'''
var notes = {}; var HOLD = '';
function esc(s){ return String(s); }
function _telNum(d){ return 'tel:+1' + d; }
function _textContactBlocked(r){ return HOLD; }
function isFlaggedDead(r){ return false; }
var REL = [{name:'Maria Garcia', rel:'spouse', phones:[{n:'3055550202', type:'mobile'}]}];
var out = {};
out.noOwnerPhone = _wpRelatives({case:'A', phones:['3055550404'], phsrc:['hh'], wpRelatives:REL});
out.ownerPhone   = _wpRelatives({case:'B', phones:['3055550101'], phsrc:['st'], wpRelatives:REL});
out.ownerDncOnly = _wpRelatives({case:'C', phones:['3055550101'], phsrc:['wp'], phdnc:[true], wpRelatives:REL});
notes.D = {touches:[{d:'2026-09-20', ch:'call', out:'No answer'}, {d:'2026-09-21', ch:'call', out:'Voicemail'},
                    {d:'2026-09-21', ch:'call', out:'Wrong number'}]};
out.threeMisses  = _wpRelatives({case:'D', phones:['3055550101'], phsrc:['st'], wpRelatives:REL});
notes.E = {touches:notes.D.touches.concat([{d:'2026-09-22', ch:'call', out:'Talked'}])};
out.talked       = _wpRelatives({case:'E', phones:['3055550101'], phsrc:['st'], wpRelatives:REL});
HOLD = 'bk';
out.held         = _wpRelatives({case:'F', phones:[], wpRelatives:REL});
HOLD = '';
out.ddhold       = _wpRelatives({case:'G', phones:[], ddhold:true, wpRelatives:REL});
console.log(JSON.stringify(out));
'''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
    r = subprocess.run(['node', f.name], capture_output=True, text=True, timeout=60)
    os.unlink(f.name)
    if r.returncode:
        rec('board render runs under node', False, r.stderr[:500])
    else:
        o = json.loads(r.stdout)
        rec('board: shown when the owner has no owner-grade number', 'tel:+13055550202' in o['noOwnerPhone'])
        rec('board: call only (no sms:, WhatsApp or mailto)',
            not re.search(r'sms:|wa\.me|whatsapp|mailto:', o['noOwnerPhone'], re.I))
        rec('board: tells the caller not to mention the foreclosure', 'Do not mention the foreclosure' in o['noOwnerPhone'])
        rec('board: hidden (count only) while the owner has a number', 'tel:' not in o['ownerPhone']
            and '1 relative on file' in o['ownerPhone'])
        rec('board: a DNC owner number still counts as reachable (manual dial)', 'tel:' not in o['ownerDncOnly'])
        rec('board: 3 misses over 2 days unlocks them', 'tel:+13055550202' in o['threeMisses'])
        rec('board: a conversation hides them again', 'tel:' not in o['talked'])
        rec('board: any contact hold hides them entirely', o['held'] == '' and o['ddhold'] == '')
else:
    rec('board render (node missing: SKIPPED, not passed)', True)

rec('board: textablePhones never reads wpRelatives', 'wpRelatives' not in extract(TPL, 'textablePhones'))

# ------------------------------------------------------------------ 5. spend caps
import bd_budget                          # noqa: E402
bd_budget.LEDGER = os.path.join(TMP, 'batchdata_spend.json')
import whitepages_lookup as L             # noqa: E402

L._run.update({'spent': 0.0, 'calls': 0, 'inflight': 0.0, 'max': 0.50})
L.WP_DAILY_CAP = 100.0


def try_reserve():
    try:
        with L._spend_lock:
            L._require_wp(L.WP_EST_COST)
        return True
    except bd_budget.BudgetExhausted:
        return False


rec('run cap: first request fits', try_reserve())
L._charge_wp('wp')
rec('run cap: second request fits ($0.44 <= $0.50)', try_reserve())
rec('run cap: an in-flight reservation counts (third refused)', not try_reserve())
L._release_wp()
rec('run cap: a released (unbilled) request is not charged', L._run['spent'] == L.WP_EST_COST, L._run)
L._charge_wp('wp-miss')
rec('run cap: $0.44 spent, next $0.22 refused', not try_reserve())
rec('ledger: charges land under wp notes', abs(L.wp_spent_today() - 2 * L.WP_EST_COST) < 1e-9, L.wp_spent_today())
L._run.update({'spent': 0.0, 'inflight': 0.0, 'max': 100.0})
L.WP_DAILY_CAP = 0.50
rec('day cap: $0.44 already spent today, next $0.22 refused', not try_reserve())
L.WP_DAILY_CAP = 5.0
bd_budget.charge(3.00, 'skiptrace-tracerfy')
rec('day cap: Tracerfy spend does not count against Whitepages', try_reserve())
L._release_wp()

# ------------------------------------------------------------------ 6. contact holds
rec('contact_hold: never-contact case is not spent on', L.contact_hold({'Case #': '2025-000201-CA-01'}) != '')
rec('contact_hold: stay-flagged lead is not spent on', L.contact_hold({'Case #': '2025-111111-CA-01', 'sale_bk_active': True}) != '')
rec('contact_hold: clean lead is spent on', L.contact_hold({'Case #': '2025-111111-CA-01'}) == '')
rec('--pick-miami skips held leads and companies',
    [L._lead_key(r) for r in L.pick_miami([
        {'Case #': '2025-000201-CA-01', 'Address': '1 A ST, Miami', 'owners': 'DOE, JOHN'},
        {'Case #': '2025-222222-CA-01', 'Address': '2 B ST, Miami', 'owners': 'ACME HOLDINGS LLC'},
        {'Case #': '2025-333333-CA-01', 'Address': '3 C ST, Miami', 'owners': 'ROE, JANE', 'days': 5},
        {'Case #': 'CACE-25-000001', 'Address': '4 D ST, Hollywood', 'owners': 'POE, AL', 'county': 'BROWARD'},
    ], {}, 5)] == ['2025-333333-CA-01'])

# ------------------------------------------------------------------ 7. nightly deep-search picker
import skiptrace as _ST                              # noqa: E402
_ST._EXCLUSION_OVERRIDE = _ST._empty_index()        # no files: every lead passes the shared gates
_pool = [
    {'Case #': '2025-000201-CA-01', 'Address': '1 A ST, Miami', 'owners': 'DOE, JOHN', 'days': 3},   # never-contact
    {'Case #': '2025-222222-CA-01', 'Address': '2 B ST, Miami', 'owners': 'ACME HOLDINGS LLC', 'days': 3},
    {'Case #': '2025-444444-CA-01', 'Address': '4 C ST, Miami', 'owners': 'ROE, JANE', 'days': 10},
    {'Case #': '2025-555555-CA-01', 'Address': '5 C ST, Miami', 'owners': 'ROE, JIM', 'days': 9},
    {'Case #': 'CACE-25-000002', 'Address': '6 D ST, Hollywood', 'owners': 'POE, AL', 'county': 'BROWARD', 'days': 12},
    {'Case #': '2025-666666-CA-01', 'Address': '7 E ST, Miami', 'owners': 'LOE, ANN', 'days': 200},   # too far out
    {'Case #': '2025-777777-CA-01', 'Address': '8 F ST, Miami', 'owners': 'MOE, BO', 'days': -2},     # sale passed
    {'Case #': '2025-888888-CA-01', 'Address': '9 G ST, Miami', 'owners': 'NOE, CY'},                 # undated LP
    {'Case #': '2025-999999-CA-01', 'Address': '', 'owners': 'OOE, DI', 'days': 4},                   # no address
    {'Case #': '2025-121212-CA-01', 'Address': '10 H ST, Miami', 'owners': 'QOE, EL', 'days': 2},    # already searched
]
_st = {'2025-555555-CA-01': {'phones': [{'number': '305555%04d' % i} for i in range(8)]}}
_picked = [L._lead_key(r) for r in L.pick_callable(_pool, {'2025-121212-CA-01': {'_person': [{'name': 'x'}]}}, 10, _st)]
rec('--pick-callable: holds, companies, no-address, passed, far-out and already-searched leads are skipped',
    set(_picked) == {'2025-444444-CA-01', '2025-555555-CA-01', 'CACE-25-000002', '2025-888888-CA-01'}, _picked)
rec('--pick-callable: any county, not only Miami', 'CACE-25-000002' in _picked)
rec('--pick-callable: same week of sale -> the lead with fewer phones first',
    _picked.index('2025-444444-CA-01') < _picked.index('2025-555555-CA-01'), _picked)
rec('--pick-callable: an undated lis pendens comes after every dated sale', _picked[-1] == '2025-888888-CA-01', _picked)
rec('--pick-callable: n caps the list', len(L.pick_callable(_pool, {}, 2, _st)) == 2)
_dnc = _ST._empty_index()
_dnc['dnc_cases'] = {'2025-444444-ca-01'}
_ST._EXCLUSION_OVERRIDE = _dnc
rec('--pick-callable: an opted-out / Do Not Contact lead is not bought (skiptrace gates)',
    '2025-444444-CA-01' not in [L._lead_key(r) for r in L.pick_callable(_pool, {}, 10, _st)])
_bad = _ST._empty_index()
_bad['dnc_unreadable'] = True
_ST._EXCLUSION_OVERRIDE = _bad
rec('--pick-callable: an unreadable opt-out ledger buys nothing', L.pick_callable(_pool, {}, 10, _st) == [])

# 7b. --pick-best: a bought block of searches (2026-10-29). Same gates, any sale within a year.
_ST._EXCLUSION_OVERRIDE = _ST._empty_index()
_bp = [
    {'Case #': '2025-310001-CA-01', 'Address': '1 A ST, Miami', 'owners': 'ROE, AMY', 'days': 5},     # has numbers
    {'Case #': '2025-310002-CA-01', 'Address': '2 A ST, Miami', 'owners': 'ROE, BEN', 'days': 40},    # trace miss
    {'Case #': '2025-310003-CA-01', 'Address': '3 A ST, Miami', 'owners': 'ROE, CAL', 'days': 9},     # never traced
    {'Case #': '2025-310004-CA-01', 'Address': '4 A ST, Miami', 'owners': 'ROE, DEE', 'days': 30},    # never traced, equity
    {'Case #': '2025-310005-CA-01', 'Address': '5 A ST, Miami', 'owners': 'ROE, EVE', 'days': 300},   # far out, traced miss
    {'Case #': '2025-310006-CA-01', 'Address': '6 A ST, Miami', 'owners': 'ROE, FAY', 'days': 400},   # past a year
    {'Case #': '2025-310007-CA-01', 'Address': '7 A ST, Miami', 'owners': 'ROE, GUS', 'days': 7},     # trace errored
    {'Case #': '2025-000201-CA-01', 'Address': '8 A ST, Miami', 'owners': 'DOE, JOHN', 'days': 3},    # never-contact
]
_bst = {'2025-310001-CA-01': {'phones': [{'number': '3055550001'}]},
        '2025-310002-CA-01': {'phones': []},
        '2025-310005-CA-01': {'phones': []},
        '2025-310007-CA-01': {'phones': [], 'outcome': 'error'}}
_best = [L._lead_key(r) for r in L.pick_best(_bp, {}, 20, _bst, equity={'2025-310004-CA-01': True})]
rec('--pick-best: skip-trace misses first, then no-number leads (verified equity first), then leads with numbers',
    _best == ['2025-310002-CA-01', '2025-310005-CA-01', '2025-310004-CA-01', '2025-310007-CA-01',
              '2025-310003-CA-01', '2025-310001-CA-01'], _best)
rec('--pick-best: a trace that errored is not a miss (it ranks as a no-number lead)',
    _best.index('2025-310007-CA-01') > _best.index('2025-310005-CA-01'))
rec('--pick-best: never-contact and past-a-year leads are not bought',
    '2025-000201-CA-01' not in _best and '2025-310006-CA-01' not in _best, _best)
rec('--pick-best: n caps the list', len(L.pick_best(_bp, {}, 2, _bst, equity={})) == 2)
_cached = {'2025-310002-CA-01': {'result': {'ownership_info': {}}, '_http': 200},
           '2025-310003-CA-01': {'result': None, '_http': 404},
           '2025-310004-CA-01': {'_prop_id': 'pro-abcd1234'}}                 # a bare id stamp is not a lookup
_bc = [L._lead_key(r) for r in L.pick_best(_bp, _cached, 20, _bst, equity={})]
rec('--pick-best: a lead already looked up (Property hit or 404) is not bought again; a bare id stamp is',
    '2025-310002-CA-01' not in _bc and '2025-310003-CA-01' not in _bc and '2025-310004-CA-01' in _bc, _bc)
_bst2 = dict(_bst, **{'2025-310004-CA-01': {'phones': []}})
_bp2 = [dict(r, phones=['3055550044']) if r['Case #'] == '2025-310004-CA-01' else r for r in _bp]
_b2 = [L._lead_key(r) for r in L.pick_best(_bp2, {}, 20, _bst2, equity={})]
rec('--pick-best: a trace miss whose county file has a number is not in the first tier',
    _b2.index('2025-310004-CA-01') > _b2.index('2025-310003-CA-01'), _b2)
_ST._EXCLUSION_OVERRIDE = _bad
rec('--pick-best: an unreadable opt-out ledger buys nothing', L.pick_best(_bp, {}, 20, _bst, equity={}) == [])
_ST._EXCLUSION_OVERRIDE = None
rec('--pick-best: an unreadable board twin orders without equity, buys normally',
    L._board_equity(os.path.join(HERE, 'no-such-board.html')) == {})

# WP_PRICE: a plan bought at another price reads in real dollars
import subprocess  # noqa: E402
_pr = subprocess.run([sys.executable, '-c', 'import whitepages_lookup as L; print(L.WP_EST_COST)'],
                     cwd=HERE, env=dict(os.environ, WP_PRICE='0.20'), capture_output=True, text=True)
rec('WP_PRICE sets the per-search price', _pr.stdout.strip().endswith('0.2'), _pr.stdout + _pr.stderr)
_pr0 = subprocess.run([sys.executable, '-c', 'import whitepages_lookup as L; print(L.WP_EST_COST)'],
                      cwd=HERE, env=dict(os.environ, WP_PRICE='0'), capture_output=True, text=True)
rec('WP_PRICE=0 cannot switch the caps off (falls back to 0.22)', _pr0.stdout.strip().endswith('0.22'),
    _pr0.stdout + _pr0.stderr)
rec('the .tmp cache copy is gitignored', 'whitepages_lookup.json.tmp' in open(os.path.join(HERE, '.gitignore'),
                                                                        encoding='utf-8').read())

# the cache is written then renamed, so a bake reading it mid-run never sees half a file
_src = open(os.path.join(HERE, 'whitepages_lookup.py'), encoding='utf-8').read()
_sv = _src[_src.index('def _save_cache'):_src.index('def _count_phones_on_property')]
rec('cache write is atomic (tmp + os.replace)', 'os.replace(' in _sv and ".tmp'" in _sv)

# the nightly line must sit where the batch can reach it: after the :rebuild label, which both the
# tracerfy and no-tracerfy paths arrive at. On 10-22 it first landed between `goto :rebuild` and
# `:phones`, where no path ever runs it.
_bat = [ln.strip() for ln in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               'refresh-dealflow.bat'), encoding='utf-8')]
# held (commented) until Alex picks; the placement still matters for the day it is switched on
_wl = next((i for i, ln in enumerate(_bat) if 'whitepages_lookup.py --pick-callable' in ln
            and ln.lower().lstrip('rem ').startswith('if exist')), None)
rec('refresh-dealflow.bat: the nightly Whitepages line is HELD (commented) until Alex picks',
    _wl is not None and _bat[_wl].lower().startswith('rem'))
_rb = _bat.index(':rebuild') if ':rebuild' in _bat else None
rec('refresh-dealflow.bat: the Whitepages line exists and comes after the :rebuild label',
    _wl is not None and _rb is not None and _wl > _rb, (_wl, _rb))
rec('refresh-dealflow.bat: no label or goto between :rebuild and the Whitepages line',
    _wl is not None and _rb is not None
    and not any(ln.startswith(':') or ln.lower().startswith('goto') for ln in _bat[_rb + 1:_wl]))

# "LAST,FIRST" with no space after the comma (the Broward roll's form) and a hyphenated surname:
# the comma used to be deleted, gluing "ADEONOJOBIGINA", so the owner never cleared the bar (10-22).
rec('roll "LAST-LAST,FIRST" (no space) clears the owner bar',
    W.name_grade('MCKNIGHT-ADEONOJOBI,GINA', 'Gina Mcknight') == 'owner'
    and W.name_grade('MCKNIGHT-ADEONOJOBI,GINA', 'Gina L Mcknight-Adeonojobi') == 'owner'
    and W.name_grade('SMITH,JOHN', 'John Smith') == 'owner')
rec('a shortened surname is still only kin, a stranger still no',
    W.name_grade('MCKNIGHT-ADEONOJOBI,GINA', 'Gina L Onojobi') == 'kin'
    and W.name_grade('MCKNIGHT-ADEONOJOBI,GINA', 'Z Victor') == 'no')

rec('likely owner: same first name + a 5+ letter surname piece inside ours (10-22)',
    W.likely_owner('MCKNIGHT-ADEONOJOBI,GINA', 'Gina L Onojobi')
    and W.likely_owner('MCKNIGHT-ADEONOJOBI, GINA', 'Gina Onojobi')
    and W.likely_owner('PEREZ, ANA & ROSARIOGOMEZ, LUIS', 'Luis Rosario'))
rec('likely owner refuses: owner already, short piece, other first name, entity, empty',
    not W.likely_owner('MCKNIGHT-ADEONOJOBI,GINA', 'Gina Mcknight')
    and not W.likely_owner('MCKNIGHT-ADEONOJOBI,GINA', 'Gina Jobi')
    and not W.likely_owner('MCKNIGHT-ADEONOJOBI,GINA', 'Tina Onojobi')
    and not W.likely_owner('ONOJOBI HOLDINGS LLC', 'Gina Onojobi')
    and not W.likely_owner('MCKNIGHT-ADEONOJOBI,GINA', '')
    and not W.likely_owner('', 'Gina Onojobi'))
rec('likely owner: a shared middle name is not a surname piece',
    not W.likely_owner('GARCIA, MARIA ISABEL', 'MARIA ISABEL LOPEZ')
    and not W.likely_owner('SMITH, MARY ELIZABETH', 'Mary Elizabeth Jones')
    and not W.likely_owner('GARCIA MARIA ISABEL', 'Maria Isabel Lopez')
    and W.likely_owner('ADEONOJOBI GINA MARIE', 'Gina Onojobi'))
rec('likely owner never changes the owner bar or the property tag',
    W.name_grade('MCKNIGHT-ADEONOJOBI,GINA', 'Gina L Onojobi') == 'kin'
    and W.grade_property_owner('MCKNIGHT-ADEONOJOBI,GINA', 'Gina L Onojobi') == 'hh')

shutil.rmtree(TMP, ignore_errors=True)
print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
