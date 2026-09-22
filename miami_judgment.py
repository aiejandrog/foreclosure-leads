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
import json
import re
import sys

import document_collectors as DC
import document_store as DS
from document_queue import DocumentQueue

COUNTY = 'MIAMI-DADE'

# A recorded FINAL JUDGMENT indexes under a doc type starting with JUDGMENT (or LIS PENDENS /
# CERT OF TITLE for the neighbours we also want). Shortlisting only — see judgment_candidates.
RECORDED_JUDGMENT_RE = re.compile(r'^(JUDGMENT|FINAL JUDGMENT|JUDG)', re.I)

# Money as a US court writes it. The decimals are required: "$500" in a judgment is nearly always
# a fee or a cost, while the total carries cents. This is a CANDIDATE extractor, not a reader.
MONEY_RE = re.compile(r'\$\s?([0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})')
TOTAL_RE = re.compile(r'\b(total\s+(?:sum|amount|indebtedness)|amount\s+due|there\s+is\s+due|'
                      r'total\s+judgment|judgment\s+is\s+(?:hereby\s+)?entered)\b', re.I)


def judgment_amount_candidates(reading):
    """Dollar figures sitting on a line that also talks about a judgment total.

    Returns candidates with page and the verbatim line. NOTHING here is a finding: a scanned page
    contributes nothing, an OCR'd page contributes whatever OCR got, and two different totals on
    two pages means we do not know the number — see `agreed_amount`.
    """
    out = []
    for page in reading['pages']:
        if page['outcome'] != 'text':
            continue
        for line in (page.get('text') or '').splitlines():
            if not TOTAL_RE.search(line):
                continue
            for match in MONEY_RE.finditer(line):
                out.append({'amount': float(match.group(1).replace(',', '')),
                            'page': page['page'], 'passage': line.strip()[:300],
                            'source': 'document_text', 'verified': False})
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


def recorded_judgments(records):
    return [r for r in records if RECORDED_JUDGMENT_RE.match(str(r.get('doC_TYPE') or '').strip())]


def collect_recorded(case, records, collector=None, queue=None, county=COUNTY):
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
            job = queue.claim_ref(owner, county, case, ref, 'recorded_instrument')
            if job is None:
                # Already resolved on an earlier run (done, or a recorded gap). Resuming means
                # not fetching it again.
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
            reading = DS.read_pages(manifest['path'])
            DS.record_read(manifest['meta_path'], reading)
            row.update({'status': 'stored', 'sha256': manifest['sha256'],
                        'pages': manifest['pages'],
                        'pages_expected': manifest.get('pages_expected'),
                        'page_count_verified': manifest['page_count_verified'],
                        'page_count_note': manifest.get('page_count_note'),
                        'read_status': reading['read_status'],
                        'pages_unresolved': reading['pages_unresolved'],
                        'path': manifest['path'],
                        'amount_candidates': judgment_amount_candidates(reading)})
            if job:
                queue.complete(job['id'], owner, sha256=manifest['sha256'])
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


def run(case, records=None, collector=None, queue=None, county=COUNTY):
    inventory = enumerate_case(case, collector=collector)
    rows = collect_recorded(case, records or [], collector=collector, queue=queue, county=county)
    candidates = [c for row in rows for c in (row.get('amount_candidates') or [])]
    report = {
        'case': case, 'county': county,
        'docket_entries': len(inventory['entries']),
        # False, always: the OCS API publishes no cursor, so "we saw every entry" is unproven.
        'docket_pagination_verified': inventory['pagination_verified'],
        'judgment_candidates_by_keyword': [c['source_ref'] for c in inventory['judgment_candidates']],
        'documents': rows,
        'documents_stored': sum(1 for r in rows if r['status'] == 'stored'),
        'documents_page_verified': sum(1 for r in rows if r.get('page_count_verified')),
        'documents_fully_read': sum(1 for r in rows if r.get('read_status') == 'read'),
        'access_gaps': [{'source_ref': r['source_ref'], 'reason': r['reason']}
                        for r in rows if r['status'] in ('gap', 'rejected')],
        'judgment_amount_candidates': candidates,
        # None whenever the pages disagree, or nothing was readable. Never a best guess.
        'judgment_amount_agreed': agreed_amount(candidates),
        'judgment_amount_status': 'unverified_extraction',
    }
    # The stricter figure: only from a document that was page-verified AND fully read. This is the
    # one a caller may hand to records_liens.analyze; `judgment_amount_agreed` above is the looser
    # view for a human reading the report.
    report['judgment_amount_usable'] = judgment_for_analyze(report)
    if queue is not None:
        report['coverage'] = queue.coverage(county, case)
    return report


def judgment_for_analyze(report):
    """The value a caller MAY pass as `records_liens.analyze(..., judgment=...)`, or None.

    Only from a document that was page-verified against the recording index AND fully read AND
    whose pages agree on one total. Anything less returns None and the caller keeps the board's
    own figure — which is what happens today.
    """
    good = [r for r in report.get('documents', [])
            if r.get('page_count_verified') and r.get('read_status') == 'read']
    candidates = [c for r in good for c in (r.get('amount_candidates') or [])]
    return agreed_amount(candidates) if candidates else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('case', help='Miami-Dade case number, e.g. 2026-020206-CC-25')
    parser.add_argument('--records', help='JSON list of recordingModels rows to fetch')
    parser.add_argument('--judgments-only', action='store_true',
                        help='from --records, fetch only rows whose doC_TYPE starts with JUDGMENT')
    parser.add_argument('--dry-run', action='store_true', help='enumerate only; fetch nothing')
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
        if args.judgments_only:
            records = recorded_judgments(records)
    if not records:
        parser.exit(2, 'Nothing to fetch: pass --records, or --dry-run to enumerate only\n')

    queue = None if args.no_queue else DocumentQueue()
    try:
        report = run(args.case, records, queue=queue)
    finally:
        if queue:
            queue.close()

    print('%s: %d/%d stored, %d page-verified against the recording index, %d fully read'
          % (args.case, report['documents_stored'], len(report['documents']),
             report['documents_page_verified'], report['documents_fully_read']))
    for gap in report['access_gaps']:
        print('  GAP %s — %s' % (gap['source_ref'], gap['reason']))
    agreed = report['judgment_amount_agreed']
    shown = ('${:,.2f}'.format(agreed) if agreed is not None else 'not established')
    print('  judgment amount: %s (%s)' % (shown, report['judgment_amount_status']))
    if args.out:
        import case_review
        target = case_review.output_path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print('  report -> %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
