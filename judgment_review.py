"""The 12-case eyeball review: each scan-read judgment figure beside the page it came off.

`miami_judgment.judgment_for_analyze` refuses every figure read off a scan (OCR or the vision
reader), even one the document's own line items reproduce, until a person has compared
REVIEW_THRESHOLD of them against the page images. `judgment_corroborations.json` counts the
candidates. This tool is how the checking actually gets done:

    python judgment_review.py                          build the sheet, print where it is
    python judgment_review.py --mark 2025-023462-CA-01 --ok
    python judgment_review.py --mark 2025-023462-CA-01 --wrong --note "page 3 total is 1,061,578.12"
    python judgment_review.py --status                 the count, nothing else

The sheet is a local HTML file under paths.DEALFLOW_DIR (never the repo, never OneDrive): the page
images are court records with homeowner names on them.

The count is REPORTED here, never ACTED on. Nothing in this module changes judgment_for_analyze.
Lifting the refusal is Alejandro's decision, taken after the count is met, in a reviewed commit.
One figure marked wrong is evidence against lifting it at all, whatever the count says, and the
status line says so.
"""
import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone

import case_review
import document_store as DS
import miami_judgment as MJ

SHEET_DIR = 'judgment-review'
RENDER_DPI = 150


def load(filename=MJ.CORROBORATION_LOG):
    target = case_review.output_path(filename)
    try:
        entries = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [], target
    return (entries if isinstance(entries, list) else []), target


def status(entries):
    """-> dict with the counts and one sentence a person can act on."""
    ok = [e for e in entries if (e.get('review') or {}).get('verdict') == 'ok']
    wrong = [e for e in entries if (e.get('review') or {}).get('verdict') == 'wrong']
    pending = [e for e in entries if not e.get('review')]
    if wrong:
        line = ('%d figure(s) marked WRONG against the page image. The scan-figure refusal stays, '
                'whatever the count: a corroborated figure that is wrong is the failure the '
                'refusal exists for.' % len(wrong))
    elif len(ok) >= MJ.REVIEW_THRESHOLD:
        line = ('%d of %d checked correct and none wrong. The refusal in judgment_for_analyze can '
                'now be revisited; that is a decision, not something this tool does.'
                % (len(ok), MJ.REVIEW_THRESHOLD))
    else:
        line = ('%d of %d checked correct, %d waiting for a look, %d more corroborated case(s) '
                'needed before the count can be met.'
                % (len(ok), MJ.REVIEW_THRESHOLD, len(pending),
                   max(0, MJ.REVIEW_THRESHOLD - len(ok) - len(pending))))
    return {'ok': len(ok), 'wrong': len(wrong), 'pending': len(pending),
            'threshold': MJ.REVIEW_THRESHOLD, 'line': line}


def mark(entries, case, verdict, note=None, now=None):
    """Record a human verdict on one case. -> the entry. Raises KeyError for an unknown case."""
    if verdict not in ('ok', 'wrong'):
        raise ValueError('verdict is ok or wrong')
    for entry in entries:
        if entry.get('case') == case:
            entry['checked_against_image'] = True
            entry['review'] = {'verdict': verdict, 'amount_checked': entry.get('amount'),
                               'at': (now or datetime.now(timezone.utc)).isoformat(
                                   timespec='seconds'),
                               'note': note or ''}
            return entry
    raise KeyError(case)


def save(entries, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    DS._atomic_write_text(str(target), json.dumps(entries, indent=2) + '\n')


def _safe(case):
    return ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in str(case))


def page_image(figure, out_dir, case, index):
    """-> file name of a PNG of the figure's page inside out_dir, or None with a reason.

    Rendered from the stored PDF, so the sheet does not depend on the run having kept images
    (the nightly does not). Falls back to an image the run did keep.
    """
    page = figure.get('page')
    pdf = figure.get('pdf')
    name = '%s-%d-p%s.png' % (_safe(case), index, page)
    if pdf and page and os.path.exists(pdf):
        try:
            fitz = DS._fitz()
            with fitz.open(pdf) as doc:
                if 1 <= int(page) <= len(doc):
                    pix = doc[int(page) - 1].get_pixmap(dpi=RENDER_DPI)
                    pix.save(os.path.join(out_dir, name))
                    return name, None
                return None, 'page %s is outside the stored PDF (%d pages)' % (page, len(doc))
        except Exception as exc:
            return None, 'could not render %s: %s' % (os.path.basename(pdf), exc)
    kept = figure.get('images_dir')
    if kept and page and os.path.isdir(kept):
        for candidate in sorted(os.listdir(kept)):
            if candidate.endswith('.png') and ('p%02d' % int(page)) in candidate:
                # A kept image lives beside its PDF, outside the sheet folder: link it by URI.
                from pathlib import Path
                return Path(kept, candidate).resolve().as_uri(), None
    return None, ('no stored PDF for this figure; re-run the case to record where it came from'
                  if not pdf else 'stored PDF is missing: %s' % pdf)


def _money(value):
    return '${:,.2f}'.format(value) if isinstance(value, (int, float)) else 'not established'


def build_sheet(entries, out_dir):
    """Write index.html plus one PNG per figure into out_dir. -> path of index.html."""
    os.makedirs(out_dir, exist_ok=True)
    s = status(entries)
    esc = html.escape
    cards = []
    # Unreviewed first, then wrong, then ok: the sheet is a to-do list.
    order = {None: 0, 'wrong': 1, 'ok': 2}
    for entry in sorted(entries, key=lambda e: (order.get((e.get('review') or {}).get('verdict')),
                                                e.get('case') or '')):
        case = entry.get('case') or '?'
        review = entry.get('review') or {}
        figures = entry.get('figures') or []
        blocks = []
        for i, figure in enumerate(figures):
            img, why = page_image(figure, out_dir, case, i)
            comps = figure.get('components') or []
            comp_html = ''.join('<li>%s</li>' % esc(_money(c)) for c in comps)
            total = sum(c for c in comps if isinstance(c, (int, float)))
            blocks.append(
                '<div class="fig"><div class="img">%s</div><div class="facts">'
                '<div class="amt">%s</div>'
                '<div>page %s, read by <b>%s</b>, %s</div>'
                '<div class="q">%s</div>'
                '<div>line items the reader saw (sum %s):</div><ul>%s</ul>'
                '<div class="why">%s</div></div></div>'
                % (('<img src="%s" alt="page %s">' % (esc(img), esc(str(figure.get('page')))))
                   if img else '<p class="gap">%s</p>' % esc(why),
                   esc(_money(figure.get('amount'))), esc(str(figure.get('page'))),
                   esc(str(figure.get('text_source') or 'embedded')),
                   esc(str(figure.get('source_ref') or '')),
                   esc(figure.get('passage') or ''), esc(_money(total)), comp_html,
                   esc(figure.get('sum_check_reason') or '')))
        if not figures:
            blocks.append('<p class="gap">This entry predates figure locations. Re-run the case '
                          'with miami_judgment.py to put its page here. Kept images: %s</p>'
                          % esc(', '.join(entry.get('images') or []) or 'none'))
        verdict = review.get('verdict')
        cards.append(
            '<section class="card %s"><h2>%s <span>%s</span></h2>'
            '<p class="state">%s</p>%s'
            '<p class="cmd">Correct: <code>python judgment_review.py --mark %s --ok</code><br>'
            'Wrong: <code>python judgment_review.py --mark %s --wrong --note "what the page says"'
            '</code></p></section>'
            % (esc(verdict or 'pending'), esc(case), esc(_money(entry.get('amount'))),
             esc('Checked %s on %s. %s' % (verdict.upper(), review.get('at'), review.get('note')
                                           or '') if verdict else 'Not checked yet.'),
             ''.join(blocks), esc(case), esc(case)))
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Judgment review</title><style>
body{font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;background:#fafaf7;color:#1c1c1a}
h1{font-size:20px;margin:0 0 4px}.status{margin:0 0 16px;max-width:70ch}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:0 0 16px}
.card.wrong{border-color:#b3261e}.card.ok{border-color:#2e7d32;opacity:.8}
h2{font-size:17px;margin:0}h2 span{font-weight:400;margin-left:8px}
.fig{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}
.img{flex:1 1 520px;max-width:100%}.img img{width:100%;border:1px solid #ccc}
.facts{flex:1 1 260px}.amt{font-size:22px;font-weight:600}
.q{font-family:ui-monospace,monospace;font-size:13px;background:#f3f3ee;padding:6px;margin:6px 0}
.why{color:#555;font-size:13px}.gap{color:#b3261e}.state{color:#555;margin:4px 0}
.cmd{font-size:13px}code{font-size:12px;background:#f3f3ee;padding:1px 4px;word-break:break-all}
</style></head><body>
<h1>Judgment figures to check against the page</h1>
<p class="status">__STATUS__</p>
<p class="status">For each case: find the figure on the page image. It is correct only if the
page prints that exact amount as the judgment total, and the line items shown are the ones on
the page. Any digit off is wrong.</p>
__CARDS__</body></html>"""
    # str.replace, not %: the stylesheet is full of literal percent signs.
    page = page.replace('__STATUS__', html.escape(s['line'])).replace(
        '__CARDS__', ''.join(cards) or '<p>No corroborated figures logged yet.</p>')
    path = os.path.join(out_dir, 'index.html')
    DS._atomic_write_text(path, page)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--mark', metavar='CASE', help='record a verdict on this case')
    verdict = parser.add_mutually_exclusive_group()
    verdict.add_argument('--ok', action='store_true', help='the page prints this exact total')
    verdict.add_argument('--wrong', action='store_true', help='the page does not')
    parser.add_argument('--note', help='what the page actually says, for a wrong figure')
    parser.add_argument('--status', action='store_true', help='print the count only')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    entries, target = load()
    if args.mark:
        if not (args.ok or args.wrong):
            parser.exit(2, '--mark needs --ok or --wrong\n')
        try:
            mark(entries, args.mark, 'ok' if args.ok else 'wrong', note=args.note)
        except KeyError:
            parser.exit(2, 'no logged figure for %s in %s\n' % (args.mark, target))
        save(entries, target)
    s = status(entries)
    print(s['line'])
    if args.status or args.mark:
        return 0
    out_dir = str(case_review.output_path(os.path.join(SHEET_DIR, 'index.html')).parent)
    print('sheet -> %s' % build_sheet(entries, out_dir))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
