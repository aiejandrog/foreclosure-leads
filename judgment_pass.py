"""judgment_pass — the $0 whole-case docket pass over every Miami lead, then a priced read plan.

Why this exists (2026-09-25): a judgment amount is only trusted when the court's own copy was read
and its line items reproduce the printed total (judgment_money's exact-cents check). On the 09-24
sweep that held for 30 of 349 Miami leads. The 09-23 backfill went through the recorded-instrument
route, which needs a cached owner-search token, and 284 cases had none. The whole-case timeline
(#53, run_case_timeline) reaches the final judgment through the court docket instead: no token, no
captcha. Its only paid step is the vision read of amount pages.

So this does two things, in order, and neither spends:

  1. PASS: run_case_timeline.timeline_case(case, collect=True/False) with NO vision budget, soonest
     sale first. That collects the full OCS docket, downloads the accessible filings, OCRs them for
     free, and reuses any hash-bound vision evidence already on disk. Resumable: a case whose
     timeline was written in the last 20 hours is skipped.
  2. PLAN: for every case, from files on disk only, whether its controlling (or latest) judgment
     already has an exact-cents verified amount, and if not, how many amount pages the paid reader
     would have to send to reach it, priced at the measured average of the evidence already bought.

It never creates an API client, never mints a token, never writes equity. Amounts it reports are
arithmetic agreement on one filing (see run_case_timeline.read_amounts), never an equity input.

    python judgment_pass.py --collect             # the free pass, then the plan
    python judgment_pass.py --collect --days 30   # only sales in the next 30 days
    python judgment_pass.py --report-only         # the plan from what is already on disk
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

# Measured 2026-09-22 on the desktop: $0.045 per judgment once page selection sent two pages.
# Used only when no bought evidence is on disk to average; the report says which it used.
FALLBACK_USD_PER_PAGE = 0.0225


def _is_case(value):
    import run_case_timeline as RCT
    try:
        RCT.validate_case(value)
        return True
    except ValueError:
        return False


def _auction(entry):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(entry.get('auction_date') or ''), fmt).date()
        except ValueError:
            continue
    return None


def _with_cached_ocr(row, base):
    """The row as the paid reader sees it: plus the free 300-DPI OCR the pass already cached
    (miami_timeline_ocr's hash-keyed file). Cache only: supplement() itself would OCR, and off
    Windows it would write ocr_failed into the cache a real pass later trusts."""
    import copy
    manifest = row.get('manifest') or {}
    path = manifest.get('path') or manifest.get('pdf_path') or row.get('path')
    if not path:
        return row                     # no stored file at all: the pass could not OCR it either
    # supplement() OCRs only text/embedded pages; a row with none has nothing to look up.
    targets = [p for p in (row.get('reading') or {}).get('pages', [])
               if p.get('outcome') == 'text' or p.get('text_source') == 'embedded']
    if not targets:
        return row
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        # The stored PDF is not reachable here (a report run on another machine): the free OCR
        # cannot be looked up, so amount pages may be missing. Marked, never silent.
        return dict(row, _ocr_unreachable='pdf')
    try:
        saved = json.loads((Path(base) / 'timeline-ocr' / (digest + '-ocr300-v1.json'))
                           .read_text(encoding='utf-8'))
    except (OSError, ValueError):
        # No cache means the pass never OCR'd it, so OCR-only amount pages are unknown here.
        return dict(row, _ocr_unreachable='cache')
    row = copy.deepcopy(row)
    if any(str(p.get('page')) not in (saved.get('pages') or {}) for p in targets):
        # supplement() checkpoints page by page: a pass killed mid-document leaves a partial cache.
        row['_ocr_unreachable'] = 'cache'
    for page in (row.get('reading') or {}).get('pages', []):
        # Only the pages supplement() would OCR: a stale cache entry on another page is not one
        # the paid reader would select.
        if page.get('outcome') != 'text' and page.get('text_source') != 'embedded':
            continue
        got = (saved.get('pages') or {}).get(str(page.get('page')))
        if got:
            page['supplemental_ocr'] = got
    return row


def load_entries(runner, leads_file=None):
    import document_backfill
    rows = json.loads(Path(leads_file or runner.LEADS).read_text(encoding='utf-8'))
    return document_backfill.select_cases(rows, runner._load(runner.CHAINS, {}))


def timeline_path(runner, case):
    path = Path(runner.dossier_path(runner.COUNTY, case))
    return path.with_name(path.stem + '-timeline.json')


FRESH_HOURS = 20


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0


def built_recently(path, hours=FRESH_HOURS, now=None):
    """A timeline written in the last `hours` is skipped on a re-run. By file age, not the
    timeline's as_of date: a pass that crosses local midnight would otherwise rebuild every case
    the previous day's half already built."""
    try:
        age = (now or time.time()) - Path(path).stat().st_mtime
    except OSError:
        return False
    return 0 <= age < hours * 3600


def evidence_rate(root):
    """Average dollars per amount page over the vision evidence already bought, or None."""
    usd = pages = 0
    for path in Path(root).glob('*/amount-vision-*.json'):
        try:
            detail = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        # Only pages that were actually billed: a page cut off by the cap, an errored page or a
        # ledger-cache hit at $0 is not a price sample.
        for result in (detail.get('pages') or {}).values():
            cost = float((result or {}).get('usd') or 0)
            if cost > 0:
                usd += cost
                pages += 1
    return (usd / pages, pages) if pages else (None, 0)


def _target(recon, controlling):
    """-> (entry_id or None, reason). The controlling entry when the timeline established one;
    else the latest OPERATIVE judgment of record in the docket's own reconciliation. A vacated,
    satisfied, supplemental or duplicate entry is never the target: its amount proves nothing
    about the current debt."""
    import miami_case_timeline as MCT
    if controlling:
        return controlling, 'controlling'
    judgments = (recon or {}).get('judgments') or []
    of_record = [j for j in judgments if j.get('role') not in MCT._NOT_A_JUDGMENT_OF_RECORD
                 and j.get('date')]
    operative = [j for j in of_record if j.get('status') in ('operative', 'partially_vacated')]
    if operative:
        best = max(operative, key=lambda j: (j['date'], _entry_order(j['entry_id'])))
        return str(best['entry_id']), 'latest_operative'
    # The reconciliation could not tie them together (two same-day entries, a vacatur naming no
    # judgment): the latest unclear one is still the one to read, and the basis says so.
    unclear = [j for j in of_record if j.get('status') == 'unclear']
    if unclear:
        best = max(unclear, key=lambda j: (j['date'], _entry_order(j['entry_id'])))
        return str(best['entry_id']), 'latest_unclear'
    return None, ('no_operative_judgment' if judgments else 'no_judgment_on_docket')


def _entry_order(entry_id):
    text = str(entry_id)
    return (0, int(text), '') if text.isdigit() else (1, 0, text)


def _every_document_checked(target, checks, order, done):
    """Every bought amount document of the target produced at least one exact-cents check."""
    import miami_timeline_amounts as amounts
    checked = {c.get('source_ref') for c in checks}
    for row in order:
        if str(row.get('entry_ref') or '') != target:
            continue
        if not amounts.amount_page_numbers(row.get('reading') or {}):
            continue
        key = 'amount-vision-' + hashlib.sha256(str(row.get('source_ref')).encode()).hexdigest() + '.json'
        if key in done and row.get('source_ref') not in checked:
            return False
    return True


def assess(case, base, timeline, as_of, timeline_mtime=None):
    """One case's state from disk. -> dict with 'state' and the page counts a paid read needs.

    Evidence is read through run_case_timeline.read_amounts with no budget: the same hash binding
    and the same exact-cents check the timeline itself uses, so the two cannot disagree."""
    import document_store as DS
    import document_prioritizer as DP
    import miami_timeline_amounts as amounts
    import run_case_timeline as RCT
    out = {'case': case, 'pages_to_judgment': 0, 'pages_all': 0}
    inventory = DS.pipeline_load(Path(base) / 'inventory.json')
    if inventory is None:
        out['state'] = 'no_docket'
        return out
    if timeline is None:
        # Nothing read the judgment bodies or checked their attachments, so no target the docket
        # index alone suggests is a finding. The pass builds it.
        out.update(state='timeline_missing', detail='no readable timeline on disk: run the pass '
                   'for this case')
        return out
    try:
        # As of the day the timeline was built, so the read order and the timeline's own
        # reconciliation see the same docket (a pass may cross midnight before the report).
        plan = DP.prioritize(case, inventory, str(timeline.get('as_of') or as_of)[:10])
    except ValueError as exc:
        out.update(state='docket_incomplete', detail=str(exc)[:200])
        return out
    try:
        docket_mtime = (Path(base) / 'inventory.json').stat().st_mtime
    except OSError:
        docket_mtime = None
    if timeline_mtime is not None and docket_mtime is not None \
            and timeline_mtime < docket_mtime:
        # The docket was refreshed after the timeline was built (a rebuild that failed part-way):
        # its controlling judgment and checks may predate a new vacatur or satisfaction.
        out.update(state='timeline_older_than_docket',
                   detail='re-run the pass for this case before trusting any verdict')
        return out
    # Only the timeline, which read the bodies, can establish a controlling judgment. The docket
    # plan's reconciliation is index metadata (it says controlling_judgment_established False).
    controlling = str((timeline.get('judgments') or {}).get('controlling_entry') or '')
    out['controlling_entry'] = controlling or None
    # The timeline's reconciliation read the bodies (a vacatur citing its judgment's date, an
    # image-less same-day twin); the plan's is the docket index alone. Prefer the timeline's.
    recon = timeline.get('judgments') or plan.get('judgments')
    target, why = _target(recon, controlling)
    if target is None:
        out['state'] = why
        return out
    out.update(target_entry=target, target_basis=why)
    if why != 'controlling':
        out['detail'] = ('no controlling judgment established; target is the %s one'
                         % why.replace('latest_', 'latest '))
    rows = RCT.load_rows(base)
    bought = RCT.read_amounts(rows, base)
    target_checks = [c for c in bought['amount_checks'] if str(c.get('entry_id')) == target]
    # Classify every recorded gap by what the NEXT paid run would do with that page.
    # - Unreadable: the ledger cache returns the same answer at $0. The page needs a person.
    # - An API or network error: read_page reserves in the ledger before messages.create, and
    #   nothing releases a reservation whose call raised, so the next run's cached_read refuses
    #   that page as an UncertainPaidCall. The page needs a person, and so does an
    #   UncertainPaidCall itself. read_document carries on to the document's other pages.
    # - The cap, the reader's stop, or a setup failure raised before any reservation (no key, no
    #   SDK, a rejected key, no PDF renderer): paying on a fixed setup buys the page.
    rebuy, stuck_docs, stuck_pages = {}, set(), {}
    for gap in bought['gaps']:
        ref, reason = gap.get('source_ref'), str(gap.get('reason') or '')
        if reason.startswith('Vision returned unreadable') or re.search(
                r'UncertainPaidCall|APIStatus|APIConnection|RateLimit|Timeout|overloaded|'
                r'InternalServer|ServiceUnavailable|Connection|Error code: (?:429|5\d\d)', reason):
            stuck_pages.setdefault(ref, set()).add(gap.get('page'))
        elif re.search(r'budget|cap or reader stop|NotConfigured|'
                       r'not configured|ANTHROPIC_API_KEY|SDK is not installed|Authentication|'
                       r'PermissionDenied|Error code: 40[13]|PyMuPDF is not installed', reason, re.I):
            rebuy.setdefault(ref, set()).add(gap.get('page'))
        else:
            # Anything unrecognised (a rejected or missing file, an uncertain paid call) is taken
            # to repeat: under-pricing a case beats promising a read that cannot happen.
            stuck_docs.add(ref)
    done = {Path(f).name for f in bought['evidence_files']}
    # Only the rows the paid reader would walk get the (hash-keyed, whole-file) OCR lookup.
    order = [_with_cached_ocr(r, base) for r in DP.timeline_read_order(plan, rows)['order']]
    # The judgment's documents the plan holds out of the order (an undated entry, no image
    # count): the reader defers them, but they still decide whether the judgment is fully read.
    in_order = {r.get('source_ref') for r in order}
    held = [_with_cached_ocr(r, base) for r in rows
            if str(r.get('entry_ref') or '') == target and r.get('source_ref') not in in_order]
    held_refs = {r.get('source_ref') for r in held}

    def pages_left(row, pages):
        ref = row.get('source_ref')
        key = 'amount-vision-' + hashlib.sha256(str(ref).encode()).hexdigest() + '.json'
        if key not in done:
            return len(pages)
        if ref in stuck_docs:
            return 0
        detail = DS.pipeline_load(Path(base) / key) or {}
        # Amount pages the free OCR found after the purchase are new pages to buy.
        fresh = set(pages) - set(detail.get('selected_pages') or pages)
        # A capped page the current OCR no longer selects is not one the reader would buy.
        return len((((rebuy.get(ref) or set()) & set(pages)) | fresh)
                   - (stuck_pages.get(ref) or set()))

    need = []          # (is_target, pages) for every amount-bearing row with pages left to buy
    target_amount_rows = 0
    held_unread = False
    for row in order + held:
        is_target = str(row.get('entry_ref') or '') == target
        pages = amounts.amount_page_numbers(row.get('reading') or {})
        if not pages:
            continue
        target_amount_rows += is_target
        left = pages_left(row, pages)
        if row.get('source_ref') in held_refs:
            held_unread = held_unread or bool(left)
        elif left:
            need.append((is_target, left))
    notes = [out['detail']] if out.get('detail') else []
    target_doc = next((d for d in plan['documents'] if str(d['entry_id']) == target), {})

    def held_why():
        return ','.join(target_doc.get('gaps') or []) or 'not_eligible'
    target_refs = {r.get('source_ref') for r in order + held
                   if str(r.get('entry_ref') or '') == target}
    # The timeline's own gaps on the target entry: a missing attachment, a page neither text nor
    # OCR could read, pages never assessed. Any of them means an amount page may be unseen.
    target_gaps = sorted({str(g.get('kind')) for g in timeline.get('gaps') or []
                          if str(g.get('entry_id') or '') == target})
    # Only the judgment's own documents decide a verdict. Another filing without its OCR can hide
    # pages the reader buys first, so it only makes the price a floor, and the note says so.
    why_unseen = {r.get('_ocr_unreachable') for r in order + held
                  if str(r.get('entry_ref') or '') == target} - {None, False}
    if why_unseen:
        out['ocr_unreachable'] = True
        # The judgment's own OCR-only pages are unseen, so both of its counts are floors.
        out['price_is_floor'] = out['whole_case_is_floor'] = True
    else:
        at = [i for i, r in enumerate(order) if str(r.get('entry_ref') or '') == target]
        missing = [i for i, r in enumerate(order) if r.get('_ocr_unreachable')]
        if missing:
            out['whole_case_is_floor'] = True        # every unseen page is out of the whole-case count
        # Only a filing the reader walks BEFORE the judgment can add to the cost of reaching it; a
        # judgment the plan holds out of the order has no 'before'.
        if at and any(i < max(at) for i in missing):
            out['price_is_floor'] = True
            notes.append('free OCR missing for a filing read before the judgment: its pages are not '
                     'in the price, so pages to the judgment is a floor')
    if out.get('ocr_unreachable'):
        if 'pdf' in why_unseen:
            notes.append('a judgment document\'s stored PDF is not reachable here, so its '
                         'OCR-only amount pages are unknown: report from the pass machine; if this '
                         'is the pass machine, the PDF needs downloading again')
        if 'cache' in why_unseen:
            notes.append('the pass has not OCR\'d every page of a judgment document (it arrived '
                         'after the case was built, or a pass stopped mid-document): the next '
                         'pass more than 20h after this case\'s last build fills it in')
    if any(t for t, _ in need):
        last = max(i for i, (t, _) in enumerate(need) if t)
        out.update(state='needs_paid_read', pages_to_judgment=sum(n for _, n in need[:last + 1]),
                   pages_all=sum(n for _, n in need))
    elif out.get('ocr_unreachable'):
        # Without the free OCR, amount pages may be missing from every count below.
        out['state'] = 'report_on_pass_machine'
    elif held_unread:
        # Part of the judgment carries amount pages the reader defers: never verified, never
        # priced. The fix is the docket entry.
        out['state'] = 'judgment_held_by_docket_plan'
        notes.insert(0, held_why() + ': unread amount pages the reader defers')
    elif target_gaps:
        out['state'] = 'judgment_incomplete'
        notes.append('timeline gaps on the judgment: ' + ', '.join(target_gaps))
    elif (target_checks and all(c.get('ok') for c in target_checks)
          and not any(r in stuck_docs or stuck_pages.get(r) for r in target_refs)
          and _every_document_checked(target, target_checks, order + held, done)):
        # Every printed total on every document of the target reproduces to the cent, no amount
        # page of it is unread, and the timeline has no gap on it. One total agreeing while
        # another fails, or a judgment read with no total at all, is not this.
        out['state'] = 'verified'
    elif target_amount_rows:
        out['state'] = 'read_not_verified'
        checked = {c.get('source_ref') for c in target_checks}
        if any(not c.get('ok') for c in target_checks):
            notes.append('a printed total does not reproduce to the cent'
                         + (' (another one does)' if any(c.get('ok') for c in target_checks) else ''))
        unchecked = [r for r in target_refs if r not in checked and r not in stuck_docs
                     and not stuck_pages.get(r) and 'amount-vision-' + hashlib.sha256(
                         str(r).encode()).hexdigest() + '.json' in done]
        if unchecked:
            notes.append('a document of the judgment was read and shows no printed total to check')
        if any(r in stuck_docs or stuck_pages.get(r) for r in target_refs):
            notes.append('a page came back unreadable or a paid call failed; paying again '
                         'does not re-read it')
    else:
        doc = target_doc
        fetched = any(str(r.get('entry_ref') or '') == target for r in rows)
        if not fetched:
            out['state'] = 'judgment_not_fetched'
            notes.insert(0, ','.join(doc.get('gaps') or []) or 'not_downloaded')
        elif not doc.get('eligible_for_acquisition'):
            # On disk, but the docket plan holds the entry (undated, no image count), so the paid
            # reader defers it. The fix is the docket entry, not another download.
            out['state'] = 'judgment_held_by_docket_plan'
            notes.insert(0, held_why())
        else:
            # Fetched, but no page carries a printed dollar figure the free reads could see: a
            # paid read would have nothing selected. Named, never counted as verified.
            out['state'] = 'judgment_without_amount_page'
    if notes:
        out['detail'] = '; '.join(notes)
    return out


def _docket_newer(runner, case):
    import document_store as DS
    try:
        return ((Path(DS.pipeline_folder(runner.COUNTY, case)) / 'inventory.json').stat().st_mtime
                > Path(timeline_path(runner, case)).stat().st_mtime)
    except OSError:
        return False


def run_pass(runner, entries, today, collect, log, limit=None, save=None):
    import run_case_timeline as RCT
    done = errors = skipped = 0
    for entry in entries:
        if limit is not None and done + errors >= limit:     # --limit counts cases worked, not cases skipped
            break
        case = entry['case']
        last = (log.get('built') or {}).get(case) or {}
        if (built_recently(timeline_path(runner, case))
                and not _docket_newer(runner, case)
                and (last.get('collect') or not collect)
                and time.time() - float(last.get('at') or 0) < FRESH_HOURS * 3600):
            skipped += 1
            continue
        try:
            # The date each case is built, not the pass's start: a pass that crosses midnight
            # would otherwise treat that day's docket entries as future ones.
            RCT.timeline_case(case, today or date.today(), collect=collect)   # shared=None: $0
            done += 1
            log.setdefault('built', {})[case] = {'at': time.time(), 'collect': bool(collect)}
            (log.get('errors') or {}).pop(case, None)
            (log.get('error_at') or {}).pop(case, None)
        except Exception as exc:                              # one case never stops the pass
            errors += 1
            # timeline_case writes its file before it finishes: a fresh file after a failure is
            # not a finished case, so the next pass must not skip it.
            (log.get('built') or {}).pop(case, None)
            log.setdefault('error_at', {})[case] = time.time()
            log.setdefault('errors', {})[case] = '%s %s: %s' % (
                date.today().isoformat(), type(exc).__name__, str(exc)[:200])
        if save:
            save()
    return done, skipped, errors


def plan(runner, entries, today, log):
    import document_store as DS
    root = Path(DS.pipeline_folder(runner.COUNTY, 'x')).parent
    rate, sample = evidence_rate(root)
    rows = []
    for entry in entries:
        case = entry['case']
        try:
            tpath = timeline_path(runner, case)
            try:
                timeline = DS.pipeline_load(tpath)
            except ValueError:            # a truncated write: no timeline, not an unreadable case
                timeline = None
            row = assess(case, DS.pipeline_folder(runner.COUNTY, case), timeline, today.isoformat(),
                         tpath.stat().st_mtime if timeline is not None else None)
        except Exception as exc:                  # one unreadable case never costs the report
            row = {'case': case, 'state': 'unreadable_on_disk', 'pages_to_judgment': 0,
                   'pages_all': 0, 'detail': '%s: %s' % (type(exc).__name__, str(exc)[:160])}
        auction = _auction(entry)
        row['sale'] = auction.isoformat() if auction else None
        err_at = (log.get('error_at') or {}).get(case)
        rebuilt = err_at is not None and _mtime(timeline_path(runner, case)) > err_at
        if case in (log.get('errors') or {}) and not rebuilt:
            # Another tool (run_documents --backfill --timeline) may rebuild the case later.
            row['pass_error'] = log['errors'][case]
        rows.append(row)
    return rows, rate, sample


def render(rows, skipped_ids, rate, sample, today):
    per_page = rate if rate is not None else FALLBACK_USD_PER_PAGE
    basis = ('average of the %d billed pages recorded in the evidence now on disk ($%.4f/page; a '
             'later re-run served from the ledger cache records $0 and drops out of this sample)'
             % (sample, rate)
             if rate is not None else
             'no bought evidence on disk; the 09-22 measurement, $%.4f/page' % FALLBACK_USD_PER_PAGE)
    windows = [('next 7 days', 7), ('8-30 days', 30), ('31+ days', 10 ** 6)]
    def window(row):
        if not row['sale']:
            return 'no sale date'
        days = (date.fromisoformat(row['sale']) - today).days
        if days < 0:
            return 'sale passed'
        return next(name for name, limit in windows if days <= limit)
    names = [w[0] for w in windows] + ['sale passed', 'no sale date']
    states = ['verified', 'needs_paid_read', 'read_not_verified', 'judgment_not_fetched',
              'judgment_held_by_docket_plan', 'judgment_without_amount_page',
              'timeline_older_than_docket', 'timeline_missing', 'judgment_incomplete',
              'report_on_pass_machine',
              'no_operative_judgment', 'no_judgment_on_docket', 'docket_incomplete', 'no_docket',
              'unreadable_on_disk']
    lines = ['# Miami judgment amounts: court-copy state and read plan (%s)' % today.isoformat(), '',
             'Built from files on disk after a $0 docket pass. Case numbers only; no names. '
             '"verified" means the court copy\'s line items reproduce its printed total to the cent '
             '(judgment_money); it is not an award, an open balance or an equity input.', '',
             '| State | ' + ' | '.join(names) + ' | Total |', '|---' * (len(names) + 2) + '|']
    for state in states:
        counts = [sum(1 for r in rows if r['state'] == state and window(r) == n) for n in names]
        lines.append('| %s | %s | %d |' % (state, ' | '.join(map(str, counts)), sum(counts)))
    guessed = sum(1 for r in rows if r['state'] == 'verified'
                  and r.get('target_basis') not in (None, 'controlling'))
    lines += ['', 'Of the verified, %d are on a judgment the docket did not establish as controlling '
              '(the latest operative or unclear one); their Note says which.' % guessed]
    lines += ['', 'Tax-deed IDs with no court docket, left out (all sale dates): %d' % len(skipped_ids), '']
    lines += ['## Price to read the rest', '', 'Rate: ' + basis + '.', '',
              '| Sales | Cases | Pages to the judgment | Cost | Pages, whole case | Cost |',
              '|---|---|---|---|---|---|']
    for name in names:
        need = [r for r in rows if r['state'] == 'needs_paid_read' and window(r) == name]
        if not need:
            continue
        pj = sum(r['pages_to_judgment'] for r in need)
        pa = sum(r['pages_all'] for r in need)
        floors = sum(1 for r in need if r.get('price_is_floor'))
        # A floor case's hidden pages are not in its count: the totals then are a lower bound.
        whole = sum(1 for r in need if r.get('whole_case_is_floor'))
        lines.append('| %s | %d | %d%s | $%.2f%s | %d%s | $%.2f%s |'
                     % (name, len(need), pj, '+' if floors else '', pj * per_page,
                        ' or more (%d case%s missing free OCR)' % (floors, '' if floors == 1 else 's')
                        if floors else '', pa, '+' if whole else '', pa * per_page,
                        ' or more' if whole else ''))
    lines += ['', '"read_not_verified": the judgment was read as far as a paid run can take it, and '
              'its figures do not reproduce its printed total to the cent, or a document shows no '
              'printed total, or a page came back unreadable, or a paid call failed with its '
              'outcome unknown (the reader never repeats one); the note says which. Paying again '
              'does not change it: it needs a person or the paid clerk copy, not another read.']
    lines += ['', 'Prices assume the paid run goes through run_documents --backfill --timeline --vision, '
              'whose checkpoint ledger serves pages it already bought at $0. A run through another '
              'ledger re-bills them; the "whole case" column is the ceiling for that.']
    lines += ['', '"Pages to the judgment" is what the paid reader sends in its own order '
              '(controlling orders first, then judgments newest first) until it reaches the '
              'judgment. "Whole case" is every amount page it would read with an unlimited share.',
              '', '## Every case', '', '| Case | Sale | State | Pages to judgment | Note |',
              '|---|---|---|---|---|']
    for r in sorted(rows, key=lambda r: (r['sale'] or '9999', r['case'])):
        lines.append('| %s | %s | %s | %s | %s |' % (
            r['case'], r['sale'] or '', r['state'],
            r['pages_to_judgment'] if r['state'] == 'needs_paid_read' else '',
            '; '.join(x for x in (r.get('detail'), r.get('pass_error') and
                                  'last pass error ' + r['pass_error']) if x)
            .replace('|', '/')[:200]))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--collect', action='store_true',
                        help='refresh each case\'s full OCS docket first (free)')
    parser.add_argument('--days', type=int, default=None,
                        help='only cases whose sale is within this many days')
    parser.add_argument('--limit', type=int, default=None, help='cases this pass (resumable)')
    parser.add_argument('--report-only', action='store_true', help='skip the pass; plan from disk')
    parser.add_argument('--leads-file', help='default leads_final.json')
    args = parser.parse_args(argv)
    if args.report_only and (args.collect or args.limit is not None):
        parser.error('--report-only runs no pass: --collect and --limit do nothing with it')
    import case_review
    import run_documents as runner
    today = date.today()
    entries = load_entries(runner, args.leads_file)
    skipped_ids = [e['case'] for e in entries if not _is_case(e['case'])]
    entries = [e for e in entries if _is_case(e['case'])]
    if args.days is not None:
        entries = [e for e in entries if _auction(e) and 0 <= (_auction(e) - today).days <= args.days]
    out_dir = Path(case_review.output_path('judgment-plan/.keep')).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / 'pass-log.json'      # one log across days: a pass may cross midnight
    try:
        log = json.loads(log_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log = {}
    print('judgment_pass: %d docketed Miami cases, %d tax-deed IDs left out; $0: no API client, '
          'no token mints, no captcha' % (len(entries), len(skipped_ids)))
    if not args.report_only:
        if os.name != 'nt':
            # The pass OCRs through Windows' own reader; anywhere else every page would be cached
            # as ocr_failed and a later Windows pass would trust that and never read it.
            parser.error('the pass runs on Windows only; use --report-only elsewhere')
        import document_store as DS
        def save():                              # after every case: a killed pass keeps progress
            DS._atomic_write_text(str(log_path), json.dumps(log, indent=2))
        done, skipped, errors = run_pass(runner, entries, None, args.collect, log, args.limit, save)
        today = date.today()        # the report's as_of: after the pass, which may cross midnight
        print('  pass: %d built, %d built in the last 20h, %d errors (log %s)'
              % (done, skipped, errors, log_path))
    rows, rate, sample = plan(runner, entries, today, log)
    report = render(rows, skipped_ids, rate, sample, today)
    # A --days run is a subset: its own file, so it never overwrites the full plan.
    stem = 'JUDGMENT-PLAN-%s%s' % (today.isoformat(),
                                   '-next%dd' % args.days if args.days is not None else '')
    if args.days is not None:
        report = report.replace('\n\n', '\n\nSubset: only sales in the next %d days.\n\n' % args.days, 1)
    md = out_dir / (stem + '.md')
    md.write_text(report, encoding='utf-8')
    (out_dir / (stem + '.json')).write_text(
        json.dumps({'rows': rows, 'usd_per_page': rate, 'rate_sample_pages': sample}, indent=2),
        encoding='utf-8')
    print(report.split('\n## Every case')[0])
    print('  wrote %s' % md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
