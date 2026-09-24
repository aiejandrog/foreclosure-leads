"""judgment_money: one exact-cents check for every judgment and claim amount path.

WHY ONE CHECK
Until priority 2 of the Miami automation goal, four paths checked money four different ways:

  OCR / text layer   a 2-to-6-figure subset search over every figure in the document, with 0.011
                     of float slack. Twenty-four figures taken up to six at a time is ~134,000
                     combinations, so on a ~$15,000 total a coincidental "corroboration" is not
                     rare. It corroborated a total without knowing which figure was which.
  vision             every typed row on the TOTAL'S PAGE only. A cost table that starts on page 2
                     and totals on page 3 could never verify, however well it was read.
  saved vision       the same page-only rule, re-applied to a stored reading.
  timeline reader    no check at all; every figure left 'unverified'.

The pilot's Walker and Blue Water figures were only verified because an agent transcribed them by
hand. This module is the check that replaces the transcription, and every path above now calls it.

THE CHECK
Rows arrive in reading order, each typed charge | credit | rate | subtotal | total.

  * Every amount is an exact cent (Decimal). A sub-cent or unreadable amount fails the document.
  * Rates are kept and reported, never summed. A charge equal to a stated rate needs review,
    because that is what a per-diem typed as a charge looks like.
  * Credits subtract ("Less: escrow balance", a figure printed in parentheses or with a minus).
  * Every printed subtotal equals its members to the cent and is never counted a second time.
    Members are the reader's explicit ids; a subtotal whose first member opens its page may
    continue from the rows ending the page before. Text rows have no ids, so their subtotal's
    members are the run of additive rows directly above it.
  * A printed total is verified by ONE contiguous run of rows that ENDS at the total and may start
    up to two pages earlier, whose charges minus credits equal it exactly. No tolerance and no
    subset search: every row inside the run counts. The run may not cross an unread page, a figure
    whose label could not be paired, or a different total, and may not hold a subtotal without all
    of its members.
  * Two runs that differ by anything but zero-amount rows would both "verify", which means the
    table's edge is unknown. That is ambiguous, not verified.

WHAT A PASS MEANS
The document's own printed parts reproduce its own printed total. It does not make the figure a
finding, an open balance or an equity input: judgment_for_analyze still refuses scan-read figures
until the twelve-case review, and a total that verifies is still only what this document says.
"""
import re
from decimal import Decimal, InvalidOperation

KINDS = ('charge', 'credit', 'rate', 'subtotal', 'total')
# A table that runs across a page break starts at most this many pages before its total.
MAX_SPAN_PAGES = 3
# ...and at most this many rows before it. Bounds the number of runs tried.
MAX_RUN_ROWS = 60


class NotCents(ValueError):
    """An amount that is not an exact, finite number of cents."""


def cents(value):
    try:
        amount = Decimal(str(value).replace(',', '').replace('$', '').strip())
    except (InvalidOperation, ValueError, TypeError):
        raise NotCents('not a number: %r' % (value,))
    if not amount.is_finite() or amount != amount.quantize(Decimal('0.01')):
        raise NotCents('not an exact cent amount: %r' % (value,))
    return amount


def _rate_value(value):
    try:
        rate = Decimal(str(value).replace(',', '').replace('$', '').replace('%', '').strip())
    except (InvalidOperation, ValueError, TypeError):
        raise NotCents('invalid rate: %r' % (value,))
    if not rate.is_finite():
        raise NotCents('invalid rate: %r' % (value,))
    return rate


# Rates a label carries inline: "interest at $197.00/day", "7.875%", "per diem: $20.55".
_INLINE_RATE_RES = (
    re.compile(r'\$?([\d,]+(?:\.\d+)?)\s*(?:/\s*day\b|per\s+(?:day|diem)\b|%)', re.I),
    re.compile(r'per\s+diem\s*:\s*\$?([\d,]+(?:\.\d+)?)', re.I),
)


def inline_rates(label):
    out = set()
    for pattern in _INLINE_RATE_RES:
        for value in pattern.findall(str(label or '')):
            try:
                out.add(Decimal(value.replace(',', '')))
            except InvalidOperation:
                continue
    return out


def _page_no(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---- rows from a vision reading ---------------------------------------------------------------

def vision_rows(figures):
    """Typed vision figures (document_vision.read_document 'figures') -> normalized rows.

    Ids are the reader's, unique per PAGE, so they are namespaced by page. A reader that writes
    kind 'charge' with a negative amount has printed a credit; that sign is kept.
    """
    rows = []
    indexed = sorted(enumerate(figures or []), key=lambda p: (_page_no(p[1].get('page')), p[0]))
    for order, (_, fig) in enumerate(indexed):
        page = _page_no(fig.get('page'))
        ident = fig.get('id')
        members = fig.get('item_ids')
        rows.append({
            'gid': 'p%d:%s' % (page, ident) if isinstance(ident, str) and ident else None,
            'page': page, 'order': order, 'label': str(fig.get('label') or '')[:160],
            'kind': fig.get('kind'), 'amount': fig.get('amount'),
            'members': (['p%d:%s' % (page, m) if isinstance(m, str) else m for m in members]
                        if isinstance(members, list) else members),
            'explicit': True, 'confident': fig.get('confident') is True,
            'barrier': False, 'source': 'vision'})
    return rows


# ---- the check --------------------------------------------------------------------------------

class _Fail(Exception):
    pass


def _prepare(rows):
    """Validate every row and resolve every subtotal ONCE, over the whole document.

    -> {'rows', 'by_gid', 'rates', 'values', 'subtotals'}; raises _Fail with the reason.
    """
    seen, rates, values = set(), [], {}
    for row in rows:
        if row.get('barrier'):
            continue
        kind, gid = row.get('kind'), row.get('gid')
        if (kind not in KINDS or not isinstance(gid, str) or not gid or gid in seen
                or row.get('confident') is not True):
            raise _Fail('missing/duplicate id, missing kind, or uncertain figure; review required')
        seen.add(gid)
        try:
            if kind == 'rate':
                rates.append({'gid': gid, 'page': row['page'], 'label': row['label'],
                              'value': _rate_value(row['amount']), 'unit': row.get('rate_unit')})
                continue
            amount = cents(row['amount'])
        except NotCents:
            raise _Fail('invalid or sub-cent monetary figure')
        if kind == 'credit':
            amount = -abs(amount)
        values[gid] = amount
    # The guard is for a per-diem or percentage typed as a charge. An hourly fee rate ("10 hours
    # at $300.00 per hour") is kept and reported but does not make an equal charge suspicious.
    rate_values = {r['value'] for r in rates if r.get('unit') != 'hour'}
    for row in rows:
        if not row.get('barrier'):
            rate_values |= inline_rates(row.get('label'))
    for row in rows:
        if row.get('barrier') or row['kind'] not in ('charge', 'credit'):
            continue
        if abs(values[row['gid']]) in rate_values:
            raise _Fail('charge equals a stated rate; review required')
    state = {'rows': rows, 'values': values, 'rates': rates, 'subtotals': {}}
    for index, row in enumerate(rows):
        if not row.get('barrier') and row['kind'] == 'subtotal':
            state['subtotals'][row['gid']] = _resolve_subtotal(rows, index, values,
                                                               state['subtotals'])
    return state


def _additive(row):
    return not row.get('barrier') and row['kind'] in ('charge', 'credit')


def _resolve_subtotal(rows, index, values, resolved=None):
    """-> {'members': [gid], 'membership': how}; raises _Fail when the subtotal does not agree.

    `resolved`: the subtotals above this one, already resolved. A text subtotal may be a RUNNING
    one, the subtotal before it plus the rows since (Blue Water: 327,395.70 + eight costs =
    331,511.79); its members are then that subtotal and those rows."""
    row = rows[index]
    amount = values[row['gid']]
    members = row.get('members')
    if row.get('explicit'):
        by_gid = {r['gid']: r for r in rows if not r.get('barrier')}
        if (not isinstance(members, list) or not members
                or len(members) != len(set(members))
                or any(not isinstance(m, str) or m not in by_gid or not _additive(by_gid[m])
                       for m in members)):
            raise _Fail('printed subtotal lacks valid members or disagrees with its own items')
        if sum(values[m] for m in members) == amount:
            return {'members': list(members), 'membership': 'explicit'}
        # The reader sees one page. When the listed members are exactly the rows that open the
        # page, the subtotal may also cover the rows that END the page before it.
        page = row['page']
        page_additive = [r['gid'] for r in rows if r['page'] == page and _additive(r)]
        if page_additive[:len(members)] == list(members):
            earlier = [r for r in rows if r['page'] == page - 1]
            tail = []
            for prior in reversed(earlier):
                if not _additive(prior):
                    break
                tail.insert(0, prior['gid'])
                if sum(values[m] for m in tail + list(members)) == amount:
                    return {'members': tail + list(members),
                            'membership': 'explicit_continued_from_page_%d' % (page - 1)}
        raise _Fail('printed subtotal lacks valid members or disagrees with its own items')
    # Text rows: the additive rows directly above the subtotal, back to the previous subtotal,
    # total or barrier. The shortest run that agrees; none agreeing is not a subtotal (see below).
    run, back = [], index - 1
    while back >= 0 and len(run) < MAX_RUN_ROWS:
        prior = rows[back]
        if (run and not prior.get('barrier') and prior['kind'] == 'subtotal'
                and (resolved or {}).get(prior['gid'])):
            if values[prior['gid']] + sum(values[m] for m in run) == amount:
                return {'members': [prior['gid']] + run, 'membership': 'running_from_subtotal'}
            break
        if not _additive(prior):
            break
        run.insert(0, prior['gid'])
        if sum(values[m] for m in run) == amount:
            return {'members': list(run), 'membership': 'rows_above'}
        back -= 1
    return None


def _structural_fixups(state):
    """A text line labelled like a subtotal whose rows above do not add up to it is treated as a
    charge. Safe in both directions: if it really was a subtotal of misread rows, counting it AND
    its members breaks the total; if it was a one-line item, it is counted once, as it should be.

    Except a line labelled SUBTOTAL itself. That word is never a one-line item, so rows above it
    that do not add up mean a row was misread or missed, and counting it as a charge would let the
    total "verify" through the bad rows (a running SUBTOTAL, then the fees after it). It becomes a
    barrier instead: no run may cross it, so the total fails unless it is reproduced below it."""
    for gid, resolved in list(state['subtotals'].items()):
        if resolved is None:
            row = next(r for r in state['rows'] if r['gid'] == gid)
            if _SUBTOTAL_WORD_RE.search(row.get('label') or ''):
                row['barrier'] = True
                row['note'] = 'SUBTOTAL whose rows above do not add up to it; not crossed'
            else:
                row['kind'] = 'charge'
                row['note'] = 'subtotal label with no agreeing rows above it; counted as a line item'
            del state['subtotals'][gid]


def _component(row, values):
    out = {'gid': row['gid'], 'page': row['page'], 'label': row['label'],
           'amount': float(values[row['gid']])}
    if row.get('note'):
        out['note'] = row['note']
    return out


def _summary(state, run_rows, total_row, reason, how):
    values = state['values']
    subtotals = [{'gid': gid, 'label': next(r['label'] for r in state['rows'] if r['gid'] == gid),
                  'amount': float(values[gid]), 'members': s['members'],
                  'membership': s['membership']}
                 for gid, s in state['subtotals'].items()
                 if any(r['gid'] == gid for r in run_rows)]
    additive = [r for r in run_rows if _additive(r)]
    return {'ok': True, 'reason': reason, 'run': how,
            'components': [float(values[r['gid']]) for r in additive],
            'component_rows': [_component(r, values) for r in additive],
            'credits': [_component(r, values) for r in additive if values[r['gid']] < 0],
            'rates': [{'page': r['page'], 'label': r['label'], 'value': float(r['value'])}
                      for r in state['rates']],
            'subtotals': subtotals,
            'pages': sorted({r['page'] for r in run_rows}),
            'total_gid': total_row['gid'] if total_row else None}


def _fail(reason, components=()):
    return {'ok': False, 'reason': reason, 'components': [float(c) for c in components],
            'component_rows': [], 'credits': [], 'rates': [], 'subtotals': [], 'pages': [],
            'run': None, 'total_gid': None}


def check_all(rows, total=None):
    """Every row counts: the strict one-table check (miami_judgment.labeled_sum_check).

    `total` None checks the subtotals only.
    """
    try:
        state = _prepare(rows)
        if any(v is None for v in state['subtotals'].values()):
            raise _Fail('printed subtotal lacks valid members or disagrees with its own items')
        values = state['values']
        live = [r for r in rows if not r.get('barrier')]
        additive = [values[r['gid']] for r in live if _additive(r)]
        if total is not None:
            stated = cents(total)
            totals = [values[r['gid']] for r in live if r['kind'] == 'total']
            if not totals or any(t != stated for t in totals):
                return _fail('missing or disagreeing kind=total', additive)
            if not additive or sum(additive) != stated:
                return _fail('all labeled additive items do not equal the stated total to the '
                             'cent', additive)
        total_row = next((r for r in reversed(live) if r['kind'] == 'total'), None)
        return _summary(state, live, total_row,
                        'all labeled additive items sum exactly to stated total; subtotals agree',
                        'all_rows')
    except NotCents:
        return _fail('invalid or sub-cent monetary figure')
    except _Fail as exc:
        return _fail(str(exc))


def verify_total(rows, total_gid, read_pages=None, stated=None, whole_total_page=False):
    """Is the total row `total_gid` reproduced by ONE contiguous run of rows ending at it?

    `read_pages`: the pages whose rows are present. A run cannot cross a page that is not in it,
    because a table's rows on an unread page are not zero, they are unknown.

    `whole_total_page`: every row above the total ON ITS OWN PAGE must be in the run. Vision rows
    use it: the reader was told to type only awarded amounts, so a typed charge above the total is
    part of the table, and letting a run skip it would let a misread total that happens to equal
    the last line "verify". Text rows cannot use it: a text layer carries the recitals above the
    table ("a note in the original amount of $200,000.00") as figures too.
    """
    rows = [dict(r) for r in rows]       # the fixups below retype rows; never the caller's
    try:
        state = _prepare(rows)
        _structural_fixups(state)
    except _Fail as exc:
        return _fail(str(exc))
    values = state['values']
    index = next((i for i, r in enumerate(rows) if r.get('gid') == total_gid), None)
    if index is None or rows[index]['kind'] != 'total':
        return _fail('missing or disagreeing kind=total')
    total_row = rows[index]
    try:
        if stated is not None and cents(stated) != values[total_gid]:
            return _fail('missing or disagreeing kind=total')
    except NotCents:
        return _fail('invalid or sub-cent monetary figure')
    target = values[total_gid]
    page = total_row['page']
    pages = set(read_pages) if read_pages is not None else {r['page'] for r in rows}
    members_of = {gid: set(s['members']) for gid, s in state['subtotals'].items()}
    matches, running, run = [], Decimal('0'), []
    # The first row on the total's page that is counted; a run that must cover the page has to
    # reach it (rates and subtotals above it are not counted, so they may be left out).
    page_start = next((i for i in range(index) if rows[i]['page'] == page and _additive(rows[i])),
                      index)
    back = index - 1
    while back >= 0 and len(run) < MAX_RUN_ROWS:
        row = rows[back]
        if row['page'] < page - (MAX_SPAN_PAGES - 1):
            break
        if row['page'] != rows[back + 1]['page']:
            # Crossing a page break: every page in between must have been read.
            if any(p not in pages for p in range(row['page'], rows[back + 1]['page'] + 1)):
                break
        if row.get('barrier') or (row['kind'] == 'total' and values[row['gid']] != target):
            break
        run.insert(0, row)
        if _additive(row):
            running += values[row['gid']]
        # A candidate run must start at a real row, hold every member of every subtotal in it,
        # and add up exactly.
        if (_additive(row) and running == target
                and (not whole_total_page or back <= page_start)):
            gids = {r['gid'] for r in run}
            if all(members_of[r['gid']] <= gids for r in run if r['gid'] in members_of):
                matches.append(list(run))
        back -= 1
    if not matches:
        additive = [values[r['gid']] for r in rows[:index] if _additive(r) and r['page'] == page]
        return _fail('no contiguous run of rows ending at the total adds up to it to the cent '
                     '(tried up to %d page(s) back)' % (MAX_SPAN_PAGES - 1), additive)
    distinct = {frozenset(r['gid'] for r in m if _additive(r) and values[r['gid']] != 0)
                for m in matches}
    if len(distinct) > 1:
        return _fail('more than one run of rows adds up to the total; where the table starts '
                     'is ambiguous, review required')
    chosen = matches[0]           # the shortest; longer ones only add zero-amount rows
    first = chosen[0]
    how = ('same_page' if first['page'] == page
           else 'continued_from_page_%d' % first['page'])
    before = [r for r in rows[:index] if r['page'] >= first['page'] and r not in chosen
              and _additive(r)]
    out = _summary(state, chosen + [total_row], total_row,
                   'one run of printed rows ending at the total adds up to it exactly; '
                   'subtotals agree', how)
    out['excluded_before_run'] = [_component(r, values) for r in before]
    return out


def verify_document(figures, grand_totals, read_pages=None):
    """Vision figures + stated grand totals -> [check per grand total], same order.

    Each check also carries 'amount' and 'page'. A grand total with no kind=total row of the same
    amount on its page fails as 'missing or disagreeing kind=total'.
    """
    rows = vision_rows(figures)
    if read_pages is None:
        read_pages = {r['page'] for r in rows}
    out = []
    for total in grand_totals or []:
        page = _page_no(total.get('page'))
        try:
            stated = cents(total.get('amount'))
        except NotCents:
            out.append(dict(_fail('invalid or sub-cent monetary figure'), amount=total.get('amount'),
                            page=page))
            continue
        candidates = []
        for r in rows:
            if r['page'] != page or r['kind'] != 'total':
                continue
            try:
                if cents(r['amount']) == stated:
                    candidates.append(r)
            except NotCents:
                continue
        others = [r for r in rows if r['page'] == page and r['kind'] == 'total'
                  and r not in candidates]
        if not candidates or others:
            check = _fail('missing or disagreeing kind=total')
        else:
            check = verify_total(rows, candidates[-1]['gid'], read_pages, stated,
                                 whole_total_page=True)
        out.append(dict(check, amount=float(stated), page=page))
    return out


# ---- rows from a text layer or OCR -----------------------------------------------------------

_FIGURE_RE = re.compile(
    r'(?P<open>\(\s*)?(?P<minus>-\s*)?\$\s*(?P<num>[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})'
    r'(?P<close>\s*\))?')
_RATE_AFTER_RE = re.compile(r'^\s*(?:/\s*(?:day|hour|hr)\b|per\s+(?:day|diem|hour)\b'
                            r'|an?\s+(?:day|hour)\b|daily\b|hourly\b)', re.I)
_RATE_BEFORE_RE = re.compile(r'(?:per\s+diem|daily\s+rate|per\s+day|rate\s+of)\s*(?:interest)?'
                             r'\s*(?:of|is|at)?\s*[:=\-]?\s*$', re.I)
_HOURLY_RE = re.compile(r'^\s*(?:/\s*(?:hour|hr)\b|per\s+hour\b|an\s+hour\b|hourly\b)', re.I)
_SUBTOTAL_RE = re.compile(
    r'\bsub-?\s?total\b'
    r'|^\W*total\s+(?:court\s+)?(?:costs?|fees?|interest|assessments?|charges|expenses|advances'
    r'|attorney)'
    r'|\b(?:costs?|fees?|interest|assessments?|charges|expenses|advances)\s+total\b'
    r'|^\W*total\W*$', re.I)
# A bare "TOTAL" is a section total until the table shows otherwise: see _bare_totals.
_BARE_TOTAL_RE = re.compile(r'^\W*total\W*$', re.I)
_SUBTOTAL_WORD_RE = re.compile(r'\bsub-?\s?total\b', re.I)
_CREDIT_RE = re.compile(r'^\W*(?:less|minus)\b|\bcredits?\b|\bsuspense\b|\bunapplied\b'
                        r'|\bpayments?\s+received\b|\bescrow\s+(?:balance|surplus)\b', re.I)


def _text_kind(fragment, after, negative, total_re):
    if _RATE_AFTER_RE.match(after) or _RATE_BEFORE_RE.search(fragment):
        return 'rate'
    if _SUBTOTAL_RE.search(fragment.strip()):
        return 'subtotal'
    if total_re.search(fragment):
        return 'total'
    if negative or _CREDIT_RE.search(fragment):
        return 'credit'
    return 'charge'


# A line that is nothing but a money figure: an entry in a value column. A text layer pads the
# dollar sign with runs of no-break spaces ('$\xa0\xa0\xa0 6,935.74'), prints thousands without a
# comma ('$1050.00') and prints a credit in parentheses ('($422.11)'); all three are value lines.
ONLY_MONEY_RE = re.compile(r'^[\s|:.]*(?P<open>\(\s*)?(?P<minus>-\s*)?\$?\s*'
                           r'(?P<num>[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{2}|[0-9]+\.[0-9]{2})'
                           r'\s*(?P<close>\))?[\s|.]*$')


def only_money(line):
    """The figure on a value-only line, negative when printed in parentheses or with a minus."""
    match = ONLY_MONEY_RE.match(line)
    if not match or bool(match.group('open')) != bool(match.group('close')):
        return None
    value = float(match.group('num').replace(',', ''))
    return -value if (match.group('open') or match.group('minus')) else value


def column_blocks(lines):
    """Each run of value-only lines, with the label lines standing directly above it.

    -> [(label_line_indexes, [(value_line_index, value)])]; a value printed as a credit is negative.

    The labels stop at the first line above that carries a figure of its own. That line is a row
    in its own right ("2024: $835.00"), not a label; taking it as one hid Blue Water's year-by-year lines
    and both credits, and a label run that long never pairs, so every figure below it was lost too.
    """
    blocks = []
    index, count = 0, len(lines)
    while index < count:
        if only_money(lines[index]) is None:
            index += 1
            continue
        start = index
        values = []
        while index < count:
            value = only_money(lines[index])
            if value is not None:
                values.append((index, value))
            elif lines[index].strip():
                break
            index += 1
        labels = []
        back = start - 1
        while back >= 0:
            line = lines[back]
            if only_money(line) is not None or _FIGURE_RE.search(line):
                break
            if line.strip():
                labels.append(back)
            back -= 1
        labels.reverse()
        blocks.append((labels, values))
    return blocks


def text_rows(reading, total_re):
    """Rows off every page read as text or OCR, in reading order, across pages.

    Same-line figures become rows labelled by the text before them on the line. A label column and
    a value column pair ONLY when they are the same length, the rule miami_judgment.column_values
    applies. A value column that does not line up leaves its figures as BARRIERS placed where the
    table starts, so no run can cross them: a figure whose label is unknown can be neither counted
    nor skipped. Only the tail figure is placed, on the last label, and only when that label is a
    total.
    """
    rows = []
    for page in reading.get('pages') or []:
        if page.get('outcome') not in ('text', 'ocr_text'):
            continue
        number = _page_no(page.get('page'))
        source = page.get('text_source')
        lines = (page.get('text') or '').splitlines()
        column_lines = set()
        for labels, values in column_blocks(lines):
            column_lines.update(i for i, _ in values)
            if not values:
                continue
            if not labels:
                # A figure with no label line above it is unknown, not absent: a barrier.
                for n, (line_no, value) in enumerate(values):
                    rows.append(_text_row(number, line_no, 0, '', None, abs(value), 'unlabelled',
                                          source))
                continue
            column_lines.update(labels)
            if len(labels) == len(values):
                for label, (_, value) in zip(labels, values):
                    kind = _text_kind(lines[label], '', value < 0, total_re)
                    rows.append(_text_row(number, label, 0, lines[label].strip(), kind, abs(value),
                                          'column_pairing', source))
                continue
            for n, (_, value) in enumerate(values[:-1]):
                rows.append(_text_row(number, labels[0], -len(values) + n, '', None, abs(value),
                                      'unpaired', source))
            kind = _text_kind(lines[labels[-1]], '', values[-1][1] < 0, total_re)
            rows.append(_text_row(number, labels[-1], 0, lines[labels[-1]].strip(),
                                  kind if kind == 'total' else None, abs(values[-1][1]),
                                  'tail_figure', source))
        for i, line in enumerate(lines):
            if i in column_lines:
                continue
            start = 0
            for k, match in enumerate(_FIGURE_RE.finditer(line)):
                fragment = line[start:match.start()]
                negative = bool(match.group('minus')) or bool(match.group('open')
                                                              and match.group('close'))
                kind = _text_kind(fragment, line[match.end():], negative, total_re)
                value = float(match.group('num').replace(',', ''))
                rows.append(_text_row(number, i, k, fragment.strip(' :.-$(')[:160], kind, value,
                                      'same_line', source))
                if kind == 'rate' and _HOURLY_RE.match(line[match.end():]):
                    rows[-1]['rate_unit'] = 'hour'
                start = match.end()
    rows.sort(key=lambda r: (r['page'], r['line'], r['k']))
    _bare_totals(rows)
    return rows


def _bare_totals(rows):
    """A bare "TOTAL" that closes a table of printed SUBTOTALs is that table's total.

    Alone, "TOTAL" is as often a section total as the judgment's, so _text_kind types it a
    subtotal. Blue Water's table says which it is: two rows labelled SUBTOTAL, then TOTAL, larger
    than both, with no other total between. Only that shape is retyped; the arithmetic still has
    to reproduce it (verify_total), so a wrong call here can only fail to verify.
    """
    for index, row in enumerate(rows):
        if (row['barrier'] or row['kind'] != 'subtotal'
                or not _BARE_TOTAL_RE.match(row['label'] or '')):
            continue
        earlier = []
        for prior in reversed(rows[:index]):
            if prior['page'] < row['page'] - (MAX_SPAN_PAGES - 1) or prior['kind'] == 'total':
                break
            if not prior['barrier'] and prior['kind'] == 'subtotal':
                earlier.append(prior)
        named = [p for p in earlier if _SUBTOTAL_WORD_RE.search(p['label'] or '')]
        if named and all(float(p['amount']) < float(row['amount']) for p in earlier):
            row['kind'] = 'total'
            row['note'] = 'bare TOTAL closing a table of printed SUBTOTALs'


def _text_row(page, line, k, label, kind, value, how, text_source):
    return {'gid': 'p%d:L%d:%d' % (page, line, k), 'page': page, 'line': line, 'k': k,
            'label': label, 'kind': kind or 'charge', 'amount': '%.2f' % value,
            'members': None, 'explicit': False, 'confident': True,
            'barrier': kind is None, 'how': how, 'source': text_source or 'text'}


def verify_text_total(rows, page, line, amount, read_pages=None):
    """Check the text total printed at (page, line) with `amount` against the rows above it.

    `read_pages`: pages read as text or OCR, figures or not. Without it only pages that produced
    a row count as read, which refuses a run across a read page that simply had no figures."""
    try:
        stated = cents('%.2f' % float(amount))
    except (NotCents, TypeError, ValueError):
        return _fail('invalid or sub-cent monetary figure')
    target = next((r for r in rows if r['page'] == page and r['line'] == line
                   and not r['barrier'] and cents(r['amount']) == stated), None)
    if target is None:
        return _fail('the total\'s own line could not be placed in the table')
    if target['kind'] != 'total':
        target = dict(target, kind='total')
        rows = [target if r['gid'] == target['gid'] else r for r in rows]
    return verify_total(rows, target['gid'], read_pages, stated)
