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

# hl: the other cases of the household, for the page's Untouched / Reached out split
rows = [row(a, pk='P1', p=['3055550101']), row(b, pk='P2', p=['3055550101']), row(c3, pk='P3', p=['3055550177'])]
CM._seat_by_household(rows)
ok(rows[0].get('hl') == [b] and rows[1].get('hl') == [a], 'rows sharing a phone list each other in hl')
ok('hl' not in rows[2], 'an unlinked row carries no hl')
big = [row('2026-%06d-CA-01' % (900 + i), pk='Q%d' % i, p=['3055550888']) for i in range(CM.HL_MAX + 1)]
CM._seat_by_household(big)
ok(all('hl' not in r for r in big), 'a group past HL_MAX (a chain of shared numbers) gets no hl')
dup = [row(a, pk='P1', p=['3055550101']), row(a, pk='P1', p=['3055550101']), row(b, pk='P2', p=['3055550101'])]
CM._seat_by_household(dup)
ok(dup[2].get('hl') == [a] and dup[0].get('hl') == [b], 'a case listed twice (two sale dates) appears once in hl and never lists itself')

rows = [{'sb': 3, 'pk': 'P1'}, row(a, pk='P1')]
CM._seat_by_household(rows)
ok(rows[0]['sb'] == 3, 'a caseless row is left alone')

print('\n%d failed' % len(fails))
raise SystemExit(1 if fails else 0)
