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

FEDERAL BANKRUPTCY FROM PACER (pacer_stay.py, stacked on this gate). sale_history.py reads the
state docket, so it only sees a bankruptcy once somebody files a suggestion of bankruptcy there, and
it reads Miami-Dade only. pacer_stay.py searches the federal PACER Case Locator by owner name and
writes pacer_stay_cache.json beside sale_history_cache.json (same folder, so check() finds it
without send_server changing). Entries are keyed by pacer_key(case) -- the board's case number,
upper-cased, whitespace collapsed, a harmless label stripped -- and carry:
    verdict  'clear' | 'active' | 'unverifiable'   (a/bd/sl mirror sale_history: a True only if active)
    q        when PCL was queried (ISO, with offset); env 'prod' or 'qa'; src 'pacer_pcl'
    cases    the matched bankruptcy case numbers (court, number, chapter, dates) -- no names
How the gate uses it:
  * Miami-Dade stem: everything above is unchanged, and a PACER 'active' on the same stem ALSO
    refuses (stay_active) before a docket clear can pass. PACER cannot clear a Miami-Dade case on
    its own and a missing/unreadable PACER file changes nothing for Miami-Dade (it is additive).
  * No Miami-Dade stem (Broward CACE-, Palm Beach 50-, a Miami-Dade LP row carrying another
    county's number): PACER is the only durable source.
        no PACER file at all (lookup never ran / no credentials)   -> stay_case_unresolvable (as before)
        file unreadable, no entry, 'unverifiable', from QA,
        or a clear older than DEALFLOW_PACER_MAX_AGE_DAYS (14)     -> stay_unverified
        'active'                                                   -> stay_active
        'clear', from production, queried within the max age       -> clear
    A number with fewer than five digits ('CASE', 'OTHER', an LP- placeholder) is never looked up
    and stays stay_case_unresolvable.
A clear is a point-in-time fact about a name search: it goes stale, so it stops clearing after the
max age (pacer_stay.py re-checks clear leads every 12 days, near-sale ones every 3).

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
PACER_NAME = 'pacer_stay_cache.json'           # written by pacer_stay.py, same folder as CACHE_NAME
ENV_PACER_MAX_AGE = 'DEALFLOW_PACER_MAX_AGE_DAYS'
DEFAULT_PACER_MAX_AGE_DAYS = 14.0

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
_PMEMO = {}         # PACER file: path -> (mtime_ns, size, (by_key, by_stem))


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


def pacer_key(case):
    """The key pacer_stay.py writes and check() looks up for a case number that has no Miami-Dade stem.

    'CACE-24-012345' -> 'CACE-24-012345'; ' case no. cace - 24 - 012345 ' -> 'CACE-24-012345';
    '502024CA001234XXXAMB' unchanged. '' when fewer than five digits remain ('CASE', 'OTHER', 'LP-...')."""
    s = ' '.join(str(case or '').split()).upper()
    s = _LABEL_RE.sub('', s, count=1).strip()
    s = re.sub(r'\s*-\s*', '-', s)
    return s if len(re.findall(r'\d', s)) >= 5 else ''


def pacer_max_age_days():
    raw = os.environ.get(ENV_PACER_MAX_AGE, '').strip()
    try:
        v = float(raw) if raw else DEFAULT_PACER_MAX_AGE_DAYS
    except ValueError:
        return 0.0                                   # a bad setting clears nothing (fail closed)
    return v if v > 0 else 0.0


def _pacer_path(cache_path):
    return os.path.join(os.path.dirname(os.path.abspath(cache_path)), PACER_NAME)


def _load_pacer(path):
    """((by_key, by_stem), err, exists). A missing file is exists=False with no error."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None, '', False
    except OSError as e:
        return None, '%s unreadable (%s)' % (PACER_NAME, e.strerror or e), True
    with _LOCK:
        memo = _PMEMO.get(path)
        if memo and memo[0] == st.st_mtime_ns and memo[1] == st.st_size:
            return memo[2], '', True
    data, err = None, ''
    for attempt in range(2):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            err = ''
            break
        except Exception as e:
            err = '%s could not be parsed (%s)' % (PACER_NAME, str(e)[:120])
            data = None
            if attempt == 0:
                time.sleep(0.25)
    if not err and not isinstance(data, dict):
        err = '%s is not a JSON object' % PACER_NAME
    if err:
        with _LOCK:
            _PMEMO.pop(path, None)
        return None, err, True
    by_key, by_stem = {}, {}
    for k, v in data.items():
        if str(k).startswith('_') or not isinstance(v, dict):
            continue
        key = pacer_key(k)
        if not key:
            continue
        by_key[key] = v
        stem = case_stem(key)
        if stem:
            by_stem.setdefault(stem, []).append((key, v))
    idx = (by_key, by_stem)
    with _LOCK:
        _PMEMO[path] = (st.st_mtime_ns, st.st_size, idx)
    return idx, '', True


def _pacer_age_days(ent, now=None):
    """Days since the PCL query, or None when the entry carries no usable query time."""
    t = ent.get('t')
    try:
        t = float(t)
    except (TypeError, ValueError):
        return None
    if not (t > 0):
        return None
    return ((time.time() if now is None else now) - t) / 86400.0


def pacer_verdict(ent, now=None):
    """(code, why) for ONE pacer_stay_cache.json entry. Only a fresh production 'clear' clears."""
    if not isinstance(ent, dict):
        return UNVERIFIED, 'PACER entry is malformed'
    verdict = ent.get('verdict')
    if ent.get('env') != 'prod':
        return UNVERIFIED, 'PACER entry came from the %s environment (test data never clears)' % (
            ent.get('env') or 'unknown')
    if verdict == 'active' or ent.get('a') is True:
        cases = [c.get('no') for c in (ent.get('cases') or []) if isinstance(c, dict) and c.get('open')]
        return STAY_ACTIVE, ('ACTIVE federal bankruptcy found in PACER for the owner (%s, filed %s) -- '
                             'contacting the debtor is a stay violation'
                             % (', '.join(str(c) for c in cases[:3]) or 'case number not recorded',
                                ent.get('bd') or 'date unknown'))
    if verdict == 'unverifiable':
        return UNVERIFIED, 'PACER could not verify the owner: %s' % str(ent.get('why') or 'no reason recorded')[:160]
    if verdict != 'clear':
        return UNVERIFIED, 'PACER entry has no verdict'
    age = _pacer_age_days(ent, now)
    limit = pacer_max_age_days()
    if limit <= 0:
        return UNVERIFIED, '%s is not a positive number of days -- no PACER clear is accepted' % ENV_PACER_MAX_AGE
    if age is None:
        return UNVERIFIED, 'PACER entry has no query time'
    if age < -1 or age > limit:
        return UNVERIFIED, ('PACER clear is %.0f days old (older than %g) -- re-run pacer_stay.py'
                            % (age, limit))
    return CLEAR, 'no open federal bankruptcy for the owner in PACER (checked %s)' % str(ent.get('q') or '')[:10]


def _check_pacer(raw, out, pacer_path):
    """The verdict for a case number with no Miami-Dade stem: PACER is its only durable stay source."""
    key = pacer_key(raw)
    if not key:
        out.update(code=UNRESOLVABLE,
                   why=('case %s has no Miami-Dade case stem and is not a case number PACER results '
                        'can be keyed to' % raw[:40]))
        return out
    idx, err, exists = _load_pacer(pacer_path)
    if not exists:
        out.update(code=UNRESOLVABLE,
                   why=('case %s has no Miami-Dade case stem, and there is no PACER stay data '
                        '(%s missing -- pacer_stay.py has not run or has no credentials)'
                        % (raw[:40], PACER_NAME)))
        return out
    if err:
        out.update(code=UNVERIFIED, why=err)
        return out
    ent = idx[0].get(key)
    if ent is None:
        out.update(code=UNVERIFIED,
                   why='case %s has no PACER lookup yet -- pacer_stay.py has not searched its owner' % raw[:40])
        return out
    out['matched'] = [key]
    out['src'] = 'pacer_pcl'
    code, why = pacer_verdict(ent)
    out.update(code=code, why=why, ok=(code == CLEAR))
    if code == STAY_ACTIVE:
        out['bd'] = str(ent.get('bd') or '')
    return out


def _pacer_active_on_stem(stem, pacer_path):
    """(key, entry) of a production PACER 'active' on this Miami-Dade stem, else None. Additive only:
    a missing or unreadable PACER file returns None and leaves the docket verdict as it was."""
    idx, err, exists = _load_pacer(pacer_path)
    if not exists or err or not idx:
        return None
    for k, v in idx[1].get(stem) or []:
        if pacer_verdict(v)[0] == STAY_ACTIVE:
            return k, v
    return None


def check(case, cache_path, pacer_path=None):
    """The stay verdict for one case. Never raises.

    Returns {'ok': bool, 'code': str, 'why': str, 'case': str, 'matched': [cache keys],
             'bd': newest filing date on an active entry, 'sl': lift date when cleared by a lift}
    (+ 'src': 'pacer_pcl' when the verdict came from pacer_stay_cache.json).
    ok=True only for CLEAR. pacer_path defaults to pacer_stay_cache.json beside cache_path."""
    raw = str(case or '').strip()
    out = {'ok': False, 'code': '', 'why': '', 'case': raw, 'matched': [], 'bd': '', 'sl': ''}
    try:
        if not raw:
            out.update(code=NO_CASE, why='no case number on the request, so its bankruptcy-stay '
                                         'status cannot be checked')
            return out
        stem = case_stem(raw)
        if pacer_path is None:
            pacer_path = _pacer_path(cache_path)
        if not stem:
            return _check_pacer(raw, out, pacer_path)
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
        fed = _pacer_active_on_stem(stem, pacer_path)
        if fed:
            k, v = fed
            out['matched'] = out['matched'] + [k]
            out.update(code=STAY_ACTIVE, bd=str(v.get('bd') or ''), src='pacer_pcl',
                       why=pacer_verdict(v)[1] + ' (the state docket does not show it yet)')
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
    return {'ok': True, 'err': '', 'cases': n, 'active': act, 'pacer': pacer_health(cache_path)}


def pacer_health(cache_path):
    """PACER side of /health: is there a file, and how many entries clear / block right now."""
    pidx, perr, pexists = _load_pacer(_pacer_path(cache_path))
    if not pexists or perr:
        return {'ok': False, 'err': perr or ('%s missing' % PACER_NAME), 'cases': 0, 'active': 0, 'clear': 0}
    codes = [pacer_verdict(e)[0] for e in pidx[0].values()]
    return {'ok': True, 'err': '', 'cases': len(codes), 'active': codes.count(STAY_ACTIVE),
            'clear': codes.count(CLEAR)}
