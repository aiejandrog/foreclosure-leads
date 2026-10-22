"""wp_web.py parser + cache shape, on a synthetic copy of the pasted whitepages.com layout.

No browser, no network, no real people: the fixture mirrors the visible-text structure Alejandro
pasted on 2026-10-22 (names and numbers invented). Pins that the parser pulls owners/residents,
cell vs landline, emails and "—" gaps, that the cache entry is the shape wp_contacts reads, and
that a scraped entry grades the same way the Pro API path does (owner by name, resident same-name
is 'hh' to the bake, a different name is dropped).

Run: python _wpwebtest.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ['WP_WEB_MAX_LEADS'] = '15'

import wp_web as W          # noqa: E402
import wp_contacts as C     # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


ADDR = '1723 CANAL DR, CORAL SPRINGS, FL 33071'
# Invented. Deed owner on our lead: ROMERO, MARISOL. Whitepages files her as a RESIDENT and files
# a relative (Daniel Romero) as "Owner" — the exact inversion from the 10-22 example.
PAGE = """
1723 Canal Dr
Coral Springs, FL 33071
7 beds · 7 baths · Single Family Home · 5 owners or residents

Owners & Residents

Daniel Alberto Romero
, age 58
Broker at M5

Owner

FL, Davie (15 mi away)

Cell Phone

(305) 200-1001

Landline

(305) 200-1002

Email Address

dromero@example.com

Marisol Romero
, age 55

Resident

Cell Phone

(305) 200-2001

Landline

(212) 200-2002

Email Address

marisol@example.com

Tomas Nunez
, age 61

Resident

Cell Phone

—

Landline

(954) 200-3003

Email Address

—
"""

people = W.parse_people_text(PAGE)
by = {p['name']: p for p in people}

print('\nPARSE')
rec('three people parsed', len(people) == 3, [p['name'] for p in people])
rec('Daniel is the owner', by.get('Daniel Alberto Romero', {}).get('role') == 'owner')
rec('Marisol is a resident', by.get('Marisol Romero', {}).get('role') == 'resident')
rec('age read', by.get('Daniel Alberto Romero', {}).get('age') == 58, by.get('Daniel Alberto Romero'))
dp = {p['number']: p['type'] for p in by['Daniel Alberto Romero']['phones']}
rec('cell tagged mobile', dp.get('3052001001') == 'mobile', dp)
rec('landline tagged landline', dp.get('3052001002') == 'landline', dp)
rec('owner email read', by['Daniel Alberto Romero']['emails'] == ['dromero@example.com'])
tp = {p['number']: p['type'] for p in by['Tomas Nunez']['phones']}
rec('"—" cell is skipped, landline still read', tp == {'9542003003': 'landline'}, tp)
rec('no phantom email on the "—" person', by['Tomas Nunez']['emails'] == [])
rec('no number leaks between people (count)',
    sum(len(p['phones']) for p in people) == 5, [(p['name'], len(p['phones'])) for p in people])
rec('occupation line is not a name', 'Broker at M5' not in by)

print('\nCACHE SHAPE')
entry = W.to_cache_entry(people, ADDR)
rec('http 200', entry['_http'] == 200)
rec('tagged wpweb', entry['_src'] == 'wpweb')
po = (entry['result']['ownership_info']['person_owners'])
res = entry['result']['residents']
rec('one owner, two residents in the record', len(po) == 1 and len(res) == 2, (len(po), len(res)))
rec('owner carries the searched address', po[0]['current_addresses'][0]['full_address'] == ADDR)

print('\nGRADING: THE BOARD/BAKE PATH (lead_contacts)')
LEAD = {'case': 'CACE-25-0001', 'owners': 'ROMERO, MARISOL', 'addr': ADDR}
# through whitepages_lookup.lead_contacts, the same function the board bake and badnum compare use.
# The bake tags EVERY resident household, so the owner-filed-as-resident stays household here —
# the documented not-fixed gap (re-tagging would widen who may be texted). The card fixes display.
import whitepages_lookup as WL  # noqa: E402
k = WL.lead_contacts(LEAD, entry)
rec('a resident (even the owner by name) is household on the bake path', '3052002001' in k['household'])
rec('the relative filed as owner is household, not owner (name bar)',
    '3052001001' in k['household'] and '3052001001' not in k['owner'], k)
rec('nobody is wrongly graded owner from this page', k['owner'] == set(), k['owner'])

print('\nCALL MODE CARD READS IT (wp_phone_kinds)')
import call_mode as CM  # noqa: E402
# wpAllPhones as the bake builds it: a property owner whose name fails the owner bar is tagged
# resident (notOwner); residents are tagged resident. _wp_phone_kinds then name-checks residents.
allp = [{'n': '3052002001', 'resident': True, 'source': 'property', 'owner': 'Marisol Romero'},
        {'n': '3052001001', 'resident': True, 'source': 'property', 'owner': 'Daniel Alberto Romero'}]
kinds = CM._wp_phone_kinds({'wpAllPhones': allp, 'owners': 'ROMERO, MARISOL'})
rec('the owner filed as a resident reads as owner on the card', kinds.get('3052002001') == 'o', kinds)
rec('the relative filed as owner reads as household', kinds.get('3052001001') == 'h', kinds)

print('\nEMPTY / JUNK')
rec('no text -> no people', W.parse_people_text('') == [])
rec('a page with no roles -> no people', W.parse_people_text('just some words\nno owners here') == [])
rec('a person with no contact point is dropped',
    W.parse_people_text('Jane Q Public\nResident\nCell Phone\n—\nEmail Address\n—') == [])

print('\nLEAD SELECTION (no browser)')
leads = [{'case': 'A-1', 'owners': 'SMITH JOHN', 'addr': '5 OAK ST, MIAMI, FL 33101', 'auction': '11/01/2026', 'days': 10},
         {'case': 'B-1', 'owners': 'ACME LLC', 'addr': '6 OAK ST, MIAMI, FL 33101', 'auction': '11/01/2026', 'days': 10}]
WL.load_all_leads = lambda: leads
WL.load_skiptrace = lambda: {}
sel = W._select(type('A', (), {'case': None, 'pick_callable': 15})())
rec('company owner not selected', all(WL._lead_key(r) != 'B-1' for r in sel), [WL._lead_key(r) for r in sel])
rec('per-run cap honoured', len(sel) <= 15)
leads.append({'case': 'C-1', 'owners': 'DOE JANE', 'addr': '7 OAK ST, MIAMI, FL 33101', 'auction': '11/02/2026', 'days': 11})
WL.load_skiptrace = lambda: {'C-1': {'phones': [{'number': '3055550100'}]}}
cm = W._select(type('A', (), {'case': None, 'pick_callable': 0, 'compare': 5})())
rec('--compare takes only leads that already have trace numbers', [WL._lead_key(r) for r in cm] == ['C-1'], [WL._lead_key(r) for r in cm])

print('\nINGEST A HAND-COPIED PAGE (no browser)')
td = tempfile.mkdtemp()
import paths as _P  # noqa: E402
_P.DEALFLOW_DIR = td
WL.CACHE = os.path.join(td, 'wp.json')
W.CACHE = WL.CACHE
leads.append({'case': 'D-1', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/03/2026', 'days': 12})
open(os.path.join(td, 'p.txt'), 'w', encoding='utf-8').write(PAGE)
rc = W.ingest_file('D-1', os.path.join(td, 'p.txt'))
import json as _j  # noqa: E402
c = _j.load(open(WL.CACHE, encoding='utf-8'))
rec('ingest wrote a cache entry', rc == 0 and c.get('D-1', {}).get('_src') == 'wpweb-file', c.keys())
rec('block page is recognised', bool(W._BLOCK_RE.search('Access is temporarily restricted')))
rec('a normal page is not mistaken for a block', not W._BLOCK_RE.search(PAGE))
open(os.path.join(td, 'e.txt'), 'w').write('Access is temporarily restricted')
rec('a block page ingests nothing', W.ingest_file('D-1', os.path.join(td, 'e.txt')) == 1)

print('\nHAND-ASSISTED MODE (no browser automation, watches the clipboard)')
opened = []
import webbrowser  # noqa: E402
webbrowser.open = lambda u, *a, **k: opened.append(u)


class _Clock:
    t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


W.time = _Clock()
# per lead: the baseline read, then what each 1s poll sees
seq = ['old', PAGE,                                   # E-0: copied the page -> cached
       PAGE, 'Access is temporarily restricted',      # E-1: copied a block page -> skipped
       'Access is temporarily restricted', 'hello',   # E-2: copied something else -> skipped
       'hello']                                       # E-3: never copies -> times out, skipped
W._clipboard_text = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
WL.CACHE = os.path.join(td, 'wp2.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'E-%d' % i, 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13 + i}
            for i in range(4)]
n_ok = W.assist(list(leads), wait_s=5)
c2 = _j.load(open(WL.CACHE, encoding='utf-8')) if os.path.exists(WL.CACHE) else {}
rec('assist cached only the real page (block, other text and timeout skipped)',
    n_ok == 1 and list(c2) == ['E-0'] and c2['E-0']['_src'] == 'wpweb-hand', (n_ok, list(c2)))
rec('every lead opened in the default browser, nothing driven', len(opened) == 4
    and all(u.startswith('https://www.whitepages.com/address/') for u in opened), opened[:2])
rec('nothing is read from the terminal', 'input(' not in open(os.path.join(HERE, 'wp_web.py'), encoding='utf-8').read().split('def assist(')[1].split('def _parcel(')[0])

print('\nREAL-PAGE SHAPES (10-22, numbers and names masked)')
COMPANY = ('Overview\nOwners & Residents\nProperty Financials\nOwners, Residents & Occupancy Status\n'
           'Occupancy Status\nVacant\nOwners & Residents\nLegal Way Usa\nWhitepages Property Intel\n'
           "Property Details for 1 Main St Apt 2\n... identified by Assessor's Parcel Number (APN) "
           '74-43-43-27-27-004-0040. Built in 1980')
rec('a business-owned page parses no people', W.parse_people_text(COMPANY) == [])
rec('and says why', 'no person owner or resident' in W._why_empty(COMPANY), W._why_empty(COMPANY))
rec('APN read off the page', W.page_apn(COMPANY) == '74-43-43-27-27-004-0040', W.page_apn(COMPANY))
e2 = W.to_cache_entry(people, ADDR, page_text=COMPANY)
rec('APN carried into the cache entry', e2['result'].get('apn') == '74-43-43-27-27-004-0040')
import wp_contacts as _C  # noqa: E402
rec('the parcel gate then reads it (same digits = ok)',
    _C.wp_parcel_verdict(e2['result']['apn'], '74434327270040040') == 'ok')
rec('and a different folio = mismatch', _C.wp_parcel_verdict(e2['result']['apn'], '74434327270040041') == 'mismatch')
rec('no APN on the page: no apn key', 'apn' not in W.to_cache_entry(people, ADDR, page_text=PAGE)['result'])
rec('not-a-page copy explained', W._why_empty('hello') == 'that was not a Whitepages address page')

st = W.structure(PAGE)
rec('structure masks names, numbers and emails', 'Romero' not in st and '200-1001' not in st
    and 'example.com' not in st and 'NAME(3)' in st and '(999) 999-9999' in st and 'Owner' in st, st[:300])
rec('assist keeps each copied page outside the repo', os.path.exists(os.path.join(td, 'wpweb_pages', 'E-0.txt')))

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
