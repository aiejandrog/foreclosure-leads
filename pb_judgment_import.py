#!/usr/bin/env python
"""Manual Palm Beach judgment import. No eCaseView, no Landmark, no network.

Palm Beach judgments sit on eCaseView, whose terms limit the site to non-commercial use.
Landmark does not publish amounts. This script does not open either. Alex saves a judgment
PDF by hand into a folder. The script reads that local file, runs the same check as
broward_judgment_read.read_judgment, and writes the same record.

    python pb_judgment_import.py --in DIR --out judgment_records.json   # -> DEALFLOW_DIR/judgment_records.json

A .txt file whose pages are separated by a form feed is accepted so a test can run without
a PDF library. A PDF is read with the local text extractor (PyMuPDF) when it is installed.
A missing extractor, an empty page, or an HOA/CONO judgment writes no judgment total.
A satisfaction is not a total either: a later satisfaction of judgment, or of a mortgage,
is matched to the judgment or lien it names by case number and by instrument reference.
One clear later match is marked satisfied with that date and the file it was read from.
Two matches, a missing date, a partial satisfaction, or a satisfaction dated before the
record are flagged for review and left open. Stdout is counts only.
"""
import argparse
import json
import os
import re
import sys

import broward_judgment_read as BJR

_BOOK_PAGE_RE = re.compile(
    r'\b(?:Official\s+Records?\s+Book|O\.?\s*R\.?\s*Book|Book)\s*#?\s*(\d{3,6})'
    r'\s*,?\s*(?:Page|Pg\.?|P\.)\s*#?\s*(\d{1,6})\b', re.I)
_INSTRUMENT_NO_RE = re.compile(
    r'\bInstrument\s+(?:No\.?|Number|#)?\s*[:#]?\s*(\d{5,12})\b', re.I)


def _norm(case):
    return re.sub(r'[^A-Z0-9]', '', str(case or '').upper())


def case_from_name(path):
    found = BJR.cases_in_text(os.path.splitext(os.path.basename(path))[0])
    return found[0] if found else ''


def case_from_text(pages):
    found = []
    for page in pages or []:
        for number in BJR.cases_in_text(page or ''):
            if number not in found:
                found.append(number)
    return found[0] if len(found) == 1 else ''


def _joined(pages):
    return '\n'.join(str(p) for p in (pages or []))


def instrument_refs(text):
    """Book/page and instrument numbers the document itself cites. No street address."""
    keys = []
    for match in _BOOK_PAGE_RE.finditer(text or ''):
        key = '%d/%d' % (int(match.group(1)), int(match.group(2)))
        if key not in keys:
            keys.append(key)
    for match in _INSTRUMENT_NO_RE.finditer(text or ''):
        key = 'instrument:%s' % str(int(match.group(1)))
        if key not in keys:
            keys.append(key)
    return keys


def satisfaction_of(pages, source):
    """A satisfaction of judgment or mortgage, or None.

    The amount table on a satisfaction is never a judgment total. `kind` is taken from
    the heading: a fee or association satisfaction is `other` and is not applied to the
    foreclosure judgment.
    """
    text = _joined(pages)
    if not BJR._is_satisfaction_doc(text):
        return None
    title = BJR._title(text, n=8)
    if re.search(r'\b(?:SATISFACTION|RELEASE)\s+OF\s+(?:THE\s+)?MORTGAGE\b', title, re.I):
        kind = 'mortgage'
    elif BJR._FEE_HOA_RE.search(title) or (
            BJR._FEE_HOA_RE.search(text) and not BJR._targets_this_foreclosure(text)):
        kind = 'other'
    elif re.search(r'\bSATISFACTION\s+OF\b', title, re.I):
        kind = 'judgment'
    else:
        kind = 'other'
    dates = BJR._dates(text)
    return {
        'kind': kind,
        'partial': bool(BJR._PARTIAL_RE.search(text)),
        'cases': BJR.cases_in_text(text),
        'instruments': instrument_refs(text),
        'date': dates[0] if len(dates) == 1 else '',
        'date_count': len(dates),
        'source': os.path.basename(source or ''),
    }


def lien_of(pages, source, case):
    """A mortgage lien identified by case number or instrument. No judgment amount is stored."""
    text = _joined(pages)
    if BJR.document_signal(text) != 'mortgage' or BJR._is_satisfaction_doc(text):
        return None
    cases = BJR.cases_in_text(text)
    refs = instrument_refs(text)
    if len(cases) == 1:
        rec_case = cases[0]
    elif case and BJR.case_matches(text, case):
        rec_case = BJR.canon_case(case) or str(case).strip()
    elif case and not cases:
        rec_case = BJR.canon_case(case) or ''
    else:
        rec_case = ''
    if not rec_case and not refs:
        return None
    dates = BJR._dates(text)
    return {
        'record_kind': 'mortgage_lien',
        'case': rec_case,
        'instrument_refs': refs,
        'document_date': dates[0] if len(dates) == 1 else '',
        'source': os.path.basename(source or ''),
        'satisfied': False,
        'human_reviewed': False,
    }


def _kind_of(rec):
    return rec.get('record_kind') or 'foreclosure_judgment'


def _record_date(rec):
    return rec.get('judgment_date') or rec.get('document_date') or ''


def _flag(rec, sat, reason):
    review = rec.setdefault('review', [])
    if reason not in review:
        review.append(reason)
    rec.setdefault('satisfaction_review', []).append({
        'source': sat.get('source') or '',
        'date': sat.get('date') or '',
        'kind': sat.get('kind') or '',
        'reason': reason,
    })


def _case_hit(rec, cases):
    want = {_norm(c) for c in cases or [] if _norm(c)}
    if not want:
        return False
    have = {_norm(rec.get('case'))}
    have.discard('')
    return bool(have & want)


def _instrument_hit(rec, instruments):
    if not instruments:
        return False
    return bool(set(instruments) & set(rec.get('instrument_refs') or []))


def satisfaction_targets(records, sat):
    """('unique'|'ambiguous'|'none', records). A tie is not narrowed by guessing."""
    kind = sat.get('kind')
    if kind == 'judgment':
        pool = [r for r in records if _kind_of(r) == 'foreclosure_judgment']
    elif kind == 'mortgage':
        pool = [r for r in records if _kind_of(r) == 'mortgage_lien']
    else:
        return 'none', []
    cases, instruments = sat.get('cases') or [], sat.get('instruments') or []
    if not cases and not instruments:
        return 'none', []
    matched = []
    for rec in pool:
        case_ok = _case_hit(rec, cases) if cases else True
        inst_ok = _instrument_hit(rec, instruments) if instruments else True
        if cases and instruments:
            hit = case_ok and inst_ok
        elif cases:
            hit = case_ok
        else:
            hit = inst_ok
        if hit:
            matched.append(rec)
    if len(matched) == 1:
        return 'unique', matched
    if len(matched) > 1:
        return 'ambiguous', matched
    return 'none', []


def apply_satisfaction(records, sat):
    """Mark one later payoff, or flag the candidates and leave them open.

    Returns 'satisfied', 'review', or 'none'.
    """
    if sat.get('date_count', 0) > 1:
        _status, targets = satisfaction_targets(records, sat)
        for rec in targets:
            _flag(rec, sat, 'ambiguous_satisfaction')
        return 'review' if targets else 'none'
    status, targets = satisfaction_targets(records, sat)
    if status == 'ambiguous':
        for rec in targets:
            _flag(rec, sat, 'ambiguous_satisfaction')
        return 'review'
    if status != 'unique':
        return 'none'
    rec = targets[0]
    if sat.get('partial'):
        _flag(rec, sat, 'partial_satisfaction')
        return 'review'
    if rec.get('satisfied'):
        _flag(rec, sat, 'ambiguous_satisfaction')
        return 'review'
    sat_date = sat.get('date') or ''
    rec_date = _record_date(rec)
    if not sat_date:
        _flag(rec, sat, 'satisfaction_date_missing')
        return 'review'
    if rec_date and sat_date < rec_date:
        _flag(rec, sat, 'satisfaction_not_later')
        return 'review'
    rec['satisfied'] = True
    rec['satisfied_date'] = sat_date
    rec['satisfied_source'] = sat.get('source') or ''
    rec['satisfaction_kind'] = sat.get('kind') or ''
    return 'satisfied'


def _read_existing(path):
    """The records already on disk, [] when there is no file, None when the file is unusable."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        return None
    return data


def _write_records(path, records):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(records, fh, indent=1, sort_keys=True)
        fh.write('\n')
    os.replace(tmp, path)


def pages_from_pdf(path, opener=None):
    """(pages, problem). opener(path) -> [page text] lets a test skip the PDF library."""
    if opener is None:
        try:
            import fitz
        except ImportError:
            return None, 'pdf_text_extractor_missing'

        def opener(p):
            doc = fitz.open(p)
            try:
                return [doc[i].get_text('text') or '' for i in range(doc.page_count)]
            finally:
                doc.close()
    try:
        pages = [str(p) for p in (opener(path) or [])]
    except Exception:
        return None, 'pdf_unreadable'
    if not pages:
        return None, 'pdf_empty'
    return pages, ''


def pages_from_text_file(path):
    try:
        with open(path, encoding='utf-8') as fh:
            raw = fh.read()
    except OSError:
        return None, 'text_unreadable'
    pages = raw.split('\f')
    return pages, ''


def load_pages(path, opener=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return pages_from_pdf(path, opener=opener)
    if ext == '.txt':
        return pages_from_text_file(path)
    return None, 'unsupported_file'


def import_folder(folder, out_path, opener=None):
    """Write checked judgment totals and mortgage liens, then apply satisfactions.

    A run that reads nothing leaves an earlier file in place. A run that reads only
    satisfactions updates that file in place. A file that cannot be parsed is left
    untouched.
    """
    counts = {'files': 0, 'records': 0, 'void': 0, 'hoa': 0, 'failed': 0,
              'satisfied': 0, 'review': 0, 'liens': 0}
    records = []
    satisfactions = []
    if not folder or not os.path.isdir(folder):
        counts['failed'] += 1
        return counts
    names = sorted(n for n in os.listdir(folder)
                   if n.lower().endswith(('.pdf', '.txt')) and not n.startswith('.'))
    for name in names:
        counts['files'] += 1
        path = os.path.join(folder, name)
        pages, problem = load_pages(path, opener=opener)
        if problem:
            counts['failed'] += 1
            continue
        from_name, from_text = case_from_name(path), case_from_text(pages)
        if from_name and from_text and _norm(from_name) != _norm(from_text):
            counts['failed'] += 1
            continue
        case = from_text or from_name
        sat = satisfaction_of(pages, name)
        if sat:
            satisfactions.append(sat)
            if case:
                result = BJR.read_judgment(pages, case)
                if result.get('void'):
                    counts['void'] += 1
            continue
        if not case:
            counts['failed'] += 1
            continue
        result = BJR.read_judgment(pages, case)
        if result.get('void'):
            counts['void'] += 1
            continue
        if result.get('instrument') == 'hoa_judgment':
            counts['hoa'] += 1
            continue
        record = result.get('record')
        if result.get('ok') and isinstance(record, dict):
            stored = dict(record)
            stored['record_kind'] = 'foreclosure_judgment'
            stored['instrument_refs'] = instrument_refs(_joined(pages))
            stored['source'] = name
            stored['satisfied'] = False
            records.append(stored)
            counts['records'] += 1
            continue
        if result.get('reason') == 'unverified':
            lien = lien_of(pages, name, case)
            if lien:
                records.append(lien)
                counts['liens'] += 1
                continue
        counts['failed'] += 1
    if not records and satisfactions:
        existing = _read_existing(out_path)
        if existing is None:
            counts['failed'] += 1
            return counts                               # torn file: do not guess, do not replace it
        records = existing
    elif not records and os.path.exists(out_path):
        return counts                                   # a run that read nothing never empties an earlier file
    for sat in sorted(satisfactions, key=lambda s: (s.get('date') or '9999-99-99', s.get('source') or '')):
        outcome = apply_satisfaction(records, sat)
        if outcome == 'satisfied':
            counts['satisfied'] += 1
        elif outcome == 'review':
            counts['review'] += 1
    if not records and not satisfactions and os.path.exists(out_path):
        return counts
    _write_records(out_path, records)
    return counts


def resolve_out(out):
    """Records carry case ids, so they live outside the repo and outside OneDrive (CLAUDE.md, Output
    paths). A bare name goes to paths.P.out(); a path inside the repo or OneDrive -> '' (refused)."""
    out = str(out or '').strip()
    if not out:
        return ''
    if not os.path.isabs(out) and os.path.dirname(out) in ('', '.'):
        import paths as P
        return P.out(os.path.basename(out))
    full = os.path.realpath(os.path.abspath(out))
    repo = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
    f, r = os.path.normcase(full), os.path.normcase(repo)
    if f == r or f.startswith(r + os.sep) or 'onedrive' in f.lower() or os.path.isdir(full):
        return ''
    return full


def main(argv):
    if any(str(a).lower().startswith(('http://', 'https://')) for a in argv):
        print('refused: local files only')
        return 2
    p = argparse.ArgumentParser(description='Read hand-saved judgment files into checked totals')
    p.add_argument('--in', dest='folder', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    out = resolve_out(args.out)
    if not out:
        print('refused: --out must be a file name (written to DEALFLOW_DIR) or a path outside the repo and OneDrive')
        return 2
    counts = import_folder(args.folder, out)
    print('files=%d records=%d void=%d hoa=%d failed=%d satisfied=%d review=%d liens=%d' % (
        counts['files'], counts['records'], counts['void'], counts['hoa'], counts['failed'],
        counts['satisfied'], counts['review'], counts['liens']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
