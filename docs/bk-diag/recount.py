import sys, json, time, collections, os
sys.path.insert(0, sys.argv[1])   # worktree with bk_lookup.py
import bk_lookup as B
main = sys.argv[2]               # read-only lead files
leads = B.load_leads(main)
cache = B.load_cache()
now = time.time()
print('leads', len(leads), 'cache', len(cache), 'max_age_days', B.max_age_days())
c = collections.Counter(); 
for k, ld in leads.items():
    ent = cache.get(k)
    cty = ld['county'] or '?'
    if ent is None: st = 'no_entry'
    elif ent.get('err'): st = 'err_fresh' if not B._needs_party_search(ent, now) else 'err_retry'
    elif not B._needs_party_search(ent, now): st = 'fresh_' + str(ent.get('verdict'))
    else: st = 'stale_or_unsearched'
    c[(cty, st)] += 1
for k in sorted(c): print(k, c[k])
need = [ld for ld in leads.values() if B._needs_party_search(cache.get(ld['key']), now)]
print('need_search', len(need))
bc = collections.Counter(ld['county'] for ld in need); print(dict(bc))
print('need with email/phone/mail', sum(1 for l in need if l.get('email')), sum(1 for l in need if l.get('phone')), sum(1 for l in need if l.get('mail')))
print('cache keys not in leads', sum(1 for k in cache if k not in leads))
ts=[e.get('t') for e in cache.values() if isinstance(e,dict) and e.get('t')]
print('cache t min/max', time.strftime('%F',time.localtime(min(ts))), time.strftime('%F',time.localtime(max(ts))))
vc=collections.Counter((e.get('verdict'), bool(e.get('searched')), bool(e.get('ok_check')), bool(e.get('err'))) for e in cache.values() if isinstance(e,dict)); print(vc)
