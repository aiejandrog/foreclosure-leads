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


def classify(text):
    """Classify operative title, not mentions of earlier documents in body prose."""
    s = re.sub(r'\s+', ' ', str(text or '')).lower()
    if re.match(r'(?:response|reply|opposition)\b', s): return 'response'
    if re.match(r'(?:amended\s+)?(?:notice|request)\s+(?:of|for)\s+(?:a\s+)?(?:special set\s+|evidentiary\s+)?hearing\b', s): return 'hearing'
    if re.search(r'\bmotion\b', s) and not re.search(r'\border\b', s):
        if 'summary judgment' in s: return 'motion_for_summary_judgment'
        if 'dismiss' in s: return 'motion_to_dismiss'
        return 'motion'
    checks = [
        ('notice_of_voluntary_dismissal', r'voluntary dismissal'),
        ('order_on_motion', r'order.*(?:denying|denied)'),
        ('relief_from_stay', r'(?:order|notice).*(?:relief from|lift|terminat).*(?:stay)'),
        ('order_on_motion', r'order.*(?:denying|denied).*motion'),
        ('order_resetting_sale', r'order.*(?:reset|reschedul).*sale|order.*sale.*(?:reset|reschedul)'),
        ('order_cancelling_sale', r'order.*cancel.*sale|order.*sale.*cancel'),
        ('order_of_dismissal', r'order.*(?:of dismissal|dismissing|granting.*dismiss)'),
        ('satisfaction', r'satisfaction of (?:final )?judgment|certificate of redemption'),
        ('certificate_of_title', r'certificate of title'),
        ('certificate_of_sale', r'certificate of sale'),
        ('suggestion_of_bankruptcy', r'suggestion of bankruptcy|notice of bankruptcy'),
        ('stay', r'order.*stay|automatic stay'),
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
                'satisfaction': 'satisfied_redeemed', 'certificate_of_sale': 'sold', 'certificate_of_title': 'sold'}
    result = statuses.get(kind)
    if result is None: return None
    r = {'kind': result, 'evidence': [e['entry_id']], 'reason': e['operative_text']}
    if result == 'sale_scheduled':
        dates = _sale_dates(e.get('sale_passages', []))
        r['sale_date'] = next((x for x in reversed(dates) if x), None)
        if not r['sale_date'] and e['calendar_event']: r['sale_date'] = e['date']
        r['reset'] = kind == 'order_resetting_sale'
    if kind == 'suggestion_of_bankruptcy': r['qualification'] = 'Bankruptcy suggested on docket; scope and continuing effect not independently adjudicated.'
    return r


def build_timeline(case, inventory, document_rows, as_of):
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
        e = {'entry_id': ident, 'date': _date(meta.get('eventDate') or item.get('date')),
             'description': description, 'comments': comments, 'filed_by': meta.get('filedBy') or meta.get('filed_by') or 'unknown',
             'listed_parties': meta.get('partiesName') or '', 'index_kind': ik,
             'kind': body_kind or ik, 'kind_source': 'document' if body_kind else 'docket_text',
             'index_agrees': body_kind == ik if body_kind else None,
             'operative_text': title or index_text, 'calendar_event': str(meta.get('eventType', '')).lower() == 'hearing'}
        e['limited_scope'] = bool(re.search(r'(?:dismiss\w*|satisf\w*|releas\w*)[^\n.]{0,100}(?:\bas to\b|\bone defendant\b|\bpartial\b|\bonly\b)|\bpartial (?:dismissal|satisfaction)|\bas to (?:defendant|party)\b', scope_text, re.I))
        if e['calendar_event'] and e['kind'] != 'notice_of_sale': e['kind'] = 'hearing'
        e['sale_passages'] = [index_text] if re.search(r'\bsale\b', index_text, re.I) else []
        body_lines = [line for p in pages for line in str(p.get('text') or '').splitlines()]
        e['motion_disposition_passages'] = [line for line in body_lines if re.search(r'\bmotion\b', line, re.I) and re.search(r'\b(?:is|hereby|be)\s+(?:granted|denied)\b|\b(?:grants|denies)\b', line, re.I)] if e['kind'] in ('final_judgment', 'order_on_motion') else []
        e['sale_passages'] += [line for line in body_lines if re.search(r'\b(?:sale|sell|auction|reset|reschedul\w*)\b', line, re.I)]
        if e['kind'] == 'order_cancelling_sale':
            reasons = [line for line in body_lines if re.search(r'\b(?:because|due to|reason|cancel\w*)\b', line, re.I)]
            if reasons: e['operative_text'] += ' — ' + ' '.join(reasons[:3])
        expected = item.get('expected_documents', meta.get('numberOfDocuments', 0)) or 0
        failed = [p for p in pages if p.get('outcome') not in ('text', 'ocr_text', 'vision_text', 'read_as_label', 'exhibit_divider')]
        e['image_status'] = 'unreadable_pages' if failed else ('read' if pages else ('not_fetched' if expected else 'no_image_indexed'))
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
        if e['image_status'] != 'read': gaps.append({'entry_id': ident, 'kind': e['image_status'], 'reason': 'No image indexed by county.' if not expected and not pages else 'Image not fetched or one or more pages unresolved.', 'pages': [p.get('page') for p in failed]})
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
    day_changes = {}
    for e in entries:
        if not e['date'] or e['date'] > today: continue
        if e['kind'].startswith('motion'):
            pending.append({'type': 'motion', 'entry_id': e['entry_id'], 'date': e['date'], 'description': e['description'], 'status': 'no_matching_order_identified'})
        for passage in e.get('motion_disposition_passages', []):
            topics = ('summary judgment', 'default', 'dismiss', 'cancel', 'attorney fees', "attorney's fees")
            pending[:] = [p for p in pending if not (p['type'] == 'motion' and p['date'] < e['date'] and any(topic in _topic_text(passage) and topic in _topic_text(p['description']) for topic in topics))]
        if e['kind'] in ('order_cancelling_sale', 'order_resetting_sale', 'order_of_dismissal') or (e['kind'] == 'order_on_motion' and re.search(r'\b(granting|denying|granted|denied|awarding)\b', e['operative_text'], re.I)):
            # Only close a motion when its substantive phrase is explicitly repeated.
            text = e['operative_text'].lower()
            topics = ('dismiss', 'summary judgment', 'cancel', 'reset', 'attorney fees', "attorney's fees", 'default')
            pending[:] = [p for p in pending if not (p['type'] == 'motion' and p['date'] < e['date'] and any(topic in _topic_text(text) and topic in _topic_text(p['description']) for topic in topics))]
        if e['kind'] == 'relief_from_stay':
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
    return {'case': case, 'county': 'MIAMI-DADE', 'as_of': today, 'entries': entries, 'status': status,
            'pending': pending, 'amounts': amounts, 'gaps': gaps, 'coverage_complete': not gaps,
            'qualification': 'Status is derived from available docket evidence, not confirmation of a complete court record. Amount extractions are not verified balances or equity inputs.'}


def _topic_text(text):
    return re.sub(r"\battorney(?:s|['’]s)?\b", 'attorney', str(text).lower())


def render_markdown(result):
    def esc(value): return str(value or '').replace('|', '\\|').replace('\n', ' ')
    lines = [f"# Case timeline — {result['case']}", '', f"As of {result['as_of']}. Status: **{result['status']['kind']}**.",
             f"Evidence: {', '.join(result['status']['evidence']) or 'none'}. {result['status']['reason']}", '', result['qualification'], '',
             '| Entry | Date | Kind | Filed by | Description | Image |', '|---|---|---|---|---|---|']
    if 'sale_date' in result['status']:
        lines.insert(4, f"Sale date: {result['status']['sale_date'] or 'unknown'}. Reset: {bool(result['status'].get('reset'))}.")
    for e in result['entries']:
        lines.append('| ' + ' | '.join(esc(e.get(k)) for k in ('entry_id', 'date', 'kind', 'filed_by', 'description', 'image_status')) + ' |')
    for section in ('pending', 'amounts', 'gaps'):
        lines += ['', f'## {section.title()}', '']
        lines += ['- ' + '; '.join(f'{k}: {esc(v)}' for k, v in row.items()) for row in result[section]] or ['No items identified in available evidence; completeness is not implied.']
    return '\n'.join(lines) + '\n'
