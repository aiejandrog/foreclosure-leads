import sys, time, itertools
sys.path.insert(0, sys.argv[1])
import bk_lookup as B
leads = B.load_leads(sys.argv[2]); items = B.load_filings().get('items') or {}
print('leads', len(leads), 'filings', len(items))
sample = dict(itertools.islice(leads.items(), 150))
cache = {}  # throwaway, never saved
t=time.time(); B.match_filings_to_leads(sample, items, cache, time.time()); dt=time.time()-t
print('150 leads: %.2fs -> full %.0fs (%.1f min)' % (dt, dt*len(leads)/150, dt*len(leads)/150/60))
