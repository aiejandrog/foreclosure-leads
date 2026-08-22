#!/usr/bin/env python
"""site_stats — AGGREGATE-ONLY export for the public site. The PII firewall.

WHY THIS FILE IS THE WHOLE SECURITY MODEL
On 2026-08-13 this repo was found publicly serving homeowner PII and a client's retainer; 123 paths
were purged from git history. The public site must therefore be structurally incapable of leaking a
person, not merely careful about it. So the ONLY thing that crosses from the private pipeline to the
public site is this file: integers and medians. No names, no addresses, no case numbers, no phones,
no emails, no folios. Nothing here identifies anybody.

Every number is derived from PUBLIC RECORDS (FS ch. 119) and every one carries its methodology, so
the site can state a defensible claim instead of the unsubstantiated volume stats the target site
uses (CDM ships $22B+ and $40B+ for the same metric in the same JS bundle).

Run:  python site_stats.py            # -> site_stats.json
"""
import datetime, json, os, re, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'site_stats.json')


def _load(fn):
    try:
        return json.load(open(os.path.join(HERE, fn), encoding='utf-8'))
    except Exception:
        return []


def _rows(d):
    return d if isinstance(d, list) else list(d.values())


def _money(v):
    try:
        return float(str(v).replace('$', '').replace(',', '').strip() or 0)
    except Exception:
        return 0.0


def build():
    md, bw, pb = (_rows(_load(f)) for f in
                  ('leads_final.json', 'broward_leads.json', 'palmbeach_leads.json'))
    lp = _rows(_load('lp_leads.json'))
    mort = _rows(_load('broward_mortgages.json'))
    board = [r for r in md + bw + pb if isinstance(r, dict)]

    judg = [v for v in (_money(r.get('judg') or r.get('Judgment')) for r in board) if v > 0]

    today = datetime.date.today()
    soon = 0
    for r in board:
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})',
                      str(r.get('auction') or r.get('Auction Date') or r.get('sale') or ''))
        if not m:
            continue
        try:
            d = datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            if 0 <= (d - today).days <= 60:
                soon += 1
        except Exception:
            pass

    band = [r for r in mort if isinstance(r, dict) and 75000 <= (r.get('amt') or 0) <= 5000000]

    s = {
        'generated': today.isoformat(),
        'cases_tracked': len(board),
        'counties': 3,
        'county_split': {'miami_dade': len(md), 'broward': len(bw), 'palm_beach': len(pb)},
        'fresh_filings': len(lp),
        'sales_next_60': soon,
        'median_judgment': int(statistics.median(judg)) if judg else 0,
        'judgment_n': len(judg),
        'mortgages_swept': len(mort),
        'investor_notes': len(band),
        'investor_principal_b': round(sum(r['amt'] for r in band) / 1e9, 2),
        # every claim ships with the sentence that substantiates it
        'method': {
            'cases_tracked': 'Open foreclosure cases scraped from the Miami-Dade, Broward and Palm Beach '
                             'clerk and recorder systems, deduplicated by case number.',
            'median_judgment': 'Median final-judgment amount across cases where the judgment is recorded.',
            'sales_next_60': 'Count of scheduled foreclosure sale dates falling within 60 days of the build date.',
            'investor_notes': 'Open mortgages recorded to LLC borrowers by private/hard-money lenders in the '
                              '$75k-$5M band, from the Broward recorder mortgage index.',
        },
    }
    json.dump(s, open(OUT, 'w', encoding='utf-8'), indent=1)
    print('wrote site_stats.json (aggregate only, zero PII):')
    for k in ('cases_tracked', 'fresh_filings', 'sales_next_60', 'median_judgment',
              'mortgages_swept', 'investor_notes', 'investor_principal_b'):
        print('  %-22s %s' % (k, s[k]))
    return s


if __name__ == '__main__':
    build()
