# Wiring map: board -> Call Mode -> Morning Worker -> send bridge -> disk -> board

Commit edb6493 (origin/main, 2026-10-31). Line numbers are on this commit. The runnable proof is
`python _e2ewiringtest.py` (about 5 s, offline, free port, temp dir). It drives hops 1, 2, 3, 5, 6 and 7
for real and marks every hop that is broken today as `KNOWN GAP (<decision>)`. When a gap is fixed, its
check fails with "gap closed" so this map and the assertion get updated together.

Cross-checked against `docs/agent-teams/runs/2026-10-30-callfix.md` (Call Mode audit table). Path A there =
Call Mode -> localStorage -> Supabase team sync -> board `_mergeLead`; Path B = Call Mode -> bridge.
Everything below is Path B plus what the board and the worker add. Not covered by the test: Supabase team
sync itself (`queueSync`, `syncPush`), real browsers, `/wp/lookup` with a key (paid), `/send` success.

## The hops

| # | Hop | Function (file:line) | Endpoint / store | Fields that must survive | Where data can silently drop |
|---|---|---|---|---|---|
| 1 | Lead -> Call Mode row | `call_mode.call_rows` 2102, `_eqc_slim` 1775 (key `eqc` at 2496), `coverage_rows` 3032, `mark_lookup` 3134 ("Find a number" list), `build_html` 3378 | built page `docs/call` | `c`, `p` phones, `eqc{st,lb,f,q,j,js,li,b,bs,n}`, `eqv`, `pk`, `pcs`, `bq`, `lk`, `wq` | A lead the diligence gate holds is not a row (printed, not shipped). A lead with no dialable number only appears if it is in `lookup` (`lk`). Rows over `cap` 400 are cut. Net equity `n` is dropped unless `q` (may quote). |
| 2a | Outcome buttons | `logOutcome` 9230, `afterCall` 8936 | `notes[c]` in localStorage `fcLeadNotes` | `touches[]{d,ts,tsu,ch,out,by}`, `dials[]{oc,ph4,tsd}`, `cooldownH`, `status`, `no`, `badph`, `wrongown` | `noanswer` / `voicemail` leave `status` empty (see hop 7 funnel). `touches` is deduped per day+outcome (the dial row is not). |
| 2b | After the yes | `ayApply` 8847, `ayBookedUnverified` 8879, `eqFact` 8791 | same note | `status` (= stage), `stageAt{}`, `nextStep{date,what}`, `next`, `equityVerified{ok,source,d}`, `keepOrSell`, `afterYesAt`, `apptUnverified` | GATE: Offer made / Under contract / Assigned / CLOSED are refused unless `eqFact(r)` (baked `eqc.f`); ticking the box does not unlock. A refusal writes nothing and toasts. DNC / Dead take nothing. |
| 2c | Complete sheet | `completeThen` 6837, `screenComplete` 6844, `cmpLog` 6821 | same note | `touches[]{src:'manual', mk, ch, out, by, who, note}` | No dial, no status, no stage, no ledger post by design. See D18 and the funnel rows below. A chip already logged today is locked, written once. |
| 2d | Find a number / paste | `wpLookup` 6577, `lkSave` 6614, `pasteCall` 7053 | same note; `/wp/lookup` for the paid button | `phone`, `wpm[]`, `wpl` | `wpm` reaches disk only as part of a whole new note (see hop 5). `wpl` is local + Supabase only. |
| 2e | Text composer | `_openText` (inside `afterCall`), `textPreflight` 5973, `callTextLedger` 5942, `pollTextHold` 6002 | `POST /text/check`, `POST /text` | `case,pkey,to,owner,stage,tone,lang,body_len,confirmed,device` | `callTextLedger` swallows its own failure (`.catch(function(){})`): a refused `/text` row is invisible to the caller. Hand-logged "Texted" in Complete never posts `/text` (D18). |
| 2f | Paste an email | `mailViaBridge` 6921 | `POST /send` | `meta{c,owner,addr,lang,pcs}`, `to,subj,body` | Only `ok:true` counts; the touch and `wpe` are written after. |
| 3 | Page -> bridge transport | `bridgeFetch` 5866 (block `BRIDGE-KEY-START`, identical in `tracker_template.html` 17268), `saveNotes` 4625, `callNotesPush` 5929 | all bridge routes, `X-Dealflow-Key` from localStorage `dfBridgeKey`, base from `dfBridgeBase` | `/notes` body = `{_dealflow_notes:1, exported, device, notes}` only | No key + non-loopback = 401 `device_key`, shown once as a plain toast. `callNotesPush` is debounced 4 s (`callNotesPushSoon` 5928); a tab closed inside that window loses the push (the note is still in localStorage). A fetch rejection is swallowed. `workerLog` / `sentArchive` are deliberately left out (server reads `[]` as a deletion). |
| 4 | Board -> bridge | `save` 17201, `_notesBridgePayload` 17332, `notesBridgePush` 17338 (5 s debounce 17408), `_textLedgerPost` 6572 | `/notes`, `/text` | same plus `workerLog`, `sentArchive` | Warned in `#nbwarn`; the only silent case is a closed tab inside the 5 s window. |
| 5 | Bridge write | `send_server._handle_notes` 2588 -> `_write_notes` 979 -> `_merge_payload` 919 -> `_merge_note` 836; `ledger_from_notes` (optout_sync) | `worker_notes.json` (+ `worker_notes_snapshots/`), `optouts.json` | additive `touches/dials/optlog`, union `badph/dntph`, sticky `optout/wrongown`, status rank, after-the-yes unit by larger `afterYesAt` | See "Known silent drops". `ledger_from_notes` failure is only logged; the response still says `ok:true` (nightly sweep is the backstop). |
| 6 | Bridge auth | `_auth` 2101, `_cors` 2093, `do_POST` 2645 (route order: `/notes`, `/marks/consumed`, `/mark`, `/runlog`, `/retrace`, `/wp/lookup`, `/text/check`, `/text`, `/send`) | every POST; `/health` is `open_ok` | header `X-Dealflow-Key` | `/pid` and `/refresh` need no key by design. `/health` without a key is liveness only and reports texting held. `sendBeacon` cannot carry the header (hop 8). |
| 7 | Disk -> board | `mergeNotes` 17803, `_mergeLead` 17616, `_funnelStats` 4189, `_ayRung` 4160, `_overdueFollowups` 4171, `STATUS_OPTS` 4142, `render` 18694 | team sync / bridge notes | status, stage unit (D14 fixed: `_mergeLead` now carries it), touches union | `_funnelStats` skips any lead with no status and no stage (line 4203). So `attempts` leaves out a lead whose only record is a no-answer dial (code-read, not asserted) and a Complete-sheet-only lead (asserted below). |
| 8 | Morning Worker | worker JS in `_docShell` 9322 (`textGate` 14421, `runlog` 13908, mark poster 14083-14095, unload `sendBeacon` 14073); `_wlogContacted` 12493; board replay of `/marks` 20555 | `POST /mark`, `GET /marks`, `POST /marks/consumed`, `POST /runlog`, `worker_marks.jsonl`, `worker_runs.jsonl` | mark `{c,k,extra}`; run event `{ev, x}` (counts and codes only) | Only kinds `sent callout text callq done skip badnum` are buffered to the bridge; any other mark kind is a breadcrumb and is not saved. Replay marks the prefix consumed 1 s after posting (20575) whether or not each handler acted. `sendBeacon` is refused from a keyed off-loopback device. `_wlogContacted` counts only `call text email letter door`, so a hand-logged "Other" / "Spoke to someone else" is not a contact. |
| 9 | Disk -> learning | `outcome_learn.compute` (reads `worker_notes.json`), `after_yes_counts` 78, `marker_counts` 45; `worker_runs.load` | reports | dials, stages, next-step dates | Counts dial rows (`oc`) only. Manual Complete touches never enter. After-yes counts agree with `_funnelStats` (asserted). |

## What `_e2ewiringtest.py` asserts, hop by hop

1. Three invented leads (one GUESS card, two verified) become call rows; `eqc.f` is falsy for GUESS, 1 for verified.
2. The built page's own JS (extracted, run under node) logs an Appointment on the GUESS and a verified lead,
   runs the after-the-yes panel, logs Complete-sheet chips, saves, and records the three fetches it makes.
   GATE: Offer made is refused on the GUESS lead (status stays Appointment); the same save is accepted on the
   verified lead (control).
3. Those recorded requests are replayed to a real `send_server.py` on a free port (temp copy of the code,
   temp `DEALFLOW_DIR`, fresh temp `optouts.json`, `smtplib` replaced by a raising stub, non-loopback
   `urlopen` logged and refused). Device key: a proxied `/notes` with no key or a wrong key is 401 and writes
   nothing; `/wp/lookup` with no key is 401 before any lookup; keyless remote `/health` is liveness only.
4. `worker_notes.json`: stage, next-step date and text, equity block, keep-or-sell, `apptUnverified`, and the
   manual touches all arrived; repeat and older pushes do not duplicate touches or walk the next step back.
5. `/text/check`, `/text` (unconfirmed then confirmed), `/mark` -> `/marks` -> `/marks/consumed`, `/runlog`
   (counts and codes only), `/health`, `/send` to a ledgered address (refused, nothing mailed).
6. Board `_funnelStats` and `_mergeLead` run under node on the disk notes: 2 appointments, 0 offers, 1
   unverified booking, nothing overdue; a board that already holds the lead gets the stage, next step, equity
   and manual touches. `outcome_learn.after_yes_counts` equals the board funnel; `compute` reads the bridge file.

## Known silent drops (each is an asserted `KNOWN GAP` in the test unless marked)

| Drop | Decision | Effect |
|---|---|---|
| Hand-logged Emailed / Texted (Complete sheet) is a touch only: no `mail_sent.json` row, no `text_sent.json` row | D18 (OPEN, recommendation A as built) | Cadence and the 3-touch text ladder cannot see a hand-sent message. Asserted for email. |
| `_merge_note` keeps the first `wpe`, a stored `replied`, and the first `wq` (wq code-read, not asserted) | D15 (callfix finding) | A second pasted address or a newer reply stamp reaches the board by sync but not the disk copy. |
| `_merge_note` keeps a stored `no:'soft'` over an incoming `'hard'` | D15 (RULED 2026-10-30: hard beats soft; fixer item queued, not on main) | Recorded reason is wrong; status DO NOT CONTACT still wins and `/notes` still ledgers it, so nobody is contacted. |
| `wpm` (pasted / looked-up numbers) not merged when the lead is already on disk | D17 related note | A second number never reaches `worker_notes.json`; `notes.wpl` is local + Supabase only. |
| Complete-sheet-only lead is not an attempt on the board funnel and is invisible to `outcome_learn` | none yet (by design per the Complete comment, call_mode.py 6781); reported to the Lead | "Contacts / attempts" under-count hand-logged work. |
| Keyless remote caller: most bridge posts end in `.catch(function(){})` | D16 context (device key) | 401 is toasted once by `bridgeFetch`; the worker popup's `/mark` and `/runlog` and the unload `sendBeacon` fail silently. Not asserted (needs a browser). |
| `ledger_from_notes` raising inside `/notes` | none (nightly `optout_sync` sweep is the backstop) | Response is still `ok:true`; DNC reaches `optouts.json` the next morning. Not asserted. |
| Worker marks of non-buffered kinds, and replay consumed after 1 s regardless | none | Breadcrumb marks are lost on a closed tab; a failed replay handler still empties the buffer. Code-read, not asserted. |

D14 (board `_mergeLead` dropped the after-the-yes unit) is FIXED on this commit and asserted as a normal
check. D1 (newer `afterYesAt` wins on disk) is merged and asserted. D17 (paid `/wp/lookup`) is not exercised
beyond the 401.
