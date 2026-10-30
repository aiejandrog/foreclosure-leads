# File owners for agent fixes

The fix-agent skill reads this before it edits anything. Its source is docs/agent-teams/PATTERNS.md §3. A fixer edits only the files listed for its role. For any other file it messages that file's owner with the exact change and waits.

| Role (`.claude/agents/`) | Owns |
|---|---|
| equity-dev | equity_state.py, diligence_flags.py, diligence_gate.py, records_liens.py, miami_judgment.py, judgment_money.py, the equity-bake lines in foreclosure_leads.make_tracker |
| callmode-dev | call_mode.py (NOT the no / dnc / notint outcome branches), CALL_SCRIPT_NEPQ.md |
| board-dev | tracker_template.html (NOT the owned suppression functions below), outcome_learn.py, reports/ |
| qa-gate | tests/AGENT-QA-REPORT.md, _e2ewiringtest.py, QA lines in run logs. No product code |
| fixer | Only the files named in its fix item, and only if the role that owns them is named in the spawn prompt |
| Lead | docs/agent-teams/DECISIONS-NEEDED.md (master list), PLAN, summaries, merges to main |

Anyone may add a new `_<name>test.py`.

## Nobody on a team edits these (report only)
- **Owned suppression surface** (CLAUDE.md): the optouts.json writers (optout_sync.py, cadence.py, replies.py), replies.is_stop_text / is_sms_stop, bounced_emails.json, the board's _optedOutIdentities / _isOptedOutPerson / _textContactBlocked / _workerEligible / _laneStats, cadence's pre-send sweep, outreach_email._load_optouts, _suppressiontest.py / _optouttest.py / _dnctest.py.
- **Stays and contact:** stay_gate.py, never_contact.json, bk_lookup.py. Also send_server.py, apart from a change that is entered in DECISIONS-NEEDED and carries LEGAL REVIEW.
- **Generated or private:** docs/index.html and docs/call (generated), and any data, cache, key or ledger file.
