"""One household, one seat: call_mode._seat_by_household. Container-safe, no board data."""
import call_mode as CM

fails = []
def ok(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg)
    if not cond:
        fails.append(msg)

def row(c, pk=None, a='', z=None, p=()):
    r = {'c': c, 'sb': CM._seat_bucket(c)}
    if pk: r['pk'] = pk
    if a: r['a'] = a
    if z: r['z'] = z
    if p: r['p'] = list(p)
    return r

# find two cases whose own buckets land on different seats
cs = ['2026-%06d-CA-01' % i for i in range(1, 60)]
a = cs[0]; b = next(c for c in cs if CM._seat_bucket(c) % 2 != CM._seat_bucket(a) % 2)
c3 = next(c for c in cs if c not in (a, b))

rows = [row(a, pk='P1'), row(b, pk='P1')]
CM._seat_by_household(rows)
ok(rows[0]['sb'] == rows[1]['sb'] == CM._seat_bucket(min(a, b)), 'same person key -> one bucket, the lowest case')

rows = [row(a, pk='P1', a='12 NW 3 ST, MIAMI', z='33101'), row(b, pk='P2', a='12 NW 3 ST, MIAMI', z='33101')]
CM._seat_by_household(rows)
ok(rows[0]['sb'] == rows[1]['sb'], 'same street line + ZIP, different person keys -> one bucket')

rows = [row(a, pk='P1', p=['(305) 555-0101']), row(b, pk='P2', p=['13055550101'])]
CM._seat_by_household(rows)
ok(rows[0]['sb'] == rows[1]['sb'], 'same phone (any format) -> one bucket')

rows = [row(a, pk='P1', p=['3055550101']), row(b, pk='P2', p=['3055550199']), row(c3, pk='P3', p=['3055550101', '3055550199'])]
CM._seat_by_household(rows)
ok(len({r['sb'] for r in rows}) == 1, 'transitive link through a third row -> one bucket')

rows = [row(a, pk='P1', a='12 NW 3 ST', z='33101'), row(b, pk='P2', a='99 SW 8 ST', z='33130')]
before = [r['sb'] for r in rows]
n = CM._seat_by_household(rows)
ok([r['sb'] for r in rows] == before and n == 0, 'unlinked rows keep their own bucket')

rows = [row(a, pk='P1', a='UNKNOWN'), row(b, pk='P2', a='UNKNOWN')]
before = [r['sb'] for r in rows]
CM._seat_by_household(rows)
ok([r['sb'] for r in rows] == before, 'a street line with no number does not link')

rows = [row(a, pk='P1', a='12 NW 3 ST, MIAMI'), row(b, pk='P2', a='12 NW 3 ST, MIAMI')]
before = [r['sb'] for r in rows]
CM._seat_by_household(rows)
ok([r['sb'] for r in rows] == before, 'a street line with no ZIP does not link')

tower = [row(c, pk='T%d' % i, a='100 COLLINS AVE', z='33139') for i, c in enumerate([a, b, c3])]
before = [r['sb'] for r in tower]
CM._seat_by_household(tower)
ok([r['sb'] for r in tower] == before, 'a street line on 3+ person keys (a building) does not link')

# hl (_household_links): other BOARD cases sharing a phone or street line + ZIP, for the page's
# Untouched / Reached out split
def lead(c, pk, addr='', phones=(), **kw):
    d = {'case': c, 'pkey': pk, 'addr': addr, 'phones': list(phones)}
    d.update(kw)
    return d
slim = [lead(a, 'P1', phones=['3055550101']), lead(b, 'P2', phones=['(305) 555-0101']),
        lead(c3, 'P3', phones=['3055550177']), lead('OLD-SOLD', 'P9', addr='12 NW 3 ST, MIAMI, FL 33101'),
        lead('DNC-ROW', 'P8', phones=['3055550177'], phdnc=[True])]
rows = [row(a, pk='P1', p=['3055550101']), row(b, pk='P2', p=['3055550101']), row(c3, pk='P3', p=['3055550177']),
        row('NEW-LP', pk='P7', a='12 NW 3 ST, MIAMI, FL 33101', z='33101')]
CM._household_links(slim, rows)
ok(rows[0].get('hl') == [b] and rows[1].get('hl') == [a], 'rows sharing a phone list each other in hl')
ok('hl' not in rows[2], 'a DNC copy of a number does not link')
ok(rows[3].get('hl') == ['OLD-SOLD'], 'a board case that left the dial queue still links by address')
office = [lead('OF-%d' % i, 'O%d' % i, phones=['3055550888']) for i in range(CM.HL_MAX + 2)]
orow = [row('OF-0', pk='O0', p=['3055550888'])]
CM._household_links(office, orow)
ok('hl' not in orow[0], 'a number on more than HL_MAX cases (an office) does not link')
tw = [lead('T-%d' % i, 'T%d' % i, addr='100 COLLINS AVE, MIAMI BEACH, FL 33139') for i in range(3)]
trow = [row('T-0', pk='T0', a='100 COLLINS AVE, MIAMI BEACH, FL 33139', z='33139')]
CM._household_links(tw, trow)
ok('hl' not in trow[0], 'a building (street line on 3+ person keys) does not link')
srow = [row(a, pk='P1', p=['3055550101'])]
srow[0]['pcs'] = [b]
CM._household_links(slim, srow)
ok('hl' not in srow[0], 'a case already in pcs is not repeated in hl')

rows = [{'sb': 3, 'pk': 'P1'}, row(a, pk='P1')]
CM._seat_by_household(rows)
ok(rows[0]['sb'] == 3, 'a caseless row is left alone')

print('\n%d failed' % len(fails))
raise SystemExit(1 if fails else 0)
