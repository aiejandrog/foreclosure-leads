"""pipeline_alerts.py — counts-only alerts the cloud watchdog can see.

WHY. The freshness watchdog (.github/workflows/freshness-watchdog.yml) is the observer that
still runs when the laptop is asleep. It can only see what reached origin/main. health.json is
gitignored, and the private ledgers repo is not readable by this repo's Actions token, so a
signal that stays on the laptop never becomes the email. This file is the bridge: a small public
JSON of counts, percentages, balances, stage names and timestamps. No names, addresses, emails,
phone numbers, case numbers, or URLs.

The laptop writes pipeline_alerts.json and pushes that one file (commit message
"alerts: pipeline status", which the runner check does not count as a second machine). The
watchdog opens one GitHub issue per alert key (label dealflow-alert), comments on the same
issue while it stays true, closes it when the condition clears, and fails the workflow only
when an alert is new or worse — that failure is what makes GitHub email the owner.

FAIL-SOFT. publish and evening always exit 0. A monitoring hiccup must not fail the refresh,
the opt-out sync, or a send. Nothing here writes the opt-out ledger or decides a reply is STOP.

OFF UNTIL A MACHINE ON main RUNS IT. The watchdog treats a missing file as "not armed" and
does not close issues it cannot re-read.

GIT. publish writes the file first. It does no git at all while a fresh
refresh-running.flag exists (written within 6h, the Refresh task's execution
limit) or this machine's runner lease (dealflow-mine.json) is still inside its
expiry — that is the 07:15 sync overlapping a refresh that runs until 07:40–08:45.
A flag older than 6h is a killed run, not a live one: publishing ignores it and
the file carries a stale-refresh-flag warning.
Otherwise it fetches origin/main, fast-forwards when local main is strictly behind,
and commits only when local main equals origin/main and the index has no other
staged file. A rejected push is undone (soft reset to the recorded base, then
unstage this file only). Never force-push. Local main must not be left ahead of
origin, or the 05:30 `git pull --ff-only` refuses and the night builds yesterday.

ARMING (armed laptop, no admin). python pipeline_alerts.py install-hint writes
desktop-setup/tasks/DEALFLOW_Evening_Readiness.xml (gitignored) and prints:

    schtasks /Create /TN "DEALFLOW Evening Readiness" /XML "<that file>" /F

The XML is UTF-16 with a BOM (schtasks /Create /XML rejects UTF-8), RunLevel
LeastPrivilege (schtasks /RL LIMITED), DisallowStartIfOnBatteries false,
StopIfGoingOnBatteries false, StartWhenAvailable true, 21:00 local. The
battery reading is kernel32 GetSystemPowerStatus inside this process, not a child shell.

Healthchecks.io (hc_ping.py) is a separate dead-man's switch. It stays off unless
DEALFLOW_HEALTHCHECK_URL is set (or a gitignored healthcheck.url file). It is not required
for these alerts. Arm it with:  setx DEALFLOW_HEALTHCHECK_URL https://hc-ping.com/<uuid>

THRESHOLDS (env overrides; garbage values keep the default):
    TRACERFY_LOW_CREDITS          500   warn below this (same knob as skiptrace_health)
    lookup cost                   5     fail when the balance cannot pay one instant lookup
    CAPTCHA_LOW_USD               1.00  warn below this while one solve still fits
    CAPTCHA_SOLVE_USD             0.003 fail when the balance cannot pay one solve
    PAID_READS_WARN_FRACTION      0.80  warn at/above this share of the monthly cap; fail at 100%
    BOUNCE_DAY_CEILING            0.05  send_server: more than this of today's first touches
    BOUNCE_DAY_MIN_SAMPLE         10    send_server: and at least this many were sent
    PIPELINE_ALERT_OPTOUT_HOUR    8     local hour after which a missing 07:15 sync alerts
    PIPELINE_ALERT_SEND_HOUR      9     local hour after which a scheduled zero-send alerts
    READINESS_BATTERY_PCT         50    alert when charge is under this
    READINESS_PORT                8823  send server
    PIPELINE_ALERT_READINESS_HOURS 20   alert when the evening check itself is older than this
"""
import csv
import datetime as dt
import io
import json
import math
import os
import re
import socket
import struct
import subprocess
import sys
import time

import skiptrace_health

HERE = os.path.dirname(os.path.abspath(__file__))
REL = 'pipeline_alerts.json'
MSG = 'alerts: pipeline status'
LOG = os.path.join(HERE, 'pipeline-alerts.log')

# Strings allowed into the public file. A digit run of 10+ is a phone or a folio; a case
# number looks like 2024-023366-CA-01. Applied to text, never to JSON numbers (a timestamp
# integer is not a phone, and scanning the whole file would reject a normal epoch).
_UNSAFE = re.compile(
    r'@|https?://|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b|\b\d{4}-\d{3,6}-[A-Za-z]{2}-\d{2}\b|\b\d{10,}\b',
    re.I)
_LABEL = re.compile(r"[A-Za-z0-9 ._()%:+'/,&§·-]{1,80}\Z")
_STEPS = ('replies', 'optout_sync', 'ledger_sync')
_OK_TASK = {0, 267009, 267011}   # success, currently running, never run
_UNATTENDED = ('refresh', 'phones', 'replies')
# Refresh's own exit 7: a lane failed and the night still finished (RUNEXIT=7). Not a kill.
_REFRESH_DEGRADED = 7
# 267011 is SCHED_S_TASK_HAS_NOT_RUN. Fine for a task that has never been scheduled to
# matter yet; on Refresh it means the 05:30 did not start.
_REFRESH_NOT_RUN = 267011
# DEALFLOW_Refresh.xml ExecutionTimeLimit. Past this the scheduler has already killed the task.
REFRESH_FLAG_MAX_AGE_S = 6 * 3600

DEFAULTS = {
    'tracerfy_low': skiptrace_health.DEFAULT_LOW_CREDITS,
    'tracerfy_lookup': skiptrace_health.TRACERFY_INSTANT_CREDITS,
    'captcha_low': 1.0,
    'captcha_solve': 0.003,
    'paid_warn': 0.80,
    'optout_hour': 8,
    'send_hour': 9,
    'battery_pct': 50,
    'readiness_hours': 20,
    'port': 8823,
}


def _log(msg):
    print('[alerts] ' + msg, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(dt.datetime.now().astimezone().isoformat(timespec='seconds') + ' ' + msg + '\n')
    except OSError:
        pass


def _env_float(name, default, lo=0.0, hi=None):
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    if not math.isfinite(v) or v < lo or (hi is not None and v > hi):
        return default
    return v


def _env_int(name, default, lo, hi):
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if v < lo or v > hi:
        return default
    return v


def thresholds():
    """Live thresholds. TRACERFY_LOW_CREDITS stays the skiptrace_health knob."""
    th = dict(DEFAULTS)
    th['tracerfy_low'] = skiptrace_health.tracerfy_low_credit_threshold()
    th['tracerfy_lookup'] = skiptrace_health.TRACERFY_INSTANT_CREDITS
    th['captcha_low'] = _env_float('CAPTCHA_LOW_USD', DEFAULTS['captcha_low'], lo=0.0)
    th['captcha_solve'] = _env_float('CAPTCHA_SOLVE_USD', DEFAULTS['captcha_solve'], lo=0.0, hi=1.0)
    th['paid_warn'] = _env_float('PAID_READS_WARN_FRACTION', DEFAULTS['paid_warn'], lo=0.0, hi=1.0)
    th['optout_hour'] = _env_int('PIPELINE_ALERT_OPTOUT_HOUR', DEFAULTS['optout_hour'], 0, 23)
    th['send_hour'] = _env_int('PIPELINE_ALERT_SEND_HOUR', DEFAULTS['send_hour'], 0, 23)
    th['battery_pct'] = _env_int('READINESS_BATTERY_PCT', DEFAULTS['battery_pct'], 1, 100)
    th['readiness_hours'] = _env_int('PIPELINE_ALERT_READINESS_HOURS', DEFAULTS['readiness_hours'], 1, 168)
    th['port'] = _env_int('READINESS_PORT', DEFAULTS['port'], 1, 65535)
    return th


def now_local(now=None):
    if now is None:
        return dt.datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return now


def safe_label(value, limit=80):
    """A stage name, step name, or task name, or '' when it is not safe to publish."""
    text = ' '.join(str(value or '').replace('\\', ' ').split())
    if not text or len(text) > limit or _UNSAFE.search(text) or not _LABEL.match(text):
        return ''
    if ':' in text and '\\' in str(value or ''):
        return ''
    return text


def _alert(key, severity, text, at):
    text = ' '.join(str(text).split())
    if _UNSAFE.search(text) or not text:
        text = 'Alert %s is %s. Detail withheld because it was not public-safe.' % (key, severity)
    return {'key': key, 'severity': severity, 'text': text, 'at': at}


def _money(n):
    return '$%.2f' % float(n)


# ---- pure verdicts. Each returns an alert dict or None (the clear path). ----------------------

def tracerfy_alert(sig, th, at):
    if not sig or sig.get('credits') is None:
        return None
    credits = sig['credits']
    if isinstance(credits, bool) or not isinstance(credits, int):
        return None
    need = int(th['tracerfy_lookup'])
    low = int(th['tracerfy_low'])
    if credits < need:
        return _alert('tracerfy-credits', 'fail',
                      'Tracerfy balance %d credits; one lookup costs %d. Cannot pay for a lookup.'
                      % (credits, need), at)
    if credits < low:
        return _alert('tracerfy-credits', 'warn',
                      'Tracerfy balance %d credits, warn under %d.' % (credits, low), at)
    return None


def captcha_alert(sig, th, at):
    if not sig or sig.get('usd') is None:
        return None
    try:
        usd = float(sig['usd'])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(usd) or usd < 0:
        return None
    solve = float(th['captcha_solve'])
    low = float(th['captcha_low'])
    if usd < solve:
        return _alert('captcha-balance', 'fail',
                      '2Captcha balance %s; one solve costs %s. Cannot pay for a solve.'
                      % (_money(usd), _money(solve)), at)
    if usd < low:
        return _alert('captcha-balance', 'warn',
                      '2Captcha balance %s, warn under %s.' % (_money(usd), _money(low)), at)
    return None


def paid_alert(sig, th, at):
    if not sig or not sig.get('readable', False):
        return None
    try:
        spent = float(sig.get('spent'))
        cap = float(sig.get('cap'))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(spent) or not math.isfinite(cap):
        return None
    if cap <= 0:
        return _alert('paid-reads-cap', 'fail',
                      'Paid-reads monthly cap is %s, so paid reads are off.' % _money(cap), at)
    ratio = spent / cap
    if ratio >= 1:
        return _alert('paid-reads-cap', 'fail',
                      'Paid reads %s of %s (%.0f%%) this month. The monthly cap is spent.'
                      % (_money(spent), _money(cap), ratio * 100), at)
    if ratio >= float(th['paid_warn']):
        return _alert('paid-reads-cap', 'warn',
                      'Paid reads %s of %s (%.0f%%) this month, warn at %.0f%%.'
                      % (_money(spent), _money(cap), ratio * 100, float(th['paid_warn']) * 100), at)
    return None


def bounce_alert(sig, at):
    if not sig or not sig.get('readable', False):
        return None
    parts = []
    if sig.get('day_blocked'):
        when = str(sig.get('day_date') or '').strip() or 'a recent send day'
        parts.append('First-touch bounce %.1f%% (%d of %d sent on %s), over the %.0f%% daily ceiling (minimum sample %d).'
                     % (float(sig.get('day_rate') or 0) * 100,
                        int(sig.get('day_dead') or 0), int(sig.get('day_sent') or 0), when,
                        float(sig.get('day_ceiling') or 0) * 100, int(sig.get('day_min') or 0)))
    if sig.get('trailing_blocked'):
        parts.append('Trailing %d-day lower bound %.1f%% (raw %.1f%%, %d of %d). Ceiling %.0f%%.'
                     % (int(sig.get('window') or 7), float(sig.get('lb') or 0) * 100,
                        float(sig.get('rate') or 0) * 100, int(sig.get('dead') or 0),
                        int(sig.get('mailed') or 0), float(sig.get('ceiling') or 0) * 100))
    if not parts:
        return None
    parts.append('First-touch sends are paused.')
    return _alert('bounce-rate', 'fail', ' '.join(parts), at)


def _past(sig, now, th, key, hour_key):
    if key in sig:
        return bool(sig[key])
    return now.hour >= int(th[hour_key])


def optout_alert(sig, now, th, at):
    """failed = today's run finished badly or is stuck. missed = nothing recorded for today
    after the judge hour. in-progress and too-early are not alerts."""
    if not isinstance(sig, dict):
        return None
    today = now.date().isoformat()
    past = _past(sig, now, th, 'past_cutoff', 'optout_hour')
    if not sig.get('readable', False):
        if not past:
            return None
        return _alert('optout-sync', 'fail',
                      '07:15 opt-out sync has no readable status on %s.' % today, at)
    date = str(sig.get('date') or '')
    state = str(sig.get('state') or '')
    if date == today and state == 'finished' and sig.get('run_ok') is True:
        return None
    if date == today and state == 'running':
        started = sig.get('started_at')
        try:
            age = now.timestamp() - float(started)
        except (TypeError, ValueError):
            age = None
        if age is not None and age < 3600:
            return None
        return _alert('optout-sync', 'fail',
                      '07:15 opt-out sync is still running on %s.' % today, at)
    if date == today and sig.get('run_ok') is False:
        steps = [s for s in (safe_label(x) for x in (sig.get('failed_steps') or [])) if s in _STEPS]
        which = ', '.join(steps) if steps else 'a step'
        return _alert('optout-sync', 'fail',
                      '07:15 opt-out sync failed on %s (steps: %s).' % (today, which), at)
    if date != today:
        if not past:
            return None
        last = date if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) else 'never'
        return _alert('optout-sync', 'fail',
                      '07:15 opt-out sync did not run on %s (last recorded %s).' % (today, last), at)
    return None


def morning_kind(sig, now, th):
    """Why today's first-touch count is what it is.

    nothing-eligible and nothing-sent are different: a scheduled day with an empty pool is
    not a failure. unknown means the pool could not be counted, so it is not treated as empty.
    """
    if not isinstance(sig, dict) or not sig.get('readable', False):
        return 'unknown'
    if str(sig.get('date') or '') != now.date().isoformat():
        return 'wrong-day'
    if not _past(sig, now, th, 'judged', 'send_hour'):
        return 'too-early'
    if not sig.get('scheduled'):
        return 'not-scheduled'
    if sig.get('eligible') is None:
        return 'unknown'
    try:
        eligible = int(sig['eligible'])
        sent = int(sig.get('sent') or 0)
    except (TypeError, ValueError):
        return 'unknown'
    if sent > 0:
        return 'sent'
    if eligible <= 0:
        return 'nothing-eligible'
    return 'nothing-sent'


def morning_alert(sig, now, th, at):
    if morning_kind(sig, now, th) != 'nothing-sent':
        return None
    caps = sig.get('caps') or []
    try:
        cap_txt = ', '.join(str(int(c)) for c in caps) if caps else str(int(sig.get('cap') or 0))
        naddr = int(sig.get('addresses') or 0)
        eligible = int(sig['eligible'])
    except (TypeError, ValueError):
        return None
    return _alert('morning-sends', 'fail',
                  'Morning Worker was scheduled (%d addresses, per-address cap %s) and %d leads were eligible, but 0 first-touch emails were sent.'
                  % (naddr, cap_txt, eligible), at)


def readiness_alert(sig, now, th, at):
    if not isinstance(sig, dict) or not sig.get('present_check', True) and not sig.get('at'):
        return None
    if not sig.get('at'):
        return None
    when = _parse_at(sig.get('at'))
    if when is None:
        return _alert('laptop-readiness', 'fail', 'Evening readiness check did not record a time.', at)
    age_h = (now - when).total_seconds() / 3600.0
    if age_h > float(th['readiness_hours']):
        return _alert('laptop-readiness', 'fail',
                      'Evening readiness check last ran %.0fh ago.' % age_h, at)
    problems = []
    if sig.get('power_ok') is False:
        problems.append('battery status could not be read')
    elif sig.get('battery_present') is False:
        pass
    else:
        if sig.get('on_ac') is False:
            problems.append('on battery')
        pct = sig.get('pct')
        if isinstance(pct, bool):
            pct = None
        if isinstance(pct, int) and pct < int(th['battery_pct']):
            problems.append('battery %d%% (under %d%%)' % (pct, int(th['battery_pct'])))
        elif sig.get('battery_present') is not False and sig.get('on_ac') is None and pct is None:
            problems.append('battery status could not be read')
    if sig.get('port_open') is False:
        problems.append('send server port %d is not listening' % int(th['port']))
    if sig.get('tasks_ok') is False:
        problems.append('DEALFLOW scheduled tasks could not be listed')
    else:
        problems.extend(_task_bits(sig))
    degraded = [] if sig.get('tasks_ok') is False else _degraded_bits(sig)
    if not problems and not degraded:
        return None
    text = 'Laptop readiness: ' + '; '.join(problems + degraded) + '.'
    # A finished night with one bad lane is a warning. A kill, a miss, or any other
    # readiness problem stays a failure, and the degraded code is still named in it.
    if problems:
        return _alert('laptop-readiness', 'fail', text, at)
    return _alert('laptop-readiness', 'warn', text, at)


def _degraded_bits(sig):
    try:
        n = int(sig.get('degraded') or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return []
    names = [safe_label(x) for x in (sig.get('degraded_names') or [])]
    names = [x for x in names if x]
    codes = []
    for raw in sig.get('degraded_codes') or []:
        try:
            code = int(raw)
        except (TypeError, ValueError):
            continue
        if code not in codes:
            codes.append(code)
    code_txt = ', '.join(str(c) for c in codes)
    if names and code_txt:
        return ['%d DEALFLOW task(s) degraded (%s, last result %s)' % (n, ', '.join(names), code_txt)]
    if names:
        return ['%d DEALFLOW task(s) degraded (%s)' % (n, ', '.join(names))]
    if code_txt:
        return ['%d DEALFLOW task(s) degraded (last result %s)' % (n, code_txt)]
    return ['%d DEALFLOW task(s) degraded' % n]


def _task_bits(sig):
    out = []
    for nkey, label in (('disabled', 'disabled'), ('failed', 'last result failed'),
                        ('interactive', 'logon Interactive only')):
        try:
            n = int(sig.get(nkey) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            continue
        names = [safe_label(x) for x in (sig.get(nkey + '_names') or [])]
        names = [x for x in names if x]
        if names:
            out.append('%d DEALFLOW task(s) %s (%s)' % (n, label, ', '.join(names)))
        else:
            out.append('%d DEALFLOW task(s) %s' % (n, label))
    return out


def health_alert(sig, at):
    if not isinstance(sig, dict) or not sig.get('fresh'):
        return None
    raw = [str(x) for x in (sig.get('names') or []) if str(x).strip()]
    if not raw:
        return None
    kept = []
    for name in raw:
        label = safe_label(name)
        if label:
            kept.append(label)
    if not kept:
        return _alert('healthcheck-fail', 'fail',
                      '%d healthcheck FAIL stage(s); names withheld.' % len(raw), at)
    return _alert('healthcheck-fail', 'fail',
                  'healthcheck FAIL stages: %s.' % ', '.join(kept), at)


def stale_flag_alert(sig, at):
    """A refresh-running.flag older than the task limit. Publishing ignored it."""
    if not isinstance(sig, dict) or not sig.get('stale'):
        return None
    try:
        age_h = float(sig.get('age_h'))
    except (TypeError, ValueError):
        age_h = None
    if age_h is None or not math.isfinite(age_h):
        text = ('refresh-running.flag is past the 6h refresh limit, so it was ignored '
                'and this file was published.')
    else:
        text = ('refresh-running.flag is %.1fh old, past the 6h refresh limit, so it was ignored '
                'and this file was published.' % age_h)
    return _alert('stale-refresh-flag', 'warn', text, at)


def alerts_from(signals, now, th=None):
    """Every current alert. A key that is absent has cleared."""
    now = now_local(now)
    th = th or thresholds()
    at = now.isoformat(timespec='seconds')
    signals = signals or {}
    found = [
        tracerfy_alert(signals.get('tracerfy'), th, at),
        captcha_alert(signals.get('captcha'), th, at),
        paid_alert(signals.get('paid_reads'), th, at),
        bounce_alert(signals.get('bounce'), at),
        optout_alert(signals.get('optout_sync'), now, th, at),
        morning_alert(signals.get('morning_sends'), now, th, at),
        readiness_alert(signals.get('readiness'), now, th, at),
        health_alert(signals.get('health_fails'), at),
        stale_flag_alert(signals.get('refresh_flag'), at),
    ]
    return sorted((a for a in found if a), key=lambda a: a['key'])


def _parse_at(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def bounce_signal(health):
    """The public slice of send_server._bounce_health(). Numbers only."""
    if not isinstance(health, dict) or 'blocked' not in health:
        return {'readable': False}
    try:
        lb = float(health.get('lb') or 0)
        ceiling = float(health.get('ceiling') if health.get('ceiling') is not None else 0.10)
    except (TypeError, ValueError):
        return {'readable': False}
    return {
        'readable': True,
        'day_date': str(health.get('day_date') or ''),
        'day_sent': int(health.get('day_sent') or 0),
        'day_dead': int(health.get('day_dead') or 0),
        'day_rate': float(health.get('day_rate') or 0),
        'day_ceiling': float(health.get('day_ceiling') if health.get('day_ceiling') is not None else 0.05),
        'day_min': int(health.get('day_min') or 0),
        'day_blocked': bool(health.get('day_blocked')),
        'trailing_blocked': lb > ceiling,
        'lb': lb,
        'rate': float(health.get('rate') or 0),
        'dead': int(health.get('dead') or 0),
        'mailed': int(health.get('mailed') or 0),
        'window': int(health.get('window') or 7),
        'ceiling': ceiling,
    }


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def public_ok(obj):
    return not any(_UNSAFE.search(s) for s in _strings(obj))


def scrub(obj):
    """Drop strings that are not safe to publish. Numbers, bools and timestamps stay."""
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    if isinstance(obj, str):
        if _UNSAFE.search(obj):
            return None
        return obj
    if isinstance(obj, list):
        return [item for item in (scrub(v) for v in obj) if item is not None]
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if not isinstance(key, str) or _UNSAFE.search(key):
                continue
            kept = scrub(value)
            if kept is not None:
                out[key] = kept
        return out
    return None


def build_doc(now, signals, alerts):
    now = now_local(now)
    doc = {
        'version': 1,
        'published_at': now.isoformat(timespec='seconds'),
        'alerts': alerts,
        'signals': scrub(signals or {}),
    }
    if public_ok(doc):
        return doc
    return {
        'version': 1,
        'published_at': doc['published_at'],
        'alerts': [_alert('alerts-redacted', 'fail',
                          'An alert was withheld because its text was not public-safe.',
                          doc['published_at'])],
        'signals': {},
    }


# ---- laptop readings (fixtures parse the same bytes the live commands print) ------------------

def _u8(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value) & 0xFF
    except (TypeError, ValueError):
        return None


def parse_power_status(ac, flag, percent):
    """SYSTEM_POWER_STATUS fields from kernel32 GetSystemPowerStatus.

    ACLineStatus 0 offline, 1 online, 255 unknown. BatteryFlag bit 128 means no
    battery (a desktop). BatteryLifePercent is 0-100, or 255 when unknown.
    """
    ac, flag, percent = _u8(ac), _u8(flag), _u8(percent)
    if ac is None and flag is None and percent is None:
        return {'power_ok': False}
    # 128 is "no system battery". 255 is "unknown", and it happens to have that bit set.
    if flag == 128 or (flag not in (None, 255) and (flag & 128)):
        return {'power_ok': True, 'battery_present': False, 'on_ac': None, 'pct': None}
    if ac == 1:
        on_ac = True
    elif ac == 0:
        on_ac = False
    else:
        on_ac = None
    pct = percent if percent is not None and percent <= 100 else None
    return {'power_ok': True, 'battery_present': True, 'on_ac': on_ac, 'pct': pct}


def parse_power_bytes(raw):
    """The 12-byte SYSTEM_POWER_STATUS layout (4 unsigned bytes, then 2 DWORDs)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 12:
        return {'power_ok': False}
    ac, flag, percent, _sys, _life, _full = struct.unpack('<BBBBII', bytes(raw[:12]))
    return parse_power_status(ac, flag, percent)


def read_system_power():
    """Battery via kernel32, in this process. No child shell. Non-Windows is a failed probe."""
    if os.name != 'nt':
        return {'power_ok': False}
    try:
        import ctypes
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ('ACLineStatus', ctypes.c_ubyte),
                ('BatteryFlag', ctypes.c_ubyte),
                ('BatteryLifePercent', ctypes.c_ubyte),
                ('SystemStatusFlag', ctypes.c_ubyte),
                ('BatteryLifeTime', ctypes.c_ulong),
                ('BatteryFullLifeTime', ctypes.c_ulong),
            ]
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return {'power_ok': False}
        return parse_power_status(status.ACLineStatus, status.BatteryFlag, status.BatteryLifePercent)
    except Exception:
        return {'power_ok': False}


def _result_code(raw):
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def _failed_result(raw):
    text = str(raw or '').strip()
    if not text:
        return False
    code = _result_code(text)
    if code is None:
        return True
    return code not in _OK_TASK


def _refresh_task(label):
    return 'refresh' in str(label or '').lower()


def _refresh_degraded(label, raw):
    return _refresh_task(label) and _result_code(raw) == _REFRESH_DEGRADED


def _refresh_missed(label, raw):
    """Refresh only. 267011 / 'has not run' is a missed 05:30, not a quiet never-run."""
    if not _refresh_task(label):
        return False
    if 'has not run' in str(raw or '').strip().lower():
        return True
    return _result_code(raw) == _REFRESH_NOT_RUN


def _interactive_only(mode):
    text = ' '.join(str(mode or '').lower().split())
    return text in ('interactive only', 'interactive')


def _task_label(raw):
    name = str(raw or '').strip()
    if not name or re.search(r'[A-Za-z]:\\|/', name):
        return ''
    name = name.lstrip('\\').strip()
    if 'ealflow' not in name.lower():
        return ''
    return safe_label(name)


def _no_tasks():
    return {'tasks_ok': False, 'disabled': 0, 'failed': 0, 'interactive': 0, 'degraded': 0,
            'disabled_names': [], 'failed_names': [], 'interactive_names': [],
            'degraded_names': [], 'degraded_codes': []}


def parse_tasks(csv_text):
    """schtasks /Query /FO CSV /V. Disabled, failed last result, or Interactive-only logon
    on the unattended refresh / phones / replies tasks. Refresh rc=7 is degraded, not failed."""
    if not csv_text:
        return _no_tasks()
    rows = list(csv.reader(io.StringIO(csv_text)))
    header = None
    start = 0
    for i, row in enumerate(rows):
        if 'TaskName' in row and 'Status' in row:
            header = {name: n for n, name in enumerate(row)}
            start = i + 1
            break
    if not header or 'Last Result' not in header:
        return _no_tasks()
    disabled, failed, interactive, degraded = [], [], [], []
    for row in rows[start:]:
        if len(row) <= header['TaskName']:
            continue
        label = _task_label(row[header['TaskName']])
        if not label:
            continue
        status = row[header['Status']] if len(row) > header['Status'] else ''
        result = row[header['Last Result']] if len(row) > header['Last Result'] else ''
        mode = row[header['Logon Mode']] if 'Logon Mode' in header and len(row) > header['Logon Mode'] else ''
        if status.strip().lower() == 'disabled':
            disabled.append(label)
        if _refresh_degraded(label, result):
            degraded.append((label, _REFRESH_DEGRADED))
        elif _refresh_missed(label, result) or _failed_result(result):
            failed.append(label)
        low = label.lower()
        if any(k in low for k in _UNATTENDED) and _interactive_only(mode):
            interactive.append(label)
    return {
        'tasks_ok': True,
        'disabled': len(disabled), 'failed': len(failed), 'interactive': len(interactive),
        'degraded': len(degraded),
        'disabled_names': disabled, 'failed_names': failed, 'interactive_names': interactive,
        'degraded_names': [name for name, _code in degraded],
        'degraded_codes': [code for _name, code in degraded],
    }


def readiness_from(power, port_open, tasks, now):
    now = now_local(now)
    out = {
        'at': now.isoformat(timespec='seconds'),
        'present_check': True,
        'power_ok': bool(power.get('power_ok')) if isinstance(power, dict) else False,
        'battery_present': bool(power.get('battery_present')) if isinstance(power, dict) else False,
        'on_ac': power.get('on_ac') if isinstance(power, dict) else None,
        'pct': power.get('pct') if isinstance(power, dict) else None,
        'port_open': bool(port_open),
        'tasks_ok': bool(tasks.get('tasks_ok')) if isinstance(tasks, dict) else False,
        'disabled': int((tasks or {}).get('disabled') or 0) if isinstance(tasks, dict) else 0,
        'failed': int((tasks or {}).get('failed') or 0) if isinstance(tasks, dict) else 0,
        'interactive': int((tasks or {}).get('interactive') or 0) if isinstance(tasks, dict) else 0,
        'degraded': int((tasks or {}).get('degraded') or 0) if isinstance(tasks, dict) else 0,
        'disabled_names': list((tasks or {}).get('disabled_names') or []) if isinstance(tasks, dict) else [],
        'failed_names': list((tasks or {}).get('failed_names') or []) if isinstance(tasks, dict) else [],
        'interactive_names': list((tasks or {}).get('interactive_names') or []) if isinstance(tasks, dict) else [],
        'degraded_names': list((tasks or {}).get('degraded_names') or []) if isinstance(tasks, dict) else [],
        'degraded_codes': list((tasks or {}).get('degraded_codes') or []) if isinstance(tasks, dict) else [],
    }
    return out


def port_open(port, host='127.0.0.1', timeout=2):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _run_capture(args, timeout):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 and not (p.stdout or '').strip():
        return None
    return p.stdout or ''


def measure_readiness(now, port):
    tasks = _run_capture(['schtasks', '/Query', '/FO', 'CSV', '/V'], 60)
    return readiness_from(read_system_power(), port_open(port), parse_tasks(tasks), now)


# ---- live collect. Every reader is fail-soft and returns counts only. -------------------------

def _try(fn, fallback):
    try:
        return fn()
    except Exception:
        return fallback


def read_tracerfy():
    credits = skiptrace_health.probe_tracerfy_balance()
    if credits is None:
        return {'credits': None}
    return {'credits': credits}


def read_captcha():
    import captcha_solver
    if not captcha_solver.has_key():
        return {'usd': None}
    raw = captcha_solver.balance()
    try:
        return {'usd': float(raw)}
    except (TypeError, ValueError):
        return {'usd': None}


def read_paid():
    import paid_reads
    st = paid_reads.status()
    why = str(st.get('why') or '')
    if 'unreadable' in why or 'malformed' in why:
        return {'readable': False}
    return {'readable': True, 'spent': st.get('spent'), 'cap': st.get('cap'), 'month': str(st.get('month') or '')}


def read_bounce():
    import send_server
    return bounce_signal(send_server._bounce_health())


def read_optout(now, th):
    path = os.path.join(HERE, 'sync_status.json')
    try:
        with open(path, encoding='utf-8') as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {'readable': False, 'past_cutoff': now.hour >= th['optout_hour']}
    if not isinstance(st, dict):
        return {'readable': False, 'past_cutoff': now.hour >= th['optout_hour']}
    failed = []
    for step in st.get('steps') or []:
        if isinstance(step, dict) and not step.get('ok'):
            name = str(step.get('name') or '')
            if name in _STEPS:
                failed.append(name)
    return {
        'readable': True,
        'date': str(st.get('date') or ''),
        'state': str(st.get('state') or ''),
        'run_ok': st.get('ok') is True,
        'started_at': st.get('started_at') if isinstance(st.get('started_at'), (int, float)) else None,
        'failed_steps': failed,
        'past_cutoff': now.hour >= th['optout_hour'],
    }


def read_morning(now, th):
    import send_server as S
    cfg = S._load_senders()
    addrs = S._first_touch_senders(cfg)
    today = now.date()
    caps = []
    for addr in addrs:
        try:
            caps.append(int(S._first_touch_cap(cfg, addr, today)))
        except Exception:
            caps.append(0)
    day = today.isoformat()
    sent = 0
    for entry in S._load_ledger():
        if (entry.get('d') == day and entry.get('touch') == 'first' and entry.get('message_id')
                and not entry.get('test_mode') and not entry.get('error')):
            sent += 1
    eligible = None
    try:
        queue = S._first_touch_queue_health()
        if queue.get('ok') and queue.get('leads_sendable') is not None:
            eligible = int(queue['leads_sendable'])
    except Exception:
        eligible = None
    return {
        'readable': True,
        'date': day,
        'scheduled': any(c > 0 for c in caps),
        'sent': sent,
        'eligible': eligible,
        'addresses': len(addrs),
        'caps': caps,
        'cap': max(caps) if caps else 0,
        'judged': now.hour >= th['send_hour'],
    }


def read_health(today):
    path = os.path.join(HERE, 'health.json')
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {'fresh': False, 'names': []}
    checked = str((doc or {}).get('checked') or '')
    names = []
    for row in (doc or {}).get('checks') or []:
        if isinstance(row, dict) and row.get('level') == 'FAIL':
            names.append(str(row.get('name') or ''))
    return {'fresh': checked.startswith(today), 'names': names}


def gather(now, th, measure=False):
    now = now_local(now)
    th = th or thresholds()
    return {
        'tracerfy': _try(read_tracerfy, {'credits': None}),
        'captcha': _try(read_captcha, {'usd': None}),
        'paid_reads': _try(read_paid, {'readable': False}),
        'bounce': _try(read_bounce, {'readable': False}),
        'optout_sync': _try(lambda: read_optout(now, th),
                            {'readable': False, 'past_cutoff': now.hour >= th['optout_hour']}),
        'morning_sends': _try(lambda: read_morning(now, th), {'readable': False}),
        'health_fails': _try(lambda: read_health(now.date().isoformat()), {'fresh': False, 'names': []}),
        'readiness': measure_readiness(now, th['port']) if measure else None,
    }


def make_doc(now=None, measure=False, prev_readiness=None, th=None):
    now = now_local(now)
    th = th or thresholds()
    signals = gather(now, th, measure=measure)
    signals['refresh_flag'] = refresh_flag_signal(HERE)
    if not measure and isinstance(prev_readiness, dict) and prev_readiness.get('at'):
        signals['readiness'] = prev_readiness
    elif not measure:
        signals.pop('readiness', None)
    return build_doc(now, signals, alerts_from(signals, now, th))


def load_doc(path):
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_doc(path, doc):
    if not public_ok(doc):
        at = doc.get('published_at') if isinstance(doc, dict) else ''
        if not isinstance(at, str) or _UNSAFE.search(at):
            at = now_local().isoformat(timespec='seconds')
        doc = {
            'version': 1,
            'published_at': at,
            'alerts': [_alert('alerts-redacted', 'fail',
                              'An alert was withheld because its text was not public-safe.', at)],
            'signals': {},
        }
    raw = json.dumps(doc, indent=1, sort_keys=True) + '\n'
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(raw)
    os.replace(tmp, path)


def _git(args, repo):
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_MERGE_AUTOEDIT'] = 'no'
    try:
        p = subprocess.run(args, cwd=repo, capture_output=True, text=True, env=env, timeout=45)
    except subprocess.TimeoutExpired:
        return {'code': 124, 'out': '', 'err': ''}
    except OSError:
        return {'code': 127, 'out': '', 'err': ''}
    return {'code': p.returncode, 'out': p.stdout or '', 'err': ''}


def _lease_mine_path():
    """Same file runner_lock.py writes when this machine holds the cross-machine lease.
    Read locally. Never ls-remote — that is the call that would delay the 07:15 sync."""
    gitdir = os.environ.get('DEALFLOW_LOCK_GITDIR') or os.path.join(
        os.path.expanduser('~'), 'DEALFLOW', 'runner-lock.git')
    return os.path.join(gitdir, 'dealflow-mine.json')


def lease_held(now=None):
    try:
        with open(_lease_mine_path(), encoding='utf-8') as f:
            mine = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(mine, dict):
        return False
    try:
        exp = float(mine.get('expires_at'))
    except (TypeError, ValueError):
        return False
    try:
        slack = float(os.environ.get('DEALFLOW_LOCK_SKEW', '120') or 120)
    except ValueError:
        slack = 120.0
    when = time.time() if now is None else float(now)
    return when <= exp + slack


def _flag_age(repo, now=None):
    """Seconds since refresh-running.flag was written, or None when there is no flag.

    A stat failure on a flag that is there returns 0, so publishing still waits: we
    cannot prove the run is dead.
    """
    path = os.path.join(repo, 'refresh-running.flag')
    if not os.path.lexists(path):
        return None
    when = time.time() if now is None else float(now)
    try:
        return when - os.path.getmtime(path)
    except OSError:
        return 0.0


def refresh_flag_state(repo, now=None):
    """'', 'fresh', or 'stale'. Stale is older than the Refresh execution limit."""
    age = _flag_age(repo, now)
    if age is None:
        return ''
    if age > REFRESH_FLAG_MAX_AGE_S:
        return 'stale'
    return 'fresh'


def refresh_flag_signal(repo, now=None):
    """Counts for the published file. Only a stale flag is an alert."""
    if refresh_flag_state(repo, now) != 'stale':
        return {'stale': False}
    age = _flag_age(repo, now)
    out = {'stale': True}
    if isinstance(age, float) and math.isfinite(age) and age >= 0:
        out['age_h'] = round(age / 3600.0, 1)
    return out


def git_blocked(repo, now=None):
    """Why git must not run, or ''. A fresh flag or a live lease means the refresh still owns the repo.

    A flag older than 6h does not block. The scheduler's execution limit has already
    killed that run; the next refresh is what deletes the flag.
    """
    if refresh_flag_state(repo, now) == 'fresh':
        return 'refresh-running'
    if lease_held():
        return 'lease-held'
    return ''


def _ahead_behind(text):
    parts = (text or '').split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _staged_others(porcelain):
    """Index entries that are not pipeline_alerts.json. Unstaged and untracked files are not these."""
    others = []
    for line in (porcelain or '').splitlines():
        if len(line) < 4 or line[0] in (' ', '?'):
            continue
        path = line[3:].strip()
        if ' -> ' in path:
            path = path.split(' -> ', 1)[1].strip()
        if path.replace('\\', '/') != REL:
            others.append(path)
    return others


def _sha(run, repo, rev):
    got = run(['git', 'rev-parse', rev], repo)
    sha = (got.get('out') or '').strip()
    if got.get('code') != 0 or not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
        return ''
    return sha.lower()


def _undo_commit(run, repo, base):
    """Drop our commit. Soft reset keeps any other staged file staged; then unstage only ours."""
    soft = run(['git', 'reset', '--soft', base], repo)
    run(['git', 'reset', '-q', 'HEAD', '--', REL], repo)
    return soft.get('code') == 0


def _park_alerts(repo, run):
    """Lift our just-written file out of the way so a fast-forward can update the checkout.
    Returns the bytes to put back, or None when there was nothing to park."""
    path = os.path.join(repo, REL)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return None
    tracked = run(['git', 'ls-files', '--error-unmatch', '--', REL], repo)
    if tracked.get('code') == 0:
        co = run(['git', 'checkout', '-q', 'HEAD', '--', REL], repo)
        if co.get('code') != 0:
            return None
    else:
        try:
            os.remove(path)
        except OSError:
            return None
    return data


def _restore_alerts(repo, data):
    if data is None:
        return
    path = os.path.join(repo, REL)
    try:
        with open(path, 'wb') as f:
            f.write(data)
    except OSError:
        pass


def git_publish(repo, push=True, run=None):
    """Commit and (optionally) push only pipeline_alerts.json.

    No git work while a refresh or lease is in progress. Otherwise fetch, fast-forward
    if strictly behind, and commit only when HEAD == origin/main and nothing else is
    staged. A rejected push is undone so local main is not left ahead of origin.
    Never prints git output. Never force-pushes.
    """
    run = run or _git
    blocked = git_blocked(repo)
    if blocked:
        return blocked
    head = run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], repo)
    if head['code'] != 0 or head['out'].strip() != 'main':
        return 'not-on-main'
    git_dir = run(['git', 'rev-parse', '--git-dir'], repo)
    if git_dir['code'] != 0:
        return 'no-git'
    gdir = git_dir['out'].strip()
    if not os.path.isabs(gdir):
        gdir = os.path.join(repo, gdir)
    if os.path.isdir(os.path.join(gdir, 'rebase-merge')) or os.path.isdir(os.path.join(gdir, 'rebase-apply')):
        return 'rebase-in-progress'
    fetched = run(['git', 'fetch', 'origin', 'main'], repo)
    if fetched['code'] != 0:
        return 'fetch-failed'
    counts = run(['git', 'rev-list', '--left-right', '--count', 'origin/main...HEAD'], repo)
    pair = _ahead_behind(counts.get('out') if counts.get('code') == 0 else '')
    if pair is None:
        return 'fetch-failed'
    behind, ahead = pair
    if ahead > 0:
        return 'local-ahead'
    staged = run(['git', 'status', '--porcelain'], repo)
    if staged.get('code') != 0:
        return 'commit-failed'
    if _staged_others(staged.get('out')):
        return 'dirty-index'
    if behind > 0:
        saved = _park_alerts(repo, run)
        try:
            ff = run(['git', 'merge', '--ff-only', 'origin/main'], repo)
        finally:
            _restore_alerts(repo, saved)
        if ff.get('code') != 0:
            return 'ff-failed'
    local = _sha(run, repo, 'HEAD')
    origin = _sha(run, repo, 'origin/main')
    if not local or local != origin:
        return 'not-even'
    # This path is untracked the first night. `git commit -- path` only records paths git
    # already knows, so add it first. --only then commits the worktree copy of that path.
    added = run(['git', 'add', '--', REL], repo)
    if added['code'] != 0:
        return 'commit-failed'
    committed = run(['git', 'commit', '--only', '-m', MSG, '--', REL], repo)
    if committed['code'] != 0:
        run(['git', 'reset', '-q', 'HEAD', '--', REL], repo)
        status = run(['git', 'status', '--porcelain', '--', REL], repo)
        if (status.get('out') or '').strip():
            return 'commit-failed'
        return 'unchanged'
    if not push:
        return 'committed'
    pushed = run(['git', 'push', 'origin', 'HEAD:main'], repo)
    if pushed['code'] == 0:
        return 'pushed'
    if not _undo_commit(run, repo, local):
        return 'push-failed'
    return 'push-undone'


def evening_task_xml(repo):
    """Task XML for the current user. LeastPrivilege is schtasks /RL LIMITED. No password, no admin."""
    def x(text):
        return (str(text).replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('"', '&quot;'))
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <URI>\\DEALFLOW Evening Readiness</URI>
    <Description>DealFlow: counts-only pipeline alert check at 21:00 local. Writes pipeline_alerts.json and pushes that file when main is quiet.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-09-26T21:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>python</Command>
      <Arguments>-u pipeline_alerts.py evening</Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % x(repo)


def install_hint():
    """Write the evening-task XML and return the one schtasks command that registers it."""
    repo = HERE
    folder = os.path.join(repo, 'desktop-setup', 'tasks')
    os.makedirs(folder, exist_ok=True)
    xml_path = os.path.join(folder, 'DEALFLOW_Evening_Readiness.xml')
    # schtasks /Create /XML reads the file as UTF-16. A UTF-8 file is rejected
    # with "unable to switch the encoding" at the declaration. utf-16 writes the BOM.
    with open(xml_path, 'w', encoding='utf-16', newline='\n') as f:
        f.write(evening_task_xml(repo))
    return 'schtasks /Create /TN "DEALFLOW Evening Readiness" /XML "%s" /F' % xml_path


def publish(measure=False):
    """Write the public file. Push only when main is quiet and even with origin. Always returns 0."""
    try:
        blocked = git_blocked(HERE)
        if not blocked:
            if _git(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], HERE)['out'].strip() != 'main':
                _log('not on main; did not write or push')
                return 0
        prev = load_doc(os.path.join(HERE, REL))
        doc = make_doc(measure=measure, prev_readiness=(prev.get('signals') or {}).get('readiness'))
        write_doc(os.path.join(HERE, REL), doc)
        if blocked:
            keys = ','.join(a['key'] for a in doc.get('alerts') or []) or 'none'
            _log('keys=%s push=%s' % (keys, blocked))
            return 0
        how = git_publish(HERE, push=True)
        keys = ','.join(a['key'] for a in doc.get('alerts') or []) or 'none'
        _log('keys=%s push=%s' % (keys, how))
    except Exception as exc:
        _log('publish failed: %s' % type(exc).__name__)
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'publish'
    if cmd == 'install-hint':
        print(install_hint())
        return 0
    if cmd == 'evaluate':
        prev = load_doc(os.path.join(HERE, REL))
        doc = make_doc(measure=False, prev_readiness=(prev.get('signals') or {}).get('readiness'))
        print(json.dumps(doc.get('alerts') or [], indent=1))
        return 0
    if cmd == 'evening':
        return publish(measure=True)
    if cmd == 'publish':
        return publish(measure=False)
    _log('usage: pipeline_alerts.py publish | evening | install-hint | evaluate')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Exception as exc:
        _log('unexpected %s' % type(exc).__name__)
        sys.exit(0)
