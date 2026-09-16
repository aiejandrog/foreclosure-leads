#!/usr/bin/env python
"""lp_resolve2.py — second pass over the lis-pendens rows lp_resolve.py could not close.

WHY A SECOND PASS
lp_resolve.py grades 85/125 `high` and stops there on purpose: a wrong address sends a door
knock to an innocent stranger. But 40 rows sit unusable, and reading them one by one shows the
failures are NOT all ambiguity — several are mechanical:
  * the legal description literally CONTAINS the street address ("7135 COLLINS AVE APT 1523 ...")
  * a single candidate whose parcel LEGAL carries the SAME unit AND building as the LP legal
  * a candidate FOUND BY THE LEGAL whose owner of record IS the LP defendant, by name — exactly one
    (owner_rule; a candidate list built from the name itself never qualifies — see its docstring)

Each of those is two independent signals agreeing — the same bar lp_resolve.py calls `high`.
Anything short of two signals stays blank, exactly as before. This script NEVER overwrites a
row that already resolved; it only fills blanks, and it writes its own evidence string so the
provenance of every promoted address is auditable.

Run:  python lp_resolve2.py            # promote what qualifies, write lp_addresses.json
      python lp_resolve2.py --dry-run  # report only, touch nothing
"""
import argparse
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ADDR = os.path.join(HERE, 'lp_addresses.json')
PAGIS = ('https://gisweb.miamidade.gov/arcgis/rest/services/MD_ComparableSales/'
         'MapServer/5/query')
FIELDS = ('FOLIO,TRUE_OWNER1,TRUE_OWNER2,TRUE_SITE_ADDR,TRUE_SITE_ADDR_NO_UNIT,TRUE_SITE_CITY,'
          'TRUE_SITE_ZIP_CODE,LEGAL,DOR_DESC,CONDO_FLAG')
S = requests.Session()
S.headers.update({'User-Agent': 'foreclosure-leads/1.0 (lp-resolve2)'})

_STOP = {'THE', 'OF', 'AND', 'A', 'LE', 'TRS', 'TR', 'EST', 'ESTATE', 'JR', 'SR', 'II', 'III',
         'W', 'H', 'MR', 'MRS', 'INC', 'LLC', 'CORP', 'REV', 'LIV', 'TRUST'}


def q(where, cap=60):
    p = {'where': where, 'outFields': FIELDS, 'returnGeometry': 'false', 'f': 'json',
         'resultRecordCount': cap}
    for _ in range(3):
        try:
            j = S.post(PAGIS, data=p, timeout=60).json()
            if 'features' in j:
                return [f['attributes'] for f in j['features']]
        except Exception:
            pass
        time.sleep(1.2)
    return None                                  # unknown, never "empty"


def esc(s):
    return str(s).replace("'", "''")


def toks(name):
    """Significant tokens of a person name, order-free — 'LIM EDDIE P' vs 'EDDIE P LIM'."""
    w = re.sub(r'[^A-Z ]', ' ', str(name or '').upper()).split()
    return {t for t in w if len(t) > 2 and t not in _STOP}


def owner_agrees(lp_owner, parcel_owner1, parcel_owner2=''):
    """TRUE only when every significant token of the LP defendant appears in the parcel owner.
    A single shared surname is NOT agreement — that is how you knock on the wrong Garcia."""
    a = toks(lp_owner)
    if len(a) < 2:
        return False
    b = toks(parcel_owner1) | toks(parcel_owner2)
    return a.issubset(b)


def unit_of(legal):
    m = re.search(r'\bUNIT\s*(?:NO\.?\s*)?([A-Z]?-?\d+[A-Z]?)', str(legal or '').upper())
    if not m:
        m = re.search(r'\bPARCEL\s*(?:NO\.?\s*)?([A-Z]?-?\d+[A-Z]?)', str(legal or '').upper())
    return m.group(1).replace('-', '') if m else ''


def bldg_of(legal):
    m = re.search(r'\bBLDG\s*(\d+)', str(legal or '').upper())
    return m.group(1) if m else ''


ADDR_IN_LEGAL = re.compile(
    r'\b(\d{2,6}\s+(?:[NSEW]{1,2}\s+)?[A-Z0-9 ]{2,28}?\s'
    r'(?:AVE|AVENUE|ST|STREET|RD|ROAD|DR|DRIVE|CT|COURT|TER|TERR|TERRACE|PL|PLACE|LN|LANE|WAY|BLVD|CIR|CIRCLE|HWY|PKWY)\b)')


def parse_addr_in_legal(legal):
    """Some LP legals are just the street address (the filer skipped the platted legal)."""
    L = ' '.join(str(legal or '').upper().split())
    m = ADDR_IN_LEGAL.search(L)
    if not m:
        return '', ''
    street = m.group(1).strip()
    u = re.search(r'\bAPT\s*#?\s*([A-Z]?-?\d+[A-Z]?)', L)
    return street, (u.group(1) if u else '')


def norm_street(s):
    s = ' '.join(str(s or '').upper().split())
    for full, ab in (('AVENUE', 'AVE'), ('STREET', 'ST'), ('TERRACE', 'TER'), ('TERR', 'TER'),
                     ('DRIVE', 'DR'), ('COURT', 'CT'), ('PLACE', 'PL'), ('LANE', 'LN'),
                     ('ROAD', 'RD'), ('CIRCLE', 'CIR'), ('BOULEVARD', 'BLVD')):
        s = re.sub(r'\b%s\b' % full, ab, s)
    return s


R2_EVIDENCE = 'owner of record IS the LP defendant'


def owner_rule(legal, lp_owner, cands):
    """RULE 2 -> (candidate, why) or None.

    Owner agreement is a SECOND signal only when the candidate list came from an INDEPENDENT key —
    the legal description. Broward LP rows carry no legal: fl_lp/broward_resolve.py built their
    candidate list BY the defendant's name and graded it `low` because more than one parcel carries
    that name. Every candidate there "agrees" by construction, and "the legal does not contradict" is
    vacuous with no legal — so this rule used to promote the FIRST one: one signal counted twice, a
    coin flip marked high (2026-09-16: CACE-26-013184 got the defendant's homestead; the mortgage the
    lis pendens forecloses is her rental two streets over. 121 of 126 pass2 rows had this shape).
    So: a legal must exist, and exactly ONE candidate may agree without its unit contradicting."""
    if not str(legal or '').strip():
        return None
    lu = unit_of(legal)
    agree = []
    for c in cands or []:
        if not owner_agrees(lp_owner, c.get('owner', '')):
            continue
        # owner agreement alone is one signal; a candidate whose unit contradicts the legal is out
        cu = unit_of(c.get('legal', ''))
        if not lu or not cu or lu == cu:
            agree.append(c)
    if len(agree) != 1:
        return None
    return (agree[0], 'the parcel owner of record IS the LP defendant by full name (%s), '
                      'and the unit/legal does not contradict' % agree[0].get('owner', ''))


def revoke_ambiguous(data, stamp=None):
    """Self-heal for rows the old RULE 2 promoted: re-ask the fixed rule, and revoke any promotion it
    would not make. lp_addresses.json is gitignored per-machine data, so fixing the rule alone would
    leave every past coin flip standing as `high`. Only `rung == 'pass2'` RULE-2 rows are considered —
    a human-verified row, or any other rung, is never touched. Returns the revoked case numbers."""
    stamp = stamp or time.strftime('%Y-%m-%d')
    revoked = []
    for r in data.values():
        if not isinstance(r, dict) or r.get('rung') != 'pass2' or R2_EVIDENCE not in str(r.get('evidence') or ''):
            continue
        cands = r.get('candidates') or []
        hit = owner_rule(r.get('legal') or '', r.get('lpOwner') or '', cands)
        if hit and str(hit[0].get('folio') or '').strip() == str(r.get('folio') or '').strip():
            continue
        owned = [c for c in cands if c.get('addr')]
        names = ' · '.join(str(c['addr']).strip() for c in owned[:6]) + (' …' if len(owned) > 6 else '')
        for k in ('folio', 'addr', 'city', 'zip', 'paOwner', 'value', 'hs', 'beds', 'baths', 'sqft',
                  'built', 'dor', 'paOwners'):
            r.pop(k, None)
        r['confidence'] = 'low'
        r['needsHuman'] = True
        r['rung'] = 'pass2-revoked'
        r['evidence'] = ('AMBIGUOUS (pass2 promotion revoked %s): %d parcels carry the defendant\'s name '
                         '— %s — and the name cannot pick one. The lis pendens forecloses one recorded '
                         'mortgage (AcclaimWeb LP details -> DocLink); that mortgage names the parcel.'
                         % (stamp, len(cands), names or 'no addresses on file'))
        revoked.append(r.get('case'))
    return revoked


def promote(row, folio, addr, city, zipc, why, owner=''):
    row['folio'] = folio
    row['addr'] = addr
    row['city'] = city
    row['zip'] = zipc
    row['paOwner'] = owner or row.get('paOwner') or ''
    row['confidence'] = 'high'
    row['rung'] = 'pass2'
    row['needsHuman'] = False
    row['evidence'] = 'pass2: ' + why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    data = json.load(open(ADDR, encoding='utf-8'))
    revoked = revoke_ambiguous(data)
    print('revoked %d ambiguous pass2 promotion(s) back to needs-a-human%s\n'
          % (len(revoked), (': ' + ', '.join(revoked[:8]) + (' …' if len(revoked) > 8 else '')) if revoked else ''))
    todo = [r for r in data.values() if r.get('confidence') in ('low', 'none')
            and r.get('case') not in set(revoked)]
    print('second pass over %d unresolved row(s)\n' % len(todo))
    won = 0

    for r in todo:
        case, legal, lpo = r['case'], r.get('legal') or '', r.get('lpOwner') or ''
        cands = r.get('candidates') or []
        lu, lb = unit_of(legal), bldg_of(legal)

        # ---- RULE 1: the legal already IS an address -------------------------------------
        street, apt = parse_addr_in_legal(legal)
        if street:
            # A big condo tower blows past any page cap (7135 COLLINS AVE alone is 60+ parcels),
            # so when the legal names the apt, ask for that exact unit instead of paging the
            # whole building and hoping it is on the first page.
            rows = None
            if apt:
                rows = q("TRUE_SITE_ADDR = '%s %s'" % (esc(norm_street(street)), esc(apt.upper())))
            if not rows:
                rows = q("TRUE_SITE_ADDR_NO_UNIT = '%s'" % esc(norm_street(street)))
                if rows and len(rows) >= 60:
                    rows = []                    # truncated page: unusable, never guess from it
            if rows:
                pick = rows[0] if len(rows) == 1 else None
                if not pick and apt:
                    for x in rows:
                        if norm_street(x.get('TRUE_SITE_ADDR')).endswith(apt.upper()):
                            pick = x
                            break
                if pick:
                    # keep the unit: "7135 COLLINS AVE" is a 60-unit tower, the apt IS the address
                    full_addr = str(pick.get('TRUE_SITE_ADDR') or '').strip() or norm_street(street)
                    promote(r, str(pick['FOLIO']).strip(), full_addr,
                            str(pick.get('TRUE_SITE_CITY') or '').strip(),
                            str(pick.get('TRUE_SITE_ZIP_CODE') or '').strip(),
                            'the LP legal contains the street address verbatim; the parcel roll '
                            'returns %d match(es) for it%s' % (len(rows), ' + APT ' + apt if apt else ''),
                            str(pick.get('TRUE_OWNER1') or ''))
                    print('  %-22s ADDRESS-IN-LEGAL -> %s' % (case, r['addr']))
                    won += 1
                    continue

        # ---- RULE 2: exactly ONE legal-found candidate whose OWNER is the LP defendant ------
        hit = owner_rule(legal, lpo, cands)
        # ---- RULE 3: single candidate whose UNIT *and* BLDG both match -------------------
        if not hit and lu:
            for c in cands:
                cl = str(c.get('legal') or '').upper()
                cu, cb = unit_of(cl), bldg_of(cl)
                addr_u = re.search(r'([A-Z]?-?\d+[A-Z]?)$', str(c.get('addr') or '').upper())
                unit_ok = (cu and cu == lu) or (addr_u and addr_u.group(1).replace('-', '') == lu)
                bldg_ok = bool(lb) and (lb == cb or lb in str(c.get('addr') or ''))
                if unit_ok and bldg_ok:
                    hit = (c, 'condo unit %s AND building %s both match the LP legal on this '
                              'parcel' % (lu, lb))
                    break
        # ---- RULE 4: exactly ONE candidate whose condo NAME and UNIT both agree ----------
        # Two independent signals (the platted condo name + the unit number) on a single
        # parcel. The parcel roll writes "APT 115" where the LP writes "UNIT NO 115", which
        # is why the strict UNIT/BLDG rule above misses these.
        if not hit and lu and len(cands) == 1:
            c = cands[0]
            cl = str(c.get('legal') or '').upper()
            core = [w for w in re.sub(r'[^A-Z ]', ' ', legal.upper()).split()
                    if len(w) > 3 and w not in ('CONDO', 'UNIT', 'BLDG', 'INC', 'LOT', 'BLK')][:3]
            name_ok = bool(core) and all(w in cl for w in core)
            addr_u = re.search(r'([A-Z]?-?\d+[A-Z]?)$', str(c.get('addr') or '').upper())
            unit_ok = (unit_of(cl) == lu) or (addr_u and addr_u.group(1).replace('-', '') == lu)
            if name_ok and unit_ok:
                hit = (c, 'the only parcel carrying this condo name (%s) also carries unit %s'
                          % (' '.join(core), lu))

        # ---- RULE 5: targeted query when the legal names BOTH condo, unit and building ---
        if not hit and lu and lb:
            core = [w for w in re.sub(r'[^A-Z ]', ' ', legal.upper()).split()
                    if len(w) > 3 and w not in ('CONDO', 'UNIT', 'BLDG', 'INC')][:2]
            if core:
                w = ("LEGAL LIKE '%%%s%%' AND LEGAL LIKE '%%UNIT %s%%' AND LEGAL LIKE '%%BLDG %s%%'"
                     % (esc(' '.join(core)), esc(lu), esc(lb)))
                rows = q(w)
                if rows and len(rows) == 1:
                    x = rows[0]
                    hit = ({'folio': str(x['FOLIO']).strip(),
                            'addr': str(x.get('TRUE_SITE_ADDR') or '').strip(),
                            'city': str(x.get('TRUE_SITE_CITY') or '').strip(),
                            'zip': str(x.get('TRUE_SITE_ZIP_CODE') or '').strip(),
                            'owner': str(x.get('TRUE_OWNER1') or '').strip()},
                           'exactly one parcel matches condo %s + unit %s + building %s'
                           % (' '.join(core), lu, lb))

        # ---- RULE 6: platbook LOT/BLK asked of the roll directly ------------------------
        # lp_resolve's platbook rung normalizes the plat page; when that normalization misses,
        # the raw "PB 57-270 ... LOT 18 ... BLK 7" triple still identifies one parcel.
        if not hit and not cands:
            pb = re.search(r'\bPB\s*(\d+)\s*[/-]\s*(\d+)', legal.upper())
            lot = re.search(r'\bLOT\s*(\d+)', legal.upper())
            blk = re.search(r'\bBLK\s*(\d+)', legal.upper())
            if pb and lot and blk:
                w = ("LEGAL LIKE '%%PB %s-%s%%' AND LEGAL LIKE '%%LOT %s%%' AND LEGAL LIKE '%%BLK %s%%'"
                     % (esc(pb.group(1)), esc(pb.group(2)), esc(lot.group(1)), esc(blk.group(1))))
                rows = q(w)
                if rows and len(rows) == 1:
                    x = rows[0]
                    hit = ({'folio': str(x['FOLIO']).strip(),
                            'addr': str(x.get('TRUE_SITE_ADDR_NO_UNIT') or x.get('TRUE_SITE_ADDR') or '').strip(),
                            'city': str(x.get('TRUE_SITE_CITY') or '').strip(),
                            'zip': str(x.get('TRUE_SITE_ZIP_CODE') or '').strip(),
                            'owner': str(x.get('TRUE_OWNER1') or '').strip()},
                           'exactly one parcel matches the plat citation PB %s-%s LOT %s BLK %s'
                           % (pb.group(1), pb.group(2), lot.group(1), blk.group(1)))

        if hit:
            c, why = hit
            promote(r, str(c.get('folio') or '').strip(), c.get('addr') or '',
                    c.get('city') or '', c.get('zip') or '', why, c.get('owner') or '')
            print("  %-22s RESOLVED -> %s" % (case,
                                        r['addr']))
            won += 1
            continue
        print('  %-22s still unresolved (%s)' % (case, (legal or '')[:44]))

    print('\nresolved this pass: %d | still needing a human: %d' % (won, len(todo) - won))
    if args.dry_run:
        print('(dry run — nothing written)')
        return
    if won or revoked:
        tmp = ADDR + '.tmp'
        json.dump(data, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, ADDR)
        print('wrote', ADDR)


if __name__ == '__main__':
    main()
