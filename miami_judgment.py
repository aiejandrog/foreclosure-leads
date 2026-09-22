"""miami_judgment — the Miami-Dade pilot, end to end: enumerate, fetch, VERIFY, store, read.

THE PILOT, AND WHY IT IS THE ACCEPTANCE TEST
The five-page recorded final judgment is the whole proof. A recorded instrument is the ONE
document class where an independent page count exists (`doC_PAGES` in the recording index), so it
is the only place we can demonstrate that what we downloaded is the whole document and not four
fifths of it. Everything else in this pipeline is built on that being provable.

WHAT THIS DOES NOT DO
It does not change any lead, does not write to the board, and does not touch equity_state. The
judgment figure it can produce is offered to `records_liens.analyze(models, folio, judgment)` as a
BETTER value for an argument that today comes from `r.get('judgment')` on the board row — and
nothing in the nightly calls this yet. Wiring it into the refresh is a separate, reviewable change.

Usage
    python miami_judgment.py 2026-020206-CC-25 --dry-run
        enumerate the docket and shortlist judgment-looking entries. No document is fetched.

    python miami_judgment.py 2026-020206-CC-25 --records or_rows.json --out pilot.json
        fetch every recorded instrument in or_rows.json (rows exactly as
        records_liens.records_by_qs returns them), verify each against the recording index, store
        it under DEALFLOW_DIR, read every page, and write the run manifest.

`--records` is a JSON list of `recordingModels` rows. Get them from an owner search
(records_liens.records_by_qs / mint_and_fetch) and keep the rows for the subject parcel.
"""
import argparse
import itertools
import json
import os
import re
import sys

import document_collectors as DC
import document_store as DS
from document_queue import DocumentQueue

COUNTY = 'MIAMI-DADE'

# A recorded final judgment USUALLY indexes under a doc type starting with JUDGMENT.
RECORDED_JUDGMENT_RE = re.compile(r'^(JUDGMENT|FINAL JUDGMENT|JUDG)', re.I)

# ...but not always, and the 2026-09-22 pilot is the counter-example: the five-page Garden Lake
# Towers final judgment is indexed "DADE COURT PAPER - DCP" with folio 0. A doc-type filter alone
# drops the one document the pilot exists to test, which is exactly what happened — the run
# printed "Nothing to fetch".
#
# So a court paper counts as a judgment candidate when one of its parties is a PLAINTIFF on the
# case. That is a real signal: a court paper recorded between the case's own parties is a filing
# from this case, not a namesake's. It is still a CANDIDATE — what the document is gets decided by
# reading it, never by how the clerk indexed it.
COURT_PAPER_RE = re.compile(r'\b(COURT PAPER|DCP|COURT DOCUMENT)\b', re.I)
_PARTY_NOISE_RE = re.compile(
    r'\b(INC|CORP|CO|LLC|L\.?L\.?C|LP|LLP|LTD|PA|PLLC|NA|N\.?A|THE|OF|AND|A|AN|ASSN|ASSOC'
    r'|ASSOCIATION|CONDOMINIUM|CONDO|HOMEOWNERS|OWNERS|TRUST|COMPANY)\b', re.I)


def _party_key(name):
    """Distinctive tokens of a party name, for comparing a docket party to an index party."""
    cleaned = _PARTY_NOISE_RE.sub(' ', str(name or '').upper())
    return {t for t in re.split(r'[^A-Z0-9]+', cleaned) if len(t) > 2}


def plaintiffs_of(raw_docket):
    """Plaintiff names off the OCS parties list. Empty when the docket names none."""
    out = []
    for party in (raw_docket.get('parties') or []):
        kind = str(party.get('partyTypeDesc') or party.get('partyType') or '').upper()
        if 'PLAINTIFF' in kind:
            name = party.get('partyName')
            if name:
                out.append(str(name))
    return out


def _is_case_court_paper(row, plaintiff_keys):
    if not plaintiff_keys or not COURT_PAPER_RE.search(str(row.get('doC_TYPE') or '')):
        return False
    # Either side: the clerk indexes a court paper in whichever order it was presented, so
    # requiring the plaintiff to be the second party alone would miss half of them.
    for field in ('seconD_PARTY', 'firsT_PARTY'):
        key = _party_key(row.get(field))
        if key and any(key & pk for pk in plaintiff_keys):
            return True
    return False

# Money as a US court writes it. The decimals are required: "$500" in a judgment is nearly always
# a fee or a cost, while the total carries cents. This is a CANDIDATE extractor, not a reader.
MONEY_RE = re.compile(r'\$\s?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})')
# The labels a judgment's total actually carries. "GRAND TOTAL:" is on the pilot document and was
# missing from the first version of this list, which is half of why a correctly-OCR'd $14,698.60
# came out "not established"; the other half was that its value sat fifteen lines below it.
TOTAL_RE = re.compile(r'\b(total\s+(?:sum|amount|indebtedness|due)|grand\s+total|amount\s+due|'
                      r'there\s+is\s+due|total\s+judgment|total\s*:|'
                      r'judgment\s+is\s+(?:hereby\s+)?entered)', re.I)


# WHY A LABEL AND ITS VALUE ARE NOT ON THE SAME LINE
#
# The 2026-09-22 OCR run is the reason this module does column pairing at all. Windows OCR read
# the judgment's cost table COLUMN BY COLUMN: every label first, then every figure. "GRAND TOTAL:"
# landed on one line and its "$ 14,698.60" about fifteen lines further down. A same-line match
# found nothing, so a correctly-read $14,698.60 came out "not established" — and every judgment
# laid out as a table, which is most of them, would do the same.
# A line that is nothing but a money figure: an entry in a value column.
_ONLY_MONEY_RE = re.compile(r'^[\s|:.]*\$?\s?([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})[\s|.]*$')


def _money_on(line):
    return [float(m.group(1).replace(',', '')) for m in MONEY_RE.finditer(line)]


def _column_pairs(lines):
    """Pair a block of labels with the block of figures below it. -> {label line index: value}

    OCR does not read a table row by row; it reads the label column, then the value column. So
    "GRAND TOTAL:" and its "$ 14,698.60" are not near each other, and taking the first figure
    after a label gives ASSESSMENTS' figure to the grand total — which is worse than finding
    nothing, because it is confidently wrong.

    The columns are aligned FROM THE BOTTOM: the last label goes with the last figure. A judgment's
    cost table ends with its total, and OCR dropping a line item (it dropped two on the pilot page)
    shortens the top of a column, not the bottom.
    """
    pairs = {}
    index, total_lines = 0, len(lines)
    while index < total_lines:
        if not _ONLY_MONEY_RE.match(lines[index]):
            index += 1
            continue
        start = index
        values = []
        while index < total_lines:
            match = _ONLY_MONEY_RE.match(lines[index])
            if match:
                values.append(float(match.group(1).replace(',', '')))
            elif lines[index].strip():
                break
            index += 1
        labels = []
        back = start - 1
        while back >= 0 and len(labels) < len(values):
            line = lines[back]
            if line.strip() and not _ONLY_MONEY_RE.match(line):
                labels.append(back)
            elif line.strip():
                break
            back -= 1
        labels.reverse()
        for offset in range(1, min(len(labels), len(values)) + 1):
            pairs[labels[-offset]] = values[-offset]
    return pairs


def page_money(text):
    """Every money figure on a page, in reading order. The raw material for the sum check."""
    return [value for line in (text or '').splitlines() for value in _money_on(line)]


def sum_check(text, total, tolerance=0.011, max_terms=6):
    """Do some of this page's other figures add up to `total`?

    This is the guard the pilot argued for. OCR read that judgment's grand total correctly and
    misread one subtotal in the same table — 6,796.61 as 5,796.61 — and dropped two line items,
    silently, with no error anywhere. So one OCR'd figure cannot be trusted on its own. A total
    the page's own line items reproduce is a different kind of claim: it is two readings of the
    same arithmetic agreeing, and a single misread digit breaks it. On the pilot page this check
    FAILS, which is the correct answer.

    Combinations of 2 to `max_terms` figures, which is the shape a judgment's cost table actually
    has (assessments + costs + collection fees + attorney fee = grand total). Not a subset-sum
    solver: a search wide enough to hit any total by chance would corroborate anything.

    Returns {'ok', 'components', 'reason'}. `ok` False never means "the total is wrong" — it means
    "this page does not corroborate it", which is all that can honestly be said.
    """
    values = [v for v in page_money(text) if v > 0]
    others = list(values)
    if total in others:
        others.remove(total)
    others = [v for v in others if v < total][:24]
    if not others:
        return {'ok': False, 'components': [], 'reason': 'no other figures on this page to add up'}
    for size in range(2, max_terms + 1):
        if size > len(others):
            break
        for combo in itertools.combinations(others, size):
            if abs(sum(combo) - total) <= tolerance:
                return {'ok': True, 'components': sorted(combo), 'reason': 'line items sum to it'}
    return {'ok': False, 'components': [],
            'reason': 'no 2-%d of this page\'s %d other figures sum to it'
                      % (max_terms, len(others))}


def judgment_amount_candidates(reading):
    """Dollar figures this document offers as a judgment total, each with its page and passage.

    Two ways a figure reaches a label, and the candidate says which:

      same_line          "$14,698.60" on the line that says GRAND TOTAL. What a text layer gives.
      column_lookahead   the label on one line and the first value-only line within
                         TOTAL_LOOKAHEAD below it. What OCR gives, because it reads tables by
                         column. Weaker, and marked weaker.

    NOTHING here is a finding. A page that was not read contributes nothing, and two pages
    offering two different totals means we do not know the number — see `agreed_amount`.
    """
    out = []
    for page in reading['pages']:
        # 'text' and 'ocr_text' only. A page that is still `needs_ocr` contributes NOTHING —
        # its `text` is empty by construction, and the stamp that used to sit there is parked in
        # `embedded_text` precisely so a dollar figure inside a watermark can never be read as a
        # judgment total. That is the 2026-09-22 pilot regression.
        if page['outcome'] not in ('text', 'ocr_text'):
            continue
        body = page.get('text') or ''
        lines = body.splitlines()
        pairs = _column_pairs(lines)
        for index, line in enumerate(lines):
            if not TOTAL_RE.search(line):
                continue
            found = [(value, 'same_line', line) for value in _money_on(line)]
            if not found and index in pairs:
                found = [(pairs[index], 'column_pairing',
                          line.strip() + '  ->  ' + '${:,.2f}'.format(pairs[index]))]
            for value, how, passage in found:
                candidate = {'amount': value, 'page': page['page'],
                             'passage': passage.strip()[:300],
                             'source': 'document_text', 'match': how,
                             'text_source': page.get('text_source'), 'verified': False}
                check = sum_check(body, value)
                candidate['sum_check'] = check['ok']
                candidate['sum_check_reason'] = check['reason']
                candidate['sum_check_components'] = check['components']
                out.append(candidate)
    return out


def agreed_amount(candidates):
    """One number, or None. Two different totals is not a number we may use — it is a conflict.

    Deliberately strict: `records_liens.analyze` uses the judgment to pick WHICH open mortgage is
    the foreclosing first. A wrong figure there picks the wrong mortgage and the equity number
    comes out wrong in a way nothing downstream can see.
    """
    values = {c['amount'] for c in candidates}
    return values.pop() if len(values) == 1 else None


def enumerate_case(case, collector=None):
    collector = collector or DC.MiamiCollector()
    inventory = collector.enumerate_documents(case)
    inventory['judgment_candidates'] = DC.judgment_candidates(inventory)
    return inventory


def recorded_judgments(records, plaintiffs=()):
    """Rows worth fetching as judgment candidates: a JUDGMENT doc type, OR a court paper recorded
    between this case's own parties. Pass `plaintiffs` from plaintiffs_of(the docket)."""
    keys = [k for k in (_party_key(p) for p in plaintiffs) if k]
    return [r for r in records
            if RECORDED_JUDGMENT_RE.match(str(r.get('doC_TYPE') or '').strip())
            or _is_case_court_paper(r, keys)]


# The recording stamp the clerk prints on every page of a recorded instrument, top right:
#   CFN 20260305080 BOOK 35287 PAGE 4642
# It is a free, independent check that the pages we read are the instrument we asked for — the
# document vouching for its own identity, against the index we fetched it by.
_STAMP_RE = re.compile(r'BOOK\s*[:#]?\s*(\d{4,6})\s*[,;]?\s*PAGE\s*[:#]?\s*(\d{1,5})', re.I)


def stamp_identity(reading, record):
    """Does the recording stamp on the page agree with the book/page we fetched?

    Returns None when no stamp was legible — which is NOT a mismatch and must never be reported as
    one. OCR misses stamps routinely; absence of the check is absence of the check.
    """
    want = (str(record.get('reC_BOOK') or '').strip(), str(record.get('reC_PAGE') or '').strip())
    if not want[0] or not want[1]:
        return None
    seen = []
    for page in reading['pages']:
        for match in _STAMP_RE.finditer(page.get('text') or ''):
            seen.append({'book': match.group(1), 'page_no': match.group(2),
                         'on_page': page['page']})
    if not seen:
        return None
    agree = [s for s in seen if (s['book'], s['page_no']) == (want[0].lstrip('0'),
                                                              want[1].lstrip('0'))
             or (s['book'], s['page_no']) == want]
    return {'index_book': want[0], 'index_page': want[1], 'stamps_found': seen[:10],
            'agrees': bool(agree)}


def collect_recorded(case, records, collector=None, queue=None, county=COUNTY, ocr=None,
                     keep_images=False, gray_cutoff=DS.WATERMARK_GRAY_CUTOFF):
    """Fetch, verify, store and read each recorded instrument. One result row per record."""
    collector = collector or DC.MiamiCollector()
    results = []
    for index, record in enumerate(records):
        ref = 'official_records/%s-%s' % (record.get('reC_BOOK'), record.get('reC_PAGE'))
        owner = 'miami_judgment/%d' % index
        row = {'source_ref': ref, 'doc_type': record.get('doC_TYPE')}
        job = None
        if queue is not None:
            queue.add(county, case, ref, 'recorded_instrument', {'doc_type': record.get('doC_TYPE')})
            # Claim THIS ref, not "any ready job" — claiming whatever came next would fetch one
            # record's bytes under another record's lease, and a resumed run would re-download
            # everything because no claim ever matched what the loop was holding.
            #
            # reader_version and can_ocr are what let a job that an OLDER reader marked done be
            # taken again. On 2026-09-22 the pilot's judgment had been fetched by the reader that
            # counted a watermark as a read page; the fixed reader then skipped it, because done
            # meant done. A verdict from a reader we have since fixed is not one to keep.
            job = queue.claim_ref(owner, county, case, ref, 'recorded_instrument',
                                  reader_version=DS.READER_VERSION, can_ocr=ocr is not None)
            if job is None:
                # Genuinely finished, or held by another worker. Resuming means not re-fetching.
                prior = [j for j in queue.jobs(county, case)
                         if j['source_ref'] == ref and j['kind'] == 'recorded_instrument']
                state = prior[0]['status'] if prior else 'leased'
                row.update({'status': 'skipped', 'reason': 'already %s on an earlier run' % state,
                            'prior_status': state, 'sha256': prior[0]['sha256'] if prior else None})
                results.append(row)
                continue
        try:
            retrieved = collector.retrieve_document(record)
            manifest = DS.store(county, case, retrieved, source_ref=ref,
                                doc_name=str(record.get('doC_TYPE') or ''))
            images_dir = None
            if keep_images:
                # Beside the PDF, inside the same guarded case folder — never a new path.
                images_dir = os.path.join(os.path.dirname(manifest['path']),
                                          manifest['document_key'][:16] + '-pages')
            reading = DS.read_pages(manifest['path'], ocr=ocr, keep_images_in=images_dir,
                                    gray_cutoff=gray_cutoff)
            DS.record_read(manifest['meta_path'], reading)
            # The text is evidence and it cost an OCR pass; it gets written down. The pilot run
            # read five pages and kept none of the words.
            text_dir = DS.save_page_text(manifest, reading)
            row.update({'status': 'stored', 'sha256': manifest['source_sha256'],
                        'document_key': manifest['document_key'],
                        'pages': manifest['pages'],
                        'pages_expected': manifest.get('pages_expected'),
                        'page_count_verified': manifest['page_count_verified'],
                        'page_count_note': manifest.get('page_count_note'),
                        'read_status': reading['read_status'],
                        'reader_version': reading['reader_version'],
                        'pages_unresolved': reading['pages_unresolved'],
                        'pages_from_ocr': reading.get('pages_from_ocr', 0),
                        'ocr_attempted': reading.get('ocr_attempted', False),
                        'gray_cutoff': reading.get('gray_cutoff'),
                        'weak_pages': [{'page': p['page'], 'reason': p.get('weak_reason'),
                                        'outcome': p.get('outcome'),
                                        'ocr_error': p.get('ocr_error')}
                                       for p in reading['pages'] if p.get('weak_reason')],
                        'path': manifest['path'], 'text_dir': text_dir,
                        'images_dir': images_dir,
                        'recording_stamp': stamp_identity(reading, record),
                        'amount_candidates': judgment_amount_candidates(reading),
                        # Kept for case_dossier.classify_documents; stripped before the report is
                        # written, because full page text does not belong in a summary file.
                        'reading': reading})
            if job:
                queue.complete(job['id'], owner, sha256=manifest['source_sha256'],
                               reader_version=reading['reader_version'],
                               read_status=reading['read_status'])
        except DC.AccessGap as gap:
            row.update({'status': 'gap', 'reason': str(gap)})
            if job:
                queue.gap(job['id'], owner, gap)
        except DS.DocumentRejected as bad:
            # A rejected document is a coverage gap too: we HAVE bytes and cannot trust them.
            row.update({'status': 'rejected', 'reason': str(bad)})
            if job:
                queue.gap(job['id'], owner, bad)
        results.append(row)
    return results


def run(case, records=None, collector=None, queue=None, county=COUNTY, ocr=None,
        judgments_only=False, keep_images=False, gray_cutoff=DS.WATERMARK_GRAY_CUTOFF):
    inventory = enumerate_case(case, collector=collector)
    records = list(records or [])
    # Filter HERE, not in main(), because the filter needs the case's plaintiffs and the docket we
    # just pulled is where they live.
    plaintiffs = plaintiffs_of(inventory['raw'])
    if judgments_only:
        records = recorded_judgments(records, plaintiffs)
    rows = collect_recorded(case, records, collector=collector, queue=queue, county=county,
                            ocr=ocr, keep_images=keep_images, gray_cutoff=gray_cutoff)
    candidates = [c for row in rows for c in (row.get('amount_candidates') or [])]
    report = {
        'case': case, 'county': county,
        'plaintiffs': plaintiffs,
        'records_selected': len(records),
        'docket_entries': len(inventory['entries']),
        # False, always: the OCS API publishes no cursor, so "we saw every entry" is unproven.
        'docket_pagination_verified': inventory['pagination_verified'],
        'judgment_candidates_by_keyword': [c['source_ref'] for c in inventory['judgment_candidates']],
        'documents': rows,
        'documents_stored': sum(1 for r in rows if r['status'] == 'stored'),
        'documents_page_verified': sum(1 for r in rows if r.get('page_count_verified')),
        'documents_fully_read': sum(1 for r in rows if r.get('read_status') == 'read'),
        'documents_image_only': sum(1 for r in rows if r.get('read_status') == 'image_only'),
        'access_gaps': [{'source_ref': r['source_ref'], 'reason': r['reason']}
                        for r in rows if r['status'] in ('gap', 'rejected')],
        'judgment_amount_candidates': candidates,
        # None whenever the pages disagree, or nothing was readable. Never a best guess.
        'judgment_amount_agreed': agreed_amount(candidates),
        'judgment_amount_status': 'unverified_extraction',
        # A total the page's own line items reproduce. This is the evidence that would justify
        # trusting an OCR'd figure one day; on the pilot page it is False, because OCR misread a
        # subtotal the watermark crossed.
        'judgment_amount_corroborated': any(c.get('sum_check') for c in candidates),
    }
    # The stricter figure: only from a document that was page-verified AND fully read. This is the
    # one a caller may hand to records_liens.analyze; `judgment_amount_agreed` above is the looser
    # view for a human reading the report.
    report['judgment_amount_usable'] = judgment_for_analyze(report)
    report['judgment_amount_usable_with_ocr'] = judgment_for_analyze(report, allow_ocr=True)
    if queue is not None:
        report['coverage'] = queue.coverage(county, case)
    # For case_dossier section a. Carries the full docket entries, so strip_readings drops it
    # before anything is written to disk.
    report['_inventory'] = inventory
    return report


def strip_readings(report):
    """Drop the per-page text from a report before it is written to disk. The text lives in the
    stored PDF; duplicating it into every summary bloats the file and scatters homeowner data."""
    for row in report.get('documents', []):
        row.pop('reading', None)
    report.pop('_inventory', None)
    return report


def judgment_for_analyze(report, allow_ocr=False):
    """The value a caller MAY pass as `records_liens.analyze(..., judgment=...)`, or None.

    Only from a document that was page-verified against the recording index AND fully read AND
    whose pages agree on one total. Anything less returns None and the caller keeps the board's
    own figure — which is what happens today.

    OCR-sourced figures are EXCLUDED by default. `analyze` uses this number to pick which open
    mortgage is the foreclosing first, by closest amount; one misread digit picks a different
    mortgage and the equity number comes out wrong in a way nothing downstream can see. Every
    Miami judgment seen so far is a scan, so in practice this returns None until a human has
    checked the figure against the page image — which is why the rendered image is kept. Pass
    allow_ocr=True to accept OCR, deliberately and with that in view.

    The refusal was put to Alejandro on 2026-09-22 and he confirmed it: it is a reviewed decision,
    not an unexamined default. Revisit it when OCR accuracy has been measured against kept page
    images on a run of these, not before.
    """
    good = [r for r in report.get('documents', [])
            if r.get('page_count_verified') and r.get('read_status') == 'read']
    candidates = [c for r in good for c in (r.get('amount_candidates') or [])]
    if allow_ocr:
        # The escape hatch gets a guard. An OCR'd figure is admissible only when the page's own
        # line items add up to it: the pilot showed OCR reading a grand total correctly while
        # misreading a subtotal the watermark crossed, with nothing anywhere to say so. Two
        # readings of the same arithmetic agreeing is evidence; one figure is not.
        candidates = [c for c in candidates
                      if c.get('text_source') != 'ocr' or c.get('sum_check')]
    else:
        candidates = [c for c in candidates if c.get('text_source') != 'ocr']
    return agreed_amount(candidates) if candidates else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('case', help='Miami-Dade case number, e.g. 2026-020206-CC-25')
    parser.add_argument('--records', help='JSON list of recordingModels rows to fetch')
    parser.add_argument('--judgments-only', action='store_true',
                        help='from --records, keep JUDGMENT doc types plus court papers recorded '
                             'between this case\'s own parties (the pilot judgment is indexed DCP)')
    parser.add_argument('--dry-run', action='store_true', help='enumerate only; fetch nothing')
    parser.add_argument('--no-ocr', action='store_true',
                        help='do not OCR scanned pages (Windows only; on by default)')
    parser.add_argument('--keep-images', action='store_true',
                        help='keep the 300-DPI render of each OCR page (cleaned and raw), to '
                             'check the text by eye')
    parser.add_argument('--gray-cutoff', type=int, default=DS.WATERMARK_GRAY_CUTOFF,
                        help='whiten pixels at or above this grey level before OCR, to strip the '
                             'clerk watermark that crosses the amounts column (0 disables it)')
    parser.add_argument('--no-queue', action='store_true', help='skip the resumable queue')
    parser.add_argument('--out', help='write the run report to this JSON file (under DEALFLOW_DIR)')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    if args.dry_run:
        inventory = enumerate_case(args.case)
        print('%s: %d docket entries, pagination_verified=%s'
              % (args.case, len(inventory['entries']), inventory['pagination_verified']))
        for item in inventory['judgment_candidates']:
            desc = (item['metadata'].get('docketDescrition') or '').strip()
            print('  CANDIDATE (keyword, not read) %s  %s' % (item['source_ref'], desc[:90]))
        return 0

    records = []
    if args.records:
        with open(args.records, encoding='utf-8-sig') as fh:
            records = json.load(fh)
        if not isinstance(records, list):
            parser.exit(2, '--records must be a JSON list of recordingModels rows\n')
    if not records:
        parser.exit(2, 'Nothing to fetch: pass --records, or --dry-run to enumerate only\n')

    # OCR is ON by default: every Miami judgment seen so far is a scan, so without it the pilot
    # stops at image_only. It degrades to a recorded per-page reason off Windows, never silence.
    ocr = None if args.no_ocr else DS.winocr
    queue = None if args.no_queue else DocumentQueue()
    try:
        report = run(args.case, records, queue=queue, ocr=ocr,
                     judgments_only=args.judgments_only, keep_images=args.keep_images,
                     gray_cutoff=args.gray_cutoff)
    finally:
        if queue:
            queue.close()
    if args.judgments_only and not report['records_selected']:
        print('%s: --judgments-only matched none of the %d rows. Plaintiffs on this case: %s'
              % (args.case, len(records), ', '.join(report['plaintiffs']) or '(none listed)'))
        return 2

    print('%s: %d/%d stored, %d page-verified against the recording index, %d fully read'
          % (args.case, report['documents_stored'], len(report['documents']),
             report['documents_page_verified'], report['documents_fully_read']))
    for row in report['documents']:
        # A weak text layer is not a failed page. Every one of these lines used to read
        # "NOT READ", including on the five pages OCR had just read end to end, which made a
        # successful run look like a broken one. The verdict on the TEXT LAYER and the outcome
        # for the PAGE are two different facts and now print as two different words.
        for weak in (row.get('weak_pages') or []):
            if weak.get('outcome') == 'ocr_text':
                print('  READ BY OCR  %s page %s — no usable text layer (%s)'
                      % (row['source_ref'], weak['page'], weak['reason']))
            else:
                print('  NOT READ     %s page %s — %s%s'
                      % (row['source_ref'], weak['page'], weak['reason'],
                         ('; ' + weak['ocr_error']) if weak.get('ocr_error') else ''))
        stamp = row.get('recording_stamp')
        if stamp is not None:
            print('  recording stamp on the page %s the index (book %s page %s)'
                  % ('AGREES with' if stamp['agrees'] else 'DISAGREES with',
                     stamp['index_book'], stamp['index_page']))
        if row.get('text_dir'):
            print('  page text -> %s' % row['text_dir'])
    for gap in report['access_gaps']:
        print('  GAP %s — %s' % (gap['source_ref'], gap['reason']))
    for candidate in report['judgment_amount_candidates']:
        print('  candidate ${:,.2f} (page {}, {}, {}) — line items {}: {}'.format(
            candidate['amount'], candidate['page'], candidate.get('text_source') or 'embedded',
            candidate.get('match'), 'CHECK OUT' if candidate.get('sum_check') else 'do not add up',
            candidate.get('sum_check_reason')))
    agreed = report['judgment_amount_agreed']
    shown = ('${:,.2f}'.format(agreed) if agreed is not None else 'not established')
    print('  judgment amount: %s (%s)' % (shown, report['judgment_amount_status']))
    usable = report['judgment_amount_usable']
    if usable is None and report['judgment_amount_usable_with_ocr'] is not None:
        if report['judgment_amount_corroborated']:
            print('  the only figure came from OCR, but the page\'s line items sum to it — '
                  'check it against the page image, then it is worth quoting')
        else:
            print('  the only figure came from OCR and the page\'s line items do NOT sum to it — '
                  'treat it as unread until a human has compared it with the page image')
    strip_readings(report)
    if args.out:
        import case_review
        target = case_review.output_path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('  report -> %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
