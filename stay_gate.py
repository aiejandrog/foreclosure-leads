#!/usr/bin/env python
"""stay_gate.py -- the §362 bankruptcy-stay verdict for ONE case, read from the durable cache.

WHY THIS FILE EXISTS (2026-09-26). send_server.py's POST /send -- the bridge every Morning Worker
and board email goes through -- checked the opt-out ledger's age, the opt-out ledger, the caps, the
bounce breaker and mail_guard, and never asked whether the case was under an automatic stay. It
trusted the board page, and the board page is whatever was baked the last time somebody rebuilt it:
a tab left open overnight, or a build from before sale_history.py saw the petition, still shows a
stayed case as workable. Three Miami cases were emailed under a live stay the week of 2026-09-21.
Every other sender already refuses those cases -- outreach_email (cache merge + _eligible),
outreach_mail (hard gate in the queue), morning_planner (_knock_eligible), cadence's pre-send
check -- so the one path a human clicks all morning was the one with no backstop.

THE SOURCE IS sale_history_cache.json, NOT THE LEAD ROW. The row is what went stale. The cache is
the durable record sale_history.py writes and every stay restore in the repo reads
(foreclosure_leads.restore_stays_from_cache, outreach_email._load_leads, healthcheck). Field
mapping is sale_history.py's, the same one outreach_email's merge uses verbatim:
    a   True = an automatic stay is active on the newest petition
    bd  the newest bankruptcy filing date
    sl  the date the last stay closed (dismissal / discharge / relief) -- '' while one is open
and the verdict is the same predicate outreach_email._eligible and outreach_mail apply to the rows
that merge stamps: active and not lifted. entry_stay_active() below is that predicate on a cache
entry; _sendstaytest.py pins it against outreach_email's own merge + _eligible on the same cache.

CASE MATCHING. Board rows carry the full Miami-Dade number (2025-007384-CA-01); other sources drop
or vary the suffix. Two numbers are the same case when their stem (2025-007384: four-digit year,
six-digit sequence) matches. Every cache entry on the stem counts, and ONE active entry blocks:
over-blocking a sibling suffix costs an email, under-blocking is a §362 contact.

A few sources put a LABEL in front of the number: one Miami-Dade lis pendens row reads
"CASE NO 2025-...", and clerk exports write "Case No.", "Case Number:", "CASE #". case_stem() strips
exactly those labels (CASE, CASE NO / NO. / NUMBER / NUM / NBR / #, a bare NO / NO. / #, with an optional
':' '#' or '.') and tolerates spaces around the hyphen, so the row resolves to the same key the
cache is indexed on. Nothing else is stripped: a county code, a Broward CACE- prefix or a Palm Beach
50- number still has no Miami-Dade stem. The six-digit sequence must END there (a seventh digit is a
different or mistyped number, not this case), so 2025-0073841 no longer truncates onto 2025-007384,
and only a suffix may follow it (-CA-01, a space, or CA01 run on); 2025-007384x is refused.
Anything that does not parse is refused as stay_case_unresolvable, as before.

FAIL CLOSED, ALL THE WAY DOWN. This is a send gate, not a build step (compare diligence_gate.py,
which fails OPEN because it runs inside builds). "I could not check" must never read as "clear":
  * no case number on the request                      -> refused  (stay_no_case)
  * a case number with no Miami-Dade stem              -> refused  (stay_case_unresolvable)
  * a valid stem with no cache entry, or an entry from
    before sale_history carried stay fields (v<4)      -> refused  (stay_unverified)
  * cache missing, unreadable, not a dict, or empty    -> refused  (stay_data_unavailable)
  * an active stay that has not been lifted            -> refused  (stay_active)
Only an entry that affirmatively says "no active stay" (or "stay lifted") clears.

sale_history.py covers civil Miami-Dade cases only. Broward, Palm Beach and tax-deed numbers
therefore land in stay_case_unresolvable: there is no durable stay read for them anywhere in the
repo, so the bridge cannot say they are clear. That is deliberate, and it is the policy decision
the PR asks Alejandro to confirm. MIAMI-DADE LIS PENDENS numbers are civil Miami-Dade numbers
(2025-012345-CA-01), so they resolve: each is stay_unverified until sale_history.py has read its
docket (it reads the lis pendens lane since #73), and then clears or blocks on what it found.

READ FRESH. The cache is re-read whenever its mtime or size changes (one os.stat per call, a parse
only after sale_history.py writes). sale_history.py writes it with a plain json.dump, not an atomic
replace, so a read can land mid-write; one short retry absorbs that, and a second failure refuses.
"""
import json
import os
import re
import threading
import time

CACHE_NAME = 'sale_history_cache.json'

# Miami-Dade civil numbers: YYYY-NNNNNN-CA-01 / -CC-05. The stem is YYYY-NNNNNN (11 characters).
STEM_LEN = 11
_STEM_RE = re.compile(r'^\d{4}-\d{6}$')
# Harmless labels in front of the number (see CASE MATCHING). Only these words; the remainder must
# then START with the stem, so a mis-strip can only fail closed.
_LABEL_RE = re.compile(r'^(?:CASE\s*(?:NUMBER|NUM\.?|NBR\.?|NO\.?|#)?|NO\b\.?|#)\s*[:#.]?\s*')
# the stem, spaces allowed around the hyphen; the sequence must END after six digits, followed by
# nothing, a hyphen / space (the suffix), or the court code itself (2025-007384CA01)
_NUM_RE = re.compile(r'^(\d{4})\s*-\s*(\d{6})(?=$|[\s-]|C[AC])')

CLEAR = 'clear'
STAY_ACTIVE = 'stay_active'
NO_CASE = 'stay_no_case'
UNRESOLVABLE = 'stay_case_unresolvable'
UNVERIFIED = 'stay_unverified'
UNAVAILABLE = 'stay_data_unavailable'

_LOCK = threading.Lock()
_MEMO = {}          # path -> (mtime_ns, size, index)  -- index = {stem: [(key, entry), ...]}


def case_stem(case):
    """'2025-007384-CA-01' -> '2025-007384'. '' when the number has no Miami-Dade stem.

    'CASE NO 2025-007384-CA-01', 'Case No.: 2025-007384', 'CASE # 2025 - 007384-CA-01' -> '2025-007384'.
    'CACE-24-001234', '2025-0073841', 'CASE', 'BW 2025-007384' -> '' (refused as unresolvable)."""
    s = ' '.join(str(case or '').split()).upper()
    s = _LABEL_RE.sub('', s, count=1)
    m = _NUM_RE.match(s)
    if not m:
        return ''
    stem = '%s-%s' % m.groups()
    return stem if _STEM_RE.match(stem) else ''


def entry_stay_active(ent):
    """The stay predicate on ONE cache entry: active and not lifted.

    Same rule outreach_email applies after its cache merge ('a' stamps saleBkAct/sale_bk_active,
    'sl' stamps saleLift, and _eligible refuses `(saleBkAct or sale_bk_active) and not saleLift`),
    and the rule outreach_mail's queue gate applies to the same fields."""
    return bool(ent.get('a')) and not ent.get('sl')


def _index(data):
    idx = {}
    for k, v in data.items():
        stem = case_stem(k)
        if stem:
            idx.setdefault(stem, []).append((str(k), v))
    return idx


def _load(path):
    """(index, err). err is a plain-English reason; index is None whenever err is set."""
    try:
        st = os.stat(path)
    except OSError as e:
        return None, 'sale_history_cache.json is missing or unreadable (%s)' % (e.strerror or e)
    with _LOCK:
        memo = _MEMO.get(path)
        if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
            return memo[2], ''
    data, err = None, ''
    for attempt in range(2):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            err = ''
            break
        except Exception as e:                       # mid-write, truncated, bad encoding, gone
            err = 'sale_history_cache.json could not be parsed (%s)' % str(e)[:120]
            data = None
            if attempt == 0:
                time.sleep(0.25)
    if err:
        with _LOCK:
            _MEMO.pop(path, None)                    # never keep serving an older parse
        return None, err
    if not isinstance(data, dict) or not data:
        with _LOCK:
            _MEMO.pop(path, None)
        return None, ('sale_history_cache.json holds no stay data (%s)'
                      % ('empty' if isinstance(data, dict) else type(data).__name__))
    idx = _index(data)
    with _LOCK:
        # Keyed on the stat taken BEFORE the read: if the file changed while we parsed it, the next
        # call sees a different mtime/size and re-reads, rather than trusting this parse forever.
        _MEMO[path] = (st.st_mtime_ns, st.st_size, idx)
    return idx, ''


def check(case, cache_path):
    """The stay verdict for one case. Never raises.

    Returns {'ok': bool, 'code': str, 'why': str, 'case': str, 'matched': [cache keys],
             'bd': newest filing date on an active entry, 'sl': lift date when cleared by a lift}.
    ok=True only for CLEAR."""
    raw = str(case or '').strip()
    out = {'ok': False, 'code': '', 'why': '', 'case': raw, 'matched': [], 'bd': '', 'sl': ''}
    try:
        if not raw:
            out.update(code=NO_CASE, why='no case number on the request, so its bankruptcy-stay '
                                         'status cannot be checked')
            return out
        stem = case_stem(raw)
        if not stem:
            out.update(code=UNRESOLVABLE,
                       why=('case %s has no Miami-Dade case stem; sale_history_cache.json is the only '
                            'durable stay source and covers Miami-Dade civil cases only' % raw[:40]))
            return out
        idx, err = _load(cache_path)
        if err:
            out.update(code=UNAVAILABLE, why=err)
            return out
        hits = idx.get(stem) or []
        out['matched'] = [k for k, _ in hits]
        if not hits:
            out.update(code=UNVERIFIED,
                       why=('case %s has no entry in sale_history_cache.json -- sale_history.py has '
                            'not read its docket yet' % raw[:40]))
            return out
        bad = [k for k, v in hits if not isinstance(v, dict) or 'a' not in v]
        if bad:
            out.update(code=UNVERIFIED,
                       why=('cache entry %s carries no stay fields (pre-v4 read) -- re-run '
                            'sale_history.py --case %s' % (bad[0], bad[0])))
            return out
        active = [(k, v) for k, v in hits if entry_stay_active(v)]
        if active:
            k, v = active[0]
            out.update(code=STAY_ACTIVE, bd=str(v.get('bd') or ''),
                       why=('ACTIVE §362 BANKRUPTCY STAY on %s (filed %s, not lifted) -- contacting '
                            'the debtor is a stay violation' % (k, v.get('bd') or 'date unknown')))
            return out
        lifts = sorted(str(v.get('sl')) for _, v in hits if v.get('sl'))
        out.update(ok=True, code=CLEAR, sl=(lifts[-1] if lifts else ''),
                   why=('stay lifted %s' % lifts[-1]) if lifts else 'no active stay on record')
        return out
    except Exception as e:                           # a gate that throws must not become a pass
        out.update(ok=False, code=UNAVAILABLE, why='stay check failed (%s)' % str(e)[:120])
        return out


def health(cache_path):
    """For GET /health: can the bridge read stay data right now, and how many active stays."""
    idx, err = _load(cache_path)
    if err:
        return {'ok': False, 'err': err, 'cases': 0, 'active': 0}
    n = sum(len(v) for v in idx.values())
    act = sum(1 for v in idx.values() for _, e in v if isinstance(e, dict) and entry_stay_active(e))
    return {'ok': True, 'err': '', 'cases': n, 'active': act}
