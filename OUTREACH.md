# DealFlow Outreach — Virtual Mail (Round C)

Send compliant foreclosure / tax-deed letters as **real physical mail** through [Lob](https://lob.com),
paying only Lob's per-letter fee (print + postage + windowed envelope). No local printer, no per-letter
handling. Script: `outreach_mail.py`.

## One-time setup (needs YOU — I can't do these)

1. **Lob account + key.** Sign up at lob.com, add a payment method (live mail needs a funded account —
   a *test* key only produces test PDFs, no real mail). Copy your API key.
   ```
   # save it to lob.key (gitignored — never commit, never put in the tracker HTML)
   echo live_xxxxxxxxxxxxxxxxxxxx > lob.key
   ```
2. **Sender identity.** Copy `sender.json.template` → `sender.json` (gitignored) and fill in **Jose's
   entity** (this is who the letters come from). `addr` must be a full return address
   (`LINE1, CITY, FL 33172`) or `--send` is refused.

## Recommended: the "✉ Mail batch" button (in the tracker)

Easiest, safest flow — you review the exact list in the UI and the opt-outs come along automatically:

1. In the tracker toolbar click **✉ Mail batch**. It lists every mailable lead (Tier A/B, real owner —
   no LLC/vacant — deliverable address, auction 5+ days out, **not opted out**), with a live count + cost.
2. **Uncheck anyone** you don't want to mail, then **⬇ Download batch** → `dealflow-mailqueue-YYYY-MM-DD.json`.
   (The Lob key is never in the page — this only selects and exports.)
3. **Dry run** it (no key, nothing sent) — review who gets mailed + the cost + `mail_preview.html`:
   ```
   python outreach_mail.py --queue dealflow-mailqueue-2026-07-18.json
   ```
4. **Send for real** (only after the dry run looks right):
   ```
   python outreach_mail.py --queue dealflow-mailqueue-2026-07-18.json --send
   ```
   The queue trusts your picks but still skips undeliverable addresses and anyone already mailed
   (`mail_sent.json` ledger, so re-runs never double-mail). Sender identity comes from `sender.json`,
   falling back to whatever you set in the tracker's Sender identity fields.

## Alternative: filter from the whole book on the command line

1. **Capture opt-outs first.** In the tracker (⇄ Sync / team → export a notes file) download
   `dealflow-notes-YYYY-MM-DD.json`. This carries every DO NOT CONTACT / opted-out lead.
2. **Dry run** (no key needed, nothing sent) — review who gets mailed + the cost:
   ```
   python outreach_mail.py --tier A --suppress dealflow-notes-2026-07-18.json
   ```
   It prints the queue, the skips (wrong tier / company / vacant / unparseable address / too-late /
   suppressed), a cost estimate, and writes `mail_preview.html` (open it — that's the exact letter).
3. **Send for real** (only after the dry run looks right):
   ```
   python outreach_mail.py --tier A --suppress dealflow-notes-2026-07-18.json --send
   ```
   Each send is logged to `mail_sent.json` so re-runs never double-mail the same owner.

## Options

| flag | default | meaning |
|------|---------|---------|
| `--tier A,B` | `A,B` | tiers to include (`all` = every tier) |
| `--lang en\|es` | `en` | letter language |
| `--min-days N` | `5` | only mail if the auction is ≥ N days out (a first-class letter must arrive in time) |
| `--suppress FILE` | — | tracker notes export → skip DNC / opted-out cases |
| `--limit N` | `0` | cap the batch size (safety) |
| `--remail` | off | include owners already in `mail_sent.json` |
| `--send` | off | actually mail (needs `lob.key`); without it, dry run |

## Guardrails built in

- **Key never leaves your machine** — read from gitignored `lob.key`, never in the tracker or a commit.
- **Dry run by default** — real mail needs `--send` + a live key.
- **Only real homeowners** — skips LLC/company owners and vacant land (nobody to help).
- **Only deliverable, in-time** — skips unparseable addresses and auctions too close for mail to arrive.
- **Opt-outs honored** — `--suppress` drops every DO NOT CONTACT / opted-out lead from your tracker.
- **No double-mailing** — `mail_sent.json` ledger.
- Letter copy is the same vetted, attorney-aware language as the tracker's per-lead "Letter" button.

## Cost

~$0.70–$1.10 per single-page first-class B&W letter (print + postage + envelope), per Lob's 2026
pricing — verify your number at <https://help.lob.com/print-and-mail/ready-to-get-started/pricing-details>.
The dry run estimates the batch total.
