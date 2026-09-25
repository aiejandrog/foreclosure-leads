"""Conservative, offline Miami deed-party inventory. Never adjudicates title or death.

Index parties are search leads, not proof that an index listed every grantee. Latest
known deed is a candidate only: missing deeds, OCR and docket pagination remain gaps.
"""
import re
from datetime import datetime


def name_key(value):
    """Comparison only; do not discard LLC/trust/suffix identity information."""
    value = str(value or '').upper().strip()
    entity = re.search(r'\b(LLC|L\.?L\.?C|LLP|LP|INC|CORP|CORPORATION|COMPANY|CO|TRUST|TRUSTEE|BANK|ASSOCIATION|ESTATE|HEIRS)\b', value)
    # Only an explicit recorder-style comma licenses reordering. Never sort all
    # tokens: that would collapse different people and entity trade names.
    if not entity and value.count(',') == 1:
        surname, given = [part.strip() for part in value.split(',')]
        suffix = re.search(r'\s+(JR\.?|SR\.?|II|III|IV)$', given)
        suffix_text = (' ' + suffix.group(1)) if suffix else ''
        if suffix:
            given = given[:suffix.start()].strip()
        if surname and given and not re.fullmatch(r'JR\.?|SR\.?|II|III|IV', given):
            value = given + ' ' + surname + suffix_text
    return re.sub(r'[^A-Z0-9]+', ' ', value).strip()


def _folio(value):
    return re.sub(r'\D', '', str(value or '')).lstrip('0')


def _date(value):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%Y %I:%M:%S %p'):
        try:
            return datetime.strptime(str(value or ''), fmt)
        except ValueError:
            pass
    # The clerk also emits a trailing time slice ('2/1/2002 1'), which records_liens._parse_recd
    # documents. Without this the deed reads as undated, and an undated deed suppresses the
    # parcel's current deed candidate entirely.
    head = str(value or '').split(' ')[0]
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(head, fmt)
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
    deeds, unanchored, flags = [], [], []
    reference, reference_gap = parcel_legal_reference(models, folio)
    candidates = []
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
        row = {'book_page':book_page,'source_ref':ref,'date':model.get('reC_DATE'),'doc_type':label,
               'parties':_deed_parties(model, doc, ref, book_page, pages),
               'date_parsed':_date(model.get('reC_DATE'))}
        if anchored:
            gaps.append('%s: index names and bounded explicit-role extraction do not establish that every deed party was recovered.' % ref)
            deeds.append(row)
            continue
        # A deed with no folio is not an irrelevant deed: condo units, older instruments and
        # clerk certificates are often indexed without one. Keep it, with its parties and
        # whatever legal description it prints, for legal-description matching. It never
        # becomes the current deed on its own. A deed whose own folio names another parcel is
        # kept too, marked as such, so the omission is visible rather than silent.
        conflict = bool(own_folio and own_folio != target) or bool(printed - {target, ''})
        candidates.append((model, row, printed, own_folio, conflict, pages))
    # Second pass, because whether a legal-matched deed may join the chain depends on the dates
    # already in it: an undated deed, or two deeds on one day, stop a current deed being chosen
    # for the parcel at all, and a deed the index placed here never costs that much.
    # A recording date may carry a time, so days are compared as days: two deeds on one day are
    # ambiguous whether or not the index timed one of them.
    def day(value):
        return value.date() if value else None
    taken = {day(d['date_parsed']) for d in deeds}
    matched_days = {}
    for model, row, _printed, _own, conflict, _pages in candidates:
        # A deed whose folio names another parcel can never be placed here, so it does not make
        # a day ambiguous for one that can.
        if not conflict and compare_legal(reference, index_legal(model), reference_gap)['verdict'] == 'matched':
            matched_days[day(row['date_parsed'])] = matched_days.get(day(row['date_parsed']), 0) + 1
    for model, row, printed, own_folio, conflict, pages in candidates:
        ref = row['source_ref']
        verdict = None
        if not conflict:
            verdict = compare_legal(reference, index_legal(model), reference_gap)
            if verdict['verdict'] == 'matched' and not row['date_parsed']:
                verdict = dict(verdict, verdict='needs_person', basis=None,
                               reason='its legal description matches the parcel but the index '
                                      'gives it no readable recording date')
            elif verdict['verdict'] == 'matched' and (day(row['date_parsed']) in taken
                                                      or matched_days[day(row['date_parsed'])] > 1):
                # Whichever of two same-day deeds comes first in the county's answer must not
                # decide who the board calls the owner, so neither is placed.
                verdict = dict(verdict, verdict='needs_person', basis=None,
                               reason='its legal description matches the parcel but another deed '
                                      'on the parcel is recorded the same day')
            if verdict['verdict'] == 'matched':
                # The clerk's own index puts this deed on the same lot/block/plat (or condo
                # unit) as the records it filed under this parcel's folio. That is the check a
                # person did by hand; anything short of an exact match still goes to one.
                gaps.append('%s: anchored by the clerk index legal description (%s), not by '
                            'folio; index names and bounded explicit-role extraction do not '
                            'establish that every deed party was recovered.' % (ref, verdict['basis']))
                taken.add(day(row['date_parsed']))
                deeds.append(dict(row, anchored_by='legal_description', legal_match=verdict))
                continue
        gaps.append('%s: deed parcel anchor missing or conflicts; subdivision is insufficient.' % ref)
        unanchored.append(dict(row,
                               status=('folio_conflict' if conflict else
                                       'legal_description_differs' if verdict['verdict'] == 'differs' else
                                       'legal_description_match_required'),
                               legal_match=verdict,
                               index_folio=own_folio or None,
                               printed_folios=sorted(f for f in printed if f),
                               subdivision=str(model.get('subdiV_NAME') or '').strip() or None,
                               legal_description=_legal_description(pages),
                               read=bool(pages)))
    return _assemble(deeds, unanchored, gaps, flags, documents, docket)


def _deed_parties(model, doc, ref, book_page, pages):
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
    return parties


def _assemble(deeds, unanchored, gaps, flags, documents, docket):
    # An undated anchored conveyance prevents selecting a newest deed.
    ordered = sorted(deeds, key=lambda d:d['date_parsed'] or datetime.min, reverse=True)
    current = ordered[0] if ordered and all(d['date_parsed'] for d in ordered) else None
    # The index times some recordings and not others, so days are compared as days.
    if current and len(ordered)>1 and ordered[0]['date_parsed'].date()==ordered[1]['date_parsed'].date():
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
    chain = chain_of_title(ordered)
    for link in chain:
        if link['link'] == 'names_differ':
            gaps.append('Chain of title: %s grantor(s) %s do not match %s grantee(s) %s; a missing '
                        'conveyance, a name change or a spelling needs a person to check.'
                        % (link['to'], link['to_grantors'], link['from'], link['from_grantees']))
    anchored_roles = {}
    for d in ordered:
        for p in d['parties']:
            anchored_roles.setdefault(name_key(p['name']), set()).add(p['role'])
    later = []
    for d in unanchored:
        d['chain_link'] = sorted({'%s is an anchored-deed %s' % (p['name'], role)
                                  for p in d['parties'] for role in anchored_roles.get(name_key(p['name']), ())})
        grantors = {name_key(p['name']) for p in d['parties'] if p['role'] == 'grantor'}
        # Same day counts: the index times some recordings and not others, so a conveyance
        # recorded the day the current deed was cannot be ordered against it and is a question.
        # A deed the index puts on another parcel is listed too: its own index row is one
        # keystroke, and nothing corroborates it, so it is a question like the rest.
        if (current and d['status'] in ('legal_description_match_required', 'legal_description_differs')
                and grantors & owner_keys
                and (d['date_parsed'] is None
                     or d['date_parsed'].date() >= current['date_parsed'].date())):
            later.append(d['book_page'])
    if later:
        gaps.append('Deed(s) %s have no folio for this parcel, name the current grantee as grantor and '
                    'are dated on or after (or undated against) the current deed candidate: the owner may '
                    'already have conveyed. Match their legal descriptions before treating the '
                    'current candidate as the owner.' % ', '.join(later))
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
            'legal_matched_deeds':[d['book_page'] for d in ordered if d.get('anchored_by') == 'legal_description'],
            'current_deed_status':('none' if not current else
                                   'possibly_conveyed_later' if later else 'candidate'),
            'possible_later_conveyances':later,
            'chain_of_title':chain,
            'unanchored_deeds':[public(d) for d in unanchored],
            'previous_deed_candidate':public(previous),'title_parties':title_parties,
            'search_names':search_names,'defendants':defendants,
            'owners_not_named':[p for p in owners if name_key(p['name']) not in defendant_keys],
            'defendants_not_on_title':[p for p in defendants if owner_keys and name_key(p['name']) not in owner_keys],
            'comparison_status':('unknown_possible_later_conveyance' if later else
                                 'candidate_flags_only' if owner_keys and defendants else 'unknown'),
            'identity_flags':flags,'gaps':gaps}


_LEGAL_RES = (
    re.compile(r'\bLOTS?\s+[\dA-Z-]+(?:\s*(?:,|AND|&)\s*[\dA-Z-]+)*\s*,?\s*(?:OF\s+)?BLOCK\s+[\dA-Z-]+[^\n]{0,200}', re.I),
    re.compile(r'\bUNIT\s+(?:NO\.?\s*)?[\w-]+\s*,?\s*(?:OF\s+)?[^\n]{0,120}?CONDOMINIUM[^\n]{0,160}', re.I),
    re.compile(r'\blegal\s+description\s*:?\s*([^\n]{10,260})', re.I),
)


def _legal_description(pages):
    """The first legal-description passage a read deed prints, quoted, for a person or a later
    matcher to compare against the parcel. None when nothing was read or nothing matched."""
    for page in pages or []:
        text = page.get('text') or ''
        for pattern in _LEGAL_RES:
            match = pattern.search(text)
            if match:
                return {'page':page.get('page'), 'passage':re.sub(r'\s+', ' ', match.group(0)).strip()[:300]}
    return None


def _tokens(text):
    # A hyphen between two numbers is the index's other way of writing a range ('LOTS 38-40'), so
    # it becomes THRU rather than a space, which would read as two separate lots.
    text = re.sub(r'(?<=[0-9])\s*-\s*(?=[0-9])', ' THRU ', str(text or '').upper())
    return re.sub(r'[^A-Z0-9]+', ' ', text).strip()


def _lots(text):
    """'LOTS 38 THRU 40 LOTS 1 THRU 3' -> {'38','39','40','1','2','3'}. None when any piece is not a
    plain lot number (a part of a lot, a metes-and-bounds call), which only a person can compare.
    The index's punctuation is already gone by here, so runs and ranges are read token by token."""
    tokens = re.sub(r'\b(?:LOTS?|AND)\b', ' ', text).split()
    lots, i = set(), 0
    while i < len(tokens):
        token = tokens[i]
        if i + 2 < len(tokens) and tokens[i + 1] == 'THRU':
            low, high = token, tokens[i + 2]
            if not (low.isdigit() and high.isdigit()):
                return None
            if not 0 <= int(high) - int(low) <= 200:
                return None
            lots.update(str(n) for n in range(int(low), int(high) + 1))
            i += 3
            continue
        # 'LOT 24 A' is a PART of lot 24, not lots 24 and A: a bare letter is only a lot of its
        # own when it is the whole description ('LOT B'), never alongside another lot.
        if not re.fullmatch(r'\d+[A-Z]?', token) and not (re.fullmatch(r'[A-Z]', token) and len(tokens) == 1):
            return None
        lots.add(token.lstrip('0') or '0')
        i += 1
    return lots or None


def _plat(value):
    """'53/900', '053-0900' and '53 / 900' are one plat book and page. None when the index carries
    no plat (it writes '0/0'), or writes something that is not two numbers."""
    parts = [p for p in re.split(r'[^0-9A-Z]+', str(value or '').upper()) if p]
    if len(parts) != 2 or not all(p.strip('0') for p in parts):
        return None
    return '/'.join(p.lstrip('0') for p in parts)


def index_legal(model):
    """The clerk index's own structured legal for one recorded instrument: subdivision, the free
    legal field ('LOT 14', 'CONDO UNIT NO 107 BLDG 7', 'LOTS 3 & 4 SEE DOC'), block and plat
    book/page. None when the index carries none of them. 'SEE DOC' means the index shortened a
    legal that continues in the document, so the row is marked and never auto-matched."""
    sub = _tokens(model.get('subdiV_NAME'))
    desc = _tokens(model.get('legaL_DESCRIPTION'))
    block = re.sub(r'[^A-Z0-9]', '', str(model.get('blocK_NO') or '').upper()).lstrip('0')
    block = block or None      # '0' is the index's "no block", as '0/0' is its "no plat"
    plat = _plat(model.get('plaT_BOOKPAGE'))
    if not (sub or desc or block or plat):
        return None
    out = {'subdivision': sub or None, 'description': desc or None, 'block': block, 'plat': plat,
           'lots': None, 'unit': None, 'building': None, 'phase': None, 'tract': None,
           'see_document': bool(re.search(r'\bSEE DOC', desc)), 'unparsed': False}
    out['unparsed'] = not _parse_body(re.sub(r'\bSEE DOC\w*', ' ', desc), out) and bool(desc)
    return out


# A legal that carves a piece out of a lot or unit, or describes land by metes and bounds, is not
# a lot number and never compares as one. One of these words anywhere in the index legal sends the
# row to a person, whatever else it says.
_PART_RE = re.compile(r'\b(LESS|EXCEPT|EXC|PART|PT|OF|FT|AC|ACRES|BEG|COMM|MEAS|TWP|RGE|EXHIBIT|ATTACHED)\b')
# The tail after LOT / UNIT must be consumed WHOLE. Anything left over means the index is saying
# something more than a lot or unit number, and a truncated read would silently promote a partial
# conveyance into the deed chain.
_LOT_TAIL = re.compile(r'(?:LOTS?|THRU|AND|\d+[A-Z]?|[A-Z])(?:\s+(?:LOTS?|THRU|AND|\d+[A-Z]?|[A-Z]))*'
                       r'(?P<block_clause>\s+(?:BLK|BLOCK)\s+(?P<block>[A-Z0-9]+(?:\s+[A-Z0-9])?))?')
_UNIT_TAIL = re.compile(r'(?:NO\s+)?(?P<unit>[A-Z0-9]+(?:\s+[A-Z0-9]+)?)'
                        r'(?:\s+(?:BLDG|BUILDING)\s+(?:NO\s+)?(?P<building>[A-Z0-9]+))?'
                        r'(?:\s+(?:PH|PHASE)\s+(?P<phase>[A-Z0-9]+))?')
_TRACT_TAIL = re.compile(r'(?P<tract>[A-Z0-9]+)')


# What may stand before LOT / UNIT and still leave a plain lot or unit: the subdivision's own
# words. A digit, a direction or a share ('W2 LOT 14', 'N 1 2 LOT 14', 'UNDIVIDED 1 2 INT LOT 14')
# means the deed conveys a piece of it, so the row goes to a person.
_PREFIX_RE = re.compile(r'(?:[A-Z]{2,}\s+)*$')
_SHARE_RE = re.compile(r'\b(UNDIVIDED|INT|INTEREST|HALF|QUARTER|[NSEW]|NE|NW|SE|SW)\b')


def _parse_body(body, out):
    """Fill out['lots'|'unit'|'building'|'phase'|'tract'] from the index legal text. True when the
    text was read in full as one plain lot or unit; False leaves the row for a person."""
    if not body or _PART_RE.search(body):
        return False
    for anchor, tail_re in ((r'\bLOTS?\b', _LOT_TAIL), (r'\b(?:UNIT|PARCEL)\b', _UNIT_TAIL),
                            (r'\bTR(?:ACT)?\b', _TRACT_TAIL)):
        # Only the FIRST occurrence of each keyword: a second 'LOT' later in the text means the
        # legal says more than one thing, and reading from it would drop the rest.
        # 'WINSTON PARK UNIT THREE LOT 9' names a subdivision unit, not a condo unit, so LOT is
        # tried before UNIT; a tail that does not consume cleanly moves on to the next keyword.
        start = re.search(anchor, body)
        if not start:
            continue
        prefix = body[:start.start()]
        if not _PREFIX_RE.fullmatch(prefix) or _SHARE_RE.search(prefix):
            return False
        tail = tail_re.fullmatch(body[start.end():].strip())
        if not tail:
            continue
        found = tail.groupdict()
        if tail_re is _LOT_TAIL:
            out['lots'] = _lots('LOT ' + tail.group(0)[:tail.start('block_clause') - tail.start(0)
                                                       if found['block_clause'] else None])
            if out['lots'] is None:
                continue
            in_text = re.sub(r'\s', '', found['block'] or '').lstrip('0') or None
            if out['block'] and in_text and _num(out['block']) != _num(in_text):
                # The index says two different blocks for one instrument. Neither is safe to use.
                return False
            out['block'] = out['block'] or in_text
        else:
            unit = found.get('unit') or ''
            if tail_re is _UNIT_TAIL and not re.search(r'\d', unit) and not re.search(r'\bCONDO', body):
                # 'WINSTON PARK UNIT THREE' is a phase of a subdivision, not a condominium unit.
                # A lettered unit ('CONDO UNIT B BLDG 97') is real, and says CONDO.
                return False
            if re.fullmatch(r'\d+\s+\d+', found.get('unit') or ''):
                # 'UNIT 104 105' (the index writes '104 & 105', and the punctuation is gone by
                # here) is units 104 and 105, or unit 104105 with a stray space, and the index
                # does not say which. Either reading can rule a real conveyance out.
                return False
            out.update({k: v for k, v in found.items() if v})
        return True
    return False


def _kind(legal):
    return ('lot' if legal.get('lots') else 'unit' if legal.get('unit') else
            'tract' if legal.get('tract') else None)


def parcel_legal_reference(models, folio):
    """What the clerk index says this parcel's legal is, from the instruments it filed under the
    parcel's folio. Returns (reference, gap). No reference when none carries a complete legal, or
    when two of them disagree: the index is then not a safe yardstick and a person compares. A
    field one record leaves blank is not a disagreement with a record that fills it in."""
    target = _folio(folio)
    merged, records, stated = None, [], {}
    for model in models or []:
        if not target or _folio(model.get('foliO_NUMBER')) != target:
            continue
        legal = index_legal(model)
        if not legal or legal['see_document'] or legal['unparsed'] or not _kind(legal):
            continue
        book_page = '%s/%s' % (model.get('reC_BOOK'), model.get('reC_PAGE'))
        if book_page in records:          # the same instrument can come back twice in one search
            continue
        combined = dict(legal) if merged is None else _merge_legal(merged, legal)
        if combined is None:
            return None, ('records filed under this folio carry different index legal descriptions '
                          '(%s)' % ', '.join(sorted(records + [book_page])))
        merged = combined
        records.append(book_page)
        for field in _COMPARED + ('lots',):
            if legal[field]:
                stated[field] = stated.get(field, 0) + 1
    if merged is None:
        return None, 'no record filed under this folio carries a complete index legal description'
    # Corroborated means two records state the same thing, field by field. Two records that merge
    # because one is blank where the other is filled in leave that field resting on one keystroke,
    # and the blank fields here are plat and block: the ones a match turns on.
    thin = [f for f in _COMPARED + ('lots',) if merged[f] and stated.get(f, 0) < 2]
    return dict(merged, from_record=records[0], corroborated=not thin,
                uncorroborated=[{'lots': 'lot number'}.get(f, f) for f in thin]), None


_LEGAL_FIELDS = ('plat', 'block', 'unit', 'building', 'phase', 'tract', 'subdivision')
# What a verdict actually rests on. A subdivision name never places a deed (only a plat book and
# page does), so a record naming the subdivision the other leaves blank is not what corroboration
# is about, and requiring it would make the matcher a no-op on ordinary index data.
_COMPARED = ('plat', 'block', 'unit', 'building', 'phase', 'tract')


def _merge_legal(a, b):
    """One legal from two records of the same parcel, or None when they positively disagree. What
    one record states and the other leaves blank is filled in, not counted against it: the clerk
    writes '0/0' for no plat on one instrument and the real plat on the next."""
    if sorted(a['lots'] or ()) != sorted(b['lots'] or ()):
        return None
    if _kind(a) != _kind(b):
        # 'TRACT A' and 'UNIT 5' disagree in every way that matters, yet each leaves blank every
        # field the other fills in, so the field-by-field check alone would merge them.
        return None
    out = dict(a)
    for field in _LEGAL_FIELDS:
        if a[field] and b[field] and _num(a[field]) != _num(b[field]):
            # The subdivision name is not one of these: no verdict rests on it, and 'SEC 2'
            # against 'SECTION 2' would disable the matcher for the parcel over a spelling.
            if field in _COMPARED:
                return None
        out[field] = a[field] or b[field]
    return out


def _num(value):
    """Compare index values without their leading zeros and spacing: the clerk writes block 12 as
    '12' on one instrument and '012' on another, and a false 'differs' would drop a deed out of
    the later-conveyance warning entirely. Letters, order and spacing are kept: spacing is a
    question for a person ('UNIT 10 5' is not obviously unit 105), not a difference to rule on."""
    if value is None:
        return None
    return re.sub(r'(?<![0-9])0+(\d)', r'\1', str(value).upper())


def _same(a, b):
    """'exact' | 'spacing' (same characters, different spacing: a person decides) | 'differs'."""
    if a == b:
        return 'exact'
    return 'spacing' if str(a).replace(' ', '') == str(b).replace(' ', '') else 'differs'


def compare_legal(reference, legal, reference_gap=None):
    """Compare one unanchored deed's index legal with the parcel's. 'matched' only when the plat
    book/page (or, with no plat on either side, the subdivision name) agrees and the lots with the
    block, or the condo unit with its building and phase, agree exactly. 'differs' only on a
    positive disagreement. Everything else is 'needs_person', with the reason."""
    def result(verdict, why, basis=None):
        if verdict in ('differs', 'matched') and not (reference or {}).get('corroborated'):
            # One index record is one clerk keystroke, and nothing downstream gates on how a deed
            # was placed: its grantee is contacted like any owner, and a deed ruled out drops from
            # the warning that the owner may already have conveyed. Neither is worth one typo, so
            # a single record raises the question and a person answers it.
            thin = ', '.join((reference or {}).get('uncorroborated') or ()) or 'legal description'
            verdict, why = 'needs_person', ('only one record filed under this folio states this '
                                            'parcel\'s %s%s' % (thin, ': ' + why if why else
                                                                ', and it agrees: ' + (basis or '')))
            basis = None
        return {'verdict': verdict, 'reason': why, 'basis': basis,
                'reference_record': (reference or {}).get('from_record'),
                'deed_index_legal': ' / '.join(x for x in ((legal or {}).get('subdivision'),
                                                           (legal or {}).get('description'),
                                                           'BLK %s' % legal['block'] if (legal or {}).get('block') else None,
                                                           'PB %s' % legal['plat'] if (legal or {}).get('plat') else None) if x) or None}
    if not reference:
        return result('needs_person', reference_gap or 'no parcel legal description to compare against')
    if not legal:
        return result('needs_person', 'the index carries no legal description for this deed')
    if legal['see_document'] or legal['unparsed']:
        return result('needs_person', 'the index legal continues in the document (SEE DOC) or is not a plain lot or unit')
    if reference['plat'] and legal['plat']:
        if _num(reference['plat']) != _num(legal['plat']):
            return result('differs', 'plat book/page %s is not the parcel\'s %s' % (legal['plat'], reference['plat']))
        place = 'plat %s' % legal['plat']
    else:
        # Without a plat book and page on BOTH sides there is nothing that names one piece of
        # ground: Miami repeats subdivision names, and lot and block numbers repeat across plats,
        # so an agreeing name and lot can still be another parcel. Nor does a name rule one out:
        # 'SAMPLE GROVE SEC 2' and 'SAMPLE GROVE SECTION 2' are one subdivision typed two ways,
        # and calling that a difference drops the deed out of the conveyance warning. A person
        # compares.
        return result('needs_person', 'the plat book and page is missing on one side or both, and '
                                      'a subdivision name alone names more than one plat')
    kind, parcel_kind = _kind(legal), _kind(reference)
    if not kind:
        return result('needs_person', 'the index names no lot, unit or tract for this deed')
    if kind != parcel_kind:
        return result('needs_person', 'the deed describes a %s and the parcel a %s' % (kind, parcel_kind))
    if kind == 'lot':
        if _num(reference['block']) != _num(legal['block']):
            if reference['block'] and legal['block']:
                return result('differs', 'block %s is not the parcel\'s block %s' % (legal['block'], reference['block']))
            return result('needs_person', 'only one side names a block')
        if not legal['block']:
            # A plat has a lot 14 in every block, so a lot number with no block names no parcel.
            return result('needs_person', 'neither side names a block, so the lot number alone names no parcel')
        if legal['lots'] == reference['lots']:
            return result('matched', None, '%s, block %s, lot(s) %s' % (place, legal['block'],
                                                                         ', '.join(sorted(legal['lots']))))
        if legal['lots'] & reference['lots']:
            return result('needs_person', 'the deed\'s lots overlap the parcel\'s but are not the same lots')
        return result('differs', 'lot(s) %s are not the parcel\'s lot(s) %s'
                      % (', '.join(sorted(legal['lots'])), ', '.join(sorted(reference['lots']))))
    field = 'unit' if kind == 'unit' else 'tract'
    same = _same(_num(reference[field]), _num(legal[field]))
    if same == 'differs':
        return result('differs', '%s %s is not the parcel\'s %s %s' % (field, legal[field], field, reference[field]))
    if same == 'spacing':
        return result('needs_person', '%s %s and %s differ only in spacing' % (field, legal[field], reference[field]))
    for extra in ('building', 'phase'):
        if _num(reference[extra]) != _num(legal[extra]):
            if reference[extra] and legal[extra]:
                return result('differs', '%s %s is not the parcel\'s %s %s' % (extra, legal[extra], extra, reference[extra]))
            return result('needs_person', 'only one side names a %s' % extra)
    return result('matched', None, '%s, %s %s' % (place, field, legal[field]) +
                  ''.join(', %s %s' % (x, legal[x]) for x in ('building', 'phase') if legal[x]))


def chain_of_title(ordered):
    """Links between consecutive anchored deeds, oldest first: does each deed's grantor match the
    grantee of the deed before it? A mismatch is a question, never a finding: a name change, a
    trustee capacity, a spelling, or a conveyance missing from what we hold all look the same."""
    dated = sorted((d for d in ordered if d.get('date_parsed')), key=lambda d: d['date_parsed'])
    out = []
    if len(dated) != len(ordered):
        out.append({'from':None, 'to':None, 'link':'unknown_undated_deed',
                    'from_grantees':[], 'to_grantors':[]})
    for older, newer in zip(dated, dated[1:]):
        grantees = sorted({name_key(p['name']) for p in older['parties'] if p['role'] == 'grantee'})
        grantors = sorted({name_key(p['name']) for p in newer['parties'] if p['role'] == 'grantor'})
        if re.search(r'CERTIFICATE OF TITLE', str(newer.get('doc_type') or ''), re.I):
            link = 'court_transfer_not_compared'
        elif not grantees or not grantors:
            link = 'unknown_parties_missing'
        elif set(grantees) & set(grantors):
            link = 'continuous'
        else:
            link = 'names_differ'
        out.append({'from':older['book_page'], 'to':newer['book_page'], 'link':link,
                    'from_grantees':grantees, 'to_grantors':grantors})
    return out
