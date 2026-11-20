"""_coachgreettest -- the coach greets by FIRST name, never the surname ("Hi, Lopez?").

Run:  python _coachgreettest.py   (exit 0 = safe; invented names, no network, no server started)

WHY (2026-11-19): the coach page filled First name with the first word of the owner string. Palm Beach's
roll writes "LAST FIRST" with no comma, so every Palm Beach auction lead opened with the surname. The
server now reads the name the way Call Mode does and sends `first`; the page uses it.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_coach_server as S  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


G = S.greet_first
CASES = [
    # (oname, owners, county, case, officer, want)
    ('LOPEZ MARIA', 'LOPEZ MARIA', 'PALM BEACH', '502026CA000001XXXXMB', False, 'Maria'),
    ('LOPEZ MARIA E', 'LOPEZ MARIA E &', '', '502026CA000002XXXXMB', False, 'Maria'),     # county missing: the case says PB
    ('Hazan, Elizabeth', '', 'MIAMI-DADE', '2026-000001-CA-01', False, 'Elizabeth'),
    ('Maria Lopez', 'LOPEZ,MARIA', 'BROWARD', 'CACE-26-000001', False, 'Maria'),
    ('LOPEZ MARIA', 'LOPEZ,MARIA', 'BROWARD', 'CACE-26-000002', False, 'Maria'),          # clean name still surname-first
    ('', 'GRANT-HENRIQUES,MATTHEW', 'BROWARD', 'CACE-26-000003', False, 'Matthew'),
    ('Maria De La Cruz', '', 'BROWARD', 'CACE-26-000004', False, 'Maria'),
    ('DE LA CRUZ MARIA', 'DE LA CRUZ MARIA', 'PALM BEACH', '50', False, ''),              # a particle is not a name: say none
    ('WE BUY HOMES LLC', 'WE BUY HOMES LLC', 'BROWARD', 'CACE-26-000005', False, ''),
    ('', 'ESTATE OF JOHN SMITH', 'MIAMI-DADE', '2026-000002-CA-01', False, ''),
    ('', 'SUNRISE LAKES CONDOMINIUM ASSN INC', 'BROWARD', '', False, ''),
    ('Carlos Perez', 'PEREZ HOLDINGS LLC', 'BROWARD', 'CACE-26-000006', True, 'Carlos'),  # investor lane: the officer answers
    ('Carlos Perez', 'PEREZ HOLDINGS LLC', 'BROWARD', 'CACE-26-000006', False, ''),
    ('', '', '', '', False, ''),
    ('UNKNOWN TENANT', '', '', '', False, ''),
    ('Ana Lopez', 'LOPEZ, ANA; LOPEZ, JUAN', 'MIAMI-DADE', '', False, 'Ana'),
    ("O'Brien, Sean", '', 'BROWARD', '', False, 'Sean'),
]
for on, own, cty, case, off, want in CASES:
    got = G(on, own, cty, case, officer=off)
    rec('%-22r / %-32r %-10s -> %r' % (on, own, cty[:10], want), got == want, got)

pb = S.slim_card({'case': '502026CA000009XXXXMB', 'oname': 'LOPEZ MARIA', 'owners': 'LOPEZ MARIA', 'county': 'PALM BEACH',
                  'addr': '1 A ST', 'st': 'FC'}, 'palmbeach_leads.json')
rec('a Palm Beach card carries first = the first name', pb.get('first') == 'Maria', pb.get('first'))
bal = S.slim_card({'case': 'B1', 'oname': 'Carlos Perez', 'owners': 'PEREZ HOLDINGS LLC', 'county': 'BROWARD', 'st': 'BAL'}, 'balloon_leads.json')
rec('an investor card greets the officer', bal.get('first') == 'Carlos', bal.get('first'))
md = S.lead_card({'Case #': '2026-000003-CA-01', 'owners': 'GARCIA, MARIA', 'owner_clean': 'Maria Garcia', 'Address': '1 B ST'})
rec('a Miami-Dade card carries first from the cleaned name', md.get('first') == 'Maria', md.get('first'))

page = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
a = page.index('function pick(i){')
body = page[a:page.index('\nfunction leadChip()', a)]
rec('the page fills First name from the server\'s `first`', "typeof m.first==='string'" in body and "$('f_first').value=m.first" in body)
rec('...and an empty `first` leaves the box empty (no guess from the owner string)',
    re.search(r"if\(typeof m\.first==='string'\)\{[^}]*\}\s*else if\(own\[0\]", body) is not None)
rec('...and the last lead\'s auto-filled name does not carry to the next lead', 'S._autoFirst' in body)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
