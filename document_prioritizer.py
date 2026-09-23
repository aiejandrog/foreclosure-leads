"""Offline docket-first acquisition order. Ranking never establishes legal effect."""
from datetime import date
import re
from miami_case_timeline import classify, _date

CRITICAL={'vacatur','satisfaction','notice_of_voluntary_dismissal','order_of_dismissal',
          'suggestion_of_bankruptcy','relief_from_stay','order_cancelling_sale','order_resetting_sale'}


def prioritize(case, inventory, as_of):
    """Use the complete fetched OCS payload, never the board's truncated cache.

    Inspect potentially controlling orders first for free, then latest judgment
    candidates. All entries survive; inaccessible/undated/future entries are gaps.
    This plan authorizes neither spending nor a finding that a judgment controls.
    """
    if not re.fullmatch(r'\d{4}-\d{6}-(?:CA-01|CC-\d{2})',case):
        raise ValueError('Miami civil case required')
    today=date.fromisoformat(as_of)
    raw=inventory.get('raw') or {}
    entries=inventory.get('entries')
    if ((raw.get('caseNumber') or raw.get('caseNo'))!=case or
            not isinstance(raw.get('dockets'),list) or not isinstance(entries,list) or
            [e.get('metadata') for e in entries]!=raw['dockets']):
        raise ValueError('Untruncated matching full OCS inventory required')
    documents=[]; gaps=[]; seen=set()
    if inventory.get('pagination_verified') is not True:
        gaps.append({'reason':'county_pagination_unverified'})
    for item in entries:
        meta=item['metadata']
        ident=str(item.get('source_id') or meta.get('eventID') or '')
        if not ident or ident in seen:
            raise ValueError('Missing or duplicate entry identity')
        seen.add(ident)
        description=str(meta.get('docketDescrition') or meta.get('docketDescription') or '')
        comments=str(meta.get('comments') or '')
        kind=classify(description+' '+comments)
        if re.match(r'\s*(?:certificate of service|proof of service|notice of filing)\b',description,re.I):
            kind='service_or_filing_notice'
        stamp=_date(meta.get('eventDate'))
        count=item.get('expected_documents')
        reasons=[]
        if not stamp: reasons.append('entry_date_unknown')
        elif date.fromisoformat(stamp)>today: reasons.append('future_entry_not_current_evidence')
        if type(count) is not int or count<1: reasons.append('no_image_count_established')
        group=0 if kind in CRITICAL else 1 if kind=='final_judgment' else 2 if kind in ('order_on_motion','notice_of_sale') else 3
        documents.append({'entry_id':ident,'source_ref':item.get('source_ref'),
            'date':stamp,'description':description,'comments':comments,'kind':kind,
            'priority':group,'expected_documents':count,
            'eligible_for_acquisition':not reasons,'requires_body_identity_check':True,
            'selection_basis':'full_case_docket_index_only','gaps':reasons})
        gaps.extend({'entry_id':ident,'reason':reason} for reason in reasons)
    documents.sort(key=lambda d:(not d['eligible_for_acquisition'],d['priority'],
        -date.fromisoformat(d['date']).toordinal() if d['date'] else 0,d['entry_id']))
    return {'case':case,'as_of':as_of,'documents':documents,'gaps':gaps,
            'controlling_judgment_established':False,'coverage_complete':False,
            'paid_calls_authorized':False,'full_ocs_entries':len(entries),
            'next_step':'Acquire accessible controlling-order and latest judgment candidates; validate bodies before paid amount reading.'}
