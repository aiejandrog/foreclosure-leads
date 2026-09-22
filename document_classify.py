"""document_classify — what IS this document, decided from its own text.

WHY THIS EXISTS
Until now the only answer to "what is this document?" was the string the clerk typed into the
index, copied through untouched. The 2026-09-22 pilot is the argument against trusting it: the
five-page Garden Lake Towers FINAL JUDGMENT is indexed `DADE COURT PAPER - DCP` with folio 0. An
index label is a filing clerk's keystroke. It is not a reading.

CLAUDE.md is explicit — "Document metadata and keyword signals must never be represented as
documents read or verified findings" — so this module classifies from PAGE TEXT and nothing else.
The index label is carried alongside the verdict, and whether the two agree is itself reported:
a disagreement is the interesting case, not an error.

WHAT IT REFUSES TO DO
A document whose pages were never read gets `unknown`, `basis='not_read'`, and no amount of index
label moves it. That is the whole point: every Miami judgment is a scan, so without OCR this
module correctly says "I do not know what this is" on the most important document in the case.
"""
import re

# kind -> (title patterns, corroborating patterns)
# A TITLE is the line a recorded instrument leads with. Corroborators are phrases that belong to
# that instrument's body and to few others; they turn a title seen deep in a document from a guess
# into a reading, and they carry a document whose first page is a cover sheet.
RULES = {
    'final_judgment': (
        [r'\b(SUMMARY\s+)?FINAL\s+JUDGMENT\b', r'\bIN\s+REM\s+FINAL\s+JUDGMENT\b',
         r'\bJUDGMENT\s+OF\s+FORECLOSURE\b', r'\bFINAL\s+JUDGMENT\s+OF\s+FORECLOSURE\b'],
        [r'\blet\s+execution\s+issue\b', r'\bORDERED\s+AND\s+ADJUDGED\b',
         r'\bthere\s+is\s+due\b', r'\bforeclosure\s+sale\b', r'\bclerk\s+shall\s+sell\b',
         r'\btotal\s+sum\b']),
    'lis_pendens': (
        [r'\bNOTICE\s+OF\s+LIS\s+PENDENS\b', r'\bLIS\s+PENDENS\b'],
        [r'\baction\s+has\s+been\s+commenced\b', r'\bTO\s+THE\s+DEFENDANTS?\b',
         r'\bnotice\s+is\s+hereby\s+given\b']),
    'mortgage': (
        [r'\bTHIS\s+MORTGAGE\b', r'\bMORTGAGE\s+DEED\b', r'\bOPEN[- ]END\s+MORTGAGE\b'],
        [r'\bSecurity\s+Instrument\b', r'\bBorrower\b', r'\bLender\b', r'\bpromissory\s+note\b',
         r'\bprincipal\s+sum\b']),
    'assignment_of_mortgage': (
        [r'\bASSIGNMENT\s+OF\s+MORTGAGE\b'],
        [r'\bassigns?,?\s+and\s+transfers?\b', r'\bassignor\b', r'\bassignee\b']),
    'satisfaction_of_mortgage': (
        [r'\bSATISFACTION\s+OF\s+MORTGAGE\b', r'\bRELEASE\s+OF\s+MORTGAGE\b',
         r'\bDISCHARGE\s+OF\s+MORTGAGE\b'],
        [r'\bpaid\s+in\s+full\b', r'\bhereby\s+cancels?\b', r'\bsatisfied\b',
         r'\bfully\s+paid\b']),
    'deed': (
        [r'\b(SPECIAL\s+)?WARRANTY\s+DEED\b', r'\bQUIT[- ]?CLAIM\s+DEED\b',
         r"\bTRUSTEE'?S?\s+DEED\b", r'\bPERSONAL\s+REPRESENTATIVE\'?S?\s+DEED\b'],
        [r'\bgrantor\b', r'\bgrantee\b', r'\bconveys?\s+and\s+warrants?\b',
         r'\bin\s+consideration\s+of\b']),
    'certificate_of_title': (
        [r'\bCERTIFICATE\s+OF\s+TITLE\b'],
        [r'\bno\s+objections\b', r'\bsold\s+the\s+property\b', r'\bconfirms?\s+the\s+sale\b']),
    'certificate_of_sale': (
        [r'\bCERTIFICATE\s+OF\s+SALE\b'],
        [r'\bpublic\s+sale\b', r'\bhighest\s+bidder\b']),
    'hoa_lien': (
        [r'\bCLAIM\s+OF\s+LIEN\b'],
        [r'\bassessments?\b', r'\bcondominium\s+association\b', r'\bhomeowners?\s+association\b',
         r'\bmaintenance\s+fees?\b']),
    'federal_tax_lien': (
        [r'\bNOTICE\s+OF\s+FEDERAL\s+TAX\s+LIEN\b'],
        [r'\bInternal\s+Revenue\b', r'\bunpaid\s+balance\s+of\s+assessments?\b']),
    'tax_deed_or_certificate': (
        [r'\bTAX\s+DEED\b', r'\bTAX\s+CERTIFICATE\b', r'\bNOTICE\s+OF\s+TAX\s+LIEN\b'],
        [r'\bdelinquent\s+taxes?\b', r'\btax\s+collector\b']),
    'code_enforcement_lien': (
        [r'\bCODE\s+ENFORCEMENT\b', r'\bORDER\s+IMPOSING\s+(A\s+)?(FINE|PENALTY)\b'],
        [r'\bviolation\b', r'\bfine\s+of\b', r'\bspecial\s+magistrate\b']),
    'order': (
        [r'\bORDER\s+(GRANTING|DENYING|ON|SETTING|CANCELL?ING|VACATING|APPROVING)\b',
         r'\bAGREED\s+ORDER\b'],
        [r'\bORDERED\s+AND\s+ADJUDGED\b', r'\bhearing\s+(was\s+)?held\b']),
}

# Compiled once; page-1 title hits are weighted highest because a recorded instrument leads with
# its own name, and a title word appearing deep in a document is usually a reference to a DIFFERENT
# instrument ("the mortgage recorded in O.R.B. ...").
_RULES = {kind: ([re.compile(p, re.I) for p in titles], [re.compile(p, re.I) for p in body])
          for kind, (titles, body) in RULES.items()}

TITLE_ON_FIRST_PAGE = 5
TITLE_ELSEWHERE = 2
CORROBORATOR = 1
MIN_SCORE = 3           # below this we do not claim to know
MIN_MARGIN = 2          # and the runner-up must be this far behind

# A satisfaction, an assignment and a final judgment all QUOTE the mortgage they act on, so
# mortgage language inside them is expected and must not win. When a more specific instrument
# carries its own title and clears MIN_SCORE, the generic one it names is out of contention.
SUBSUMED_BY = {
    'mortgage': ('satisfaction_of_mortgage', 'assignment_of_mortgage', 'final_judgment'),
    'order': ('final_judgment',),
}

# The clerk's own label, mapped to the same vocabulary, PURELY so agreement can be reported.
# Never used to decide anything.
INDEX_HINTS = [
    (re.compile(r'\bJUDG', re.I), 'final_judgment'),
    (re.compile(r'\bLIS\s*PEND', re.I), 'lis_pendens'),
    (re.compile(r'\bSATISFACTION|RELEASE\b', re.I), 'satisfaction_of_mortgage'),
    (re.compile(r'\bASSIGNMENT\b', re.I), 'assignment_of_mortgage'),
    (re.compile(r'\bMORTGAGE\b', re.I), 'mortgage'),
    (re.compile(r'\bCERT.*TITLE\b', re.I), 'certificate_of_title'),
    (re.compile(r'\bCERT.*SALE\b', re.I), 'certificate_of_sale'),
    (re.compile(r'\bDEED\b', re.I), 'deed'),
    (re.compile(r'\bLIEN\b', re.I), 'hoa_lien'),
]


def index_kind(label):
    for pattern, kind in INDEX_HINTS:
        if pattern.search(str(label or '')):
            return kind
    return None


def _readable(reading):
    return [p for p in reading.get('pages', []) if p.get('outcome') in ('text', 'ocr_text')]


def classify(reading, index_label=''):
    """-> a verdict dict. `kind` is 'unknown' whenever the text does not clearly say."""
    label_kind = index_kind(index_label)
    pages = _readable(reading)
    base = {'index_label': index_label or None, 'index_label_kind': label_kind,
            'basis': 'document_text', 'evidence': [], 'scores': {}}
    if not pages:
        # The document was never read. The clerk's label is NOT a fallback — that substitution is
        # exactly what this module exists to stop.
        base.update({'kind': 'unknown', 'confidence': 'none', 'basis': 'not_read',
                     'why': 'no page of this document yielded text; nothing has read it',
                     'index_agrees': None})
        return base

    scores, evidence = {}, {}
    for kind, (titles, body) in _RULES.items():
        score = 0
        hits = []
        for page in pages:
            first = page['page'] == pages[0]['page']
            for line in (page.get('text') or '').splitlines():
                for pattern in titles:
                    match = pattern.search(line)
                    if match:
                        score += TITLE_ON_FIRST_PAGE if first else TITLE_ELSEWHERE
                        hits.append({'page': page['page'], 'passage': line.strip()[:240],
                                     'matched': match.group(0), 'as': 'title'})
                        break
            text = page.get('text') or ''
            for pattern in body:
                match = pattern.search(text)
                if match:
                    score += CORROBORATOR
                    if len(hits) < 6:
                        hits.append({'page': page['page'], 'matched': match.group(0),
                                     'as': 'corroborator'})
        if score:
            scores[kind] = score
            evidence[kind] = hits
    base['scores'] = scores
    if not scores:
        base.update({'kind': 'unknown', 'confidence': 'none', 'index_agrees': None,
                     'why': 'pages were read but match no known instrument'})
        return base

    for generic, specifics in SUBSUMED_BY.items():
        if generic not in scores:
            continue
        for specific in specifics:
            if scores.get(specific, 0) >= MIN_SCORE and any(
                    h.get('as') == 'title' for h in evidence.get(specific, [])):
                scores.pop(generic, None)
                break
    if not scores:
        base.update({'kind': 'unknown', 'confidence': 'none', 'index_agrees': None,
                     'why': 'pages were read but match no known instrument'})
        return base

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, top_score = ranked[0]
    runner, runner_score = ranked[1] if len(ranked) > 1 else (None, 0)
    if top_score < MIN_SCORE or (top_score - runner_score) < MIN_MARGIN:
        # Two instruments read equally well is genuinely ambiguous — a judgment that quotes the
        # mortgage it forecloses, say. Saying so beats picking the higher number.
        base.update({'kind': 'unknown', 'confidence': 'none',
                     'why': 'no single instrument stands out (%s)'
                            % ', '.join('%s=%d' % kv for kv in ranked[:3]),
                     'runner_up': runner, 'index_agrees': None})
        return base

    title_hits = [h for h in evidence[top] if h.get('as') == 'title']
    on_first = any(h['page'] == pages[0]['page'] for h in title_hits)
    confidence = 'high' if (on_first and top_score >= TITLE_ON_FIRST_PAGE + CORROBORATOR) else (
        'medium' if title_hits else 'low')
    base.update({'kind': top, 'confidence': confidence, 'runner_up': runner,
                 'evidence': evidence[top][:6],
                 'index_agrees': (label_kind == top) if label_kind else None,
                 'from_ocr': all(p.get('text_source') == 'ocr' for p in pages)})
    return base


# ---- what this document points AT ----------------------------------------------------------------
# The seed of chain-following: an instrument names the instruments it affects, by book and page.
# An owner-name search never sees a lien recorded against a prior owner or a misspelled name, but
# the document that references it does. Fetching these is the next stage and is NOT done here —
# this only reports what the text cites.
_BOOKPAGE_RE = re.compile(
    r'(?:O\.?\s?R\.?\s?B\.?|OFFICIAL\s+RECORDS?\s+BOOK|BOOK)\s*[.:#]?\s*(\d{3,6})'
    r'[\s,]*(?:AT\s+)?(?:PAGE|PG\.?|P\.)\s*[.:#]?\s*(\d{1,5})', re.I)


def cited_instruments(reading):
    """Book/page references the document's own text cites. Candidates to fetch, never findings."""
    out, seen = [], set()
    for page in _readable(reading):
        for line in (page.get('text') or '').splitlines():
            for match in _BOOKPAGE_RE.finditer(line):
                key = (match.group(1), match.group(2))
                if key in seen:
                    continue
                seen.add(key)
                out.append({'book': match.group(1), 'page_no': match.group(2),
                            'cited_on_page': page['page'], 'passage': line.strip()[:240],
                            'fetched': False, 'source': 'document_text'})
    return out
