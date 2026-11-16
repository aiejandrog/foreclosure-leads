"""_mdwalktest -- Miami-Dade case-number walk and the Miami name resolution of its rows.

Run:  python _mdwalktest.py   (exit 0 = safe; no network, invented cases and parties only)

WHAT THIS PINS
  1. to_row(): only an OPEN mortgage foreclosure is a row; the owner is the first named defendant who
     is not 'Unknown Tenant/Spouse'; no plaintiff, owner or date means no row.
  2. walk(): forward from the highest case held; a run of unknown numbers marks the newest filing and
     parks the cursor there; a failed lookup is retried, never skipped past; an unreachable court
     stops the run; backward skips what we hold and does not count it against the slice.
  3. merge(): an existing row is never changed or duplicated.
  4. A recorder row (with a legal description) replaces a court-walk row for the same case.
  5. The Miami resolver only touches case-walk rows, asks the Miami-Dade roll, and writes the md rung.
  6. Wiring: the walk runs in the chain before the recorder resolver, and its cursor file is ignored.
"""
import json
import os
import sys
import tempfile
import datetime as dt

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import md_case_walk as W  # noqa: E402
from fl_lp import palmbeach_resolve as PN  # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    extra = str(extra)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + extra[:220]) if extra else ''))
    if not ok:
        fails.append(name)


KIND = lambda parties: 'BANK-1st' if 'BANK' in parties else 'OTHER/PRIVATE'


def case_json(seq, ctype='RPMF -Homestead', owner='SMITH, JOHN', plaintiff='TEST BANK NA', disposed=False, year=2026):
    return {'caseNumber': W.case_no(year, seq), 'caseType': ctype, 'caseStyle': 'x', 'filingDate': '09/04/2026',
            'dispositionCode': 'DISM' if disposed else '', 'dispositionDate': '09/05/2026' if disposed else '',
            'parties': [{'partyTypeDesc': 'Plaintiff', 'partyName': plaintiff},
                        {'partyTypeDesc': 'Defendant', 'partyName': 'UNKNOWN TENANT #1'},
                        {'partyTypeDesc': 'Defendant', 'partyName': owner},
                        {'partyTypeDesc': 'Defendant AKA', 'partyName': 'SMITH, J'}]}


# ---- 1. to_row ----------------------------------------------------------------------------------
r = W.to_row(case_json(18100), KIND)
rec('1a: an open foreclosure becomes a row in the file shape',
    r and r['case'] == '2026-018100-CA-01' and r['plaintiff'] == 'TEST BANK NA' and r['owner'] == 'SMITH, JOHN'
    and r['date'] == '9/4/2026' and r['county'] == 'MIAMI-DADE' and r['kind'] == 'BANK-1st'
    and r['legal'] == '' and r['src'] == 'court-walk' and r['parties'] == 'TEST BANK NA; SMITH, JOHN', r)
need = {'bookpage', 'case', 'date', 'docType', 'instrument', 'kind', 'legal', 'owner', 'parties', 'plaintiff'}
rec('1b: it carries every key an existing row carries', need <= set(r))
rec('1c: Unknown Tenant is not the owner', 'TENANT' not in r['owner'])
rec('1d: a case that is not a foreclosure is not a row', W.to_row(case_json(18101, ctype='Auto Negligence'), KIND) is None)
rec('1e: a closed foreclosure is not a row', W.to_row(case_json(18102, disposed=True), KIND) is None)
rec('1f: a private-plaintiff foreclosure IS a row (the old 59-name list missed these)',
    W.to_row(case_json(18103, plaintiff='JANE Q PRIVATE LENDER'), KIND)['kind'] == 'OTHER/PRIVATE')
j = case_json(18104)
j['parties'] = [p for p in j['parties'] if 'UNKNOWN' not in p['partyName'] and p['partyTypeDesc'] != 'Defendant']
rec('1g: no named defendant means no row', W.to_row(j, KIND) is None)
rec('1h: both homestead and non-homestead foreclosure types count',
    W.is_foreclosure({'caseType': 'RPMF -Non-Homestead'}) and W.is_foreclosure({'caseType': 'Real Property Foreclosure'})
    and not W.is_foreclosure({'caseType': 'Contract & Indebtedness'}))
rec('1i: sequence numbers parse only for the right year and division',
    W.seq_of('2026-018040-CA-01') == 18040 and W.seq_of('2025-018040-CA-01') is None
    and W.seq_of('2026-018040-CC-05') is None and W.seq_of('CACE-26-018544') is None)

# ---- 2. walk -------------------------------------------------------------------------------------
HELD = [{'case': W.case_no(2026, 100), 'county': 'MIAMI-DADE'}, {'case': W.case_no(2026, 103), 'county': 'MIAMI-DADE'},
        {'case': 'CACE-26-000001', 'county': 'BROWARD'}]
COURT = {s: case_json(s) for s in range(104, 112)}               # 104..111 exist; 112+ do not
COURT[106] = case_json(106, ctype='Auto Negligence')
COURT[107] = case_json(107, disposed=True)
COURT.update({s: case_json(s) for s in (101, 102, 99)})


def make_pull(table, fail=()):
    log = []
    fail = dict(fail) if isinstance(fail, dict) else {s: 99 for s in fail}

    def pull(case):
        seq = W.seq_of(case)
        log.append(seq)
        if fail.get(seq, 0) > 0:
            fail[seq] -= 1
            raise OSError('timeout')
        if seq in table:
            return table[seq]
        e = Exception('404'); e.response = type('R', (), {'status_code': 404})()
        raise e
    pull.log = log
    return pull


pull = make_pull(COURT)
orig = W.FRONTIER_RUN
W.FRONTIER_RUN = 3
new, state, rep = W.walk(HELD, {}, fwd=50, back=0, pull=pull, pause=0, kind_of=KIND, say=lambda *a: None)
rec('2a: forward starts one past the highest case held', pull.log[0] == 104, pull.log[:3])
rec('2b: only open foreclosures come back', sorted(int(r['case'][5:11]) for r in new) == [104, 105, 108, 109, 110, 111], [r['case'] for r in new])
rec('2c: a run of unknown numbers marks the frontier and parks the cursor on the first unknown',
    rep['frontier'] is True and state['2026']['fwd'] == 112, (rep, state))
rec('2d: it did not look beyond the frontier run', max(pull.log) == 114, pull.log[-4:])
rec('2e: closed and non-foreclosure cases are counted, not returned', rep['found'] == 8 and rep['closed'] == 1, rep)
pull = make_pull(COURT, fail={105: 2})
new, state, rep = W.walk(HELD, {}, fwd=3, back=0, pull=pull, pause=0, kind_of=KIND, say=lambda *a: None)
rec('2f: a failed lookup is retried, never skipped past', pull.log[:4] == [104, 105, 105, 105] and 105 in [int(r['case'][5:11]) for r in new], pull.log)
pull = make_pull(COURT, fail={105})
new, state, rep = W.walk(HELD, {}, fwd=10, back=5, pull=pull, pause=0, kind_of=KIND, say=lambda *a: None)
rec('2g: an unreachable court stops the run and the cursor stays on the failing case',
    rep['unreachable'] is True and state['2026']['fwd'] == 105 and pull.log.count(105) == W.STOP_ERRORS and 99 not in pull.log, (rep, state))
W.FRONTIER_RUN = orig

pull = make_pull(COURT)
new, state, rep = W.walk(HELD, {}, fwd=0, back=3, pull=pull, pause=0, kind_of=KIND, say=lambda *a: None)
rec('2h: backward starts at the highest case held and skips what is held, without counting it',
    pull.log == [102, 101, 99], pull.log)
rec('2i: the backward cursor sits on the next number to look at', state['2026']['back'] == 98, state)
new2, state2, rep2 = W.walk(HELD, state, fwd=0, back=200, pull=make_pull({}), pause=0, kind_of=KIND, say=lambda *a: None)
rec('2j: it reaches case 1 and reports the backfill done', state2['2026']['back'] == 0 and rep2['back_done'] is True, (state2, rep2))
rec('2k: a saved cursor is continued, not restarted',
    W.walk(HELD, {'2026': {'fwd': 500, 'back': 0}}, fwd=1, back=0, pull=(p3 := make_pull({})), pause=0, say=lambda *a: None) and p3.log == [500], p3.log)

# ---- 3. merge -------------------------------------------------------------------------------------
existing = [{'case': W.case_no(2026, 100), 'county': 'MIAMI-DADE', 'legal': 'LOT 1', 'owner': 'KEEP'}]
snap = json.dumps(existing, sort_keys=True)
rows = [W.to_row(case_json(100), KIND), W.to_row(case_json(104), KIND), W.to_row(case_json(104), KIND)]
merged, added = W.merge(existing, rows)
rec('3a: a held case is not added and one new case is added once', added == 1 and len(merged) == 2, added)
rec('3b: the existing row is untouched', json.dumps(merged[:1], sort_keys=True) == snap)

# ---- 4. recorder row replaces a walk row -------------------------------------------------------------
lp = open(os.path.join(HERE, 'lis_pendens.py'), encoding='utf-8').read()
rec('4a: lis_pendens.merge lets a recorder row with a legal description replace a court row for the same case',
    "merged[k].get('src') in ('court-walk', 'court') and x.get('legal') and not merged[k].get('legal')" in lp)

# ---- 5. Miami resolver -------------------------------------------------------------------------------------
def parcel(pid, owner, addr='12 TEST LN', hs=0):
    return {'PARCEL_ID': pid, 'OWN_NAME': owner, 'PHY_ADDR1': addr, 'PHY_CITY': 'MIAMI', 'PHY_ZIPCD': '33172',
            'JV': 300000, 'JV_HMSTD': hs, 'OWN_ADDR1': '9 MAIL RD', 'OWN_CITY': 'MIAMI', 'OWN_STATE': 'FL', 'OWN_ZIPCD': '33172'}


asked = []


def fetch(where, n):
    asked.append(where)
    return [parcel('3040320410710', 'SMITH JOHN', hs=1)] if "LIKE 'SMITH JOHN%'" in where else []


with tempfile.TemporaryDirectory() as td:
    src, out = os.path.join(td, 'lis.json'), os.path.join(td, 'addr.json')
    json.dump([
        dict(W.to_row(case_json(104), KIND)),                                    # a case-walk row: resolved here
        {'case': '2026-009999-CA-01', 'owner': 'SMITH, JOHN', 'legal': 'LOT 4 BLK 2', 'county': 'MIAMI-DADE',
         'src': ''},                                                              # recorder row: lp_resolve's, not ours
        {'case': '502026CA000001XXXAMB', 'owner': 'SMITH, JOHN', 'county': 'PALM BEACH'},
    ], open(src, 'w'))
    counts = PN.run(src=src, out=out, fetch=fetch, pause=0, today=dt.date(2026, 11, 16), say=lambda *a: None, county='MIAMI-DADE')
    got = json.load(open(out))
    rec('5a: only the case-walk row is resolved, on the Miami-Dade roll (county 53)',
        list(got) == ['2026-000104-CA-01'] or list(got) == [W.case_no(2026, 104)], list(got))
    e = got[W.case_no(2026, 104)]
    rec('5b: it is graded like Broward and Palm Beach and carries the md rung and county',
        e['confidence'] == 'high' and e['rung'] == 'md-name' and e['county'] == 'MIAMI-DADE' and e['folio'] == '3040320410710', e)
    rec('5c: the query used county number 53', asked and all('CO_NO=53' in w for w in asked), asked)
    counts2 = PN.run(src=src, out=out, fetch=fetch, pause=0, today=dt.date(2026, 11, 17), say=lambda *a: None, county='MIAMI-DADE')
    rec('5d: a resolved case is not asked again', counts2 == {}, counts2)

# ---- 6. wiring --------------------------------------------------------------------------------------------------
ref = open(os.path.join(HERE, 'lp_refresh.py'), encoding='utf-8').read()
i_w, i_r, i_m = ref.find("'md_case_walk.py'"), ref.find("run('RESOLVE (lp_resolve)'"), ref.find("'--county', 'miami'")
rec('6a: the chain walks Miami-Dade case numbers before the recorder resolver, and names its filings after',
    0 < i_w < i_r < i_m, (i_w, i_r, i_m))
rec('6b: the walk is allowed to exit 3 (court unreachable) without stopping the chain', "ok=(0, 3))" in ref[i_w:i_w + 120])
gi = open(os.path.join(HERE, '.gitignore'), encoding='utf-8').read().split()
rec('6c: the cursor file is ignored', 'md_walk_state.json' in gi)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
