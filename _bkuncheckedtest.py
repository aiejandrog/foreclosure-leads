"""_bkuncheckedtest — the owner's waiver for UNCHECKED Broward / Palm Beach leads (2026-10-13).

Run:  python _bkuncheckedtest.py

DEALFLOW_BK_ALLOW_UNCHECKED=1 (or the tracked bk_waiver.json) releases a Broward / Palm Beach
lead whose federal check has not run, errored, gone stale, or is an unconfirmed CourtListener
clear, on the dial queue (federal_hold), the board bake (flags_for_cases, with ask:true), and
the send gate (stay_gate.check). It never releases an open or possible match, a PACER hit, a
never-contact case, or a Miami-Dade stem's docket verdict. No network, no real names.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='bkunchecked_'))

for _k in ('COURTLISTENER_TOKEN', 'DEALFLOW_BK_PROVIDER', 'DEALFLOW_BK_MAX_AGE_DAYS', 'DEALFLOW_DIR',
           'DEALFLOW_BK_CACHE', 'DEALFLOW_BK_FILINGS', 'DEALFLOW_BK_OVERRIDES', 'DEALFLOW_BK_STATUS',
           'DEALFLOW_BK_BUDGET', 'DEALFLOW_BK_PULL_STATE', 'DEALFLOW_BK_ALLOW_CL_CLEAR',
           'DEALFLOW_BK_ALLOW_UNCHECKED', 'DEALFLOW_CLERK_BK'):
    os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP / 'boot')

import bk_lookup as BL
import stay_gate as SG

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:300]) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


def isolate(name):
    d = TMP / name
    d.mkdir(parents=True, exist_ok=True)
    os.environ['DEALFLOW_DIR'] = str(d)
    for k, f in (('DEALFLOW_BK_CACHE', 'bk_lead_cache.json'), ('DEALFLOW_BK_OVERRIDES', 'bk_overrides.json'),
                 ('DEALFLOW_BK_STATUS', 'bk_lookup_status.json'), ('DEALFLOW_BK_BUDGET', 'bk_budget.json'),
                 ('DEALFLOW_BK_FILINGS', 'bk_filings.json'), ('DEALFLOW_BK_PULL_STATE', 'bk_pull_state.json')):
        os.environ[k] = str(d / f)
    BL.PACER_ROOT = str(d)          # pacer_stay_cache.json / pacer_newfiler_hits.json live here too
    BL._HOLD_MEMO = None
    return d


def waive(on):
    # the repo ships bk_waiver.json, so "off" is the explicit 0 switch, not an unset env
    os.environ['DEALFLOW_BK_ALLOW_UNCHECKED'] = '1' if on else '0'
    BL._HOLD_MEMO = None


NOW = time.time()
BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ACTIVE, BR_POSSIBLE, BR_ERR = (
    'CACE-99-777701', 'CACE-99-777702', 'CACE-99-777703', 'CACE-99-777704', 'CACE-99-777705',
    'CACE-99-777706', 'CACE-99-777707')
PB_NONE = '502099CA007771XXXXMB'
MIAMI = '2099-007771-CA-01'
NEVER = 'CACE-99-777799'
OPEN_EXACT = {'no': '26-77705', 'court': 'flsb', 'open': True, 'match': 'exact', 'filed': '2026-09-01'}
OPEN_PLAUS = {'no': '26-77706', 'court': 'flsb', 'open': True, 'match': 'plausible', 'filed': '2026-09-01'}


def ent(**kw):
    e = {'verdict': 'unavailable', 'why': '', 'searched': False, 'ok_check': False, 'err': '', 't': NOW,
         'cases': [], 'src': 'courtlistener'}
    e.update(kw)
    return e


def seed(d):
    BL._dump(BL.cache_path(), {
        BR_NOTRUN: ent(),
        BR_STALE: ent(verdict='clear', searched=True, ok_check=True, t=NOW - 400 * 86400),
        BR_UNCONF: ent(verdict='clear', searched=True, ok_check=True),
        BR_ACTIVE: ent(verdict='active', searched=True, ok_check=True, cases=[OPEN_EXACT]),
        BR_POSSIBLE: ent(verdict='possible', searched=True, ok_check=True, cases=[OPEN_PLAUS]),
        BR_ERR: ent(err='HTTP 500'),
    })
    (d / 'never_contact.json').write_text(json.dumps([NEVER]), encoding='utf-8')
    (d / 'sale_history_cache.json').write_text(
        json.dumps({'2099-007771-CA-01': {'a': True, 'bd': '2026-09-01', 'sl': ''}}), encoding='utf-8')
    BL._HOLD_MEMO = None


# ------------------------------------------------------------------------------- flag off: unchanged
print('-- waiver off')
d = isolate('off')
seed(d)
waive(False)
sh = str(d / 'sale_history_cache.json')
check('unchecked_allowed is false with no env and no waiver file',
      BL.unchecked_allowed(env={}, here=str(d)) is False)
for case in (BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ACTIVE, BR_POSSIBLE, BR_ERR, PB_NONE, NEVER):
    h, w = BL.federal_hold(case)
    check('off: dial queue holds %s' % case, h is True, (h, w))
    check('off: send gate holds %s' % case, SG.check(case, sh)['ok'] is False)
flags = BL.flags_for_cases([BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ACTIVE, BR_POSSIBLE, BR_ERR, PB_NONE])
check('off: board holds every one of them', all(flags.get(c, {}).get('hold') is True for c in flags) and len(flags) == 8, flags)

# -------------------------------------------------------------------------------- flag on: release
print('-- waiver on (env)')
waive(True)
try:
    for case in (BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ERR, PB_NONE):
        h, w = BL.federal_hold(case)
        check('on: dial queue releases %s' % case, h is False, (h, w))
        v = SG.check(case, sh)
        check('on: send gate releases %s with the ask-first reason' % case,
              v['ok'] is True and v['code'] == SG.CLEAR and 'Ask first' in v['why'], v)
    for case, label in ((BR_ACTIVE, 'open exact match'), (BR_POSSIBLE, 'possible match'), (NEVER, 'never-contact')):
        h, w = BL.federal_hold(case)
        check('on: dial queue still holds a %s' % label, h is True, (h, w))
        check('on: send gate still holds a %s' % label, SG.check(case, sh)['ok'] is False)
    m = SG.check(MIAMI, sh)
    check('on: a Miami docket stay is untouched', m['ok'] is False and m['code'] == SG.STAY_ACTIVE, m)
    check('on: a Miami stem is not the waiver\'s to decide',
          BL._unchecked_release(MIAMI, None, NOW) is None)
    flags = BL.flags_for_cases([BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ERR, PB_NONE, BR_ACTIVE, BR_POSSIBLE])
    rel = [BR_NONE, BR_NOTRUN, BR_STALE, BR_UNCONF, BR_ERR, PB_NONE]
    check('on: board marks every released lead ask:true and not held',
          all(flags.get(c, {}).get('hold') is False and flags[c].get('ask') is True for c in rel), flags)
    check('on: board still holds the open match (hard) and the possible match',
          flags.get(BR_ACTIVE, {}).get('hold') is True and flags[BR_ACTIVE].get('hard') is True
          and flags.get(BR_POSSIBLE, {}).get('hold') is True and flags[BR_POSSIBLE].get('ask') is not True, flags)
    check('on: every release carries the ask-first wording',
          all('Ask first' in flags[c]['why'] for c in rel), flags)
    # PACER evidence on a Broward key: the waiver is for leads NOBODY looked at. A production
    # new-filer hit, a PACER active entry, or a torn PACER file holds every path (review of #234).
    def _all_paths(case):
        BL._HOLD_MEMO = None
        return (SG.check(case, sh)['ok'], BL.federal_hold(case)[0],
                BL.flags_for_cases([case]).get(case, {}).get('hold'))
    (d / 'pacer_newfiler_hits.json').write_text(json.dumps({
        BR_NONE: {'env': 'prod', 'cases': [{'no': '26-77701', 'filed': '2026-10-01', 'court': 'flsb',
                                            'open': True, 'match': 'exact'}], 'kind': 'exact', 't': NOW}}),
        encoding='utf-8')
    r = _all_paths(BR_NONE)
    check('on: a PACER new-filer hit still holds the send gate, the dial queue and the board', r == (False, True, True), r)
    os.remove(d / 'pacer_newfiler_hits.json')
    (d / 'pacer_stay_cache.json').write_text(json.dumps({
        BR_NONE: {'env': 'prod', 'verdict': 'active', 'a': True, 't': NOW, 'bd': '2026-09-01',
                  'cases': [{'no': '26-77701', 'open': True}]}}), encoding='utf-8')
    r = _all_paths(BR_NONE)
    check('on: a PACER active entry still holds the send gate, the dial queue and the board', r == (False, True, True), r)
    (d / 'pacer_stay_cache.json').write_text('{bad', encoding='utf-8')
    r = _all_paths(BR_NONE)
    check('on: a torn PACER cache is not "unchecked": dial queue and board hold', r[1] is True and r[2] is True, r)
    os.remove(d / 'pacer_stay_cache.json')
    check('on: a never-contact case is held on the board bake too',
          BL.flags_for_cases([NEVER]).get(NEVER, {}).get('hold') is True)
    # clerk flag on: a Palm Beach key's clerk hold replaces the ask entry, not the other way round
    os.environ['DEALFLOW_CLERK_BK'] = '1'
    try:
        BL._HOLD_MEMO = None
        f = BL.flags_for_cases([PB_NONE]).get(PB_NONE, {})
        check('on: with DEALFLOW_CLERK_BK=1 the clerk hold still wins on the board', f.get('hold') is True and f.get('ask') is not True, f)
    finally:
        os.environ.pop('DEALFLOW_CLERK_BK', None)
        BL._HOLD_MEMO = None
    # unreadable cache: still held
    (d / 'bk_lead_cache.json').write_text('{not json', encoding='utf-8')
    BL._HOLD_MEMO = None
    h, w = BL.federal_hold(BR_NONE)
    check('on: an unreadable cache still holds the dial queue', h is True, (h, w))
    seed(d)
    # the off switch wins over the file
    (d / 'bk_waiver.json').write_text(json.dumps({'unchecked_contact': True}), encoding='utf-8')
    check('waiver file turns it on', BL.unchecked_allowed(env={}, here=str(d)) is True)
    check('DEALFLOW_BK_ALLOW_UNCHECKED=0 wins over the file',
          BL.unchecked_allowed(env={'DEALFLOW_BK_ALLOW_UNCHECKED': '0'}, here=str(d)) is False)
    (d / 'bk_waiver.json').write_text(json.dumps({'unchecked_contact': 'yes'}), encoding='utf-8')
    check('anything but a literal true in the file is not a release', BL.unchecked_allowed(env={}, here=str(d)) is False)
    (d / 'bk_waiver.json').write_text('{bad', encoding='utf-8')
    check('a torn waiver file is not a release', BL.unchecked_allowed(env={}, here=str(d)) is False)
finally:
    waive(False)

# -------------------------------------------------------------------- the tracked file in the repo
print('-- repo waiver file')
repo = json.loads((HERE / 'bk_waiver.json').read_text(encoding='utf-8'))
check('the tracked bk_waiver.json releases unchecked contact and names its records',
      repo.get('unchecked_contact') is True and repo.get('approved') == '2026-10-13'
      and any('ALL-CHANNELS' in r for r in repo.get('records', [])), repo)
check('the bake stamps bkAsk from the ask flag',
      "d['bkAsk'] = True" in (HERE / 'foreclosure_leads.py').read_text(encoding='utf-8'))
src = (HERE / 'call_mode.py').read_text(encoding='utf-8')
check('Call Mode ships the ask flag and the card asks first, on the balloon card too',
      "'bq': 1 if d.get('bkAsk') else None" in src and src.count('if(r.bq) who += _bqBar();') == 2
      and 'function _bqBar' in src and 'Have you filed for bankruptcy?' in src)

print('\n%d failure(s)' % len(FAILS))
sys.exit(1 if FAILS else 0)
