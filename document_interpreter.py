"""document_interpreter — the seam between read pages and page-cited findings.

WHY THIS IS AN INTERFACE AND NOT A FUNCTION
The plan this branch implements says "use the existing Claude subscription through the installed
CLI in non-interactive mode". On 2026-09-22 the desktop session doing exactly that hit its usage
limit MID-CASE and stopped until 2:13 PM. A pipeline that runs nightly cannot have a hard stop
that depends on a human's chat quota, and a pipeline that silently swaps to metered API billing
when the quota runs out is worse — that is an unbounded bill nobody approved.

So: two named backends, chosen explicitly, and NEITHER falls back to the other.

    api   the intended production path. Metered, and therefore capped — `Budget` refuses the call
          that would take the run past its dollar limit, before it is made.
    cli   a DEV switch only. Uses the local `claude` CLI and the operator's subscription. Must be
          asked for by name (DEALFLOW_INTERPRETER=cli); it is never selected automatically and it
          is never what a scheduled run gets.

WHAT AN INTERPRETATION IS ALLOWED TO BE
A page-cited extraction, and nothing more. Every finding carries the document hash, the page, and
the verbatim passage it rests on — and `verified: False` until something checks it. Nothing here
writes to a lead, and nothing here can move a lead into equity_state's FACT states: `clear` and
`priced` are reached through records_liens' recorded-instrument evidence, not through a model
reading a PDF. See equity_state's docstring for why that line is where it is.
"""
import json
import os
import subprocess

# Anthropic first-party list prices, US$ per million tokens, as published in the Claude API
# reference (cached 2026-06-24). Used ONLY to cap spend; it is not a billing record.
PRICING = {
    'claude-opus-5':   {'input': 5.00, 'output': 25.00},
    'claude-sonnet-5': {'input': 2.00, 'output': 10.00},
    'claude-haiku-4-5': {'input': 1.00, 'output': 5.00},
}
DEFAULT_MODEL = 'claude-opus-5'
DEFAULT_MAX_TOKENS = 4000


class NotConfigured(RuntimeError):
    """This backend cannot run here, and no other backend will be substituted for it."""


class BudgetExhausted(RuntimeError):
    """The run's dollar cap would be exceeded by the next call. The call is not made."""


class Budget:
    """A per-run dollar ceiling, checked BEFORE each call against that call's worst case.

    The pre-check uses the worst case (every output token billed at `max_tokens`), not an average,
    because a cap that is only noticed after the money is spent is not a cap.
    """

    def __init__(self, limit_usd, model=DEFAULT_MODEL):
        if limit_usd is None or float(limit_usd) <= 0:
            raise ValueError('An interpretation run requires a positive dollar cap')
        if model not in PRICING:
            raise ValueError('No published price for model %r; refusing to run uncapped' % model)
        self.limit = float(limit_usd)
        self.model = model
        self.spent = 0.0
        self.calls = 0

    def price(self, input_tokens, output_tokens):
        p = PRICING[self.model]
        return (input_tokens * p['input'] + output_tokens * p['output']) / 1_000_000.0

    def check(self, input_tokens, max_output_tokens):
        worst = self.price(input_tokens, max_output_tokens)
        if self.spent + worst > self.limit:
            raise BudgetExhausted(
                'next call could cost $%.4f; $%.4f of the $%.2f cap is already spent'
                % (worst, self.spent, self.limit))
        return worst

    def record(self, input_tokens, output_tokens):
        self.spent += self.price(input_tokens, output_tokens)
        self.calls += 1
        return self.spent

    def report(self):
        return {'model': self.model, 'limit_usd': round(self.limit, 4),
                'spent_usd': round(self.spent, 6), 'calls': self.calls}


INSTRUCTION = (
    'You are reading pages of a Florida foreclosure court document. Extract only what the text '
    'in front of you states. For every item, quote the exact passage it comes from and give its '
    'page number. If the pages do not state something, omit it — never infer it. Distinguish what '
    'a party ALLEGED from what the court FOUND or ORDERED. Reply with JSON only, shaped: '
    '{"findings": [{"field": "...", "value": "...", "page": N, "passage": "..."}], '
    '"unresolved": ["..."]}'
)


class Interpreter:
    name = ''

    def interpret(self, pages, budget, instruction=INSTRUCTION):
        raise NotImplementedError


class ApiInterpreter(Interpreter):
    """Metered Claude API. Requires a Budget; there is no uncapped mode."""
    name = 'api'

    def __init__(self, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS, client=None):
        self.model = model
        self.max_tokens = max_tokens
        self._client = client

    def client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError:
            raise NotConfigured('the anthropic SDK is not installed (pip install anthropic)')
        if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
            # Deliberately not falling through to the CLI. An unattended run that quietly switched
            # billing model is the failure this module exists to prevent.
            raise NotConfigured('no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in the environment')
        self._client = anthropic.Anthropic()
        return self._client

    def interpret(self, pages, budget, instruction=INSTRUCTION):
        if budget is None:
            raise ValueError('ApiInterpreter requires a Budget')
        client = self.client()
        body = '\n\n'.join('--- page %d ---\n%s' % (p['page'], p.get('text') or '')
                           for p in pages if p.get('outcome') == 'text')
        if not body.strip():
            # Every page was an image. There is nothing to read, and asking anyway would buy a
            # confident answer about a blank prompt.
            return {'findings': [], 'unresolved': ['no extractable text on any page'],
                    'skipped': 'image_only'}
        counted = client.messages.count_tokens(
            model=self.model, system=instruction,
            messages=[{'role': 'user', 'content': body}])
        budget.check(counted.input_tokens, self.max_tokens)
        response = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=instruction,
            messages=[{'role': 'user', 'content': body}])
        budget.record(response.usage.input_tokens, response.usage.output_tokens)
        text = ''.join(b.text for b in response.content if b.type == 'text')
        return _parse(text)


class CliInterpreter(Interpreter):
    """DEV ONLY. The local `claude` CLI on the operator's subscription.

    Never selected by default and never used as a fallback: a scheduled run must not depend on a
    personal chat quota, and the desktop session proved on 2026-09-22 that it will run out.
    """
    name = 'cli'

    def __init__(self, binary='claude', timeout=300):
        self.binary = binary
        self.timeout = timeout

    def interpret(self, pages, budget=None, instruction=INSTRUCTION):
        body = '\n\n'.join('--- page %d ---\n%s' % (p['page'], p.get('text') or '')
                           for p in pages if p.get('outcome') == 'text')
        if not body.strip():
            return {'findings': [], 'unresolved': ['no extractable text on any page'],
                    'skipped': 'image_only'}
        try:
            proc = subprocess.run([self.binary, '-p', instruction + '\n\n' + body],
                                  capture_output=True, text=True, timeout=self.timeout)
        except FileNotFoundError:
            raise NotConfigured('the `claude` CLI is not on PATH')
        if proc.returncode != 0:
            raise NotConfigured('claude CLI exited %d: %s'
                                % (proc.returncode, (proc.stderr or '')[:300]))
        return _parse(proc.stdout)


BACKENDS = {'api': ApiInterpreter, 'cli': CliInterpreter}


def build(name=None, **kw):
    """Explicit selection only. An unknown name is an error, never a default."""
    name = (name or os.environ.get('DEALFLOW_INTERPRETER') or 'api').strip().lower()
    if name not in BACKENDS:
        raise ValueError('Unknown interpreter %r; choose one of %s'
                         % (name, ', '.join(sorted(BACKENDS))))
    return BACKENDS[name](**kw)


def _parse(text):
    """Model output is untrusted text. A reply we cannot parse is unresolved, not empty."""
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0]
    try:
        data = json.loads(raw)
    except ValueError:
        return {'findings': [], 'unresolved': ['interpreter reply was not JSON'],
                'raw': raw[:2000]}
    findings = []
    for item in (data.get('findings') or []):
        if not isinstance(item, dict):
            continue
        # A finding without a page and a passage is an assertion, not evidence. Dropped.
        if item.get('page') is None or not str(item.get('passage') or '').strip():
            continue
        findings.append({'field': item.get('field'), 'value': item.get('value'),
                         'page': item.get('page'), 'passage': item.get('passage'),
                         'verified': False, 'source': 'document_text'})
    return {'findings': findings,
            'unresolved': [str(u) for u in (data.get('unresolved') or [])]}
