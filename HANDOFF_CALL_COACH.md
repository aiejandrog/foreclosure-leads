# HANDOFF — BSG Live Call Coach (recorder + instant transcript + live objection coach)

**Written:** 2026-11-04 · **For:** Claude (cold pickup) · **Owner:** Alejandro Gonzalez
**Project root:** `C:\Users\olqbb\projects\foreclosure-leads`

---

## 1. What this is

A **local, single-machine web app** that runs on Alejandro's laptop while he dials foreclosure
leads from his iPhone on speakerphone. Three jobs, all at once:

1. **Record** the whole call to disk (audio, 5-second chunks, crash-safe).
2. **Transcribe** live on screen, near-instant, both sides of the conversation.
3. **Coach** — shows the exact next line for the current NEPQ stage, and **auto-fires the matching
   objection rebuttal** the moment the prospect says a trigger phrase.

Every session is saved as a folder he can cut up manually later:
`call_sessions/<YYYY-MM-DD_HHMMSS>_<case>/` → `audio.webm`, `transcript.txt`, `transcript.json`, `meta.json`.

**No cloud. No third-party Python deps. Binds `127.0.0.1` only.**

---

## 2. Current state — READ THIS FIRST

| File | Status |
|---|---|
| `call_coach_server.py` | ✅ **DONE** — 11,765 bytes, complete, untested |
| `call_coach_live.html` | ❌ **MISSING** — this is the whole remaining job |
| `CALL_SCRIPT_NEPQ.md` | ✅ exists — source of truth for stage copy + objections |
| `call_objections.json` | ✅ exists — 14 objection cards, keys `n,t,say,reb,one,es` |
| `leads_final.json` | ✅ exists — 367 leads, the field names the server reads |
| `call_mode.py` | ✅ exists — separate phone-first logging page, **do not touch** |

**Nothing has been run yet.** The server has never been started. The HTML has never existed.

---

## 3. Server API contract (the HTML MUST match this exactly)

`python call_coach_server.py` → serves on `http://127.0.0.1:8777/`

### GET
| Route | Returns |
|---|---|
| `/` | `call_coach_live.html` |
| `/api/health` | `{ok, leads, sessions_dir, port}` |
| `/api/lead?q=<text>` | `{matches: [leadCard, ...]}` — all-token substring over Address/owners/Case #/people_name/mailing_address |
| `/api/leads/recent` | `{leads: [leadCard,...]}` — 25 soonest auctions |
| `/api/sessions` | `{sessions: [dirname,...]}` |

`leadCard` shape:
```json
{"case","address","owners","auction","days","judgment","opening_bid",
 "plaintiff","equity","tier","score","homestead"}
```
`days` = int days from today to auction (negative = past), or `null`.

### POST
| Route | Body | Returns |
|---|---|---|
| `/api/session/start` | `{case, address, owners, auction, phone}` | `{session_id, dir}` |
| `/api/session/<sid>/audio` | **raw binary** (`application/octet-stream`) | `{ok, bytes}` |
| `/api/session/<sid>/transcript` | `{lines:[{t,text}]}` | `{ok, lines}` |
| `/api/session/<sid>/end` | `{duration_s, stages_visited, objections_fired, redflags_fired, consent_ack}` | `{ok, meta}` |

Server rejects any `sid` containing `/`, `\`, or `..`. Audio is **appended** (`ab`), never overwritten.

---

## 4. What `call_coach_live.html` must contain

Single file. No build step. No CDN. Vanilla JS in an IIFE assigned to `window.Coach`
(the server's gate buttons call `Coach.start()` and `Coach.plain()` inline).

### 4.1 Gate screen (`#gate`)
- The consent line, verbatim: *"Quick thing before we start — I record my calls so I keep your file straight. That okay with you?"*
- A required checkbox: *"I read that line and I'm recording this call. I accept responsibility for consent on this line."*
- `Start session` button (disabled until checkbox) → `Coach.start()`
- `Plain Call Mode (no recording)` → `Coach.plain()` — runs the coach UI with **no mic, no recorder, no session folder**. This is the fallback when the prospect says no.
- Three honest footnotes: audio stays local; **Chrome's speech recognizer sends audio to Google**; **speakerphone only** (neither iOS nor Android hands call audio to an app — laptop mic hears the room).

### 4.2 Main app (`#app`, shown by adding class `on`)
Grid: `auto auto 1fr` rows × `1fr 1fr` cols.

**Top bar:** `● REC` (pulsing), elapsed clock `mm:ss`, word count, language `<select>` (`en-US`/`es-US`), `Next stage (N)`, `Back (B)`, `Stop & save (Esc)`.

**Red-flag banner** (`#redflag`, spans both cols): fires on guardrail violations, shows the phrase + why.

**Left column:** live transcript, `white-space:pre-wrap`, interim results in dim italic, timestamped finals.

**Right column, top→bottom:**
1. Lead lookup input → debounced `/api/lead`, clickable hits, selected lead renders as a key/value card. Selecting a lead auto-fills `case/address/owners/auction` and pre-fills the merge fields.
2. Merge fields: `first`, `st1` (street), `date` (sale date spoken form), `sender` (default `Alejandro`). Every stage line and objection body runs through a `fill()` that replaces `{first} {st1} {date} {sender}`.
3. Stage chips row — 8 chips, current one highlighted, clickable to jump.
4. `#nowstage` label + `#sayline` (the big line to read) + `#saynote` (the coaching note under it).
5. `#objection` panel — amber, dismissible `×`, shows `who` + `body`.
6. `#probes` panel — the probing-loop questions for the current stage, each a button that copies into `#sayline` as a "say this instead" variant.
7. `← Back` / `Next stage →`.

### 4.3 The 8 stages (verbatim copy — do not paraphrase)

| # | id | name |
|---|---|---|
| 0 | `consent` | Consent |
| 1 | `opener` | Opener |
| 2 | `frame` | Status frame |
| 3 | `situation` | Situation |
| 4 | `problem` | Problem awareness |
| 5 | `solution` | Solution awareness |
| 6 | `consequence` | Consequence |
| 7 | `bridge` | Bridge |
| 8 | `close` | Book the advisor |

**Full `say` + `note` + `probes` text for all 8 stages is already drafted** — it lives in the
`STAGES` array that was being written when the previous session died. Re-derive it from
`CALL_SCRIPT_NEPQ.md` (sections STAGE 0 → CLOSE) if it is not recoverable. Key lines:

- **Opener:** *"Hi, {first}? Can you hear me? … It's {sender} with Biscayne Solutions Group. Do you know your property on {st1} is in pre-foreclosure{date}? … I'm not your lender and I'm not calling to buy your house. I help owners see what options they have before this goes any further. Can I ask you a couple of quick questions?"*
- **Problem:** *"When the bank or the attorney talked to you about that sale date… did anybody actually explain what happens to your balance every time that date gets pushed back?"*
- **Consequence:** *"Let me ask you straight — if nothing changes… and that date comes and goes… where does that leave you and your family?"*
- **Close:** *"…our senior advisor — 30-plus years in mortgages and foreclosure workouts — takes about five minutes and lays your real options out. Worst case, you know more than you did this morning. That's fair, right?… Is later today better… or first thing tomorrow?"*

### 4.4 Objection engine

12 cards. Each: `{id, who, s (score weight), m (trigger phrases, lowercase), body}`.
**Bodies are verbatim from `CALL_SCRIPT_NEPQ.md` § OBJECTIONS** — copy them, do not rewrite:

`notint` · `writing` · `busy` · `plan` · `legit` · `price` · `bail` · `bank` · `nothing` · `money` · `worth` · `talked`

**Matching algorithm (must be this, not fuzzy):**
```
on every final transcript line:
  window = last 240 chars of transcript, lowercased, punctuation→space
  for each card:
    hits = count of card.m phrases that appear in window
    if hits > 0: score = hits * card.s + recency_bonus(phrase in last 80 chars ? 2 : 0)
  pick highest score; if score >= 3 and != lastFiredId → show panel, log to objections_fired
```
Debounce: never re-fire the same card within 20 seconds. Manual `×` sets a 45-second cooldown on that card.

### 4.5 Red-flag engine (16 regexes)

Fires on **Alejandro's own words** — guardrail violations. Each: `{re, why}`. Must include at minimum:

| Pattern | Why |
|---|---|
| `guarantee(d|s)?` | Never guarantee an outcome |
| `we('ll| will) stop the (sale|foreclosure)` | Promised outcome |
| `save your home` | FS 501.1377 language — say "options" |
| `(sign|transfer)…(deed|title)` | Core 501.1377 red flag — attorney only |
| `quit ?claim` | Deed instrument, never on a money-side call |
| `our attorneys?` | Guardrail: say "our senior advisor" |
| `my supervisor` | Guardrail: say "our senior advisor, 30+ years" |
| `my clients?` | Guardrail |
| `(up ?front|advance) fee` / `pay (us|me) (a|the) fee` | No money up front, ever |
| `we buy houses` | Contradicts the opener |
| `sign (today|now|tonight)` | Pressure to sign |
| `100 ?percent` / `i promise` | Absolute claim |
| `we place loans?` | Licensed lenders lend; we introduce |

Banner shows the matched phrase + the `why`. Auto-clears after 8 seconds. Counted in `redflags_fired`.

### 4.6 Recording loop

```js
navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}})
→ MediaRecorder(mimeType:'audio/webm;codecs=opus')
→ ondataavailable every 5000ms  → POST raw blob to /api/session/<sid>/audio
→ on stop: flush final chunk, then POST /end
```
`timeslice: 5000` on `start()`. Each chunk POSTed immediately — a crash costs 5 seconds, not the call.
If a POST fails, buffer it in memory and retry on the next tick; never drop audio silently.

### 4.7 Transcript loop

```js
var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
rec.continuous = true; rec.interimResults = true; rec.lang = 'en-US';
rec.onresult → append finals to #transcript, show interim dim
rec.onend → if still running, rec.start() again (Chrome kills it every ~60s)
```
**Chrome only.** Firefox has no Web Speech API. Guard with a visible error if `SR` is undefined.
On stop: POST `{lines:[{t:"mm:ss",text:"..."}]}` to `/transcript`.

### 4.8 Keyboard

`N` = next stage · `B` = back · `Esc` = stop & save · `1`–`9` = jump to stage · `D` = dismiss objection.

---

## 5. Remaining work — checklist

- [ ] Write `call_coach_live.html` (single file, ~700–900 lines, per §4)
- [ ] `python call_coach_server.py` → confirm `leads loaded : 367`
- [ ] Open `http://127.0.0.1:8777/` in **Chrome**, accept mic prompt
- [ ] Test: checkbox gates the Start button
- [ ] Test: speak a sentence → appears in transcript within ~1s
- [ ] Test: say *"I already have a lawyer"* → `plan` card fires with the parachute rebuttal
- [ ] Test: say *"we'll stop the sale"* → red banner fires
- [ ] Test: Stop → `call_sessions/<stamp>_<case>/` has `audio.webm` > 0 bytes, `transcript.txt`, `meta.json`
- [ ] Test: `audio.webm` plays in VLC and contains both voices
- [ ] Test: Plain Call Mode opens the coach with no mic and writes nothing to disk
- [ ] Test: lead lookup by address fragment returns the right card
- [ ] Test: `{first}` / `{st1}` / `{date}` merge correctly in every stage line

---

## 6. Hard constraints — do not violate

1. **Speakerphone only.** No app can capture a cellular call's audio on iOS or Android. The laptop
   mic hears the room. This is a physical limit, not a bug. Say so in the UI.
2. **Chrome only** for the transcript (Web Speech API). Audio recording alone works in Edge.
3. **`127.0.0.1` only.** Never bind `0.0.0.0`. No auth exists; the only protection is the loopback.
4. **No Python deps.** Stdlib `http.server` only. The machine has Python 3.11.7 and Node 24.14.0.
5. **Never touch `call_mode.py`** (10,409 lines, the phone-first logging page) or `leads_final.json`.
6. **Never break the board.** This app is standalone; it must not import from or mutate
   `foreclosure_leads.py`.
7. **DNC numbers never appear.** The server only serves leads already in `leads_final.json`.

---

## 7. Legal position (owner's call, recorded for the record)

Alejandro has **explicitly accepted the recording-consent risk and owns it.** Florida is an
all-party-consent state (Fla. Stat. § 934.03; 18 U.S.C. § 2511) — recording without consent is a
third-degree felony. The design mitigates rather than ignores this:

- Stage 0 is a **spoken consent line before anything else**, and the gate screen forces him to read it.
- A **no** answer routes to Plain Call Mode (no recording at all).
- `meta.json` stores `consent_ack` so there is a per-call record of the gate being passed.

That is the whole mitigation. Do not add more warnings to the UI — he has read them.

---

## 8. Why a server at all (so Claude doesn't "simplify" it away)

Chrome refuses `getUserMedia` on `file://` origins. Serving from `http://127.0.0.1` makes it a
secure context, so the mic works — and the same origin can accept the audio POST. One process,
two jobs, zero CORS pain. **Do not convert this to a file:// page.**

---

## 9. One-paragraph brief for Claude

> Build `call_coach_live.html` in `C:\Users\olqbb\projects\foreclosure-leads` — a single-file,
> no-dependency, vanilla-JS live call coach that talks to the already-written
> `call_coach_server.py` on `127.0.0.1:8777`. It records audio in 5-second chunks to disk, shows a
> live Chrome speech transcript, walks 8 NEPQ stages with the exact line to read, auto-fires the
> matching objection rebuttal from 12 cards when trigger phrases appear in the transcript, and
> flashes a red banner on 16 guardrail-violating phrases. Copy for stages and objections is in
> `CALL_SCRIPT_NEPQ.md` and must be verbatim. Full spec in §4 above. Test checklist in §5.