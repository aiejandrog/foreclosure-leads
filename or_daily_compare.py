#!/usr/bin/env python
"""or_daily_compare.py — one day's Official Records file against the records_liens scraper.

Written to answer the 2026-09-25 question: for the same Miami-Dade cases, what did
`dly_records_09252026` catch that the owner-search chain caught, and what did each
miss? The script is the same comparison for any later file date.

THE TWO SIDES ARE NOT THE SAME KIND OF SEARCH
The daily file is one night's recordings (plus, on the sample day, a few older
dates that were in that delivery). records_liens.json is every instrument an
owner-name search has ever returned for a case. Scoring the whole chain as
"missed by the daily file" would call ten years of mortgages a miss. The overlap
below is the file date only. The chain's other dates are a count, for context.

Instruments are the same recording when the case and the book/page agree.
Liens, judgments, and mortgages are counted separately. Party names are not
written into the report.

Report only. The markdown file goes under ~/DEALFLOW/or_daily, outside the repo.
Nothing here changes send, callable, or hold.

    python or_daily_compare.py --date 2026-09-25
    python or_daily_compare.py                  # newest file already loaded
"""
import argparse
import datetime as dt
import json
import os
import sys

import or_daily_file as daily

COMPARE = ('lien', 'judgment', 'mortgage')
LIST_CAP = 25


def scraper_category(row):
    doc = (row.get('doc') or '').upper()
    kind = (row.get('kind') or '').lower()
    if 'SATISF' in doc or 'RELEASE' in doc:
        return ''
    if 'JUDGMENT' in doc or kind == 'judgment':
        return 'judgment'
    if 'LIS PENDENS' in doc or kind == 'lis_pendens':
        return ''
    if 'MORTGAGE' in doc and 'LIEN' not in doc:
        return 'mortgage'
    if kind in ('association', 'code', 'irs', 'state_tax') or 'LIEN' in doc:
        return 'lien'
    return ''


def instruments_from_chain(chain):
    """Mortgages from the chain's `liens` list; judgments and liens from `other`."""
    out = []
    if not isinstance(chain, dict):
        return out
    seen = set()

    def add(category, bp, rec_date, doc):
        if category not in COMPARE or not bp:
            return
        key = (category, bp)
        if key in seen:
            return
        seen.add(key)
        out.append({'category': category, 'bp': bp, 'rec_date': rec_date or '', 'doc': doc or ''})

    for row in chain.get('liens') or []:
        if isinstance(row, dict):
            add('mortgage', daily.parse_bp_text(row.get('bp')), daily.parse_loose_date(row.get('d')),
                'MORTGAGE')
    for row in chain.get('other') or []:
        if not isinstance(row, dict):
            continue
        add(scraper_category(row), daily.parse_bp_text(row.get('bp')),
            daily.parse_loose_date(row.get('d')), row.get('doc') or '')
    return out


def load_scraper(path):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for case, chain in data.items():
        key = daily.norm_case(case)
        rows = instruments_from_chain(chain)
        if key and rows:
            out[key] = rows
    return out


def _bp_of(match):
    bp = match.get('bp') or ''
    parsed = daily.parse_bp_text(bp) if isinstance(bp, str) else bp
    return daily.bp_text(parsed) if parsed else ''


def build_report(file_date, daily_matches, scraper_by_case):
    """Counts and miss lists. Both inputs are already limited to the Miami cases in scope."""
    if hasattr(file_date, 'isoformat'):
        file_date = file_date.isoformat()
    file_date = str(file_date or '')
    sections = {}
    for cat in COMPARE:
        daily_items = []
        for match in daily_matches or []:
            if match.get('category') != cat:
                continue
            bp = _bp_of(match)
            if not bp or not match.get('case'):
                continue
            daily_items.append({'case': match['case'], 'bp': bp,
                                'rec_date': match.get('rec_date') or ''})
        scraper_all, scraper_day = [], []
        for case, rows in (scraper_by_case or {}).items():
            for row in rows:
                if row.get('category') != cat or not row.get('bp'):
                    continue
                bp = row['bp'] if isinstance(row['bp'], str) else daily.bp_text(row['bp'])
                if not bp:
                    continue
                item = {'case': case, 'bp': bp, 'rec_date': row.get('rec_date') or ''}
                scraper_all.append(item)
                if item['rec_date'] == file_date:
                    scraper_day.append(item)

        def keys(items):
            return {(item['case'], item['bp']) for item in items}

        daily_keys, day_keys, all_keys = keys(daily_items), keys(scraper_day), keys(scraper_all)
        sections[cat] = {
            'daily': len(daily_keys),
            'scraper_same_day': len(day_keys),
            'overlap': len(daily_keys & day_keys),
            'scraper_chain': len(all_keys),
            'daily_only': [{'case': c, 'bp': b} for c, b in sorted(daily_keys - day_keys)],
            'scraper_only': [{'case': c, 'bp': b} for c, b in sorted(day_keys - daily_keys)],
            'not_in_chain': [{'case': c, 'bp': b} for c, b in sorted(daily_keys - all_keys)],
        }
    cases = {m['case'] for m in (daily_matches or []) if m.get('case')}
    cases |= set(scraper_by_case or {})
    return {
        'file_date': file_date,
        'report_only': True,
        'affects_send': False,
        'cases': len(cases),
        'categories': sections,
    }


def _list_block(title, rows):
    lines = ['', title]
    if not rows:
        lines.append('(none)')
        return lines
    for row in rows[:LIST_CAP]:
        lines.append('- %s  %s' % (row['case'], row['bp']))
    if len(rows) > LIST_CAP:
        lines.append('- ... %d more' % (len(rows) - LIST_CAP))
    return lines


def render(report):
    lines = [
        '# Daily file vs records_liens — %s' % (report.get('file_date') or '?'),
        '',
        'Report only. This comparison does not change send, callable, or hold.',
        '',
        'Same Miami-Dade cases. The daily file is one delivery. The scraper chain is',
        'every instrument an owner search has returned, so the overlap is the file',
        'date only. A recording the chain holds from another day is not a miss.',
        '',
        'Cases in this comparison: %s' % report.get('cases'),
        '',
    ]
    labels = {'lien': 'Liens', 'judgment': 'Judgments', 'mortgage': 'Mortgages'}
    for cat in COMPARE:
        sec = report['categories'][cat]
        lines.append('## %s' % labels[cat])
        lines.append('- Daily file matched: %d' % sec['daily'])
        lines.append('- Scraper recorded on this date: %d' % sec['scraper_same_day'])
        lines.append('- Overlap: %d' % sec['overlap'])
        lines.append('- Daily only (not in the scraper on this date): %d' % len(sec['daily_only']))
        lines.append('- Scraper only (this date, not in the daily file): %d' % len(sec['scraper_only']))
        lines.append('- Daily matches the full scraper chain does not contain: %d' % len(sec['not_in_chain']))
        lines.append('- Scraper chain, any date (context): %d' % sec['scraper_chain'])
        lines += _list_block('Daily only:', sec['daily_only'])
        lines += _list_block('Scraper only:', sec['scraper_only'])
        lines.append('')
    return '\n'.join(lines)


def newest_file_date(db_path):
    if not db_path or not os.path.exists(db_path):
        return ''
    conn = daily.open_db(db_path)
    try:
        rows = conn.execute('SELECT name FROM applied').fetchall()
    finally:
        conn.close()
    dates = [daily.file_stamp(row['name']) for row in rows]
    dates = [d for d in dates if d]
    return max(dates) if dates else ''


def matches_for_date(db_path, repo, file_date):
    """Match the one delivery. A file can carry a few older recording dates; the source file is the delivery."""
    if not os.path.exists(db_path):
        return []
    stamp = file_date if isinstance(file_date, str) else file_date.isoformat()
    name = 'dly_records_' + dt.date.fromisoformat(stamp).strftime('%m%d%Y')
    docs = daily.documents_from_rows(_rows_for_file(db_path, name))
    cases = daily.load_tracked_cases(repo)
    chain = daily.load_chain_book_pages(os.path.join(repo, 'records_liens.json'))
    index = daily.load_index_cfn_pages(os.path.join(repo, 'records_index.json'))
    return daily.match_documents(docs, cases, chain, index)


def _rows_for_file(db_path, name):
    conn = daily.open_db(db_path)
    try:
        return daily.live_party_rows(conn, name)
    finally:
        conn.close()


def scope_scraper(scraper, tracked):
    if tracked is None:
        return scraper
    return {case: rows for case, rows in scraper.items() if case in tracked}


def write_report(text, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
        if not text.endswith('\n'):
            fh.write('\n')
    os.replace(tmp, out_path)


def main(argv=None, repo_dir=None, db_path=None, liens_path=None, out_path=None):
    ap = argparse.ArgumentParser(description='Compare one Official Records daily file with records_liens')
    ap.add_argument('--date', default='', help='file date YYYY-MM-DD (default: newest loaded file)')
    ap.add_argument('--db', default='', help='or_daily.sqlite (default: ~/DEALFLOW/or_daily/or_daily.sqlite)')
    ap.add_argument('--liens', default='', help='records_liens.json (default: beside this script)')
    ap.add_argument('--out', default='', help='markdown path (default: beside the database)')
    args = ap.parse_args(argv)
    repo = repo_dir if repo_dir is not None else daily.HERE
    db = db_path or args.db or os.path.join(daily.P.DEALFLOW_DIR, 'or_daily', 'or_daily.sqlite')
    liens = liens_path or args.liens or os.path.join(repo, 'records_liens.json')
    file_date = args.date or newest_file_date(db)
    if not file_date:
        print('OR daily compare: no loaded daily file to compare')
        return 0
    if out_path is None:
        out_path = args.out
    if not out_path:
        out_path = os.path.join(os.path.dirname(os.path.abspath(db)), 'compare-%s.md' % file_date)
    low = os.path.abspath(out_path).lower()
    repo_abs = os.path.abspath(repo)
    if low == repo_abs.lower() or low.startswith(repo_abs.lower() + os.sep) or 'onedrive' in low:
        print('OR daily compare: refusing to write the report inside the repo or OneDrive')
        return 0
    tracked = set(daily.load_tracked_cases(repo))
    matches = matches_for_date(db, repo, file_date) if os.path.exists(db) else []
    matches = [m for m in matches if m['case'] in tracked] if tracked else matches
    scraper = scope_scraper(load_scraper(liens), tracked if tracked else None)
    report = build_report(file_date, matches, scraper)
    text = render(report)
    write_report(text, out_path)
    sec = report['categories']
    print('OR daily compare: %s liens %d/%d judgments %d/%d mortgages %d/%d (daily/scraper same day) -> %s' % (
        file_date,
        sec['lien']['daily'], sec['lien']['scraper_same_day'],
        sec['judgment']['daily'], sec['judgment']['scraper_same_day'],
        sec['mortgage']['daily'], sec['mortgage']['scraper_same_day'],
        out_path))
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
