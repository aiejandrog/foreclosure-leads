# Claude Code agent teams: notes from the official docs

Fetched 2026-10-29 by `researcher` with WebFetch. Every line ends with the page and section it came from.
Nothing here is from memory.

## Fetch status

| Page | URL | Status |
|---|---|---|
| agent-teams | https://code.claude.com/docs/en/agent-teams | OK, read in full |
| hooks | https://code.claude.com/docs/en/hooks.md | OK, read in full (page is 283,467 chars; fetched in three windows: 0-100k, 100k-200k, 200k-end). Lifecycle, matcher, decision-control and exit-code tables came from the summary of window 1; the TeammateIdle, TaskCreated and TaskCompleted sections were read directly |
| sub-agents | https://code.claude.com/docs/en/sub-agents.md | PARTIAL: first 100,000 of 116,070 chars read. The "Fork the current conversation" section was missing from the captured text, so fork behaviour is cited only from the passages that mention it |
| costs | https://code.claude.com/docs/en/costs.md | OK, read in full (linked from agent-teams > Token usage) |

No fetch errored. Links followed were the ones printed on the agent-teams page.

## Enabling

- Agent teams are experimental and disabled by default. Enable with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in settings.json or the environment. (agent-teams > top warning; Enable agent teams)
- settings.json form is `{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}`. (agent-teams > Enable agent teams)
- Without the variable, no team is set up at session start, no team directories are written, and Claude does not spawn or propose teammates. (agent-teams > top warning)
- Spawning needs an interactive session. With `-p`, including Agent SDK sessions, Claude does not spawn teammates; a subagent Claude names runs as an ordinary subagent. (agent-teams > Enable agent teams)
- Side effect of enabling: a subagent Claude names launches as a teammate, so teams can form when you did not ask for one. Claude Code does not ask you to confirm the launch. (agent-teams > Enable agent teams; How Claude starts agent teams)
- A teammate launches when Claude calls the Agent tool with a `name` while teams are enabled, unless the call is a fork or passes `isolation` on the call itself. (agent-teams > How Claude starts agent teams)
- To turn this off, set the variable to `0`. It is re-read each time Claude spawns a subagent, so no new session is needed. Project, local, `--settings` and managed settings can still set it back to `1`. (agent-teams > Troubleshooting > Claude spawns teammates instead of subagents)
- Claude may use subagents instead of creating a team, and subagents appear in the same agent panel as teammates; if that happens, ask again and explicitly request an agent team. (agent-teams > Start your first agent team)

## Roles

- Team lead: the main Claude Code session that spawns teammates and coordinates work. (agent-teams > Architecture)
- The main session is the lead for its lifetime; a teammate cannot be promoted and leadership cannot be transferred. (agent-teams > Limitations > Lead is fixed)
- Teammates: separate Claude Code instances that each work on assigned tasks, each in its own context window. (agent-teams > Architecture; intro)
- You can talk to any teammate directly without going through the lead. (agent-teams > intro; Talk to teammates directly)
- Only the lead can manage the team; teammates cannot spawn their own teammates (no nested teams). (agent-teams > Limitations > No nested teams)
- One team per session; you cannot create additional named teams or share a team across sessions. (agent-teams > Limitations > One team per session)
- The lead assigns every teammate a name at spawn; any teammate can message any other by that name. To get predictable names, say what to call each one in the spawn instruction. (agent-teams > Context and communication)
- Team config `members` array lists each member's name and agent ID; the lead's entry always has agent type `team-lead`. Teammates can read the file to discover members. (agent-teams > Architecture)
- Do not hand-edit or pre-author the team config; changes are overwritten on the next state update. `.claude/teams/teams.json` in a project is not recognized as configuration. (agent-teams > Architecture)
- Teammates load CLAUDE.md, MCP servers and skills like a regular session, plus the spawn prompt. The lead's conversation history does not carry over. (agent-teams > Context and communication)
- If the lead starts with `--setting-sources`, teammates load from the same restricted list. Before v2.1.281, split-pane teammates loaded every settings source. (agent-teams > Context and communication)

## Shared task list

- Tasks have three states: pending, in progress, completed. (agent-teams > Assign and claim tasks)
- A task can depend on other tasks and shows as blocked until they complete. Teammates do not self-claim a blocked task, but you or the lead can still assign it. (agent-teams > Assign and claim tasks)
- Claude Code unblocks dependent tasks automatically when a teammate completes the task they depend on. (agent-teams > Architecture)
- Lead assigns: tell the lead which task goes to which teammate. Self-claim: after finishing, a teammate picks up the next unassigned, unblocked task. (agent-teams > Assign and claim tasks)
- Claiming uses file locking to prevent two teammates claiming the same task. (agent-teams > Assign and claim tasks)
- The task list exists only for agents that have the Task tools; agents without them coordinate through messages. (agent-teams > Assign and claim tasks; Context and communication)
- Task list location: `~/.claude/tasks/{team-name}/`; it stays on disk after the session ends, governed by `cleanupPeriodDays`. Team name is `session-` plus the first eight characters of the session ID. (agent-teams > Architecture)
- Ctrl+T toggles the task list in in-process mode. (agent-teams > Talk to teammates directly)
- Task status can lag: teammates sometimes fail to mark tasks completed, which blocks dependents. Check the work and update the status by hand, or tell the lead to nudge. (agent-teams > Limitations > Task status can lag)
- Sizing: too small and coordination overhead exceeds the benefit; too large and teammates run long without check-ins; right is a self-contained unit with a clear deliverable such as a function, a test file or a review. (agent-teams > Best practices > Size tasks appropriately)
- 5-6 tasks per teammate keeps everyone productive and lets the lead reassign if someone gets stuck. If the lead is not creating enough tasks, ask it to split the work. (agent-teams > Best practices > Size tasks appropriately)

## Messaging and mailbox

- Mailbox is the messaging system between agents. Each agent's mailbox is a JSON file at `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`. (agent-teams > Architecture)
- Claude Code validates every mailbox entry on read; entries that do not match the format are reported as errors and removed, and valid messages are still delivered. (agent-teams > Architecture)
- A message counts as sent only when the write to the recipient's mailbox succeeds; on failure the sender gets an error and nothing is sent. (agent-teams > Architecture)
- Messages are delivered automatically; the lead does not need to poll. (agent-teams > Context and communication)
- When a teammate finishes and stops, it notifies the lead and includes its final answer. A turn that ends on an API error notifies the lead with the error text. (agent-teams > Context and communication)
- To message one teammate, send by name. To reach everyone, send one message per recipient. (agent-teams > Context and communication)
- A message from the lead or another teammate wakes an in-process teammate that is waiting to retry a failed API request. (agent-teams > Troubleshooting > Agents stopping early)
- Shutdown: ask the lead to shut a named teammate down; the lead sends a request and the teammate can approve or reject with an explanation. (agent-teams > Shut down teammates)
- Shutdown can be slow: the teammate finishes its current request or tool call first. (agent-teams > Limitations > Shutdown can be slow)
- Team config directory is removed when the session ends; no separate cleanup step. (agent-teams > Shut down teammates; Architecture)

## Display modes (Windows, VS Code integrated terminal)

- Two modes: in-process (all teammates inside the main terminal, works in any terminal, no setup) and split panes (each teammate in its own pane, requires tmux or iTerm2). (agent-teams > Choose a display mode)
- Default is `"in-process"`. `"auto"` uses split panes when already inside tmux or in iTerm2 with the `it2` CLI, otherwise falls back to in-process. `"tmux"` enables split panes and auto-detects tmux or iTerm2. `"iterm2"` uses iTerm2 panes and errors if `it2` is missing. (agent-teams > Choose a display mode)
- Set `teammateMode` in `~/.claude/settings.json`, or per session with `claude --teammate-mode auto`. The flag is experimental and absent from `claude --help`. (agent-teams > Choose a display mode)
- **Split-pane mode is not supported in VS Code's integrated terminal, Windows Terminal, or Ghostty.** (agent-teams > Limitations > Split panes require tmux or iTerm2)
- tmux has known limitations on certain operating systems and traditionally works best on macOS; `tmux -CC` in iTerm2 is the suggested entrypoint. (agent-teams > Choose a display mode, note)
- The page names no Windows-specific setup for teammates and no VS Code-specific setting; on Windows and in the VS Code terminal the page leaves in-process as the only documented mode. (agent-teams > Choose a display mode; Limitations. This line is an inference from absence, not a quoted statement.)
- In-process controls: Up/Down in the agent panel selects a teammate; Enter opens its transcript and lets you message it; Escape clears the selection, and while viewing a transcript Escape interrupts that teammate's current turn; `x` on a selected teammate stops it; Ctrl+T toggles the task list. (agent-teams > Start your first agent team; Talk to teammates directly)
- Idle rows hide 30 seconds after the whole panel goes idle and reappear on the teammate's next turn; the teammate stays running and addressable. More than three idle teammates collapse into one `N idle agents` row that Enter expands. (agent-teams > Start your first agent team; Troubleshooting > Teammates not appearing)
- While viewing an in-process teammate, plain text and skills go to the teammate; built-in commands go to the lead. `/compact`, `/clear`, `/rewind` act on the lead and ask for confirmation; `/model` and `/fast` do not run from this view. A teammate's model and fast mode are fixed at spawn. (agent-teams > Talk to teammates directly)
- Orphaned tmux sessions: `tmux ls` then `tmux kill-session -t <session-name>`. (agent-teams > Troubleshooting > Orphaned tmux sessions)
- Hooks on Windows can run in PowerShell by setting `"shell": "powershell"` on a command hook; Claude Code auto-detects `pwsh.exe` and falls back to `powershell.exe`. (hooks > Windows PowerShell tool)

## Models

- Model order for a teammate: (1) the model the spawn prompt names, (2) the subagent definition's `model` (`inherit` selects the lead's), (3) `CLAUDE_CODE_SUBAGENT_MODEL` if not `inherit`, (4) the lead's current model. (agent-teams > Specify teammates and models)
- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` skips sources 1 and 2; requires v2.1.257 or later. Before v2.1.251 `CLAUDE_CODE_SUBAGENT_MODEL` came first. (agent-teams > Specify teammates and models)
- `teammateDefaultModel` was removed in v2.1.234; name the model in the prompt. (agent-teams > Specify teammates and models, note)
- A model blocked by the organization's `availableModels` allowlist is substituted: a family alias goes to the newest permitted version of that family on the Anthropic API; any other blocked value runs on the lead's model. (agent-teams > Specify teammates and models)
- Teammates inherit the lead's effort level by default; in split-pane mode this applies from v2.1.186. (agent-teams > Specify teammates and models)

## Subagent definitions used as teammates

- You can name a subagent type from the project, user, managed or plugin scope when spawning a teammate, in either display mode. Example prompt: "Spawn a teammate using the security-reviewer agent type to audit the auth module." (agent-teams > Use subagent definitions for teammates)
- Scope and priority when names collide: managed settings (1), `--agents` flag (2), `.claude/agents/` (3), `~/.claude/agents/` (4), plugin `agents/` (5). Project definitions are found by walking up from the working directory; the closest wins. (sub-agents > Choose the subagent scope)
- Applied to a teammate: `tools` (limits it; for in-process, `SendMessage` and, where Task tools exist, `TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate` are added). `model` (both modes, if the spawn prompt names none). (agent-teams > Use subagent definitions for teammates)
- In-process only: `disallowedTools` (SendMessage and the Task tools stay even if listed), `effort`, `autoCompactWindow` (v2.1.296+), and the body, which is appended to the default system prompt. (agent-teams > Use subagent definitions for teammates)
- Split-pane: the body replaces the default system prompt; `mcpServers` from the definition is applied; `autoCompactWindow` is ignored. An in-process teammate ignores `mcpServers`. (agent-teams > Use subagent definitions for teammates)
- `skills` in the definition is not applied to a teammate in either mode; the teammate loads skills from project and user settings. (agent-teams > Use subagent definitions for teammates)
- When a teammate that is no longer running is messaged, Claude Code brings it back in the same session and restores any saved conversation. After `/resume`, teammates are not brought back this way. (agent-teams > Use subagent definitions for teammates)
- For a brought-back teammate, a definition from a project's `.claude/agents/` or an `--add-dir` directory is re-applied only if you have trusted the folder the agent file is in; trusting a parent folder does not count. Until then the teammate has none of the definition's tools or instructions, only the tools added to every in-process teammate. (agent-teams > Use subagent definitions for teammates)
- Frontmatter `isolation: worktree` runs a subagent in a temporary git worktree. An `isolation` value in frontmatter does not stop a named subagent launching as a teammate, and that teammate runs in the main session's working directory. (sub-agents > Supported frontmatter fields; Subagent names)
- Passing `isolation` on the Agent call itself, or a fork, keeps the call from launching as a teammate. (agent-teams > How Claude starts agent teams; sub-agents > Subagent names)
- Named subagents get a sibling roster reminder listing `main` and other named agents, only when the subagent has `SendMessage` and another agent has a name. It is a snapshot at start. (sub-agents > What loads at startup)
- Frontmatter fields the page lists include `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `autoCompactWindow`, `skills`, `mcpServers`, `hooks`, `memory`, `background`, `omitClaudeMd`, `effort`, `isolation`, `color`, `initialPrompt`, `experimental`. Unrecognized fields are ignored without an error. (sub-agents > Supported frontmatter fields)
- An in-process teammate's own subagents run in the foreground; a definition with `background: true` errors when a teammate spawns it. (agent-teams > Limitations > No background subagents from in-process teammates)

## Hooks: TeammateIdle, TaskCreated, TaskCompleted

None of the three supports matchers; each fires on every occurrence. (hooks > TeammateIdle; TaskCreated; TaskCompleted)

General exit-code rules:
- Exit 0 is success; Claude Code applies any JSON output printed. (hooks > Exit code output)
- Exit 2 is a blocking error; on events that can block, Claude Code stops the action. All three events here can block. (hooks > Exit code output; Exit code 2 behavior per event)
- Any other exit code (or failure to start, or invalid JSON) is a non-blocking error: the action goes ahead. (hooks > Exit code output)
- Claude never sees stderr from a hook that exits 0. (hooks > Exit code output)
- Exit 2 blocks even if the hook prints valid JSON; the JSON cannot override the block. (hooks > Exit code output)
- On exit 0, stdout for most events goes to the debug log and is not shown in the transcript. (hooks > Exit code output; this is a general statement, the per-event sections for these three events say nothing different)
- Command hooks default to a 600-second timeout; a timed-out hook's output is discarded apart from async hooks. (hooks > Exit code output)
- Universal JSON fields: `continue` (false stops Claude and takes precedence), `stopReason`, `suppressOutput` (no effect), `systemMessage`. (hooks > JSON output)

TeammateIdle:
- Runs when a teammate is about to go idle after finishing its turn. (hooks > TeammateIdle)
- Input: common fields plus `teammate_name`, `team_name` (deprecated), and `agent_id` (v2.1.290+, may be absent). (hooks > TeammateIdle input)
- Exit 2: the teammate receives the stderr text as feedback and keeps working instead of going idle. (hooks > TeammateIdle decision control)
- JSON `{"continue": false, "stopReason": "..."}`: stops the teammate entirely, like a `Stop` hook; `stopReason` is shown to the user. (hooks > TeammateIdle decision control)
- Prompt-based hook `ok: false`: by default the teammate stops and the reason shows as a warning line; `continueOnBlock: true` feeds the reason back and keeps it working. (hooks > Prompt-based hooks, per-event behavior)

TaskCreated:
- Runs when a task is created via the `TaskCreate` tool. In a session without the Task tools this event does not fire. (hooks > TaskCreated)
- Input: common fields plus `task_id`, `task_subject`, optional `task_description`, `teammate_name`, `team_name` (deprecated), and `agent_id` (v2.1.290+). (hooks > TaskCreated input)
- Exit 2: Claude Code deletes the task and returns the stderr text to Claude as the tool's error. (hooks > TaskCreated decision control)
- JSON `{"decision": "block", "reason": "..."}` does the same with `reason` as the message. `continue: false` is ignored and Claude keeps working. (hooks > TaskCreated decision control)

TaskCompleted:
- Fires in two cases: an agent marks a task completed through `TaskUpdate`, or an agent-team teammate finishes its turn with in-progress tasks. (hooks > TaskCompleted)
- Input: common fields plus `task_id`, `task_subject`, optional `task_description`, `teammate_name`, `team_name` (deprecated), and `agent_id` (v2.1.290+). (hooks > TaskCompleted input)
- Exit 2: the task is not marked completed and the stderr text is fed back to the model as feedback. (hooks > TaskCompleted decision control)
- JSON `{"continue": false, "stopReason": "..."}`: stops the teammate entirely when a teammate finishing its turn triggered the event; ignored when `TaskUpdate` triggered it, where exit 2 still blocks. (hooks > TaskCompleted decision control)
- Prompt-based hook `ok: false` when a task is completed during a turn: reason returned to Claude as a tool error and the turn continues, regardless of `continueOnBlock`. When a teammate stopping triggered it, it behaves like TeammateIdle. (hooks > Prompt-based hooks, per-event behavior)
- Page's examples: TeammateIdle checks `./dist/output.js` exists and exits 2 with a stderr message; TaskCreated enforces a `[TICKET-n]` subject prefix; TaskCompleted runs `npm test` and exits 2 on failure. All write the message to stderr. (hooks > TeammateIdle; TaskCreated decision control; TaskCompleted decision control)
- agent-teams summarizes the same three: TeammateIdle "Exit with code 2 to send feedback and keep the teammate working"; TaskCreated "Exit with code 2 to prevent creation and send feedback"; TaskCompleted "Exit with code 2 to prevent completion and send feedback". (agent-teams > Enforce quality gates with hooks)

## Permissions

- Teammates start with the lead's permission mode, except `dontAsk`, which they do not inherit. If the lead runs with `--dangerously-skip-permissions`, all teammates do too. (agent-teams > Permissions)
- You can change one teammate's mode after spawning but cannot set per-teammate modes at spawn time. (agent-teams > Permissions; Limitations > Permissions set at spawn)
- An in-process teammate runs under the lead's live allow and deny rules, so `--disallowedTools`, `--tools` and `--allowedTools` apply. (agent-teams > Permissions)
- A split-pane teammate gets the lead's `--disallowedTools` and `--tools` (v2.1.309+); `--allowedTools` is not passed, so put pre-approvals under `permissions.allow` in `--settings`. (agent-teams > Permissions)
- Teammate permission prompts appear in the lead session and are approved there. You do not approve a teammate's plan; with the lead in plan mode, Claude Code approves it when the request arrives unless the teammate's mode was changed. (agent-teams > Permissions; Have teammates plan before implementing)
- If you put a running in-process teammate in plan mode with Shift+Tab, the lead reviews that plan instead. (agent-teams > Have teammates plan before implementing)
- Pre-approve common operations in permission settings before spawning to cut prompts. (agent-teams > Troubleshooting > Too many permission prompts)
- A `SendMessage` message is told to the receiver as coming from another Claude session, not from you. A teammate cannot approve a prompt or give consent on your behalf, and a denied teammate cannot relay the request to another teammate to bypass the check. The same applies to messages from your other Claude Code sessions. (agent-teams > Permissions > Messages between agents)
- In auto mode the classifier treats a relayed approval claim as untrusted and reviews each message before delivery; a blocked message never reaches the recipient. (agent-teams > Permissions > Messages between agents)

## Limits

- No session resumption with in-process teammates: `/resume` and `/rewind` do not restore them; tell the lead to spawn new ones. (agent-teams > Limitations)
- Task status can lag; shutdown can be slow; one team per session; no nested teams; lead is fixed; permissions set at spawn. (agent-teams > Limitations)
- No background subagents from in-process teammates. (agent-teams > Limitations)
- Split panes are not supported in VS Code's integrated terminal, Windows Terminal or Ghostty. (agent-teams > Limitations)
- No hard limit on teammate count. (agent-teams > Best practices > Choose an appropriate team size)
- Start with 3-5 teammates; three focused ones often outperform five scattered ones; scale up only when work benefits from simultaneity. (agent-teams > Best practices > Choose an appropriate team size)
- Two teammates editing the same file leads to overwrites; give each teammate a different set of files. (agent-teams > Best practices > Avoid file conflicts)
- Sometimes the lead starts implementing instead of waiting; tell it "Wait for your teammates to complete their tasks before proceeding". (agent-teams > Best practices > Wait for teammates to finish)
- The lead can decide the team is finished before all tasks are; tell it to keep going. Teammates may stop after errors; give instructions or spawn a replacement. (agent-teams > Troubleshooting > Agents stopping early)
- Letting a team run unattended too long increases the risk of wasted effort; check in and steer. (agent-teams > Best practices > Monitor and steer)
- If new, start with research and review rather than parallel implementation. (agent-teams > Best practices > Start with research and review)
- Teammates need task-specific detail in the spawn prompt because they do not inherit the lead's conversation. (agent-teams > Best practices > Give teammates enough context)
- Agent teams suit parallel exploration; for sequential tasks, same-file edits or many dependencies a single session or subagents are more effective. (agent-teams > When to use agent teams)

## Costs and token use

- Agent teams add coordination overhead and use significantly more tokens than a single session; each teammate is a separate Claude instance and usage scales with the number of active teammates. (agent-teams > When to use agent teams; Token usage)
- Subagents cost less because results are summarized back; teams cost more. (agent-teams > Compare with subagents)
- An in-process teammate's cache holds five minutes by default, including on a subscription; set `subagentPromptCacheTtl` to `1h` to keep it an hour, at a higher cache-write rate. (agent-teams > Token usage)
- Cost advice: use Sonnet for teammates; keep teams small; keep spawn prompts focused; shut teammates down when done, since each active one keeps consuming tokens until it exits or the session ends. (costs > Agent team token costs)
- "Agent teams use approximately 7x more tokens than standard sessions when teammates run in plan mode." (costs > Reduce token usage > Manage agent team costs)
- Each active teammate keeps consuming tokens until it exits. (costs > Why usage climbs in a long session)

## Differences from the lead's FACTS block

| # | FACT | Verdict | Evidence |
|---|---|---|---|
| 1a | Experimental, off by default; env var in settings.json "env" or shell | CONFIRMED | "Agent teams are experimental and disabled by default. Enable them by setting `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in your settings.json or environment." (agent-teams > top warning) |
| 1b | Interactive sessions only; -p / SDK spawn no teammates | CONFIRMED | "In non-interactive mode with the `-p` flag, including Agent SDK sessions, Claude doesn't spawn teammates" (agent-teams > Enable agent teams) |
| 2a | Lead is this session, fixed for life | CONFIRMED | "Lead is fixed: the main session is the lead for its lifetime." (agent-teams > Limitations) |
| 2b | Teammates are separate sessions with own context | CONFIRMED | "Separate Claude Code instances", "each in its own context window" (agent-teams > Architecture; intro) |
| 2c | Task list pending / in progress / completed, with dependencies; self-claim or lead-assigns | CONFIRMED, with a condition | Only for "agents that have the Task tools"; agents without them "coordinate through messages instead" (agent-teams > Assign and claim tasks). |
| 2d | Mailbox: SendMessage by name; one message per recipient | CONFIRMED | "send a message to one specific teammate by name. To reach everyone, send one message per recipient." (agent-teams > Context and communication). Mailbox is a per-agent JSON file under `~/.claude/teams/` (Architecture) |
| 3 | Teammates load CLAUDE.md, MCP servers, skills, NOT the lead's conversation | CONFIRMED | "a teammate loads ... CLAUDE.md, MCP servers, and skills ... The lead's conversation history does not carry over." (agent-teams > Context and communication). Caveat: a definition's own `skills` field is not applied (Use subagent definitions for teammates) |
| 4a | In-process default | CONFIRMED | "The default is `\"in-process\"`." (agent-teams > Choose a display mode) |
| 4b | Split panes need tmux or iTerm2, NOT supported in VS Code integrated terminal | CONFIRMED, wider | "Split-pane mode isn't supported in VS Code's integrated terminal, Windows Terminal, or Ghostty." Windows Terminal and Ghostty are also unsupported (agent-teams > Limitations) |
| 4c | Arrow keys + Enter open a teammate, x stops it, Ctrl+T toggles the task list | CONFIRMED | "Press `x` on a selected teammate to stop it. Press Ctrl+T to toggle the task list." (agent-teams > Talk to teammates directly). Escape also clears selection / interrupts a viewed teammate's turn |
| 5 | Model named in the spawn prompt | CONFIRMED | First in the order: "The model your spawn prompt names for that teammate." (agent-teams > Specify teammates and models) |
| 6a | Subagent definitions in `.claude/agents/<name>.md` can be spawned as teammates | CONFIRMED | Definitions from "project, user, managed, or plugin subagent scope" can be named (agent-teams > Use subagent definitions for teammates). The `.claude/agents/` path is from sub-agents > Choose the subagent scope; the `<name>.md` filename pattern is not stated on the pages read ("The filename doesn't have to match" the `name`, sub-agents > Supported frontmatter fields) |
| 6b | Project definitions re-applied only if the folder is trusted | CONFIRMED, narrower | Applies to "a teammate it brings back" (messaged after it stopped), and the folder holding the agent file must be trusted; "Trusting a parent folder doesn't count." (agent-teams > Use subagent definitions for teammates). The page does not say it governs a first-time spawn |
| 7a | Hooks TeammateIdle, TaskCreated, TaskCompleted | CONFIRMED | (agent-teams > Enforce quality gates with hooks; hooks > lifecycle table) |
| 7b | Exit code 2 blocks and sends feedback | CONFIRMED | Exit 2 returns stderr as feedback on all three. TaskCreated deletes the task; TaskCompleted keeps it uncompleted; TeammateIdle keeps the teammate working. Other non-zero codes do NOT block (hooks > Exit code output) |
| 8a | Teammate permission prompts bubble to the lead | CONFIRMED | "Teammate permission prompts appear in the lead session" (agent-teams > Permissions) |
| 8b | A teammate message is never the user's approval | CONFIRMED | "A teammate can't approve a permission prompt or supply consent on your behalf" (agent-teams > Permissions > Messages between agents) |
| 9a | One team per session | CONFIRMED | (agent-teams > Limitations) |
| 9b | No nested teams | CONFIRMED | "teammates cannot spawn their own teammates" (agent-teams > Limitations) |
| 9c | /resume does not restore in-process teammates | CONFIRMED, wider | "`/resume` and `/rewind` do not restore in-process teammates." (agent-teams > Limitations) |
| 9d | Task status can lag | CONFIRMED | (agent-teams > Limitations) |
| 9e | Shutdown slow | CONFIRMED | "Shutdown can be slow" (agent-teams > Limitations) |
| 9f | Passing `isolation` makes a plain subagent, so worktrees are made by hand | PARTLY CONFIRMED / PARTLY NOT FOUND | Confirmed: a named Agent call that "passes `isolation` on the call itself" is not launched as a teammate (agent-teams > How Claude starts agent teams; sub-agents > Subagent names). The two pages read do not use the phrase "plain subagent" and do not say worktrees must be made by hand. They say the opposite direction too: frontmatter `isolation` "doesn't prevent" the teammate launch, and that teammate "runs in the main session's working directory" (sub-agents > Subagent names). The sub-agents Fork section was not in the captured text |
| 10a | 3-5 teammates | CONFIRMED | "Start with 3-5 teammates for most workflows." (agent-teams > Best practices) |
| 10b | 5-6 tasks each | CONFIRMED | "Having 5-6 tasks per teammate keeps everyone productive" (agent-teams > Best practices > Size tasks appropriately) |
| 10c | One owner per file | CONFIRMED | "Break the work so each teammate owns a different set of files." (agent-teams > Best practices > Avoid file conflicts) |
| 10d | Lead waits | CONFIRMED | "Wait for your teammates to complete their tasks before proceeding" (agent-teams > Best practices > Wait for teammates to finish) |
| 10e | Steer often | CONFIRMED | "Check in on teammates' progress, redirect approaches that aren't working" (agent-teams > Best practices > Monitor and steer) |

Facts on the pages that the FACTS block does not mention and that matter here:
- Enabling teams makes any subagent Claude names launch as a teammate, with no confirmation. (agent-teams > Enable agent teams; How Claude starts agent teams)
- Windows Terminal is unsupported for split panes, not only VS Code. (agent-teams > Limitations)
- Teammates start with the lead's permission mode (except `dontAsk`); `--dangerously-skip-permissions` is inherited by all. (agent-teams > Permissions)
- A teammate's model and fast mode are fixed at spawn. (agent-teams > Talk to teammates directly)
- Plan-mode teammates can use roughly 7x the tokens of a standard session. (costs > Manage agent team costs)
- Non-2 non-zero hook exits do not block. (hooks > Exit code output)
- No page text names a Windows-specific team setup; the "in-process only on Windows and VS Code" reading is an inference. (agent-teams > Choose a display mode; Limitations)
