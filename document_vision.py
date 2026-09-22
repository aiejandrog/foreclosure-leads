"""document_vision — a SECOND reader for a page OCR could not read, and only that.

WHY THIS EXISTS
Windows OCR reads the Miami clerk's scans, and on 2026-09-22 it read one wrong three times in a
row. Every error sat where the clerk's light-grey diagonal watermark crosses a figure: 6,796.61
read as 5,796.61, 2,010.74 read as "40", 317.05 and 31.19 dropped entirely. Whitening the render
above a grey cutoff did not recover them, and four cutoffs produced the same four figures — so the
stamp is not sitting *beside* those digits, it is on top of them, and thresholding cannot separate
ink that overlaps. Nothing about the layout is the problem either: the figures already arrive as a
clean column block in the OCR text. That leaves one honest option, which is a reader that can
recognise a digit a stamp runs through the way a person reading the page does.

WHAT IT IS NOT
It is not a second opinion that wins. A model reading a scan can be wrong with total confidence,
and a judgment total picks WHICH open mortgage records_liens treats as the foreclosing first, so a
wrong figure moves an equity number invisibly. So this module produces CANDIDATES, and the same
arithmetic check that governs OCR governs it: a total is only admissible when the document's own
line items add up to it. Two independent readings agreeing on the arithmetic is the evidence;
either reading on its own is not.

WHAT IT COSTS, AND WHO DECIDES
Metered Claude API, capped per run by document_interpreter.Budget, which refuses the call that
would take a run past its dollar limit BEFORE the call is made. Pages are rendered at VISION_DPI
(not the 300 DPI OCR gets) because the API downsamples anything larger anyway and the tokens are
billed on the pixels sent. Every call's real cost is recorded from response.usage, so the run
reports what it actually spent rather than an estimate. There is no CLI fallback: a nightly run
must not depend on a human's chat quota, and must not silently start spending either.
"""
import base64
import json
import os
import re
import tempfile

import document_interpreter as DI
import document_store as DS

# A US Letter page at 140 DPI is about 1190x1540 — under the API's 1568px long-edge downsample
# threshold, so nothing is thrown away and nothing is paid for twice. 300 DPI (what OCR gets)
# would be downsampled on arrival, billing the same tokens for a bigger upload.
VISION_DPI = 140
DEFAULT_MAX_TOKENS = 2000

INSTRUCTION = (
    'You are looking at one page of a Florida county court judgment, scanned by the clerk. A '
    'light grey diagonal watermark reading "NOT AN OFFICIAL COPY - PUBLIC ACCESS" runs across the '
    'page and crosses some of the figures; read the printed digits underneath it. '
    'Transcribe the money amounts on this page as a table, in the order they appear. For each '
    'one give the label printed beside it and the amount as digits. Also give the page\'s grand '
    'total if one is printed. '
    'Rules: transcribe only what is printed. Never compute a figure, never correct one, and never '
    'fill in a figure you cannot read — mark it unreadable instead. If a digit is genuinely '
    'ambiguous under the watermark, say so for that row rather than guessing. '
    'Reply with JSON only, shaped: '
    '{"rows": [{"label": "...", "amount": "1234.56", "confident": true}], '
    '"grand_total": "1234.56" or null, "unreadable": ["..."]}'
)

_NUM_RE = re.compile(r'-?[0-9][0-9,]*(?:\.[0-9]{2})?')


class VisionReader:
    """Reads a page IMAGE. Same Budget, same refusal to fall back, as ApiInterpreter."""
    name = 'vision'

    def __init__(self, model=DI.DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS, client=None,
                 dpi=VISION_DPI):
        self.model = model
        self.max_tokens = max_tokens
        self.dpi = dpi
        self._client = client

    def client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError:
            raise DI.NotConfigured('the anthropic SDK is not installed (pip install anthropic)')
        if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
            raise DI.NotConfigured('no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment')
        self._client = anthropic.Anthropic()
        return self._client

    def read_page(self, png_bytes, budget, instruction=INSTRUCTION):
        """One page image -> {'rows': [...], 'grand_total': ..., 'usd': ...}."""
        if budget is None:
            raise ValueError('VisionReader requires a Budget')
        client = self.client()
        content = [
            {'type': 'image',
             'source': {'type': 'base64', 'media_type': 'image/png',
                        'data': base64.standard_b64encode(png_bytes).decode('ascii')}},
            {'type': 'text', 'text': 'Transcribe the money amounts on this page.'},
        ]
        messages = [{'role': 'user', 'content': content}]
        counted = client.messages.count_tokens(model=self.model, system=instruction,
                                               messages=messages)
        budget.check(counted.input_tokens, self.max_tokens)
        response = client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                          system=instruction, messages=messages)
        before = budget.spent
        budget.record(response.usage.input_tokens, response.usage.output_tokens)
        out = _parse(''.join(b.text for b in response.content if b.type == 'text'))
        out['usd'] = round(budget.spent - before, 6)
        out['input_tokens'] = response.usage.input_tokens
        out['output_tokens'] = response.usage.output_tokens
        return out


def _amount(value):
    if value is None:
        return None
    match = _NUM_RE.search(str(value).replace('$', ''))
    if not match:
        return None
    try:
        return float(match.group(0).replace(',', ''))
    except ValueError:
        return None


def _parse(text):
    """Tolerant JSON read. A row without a readable amount is DROPPED, never defaulted to zero."""
    body = text.strip()
    if body.startswith('```'):
        body = re.sub(r'^```[a-z]*\s*|\s*```$', '', body)
    try:
        data = json.loads(body)
    except ValueError:
        start, end = body.find('{'), body.rfind('}')
        if start < 0 or end <= start:
            return {'rows': [], 'grand_total': None, 'unreadable': ['reply was not JSON']}
        try:
            data = json.loads(body[start:end + 1])
        except ValueError:
            return {'rows': [], 'grand_total': None, 'unreadable': ['reply was not JSON']}
    rows = []
    for row in (data.get('rows') or []):
        amount = _amount(row.get('amount'))
        if amount is None:
            continue
        rows.append({'label': str(row.get('label') or '')[:120], 'amount': amount,
                     'confident': bool(row.get('confident', True))})
    return {'rows': rows, 'grand_total': _amount(data.get('grand_total')),
            'unreadable': [str(u)[:200] for u in (data.get('unreadable') or [])]}


def render_page(path, page_no, dpi=VISION_DPI, out_dir=None):
    """Render one 1-indexed page of a stored PDF to PNG bytes.

    The render is NOT watermark-cleaned. The cleaning exists to help OCR, and the whole reason to
    ask a second reader is that it can read what the stamp crosses; handing it a thresholded image
    would throw away the grey information it needs to tell stamp from ink.
    """
    fitz = DS._fitz()
    doc = fitz.open(str(path))
    try:
        pix = doc.load_page(page_no - 1).get_pixmap(dpi=dpi)
        data = pix.tobytes('png')
    finally:
        doc.close()
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, 'vision-p%02d.png' % page_no)
        DS._atomic_write_bytes(target, data)
    return data


def read_document(path, pages, budget, reader=None, out_dir=None):
    """Read the listed pages of a stored PDF. -> {'pages': {...}, 'figures': [...], 'usd': ...}

    `pages` is 1-indexed and deliberately a caller's choice: this is the expensive reader, and it
    runs on the pages that need it, not on every page of every document.
    """
    reader = reader or VisionReader()
    out = {'pages': {}, 'figures': [], 'grand_totals': [], 'usd': 0.0, 'errors': {}}
    for page_no in pages:
        try:
            png = render_page(path, page_no, dpi=reader.dpi, out_dir=out_dir)
            result = reader.read_page(png, budget)
        except DI.BudgetExhausted as stop:
            out['errors'][page_no] = 'budget: %s' % stop
            break
        except (DI.NotConfigured, DS.DocumentRejected) as exc:
            out['errors'][page_no] = str(exc)
            break
        except Exception as exc:                      # noqa: BLE001 - recorded, never swallowed
            out['errors'][page_no] = '%s: %s' % (type(exc).__name__, str(exc)[:200])
            continue
        out['pages'][page_no] = result
        out['usd'] = round(out['usd'] + result.get('usd', 0.0), 6)
        for row in result['rows']:
            out['figures'].append({'amount': row['amount'], 'label': row['label'],
                                   'page': page_no, 'confident': row['confident']})
        if result.get('grand_total') is not None:
            out['grand_totals'].append({'amount': result['grand_total'], 'page': page_no})
    return out
