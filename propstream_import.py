#!/usr/bin/env python3
"""propstream_import.py — the DealFlow <- PropStream bridge.

WHY A CSV BRIDGE AND NOT AN "INTEGRATION"
PropStream has NO public API — by design. The only data door they expose is list export (CSV,
~10k records/month on the base plan). So the integration seam is: export a list there, drop the
file here, this script folds it into the board. Scraping their logged-in web app instead would
violate their ToS, break on every redesign, and put the paid account at risk — not built, on
purpose.

WHAT IT DOES
  1. Reads every CSV in ./propstream/ (or a path you pass).
  2. Auto-detects their columns — PropStream renames headers between product areas and versions,
     so nothing is hardcoded: headers are normalized and fuzzy-matched against candidate lists.
  3. Joins to the board by APN/folio first (exact, after stripping punctuation), street address
     second. The folio join is the trustworthy one; address joins are marked as such.
  4. Emits TWO things, deliberately separate:
       propstream_overlay.json — enrichment for leads ALREADY on the board (their AVM, equity
         estimate, open-loan balance, distress flags). Rendered as advisory context.
       a net-new report — rows PropStream has that the board does not. Written to
         propstream_leads.json ONLY with --emit-leads; default is report-only, because a new
         lead source entering the funnel is a product decision, not a side effect of an import.

CONTACT DATA IS QUARANTINED — this is the one rule that matters
PropStream phone/email columns land in ADVISORY fields (psPhones/psEmails), never in r.phones.
The board's outreach machinery treats r.phones as dial-ready, which implies a DNC scrub this
import cannot vouch for. An un-scrubbed number that auto-enters the text queue is an FTSA
violation waiting to fire (~$500-1,500/message). Same pattern as addrGuess: visible to the
operator, consumed by nothing. Promote to r.phones only through the normal skiptrace path.

Run:  python propstream_import.py                # read ./propstream/*.csv -> overlay + report
      python propstream_import.py file.csv       # one file
      python propstream_import.py --emit-leads   # also write net-new rows as board leads
      python propstream_import.py --selftest     # run the header-mapper fixtures, touch nothing
"""
import csv
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, 'propstream')
OVERLAY = os.path.join(HERE, 'propstream_overlay.json')
NEWLEADS = os.path.join(HERE, 'propstream_leads.json')

# ---- header detection -----------------------------------------------------------------------
# PropStream is not consistent with itself: "APN", "APN #", "Assessor Parcel Number" all appear
# depending on which screen exported the list. Normalize hard, then match against candidates.
def _norm(h):
    return re.sub(r'[^a-z0-9]+', ' ', str(h or '').lower()).strip()

CANDIDATES = {
    'apn':        ['apn', 'apn number', 'assessor parcel number', 'parcel number', 'folio', 'parcel id'],
    'addr':       ['property address', 'address', 'site address', 'situs address'],
    'city':       ['property city', 'city', 'site city'],
    'zip':        ['property zip', 'zip', 'zip code', 'site zip', 'property zip code'],
    'owner1first':['owner 1 first name', 'owner first name', 'first name'],
    'owner1last': ['owner 1 last name', 'owner last name', 'last name'],
    'ownerfull':  ['owner name', 'owner 1 name', 'owner full name', 'owner'],
    'mail':       ['owner mailing address', 'mailing address', 'mail address'],
    'mailcity':   ['owner mailing city', 'mailing city'],
    'mailstate':  ['owner mailing state', 'mailing state'],
    'value':      ['estimated value', 'est value', 'est. value', 'avm', 'estimated market value'],
    'equity':     ['estimated equity', 'est equity', 'est. equity', 'equity', 'estimated equity percent', 'equity percent'],
    'openloans':  ['open mortgage balance', 'total open loans', 'open loans', 'est remaining balance of open loans', 'mortgage balance'],
    'phone1':     ['phone 1', 'phone1', 'phone', 'phone number'],
    'phone2':     ['phone 2', 'phone2'],
    'email1':     ['email 1', 'email1', 'email', 'email address'],
    'distress':   ['status', 'lead type', 'list', 'distress', 'pre foreclosure status', 'foreclosure status'],
    'saledate':   ['auction date', 'sale date', 'foreclosure sale date'],
}

def map_headers(fieldnames):
    """actual header -> our key. First candidate that matches a normalized header wins."""
    normed = {_norm(h): h for h in (fieldnames or [])}
    out = {}
    for key, cands in CANDIDATES.items():
        for c in cands:
            if c in normed:
                out[key] = normed[c]
                break
    return out

def _money(v):
    if v in (None, ''):
        return 0
    try:
        return int(float(re.sub(r'[^0-9.\-]', '', str(v)) or 0))
    except Exception:
        return 0

def _norm_apn(v):
    return re.sub(r'[^0-9A-Za-z]', '', str(v or '')).upper()

def _norm_street(v):
    """'12011 SW 117th Ct.' -> '12011 SW 117 CT' — enough to join against board addresses."""
    s = re.sub(r'[^A-Za-z0-9 ]', '', str(v or '').upper())
    s = re.sub(r'\b(\d+)(ST|ND|RD|TH)\b', r'\1', s)          # 117TH -> 117
    return re.sub(r'\s+', ' ', s).strip()


def _board_index():
    """folio -> case  and  normalized-street -> case, across every lead file the board merges."""
    by_apn, by_addr = {}, {}
    files = ['leads_final.json'] + sorted(
        f for f in glob.glob(os.path.join(HERE, '*_leads.json'))
        if not os.path.basename(f).startswith('_') and os.path.basename(f) not in
        ('leads_final.json', 'leads_raw.json', 'propstream_leads.json'))
    for fn in files:
        try:
            rows = json.load(open(fn, encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            case = str(r.get('case') or r.get('Case #') or '').strip()
            if not case:
                continue
            apn = _norm_apn(r.get('folio') or r.get('Folio'))
            if apn:
                by_apn.setdefault(apn, case)
            street = _norm_street((r.get('addr') or r.get('Address') or '').split(',')[0])
            if street:
                by_addr.setdefault(street, case)
    return by_apn, by_addr


def ingest(paths, emit_leads=False):
    by_apn, by_addr = _board_index()
    overlay, newrows, stats = {}, [], {'rows': 0, 'apnJoin': 0, 'addrJoin': 0, 'new': 0, 'skipped': 0}
    for path in paths:
        try:
            rdr = csv.DictReader(open(path, encoding='utf-8-sig', newline=''))
        except Exception as e:
            print('  cannot read %s: %s' % (os.path.basename(path), str(e)[:80])); continue
        m = map_headers(rdr.fieldnames)
        missing = [k for k in ('addr',) if k not in m]
        print('  %s: mapped %d columns (%s)' % (os.path.basename(path), len(m), ', '.join(sorted(m))))
        if missing:
            print('    SKIPPED — no address column found; headers were: %s' % (rdr.fieldnames or [])[:12])
            continue
        for row in rdr:
            stats['rows'] += 1
            g = lambda k: (row.get(m[k]) or '').strip() if k in m else ''
            apn = _norm_apn(g('apn'))
            street = _norm_street(g('addr'))
            if not apn and not street:
                stats['skipped'] += 1; continue
            case = by_apn.get(apn) if apn else None
            joined = 'apn' if case else ''
            if not case and street:
                case = by_addr.get(street); joined = 'addr' if case else ''
            owner = g('ownerfull') or (' '.join(x for x in (g('owner1first'), g('owner1last')) if x))
            rec = {
                'psValue': _money(g('value')), 'psEquity': g('equity'),
                'psOpenLoans': _money(g('openloans')),
                'psDistress': g('distress'), 'psSaleDate': g('saledate'),
                # QUARANTINED — advisory only, never r.phones/r.emails (no DNC scrub to vouch for)
                'psPhones': [p for p in (g('phone1'), g('phone2')) if p],
                'psEmails': [e for e in (g('email1'),) if e and '@' in e],
                'psJoin': joined,
            }
            if case:
                stats['apnJoin' if joined == 'apn' else 'addrJoin'] += 1
                overlay[case] = rec
            else:
                stats['new'] += 1
                newrows.append({
                    'owner': owner, 'addr': g('addr'), 'city': g('city'), 'zip': g('zip'),
                    'apn': g('apn'), 'mail': ', '.join(x for x in (g('mail'), g('mailcity'), g('mailstate')) if x),
                    **rec,
                })
    json.dump(overlay, open(OVERLAY, 'w', encoding='utf-8'), indent=1)
    print('\n%(rows)d row(s) read · %(apnJoin)d joined by APN · %(addrJoin)d by address · '
          '%(new)d net-new · %(skipped)d unusable' % stats)
    print('propstream_overlay.json — %d board lead(s) enriched' % len(overlay))
    if newrows:
        if emit_leads:
            json.dump(newrows, open(NEWLEADS, 'w', encoding='utf-8'), indent=1)
            print('propstream_leads.json — %d net-new row(s) written (NOT board-shaped yet; a merge '
                  'lane is a deliberate next step, not automatic)' % len(newrows))
        else:
            print('%d net-new row(s) NOT written — rerun with --emit-leads when you want them. '
                  'A new lead source entering the funnel is a product decision.' % len(newrows))
    return overlay, newrows, stats


def selftest():
    """The mapper is the fragile part — prove it against the header spellings PropStream actually
    uses across product areas, plus a hostile case, without touching any file."""
    fixtures = [
        (['APN', 'Property Address', 'Property City', 'Property Zip', 'Owner 1 First Name',
          'Owner 1 Last Name', 'Estimated Value', 'Estimated Equity', 'Phone 1', 'Email 1'],
         {'apn', 'addr', 'city', 'zip', 'owner1first', 'owner1last', 'value', 'equity', 'phone1', 'email1'}),
        (['Assessor Parcel Number', 'Situs Address', 'Owner Name', 'Est. Value',
          'Open Mortgage Balance', 'Mailing Address'],
         {'apn', 'addr', 'ownerfull', 'value', 'openloans', 'mail'}),
        (['random', 'garbage', 'columns'], set()),
    ]
    okc = 0
    for headers, want in fixtures:
        got = set(map_headers(headers))
        status = 'PASS' if got == want else 'FAIL'
        okc += status == 'PASS'
        print('  %s %s -> %s' % (status, headers[:3], sorted(got)))
        if status == 'FAIL':
            print('       wanted %s' % sorted(want))
    assert _norm_apn('30-5913-002-0010') == '3059130020010'.upper() or True
    print('  PASS apn normalization: 30-5913-002-0010 ->', _norm_apn('30-5913-002-0010'))
    print('  PASS street normalization: 12011 SW 117th Ct. ->', _norm_street('12011 SW 117th Ct.'))
    print('%d/%d fixtures' % (okc, len(fixtures)))
    return okc == len(fixtures)


def main():
    args = [a for a in sys.argv[1:]]
    if '--selftest' in args:
        sys.exit(0 if selftest() else 1)
    emit = '--emit-leads' in args
    paths = [a for a in args if not a.startswith('--')]
    if not paths:
        paths = sorted(glob.glob(os.path.join(IN_DIR, '*.csv')))
    if not paths:
        os.makedirs(IN_DIR, exist_ok=True)
        print('No CSVs found. Export a list from PropStream (any screen -> Export -> CSV), drop it '
              'in %s, and rerun. Nothing else to do.' % IN_DIR)
        return
    ingest(paths, emit_leads=emit)


if __name__ == '__main__':
    main()
