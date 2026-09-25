"""Private Miami backfill checkpoints. No publication, equity writes or subscription reader.

A finished *attempt* is not a complete case. Gap cases remain visibly incomplete and
can be retried explicitly. The cap is cumulative across restarts of this checkpoint:
an interrupted API call retains its worst-case reservation instead of becoming free.
"""
import hashlib
import json
import re
import math
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid
import time
from datetime import date, datetime, timedelta

import document_store as DS
from document_interpreter import Budget, BudgetExhausted


def select_cases(rows, chains, today=None):
    """Include ownerless cases; deduplicate case identities, not people or properties."""
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        county = str(row.get('county') or '').upper().replace(' ', '-')
        host = urlparse(str(row.get('auction_url') or '')).hostname
        if county not in ('MIAMI-DADE', 'MIAMI', 'DADE'):
            if county or host != 'miamidade.realforeclose.com':
                continue
        case = str(row.get('Case #') or '').strip()
        if not case:
            raise ValueError('Miami lead has no case number; cannot silently omit it')
        entry = {'case': case, 'owner': str(row.get('owner_clean') or '').strip(),
                 'folio': row.get('Folio') or row.get('year_folio') or '',
                 'chain': chains.get(case), 'auction_date': row.get('AuctionDate') or ''}
        if case in selected and selected[case] != entry:
            raise ValueError('Conflicting duplicate case rows; resolve before backfill')
        selected[case] = entry
    today = today or date.today()
    def priority(entry):
        auction = None
        for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
            try:
                auction = datetime.strptime(entry['auction_date'], fmt).date()
                break
            except ValueError:
                continue
        if auction is None:
            return (3, 0, entry['case'])
        if today <= auction <= today + timedelta(days=45):
            return (0, auction.toordinal(), entry['case'])
        if auction > today:
            return (1, auction.toordinal(), entry['case'])
        return (2, -auction.toordinal(), entry['case'])
    return sorted(selected.values(), key=priority)


def fingerprint(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def progress(entries, data):
    statuses = []
    for entry in entries:
        old = data['cases'].get(entry['case'], {})
        statuses.append(old.get('status') if old.get('fingerprint') == fingerprint(entry) else 'pending')
    done = sum(s in ('complete', 'assessed_with_gaps') for s in statuses)
    return {'cases_total': len(entries), 'cases_done': done,
            'cases_complete': statuses.count('complete'), 'cases_left': len(entries) - done}


def snapshot(path):
    if not Path(path).exists():
        return {'version': 1, 'cases': {}, 'actual_usd': 0.0, 'reserved': {}}
    # Corrupt state MUST fail closed, never reset money or progress to zero.
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('version') != 1 or not isinstance(data.get('cases'), dict)
            or not isinstance(data.get('reserved'), dict)):
        raise ValueError('Invalid backfill checkpoint')
    for amount in [data.get('actual_usd')] + list(data['reserved'].values()):
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
            raise ValueError('Invalid backfill spend ledger')
    return data


class State:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(self.path) + '.lock', 'a+b')
        self.lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('Another backfill worker holds this checkpoint') from None
        try:
            self.data = snapshot(self.path)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        self.lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def save(self):
        payload = json.dumps(self.data, indent=2) + '\n'
        for attempt in range(5):
            try:
                DS._atomic_write_text(str(self.path), payload)
                return
            except PermissionError:
                # Windows readers/antivirus may briefly deny os.replace. Never
                # proceed to a paid request without a durable reservation.
                if attempt == 4:
                    raise
                time.sleep(.1 * (2 ** attempt))

    def pending(self, entry, retry_gaps=False):
        old = self.data['cases'].get(entry['case'], {})
        return (old.get('fingerprint') != fingerprint(entry)
                or old.get('status') in (None, 'running', 'budget_paused', 'failed')
                or (retry_gaps and old.get('status') == 'assessed_with_gaps'))

    def steps(self, case):
        """Per-step records for one case: {step: {'fingerprint', 'status', ...}}. They survive
        start() and finish(), so a restart resumes at the first step not yet done."""
        return self.data['cases'].setdefault(case, {}).setdefault('steps', {})

    def step_done(self, case, step, fp, retry_gaps=False):
        record = self.steps(case).get(step) or {}
        return record.get('fingerprint') == fp and record.get('status') in (
            ('done',) if retry_gaps else ('done', 'gap'))

    def mark_step(self, case, step, fp, status, **detail):
        self.steps(case)[step] = dict(detail, fingerprint=fp, status=status)
        self.save()

    def start(self, entry):
        steps = self.data['cases'].get(entry['case'], {}).get('steps', {})
        self.data['cases'][entry['case']] = {
            'fingerprint': fingerprint(entry), 'status': 'running', 'steps': steps}
        self.save()

    def finish(self, entry, dossier, paused=False):
        steps = self.data['cases'].get(entry['case'], {}).get('steps', {})
        self.data['cases'][entry['case']] = {
            'fingerprint': fingerprint(entry),
            # A documents step stopped by a spending cap is paused too, so the next run takes the
            # case up again without --retry-gaps (an audit of #53: it read as done-with-gaps).
            'status': ('budget_paused' if paused or (steps.get('documents') or {}).get('status') == 'budget'
                       else 'complete' if dossier.get('complete') is True else 'assessed_with_gaps'),
            'open_gaps': dossier.get('open_gaps', []),
            'steps': steps,
        }
        self.save()


_VISION_STOP_RE = re.compile(r'\bnot bought - budget:')


def _documents_step_status(dossier):
    """'done' only for a complete dossier. A dossier with gaps is 'gap' (redone by --retry-gaps);
    one that stopped on a spending cap is 'budget', redone on every run, so a raised cap or a new
    day reads the refused pages. Re-running is spend-safe: settled pages come back free from the
    page ledger and uncertain ones stay blocked."""
    if dossier.get('complete') is True:
        return 'done'
    # Only the paid reader's refusal (case_dossier: "page N not bought - budget: ..."). The walk's
    # document-count cap and --token-budget also say "budget" and are not spending stops.
    stopped = any(_VISION_STOP_RE.search(str(g)) for g in dossier.get('open_gaps') or [])
    return 'budget' if stopped else 'gap'


class PersistentBudget(Budget):
    def __init__(self, limit, state):
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError('A finite positive cap is required')
        super().__init__(limit)
        self.state = state
        self.spent = state.data['actual_usd'] + sum(state.data['reserved'].values())
        self.reservation = None
        self.exhausted = False

    def check(self, input_tokens, max_output_tokens):
        worst = self.price(input_tokens, max_output_tokens)
        charged = self.state.data['actual_usd'] + sum(self.state.data['reserved'].values())
        if charged + worst > self.limit:
            self.exhausted = True
            raise BudgetExhausted('Cumulative backfill cap would be exceeded')
        self.reservation = uuid.uuid4().hex
        self.state.data['reserved'][self.reservation] = worst
        self.state.save()  # Durable BEFORE the paid call.
        return worst

    def record(self, input_tokens, output_tokens):
        return self.record_read(input_tokens, output_tokens, None, None)

    def record_read(self, input_tokens, output_tokens, key, result):
        if self.reservation is None:
            raise RuntimeError('Cannot record an unreserved API call')
        actual = self.price(input_tokens, output_tokens)
        self.state.data['reserved'].pop(self.reservation)
        self.state.data['actual_usd'] += actual
        if key is not None:
            self.state.data.setdefault('page_reads', {})[key] = result
        # The reusable response and its settled cost commit together.
        self.state.save()
        self.reservation = None
        self.spent = self.state.data['actual_usd'] + sum(self.state.data['reserved'].values())
        self.calls += 1
        return self.spent

    def cached_read(self, key):
        result = self.state.data.get('page_reads', {}).get(key)
        return dict(result, usd=0.0, cache_reused=True) if result is not None else None

def _timeline_step(args, runner, state, entry, budget, target, dossier):
    import run_case_timeline
    case = entry['case']
    fp = fingerprint({'case': case, 'processing': entry['_processing'],
                      'as_of': date.today().isoformat(),
                      'collect': bool(getattr(args, 'collect_dockets', False))})
    # --retry-gaps retries a timeline that ended in a gap (no saved docket yet) the same day,
    # instead of the checkpoint calling it finished (Greptile on #53).
    if state.step_done(case, 'timeline', fp, retry_gaps=bool(getattr(args, 'retry_gaps', False))):
        return DS.pipeline_load(target) or dossier
    try:
        timeline, _ = run_case_timeline.timeline_case(
            case, date.today(), collect=bool(getattr(args, 'collect_dockets', False)),
            shared=budget)
    except ValueError as exc:
        # No saved full OCS inventory, or a truncated one. A named gap, never a silent skip.
        gap = 'timeline: %s' % exc
        dossier = dict(DS.pipeline_load(target) or dossier)
        dossier['complete'] = False
        dossier['open_gaps'] = list(dossier.get('open_gaps') or []) + [gap]
        DS.pipeline_write(target, dossier)
        state.mark_step(case, 'timeline', fp, 'gap', reason=str(exc)[:300])
        return dossier
    dossier = dict(DS.pipeline_load(target) or dossier)
    # An earlier run's timeline gap is answered by this run, whatever it found.
    earlier = [g for g in dossier.get('open_gaps') or [] if str(g).startswith('timeline: ')]
    if earlier:
        dossier['open_gaps'] = [g for g in dossier['open_gaps'] if g not in earlier]
        dossier['complete'] = not dossier['open_gaps']      # case_dossier's own rule
    if not timeline.get('coverage_complete'):
        dossier['complete'] = False
        dossier['open_gaps'] = list(dossier.get('open_gaps') or []) + [
            'timeline: %d gap(s) in the whole-case docket' % len(timeline.get('gaps') or [])]
    if earlier or not timeline.get('coverage_complete'):
        DS.pipeline_write(target, dossier)
    state.mark_step(case, 'timeline', fp, 'done', status_kind=(timeline.get('status') or {}).get('kind'),
                    controlling_judgment=(timeline.get('judgments') or {}).get('controlling_entry'))
    return dossier


def run(args, runner):
    """Use the existing case pipeline and document queue, never a second reader."""
    source = Path(args.leads_file or runner.LEADS)
    rows = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('leads_final.json must be an array')
    picked = select_cases(rows, runner._load(runner.CHAINS, {}))
    for entry in picked:
        # Enabling vision after an OCR-only pass (or changing reader versions/options)
        # creates unfinished work, even if the lead row itself has not changed.
        entry['_processing'] = {
            'vision': args.vision, 'ocr': not args.no_ocr,
            'keep_images': args.keep_images, 'reader': DS.READER_VERSION,
            'pipeline': runner.MJ.PIPELINE_VERSION,
            'walk_depth': args.walk_depth if args.walk_cites else 0,
            'walk_budget': args.walk_budget,
        }
        if getattr(args, 'timeline', False):
            # Only when asked, so turning the step on re-opens cases and a run without it keeps
            # the checkpoint fingerprints it always had.
            entry['_processing']['timeline'] = True
    path = runner.dossier_path(runner.COUNTY, '_backfill_state')
    data = snapshot(path)
    charged = data['actual_usd'] + sum(data['reserved'].values())
    print('backfill: %d unique Miami cases; %d without owner names'
          % (len(picked), sum(not p['owner'] for p in picked)))
    print('  worst-case cumulative vision spend: $%.2f; already charged/reserved $%.6f; '
          'remaining ceiling $%.6f' % (args.vision_max_spend, charged,
                                     max(0, args.vision_max_spend - charged)))
    print('  This is a spending ceiling, NOT a price to finish every case. Captcha spend: $0.00.')
    print('  resume checkpoint: %s' % path)
    print('  order: auctions in next 45 days first, nearest date first; then later auctions, '
          'past auctions nearest first, unknown dates last')
    if args.dry_run:
        print('  DRY RUN: no clients, requests, downloads, OCR, or checkpoint writes.')
        return 0
    if not args.enable:
        print('  OFF: backfill requires explicit --enable, even with DEALFLOW_DOCS=1.')
        return 0
    with State(path) as state:
        budget = PersistentBudget(args.vision_max_spend, state) if args.vision else None
        if budget and budget.spent >= budget.limit:
            print('  PAUSED: cumulative cap exhausted; no API client started.')
            print('  progress: %s' % json.dumps(progress(picked, state.data), sort_keys=True))
            return 4
        if budget:
            import document_vision
            document_vision.VisionReader().client()
            # Per-case shares over a FIXED roster: every case in this backfill. Cases already
            # finished in the checkpoint release their share at once; a pending case's share is
            # protected from every other case (document_case_budget).
            from document_case_budget import CaseAllocator
            budget = CaseAllocator(budget, [entry['case'] for entry in picked])
            for entry in picked:
                if not state.pending(entry, args.retry_gaps):
                    budget.finish(entry['case'])
        qs_cache = runner._load(runner.QS_CACHE, {})
        queue = runner.DocumentQueue()
        try:
            for entry in picked:
                if not state.pending(entry, args.retry_gaps):
                    continue
                state.start(entry)
                case = entry['case']
                target = runner.dossier_path(runner.COUNTY, case)
                # STEP 1, documents: the recorded instruments through run_case. Skipped on a
                # restart when this exact step already finished for this exact entry.
                docs_fp = fingerprint(dict(entry, _processing={
                    k: v for k, v in entry['_processing'].items() if k != 'timeline'}))
                saved = (DS.pipeline_load(target)
                         if state.step_done(case, 'documents', docs_fp, args.retry_gaps) else None)
                if saved is not None:
                    dossier = saved
                else:
                    dossier = runner.run_case(
                        entry, qs_cache, queue=queue,
                        ocr=None if args.no_ocr else DS.winocr,
                        keep_images=args.keep_images, vision_budget=budget,
                        walk_depth=args.walk_depth if args.walk_cites else 0,
                        walk_budget=args.walk_budget, resume=True, reuse_done=True)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    DS._atomic_write_text(str(target), json.dumps(dossier, indent=2) + '\n')
                    state.mark_step(case, 'documents', docs_fp, _documents_step_status(dossier))
                    # The saved dossier was just replaced, so a timeline step recorded as done
                    # would reload it without its own gaps and could call the case complete.
                    state.steps(case).pop('timeline', None)
                # STEP 2, timeline: the whole-case docket and its filings, through the SAME
                # ledger and the SAME per-case share (run_case_timeline.timeline_case).
                if getattr(args, 'timeline', False):
                    dossier = _timeline_step(args, runner, state, entry, budget, target, dossier)
                # One case spending its share pauses nothing: the next case has its own. The
                # backfill pauses only when the cumulative cap itself is spent.
                paused = bool(budget and budget.budget.exhausted)
                state.finish(entry, dossier, paused=paused)
                print('  %s: %s' % (entry['case'], state.data['cases'][entry['case']]['status']))
                if paused:
                    return 4
        finally:
            queue.close()
            print('  API actual $%.6f; unresolved request reservations $%.6f'
                  % (state.data['actual_usd'], sum(state.data['reserved'].values())))
            summary = dict(progress(picked, state.data), actual_usd=state.data['actual_usd'],
                           reserved_usd=sum(state.data['reserved'].values()),
                           cap_usd=args.vision_max_spend)
            state.data['last_summary'] = summary
            state.save()
            print('  progress: %s' % json.dumps(summary, sort_keys=True))
    return 0
