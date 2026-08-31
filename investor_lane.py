#!/usr/bin/env python3
"""investor_lane.py -- the REAL pass on the investor / hard-money refi lane.

WHY THIS EXISTS
The 8/29 meeting brief quoted "~467 investor-refi candidates". That was a keyword cut -- anything
whose plaintiff did not contain BANK/MORTGAGE/ASSOCIATION -- and it is wrong in both directions. It
counted every servicer and trust whose name happened to miss the word list, and it never once asked
the question that actually matters: is the BORROWER an investor?

A hard-money refi pitch only works when BOTH sides are true:
  1. the plaintiff is a PRIVATE lender (an individual, a small LLC, an IRA/fund) -- not a bank, not
     a servicer, not a securitisation trust, not an HOA, not a taxing authority; and
  2. the borrower is an INVESTOR, not a family in their homestead -- entity on title, or no
     homestead exemption, or mail going somewhere other than the property.

Miss (2) and you are pitching a DSCR loan to a widow in the house she raised her kids in. That is
both useless and exactly the audience the foreclosure-rescue statutes exist to protect.

Run:  python investor_lane.py            # summary + funnel
      python investor_lane.py --csv      # writes investor_lane.csv for the call list
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# --- plaintiff taxonomy -------------------------------------------------------------------------
# INSTITUTIONAL: a bank, a servicer, a GSE, a securitisation trust. Cannot be refinanced away by us
# on a relationship basis and the borrower is usually a consumer.
# NOTE ON \b: the first version of this wrapped everything in \b...\b and it silently mis-filed
# CITIBANK as a private lender -- there is no word boundary between CITI and BANK, so neither \bCITI\b
# nor \bBANK\b matched. Same bug ate CONDOMINIUMS (trailing S kills \bCONDOMINIUM\b). Substrings are
# correct here: any plaintiff containing "BANK" anywhere IS a bank. Bias every ambiguous case toward
# INSTITUTIONAL -- a false "private" wastes a pitch on someone who cannot be refinanced away, while a
# false "institutional" only parks a lead we can recover later.
INSTITUTIONAL = re.compile(r'''
    (BANK|BANCORP|BANCO|CREDIT\s+UNION|SAVINGS|FSB|N\.?A\.?$|NATIONAL\s+ASSOCIATION
    |MORTGAGE|MERS|LOAN\s+(CO|CORP|SERVIC)|SERVIC(ING|ER|IS)|SERVIS|MASTER\s+SERVICER
    |FANNIE|FREDDIE|GINNIE|FHLMC|FNMA|GNMA|\bHUD\b|VETERANS|USDA|FHA\b
    |WELLS\s+FARGO|CHASE|CITI|PNC|TRUIST|REGIONS|SUNTRUST|BB&T|FIFTH\s+THIRD
    |DEUTSCHE|WILMINGTON|BNY|HSBC|BARCLAYS|GOLDMAN|MORGAN|SANTANDER|BBVA
    |NATIONSTAR|MR\.?\s*COOPER|OCWEN|PHH|SELENE|SHELLPOINT|CARRINGTON|RUSHMORE
    |FREEDOM|LAKEVIEW|PENNYMAC|ROCKET|QUICKEN|LOANDEPOT|NEWREZ|CENLAR|CROSSCOUNTRY
    |WHOLESALE|FINANCE\s+OF\s+AMERICA|REVERSE|GENWORTH|INSURANCE|LIFE\s+INSURANCE
    |ASSET\s+(CO|COMPANY|MANAGEMENT)|CERTIFICATEHOLDER|PASS-?THROUGH|SECURITIZ|REMIC
    |INDENTURE\s+TRUSTEE|AS\s+TRUSTEE\s+FOR|NOT\s+IN\s+ITS\s+INDIVIDUAL\s+CAPACITY)
''', re.I | re.X)

# ASSOCIATION: HOA / COA. Different playbook entirely (small lien, junior, senior mortgage survives).
# Substrings again, deliberately: CONDOMINIUMS / ASSOCIATES / TOWNHOMES all have to hit.
ASSOCIATION = re.compile(
    r'(ASSOCIATION|ASSOCIATES|\bASSN\b|CONDOMINIUM|\bCONDO\b|HOMEOWNER|PROPERTY\s+OWNER'
    r'|MASTER\s+ASSOC|TOWNHOM|\bVILLAS?\b|COMMUNITY\s+ASSOC|CLUB\s+ASSOC|MAINTENANCE\s+ASSOC'
    r'|\bH\.?O\.?A\.?\b)', re.I)

# GOVERNMENT / taxing authority.
GOVERNMENT = re.compile(
    r'\b(COUNTY|CITY\s+OF|STATE\s+OF|UNITED\s+STATES|U\.?S\.?A\b|IRS|INTERNAL\s+REVENUE'
    r'|TAX\s+COLLECTOR|DEPARTMENT\s+OF|MUNICIPAL|CLERK\s+OF)\b', re.I)

# PRIVATE-LENDER positive signals -- capital/fund/lending shops and self-directed retirement money.
PRIVATE_HINT = re.compile(
    r'\b(CAPITAL|FUNDING|FUND\b|LENDING|LENDERS?|EQUITY|INVEST|HOLDINGS|VENTURES|PARTNERS'
    r'|IRA\b|SELF[- ]DIRECTED|SOLO\s*401|TRUST\s+COMPANY\s+FBO|FBO\b|REALTY\s+CAPITAL'
    r'|BRIDGE|HARD\s*MONEY|PRIVATE)\b', re.I)

ENTITY = re.compile(r'\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|COMPANY|CO\b|LP\b|LLP|LTD|PLLC'
                    r'|TRUST|HOLDINGS|PROPERTIES|REALTY|INVESTMENTS?|GROUP|ENTERPRISES)\b', re.I)

# A person's name as plaintiff (SMITH, JOHN / JOHN A SMITH) is the strongest private-lender tell.
PERSONISH = re.compile(r'^[A-Z][A-Za-z\'\-]+,\s*[A-Z][A-Za-z\'\-]+(\s+[A-Z]\.?)?$')


def classify_plaintiff(p):
    p = (p or '').strip()
    if not p:
        return 'unknown'
    if GOVERNMENT.search(p):
        return 'government'
    if ASSOCIATION.search(p):
        return 'association'
    if INSTITUTIONAL.search(p):
        return 'institutional'
    if PERSONISH.match(p):
        return 'private_individual'
    if PRIVATE_HINT.search(p):
        return 'private_entity'
    if ENTITY.search(p):
        return 'private_entity'        # an LLC that is not a known institution
    return 'private_individual' if len(p.split()) <= 4 else 'unknown'


def _norm_addr(s):
    s = re.sub(r'[^A-Z0-9 ]', ' ', str(s or '').upper())
    s = re.sub(r'\b(APT|UNIT|STE|SUITE|#)\b.*$', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _num(v):
    try:
        return float(re.sub(r'[^0-9.\-]', '', str(v)))
    except Exception:
        return 0.0


def load():
    L = []
    for f in ('leads_final.json', 'lp_leads.json'):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            L += [dict(r, _src=f) for r in json.load(open(p, encoding='utf-8'))]
    return L


def owner_is_investor(r):
    """-> (bool, reason). Any ONE of these is enough; they are independent evidence."""
    owner = str(r.get('owners') or r.get('oname') or r.get('owner_clean') or '')
    if ENTITY.search(owner):
        return True, 'entity on title'
    hs = r.get('hs', r.get('homestead'))
    if hs in (0, '0', False, 'N', 'NO', 'None', ''):
        if hs is not None and hs != '':
            return True, 'no homestead exemption'
    prop = _norm_addr(r.get('addr') or r.get('Address'))
    mail = _norm_addr(r.get('mail') or r.get('mailing_address'))
    if prop and mail and not mail.startswith(prop[:14]):
        return True, 'absentee (mail != property)'
    return False, ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', action='store_true')
    a = ap.parse_args()

    L = load()
    print('rows loaded'.ljust(42), len(L))

    # ---- funnel, printed step by step so the number is auditable ----
    buckets = {}
    for r in L:
        buckets.setdefault(classify_plaintiff(r.get('plaintiff')), []).append(r)
    print('\nPLAINTIFF TYPE')
    for k in ('institutional', 'association', 'government', 'private_entity',
              'private_individual', 'unknown'):
        print(f'  {k:22s} {len(buckets.get(k, [])):5d}')

    private = buckets.get('private_entity', []) + buckets.get('private_individual', [])
    print(f'\n  private lenders (entity + individual) {len(private):5d}')

    inv, why = [], {}
    for r in private:
        ok, reason = owner_is_investor(r)
        if ok:
            inv.append(r)
            why[reason] = why.get(reason, 0) + 1
    print(f'  ...of those, borrower looks like an INVESTOR {len(inv):5d}')
    for k, v in sorted(why.items(), key=lambda x: -x[1]):
        print(f'       {k:32s} {v:5d}')

    # equity: a refi needs room. judgment vs value.
    with_eq = []
    for r in inv:
        val = _num(r.get('value') or r.get('market_value') or r.get('basis') or 0)
        debt = _num(r.get('judg') or r.get('judgment') or r.get('Final Judgment Amount') or 0)
        if val > 0 and debt > 0 and (val - debt) > 50000:
            r['_val'], r['_debt'], r['_room'] = val, debt, val - debt
            with_eq.append(r)
    print(f'\n  ...and >$50k between value and the judgment {len(with_eq):5d}   <-- THE LANE')

    resets = [r for r in with_eq if _num(r.get('sale_survived')) > 0]
    print(f'       of those, sale already reset at least once {len(resets):5d}'
          '   (they are fighting = they have money)')

    with_eq.sort(key=lambda r: -r['_room'])
    print('\nTOP 15 BY ROOM BETWEEN VALUE AND DEBT')
    print(f'  {"owner":34s} {"value":>11s} {"judgment":>11s} {"room":>11s}  plaintiff')
    for r in with_eq[:15]:
        own = str(r.get('owners') or r.get('oname') or '')[:33]
        pl = str(r.get('plaintiff') or '')[:34]
        print(f'  {own:34s} {r["_val"]:11,.0f} {r["_debt"]:11,.0f} {r["_room"]:11,.0f}  {pl}')

    if a.csv:
        out = os.path.join(HERE, 'investor_lane.csv')
        cols = ['case', 'owners', 'addr', 'county', 'plaintiff', '_val', '_debt', '_room',
                'auction', 'days', 'sale_survived', 'folio']
        with open(out, 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in with_eq:
                w.writerow([r.get(c, '') for c in cols])
        print(f'\nwrote {out}  ({len(with_eq)} rows)')
        print('NOT committed - investor_lane.csv is client data and the repo is PUBLIC.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
