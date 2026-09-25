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
        seen = {(key_of(c.get('book'), c.get('page_no')), c.get('under_name')) for c in claims}
        for search in report.get('searched', []):
            name = search['name']
            for model in raw_results.get(name) or []:
                book, page = model.get('reC_BOOK'), model.get('reC_PAGE')
                if not book or not page:
                    continue
                key = key_of(book, page)
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
