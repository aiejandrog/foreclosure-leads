"""stub_resolve: the 'defendant owns exactly one parcel' rule needs a whole search and an owner slot
that is this person, and legacy picks made by the old token rule are re-resolved, not trusted.
Fake names and folios; the appraiser search is replaced, no network."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stub_resolve as SR

fails = 0
def check(name, ok, extra=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + ((' ' + str(extra)) if extra and not ok else ''))
    fails += 0 if ok else 1

def C(folio, *slots, capped=False):
    return {'folio': folio, 'owner': ' '.join(slots), 'slots': list(slots), 'capped': capped,
            'addr': 'x', 'legal': ''}

def run(cands, defs, plaintiff='BIG BANK NA', county='MIAMI-DADE'):
    fn = {'MIAMI-DADE': '_md_candidates', 'BROWARD': '_bw_candidates'}[county]
    real, real_legal = getattr(SR, fn), SR._legal_of
    setattr(SR, fn, lambda last, first: [dict(c) for c in cands])
    SR._legal_of = lambda county, folio: ('', {'market_value': 1})
    try:
        return SR.resolve_case(county, 'X', plaintiff, defs)
    finally:
        setattr(SR, fn, real); SR._legal_of = real_legal

r = run([C('1', 'GARCIA JOSE')], 'Garcia, Jose')
check('exact owner slot resolves', r and r['folio'] == '1' and r['how'] == 'owner-unique', r)
r = run([C('1', 'GARCIA JOSE A')], 'Garcia, Jose')
check('one middle initial still resolves', r and r['folio'] == '1', r)
r = run([C('1', 'GARCIA JOSE ANTONIO')], 'Garcia, Jose Antonio')
check("the defendant's own middle name resolves", r and r['folio'] == '1', r)
r = run([C('1', 'GARCIA MARIA JOSE')], 'Garcia, Jose')
check('a namesake whose middle name is the first name does not', r is None, r)
r = run([C('1', 'GARCIA JOSE LUIS')], 'Garcia, Jose')
check('an unexplained extra given name does not', r is None, r)
r = run([C('1', 'GARCIA JOSE JR')], 'Garcia, Jose')
check('a JR namesake does not', r is None, r)
r = run([C('1', 'GARCIA JOSE', capped=True)], 'Garcia, Jose')
check('a search at its page cap does not', r is None, r)
r = run([C('1', 'LOPEZ MARIA', 'GARCIA JOSE')], 'Garcia, Jose')
check('this person in a co-owner slot still resolves', r and r['folio'] == '1', r)
r = run([C('1', 'GARCIA, JOSE')], 'Garcia, Jose', county='BROWARD')
check('Broward comma format resolves', r and r['folio'] == '1', r)

r = run([C('1', 'GARCIA JOSE &W MARIA')], 'Garcia, Jose')
check('a couple written in one field still resolves the husband', r and r['folio'] == '1', r)
# review 2026-10-12: the couple's real parcel is titled wife-first and never comes back from the Jose
# search; a namesake couple written 'GARCIA JOSE L &W MARIA' must not be read as theirs
r = run([C('1', 'GARCIA JOSE L &W MARIA'), C('2', 'GARCIA JOSE')], 'Garcia, Jose; Garcia, Maria')
check('a namesake couple in one field never pairs', r is None, r)
for owner in ('GARCIA JOSE (LE)', 'GARCIA JOSE H/E', 'REM GARCIA JOSE',
              'GARCIA JOSE &WF MARIA', 'GARCIA JOSE JTRS'):
    r = run([C('1', owner)], 'Garcia, Jose')
    check('roll designator read past: %s' % owner, r and r['folio'] == '1', r)
for owner in ('GARCIA JOSE TR', 'GARCIA JOSE EST OF', 'GARCIA JOSE LE'):
    r = run([C('1', owner)], 'Garcia, Jose')
    check('a trustee, an estate or a bare LE is not the person: %s' % owner, r is None, r)
for owner in ('LE NGUYEN THANH', 'NGUYEN THANH LE'):
    r = run([C('1', owner)], 'Nguyen, Thanh')
    check('Le as a name is never stripped: %s' % owner, r is None, r)

# cache trust
check('a legacy owner-unique hit is not trusted', SR._trusted({'folio': '9', 'how': 'owner-unique'}) == '')
check('a strict owner-unique hit is trusted', SR._trusted({'folio': '9', 'how': 'owner-unique', 'rule': 2}) == '9')
check('an association hit stays trusted', SR._trusted({'folio': '9', 'how': 'owner+assoc-legal'}) == '9')
check('a miss is never a folio', SR._trusted({'miss': True}) == '')

check('a legacy hit is flagged for re-resolution', SR._legacy({'folio': '9', 'how': 'owner-unique'}))
check('a trusted hit or a miss is not', not SR._legacy({'folio': '9', 'how': 'owner-pair'})
      and not SR._legacy({'miss': True}))

# the sweep re-resolves a legacy pick even when the row's warning is not 'parcel not linked'
import json, tempfile
tmp = tempfile.mkdtemp()
rows = [{'Case #': 'A', 'warning': 'HOA/assoc case - verify senior mortgage on docket', 'judgment': 0,
         'defendants': 'Garcia, Jose', 'plaintiff': 'X'},
        {'Case #': 'B', 'warning': 'HOA/assoc case - verify senior mortgage on docket', 'judgment': 100,
         'defendants': 'Garcia, Jose', 'plaintiff': 'X'}]
json.dump(rows, open(os.path.join(tmp, 'leads_final.json'), 'w'))
cache = {'A': {'folio': '0130000000001', 'how': 'owner-unique'},
         'B': {'folio': '0130000000002', 'how': 'owner-unique'}}
seen = []
saved = (SR.HERE, SR.resolve_case, SR._legal_of, SR._save_cache)
SR.HERE = tmp
SR.resolve_case = lambda county, case, pl, defs, v=False: seen.append(case) or (
    {'folio': '0130000000003', 'how': 'owner-unique', 'addr': '', 'info': {'market_value': 1000}}
    if case == 'B' else None)
SR._legal_of = lambda county, folio: ('', {'market_value': 1})
SR._save_cache = lambda c: None
try:
    SR._patch_md(cache, False)
finally:
    SR.HERE, SR.resolve_case, SR._legal_of, SR._save_cache = saved
check('the sweep re-resolves a legacy pick whatever its warning', seen == ['A', 'B'], seen)
check('...and a failed re-resolve is cached as a miss, never the old folio', cache['A'].get('miss'), cache['A'])
out = json.load(open(os.path.join(tmp, 'leads_final.json')))
check('...a re-link writes the new folio and keeps the other warning', out[1].get('Folio') == '0130000000003'
      and out[1]['warning'].startswith('HOA'), out[1])
check('...and the new pick is cached under the current rule', cache['B'].get('rule') == SR.RULE, cache['B'])

print('\n%d failure(s)' % fails)
sys.exit(1 if fails else 0)
