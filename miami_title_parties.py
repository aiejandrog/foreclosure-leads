"""Conservative, offline Miami deed-party inventory. Never adjudicates title or death.

Index parties are search leads, not proof that an index listed every grantee. Latest
known deed is a candidate only: missing deeds, OCR and docket pagination remain gaps.
"""
import re
from datetime import datetime


def name_key(value):
    """Comparison only; do not discard LLC/trust/suffix identity information."""
    return re.sub(r'[^A-Z0-9]+', ' ', str(value or '').upper()).strip()


def _folio(value):
    return re.sub(r'\D', '', str(value or '')).lstrip('0')


def _date(value):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%Y %I:%M:%S %p'):
        try:
            return datetime.strptime(str(value or ''), fmt)
        except ValueError:
            pass
    return None


def _names(value):
    # Never split commas or AND: both occur inside valid corporate/trust names.
    if isinstance(value, list):
        return [str(n).strip() for n in value if str(n).strip()]
    return [n.strip(' .') for n in re.split(r'[;\n]+', str(value or '')) if n.strip(' .')]


def build_title_parties(models, documents, docket, folio):
    """Return named evidence and explicit unknowns from recordingModel/read-row inputs.

    `documents` accepts the existing {'source_ref','stored','reading'} rows. No file,
    network, AI, title resolution or equity side effects. Comparisons are flags only.
    """
    gaps = ['Recorded deed inventory and OCS party pagination completeness are unverified; present title remains unknown.']
    docs = {d.get('source_ref'): d for d in documents or [] if isinstance(d, dict)}
    deeds, flags = [], []
    for model in models or []:
        label = str(model.get('doC_TYPE') or '')
        if not re.search(r'\b(DEED|CERTIFICATE OF TITLE)\b', label, re.I):
            continue
        if re.search(r'TAX|MORTGAGE|TRUST DEED', label, re.I):
            continue
        book_page = '%s/%s' % (model.get('reC_BOOK'), model.get('reC_PAGE'))
        ref = 'official_records/%s-%s' % (model.get('reC_BOOK'), model.get('reC_PAGE'))
        doc = docs.get(ref) or docs.get('recorded:%s' % model.get('cfN_MASTER_ID')) or {}
        pages = (doc.get('reading') or {}).get('pages') or []
        own_folio = _folio(model.get('foliO_NUMBER'))
        target = _folio(folio)
        printed = { _folio(m.group(1)) for p in pages for m in re.finditer(
            r'\b(?:folio|parcel(?:\s+(?:id|identification))?)\s*(?:number|no\.?|#)?\s*:?\s*([\d-]{10,20})',
            p.get('text') or '', re.I)}
        anchored = bool(target and ((own_folio == target) or (not own_folio and printed == {target})))
        if not anchored:
            gaps.append('%s: deed parcel anchor missing or conflicts; subdivision is insufficient.' % ref)
            continue
        parties = []
        for role, field in (('grantor','firsT_PARTY'), ('grantee','seconD_PARTY')):
            for name in _names(model.get(field)):
                parties.append({'name':name, 'role':role, 'evidence':{
                    'source_ref':ref,'book_page':book_page,'page':None,
                    'document_hash':None,'basis':'recording_index','passage':str(model.get(field))}})
        for page in pages:
            if page.get('outcome') not in ('text','ocr_text','vision_text','read_as_label'):
                continue
            for line in (page.get('text') or '').splitlines():
                match = re.match(r'^\s*(grantors?|grantees?)\s*:\s*(.+?)\s*$', line, re.I)
                if match:
                    for name in _names(match.group(2)):
                        parties.append({'name':name,'role':'grantor' if match.group(1).lower().startswith('grantor') else 'grantee',
                                        'evidence':{'source_ref':ref,'book_page':book_page,
                                                    'page':page.get('page'),'document_hash':(doc.get('stored') or {}).get('source_sha256'),
                                                    'basis':'explicit_document_role','passage':line}})
            # Deliberately narrow prose grammar: addresses/qualifications embedded before
            # a role are not silently treated as a person's name. Other forms stay gaps.
            prose = re.sub(r'\s+', ' ', page.get('text') or '')
            match = re.search(
                r'\bbetween\s+([^,;.]{2,180}),\s*hereinafter\s+(?:called|referred to as)\s+(?:the\s+)?grantors?,?\s+and\s+'
                r'([^,;.]{2,180}),\s*hereinafter\s+(?:called|referred to as)\s+(?:the\s+)?grantees?\b', prose, re.I)
            if not match:
                # Statutory deed form: extract only text before the explicit capacity
                # clause, not its address or a notary/signatory's name. Bounds prevent
                # a stray reference to grantor from consuming the rest of a document.
                capacity = r'(?:a|an)\s+(?:single\s+(?:man|woman|person)|married\s+(?:man|woman|person)|(?:Florida\s+)?(?:corporation|limited liability company))'
                match = re.search(
                    r'\bbetween\s+([^,;]{2,180}),\s*' + capacity +
                    r'\b.{0,600}?\bGrantor\s*,\s*to\s+([^,;]{2,180}),\s*' + capacity +
                    r'\b.{0,600}?\bGrantee\s*:', prose, re.I)
            if match:
                for role, name in zip(('grantor','grantee'), match.groups()):
                    parties.append({'name':name.strip(),'role':role,'evidence':{
                        'source_ref':ref,'book_page':book_page,'page':page.get('page'),
                        'document_hash':(doc.get('stored') or {}).get('source_sha256'),
                        'basis':'explicit_document_role','passage':match.group(0)}})
        gaps.append('%s: index names and bounded explicit-role extraction do not establish that every deed party was recovered.' % ref)
        deeds.append({'book_page':book_page,'source_ref':ref,'date':model.get('reC_DATE'),
                      'parties':parties,'date_parsed':_date(model.get('reC_DATE'))})
    # An undated anchored conveyance prevents selecting a newest deed.
    ordered = sorted(deeds, key=lambda d:d['date_parsed'] or datetime.min, reverse=True)
    current = ordered[0] if ordered and all(d['date_parsed'] for d in ordered) else None
    if len(ordered)>1 and ordered[0]['date_parsed']==ordered[1]['date_parsed']:
        current = None
        gaps.append('Same-date deeds need document chronology reconciliation; no current deed selected.')
    previous = ordered[1] if current and len(ordered)>1 else None
    if not previous:
        gaps.append('Preceding vesting deed is unknown.')
    selected = ordered[:2] if current else ordered
    title_parties = [dict(p, deed_role='current_candidate' if d is current else 'prior_candidate')
                     for d in selected for p in d['parties']]
    defendants = []
    for i, party in enumerate((docket or {}).get('parties') or []):
        if not isinstance(party, dict):
            continue
        role = str(party.get('partyTypeDesc') or party.get('partyType') or '')
        if 'DEFENDANT' in role.upper() and party.get('partyName'):
            defendants.append({'name':party['partyName'],'source_ref':'dockets/parties/%s'%i})
    owners = [p for p in (current or {}).get('parties',[]) if p['role']=='grantee']
    owner_keys = {name_key(p['name']) for p in owners}
    defendant_keys = {name_key(p['name']) for p in defendants}
    # Flags quote text, never infer that a person has died (especially if deceased).
    sources = [(d.get('source_ref'),p.get('page'),p.get('text') or '')
               for d in documents or [] for p in (d.get('reading') or {}).get('pages',[])]
    sources += [('dockets/parties/%s'%i,None,str(p.get('partyName') or ''))
                for i,p in enumerate((docket or {}).get('parties') or []) if isinstance(p,dict)]
    for ref, page, text in sources:
        for line in text.splitlines():
            # "estate" also means an interest in property, not a decedent's estate.
            signal_text = re.sub(r'\b(?:claim|right|interest|title)(?:\s*,?\s*(?:or|and)\s+)estate\s+of\b',
                                 'property interest of', line, flags=re.I)
            if re.search(r'\b(death certificate|probate|estate of|heirs of|deceased)\b', signal_text, re.I):
                flags.append({'source_ref':ref,'page':page,'passage':line,
                              'conditional':bool(re.search(r'\bif\s+(?:\w+\s+){0,3}deceased\b',line,re.I)),
                              'death_established':False,'status':'identity_sensitive_unresolved'})
    search_names = []
    seen = set()
    for p in title_parties:
        key = name_key(p['name'])
        if key and key not in seen:
            seen.add(key)
            search_names.append({'name':p['name'],'why':'anchored deed '+p['role'],
                                 'source_ref':p['evidence']['source_ref']})
    def public(d):
        return {k:v for k,v in d.items() if k!='date_parsed'} if d else None
    return {'status':'unknown','current_deed_candidate':public(current),
            'previous_deed_candidate':public(previous),'title_parties':title_parties,
            'search_names':search_names,'defendants':defendants,
            'owners_not_named':[p for p in owners if name_key(p['name']) not in defendant_keys],
            'defendants_not_on_title':[p for p in defendants if owner_keys and name_key(p['name']) not in owner_keys],
            'comparison_status':'candidate_flags_only' if owner_keys and defendants else 'unknown',
            'identity_flags':flags,'gaps':gaps}
