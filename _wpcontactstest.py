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
rec('bake: namesake Person records are skipped', 'if _grade is None:\n                            continue' in FL)
rec('bake: held leads lose relatives after every hold is set',
    FL.index('relatives gate: stripped') > FL.index("federal bankruptcy lookup: %d lead(s) held"))

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

shutil.rmtree(TMP, ignore_errors=True)
print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
