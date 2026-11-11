"""Read-only adapter from deep_equity/latest.json to the board's existing equity contract.

No research, network calls, cache writes, judgment fabrication, or contact policy changes.
Missing cache is optional. A present unreadable cache aborts baking rather than publishing
old facts. A rejected case demotes even a legacy priced chain.
"""
import copy
import datetime as dt
import json
import math
import os
import re

import paths as P

REQUIRED = ('appraiser', 'case', 'records', 'tax')
MAX_AGE_DAYS = 14


def _key(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('missing or nonnumeric amount')
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid amount')
    return float(value)


def _fresh(value, now):
    stamp = dt.datetime.fromisoformat(value)
    # The producer writes local naive timestamps; aware timestamps are also accepted.
    clock = now if stamp.tzinfo is None else now.astimezone(stamp.tzinfo)
    age = clock - stamp
    if not dt.timedelta(0) <= age <= dt.timedelta(days=MAX_AGE_DAYS):
        raise ValueError('stale or future evidence')


def _equity(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('equity is missing or nonfinite')
    return float(value)


def load(path=None):
    path = path or os.path.join(P.DEALFLOW_DIR, 'deep_equity', 'latest.json')
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError('deep-equity cache unreadable; board not rebuilt') from exc
    if not isinstance(data, dict):
        raise ValueError('deep-equity cache is not a case mapping')
    return data


def validate(row, result, now):
    if not isinstance(result, dict) or result.get('verdict') != 'VERIFIED':
        raise ValueError('latest evidence is not verified')
    case = _key(row.get('case'))
    if (not case or result.get('case_key') != case
            or _key(result.get('case_number')) != case):
        raise ValueError('case mismatch')
    parcel = _key(row.get('folio'))
    if len(parcel) != 12 or parcel != _key(result.get('parcel')):
        raise ValueError('parcel missing or mismatched')
    if str(row.get('county', '')).upper() != 'BROWARD' or row.get('st') == 'TD':
        raise ValueError('unsupported county or sale type')
    from equity_state import lender_foreclosure
    from deep_equity import HOA_RE
    if (not lender_foreclosure(row) or row.get('ftype') == 'HOA'
            or not result.get('case_plaintiff') or HOA_RE.search(result['case_plaintiff'])):
        raise ValueError('not an established lender foreclosure')
    if result.get('case_status', '').lower() not in ('active', 'open', 'pending'):
        raise ValueError('case is not active')
    if result.get('unknowns') != []:
        raise ValueError('debt components unresolved')
    _fresh(result.get('last_checked'), now)
    for name in REQUIRED:
        if result.get('source_status', {}).get(name) != 'OK':
            raise ValueError('required source not OK')
        _fresh(result.get('source_at', {}).get(name), now)
    first = _amount(result.get('debt_first'))
    if first <= 0 or not result.get('debt_first_inst') or not result.get('debt_first_src'):
        raise ValueError('first mortgage incomplete')
    seen = {result['debt_first_inst']}
    hud = []
    for key in ('debt_subordinate', 'debt_liens'):
        items = result.get(key)
        if not isinstance(items, list):
            raise ValueError('debt inventory missing')
        total = 0
        for item in items:
            if not isinstance(item.get('kind'), str) or not item['kind']:
                raise ValueError('instrument kind missing')
            inst = item.get('inst')
            if not inst or inst in seen:
                raise ValueError('missing or duplicate instrument')
            seen.add(inst)
            amount = _amount(item.get('amount'))
            if amount <= 0:
                raise ValueError('open instrument amount missing')
            total += amount
            if item.get('kind') == 'hud_partial_claim':
                if item.get('fha_partial_claim') is not True:
                    raise ValueError('HUD claim not flagged')
                hud.append(inst)
        if abs(total - _amount(result.get(key + '_total'))) > 0.011:
            raise ValueError('itemized debt does not reconcile')
    if sorted(hud) != sorted(result.get('fha_partial_claims', [])):
        raise ValueError('HUD inventory does not reconcile')
    debt = first + result['debt_subordinate_total'] + result['debt_liens_total']
    if abs(debt - _amount(result.get('debt_total'))) > 0.011:
        raise ValueError('total debt does not reconcile')
    low = _amount(result.get('value_low'))
    if low <= 0 or abs(low - debt - _equity(result.get('equity_low'))) > 0.011:
        raise ValueError('county equity does not reconcile')
    high = result.get('value_high')
    if high is not None:
        if result.get('source_status', {}).get('avm') != 'OK':
            raise ValueError('online estimate not verified')
        _fresh(result.get('source_at', {}).get('avm'), now)
        if abs(_amount(high) - debt - _equity(result.get('equity_high'))) > 0.011:
            raise ValueError('online equity does not reconcile')


def attach(rows, latest, now=None):
    """Apply after judgment interest and value joins, before diligence and serialization."""
    now = now or dt.datetime.now()
    for row in rows:
        case = _key(row.get('case'))
        if case not in latest:
            continue
        try:
            entry = latest[case]
            result = entry.get('verified')
            attempt = entry.get('last_attempt')
            validate(row, result, now)
            # A failed recheck cannot silently resurrect the preserved historical success.
            validate(row, attempt, now)
            if attempt != result:
                raise ValueError('latest attempt differs from saved verification')
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
            row.pop('deep_equity', None)
            row['eqstate'] = 'none'
            row['eqstate_why'] = 'UNVERIFIED: deep-equity evidence rejected (%s)' % exc
            continue
        row['eqstate'] = 'priced'
        row['eqstate_why'] = ('Verified itemized recorded/claimed debt. '
                              'Interest, escrow advances and fees are additional; not a payoff.')
        # Publish provenance and amounts only, never raw name-search responses or owner names.
        keys = ('last_checked', 'debt_first', 'debt_total', 'debt_note', 'debt_first_src',
                'debt_subordinate_total', 'debt_liens_total', 'equity_low', 'equity_high')
        row['deep_equity'] = {k: copy.deepcopy(result.get(k)) for k in keys}
        # Use the county basis verified with this inventory. Existing online/comps values
        # remain advisory; they cannot silently turn this conservative net into a higher one.
        row['value'] = result['value_low']
        row['arvconf'] = ''
        row['orconf'] = 'ok'
        row['orliens'] = [{'kind': 'mortgage', 'amt': result['debt_first'],
                           'src': result['debt_first_src'], 'st': 'OPEN'}] + [
            {'kind': i['kind'], 'amt': i['amount'], 'd': i.get('recorded', ''),
             'src': 'deep-equity official records', 'st': 'OPEN'}
            for i in result['debt_subordinate'] + result['debt_liens']]
        # Legacy board math adds the judgment and these buckets. Keep the first mortgage
        # out of the extra debt, except for any principal not covered by that judgment.
        owed = max(float(row.get(k) or 0) for k in ('judg', 'judgc', 'payoff'))
        extra = max(0, result['debt_first'] - owed) + result['debt_liens_total']
        row['orsurvsen'] = round(extra, 2)
        row['orjuniors'] = result['debt_subordinate_total']
        row['orsurv'] = row['orjunior'] = round(extra + row['orjuniors'], 2)
        row['orsurvfirst'] = row['orsurv']
        # All non-mortgage liens are already in extra; do not add them a second time.
        row['orhoa'] = row['orcode'] = row['orirs'] = 0
        row['eqnote'] = row['eqstate_why']
        import equity_state
        row['eqc'] = equity_state.card(row)
