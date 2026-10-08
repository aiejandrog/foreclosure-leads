"""Board-lead owner repair — DRY by default, --write to apply.

NOTE (2026-10-08): pacer_stay's recorded loader now falls back owners->oname->paOwner, so the common
case (clean oname/paOwner present, owners empty) is handled automatically at search time — no write
needed. This stays as (a) the DRY diagnostic that counts ownerless/repairable/orphan board leads, and
(b) an optional manual --write for any consumer that reads `owners` directly and does NOT fall back.


The recorded PACER stay search reads the `owners` field on each board lead (leads_final.json,
*_leads.json, lp_leads.json). A lead whose `owners` is empty / an entity / an unsplittable string
returns "no owner name" and can never clear. This repairs those: when `owners` is unusable but a
clean person name exists in a TRUSTWORTHY fallback field on the same record (paOwner = Property
Appraiser owner-of-record, then oname/rname/owner), copy it into `owners` so the next refresh runs a
real recorded search.

SAFETY:
  * DRY by default — prints counts only, no names, writes nothing.
  * --write makes a timestamped backup of each file first, only touches records it repairs, and only
    when the fallback is a clean splittable person name (never an entity/estate/ET-AL string).
  * Never invents a name; only promotes one already on the same record from a better field.
  * Skips dismissed/closed/balloon rows. Optional --window N limits to leads with a sale within N days.
"""
import datetime as dt
import glob
import json
import os
import re
import shutil
import sys

OWNER_FALLBACK = ('paOwner', 'oname', 'rname', 'owner', 'Owner')   # trustworthy -> less so; NOT defendant
_ENTITY = re.compile(r'\b(LLC|L\.?L\.?C|INC|CORP|TRUST|TRUSTEES?|BANK|MORTGAGE|SERVICING|HOLDINGS?|'
                     r'PROPERTIES|INVESTMENTS?|CAPITAL|FUND|ASSOC|LP|LLP|COMPANY|REALTY|HOMES|GROUP|'
                     r'PARTNERS?|MANAGEMENT|ENTERPRISES?|REO|N\.?A\.?|FEDERAL|NATIONAL|SAVINGS|'
                     r'CREDIT UNION|HOA|HUD|SECRETARY|COUNTY|CITY OF|STATE OF|ASSOCIATION|CONDOMINIUM)\b', re.I)
_ESTATE = re.compile(r'\bEST(ATE)? OF\b|\bDECEASED\b|\bHEIRS\b|\bUNKNOWN\b|\bET\s*AL\b|\bETAL\b', re.I)
FILES = ('leads_final.json', '*_leads.json', 'lp_leads.json')
SALE_FIELDS = ('AuctionDate', 'auction', 'sale_date')


def usable(name):
    name = str(name or '').strip()
    if not name or name.startswith('(') or _ENTITY.search(name) or _ESTATE.search(name):
        return False
    first = name.split(';')[0]
    toks = [t for t in re.split(r'[,\s]+', first) if any(c.isalpha() for c in t)]
    return len(toks) >= 2


def sale_days(rec, today):
    for f in SALE_FIELDS:
        s = str(rec.get(f) or '').strip()
        m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s) or re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
        if m:
            try:
                d = (dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if '/' in s[:5]
                     else dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                return (d - today).days
            except ValueError:
                pass
    return None


def main(argv):
    write = '--write' in argv
    argv = [a for a in argv if a != '--write']
    window = None
    if '--window' in argv:
        i = argv.index('--window'); window = int(argv[i + 1]); del argv[i:i + 2]
    today = dt.date.today()
    if '--today' in argv:                       # override for reproducible runs, e.g. --today 2026-10-08
        i = argv.index('--today'); today = dt.date.fromisoformat(argv[i + 1]); del argv[i:i + 2]
    here = os.getcwd()
    paths = []
    for pat in FILES:
        paths += [p for p in glob.glob(os.path.join(here, pat)) if not os.path.basename(p).startswith('_')
                  and os.path.basename(p) not in ('leads_raw.json', 'balloon_leads.json')]
    paths = sorted(set(paths))
    print('mode: %s   window: %s' % ('WRITE' if write else 'DRY (counts only, nothing written)',
                                     ('<=%dd to sale' % window) if window else 'all active'))
    print('%-26s %6s %8s %8s %10s %9s' % ('file', 'active', 'has_own', 'no_own', 'repairable', 'orphan'))
    print('-' * 74)
    tot = dict(active=0, has=0, no=0, repair=0, orphan=0, wrote=0)
    stamp = '20261008-repair'
    for path in paths:
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        active = has = no = repair = orphan = wrote = 0
        changed = False
        for rec in data:
            if not isinstance(rec, dict) or rec.get('lpDismissed') or rec.get('lpClosed') or rec.get('st') == 'BAL':
                continue
            if window is not None:
                d = sale_days(rec, today)
                if d is None or d < 0 or d > window:
                    continue
            active += 1
            if usable(rec.get('owners')):
                has += 1
                continue
            no += 1
            src = next((f for f in OWNER_FALLBACK if usable(rec.get(f))), None)
            if src:
                repair += 1
                if write:
                    rec['owners'] = str(rec[src]).strip()
                    rec['owners_repaired_from'] = src        # audit trail on the record
                    changed = True
                    wrote += 1
            else:
                orphan += 1
        print('%-26s %6d %8d %8d %10d %9d' % (os.path.basename(path), active, has, no, repair, orphan))
        for k, v in (('active', active), ('has', has), ('no', no), ('repair', repair), ('orphan', orphan), ('wrote', wrote)):
            tot[k] += v
        if write and changed:
            bak = path + '.' + stamp + '.bak'
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-' * 74)
    print('TOTAL  active=%d  has_owner=%d  no_owner=%d  repairable=%d  orphan=%d%s'
          % (tot['active'], tot['has'], tot['no'], tot['repair'], tot['orphan'],
             ('  WROTE=%d (backups: *.%s.bak)' % (tot['wrote'], stamp)) if write else ''))
    if not write:
        print('DRY — nothing written. Re-run with --write (and optional --window N) to apply.')


if __name__ == '__main__':
    main(sys.argv[1:])
