# Camoufox vs the Broward + Palm Beach portals — measured 2026-08-22

Tested whether [Camoufox](https://github.com/daijro/camoufox) (anti-detect Firefox fork, v152.0.4-beta.28)
can replace or improve the current transports. Method: monkey-patch `_curl` in `broward_liens.py`
and `palmbeach_liens.py` with an in-page `fetch()` backed by Camoufox, then call **the repo's own
`start_session()` / `search_docs()`** — so this measures production code paths, not a
re-implementation.

In-page `fetch()` deliberately, not Playwright's `page.request`: `APIRequestContext` runs from the
Node driver and carries Node's TLS fingerprint, which would invalidate the whole test.

Harness lives at `C:\Users\olqbb\tools\camoufox\` (`dropin_test.py`, `dropin_vanilla.py`).

## Results

| Transport | Broward AcclaimWeb | Palm Beach Landmark |
|---|---|---|
| `requests` | **403** on landing | loads, gated |
| vanilla headless Chromium | **403** at `SearchTypeName` | loads, gated |
| System32 `curl` (current) | 400 docs | loads, **gated** |
| **Camoufox** | **400 docs, 3/3 trials, 0.5–0.9s** | loads, **gated** |

## Broward — Camoufox works, and it is not just "any browser"

The control matters more than the result. Vanilla headless Chromium, running the identical flow
through the identical production functions, dies with **HTTP 403 / 4,587 bytes** at
`/Search/SearchTypeName` — the exact request where Camoufox returns **200 / 50,666 bytes**. So the
docstring claim in `broward_liens.py` ("Cloudflare … blocks python-requests' TLS fingerprint AND
headless browsers") is still accurate, and Camoufox specifically defeats it.

Camoufox returned the same 400 documents and the same first row (`JAGDEOSINGH,SONNY`) as the curl
path, across 3 consecutive fresh-browser trials.

**What this is worth:** not speed — curl is already fast and free. It is the *portability*. The
current transport is pinned to `C:\Windows\System32\curl.exe` specifically, to the point that the
mingw64 curl on PATH under Git Bash gets blocked (documented at `broward_liens.py:48-51`). That
makes Broward lien tracing a Windows-only, this-machine-only capability — it cannot run on the
GitHub Actions Linux runner. Camoufox is cross-platform and passes the same wall.

## Palm Beach — no benefit, do not bother

`ShowCaptcha` returns `True` for Camoufox and `True` for curl. Identical. The reCAPTCHA v2 gate on
`erec.mypalmbeachclerk.com` is not fingerprint-driven, so a better fingerprint does not open it.
The 2Captcha spend and the `--limit 6` throttle stay exactly as they are.

(An earlier probe of mine read PB as "OPEN" — that was a harness bug: the session had not been
established, so `ShowCaptcha` returned a 1,929-byte page instead of the 4-byte boolean. Established
sessions return `True` on both transports.)

## Honest limits of this test

- One Broward name (`JAGDEOSINGH`, from lead `COCE-25-034167`) and one PB PCN. Not a volume test.
- Three trials over a few minutes from one residential IP. Cloudflare scores on reputation over
  time; this says nothing about what happens at 60 leads/night, every night, for a month.
- Camoufox pins `playwright==1.60.0`; the repo runs 1.62.0. It is installed in an isolated venv at
  `C:\Users\olqbb\tools\camoufox\.venv` precisely so nothing shared changed. Adopting it in the
  repo means resolving that pin — see that folder's README.

## Miami-Dade — Camoufox mints its own Turnstile token. This is the one that pays.

Tested 2026-08-22 with `md_probe_ui.py`. **No 2Captcha calls were made**, so the test cost nothing
either way.

| Transport | own token minted | standardsearch | getStandardRecords | outcome |
|---|---|---|---|---|
| `requests`, no token | — | 200 `isValidSearch=false` | — | walled |
| vanilla headless Chromium | **0 chars** | 200, no `qs` | never fired | walled |
| **Camoufox** | **666–688 chars** | **200 + `qs`** | **200** | **42 records for HONDROULIS** |

**4/4 trials.** Turnstile is in invisible/managed mode here, so it silently hands a legitimate-looking
browser a token — and it judged Camoufox legitimate every time. Vanilla headless got *twice* the
`challenges.cloudflare.com` traffic (28 events vs 14) and still never passed: it was being
re-challenged and failing, then posting the search with an empty `x-recaptcha-token`.

This is the structural difference from Palm Beach. PB is reCAPTCHA v2 **checkbox** — it cannot
auto-pass, so no fingerprint work opens it. Miami-Dade is **Turnstile**, which can and does.

### What it's worth

Two things, and the second is the bigger one:

1. **The 2Captcha line for MD goes away.** ~$0.003/solve, `--limit 60` ≈ $0.18/run.
2. **The `--limit 60` cap exists to bound that spend.** With the token free, the cap is no longer
   protecting anything — so the ceiling on how many new leads get a lien chain per night is set by
   time, not by budget. `refresh-dealflow.bat` notes ~21 new cases land daily; the spend cap is part
   of why coverage sits where it does.

It also replaces the existing fallback. `records_liens.mint_and_fetch` drives Playwright to mint a
token and its own docstring says the county "walls it ~half the time headless". Camoufox went 4/4.

### Getting there

Three probes, because the first two tested the wrong thing and said so:

1. POSTed `api/home/standardsearch` cold — Turnstile's script was present but no widget had
   rendered and no token existed. Testing an API path the app never uses cold is not a fair test.
2. Looked for a search box on the landing page — it is behind the sidebar's
   Standard Search → Name/Document, so nothing was found.
3. Clicked through, filled `#lastName`, submitted, and read the app's own request. That is where
   the answer was.

Search quirk confirmed along the way, matching `fetch_via_turnstile`'s comment: the clerk takes
**one word**. `HONDROULIS` works.

## Not done

Nothing in the repo was rewired — `records_liens.py` still buys tokens from 2Captcha. Switching it
to a Camoufox-minted token is a real change to the nightly's critical path and wants its own pass,
including what happens when Turnstile stops being generous. The measurement is in; the migration is
not.
