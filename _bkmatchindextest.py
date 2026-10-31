"""_bkmatchindextest -- the token-indexed match step gives exactly the full cross product's answer.

Run:  python _bkmatchindextest.py

Invented names only, no network, a temp DEALFLOW_DIR. The REFERENCE matcher below is a verbatim copy
of match_filings_to_leads as it was on origin/main 64fe209 (every lead against every filing). The new
matcher must return the same hold count, write the same cache entries (every field), and give the same
bk_overrides outcome on every input: a hand-built set covering each matching rule, plus a seeded random
set built to collide heavily. A speed check follows.
"""
import copy
import json
import os
import pathlib
import random
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = pathlib.Path(tempfile.mkdtemp(prefix='bkmatchindex_'))
for _k in list(os.environ):
    if _k.startswith('DEALFLOW_BK_'):
        os.environ.pop(_k, None)
os.environ['DEALFLOW_DIR'] = str(TMP)
os.environ['DEALFLOW_BK_ALLOW_UNCHECKED'] = '0'

import bk_lookup as BL

FAILS = []
NOW = 1_800_000_000.0
BL._stamp = lambda: 'STAMP'   # the only non-deterministic field in a cache entry


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:600]) if not cond else ''))
    if not cond:
        FAILS.append(name)


def reference_match(leads, items, cache, now):
    """origin/main 64fe209 match_filings_to_leads, unchanged."""
    holds = 0
    for ld in leads.values():
        owners = BL.lead_owners(ld)
        if not owners:
            continue
        found = []
        for item in items.values():
            if not isinstance(item, dict):
                continue
            found.extend(BL.cases_from_hits(owners, [item], county=ld.get('county') or '',
                                            place=ld.get('place') or ''))
        if not found:
            continue
        entry = BL.put_entry(cache, ld['key'], found, searched=False, ok_check=True, err_why='', now=now)
        if entry['verdict'] == 'clear':
            entry['verdict'] = 'unavailable'
            entry['ok_check'] = False
            entry['searched'] = False
            entry['why'] = 'federal bankruptcy check has not run for this lead'
        if entry['verdict'] in ('active', 'possible'):
            holds += 1
    return holds


def lead(key, raw, order='last_first', county='BROWARD', place='', extra=None):
    ow = raw if isinstance(raw, list) else [(raw, order)]
    d = {'key': key, 'county': county, 'place': place, 'owners_raw': ow}
    if extra:
        d.update(extra)
    return d


def filing(no, parties, court='flsb', filed='2026-09-20', open_=True, terminated=''):
    return {'no': no, 'court_id': court, 'filed': filed, 'terminated': terminated,
            'open': open_, 'parties': parties}


def compare(name, leads, items, overrides=None):
    c_old, c_new = {}, {}
    # a pre-existing entry on one lead proves merge behaviour is the same too
    first = next(iter(leads.values()))['key']
    seed = {first: {'verdict': 'unavailable', 'why': 'x', 'searched': True, 'ok_check': False,
                    'err': '', 'cases': [], 't': 1.0}}
    c_old.update(copy.deepcopy(seed))
    c_new.update(copy.deepcopy(seed))
    h_old = reference_match(leads, copy.deepcopy(items), c_old, NOW)
    h_new = BL.match_filings_to_leads(leads, copy.deepcopy(items), c_new, NOW)
    same_cache = json.dumps(c_old, sort_keys=True) == json.dumps(c_new, sort_keys=True)
    ov = overrides or {}
    ops_old = {k: BL.entry_opinion(k, e, ov, NOW) for k, e in c_old.items()}
    ops_new = {k: BL.entry_opinion(k, e, ov, NOW) for k, e in c_new.items()}
    same_ops = json.dumps(ops_old, sort_keys=True, default=str) == json.dumps(ops_new, sort_keys=True, default=str)
    check('%s: hold count identical (%d)' % (name, h_old), h_old == h_new, (h_old, h_new))
    check('%s: cache entries identical (%d entries)' % (name, len(c_old)), same_cache,
          [k for k in set(c_old) | set(c_new) if c_old.get(k) != c_new.get(k)][:5])
    check('%s: stay-gate opinion identical for every entry' % name, same_ops)
    return c_new


# ---------------------------------------------------------------- hand-built, every rule
LEADS = {}
for i, (raw, order) in enumerate([
        ('JOSE NUNEZ', 'first_last'),                      # accents in the filing
        ("O'BRIEN, MARY", 'last_first'),                   # apostrophe
        ('JUAN M GARCIA', 'first_last'),                   # middle initial agrees
        ('JUAN R GARCIA', 'first_last'),                   # middle initial conflicts
        ('GARCIA LOPEZ, JUAN', 'last_first'),              # Hispanic double surname
        ('LOPEZ, JUAN', 'last_first'),                     # one of the two surnames
        ('SUNSHINE HOLDINGS LLC', 'first_last'),           # LLC
        ('SUNSHINE HOLDINGS', 'first_last'),               # not flagged an entity: person path
        ('THE RIVERA FAMILY TRUST', 'first_last'),         # TRUST
        ('RIVERA, ANA & RIVERA, LUIS', 'last_first'),      # joint owners
        ('RIVERA, ANA AND LUIS', 'last_first'),            # joint, second has first name only
        ('SMITH, J', 'last_first'),                        # initial only
        ('J SMITH', 'first_last'),
        ('SMITH, JOHN JR', 'last_first'),                  # generation suffix
        ('PEREZ, MARIA ELENA', 'last_first'),              # middle name vs initial
        ('NO MATCH, NOBODY', 'last_first'),                # matches nothing
        ('XY', 'first_last'),                              # too thin
        ('ALVAREZ DE LA CRUZ, PEDRO', 'last_first'),       # many tokens
        ('LEE, ANNE; LEE, BOB', 'last_first'),             # semicolon owners
        ('ACME TOWERS LLC & TOWERS, ACME', 'last_first'),  # entity + person
        ('CLOSED, CARLOS', 'last_first'),                  # only a closed case
        ('ELSEWHERE, ELENA', 'last_first'),                # exact in a non-FL bankruptcy court
        ('PLACE, PAULA', 'last_first'),                    # same, but place evidence
        ]):
    k = 'CACE-99-%06d' % (100 + i)
    extra = {'place': 'MIRAMAR'} if raw.startswith('PLACE') else None
    LEADS[k] = lead(k, raw, order, extra=extra)
LEADS['BLANK'] = lead('BLANK', '', 'first_last')
LEADS['NOOWN'] = {'key': 'NOOWN', 'county': 'BROWARD'}

ITEMS = {}
rows = [
    (['Nunez, Jose'], 'flsb', True), (['José Núñez'], 'flsb', True), (['Mary Obrien'], 'flsb', True),
    (['Garcia, Juan M'], 'flsb', True), (['Garcia, Juan R'], 'flsb', True),
    (['Garcia Lopez, Juan'], 'flsb', True), (['Lopez, Juan'], 'flmb', True),
    (['Sunshine Holdings, LLC'], 'flsb', True), (['Sunshine Holdings Group LLC'], 'flsb', True),
    (['Sunshine'], 'flsb', True),
    (['Rivera Family Trust'], 'flsb', True), (['The Rivera Family Trust'], 'flnb', True),
    (['Rivera, Ana', 'Rivera, Luis'], 'flsb', True), (['Luis Rivera'], 'flsb', True),
    (['Smith, J'], 'flsb', True), (['Smith, John Jr.'], 'flsb', True), (['John Smith'], 'flsb', True),
    (['Perez, Maria E'], 'flsb', True), (['Perez Maria Elena'], 'flsb', True),
    (['Alvarez de la Cruz, Pedro'], 'flsb', True), (['Pedro Alvarez'], 'flsb', True),
    (['In re: Lee, Anne'], 'flsb', True), (['IN RE Lee Bob'], 'flsb', True),
    (['Acme Towers LLC'], 'flsb', True), (['Towers, Acme'], 'flsb', True),
    (['Closed, Carlos'], 'flsb', False), (['Elsewhere, Elena'], 'nysb', True),
    (['Place, Paula'], 'nysb', True),
    (['Garcia, Juan M'], 'ca9', True),                     # not a bankruptcy court
    (['Nunez, Jose'], 'flsd', True),                       # not a bankruptcy court
    ([], 'flsb', True), ([''], 'flsb', True), (['Nobody'], 'flsb', True),
]
for i, (parties, court, op) in enumerate(rows):
    ITEMS['26-%05d|%s' % (i, court)] = filing('26-%05d' % i, parties, court=court, open_=op,
                                              terminated='' if op else '2025-01-01')
ITEMS['not-a-dict'] = 'garbage'
ITEMS['parties-not-a-list'] = {'no': '26-99998', 'court_id': 'flsb', 'open': True, 'parties': 'Garcia, Juan M'}
ITEMS['no-parties-key'] = {'no': '26-99997', 'court_id': 'flsb', 'open': True}
ITEMS['none-party'] = filing('26-99996', [None, 'Rivera, Ana'])

OV = {'CACE-99-000100': {BL.case_no_key('26-00000')}}
cache_hand = compare('hand-built', LEADS, ITEMS, overrides=OV)
verdicts = sorted({e['verdict'] for e in cache_hand.values()})
check('hand-built set exercises active, possible and no-match', 'active' in verdicts and 'possible' in verdicts
      and len(cache_hand) >= 12, (verdicts, len(cache_hand)))
check('hand-built set: a non-FL exact stays only plausible (possible hold)',
      cache_hand['CACE-99-000121']['verdict'] == 'possible', cache_hand.get('CACE-99-000121'))
check('hand-built set: closed case writes no hold',
      cache_hand['CACE-99-000120']['verdict'] != 'active', cache_hand.get('CACE-99-000120'))
check('hand-built set: bk_overrides changes the opinion the same way on both',
      BL.entry_opinion('CACE-99-000100', cache_hand['CACE-99-000100'], OV, NOW)
      != BL.entry_opinion('CACE-99-000100', cache_hand['CACE-99-000100'], {}, NOW), '')

# ---------------------------------------------------------------- seeded random, built to collide
rng = random.Random(20261031)
SUR = ['GARCIA', 'LOPEZ', 'PEREZ', 'RIVERA', 'SMITH', 'NUNEZ', 'DIAZ', 'RUIZ', 'ORTIZ', 'COLE']
FIRST = ['JUAN', 'JOSE', 'MARIA', 'ANA', 'LUIS', 'J', 'M', 'JO', 'JOHN', 'MARY', 'CARLOS']
ENT = ['SUNSHINE', 'HOLDINGS', 'ACME', 'TOWERS', 'RIVERA', 'GROUP', 'BAYVIEW', 'PALM']
TAIL = ['LLC', 'INC', 'TRUST', 'CORP', 'LP']
COURT = ['flsb', 'flmb', 'flnb', 'nysb', 'txsb', 'flsd', 'ca9', 'deb']


def rnd_person_str(comma):
    n = rng.choice([1, 1, 2, 2, 3])
    sur = ' '.join(rng.sample(SUR, n if n < 3 else 2))
    first = rng.choice(FIRST)
    mid = rng.choice(['', '', 'M', 'R', 'MARIE'])
    if comma:
        return '%s, %s %s' % (sur, first, mid)
    return ('%s %s %s' % (first, mid, sur)).replace('  ', ' ')


def rnd_owner_raw():
    r = rng.random()
    if r < 0.15:
        return ' '.join(rng.sample(ENT, rng.choice([1, 2, 3]))) + ' ' + rng.choice(TAIL), 'first_last'
    parts = [rnd_person_str(rng.random() < 0.7) for _ in range(rng.choice([1, 1, 2]))]
    return (' & ' if rng.random() < 0.5 else ' AND ').join(parts), rng.choice(['last_first', 'first_last'])


def rnd_party():
    r = rng.random()
    if r < 0.2:
        return ' '.join(rng.sample(ENT, rng.choice([1, 2, 3]))) + rng.choice(['', ', ' + rng.choice(TAIL), ' ' + rng.choice(TAIL)])
    s = rnd_person_str(rng.random() < 0.6)
    if rng.random() < 0.1:
        s = 'In re: ' + s
    return s


rl = {}
for i in range(500):
    raws = [rnd_owner_raw() for _ in range(rng.choice([1, 1, 2]))]
    k = 'CACE-98-%06d' % i
    rl[k] = lead(k, raws, county=rng.choice(['BROWARD', 'PALM BEACH']), place=rng.choice(['', 'MIRAMAR']))
ri = {}
for i in range(700):
    ri['26-%05d|%d' % (i, i)] = filing('26-%05d' % i, [rnd_party() for _ in range(rng.choice([0, 1, 1, 2, 3]))],
                                        court=rng.choice(COURT), open_=rng.random() < 0.7,
                                        filed='2026-%02d-10' % rng.randint(1, 10))
c = compare('random 500x700', rl, ri)
check('random set produced matches to compare', len(c) >= 50, len(c))

# ---------------------------------------------------------------- speed
rng2 = random.Random(7)
SUR2 = ['S%04d' % i for i in range(1500)]
FIRST2 = ['F%03d' % i for i in range(300)]
big_leads = {}
for i in range(4000):
    k = 'CACE-97-%06d' % i
    big_leads[k] = lead(k, '%s, %s' % (rng2.choice(SUR2), rng2.choice(FIRST2)), 'last_first')
big_items = {}
for i in range(2000):
    big_items['27-%05d|flsb' % i] = filing('27-%05d' % i, ['%s, %s' % (rng2.choice(SUR2), rng2.choice(FIRST2))])
t0 = time.time()
c_fast = {}
BL.match_filings_to_leads(big_leads, big_items, c_fast, NOW)
fast_s = time.time() - t0
sample = dict(list(big_leads.items())[:150])
t0 = time.time()
c_ref = {}
reference_match(sample, big_items, c_ref, NOW)
ref_s = time.time() - t0
t0 = time.time()
c_fast_sample = {}
BL.match_filings_to_leads(sample, big_items, c_fast_sample, NOW)
fast_sample_s = time.time() - t0
print('speed: new 4000x2000 = %.2fs; 150-lead sample old %.2fs vs new %.2fs (old 4000 leads ~ %.0fs)'
      % (fast_s, ref_s, fast_sample_s, ref_s / 150 * 4000))
check('speed: 4,000 leads x 2,000 filings under 10 s', fast_s < 10.0, fast_s)
check('speed: sample result equals the reference on the big data',
      json.dumps(c_ref, sort_keys=True) == json.dumps(c_fast_sample, sort_keys=True), '')

print('FAILED: %s' % FAILS if FAILS else 'ALL PASS')
sys.exit(1 if FAILS else 0)
