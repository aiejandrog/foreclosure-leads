"""Miami whole-case timelines; free OCR and opt-in, cumulatively capped vision."""
import argparse
import hashlib
import json
import math
import os
import re
from datetime import date
from pathlib import Path
from contextlib import nullcontext
from collections import Counter

import document_collectors as DC
import document_queue as DQ
import document_store as DS

COUNTY = 'MIAMI-DADE'


def validate_case(value):
    if not re.fullmatch(r'\d{4}-\d{6}-(?:CA-01|CC-\d{2})', value):
        raise ValueError('Expected a Miami-Dade civil case number')
    return value


def budget_snapshot(path, cap):
    """Observe the shared ledger; never create/reset it or authorize paid reads."""
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError('Vision cap must be finite and positive')
    value = DS.pipeline_load(path)
    report = {'cap_usd': cap, 'paid_requests_this_command': 0,
              'scope': 'shared title-discovery vision ledger', 'path': str(path)}
    if value is None:
        return dict(report, status='unknown', remaining_usd=None,
                    reason='Existing spend ledger unavailable; no paid reading authorized')
    actual = float(value['actual_usd'])
    reserved = sum(float(v) for v in value['reserved'].values())
    if not all(math.isfinite(v) and v >= 0 for v in (actual, reserved)):
        raise ValueError('Invalid shared vision ledger')
    return dict(report, status='observed', actual_usd=actual, reserved_usd=reserved,
                remaining_usd=max(0.0, cap - actual - reserved))


def load_rows(base):
    rows = []
    for path in sorted(Path(base).glob('*.json')):
        if not re.fullmatch(r'[0-9a-f]{64}', path.stem):
            continue
        row = DS.pipeline_load(path)
        if not isinstance(row, dict) or 'manifest' not in row:
            continue
        ref = row.get('source_ref', '')
        if ref.startswith('court:'):
            row['entry_ref'] = ref.split(':', 2)[1]
        rows.append(row)
    return rows


def source_comparison(case, inventory, cache_path):
    """The compact board cache is comparison-only, never timeline evidence."""
    raw = inventory.get('raw') or {}
    entries = raw.get('dockets')
    if ((raw.get('caseNumber') or raw.get('caseNo')) != case or
            not isinstance(entries, list)):
        raise ValueError('Full OCS case payload required; refresh with --collect')
    if [e.get('metadata') for e in inventory.get('entries', [])] != entries:
        raise ValueError('Inventory differs from full OCS entries/comments; refresh with --collect')
    try:
        cache = DS.pipeline_load(cache_path) or {}
        cached = cache.get(case) or {}
        compact = cached.get('ents')
        count = len(compact) if isinstance(compact, list) else None
        cache_status = 'available' if count is not None else 'case_or_cache_missing'
    except (ValueError, OSError, AttributeError):
        cached, count, cache_status = {}, None, 'unreadable_cache'
    return {'timeline_source': 'full OCS GetSingleCaseResult payload',
            'full_ocs_entries': len(entries), 'timeline_entries': len(inventory['entries']),
            'cache_entries': count, 'cache_reported_total': cached.get('n'),
            'cache_path': str(cache_path), 'cache_status': cache_status,
            'cache_date': cached.get('ts'),
            'counts_differ': len(entries) != count if count is not None else None,
            'note': 'Cache is comparison-only; its reported total is not its retained entry count. OCS pagination completeness remains unproven.'}


def acquire(case, base=None, collect=False, docket_cache=None):
    """One complete queue sweep; download/read failures remain named queue gaps."""
    validate_case(case)
    base = Path(base) if base is not None else DS.pipeline_folder(COUNTY, case)
    if collect:
        DC.collect_case_documents(COUNTY, case)
    inventory = DS.pipeline_load(base / 'inventory.json')
    if inventory is None:
        raise ValueError('No existing docket inventory; use --collect to obtain it')
    inventory['source_comparison'] = source_comparison(
        case, inventory, docket_cache or Path(__file__).with_name('dockets.json'))
    with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        count = sum(job['kind'] == 'acquire' for job in queue.jobs(COUNTY, case))
    # The existing worker's default limit is ten. A whole-case pass must cover all jobs.
    DQ.resume_case_documents(COUNTY, case, limit=max(1, count), interpret=False)
    rows = load_rows(base)
    with DQ.DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        jobs = queue.jobs(COUNTY, case)
    # A document the county no longer lists keeps its saved file as a record, but it is not this
    # case's evidence any more: a replaced attachment's old copy must not be read as current.
    retired = {job['source_ref'] for job in jobs
               if job['kind'] == 'acquire' and job['status'] == DQ.SUPERSEDED}
    rows = [row for row in rows if row.get('source_ref') not in retired]
    by_ref = {row.get('source_ref'): row for row in rows}
    for job in jobs:
        if job['kind'] != 'acquire' or job['status'] in ('done', DQ.SUPERSEDED):
            continue
        row = by_ref.get(job['source_ref'])
        if row is None:
            ref = job['source_ref']
            row = {'source_ref': ref, 'entry_ref': ref.split(':', 2)[1] if ref.startswith('court:') else '',
                   'manifest': {}, 'reading': {'pages': []}}
            rows.append(row)
        row['acquisition_status'] = job['status']
        row['acquisition_gap'] = job.get('error') or 'Document acquisition ' + job['status']
    return inventory, rows


def write_timeline(dossier_path, timeline, markdown):
    path = Path(dossier_path)
    json_path = path.with_name(path.stem + '-timeline.json')
    md_path = path.with_name(path.stem + '-timeline.md')
    DS.pipeline_write(json_path, timeline)
    DS._atomic_write_text(md_path, markdown)
    dossier = DS.pipeline_load(path, {'case': timeline['case'], 'county': COUNTY, 'complete': False})
    dossier['whole_case_timeline'] = {'json': str(json_path), 'markdown': str(md_path),
                                    'status': timeline.get('status'),
                                    'entries': len(timeline.get('entries', [])),
                                    'gaps': len(timeline.get('gaps', []))}
    DS.pipeline_write(path, dossier)
    return {'json': str(json_path), 'markdown': str(md_path)}


def read_amounts(rows, base, budget=None, plan=None):
    """Reuse hash-bound private evidence; paid selection requires an explicit budget.

    Paid reads follow the docket plan (document_prioritizer.timeline_read_order), not the order the
    evidence files sort in on disk, and stop at this case's share of the cap: every document the
    share did not reach is a named `budget_exhausted` gap rather than a silent omission.
    """
    import judgment_money as JM
    import miami_timeline_amounts as amounts
    result = {'figures': [], 'gaps': [], 'evidence_files': [], 'amount_checks': []}
    kinds = {str(d.get('entry_id')): d.get('kind') for d in (plan or {}).get('documents') or []}
    if budget is not None:
        import document_prioritizer as DP
        selection = DP.timeline_read_order(plan, rows)
        result['selection'] = {'order': [r.get('source_ref') for r in selection['order']],
                               'deferred': selection['deferred']}
        result['gaps'].extend(dict(item, reason='paid_read_not_selected: ' + item['reason'])
                              for item in selection['deferred']
                              if amounts.amount_page_numbers(
                                  next((r.get('reading') or {} for r in rows
                                        if r.get('source_ref') == item['source_ref']), {})))
        rows = selection['order']
    for row in rows:
        if budget is not None and getattr(budget, 'exhausted', False):
            if amounts.amount_page_numbers(row.get('reading') or {}):
                result['gaps'].append({'source_ref': row.get('source_ref'),
                                       'reason': 'budget_exhausted: this case\'s vision share '
                                                 'was spent before this document'})
            continue
        current_hash = ((row.get('manifest') or {}).get('source_sha256') or
                        (row.get('manifest') or {}).get('sha256'))
        key = hashlib.sha256(str(row.get('source_ref')).encode()).hexdigest()
        path = Path(base) / ('amount-vision-' + key + '.json')
        if budget is None:
            detail = DS.pipeline_load(path)
            if (not detail or not current_hash or detail.get('document_hash') != current_hash or
                    detail.get('source_ref') != row.get('source_ref')):
                continue
            detail = dict(detail)
        else:
            if not amounts.amount_page_numbers(row.get('reading') or {}):
                gap = no_amount_text_gap(row, kinds.get(str(row.get('entry_ref') or '')))
                if gap:
                    result['gaps'].append(gap)
                continue
            detail = amounts.assess_amount_pages(row, budget)
        detail['source_ref'] = row.get('source_ref')
        detail['document_key'] = (row.get('manifest') or {}).get('document_key')
        detail['document_hash'] = current_hash
        detail['entry_id'] = row.get('entry_ref')
        if budget is not None:
            DS.pipeline_write(path, detail)
        result['evidence_files'].append(str(path))
        result['gaps'].extend(dict(gap, source_ref=row.get('source_ref')) for gap in detail.get('gaps', []))
        # The one exact-cents check (judgment_money): a figure inside a run of printed rows that
        # reproduces the document's own printed total is 'in_verified_table'. That is arithmetic
        # agreement on one filing, never an award, an open balance or an equity input.
        checks = JM.verify_document(detail.get('figures') or [], detail.get('grand_totals') or [],
                                    {JM._page_no(p) for p in (detail.get('pages') or {})})
        if detail.get('gaps') or detail.get('errors'):
            checks = [dict(c, ok=False, reason='amount pages have unresolved reading gaps')
                      for c in checks]
        in_table = {r['gid'] for c in checks if c['ok'] for r in c['component_rows']}
        # What the document is, judged the same way the text path judges it (_text_checks_for_row):
        # its own text, never the clerk's label, and never the vision reader's say-so. case_verdict
        # holds a verified image total as a gap unless the document reads as a final judgment by
        # these two tests or is the only read document on its entry. Recomputed from the current
        # reading on every pass, so a cached evidence file cannot carry a stale verdict forward.
        identity = _document_identity(row) if checks else {}
        result.setdefault('amount_checks', []).extend(
            {'source_ref': row.get('source_ref'), 'entry_id': detail['entry_id'],
             'source': 'vision', 'document_key': detail['document_key'],
             'document_hash': detail['document_hash'],
             'document_kind': identity.get('document_kind'),
             'judgment_title': identity.get('judgment_title'),
             'amount': c['amount'], 'page': c['page'], 'ok': c['ok'], 'reason': c['reason'],
             'pages': c['pages'], 'run': c['run'], 'components': c['components'],
             'credits': c['credits'], 'rates': c['rates'], 'subtotals': c['subtotals'],
             # judgment_money reports a printed subtotal its own rows do not reproduce per failed
             # check, and the saved row used to drop it. Faithful passthrough now - but note what
             # it is NOT: verify_document builds its rows with vision_rows, which marks every row
             # `explicit`, and _resolve_subtotal never returns None for an explicit row, so the
             # notes disagreeing_subtotals is collected from are never written on this path. It is
             # always [] here today. 2018-026274's $0.60 reaches a saved check only as the reason
             # 'printed subtotal lacks valid members or disagrees with its own items', which covers
             # an unreadable subtotal and a disagreeing one alike. The breakdown itself lives in
             # miami_judgment's text path as sum_check_disagreeing_subtotals.
             'disagreeing_subtotals': c.get('disagreeing_subtotals') or []}
            for c in checks)
        result['figures'].extend(dict(figure, source_ref=row.get('source_ref'),
            document_key=detail['document_key'], document_hash=detail['document_hash'],
            entry_id=detail['entry_id'],
            verification_status=('in_verified_table'
                                 if 'p%d:%s' % (JM._page_no(figure.get('page')), figure.get('id'))
                                 in in_table else 'unverified'),
            interpretation='Vision-extracted amount; not an accepted judgment or equity input')
            for figure in detail.get('figures', []))
    return result


def _document_identity(row):
    """-> {'document_kind', 'judgment_title'} from the document's own reading.

    Shared by the text and image paths so both are held to one test of "is this the judgment".
    A reading with no text gives kind 'unknown' and title False, which case_verdict holds.
    """
    import document_classify
    manifest = row.get('manifest') or {}
    reading = row.get('reading') or {}
    try:
        kind = document_classify.classify(reading, manifest.get('document_name') or '').get('kind')
        titled = judgment_titled(reading)
    except Exception:                                         # noqa: BLE001 - fails toward a gap
        kind, titled = 'unknown', False
    return {'document_kind': kind, 'judgment_title': titled}


def _stored_bytes_match(manifest):
    """-> True / False / None: do the stored document's bytes still hash to the manifest's digest?

    None means the file is not on this machine (a copied evidence folder, a container), so the hash
    could not be rechecked; the check then carries `hash_rechecked: False` instead of pretending.
    """
    path = manifest.get('path')
    want = manifest.get('source_sha256') or manifest.get('sha256')
    if not path or not want or not os.path.isfile(path):
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() == want
    except OSError:
        return None


# The document's OWN title line, not a phrase somewhere in a line: "AFFIDAVIT IN SUPPORT OF MOTION FOR
# FINAL JUDGMENT" and "NOTICE OF FILING FINAL JUDGMENT" contain the words and are not the judgment.
_JUDGMENT_TITLE_RE = re.compile(
    r'^[\W_]*(?:\d+[.)]\s*)?(?:(?:FIRST|SECOND|THIRD)\s+)?'
    r'(?:(?:AMENDED|CORRECTED|DEFAULT|CONSENT|AGREED|STIPULATED|SUMMARY|IN\s+REM|UNCONTESTED)\s+)*'
    r'FINAL\s+(?:(?:SUMMARY|IN\s+REM)\s+)*JUDGMENT'
    r'(?:\s+OF\s+(?:MORTGAGE\s+)?FORECLOSURE)?'
    r'(?:\s+(?:IN\s+REM|AND\s+ORDER\s+(?:SETTING(?:\s+FORECLOSURE)?|CANCEL\w*|RESETTING)\s+SALE))?'
    r'(?:\s+AND\s+FOR\s+OTHER\s+RELIEF)?\s*[.:]*\s*$', re.I)
_NOT_A_JUDGMENT_RE = re.compile(
    r'\b(AFFIDAVIT|MOTION|NOTICE|CERTIFICATE|PROPOSED|REQUEST|RESPONSE|OBJECTION|IN\s+SUPPORT|'
    r'DENYING|GRANTING|SATISFACTION|VACAT\w+|SET\s+ASIDE|EXHIBIT|STATEMENT|SCHEDULE|COST\s+BILL)\b', re.I)


# The line right under a judgment's title is a caption fragment or the opening sentence. An
# allow-list, because a block-list of instrument words was beaten one new word at a time.
_NEXT_LINE_OK_RE = re.compile(
    r"^[\W_]*(?:THIS\s+(?:CAUSE|ACTION|MATTER)\b|PLAINTIFFS?[,.]?\s*$|DEFENDANTS?[,.]?\s*$|V[S]?\.?\s*$|"
    r"CASE\s+(?:NO|NUMBER)\b|IN\s+THE\s+CIRCUIT\b|CIRCUIT\s+COURT\b|OF\s+(?:MORTGAGE\s+)?FORECLOSURE\b|"
    r"AND\s+ORDER\s+(?:SETTING|CANCEL|RESETTING)|"
    r"[A-Z][A-Z0-9 .&'/-]{2,},?\s*(?:FSB|N\.A\.|INC\.?|LLC|TRUSTEE|ASSOCIATION|COMPANY|CORPORATION)[,.]?\s*$)",
    re.I)
# Wording that marks a sworn statement or a draft, anywhere on the title's page.
_SWORN_OR_SCHEDULE_RE = re.compile(
    r'\b(AFFIANT|SWORN|NOTARY|BEFORE\s+ME|UNDER\s+PENALT\w+|PROPOSED|DRAFT|SUBMITTED\s+BY|PREPARED\s+BY|FOR\s+(?:THE\s+)?(?:COURT[\'\u2019]?S?\s+)?SIGNATURE)\b', re.I)
# Wording that names an affidavit or a payoff schedule. A real judgment's body refers to these ("the
# affidavits filed in support", "amounts due as set forth in...", "Affidavit of Indebtedness", "cost
# declaration"), so they count only in the title block: every line above the title through the decree's
# opening sentence, where a sworn paper or schedule announces itself. With no opening sentence in view the
# block runs to the end of what is read, so a heading is never excused by distance.
_SCHEDULE_REF_RE = re.compile(r'\b(AFFIDAVIT|DECLARATION|PAYOFF|INDEBTEDNESS|AMOUNTS?\s+DUE)\b', re.I)
# A title that opens a quotation ("FINAL JUDGMENT, quoted from a motion or an exhibit) is not the paper's own.
_SCHEDULE_WORD_RE = re.compile(r'\b(AFFIDAVIT|DECLARATION|PAYOFF|INDEBTEDNESS)\b', re.I)


_HEADING_CONNECTORS = {'of', 'and', 'the', 'in', 'to', 'for', 'by', 'on', 'a'}


def _schedule_heading(ln):
    """A line that is itself the heading of an affidavit, declaration or payoff schedule. Decided by
    wording and structure, never by punctuation: a few words, every one capitalised (or a small
    connector), at least one of them AFFIDAVIT / DECLARATION / PAYOFF / INDEBTEDNESS. A judgment's
    recital ("Affidavit of Indebtedness filed in support of the Motion.") carries lowercase running
    text, figures or a name of a different shape, and is not a heading."""
    words = ln.split()
    if not words or len(words) > 10 or not _SCHEDULE_WORD_RE.search(ln):
        return False
    for w in words:
        core = w.strip('.:;,()"\'\u201c\u201d')
        if not core or not re.fullmatch(r"[A-Za-z&/'\u2019-]+", core):
            return False
        if not (core[0].isupper() or core.lower() in _HEADING_CONNECTORS):
            return False
    return True


_QUOTE_OPEN_RE = re.compile(r'^\s*[>"\'“‘«]')
_OPENING_SENTENCE_RE = re.compile(r'^[\W_]*(?:THIS\s+(?:CAUSE|ACTION|MATTER)\b|ORDERED\b|IT\s+IS\b)', re.I)
# What only a decree says. An affidavit or motion does not order the clerk to sell.
_DECREE_RE = re.compile(r'ORDERED\s+AND\s+ADJUDGED|let\s+execution\s+issue|clerk\s+shall\s+sell|'
                        r'shall\s+sell\s+the\s+(?:subject\s+)?property', re.I)


# What may sit directly above a judgment's title: caption (court heading, case number, division, party
# lines) or a bare modifier line ("AMENDED"). Anything else ("ORDER APPROVING", "CLERK'S") means the
# title is the tail of another heading. An allow-list, walked upward until the caption starts.
_BARE_MODIFIER_RE = re.compile(
    r'^[\W_]*(?:(?:FIRST|SECOND|THIRD|AMENDED|CORRECTED|DEFAULT|CONSENT|AGREED|STIPULATED|SUMMARY|IN\s+REM|'
    r'UNCONTESTED)\s*)+$', re.I)
_CAPTION_LINE_RE = re.compile(
    r"^[\W_]*(?:CASE\s+(?:NO|NUMBER)\b|DIVISION\b|(?:CIVIL|GENERAL\s+JURISDICTION)\s+DIVISION\b|SECTION\b|COMPLEX\b|IN\s+THE\s+CIRCUIT\b|"
    r"IN\s+AND\s+FOR\b|THE\s+\w+\s+JUDICIAL\b|.*\bCOUNTY,?\s+FLORIDA\b|"
    r"V[S]?\.?\s*$|PLAINTIFFS?[,.]?\s*$|.*\bDEFENDANTS?[,.]?\s*$|\d+\s*$|"
    r".*\b(?:FSB|N\.A\.|INC|LLC|TRUSTEE|ASSOCIATION|COMPANY|CORPORATION|ET\s+AL)\b\.?,?\s*$|"
    r"[A-Z][A-Z0-9 .&'/#;()-]+,\s*$)", re.I)


_INSTRUMENT_START_RE = re.compile(
    r'\b(ORDER|STIPULATION|STIPULATED\s+(?:MOTION|ORDER)|REPORT|CLERK|JOINT|NOTICE|MOTION|REQUEST|'
    r'RESPONSE|REPLY|AFFIDAVIT|DECLARATION|CERTIFICATE|SUGGESTION|PETITION|CLAIM|JUDGMENT\s+LIEN|'
    r'AGREED|SETTLEMENT|MEMORANDUM|OBJECTION|SATISFACTION|RELEASE|WRIT|SUMMONS|SUBPOENA|MAGISTRATE|LIS\s+PENDENS|'
    r'APPROV\w*|ADOPT\w*|REINSTAT\w*|CANCEL\w*|GRANT\w*|DENY\w*|VACAT\w*)\b', re.I)
_RULE_LINE_RE = re.compile(r'^[\s_/\\.-]*$')
# "Plaintiff(s) / Petitioner(s)," "Defendant(s) / Respondent(s)." county-court caption role lines.
_ROLE_LINE_RE = re.compile(
    r'^[\W_]*(?:PLAINTIFF|DEFENDANT|PETITIONER|RESPONDENT)(?:\(S\)|S)?'
    r'(?:\s*/\s*(?:PLAINTIFF|DEFENDANT|PETITIONER|RESPONDENT)(?:\(S\)|S)?)?[,.]?\s*$', re.I)
# County-court order header fields printed under the title.
_ORDER_HEADER_FIELD_RE = re.compile(
    r'^[\W_]*(?:MOTION\s+(?:NUMBER|NO\.?)|HEAR(?:ING)?\s+DATE|DOCKET\s+INDEX\s+(?:NUMBER|NO\.?)|DATE\s+FILED|'
    r'FULL\s+NAME\s+OF\s+MOTION)\s*:', re.I)
_VS_LINE_RE = re.compile(r'^[\W_]*V[S]?\.?\s*$', re.I)
_PAREN_LINE_RE = re.compile(r'^\([^()]{1,40}\)$')

def _title_block_above_ok(above):
    """`above` = the lines (oldest first) directly above the title. Walk up past bare modifiers; the
    first other line must be caption. No lines above is fine (the title opens the page)."""
    vs_at = max([k for k, ln in enumerate(above) if _VS_LINE_RE.match(ln)] or [-1])
    for pos in range(len(above) - 1, -1, -1):
        ln = above[pos]
        if _BARE_MODIFIER_RE.match(ln):
            continue
        if _RULE_LINE_RE.match(ln) or _ROLE_LINE_RE.match(ln):
            continue                                     # a caption's closing rule or "Defendant(s)."
        if _JUDGMENT_TITLE_RE.match(ln) and not _NOT_A_JUDGMENT_RE.search(ln):
            continue                                     # the title printed twice, or wrapped in two titles
        # A defendant's name line under "vs." is caption whatever it looks like, unless it names an instrument.
        in_party_zone = pos > vs_at >= 0
        return ((bool(_CAPTION_LINE_RE.match(ln)) or in_party_zone) and not _NOT_A_JUDGMENT_RE.search(ln)
                and not _INSTRUMENT_START_RE.search(ln))
    return len(above) < 12


def judgment_titled(reading):
    """True when the first readable page leads with a final-judgment TITLE (the whole line, an
    allowed line after it), carries no affidavit or payoff-schedule wording anywhere on that page,
    and the document contains decree wording. A heuristic that only ever fails toward a gap: a
    real judgment with an unusual caption is held, never accepted wrongly on purpose."""
    pages = [p for p in (reading or {}).get('pages') or []
             if isinstance(p, dict) and p.get('outcome') in ('text', 'ocr_text')]
    if not pages:
        return False
    if pages[0].get('page') not in (None, 1):
        return False                                     # page 1 unread: its heading and oath are unknown
    if not any(_DECREE_RE.search(str(p.get('text') or '')) for p in pages):
        return False
    first = str(pages[0].get('text') or '')
    if _SWORN_OR_SCHEDULE_RE.search(first):
        return False
    if len(pages) > 1:
        # A title can sit alone on a cover page with the paper's own heading and oath on the next one.
        second = [ln.strip() for ln in str(pages[1].get('text') or '').splitlines() if ln.strip()]
        if _SWORN_OR_SCHEDULE_RE.search('\n'.join(second[:25])) or any(_schedule_heading(ln) for ln in second):
            return False
    every_line = [ln.strip() for ln in first.splitlines() if ln.strip()]
    lines = every_line[:25]                              # the title is searched for here; headings everywhere

    def opens(k):
        # The decree's opening sentence, even when wrapped ("THIS" / "ACTION was heard").
        return bool(_OPENING_SENTENCE_RE.match(lines[k])
                    or (k + 1 < len(lines) and _OPENING_SENTENCE_RE.match(lines[k] + ' ' + lines[k + 1])))

    if any(_schedule_heading(ln) for ln in every_line):
        return False                                     # an affidavit's or schedule's own heading, wherever it sits
    for i, ln in enumerate(lines):
        if _QUOTE_OPEN_RE.match(ln):
            continue
        if len(ln) <= 100 and _JUDGMENT_TITLE_RE.match(ln) and not _NOT_A_JUDGMENT_RE.search(ln):
            # A heading wrapped over two lines ("ORDER GRANTING MOTION FOR" / "FINAL JUDGMENT"): the
            # title block includes the lines above, so they must not name another instrument or end
            # on a connector that hands the sentence to this line.
            if not _title_block_above_ok(lines[max(0, i - 12):i]):
                continue
            # Nothing above the title, back to the top of the page, may open as another instrument
            # ("NOTICE OF FILING", "MOTION FOR"), and the title is not introduced by a lead-in colon.
            # Party names are not read here (a plaintiff called "GRANT PROPERTIES"): only what follows the
            # caption's closing line (rule or "Defendant(s).").
            closed = max([k for k, a in enumerate(lines[:i])
                          if _RULE_LINE_RE.match(a) or _ROLE_LINE_RE.match(a) or re.match(r'^[\W_]*DEFENDANTS?\b', a, re.I)] or [-1])
            if any((_NOT_A_JUDGMENT_RE.match(a) or _INSTRUMENT_START_RE.match(a))
                   and not _BARE_MODIFIER_RE.match(a) and not _JUDGMENT_TITLE_RE.match(a) for a in lines[closed + 1:i]) \
                    or (i and lines[i - 1].endswith(':')):
                continue
            # The title block runs from the top of the page to where the opening sentence of the decree
            # begins ("THIS ACTION was heard..."), or to the end of what is read when none is in view.
            end = i + 1
            while end < len(lines) and not opens(end):
                end += 1
            if _SCHEDULE_REF_RE.search(' '.join(lines[:end])):
                continue
            j = i + 1
            while j < len(lines) and j < i + 7 and _ORDER_HEADER_FIELD_RE.match(lines[j]):
                j += 1                                   # court-form header fields printed under the title
            rest = lines[j:j + 2]
            if not rest or ((_NEXT_LINE_OK_RE.match(rest[0]) or _PAREN_LINE_RE.match(rest[0])
                             or (len(rest) > 1 and _NEXT_LINE_OK_RE.match(rest[0] + ' ' + rest[1]))
                             or (_JUDGMENT_TITLE_RE.match(rest[0]) and len(rest[0]) <= 100))
                            and not any(_NOT_A_JUDGMENT_RE.match(nxt) for nxt in rest)):
                return True
    return False


def _text_checks_for_row(case, row):
    import document_classify
    import miami_judgment as MJ
    ref = str(row.get('source_ref') or '')
    parts = ref.split(':')
    if len(parts) < 3 or parts[0] != 'court' or not parts[1]:
        return []
    manifest = row.get('manifest') or {}
    reading = row.get('reading') or {}
    if not reading.get('pages'):
        return []
    doc_hash = manifest.get('source_sha256') or manifest.get('sha256')
    candidates = [c for c in MJ.judgment_amount_candidates(reading) if not c.get('composed')]
    if not candidates:
        return []
    # What the document's own text says it is, never the clerk's label. The bytes are re-hashed
    # only when a candidate would otherwise verify (a large PDF is not read for nothing).
    kind = document_classify.classify(reading, manifest.get('document_name') or '').get('kind')
    intact = _stored_bytes_match(manifest) if any(c.get('sum_check') for c in candidates) else None
    base = {'case': case, 'entry_id': parts[1], 'source_ref': ref,
            'document_key': manifest.get('document_key'), 'document_hash': doc_hash,
            'document_kind': kind, 'judgment_title': judgment_titled(reading),
            'source': 'document_text', 'hash_rechecked': intact is not None}
    out = []
    for c in candidates:
        ok = bool(c.get('sum_check'))
        reason = c.get('sum_check_reason')
        if ok and not doc_hash:
            ok, reason = False, 'the document has no recorded hash, so this reading cannot be tied to it'
        elif ok and intact is False:
            ok, reason = False, ('the stored document no longer hashes to the digest this reading '
                                 'was made from')
        out.append(dict(base, amount=c['amount'], page=c['page'], ok=ok, reason=reason,
                        pages=c.get('sum_check_pages') or [], run=c.get('sum_check_run'),
                        components=c.get('sum_check_components') or [],
                        credits=c.get('sum_check_credits') or [],
                        rates=c.get('sum_check_rates') or [],
                        subtotals=c.get('sum_check_subtotals') or [],
                        disagreeing_subtotals=c.get('sum_check_disagreeing_subtotals') or [],
                        text_source=c.get('text_source'), match=c.get('match')))
    return out


def text_amount_checks(case, rows):
    """Exact-cents checks of printed judgment totals read from a court document's own TEXT.

    The vision path saves `amount_vision.amount_checks`; the text path used to stop at
    `replay_money_check`'s report, so a total the arithmetic verified from embedded or OCR text never
    reached `case_verdict` (acceptance run 2026-10-03: 5 of 7 cases). This is the same
    `judgment_money` contract on the same rows, saved with the timeline.

    Attribution is per check: case, entry (taken from the court source_ref, never from a field a
    caller can set separately), source_ref, document_key, document_hash, page(s), the text layer and
    what the document's own text reads as (`document_kind`). Only `court:` documents are checked,
    so a recorded instrument's principal can never be mistaken for a judgment total (verify-12
    defect D5). A document whose stored bytes no longer hash to its manifest digest is saved as a
    FAILED check, not dropped. A row that cannot be read is saved as a failed check naming the error:
    one malformed row must not take the whole case build down.
    """
    out = []
    for row in rows:
        try:
            out.extend(_text_checks_for_row(case, row))
        except ImportError:
            raise                                                 # a broken install is not one bad row
        except Exception as exc:                                  # noqa: BLE001 - named, never swallowed
            ref = str((row or {}).get('source_ref') if isinstance(row, dict) else '')
            parts = ref.split(':')
            if len(parts) >= 3 and parts[0] == 'court' and parts[1]:
                out.append({'case': case, 'entry_id': parts[1], 'source_ref': ref,
                            'source': 'document_text', 'ok': False, 'amount': None, 'page': None,
                            'reason': 'the text of this document could not be checked: %s: %s'
                                      % (type(exc).__name__, str(exc)[:120]),
                            'pages': [], 'run': None, 'components': [], 'credits': [],
                            'rates': [], 'subtotals': [], 'disagreeing_subtotals': []})
    return out


def attach_text_checks(timeline, case, rows):
    """Save the text-path checks with the timeline, where `case_verdict` reads `amount_checks`."""
    timeline['amount_checks'] = text_amount_checks(case, rows)
    return timeline


_AMOUNT_KINDS = ('final_judgment', 'judgment')


def no_amount_text_gap(row, kind):
    """A judgment whose pages carry no dollar text is a gap, not "no amount" (Greptile on #53):
    its money page may be a scan OCR could not read, or a watermark. Other filings with no dollar
    text are not gaps; most have no amount."""
    import document_coverage as COV
    # Only the judgment itself must state an amount; a motion for summary judgment need not.
    if kind not in _AMOUNT_KINDS:
        return None
    pages = (row.get('reading') or {}).get('pages') or []
    unread = [p.get('page') for p in pages if not COV.page_is_read(p)]
    return {'source_ref': row.get('source_ref'),
            'reason': ('amount_page_unreadable: no dollar text found and %d of %d page(s) unread '
                       '(%s)' % (len(unread), len(pages), ', '.join(str(p) for p in unread[:8]))
                       if unread or not pages else
                       'amount_text_not_found: every page was read and none carries a dollar figure')}


def keep_cached_amounts(paid, cached):
    """Put back the saved figures of documents the paid pass did not read.

    The paid pass reads only the documents it selects, up to this case's share. A document it
    deferred or never reached keeps the figures its saved, hash-checked evidence already gives
    (Greptile on #53: they vanished from the final timeline). Its 'not selected' or
    'budget exhausted' gap is dropped, because the saved evidence answers it."""
    read = set(paid.get('evidence_files') or [])
    kept = {}
    for path in cached.get('evidence_files') or []:
        if path not in read:
            kept[path] = None
    if not kept:
        return paid
    refs = {f.get('source_ref') for f in cached.get('figures') or []} | {
        c.get('source_ref') for c in cached.get('amount_checks') or []}
    refs = {r for r in refs if _evidence_path_of(r, cached) in kept}
    out = dict(paid)
    out['evidence_files'] = list(paid.get('evidence_files') or []) + list(kept)
    out['figures'] = list(paid.get('figures') or []) + [
        f for f in cached.get('figures') or [] if f.get('source_ref') in refs]
    out['amount_checks'] = list(paid.get('amount_checks') or []) + [
        c for c in cached.get('amount_checks') or [] if c.get('source_ref') in refs]
    out['gaps'] = [g for g in paid.get('gaps') or []
                   if not (g.get('source_ref') in refs and str(g.get('reason') or '').startswith(
                       ('paid_read_not_selected', 'budget_exhausted')))] + [
        g for g in cached.get('gaps') or [] if g.get('source_ref') in refs]
    return out


def _evidence_path_of(source_ref, cached):
    key = hashlib.sha256(str(source_ref).encode()).hexdigest()
    return next((p for p in cached.get('evidence_files') or []
                 if Path(p).name == 'amount-vision-' + key + '.json'), None)


def summary_counts(rows, timeline):
    pages = [p for row in rows for p in (row.get('reading') or {}).get('pages', [])]
    return {'entries': len(timeline.get('entries', [])),
            'documents': sum(bool(row.get('manifest', {}).get('pages')) for row in rows),
            'pages': len(pages),
            'ocr_text_pages': sum(p.get('outcome') == 'ocr_text' or
                (p.get('supplemental_ocr') or {}).get('outcome') == 'ocr_text' for p in pages),
            'supplemental_ocr_pages': sum(bool(p.get('supplemental_ocr')) for p in pages),
            'unreadable_source_pages': sum(p.get('outcome') == 'unreadable_source' for p in pages),
            'gaps': len(timeline.get('gaps', [])),
            'pending_types': dict(Counter(p.get('type', 'unknown') for p in timeline.get('pending', [])))}


def recorded_copies(case):
    """Stored Official Records documents for this case, each with the kind its own text reads
    as and its recording date: the pool an unreadable court filing's public copy can come from."""
    import document_classify
    out = []
    for manifest, reading in DS.stored_documents(COUNTY, case):
        ref = str(manifest.get('source_ref') or '')
        if not ref.startswith('official_records/'):
            continue
        out.append({'source_ref': ref, 'reading': reading,
                    'recorded_date': (manifest.get('record_key') or {}).get('rec_date'),
                    'kind': document_classify.classify(reading).get('kind')})
    return out


def case_coverage(case, inventory, rows, timeline):
    import document_coverage
    return document_coverage.coverage(inventory, rows, timeline.get('entries'),
                                      recorded_copies(case), case)


def fetch_alternate_copies(case, coverage, ocr=None, collector=None, collect=None):
    """E1: fetch the cited public copies coverage() asked for, free. -> the fetch report, or None
    when nothing is cited. Never raises: a failed fetch stays a named row."""
    import document_coverage
    needed = [n for n in (coverage or {}).get('alternate_copy_needed') or [] if n.get('cited_book_page')]
    if not needed:
        return None
    try:
        return document_coverage.fetch_alternates(needed, case, ocr=ocr, collector=collector, collect=collect)
    except Exception as exc:
        return [{'status': 'error', 'reason': '%s: %s' % (type(exc).__name__, str(exc)[:120])}]


def timeline_case(case, as_of, collect=False, docket_cache=None, shared=None, ledger=None,
                  cap=None, fetch_alternates=None):
    """One case's whole-case timeline: acquire, free reading, then (with `shared`, a
    document_case_budget.CaseAllocator) paid amount reads within this case's share.

    The one per-case body behind both this command and `run_documents --backfill --timeline`, so
    the orchestrated run and the standalone run cannot drift apart. -> (timeline, paths)
    """
    import miami_case_timeline
    import miami_timeline_ocr
    import document_prioritizer
    import run_documents
    as_of = as_of.isoformat() if hasattr(as_of, 'isoformat') else str(as_of)
    budget = shared.for_case(case) if shared is not None else None
    inventory, rows = acquire(case, collect=collect,
                              docket_cache=docket_cache or Path(__file__).with_name('dockets.json'))
    base = DS.pipeline_folder(COUNTY, case)
    rows = [miami_timeline_ocr.supplement(row, base / 'timeline-ocr') for row in rows]
    timeline = miami_case_timeline.build_timeline(case, inventory, rows, as_of=as_of)
    timeline['source_comparison'] = inventory.get('source_comparison')
    timeline['coverage'] = case_coverage(case, inventory, rows, timeline)
    attach_text_checks(timeline, case, rows)
    # E1: auto-fetch the public OR copies the docket cites for unread attachments. Free (index CFN +
    # anonymous image endpoint). On by default when this run may use the network (--collect).
    if (collect if fetch_alternates is None else fetch_alternates):
        fetched = fetch_alternate_copies(case, timeline['coverage'], ocr=DS.winocr if os.name == 'nt' else None)
        if fetched is not None:
            timeline['coverage'] = case_coverage(case, inventory, rows, timeline)
            timeline['coverage']['alternate_fetch'] = fetched
    # Preserve the free whole-case analysis even if a paid reader fails.
    if ledger is not None:
        timeline['vision_budget'] = budget_snapshot(ledger, cap)
    timeline['counts'] = summary_counts(rows, timeline)
    cached_amounts = read_amounts(rows, base)
    if cached_amounts['evidence_files']:
        timeline['amount_vision'] = cached_amounts
        timeline.setdefault('gaps', []).extend(cached_amounts['gaps'])
        timeline.setdefault('amounts', []).extend(cached_amounts['figures'])
        if cached_amounts['gaps']:
            timeline['coverage_complete'] = False
    write_timeline(run_documents.dossier_path(COUNTY, case), timeline,
                   miami_case_timeline.render_markdown(timeline))
    if budget is not None:
        # Rebuild before replacing cached figures so the paid refresh cannot duplicate them.
        timeline = miami_case_timeline.build_timeline(case, inventory, rows, as_of=as_of)
        timeline['source_comparison'] = inventory.get('source_comparison')
        _alt = (timeline.get('coverage') or {}).get('alternate_fetch')
        timeline['coverage'] = case_coverage(case, inventory, rows, timeline)
        attach_text_checks(timeline, case, rows)
        if _alt is not None:
            timeline['coverage']['alternate_fetch'] = _alt
        try:
            plan = document_prioritizer.prioritize(case, inventory, as_of)
        except ValueError:
            plan = None      # every row is then deferred as not_in_docket_plan
        amounts = keep_cached_amounts(read_amounts(rows, base, budget, plan=plan), cached_amounts)
        shared.finish(case)
        timeline['amount_vision'] = amounts
        timeline.setdefault('gaps', []).extend(amounts['gaps'])
        timeline.setdefault('amounts', []).extend(amounts['figures'])
        if amounts['gaps']:
            timeline['coverage_complete'] = False
    spending = (budget_snapshot(ledger, cap) if ledger is not None
                else {'status': 'free_run', 'paid_requests_this_command': 0})
    if budget is not None:
        spending['paid_requests_this_command'] = budget.calls
        spending['case_share'] = shared.report(case)
    timeline['vision_budget'] = spending
    timeline['counts'] = summary_counts(rows, timeline)
    paths = write_timeline(run_documents.dossier_path(COUNTY, case), timeline,
                           miami_case_timeline.render_markdown(timeline))
    print(json.dumps({'case': case, 'status': timeline.get('status'), 'files': paths,
                      'counts': timeline['counts'], 'source_comparison': timeline.get('source_comparison'),
                      'vision_budget': spending}), flush=True)
    return timeline, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', required=True)
    parser.add_argument('--collect', action='store_true', help='Refresh using the existing county collector')
    parser.add_argument('--docket-cache', type=Path, default=Path(__file__).with_name('dockets.json'),
                        help='Compact cache to compare only; never used to build timeline')
    parser.add_argument('--as-of', default=date.today().isoformat(), type=date.fromisoformat)
    parser.add_argument('--vision-max-spend', type=float, default=None,
                        help='Existing shared cumulative cap, at most $1. Required with --vision; '
                             'without --vision it is optional and only reported')
    parser.add_argument('--vision', action='store_true', help='Read only amount-bearing pages within shared cap')
    parser.add_argument('--no-fetch-alternates', action='store_true',
                        help='with --collect, do NOT fetch the cited public OR copies of unread attachments')
    args = parser.parse_args(argv)
    import case_review
    try:
        cases = list(dict.fromkeys(validate_case(case) for case in args.case))
        ledger = case_review.output_path('title_discovery/vision-budget.json')
        if args.vision_max_spend is None or args.vision_max_spend == 0:
            # A free run: nothing is authorized, so there is no cap to observe against.
            if args.vision:
                raise ValueError('--vision needs a positive --vision-max-spend')
            ledger, args.vision_max_spend = None, None
            spending = {}
        else:
            spending = budget_snapshot(ledger, args.vision_max_spend)
        if (args.vision_max_spend or 0) > 1:
            raise ValueError('This run is authorized for at most $1 cumulative vision spend')
        if args.vision and spending['status'] != 'observed':
            raise ValueError('Existing shared vision ledger required; refusing to reset cumulative spend')
    except ValueError as exc:
        parser.error(str(exc))
    from document_backfill import State, PersistentBudget
    from document_case_budget import CaseAllocator
    with (State(ledger) if args.vision else nullcontext()) as state:
        # One share per named case: the first case cannot spend what the others were given.
        shared = (CaseAllocator(PersistentBudget(args.vision_max_spend, state), cases)
                  if args.vision else None)
        for case in cases:
            timeline_case(case, args.as_of, collect=args.collect, docket_cache=args.docket_cache,
                          shared=shared, ledger=ledger, cap=args.vision_max_spend,
                          fetch_alternates=(args.collect and not args.no_fetch_alternates))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
