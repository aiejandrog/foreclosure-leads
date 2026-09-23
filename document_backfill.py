"""Private Miami backfill checkpoints. No publication, equity writes or subscription reader.

A finished *attempt* is not a complete case. Gap cases remain visibly incomplete and
can be retried explicitly. The cap is cumulative across restarts of this checkpoint:
an interrupted API call retains its worst-case reservation instead of becoming free.
"""
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlparse
import uuid

import document_store as DS
from document_interpreter import Budget, BudgetExhausted


def select_cases(rows, chains):
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
    return list(selected.values())


def fingerprint(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


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
        DS._atomic_write_text(str(self.path), json.dumps(self.data, indent=2) + '\n')

    def pending(self, entry, retry_gaps=False):
        old = self.data['cases'].get(entry['case'], {})
        return (old.get('fingerprint') != fingerprint(entry)
                or old.get('status') in (None, 'running', 'budget_paused', 'failed')
                or (retry_gaps and old.get('status') == 'assessed_with_gaps'))

    def start(self, entry):
        self.data['cases'][entry['case']] = {
            'fingerprint': fingerprint(entry), 'status': 'running'}
        self.save()

    def finish(self, entry, dossier, paused=False):
        self.data['cases'][entry['case']] = {
            'fingerprint': fingerprint(entry),
            'status': ('budget_paused' if paused else
                       'complete' if dossier.get('complete') is True else 'assessed_with_gaps'),
            'open_gaps': dossier.get('open_gaps', []),
        }
        self.save()


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
            return 4
        if budget:
            import document_vision
            document_vision.VisionReader().client()
        qs_cache = runner._load(runner.QS_CACHE, {})
        queue = runner.DocumentQueue()
        try:
            for entry in picked:
                if not state.pending(entry, args.retry_gaps):
                    continue
                state.start(entry)
                dossier = runner.run_case(
                    entry, qs_cache, queue=queue,
                    ocr=None if args.no_ocr else DS.winocr,
                    keep_images=args.keep_images, vision_budget=budget,
                    walk_depth=args.walk_depth if args.walk_cites else 0,
                    walk_budget=args.walk_budget, resume=True, reuse_done=True)
                target = runner.dossier_path(runner.COUNTY, entry['case'])
                target.parent.mkdir(parents=True, exist_ok=True)
                DS._atomic_write_text(str(target), json.dumps(dossier, indent=2) + '\n')
                paused = bool(budget and budget.exhausted)
                state.finish(entry, dossier, paused=paused)
                print('  %s: %s' % (entry['case'], state.data['cases'][entry['case']]['status']))
                if paused:
                    return 4
        finally:
            queue.close()
            print('  API actual $%.6f; unresolved request reservations $%.6f'
                  % (state.data['actual_usd'], sum(state.data['reserved'].values())))
    return 0
