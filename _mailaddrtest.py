"""_mailaddrtest.py - fl_lp.broward_resolve keeps the owner's MAILING address off the cadastral roll.

Offline: the BCPA search and the cadastral call are faked. Pins that the mailing address is stored
beside (never instead of) the site address, that a homestead row and a no-homestead row both carry it,
that the backfill touches Broward rows with a folio (condo letters included), is bounded, and leaves a row
the roll cannot return without the key so it is retried.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'fl_lp'))

from fl_lp import broward_resolve as BR      # noqa: E402

fails = []


def chk(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


HIT = {'folioNumber': '494134010170', 'ownerName1': 'MARTIN SHAWN E', 'ownerName2': '',
       'siteAddress1': '1760 NW 73 AVE', 'siteAddress2': 'PLANTATION, FL 33313'}
CAD = {'market_value': 300000, 'homestead': False, 'mail_addr': '  500  OCEAN DR  MIAMI BEACH FL 33139 ',
       'site_addr': '1760 NW 73 AVE'}

BR.bcpa_name = lambda q, tries=3: [HIT]
BR.time.sleep = lambda s: None

# 1. no homestead -> medium, mailing address kept, site address untouched
BR.FC.enrich = lambda parcel_id=None, **k: dict(CAD)
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('medium row carries the mailing address, whitespace squeezed', r.get('mailAddr') == '500 OCEAN DR MIAMI BEACH FL 33139')
chk('site address still the parcel address', r.get('addr') == '1760 NW 73 AVE')
chk('still medium (mailing address does not raise confidence)', r.get('confidence') == 'medium')

# 2. homestead -> high, also carries it
BR.FC.enrich = lambda parcel_id=None, **k: dict(CAD, homestead=True)
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('high row carries the mailing address', r.get('confidence') == 'high' and r.get('mailAddr') == '500 OCEAN DR MIAMI BEACH FL 33139')

# 2b. roll has the parcel but no owner address -> key absent, so the backfill retries it later
BR.FC.enrich = lambda parcel_id=None, **k: dict(CAD, mail_addr='  ')
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('empty mailing address is not stored as an empty key', 'mailAddr' not in r)

# 3. cadastral returns nothing -> no key invented
BR.FC.enrich = lambda parcel_id=None, **k: None
r = BR.resolve_row({'owner': 'MARTIN, SHAWN E'})
chk('no cadastral -> no mailAddr key', 'mailAddr' not in r)

# 4. backfill
calls = []


def fake(parcel_id=None, **k):
    calls.append(parcel_id)
    if parcel_id == '333':
        return None
    return {'mail_addr': 'PO BOX 1 DAVIE FL 33314'}


BR.FC.enrich = fake
# a condo folio is asked by its exact lettered PARCEL_ID (never FC.enrich's digits-only fallback)
BR.FC._q = lambda where, n=1: (calls.append(where.split("'")[1]),
                               [{'PARCEL_ID': where.split("'")[1], 'OWN': 1}])[1]
BR.FC._norm = lambda a: {'mail_addr': 'PO BOX 1 DAVIE FL 33314'}
prev = {
    'A': {'county': 'BROWARD', 'folio': '111'},                        # fill
    'B': {'county': 'BROWARD', 'folio': '222', 'mailAddr': 'X'},       # already has it
    'C': {'county': 'BROWARD', 'folio': '333'},                        # roll returns nothing
    'D': {'county': 'BROWARD', 'folio': '494134CB0170'},               # condo folio: raw lookup works (2026-10-22)
    'E': {'county': 'MIAMI-DADE', 'folio': '444'},                     # not Broward
    'F': {'county': 'BROWARD', 'folio': ''},                           # no folio
    'G': 'junk',
}
n = BR.backfill_mail(prev, cap=50, pause=0)
chk('backfill filled the two eligible rows, condo included', n == 2 and prev['A'].get('mailAddr') == 'PO BOX 1 DAVIE FL 33314'
    and prev['D'].get('mailAddr') == 'PO BOX 1 DAVIE FL 33314')
chk('existing mailAddr not overwritten', prev['B']['mailAddr'] == 'X')
chk('unreturnable row left without the key (retried next run)', 'mailAddr' not in prev['C'])
chk('non-Broward, no-folio and junk rows never queried; the condo is asked with its letters',
    sorted(calls) == ['111', '333', '494134CB0170'])

calls[:] = []
many = {'K%d' % i: {'county': 'BROWARD', 'folio': str(1000 + i)} for i in range(10)}
n = BR.backfill_mail(many, cap=3, pause=0)
chk('backfill honours its per-run cap', n == 3 and len(calls) == 3)

# cap counts ATTEMPTS: a roll that returns nothing must not be queried more than `cap` times
calls[:] = []
BR.FC.enrich = lambda parcel_id=None, **k: (calls.append(parcel_id), None)[1]
dead = {'D%d' % i: {'county': 'BROWARD', 'folio': str(2000 + i)} for i in range(50)}
n = BR.backfill_mail(dead, cap=20, pause=0, max_fails=1000)
chk('cap bounds attempts, not just fills', n == 0 and len(calls) == 20)

# consecutive failures stop the run early
calls[:] = []
n = BR.backfill_mail(dead, cap=50, pause=0, max_fails=5)
chk('stops after max_fails consecutive misses', len(calls) == 5)

# wall-clock budget stops the run
calls[:] = []
n = BR.backfill_mail(dead, cap=50, pause=0, budget_s=-1, max_fails=1000)
chk('wall-clock budget stops the run', len(calls) == 0)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
