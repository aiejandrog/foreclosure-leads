#!/usr/bin/env python
"""Turn extracted judgment page text into one checked total.

broward_judgment_dates.py finds the recorded instrument. fl_lp/broward_pin.py can download
its PDF. This step starts after the text is already in hand: no clerk site, no PACER, no
download. Equity code is not called and not edited. The record below is the contract that
code can consume later.

    {"case", "total_cents", "judgment_date", "verified_by": "judgment_money",
     "pages_read", "human_reviewed": false}

A total is returned only when judgment_money.verify_text_total reproduces it to the cent
from one contiguous run, and a single entry/order date is on the page.

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


def _is_satisfaction_doc(text):
    return bool(re.search(r'\bSATISFACTION\s+OF\b', _title(text), re.I))


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
