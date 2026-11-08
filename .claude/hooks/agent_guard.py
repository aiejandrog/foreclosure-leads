#!/usr/bin/env python3
"""agent_guard.py - flight recorder + freeze guard for Claude Code agent teams on DEALFLOW.

Installed by hand (never by an agent) as a Claude Code hook in the MAIN checkout's
`.claude/settings.local.json` (gitignored, one machine). See
docs/agent-teams/AUDIT-LOOP-PROMPT-2026-11-08.md, "Setup paste".

What it does, per hook event (reads the hook JSON on stdin):

  every event   append ONE scrubbed line to <DEALFLOW_DIR>/agent-logs/<run>/events.jsonl
                (who, event, tool, a shortened scrubbed input, a shortened scrubbed result).
                Emails, phone numbers, long base64 and key=value secrets are masked before writing.
  PreToolUse    refuse (exit 2, reason on stderr) a tool call that breaks the freeze in
                docs/agent-teams/OPERATING-RULES-2026-11-07.md: running a .bat, a send / publish /
                paid-lookup script, touching scheduled tasks, DEALFLOW_FOLLOWUPS_ON, pushing anything
                but a claude/* branch, merging, force-pushing, reading key files, calling a paid vendor
                host, killing processes, or editing a report-only file (suppression surface, stays,
                disclaimers, Whitepages code, generated pages, the guard itself).
  Stop /        copy the session transcript (transcript_path / agent_transcript_path) into
  SubagentStop  <run>/transcripts/ so a failed run can be audited after the session is gone.
                Raw transcripts are NOT scrubbed; they live outside the repo and outside OneDrive.

The run folder name is AGENT_RUN_ID if set, else today's date (UTC).

    python agent_guard.py digest [RUN]        # counts per agent, blocked attempts, errors -> <run>/digest.md
    python agent_guard.py export RUN DEST     # scrubbed copy of the run's *.md (not events/transcripts)
                                              # into DEST (agent-loop/<run>/ in a claude/agents-* worktree)

Design rules:
  * Fails OPEN on its own bugs (exit 0): a broken recorder must not wedge a team. The permission
    deny list in ~/.claude/settings.json is the second layer and still applies.
  * Exit 2 is the only blocking exit (Claude Code hooks: any other non-zero exit does not block).
  * DEALFLOW_AGENT_UNLOCK="send_server.py,disclaimer.py" (comma list of basenames) lifts the
    edit block for those files only, for a session Alex starts on purpose. Commands are never unlocked.
  * Standard library only. Never imports repo code (the hook must work in any worktree state).
"""
import datetime
import json
import os
import re
import shutil
import sys

MAX_STR = 600
MAX_RESULT = 400

# ---------------------------------------------------------------- scrubbing
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE = re.compile(r'(?<![\w])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\w])')
_BLOB = re.compile(r'[A-Za-z0-9+/]{120,}={0,2}')
_SECRET = re.compile(r'(?i)\b(token|api[_-]?key|password|passwd|secret|authorization|bearer)\b(\s*[:=]\s*(?:token\s+|bearer\s+)?|\s+(?=[A-Za-z0-9_\-]{20,}))(\S+)')


def scrub(s):
    if not isinstance(s, str):
        s = json.dumps(s, ensure_ascii=False, default=str)
    s = _BLOB.sub('<blob>', s)
    s = _SECRET.sub(lambda m: m.group(1) + m.group(2) + '<secret>', s)
    s = _EMAIL.sub('<email>', s)
    s = _PHONE.sub('<phone>', s)
    return s


def short(v, n=MAX_STR):
    s = scrub(v)
    return s if len(s) <= n else s[:n] + '...<+%d chars>' % (len(s) - n)


# ---------------------------------------------------------------- where logs go
def log_root():
    base = os.environ.get('DEALFLOW_DIR') or os.path.join(os.path.expanduser('~'), 'DEALFLOW')
    return os.path.join(base, 'agent-logs')


def run_id():
    rid = os.environ.get('AGENT_RUN_ID') or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    return re.sub(r'[^A-Za-z0-9_.-]', '_', rid)[:60]


def run_dir(rid=None):
    d = os.path.join(log_root(), rid or run_id())
    os.makedirs(d, exist_ok=True)
    return d


def who(data):
    for k in ('teammate_name', 'agent_type', 'agent_id'):
        if data.get(k):
            return str(data[k])
    return 'main'


def record(data, verdict=None, reason=None):
    ev = {
        'ts': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'event': data.get('hook_event_name', '?'),
        'who': who(data),
        'session': str(data.get('session_id', ''))[:12],
    }
    if data.get('agent_id'):
        ev['agent_id'] = str(data['agent_id'])[:40]
    if data.get('tool_name'):
        ev['tool'] = data['tool_name']
    if 'tool_input' in data:
        ev['input'] = short(data['tool_input'])
    if 'tool_response' in data:
        ev['result'] = short(data['tool_response'], MAX_RESULT)
    for k in ('prompt', 'message', 'task_subject', 'reason'):
        if data.get(k):
            ev[k] = short(data[k])
    if verdict:
        ev['verdict'] = verdict
    if reason:
        ev['why'] = reason
    with open(os.path.join(run_dir(), 'events.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n')


def keep_transcripts(data):
    dest = os.path.join(run_dir(), 'transcripts')
    os.makedirs(dest, exist_ok=True)
    for k in ('transcript_path', 'agent_transcript_path'):
        p = data.get(k)
        if p and os.path.isfile(p):
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', who(data)) + '__' + os.path.basename(p)
            shutil.copy2(p, os.path.join(dest, name))


# ---------------------------------------------------------------- the guard
FREEZE_SCRIPTS = (
    'send_server', 'cadence', 'outreach_email', 'publish_site', 'quick_publish', 'access_codes',
    'warmup', 'whitepages_lookup', 'pacer_stay', 'bk_lookup', 'clerk_bk', 'clerk_bk_accept',
    'tracerfy', 'tracerfy_lookup', 'skiptrace', 'batchdata', 'records_liens', 'miami_judgment',
    'optout_sync', 'replies', 'bounces', 'bounce_scan', 'sheets_crm', 'lp_status', 'deep_equity',
    'call_coach_server', 'make_tracker', 'foreclosure_leads', 'run_documents', 'scrape_all',
)
_RUN_PY = re.compile(r'(?i)\b(?:python3?|py)(?:\.exe)?(?:\s+-\d+(?:\.\d+)?)?(?:\s+-u)?\s+'
                     r'(?:-m\s+)?["\']?(?:[\w.:~\\/-]*[\\/])?(' + '|'.join(FREEZE_SCRIPTS) + r')(?:\.py)?["\']?(?=\s|$|;|&|\|)')
_BAT = re.compile(r'(?i)[\w.\\/:-]*\.(?:bat|cmd)\b')
READ_VERBS = ('cat', 'type', 'get-content', 'gc', 'grep', 'rg', 'select-string', 'sls', 'head', 'tail',
              'less', 'more', 'wc', 'ls', 'dir', 'git diff', 'git log', 'git show', 'git blame',
              'git grep', 'git status')
_TASKS = re.compile(r'(?i)(enable-scheduledtask|start-scheduledtask|register-scheduledtask|'
                    r'set-scheduledtask|new-scheduledtask|schtasks(?:\.exe)?\s+/(?:run|change|create)|'
                    r'install-tasks\.ps1)')
_KEYFILE = re.compile(r'(?i)(?:[\w-]+\.(?:key|pass|codes)\b(?!\s*\()|\bsite\.url\b|[\w-]+\.url\b(?=\s|$|["\'])|'
                      r'\bsender\.json\b|\bfootprint\.local\.json\b|\bpacer\.pass\b)')
_VENDOR = re.compile(r'(?i)(whitepages|tracerfy|twilio|courtlistener|pacer\.uscourts|pcl\.uscourts|'
                     r'browardclerk|2captcha|deepgram|batchdata|mypalmbeachclerk|openphone)')
_NET = re.compile(r'(?i)\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|requests\.|urllib|httpx)\b')
_KILL = re.compile(r'(?i)\b(taskkill|stop-process|kill\s+-9|pkill|killall)\b')
_MERGE = re.compile(r'(?i)\b(gh\s+pr\s+merge|git\s+merge\b[^;&|]*\b(?:origin/)?main\b|'
                    r'git\s+checkout\s+main\b[^;&|]*&&[^;&|]*git\s+merge)')


def _git_push_problem(cmd):
    for m in re.finditer(r'(?i)\bgit\s+push\b([^;&|\n]*)', cmd):
        args = m.group(1)
        if re.search(r'(?i)(--force\b|--force-with-lease|\s-f\b|\s\+\S)', ' ' + args):
            return 'force-push is never allowed'
        if re.search(r'(?i)(?:^|\s|:)(main|master)\b', args):
            return 'pushing to main is never allowed; push your claude/agents-* branch and open a PR'
        if 'claude/' not in args:
            return 'name the branch: git push -u origin claude/agents-<name> (a bare push could hit main)'
    return None


def _starts_read_only(cmd):
    c = cmd.strip().lower()
    return any(c == v or c.startswith(v + ' ') for v in READ_VERBS)


_MSG = re.compile(r'(?s)(\s-m\s+)("(?:[^"\\]|\\.)*"|\'[^\']*\')')


def check_command(cmd):
    """Return a refusal reason for a shell command, or None."""
    if not cmd:
        return None
    cmd = _MSG.sub(r'\1""', cmd)          # a commit message may name a .bat or a key file; it runs nothing
    if 'DEALFLOW_FOLLOWUPS_ON' in cmd:
        return 'DEALFLOW_FOLLOWUPS_ON is frozen (OPERATING-RULES rule 3)'
    m = _RUN_PY.search(cmd)
    if m:
        name = m.group(1).lower()
        return ('running %s.py is frozen for agents (send / publish / paid lookup / live data). '
                'Import its functions in a test with a temp DEALFLOW_DIR instead.' % name)
    if re.search(r'(?i)\breply_send(?:\.py)?\b[^;&|]*--send\b', cmd):
        return 'reply_send --send emails a homeowner; only Alex runs it'
    if _BAT.search(cmd) and not _starts_read_only(cmd):
        return 'running a .bat/.cmd is frozen (OPERATING-RULES rule 2); read it with the Read tool'
    if _TASKS.search(cmd):
        return 'scheduled tasks are frozen (OPERATING-RULES rule 1)'
    why = _git_push_problem(cmd)
    if why:
        return why
    if _MERGE.search(cmd):
        return 'agents never merge; QA writes "ready for Alex" and Alex merges'
    if _KEYFILE.search(cmd):
        return 'key / code / sender files are never read, printed or copied by an agent'
    if _VENDOR.search(cmd) and _NET.search(cmd):
        return 'paid or rate-limited vendor call ($0 rule)'
    if _KILL.search(cmd):
        return 'never kill processes you did not start (other agents\' tests run there)'
    return None


# report-only files: basenames, and path tails (forward slashes, lower case)
BLOCK_BASENAMES = {
    # owned suppression surface (CLAUDE.md)
    'optout_sync.py', 'cadence.py', 'replies.py', 'bounced_emails.json', 'optouts.json',
    '_suppressiontest.py', '_optouttest.py', '_dnctest.py', 'outreach_email.py', 'text_hold.py',
    'sync_gate.py',
    # stays and contact
    'stay_gate.py', 'never_contact.json', 'bk_lookup.py', 'bk_waiver.json', 'pacer_stay.py',
    'send_server.py', 'reply_send.py', 'mail_guard.py',
    # legal wording, paid lookups, Whitepages
    'disclaimer.py', 'whitepages_lookup.py', 'wp_contacts.py',
    # data and identity
    'mail_sent.json', 'worker_notes.json', 'clients.json', 'deads.json', 'sender.json',
    'footprint.local.json', 'site.codes', 'pacer.pass', 'call_objections.json',
    # publish gates
    'publish_guard.py', 'healthcheck.py', 'board_url.py', 'footprint.py',
}
BLOCK_TAILS = (
    'docs/index.html', 'docs/call/', '.github/workflows/', 'desktop-setup/', '.claude/settings',
    '.claude/hooks/',
)
BLOCK_EXT = ('.bat', '.cmd', '.key', '.pass', '.codes', '.url', '.jsonl')
_LEADS_JSON = re.compile(r'^leads_.*\.json$')
_SUPP_FUNCS = re.compile(r'(_optedOutIdentities|_isOptedOutPerson|_textContactBlocked|_workerEligible|'
                         r'_laneStats|def\s+is_stop_text|def\s+is_sms_stop|NEVER_CONTACT)')


def _unlocked():
    raw = os.environ.get('DEALFLOW_AGENT_UNLOCK', '')
    return {x.strip().lower() for x in raw.split(',') if x.strip()}


def check_path(tool, path, tool_input):
    if not path:
        return None
    p = path.replace('\\', '/').lower()
    base = p.rsplit('/', 1)[-1]
    if tool == 'Read':
        if base.endswith(('.key', '.pass', '.codes', '.url')) or base in (
                'sender.json', 'footprint.local.json', 'pacer.pass', 'site.codes'):
            return 'key / code / sender files are never read by an agent'
        return None
    if base in _unlocked():
        return None
    if base in BLOCK_BASENAMES or base.endswith(BLOCK_EXT) or _LEADS_JSON.match(base):
        return ('%s is report-only for agents (OWNERS.md "Nobody on a team edits these"). '
                'Write the finding and a DECISIONS-NEEDED entry instead.' % base)
    for t in BLOCK_TAILS:
        if t in p:
            return '%s is generated, CI, machine setup or the guard itself; agents never edit it' % t
    if base == 'tracker_template.html':
        if tool == 'Write':
            return 'never overwrite tracker_template.html whole; Edit the one range you own'
        blob = '%s\n%s' % (tool_input.get('old_string', ''), tool_input.get('new_string', ''))
        for e in tool_input.get('edits', []) or []:
            blob += '\n%s\n%s' % (e.get('old_string', ''), e.get('new_string', ''))
        if _SUPP_FUNCS.search(blob):
            return 'that edit touches a board suppression function (owned surface); report it instead'
    if tool in ('Edit', 'MultiEdit'):
        blob = '%s\n%s' % (tool_input.get('old_string', ''), tool_input.get('new_string', ''))
        if re.search(r'def\s+is_(stop_text|sms_stop)\b', blob):
            return 'stop detection is the owned suppression surface; report it instead'
    return None


def check(data):
    tool = data.get('tool_name') or ''
    ti = data.get('tool_input') or {}
    if not isinstance(ti, dict):
        return None
    if tool in ('Bash', 'PowerShell') or 'command' in ti and tool not in ('Edit', 'Write', 'Read'):
        return check_command(ti.get('command') or '')
    if tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'Read'):
        return check_path(tool, ti.get('file_path') or ti.get('notebook_path') or '', ti)
    return None


# ---------------------------------------------------------------- digest
def digest(rid):
    d = os.path.join(log_root(), rid)
    path = os.path.join(d, 'events.jsonl')
    if not os.path.isfile(path):
        print('no events for run %s (%s)' % (rid, path))
        return 1
    per, blocked, errors = {}, [], []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            per[ev.get('who', '?')] = per.get(ev.get('who', '?'), 0) + 1
            if ev.get('verdict') == 'BLOCKED':
                blocked.append(ev)
            res = ev.get('result', '')
            if re.search(r'(?i)(traceback|error|exit code [1-9]|FAIL\b|refused)', res):
                errors.append(ev)
    out = ['# Agent run %s: digest (scrubbed, counts and commands only)' % rid, '',
           '| agent | events |', '|---|---|']
    out += ['| %s | %d |' % (k, v) for k, v in sorted(per.items())]
    out += ['', '## Blocked by the guard (%d)' % len(blocked)]
    out += ['- %s %s %s: %s' % (e['ts'], e['who'], e.get('tool', ''), e.get('why', '')) for e in blocked]
    out += ['', '## Tool results that look like failures (%d, first 60)' % len(errors)]
    out += ['- %s %s %s: %s' % (e['ts'], e['who'], e.get('tool', ''), e.get('input', '')[:160]) for e in errors[:60]]
    text = '\n'.join(out) + '\n'
    with open(os.path.join(d, 'digest.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    print(text)
    return 0


def export(rid, dest):
    """Copy the run's markdown (BOARD, worklogs, REPORT, AUTOPSY, digest) through scrub() into dest.
    Raw events.jsonl and transcripts never leave DEALFLOW_DIR."""
    src = os.path.join(log_root(), rid)
    if not os.path.isdir(src):
        print('no run folder %s' % src)
        return 1
    os.makedirs(dest, exist_ok=True)
    n = masked = 0
    for name in sorted(os.listdir(src)):
        if not name.lower().endswith('.md'):
            continue
        with open(os.path.join(src, name), encoding='utf-8', errors='replace') as f:
            raw = f.read()
        clean = scrub(raw)
        masked += int(clean != raw)
        with open(os.path.join(dest, name), 'w', encoding='utf-8') as f:
            f.write(clean)
        n += 1
    print('exported %d file(s) to %s; %d needed masking (read those before you commit)' % (n, dest, masked))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'digest':
        return digest(sys.argv[2] if len(sys.argv) > 2 else run_id())
    if len(sys.argv) > 3 and sys.argv[1] == 'export':
        return export(sys.argv[2], sys.argv[3])
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            return 0
    except Exception:
        return 0
    event = data.get('hook_event_name', '')
    reason = None
    try:
        if event == 'PreToolUse':
            reason = check(data)
    except Exception:
        reason = None                     # guard bug: fail open, deny list still applies
    try:
        if event == 'PreToolUse':
            if reason:
                record(data, 'BLOCKED', reason)
        else:
            record(data)
        if event in ('Stop', 'SubagentStop', 'SessionEnd'):
            keep_transcripts(data)
    except Exception:
        pass
    if reason:
        sys.stderr.write('agent_guard BLOCKED: %s\n' % reason)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
