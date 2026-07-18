<!-- [Cursor cloud edit] authored by the Cursor cloud agent -->
# Mail batch — send letters to owners (safely)

The tracker can hand you a **queue of the leads worth mailing right now**; a small local script
(`outreach_mail.py`) does the actual mailing through [Lob](https://lob.com). The Lob key stays on your
machine and **never** touches the browser or the public site.

## The flow

1. In the tracker, click **✉ Mail batch** in the toolbar. A modal lists every mailable lead:
   - Tier **A/B**, a **real homeowner** (no LLC/company, no vacant land),
   - a **deliverable address**, an **auction 5+ days out** (mail needs runway), and
   - **not opted out / dead** (read straight from your own notes — DNC/opt-out leads never appear).
   It shows a live **count** and estimated **postage**.
2. Uncheck anyone you don't want, then click **⬇ Download batch** — you get
   `dealflow-mail-batch-YYYY-MM-DD.json` (your sender identity rides along inside it).
3. In a terminal in this folder, **dry run first** (no send, no charge):
   ```
   python outreach_mail.py --queue dealflow-mail-batch-YYYY-MM-DD.json
   ```
   It parses every address, builds each letter, and prints exactly what *would* be sent.
4. When it looks right, actually mail it:
   ```
   python outreach_mail.py --queue dealflow-mail-batch-YYYY-MM-DD.json --send
   ```

## One-time setup to actually mail

- **Fund a Lob account** and put the secret key in a file named **`lob.key`** in this folder
  (it's gitignored — it never leaves your PC). Or pass `--key path/to/key`.
- **Set your return address.** Lob requires a `from` address. Fill the **Sender** fields under
  *Filters & setup* in the tracker (the `addr` field especially) — they're carried into the export —
  or fill **`sender.json`** (see the template in this repo). Sender fields in the export win; blank
  fields fall back to `sender.json`.

## Options

| flag | meaning |
|---|---|
| `--queue <file>` | the JSON exported by the Mail batch button (required) |
| `--send` | actually create letters via Lob (costs money). Omit = dry run. |
| `--key <file>` | Lob key file (default `lob.key`) |
| `--sender <file>` | sender identity file (default `sender.json`) |
| `--limit <n>` | cap the number of letters (handy for a first test send) |

## Notes

- The letter copy matches the tracker's on-screen **✉ Letter** (English), personalized per owner
  (owner name, address, sale date, case #, plaintiff). Multi-owner records are reduced to the primary.
- **Opt-outs are honored automatically** because the button reads the tracker's own opt-out ledger —
  you don't need a separate suppression export.
- Direct mail is **not** restricted the way calls/texts are (no TCPA/FTSA/DNC texting rules), which is
  why this is the compliance-safest cold-outreach channel. Keep letters honest and FS&nbsp;501.1377-compliant.
- Start with `--limit 3` for your first real send to confirm formatting and delivery before mailing the
  whole batch.
