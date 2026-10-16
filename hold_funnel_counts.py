#!/usr/bin/env python
"""Read-only Broward / Palm Beach hold funnel. Counts only.

Prints aggregate bucket counts. Does not print names, phones, emails, addresses, case
numbers, or the text of any why field. Does not write a cache and does not contact PACER,
CourtListener, a clerk site, or any other network.

Allowed inputs, read-only:
  DEALFLOW/bk_lead_cache.json
  DEALFLOW/bk_lookup_status.json
  DEALFLOW/clerk_bk_status.json
  the checkout's pacer_stay_cache.json (and, only inside pacer_confirmed, the new-filer
  hits file that already sits beside it)

Leads that are in neither cache are not counted. bk_overrides.json is not read, so an
override cannot move a lead into release_today. An unreadable cache forces release_today
to 0: a count must not claim a release the file might be contradicting.

    python hold_funnel_counts.py
    python hold_funnel_counts.py --dealflow DIR --pacer PATH

Run this on the laptop. Do not point it at a copy of homeowner data in the repo.
"""
import argparse
import json
import os
import re
import sys
import time


CL_BUCKETS = (
    'clear_unconfirmed', 'truncated', 'stale', 'not_run', 'errored', 'possible_match', 'exact_match',
)
PACER_BUCKETS = (
    'clear_fresh', 'clear_held', 'active', 'unverifiable', 'not_searched', 'errored',
)
WHY_BUCKETS = (
    'no_single_debtor_name', 'too_many_people_or_searches', 'out_of_state_namesake', 'pcl_error', 'other',
)


def _load(path):
    """(obj or None, readable). Missing is (None, False). Unreadable is (None, False) too;
    the caller treats a path that exists but will not parse as blocking a release."""
    if not path or not os.path.exists(path):
        return None, False
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    return data, True


def _exists_unreadable(path):
    return bool(path) and os.path.exists(path) and _load(path)[0] is None


def cl_bucket(ent, now, max_age):
    """One CourtListener cache entry -> a fixed bucket name. why text never leaves this function."""
    if not isinstance(ent, dict):
        return 'not_run'
    cases = ent.get('cases') if isinstance(ent.get('cases'), list) else []
    exact = ent.get('verdict') == 'active' or ent.get('a') is True or any(
        isinstance(c, dict) and c.get('open') and c.get('match') == 'exact' for c in cases)
    if exact:
        return 'exact_match'
    if ent.get('verdict') == 'possible' or any(
            isinstance(c, dict) and c.get('open') and c.get('match') == 'plausible' for c in cases):
        return 'possible_match'
    blob = (str(ent.get('err') or '') + '\n' + str(ent.get('why') or '')).lower()
    if 'truncat' in blob:
        return 'truncated'
    if ent.get('err'):
        return 'errored'
    verdict = str(ent.get('verdict') or '')
    if verdict in ('clear', 'clear_unconfirmed'):
        try:
            age = (float(now) - float(ent.get('t') or 0)) / 86400.0
        except (TypeError, ValueError):
            age = None
        if age is None or max_age <= 0 or age < -1 or age > max_age:
            return 'stale'
        return 'clear_unconfirmed'
    if not ent.get('searched') or verdict in ('', 'unavailable'):
        return 'not_run'
    return 'errored'


def unverifiable_bucket(why):
    """Map one why string onto a fixed label. The string itself is not returned."""
    w = str(why or '').lower()
    if 'pcl error' in w:
        return 'pcl_error'
    if 'page(s) of results' in w or 'common name' in w:
        return 'other'
    if 'more than' in w and ('owner' in w or 'search' in w or 'ways to read' in w):
        return 'too_many_people_or_searches'
    if 'same name' in w or 'cannot tell if it is this owner' in w:
        return 'out_of_state_namesake'
    if any(s in w for s in (
            'estate', 'trust', 'heir', 'no single debtor', 'no owner', 'one-word', 'one word',
            'cannot be split', 'only initials', 'government', 'et al', 'et ux', 'et vir',
            'cannot be searched', 'unsearchable')):
        return 'no_single_debtor_name'
    return 'other'


def pacer_bucket(ent, now):
    """(bucket, unverifiable label or '')."""
    if not isinstance(ent, dict):
        return 'not_searched', ''
    import stay_gate
    verdict = ent.get('verdict')
    if ent.get('err') and verdict not in ('clear', 'active', 'unverifiable'):
        return 'errored', ''
    if verdict == 'active' or ent.get('a') is True:
        return 'active', ''
    if verdict == 'clear':
        code, _why = stay_gate.pacer_verdict(ent, now)
        return ('clear_fresh' if code == stay_gate.CLEAR else 'clear_held'), ''
    if verdict == 'unverifiable':
        return 'unverifiable', unverifiable_bucket(ent.get('why'))
    return 'errored', ''


def _blank():
    return {
        'leads': 0,
        'cl': {b: 0 for b in CL_BUCKETS},
        'pacer': {b: 0 for b in PACER_BUCKETS},
        'why': {b: 0 for b in WHY_BUCKETS},
        'release_today': 0,
    }


def _releases(key, cl_ent, now):
    """Current gate: a Broward / Palm Beach lead releases only when entry_opinion says ok,
    or when there is no blocking CourtListener opinion and pacer_confirmed is true.
    An exact or possible CourtListener match stays held. PACER is not asked to overrule it."""
    import bk_lookup
    if isinstance(cl_ent, dict):
        op = bk_lookup.entry_opinion(key, cl_ent, {}, now)
    else:
        op = None
    if isinstance(op, dict) and op.get('ok'):
        ok = True
    elif isinstance(op, dict) and op.get('blocks') and not op.get('bk_need'):
        ok = False
    else:
        ok = bool(bk_lookup.pacer_confirmed(key, now))
    if not ok:
        return False
    if str(os.environ.get('DEALFLOW_CLERK_BK') or '').strip() == '1':
        try:
            import clerk_bk
            cop = clerk_bk.gate_opinion(key, now)
        except Exception:
            return False
        if not isinstance(cop, dict) or cop.get('blocks'):
            return False
    return True


def funnel(cl, pacer, now=None, release=True):
    """{county: counts}. cl and pacer are already-loaded dicts (or None)."""
    import bk_lookup
    import clerk_bk
    now = time.time() if now is None else float(now)
    max_age = bk_lookup.max_age_days()
    cl = cl if isinstance(cl, dict) else {}
    pacer = pacer if isinstance(pacer, dict) else {}
    by = {}
    keys = set(cl) | set(pacer)
    for key in keys:
        if str(key).startswith('_'):
            continue
        county = clerk_bk.county_of(key)
        if county not in ('broward', 'palmbeach'):
            continue
        row = by.setdefault(county, _blank())
        row['leads'] += 1
        cent = cl.get(key)
        row['cl'][cl_bucket(cent, now, max_age)] += 1
        pent = pacer.get(key)
        pb, why = pacer_bucket(pent, now)
        row['pacer'][pb] += 1
        if why:
            row['why'][why] += 1
        if release and _releases(key, cent if isinstance(cent, dict) else None, now):
            row['release_today'] += 1
    return by


def _status_line(label, data, readable):
    if not readable or not isinstance(data, dict):
        return '%s readable=0' % label

    def num(k):
        try:
            return int(data.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    return '%s readable=1 pull_ok=%d filings=%d leads_checked=%d holds=%d errors=%d truncated=%d' % (
        label, 1 if data.get('pull_ok') else 0, num('filings'), num('leads_checked'),
        num('holds'), num('errors'), num('truncated'))


def format_report(by, cl_status, cl_ok, clerk_status, clerk_ok, blocked):
    lines = ['hold_funnel counts_only',
             _status_line('bk_status', cl_status, cl_ok),
             _status_line('clerk_status', clerk_status, clerk_ok),
             'release_blocked=%d' % (1 if blocked else 0)]
    for county in ('broward', 'palmbeach'):
        row = by.get(county) or _blank()
        lines.append('county %s leads=%d' % (county, row['leads']))
        lines.append('  cl ' + ' '.join('%s=%d' % (b, row['cl'][b]) for b in CL_BUCKETS))
        lines.append('  pacer ' + ' '.join('%s=%d' % (b, row['pacer'][b]) for b in PACER_BUCKETS))
        lines.append('  pacer_unverified ' + ' '.join('%s=%d' % (b, row['why'][b]) for b in WHY_BUCKETS))
        lines.append('  release_today=%d' % row['release_today'])
    return '\n'.join(lines)


def run(dealflow, pacer_path, now=None):
    cl_path = os.path.join(dealflow, 'bk_lead_cache.json')
    cl, cl_ok = _load(cl_path)
    cl_status, cl_status_ok = _load(os.path.join(dealflow, 'bk_lookup_status.json'))
    clerk_status, clerk_ok = _load(os.path.join(dealflow, 'clerk_bk_status.json'))
    pacer, pacer_ok = _load(pacer_path)
    blocked = _exists_unreadable(cl_path) or _exists_unreadable(pacer_path)
    import bk_lookup
    prev = bk_lookup.PACER_ROOT
    bk_lookup.PACER_ROOT = os.path.dirname(os.path.abspath(pacer_path)) if pacer_path else prev
    try:
        by = funnel(cl if cl_ok else {}, pacer if pacer_ok else {}, now=now, release=not blocked)
    finally:
        bk_lookup.PACER_ROOT = prev
    if blocked:
        for row in by.values():
            row['release_today'] = 0
    return format_report(by, cl_status, cl_status_ok, clerk_status, clerk_ok, blocked)


_COUNT_LINE = re.compile(
    r'^(?:hold_funnel counts_only'
    r'|(?:bk_status|clerk_status) readable=\d+(?: pull_ok=\d+ filings=\d+ leads_checked=\d+ '
    r'holds=\d+ errors=\d+ truncated=\d+)?'
    r'|release_blocked=[01]'
    r'|county (?:broward|palmbeach) leads=\d+'
    r'|  cl(?: [a-z_]+=\d+)+'
    r'|  pacer(?: [a-z_]+=\d+)+'
    r'|  pacer_unverified(?: [a-z_]+=\d+)+'
    r'|  release_today=\d+)$'
)


def main(argv):
    p = argparse.ArgumentParser(description='Broward / Palm Beach hold funnel, counts only')
    p.add_argument('--dealflow', default='')
    p.add_argument('--pacer', default='')
    args = p.parse_args(argv)
    if not args.dealflow:
        import paths
        args.dealflow = paths.DEALFLOW_DIR
    if not args.pacer:
        args.pacer = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pacer_stay_cache.json')
    text = run(args.dealflow, args.pacer)
    for line in text.splitlines():
        if not _COUNT_LINE.match(line):
            raise SystemExit('refusing to print a line that is not a count')
    print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
