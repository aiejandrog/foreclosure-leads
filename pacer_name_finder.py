"""PACER owner-name finder (labels-only output) v2.

Given case numbers, search local lead/lis-pendens/cache/archive files for the owner-of-record,
prefer a REAL owner field over the foreclosure `defendant` list, skip entity/estate strings, and
validate the name is PACER-usable (splittable into first/last). With --run, feed the best usable
name into the lookup-only search:  pacer_stay.py --case <case> --owner "<name>".

PRIVACY CONTRACT: the owner name is NEVER printed/logged/written. It lives in memory and, with --run,
is passed to the pacer_stay subprocess argv. Output is LABELS ONLY (case, where it was found, field,
length, form, PACER-usable y/n). --where lists every (file, field) a case appears in, still no names.
"""
import glob
import json
import os
import re
import subprocess
import sys

# real owner-of-record fields, in preference order; `defendant`/`Defendant` are LAST (weak: the
# foreclosure defendant list is often ET-AL soup or an entity, not the person who owns the home).
OWNER_FIELDS = ('owners', 'oname', 'paOwner', 'rname', 'owner', 'Owner')
WEAK_FIELDS = ('defendant', 'Defendant')
CASE_FIELDS = ('case', 'Case #', 'cert', 'caseNumber', 'case_no')
FILE_GLOBS = ('leads_final.json', '*_leads.json', 'lp_leads.json', 'lis_pendens.json',
              'ownership.json', 'ownership_cache.json', 'stub_folios.json', 'county_plaintiffs.json',
              'skiptrace_results.json', 'auction_archive.json', 'records-liens-*.json',
              'leads_raw.json', 'leads-*.json')

_ENTITY = re.compile(r'\b(LLC|L\.?L\.?C|INC|CORP|TRUST|TRUSTEES?|BANK|MORTGAGE|SERVICING|HOLDINGS?|'
                     r'PROPERTIES|INVESTMENTS?|CAPITAL|FUND|ASSOC|LP|LLP|COMPANY|REALTY|HOMES|GROUP|'
                     r'PARTNERS?|MANAGEMENT|ENTERPRISES?|REO|N\.?A\.?|FEDERAL|NATIONAL|SAVINGS|'
                     r'CREDIT UNION|HOA|HUD|SECRETARY|COUNTY|CITY OF|STATE OF|ASSOCIATION|CONDOMINIUM)\b', re.I)
_ESTATE = re.compile(r'\bEST(ATE)? OF\b|\bDECEASED\b|\bHEIRS\b|\bUNKNOWN\b|\bET\s*AL\b|\bETAL\b', re.I)


def _norm_case(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _iter_records(obj):
    if isinstance(obj, list):
        for r in obj:
            if isinstance(r, dict):
                yield None, r
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                yield k, v


def _record_case(key, rec):
    for f in CASE_FIELDS:
        if rec.get(f):
            return _norm_case(rec.get(f))
    return _norm_case(key) if key else ''


def _usable(name):
    """PACER-usable = a splittable person name, not an entity/estate/ET-AL string, >=2 name tokens."""
    if not name or _ENTITY.search(name) or _ESTATE.search(name):
        return False
    # first owner only (';' separates co-owners); strip a trailing suffix noise
    first = name.split(';')[0]
    toks = [t for t in re.split(r'[,\s]+', first) if t.strip(' .')]
    return len([t for t in toks if any(c.isalpha() for c in t)]) >= 2


def scan(case, roots):
    """Return (best_owner, best_src, best_field, best_form, best_usable, appearances[list of (file,field)])."""
    want = _norm_case(case)
    appear, cands = [], []
    for root in roots:
        for pat in FILE_GLOBS:
            for path in glob.glob(os.path.join(root, pat)):
                b = os.path.basename(path)
                if b.startswith('_'):
                    continue
                try:
                    data = json.load(open(path, encoding='utf-8'))
                except Exception:
                    continue
                for key, rec in _iter_records(data):
                    if _record_case(key, rec) != want:
                        continue
                    for f in OWNER_FIELDS + WEAK_FIELDS:
                        v = rec.get(f)
                        if isinstance(v, str) and v.strip() and not v.strip().startswith('('):
                            owner = v.strip()
                            appear.append((b, f))
                            # rank: real-owner-field(0) beats weak(1); usable beats not; comma beats not
                            rank = (0 if f in OWNER_FIELDS else 1, 0 if _usable(owner) else 1,
                                    0 if ',' in owner else 1)
                            cands.append((rank, owner, b, f))
    if not cands:
        return (None, None, None, None, False, appear)
    cands.sort(key=lambda c: c[0])
    _, owner, src, field = cands[0]
    return (owner, src, field, 'LAST,FIRST' if ',' in owner else 'First Last', _usable(owner), appear)


def main(argv):
    run = '--run' in argv
    show_where = '--where' in argv
    argv = [a for a in argv if a not in ('--run', '--where')]
    roots = []
    while '--root' in argv:
        i = argv.index('--root'); roots.append(argv[i + 1]); del argv[i:i + 2]
    if not roots:
        here = os.getcwd(); dealflow = os.path.join(os.path.expanduser('~'), 'DEALFLOW')
        roots = [here] + ([dealflow] if os.path.isdir(dealflow) else [])
    cases = argv
    if not cases:
        sys.exit('usage: python pacer_name_finder.py [--run] [--where] [--root DIR] CASE [CASE ...]')
    print('search roots: %s' % ', '.join(roots))
    print('cases: %d   mode: %s' % (len(cases), 'RUN lookup-only PACER search' if run else 'find + label only (nothing sent)'))
    print('-' * 100)
    usable_run = 0
    for case in cases:
        owner, src, field, form, usable, appear = scan(case, roots)
        if not owner:
            print('case %-22s NOT FOUND in any local file' % case); continue
        print('case %-22s FOUND  src=%-26s field=%-10s len=%d form=%-10s PACER-usable=%s'
              % (case, src, field, len(owner), form, 'YES' if usable else 'no (entity/estate/unsplittable)'))
        if show_where:
            seen = []
            for fb, ff in appear:
                if (fb, ff) not in seen:
                    seen.append((fb, ff))
            print('        appears in: ' + ', '.join('%s:%s' % (fb, ff) for fb, ff in seen))
        if run and usable:
            usable_run += 1
            r = subprocess.run([sys.executable, 'pacer_stay.py', '--case', case, '--owner', owner])
            print('        -> pacer_stay --case %s exited %d (lookup-only; name not shown)' % (case, r.returncode))
        elif run and not usable:
            print('        -> SKIPPED --run: name is not PACER-usable, would only burn a refused search')
    print('-' * 100)
    if run:
        print('ran lookup-only on %d of %d (skipped the non-usable names)' % (usable_run, len(cases)))
    print('done. owner names were never printed.')


if __name__ == '__main__':
    main(sys.argv[1:])
