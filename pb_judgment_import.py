#!/usr/bin/env python
"""Manual Palm Beach judgment import. No eCaseView, no Landmark, no network.

Palm Beach judgments sit on eCaseView, whose terms limit the site to non-commercial use.
Landmark does not publish amounts. This script does not open either. Alex saves a judgment
PDF by hand into a folder. The script reads that local file, runs the same check as
broward_judgment_read.read_judgment, and writes the same record.

    python pb_judgment_import.py --in DIR --out judgment_records.json   # -> DEALFLOW_DIR\judgment_records.json

A .txt file whose pages are separated by a form feed is accepted so a test can run without
a PDF library. A PDF is read with the local text extractor (PyMuPDF) when it is installed.
A missing extractor, an empty page, a satisfaction, or an HOA/CONO judgment writes no record.
Stdout is counts only.
"""
import argparse
import json
import os
import re
import sys

import broward_judgment_read as BJR

_CASE_RE = re.compile(
    r'\b((?:CACE|CONO|COCE|COWE|COSO)-\d{2}-\d{6}|50\d{4}(?:CA|CC)[A-Z0-9]{6,16})\b', re.I)


def _norm(case):
    return re.sub(r'[^A-Z0-9]', '', str(case or '').upper())


def case_from_name(path):
    m = _CASE_RE.search(os.path.splitext(os.path.basename(path))[0])
    return m.group(1).upper() if m else ''


def case_from_text(pages):
    found = []
    for page in pages or []:
        for m in _CASE_RE.finditer(page or ''):
            found.append(m.group(1).upper())
    uniq = list(dict.fromkeys(found))
    return uniq[0] if len(uniq) == 1 else ''


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
    """Write records for foreclosure judgments whose total reproduced. Returns counts."""
    counts = {'files': 0, 'records': 0, 'void': 0, 'hoa': 0, 'failed': 0}
    records = []
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
        result = BJR.read_judgment(pages, case)
        if result.get('void'):
            counts['void'] += 1
            continue
        if result.get('instrument') == 'hoa_judgment':
            counts['hoa'] += 1
            continue
        record = result.get('record')
        if result.get('ok') and isinstance(record, dict):
            records.append(record)
            counts['records'] += 1
        else:
            counts['failed'] += 1
    if not records and os.path.exists(out_path):
        return counts                                  # a run that read nothing never empties an earlier file
    tmp = out_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, out_path)
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
    if full == repo or full.startswith(repo + os.sep) or 'onedrive' in full.lower():
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
    print('files=%d records=%d void=%d hoa=%d failed=%d' % (
        counts['files'], counts['records'], counts['void'], counts['hoa'], counts['failed']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
