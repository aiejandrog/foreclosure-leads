"""miami_present_title: who holds this parcel now, and which recorded claims might bind it. $0.

Priority 6 of the Miami automation goal. Title discovery already collects the deed chain and the
name searches; what was missing is one place that says, for the parcel today:

  OWNERSHIP  the current deed candidate, its status ('candidate', 'possibly_conveyed_later',
             'none'), the chain-of-title links behind it, the deeds placed on the parcel by the
             clerk index's legal description, and the deeds that still need a person to match one. Always a candidate; the status field of the report stays 'unknown'.
  ENTITIES   for an entity grantee, what Sunbiz says (sunbiz_entities), with no title or contact
             authority attached. An entity-only owner is never call-ready on this evidence.
  CLAIMS     every recorded money claim or encumbrance the searches returned, each with:
               parcel      'folio_matched' | 'subdivision_only' | 'name_search_only'
               party       whose name it was found under, relative to the current deed:
                           'current_grantee' | 'prior_title_party' | 'defendant_not_on_title' |
                           'lead_owner_not_on_title' | 'other_name'
               debt        always 'not_established'. A recorded instrument is not a balance, a
                           judgment found under a name is not a lien on this parcel, and no
                           satisfaction found is not proof the debt is open.
               satisfaction the reconciled status with 'no_satisfaction_found_not_proof_open' in
                           place of a bare 'unknown'.
             A claim under a HISTORICAL grantee's name is kept and labelled as such, never counted
             against the current owner.

No network, no spending, no writes: the caller passes the discovery report and, optionally, the
Sunbiz records it already resolved.
"""
from miami_title_parties import name_key
import sunbiz_entities as SE


def _names(parties, role):
    return [p['name'] for p in parties or [] if p.get('role') == role]


def _relation(name, current_keys, prior_keys, defendant_keys, owner_key):
    key = name_key(name)
    if key in current_keys:
        return 'current_grantee'
    if key in prior_keys:
        return 'prior_title_party'
    if key in defendant_keys:
        return 'defendant_not_on_title'
    if owner_key and key == owner_key:
        return 'lead_owner_not_on_title'
    return 'other_name'


def _satisfaction(status):
    return {'unknown': 'no_satisfaction_found_not_proof_open', None: 'no_satisfaction_found_not_proof_open',
            '': 'no_satisfaction_found_not_proof_open'}.get(status, status)


def present_title(report, entities=None):
    title = report.get('title_parties') or {}
    current = title.get('current_deed_candidate') or {}
    grantees = _names(current.get('parties'), 'grantee')
    current_keys = {name_key(n) for n in grantees}
    prior_keys = {name_key(p['name']) for p in title.get('title_parties') or []} - current_keys
    defendant_keys = {name_key(d['name']) for d in title.get('defendants') or []}
    owner_key = name_key(report.get('owner') or '')
    unanchored = title.get('unanchored_deeds') or []
    ownership = {
        'status': title.get('current_deed_status') or ('candidate' if current else 'none'),
        'deed': current.get('book_page'), 'recorded': current.get('date'),
        'grantees': grantees,
        'chain_of_title': title.get('chain_of_title') or [],
        'possible_later_conveyances': title.get('possible_later_conveyances') or [],
        'legal_description_match_required': [d['book_page'] for d in unanchored
                                             if d.get('status') == 'legal_description_match_required'],
        'folio_conflicts_kept': [d['book_page'] for d in unanchored if d.get('status') == 'folio_conflict'],
        # Placed by the clerk index's lot/block/plat or condo unit rather than a folio: matched ones
        # joined the deed chain above; differing ones describe another parcel and are kept visible.
        'legal_description_matched': title.get('legal_matched_deeds') or [],
        'legal_description_differs': [d['book_page'] for d in unanchored
                                      if d.get('status') == 'legal_description_differs'],
        'anchored_by': current.get('anchored_by') or ('folio' if current else None),
        # A deed placed by a legal description only one index row states is the weakest ownership
        # this module will report. It says so here, not only in the report's gap lines.
        'legal_description_corroborated': (None if current.get('anchored_by') != 'legal_description'
                                           else bool((current.get('legal_match') or {}).get('corroborated'))),
        'basis': (('Newest deed placed on this parcel by the clerk index legal description (no '
                   'folio on the deed) in what was searched and stored. '
                   if current.get('anchored_by') == 'legal_description' else
                   'Newest folio-anchored deed in what was searched and stored. ') +
                  'Deed inventory completeness is unverified; this is a candidate, not a title finding.'),
    }
    records = {SE._key(e.get('name')): e for e in entities or []}
    owner_entities = []
    for name in grantees:
        kind = SE.kind_of(name)
        if kind == 'person':
            continue
        record = records.get(SE._key(name)) or {'name': name, 'kind': kind, 'lookup': 'not_run',
                                                 'title_authority': 'not_established',
                                                 'contact_authority': 'not_established',
                                                 'call_ready': False}
        owner_entities.append(dict(record, people_to_research=SE.people_to_research(record)))
    persons = [n for n in grantees if SE.kind_of(n) == 'person']
    claims = []
    for search in report.get('other_name_searches') or []:
        tagged = []
        for row in search.get('parcel_candidates') or []:
            tagged.append(('folio_matched', row))
        for row in search.get('uncertain_parcel_candidates') or []:
            tagged.append(('subdivision_only', row))
        for row in search.get('potential_title_party_claims') or []:
            tagged.append(('folio_matched' if row.get('parcel_status') == 'matched' else 'name_search_only', row))
        for parcel, row in tagged:
            claims.append({
                'book_page': '%s/%s' % (row.get('book'), row.get('page_no')),
                'doc_type': row.get('doc_type'), 'body_kind': row.get('body_kind'),
                'recorded': row.get('rec_date'), 'under_name': row.get('under_name'),
                'parcel': parcel,
                'party': _relation(row.get('under_name'), current_keys, prior_keys, defendant_keys, owner_key),
                'amount': row.get('amount'),
                'amount_status': row.get('amount_status') or row.get('amount_basis') or 'unknown',
                'satisfaction': _satisfaction(row.get('satisfaction_status')),
                'attachment': row.get('attachment_status') or 'unknown',
                'debt': 'not_established'})
    # One instrument found under two names, or as both a parcel row and a claim row, is one claim.
    merged = {}
    for claim in claims:
        key = claim['book_page']
        if key not in merged:
            merged[key] = dict(claim, under_names=[claim['under_name']])
            continue
        kept = merged[key]
        if claim['under_name'] not in kept['under_names']:
            kept['under_names'].append(claim['under_name'])
        rank = ('folio_matched', 'subdivision_only', 'name_search_only')
        if rank.index(claim['parcel']) < rank.index(kept['parcel']):
            kept['parcel'] = claim['parcel']
        order = ('current_grantee', 'lead_owner_not_on_title', 'defendant_not_on_title',
                 'prior_title_party', 'other_name')
        if order.index(claim['party']) < order.index(kept['party']):
            kept['party'] = claim['party']
        for field in ('amount', 'body_kind'):
            kept[field] = kept.get(field) or claim.get(field)
    # A hit under a name that is nobody on this title (a lender, HUD, a finance company) and not
    # tied to the parcel is a search artifact, not a claim: 2023-020247 listed 131 such rows from
    # creditor-name searches capped at 500 each (verify-12 defect 6). They are counted, by name,
    # and never listed as claims. A folio or subdivision match keeps a row whatever the name.
    unlisted = {}
    for claim in list(merged.values()):
        if claim['parcel'] == 'name_search_only' and claim['party'] == 'other_name':
            del merged[claim['book_page']]
            for name in claim['under_names']:
                unlisted[name] = unlisted.get(name, 0) + 1
    claims = sorted(merged.values(), key=lambda c: (c['parcel'] != 'folio_matched', c['book_page']))
    for claim in claims:
        claim.pop('under_name', None)
    held = []
    if ownership['status'] != 'candidate':
        held.append('ownership: ' + ownership['status'])
    if not persons and owner_entities:
        held.append('owner is an entity; no person is established as able to act for it')
    if ownership['legal_description_corroborated'] is False:
        held.append('the current deed is placed on this parcel by a legal description only one '
                    'record filed under the folio states')
    if ownership['legal_description_match_required']:
        held.append('%d deed(s) without a folio need legal-description matching'
                    % len(ownership['legal_description_match_required']))
    return {'ownership': ownership, 'owner_entities': owner_entities, 'owner_persons': persons,
            'claims': claims,
            'counts': {'claims': len(claims),
                       'folio_matched': sum(c['parcel'] == 'folio_matched' for c in claims),
                       'name_search_only': sum(c['parcel'] == 'name_search_only' for c in claims),
                       'under_prior_title_party': sum(c['party'] == 'prior_title_party' for c in claims),
                       'unlisted_other_name_hits': sum(unlisted.values())},
            'unlisted_other_name_hits': dict(sorted(unlisted.items())),
            'open_debt': 'not_established',
            'call_ready': False,
            'held_because': held,
            'qualification': ('Nothing here establishes a balance owed, a lien on this parcel, or who may '
                              'sign for the owner. Folio-matched instruments are recorded against the '
                              'parcel; name-search results are leads under a name. call_ready is '
                              'decided by the ranking step with fresh checks, never by this record.')}
