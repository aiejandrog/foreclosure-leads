import sys, time, collections
sys.path.insert(0, sys.argv[1])
import bk_lookup as B
leads = B.load_leads(sys.argv[2]); cache = B.load_cache(); now=time.time()
c=collections.Counter(); 
for ld in leads.values():
    if ld['county'] not in ('BROWARD','PALM BEACH'): continue
    if not B._needs_party_search(cache.get(ld['key']), now): continue
    ow = B.lead_owners(ld)
    qs=[]
    for o in ow[:4]:
        q=B.query_for(o)
        if q and q not in qs: qs.append(q)
    c['unsearchable' if (not ow or not qs or len(ow)>4) else 'q%d'%len(qs)] += 1
print(sorted(c.items()))
