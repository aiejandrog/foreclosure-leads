"""case_dossier — one ordered answer per case: a) filed, b) indexed, c) read, d) the picture.

WHY IT IS ORDERED, AND WHY d COMES LAST
Alejandro asked for the result as a sequence that adds up to one conclusion. The order is not
presentation — it is the evidence ladder, and each rung is allowed to be empty:

    a  WHAT WAS FILED          the court docket. Entries, parties, what the case says happened.
                               Its own coverage is unproven: OCS publishes no pagination cursor.
    b  WHAT THE INDEX SHOWS    the recorded chain from records_liens — mortgages, satisfactions,
                               HOA/code/IRS liens, the foreclosing first. INDEX METADATA. It is
                               what the county's search returned, not a document anyone opened.
    c  WHAT THE DOCUMENTS SAY  documents fetched, page-verified against the recording index, read,
                               and classified from their own text. Empty until OCR reads a scan.
    d  THE PICTURE             equity_state's verdict, and WHICH rungs it rests on.

THE RULE THAT MAKES IT HONEST
`d` names its basis. Today that is `['b']` for every case, because nothing from `c` is wired into
the equity verdict — `equity_state` keeps its five states and its two FACT states, and a document
reading cannot promote a lead into `clear` or `priced`. So a dossier whose `c` is empty says so in
one line and `d` reads exactly as it always did. That is the intended output, not a shortfall: it
stops a reader inferring that the documents were read because a verdict appeared next to them.

Nothing here fetches, publishes, or writes to a lead.
"""
from datetime import datetime, timezone

import document_classify
import equity_state

SCHEMA_VERSION = 1
SECTIONS = ('a_filed', 'b_indexed', 'c_documents', 'd_picture')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _section(title, status, basis, **kw):
    out = {'title': title, 'status': status, 'basis': basis}
    out.update(kw)
    return out


def _a(inventory):
    """a) What the court docket says was filed."""
    if not inventory:
        return _section('What was filed', 'missing', 'court_docket',
                        note='no docket was pulled for this case')
    entries = inventory.get('entries') or []
    with_docs = [e for e in entries if e.get('expected_documents')]
    return _section(
        'What was filed', 'present' if entries else 'empty', 'court_docket',
        docket_entries=len(entries),
        entries_claiming_documents=len(with_docs),
        documents_claimed=sum(int(e.get('expected_documents') or 0) for e in with_docs),
        # False, always. Without a pagination cursor "we saw the whole docket" is unproven, and an
        # unproven claim of completeness is the one that makes every count below look final.
        pagination_verified=bool(inventory.get('pagination_verified')),
        judgment_candidates=[{'source_ref': c.get('source_ref'),
                              'description': (c.get('metadata') or {}).get('docketDescrition'),
                              'match': 'candidate_by_keyword'}
                             for c in (inventory.get('judgment_candidates') or [])],
        caveat='Docket metadata only. No document is read at this rung.')


def _b(chain):
    """b) What the recorded-records INDEX shows. Not a document anyone opened."""
    if not chain:
        return _section('What the records index shows', 'missing', 'records_index',
                        note='no recorded chain has been pulled for this case')
    liens = [l for l in (chain.get('liens') or []) if isinstance(l, dict)]
    return _section(
        'What the records index shows', 'present', 'records_index',
        confidence=chain.get('conf'),
        records_examined=chain.get('nrec'),
        surviving_instruments=chain.get('open_count'),
        surviving_total=chain.get('surv'),
        first_mortgage_estimate=chain.get('first_est'),
        hoa_open=chain.get('hoa_open'), code_open=chain.get('code_open'),
        irs_open=chain.get('irs_open'),
        liens=[{'date': l.get('d'), 'amount': l.get('amt'), 'party': l.get('party'),
                'book_page': l.get('bp'), 'status': l.get('st')} for l in liens],
        anchored_to=chain.get('subdiv') or None,
        caveat='Index rows and party names. Amounts are RECORDED figures, never a current payoff.')


def _c(documents):
    """c) What the documents themselves say."""
    documents = documents or []
    if not documents:
        return _section('What the documents say', 'empty', 'document_text',
                        documents=[],
                        note='no document has been fetched and read for this case')
    read = [d for d in documents if d.get('read_status') == 'read']
    classified = [d for d in documents
                  if (d.get('classification') or {}).get('kind') not in (None, 'unknown')]
    rows = []
    for d in documents:
        verdict = d.get('classification') or {}
        rows.append({
            'source_ref': d.get('source_ref'),
            'status': d.get('status'),
            'reason': d.get('reason'),
            # The stable identity of the document, plus the hash of what arrived on the last
            # fetch. They are different things: the clerk re-serialises the PDF per request, so
            # the byte hash changes between fetches of the same instrument.
            'document_key': d.get('document_key'),
            'source_sha256': d.get('source_sha256'),
            'recording_stamp_agrees': (d.get('recording_stamp') or {}).get('agrees')
            if d.get('recording_stamp') else None,
            'index_label': verdict.get('index_label') or d.get('doc_type'),
            'is': verdict.get('kind', 'unknown'),
            'classified_from': verdict.get('basis'),
            'confidence': verdict.get('confidence'),
            'index_agrees': verdict.get('index_agrees'),
            'pages': d.get('pages'),
            'page_count_verified': d.get('page_count_verified'),
            'read_status': d.get('read_status'),
            'pages_unresolved': d.get('pages_unresolved'),
            'amounts': d.get('amount_candidates') or [],
            'cites_instruments': d.get('cited_instruments') or [],
        })
    status = 'present' if read else ('fetched_unread' if documents else 'empty')
    return _section(
        'What the documents say', status, 'document_text',
        documents=rows,
        fetched=len(documents), fully_read=len(read), classified=len(classified),
        page_verified=sum(1 for d in documents if d.get('page_count_verified')),
        # Every book/page the read documents point at. An owner-name search never sees a lien
        # recorded against a prior owner or a misspelt name; the document that references it does.
        cited_but_not_fetched=[c for d in documents for c in (d.get('cited_instruments') or [])
                               if not c.get('fetched')],
        caveat=('Nothing at this rung is verified. Every amount carries its page and the line it '
                'came from, and an OCR-sourced figure is marked as such.'))


def _d(chain, section_c):
    """d) The picture, and which rungs it rests on."""
    state = equity_state.state_of(chain)
    rests_on = ['b'] if chain else []
    doc_note = None
    if section_c['status'] == 'empty':
        doc_note = 'No document has been read, so this verdict is the index-based one.'
    elif section_c.get('fully_read'):
        # Deliberate: reading a document does NOT move the verdict. equity_state's two FACT states
        # are reached through the recorded chain, and this branch exists to say so out loud rather
        # than let a reader assume the documents were folded in.
        doc_note = ('%d document(s) were read. They are NOT folded into this verdict: the equity '
                    'states are computed from the recorded chain alone.' % section_c['fully_read'])
    else:
        doc_note = ('Documents were fetched but not read, so this verdict is the index-based one.')
    return _section(
        'The picture', 'present' if chain else 'missing', 'equity_state',
        eqstate=state,
        verdict=equity_state.LABEL[state],
        short=equity_state.SHORT[state],
        speakable_as_fact=state in equity_state.FACT,
        rests_on=rests_on,
        documents_note=doc_note,
        caveat=('Only %s may be spoken as fact to a homeowner. A recorded figure is never a '
                'current payoff.' % ' or '.join(equity_state.FACT)))


def build(case, county, inventory=None, chain=None, documents=None, walk=None):
    """Assemble one case's dossier. Pure: everything it reports was handed to it."""
    a = _a(inventory)
    b = _b(chain)
    c = _c(documents)
    if walk is not None:
        c['walk'] = _walk_section(walk)
    d = _d(chain, c)
    gaps = []
    if not a.get('pagination_verified'):
        gaps.append('the docket may be incomplete: no pagination cursor is published')
    if b['status'] == 'missing':
        gaps.append('no recorded chain pulled')
    if c['status'] != 'present':
        gaps.append('no document has been read')
    for row in c.get('documents', []):
        if row.get('status') in ('gap', 'rejected', 'skipped') and row.get('reason'):
            gaps.append('%s: %s' % (row['source_ref'], row['reason']))
        if row['read_status'] and row['read_status'] != 'read':
            gaps.append('%s: %s' % (row['source_ref'], row['read_status']))
        if not row['page_count_verified']:
            gaps.append('%s: page count never verified against the recording index'
                        % row['source_ref'])
    unfetched = len(c.get('cited_but_not_fetched') or [])
    if unfetched:
        gaps.append('%d instrument(s) cited by a read document have not been fetched' % unfetched)
    for row in (c.get('walk') or {}).get('unresolved') or []:
        # An unresolved citation is a named instrument we could not open. It belongs in the gap
        # list for the same reason an unfetched one does: the document says it exists.
        gaps.append('cited instrument %s/%s not resolved: %s'
                    % (row.get('book'), row.get('page_no'), row.get('reason')))
    stopped = (c.get('walk') or {}).get('stopped_because')
    if stopped:
        gaps.append('the citation walk stopped early - %s' % stopped)
    planned = ((c.get('walk') or {}).get('name_search') or {}).get('skipped') or 0
    if planned:
        gaps.append('%d name(s) on this parcel\'s deeds or docket were never searched; a lien '
                    'recorded against one of them would not be in b' % planned)
    return {
        'schema_version': SCHEMA_VERSION,
        'case': case, 'county': county, 'built_at': _now(),
        'a_filed': a, 'b_indexed': b, 'c_documents': c, 'd_picture': d,
        'conclusion': _conclusion(case, b, c, d),
        'rests_on': d['rests_on'],
        'open_gaps': gaps,
        'complete': not gaps,
    }


def _walk_section(walk):
    """What the citation walk followed, and what it could not reach.

    Kept INSIDE c and outside d on purpose. A walked document is a document — it belongs on the
    same rung as one we fetched from the index, with the extra fact that another document is why
    we knew to look for it. It does not move the equity verdict, and `d.documents_note` still says
    so in words.
    """
    walk = walk or {}
    followed = walk.get('followed') or []
    return {
        'followed': followed,
        'documents_fetched': walk.get('documents_fetched') or 0,
        'unresolved': walk.get('unresolved') or [],
        'depth': walk.get('depth'),
        'budget': walk.get('budget'),
        'stopped_because': walk.get('stopped_because') or '',
        'name_search': walk.get('name_search') or {},
        'note': walk.get('note') or '',
    }


def _conclusion(case, b, c, d):
    """One sentence a person can act on, and it names what it stands on."""
    if b['status'] == 'missing' and c['status'] == 'empty':
        return ('%s: nothing has been established. No recorded chain, no document read.' % case)
    read = c.get('fully_read') or 0
    kinds = sorted({r['is'] for r in c.get('documents', []) if r['is'] != 'unknown'})
    part_c = ('%d document(s) read (%s)' % (read, ', '.join(kinds) or 'none classified')
              if read else 'no document read')
    return ('%s: %s. %s. The equity verdict rests on %s.'
            % (case, d['short'], part_c,
               ' and '.join(d['rests_on']) or 'nothing'))


def classify_documents(documents):
    """Attach a text-based verdict and the instruments each document cites.

    `documents` are collect_recorded rows carrying a `reading`. A row without one keeps
    `unknown` — a document that was not read is not classified from its label.
    """
    for row in documents or []:
        reading = row.get('reading')
        if not reading:
            row['classification'] = {'kind': 'unknown', 'confidence': 'none',
                                     'basis': 'not_read', 'index_label': row.get('doc_type')}
            continue
        row['classification'] = document_classify.classify(reading, row.get('doc_type') or '')
        row['cited_instruments'] = document_classify.cited_instruments(reading)
    return documents
