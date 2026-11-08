"""_agentguardtest.py - the agent-team flight recorder / freeze guard (.claude/hooks/agent_guard.py).

Runs the hook as Claude Code would: JSON on stdin, exit 2 = blocked, exit 0 = allowed. Temp
DEALFLOW_DIR, no network, invented values only (example.com, 555 numbers).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, '.claude', 'hooks', 'agent_guard.py')
TMP = tempfile.mkdtemp(prefix='agentguard_')
ENV = dict(os.environ, DEALFLOW_DIR=TMP, AGENT_RUN_ID='t-run')
ENV.pop('DEALFLOW_AGENT_UNLOCK', None)
fails = []


def run(payload, env=None, raw=None):
    p = subprocess.run([sys.executable, HOOK], input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, env=env or ENV, timeout=30)
    return p.returncode, p.stderr


def bash(cmd):
    return {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': {'command': cmd},
            'session_id': 'sess-abc'}


def edit(path, old='a', new='b', tool='Edit'):
    return {'hook_event_name': 'PreToolUse', 'tool_name': tool,
            'tool_input': {'file_path': path, 'old_string': old, 'new_string': new, 'content': new}}


def expect(name, payload, code, env=None):
    got, err = run(payload, env)
    if got != code:
        fails.append('%s: expected exit %d, got %d (%s)' % (name, code, got, err.strip()))


# ---- commands that must be refused
for c in ['refresh-dealflow.bat', r'.\run-leads.bat', 'cmd /c cadence-daily.bat',
          'python send_server.py --port 8765', 'py -3 cadence.py', 'python outreach_email.py',
          r'python C:\x\foreclosure-leads\publish_site.py', 'python quick_publish.py',
          'python whitepages_lookup.py --max-spend 2', 'python bk_lookup.py --case 2025-000201',
          'python -m pacer_stay --bulk', 'python reply_send.py --case C --to a --send',
          '$env:DEALFLOW_FOLLOWUPS_ON=1; python x.py',
          'pwsh .\\desktop-setup\\install-tasks.ps1 -Enable', 'Enable-ScheduledTask -TaskName X',
          'schtasks /run /tn "DealFlow Refresh"',
          'git push origin main', 'git push', 'git push -f origin claude/agents-x',
          'git push origin HEAD:main', 'git push --force-with-lease origin claude/agents-x',
          'gh pr merge 450', 'git merge origin/main && git push origin main',
          'Get-Content whitepages.key', 'type site.codes', 'cat sender.json',
          'curl https://api.twilio.com/2010-04-01/Accounts', 'taskkill /im python.exe /f',
          'python foreclosure_leads.py', 'echo agent-guard-probe.bat']:
    expect('refuse: ' + c, bash(c), 2)

# ---- commands that must pass
for c in ['python _cadencetest.py', 'python ci_suite.py', 'python build_preview.py',
          'git push -u origin claude/agents-site', 'git status', 'git log --oneline -5',
          'grep -n publish_guard refresh-dealflow.bat', 'python -c "import call_mode"',
          'git commit -m "docs: note that refresh-dealflow.bat stays frozen; site.codes untouched"',
          'git worktree add ../fl-audit-site -b claude/agents-audit-site origin/main',
          'node docs-check.js', 'python _agentguardtest.py']:
    expect('allow: ' + c, bash(c), 0)

# ---- edits
for path in ['cadence.py', r'C:\fl-x\stay_gate.py', '/w/fl-a/send_server.py', 'disclaimer.py',
             'refresh-dealflow.bat', 'docs/index.html', 'docs/call/carlos/index.html',
             'wp_contacts.py', '.claude/settings.local.json', '.claude/hooks/agent_guard.py',
             'leads_final.json', 'optouts.json', '.github/workflows/ci.yml']:
    expect('refuse edit: ' + path, edit(path), 2)
expect('refuse Write tracker_template', edit('tracker_template.html', tool='Write'), 2)
expect('refuse suppression fn edit', edit('tracker_template.html', 'function _laneStats(', 'x'), 2)
expect('refuse is_stop_text edit', edit('some_new.py', 'def is_stop_text(t):', 'x'), 2)
for path in ['call_mode.py', 'tracker_template.html', 'agent-loop/2026-11-08/BOARD.md',
             '_newfeaturetest.py', 'equity_state.py']:
    expect('allow edit: ' + path, edit(path, 'cardTitle', 'cardTitle2'), 0)
expect('refuse Read of key', {'hook_event_name': 'PreToolUse', 'tool_name': 'Read',
                              'tool_input': {'file_path': r'C:\Users\x\DEALFLOW\pacer.pass'}}, 2)
expect('allow Read of code', {'hook_event_name': 'PreToolUse', 'tool_name': 'Read',
                              'tool_input': {'file_path': 'cadence.py'}}, 0)
unlock = dict(ENV, DEALFLOW_AGENT_UNLOCK='send_server.py')
expect('unlock lifts one file', edit('send_server.py'), 0, unlock)
expect('unlock does not lift another', edit('stay_gate.py'), 2, unlock)

# ---- fail open
code, _ = run(None, raw='not json')
if code != 0:
    fails.append('malformed stdin must exit 0, got %d' % code)
code, _ = run({'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'tool_input': 'weird'})
if code != 0:
    fails.append('non-dict tool_input must exit 0, got %d' % code)

# ---- recorder: scrubbed line per event, blocked attempts logged, transcript kept
tr = os.path.join(TMP, 'sess.jsonl')
with open(tr, 'w') as f:
    f.write('{"x":1}\n')
run({'hook_event_name': 'PostToolUse', 'tool_name': 'Bash', 'teammate_name': 'site',
     'tool_input': {'command': 'echo owner@example.com 305-555-0142 token=abcdef123456'},
     'tool_response': {'stdout': 'call (786) 555-0199 now'}})
run({'hook_event_name': 'SubagentStop', 'teammate_name': 'site', 'transcript_path': tr})
log = os.path.join(TMP, 'agent-logs', 't-run', 'events.jsonl')
lines = [json.loads(l) for l in open(log, encoding='utf-8')] if os.path.isfile(log) else []
text = open(log, encoding='utf-8').read() if lines else ''
if not lines:
    fails.append('no events.jsonl written')
for leak in ('owner@example.com', '305-555-0142', '555-0199', 'abcdef123456'):
    if leak in text:
        fails.append('recorder leaked %s' % leak)
if not any(l.get('verdict') == 'BLOCKED' and 'send_server' in l.get('why', '') for l in lines):
    fails.append('blocked attempt not recorded')
if not any(l.get('who') == 'site' for l in lines):
    fails.append('teammate name not recorded')
if not os.path.isfile(os.path.join(TMP, 'agent-logs', 't-run', 'transcripts', 'site__sess.jsonl')):
    fails.append('transcript not copied on SubagentStop')
p = subprocess.run([sys.executable, HOOK, 'digest', 't-run'], capture_output=True, text=True, env=ENV,
                   timeout=30)
if p.returncode != 0 or 'Blocked by the guard' not in p.stdout:
    fails.append('digest failed: %s' % (p.stdout[-300:] + p.stderr[-300:]))

with open(os.path.join(TMP, 'agent-logs', 't-run', 'BOARD.md'), 'w', encoding='utf-8') as f:
    f.write('T-1 owner owner@example.com 954-555-0100 case 2025-000201\n')
dest = os.path.join(TMP, 'export')
p = subprocess.run([sys.executable, HOOK, 'export', 't-run', dest], capture_output=True, text=True, env=ENV,
                   timeout=30)
out = open(os.path.join(dest, 'BOARD.md'), encoding='utf-8').read() if os.path.isfile(
    os.path.join(dest, 'BOARD.md')) else ''
if p.returncode != 0 or 'example.com' in out or '954-555-0100' in out or '2025-000201' not in out:
    fails.append('export did not scrub, or ate the case number: %r %s' % (out, p.stdout + p.stderr))
if os.path.exists(os.path.join(dest, 'events.jsonl')):
    fails.append('export copied raw events')

print('agent_guard checks: %d failure(s)' % len(fails))
for f in fails:
    print('FAIL', f)
sys.exit(1 if fails else 0)
