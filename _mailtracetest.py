"""_mailtracetest -- phone search by tax-roll mailing address on one-parcel Broward filings.
Fixtures are fake. Run: python _mailtracetest.py  (exit 0 = pass)

2026-10-15, Alejandro chose "Yes, one-match only": a 'medium' Broward LP row (exactly one parcel
carries every name token, no homestead) gets `mailTrace` = the parcel owner's mailing address, and
skiptrace may search the owner there. The property stays unconfirmed, so: `addr` stays empty, the
row never reaches Call Mode's dial queue, and the board merge attaches no email.
"""
import json
import os
import sys
import tempfile

os.environ['DEALFLOW_FORCE'] = '1'
os.environ.setdefault('DEALFLOW_BK_ALLOW_UNCHECKED', '0')
import lp_leads as L    # noqa: E402
import skiptrace as S   # noqa: E402
import call_mode as CM  # noqa: E402

fails, ran = [], []


def chk(name, cond):
    ran.append(name)
    if not cond:
        fails.append(name)


MAIL = '55 OTHER RD, CORAL SPRINGS, FL 33065'
feed, addrs = [], {}
def lp(case, owner, county='BROWARD'):
    feed.append({'date': '10/01/2026', 'case': case, 'docType': 'LIS PENDENS', 'bookpage': '1/1',
                 'legal': '', 'parties': 'BANK / ' + owner, 'plaintiff': 'BANK', 'owner': owner,
                 'kind': 'BANK-1st', 'county': county})

lp('CACE-26-000101', 'ROE MARY'); addrs['CACE-26-000101'] = {      # medium, one parcel -> traced
    'confidence': 'medium', 'addr': '10 EAST ST', 'city': 'SUNRISE', 'zip': '33313', 'folio': '494134000010',
    'candidates': [], 'county': 'BROWARD', 'mailAddr': MAIL, 'value': 300000}
lp('CACE-26-000102', 'DOE JOHN'); addrs['CACE-26-000102'] = {      # several parcels -> never
    'confidence': 'low', 'county': 'BROWARD', 'mailAddr': MAIL,
    'candidates': [{'folio': '1', 'addr': '1 A ST'}, {'folio': '2', 'addr': '2 B ST'}]}
lp('CACE-26-000103', 'POE ANN'); addrs['CACE-26-000103'] = {       # high -> real addr, no mailTrace
    'confidence': 'high', 'addr': '30 NORTH ST', 'city': 'DAVIE', 'zip': '33324', 'folio': '494134000030',
    'candidates': [], 'county': 'BROWARD', 'mailAddr': MAIL, 'value': 250000, 'hs': True}
lp('CACE-26-000104', 'ZOE LEE'); addrs['CACE-26-000104'] = {       # medium but owner changed -> never
    'confidence': 'medium', 'addr': '40 SOUTH AVE', 'city': 'SUNRISE', 'zip': '33313', 'folio': '494134000040',
    'candidates': [], 'county': 'BROWARD', 'mailAddr': MAIL, 'ownerMismatch': True, 'paOwner': 'X'}
lp('CACE-26-000105', 'KOE SAM'); addrs['CACE-26-000105'] = {       # medium, no mail on file -> none
    'confidence': 'medium', 'addr': '50 WEST AVE', 'city': 'SUNRISE', 'zip': '33313', 'folio': '494134000050',
    'candidates': [], 'county': 'BROWARD'}

d = tempfile.mkdtemp()
L.IN, L.ADDR, L.STATUS, L.OUT = (os.path.join(d, n) for n in ('lp.json', 'addr.json', 'st.json', 'out.json'))
json.dump(feed, open(L.IN, 'w')); json.dump(addrs, open(L.ADDR, 'w'))
L.build()
rows = {r['case']: r for r in json.load(open(L.OUT))}

chk('one-parcel medium row carries mailTrace', rows['CACE-26-000101'].get('mailTrace') == MAIL)
chk('...and still no confirmed addr', rows['CACE-26-000101'].get('addr') == '')
chk('several parcels: no mailTrace', not rows['CACE-26-000102'].get('mailTrace'))
chk('high row: real addr, no mailTrace', rows['CACE-26-000103'].get('addr') and not rows['CACE-26-000103'].get('mailTrace'))
chk('owner changed since filing: no mailTrace', not rows['CACE-26-000104'].get('mailTrace'))
chk('no mailing address on file: no mailTrace', not rows['CACE-26-000105'].get('mailTrace'))

a = S.address_for(rows['CACE-26-000101'])
chk('skiptrace searches the mailing address', a and a['street'] == '55 OTHER RD' and a['zip'] == '33065')
chk('skiptrace still prefers a confirmed addr', S.address_for(rows['CACE-26-000103'])['street'] == '30 NORTH ST')
chk('skiptrace has nothing for a several-parcel row', not S.address_for(rows['CACE-26-000102']))

ph = {'phones': ['9545550101'], 'phdnc': [False], 'phsrc': ['st'], 'phrank': [''], 'phbest': 0}
r1 = dict(rows['CACE-26-000101'], **ph)
r3 = dict(rows['CACE-26-000103'], **ph)
out, _n = CM.call_rows([r1, r3])
got = {r.get('c') for r in out}
chk('mailTrace row stays off the dial queue', 'CACE-26-000101' not in got)
chk('confirmed-address twin still ships', 'CACE-26-000103' in got)
r1b = dict(r1, addr='10 EAST ST, SUNRISE, FL 33313')
chk('once the address is confirmed it ships', 'CACE-26-000101' in {r.get('c') for r in CM.call_rows([r1b])[0]})

import diligence_flags as DF   # noqa: E402
import diligence_gate as DG    # noqa: E402
chk('diligence: mailTrace row with no addr is ADDRESS_UNCONFIRMED', DF.address_unconfirmed(r1))
chk('diligence: confirmed twin is not', not DF.address_unconfirmed(r3) and not DF.address_unconfirmed(r1b))
chk('diligence: the code is a hold-always code', 'ADDRESS_UNCONFIRMED' in DF._HOLD_ALWAYS)
chk('diligence: the gate holds it (Morning Worker, call sheet, Call Mode read this)', DG.is_hold(r1) and DG.gate(r1)['hold'])
chk('diligence: the gate off switch cannot release it', 'ADDRESS_UNCONFIRMED' in DG.NEVER_RELEASED)
chk('diligence: junk never raises', DF.address_unconfirmed(None) is False and DF.address_unconfirmed({}) is False)

chk('skiptrace: a mailing-address-only search is stamped via mailTrace', S._via(rows['CACE-26-000101']) == 'mailTrace')
chk('skiptrace: a confirmed address is not', S._via(rows['CACE-26-000103']) == '' and S._via(r1b) == '')

src = open('foreclosure_leads.py', encoding='utf-8').read()
chk('board merge: a via-mailTrace result attaches only to the unconfirmed LP row',
    "if _ph and _ph.get('via') == 'mailTrace' and not _mtrow:" in src)
chk('board merge: no email on that row', "if _mtrow:\n                        _d['emails'] = []" in src)
sk = open('skiptrace.py', encoding='utf-8').read()
chk('skiptrace: stores no email for a via-mailTrace search', "results[case]['emails'] = []" in sk)
chk('skiptrace: re-searches once the address is confirmed', '_mail_only_cached(r)]' in sk)

for f in fails:
    print('  FAILED: ' + f)
print('%d checks, %d failed' % (len(ran), len(fails)))
sys.exit(1 if fails else 0)
