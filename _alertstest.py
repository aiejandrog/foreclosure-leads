"""pipeline_alerts.py — each alert's trigger and its clear, fixtures only.

No network, no Tracerfy call, no 2Captcha call, no git push. Ledger rows use reserved
example domains and the assertions check that none of those addresses reach the public text.
"""
import datetime as dt
import json
import os
import shutil
import struct
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

import pipeline_alerts as PA
import send_server as S

ET = ZoneInfo('America/New_York')
NOW = dt.datetime(2026, 9, 27, 21, 5, tzinfo=ET)   # ramp has started; evening; after both judge hours
EARLY = dt.datetime(2026, 9, 27, 7, 20, tzinfo=ET)
TH = dict(PA.DEFAULTS)
ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)) if detail and not cond else ''))


def alerts(signals, now=NOW, th=TH):
    return {a['key']: a for a in PA.alerts_from(signals, now, th)}


def one(signals, key, now=NOW, th=TH):
    return alerts(signals, now, th).get(key)


def clean(text):
    return '@' not in text and 'http' not in text and 'example.com' not in text


print('-- tracerfy credits --')
rec('499 warns', (a := one({'tracerfy': {'credits': 499}}, 'tracerfy-credits'))
    and a['severity'] == 'warn' and '499' in a['text'] and '500' in a['text'] and clean(a['text']))
rec('500 is clear', one({'tracerfy': {'credits': 500}}, 'tracerfy-credits') is None)
rec('5 still buys one lookup, so it warns', (a := one({'tracerfy': {'credits': 5}}, 'tracerfy-credits'))
    and a['severity'] == 'warn' and 'Cannot pay' not in a['text'])
rec('4 cannot pay one lookup', (a := one({'tracerfy': {'credits': 4}}, 'tracerfy-credits'))
    and a['severity'] == 'fail' and 'Cannot pay' in a['text'] and clean(a['text']))
rec('0 is exhausted', one({'tracerfy': {'credits': 0}}, 'tracerfy-credits')['severity'] == 'fail')
rec('a missed probe is not an empty account', one({'tracerfy': {'credits': None}}, 'tracerfy-credits') is None)
rec('a missing signal is clear', one({}, 'tracerfy-credits') is None)

print('-- 2captcha balance --')
rec('under $1 warns while a solve still fits',
    (a := one({'captcha': {'usd': 0.40}}, 'captcha-balance')) and a['severity'] == 'warn' and clean(a['text']))
rec('$1.00 is clear', one({'captcha': {'usd': 1.0}}, 'captcha-balance') is None)
rec('below one solve fails', (a := one({'captcha': {'usd': 0.0}}, 'captcha-balance'))
    and a['severity'] == 'fail' and 'Cannot pay' in a['text'])
rec('exactly one solve warns rather than fails',
    one({'captcha': {'usd': TH['captcha_solve']}}, 'captcha-balance')['severity'] == 'warn')
rec('no key / probe down is clear', one({'captcha': {'usd': None}}, 'captcha-balance') is None)

print('-- paid-reads cap --')
rec('80% warns', (a := one({'paid_reads': {'readable': True, 'spent': 40, 'cap': 50}}, 'paid-reads-cap'))
    and a['severity'] == 'warn' and '80%' in a['text'] and clean(a['text']))
rec('79% is clear', one({'paid_reads': {'readable': True, 'spent': 39.5, 'cap': 50}}, 'paid-reads-cap') is None)
rec('100% fails', one({'paid_reads': {'readable': True, 'spent': 50, 'cap': 50}}, 'paid-reads-cap')['severity'] == 'fail')
rec('a $0 cap fails', one({'paid_reads': {'readable': True, 'spent': 0, 'cap': 0}}, 'paid-reads-cap')['severity'] == 'fail')
rec('an unreadable ledger is not an alert', one({'paid_reads': {'readable': False}}, 'paid-reads-cap') is None)

print('-- bounce: the same block, public text only --')


def health_rows(rows, bounced):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'bounced_emails.json'), 'w', encoding='utf-8') as f:
        json.dump(sorted(bounced), f)
    old_here, old_ledger = S.HERE, S._load_ledger
    try:
        S.HERE = tmp
        S._load_ledger = lambda: rows
        return S._bounce_health()
    finally:
        S.HERE, S._load_ledger = old_here, old_ledger
        shutil.rmtree(tmp, ignore_errors=True)


def ft_rows(dead, mailed, day=None):
    day = day or S.dt.date.today().isoformat()
    bad = ['dead%d@example.com' % i for i in range(dead)]
    live = ['live%d@example.com' % i for i in range(mailed - dead)]
    rows = [{'ch': 'email', 'message_id': '<%d>' % i, 'd': day, 'to': addr, 'touch': 'first'}
            for i, addr in enumerate(bad + live)]
    return rows, set(bad)


hot = PA.bounce_signal(health_rows(*ft_rows(2, 20)))
cold = PA.bounce_signal(health_rows(*ft_rows(0, 20)))
rec('2 of 20 first touches trips the shared block and the alert',
    hot['day_blocked'] is True and hot['trailing_blocked'] is False
    and (a := one({'bounce': hot}, 'bounce-rate')) and a['severity'] == 'fail'
    and '2 of 20' in a['text'] and clean(a['text']), a['text'] if hot.get('day_blocked') else hot)
rec('0 of 20 clears the alert', one({'bounce': cold}, 'bounce-rate') is None and cold['day_blocked'] is False)
rec('the public bounce signal has no address', clean(json.dumps(hot)) and hot.get('day_date'))
yday = (S.dt.date.today() - S.dt.timedelta(days=1)).isoformat()
yhot = PA.bounce_signal(health_rows(*ft_rows(2, 20, day=yday)))
ycold = PA.bounce_signal(health_rows(*ft_rows(1, 20, day=yday)))
rec("yesterday's 2 of 20 is the cohort the alert names",
    yhot['day_blocked'] is True and yhot['day_date'] == yday and yhot['trailing_blocked'] is False
    and (a := one({'bounce': yhot}, 'bounce-rate')) and yday in a['text'] and '2 of 20' in a['text']
    and clean(a['text']), a['text'] if yhot.get('day_blocked') else yhot)
rec("yesterday's 1 of 20 does not alert",
    ycold['day_blocked'] is False and ycold['day_date'] == yday
    and one({'bounce': ycold}, 'bounce-rate') is None)

print('-- opt-out sync --')
ran = {'readable': True, 'date': '2026-09-27', 'state': 'finished', 'run_ok': True, 'past_cutoff': True}
rec('a finished OK run is clear', one({'optout_sync': ran}, 'optout-sync') is None)
failed = dict(ran, run_ok=False, failed_steps=['optout_sync', 'owner@example.com'])
rec('a failed step alerts, and a bad step name is not published',
    (a := one({'optout_sync': failed}, 'optout-sync')) and a['severity'] == 'fail'
    and 'optout_sync' in a['text'] and clean(a['text']))
missed = {'readable': True, 'date': '2026-09-26', 'state': 'finished', 'run_ok': True, 'past_cutoff': True}
rec('no run today, after 08:00, alerts',
    (a := one({'optout_sync': missed}, 'optout-sync')) and 'did not run' in a['text'] and '2026-09-26' in a['text'])
rec('the same gap before the judge hour does not alert',
    one({'optout_sync': dict(missed, past_cutoff=False)}, 'optout-sync', now=EARLY) is None)
running = {'readable': True, 'date': '2026-09-27', 'state': 'running', 'run_ok': False,
           'started_at': NOW.timestamp() - 120, 'past_cutoff': True}
rec('a run that started two minutes ago is still in progress', one({'optout_sync': running}, 'optout-sync') is None)
stuck = dict(running, started_at=NOW.timestamp() - 7200)
rec('a run still marked running after two hours alerts', one({'optout_sync': stuck}, 'optout-sync') is not None)
rec('an unreadable status after the judge hour alerts',
    one({'optout_sync': {'readable': False, 'past_cutoff': True}}, 'optout-sync') is not None)
rec('an unreadable status before the judge hour does not',
    one({'optout_sync': {'readable': False, 'past_cutoff': False}}, 'optout-sync', now=EARLY) is None)

print('-- morning worker: nothing eligible is not nothing sent --')
base_m = {'readable': True, 'date': '2026-09-27', 'scheduled': True, 'sent': 0, 'eligible': 14,
          'addresses': 2, 'caps': [10, 10], 'judged': True}
rec('scheduled, eligible, sent 0 alerts',
    (a := one({'morning_sends': base_m}, 'morning-sends')) and '0 first-touch' in a['text']
    and '14' in a['text'] and clean(a['text']))
rec('sent 0 with nothing eligible is clear',
    PA.morning_kind(dict(base_m, eligible=0), NOW, TH) == 'nothing-eligible'
    and one({'morning_sends': dict(base_m, eligible=0)}, 'morning-sends') is None)
rec('a send clears it', one({'morning_sends': dict(base_m, sent=3)}, 'morning-sends') is None
    and PA.morning_kind(dict(base_m, sent=3), NOW, TH) == 'sent')
rec('not scheduled (before the ramp) is clear',
    PA.morning_kind(dict(base_m, scheduled=False), NOW, TH) == 'not-scheduled'
    and one({'morning_sends': dict(base_m, scheduled=False)}, 'morning-sends') is None)
rec('before the judge hour, a zero is not yet a miss',
    PA.morning_kind(dict(base_m, judged=False), EARLY, TH) == 'too-early')
rec('an uncounted pool is unknown, not empty',
    PA.morning_kind(dict(base_m, eligible=None), NOW, TH) == 'unknown'
    and one({'morning_sends': dict(base_m, eligible=None)}, 'morning-sends') is None)

cfg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'senders.json'), encoding='utf-8'))
addrs = S._first_touch_senders(cfg)
rec('repo ramp: two warm-up addresses', len(addrs) == 2)
rec('repo ramp: cap 0 on 2026-09-26',
    all(S._first_touch_cap(cfg, a, dt.date(2026, 9, 26)) == 0 for a in addrs))
rec('repo ramp: cap 10 on 2026-09-27',
    all(S._first_touch_cap(cfg, a, dt.date(2026, 9, 27)) == 10 for a in addrs))

print('-- laptop readiness --')


def ready(**kw):
    base = {'at': NOW.isoformat(), 'present_check': True, 'power_ok': True, 'battery_present': True,
            'on_ac': True, 'pct': 80, 'port_open': True, 'tasks_ok': True,
            'disabled': 0, 'failed': 0, 'interactive': 0,
            'disabled_names': [], 'failed_names': [], 'interactive_names': []}
    base.update(kw)
    return {'readiness': base}


rec('plugged in, charged, port up, tasks healthy is clear', one(ready(), 'laptop-readiness') is None)
rec('on battery alerts even at a high charge',
    (a := one(ready(on_ac=False, pct=90), 'laptop-readiness')) and 'on battery' in a['text'] and clean(a['text']))
rec('under 50% alerts on AC', 'battery 42%' in one(ready(pct=42), 'laptop-readiness')['text'])
rec('50% is not under 50', one(ready(pct=50), 'laptop-readiness') is None)
rec('a closed send-server port alerts', '8823' in one(ready(port_open=False), 'laptop-readiness')['text'])
rec('a disabled task alerts by name',
    'DEALFLOW Refresh' in one(ready(disabled=1, disabled_names=['DEALFLOW Refresh']), 'laptop-readiness')['text'])
rec('a failed last result alerts', one(ready(failed=1, failed_names=['DEALFLOW Phones']), 'laptop-readiness') is not None)
rec('an Interactive-only unattended task alerts',
    one(ready(interactive=1, interactive_names=['DEALFLOW Replies']), 'laptop-readiness') is not None)
rec('a task name that is an email is counted but not printed',
    (a := one(ready(disabled=1, disabled_names=['owner@example.com']), 'laptop-readiness'))
    and 'disabled' in a['text'] and clean(a['text']))
old = (NOW - dt.timedelta(hours=26)).isoformat()
rec('an evening check older than 20h alerts', '26h' in one(ready(at=old), 'laptop-readiness')['text'])
rec('no readiness block means the check is not armed', one({}, 'laptop-readiness') is None)
rec('a desktop with no battery does not alert on power',
    one(ready(battery_present=False, on_ac=None, pct=None), 'laptop-readiness') is None)
def power_bytes(ac, flag, pct):
    return struct.pack('<BBBBII', ac, flag, pct, 0, 0, 0)


power = PA.parse_power_bytes(power_bytes(0, 1, 42))
rec('ctypes power bytes: discharging at 42%', power['on_ac'] is False and power['pct'] == 42 and power['power_ok'] is True)
ac = PA.parse_power_bytes(power_bytes(1, 8, 80))
rec('ctypes power bytes: on AC at 80%', ac['on_ac'] is True and ac['pct'] == 80)
desk = PA.parse_power_bytes(power_bytes(1, 128, 255))
rec('ctypes power bytes: flag 128 is a desktop, not a failed probe',
    desk['battery_present'] is False and desk['power_ok'] is True)
rec('a short buffer is a failed probe', PA.parse_power_bytes(b'')['power_ok'] is False and PA.parse_power_bytes(None)['power_ok'] is False)
unknown = PA.parse_power_bytes(power_bytes(255, 255, 255))
rec('unknown percent is not a charge reading', unknown['pct'] is None and unknown['on_ac'] is None and unknown['battery_present'] is True)
csv_text = (
    '"TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result"\n'
    '"\\DEALFLOW Refresh","N/A","Ready","Interactive only","N/A","0"\n'
    '"\\DEALFLOW Phones","N/A","Disabled","Password","N/A","1"\n'
    '"\\DealFlow Replies","N/A","Ready","Password","N/A","267011"\n'
    '"\\DEALFLOW Evening Readiness","N/A","Ready","Interactive only","N/A","0"\n'
    '"\\Something Else","N/A","Disabled","Interactive only","N/A","1"\n'
)
tasks = PA.parse_tasks(csv_text)
rec('task parse: one disabled, one failed, one interactive unattended',
    (tasks['disabled'], tasks['failed'], tasks['interactive']) == (1, 1, 1), tasks)
rec('the evening task is not flagged just for being interactive',
    'Evening' not in ' '.join(tasks['interactive_names']))
rec('a task outside DEALFLOW is ignored', all('Something' not in n for n in tasks['disabled_names']))
built = PA.readiness_from(power, False, tasks, NOW)
rec('the parts combine into one alert',
    (a := one({'readiness': built}, 'laptop-readiness')) and 'on battery' in a['text']
    and '8823' in a['text'] and 'disabled' in a['text'] and clean(a['text']))
clear_parts = PA.readiness_from(ac, True, PA.parse_tasks(
    '"TaskName","Status","Logon Mode","Last Result"\n"\\DEALFLOW Refresh","Ready","Password","0"\n'), NOW)
rec('healthy parts clear', one({'readiness': clear_parts}, 'laptop-readiness') is None)

print('-- healthcheck FAIL lines: stage names only --')
rec('today\'s FAIL stages alert',
    (a := one({'health_fails': {'fresh': True, 'names': ['upstream sources', 'lead count (MD auction pipeline)']}},
              'healthcheck-fail'))
    and a['severity'] == 'fail' and 'upstream sources' in a['text'] and clean(a['text']))
rec('no FAIL lines is clear', one({'health_fails': {'fresh': True, 'names': []}}, 'healthcheck-fail') is None)
rec('yesterday\'s file is not today\'s outage',
    one({'health_fails': {'fresh': False, 'names': ['upstream sources']}}, 'healthcheck-fail') is None)
rec('a stage name that is an address is withheld',
    (a := one({'health_fails': {'fresh': True, 'names': ['owner@example.com']}}, 'healthcheck-fail'))
    and 'withheld' in a['text'] and clean(a['text']))

print('-- the file that gets committed cannot carry the fixture address --')
dirty = {'health_fails': {'fresh': True, 'names': ['owner@example.com', 'upstream sources']}}
doc = PA.build_doc(NOW, dirty, PA.alerts_from(dirty, NOW, TH))
blob = json.dumps(doc)
rec('scrubbed document has the safe stage and not the address',
    'upstream sources' in blob and 'owner@example.com' not in blob and PA.public_ok(doc))
tmp = tempfile.mkdtemp(prefix='alertsfile_')
try:
    leaked = {'version': 1, 'published_at': NOW.isoformat(),
              'alerts': [{'key': 'x', 'severity': 'fail', 'text': 'ping owner@example.com'}], 'signals': {}}
    PA.write_doc(os.path.join(tmp, 'pipeline_alerts.json'), leaked)
    written = open(os.path.join(tmp, 'pipeline_alerts.json'), encoding='utf-8').read()
    rec('write_doc refuses an unsafe document', '@' not in written and 'alerts-redacted' in written)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print('-- git publishes only this file, and only when main matches origin --')


def g_at(repo, args):
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)


def init_pair():
    root = tempfile.mkdtemp(prefix='alertsgit_')
    origin = os.path.join(root, 'origin.git')
    local = os.path.join(root, 'local')
    subprocess.run(['git', 'init', '--bare', '-b', 'main', origin], check=True, capture_output=True)
    subprocess.run(['git', 'init', '-b', 'main', local], check=True, capture_output=True)
    g_at(local, ['git', 'config', 'user.email', 'dev@example.com'])
    g_at(local, ['git', 'config', 'user.name', 'Alerts Test'])
    with open(os.path.join(local, 'README'), 'w', encoding='utf-8') as f:
        f.write('seed\n')
    g_at(local, ['git', 'add', 'README'])
    g_at(local, ['git', 'commit', '-m', 'seed'])
    g_at(local, ['git', 'remote', 'add', 'origin', origin])
    g_at(local, ['git', 'push', '-u', 'origin', 'main'])
    return root, local


def write_alert(local, credits=400):
    doc = PA.build_doc(NOW, {'tracerfy': {'credits': credits}},
                       PA.alerts_from({'tracerfy': {'credits': credits}}, NOW, TH))
    PA.write_doc(os.path.join(local, PA.REL), doc)


def head_sha(local):
    return g_at(local, ['git', 'rev-parse', 'HEAD']).stdout.strip()


def head_msg(local):
    return g_at(local, ['git', 'log', '-1', '--format=%s']).stdout.strip()


root, local = init_pair()
try:
    with open(os.path.join(local, 'notes.txt'), 'w', encoding='utf-8') as f:
        f.write('do not commit this\n')
    g_at(local, ['git', 'add', 'notes.txt'])
    write_alert(local)
    before = head_sha(local)
    rec('a dirty index touching another file skips git commit',
        PA.git_publish(local, push=False) == 'dirty-index' and head_sha(local) == before)
    staged = g_at(local, ['git', 'diff', '--cached', '--name-only']).stdout
    rec('the other staged file is still staged and uncommitted',
        'notes.txt' in staged and PA.REL not in staged.split())
    g_at(local, ['git', 'reset', '-q', 'HEAD', '--', 'notes.txt'])
    how = PA.git_publish(local, push=False)
    rec('commit happens without a push once the index is otherwise clean', how == 'committed', how)
    shown = g_at(local, ['git', 'show', '--name-only', '--pretty=format:%s', 'HEAD'])
    rec('the commit is the alerts message and only that file',
        [ln for ln in shown.stdout.splitlines() if ln.strip()] == [PA.MSG, PA.REL], shown.stdout)
    blob = g_at(local, ['git', 'show', 'HEAD:' + PA.REL]).stdout
    rec('the committed text is the warn, with no address', '400' in blob and '@' not in blob)
    rec('an unstaged unrelated file stayed out of the commit', 'notes.txt' not in shown.stdout)
    g_at(local, ['git', 'checkout', '-b', 'elsewhere'])
    with open(os.path.join(local, PA.REL), 'a', encoding='utf-8') as f:
        f.write('\n')
    rec('off main does not commit', PA.git_publish(local, push=False) == 'not-on-main')
finally:
    shutil.rmtree(root, ignore_errors=True)

print('-- git runner: do not leave local main ahead of origin --')
calls = []


def counting_run(args, repo):
    calls.append(list(args))
    return {'code': 0, 'out': 'main\n', 'err': ''}


flag_root = tempfile.mkdtemp(prefix='alertflag_')
try:
    with open(os.path.join(flag_root, 'refresh-running.flag'), 'w', encoding='utf-8') as f:
        f.write('running\n')
    calls.clear()
    rec('refresh-running.flag means zero git calls',
        PA.git_publish(flag_root, push=True, run=counting_run) == 'refresh-running' and calls == [])
finally:
    shutil.rmtree(flag_root, ignore_errors=True)

lease_dir = tempfile.mkdtemp(prefix='alertlease_')
old_gitdir = os.environ.get('DEALFLOW_LOCK_GITDIR')
try:
    os.environ['DEALFLOW_LOCK_GITDIR'] = lease_dir
    with open(os.path.join(lease_dir, 'dealflow-mine.json'), 'w', encoding='utf-8') as f:
        json.dump({'expires_at': time.time() + 3600, 'runner': 'refresh-dealflow.bat'}, f)
    calls.clear()
    rec('a live lease means zero git calls',
        PA.git_publish(tempfile.mkdtemp(prefix='alertlease2_'), push=True, run=counting_run) == 'lease-held'
        and calls == [])
    with open(os.path.join(lease_dir, 'dealflow-mine.json'), 'w', encoding='utf-8') as f:
        json.dump({'expires_at': time.time() - 10000, 'runner': 'refresh-dealflow.bat'}, f)
    rec('an expired lease is not held', PA.lease_held() is False)
finally:
    if old_gitdir is None:
        os.environ.pop('DEALFLOW_LOCK_GITDIR', None)
    else:
        os.environ['DEALFLOW_LOCK_GITDIR'] = old_gitdir
    shutil.rmtree(lease_dir, ignore_errors=True)


def reject_push(args, repo):
    if len(args) >= 2 and args[1] == 'push':
        return {'code': 1, 'out': '', 'err': 'rejected'}
    return PA._git(args, repo)


root, local = init_pair()
try:
    origin_sha = g_at(local, ['git', 'rev-parse', 'origin/main']).stdout.strip()
    write_alert(local)
    how = PA.git_publish(local, push=True, run=reject_push)
    rec('a rejected push is undone', how == 'push-undone', how)
    rec('local main is back at origin and the file is uncommitted',
        head_sha(local) == origin_sha
        and head_msg(local) != PA.MSG
        and PA.REL not in g_at(local, ['git', 'diff', '--cached', '--name-only']).stdout
        and os.path.isfile(os.path.join(local, PA.REL)))
finally:
    shutil.rmtree(root, ignore_errors=True)

root, local = init_pair()
try:
    with open(os.path.join(local, 'README'), 'a', encoding='utf-8') as f:
        f.write('local only\n')
    g_at(local, ['git', 'add', 'README'])
    g_at(local, ['git', 'commit', '-m', 'local only'])
    ahead = head_sha(local)
    write_alert(local)
    how = PA.git_publish(local, push=True)
    rec('local commits ahead of origin skip the publish', how == 'local-ahead', how)
    rec('an ahead main is not moved and the alert file is not committed',
        head_sha(local) == ahead and head_msg(local) == 'local only')
finally:
    shutil.rmtree(root, ignore_errors=True)

root, local = init_pair()
try:
    with open(os.path.join(local, 'README'), 'a', encoding='utf-8') as f:
        f.write('on origin\n')
    g_at(local, ['git', 'add', 'README'])
    g_at(local, ['git', 'commit', '-m', 'origin moved'])
    g_at(local, ['git', 'push', 'origin', 'main'])
    g_at(local, ['git', 'reset', '--hard', 'HEAD~1'])
    write_alert(local)
    how = PA.git_publish(local, push=True)
    rec('strictly behind fast-forwards, then commits and pushes', how == 'pushed', how)
    rec('the alerts commit sits on the origin commit it fast-forwarded to',
        head_msg(local) == PA.MSG
        and 'origin moved' in g_at(local, ['git', 'log', '-2', '--format=%s']).stdout
        and head_sha(local) == g_at(local, ['git', 'rev-parse', 'origin/main']).stdout.strip())
finally:
    shutil.rmtree(root, ignore_errors=True)

print('-- thresholds follow the env knobs --')
os.environ['TRACERFY_LOW_CREDITS'] = '50'
os.environ['CAPTCHA_LOW_USD'] = '2'
os.environ['PAID_READS_WARN_FRACTION'] = '0.5'
try:
    th = PA.thresholds()
    rec('TRACERFY_LOW_CREDITS=50', th['tracerfy_low'] == 50)
    rec('CAPTCHA_LOW_USD=2', th['captcha_low'] == 2)
    rec('PAID_READS_WARN_FRACTION=0.5', th['paid_warn'] == 0.5)
    rec('50-credit knob warns at 40 and not at 80',
        one({'tracerfy': {'credits': 40}}, 'tracerfy-credits', th=th) is not None
        and one({'tracerfy': {'credits': 80}}, 'tracerfy-credits', th=th) is None)
finally:
    os.environ.pop('TRACERFY_LOW_CREDITS', None)
    os.environ.pop('CAPTCHA_LOW_USD', None)
    os.environ.pop('PAID_READS_WARN_FRACTION', None)

hint = PA.install_hint()
xml_path = os.path.join(os.path.dirname(PA.__file__), 'desktop-setup', 'tasks', 'DEALFLOW_Evening_Readiness.xml')
raw_xml = open(xml_path, 'rb').read()
xml = raw_xml.decode('utf-16')
rec('the task XML starts with the UTF-16 BOM and declares UTF-16',
    raw_xml.startswith(b'\xff\xfe') and xml.startswith('<?xml version="1.0" encoding="UTF-16"?>'))
rec('install hint is schtasks /Create /XML and not a shell',
    hint.startswith('schtasks /Create /TN "DEALFLOW Evening Readiness" /XML ') and hint.endswith(' /F')
    and 'powershell' not in hint.lower())
rec('the task XML is least privilege and starts on battery at 21:00',
    '<RunLevel>LeastPrivilege</RunLevel>' in xml
    and '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>' in xml
    and '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>' in xml
    and '<StartWhenAvailable>true</StartWhenAvailable>' in xml
    and 'T21:00:00' in xml)
rec('install hint and the task XML have no token URL',
    'http' not in hint and '@' not in hint and '@' not in xml and 'hc-ping' not in xml)
src = open(PA.__file__, encoding='utf-8').read().lower()
rec('the publisher has no child powershell', 'powershell' not in src)

print('-- refresh-dealflow.bat order --')
bat_body = '\n'.join(
    line for line in open(os.path.join(os.path.dirname(PA.__file__), 'refresh-dealflow.bat'),
                           encoding='utf-8', errors='replace')
    if not line.strip().lower().startswith('rem'))
i_checkout = bat_body.find('git checkout -q HEAD -- pipeline_alerts.json')
i_pull = bat_body.find('git pull --ff-only origin main')
i_flag = bat_body.find('if exist refresh-running.flag del refresh-running.flag')
i_pub = bat_body.find('python -u pipeline_alerts.py publish')
i_exit = bat_body.find('endlocal & exit /b %RUNEXIT%')
rec('05:30 pull restores pipeline_alerts.json from HEAD first', 0 <= i_checkout < i_pull)
rec('nightly publish runs after the refresh flag is deleted and before the exit',
    0 <= i_flag < i_pub < i_exit)
rec('the nightly has one alerts publish', bat_body.count('python -u pipeline_alerts.py publish') == 1)

print()
print('%d/%d passed' % (len(ok), len(ok) + len(bad)))
raise SystemExit(0 if not bad else 1)
