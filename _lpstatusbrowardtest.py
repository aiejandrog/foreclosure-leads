"""Broward LP dismissal check (lp_status.run_broward): strict mapping, one request per case,
a failed read stores nothing, flag and key both required. Fake transport only; no network."""
import json, os, sys, tempfile, pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
KEY = 'lp-test-key-not-real'
NOW = 1_800_000_000.0
for k in ('DEALFLOW_LP_BROWARD', 'BROWARD_CLERK_API_KEY', 'LP_BROWARD_MAX_CASES',
          'LP_BROWARD_MAX_RUNTIME_S', 'CLERK_BK_MIN_INTERVAL'):
    os.environ.pop(k, None)
os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='lpbw_')
os.environ['CLERK_BK_MIN_INTERVAL'] = '0'

import clerk_bk as CK
import lp_status as LS

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)[:300]) if not cond and detail else ''))
    if not cond:
        FAILS.append(name)


def api(case):
    return case.replace('-', '')


def body(case, label):
    return json.dumps({'Case_Number': api(case), 'Disposition_Status': label}).encode()


class Fake:
    def __init__(self, labels, special=None):
        self.labels = labels            # case -> label
        self.special = special or {}    # case -> (status, body)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        for case in list(self.labels) + list(self.special):
            if '/api/case/%s/' % api(case) in url:
                if case in self.special:
                    return self.special[case]
                return 200, body(case, self.labels[case])
        return 404, b'{}'


def clock():
    return CK.Clock(now=NOW, sleeper=lambda s: None)


def feed(*cases):
    return [{'case': c, 'county': 'BROWARD', 'date': '2026-10-%02d' % (i + 1)} for i, c in enumerate(cases)]


# --- verdict mapping: precision on the drop --------------------------------------------------
V = LS.broward_verdict
check('plain dismissal drops', V('Dismissed') == (True, False), V('Dismissed'))
check('dismissed without prejudice drops', V('Dismissed Without Prejudice') == (True, False))
check('voluntary dismissal drops', V('Voluntary Dismissal') == (True, False))
for _l in ('Dismissal Vacated','Order Setting Aside Dismissal','Reinstated After Dismissal','Motion to Dismiss Denied','Not Dismissed','Dismissed - Defendant Jones'):
    check('%s is review, never a drop' % _l, V(_l) == (False, True), V(_l))
check('dismissal of unknown parties does NOT drop (case is alive)',
      V('Dismissed As To Unknown Parties') == (False, True), V('Dismissed As To Unknown Parties'))
check('dismissal of a count does NOT drop', V('Dismissed - Count II') == (False, True))
check('final judgment goes to review, not dropped', V('Final Judgment') == (False, True))
check('open case: nothing', V('Pending') == (False, False) and V('') == (False, False))

# --- one request per case, record shape ------------------------------------------------------
C1, C2, C3 = 'CACE-26-000101', 'CACE-26-000102', 'CACE-26-000103'
f = Fake({C1: 'Dismissed', C2: 'Pending', C3: 'Final Judgment'})
os.environ.update({'DEALFLOW_LP_BROWARD': '1', 'BROWARD_CLERK_API_KEY': KEY})
cache = {}
out = LS.run_broward(feed(C1, C2, C3), cache, transport=f, clock=clock())
check('one request per case', len(f.urls) == 3 and all(u.endswith('case.json?auth_key=' + KEY) for u in f.urls), f.urls)
check('dismissed row recorded dismissed+terminal', cache[C1]['dismissed'] and cache[C1]['terminal'])
check('pending row recorded live', not cache[C2]['dismissed'] and not cache[C2]['terminal'] and not cache[C2]['review'])
check('final judgment row is review, not terminal', cache[C3]['review'] and not cache[C3]['terminal'])
check('review labels are counted', out['review'] == {'FINAL JUDGMENT': 1}, out['review'])
check('no key or names in the cache', KEY not in json.dumps(cache) and 'owner' not in json.dumps(cache).lower())

# --- cache is respected ----------------------------------------------------------------------
f2 = Fake({C1: 'Dismissed', C2: 'Pending', C3: 'Final Judgment'})
cache2 = {c: dict(r, at=NOW) for c, r in cache.items()}
out = LS.run_broward(feed(C1, C2, C3), cache2, transport=f2, clock=clock())
check('fresh cache entries are not re-asked', not f2.urls and out['fresh'] == 3)
stale = {c: dict(r, at=NOW - 20 * 86400) for c, r in cache.items()}
f3 = Fake({C1: 'Pending', C2: 'Pending', C3: 'Pending'})
LS.run_broward(feed(C1, C2, C3), stale, transport=f3, clock=clock())
check('stale entries are re-asked and a reinstated case clears', len(f3.urls) == 3 and not stale[C1]['dismissed'])

# --- failures store nothing and stop the pass ------------------------------------------------
f4 = Fake({}, special={C1: (403, b'{}'), C2: (403, b'{}')})
c4 = {}
out = LS.run_broward(feed(C1, C2), c4, transport=f4, clock=clock())
check('denied key stores nothing and stops after one request', not c4 and len(f4.urls) == 1 and out['stopped'] == 'denied', out)
f5 = Fake({}, special={C1: (500, b'{}')})
c5 = {}
LS.run_broward(feed(C1), c5, transport=f5, clock=clock())
check('a server error stores nothing', not c5)
f6 = Fake({}, special={C1: (200, b'<html>captcha</html>')})
c6 = {}
LS.run_broward(feed(C1), c6, transport=f6, clock=clock())
check('an html/captcha body stores nothing', not c6)
f7 = Fake({}, special={C1: (200, json.dumps({'Case_Number': 'CACE26999999', 'Disposition_Status': 'Dismissed'}).encode())})
c7 = {}
LS.run_broward(feed(C1), c7, transport=f7, clock=clock())
check('a response for a different case number is not trusted', not c7)

# --- unknown schema and outages ----------------------------------------------------------------
f10 = Fake({}, special={C1: (200, json.dumps({'Case_Number': api(C1), 'Foo': 1}).encode())})
c10 = {}
out = LS.run_broward(feed(C1), c10, transport=f10, clock=clock())
check('a payload with no disposition field stores nothing', not c10 and out['problems'].get('no_disposition') == 1, out)
def boom(url, timeout):
    raise OSError('connection reset ' + KEY)
many = ['CACE-26-%06d' % (200 + i) for i in range(12)]
c11 = {}
out = LS.run_broward(feed(*many), c11, transport=boom, clock=clock())
check('an outage stops after a run of failures, not at the deadline', out['stopped'] == 'consecutive_failures' and out['asked'] == 5, out)
check('transport error text (and the key) never reach the problem counts', KEY not in json.dumps(out) and set(out['problems']) <= {'error'}, out)

# --- budget ----------------------------------------------------------------------------------
os.environ['LP_BROWARD_MAX_CASES'] = '1'
f8 = Fake({C1: 'Pending', C2: 'Pending', C3: 'Pending'})
LS.run_broward(feed(C1, C2, C3), {}, transport=f8, clock=clock())
check('max cases caps the pass', len(f8.urls) == 1)
check('newest filing is asked first', api(C3) in f8.urls[0], f8.urls)
os.environ.pop('LP_BROWARD_MAX_CASES')

# --- non-Broward and bad numbers -------------------------------------------------------------
f9 = Fake({})
mixed = [{'case': '2026-001234-CA-01', 'county': 'MIAMI-DADE', 'date': '2026-10-01'},
         {'case': 'CASE', 'county': 'BROWARD', 'date': '2026-10-02'}]
LS.run_broward(mixed, {}, transport=f9, clock=clock())
check('Miami rows and non-case strings are never sent to the Broward API', not f9.urls)

# --- flag + key both required ----------------------------------------------------------------
os.environ.pop('BROWARD_CLERK_API_KEY')
check('flag without key: Broward not an adapter', 'BROWARD' not in LS.adapters())
os.environ['BROWARD_CLERK_API_KEY'] = KEY
os.environ.pop('DEALFLOW_LP_BROWARD')
check('key without flag: Broward not an adapter', 'BROWARD' not in LS.adapters())
os.environ['DEALFLOW_LP_BROWARD'] = '1'
check('both set: Broward is an adapter', 'BROWARD' in LS.adapters() and 'MIAMI-DADE' in LS.adapters())
check('COUNTY_ADAPTERS constant unchanged (Miami only)', LS.COUNTY_ADAPTERS == ('MIAMI-DADE',))

print('\n%s' % ('ALL PASS' if not FAILS else 'FAILED: ' + '; '.join(FAILS)))
sys.exit(1 if FAILS else 0)
