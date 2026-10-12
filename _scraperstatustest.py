"""scraper_status.py fail-safe contract (2026-10-12).

The header panel must NEVER break the board build: scrape_status_html() never raises and never
leaks a traceback or raw placeholder, for ANY log state; subst_build_facts survives the module
itself failing to import. Alejandro: "you made sure it doesn't break?" — this is the proof.
"""
import importlib
import os
import sys
import tempfile

import scraper_status as SS

fails = []
def rec(name, ok, extra=''):
    print(('PASS ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:100]) if extra else ''))
    if not ok:
        fails.append(name)

def html_ok(h):
    return isinstance(h, str) and 'Traceback' not in h and '__SCRAPER_STATUS__' not in h

rec('baseline renders', html_ok(SS.scrape_status_html()))

real = SS.LOG
SS.LOG = os.path.join(os.getcwd(), 'DOES_NOT_EXIST.log')
rec('missing log -> neutral, no raise', 'unavailable' in SS.scrape_status_html())

fd, p = tempfile.mkstemp(); os.close(fd)
open(p, 'wb').write(os.urandom(4096))
SS.LOG = p
rec('binary garbage -> no raise', html_ok(SS.scrape_status_html()))

open(p, 'w', encoding='utf-8').write('==================== REFRESH Mon 10/12/2026 12:21:39.33 ====\nMIAMI-DADE: 10 row(s)\n')
rec('killed mid-run (no ENDED) -> no raise', html_ok(SS.scrape_status_html()))

open(p, 'w', encoding='utf-8').write('==== REFRESH ENDED rc=0 Mon 13/45/2026 99:99:99.00 ====\n')
rec('impossible date -> no raise', html_ok(SS.scrape_status_html()))

open(p, 'w').write('')
rec('empty log -> neutral, no raise', 'unavailable' in SS.scrape_status_html())
os.remove(p)
SS.LOG = real

import foreclosure_leads as F
sys.modules['scraper_status'] = None      # make `import scraper_status` yield a broken module
tpl = ('HEAD __SCRAPER_STATUS__ __UPDATED__ __ENTITY_LLC__ __CLIENT_EMAIL__ '
       '__EMBLEM_W__ __EMBLEM_H__ __BOARDURL__ __ENTITY_VERIFIED__')
try:
    out = F.subst_build_facts(tpl, 'today')
    rec('build survives a broken scraper_status import', '__SCRAPER_STATUS__' not in out)
except Exception as e:
    rec('build survives a broken scraper_status import', False, repr(e)[:80])
finally:
    sys.modules.pop('scraper_status', None)
    importlib.import_module('scraper_status')

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
