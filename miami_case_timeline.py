"""Evidence-only Miami docket timeline. No collection, networking or equity writes."""
from datetime import datetime
from decimal import Decimal
import re


def _date(value):
    value = str(value or '').strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(value[:10] if fmt == '%Y-%m-%d' else value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _sale_dates(passages):
    months = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
    pattern = rf'\b(?:\d{{1,2}}/\d{{1,2}}/(?:\d{{4}}|\d{{2}})|{months}\s+\d{{1,2}},?\s+\d{{4}})\b'
    regular, reset = [], []
    for passage in passages:
        for found in re.finditer(pattern, passage, re.I):
            raw = found.group()
            value = _date(raw)
            if not value:
                try:
                    value = datetime.strptime(re.sub(r'\s+', ' ', raw.replace(',', '')), '%B %d %Y').date().isoformat()
                except ValueError:
                    continue
            regular.append(value)
            context = passage[max(0, found.start() - 55):found.start()]
            if re.search(r'\b(?:reset|reschedul\w*)\b[^;\n.]*$', context, re.I): reset.append(value)
    return reset or regular


_LOGIN_RE = re.compile(r'login|log in|sign in', re.I)
_IMAGE_GAP_REASON = {
    'login_required': 'The county answered with its login page: the document exists and was not read.',
    'login_required_likely': ('The docket links a document it does not count (eventType Judgment, 0 '
                              'documents); the county holds these behind its login. Not read.')}

# A docket line about the bankruptcy court's case, which a notice often carries: "Notice of Filing:
# Order Dismissing Chapter 13 Case". Without this the notice_of_filing rule swallowed it and the
# stay history never saw it (2018-026274 entries 209208510 and 209744732, verify-12 defect 9).
# On a state foreclosure docket "chapter 13" is always the bankruptcy: the clerk's reinstatement
# reads "copy Order Reinstating Chapter 13 Bankruptcy", with no "case" after it.
_BK_CASE = (r'(?:chapter\s+(?:7|11|12|13)\b|bankruptcy\s+(?:case|petition|proceeding)'
            r'|debtor\S*\s+case)')
_BANKRUPTCY_CONTEXT_RE = re.compile(r'\bchapter\s+(?:7|11|12|13)\b|\bdebtor|\b11\s+u\.?\s?s\.?\s?c|'
                                    r'\b362\b|bankruptcy court|\bu\.?\s?s\.? trustee', re.I)


def classify(text):
    """Classify operative title, not mentions of earlier documents in body prose."""
    s = re.sub(r'\s+', ' ', str(text or '')).lower()
    if re.match(r'(?:response|reply|opposition)\b', s): return 'response'
    if re.match(r'(?:notice of )?appeal\b', s): return 'appeal'
    if re.match(r'objection\b', s): return 'objection'
    if re.match(r'(?:amended\s+)?(?:notice|request)\s+(?:of|for)\s+(?:a\s+)?(?:special set\s+|evidentiary\s+)?hearing\b', s): return 'hearing'
    if re.match(r'(?:(?:amended|emergency|renewed)\s+)*motion\b', s) or (re.search(r'\bmotion\b', s) and not re.search(r'\border\b', s)):
        if 'summary judgment' in s: return 'motion_for_summary_judgment'
        if 'dismiss' in s: return 'motion_to_dismiss'
        return 'motion'
    # A notice ABOUT a judgment is not the judgment: "Notice of serving final judgment" was read
    # as a sixth final judgment on 6828 (desktop replay, 2026-09-24) and blocked a controlling
    # entry. Bankruptcy and stay notices keep their own meaning below.
    if (re.match(r'(?:amended\s+)?notice of (?:serving|service of|filing|intent|compliance|mailing|'
                 r'dropping|appearance|designation|submitting|lodging)\b', s)
            and not re.search(r'bankrupt|\bstay\b|voluntary dismissal', s)
            and not _BANKRUPTCY_CONTEXT_RE.search(s)):
        return 'notice_of_filing'
    if re.match(r'(?:amended\s+)?proposed\b', s): return 'proposed_order'
    # Filed ABOUT a judgment, not the judgment: 6828's "Certificate of Service of serving final
    # judgment" and "Affidavit of Indebtedness" both read as judgments (desktop replay, 09-24).
    if re.match(r'(?:amended\s+)?certificate of (?:service|mailing|compliance|filing)\b', s):
        return 'certificate_of_service'
    if re.match(r'(?:amended\s+|supplemental\s+)?affidavit\b', s): return 'affidavit'
    checks = [
        ('order_on_motion', r'^order.*(?:denying|denied)'),
        # An order that reinstates a stay, or vacates the order that lifted it, would otherwise
        # match relief_from_stay below and read as the stay ENDING (priority 3, 2023-020247).
        ('stay_reinstated', r'(?:reinstat|reimpos)\w*[^.;]*\bstay\b'
                            r'|vacat\w*[^.;]*(?:relief from|lift\w*|terminat\w*)[^.;]*\bstay\b'
                            # The bankruptcy CASE reinstated, or its dismissal vacated: the stay
                            # comes back with it (2018-026274's 01-31 reinstatement, verify-12 #9).
                            r'|(?:reinstat|reimpos)\w*[^.;]*' + _BK_CASE +
                            r'|vacat\w*[^.;]*dismiss\w*[^.;]*' + _BK_CASE),
        # A dismissed BANKRUPTCY is not a dismissed foreclosure; without this it matched
        # order_of_dismissal and closed the case.
        # Only a dismissal OF the bankruptcy: "order dismissing case due to bankruptcy" is the
        # foreclosure being dismissed and still falls through to order_of_dismissal.
        ('bankruptcy_dismissed', r'dismiss\w*\s+(?:of\s+)?(?:the\s+)?(?:debtor\S*\s+)?(?:chapter\s+\d+\s+)?bankruptcy'
                                 r'|bankruptcy\s+(?:case\s+|petition\s+|proceeding\s+)?(?:no\.?\s*[\w-]+\s+)?'
                                 r'(?:was\s+|has\s+been\s+|is\s+|been\s+)?dismissed'
                                 # "Order Dismissing Chapter 13 Case", filed as a Notice of Filing
                                 # (2018-026274, verify-12 defect 9).
                                 r'|dismiss\w*\s+(?:the\s+)?(?:debtor\S*\s+)?chapter\s+(?:7|11|12|13)\b'
                                 r'|chapter\s+(?:7|11|12|13)\s+(?:case\s+|petition\s+)?(?:no\.?\s*[\w-]+\s+)?'
                                 r'(?:was\s+|has\s+been\s+|is\s+)?dismissed'),
        # A discharge ends the automatic stay (11 U.S.C. 362(c)(2)(C)); the lien survives it.
        ('bankruptcy_discharged', r'discharg\w*[^.;]*(?:debtor|chapter\s+(?:7|11|12|13)\b|bankruptcy)'
                                  r'|(?:debtor|chapter\s+(?:7|11|12|13)\b|bankruptcy)[^.;]*discharg'),
        # An order undoing a certificate or the sale must not fall through to the certificate
        # rules below and read as fresh evidence of a sale (Greptile on #50, 2026-09-23).
        ('vacatur', r'order.*(?:vacat|set.*aside).*(?:judgment|certificate of (?:title|sale)|sale)'),
        ('notice_of_voluntary_dismissal', r'voluntary dismissal'),
        ('order_on_motion', r'order.*(?:denying|denied)'),
        ('relief_from_stay', r'(?:order|notice).*(?:relief from|lift|terminat).*(?:stay)'),
        ('order_on_motion', r'order.*(?:denying|denied).*motion'),
        ('order_resetting_sale', r'order.*(?:reset|reschedul).*sale|order.*sale.*(?:reset|reschedul)'),
        ('order_cancelling_sale', r'order.*cancel.*sale|order.*sale.*cancel'),
        ('order_of_dismissal', r'order.*(?:of dismissal|dismissing|granting.*dismiss)'),
        ('satisfaction', r'satisfaction of (?:final )?judgment|certificate of redemption'),
        # The clerk's own sale-day entries: "Bid Amount" (BIDSCV) and "Mortgage Foreclosure
        # Deposit" (MFDPCV) are posted when a sale is held, days before the certificate
        # (2025-023462 on 09-23, 2024-006803 on 10-28-2025; verify-12 defect 10).
        ('sale_bid', r'^bid amount\b'),
        ('sale_deposit', r'^mortgage foreclosure deposit\b'),
        ('certificate_of_title', r'certificate of title'),
        ('certificate_of_sale', r'certificate of sale'),
        ('suggestion_of_bankruptcy', r'suggestion of bankruptcy|notice of bankruptcy'
                                     r'|bankruptcy\s+(?:petition|notice|filing)'
                                     r'|(?:petition|filing)\s+(?:for|of|in)\s+bankruptcy'
                                     r'|voluntary petition|notice of (?:commencement|filing)[^.;]*chapter\s+(?:7|11|12|13)\b'),
        ('stay', r'order.*stay.*bankrupt|order.*bankrupt.*stay|automatic stay'),
        ('nonbankruptcy_stay', r'order.*stay'),
        ('order_on_motion', r'order.*motion|order (?:granting|denying|awarding)'),
        ('amended_complaint', r'amended complaint'),
        ('complaint', r'\bcomplaint\b'),
        ('final_judgment', r'final judgment'),
        ('notice_of_sale', r'notice of (?:(?:foreclosure|judicial) )?sale|mortgage foreclosure sale'),
        ('summons_service', r'summons|service of process|return of service|served'),
        ('answer', r'\banswer\b'),
        ('default', r'\bdefault\b'),
        ('mediation', r'\bmediation\b'),
        ('objection', r'\bobjection\b'),
        ('appeal', r'\bappeal\b'),
        ('hearing', r'hearing|trial'),
    ]
    return next((kind for kind, pattern in checks if re.search(pattern, s)), 'other')


def _body_kind(pages):
    # Only title-like lines, not a complaint's exhibits or quoted historical orders.
    for page in pages[:1]:
        lines = str(page.get('text') or '').splitlines()[:45]
        for position, line in enumerate(lines):
            line = line.strip()
            if len(line) > 180 or not line: continue
            if re.match(r'notice is hereby|order entered|motion filed', line, re.I): continue
            if not re.match(r'^(?:amended |agreed |amended agreed )?(?:final judgment|complaint|amended complaint|notice |order |motion |suggestion |certificate |satisfaction |summons|answer|objection|response|reply|opposition)', line, re.I):
                continue
            # Continue uppercase heading lines only, not body paragraphs citing history.
            for following in lines[position+1:position+3]:
                if following.strip() and following.strip().isupper() and len(following) < 100:
                    line += ' ' + following.strip()
                else: break
            kind = classify(line)
            if kind != 'other': return kind, line
    return None, None


def _transition(e):
    kind = e['kind']
    if kind in ('notice_of_voluntary_dismissal', 'order_of_dismissal', 'satisfaction') and e.get('limited_scope'):
        return None
    statuses = {'complaint': 'active_pre_judgment', 'amended_complaint': 'active_pre_judgment',
                'final_judgment': 'judgment_entered', 'notice_of_sale': 'sale_scheduled',
                'order_resetting_sale': 'sale_scheduled', 'order_cancelling_sale': 'sale_cancelled',
                'suggestion_of_bankruptcy': 'stayed_by_bankruptcy', 'stay': 'stayed_by_bankruptcy',
                'notice_of_voluntary_dismissal': 'dismissed', 'order_of_dismissal': 'dismissed',
                'satisfaction': 'satisfied_redeemed', 'certificate_of_sale': 'sold', 'certificate_of_title': 'sold',
                'stay_reinstated': 'stayed_by_bankruptcy'}
    if kind == 'vacatur': return {'kind': 'unclear', 'evidence': [e['entry_id']], 'reason': 'Order vacating the judgment, certificate or sale found; subsequent case posture requires explicit evidence.'}
    result = statuses.get(kind)
    if result is None: return None
    r = {'kind': result, 'evidence': [e['entry_id']], 'reason': e['operative_text']}
    if result == 'sale_scheduled':
        dates = _sale_dates(e.get('sale_passages', []))
        r['sale_date'] = next((x for x in reversed(dates) if x), None)
        if not r['sale_date'] and e['calendar_event']: r['sale_date'] = e['date']
        r['reset'] = kind == 'order_resetting_sale'
    if kind == 'suggestion_of_bankruptcy': r['qualification'] = 'Bankruptcy suggested on docket; scope and continuing effect not independently adjudicated.'
    if kind == 'stay_reinstated': r['qualification'] = 'The document puts the bankruptcy stay back in effect; foreclosure activity needs new relief from the bankruptcy court.'
    return r


# What a docket description names when the filing is ABOUT a judgment, sale or title.
# 2018-026274's Memorandum (#117) and Status Report (#119) and 2022-012065's Request for Judicial
# Notice (#152) each carried a judgment copy and read as judgments (desktop rerun, 09-24).
_FILED_ABOUT_RE = re.compile(r'(?:(?:amended|emergency|renewed|agreed|verified|supplemental|joint)\s+)*'
                             r'(?:motion|affidavit|certificate of (?:service|mailing|compliance)|'
                             r'notice\b|response|reply|objection|proposed|memorandum|status report|'
                             r'request|brief|exhibit|transcript|stipulation|letter|correspondence|'
                             r'praecipe|designation|return of service|summons|subpoena|deposition)\b', re.I)
_DISPOSITIVE_BODIES = {'final_judgment', 'certificate_of_title', 'certificate_of_sale', 'satisfaction',
                       'vacatur', 'order_of_dismissal'}
_STAY_CARRIERS = {'suggestion_of_bankruptcy', 'stay', 'relief_from_stay', 'notice_of_filing',
                  'nonbankruptcy_stay', 'order_on_motion', 'bankruptcy_dismissed', 'bankruptcy_discharged',
                  'stay_reinstated'}
_REINSTATED_RE = re.compile(
    r'\b(?:stay\b[^.;]{0,120}?\b(?:is|are|be|shall be|hereby|was|has been)\s+(?:hereby\s+)?'
    r'(?:reinstated|reimposed|re-imposed)'
    r'|(?:reinstat|reimpos)\w*\s+(?:the\s+)?(?:automatic\s+)?stay\b'
    r'|stay\b[^.;]{0,120}?\b(?:is|are|shall be|be|remains?)\s+(?:hereby\s+)?(?:once\s+)?again\s+'
    r'in\s+(?:full\s+)?(?:force\s+and\s+)?effect'
    r'|(?:is|are|be|hereby)\s+(?:hereby\s+)?vacated[^.;]{0,80}(?:relief from|lifting|terminating)[^.;]{0,40}\bstay'
    r'|(?:relief from|lifting|terminating)[^.;]{0,60}\bstay\b[^.;]{0,60}\b(?:is|are|be|hereby)\s+(?:hereby\s+)?vacated)',
    re.I)
# Only what sits right before the match: "Motion to reinstate", "request for", "if the stay is".
_NOT_OPERATIVE_RE = re.compile(r'(?:\b(?:motion|request|petition|application)\s+(?:to|for)\s+(?:the\s+)?$'
                               r'|\b(?:moves?|moving|seeks?|seeking|asks?|asking|requests?|requesting|'
                               r'intends?|wishes)\s+(?:(?:the|this)\s+court\s+)?(?:to\s+)?$'
                               r'|\b(?:if|whether|unless|until)\b[^.;]{0,40}$)', re.I)
# Citation periods would end a sentence early: "11 U.S.C. 362(a) is hereby reinstated".
_ABBREV_RE = re.compile(r'\b(?:U\.\s?S\.\s?C|U\.\s?S|Fla\.\s?Stat|Stat|No|Bankr|Fed|R|P)\.', re.I)


def stay_reinstatement_passages(pages):
    """Operative sentences in a document's body saying the bankruptcy stay is back in force.
    A motion asking for it, or a conditional, is not one: the words just before the match must
    not be 'motion to', 'if', 'whether' and the like."""
    out = []
    for page in pages or []:
        text = re.sub(r'[ \t]+', ' ', str(page.get('text') or ''))
        flat = _ABBREV_RE.sub(lambda m: m.group(0).replace('.', ''), re.sub(r'\s*\n\s*', ' ', text))
        for match in _REINSTATED_RE.finditer(flat):
            before = flat[max(0, match.start() - 60):match.start()]
            if _NOT_OPERATIVE_RE.search(before):
                continue
            start = max(flat.rfind('.', 0, match.start()) + 1, match.start() - 200)
            end = flat.find('.', match.end())
            passage = flat[start:(end + 1 if end != -1 else match.end() + 120)].strip()
            out.append({'page': page.get('page'), 'source_ref': page.get('_source_ref'),
                        'passage': passage[:300]})
    return out


def build_timeline(case, inventory, document_rows, as_of):
    import document_collectors as DC
    import document_coverage as COV
    today = _date(as_of)
    if not today: raise ValueError('as_of must be a valid date')
    docs = {}
    for row in document_rows:
        for key in ('entry_ref', 'event_id', 'source_id', 'source_ref'):
            if row.get(key) is not None:
                bucket = docs.setdefault(str(row[key]), [])
                if row not in bucket: bucket.append(row)
        source = str(row.get('source_ref') or '')
        if source.startswith('court:'):
            bucket = docs.setdefault(source.split(':')[1], [])
            if row not in bucket: bucket.append(row)
    entries, gaps, amounts, pending = [], [], [], []
    defendants = docket_defendants(inventory)
    if not inventory.get('pagination_verified'):
        gaps.append({'kind': 'inventory_completeness_unknown', 'reason': 'Docket pagination completeness is not verified.'})
    for i, item in enumerate(inventory.get('entries', [])):
        meta = item.get('metadata') or item
        ident = str(item.get('source_id') or meta.get('eventID') or item.get('source_ref') or i)
        description = str(meta.get('docketDescrition') or meta.get('description') or item.get('description') or '')
        comments = str(meta.get('comments') or '')
        index_text = ' '.join(x for x in (description, comments) if x)
        matched = docs.get(ident) or docs.get(str(item.get('source_ref'))) or []
        pages = [dict(p, _source_ref=d.get('source_ref'), _document_hash=(d.get('manifest') or {}).get('source_sha256') or (d.get('manifest') or {}).get('sha256') or d.get('document_hash')) for d in matched for p in (d.get('reading') or {}).get('pages', [])]
        body_kind, title = _body_kind(pages)
        scope_text = index_text + '\n' + '\n'.join(str(p.get('text') or '') for p in pages[:2])
        ik = classify(index_text)
        attached = None
        if body_kind in _DISPOSITIVE_BODIES and _FILED_ABOUT_RE.match(description.strip()):
            # The docket says this entry is a motion, affidavit or certificate; a judgment on its
            # first page is an exhibit or the thing it certifies, not a new judgment (6828 #49,
            # #58, #65). The docket description alone decides, not its comments.
            attached, body_kind, title = body_kind, None, None
            ik = classify(description)
        e = {'entry_id': ident, 'date': _date(meta.get('eventDate') or item.get('date')),
             'description': description, 'comments': comments, 'filed_by': meta.get('filedBy') or meta.get('filed_by') or 'unknown',
             'listed_parties': meta.get('partiesName') or '', 'index_kind': ik,
             'kind': body_kind or ik, 'kind_source': 'document' if body_kind else 'docket_text',
             'index_agrees': body_kind == ik if body_kind else None,
             'operative_text': title or index_text, 'calendar_event': str(meta.get('eventType', '')).lower() == 'hearing'}
        if attached:
            e['attached_document_kind'] = attached
        e['limited_scope'], e['dismissed_parties'] = scope_of(e['kind'], scope_text, defendants)
        if e['kind'] in ('final_judgment', 'vacatur', 'satisfaction'):
            # Only for linking judgments to what acts on them; dropped before the timeline is saved.
            e['_body'] = '\n'.join(str(p.get('text') or '') for p in pages[:3])[:6000]
        # A bankruptcy filing often carries the bankruptcy court's own order as an attachment.
        # 2023-020247's reinstated stay was on pages 3-4 of such a filing, and the docket title only
        # said "suggestion of bankruptcy", so the index never saw it (desktop replay, 2026-09-24).
        if e['kind'] in _STAY_CARRIERS:
            passages = stay_reinstatement_passages(pages)
            if passages:
                e.update(kind='stay_reinstated', kind_source='document_passage',
                         stay_passages=passages, index_agrees=e['index_kind'] == 'stay_reinstated',
                         operative_text=passages[0]['passage'])
        if e['calendar_event'] and e['kind'] != 'notice_of_sale': e['kind'] = 'hearing'
        e['sale_passages'] = [index_text] if re.search(r'\bsale\b', index_text, re.I) else []
        body_lines = [line for p in pages for line in str(p.get('text') or '').splitlines()]
        e['motion_disposition_passages'] = [line for line in body_lines if re.search(r'\bmotion\b', line, re.I) and re.search(r'\b(?:is|hereby|be)\s+(?:granted|denied)\b|\b(?:grants|denies)\b', line, re.I)] if e['kind'] in ('final_judgment', 'order_on_motion') else []
        e['sale_passages'] += [line for line in body_lines if re.search(r'\b(?:sale|sell|auction|reset|reschedul\w*)\b', line, re.I)]
        if e['kind'] == 'order_cancelling_sale':
            reasons = [line for line in body_lines if re.search(r'\b(?:because|due to|reason|cancel\w*)\b', line, re.I)]
            if reasons: e['operative_text'] += ' — ' + ' '.join(reasons[:3])
        expected = item.get('expected_documents', meta.get('numberOfDocuments', 0)) or 0
        failed = [p for p in pages if not COV.page_is_read(p)]
        for page in failed:
            if page.get('outcome') in COV.READABLE:
                # A readable outcome with nothing on it but the watermark (verify-12 defect 12).
                page = dict(page, weak_reason=page.get('weak_reason') or 'watermark or stamp only; '
                            'no page content was read')
            assessment = page.get('assessment') or {}
            reason = assessment.get('reason') if isinstance(assessment, dict) else str(assessment)
            reason = reason or page.get('ocr_error') or page.get('weak_reason') or page.get('error') or 'No readable page outcome was recorded.'
            gaps.append({'entry_id': ident, 'kind': 'page_unreadable', 'source_ref': page.get('_source_ref'), 'document_hash': page.get('_document_hash'), 'page': page.get('page'), 'outcome': page.get('outcome'), 'reason': reason})
        e['docket_code'] = meta.get('docketCode')
        if failed:
            e['image_status'] = 'unreadable_pages'
        elif pages:
            e['image_status'] = 'read'
        elif item.get('inventory_status') == 'gap' and _LOGIN_RE.search(str(item.get('gap') or '')):
            e['image_status'] = 'login_required'
        elif DC.links_uncounted_document(meta):
            # verify-12 defect 11: these were labelled no_image_indexed; the county holds them
            # behind its login. Not fetched yet, so "likely", and a gap either way.
            e['image_status'] = 'login_required_likely'
        else:
            e['image_status'] = 'not_fetched' if expected else 'no_image_indexed'
        if len(matched) < int(expected) and pages: e['image_status'] = 'missing_attachments'
        for d in matched:
            manifest = d.get('manifest') or {}
            reading = d.get('reading') or {}
            count = manifest.get('pages') or reading.get('page_count') or reading.get('pages_total')
            if isinstance(count, int) and count > 0:
                seen = {p.get('page') for p in reading.get('pages', [])}
                missing = [n for n in range(1, count + 1) if n not in seen]
                if missing:
                    e['image_status'] = 'unassessed_pages'
                    gaps.append({'entry_id': ident, 'kind': 'unassessed_pages', 'source_ref': d.get('source_ref'), 'pages': missing, 'reason': 'Expected document pages have no assessment.'})
        if e['image_status'] != 'read': gaps.append({'entry_id': ident, 'kind': e['image_status'], 'reason': _IMAGE_GAP_REASON.get(e['image_status']) or ('No image indexed by county.' if not expected and not pages else 'Image not fetched or one or more pages unresolved.'), 'pages': [p.get('page') for p in failed]})
        for d in matched:
            if d.get('acquisition_gap'): gaps.append({'entry_id': ident, 'kind': 'acquisition_gap', 'source_ref': d.get('source_ref'), 'reason': str(d['acquisition_gap'])})
            for gap in d.get('supplemental_ocr_gaps', []): gaps.append(dict(gap, entry_id=ident, kind='supplemental_ocr_failed', source_ref=d.get('source_ref')))
        if not e['date']: gaps.append({'entry_id': ident, 'kind': 'date_unknown', 'reason': 'Entry cannot be reliably ordered.'})
        for page in pages:
            for line in str(page.get('text') or '').splitlines():
                for number in re.findall(r'\$\s*(\d[\d,]*\.\d{2})', line):
                    amounts.append({'entry_id': ident, 'source_ref': page.get('_source_ref'), 'document_hash': page.get('_document_hash'), 'page': page.get('page'), 'amount': str(Decimal(number.replace(',', ''))), 'passage': line, 'verification': 'unverified_extraction'})
        if e['calendar_event'] and e['date'] and e['date'] >= today:
            pending.append({'type': 'hearing', 'entry_id': ident, 'date': e['date'], 'description': index_text})
        due = _date(meta.get('dueDate'))
        if due and not meta.get('completedDate'):
            pending.append({'type': 'deadline', 'entry_id': ident, 'date': due, 'description': index_text, 'status': 'future' if due >= today else 'past_due_completion_unknown'})
        entries.append(e)
    entries.sort(key=lambda e: (e['date'] or '9999', e['entry_id']))
    status = {'kind': 'unclear', 'evidence': [], 'reason': 'No dated dispositive entry.'}
    pre_stay = None
    unresolved_stay = []
    stay_history = []
    day_changes = {}
    for e in entries:
        if not e['date'] or e['date'] > today: continue
        if e['kind'].startswith('motion'):
            pending.append({'type': 'motion', 'entry_id': e['entry_id'], 'date': e['date'], 'description': e['description'], 'status': 'no_matching_order_identified'})
        for passage in e.get('motion_disposition_passages', []):
            topics = ('summary judgment', 'default', 'dismiss', 'cancel', 'attorney fees', "attorney's fees")
            _close_unique_motion(pending, passage, e['date'], topics)
        if e['kind'] in ('order_cancelling_sale', 'order_resetting_sale', 'order_of_dismissal') or (e['kind'] == 'order_on_motion' and re.search(r'\b(granting|denying|granted|denied|awarding)\b', e['operative_text'], re.I)):
            # Only close a motion when its substantive phrase is explicitly repeated.
            text = e['operative_text'].lower()
            topics = ('dismiss', 'summary judgment', 'cancel', 'reset', 'attorney fees', "attorney's fees", 'default')
            _close_unique_motion(pending, text, e['date'], topics)
        if e['kind'] in ('relief_from_stay', 'bankruptcy_dismissed', 'bankruptcy_discharged', 'stay_reinstated', 'suggestion_of_bankruptcy', 'stay'):
            stay_history.append({'entry_id': e['entry_id'], 'date': e['date'], 'event': {
                'relief_from_stay': 'limited_relief' if e.get('limited_scope') else 'relief',
                'bankruptcy_dismissed': 'bankruptcy_dismissed', 'bankruptcy_discharged': 'discharged',
                'stay_reinstated': 'reinstated', 'suggestion_of_bankruptcy': 'stayed', 'stay': 'stayed'}[e['kind']],
                'text': e['operative_text'][:200]})
        if e['kind'] in ('relief_from_stay', 'bankruptcy_dismissed', 'bankruptcy_discharged'):
            if e.get('limited_scope'):
                status = {'kind': 'unclear', 'evidence': unresolved_stay + [e['entry_id']], 'reason': 'Partial or limited stay relief does not establish that all foreclosure restrictions ended.'}
                continue
            unresolved_stay = []
            if pre_stay:
                status = dict(pre_stay, evidence=pre_stay['evidence'] + [e['entry_id']], reason=e['operative_text'])
            else: status = {'kind': 'unclear', 'evidence': [e['entry_id']], 'reason': 'Stay relief found without established pre-stay state.'}
            continue
        change = _transition(e)
        if change:
            if e['kind'] in ('complaint', 'amended_complaint') and status['evidence']:
                continue
            if change['kind'] == 'stayed_by_bankruptcy': unresolved_stay.append(e['entry_id'])
            elif unresolved_stay and change['kind'] in ('sale_scheduled', 'sold', 'judgment_entered'):
                status = {'kind': 'unclear', 'evidence': unresolved_stay + change['evidence'], 'reason': 'Later foreclosure activity conflicts with an unresolved bankruptcy stay; no relief identified.'}
                day_changes[e['date']] = dict(status)
                continue
            if change['kind'] == 'stayed_by_bankruptcy' and status['kind'] != 'stayed_by_bankruptcy': pre_stay = dict(status)
            prior = day_changes.get(e['date'])
            if prior and (prior['kind'] != change['kind'] or prior.get('sale_date') != change.get('sale_date')):
                status = {'kind': 'unclear', 'evidence': prior['evidence'] + change['evidence'], 'reason': 'Conflicting same-date entries; reliable within-day order is unavailable.'}
            else: status = change
            day_changes[e['date']] = dict(status)
    undated = [e['entry_id'] for e in entries if not e['date'] and _transition(e)]
    if undated: status = {'kind': 'unclear', 'evidence': status['evidence'] + undated, 'reason': 'Undated dispositive entry prevents reliable chronology.'}
    held = sale_held(entries, today)
    if status['kind'] == 'sale_scheduled' and status.get('sale_date') and status['sale_date'] < today:
        # A past sale date is not a sale. Only a certificate of sale or title says one happened;
        # the clerk's bid and deposit entries say it was HELD, which is not the same thing.
        mine = held if held and held['date'] >= status['sale_date'] else None
        if mine:
            status = dict(status, sale_outcome='held_no_certificate_yet', sale_held=mine,
                          reason=status['reason'] + ' | The clerk posted the sale-day bid and deposit '
                          'entries on %s; no certificate of sale is on the docket yet.' % mine['date'])
        else:
            status = dict(status, sale_outcome='unknown_no_certificate',
                          reason=status['reason'] + ' | Sale date has passed and no certificate of sale is on the docket; whether a sale occurred is unknown.')
    stay_now = None
    if stay_history:
        last = stay_history[-1]['event']
        stay_now = (True if last in ('stayed', 'reinstated') else False if last in ('relief', 'bankruptcy_dismissed', 'discharged') else None)
    judgments = reconcile_judgments(entries, today)
    for e in entries:
        e.pop('_body', None)
    return {'case': case, 'county': 'MIAMI-DADE', 'as_of': today, 'entries': entries, 'status': status,
            'judgments': judgments,
            'stay_history': stay_history, 'stay_in_effect': stay_now, 'sale_held': held,
            'pending': pending, 'amounts': amounts, 'gaps': gaps, 'coverage_complete': not gaps,
            'qualification': 'Status is derived from available docket evidence, not confirmation of a complete court record. Amount extractions are not verified balances or equity inputs.'}


def sale_held(entries, today):
    """The newest sale the clerk's bid and deposit entries say was held, or None.

    -> {'date', 'evidence': [entry ids], 'certificate': entry id or None, 'bankruptcy_same_day':
    [entry ids], 'bankruptcy_order', 'qualification'}. The docket gives dates, not times, so a
    bankruptcy entry on the sale day leaves 'bankruptcy_order' 'unresolved'. A clerk comment saying
    before or after the sale is never taken as the time: 2025-023462's says "FILED AFTER THE SALE"
    and the petition's own image shows it entered at 08:41, before the sale."""
    marks = [e for e in entries if e['kind'] in ('sale_bid', 'sale_deposit') and e.get('date')
             and e['date'] <= today]
    if not marks:
        return None
    day = max(e['date'] for e in marks)
    evidence = [e['entry_id'] for e in marks if e['date'] == day]
    certificate = next((e['entry_id'] for e in entries if e['kind'] in ('certificate_of_sale', 'certificate_of_title')
                        and e.get('date') and e['date'] >= day), None)
    bankrupt = [e['entry_id'] for e in entries if e.get('date') == day
                and e['kind'] in ('suggestion_of_bankruptcy', 'stay', 'stay_reinstated')]
    return {'date': day, 'evidence': evidence, 'certificate': certificate,
            'bankruptcy_same_day': bankrupt, 'bankruptcy_order': 'unresolved' if bankrupt else None,
            'qualification': ('Held per the clerk\'s bid and deposit entries. A certificate of sale '
                              'follows if no objection is sustained; the sale can still be vacated.'
                              + (' A bankruptcy entry the same day: whether the petition preceded the '
                                 'sale decides whether the sale is void under the automatic stay. '
                                 'Unresolved until the petition\'s filing time is read against the '
                                 'sale time; the docket comment is not that evidence.'
                                 if bankrupt else ''))}


def scope_of(kind, text, defendants=()):
    """-> (limited_scope, parties named). Limited when the text says partial/as to/only/a count,
    or when a dismissal, vacatur or satisfaction names some defendants and not the action or all
    of them: "Order dismissing Defendant UNKNOWN TENANT" does not end the foreclosure."""
    limited = bool(re.search(r'(?:dismiss\w*|satisf\w*|releas\w*|vacat\w*)[^\n.]{0,100}(?:\bas to\b|\bone defendant\b|\bpartial\b|\bonly\b)|\bpartial (?:dismissal|satisfaction|vacatur)|\bas to (?:defendants?|party)\b', text, re.I))
    if re.search(r'\b(?:count\s+[IVX\d]+|cause of action|partial relief|limited relief)\b', text, re.I):
        limited = True
    named = (_named_parties(text, defendants)
             if kind in ('notice_of_voluntary_dismissal', 'order_of_dismissal', 'vacatur', 'satisfaction') else [])
    if named and not _whole_case(text):
        limited = True
    if re.search(r'\bas to all (?:defendants|parties)\b', text, re.I) and not re.search(r'\bcount\s+[IVX\d]+', text, re.I):
        limited = False
    return limited, named


def docket_defendants(inventory):
    return [str(p.get('partyName') or '') for p in ((inventory.get('raw') or {}).get('parties') or [])
            if 'DEFENDANT' in str(p.get('partyTypeDesc') or p.get('partyType') or '').upper()]


_NAME_NOISE = re.compile(r'\b(?:INC|CORP|CO|LLC|L\.?L\.?C|LP|LLP|LTD|PA|PLLC|NA|N\.?A|THE|OF|AND|A|AN|ASSN|ASSOC'
                         r'|ASSOCIATION|TRUST|COMPANY|UNKNOWN|MR|MRS|MS|JR|SR|II|III|AS|TRUSTEE)\b', re.I)


def _name_tokens(name):
    return {t for t in re.split(r'[^A-Z0-9]+', _NAME_NOISE.sub(' ', str(name or '').upper())) if len(t) > 2}


def _named_parties(text, defendants):
    """Defendants this text names. Unknown tenants/spouses count by their role; a named party
    counts when every distinctive word of the party name appears in the text."""
    upper = str(text or '').upper()
    words = set(re.split(r'[^A-Z0-9]+', upper))
    named = [d for d in defendants if _name_tokens(d) and _name_tokens(d) <= words]
    for role in re.findall(r'UNKNOWN\s+(?:TENANT|SPOUSE|PART(?:Y|IES)|HEIRS?)[^,.;\n]{0,20}', upper):
        named.append(role.strip())
    if re.search(r'\bAS TO (?:THE )?DEFENDANTS?\b|\bDEFENDANTS?,?\s+[A-Z]{2,}', upper) and not named:
        named.append('a named defendant')
    return list(dict.fromkeys(named))


def _whole_case(text):
    return bool(re.search(r'\b(?:this|the|above[- ]styled|entire|instant)\s+(?:action|case|cause|matter|lawsuit|complaint)\b'
                          r'[^.;]{0,80}\bdismiss|\bdismiss\w*\b[^.;]{0,80}\b(?:this|the|above[- ]styled|entire|instant)\s+'
                          r'(?:action|case|cause|matter|lawsuit)\b|\ball (?:defendants|parties)\b', str(text or ''), re.I))


_NO_IMAGE = ('no_image_indexed', 'county_no_document')
# The document exists and was not read: the county holds it behind a login.
_LOGIN_WALLED = ('login_required', 'login_required_likely')
_NOT_A_JUDGMENT_OF_RECORD = ('supplemental', 'docket_duplicate')
_REPLACES = re.compile(r'\b(?:amended|corrected|amending|substitut\w*|replacement|re-?entered)\b', re.I)
_ADDS_TO = re.compile(r'\bsupplemental\b|\b(?:attorney.?s?|attorneys)\s+fees?\b|\bcosts? judgment\b', re.I)


_MONTHS = {m: i for i, m in enumerate(('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep',
                                      'oct', 'nov', 'dec'), 1)}
_LONG_DATE_RE = re.compile(
    r'\b(?:(?P<m1>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y1>\d{4})'
    r'|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+(?P<m2>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+(?P<y2>\d{4}))',
    re.I)


def _dates_in(text):
    """Every date a passage cites: 10/14/2025, October 14, 2025, the 14th day of October, 2025."""
    out = set()
    for raw in re.findall(r'\b\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})\b', str(text or '')):
        value = _date(raw)
        if value:
            out.add(value)
    for m in _LONG_DATE_RE.finditer(str(text or '')):
        month = _MONTHS.get((m.group('m1') or m.group('m2')).lower()[:3])
        day, year = int(m.group('d1') or m.group('d2')), int(m.group('y1') or m.group('y2'))
        try:
            out.add(datetime(year, month, day).date().isoformat())
        except ValueError:
            pass
    return out


def reconcile_judgments(entries, today):
    """Which final judgment is current, and what later docket entries did to each one.

    Newest is not controlling. Each judgment is 'operative' until a LATER dated entry acts on it:
      superseded   an amended/corrected/substituted judgment replaces it
      vacated      an order vacating or setting aside a judgment
      satisfied    a satisfaction of judgment
    and the 'partially_' form of vacated/satisfied when that entry is limited to some parties.
    A later entry acts on the judgment whose date it cites; with no cited date, on the one
    operative judgment there is; with several and no citation, nothing is decided and both are
    'unclear'. A supplemental judgment (fees, costs) adds to the one it follows and replaces
    nothing. Two plain final judgments with no link between them are both 'unclear'.

    'no_satisfaction_found' on an operative judgment is exactly that. It is not an open balance.
    """
    judgments, events = [], []
    # A judgment entry the county holds no image for, on a day another judgment entry does have
    # one, is taken as the docket listing that judgment twice (2024-009959 #79/#80, 2023-020247 #91/#92,
    # 2022-012065 #174/#177). Without this the image-less entry blocks, or even becomes, the
    # controlling judgment. Inferred from the docket, so it is labelled, not hidden.
    # The pilot's image-less entries turned out to be the county's login-walled "Judgment" events
    # (verify-12 defect 11), so the inference now also needs the same docket code (or, without
    # one, the same description) as the imaged entry, and says when the other copy is behind a
    # login rather than absent.
    imaged = [e for e in entries if e['kind'] == 'final_judgment' and e.get('date')
              and e.get('image_status') == 'read']
    for e in entries:
        if not e.get('date') or e['date'] > today:
            continue
        text = ' '.join(str(e.get(k) or '') for k in ('operative_text', 'description', 'comments'))
        if e['kind'] == 'final_judgment':
            j = {'entry_id': e['entry_id'], 'date': e['date'], 'title': str(e.get('operative_text') or '')[:200],
                 'role': 'supplemental' if _ADDS_TO.search(text) else 'replacement' if _REPLACES.search(text) else 'judgment',
                 'status': 'operative', 'by': [], 'satisfaction': 'no_satisfaction_found', 'reason': ''}
            twin = next((o for o in imaged if o['date'] == e['date'] and o is not e
                         and _same_listing(o, e)), None)
            if (j['role'] == 'judgment' and twin is not None
                    and e.get('image_status') in _NO_IMAGE + _LOGIN_WALLED):
                walled = e.get('image_status') in _LOGIN_WALLED
                j.update(role='docket_duplicate', status='docket_duplicate_inferred', twin=twin['entry_id'],
                         reason=('its document is behind the county login and was not read; entry %s the '
                                 'same day with the same docket code has a read copy, so this is taken as '
                                 'the same judgment listed twice (inferred, not read)' % twin['entry_id'])
                         if walled else
                         ('no document image; entry %s the same day with the same docket code has one, '
                          'so this is taken as the same judgment listed twice (inferred, not read)'
                          % twin['entry_id']))
                judgments.append(j)
                continue
            if j['role'] == 'replacement':
                targets, basis = _target(judgments, text)
                if not targets and e.get('_body'):
                    body_targets, body_basis = _target(judgments, e['_body'])
                    if body_targets:
                        targets, basis = body_targets, body_basis + ' in the document body'
                for target in targets:
                    target.update(status='superseded', reason='replaced by %s (%s)' % (j['entry_id'], basis))
                    target['by'].append(j['entry_id'])
                if targets:
                    j['replaces'] = targets[0]['entry_id'] if len(targets) == 1 else [t['entry_id'] for t in targets]
                else:
                    j['reason'] = basis
            elif j['role'] == 'supplemental':
                prior = [x for x in judgments if x['status'] == 'operative' and x['role'] not in _NOT_A_JUDGMENT_OF_RECORD]
                j['adds_to'] = prior[-1]['entry_id'] if prior else None
            else:
                live = [x for x in judgments if x['status'] == 'operative' and x['role'] not in _NOT_A_JUDGMENT_OF_RECORD]
                if live:
                    same_day = all(x['date'] == j['date'] for x in live)
                    for x in live + [j]:
                        x.update(status='unclear', reason=(
                            'more than one final judgment entry the same day and nothing yet names one; '
                            'possibly one judgment filed twice' if same_day else
                            'more than one final judgment with no amendment, vacatur or scope linking them'))
            judgments.append(j)
            continue
        action = {'vacatur': 'vacated', 'satisfaction': 'satisfied'}.get(e['kind'])
        if not action or (action == 'vacated' and not re.search(r'judgment', text, re.I)):
            continue
        targets, basis = _target(judgments, text)
        if not targets and e.get('_body'):
            # The docket title often says only "Order vacating final judgment"; the order itself
            # usually names the judgment by its date (6828, desktop replay 2026-09-24).
            body_targets, body_basis = _target(judgments, e['_body'])
            if body_targets:
                targets, basis = body_targets, body_basis + ' in the document body'
        if action == 'vacated' and not targets:
            # A vacated judgment needs its target; an unmatched vacatur leaves every candidate open.
            for x in judgments:
                if x['status'] in ('operative', 'superseded'):
                    x.update(status='unclear', reason='%s vacates a judgment it does not identify (%s)' % (e['entry_id'], basis))
            continue
        if not targets:
            events.append({'entry_id': e['entry_id'], 'kind': e['kind'], 'reason': basis})
            continue
        partial = 'partially_' if e.get('limited_scope') else ''
        for target in targets:
            if action == 'satisfied':
                target['satisfaction'] = partial + 'satisfied'
                target['by'].append(e['entry_id'])
                if not partial:
                    target.update(status='satisfied', reason='satisfaction %s (%s)' % (e['entry_id'], basis))
            else:
                target['by'].append(e['entry_id'])
                target.update(status=partial + 'vacated', reason='vacatur %s (%s)%s' % (
                    e['entry_id'], basis, '; limited to %s' % ', '.join(e.get('dismissed_parties') or ['some parties']) if partial else ''))
    operative = [j for j in judgments if j['status'] in ('operative', 'partially_vacated') and j['role'] not in _NOT_A_JUDGMENT_OF_RECORD]
    unclear = [j for j in judgments if j['status'] == 'unclear']
    controlling = operative[0]['entry_id'] if len(operative) == 1 and not unclear else None
    duplicates = [j['entry_id'] for j in judgments if j['role'] == 'docket_duplicate']
    return {'judgments': judgments, 'unmatched': events, 'controlling_entry': controlling,
            'docket_duplicates_inferred': duplicates,
            'controlling_reason': ('one operative judgment after amendments, vacaturs and satisfactions'
                                   + ('; image-less same-day entr%s %s taken as the same judgment listed twice'
                                      % ('y' if len(duplicates) == 1 else 'ies', ', '.join(duplicates))
                                      if duplicates else '') if controlling
                                   else 'no operative judgment' if not operative and not unclear
                                   else 'judgments conflict or could not be linked; review required'),
            'qualification': 'Docket-index reconciliation. The judgment bodies decide scope; no satisfaction found is not proof of an open balance.'}


def _same_listing(a, b):
    """Same docket code when both carry one; otherwise the same docket description."""
    if a.get('docket_code') and b.get('docket_code'):
        return a['docket_code'] == b['docket_code']
    norm = lambda e: re.sub(r'\s+', ' ', str(e.get('description') or '')).strip().lower()
    return norm(a) == norm(b)


def _target(judgments, text):
    """-> ([judgment], basis) or ([], why).

    Several only when they are the entries of ONE day: 6828 has two "Final Judgment" entries on
    2025-09-08 and an order vacating "the Final Judgment of Foreclosure entered on September 8,
    2025". Whatever the second entry is (the same judgment filed twice, or two that day), the
    order names the day, so it acts on that day's entries together."""
    cited = _dates_in(text)
    by_date = [j for j in judgments if j['date'] in cited and j['role'] not in _NOT_A_JUDGMENT_OF_RECORD]
    if by_date and len({j['date'] for j in by_date}) == 1:
        return by_date, 'cites its date %s%s' % (by_date[0]['date'], _same_day(by_date))
    live = [j for j in judgments if j['status'] in ('operative', 'unclear', 'partially_vacated')
            and j['role'] not in _NOT_A_JUDGMENT_OF_RECORD]
    if live and len({j['date'] for j in live}) == 1 and not cited:
        return live, 'the only operative judgment' + _same_day(live)
    if not live:
        return [], 'no earlier judgment on the docket'
    return [], 'cites no date that matches exactly one earlier judgment'


def _same_day(group):
    return '' if len(group) == 1 else ', shared by %d judgment entries that day' % len(group)


def _topic_text(text):
    return re.sub(r"\battorney(?:s|['’]s)?\b", 'attorney', str(text).lower())


def _close_unique_motion(pending, text, order_date, topics):
    matches = [p for p in pending if p['type'] == 'motion' and p['date'] < order_date and any(topic in _topic_text(text) and topic in _topic_text(p['description']) for topic in topics)]
    if len(matches) == 1:
        pending.remove(matches[0])


def render_markdown(result):
    def esc(value): return str(value or '').replace('|', '\\|').replace('\n', ' ')
    lines = [f"# Case timeline — {result['case']}", '', f"As of {result['as_of']}. Status: **{result['status']['kind']}**.",
             f"Evidence: {', '.join(result['status']['evidence']) or 'none'}. {result['status']['reason']}", '', result['qualification'], '',
             '| Entry | Date | Kind | Filed by | Description | Image |', '|---|---|---|---|---|---|']
    if 'sale_date' in result['status']:
        lines.insert(4, f"Sale date: {result['status']['sale_date'] or 'unknown'}. Reset: {bool(result['status'].get('reset'))}.")
    source = result.get('source_comparison')
    if source:
        lines.insert(4, 'Source counts: full OCS = %s; timeline = %s; dockets.json retained = %s; cache reported total = %s. Counts differ: %s. %s' % (
            source['full_ocs_entries'], source['timeline_entries'], source['cache_entries'],
            source['cache_reported_total'], source['counts_differ'], source['note']))
    for e in result['entries']:
        lines.append('| ' + ' | '.join(esc(e.get(k)) for k in ('entry_id', 'date', 'kind', 'filed_by', 'description', 'image_status')) + ' |')
    recon = result.get('judgments') or {}
    if recon.get('judgments'):
        lines += ['', '## Judgments', '', 'Controlling: %s (%s). %s' % (
            recon.get('controlling_entry') or 'not established', recon.get('controlling_reason'), recon.get('qualification')), '']
        lines += ['- %s %s: %s, %s; satisfaction: %s. %s' % (j['entry_id'], j['date'], j['role'], j['status'], j['satisfaction'], esc(j['reason']))
                  for j in recon['judgments']]
    cov = result.get('coverage') or {}
    if cov.get('attachments'):
        lines += ['', '## Document coverage', '', '%s of %s expected attachment(s) read. %s' % (
            cov.get('read'), cov.get('expected'), ', '.join('%s: %s' % kv for kv in sorted((cov.get('counts') or {}).items()))),
            cov.get('qualification', ''), '']
        lines += ['- %s %s: %s%s%s' % (a['entry_id'], esc(a.get('description')), a['state'],
                                        ' (%s)' % esc('; '.join(str(d) for d in a['detail'])) if a.get('detail') else '',
                                        ' | public recorded copy candidate: %s' % a['alternate_copy'] if a.get('alternate_copy') else '')
                  for a in cov['attachments'] if a['state'] not in ('read', 'county_no_document')]
    if result.get('stay_history'):
        lines += ['', '## Stay history', '', 'Stay in effect now: %s.' % {True: 'yes', False: 'no', None: 'unknown'}[result.get('stay_in_effect')], '']
        lines += ['- %s %s: %s' % (h['entry_id'], h['date'], h['event']) for h in result['stay_history']]
    for section in ('pending', 'amounts', 'gaps'):
        lines += ['', f'## {section.title()}', '']
        lines += ['- ' + '; '.join(f'{k}: {esc(v)}' for k, v in row.items()) for row in result[section]] or ['No items identified in available evidence; completeness is not implied.']
    return '\n'.join(lines) + '\n'
