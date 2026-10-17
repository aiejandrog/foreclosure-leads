#!/usr/bin/env python
"""Turn extracted judgment page text into one checked total.

broward_judgment_dates.py finds the recorded instrument. fl_lp/broward_pin.py can download
its PDF. This step starts after the text is already in hand: no clerk site, no PACER, no
download. Equity code is not called and not edited. The record below is the contract that
code can consume later.

    {"case", "total_cents", "judgment_date", "verified_by": "judgment_money",
     "pages_read", "human_reviewed": false}

A total is returned only when three things are true together: the document's own
heading is a final judgment of foreclosure, the case number printed on the page is
the case this read is for, and judgment_money.verify_text_total reproduces the
total to the cent from one contiguous run, with a single entry/order date on the
page. A balanced table is not enough. A mortgage, lis pendens, order, or any other
case returns reason 'unverified' and no amount. A satisfaction keeps its own
verdict (void or not) and never yields an amount.

A satisfaction voids that total only when the document is a satisfaction of this case's
foreclosure judgment and the satisfaction is in full. A partial satisfaction does not void.
A satisfaction of a separate fee judgment or HOA judgment does not void. A blank page is a
partial read: it neither voids nor produces a total.
"""
import re
from decimal import Decimal, InvalidOperation

import judgment_money as JM

# Labels that are the judgment's own total, not a section total inside the table.
TOTAL_RE = re.compile(
    r'\b(?:grand\s+total|total\s+amount\s+due|total\s+judgment|amount\s+due|'
    r'final\s+judgment\s+amount|total\s+sum)\b', re.I)
_FORECLOSURE_RE = re.compile(
    r'\b(?:final\s+judgment\s+of\s+foreclosure|judgment\s+of\s+foreclosure|foreclosure\s+judgment)\b',
    re.I)
_PARTIAL_RE = re.compile(r'\bpartial(?:\s+satisfaction|ly\s+satisf)', re.I)
_FULL_RE = re.compile(r'\b(?:satisfied\s+in\s+full|paid\s+in\s+full|full\s+satisfaction)\b', re.I)
_FEE_HOA_RE = re.compile(
    r"\b(?:attorney(?:'s|s)?\s+fee\s+judgment|fee\s+judgment|cost\s+judgment|"
    r'assessment\s+judgment|homeowners?\s+association\s+judgment|association\s+judgment|'
    r'hoa\s+judgment)\b', re.I)
_HOA_BODY_RE = re.compile(
    r'\b(?:homeowners?\s+association|condominium\s+association|association\s+assessments?)\b', re.I)
_MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
}
_DATE_RE = re.compile(
    r'\b(?:done\s+and\s+ordered|entered|ordered|rendered|dated)\b'
    r'(?:\s+\w+){0,8}\s+on\s+'
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{1,2}),\s+(\d{4})', re.I)

# The document's own heading, one line. A balanced table does not decide this, and a
# sentence that merely quotes one of these words does not either.
_HEADING_RES = (
    ('satisfaction', re.compile(
        r'^[\W_]*(?:PARTIAL\s+)?(?:SATISFACTION\s+OF\b|RELEASE\s+OF\s+(?:THE\s+)?MORTGAGE\b)',
        re.I)),
    ('mortgage', re.compile(
        r'^[\W_]*(?:THIS\s+)?(?:OPEN[- ]END\s+)?MORTGAGE(?:\s+DEED)?\b', re.I)),
    ('lis_pendens', re.compile(r'^[\W_]*(?:NOTICE\s+OF\s+)?LIS\s+PENDENS\b', re.I)),
    ('order', re.compile(
        r'^[\W_]*ORDER\s+(?:GRANTING|DENYING|ON|SETTING|CANCELLING|CANCELING|VACATING|APPROVING)\b',
        re.I)),
    ('foreclosure_judgment', re.compile(
        r'^[\W_]*(?:(?:AMENDED|CORRECTED|DEFAULT|CONSENT|AGREED|STIPULATED|SUMMARY|'
        r'IN\s+REM|UNCONTESTED)\s+)*'
        r'(?:FINAL\s+JUDGMENT\s+OF\s+(?:MORTGAGE\s+)?FORECLOSURE|'
        r'JUDGMENT\s+OF\s+(?:MORTGAGE\s+)?FORECLOSURE|'
        r'FORECLOSURE\s+JUDGMENT)\b', re.I)),
)
# Broward CACE-99-000123 and Palm Beach 502099CA000123XXXXMB, hyphens optional.
# Miami-Dade numbers are not read here; that county has its own judgment path.
_BROWARD_CASE_RE = re.compile(
    r'\b(CACE|CONO|COCE|COWE|COSO)[\s\-–]*(\d{2})[\s\-–]*(\d{6})\b', re.I)
_PB_CASE_RE = re.compile(
    r'\b(50[-–]?\d{4}[-–]?(?:CA|CC)[-–]?[A-Z0-9](?:[-–]?[A-Z0-9]){5,22})\b', re.I)
_PB_CANON_RE = re.compile(r'^50\d{4}(?:CA|CC)[A-Z0-9]{6,16}$')


def _result(ok=False, void=False, reason='', record=None, instrument='', checked=False):
    return {
        'ok': bool(ok), 'void': bool(void), 'reason': reason, 'instrument': instrument,
        'checked': bool(checked), 'record': record,
    }


def _norm_case(case):
    return re.sub(r'[^A-Z0-9]', '', str(case or '').upper())


def _hoa_case(case):
    c = _norm_case(case)
    if c.startswith(('CONO', 'COCE', 'COWE', 'COSO')):
        return True
    return bool(re.match(r'^50\d{4}CC', c))


def _title(text, n=4):
    """The first few non-empty lines. A caption, not a sentence buried in the table."""
    lines = [ln.strip() for ln in str(text or '').splitlines() if ln.strip()]
    return '\n'.join(lines[:n])


def _lines(text):
    return [ln.strip() for ln in str(text or '').splitlines() if ln.strip()]


def document_signal(text):
    """The document's own first instrument heading, or ''.

    'foreclosure_judgment' is the only signal that may carry an amount. A mortgage,
    lis pendens, order, or satisfaction that merely contains a balanced table is not
    that signal, and neither is a sentence that quotes the judgment.
    """
    for ln in _lines(text):
        for kind, pattern in _HEADING_RES:
            if pattern.match(ln):
                return kind
    return ''


def _is_satisfaction_doc(text):
    if document_signal(text) == 'satisfaction':
        return True
    # A satisfaction caption inside the first lines, even when the line does not
    # open with the word. Existing callers depend on this.
    return bool(re.search(r'\bSATISFACTION\s+OF\b', _title(text), re.I))


def canon_case(case):
    """One comparable spelling, or '' when this is not a Broward or Palm Beach case number."""
    raw = str(case or '')
    match = _BROWARD_CASE_RE.search(raw)
    if match:
        return '%s-%s-%s' % (match.group(1).upper(), match.group(2), match.group(3))
    match = _PB_CASE_RE.search(raw)
    if match:
        compact = re.sub(r'[^A-Z0-9]', '', match.group(1).upper())
        if _PB_CANON_RE.match(compact):
            return compact
    return ''


def cases_in_text(text):
    """Case numbers printed in the text, Broward then Palm Beach, first occurrence order."""
    found = []
    for pattern in (_BROWARD_CASE_RE, _PB_CASE_RE):
        for match in pattern.finditer(text or ''):
            number = canon_case(match.group(0))
            if number and number not in found:
                found.append(number)
    return found


def case_matches(text, case):
    """True only when `case` itself is printed on the document."""
    want = _norm_case(canon_case(case) or case)
    if len(want) < 8:
        return False
    return want in {_norm_case(number) for number in cases_in_text(text)}


def proves_this_foreclosure_judgment(text, case):
    """True when the heading is a foreclosure judgment and the case number matches."""
    return document_signal(text) == 'foreclosure_judgment' and case_matches(text, case)


def _case_in_text(text, case):
    key = _norm_case(case)
    return len(key) >= 8 and key in _norm_case(text)


def _targets_this_foreclosure(text):
    """True when a full-satisfaction sentence is about the foreclosure judgment itself."""
    title = bool(re.search(
        r'\bSATISFACTION\s+OF\s+(?:THE\s+)?(?:FINAL\s+)?JUDGMENT\s+OF\s+FORECLOSURE\b',
        _title(text), re.I))
    for sent in re.split(r'(?<=[.])\s+|\n+', text):
        if not _FULL_RE.search(sent):
            continue
        if _FEE_HOA_RE.search(sent) and not _FORECLOSURE_RE.search(sent):
            continue
        if _FORECLOSURE_RE.search(sent) or title:
            return True
    return False


def satisfaction_effect(text, case):
    """(void, reason). A void requires this case and a full satisfaction of its foreclosure judgment."""
    if not _is_satisfaction_doc(text):
        return False, ''
    if _PARTIAL_RE.search(text):
        return False, 'partial_satisfaction'
    if not _case_in_text(text, case):
        return False, 'satisfaction_not_this_case'
    if _FEE_HOA_RE.search(text) and not _targets_this_foreclosure(text):
        return False, 'satisfaction_of_other_judgment'
    if not _targets_this_foreclosure(text):
        return False, 'satisfaction_not_this_judgment'
    return True, 'satisfied_in_full'


def _dates(text):
    found = []
    for month, day, year in _DATE_RE.findall(text or ''):
        try:
            m, d, y = _MONTHS[month.lower()], int(day), int(year)
            if not (1 <= d <= 31 and 1900 <= y <= 2100):
                continue
            found.append('%04d-%02d-%02d' % (y, m, d))
        except (KeyError, ValueError):
            continue
    return list(dict.fromkeys(found))


def _cents(amount):
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return None
    cents = value * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


def _reading(pages):
    return {'pages': [
        {'page': i + 1, 'outcome': 'text', 'text_source': 'embedded', 'text': text}
        for i, text in enumerate(pages)
    ]}


def _verified_total(pages):
    reading = _reading(pages)
    rows = JM.text_rows(reading, TOTAL_RE)
    read_pages = {p['page'] for p in reading['pages']}
    verified = []
    for row in rows:
        if row.get('kind') != 'total' or not TOTAL_RE.search(row.get('label') or ''):
            continue
        check = JM.verify_text_total(rows, row['page'], row['line'], float(row['amount']), read_pages)
        if check.get('ok'):
            verified.append(row)
    amounts = {_cents(r['amount']) for r in verified}
    amounts.discard(None)
    if len(verified) < 1 or len(amounts) != 1:
        return None
    return amounts.pop()


def read_judgment(pages, case):
    """Extracted page texts, in order, plus the case id the PDF was saved under.

    pages: a list of strings, one per page. A blank page is a partial read.
    """
    case = str(case or '').strip()
    if not case:
        return _result(reason='no_case')
    if not pages or any(not str(p or '').strip() for p in pages):
        return _result(reason='partial_read')
    pages = [str(p) for p in pages]
    text = '\n'.join(pages)
    if _is_satisfaction_doc(text):
        void, reason = satisfaction_effect(text, case)
        return _result(void=void, reason=reason or 'satisfaction', instrument='satisfaction')
    if _hoa_case(case) or (_HOA_BODY_RE.search(text) and not _FORECLOSURE_RE.search(text)):
        checked = _verified_total(pages) is not None
        return _result(reason='hoa_or_cono_judgment', instrument='hoa_judgment', checked=checked)
    # A balanced table is not a judgment total. The heading has to be this case's
    # final judgment of foreclosure before any amount is accepted.
    if not proves_this_foreclosure_judgment(text, case):
        return _result(reason='unverified')
    total = _verified_total(pages)
    if total is None:
        return _result(reason='total_not_reproduced', instrument='foreclosure_judgment')
    dates = _dates(text)
    if len(dates) != 1:
        return _result(reason='ambiguous_or_missing_date', instrument='foreclosure_judgment',
                       checked=True)
    record = {
        'case': case,
        'total_cents': total,
        'judgment_date': dates[0],
        'verified_by': 'judgment_money',
        'pages_read': len(pages),
        'human_reviewed': False,
    }
    return _result(ok=True, instrument='foreclosure_judgment', record=record, checked=True)
