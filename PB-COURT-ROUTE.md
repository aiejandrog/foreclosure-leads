# Palm Beach: what is reachable, and how (measured 2026-11-16)

Every line below was tested live on the engine laptop. Nothing here is assumed.

| # | Route | Result | Used for |
|---|---|---|---|
| 1 | Landmark **search** (`erec.mypalmbeachclerk.com/Search/*`) with no token | **Blocked.** NameSearch, ParcelIdSearch, BookPageSearch, RecordDateSearch, DocumentTypeSearch all answer `Invalid Captcha` | nothing |
| 2 | Landmark **direct document link** `/Document/GetDocumentByBookPage/?booktype=O&booknumber=B&pagenumber=P` | **Works.** Whole instrument as a PDF, plain request, no session, no token | `pb_recorded.py`, `lien_amounts.py` |
| 3 | Landmark **direct CFN link** `/Document/GetDocumentByCFN/?cfn=N` | **Works.** Same as 2 | `pb_recorded.py` |
| 4 | Court site **eCaseView in a scripted browser** (Playwright driving real Chrome, fresh or saved profile) | **Blocked** at guest login: `reCAPTCHA failed: Could not verify` | nothing |
| 5 | Court site **eCaseView in the operator's own Chrome** | **Works.** Guest login with no challenge; search by case type + filing date; full docket; complaint PDF (a 32-page complaint came back whole) | `pb_court_inbox.py`, `pb_court_filings.py`, `complaint_reader.py` |

`booktype` must be the letter `O`. `OR` and `3` return a 6 KB error page.

Two things found on the way:

- **Local OCR was dead on the engine.** The OCR bridge started Windows PowerShell 5.1, which does
  not start on this laptop. Every scanned page in every county came back unread. `winocr_py.py`
  calls the same Windows OCR engine from Python; `fl_lp/broward_pin.ocr_images` uses it first.
- **Palm Beach does record judgments.** `palmbeach_liens.json` holds book/page for 900+ lien and
  judgment rows; each is one free request away from its document.

## The court route (route 5)

The court site admits a real person's Chrome and nothing else, so its two jobs run in Chrome and
hand their results to a listener on this machine.

1. Start the listener: `python pb_court_inbox.py --once-idle 900`
2. In Chrome, open `https://appsgp.mypalmbeachclerk.com/eCaseView/` and click **Login as Guest User**.
3. The first time the tab posts to `127.0.0.1:8831`, Chrome asks whether the site may reach this
   computer. Click **Allow**. (Until then every post fails with `Failed to fetch`; Chrome reports
   the permission as `prompt`.)

**New filings.** Search Criteria: Court Type `Circuit Civil`, one Case Type from
`pb_court_filings.CASE_TYPES`, File Begin/End Date, **Start Search**. On the results page, post the
rows:

```js
const rows=[...document.querySelectorAll('#searchResults tbody tr')].map(r=>({case:r.cells[0].innerText.trim(),
  type:r.cells[2].innerText.trim(),filed:r.cells[4].innerText.trim(),style:r.cells[5].innerText.trim(),
  status:r.cells[6].innerText.trim()}));
await fetch('http://127.0.0.1:8831/filings',{method:'POST',body:JSON.stringify({rows})}).then(r=>r.json())
```

Repeat per case type, then `python pb_court_filings.py` merges them into `lis_pendens.json`
(existing rows always win; a case already on file is skipped).

**A complaint.** Open the case, **Dockets & Documents**, set entries per page to All, click the
image icon on the `COMPLAINT` row. The page fetches the PDF; post those bytes to
`/doc?case=<UCN>&din=<n>&kind=COMPLAINT`. `python complaint_reader.py --county pb` reads every page
of whatever is in the inbox.

## What stays a gap

- Landmark search (route 1). New Palm Beach filings come from the court (route 5) instead.
- An unattended nightly court pull. The court site turns a scripted browser away, and making a
  script look like a person to get past that check is not something this project does.

## Broward court route (measured 2026-11-16, corrected the same day)

An earlier test recorded Broward's court search as "refused". That test submitted the search while
the site's own check was still running. Done properly, in the operator's own Chrome on the engine
laptop, it works with no interaction beyond a normal search:

1. `https://www.browardclerk.org/Web2`, Case Number tab, type the case with no dashes
   (`CACE26018549`). **Wait** until the Cloudflare box under the field says `Success!`, then Search.
2. Click the case in the results. The docket lists `Complaint (eFiled)` with a page count.
3. Click its document button. A viewer tab opens; **View all Pages** loads the whole PDF in a frame
   (`WebForms/PDF.aspx`). An 18-page complaint came back as 18 pages, 683 KB.
4. Save it as `<CASE>_COMPLAINT.pdf` into `~/DEALFLOW/documents/BROWARD/_court_inbox/`
   (a page-triggered download lands in Downloads on the same machine; move it).
5. `python complaint_reader.py --county broward` reads every page. A complaint from this inbox
   replaces the recorded-notice reading for that case.

Two traps:
- **Which Chrome.** The Claude extension may be connected on the other machine. Anything local
  (the inbox on 127.0.0.1, Downloads, the clipboard) only works when the Chrome in use is the one
  on this laptop: check `list_connected_browsers` and select the one marked `onThisComputer`.
- **Scans.** The court serves watermarked page images; amounts in a private note are often written
  in words. `complaint_reader.words_to_number` reads them; a garbled word is never guessed.
