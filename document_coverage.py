"""document_coverage: every attachment the docket claims, and what became of each one.

Priority 5 of the Miami automation goal. "An inventoried document is not necessarily downloaded
or read" was true and invisible: the timeline flagged an entry as `missing_attachments` or
`unassessed_pages`, but nothing listed attachment by attachment which ones were read, which were
never fetched, which the county refused, and which the county put behind a login.

WHAT THIS PRODUCES
One row per expected attachment of every docket entry, in one of these states:

  read                every page has a readable outcome
  read_partial        stored, some pages unreadable or never assessed
  fetched_unread      stored, not read yet
  queued              a queue job exists and has not finished
  failed              the queue gave up after its retries
  restricted          the county requires a login (or says the filing is confidential/sealed)
  restricted_likely   the docket links a document it does not count (eventType Judgment, 0
                      documents), which the county holds behind its login; not fetched yet
  access_gap          the county refused or returned something unusable, with its reason
  not_enumerated      the entry's attachment list itself could not be obtained
  county_no_document  the docket says this entry carries no document. The docket's claim, not a
                      verified absence.

A restricted or refused court filing is a GAP, and it stays one. The only thing that can stand
beside it is an AUTHORIZED ALTERNATE COPY: the same instrument as recorded in the public Official
Records. Those are public by statute, and this module only links one that is already stored,
prints this case number, is the same kind of instrument, and was recorded close to the docket
entry's date. The link is a candidate (`same_instrument_unverified`); it never marks the court
attachment read, and the coverage total never counts it as the court's copy.

No network, no spending, no writes. timeline_case calls it and saves the result with the timeline.
"""
import re
from datetime import date, timedelta

READABLE = ('text', 'ocr_text', 'vision_text', 'read_as_label', 'exhibit_divider')
_RESTRICTED_RE = re.compile(r'login|log in|sign in|confidential|sealed|restricted|not available to the public',
                            re.I)
# Instruments that are recorded in Official Records as well as filed in the case.
# timeline kind -> the document_classify kinds (read from the recorded copy's own text) that can
# be the same instrument. A satisfaction of MORTGAGE is not a satisfaction of the judgment.
RECORDABLE = {'final_judgment': {'final_judgment'}, 'certificate_of_title': {'certificate_of_title'},
              'certificate_of_sale': {'certificate_of_sale'}, 'satisfaction': {'satisfaction_of_judgment'},
              'vacatur': {'order'}, 'order_of_dismissal': {'order'}}
_BOOK_PAGE_RE = re.compile(r'\b(?:OR\s*)?(?:BK|BOOK)\.?\s*(\d{3,6})\s*,?\s*(?:PG|PAGE)\.?\s*(\d{1,5})\b', re.I)
ALTERNATE_WINDOW_BEFORE = timedelta(days=3)
ALTERNATE_WINDOW_AFTER = timedelta(days=120)


def _iso(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _mdy(value):
    match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', str(value or ''))
    if not match:
        return _iso(value)
    try:
        return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None


# The clerk's watermark and stamp words, and what OCR makes of them ("AL COPY / Nor Atv copy").
_WATERMARK_WORDS_RE = re.compile(r'\b(?:not|an|official|copy|public|access|al|nor|atv)\b', re.I)
MIN_CONTENT_CHARS = 12


def page_is_read(page):
    """Is this page read, not just given a readable outcome?

    A page OCR turns into one character ('o', a returned-mail envelope) or into the watermark alone
    ('AL COPY Nor Atv copy') was counted as read (2022-012065, verify-12 defect 12): nothing on it
    was read. Labels and exhibit dividers ('EXHIBIT 1') are short on purpose and stay read."""
    outcome = page.get('outcome')
    if outcome not in READABLE:
        return False
    if outcome in ('read_as_label', 'exhibit_divider'):
        return True
    content = re.sub(r'[^A-Za-z0-9]', '', _WATERMARK_WORDS_RE.sub(' ', str(page.get('text') or '')))
    return len(content) >= MIN_CONTENT_CHARS


def _reading_state(row):
    manifest = row.get('manifest') or {}
    reading = row.get('reading') or {}
    pages = reading.get('pages') or []
    if not manifest and not pages:
        return None
    if not pages:
        return 'fetched_unread', []
    count = manifest.get('pages') or reading.get('page_count') or reading.get('pages_total')
    seen = {p.get('page') for p in pages}
    missing = [n for n in range(1, count + 1) if n not in seen] if isinstance(count, int) else []
    bad = [p.get('page') for p in pages if not page_is_read(p)]
    if missing or bad:
        return 'read_partial', sorted(set(missing + bad), key=lambda n: (n is None, n))
    return 'read', []


def _attachment_state(row):
    """-> (state, detail) for one court attachment row as run_case_timeline.acquire returns it."""
    reading = _reading_state(row)
    status = row.get('acquisition_status')
    gap = str(row.get('acquisition_gap') or '')
    if reading is not None and status in (None, 'done'):
        return reading
    if status in ('pending', 'leased'):
        return 'queued', []
    if status == 'failed':
        return 'failed', [gap[:200]]
    if status == 'gap':
        return ('restricted' if _RESTRICTED_RE.search(gap) else 'access_gap'), [gap[:200]]
    if reading is not None:
        return reading
    return 'queued', []


def coverage(inventory, rows, entries=(), recorded=(), case=''):
    """-> {'attachments': [...], 'counts': {...}, 'complete': bool, 'alternate_copies': [...]}

    `inventory`   the saved full OCS inventory (entries with inventory_status / attachments)
    `rows`        run_case_timeline.acquire rows ('court:<entry>:<document>')
    `entries`     timeline entries (for each entry's kind and date), optional
    `recorded`    stored Official Records rows: {'source_ref', 'reading', 'recorded_date',
                  'kind'} (kind from the document's own text), optional
    """
    by_entry = {}
    for row in rows or []:
        ref = str(row.get('source_ref') or '')
        if ref.startswith('court:'):
            by_entry.setdefault(ref.split(':', 2)[1], []).append(row)
    kinds = {str(e.get('entry_id')): e for e in entries or []}
    out = []
    for item in inventory.get('entries') or []:
        meta = item.get('metadata') or {}
        ident = str(item.get('source_id') or meta.get('eventID') or '')
        expected = item.get('expected_documents')
        status = item.get('inventory_status')
        base = {'entry_id': ident, 'date': (kinds.get(ident) or {}).get('date') or str(_mdy(meta.get('eventDate')) or ''),
                'kind': (kinds.get(ident) or {}).get('kind'),
                'description': str(meta.get('docketDescrition') or meta.get('docketDescription') or '')[:120]}
        if status == 'gap':
            gap = str(item.get('gap') or '')
            state = 'restricted' if _RESTRICTED_RE.search(gap) else 'not_enumerated'
            out.extend(dict(base, document=None, index=n, state=state, detail=[gap[:200]])
                       for n in range(max(int(expected or 0), 1)))
            continue
        import document_collectors as DC
        if not expected and DC.links_uncounted_document(meta):
            # verify-12 defect 11: five of these were reported as county_no_document; every one
            # the county was asked for answered with its login page.
            out.append(dict(base, document=None, index=0, state='restricted_likely',
                            detail=['docket links a document it counts as 0 (eventType Judgment); '
                                    'the county holds these behind its login']))
            continue
        if not expected or status == 'county_reports_no_document':
            out.append(dict(base, document=None, state='county_no_document', detail=[]))
            continue
        found = by_entry.get(ident, [])
        for row in found:
            state, detail = _attachment_state(row)
            out.append(dict(base, document=str(row.get('source_ref')), state=state, detail=detail))
        # The docket said more documents than the queue ever held: each missing one is named.
        for n in range(len(found), int(expected)):
            out.append(dict(base, document=None, index=n, state='not_enumerated',
                            detail=['docket says %s document(s); %d reached the queue'
                                    % (expected, len(found))]))
    counts = {}
    for row in out:
        counts[row['state']] = counts.get(row['state'], 0) + 1
    unread = [r for r in out if r['state'] not in ('read', 'county_no_document')]
    alternates = alternate_copies(unread, recorded, case)
    needed = []
    for row in unread:
        link = next((a for a in alternates if a['entry_id'] == row['entry_id']
                     and a['status'] == 'same_instrument_unverified'), None)
        if link:
            row['alternate_copy'] = link['source_ref']
        elif row.get('kind') in RECORDABLE and row['entry_id'] not in {n['entry_id'] for n in needed}:
            # No stored public copy. Name the book/page the docket itself cites, when it cites
            # one, so the recorded-instrument walk can fetch that exact instrument for free.
            meta = next((i.get('metadata') or {} for i in inventory.get('entries') or []
                         if str(i.get('source_id') or (i.get('metadata') or {}).get('eventID')) == row['entry_id']), {})
            text = ' '.join(str(meta.get(k) or '') for k in ('docketDescrition', 'docketDescription', 'comments'))
            needed.append({'entry_id': row['entry_id'], 'kind': row['kind'],
                           'court_attachment_state': row['state'],
                           'cited_book_page': ['%s-%s' % m for m in _BOOK_PAGE_RE.findall(text)],
                           'reason': 'no stored public recorded copy of this instrument'})
    return {'attachments': out, 'counts': counts,
            'expected': sum(1 for r in out if r['state'] != 'county_no_document'),
            'read': counts.get('read', 0),
            'complete': not unread,
            'alternate_copies': alternates,
            'alternate_copy_needed': needed,
            'qualification': ('Counts follow the docket\'s own document counts, which OCS does not '
                              'let us verify. An alternate copy is a public recorded candidate for '
                              'the same instrument; it does not make the court attachment read.')}


def alternate_copies(unread, recorded, case):
    """Recorded Official Records copies that may stand in for court attachments we could not read.

    A candidate needs all of: already stored and read; prints THIS case number; its own text reads
    as the same kind of instrument; recorded from 3 days before to 120 days after the docket
    entry. One recorded document is offered for at most one entry, and an entry with two
    candidates gets none: which is the copy is then a question for a person.
    """
    import document_classify
    pool = []
    for doc in recorded or ():
        reading = doc.get('reading') or {}
        if not reading.get('pages') or not case:
            continue
        if document_classify.case_identity(reading, case).get('agrees') is not True:
            continue
        pool.append(doc)
    out, used = [], set()
    for row in unread:
        kinds = RECORDABLE.get(row.get('kind') or '')
        when = _iso(row.get('date'))
        if not kinds or when is None or row['entry_id'] in {o['entry_id'] for o in out}:
            continue
        noun = sorted(kinds)[0].replace('_', ' ')
        matches = []
        for doc in pool:
            recorded_on = _mdy(doc.get('recorded_date'))
            if (recorded_on and when - ALTERNATE_WINDOW_BEFORE <= recorded_on <= when + ALTERNATE_WINDOW_AFTER
                    and doc.get('kind') in kinds and doc['source_ref'] not in used):
                matches.append(doc)
        if len(matches) == 1:
            used.add(matches[0]['source_ref'])
            out.append({'entry_id': row['entry_id'], 'source_ref': matches[0]['source_ref'],
                        'status': 'same_instrument_unverified',
                        'basis': 'prints this case number; reads as %s; recorded %s, docket entry %s'
                                 % (noun, matches[0].get('recorded_date'), row.get('date')),
                        'court_attachment_state': row['state']})
        elif len(matches) > 1:
            out.append({'entry_id': row['entry_id'], 'source_ref': None, 'status': 'ambiguous',
                        'basis': '%d recorded candidates; not chosen automatically' % len(matches),
                        'candidates': [m['source_ref'] for m in matches],
                        'court_attachment_state': row['state']})
    return out
