"""Offline docket-first acquisition order. Ranking never establishes legal effect."""
from datetime import date, timedelta
import re
from miami_case_timeline import classify, _date, docket_defendants, reconcile_judgments, scope_of

CRITICAL={'vacatur','satisfaction','notice_of_voluntary_dismissal','order_of_dismissal',
          'suggestion_of_bankruptcy','relief_from_stay','order_cancelling_sale','order_resetting_sale',
          'stay_reinstated','bankruptcy_dismissed'}


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
    # What the docket index alone says each final judgment's status is. Bodies still decide.
    defendants=docket_defendants(inventory)
    judgments=reconcile_judgments(
        [dict(d,operative_text=d['description'],
              limited_scope=scope_of(d['kind'],d['description']+' '+d['comments'],defendants)[0],
              dismissed_parties=scope_of(d['kind'],d['description']+' '+d['comments'],defendants)[1])
         for d in sorted(documents,key=lambda d:(d['date'] or '9999',d['entry_id']))],
        as_of)
    documents.sort(key=lambda d:(not d['eligible_for_acquisition'],d['priority'],
        -date.fromisoformat(d['date']).toordinal() if d['date'] else 0,d['entry_id']))
    return {'case':case,'as_of':as_of,'documents':documents,'gaps':gaps,'judgments':judgments,
            'controlling_judgment_established':False,'coverage_complete':False,
            'paid_calls_authorized':False,'full_ocs_entries':len(entries),
            'next_step':'Acquire accessible controlling-order and latest judgment candidates; validate bodies before paid amount reading.'}


# ---- PAID-READ ORDER ---------------------------------------------------------------------------
# prioritize() above orders what to ACQUIRE from the docket. The functions below order what to PAY
# to read, and they are the fix for the 2026-09-23 five-case pilot: the reader walked a case's
# recorded documents in the order the owner-NAME search returned them and spent the approval on
# historical and other-lawsuit judgments before it reached the current one. Selection now happens
# before any paid call, from facts that are already free: the docket's own judgment entry dates,
# the case number printed on the page (read by free OCR/text), the recording date in the county
# index, and the year the case number itself says the case was filed.
#
# What this does NOT do: decide which judgment controls. A later judgment can be vacated, an
# amended one can replace an earlier one only in part. Order is an acquisition priority; operative
# effect is reconciled from the documents, and `later_orders` names the docket orders a reader has
# to look at before calling any judgment current.

# A recording lags its docket entry; an amended judgment is recorded after the entry it amends.
RECORDING_WINDOW_BEFORE = timedelta(days=3)
RECORDING_WINDOW_AFTER = timedelta(days=120)

_SATISFACTION_TYPE = re.compile(r'^(SATISF|REL(EASE)?\b|PARTIAL\s+REL)', re.I)


def _record_date(value):
    stamp = _date(str(value or '').strip()[:10])
    return date.fromisoformat(stamp) if stamp else None


def _case_year_start(case):
    match = re.match(r'(\d{4})-', case or '')
    return date(int(match.group(1)), 1, 1) if match else None


def docket_judgment_dates(plan):
    """Dates of the docket's own final-judgment entries (amended ones included), newest first."""
    out = []
    for doc in (plan or {}).get('documents') or []:
        if doc.get('kind') == 'final_judgment' and doc.get('date') and \
                'future_entry_not_current_evidence' not in (doc.get('gaps') or []):
            out.append(date.fromisoformat(doc['date']))
    return sorted(set(out), reverse=True)


def later_orders(plan, when):
    """Docket orders dated on/after a judgment that could change it: a reader must review these
    before calling that judgment current. Listing them is not a finding that they apply."""
    out = []
    for doc in (plan or {}).get('documents') or []:
        if doc.get('kind') in CRITICAL and doc.get('date') and \
                date.fromisoformat(doc['date']) >= when:
            out.append({'entry_id': doc['entry_id'], 'kind': doc['kind'], 'date': doc['date']})
    return out


_SPENT_STATUSES = ('superseded', 'vacated', 'satisfied')


def _docket_status(plan, matched):
    """The reconciled status of the docket judgment(s) dated `matched`, or None."""
    if matched is None:
        return None
    found = {j['status'] for j in ((plan or {}).get('judgments') or {}).get('judgments') or []
             if j.get('date') == matched.isoformat() and j.get('role') != 'supplemental'}
    return found.pop() if len(found) == 1 else ('unclear' if found else None)


def _is_satisfaction(row):
    kind = str((row.get('classification') or {}).get('kind') or '')
    return kind.startswith('satisfaction') or bool(
        _SATISFACTION_TYPE.match(str(row.get('doc_type') or '').strip()))


def recorded_read_order(case, rows, records=(), plan=None):
    """Order stored Official Records rows for PAID reading, before any paid call.

    Tiers, cheapest-to-justify first:
      0  the page prints THIS case number
      1  prints no case number, and its recording date lines up with a docket judgment entry
    Never bought (listed in `deferred` with the reason, and still read for free):
      - a page that prints no case number and lines up with no docket judgment date: nothing ties
        it to this case (without a saved docket, that is every page without the case number)
      - a page that prints a DIFFERENT case number: another lawsuit against a shared party
      - anything recorded before the year this case number was filed in: it cannot be this
        case's judgment, whatever a name search matched it on
      - satisfactions and releases: their figures recite the debt they discharge
      - rows with no stored reading: there is nothing to price, and the fetch gap stands
    Within a tier, newest recording first; that is an acquisition order, not a verdict that the
    newest judgment controls.
    """
    import document_classify
    by_ref = {}
    for record in records or ():
        ref = 'official_records/%s-%s' % (record.get('reC_BOOK'), record.get('reC_PAGE'))
        by_ref.setdefault(ref, record)
    judgments = docket_judgment_dates(plan)
    year_start = _case_year_start(case)
    ranked, deferred = [], []
    for row in rows:
        ref = row.get('source_ref')
        if row.get('status') != 'stored' or not row.get('reading'):
            deferred.append({'source_ref': ref, 'reason': 'no_stored_reading'})
            continue
        if _is_satisfaction(row):
            deferred.append({'source_ref': ref, 'reason': 'satisfaction_read_free_only'})
            continue
        identity = document_classify.case_identity(row['reading'], case)
        if identity['agrees'] is False:
            deferred.append({'source_ref': ref, 'reason': 'other_action',
                             'detail': 'prints %s' % ', '.join(identity['found'][:3])})
            continue
        recorded = _record_date((by_ref.get(ref) or {}).get('reC_DATE') or row.get('recorded_date'))
        if recorded and year_start and recorded < year_start:
            deferred.append({'source_ref': ref, 'reason': 'recorded_before_case_year',
                             'detail': 'recorded %s; case %s was filed no earlier than %s'
                                       % (recorded.isoformat(), case, year_start.isoformat())})
            continue
        matched = None
        if recorded:
            matched = next((d for d in judgments if d - RECORDING_WINDOW_BEFORE <= recorded
                            <= d + RECORDING_WINDOW_AFTER), None)
        tier = 0 if identity['agrees'] is True else 1 if matched else 2
        if tier == 2:
            # Nothing ties it to this case: a name-search judgment against a shared party, most
            # often an old one. The 2026-09-24 desktop replay showed four of five pilot cases
            # would still have bought one of these first, because none of their own judgments
            # was in the recorded store. Its figure is not this case's amount; read it free.
            deferred.append({'source_ref': ref, 'reason': 'not_tied_to_this_case_read_free_only',
                             'detail': 'prints no case number and was recorded %s, not within the '
                                       'window of any docket judgment date (%s)'
                                       % (recorded.isoformat() if recorded else 'on an unknown date',
                                          ', '.join(d.isoformat() for d in judgments) or 'none known')})
            continue
        status = _docket_status(plan, matched)
        ranked.append({'row': row, 'source_ref': ref, 'tier': tier,
                       'recorded': recorded.isoformat() if recorded else None,
                       'docket_judgment_date': matched.isoformat() if matched else None,
                       'docket_judgment_status': status,
                       'later_orders': later_orders(plan, matched) if matched else []})
    # Within a tier, a recording that lines up with a judgment the docket shows superseded,
    # vacated or satisfied is bought after one that lines up with an operative judgment. It is
    # still bought when the share allows: the docket index is not the judgment's body.
    ranked.sort(key=lambda r: (r['tier'], r['docket_judgment_status'] in _SPENT_STATUSES,
                               -(date.fromisoformat(r['recorded']).toordinal()
                                 if r['recorded'] else 0), str(r['source_ref'])))
    return {'order': ranked, 'deferred': deferred,
            'docket_judgment_dates': [d.isoformat() for d in judgments],
            'basis': 'docket judgment dates, printed case number, recording date, case year; '
                     'selection is not a finding that any judgment controls'}


def timeline_read_order(plan, rows):
    """Order whole-case timeline rows ('court:<entry>:...') for paid amount reading by the docket
    plan: potentially controlling orders, then judgments newest first. Entries the plan holds as
    gaps (undated, future, no image count) are deferred with the plan's own reasons."""
    position = {d['entry_id']: (i, d) for i, d in enumerate((plan or {}).get('documents') or [])}
    ranked, deferred = [], []
    for row in rows:
        entry = str(row.get('entry_ref') or '')
        found = position.get(entry)
        if found is None:
            deferred.append({'source_ref': row.get('source_ref'), 'reason': 'not_in_docket_plan'})
            continue
        index, doc = found
        if not doc['eligible_for_acquisition']:
            deferred.append({'source_ref': row.get('source_ref'),
                             'reason': ','.join(doc['gaps']) or 'not_eligible'})
            continue
        ranked.append((index, str(row.get('source_ref')), row))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return {'order': [r[2] for r in ranked], 'deferred': deferred}
