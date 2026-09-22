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
    # A document from a DIFFERENT action was read, and it is not a document of this case. It is
    # counted and reported separately so no count above it can quietly include it.
    elsewhere = [d for d in documents
                 if (d.get('case_identity') or {}).get('agrees') is False]
    read = [d for d in documents
            if d.get('read_status') == 'read' and d not in elsewhere]
    classified = [d for d in documents
                  if (d.get('classification') or {}).get('kind') not in (None, 'unknown')
                  and d not in elsewhere]
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
            # What the metered second reader transcribed, whether or not it produced a total.
            # Without this a dossier for a page the reader was PAID to read looks identical to
            # one where it was never run.
            'second_reader': ({'figures': d.get('vision_figures') or [],
                               'errors': d.get('vision_errors') or {},
                               'saved_to': d.get('vision_path')}
                              if d.get('vision_figures') is not None else None),
            'cites_instruments': d.get('cited_instruments') or [],
        })
    status = 'present' if read else ('fetched_unread' if documents else 'empty')
    return _section(
        'What the documents say', status, 'document_text',
        documents=rows,
        fetched=len(documents), fully_read=len(read), classified=len(classified),
        read_from_other_actions=len(elsewhere),
        judgment=_operative_judgment(rows),
        page_verified=sum(1 for d in documents if d.get('page_count_verified')),
        # Every book/page the read documents point at. An owner-name search never sees a lien
        # recorded against a prior owner or a misspelt name; the document that references it does.
        cited_but_not_fetched=[c for d in documents for c in (d.get('cited_instruments') or [])
                               if not c.get('fetched')
                               and document_classify.key_of(c.get('book'), c.get('page_no'))
                               not in document_classify.own_spans(documents)],
        # Court papers from the owner's OTHER lawsuits, kept out of everything above and listed
        # here. They are real and they are about this person; they are not about this case.
        other_actions=[{'source_ref': d.get('source_ref'),
                        'belongs_to': (d.get('case_identity') or {}).get('found') or [],
                        'text_kind': (d.get('classification') or {}).get('text_kind')}
                       for d in documents
                       if (d.get('case_identity') or {}).get('agrees') is False],
        caveat=('Nothing at this rung is verified. Every amount carries its page and the line it '
                'came from, and an OCR-sourced figure is marked as such.'))


def _operative_judgment(rows):
    """Which read document IS this case's judgment, or an honest refusal to say.

    MEASURED 2026-09-22 on 2026-013492-CC-26: TWO documents came back classified
    `final_judgment`, 35460-173 and 35460-2179, and the dossier reported both without saying
    which one controls. Picking the later recording would be a guess dressed as a finding: an
    original and an amended judgment, two judgments on separate counts, and a judgment in a
    different action that prints no case number all look the same from the text.

    So one candidate is named, several are named as several, and the reader is told what would
    settle it. `amount` is carried only when exactly one candidate has one, for the same reason.
    """
    found = [r for r in rows if r.get('is') == 'final_judgment']
    if not found:
        return {'operative': None, 'candidates': [], 'certain': False,
                'why': 'no read document on this case classifies as a final judgment'}
    refs = [r.get('source_ref') for r in found]
    if len(found) == 1:
        amounts = found[0].get('amounts') or []
        return {'operative': refs[0], 'candidates': refs, 'certain': True,
                'amount': amounts[0]['amount'] if len(
                    {a['amount'] for a in amounts}) == 1 else None}
    return {'operative': None, 'candidates': refs, 'certain': False,
            'why': ('%d read documents on this case classify as a final judgment (%s). Which one '
                    'controls is a legal question the text does not answer: they may be an '
                    'original and an amended judgment, judgments on separate counts, or one of '
                    'them may belong to another action that prints no case number. Open them and '
                    'compare their dates and their decretal paragraphs.'
                    % (len(refs), ', '.join(str(r) for r in refs)))}


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
    for row in c.get('other_actions') or []:
        gaps.append('%s is a document in %s, not this case, so this case\'s judgment has still '
                    'not been read' % (row['source_ref'], ', '.join(row['belongs_to']) or '?'))
    judgment = c.get('judgment') or {}
    if judgment.get('candidates') and not judgment.get('certain'):
        gaps.append(judgment['why'])
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
    anchor = (((c.get('walk') or {}).get('name_search') or {}).get('anchor') or {})
    if anchor and not anchor.get('anchored'):
        gaps.append('the parcel could not be anchored: %s' % anchor.get('why'))
    planned = ((c.get('walk') or {}).get('name_search') or {}).get('skipped') or 0
    if planned:
        gaps.append('%d name(s) on this parcel\'s deeds or docket were never searched; a lien '
                    'recorded against one of them would not be in b' % planned)
    names = (c.get('walk') or {}).get('names') or {}
    for row in names.get('found_under_other_names') or []:
        # NOT a gap in the sense of missing data - it is found data that rung b does not contain,
        # and the dossier has nowhere else that a reader is guaranteed to look.
        gaps.append('%s recorded %s against %s sits on this parcel and was NOT in the owner-name '
                    'search behind b' % (row.get('doc_type'), row.get('rec_date'),
                                         row.get('under_name')))
    for row in names.get('searched') or []:
        if row.get('outcome') in ('not_reached', 'error'):
            gaps.append('%s was never searched: %s' % (row.get('name'), row.get('reason')))
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
        # What the extra name searches actually returned. Kept separate from `documents` because
        # these are INDEX rows, not documents anyone opened — the same distinction rung b already
        # carries. An entry here is an encumbrance on the subject parcel recorded under a name the
        # owner search never used, which is the one thing the recorded chain structurally cannot
        # see.
        'names': walk.get('names') or None,
        'note': walk.get('note') or '',
    }


def _conclusion(case, b, c, d):
    """One sentence a person can act on, and it names what it stands on."""
    if b['status'] == 'missing' and c['status'] == 'empty':
        return ('%s: nothing has been established. No recorded chain, no document read.' % case)
    read = c.get('fully_read') or 0
    kinds = sorted({r['is'] for r in c.get('documents', [])
                    if r['is'] not in ('unknown', 'other_action')})
    part_c = ('%d document(s) read (%s)' % (read, ', '.join(kinds) or 'none classified')
              if read else 'no document read')
    # Saying "no document read" while the store holds one is the kind of half-truth that sends a
    # reader looking for a bug. Name what was read and say whose case it belongs to.
    other = c.get('read_from_other_actions') or 0
    if other:
        part_c += ('; %d other document(s) read belong to a different action against the same '
                   'party and are not evidence about this case' % other)
    return ('%s: %s. %s. The equity verdict rests on %s.'
            % (case, d['short'], part_c,
               ' and '.join(d['rests_on']) or 'nothing'))


def classify_documents(documents, case=''):
    """Attach a text-based verdict, the instruments each document cites, and WHICH CASE it is in.

    `documents` are collect_recorded rows carrying a `reading`. A row without one keeps
    `unknown` — a document that was not read is not classified from its label.

    `case` is the case these documents are being filed under. An owner-name search returns
    everything recorded against a person, which includes court papers from that person's OTHER
    lawsuits, and on 2024-014334-CA-01 exactly one document came back: a small-claims default
    judgment from 2026-058556-SP-26, filed as this foreclosure's judgment at confidence high.
    A document whose own text names a different case keeps its text verdict under `text_kind`
    and is relabelled `other_action`, so it cannot fill this case's judgment slot, cannot reach
    the conclusion line, and is still listed — it is evidence about the owner, just not about
    this case.
    """
    for row in documents or []:
        reading = row.get('reading')
        if not reading:
            row['classification'] = {'kind': 'unknown', 'confidence': 'none',
                                     'basis': 'not_read', 'index_label': row.get('doc_type')}
            continue
        verdict = document_classify.classify(reading, row.get('doc_type') or '')
        identity = document_classify.case_identity(reading, case)
        row['case_identity'] = identity
        if identity.get('agrees') is False:
            verdict = dict(verdict, text_kind=verdict.get('kind'), kind='other_action',
                           belongs_to=identity['found'], why=identity['why'])
        row['classification'] = verdict
        row['cited_instruments'] = document_classify.cited_instruments(reading)
    return documents
