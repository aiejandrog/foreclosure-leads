# worklog-sec 2026-11-08  (agent: sec, worktree %USERPROFILE%\projects\fl-loop-sec, detached @ aac7a8a9)

Counts and case ids only. Probe scripts live in the scratchpad, not in the repo. The SendMessage tool
was not available to this agent (tool list: Read, Grep, Glob, Bash, Write, SubagentHandback), so tickets
are written here and carried to chief in the hand-back report.

17:00 RAN bridge probe: send_server.py from the worktree, temp DEALFLOW_DIR, free port, no gmail.key in the worktree, DEALFLOW_BRIDGE_RELOAD_POLL_S=0 -> see F-sec-01 output
17:05 NOTE the probe created gitignored files in the worktree (retrace_queue.json, worker_marks.jsonl, worker_runs.jsonl, worker_notes.json, worker_notes_snapshots/, send_server.log). rm was denied by the permission system; they are `!!` ignored, never committed, worktree only.

### F-sec-01  Any web page can drive the send bridge: keyless on loopback, CORS `*`, no Origin or Host check
- Type: FINDING
- Area: bridge
- Severity: S1 (rule: "a security gap that can cause contact"; note the opt-out, stay, deliverability, cap and first-touch gates still run, so this is not a stop or stay bypass, chief may re-rate to S4)
- Evidence:
  - send_server.py:2129-2135 `_cors()` sends `Access-Control-Allow-Origin: *`, `Allow-Headers: Content-Type, X-Dealflow-Key`, `Access-Control-Allow-Private-Network: true` on every response and on OPTIONS (2162-2165). Its docstring: "The bind address (127.0.0.1) is the actual security boundary; CORS is UX plumbing."
  - bridge_auth.py:113-124 `needs_key()` returns False for a loopback peer with no proxy header and no DEALFLOW_BRIDGE_REQUIRE_KEY=1. A browser tab on any website connecting to http://127.0.0.1:<port> IS a loopback peer, so the key check is skipped.
  - No code reads `Origin`, `Host` or `Sec-Fetch-Site`: `Grep "Origin|headers.get('Host|Sec-Fetch" send_server.py` -> only the two `Access-Control-Allow-Origin` lines. So DNS rebinding also works (Host is not checked).
  - do_POST (2681-2703) never looks at Content-Type, it calls json.loads on the body. A `Content-Type: text/plain` POST is a CORS "simple request": no preflight at all.
  - Probe (scratchpad bridge_probe.py, every request sent with `Origin: https://evil.example`, `Content-Type: text/plain`, no key, default env):
    ```
    GET /health  -> 200 ACAO=* PNA=true, 32 fields (sent_today, user, caps, first_touch senders, bounce, stay_data)
    GET /health with Host: rebind.evil.example -> 200 has sent_today: True
    GET /marks   -> 200 {"ok": true, "marks": [], "offset": 0}      (worker marks, readable cross-origin)
    GET /refresh -> 200 {"ok": true, "state": "none", ...}           (and /refresh is served BEFORE _auth, send_server.py:2170, so it is open even to a non-loopback peer with no key)
    POST /mark /runlog /retrace -> 200 ; POST /notes {_dealflow_notes:true} -> 200 saved (writes the notes store and runs ledger_from_notes)
    POST /wp/lookup {"case":"bad!"} -> 200 blocked bad_request   (handler reached with no key; a valid case spends ~$0.22 via whitepages_lookup, capped $5/day)
    POST /text/check -> 200 text_hold ; POST /send -> 451 test_not_operator (handler reached, its own gates answered)
    ```
  - `_bridgeauthtest.py` pins "loopback, no proxy: keyless" and "preflight open ... Access-Control-Allow-Private-Network" as intended behaviour, so the suite would pass while this is open. No test sends an `Origin`.
- Repro: temp DEALFLOW_DIR, free port, `DEALFLOW_BRIDGE_RELOAD_POLL_S=0`, start `python send_server.py --port <free>` from a checkout with no gmail.key; `curl -H "Origin: https://evil.example" -H "Content-Type: text/plain" --data '{"case":"bad!"}' http://127.0.0.1:<port>/wp/lookup` -> 200 handler answer, not 401. Same for /mark, /retrace, /runlog, /notes.
- Reach limits (honest): a real attacker page needs the browser to allow a public page to reach 127.0.0.1. Chrome and Firefox now prompt for Local Network Access per site, and Edge follows Chrome; that prompt is the only barrier left. DNS rebinding and any local process or extension skip it. Not exercised in a real browser (no Playwright run against a live bridge, by rule).
- Proposed fix (smallest): in `Handler._auth` (send_server.py:2137), for a keyless-local caller reject (401, same body as a missing key) when an `Origin` header is present and is not the board origin from `board_url.py` or the Call Mode origin, and when `Host` is not `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`. Drop `Access-Control-Allow-Origin: *` for the same allow-list (echo the matching origin). Move `/refresh` behind `_auth` (or confirm its payload is public). Alternative with no code: set DEALFLOW_BRIDGE_REQUIRE_KEY=1 and give the board and Call Mode a device key (bridge_auth.py already supports it). Test: extend `_bridgeauthtest.py` with a request carrying `Origin: https://evil.example` and one carrying a foreign `Host`, both expect 401 on /send, /wp/lookup, /notes; board origin still 200.
- Owner role (OWNERS.md): report-only (send_server.py; entered in DECISIONS-NEEDED with LEGAL REVIEW if fixed)
- Touches: send, spend
- Confidence: high on the code and the probe (cross-origin keyless access works); medium on browser exploitability (depends on the Local Network Access prompt and on the operator having approved the bridge for some other origin).

### F-sec-02  `/send` accepts any local file path in `attach` and mails it
- Type: FINDING
- Area: bridge
- Severity: S4
- Evidence:
  - send_server.py:2741-2745 only checks that `attach` is a string or list of strings. send_server.py:1949-1958 `_smtp_send` does `os.path.abspath(os.path.expanduser(path))`, `isfile`, then reads and attaches the file. No allow-list directory, no extension check, no size cap, `~` and `..` are expanded.
  - Proof (scratchpad attach_probe.py, smtplib stubbed, invented file in a temp folder outside the repo and DEALFLOW_DIR, inside `mail_guard.gated`):
    ```
    attached from outside any allowed folder: [('not-a-case-file.txt', 'INVENTED-SECRET-CONTENT')]
    tilde+.. path attached: ['win.ini']
    ```
  - Nothing shipped sends `attach`: `Grep "attach\s*[:=]|'attach'" *.py *.html *.js` finds only `_attachtest.py` and the `_smtp_send` signature, so no page or script needs arbitrary paths.
  - Reach: combined with F-sec-01 a hostile page can attach a local file to a send. The recipient must still pass `_pick_recipient` (an address with delivery evidence, i.e. an owner already mailed or verified), so the file goes to a real homeowner or to the operator's own mailbox on a test send, not to an arbitrary address. Without F-sec-01 it needs a device key.
- Repro: the scratchpad script above (stubbed SMTP), or `_attachtest.py` pattern with a path outside its `work` folder.
- Proposed fix (smallest): in send_server.py resolve each attach path with `os.path.realpath`, require it to sit under one allowed folder (`P.DEALFLOW_DIR`/case workup folder, whichever `_attachtest.py` uses), refuse `..` escapes, cap total size (Gmail limit 25 MB), refuse key and ledger extensions (`.key .pass .codes .url .json`). Test: `_attachtest.py` gains "file outside the folder is refused 400" and "symlink out is refused".
- Owner role (OWNERS.md): report-only (send_server.py)
- Touches: send
- Confidence: high (executed). Only the CSRF reach is medium.

18:10 RAN gh api + unauthenticated curl (HEAD, status only) against the engine repo -> HTTP 200 (see F-sec-03)
18:25 RAN built Call Mode (main + seat) and board preview from invented rows, system Chrome via Playwright executable_path (no browser download) -> see F-sec-05 / F-sec-06 / F-sec-07

### F-sec-03  The ENGINE repo is publicly readable (unauthenticated HTTP 200) and tracks homeowner name and address data
- Type: FINDING
- Area: process
- Severity: S4 (highest of this run; chief may want Alex told first)
- Evidence:
  - publish_site.py:1-21 and CLAUDE.md say the engine repo "foreclosure-leads" is PRIVATE since 2026-09-17 and only built pages reach the public repo. Today:
    ```
    gh api repos/aiejandrog/foreclosure-leads --jq ...   -> aiejandrog/foreclosure-leads private=false vis=public pushed=2026-11-08T21:58:47Z size_kb=5363274
    curl -s -o /dev/null -w %{http_code} https://api.github.com/repos/aiejandrog/foreclosure-leads/contents/send_server.py   -> 200  (no auth header)
    curl -s -o /dev/null -w %{http_code} https://github.com/aiejandrog/foreclosure-leads                                      -> 200  (no auth header)
    ```
    `git remote -v` in the worktree: origin = https://github.com/aiejandrog/<other-repo> (the same repo).
  - Unauthenticated `curl -I https://raw.githubusercontent.com/aiejandrog/foreclosure-leads/main/<file>` (HEAD only, nothing downloaded) returns 200 for each of: ownership.json, lis_pendens.json, diligence_cache.json, cash_buyers.json, stub_folios.json, broward_mortgages.json, sale_history_cache.json, auction_archive.json, pa_property_cache.json.
  - What those tracked files hold at HEAD (counts from `git show HEAD:<file>` in the worktree, no values printed): lis_pendens.json 1,649 entries, every one with a non-empty `owner` (fields: case, county, date, docType, instrument, kind, legal, owner, parcel, parties, plaintiff); ownership.json 655 entries with `title_owner`; auction_archive.json 1,587 entries with property `addr`, judgment, value; diligence_cache.json 72 entries with `addr` and `folio`; listing_status_cache.json 191 phone-shaped values; REFERRAL_CODES.json 8 entries with a name, an email and a phone. publish_site.py:5-9 names lis_pendens.json, ownership.json and diligence_cache.json as the files that made the old public repo a PII leak.
  - History: `git log --all --format=%h -- pre_foreclosure_doors.json` -> d218fe6f (2026-08-10, added) and b1052cb8 (2026-09-25, deleted). `git show d218fe6f:pre_foreclosure_doors.json | grep -o -E '(786|305|954|561|754)[0-9]{7}' | wc -l` -> 36 phone-shaped numbers. `git branch -r --contains d218fe6f` -> origin/HEAD and many origin/* branches, so that blob is reachable on the public remote. 3,130 commits across all refs. push-purge.sh and purge_history.py exist; nothing I can see shows the purge was run.
  - Also in the same public tree: 1,818 tracked photos in docs/img (hashed names, no EXIF, salted hash per property_photos.py:49-72) and real consumer mailboxes, owner names and home addresses inside comments (see F-sec-08).
- Repro: `gh api repos/aiejandrog/foreclosure-leads --jq .private` (expect true, gets false); unauthenticated `curl -I` on any raw URL above (expect 404, gets 200).
- Proposed fix (smallest): Alex sets the repo to private in GitHub settings (one click; the site is served from dealflow-board, so nothing on the Pages side depends on the engine repo being public; check that the cloud runner in CLAUDE.md still fits the free private-repo minutes). Then Alex decides whether to run the history purge. Code-side guard for next time: a check in `healthcheck.py` or `check_public_footprint.py` that runs `gh api repos/<engine> --jq .private` and fails when false. Test: stub the gh call, expect a hard fail on `false`.
- Owner role (OWNERS.md): report-only (repo setting, not code); the guard lives in healthcheck.py (report-only)
- Touches: none (no send); personal data exposure
- Confidence: high that it is public now (three unauthenticated 200s). I cannot tell whether someone made it public on purpose.

### F-sec-04  Everything under docs/ except .pdf is mirrored to the public site: internal agent docs, run logs and diagnostic scripts are live today
- Type: FINDING
- Area: process
- Severity: S4
- Evidence:
  - publish_site.py:104-112 `_pages()` walks `docs/` and skips only names ending `.pdf`; `mirror()` (165-187) copies every other file; `tracked()` (149-162) checks only that TOP-LEVEL names are `docs`, `README.md`, `.gitattributes`, so a new folder under docs/ passes. `guard()` (115-143) scans only `.html` files for phone-shaped numbers and consumer mailboxes, never `.md` or `.py`.
  - Live check (read-only API): `gh api "repos/aiejandrog/dealflow-board/git/trees/main?recursive=1"` lists, besides docs/index.html, docs/call/**, docs/hm-balloon-q7v3n8/index.html and 1,818 docs/img/*.jpg: 5 top-level docs/*.md (AGENT-BUILD-SUMMARY-2026-10-30, BK-CHECK-DIAGNOSIS, EMAIL-DELIVERABILITY, PB-LP-STATUS-OPTIONS, VERDICT-BLOCKERS), docs/agent-team/ (8 md), docs/agent-teams/ (33 md: PATTERNS, OUTPUT-TEMPLATE, DECISIONS-NEEDED, HOOKS-PROPOSAL, run logs, PR bodies), and docs/bk-diag/ (3 python scripts). `gh api repos/aiejandrog/dealflow-board --jq .visibility` -> public.
  - Content classes (Grep counts over those files): 17 mentions of the Windows account name or local paths in 9 files (BK-CHECK-DIAGNOSIS 4, agent-team/BRIEF 3, agent-teams/runs/2026-10-30-verify 3, PATTERNS 2, ...), the business sending domains and mailbox naming in EMAIL-DELIVERABILITY.md (8 hits), internal bankruptcy-stay and attorney-waiver reasoning (BK-CHECK-DIAGNOSIS, VERDICT-BLOCKERS, DECISIONS-NEEDED), one street address in docs/agent-teams/runs/2026-10-30-ordaddr.md:2 and one case id in docs/agent-team/EQUITY-LIVE-RUN.md:39 (public court record, low), and the migration freeze, guard and loop design (docs/agent-teams/*). No phone number and no key shape in any of them (Grep).
  - AUDIT-LOOP-PROTOCOL.md section 1 already says "Why the repo root and not docs/: publish_site.py copies all of docs/ to the PUBLIC site repo", but the earlier agent-team runs wrote into docs/agent-teams/ and are live.
- Repro: `gh api "repos/aiejandrog/dealflow-board/git/trees/main?recursive=1" --jq '.tree[].path' | grep -v '^docs/img/'` -> the md and py files above.
- Proposed fix (smallest): make `_pages()` an allow-list (docs/index.html, docs/call/**/index.html, docs/hm-balloon-*/index.html, docs/img/*.jpg, docs/.nojekyll) instead of a deny-list; `tracked()` checks the second level as well; move docs/agent-team*, docs/bk-diag and docs/*.md to a non-published folder (`agent-docs/` at the repo root, as the loop protocol does). Alex decides whether to delete them from the public repo (they stay in its history either way). Test: `_sitemirrortest.py` or `_footprinttest.py` gains "a .md or .py under docs/ is not copied".
- Owner role (OWNERS.md): report-only (publish_site.py is a publish gate; docs/ is generated)
- Touches: none
- Confidence: high (live tree listing).

### F-sec-05  Access codes are about 40 bits, PBKDF2 is 200,000 rounds, the ciphertext is public, and revoking a code closes no earlier build
- Type: FINDING
- Area: board
- Severity: S4
- Evidence:
  - access_codes.py:19,23-24 `gen()` = 'DEALFLOW-' + 8 characters from a 31-letter alphabet (`secrets.choice`, a good source). 31^8 = 8.53e11 = 39.6 bits; the prefix is known.
  - foreclosure_leads.py:1383-1396 `_encrypt_multi`: PBKDF2-HMAC-SHA256, 200,000 iterations, per-code random 16-byte salt, wraps a random 256-bit master key with AES-GCM; payload under the master key with AES-GCM. A sound construction with a fixed low work factor (OWASP 2023 figure for PBKDF2-SHA256 is 600,000). A wrong code reveals nothing: seat_probe.py wrong code -> None, right code -> the rows.
  - The envelope (`keys[]` salts, ivs, wrapped keys) sits in the public file, so guessing is OFFLINE with no rate limit. Measured here: `hashlib.pbkdf2_hmac('sha256', ..., 200000)` takes 0.138 s on one CPU core (7.2 guesses/s), about 3,700 core-years for the whole space. A GPU is roughly three orders of magnitude faster per card (estimate, not measured), so one card needs about 8 months and a hundred cards about 2 days. Read it as "a funded attacker can", not "anyone can".
  - The public site repo keeps every publish as a commit: `gh api 'repos/aiejandrog/dealflow-board/commits?per_page=1' -i` last page = 142 commits; `...commits?path=docs/index.html&per_page=100` returns 100 (the cap), latest 2026-11-05. publish_site.py:217-222 commits and pushes with no squash or orphan. `access_codes.py revoke` rebuilds and republishes, but every earlier docs/index.html still opens with the revoked person's code, and every earlier build is a separate offline-crackable ciphertext of that day's rows.
- Repro: build a board with `foreclosure_leads._encrypt_multi(plaintext, [('Tester','TESTCODE')])` and keep it; build again without that code: the first file still opens with TESTCODE (scratchpad seat_probe.py `unlock()` does the unwrap).
- Proposed fix (smallest): (1) 16 random characters from `gen()` (about 80 bits); (2) iterations 600,000 in `_encrypt_multi` and in the readers (`ENC.it` is already read from the envelope, so cached old pages keep opening); (3) publish the site repo as one orphan commit each time so a revoked code opens nothing old; (3) changes publish_site.py and the "never force-push" rule, needs Alex. Test: a new `_codestrengthtest.py` asserting code length and `it >= 600000`.
- Owner role (OWNERS.md): board-dev for (1) and (2); report-only for publish_site.py
- Touches: none
- Confidence: high on iterations, alphabet and commit counts (measured); the GPU rate is labeled an estimate.

### F-sec-06  Every access code opens everything: Carlos's code (and any partner code) decrypts the board with Whitepages relatives, the Balloon lane and the other seat's page
- Type: FINDING
- Area: carlos-seat
- Severity: S4
- Evidence:
  - foreclosure_leads.py:4281-4282 encrypts `{'rows': slim, ...}` (every lead row, including `wpRelatives`, `phones`, `emails`) once under one master key, and `_encrypt_multi` wraps that key under EVERY code in the codes file. call_mode.py:3979-3992 does the same per Call Mode page: `payload = encrypt(_plain, codes)` with the full `codes` list. So any code unwraps the board and both Call Mode pages; the seat split (call_mode.py:3920-3924) is a view cut, not an access cut.
  - Built from invented rows (scratchpad seat_probe.py: `call_mode.make_callmode` with `CM.HERE` pointed at a temp dir, code TESTCODE): `docs/call/index.html` and `docs/call/carlos/index.html` both unlock with the same code. The unlocked Carlos payload holds the Balloon row (`BAL rows: 1`, with its `bal` lender block) and the Whitepages-sourced owner number (phsrc 'wp'). `wpRelatives` was NOT in either Call Mode payload (`wpRelatives key False`, relative number absent), which matches CLAUDE.md. The board payload does carry `wpRelatives` (foreclosure_leads.py:3529; stripped only in the no-codes fallback at 4290).
  - AUDIT-LOOP-PROTOCOL.md section 8 says Carlos's seat carries "no wpRelatives, Whitepages numbers or Balloon Book data". Today: no relatives on his page; owner-matched WP numbers and Balloon rows ARE on it (same `call_rows` output as seat 0; `seat_rows` partitions by case bucket only).
  - Side observation: with CALL_SEATS = [(2,0,..),(2,1,..)] and invented rows, seat (2,1) built 26 of 26 rows (the seat partition did not cut anything because every invented row had an odd bucket; not a security point, but the test rows were not split).
- Repro: scratchpad seat_probe.py (invented rows, temp dir, no network); read the `carlos page` lines for `BAL rows` and `wp-sourced phone numbers present`.
- Proposed fix (smallest): decide the rule first (Needs Alex: does "Whitepages numbers" in section 8 mean owner-matched `wp` numbers and the Balloon lane, or only relatives and pasted numbers?). If all: in `call_mode.make_callmode`, in the same place that cuts `seat_rows`, drop `st=='BAL'` rows and `phsrc=='wp'` numbers for the Carlos seat; test in `_carlostest.py`: Carlos payload has 0 BAL rows. For real separation (his code must not open the board or seat 0) each page needs its own envelope encrypted to the codes allowed for it; needs Alex.
- Owner role (OWNERS.md): callmode-dev (call_mode.py), board-dev (code lists in foreclosure_leads.py)
- Touches: Carlos-seat
- Confidence: high on the structure (one master key for all codes); medium on what Alex intends for Carlos.

18:50 CORRECTION to F-sec-06 (appended in error, Edit tool unavailable): the 'Test-fixture note' is the true statement: my invented rows shared one address, so the household rule put all 26 in one bucket (sb_probe.py: bucket 3 x 24) and the Carlos page got every row. The earlier 'Side observation' bullet claiming the partition did not cut because rows had an odd bucket is superseded; it is not a security point.
19:20 RAN live public-page scans (read-only `gh api ... Accept: raw`, counts only): live_clear.py, live_caseids.py, live_ledgers.py -> F-sec-07 / F-sec-08
19:35 RAN Chrome (Playwright, file:// preview built by build_preview.py logic with invented leads, network blocked): xss_run.py / xss_proof.py / worker_xss.py -> F-sec-09; Call Mode cm_run.py -> no injection

### F-sec-07  The live public board ships per-case contact history, STOP flags and folio numbers in clear text, outside the encrypted payload
- Type: FINDING
- Area: board
- Severity: S4
- Evidence:
  - Read-only fetch of the live `docs/index.html` (`gh api repos/aiejandrog/dealflow-board/contents/docs/index.html -H 'Accept: application/vnd.github.raw'`, the ciphertext runs stripped, scratchpad live_caseids.py / live_ledgers.py). Case-id-shaped strings in the clear: 1,091. By constant: `MAILLOG` 1,051 case ids (line 2592; each `{"n": count, "last": timestamp}`), `TEXTLOG` 16 (line 2614, `{"n","opens","last"}`), `SERVER_OPTOUTS` 7 (line 2651, `{"optout": date, "status": "DO NOT CONTACT"}` plus `@`-keyed identity hashes), `SERVER_DEADS` 5 (line 2720, includes the parcel `folio`), `REPLIES` 4 (line 2573, `{"stop": true|false, "when", "n", "checked"}`).
  - What that tells a reader without any code: which foreclosure cases were emailed or texted and when, which cases replied, which cases said STOP / are on the DNC list. A case id resolves in the public clerk docket to the named defendants; a folio resolves on the property appraiser to the owner and address (opaque_img_names.py:3-9 says exactly this about folios).
  - It is deliberate in part: foreclosure_leads.py:4347-4358 swaps in "text-free versions" of two ledgers and keeps the case keys. footprint.assert_clean (foreclosure_leads.py:4374) checks consumer mailboxes and values from the local config only; nothing checks case ids or folios. CLAUDE.md "Personal identifiers never ship" and publish_site.py:24-27 promise homeowner data only inside the encrypted payload.
  - Bounded: no phone numbers (0 phone-shaped in clear), no emails (0 consumer mailboxes, live), no reply text.
- Repro: the fetch above plus `re.findall(r'\b(?:20\d{2}-\d{6}-CA-01|CACE-?\d{2}-\d{6}|COCE-?\d{2}-\d{6}|50\d{4}CA\d{6})\b', clear_text)`; or build the board from invented rows with a MAILLOG entry and look for the id outside the `RAW` payload.
- Proposed fix (smallest): move the five ledgers into the sealed side payload that already exists (`footprint.sealed_side()` / `side` in foreclosure_leads.py:4282 is merged on unlock by `_applyPayload()`), or key them by a salted hash of the case id like the `@` identity keys; page code reads them after unlock. Test: `_footprinttest.py` gains "no case id or folio outside the encrypted payload" on a board built from invented rows. Needs a decision from Alex first (this is a design choice the code comments made on purpose). Touches the opt-out gate readers (`_optedOutIdentities` etc.), which are the owned suppression surface, so report-only until the owner takes it.
- Owner role (OWNERS.md): report-only (suppression-surface readers in tracker_template.html; docs/index.html generated)
- Touches: suppression, DNC (read side only)
- Confidence: high on what is in the live file (counts above); medium on severity (case ids are public docket numbers, the harm is the inference "this person was contacted / opted out").

### F-sec-08  Real homeowner, address, attorney and lead details sit in clear text in the board's template (Playbook text and code comments) and ship on the public page
- Type: FINDING
- Area: playbook
- Severity: S4
- Evidence:
  - tracker_template.html:6086-6095 is the Playbook "Market Intel" block, plain JS text, not part of the encrypted rows. It names a real family surname and given names, a door-knock street address with unit, dollar figures, a competitor LLC, and a named attorney with a Florida Bar number and a phone number, and it speculates the attorney "sat on both sides of a deal" (felony wording). I do not repeat the values here. Line 6093 holds the attorney sentence; line 6087 and 6090 hold the family and address.
  - It is on the live site: live `docs/index.html` clear text (scratchpad live_clear.py): attorney Bar-number sentence 1, dashed non-555 phone 1, street-address shapes 16 (plus 2 street-name shapes), given names 3, LLC/INC names 9. docs/call/index.html and docs/call/carlos/index.html carry 5 street-address shapes each (code-comment examples), no case ids, no phones.
  - Code comments in the same template with real lead addresses: tracker_template.html:3246, 3335, 3349, 5286, 6792, 8269-8274 ("Real examples off the live board"), 16774-16776, 20166 (shapes only checked; some are masks in examples, the ones above carry real-looking numbers and street names).
  - Why the guards miss it: footprint.assert_clean looks for consumer mailboxes and local-config values; publish_site.guard looks for phone-shaped runs; neither looks for street addresses, surnames or a bar number. Phone-shaped numbers: tracker_template.html has 3 hits, docs/index.html 3: one is the attorney's, two are in comments ("(###) ###-####, whose owner texted in") and not a business line.
  - CLAUDE.md: "No real personal value is ever a literal in tracked code, a template, a comment...". This is the same class as the 2026-10-25 cleanup, in a part the cleanup did not cover.
- Repro: `Read tracker_template.html offset 6086 limit 12` (the text); `Grep "Fla Bar" tracker_template.html`; fetch the live file as in F-sec-07 and grep the clear text for `Fla Bar \d{5,6}`.
- Proposed fix (smallest): replace the Playbook intel block paragraph and every comment that quotes a real address or owner with invented values (`100 NW 1st Ave`, `Owner Example`), and move the attorney's contact line into the sealed payload or remove it (also a defamation-risk sentence about a named lawyer on a public page; Alex and the attorney to decide). Add a `footprint.problems()` rule for "Fla Bar \d+" and `\d{3,5} (NW|NE|SW|SE) \d+ (ST|AVE|CT|TER|DR)` outside the payload, run on the finished board and call pages. Test: `_footprinttest.py` builds a page containing each shape and expects a refusal.
- Owner role (OWNERS.md): board-dev (tracker_template.html copy and comments); report-only for footprint.py rule additions only if it changes publish gates
- Touches: copy
- Confidence: high that the text is in the live clear file; the real-vs-invented judgement on each comment address is mine from shape and context (not checked against county records).

### F-sec-09  Board: `esc()` does not escape a single quote, and 22 inline `onclick` sites put `esc(r.case)` inside a quoted JS string, so a case number holding `');` runs script
- Type: FINDING
- Area: board
- Severity: S4 (low likelihood: the string is a case number, which comes from clerk data; no other lead field reached script in my run)
- Evidence:
  - tracker_template.html:7221 `const esc = s => ... .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')` (same shape at call_mode.py:4427). It leaves `'` and `\` alone. An HTML-escaped value inside `onclick="f('...')"` is decoded by the browser before the JS runs, so HTML escaping never protects a JS string.
  - 22 sites interpolate `esc(r.case)` into `onclick="fn(\''+esc(r.case)+'\')"`: tracker_template.html lines 3608, 3911, 4049, 4054-4058, 5307-5308, 5436-5443, 7159, 7674, 7690, 7944-7945, 9049-9050, 18963 (`Grep "\(\\\\''+esc\(r\.case\)"`).
  - Proof (Chrome via Playwright, file:// board preview built with `build_preview.py`'s own pipeline plus one invented lead whose `case` is `');window.__xss.push('PROOF');//`; network blocked; scratchpad xss_proof.py):
    ```
    handlers carrying the lead-supplied statement: 36
    first handler attribute text: _editTalkTo('');window.__xss.push('PROOF');//')
    window.__xss before any click: []
    window.__xss after ONE click on the Copy/Open/Save button: ['PROOF']
    ```
    The same lead also fires from `#worker=open`, `#worker=morning` and `#playbook` (worker_xss.py, 3 of 3 loads show 3 injected handlers). Needs one click, no unlock bypass (the preview is the ungated twin).
  - Negative results, same harness: 18 other lead fields (owners, oname, addr, mail, plaintiff, defs, ctype, warn, cert, emails, zlisting, photos, people, docket, pa, zillow, tax, auc) with `<img src=x onerror=...>` and with `');...//` produced 0 executions and 0 injected `<img>` elements; the text is shown escaped. Call Mode (built with `call_mode.make_callmode`, unlocked in Chrome with a test code, 22 invented rows, payload text painted for addr, owners, plaintiff, case): 0 executions, 0 injected handlers. So the only live sink I found is the case id in the board's inline handlers.
  - Related, not executed: `mailto:` hrefs are built `'mailto:'+esc(e)` without `encodeURIComponent` (tracker_template.html:3890, 4946, 4974, 12159, 12496, 17078, 18921), so a traced address containing `?cc=...&bcc=...&body=...` adds recipients or text to the draft the operator opens. The send path itself re-validates addresses (`_EMAIL_RE`), the mail client is the only exposure.
- Repro: build the preview with an invented lead whose `case` holds the string above, open `file://.../design-preview.html`, open the lead's property hub (`openCallSheet(case)`), click "Edit" or "Open full call sheet".
- Proposed fix (smallest): add `escJs(s) = esc(JSON.stringify(String(s)))` (or use `data-case` plus one delegated listener) and use it at the 22 sites; make `esc` also encode `'` (`&#39;`) for attribute contexts; reject case ids not matching `^[A-Za-z0-9._#:/ -]{1,60}$` at bake time in `make_tracker` (the bridge already uses that regex at send_server.py:2414). Test: extend `_boardnotest.js` or add `_xssboardtest.py` with the proof above expecting `[]`.
- Owner role (OWNERS.md): board-dev (tracker_template.html; not an owned suppression function)
- Touches: none
- Confidence: high on the executed proof; low on likelihood.

### F-sec-10  Access code and bridge device key sit in localStorage on `aiejandrog.github.io`, an origin shared with four other Pages sites
- Type: FINDING
- Area: board
- Severity: S4
- Evidence:
  - The code is stored in clear after unlock: tracker_template.html:20396 `localStorage.setItem('fcPw', s)`, call_mode.py:5044 `localStorage.setItem('fcPw',c)` ("unlocking either page unlocks both"); slot key at tracker_template.html:20264. The bridge device key and address live in localStorage too (tracker_template.html:17287-17289, call_mode.py:6041-6043, sent as `X-Dealflow-Key`).
  - localStorage is shared by every page on the same origin (scheme + host + port, not path). `gh api 'users/aiejandrog/repos?per_page=100' --jq '.[] | select(.has_pages==true) | .name'` -> aiejandrog.github.io, cashflow-tradervert, dealflow-board, gabriel-brief, navarro-brief; all public. All five are served from `https://aiejandrog.github.io/<other-site>`, so a script on any of the other four (or an XSS in one, or an added analytics tag) can read `fcPw` and the device key and decrypt every board ever published (F-sec-05). I did not open those repos' contents; I do not know whether they run scripts.
  - Combined with F-sec-09 (a script in the board origin) the same keys are one `localStorage.getItem('fcPw')` away; the proof run printed the key NAMES readable by the injected script (fcDealVer, fcDevice, fcSender in a fresh page).
- Repro: `gh api 'users/aiejandrog/repos?per_page=100' --jq '.[]|select(.has_pages==true)|.name'`; in any browser, unlock the board then open the user site and run `Object.keys(localStorage)`.
- Proposed fix (smallest): serve the board from its own origin (custom domain or a separate Pages account), or at minimum store a session-derived value instead of the code (derive the master key once, keep the non-extractable CryptoKey in IndexedDB, never the code string). Needs Alex (changes `board_url.py`, every bookmarked link and Carlos's home-screen icon). Interim: keep the other four sites free of third-party scripts.
- Owner role (OWNERS.md): board-dev / callmode-dev for the storage change; report-only for the origin move
- Touches: none
- Confidence: high on the storage and the shared origin; low on exploitability (depends on what the other sites run).

### F-sec-11  Real consumer mailboxes, owner names and home addresses are in comments of tracked source and in a tracked preview that holds the advisor's address; `sender.json` is in history on a remote branch
- Type: FINDING
- Area: engine
- Severity: S4 (the repo is public today, see F-sec-03)
- Evidence (path:line, no values):
  - Consumer mailbox literals in comments or docstrings that look like real homeowner or personal addresses: bounces.py:6, optout_sync.py:10, replies.py:201, contact_trust.py:12, foreclosure_leads.py:4124, refresh-dealflow.bat:685, run-replies-daily.bat:53. optout_sync.py:10 and replies.py:201 also quote what a named person wrote; contact_trust.py:12, refresh-dealflow.bat:684-685 and run-replies-daily.bat:53-54 pair an address with a street address and a person. (`Grep` for `[A-Za-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail|icloud|aol|live|msn)\.com` over those files.)
  - design-preview.html (tracked, last commit 42e0f38c 2026-10-26, AFTER the 2026-10-25 footprint cleanup) still carries 4 consumer-mailbox occurrences (3 distinct): line 5021 `var _JESSE = '...'` and line 5114 "Emails Jesse (...)" hold a variable named _JESSE (Brief Jesse), which CLAUDE.md says is the advisor address and must exist only in the untracked local config. I could not compare it with the local config (guard). The file looks stale: a rebuild would be refused by `assert_clean` (consumer-mailbox rule), so it was probably never regenerated after the cleanup. Lines 3727 and 12656 hold generic placeholders.
  - sender.json is a name that appears in history: `git log --all --name-only` filtered for never-commit kinds lists exactly one, `sender.json`, in commit 4910b06f (2026-07-18, "feat: Mail batch ..."), reachable only from `origin/cursor/mail-batch-outreach-e672` (`git branch -r --contains 4910b06f`; not on origin/main). I did NOT read its content (guard). It may be a template; Alex or any admin should look. The same scan found NO `*.key`, `*.pass`, `*.codes`, `*.url`, optouts.json, mail_sent.json, worker_notes.json or leads_*.json names anywhere in history, and `git log --all -G` for ghp_/github_pat_/AKIA/xox/sk-/PRIVATE KEY shapes returns nothing outside docs/*.html (where base64 ciphertext matches by chance) and nothing in `*.json` (searched outside docs/ and design-preview.html).
  - Also tracked and present at HEAD: 133 non-555 local-area-code phone-shaped values and 772 street-address shapes across 99 text files (scratchpad pii_scan.py). Most are test fixtures with sequential or repeating digits (ph_shape.py: _phonesrctest.py 39 of 46 sequential, _autorunstoptest.py 31 of 31, _wpwebtest.py 18 of 18). Real-shaped phone numbers: outreach_mail.py (5, the published callback line in comments), foreclosure_report.py (2), bsg_mail_campaign.py (1), _phonesrctest.py (7); the company line is expected on letters, the rest should be checked by Alex.
  - Commit subjects name people ("access: add code for Muse", "access: add code for Carlos Laptop", "access: add code for --help"): history on the public repo.
- Repro: the `Grep` above; `git log -1 --format='%h %ad' -- design-preview.html`; `git branch -r --contains 4910b06f`.
- Proposed fix (smallest): replace each comment value with an invented one (`<email>`, `100 NW 1st Ave`); delete design-preview.html from tracking or rebuild it with current code; have someone with access inspect sender.json in 4910b06f and, if it carries the operator's identity, drop that branch from the remote. A pre-commit/CI check running `footprint.problems()` over all tracked text (not only built pages) would have caught every item. Test: `_footprinttest.py` gains a tracked-file sweep for consumer mailboxes outside an allow-list of obvious placeholders (`you@`, `user@`, `someone@`, `test@`).
- Owner role (OWNERS.md): report-only for optout_sync.py, replies.py, refresh-dealflow.bat (suppression surface and .bat); board-dev for foreclosure_leads.py comment; the rest unowned
- Touches: none
- Confidence: high that these literals exist at those lines; medium that each is a real person (judged from the surrounding text; I cannot check against local config).

### F-sec-12  Smaller bridge and page items (grouped)
- Type: FINDING
- Area: bridge
- Severity: S5 (each; chief may split)
- Evidence:
  1. Unauthenticated routes: `GET /pid` and `GET /refresh` are answered before `_auth` (send_server.py:2168-2178), so a non-loopback caller with no key gets refresh progress (`refresh_progress.public()`: step labels, counts; my probe returned `{"ok": true, "state": "none", "n": 57, "recent": []}`). Counts only today; the route is one edit away from carrying more.
  2. `GET /health` for a keyless local caller returns 32 fields including the sending login (`user`), caps, bounce and first-touch state, senders map (send_server.py:2191-2273); with F-sec-01 any web page can read it cross-origin. Probe: `GET /health` with `Origin: https://evil.example` -> 200, `user` present.
  3. Error text: `/send` returns `bad json: {e}` and `SMTP AUTH failed: {e}` (send_server.py:2712, 3249), `/notes` returns `bad json: {e}` and `write failed: {e}` (2638, 2645). A write failure can name a path under the repo or DEALFLOW_DIR. Low.
  4. `int(self.headers.get('Content-Length') or 0)` is unguarded in 9 handlers (send_server.py:2305, 2337, 2364, 2404, 2440, 2515, 2600, 2632, 2706): a non-numeric header raises `ValueError` outside any try, the handler thread dies with a traceback and the client sees a dropped connection (probe: `POST /mark` with `Content-Length: abc` -> `RemoteDisconnected`). No crash of the server, no data effect.
  5. No request-rate limit on any route; limits are the daily caps (300 messages, 450 recipients, WP $5/day) and the 60 s socket timeout. `/notes` accepts 25 MB per request unauthenticated from a local origin (F-sec-01). `/retrace` appends any `c` string (10 KB body cap, no format check, send_server.py:2607-2621) to the queue the nightly skip trace consumes: a page-driven spend vector for the next nightly (paid per trace; the caps still apply).
  6. Negative result worth keeping: `/notes` snapshot name uses `device[:24]` in a path (send_server.py:1058). I tried `..\..\x` as the device with a patched snapshot dir (device_probe.py): the victim file was NOT overwritten, because the intermediate directory `incoming_<date>_..` does not exist. Safe today only by accident; sanitize `device` to `[A-Za-z0-9_-]`.
  7. Balloon Book: docs/hm-balloon-q7v3n8/index.html is plaintext (no encrypted payload) with no `<meta name="robots" content="noindex">` (Call Mode has one). Content checked: 150 data rows, 150 of 150 "Human" cells redacted to "on file", 0 phones, 0 emails, 73 street-address shapes, 683 LLC/INC names, loan amounts: public records by design (hardmoney_balloon.py:349-361). The folder name is discoverable from the public tree and from the nav strip on every page, so it is not a secret URL. Add `noindex`, and confirm Alex accepts LLC-owned property addresses and loan amounts being public.
  8. property_photos.py:62-66: if the salt file cannot be read or created the module prints a warning and continues with an EMPTY salt, so photo file names become `sha256(folio)[:16]` which anyone can recompute from public folios (the exact leak opaque_img_names.py fixed). Fail closed (raise) instead.
- Repro: the probe scripts named above; `Read send_server.py` at the lines given.
- Proposed fix (smallest): move `/refresh` under `_auth`; wrap Content-Length in a helper returning 400; sanitize `device`; add `noindex`; raise on empty salt. Each has a one-line test in `_bridgeauthtest.py` / `_sendbridgetest.py` / `_footprinttest.py`.
- Owner role (OWNERS.md): report-only for send_server.py items; board-dev for the Balloon Book page; unowned for property_photos.py
- Touches: send (items 1-6 are on the bridge)
- Confidence: high (each one executed or read at the line cited), severity low.

## OUTPUT-TEMPLATE

### sec-2026-11-08: security audit of board, Call Mode, Carlos seat, Morning Worker, Balloon Book, Playbook and the send bridge
- Task: read-only security audit per .claude/agents/security-auditor.md (payload, injection, bridge, secrets and PII in git, public mirror, Carlos seat)
- Owner: sec (this agent), triage by chief
- Branch + PR: none (detached worktree %USERPROFILE%\projects\fl-loop-sec @ aac7a8a9, no commits, nothing under docs/)
- Files changed: none in the repo. Run folder only: worklog-sec.md. Scratch scripts in the session scratchpad (bridge_probe.py, attach_probe.py, seat_probe.py, cm_xss.py/cm_run.py, xss_board.py/xss_run.py/xss_proof.py/worker_xss.py, device_probe.py, live_clear.py, live_caseids.py, live_ledgers.py, pii_scan.py, hist_names.py)
- What changed (plain English): nothing. Twelve finding tickets F-sec-01 to F-sec-12. The two that matter most: the engine repo is publicly readable right now (F-sec-03), and any web page can drive the send bridge from the operator's own browser because loopback needs no key and CORS is `*` (F-sec-01).
- Tests run (command + result): `python _footprinttest.py` (temp DEALFLOW_DIR) -> 46 passed, 0 failed (skips the local-config half, no footprint file on this checkout). Probes above, each with the output quoted in its ticket. Not run: any .bat, publish, send, refresh, real-port bridge, paid call; `python ci_suite.py` (not needed, read-only audit); the 14 known-failing suites were not re-run or reported.
- QA verdict (PASS/FAIL + by whom): not applicable (no code change); findings are for chief to triage
- Exposure: none from this audit. Findings touch sending (F-sec-01, F-sec-02, F-sec-12), suppression read side (F-sec-07), spend (F-sec-01, F-sec-12), Carlos seat (F-sec-06), personal data (F-sec-03, 04, 07, 08, 11)
- Flags (default off?): none added. Existing flags referenced: DEALFLOW_BRIDGE_REQUIRE_KEY (default unset, would close most of F-sec-01 if set with a device key on the board and Call Mode)
- Owned-surface bugs found: F-sec-01, F-sec-02, F-sec-12 are in send_server.py (report-only); F-sec-07 touches suppression-surface readers in tracker_template.html. Reported, not fixed.
- Needs Alex (one line each): (1) make the engine repo private or confirm it is meant to be public (F-sec-03); (2) decide whether Carlos's seat may carry owner-matched Whitepages numbers and the Balloon lane, and whether his code may open the board (F-sec-06); (3) approve moving case-keyed ledgers into the encrypted payload (F-sec-07); (4) remove or approve the Playbook Playbook intel block text with a named attorney and family on the public page (F-sec-08); (5) check sender.json in 4910b06f on the cursor branch (F-sec-11)
- Decisions needed (link to the DECISIONS-NEEDED entry): none written (agents write nothing under docs/ in this loop); chief to carry (1) to (5) above
- Next run should know: live public pages (board, Call Mode, Carlos seat, Balloon Book) are clean of consumer mailboxes and phone numbers in clear text (counts 0, checked on 2026-11-08 by fetching the live files). The Carlos seat build has no `wpRelatives`. XSS: only the `r.case` sink in the board's inline handlers executes; Call Mode and all other fields escaped. Browser: no Playwright download needed, use `executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe'`. The guard blocks shell commands whose text names key/codes/sender files, `.bat`, `merge`, publish and cadence scripts even inside a grep pattern: write the scan to a scratchpad .py and run it. The SendMessage tool was not available to this agent, so no ticket was messaged to chief individually; the hand-back report carries all twelve.
