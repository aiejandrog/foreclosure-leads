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
from datetime import datetime, timezone
import re
import sys

import document_collectors as DC
import document_store as DS
import document_vision as DV
import document_interpreter as DI
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
# Windows OCR reads a cost table COLUMN BY COLUMN: every label first, then every figure. On the
# pilot judgment "GRAND TOTAL:" landed on one line and its "$ 14,698.60" about fifteen lines
# further down, so a same-line match found nothing and a correctly-read total came out "not
# established".
#
# AND WHY PAIRING THEM BY POSITION IS DANGEROUS
# The first attempt paired the two columns positionally. On the 2026-09-22 rerun OCR produced
# 14 labels and only 12 figures — it had lost 31.19 and 2,010.74 where the watermark crosses them
# — so every pair after the first gap was off by one and "GRAND TOTAL:" was handed $150.00, the
# Collection Process Fee. A confidently wrong number is worse than none.
#
# So pairing is now STRICT: the two columns pair only when they are the same length. When they are
# not, the one thing still safe to say is that a table's total is its LAST figure, so a total
# label that is the last label takes the last figure and is marked `tail_figure` — weaker, and
# only admissible if the arithmetic below corroborates it.

# A line that is nothing but a money figure: an entry in a value column.
_ONLY_MONEY_RE = re.compile(r'^[\s|:.]*\$?\s?([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})[\s|.]*$')


def _money_on(line):
    return [float(m.group(1).replace(',', '')) for m in MONEY_RE.finditer(line)]


def page_money(text):
    """Every money figure on a page, in reading order."""
    return [value for line in (text or '').splitlines() for value in _money_on(line)]


def document_money(reading):
    """Every money figure in the whole document.

    The sum check was looking at one page and reporting "no other figures on this page to add up"
    while the subtotals it needed sat on page 1 and the rest on page 2. A judgment's cost table
    runs across a page break as often as not, so the pool is the document.
    """
    return [value for page in reading['pages']
            if page['outcome'] in ('text', 'ocr_text')
            for value in page_money(page.get('text'))]


def _column_blocks(lines):
    """Each run of value-only lines, with the label lines standing directly above it.

    -> [(label_line_indexes, [values])]
    """
    blocks = []
    index, count = 0, len(lines)
    while index < count:
        if not _ONLY_MONEY_RE.match(lines[index]):
            index += 1
            continue
        start = index
        values = []
        while index < count:
            match = _ONLY_MONEY_RE.match(lines[index])
            if match:
                values.append(float(match.group(1).replace(',', '')))
            elif lines[index].strip():
                break
            index += 1
        labels = []
        back = start - 1
        while back >= 0:
            line = lines[back]
            if _ONLY_MONEY_RE.match(line):
                break
            if line.strip():
                labels.append(back)
            back -= 1
        labels.reverse()
        blocks.append((labels, values))
    return blocks


def column_values(lines):
    """-> ({label line index: (value, how)}, [notes]) for the label/value columns on a page."""
    pairs, notes = {}, []
    for labels, values in _column_blocks(lines):
        if not labels or not values:
            continue
        if len(labels) == len(values):
            for label, value in zip(labels, values):
                pairs[label] = (value, 'column_pairing')
            continue
        notes.append('%d labels against %d figures: columns do not line up, so they were not '
                     'paired (OCR drops a figure where the watermark crosses it)'
                     % (len(labels), len(values)))
        # The one thing still safe: a cost table ends with its total.
        pairs[labels[-1]] = (values[-1], 'tail_figure')
    return pairs, notes


def sum_check(pool, total, tolerance=0.011, max_terms=6):
    """Do some of the document's other figures add up to `total`?

    This is the guard the pilot argued for. OCR read that judgment's grand total correctly and
    misread one subtotal in the same table — 6,796.61 as 5,796.61 — and dropped two line items,
    silently, with no error anywhere. So one OCR'd figure cannot be trusted on its own. A total
    the document's own line items reproduce is two readings of the same arithmetic agreeing, and
    a single misread digit breaks it.

    Combinations of 2 to `max_terms` figures, which is the shape a judgment's cost table has
    (assessments + costs + collection fees + attorney fee = grand total). Not a subset-sum solver:
    a search wide enough to hit any total by chance would corroborate anything.

    Returns {'ok', 'components', 'reason'}. `ok` False never means "the total is wrong" — it means
    "the document does not corroborate it", which is all that can honestly be said.
    """
    others = [v for v in pool if 0 < v < total]
    seen, unique = set(), []
    for value in others:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    unique = unique[:24]
    if not unique:
        return {'ok': False, 'components': [],
                'reason': 'no other figures in this document to add up'}
    for size in range(2, max_terms + 1):
        if size > len(unique):
            break
        for combo in itertools.combinations(unique, size):
            if abs(sum(combo) - total) <= tolerance:
                return {'ok': True, 'components': sorted(combo), 'reason': 'line items sum to it'}
    return {'ok': False, 'components': [],
            'reason': 'no 2-%d of the document\'s %d other figures sum to it'
                      % (max_terms, len(unique))}


def admissible(candidate):
    """May this figure be shown as THE judgment amount?

    A figure read off a text layer stands on its own. A figure read off a SCAN does not, whether
    OCR or the vision reader produced it: the 2026-09-22 runs produced two different wrong totals,
    $150.00 and a misread subtotal, both looking exactly like a right one, and a model transcribing
    an image can be wrong with the same confidence. So a figure off a scan is admissible only when
    the document's own line items reproduce it.
    """
    return candidate.get('text_source') in (None, 'embedded') or bool(candidate.get('sum_check'))


def judgment_amount_candidates(reading):
    """Dollar figures this document offers as a judgment total, each with its page and passage.

    Three ways a figure reaches a label, and the candidate says which:

      same_line        "$14,698.60" on the line that says GRAND TOTAL. What a text layer gives.
      column_pairing   label column and figure column, same length, paired by position.
      tail_figure      the columns did NOT line up, so only the last label and last figure were
                       matched. Weak on purpose.

    NOTHING here is a finding, and nothing here is filtered. A candidate the arithmetic does not
    corroborate stays in the list with `sum_check: false` so it is visible; `admissible` is what
    decides whether it may be spoken as the amount.
    """
    out = []
    pool = document_money(reading)
    for page in reading['pages']:
        # 'text' and 'ocr_text' only. A page that is still `needs_ocr` contributes NOTHING —
        # its `text` is empty by construction, and the stamp that used to sit there is parked in
        # `embedded_text` precisely so a dollar figure inside a watermark can never be read as a
        # judgment total. That is the 2026-09-22 pilot regression.
        if page['outcome'] not in ('text', 'ocr_text'):
            continue
        lines = (page.get('text') or '').splitlines()
        pairs, notes = column_values(lines)
        for index, line in enumerate(lines):
            if not TOTAL_RE.search(line):
                continue
            found = [(value, 'same_line', line) for value in _money_on(line)]
            if not found and index in pairs:
                value, how = pairs[index]
                found = [(value, how, line.strip() + '  ->  ' + '${:,.2f}'.format(value))]
            for value, how, passage in found:
                check = sum_check(pool, value)
                out.append({'amount': value, 'page': page['page'],
                            'passage': passage.strip()[:300],
                            'source': 'document_text', 'match': how,
                            'text_source': page.get('text_source'), 'verified': False,
                            'sum_check': check['ok'],
                            'sum_check_reason': check['reason'],
                            'sum_check_components': check['components'],
                            'column_notes': notes if how == 'tail_figure' else []})
    return out + composed_candidates(reading, out)


# A judgment that states its parts and never states their sum. The 2026-09-22 read of
# 2026-058556-SP-26 said, in one sentence, "the principal sum of $3,941.07, court costs in the
# amount of $379.85" and nothing else — no total line, so TOTAL_RE matched nothing and the
# extractor recorded NEITHER figure. The document was read, the money was on the page, and the
# dossier said `amounts: []`.
_COMPONENT_RES = (
    ('principal', re.compile(r'principal\s+(?:sum|balance|amount)?[^$\n]{0,30}?' + MONEY_RE.pattern,
                             re.I)),
    ('costs', re.compile(r'(?:court\s+)?costs?\b[^$\n]{0,40}?' + MONEY_RE.pattern, re.I)),
    ('interest', re.compile(r'interest\b[^$\n]{0,40}?' + MONEY_RE.pattern, re.I)),
    ('attorney_fees', re.compile(r"attorney'?s?\s+fees?\b[^$\n]{0,40}?" + MONEY_RE.pattern, re.I)),
)


def composed_candidates(reading, already):
    """principal + costs + interest + fees, when the document states no total of its own.

    THE SUM CHECK CANNOT APPLY HERE, and that is the whole point of keeping it separate. Every
    other candidate is a total the document PRINTED, checked against the parts it printed — two
    independent statements, one corroborating the other. This figure is arithmetic we did
    ourselves on parts nobody totalled, so there is no second statement to check it against. It
    carries `composed: True`, `sum_check: False` and its components, it is never admissible, and
    `judgment_for_analyze` drops it with every other scan-read figure. It exists so a reader can
    see the money that is on the page instead of an empty list.
    """
    if already:
        return []
    parts, pages = {}, set()
    for page in reading['pages']:
        if page['outcome'] not in ('text', 'ocr_text'):
            continue
        for name, pattern in _COMPONENT_RES:
            if name in parts:
                continue
            match = pattern.search(page.get('text') or '')
            if not match:
                continue
            try:
                parts[name] = {'amount': float(match.group(1).replace(',', '')),
                               'page': page['page'],
                               'passage': match.group(0).strip()[:160],
                               'text_source': page.get('text_source')}
            except ValueError:
                continue
            pages.add(page['page'])
    # A principal alone is not a judgment total, it is one line of one. Two named parts is the
    # floor, otherwise this invents a total out of the only number on the page.
    if 'principal' not in parts or len(parts) < 2:
        return []
    total = round(sum(p['amount'] for p in parts.values()), 2)
    order = [name for name, _ in _COMPONENT_RES if name in parts]
    return [{'amount': total, 'page': min(pages),
             'passage': ' + '.join('%s ${:,.2f}'.format(parts[n]['amount']) % n for n in order),
             'source': 'document_text', 'match': 'principal_plus_components',
             'text_source': parts['principal']['text_source'], 'verified': False,
             'composed': True, 'sum_check': False,
             'sum_check_reason': ('the document states no total of its own, so this is our '
                                  'arithmetic on its named parts and nothing in the document '
                                  'corroborates it'),
             'sum_check_components': [parts[n]['amount'] for n in order],
             'component_passages': [dict(parts[n], part=n) for n in order],
             'column_notes': []}]


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


# A satisfaction or release the PLAINTIFF signed. Measured 2026-09-22 on Palm Beach
# 50-2026-CA-000685: a $993,885.33 judgment was paid and satisfied two months later, and a stage
# that fetches only the judgment reports that paid debt as owed. The nightly runs judgments_only,
# so without this the satisfaction is never even fetched. Party-matched to the plaintiff on
# purpose: an owner's decades of unrelated mortgage releases stay out, and so does their cost.
SATISFACTION_TYPE_RE = re.compile(r'^(SATISF|REL(EASE)?\b|PARTIAL\s+REL)', re.I)


def _signed_by_plaintiff(row, plaintiff_keys):
    if not plaintiff_keys or not SATISFACTION_TYPE_RE.match(str(row.get('doC_TYPE') or '').strip()):
        return False
    for field in ('firsT_PARTY', 'seconD_PARTY'):
        key = _party_key(row.get(field))
        if key and any(key & pk for pk in plaintiff_keys):
            return True
    return False


def is_satisfaction_row(row):
    """A fetched satisfaction/release. Its figures RECITE the debt it discharges; they are never a
    candidate judgment amount and never worth paying the second reader for."""
    kind = str((row.get('classification') or {}).get('kind') or '')
    return (kind.startswith('satisfaction')
            or bool(SATISFACTION_TYPE_RE.match(str(row.get('doc_type') or '').strip())))


def recorded_judgments(records, plaintiffs=()):
    """Rows worth fetching as judgment evidence: a JUDGMENT doc type, a court paper recorded
    between this case's own parties, or a satisfaction/release the plaintiff is a party to (the
    document that says the judgment or the foreclosed loan was paid). Pass `plaintiffs` from
    plaintiffs_of(the docket)."""
    keys = [k for k in (_party_key(p) for p in plaintiffs) if k]
    return [r for r in records
            if RECORDED_JUDGMENT_RE.match(str(r.get('doC_TYPE') or '').strip())
            or _is_case_court_paper(r, keys)
            or _signed_by_plaintiff(r, keys)]


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


_MONEY_ON_LINE = re.compile(r'\$?\s*\d[\d,]*\.\d{2}')


def total_label_lines(text):
    """Lines that are a total ROW in a table, not prose that happens to mention a total.

    The 2026-09-22 pilot paid to have a third page read because its body text contained the words
    "grand total sum" in the middle of a sentence. A label that decides a figure sits at the START
    of its own line, in a label column, and the line either carries the figure or is short because
    the figure is in the column beside it. A sentence is neither.
    """
    out = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        match = TOTAL_RE.search(line)
        # start() > 2 means the words appear inside a sentence, not as the row's label.
        if not match or match.start() > 2:
            continue
        if _MONEY_ON_LINE.search(line) or len(line) <= 48:
            out.append(line)
    return out


def vision_candidates(path, reading, budget, reader=None, out_dir=None):
    """Ask the second reader for the pages OCR could not resolve. -> (candidates, detail)

    Runs ONLY on pages that carry a total label, because those are the pages whose figures decide
    anything, and this reader costs money per page. Its output goes through exactly the same sum
    check as OCR's: a total the document's own line items do not reproduce is not admissible,
    whichever reader produced it.
    """
    wanted = sorted({page['page'] for page in reading['pages']
                     if page['outcome'] in ('text', 'ocr_text')
                     and total_label_lines(page.get('text'))})
    if not wanted:
        # No page claims to carry a total. Reading them all would be spending to find that out.
        wanted = sorted({page['page'] for page in reading['pages']
                         if page['outcome'] in ('ocr_text', 'needs_ocr')})[:2]
    detail = DV.read_document(path, wanted, budget, reader=reader, out_dir=out_dir)
    pool = [f['amount'] for f in detail['figures']]
    out = []
    for total in detail['grand_totals']:
        check = sum_check(pool, total['amount'])
        out.append({'amount': total['amount'], 'page': total['page'],
                    'passage': 'grand total transcribed from the page image',
                    'source': 'page_image', 'match': 'vision_grand_total',
                    'text_source': 'vision', 'verified': False,
                    'sum_check': check['ok'], 'sum_check_reason': check['reason'],
                    'sum_check_components': check['components'], 'column_notes': []})
    return out, detail


def _save_vision(row, detail):
    """Persist the second reader's output beside the page text it was bought to supplement."""
    target = row.get('text_dir')
    if not target:
        return
    try:
        path = os.path.join(str(target), 'vision.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'read_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'source_ref': row.get('source_ref'),
                       'usd': detail.get('usd'),
                       'pages': {str(k): v for k, v in (detail.get('pages') or {}).items()},
                       'figures': detail.get('figures') or [],
                       'grand_totals': detail.get('grand_totals') or [],
                       'errors': detail.get('errors') or {}}, fh, indent=1, sort_keys=True)
        row['vision_path'] = path
    except (OSError, TypeError, ValueError) as exc:
        # Losing the sidecar must not lose the run; the figures are still on the row.
        row['vision_save_error'] = '%s: %s' % (type(exc).__name__, str(exc)[:140])


def read_with_sweep(path, ocr=None, images_dir=None, gray_cutoff=None):
    """Read a stored document, trying grey cutoffs until the arithmetic corroborates a total.

    The sweep re-READS the file already on disk; it never re-fetches and never touches the queue,
    so trying four cutoffs costs four OCR passes and no downloads. It stops at the first cutoff
    whose figures the document's own line items reproduce, and falls back to the first reading
    when none does — which is the honest outcome, not a failure.

    -> (reading, [{gray_cutoff, corroborated, figures}])
    """
    cutoffs = ([gray_cutoff] if gray_cutoff is not None
               else list(GRAY_CUTOFF_SWEEP) if ocr is not None else [DS.WATERMARK_GRAY_CUTOFF])
    first, tried = None, []
    for cutoff in cutoffs:
        reading = DS.read_pages(path, ocr=ocr, keep_images_in=images_dir, gray_cutoff=cutoff)
        corroborated = any(c.get('sum_check') for c in judgment_amount_candidates(reading))
        tried.append({'gray_cutoff': cutoff, 'corroborated': corroborated,
                      'figures': len(document_money(reading))})
        first = first if first is not None else reading
        # Nothing was OCR'd, so the cutoff changed nothing and trying another is wasted work.
        if corroborated or not reading.get('pages_from_ocr'):
            return reading, tried
    return first, tried


def collect_recorded(case, records, collector=None, queue=None, county=COUNTY, ocr=None,
                     keep_images=False, gray_cutoff=None, resume=False):
    """Fetch, verify, store and read each recorded instrument. One result row per record.

    `resume=False` — the default, and what the pilot CLI uses — re-reads a document the queue has
    already marked done. This tool exists to run the same document again after a change; needing
    --no-queue to do the thing the tool is for was a bug three times over.

    `resume=True` is for the nightly stage, where finishing the day's backlog is the point and
    re-reading yesterday's documents is not.
    """
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
                                  reader_version=PIPELINE_VERSION, can_ocr=ocr is not None,
                                  force=not resume)
            if job is None:
                # Genuinely finished, or held by another worker. Resuming means not re-fetching.
                prior = [j for j in queue.jobs(county, case)
                         if j['source_ref'] == ref and j['kind'] == 'recorded_instrument']
                state = prior[0]['status'] if prior else 'leased'
                row.update({'status': 'skipped', 'reason': 'already %s on an earlier run' % state,
                            'prior_status': state,
                            'document_key': prior[0]['sha256'] if prior else None})
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
            reading, tried = read_with_sweep(manifest['path'], ocr=ocr, images_dir=images_dir,
                                             gray_cutoff=gray_cutoff)
            DS.record_read(manifest['meta_path'], reading)
            # The text is evidence and it cost an OCR pass; it gets written down. The pilot run
            # read five pages and kept none of the words.
            text_dir = DS.save_page_text(manifest, reading)
            # No field here is called 'sha256'. There are two different hashes in play and one
            # ambiguous name for them was enough to make a stable identity look unstable: the
            # DOCUMENT is `document_key` and never changes between fetches; `source_sha256` is
            # what the clerk happened to serve this time and changes every fetch, by design.
            row.update({'status': 'stored',
                        'document_key': manifest['document_key'],
                        'source_sha256': manifest['source_sha256'],
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
                        'gray_cutoffs_tried': tried,
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
                # The queue records the stable identity, not this fetch's bytes: a queue row
                # keyed on bytes that change every fetch can never say "we already have this".
                queue.complete(job['id'], owner, sha256=manifest['document_key'],
                               reader_version=PIPELINE_VERSION,
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


# How many corroborated judgments to eyeball against their page images before the refusal in
# judgment_for_analyze is worth revisiting. A number, so the review has an end.
REVIEW_THRESHOLD = 12

# Grey cutoffs to try, in order, when the first one's reading is not corroborated.
#
# One cutoff is a guess, and the 2026-09-22 rerun showed it was the wrong guess: at 160 the
# watermark still cost OCR 31.19 and 2,010.74. Too high leaves watermark strokes touching the
# digits; too low eats faint print. Rather than guess again, the run tries a short sweep and stops
# at the first cutoff whose figures the document's own arithmetic corroborates — the sum check
# decides, not a human eyeballing renders. `--gray-cutoff N` pins a single value; 0 disables the
# cleaning entirely.
GRAY_CUTOFF_SWEEP = (160, 200, 130, 220)

# WHY THERE IS A PIPELINE VERSION AND NOT JUST A READER VERSION
#
# The queue skipped this case's document on three consecutive runs after three code changes, and
# only --no-queue ever got past it. The first skip was because `done` meant done. The second was
# fixed. The THIRD, on 51c554a, was this: `done`-ness was keyed on document_store.READER_VERSION,
# the change that run shipped was in the amount extraction, and the reader had not moved — so by
# its own rule the job was correctly finished and by every human measure it was not.
#
# Anything downstream of the stored bytes can make an old verdict stale, not only the reader. So
# the number the queue compares is this one, and it covers the reader, the extraction AND the
# classification. Bump it when any of them changes.
#
# It happened a FOURTH time on b968725, and this comment is the reason it is embarrassing rather
# than surprising: that commit taught the classifier to reject a document belonging to another
# lawsuit and taught the extractor to compose principal-plus-costs, and did not bump this number.
# So the rerun on 2024-014334-CA-01 skipped the Bank of America paper with "already done on an
# earlier run", demoted it to `unknown` because a skipped row has no reading to classify, and
# never exercised the new check on the text at all. The verdict looked right and nothing had
# re-read anything.
# 8: the nightly's judgments_only filter now also fetches satisfactions the plaintiff signed.
# 7: the classifier learned satisfaction_of_judgment (a satisfaction quotes its judgment, so
# version 6 would have filed it as a final judgment and reported a paid debt as owed).
PIPELINE_VERSION = 8


def run(case, records=None, collector=None, queue=None, county=COUNTY, ocr=None,
        judgments_only=False, keep_images=False, gray_cutoff=None, resume=False,
        vision_budget=None, vision_reader=None):
    inventory = enumerate_case(case, collector=collector)
    records = list(records or [])
    # INDEX EVERY ROW WE WERE HANDED, before the judgment filter throws most of them away.
    # These carry cfN_MASTER_ID, which is what makes an instrument addressable, and a book/page
    # citation does not. Until 2026-09-22 only run_documents built that index, so the pilot CLI
    # could fetch and read a judgment four times and document_walk would still report "0
    # instruments addressable" — the same six recorded rows, discarded each run. The index is
    # county metadata and a pure write; nothing downstream changes because of it.
    try:
        import document_walk
        _index = document_walk.RecordIndex()
        if _index.add_models(records):
            _index.save()
    except Exception:
        pass                       # an index is an optimisation; it never fails a pilot run
    # Filter HERE, not in main(), because the filter needs the case's plaintiffs and the docket we
    # just pulled is where they live.
    plaintiffs = plaintiffs_of(inventory['raw'])
    if judgments_only:
        records = recorded_judgments(records, plaintiffs)
    rows = collect_recorded(case, records, collector=collector, queue=queue, county=county,
                            ocr=ocr, keep_images=keep_images, gray_cutoff=gray_cutoff,
                            resume=resume)
    # CITATIONS, HERE, not only inside run_documents. The pilot CLI read seventeen pages of this
    # case on 2026-09-22 and the report named not one instrument the text pointed at, because
    # cited_instruments was attached in a branch this tool never enters. The reading is in hand;
    # extracting what it cites costs nothing and is the whole reason the pages were read.
    # Self-citations are dropped: a five-page instrument stamps its own book and five pages across
    # its own pages, and five of the seven "citations" found that day were exactly that.
    try:
        import document_classify
        import document_walk
        own = document_walk.own_spans(rows)
        for row in rows:
            reading = row.get('reading')
            if not reading:
                continue
            row['cited_instruments'] = [
                c for c in document_classify.cited_instruments(reading)
                if document_walk.key_of(c['book'], c['page_no']) not in own]
    except Exception:
        pass                       # citations are an addition; they never fail a pilot run
    candidates = [c for row in rows if not is_satisfaction_row(row)
                  for c in (row.get('amount_candidates') or [])]
    tried = [t for row in rows for t in (row.get('gray_cutoffs_tried') or [])]

    # The second reader, and ONLY when the first one's figures did not add up. If OCR produced a
    # total the document's line items reproduce, there is nothing left to buy.
    vision = None
    if vision_budget is not None and not any(c.get('sum_check') for c in candidates):
        vision = {'pages_read': 0, 'usd': 0.0, 'documents': [], 'pages': []}
        for row in rows:
            if row.get('status') != 'stored' or not row.get('reading') or is_satisfaction_row(row):
                continue
            found, detail = vision_candidates(
                row['path'], row['reading'], vision_budget, reader=vision_reader,
                out_dir=row.get('images_dir'))
            row['vision_candidates'] = found
            # ALSO on the row's own candidate list. They were only ever in `vision_candidates`,
            # which judgment_for_analyze and case_dossier do not read, so a correctly read
            # judgment reached the report and stopped there. Whether the figure is then usable
            # is a policy question, decided in judgment_for_analyze; it should not be decided by
            # a figure quietly not arriving.
            row['amount_candidates'] = (row.get('amount_candidates') or []) + found
            row['vision_figures'] = detail['figures']
            row['vision_errors'] = detail['errors']
            # WRITE DOWN WHAT THE MONEY BOUGHT. The 2026-09-22 run on 2024-014334-CA-01 was
            # billed $0.0331, produced no grand total, and left nothing on disk: the stored
            # reading had no vision block, so there was no way to see what the second reader had
            # actually transcribed or to tell a bad read from an honest "no total on this page".
            # A metered reader whose output is not persisted is money spent on nothing.
            _save_vision(row, detail)
            candidates.extend(found)
            vision['pages_read'] += len(detail['pages'])
            vision['usd'] = round(vision['usd'] + detail['usd'], 6)
            # Per PAGE, not just per document. A nightly cap set from a per-document total is
            # set from a number nobody can check; the pilot's $0.0675 was three pages, one of
            # which should never have been sent.
            per_page = [{'page': no,
                         'usd': result.get('usd'),
                         'input_tokens': result.get('input_tokens'),
                         'output_tokens': result.get('output_tokens')}
                        for no, result in sorted(detail['pages'].items())]
            vision['pages'].extend(dict(page, source_ref=row['source_ref'])
                                   for page in per_page)
            vision['documents'].append({'source_ref': row['source_ref'],
                                        'pages': sorted(detail['pages']),
                                        'page_costs': per_page,
                                        'figures': len(detail['figures']),
                                        'errors': detail['errors'], 'usd': detail['usd']})
        vision['budget'] = vision_budget.report()
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
        # ADMISSIBLE candidates only. On 2026-09-22 this field carried $150.00 — the Collection
        # Process Fee, handed to GRAND TOTAL by a mis-shifted column pairing — while the sum check
        # on that same figure had already failed. The strict gates below held, but anyone reading
        # the report or pilot.json got a wrong number with nothing marking it wrong. A figure the
        # arithmetic does not corroborate is not "the judgment amount" anywhere in this report.
        'judgment_amount_agreed': agreed_amount([c for c in candidates if admissible(c)]),
        'judgment_amount_rejected': [c for c in candidates if not admissible(c)],
        'judgment_amount_status': 'unverified_extraction',
        'gray_cutoffs_tried': tried,
        'vision': vision,
        'gray_cutoff': tried[-1]['gray_cutoff'] if tried else None,
        # A total the page's own line items reproduce. This is the evidence that would justify
        # trusting an OCR'd figure one day; on the pilot page it is False, because OCR misread a
        # subtotal the watermark crossed.
        'judgment_amount_corroborated': any(c.get('sum_check') for c in candidates),
    }
    # Says in WORDS why a corroborated figure still does not reach records_liens.analyze. It is a
    # reviewed decision (Alejandro, 2026-09-22), and a null field with no reason beside it reads
    # like a bug to the next person who opens this file.
    report['judgment_amount_held_back'] = (
        'corroborated by the document\'s own line items, and still held back from the equity '
        'math by policy: every figure here is read off a scan, and analyze() picks which mortgage '
        'is the foreclosing first by closest amount. Revisit after %d of these have been checked '
        'against the kept page images.' % REVIEW_THRESHOLD
        if any(c.get('sum_check') for c in candidates) else None)
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


# A figure produced by reading an IMAGE, by either reader. `None` and 'embedded' mean the PDF's
# own text layer, which is the county's typesetting and not a reading of ink.
SCAN_SOURCES = ('ocr', 'vision')


CORROBORATION_LOG = 'judgment_corroborations.json'


def log_corroboration(report, filename=CORROBORATION_LOG):
    """Append this case to the review log when a scanned figure was corroborated. -> count, path

    The refusal in judgment_for_analyze ends when enough of these have been checked by a human
    against the kept page images. That needs a count that survives the run, not a number someone
    remembers. Add-only, one entry per case (the newest wins on a re-run), and it goes to the
    DealFlow folder through case_review.output_path like every other file carrying case data.
    """
    if not report.get('judgment_amount_corroborated'):
        return None, None
    import case_review
    target = case_review.output_path(filename)
    try:
        entries = json.loads(target.read_text(encoding='utf-8'))
        if not isinstance(entries, list):
            entries = []
    except (OSError, ValueError):
        entries = []
    entries = [e for e in entries if e.get('case') != report['case']]
    sources = sorted({c.get('text_source') for c in report['judgment_amount_candidates']
                      if c.get('sum_check')} - {None})
    entries.append({'case': report['case'], 'county': report['county'],
                    'amount': report['judgment_amount_agreed'],
                    'read_by': sources,
                    'images': sorted({r.get('images_dir') for r in report.get('documents', [])
                                      if r.get('images_dir')}),
                    # A human sets this to true once they have compared the figure to the image.
                    'checked_against_image': False})
    target.parent.mkdir(parents=True, exist_ok=True)
    DS._atomic_write_text(str(target), json.dumps(entries, indent=2) + '\n')
    return len(entries), target


def judgment_for_analyze(report, allow_ocr=False):
    """The value a caller MAY pass as `records_liens.analyze(..., judgment=...)`, or None.

    Only from a document that was page-verified against the recording index AND fully read AND
    whose pages agree on one total. Anything less returns None and the caller keeps the board's
    own figure — which is what happens today.

    Figures read off a SCAN are EXCLUDED by default — OCR's and the vision reader's alike. The
    reader changed on 2026-09-22; the reason for the refusal did not. `analyze` uses this number to pick which open
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
            if r.get('page_count_verified') and r.get('read_status') == 'read'
            and not is_satisfaction_row(r)]
    candidates = [c for r in good for c in (r.get('amount_candidates') or [])]
    if allow_ocr:
        # The escape hatch gets a guard. A scanned figure is admissible only when the page's own
        # line items add up to it: the pilot showed OCR reading a grand total correctly while
        # misreading a subtotal the watermark crossed, with nothing anywhere to say so. Two
        # readings of the same arithmetic agreeing is evidence; one figure is not.
        candidates = [c for c in candidates
                      if c.get('text_source') not in SCAN_SOURCES or c.get('sum_check')]
    else:
        candidates = [c for c in candidates if c.get('text_source') not in SCAN_SOURCES]
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
    parser.add_argument('--gray-cutoff', type=int, default=None,
                        help='pin the grey level at or above which pixels are whitened before OCR,'
                             ' to strip the clerk watermark crossing the amounts column. Default '
                             'is to sweep %s and stop at the first whose figures add up; 0 '
                             'disables the cleaning.' % (GRAY_CUTOFF_SWEEP,))
    parser.add_argument('--vision', action='store_true',
                        help='when OCR\'s figures do not add up, read the page IMAGE with the '
                             'Claude API as a second reader. Metered; requires --max-spend.')
    parser.add_argument('--max-spend', type=float,
                        help='dollar cap for --vision on this run. Checked against each call\'s '
                             'worst case BEFORE the call is made.')
    parser.add_argument('--vision-model', default=DI.DEFAULT_MODEL,
                        help='model for --vision (default %(default)s)')
    parser.add_argument('--no-queue', action='store_true', help='skip the resumable queue')
    parser.add_argument('--resume', action='store_true',
                        help='honour the queue\'s done state instead of re-reading. Off by '
                             'default: this tool is for running the same document again.')
    parser.add_argument('--out', help='write the run report to this JSON file (under DEALFLOW_DIR)')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    # Validated before any data is loaded: a metered flag with no cap is an error whether or not
    # this case happens to have documents to read.
    if args.vision and not args.max_spend:
        parser.exit(2, '--vision is metered: pass --max-spend, e.g. --vision --max-spend 0.50\n')
    vision_budget = (DI.Budget(args.max_spend, model=args.vision_model)
                     if args.vision else None)
    vision_reader = DV.VisionReader(model=args.vision_model) if args.vision else None
    if vision_reader is not None:
        # Fail on the missing key HERE, not at the first call. The 2026-09-22 run that found this
        # had already downloaded the document and made four OCR passes before anything asked
        # whether it could pay for the reader it was about to use. A precondition checked after
        # the work is not a precondition.
        try:
            vision_reader.client()
        except DI.NotConfigured as gap:
            parser.exit(2, '--vision cannot run here: %s\n'
                           'Set ANTHROPIC_API_KEY in this shell, or drop --vision.\n' % gap)

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
                     gray_cutoff=args.gray_cutoff, resume=args.resume,
                     vision_budget=vision_budget, vision_reader=vision_reader)
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
        for cite in row.get('cited_instruments') or []:
            print('  CITES %s/%s (page %s, %s): %s'
                  % (cite['book'], cite['page_no'], cite['cited_on_page'],
                     cite.get('pattern') or 'strict', cite['passage'][:90]))
        if row.get('text_dir'):
            print('  page text -> %s' % row['text_dir'])
    _cited = sorted({(c['book'], c['page_no']) for row in report['documents']
                     for c in (row.get('cited_instruments') or [])})
    if _cited:
        print('  %d instrument(s) cited by this case\'s documents and NOT already fetched: %s'
              % (len(_cited), ', '.join('%s/%s' % bp for bp in _cited)))
        print('    follow them with: python -u document_walk.py --case %s --dry-run' % args.case)
    for gap in report['access_gaps']:
        print('  GAP %s — %s' % (gap['source_ref'], gap['reason']))
    if len(report.get('gray_cutoffs_tried') or []) > 1:
        print('  grey cutoffs tried: %s' % ', '.join(
            '%s (%d figures%s)' % (t['gray_cutoff'], t['figures'],
                                   ', corroborated' if t['corroborated'] else '')
            for t in report['gray_cutoffs_tried']))
    for candidate in report['judgment_amount_candidates']:
        print('  candidate ${:,.2f} (page {}, {}, {}) — line items {}: {}{}'.format(
            candidate['amount'], candidate['page'], candidate.get('text_source') or 'embedded',
            candidate.get('match'), 'CHECK OUT' if candidate.get('sum_check') else 'do not add up',
            candidate.get('sum_check_reason'),
            '' if admissible(candidate) else '  [NOT USED]'))
        for note in (candidate.get('column_notes') or []):
            print('      %s' % note)
    if report.get('vision'):
        v = report['vision']
        print('  second reader (page images): %d page(s), $%.4f of the $%.2f cap'
              % (v['pages_read'], v['usd'], v['budget']['limit_usd']))
        for page in v.get('pages') or []:
            print('      page %s: $%.4f (%s in, %s out)'
                  % (page['page'], page['usd'] or 0.0,
                     page['input_tokens'], page['output_tokens']))
        for doc in v['documents']:
            for page, why in sorted((doc['errors'] or {}).items()):
                print('      page %s not read: %s' % (page, why))
    agreed = report['judgment_amount_agreed']
    shown = ('${:,.2f}'.format(agreed) if agreed is not None else 'not established')
    print('  judgment amount: %s (%s)' % (shown, report['judgment_amount_status']))
    if agreed is None and report['judgment_amount_rejected']:
        print('  a figure WAS extracted and is not being reported: the document\'s own line items '
              'do not add up to it, so it is as likely to be the wrong row as the right one')
    if report.get('judgment_amount_held_back'):
        print('  NOT a bug: %s' % report['judgment_amount_held_back'])
        count, target = log_corroboration(report)
        if count is not None:
            print('  %d of %d corroborated judgment(s) logged for review -> %s'
                  % (count, REVIEW_THRESHOLD, target))
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
