"""_lpcondotest.py - a Broward LP condo gets the same homestead cross-check as a house.

fl_lp/broward_resolve capped every lettered (condo) folio at 'medium' because the cadastral lookup
used to strip the letters. fl_cadastral.enrich has asked for the raw folio first since 2026-08-12,
so the cap only kept homesteaded condo owners out of 'high': on the 10-22 laptop count, 0 of 398
Broward LP folios carried letters while ~45% of Broward auction folios do. Offline: BCPA and the
cadastral are faked. Run: python _lpcondotest.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'fl_lp'))

from fl_lp import broward_resolve as BR      # noqa: E402

fails = []


def chk(name, cond, detail=''):
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  :: %r' % (detail,)))
    if not cond:
        fails.append(name)


CONDO = {'folioNumber': '494134CB0170', 'ownerName1': 'MARTIN SHAWN E', 'ownerName2': '',
         'siteAddress1': '1760 NW 73 AVE #48', 'siteAddress2': 'PLANTATION, FL 33313'}
CAD = {'market_value': 210000, 'homestead': True, 'mail_addr': '1760 NW 73 AVE 48 PLANTATION FL 33313'}
asked = []


ROLL = {'494134CB0170': dict(CAD)}       # what the state roll holds, by exact PARCEL_ID


def q(where, n=1):
    pid = re.search(r"PARCEL_ID='([^']*)'", where).group(1)
    asked.append(pid)
    return [{'PARCEL_ID': pid, 'cad': ROLL[pid]}] if pid in ROLL else []


def enrich(parcel_id=None, **k):
    raise AssertionError('a condo folio must never go through FC.enrich (digits-only fallback)')


BR.bcpa_name = lambda q, tries=3: [CONDO]
BR.time.sleep = lambda s: None
BR.FC._q = q
BR.FC._norm = lambda a: dict(a['cad'])
BR.FC.enrich = enrich

print('\nNEW ROWS')
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('a homesteaded condo on the roll is high', r.get('confidence') == 'high', r.get('confidence'))
chk('the roll is asked for the folio with its letters', asked == ['494134CB0170'], asked)
chk('the condo carries value and mailing address', r.get('value') == 210000 and r.get('mailAddr'), r)
chk('the folio keeps its letters', r.get('folio') == '494134CB0170', r.get('folio'))

ROLL['494134CB0170'] = dict(CAD, homestead=False)
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('a condo with no homestead stays medium (same bar as a house)', r.get('confidence') == 'medium'
    and r.get('needsHuman') is True and 'no homestead' in r.get('evidence', ''), r)

ROLL.clear()
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('a condo the roll cannot return stays medium and says so', r.get('confidence') == 'medium'
    and 'not on the cadastral roll' in r.get('evidence', ''), r.get('evidence'))

# the roll answers with a DIFFERENT parcel (what a digits-only fallback would do): never accepted
BR.FC._q = lambda where, n=1: [{'PARCEL_ID': '4941340170', 'cad': dict(CAD)}]
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('a roll row that is not this exact lettered folio never grades it', r.get('confidence') == 'medium'
    and 'value' not in r and 'mailAddr' not in r, r)
BR.FC._q = q

BR.bcpa_name = lambda q, tries=3: [CONDO, dict(CONDO, folioNumber='494134CB0180', siteAddress1='1760 NW 73 AVE #49')]
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('two matching units are still low (ambiguous), never high', r.get('confidence') == 'low', r.get('confidence'))

print('\nROWS RESOLVED UNDER THE OLD CAP')
OLD = "BCPA: exactly one parcel carries every token of 'MARTIN, SHAWN E'; condo folio — no cadastral cross-check possible"


def old(folio='494134CB0170', **extra):
    d = {'county': 'BROWARD', 'folio': folio, 'confidence': 'medium', 'rung': 'bcpa-name',
         'evidence': OLD, 'needsHuman': True, 'addr': '1760 NW 73 AVE #48'}
    d.update(extra)
    return d


asked[:] = []
ROLL['494134CB0170'] = dict(CAD)
prev = {'A': old(), 'B': old('494134CB0999'), 'C': old(confidence='low'), 'D': old(rung='pin-mortgage'),
        'E': old(county='MIAMI-DADE'), 'F': old('494134010170'),
        'G': old(evidence='BCPA: exactly one parcel carries every token'), 'H': 'junk'}
n = BR.recheck_condos(prev, pause=0)
chk('the homesteaded old-cap condo is raised to high', prev['A']['confidence'] == 'high' and n == 1, prev['A'])
chk('its evidence drops the old cap line', 'no cadastral cross-check possible' not in prev['A']['evidence']
    and 'HOMESTEAD' in prev['A']['evidence'], prev['A']['evidence'])
chk('a condo the roll cannot return keeps its old row (retried next run)', prev['B'] == old('494134CB0999'), prev['B'])
chk('low, pinned, Miami, numeric and already-regraded rows are not asked',
    sorted(asked) == ['494134CB0170', '494134CB0999'], asked)

ROLL['494134CB0170'] = dict(CAD, homestead=False)
prev = {'A': old()}
n = BR.recheck_condos(prev, pause=0)
chk('an old-cap condo with no homestead stays medium with the roll evidence',
    n == 0 and prev['A']['confidence'] == 'medium' and 'no homestead' in prev['A']['evidence']
    and prev['A']['evidence'].count('BCPA') == 1, prev['A'])
n2 = BR.recheck_condos(prev, pause=0)
chk('a regraded row is not asked again', n2 == 0)

asked[:] = []
ROLL.clear()
many = {'K%d' % i: old('4941340%02dCB' % i) for i in range(30)}
BR.recheck_condos(many, pause=0, cap=10, max_fails=1000)
chk('recheck honours its per-run cap', len(asked) == 10, len(asked))
asked[:] = []
BR.recheck_condos(many, pause=0, cap=50, max_fails=4)
chk('recheck stops after max_fails consecutive misses', len(asked) == 4, len(asked))

print('\n==== %d FAIL(S) ====' % len(fails) if fails else '\n==== all LP condo checks passed ====')
sys.exit(1 if fails else 0)
