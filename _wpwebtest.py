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

print('\nPUBLIC SUMMARY WITH UNDISCLOSED ROLES')
# Manual copy of the public summary layout, fabricated contacts only. The role link is NOT
# evidence of ownership. Decade ages must not become part of the person's name.
summary = '''Owners, Residents & Occupancy Status
Synthetic Alpha Person, age 40s
Owner or Resident?View Role
Cell Phone
(305) 555-0101
Landline
(305) 555-0102
Email Address
alpha@example.com
Synthetic Beta Person, age 30s
Owner or Resident?
View Role
Cell Phone
(305) 555-0103
Landline
—
Email Address
beta@example.com
'''
summary_people = W.parse_people_text(summary)
rec('public summary imports both people', len(summary_people) == 2, summary_people)
rec('public summary preserves names without decade ages',
    [p['name'] for p in summary_people] == ['Synthetic Alpha Person', 'Synthetic Beta Person'])
rec('a hidden role is unknown, never owner',
    len(summary_people) == 2 and all(p['role'] == 'unknown' for p in summary_people))
rec('summary phone numbers stay with their person',
    [[p['number'] for p in person['phones']] for person in summary_people]
    == [['3055550101', '3055550102'], ['3055550103']])
summary_entry = W.to_cache_entry(summary_people, ADDR)
rec('unknown roles never enter person_owners',
    summary_entry['result']['ownership_info']['person_owners'] == [])
rec('unknown roles use the conservative household path',
    len(summary_entry['result']['residents']) == 2)

print('\nSUMMARY OCCUPATION AND AN EMAIL-ONLY PERSON')
occupation_summary = '''Owners & Residents
Synthetic Alpha Person, age 40s
Software Engineer at Example Company
Owner or Resident?View Role
Cell Phone
(305) 555-0191
Landline
(305) 555-0192
Email Address
alpha@example.com
Fictional Beta Contact
Owner or Resident?View Role
Cell Phone
—
Landline
—
Email Address
beta@example.com
'''
occupation_people = W.parse_people_text(occupation_summary)
rec('occupation is not used as the contact name',
    [p['name'] for p in occupation_people] == ['Synthetic Alpha Person', 'Fictional Beta Contact'],
    [p['name'] for p in occupation_people])
rec('age anchor survives the intervening occupation',
    bool(occupation_people) and occupation_people[0]['age'] == 40)
rec('phone values stay with the named person before the occupation',
    bool(occupation_people) and [p['number'] for p in occupation_people[0]['phones']]
    == ['3055550191', '3055550192'])
rec('an email-only person never borrows the other person phones',
    len(occupation_people) == 2 and occupation_people[1]['phones'] == []
    and occupation_people[1]['emails'] == ['beta@example.com'])
import whitepages_lookup as _summary_WL
occupation_contacts = _summary_WL.lead_contacts(
    {'case': 'SYNTHETIC-PUBLIC-SUMMARY', 'owners': 'CONTACT, FICTIONAL BETA', 'addr': ADDR},
    W.to_cache_entry(occupation_people, ADDR))
rec('the other listed person phone cannot become the email-only owners phone',
    occupation_contacts['owner'] == set()
    and {'3055550191', '3055550192'}.issubset(occupation_contacts['household']), occupation_contacts)

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
EMPTY = ('Home\n1 NW 1st Ct Apt 2\nMargate, FL 33063\nLooking for who owns 1 NW 1st Ct Apt 2 Margate, FL 33063 '
         'and how to contact them?\nLooking for who lives at 1 NW 1st Ct Apt 2 Margate, FL 33063? There are no '
         'people living at this property.\nResidents\nNo one currently lives at 1 NW 1st Ct Apt 2, according to public records.')
rec('the empty-address layout counts as a Whitepages page', bool(W._PAGE_MARK.search(EMPTY)))
rec('and says no one is on file', W._why_empty(EMPTY) == 'Whitepages has no one on file at this address', W._why_empty(EMPTY))
rec('not-a-page copy explained', W._why_empty('hello') == 'that was not a Whitepages address page')

st = W.structure(PAGE)
rec('structure masks names, numbers and emails', 'Romero' not in st and '200-1001' not in st
    and 'example.com' not in st and 'NAME(3)' in st and '(999) 999-9999' in st and 'Owner' in st, st[:300])
rec('assist keeps each copied page outside the repo', os.path.exists(os.path.join(td, 'wpweb_pages', 'E-0.txt')))

print('\nREVIEW FIXES (10-22): trailing numbers, other addresses, accents, overwrite, reparse')
TAIL = PAGE + "\nAssessor's Parcel Number (APN) 3040210010010\nNeed help? Call 1-844-555-0199\n(800) 555-0100\n"
tp2 = {p['name']: [x['number'] for x in p['phones']] for p in W.parse_people_text(TAIL)}
rec('a parcel number / help line after the last person is not their phone',
    tp2.get('Tomas Nunez') == ['9542003003'], tp2.get('Tomas Nunez'))
ACC = ('Owners & Residents\nJohn Smith\nOwner\nJosé García Muñoz\n, age 40\nResident\n'
       'Cell Phone\n(305) 200-4004\n')
pa = {p['name']: [x['number'] for x in p['phones']] for p in W.parse_people_text(ACC)}
rec('an accented name is parsed as its own person', pa == {'José García Muñoz': ['3052004004']}, pa)
rec('and masked by --structure', 'José' not in W.structure(ACC) and 'Muñoz' not in W.structure(ACC), W.structure(ACC))
rec('page for the lead address matches', W.page_matches_address(PAGE, '1723 CANAL DR'))
rec('9 CT on the lead matches 9th Ct on the page', W.page_matches_address('Home\n1 NW 9th Ct Apt 2\n', '1 NW 9 CT #2'))
rec('page for another house is refused', not W.page_matches_address(PAGE, '1725 CANAL DR'))
rec('page for another street is refused', not W.page_matches_address(PAGE, '1723 PALM DR'))
rec('a condo needs its own unit', W.page_matches_address(EMPTY, '1 NW 1ST CT APT 2')
    and not W.page_matches_address(EMPTY, '1 NW 1ST CT APT 3'))
# assist: a copied page for a different address is skipped, not cached as this lead
W.time = _Clock()
OTHER = PAGE.replace('1723 Canal Dr', '1725 Canal Dr')
seq[:] = ['old', OTHER, OTHER]
WL.CACHE = os.path.join(td, 'wp3.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'F-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
rec('assist refuses a page for a different address', W.assist(list(leads), wait_s=5) == 0
    and not os.path.exists(WL.CACHE) or 'F-0' not in _j.load(open(WL.CACHE, encoding='utf-8')))
# --from-file never replaces an existing lookup without --refresh
WL.CACHE = os.path.join(td, 'wp4.json')
W.CACHE = WL.CACHE
_j.dump({'F-0': {'result': {'apn': 'API'}, '_http': 200, '_ts': 1}}, open(WL.CACHE, 'w'))
open(os.path.join(td, 'p.txt'), 'w', encoding='utf-8').write(PAGE)
rec('--from-file keeps an API entry', W.ingest_file('F-0', os.path.join(td, 'p.txt')) == 1
    and _j.load(open(WL.CACHE))['F-0']['result']['apn'] == 'API')
# --reparse rebuilds kept pages and drops what fails; API entries untouched
leads[:] = [{'case': c, 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}
            for c in ('G-0', 'G-1', 'G-2')]
W._keep_page('G-0', TAIL)
W._keep_page('G-1', OTHER)
_j.dump({'G-0': {'result': {}, '_src': 'wpweb-hand', '_http': 200, '_ts': 5},
         'G-1': {'result': {}, '_src': 'wpweb-hand', '_http': 200, '_ts': 5},
         'G-2': {'result': {}, '_src': 'wpweb-hand', '_http': 200, '_ts': 5},
         'API-1': {'result': {'apn': 'x'}, '_http': 200, '_ts': 1}}, open(WL.CACHE, 'w'))
W.reparse()
c5 = _j.load(open(WL.CACHE, encoding='utf-8'))
g0 = c5.get('G-0', {}).get('result', {})
nums = [x['number'] for p in (g0.get('residents') or []) + ((g0.get('ownership_info') or {}).get('person_owners') or [])
        for x in p['phones']]
rec('reparse rebuilds a kept page with the fixed parser', '8445550199' not in nums and '3040210010' not in nums
    and '9542003003' in nums and c5['G-0']['_ts'] == 5, nums)
rec('reparse drops a wrong-address page, leaves one with no kept copy', 'G-1' not in c5 and 'G-2' in c5, list(c5))
rec('reparse never touches an API entry', c5.get('API-1', {}).get('result') == {'apn': 'x'})

# a lead whose kept page is its own address with nobody on it is not offered again
leads[:] = [{'case': 'H-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13},
            {'case': 'H-1', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 14}]
WL.load_skiptrace = lambda: {'H-0': {'phones': [{'number': '3055550100'}]}, 'H-1': {'phones': [{'number': '3055550101'}]}}
WL.CACHE = os.path.join(td, 'wp6.json')
W.CACHE = WL.CACHE
W._keep_page('H-0', '1723 Canal Dr\nCoral Springs, FL 33071\nOwners & Residents\nLegal Way Usa\n')
ks = [WL._lead_key(r) for r in W._select(type('A', (), {'case': None, 'pick_callable': 0, 'compare': 5, 'refresh': False})())]
rec('--compare skips a lead whose copied page had nobody on it', ks == ['H-1'], ks)
ks = [WL._lead_key(r) for r in W._select(type('A', (), {'case': None, 'pick_callable': 0, 'compare': 5, 'refresh': True})())]
rec('--refresh offers it again', 'H-0' in ks, ks)

rec('a # unit on the lead must match: #14 page vs #12 lead refused',
    not W.page_matches_address('123 Main St #14, Miami', '123 MAIN ST #12'))
rec('#12 lead matches an Apt 12 page', W.page_matches_address('123 Main St Apt 12', '123 MAIN ST #12'))
rec('Apt 12 lead matches a #12 page', W.page_matches_address('123 Main St #12', '123 MAIN ST APT 12'))
rec('Northwest 5th Street matches NW 5th St', W.page_matches_address('100 NW 5th St', '100 NORTHWEST 5TH STREET'))
# reparse keeps a paid Person layer and refuses to run on empty lead files
leads[:] = [{'case': 'J-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
W._keep_page('J-0', OTHER)
_j.dump({'J-0': {'result': {}, '_src': 'wpweb-hand', '_person': {'x': 1}, '_http': 200, '_ts': 5}}, open(WL.CACHE, 'w'))
W.reparse()
c7 = _j.load(open(WL.CACHE, encoding='utf-8'))
rec('reparse keeps a paid Person layer when it drops the page half',
    c7.get('J-0', {}).get('_person') == {'x': 1} and 'result' not in c7['J-0'], c7.get('J-0'))
_ll = WL.load_all_leads
WL.load_all_leads = lambda: []
rec('reparse refuses when the lead files read empty', W.reparse() == 1 and 'J-0' in _j.load(open(WL.CACHE, encoding='utf-8')))
WL.load_all_leads = _ll

rec('"West Ave" lead refuses an "Alton Ave" page', not W.page_matches_address('1234 Alton Ave', '1234 WEST AVE'))
rec('"West Ave" lead matches a "West Ave" page', W.page_matches_address('1234 West Ave', '1234 WEST AVE'))
SIGNEDOUT = '1723 Canal Dr\nCoral Springs, FL 33071\nOwners & Residents\nMarisol Romero\nResident\nCell Phone\nSign in to view\n'
leads[:] = [{'case': 'K-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
WL.load_skiptrace = lambda: {'K-0': {'phones': [{'number': '3055550100'}]}}
W._keep_page('K-0', SIGNEDOUT)
rec('a signed-out copy (people, no phones) does not hide the lead', not W._known_empty(leads[0]))

# a page for another address gets one retry with the lead's address on the clipboard
W.time = _Clock()
put = []
W._set_clipboard = lambda a: put.append(a) or True
seq[:] = ['old', OTHER, ADDR, PAGE, PAGE]
WL.CACHE = os.path.join(td, 'wp8.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'L-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
n8 = W.assist(list(leads), wait_s=10)
rec('wrong page -> address on clipboard -> the right page is cached', n8 == 1 and put == [ADDR]
    and 'L-0' in _j.load(open(WL.CACHE, encoding='utf-8')), (n8, put))

rec('"West Ave" lead refuses a "W 41st St" page', not W.page_matches_address('1234 W 41st St, Miami Beach', '1234 WEST AVE'))
rec('"West Ave" lead matches "1234 W Avenue"', W.page_matches_address('1234 W Avenue', '1234 WEST AVE'))
rec('--addr-check masks a surname that is a street word', 'Lane' not in W._shape('Jane Lane, 54, owner of 1234 West Ave')
    and 'West' not in W._shape('Jane Lane, 54, owner of 1234 West Ave'))

W.time = _Clock()
seq[:] = ['old', OTHER, ADDR + '\r\n', 'not a page', PAGE, PAGE]
WL.CACHE = os.path.join(td, 'wp9.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'M-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
rec('the address echoed back with CR/LF, or a stray copy, is not taken as the page',
    W.assist(list(leads), wait_s=10) == 1 and 'M-0' in _j.load(open(WL.CACHE, encoding='utf-8')))

CITY = ('People SearchReverse PhoneReverse Address\nSunrise, FL Property Records\n4,687 of 23,435 results\n'
        'Search property records in Sunrise, FL to see who lives at or owns each home\n'
        '[1723 Canal Dr](https://www.whitepages.com/address/1723-Canal-Dr/Sunrise-FL/Abc)\n')
rec('the city property list is named as such', W._why_empty(CITY) == "Whitepages showed the city's property list, not the house", W._why_empty(CITY))
W.time = _Clock()
put[:] = []
seq[:] = ['old', CITY, ADDR, PAGE, PAGE]
WL.CACHE = os.path.join(td, 'wp10.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'N-0', 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
rec('city list -> address on clipboard -> the house page is cached', W.assist(list(leads), wait_s=10) == 1 and put == [ADDR])
import wp_linkprobe as _LP  # noqa: E402
_S = '2634 SE 19 CT #205C'
_U = 'https://www.whitepages.com/address/'
_lp = [
    (_LP.classify(_U + '2634-SE-19th-Ct-205th-C/HOMESTEAD-FL/Id', '', _S, '205C'), 'unit'),
    (_LP.classify(_U + '2634-SE-19th-Ct/Homestead-FL', '2634 SE 19th Ct, Homestead, FL 33035', _S, '205C',
                  _U + '2634-SE-19th-Ct-205th-C/HOMESTEAD-FL'), 'building'),
    (_LP.classify(_U + '2634-SE-19th-Ct-Apt-2051/X-FL/Id', '', _S, '205C'), 'other'),
    (_LP.classify(_U + '123-NW-1st-St/Miami-FL/Id', '123 NW 1st St, Miami, FL 33125', '123 NW 1 ST #5', '5'), 'building'),
    (_LP.classify(_U + '123-NW-1st-St-Apt-205/Miami-FL/Id', '', '123 NW 1 ST #5', '5'), 'other'),
    (_LP.classify(_U + '123-NW-1st-St-Apt-1/Miami-FL/Id', '', '123 NW 1 ST #1', '1'), 'unit'),
    (_LP.classify(_U + 'x', 'Just a moment...', _S, '205C', _U + 'x'), 'blocked'),
    (_LP.classify(_U + 'x', 'Whitepages', _S, '205C', _U + 'x'), 'none'),
    (_LP.classify(_U + '2929-NW-123rd-Ter/Miami-FL/Id', '', '2929 NW 123 TER', ''), 'house'),
    (_LP.classify(_U + '2929-NW-124th-Ter/Miami-FL/Id', '', '2929 NW 123 TER', ''), 'other'),
]
rec('link probe: whole-unit match, no ZIP/street/substring false hits, bot check, no-redirect',
    all(g == w for g, w in _lp), _lp)
rec('link probe: a bare "205-C" after the suffix is a unit', _LP._street_unit('123 MAIN ST 205-C') == ('123 Main St', '205-C'))
rec('our street in Whitepages link spelling', W._wp_street('8600 NW 34 PL #B105') == '8600 NW 34th Pl Apt B105'
    and W._wp_street('2929 NW 123 TER') == '2929 NW 123rd Ter' and W._wp_street('10442 SUNRISE LAKES BLVD 105')
    == '10442 Sunrise Lakes Blvd Apt 105', W._wp_street('8600 NW 34 PL #B105'))

rec('State Road 7 keeps its number, not a unit', W._wp_street('4500 N STATE ROAD 7') == '4500 N State Road 7', W._wp_street('4500 N STATE ROAD 7'))
rec('US Highway 1 is not "1st"', W._wp_street('1234 US HIGHWAY 1') == '1234 US Highway 1', W._wp_street('1234 US HIGHWAY 1'))
rec('a State Rd page without the 7 is refused', not W.page_matches_address('4500 N State Rd, Lauderdale Lakes', '4500 N STATE ROAD 7'))
rec('a State Road 7 page matches', W.page_matches_address('4500 N State Road 7, Lauderdale Lakes', '4500 N STATE ROAD 7'))
rec('a house page with a search-records link is not the city list',
    W._why_empty(PAGE + '\nSearch property records in Coral Springs, FL\n') != "Whitepages showed the city's property list, not the house")

# selection skips cached leads and leads with two misses this week
leads[:] = [{'case': 'P-%d' % i, 'owners': 'ROMERO, MARISOL', 'addr': ADDR.replace('1723', str(1800 + i)),
             'auction': '11/04/2026', 'days': 13 + i} for i in range(3)]
WL.load_skiptrace = lambda: {'P-%d' % i: {'phones': [{'number': '305555010%d' % i}]} for i in range(3)}
WL.CACHE = os.path.join(td, 'wp11.json')
W.CACHE = WL.CACHE
_j.dump({'P-0': {'result': {}, '_src': 'wpweb-hand', '_http': 200, '_ts': 1}}, open(WL.CACHE, 'w'))
W._note_attempt('P-1')
W._note_attempt('P-1')
ks = [WL._lead_key(r) for r in W._select(type('A', (), {'case': None, 'pick_callable': 0, 'compare': 5, 'refresh': False})())]
rec('a run opens only fresh leads (not cached, not tried twice)', ks == ['P-2'], ks)

T = 'Home\nProperty Details for 100 NW 5th St Apt 2\n'
rec('--addr-check says which part differs, no values', W._part_diff('100 NW 5 ST APT 3', 'Property Details for 100 NW 5th St Apt 2')
    == 'house same, street same, unit DIFFERENT', W._part_diff('100 NW 5 ST APT 3', 'Property Details for 100 NW 5th St Apt 2'))
rec('lead unit with no unit on the page', W._part_diff('100 NW 5 ST #3', 'Property Details for 100 NW 5th St').endswith('lead has a unit, page has none'))
W._keep_page('R-0', T, rejected=True)
rec('a rejected copy is kept apart, never as the lead page', os.path.exists(os.path.join(td, 'wpweb_pages', 'rejected', 'R-0.txt'))
    and not os.path.exists(os.path.join(td, 'wpweb_pages', 'R-0.txt')))
W._keep_page('R-0', T)
rec('the lead page coming in clears its old rejected copy', not os.path.exists(os.path.join(td, 'wpweb_pages', 'rejected', 'R-0.txt'))
    and os.path.exists(os.path.join(td, 'wpweb_pages', 'R-0.txt')))
rec('a unit number equal to the house is not "house same"',
    W._part_diff('1723 NW 5 ST', 'Property Details for 1500 Brickell Ave Apt 1723').startswith('house DIFFERENT, street DIFFERENT'))
rec('a unit number equal to the street is not "street same"',
    W._part_diff('100 NW 5 ST APT 3', 'Property Details for 100 NW 15th St Apt 5').startswith('house same, street DIFFERENT'))
rec('UNIT 3-B and #3B are the same unit', W._part_diff('100 NW 5 ST UNIT 3-B', 'Property Details for 100 NW 5th St #3B').endswith('unit same'),
    W._part_diff('100 NW 5 ST UNIT 3-B', 'Property Details for 100 NW 5th St #3B'))
rec('--retry-misses refuses the automated run', W.main(['--retry-misses']) == 2)

leads[:] = [{'case': 'S-%d' % i, 'owners': 'ROMERO, MARISOL', 'addr': ADDR.replace('1723', str(1900 + i)),
             'auction': '11/04/2026', 'days': 13 + i} for i in range(2)]
WL.load_skiptrace = lambda: {}
W._note_attempt('S-1')
ks = [WL._lead_key(r) for r in W._select(type('A', (), {'case': None, 'pick_callable': 0, 'compare': 0, 'refresh': False, 'retry_misses': True})())]
rec('--retry-misses reopens only leads that missed', ks == ['S-1'], ks)

print('\nOWNER-NAME SEARCH (no page for the address)')
rec('name url is First-Last / City-FL',
    W._name_url({'owners': 'ROMERO, MARISOL A', 'addr': ADDR}) == W.BASE + '/name/Marisol-Romero/Coral-Springs-FL',
    W._name_url({'owners': 'ROMERO, MARISOL A', 'addr': ADDR}))
rec('no name url for an entity owner', W._name_url({'owners': 'ACME HOLDINGS LLC', 'addr': ADDR}) == '')
# Invented person page: the owner, her numbers, her address, then relatives WITH numbers that must
# not be read as hers, and a toll-free help line.
PERSON = """Marisol A Romero, Age 54
Lives in Coral Springs, FL
Phone Numbers
(954) 555-0101
Mobile
(954) 555-0102 Landline
(844) 555-0000
Current Address
1723 Canal Dr, Coral Springs, FL 33071
Email
mromero@example.com
Possible Relatives
Daniel Romero, Age 30
(954) 555-0199
"""
r1, why1 = W.parse_person_page(PERSON, 'ROMERO, MARISOL', '1723 CANAL DR')
nums = [p['number'] for p in (r1 or {}).get('phones', [])]
rec('person page: the owner\'s two numbers, typed', nums == ['9545550101', '9545550102']
    and [p['type'] for p in r1['phones']] == ['mobile', 'landline'], (nums, why1))
rec('a relative\'s number below the relatives heading is not hers', '9545550199' not in nums)
rec('the email is hers', (r1 or {}).get('emails') == ['mromero@example.com'], r1)
rec('the record grades wp: owner placed at our address',
    C.grade_person_record('ROMERO, MARISOL', ADDR, r1) == 'wp', r1)
r2, why2 = W.parse_person_page(PERSON.replace('1723 Canal Dr', '88 Palm Way'), 'ROMERO, MARISOL', '1723 CANAL DR')
rec('a namesake page without our address is refused', r2 is None and 'address' in why2, why2)
r3, why3 = W.parse_person_page(PERSON.replace('Marisol A Romero', 'Marta Lopez'), 'ROMERO, MARISOL', '1723 CANAL DR')
rec('a page whose names do not clear the owner bar is refused', r3 is None and 'owner bar' in why3, why3)
LIST = 'People Search Results\n' + '\n'.join('Marisol Romero%s, Age %d\nCoral Springs, FL\n(954) 555-01%02d\n1723 Canal Dr' % (
    ' X' * i, 30 + i, i) for i in range(8))
r4, why4 = W.parse_person_page(LIST, 'ROMERO, MARISOL', '1723 CANAL DR')
rec('a copied results list is refused, not parsed', r4 is None and 'results list' in why4, why4)
ROW = "Marisol Romero, Age 54\n(954) 555-0101\n1723 Canal Dr\nJorge Diaz, Age 61\n(954) 555-0177\n"
r5, _ = W.parse_person_page(ROW, 'ROMERO, MARISOL', '1723 CANAL DR')
rec('the next person row (name + age) ends her block',
    r5 and [p['number'] for p in r5['phones']] == ['9545550101'], r5)
r6, why6 = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\nLives in Hialeah, FL\n"
                                "Jorge Diaz, Age 61\n1723 Canal Dr\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('our address on someone else\'s row does not place her at the house', r6 is None and 'address' in why6, why6)
r7, why7 = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\nPast Addresses\n1723 Canal Dr\n",
                                'ROMERO, MARISOL', '1723 CANAL DR')
rec('our address under past addresses does not count', r7 is None and 'address' in why7, why7)
r8, why8 = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\n1723 SW Canal Dr\n",
                                'ROMERO, MARISOL', '1723 NE CANAL DR')
rec('same number and street word in another quadrant is not our house', r8 is None, why8)
r9, why9 = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\n1723 Canal Dr\n"
                                "Marisol Romero, Age 71\n(954) 555-0188\n9 Oak St\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('two namesakes with ages (a short results list) is refused', r9 is None and 'results list' in why9, why9)
r10, _ = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\n1723 Canal Dr\nFamily\nMaria Garcia\n(305) 555-4444\n",
                              'ROMERO, MARISOL', '1723 CANAL DR')
rec('a family member with no age is not in her block', r10 and [p['number'] for p in r10['phones']] == ['9545550101'], r10)
r11, _ = W.parse_person_page("Marisol Romero, Age 54\n(954) 555-0101\n1723 Canal Dr\nMaria Garcia\n(305) 555-4444\n",
                              'ROMERO, MARISOL', '1723 CANAL DR')
rec('any later name line ends her block', r11 and [p['number'] for p in r11['phones']] == ['9545550101'], r11)
rec('the stored address is the page\'s own line', r11 and r11['current_addresses'][0]['full_address'] == '1723 Canal Dr', r11)
HEAD_ = "Marisol Romero, Age 54\n(954) 555-0101\n1723 Canal Dr\n"
for label, tail in (('"Jane Smith, 52" row', "Jane Smith, 52\n(305) 555-0002\n"),
                    ('"JANE DOE 52" row', "JANE DOE 52\n(305) 555-0002\n"),
                    ('"Relationships" heading', "Relationships\nJane Doe (Spouse)\n(305) 555-0002\n"),
                    ('a bare first name', "Jane\n(305) 555-0002\n"),
                    ('a number labelled with another name', "(305) 555-0002 - Mobile - Jane Doe\n")):
    rr, _ = W.parse_person_page(HEAD_ + tail, 'ROMERO, MARISOL', '1723 CANAL DR')
    rec('a relative after %s is not hers' % label, rr and [p['number'] for p in rr['phones']] == ['9545550101'], rr)
rr, why_ = W.parse_person_page("Marisol Romero\nCoral Springs, FL\nAge 54\n1723 Canal Dr\n(954) 555-0101\n"
                               "Marisol Romero\nHialeah, FL\nAge 30\n(954) 555-0110\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('two namesakes with the age two lines down is refused', rr is None, rr)
rr, _ = W.parse_person_page("Marisol Romero in Coral Springs\nPeter Jones and Marisol Romero are listed\n"
                            "1723 Canal Dr\n(954) 555-0111\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('a block that turns into another person\'s text is not hers', rr is None, rr)
for label, page in (('as someone else\'s relative', "Jane Doe, Age 52\nLives in Coral Springs, FL\nRelatives\n"
                     "Marisol Romero, Age 54\n(305) 555-0020\n1723 Canal Dr\n"),
                    ('in a people-also-viewed footer', "Jane Doe, Age 52\n1723 Canal Dr\nPeople also viewed\n"
                     "Marisol Romero, Age 54\n(305) 555-0022\n1723 Canal Dr\n")):
    rr, _ = W.parse_person_page(page, 'ROMERO, MARISOL', '1723 CANAL DR')
    rec('the owner listed %s is refused' % label, rr is None, rr)
for label, tail in (('a bulleted name', "\u2022 Jane Doe\n(305) 555-0023\n"), ('a numbered name', "1. Jane Doe\n(305) 555-0024\n"), ('a numbered name, no dot', "1 Jane Doe\n(305) 555-0026\n"),
                    ('"Robert Doe II"', "Robert Doe II\n(305) 555-0002\n"), ('"JANE DOE JR"', "JANE DOE JR\n(305) 555-0004\n"),
                    ('"TONY NG"', "TONY NG\n(305) 555-0005\n"),
                    ('"Lives in ... with" a stranger', "Lives in Miami, FL with Jane Doe, 52\n(305) 555-0006\n"),
                    ('"Lived at ... Related to"', "Lived at 9 NW 1 Ave \u00b7 Related to Jane Doe\n(305) 555-0007\n"),
                    ('a name made of label words', "Home Carrier\n(305) 555-0008\n"),
                    ('an accented name with a bare age', "\u00cd\u00f1igo N\u00fa\u00f1ez, 40\n(305) 555-0025\n")):
    rr, _ = W.parse_person_page(HEAD_ + tail, 'ROMERO, MARISOL', '1723 CANAL DR')
    rec('a relative after %s is not hers' % label, rr and [p['number'] for p in rr['phones']] == ['9545550101'], rr)
rr, _ = W.parse_person_page("Marisol Romero\n(954) 555-0101\n1723 Canal Dr\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('an owner name with no age (not a profile) is refused', rr is None, rr)
rr, _ = W.parse_person_page(HEAD_ + "(954) 555-0103 - Wireless - T-Mobile USA\n", 'ROMERO, MARISOL', '1723 CANAL DR')
rec('a number with only a type and carrier is still hers', rr and len(rr['phones']) == 2 and rr['phones'][1]['type'] == 'mobile', rr)
rec('--by-name refuses the automated run', W.main(['--by-name', '3']) == 2 and W.main(['--by-name', '0']) == 2)
WL.CACHE = os.path.join(td, 'wp_name.json')
W.CACHE = WL.CACHE
leads[:] = [{'case': 'N-%d' % i, 'owners': 'ROMERO, MARISOL', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13 + i}
            for i in range(3)] + [{'case': 'N-9', 'owners': 'ACME LLC', 'addr': ADDR, 'auction': '11/04/2026', 'days': 13}]
W._note_attempt('N-1')
W._note_attempt('N-9')
W._note_attempt(W._NAME_MISS + 'N-2'); W._note_attempt('N-2'); W._note_attempt(W._NAME_MISS + 'N-2')
ks = [WL._lead_key(r) for r in W._name_candidates(leads, {}, {})]
rec('by-name picks only leads whose address path missed, person owners, not tried out', ks == ['N-1'], ks)
ks = [WL._lead_key(r) for r in W._name_candidates(leads, {'N-1': {'_person': [{'name': 'x', 'response': []}]}}, {})]
rec('a lead already name-searched is not reopened', ks == [], ks)
import webbrowser as _wb
_wb.open = lambda *a, **k: True
W._set_clipboard = lambda t: True
W._clipboard_text = lambda: PERSON
n_ok = W.assist_name([leads[1]], wait_s=3)
c5 = _j.load(open(WL.CACHE, encoding='utf-8'))
pr = (c5.get('N-1') or {}).get('_person') or []
rec('assist_name caches a Person-layer record the bake grades wp', n_ok == 1 and len(pr) == 1
    and C.grade_person_record('ROMERO, MARISOL', ADDR, pr[0]['response'][0]) == 'wp', c5.get('N-1'))
rec('the copied person page is kept outside the repo, under name/',
    os.path.exists(os.path.join(td, 'wpweb_pages', 'name', 'N-1.txt')))
ms = W.name_structure(PERSON)
rec('the masked layout carries no name, street, city or number',
    not any(t in ms for t in ('Marisol', 'Romero', 'Canal', 'Coral', '954', 'mromero')), ms)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
