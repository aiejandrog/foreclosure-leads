# HOOKS-PROPOSAL: enforce "QA PASS before done" and "output block before idle"

**PROPOSAL, NOT INSTALLED.** Nothing in this file has been added to any `.claude/settings.json`, and the script is not committed as a runnable hook. Installing it is Alex's call (it changes how every session on this repo behaves; settings changes are not for a team to make).

Sources: CLAUDE-CODE-TEAMS-NOTES.md (NOTES), fetched 2026-10-29 from the hooks and agent-teams pages. Where the notes do not document something, this file says so.

## What the notes document (and what they do not)

| Item | Documented in NOTES? |
|---|---|
| TaskCompleted fires when an agent marks a task completed via `TaskUpdate`, or when a teammate finishes its turn with in-progress tasks | Yes (Hooks > TaskCompleted) |
| TaskCompleted stdin fields: common fields plus `task_id`, `task_subject`, optional `task_description`, `teammate_name`, `team_name` (deprecated), `agent_id` (v2.1.290+, may be absent) | Yes |
| TeammateIdle stdin fields: common fields plus `teammate_name`, `team_name` (deprecated), `agent_id` (v2.1.290+, may be absent) | Yes |
| What the "common fields" are (for example the working directory) | **No.** The notes say "common fields" and never list them. The script below does not read any common field. |
| Working directory the hook command runs in | **No.** The script assumes the repo root and checks that `docs/agent-teams/runs` exists; if not, it exits 0 (does not block). **Unverified; test before relying on it.** |
| Exit 2: TaskCompleted leaves the task not completed and feeds stderr to the model; TeammateIdle feeds stderr to the teammate and keeps it working | Yes |
| Any exit other than 0 and 2 does not block | Yes |
| No matchers: every occurrence fires | Yes |
| Command hook default timeout 600 s | Yes |
| `"shell": "powershell"` on a command hook for Windows | Yes (Hooks > Windows PowerShell tool) |
| The exact settings.json nesting for hooks (`hooks` > event > list > `type`/`command`) | **No.** The notes do not show the settings.json shape. The snippet below is the conventional shape from the hooks page and **must be checked against code.claude.com/docs/en/hooks before install**. |
| A "stop hook already active" flag to prevent block loops | **No** for these two events. The script keeps its own attempt counter instead. |

## Convention the hooks depend on

1. Run log: `docs/agent-teams/runs/<YYYY-MM-DD>*.md` (PATTERNS.md header: `<date>-<team>.md`). The hook reads every file whose name starts with today's date.
2. Every task subject begins with its task id: `P1 ...`, `E-a ...`. (`task_subject` is documented; the id convention is ours.)
3. QA writes `QA PASS <task id> <command> <result>` on one line (PATTERNS.md section 6).
4. Every teammate finishes with an OUTPUT-TEMPLATE block whose `- Owner:` line names the teammate (OUTPUT-TEMPLATE.md). The hook matches `teammate_name` against that line, case-insensitive.
5. Tasks owned by the QA teammate itself, and by `QA_NAMES`, are exempt from the PASS check (QA does not PASS its own gate task; Lead decides).

## settings.json snippet (PROPOSAL)

```json
{
  "hooks": {
    "TaskCompleted": [
      { "hooks": [ { "type": "command", "shell": "powershell",
        "command": "python docs/agent-teams/hooks/agent_team_gate.py task-completed" } ] }
    ],
    "TeammateIdle": [
      { "hooks": [ { "type": "command", "shell": "powershell",
        "command": "python docs/agent-teams/hooks/agent_team_gate.py teammate-idle" } ] }
    ]
  }
}
```

If installed, put it in a gitignored `.claude/settings.local.json` first, so it affects one machine, and test with a throwaway task before turning it on for a real round.

## Script (PROPOSAL; save as docs/agent-teams/hooks/agent_team_gate.py only when Alex approves)

```python
#!/usr/bin/env python3
"""TaskCompleted / TeammateIdle gate for agent teams. PROPOSAL, NOT INSTALLED.

Reads the hook JSON from stdin. Fields used (all documented in NOTES):
  task_subject, task_id, teammate_name.
Exit 2 + stderr message = block (documented). Any other exit does not block.
Fails OPEN on anything unexpected (exit 0): a broken gate must not wedge a team.
"""
import datetime, glob, json, os, re, sys, tempfile

RUNS = os.path.join("docs", "agent-teams", "runs")
QA_NAMES = {"qa", "qa-gate"}      # teammates exempt from the PASS check
MAX_BLOCKS = 3                    # then let it through; the Lead steers by hand


def today_logs():
    day = datetime.date.today().isoformat()
    return sorted(glob.glob(os.path.join(RUNS, day + "*.md")))


def read_all(paths):
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                out.append(f.read())
        except OSError:
            pass
    return "\n".join(out)


def bump(key):
    """Count blocks per key in the temp dir; return the new count."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    path = os.path.join(tempfile.gettempdir(), "agent_team_gate_" + safe)
    n = 1
    try:
        with open(path) as f:
            n = int(f.read().strip() or "0") + 1
    except (OSError, ValueError):
        pass
    try:
        with open(path, "w") as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def block(key, msg):
    if bump(key) > MAX_BLOCKS:
        print("gate: %d blocks on %s, letting it through; tell main." % (MAX_BLOCKS, key), file=sys.stderr)
        sys.exit(0)
    print(msg, file=sys.stderr)
    sys.exit(2)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    if not os.path.isdir(RUNS):
        sys.exit(0)                      # cwd not the repo root: do not guess
    text = read_all(today_logs())
    who = (data.get("teammate_name") or "").strip()

    if mode == "task-completed":
        if who.lower() in QA_NAMES:
            sys.exit(0)
        subject = (data.get("task_subject") or "").strip()
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9_-]*)\b", subject)
        if not m:
            sys.exit(0)                  # no id convention on this task
        tid = re.escape(m.group(1))
        if re.search(r"^\s*QA PASS\s+" + tid + r"\b.+", text, re.M):
            sys.exit(0)
        block("task-" + m.group(1),
              "Task %s is not done: no line 'QA PASS %s <command> <result>' in "
              "docs/agent-teams/runs/%s*.md. Hand it to qa with worktree, branch, SHA "
              "and test commands. Do not write the PASS line yourself."
              % (m.group(1), m.group(1), datetime.date.today().isoformat()))

    elif mode == "teammate-idle":
        if not who:
            sys.exit(0)
        if re.search(r"^- Owner:.*" + re.escape(who), text, re.M | re.I):
            sys.exit(0)
        block("idle-" + who,
              "No OUTPUT-TEMPLATE block with '- Owner: %s' in "
              "docs/agent-teams/runs/%s*.md. Append your block (see "
              "docs/agent-teams/OUTPUT-TEMPLATE.md) before you stop."
              % (who, datetime.date.today().isoformat()))

    sys.exit(0)


if __name__ == "__main__":
    main()
```

## Known gaps in this proposal

- A teammate that writes the `QA PASS` line itself defeats the TaskCompleted check; the hook cannot tell who wrote a line. Review catches it (PATTERNS.md section 6: QA writes it).
- TeammateIdle checks that a block exists for the teammate, not that it is complete or for the current task.
- The 3-block cap exists because the notes document no stop-already-active flag for these events; without a cap a missing file would keep a teammate working indefinitely.
- Two teammates with name prefixes (`qa`, `qa2`) match each other in the `- Owner:` regex. Use distinct names.
- The hooks are only as good as the cwd assumption above. Verify before relying on them.
