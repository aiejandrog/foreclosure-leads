"""Pure body-evidence enrichment for generic Miami recording index labels."""
import copy
import re

from document_walk import key_of


def saved_vision_candidates(detail, expected_source_ref):
    """Recheck saved typed arithmetic only; no reader/API calls or fresh verification."""
    from miami_judgment import labeled_sum_check
    if (not isinstance(detail, dict) or not expected_source_ref
            or detail.get('source_ref') != expected_source_ref
            or detail.get('errors') or detail.get('unreadable')):
        return []
    figures = detail.get('figures') or []
    pages = detail.get('pages') or {}
    if not isinstance(pages, dict) or any(not isinstance(p, dict) or p.get('unreadable') or p.get('errors')
                                          for p in pages.values()):
        return []
    if any(not isinstance(f, dict) or not f.get('page') for f in figures):
        return []
    import judgment_money as JM
    read_pages = {JM._page_no(p) for p in pages}
    results = []
    totals = detail.get('grand_totals') or []
    if any(not isinstance(t, dict) or not t.get('page') or t.get('amount') is None
           for t in totals):
        return []
    # The same check every other path uses: one run of rows ending at the total, which may start
    # on an earlier read page, rates and credits kept, subtotal membership exact.
    for total, check in zip(totals, JM.verify_document(figures, totals, read_pages)):
        if not check['ok']:
            return []
        results.append({'amount':total['amount'],'page':total['page'],
            'sum_check':True,'sum_check_components':check['components'],
            'sum_check_reason':check['reason'],'sum_check_rows':check['component_rows'],
            'sum_check_credits':check['credits'],'sum_check_rates':check['rates'],
            'sum_check_subtotals':check['subtotals'],'sum_check_pages':check['pages'],
            'sum_check_run':check['run'],'text_source':'vision','verified':False,
            'passage':total.get('passage') or 'grand total transcribed from the page image',
            'source_ref':expected_source_ref,'evidence_status':'saved_read_rechecked_not_fresh_vision'})
    return results


def enrich_claims(search_reports, raw_results, documents):
    """Keep body-classified judgment candidates; never decide attachment or equity."""
    reports = copy.deepcopy(search_reports)
    indexed = {}
    for doc in documents:
        record = (doc.get('stored') or {}).get('record_key') or {}
        if record.get('book') and record.get('page'):
            indexed[key_of(record['book'], record['page'])] = doc
    for report in reports:
        claims = report.setdefault('potential_title_party_claims', [])
        gaps = report.setdefault('gaps', [])
        # This foreclosure's own recorded filings were moved out of the claims by the name search
        # (document_walk.own_case_basis). Rebuilding claims from the raw results must not put them
        # back: 2024-014878's own judgment OR 34932-1256 read as a claim in 6 of 7 replayed cases.
        # A row with no book/page keys to '0/0' and would hide every other keyless claim: skip it.
        own = {key_of(o.get('book'), o.get('page_no')) for o in report.get('own_case_instruments') or []
               if o.get('own_case') and o.get('book') and o.get('page_no')}
        if own:
            claims[:] = [c for c in claims if key_of(c.get('book'), c.get('page_no')) not in own]
        seen = {(key_of(c.get('book'), c.get('page_no')), c.get('under_name')) for c in claims}
        for search in report.get('searched', []):
            name = search['name']
            for model in raw_results.get(name) or []:
                book, page = model.get('reC_BOOK'), model.get('reC_PAGE')
                if not book or not page:
                    continue
                key = key_of(book, page)
                if key in own:
                    continue
                doc = indexed.get(key)
                classification = (doc or {}).get('classification') or {}
                kind = classification.get('text_kind') or classification.get('kind') or ''
                if not doc or not kind or kind == 'unknown':
                    if re.search(r'DADE COURT PAPER|\bDCP\b|JUDG', str(model.get('doC_TYPE')), re.I):
                        gap = {'name':name, 'coverage':'unknown',
                               'reason':'%s/%s: court-paper body not classified; judgment status unknown' % (book, page)}
                        if gap not in gaps:
                            gaps.append(gap)
                    continue
                if not re.search('judgment', kind, re.I) or re.search('satisf|release|discharge', kind, re.I):
                    continue
                if (key, name) in seen:
                    claims[:] = [c for c in claims if
                        (key_of(c.get('book'), c.get('page_no')), c.get('under_name')) != (key, name)]
                seen.add((key, name))
                checked = [c for c in doc.get('amount_candidates', [])
                           if c.get('sum_check') is True and not c.get('composed') and c.get('amount') is not None]
                unique = {str(c['amount']) for c in checked}
                candidate = checked[0] if len(unique) == 1 else None
                stored = doc.get('stored') or {}
                claims.append({'book':book,'page_no':page,'under_name':name,
                    'doc_type':model.get('doC_TYPE'),'body_kind':kind,'rec_date':model.get('reC_DATE'),
                    'source_ref':doc.get('source_ref'),
                    'document_hash':stored.get('source_sha256'),
                    'amount':candidate['amount'] if candidate else None,
                    'amount_status':'corroborated_document_total' if candidate else 'unknown',
                    'amount_evidence':copy.deepcopy(candidate),
                    'identity_status':'search_result_only','attachment_status':'unknown',
                    'satisfaction_status':'unknown','parcel_status':'unknown'})
    return reports


_CROSS_REF_RE = re.compile(r'RELATED|PRIOR|COMPANION|CONSOLIDAT|TRANSFER|RELATING|\bSEE\b|\bCF\.|\bV\.?S\.?\b|ORIGINAL|FORMER|\bAND\b', re.I)


def _captioned_with(doc, want):
    """True when this document's own page-1 caption prints exactly this case number (full year,
    sequence, type and division: 2099-000001-CC-05 is not 2099-000001-CA-01). One "CASE NO" line in
    the first twelve lines, carrying one number, with no cross-reference word on it: a later paper
    that cites this foreclosure is not this foreclosure's filing."""
    import document_classify as DC
    pages = [p for p in ((doc.get('reading') or {}).get('pages') or [])
             if isinstance(p, dict) and p.get('outcome') in ('text', 'ocr_text')]
    first = next((p for p in pages if p.get('page') in (1, '1')), None)
    if not first:
        return False
    head = [ln for ln in str(first.get('text') or '').splitlines() if ln.strip()][:12]
    for ln in head:
        if not re.search(r'\bCASE\s*(?:NO|NUMBER|#)', ln, re.I) or _CROSS_REF_RE.search(ln):
            continue
        numbers = {DC.normalize_case(m.group(0)) for pat in (DC._CASE_FL_RE, DC._CASE_BROWARD_RE)
                   for m in pat.finditer(ln)}
        return numbers == {want}
    return False


def mark_own_case(search_reports, this_case, case, documents):
    """Re-mark this foreclosure's own judgment or lis pendens in a SAVED report, with no search.

    Own-case is otherwise set only while a live name search runs (document_walk.run_name_searches),
    so reports saved before that existed still list the case's own filing as a claim (6 of 7 in the
    2026-10-03 replay, unchanged at 2026-10-08). Only an EXPLICIT link counts here:
      - the record's book/page is one the docket itself says its own filing was recorded at, or
      - the record is a judgment or lis pendens whose first-page caption prints this case's number.
    Date proximity and plaintiff identity are never enough. A marked claim moves to
    `own_case_instruments`, whole, with the basis named; every other claim and gap is untouched.
    """
    reports = copy.deepcopy(search_reports)
    import document_classify as DC
    want = DC.normalize_case(case)
    try:
        docket_keys = set((this_case or {}).get('book_pages') or ())
    except TypeError:
        docket_keys = set()
    indexed = {}
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        record = (doc.get('stored') or {}).get('record_key') or {}
        if record.get('book') and record.get('page'):
            indexed[key_of(record['book'], record['page'])] = doc
    for report in reports:
        if not isinstance(report.get('own_case_instruments'), list):
            report['own_case_instruments'] = []
        own = report['own_case_instruments']
        have = {key_of(o.get('book'), o.get('page_no')) for o in own}
        kept = []
        for claim in report.get('potential_title_party_claims') or []:
            book, page = claim.get('book'), claim.get('page_no')
            key = key_of(book, page) if book and page else None
            basis = None
            if key and key in docket_keys:
                basis = 'docket_book_page'
            elif key and want:
                doc = indexed.get(key)
                kind = str(((doc or {}).get('classification') or {}).get('text_kind')
                           or ((doc or {}).get('classification') or {}).get('kind') or '')
                if doc and re.fullmatch(r'final_judgment|lis_pendens', kind) and _captioned_with(doc, want):
                    basis = 'case_number_in_caption'
            if not basis:
                kept.append(claim)
                continue
            if key not in have:
                have.add(key)
                own.append(dict(claim, own_case=True, this_case=basis, moved_from='potential_title_party_claims'))
        report['potential_title_party_claims'] = kept
    return reports
