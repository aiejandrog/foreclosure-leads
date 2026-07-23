#!/usr/bin/env python3
"""lis_pendens.py — THE FRONT OF THE FUNNEL.

Everyone buys the auction list (sale date already set) — crowded tail. The LIS PENDENS is recorded
the day the foreclosure is FILED, 8–14 months earlier. This sweeps newly-recorded LIS PENDENS from
Miami-Dade Official Records so Jose can be the owner's FIRST contact.

WORKING FREE METHOD (2026-07-23):
  Miami-Dade WALLs nameless document-type/date searches (isValidSearch:false even when the URL is
  byte-identical to the site's own postStandardSearch). Name searches still work. So we sweep by
  major lender / servicer / HOA association NAME + documentType=LIS PENDENS - LIS + date window,
  then dedupe. Coverage is not 100% of the docket (small private plaintiffs miss) but catches the
  bulk of bank-1st and big-HOA filings without a subscriber account.

FULL DOCKET (paid):
  Clerk Commercial Data Services → Official Records folder ≈ $110/mo
  https://www.miamidadeclerk.gov/clerk/commercial-data-services.page
  (no notarized form required for Official Records). Wire that feed later if Jose subscribes.

Every filing is KEPT and TAGGED (Jose: no deal is dead):
  BANK-1st | HOA/JUNIOR | OTHER/PRIVATE

Run:
  python lis_pendens.py --days 14              # lender-name sweep (default)
  python lis_pendens.py --days 14 --limit 5    # smoke-test first 5 parties
  python lis_pendens.py --blank                # prove the nameless wall (expect 0)
  python lis_pendens.py --probe                # legacy browser probe
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_records_qs as G  # BASE, UA, SITE_KEY

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "lis_pendens.json")

LENDER_RE = re.compile(
    r"\b(BANK|MORTG|LOAN|LENDING|FINANC|CAPITAL|FED(ERAL)?|CREDIT UNION|N\.?A\.?|"
    r"FSB|TRUST|SERVICING|FUND(ING)?|HOLDINGS|WILMINGTON|DEUTSCHE|WELLS FARGO|"
    r"CHASE|CITI|US BANK|NATIONSTAR|CARRINGTON|SELENE|RUSHMORE|FREEDOM|PENNYMAC|"
    r"PHH|SHELLPOINT|NEWREZ|LAKEVIEW|FANNIE|FREDDIE|HUD|SECRETARY)\b",
    re.I,
)
HOA_RE = re.compile(
    r"\b(HOA|CONDO|ASSOC|ASSN|HOMEOWNER|MASTER|COMMUNITY|VILLAS?|TOWERS?|COA|POA)\b", re.I
)

# Major MD foreclosure plaintiffs / servicers — name search + LIS PENDENS doctype works free.
# Order = roughly volume. Keep short enough for a daily CI budget (~1 Turnstile solve each).
PARTY_SEEDS = [
    "WELLS FARGO",
    "BANK OF AMERICA",
    "JPMORGAN",
    "US BANK",
    "U.S. BANK",
    "DEUTSCHE BANK",
    "NATIONSTAR",
    "NEWREZ",
    "SHELLPOINT",
    "PENNYMAC",
    "FREEDOM MORTGAGE",
    "CARRINGTON",
    "LAKEVIEW",
    "WILMINGTON",
    "CITIMORTGAGE",
    "CITIBANK",
    "HSBC",
    "PHH",
    "RUSHMORE",
    "SELENE",
    "MORTGAGE ELECTRONIC",
    "FEDERAL NATIONAL",
    "FEDERAL HOME LOAN",
    "SECRETARY OF HOUSING",
    "MIDFIRST",
    "TRUIST",
    "PNC BANK",
    "REGIONS BANK",
    "FLAGSTAR",
    "CROSSCOUNTRY",
    "LOANCARE",
    "SPECIALIZED LOAN",
    "HOMEOWNERS ASSOCIATION",
    "CONDOMINIUM ASSOCIATION",
    "PROPERTY OWNERS ASSOCIATION",
]

DOCTYPE = "LIS PENDENS - LIS"
SEARCHTYPE = "Name/Document"

# legacy browser probe (reCAPTCHA-v3) — kept for --probe
SEARCH_JS = r"""
async (args) => {
  const KEY='SITEKEY';
  if(!window.grecaptcha || !window.grecaptcha.execute){
    await new Promise((res,rej)=>{ const s=document.createElement('script'); s.src='https://www.google.com/recaptcha/api.js?render='+KEY; s.onload=res; s.onerror=()=>rej(new Error('blocked')); document.head.appendChild(s); setTimeout(()=>rej(new Error('captcha load timeout')),25000); });
    await new Promise(r=>setTimeout(r,2000));
  }
  await new Promise(res=>grecaptcha.ready(res));
  const token=await grecaptcha.execute(KEY,{action:'standardsearch'});
  const [docType, dFrom, dTo, stype] = args;
  const url='/officialrecords/api/home/standardsearch?partyName=&dateRangeFrom='+encodeURIComponent(dFrom)
    +'&dateRangeTo='+encodeURIComponent(dTo)+'&documentType='+encodeURIComponent(docType)
    +'&searchT=&firstQuery=y&searchtype='+encodeURIComponent(stype);
  const r=await fetch(url,{method:'POST',headers:{'Accept':'application/json','x-recaptcha-token':token,'content-type':'application/json; charset=utf-8'},body:''});
  let j=null, raw=''; try{ raw=await r.text(); j=JSON.parse(raw); }catch(e){}
  if(!j || !j.qs) return {success:false, status:r.status, qs:null, raw:raw.slice(0,300)};
  const g=await fetch('/officialrecords/api/SearchResults/getStandardRecords?qs='+j.qs,{headers:{'Accept':'application/json'}});
  let gj=null; try{ gj=JSON.parse(await g.text()); }catch(e){}
  const arr=(gj && gj.recordingModels) || [];
  return {success:true, qs:j.qs, count:Array.isArray(arr)?arr.length:0, sample:arr.slice(0,60)};
}
""".replace("SITEKEY", G.SITE_KEY)


def _win(days):
    to = datetime.date.today()
    fr = to - datetime.timedelta(days=days)
    return fr.strftime("%m/%d/%Y"), to.strftime("%m/%d/%Y")


def _kind(parties):
    pu = (parties or "").upper()
    if HOA_RE.search(pu) and not LENDER_RE.search(pu):
        return "HOA/JUNIOR"
    if LENDER_RE.search(pu):
        return "BANK-1st"
    return "OTHER/PRIVATE"


def normalize(rec, seed=""):
    """Map an Official Records recordingModel into a filing row.

    Party roles: for LIS PENDENS, the lender/HOA plaintiff is usually firsT_PARTY and the owner
    defendant seconD_PARTY — but mortgage indexing flips that (lender = seconD). Prefer the party
    that matches the lender/HOA seed (or LENDER_RE/HOA_RE) as plaintiff; the other is the owner.
    """
    p1 = str(rec.get("firsT_PARTY") or rec.get("first_party") or "").strip()
    p2 = str(rec.get("seconD_PARTY") or rec.get("second_party") or "").strip()
    seed_u = (seed or "").upper()

    def _is_plaintiff(name):
        u = (name or "").upper()
        if seed_u and seed_u[:8] in u:
            return True
        if LENDER_RE.search(u) or HOA_RE.search(u):
            return True
        return False

    if _is_plaintiff(p1) and not _is_plaintiff(p2):
        plaintiff, defendant = p1, p2
    elif _is_plaintiff(p2) and not _is_plaintiff(p1):
        plaintiff, defendant = p2, p1
    else:
        # default LP indexing: first = filer
        plaintiff, defendant = p1 or p2, p2 if p1 else ""
    parties = " / ".join(p for p in (plaintiff, defendant) if p)
    return {
        "date": (rec.get("reC_DATE") or rec.get("rec_date") or "")[:10],
        "docType": (rec.get("doC_TYPE") or "LIS PENDENS").strip(),
        "bookpage": rec.get("reC_BOOKPAGE") or rec.get("bookpage") or "",
        "folio": str(rec.get("foliO_NUMBER") or rec.get("folio_number") or "").strip(),
        "parties": parties.strip(),
        "plaintiff": plaintiff,
        "defendant": defendant,
        "kind": _kind(plaintiff or parties),
        "case": str(rec.get("casE_NUM") or rec.get("case_num") or "").strip(),
    }


def _search(party_name, d_from, d_to, tries=3):
    """Name + LIS PENDENS + date window via Turnstile (the path that works)."""
    import records_liens as R
    from captcha_solver import solve_turnstile

    url = (
        R.OR_BASE
        + "api/home/standardsearch?partyName="
        + urllib.parse.quote(party_name)
        + "&dateRangeFrom="
        + urllib.parse.quote(d_from)
        + "&dateRangeTo="
        + urllib.parse.quote(d_to)
        + "&documentType="
        + urllib.parse.quote(DOCTYPE)
        + "&searchT="
        + urllib.parse.quote(DOCTYPE)
        + "&firstQuery=y&searchtype="
        + urllib.parse.quote(SEARCHTYPE)
    )
    for _ in range(max(1, tries)):
        tok = solve_turnstile(R.TS_SITE_KEY, R.OR_BASE)
        if not tok:
            continue
        try:
            r = R.S.post(
                url,
                headers={
                    "x-recaptcha-token": tok,
                    "content-type": "application/json; charset=utf-8",
                },
                data="",
                timeout=35,
            )
            j = r.json()
        except Exception:
            time.sleep(1)
            continue
        qs = j.get("qs") if isinstance(j, dict) else None
        if qs:
            return R.records_by_qs(qs) or []
        # isValidSearch:false with a name usually = bad solve; retry
        time.sleep(1)
    return None  # None = failed; [] = valid empty


def lp_sweep_blank(days=14, tries=4):
    """Nameless docket sweep — expected to return [] (county wall). Kept as a regression probe."""
    import records_liens as R
    from captcha_solver import solve_turnstile

    d_from, d_to = _win(days)
    print(f"BLANK-NAME probe: {d_from} .. {d_to} (expect wall / isValidSearch:false)")
    url = (
        R.OR_BASE
        + "api/home/standardsearch?partyName="
        + "&dateRangeFrom="
        + urllib.parse.quote(d_from)
        + "&dateRangeTo="
        + urllib.parse.quote(d_to)
        + "&documentType="
        + urllib.parse.quote(DOCTYPE)
        + "&searchT="
        + urllib.parse.quote(DOCTYPE)
        + "&firstQuery=y&searchtype="
        + urllib.parse.quote("Document Type")
    )
    for attempt in range(1, tries + 1):
        tok = solve_turnstile(R.TS_SITE_KEY, R.OR_BASE)
        if not tok:
            continue
        try:
            r = R.S.post(
                url,
                headers={
                    "x-recaptcha-token": tok,
                    "content-type": "application/json; charset=utf-8",
                },
                data="",
                timeout=35,
            )
            j = r.json()
        except Exception as e:
            print(f"  attempt {attempt}: {e}")
            continue
        print(f"  attempt {attempt}: keys={list(j) if isinstance(j, dict) else type(j)} qs={bool((j or {}).get('qs'))} raw={str(j)[:180]}")
        if isinstance(j, dict) and j.get("qs"):
            return R.records_by_qs(j["qs"]) or []
        time.sleep(1)
    return []


def lp_sweep_by_party(days=14, parties=None, limit=0, tries=3):
    """WORKING free path: major plaintiff names × LIS PENDENS × date window."""
    d_from, d_to = _win(days)
    parties = list(parties or PARTY_SEEDS)
    if limit:
        parties = parties[:limit]
    print(f"LIS PENDENS lender-name sweep: {d_from} .. {d_to} · {len(parties)} parties")
    all_recs, per = [], Counter()
    for i, name in enumerate(parties, 1):
        recs = _search(name, d_from, d_to, tries=tries)
        if recs is None:
            print(f"  [{i}/{len(parties)}] {name!r}: FAIL (captcha/API)")
            continue
        # keep only actual lis pendens rows (name search can spill related docs)
        kept = [
            r
            for r in recs
            if "LIS PEND" in (r.get("doC_TYPE") or r.get("doc_type") or "").upper()
            or not (r.get("doC_TYPE") or r.get("doc_type"))
        ]
        if not kept:
            kept = recs
        print(f"  [{i}/{len(parties)}] {name!r}: {len(kept)} filing(s)")
        per[name] = len(kept)
        for r in kept:
            row = dict(r)
            row["_seed"] = name
            all_recs.append(row)
        time.sleep(0.4)
    return all_recs, per


def _dedupe(recs):
    out, seen, kinds = [], set(), Counter()
    for rec in recs:
        n = normalize(rec, seed=rec.get("_seed") or "")
        key = (n["bookpage"] or "") + "|" + (n["folio"] or "") + "|" + n["parties"][:50]
        if key in seen:
            continue
        seen.add(key)
        kinds[n["kind"]] += 1
        out.append(n)
    out.sort(key=lambda x: x["date"], reverse=True)
    return out, kinds


def _to_leads(rows):
    """Slim lead shape for make_tracker merge (stage=LP, no auction day)."""
    leads = []
    for r in rows:
        owners = r.get("defendant") or (r.get("parties") or "").split("/")[-1].strip()
        plaintiff = r.get("plaintiff") or ""
        leads.append(
            {
                "stage": "LP",
                "st": "LP",
                "tier": "B",
                "score": 55 if r.get("kind") == "BANK-1st" else 45,
                "case": r.get("case") or r.get("bookpage") or f"LP-{r.get('date')}-{r.get('bookpage')}",
                "owners": owners,
                "addr": "",
                "mail": "",
                "value": 0,
                "judg": 0,
                "eq": 0,
                "auction": "",
                "days": None,
                "filedDate": r.get("date") or "",
                "plaintiff": plaintiff,
                "defs": owners,
                "lpkind": r.get("kind") or "",
                "folio": r.get("folio") or "",
                "county": "MIAMI-DADE",
                "ctype": "Bank/Mortgage" if r.get("kind") == "BANK-1st" else ("HOA/Condo" if r.get("kind") == "HOA/JUNIOR" else "Other"),
            }
        )
    return leads


def probe():
    d_from, d_to = _win(14)
    print(f"PROBE (legacy browser): LIS PENDENS, {d_from} .. {d_to}, blank name")
    from playwright.sync_api import sync_playwright

    for dt in ("LIS PENDENS - LIS", "LIS PENDENS"):
        for st in ("Document Type", "Name/Document"):
            print(f"\n--- documentType={dt!r} searchtype={st!r} ---")
            try:
                with sync_playwright() as p:
                    b = p.chromium.launch(headless=True)
                    pg = b.new_context(user_agent=G.UA, viewport={"width": 1400, "height": 1000}).new_page()
                    pg.goto(G.BASE, timeout=40000, wait_until="domcontentloaded")
                    pg.wait_for_timeout(4000)
                    res = pg.evaluate(SEARCH_JS, [dt, d_from, d_to, st])
                    b.close()
                print(" ", res)
            except Exception as e:
                print(" ", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--blank", action="store_true", help="only run the nameless wall probe")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--limit", type=int, default=0, help="cap party list (smoke test)")
    ap.add_argument("--tries", type=int, default=3)
    a = ap.parse_args()
    if a.probe:
        probe()
        return
    if a.blank:
        recs = lp_sweep_blank(days=a.days)
        print(f"blank-name result: {len(recs)} filings")
        return

    recs, per = lp_sweep_by_party(days=a.days, limit=a.limit, tries=a.tries)
    if not recs:
        print(
            "\nNO FILINGS from lender-name sweep. Check captcha.key / TWOCAPTCHA_KEY, or try --limit 3.\n"
            "Nameless docket search remains WALLED (use --blank to confirm).\n"
            "Full coverage path: Clerk Commercial Data Services Official Records ≈ $110/mo —\n"
            "https://www.miamidadeclerk.gov/clerk/commercial-data-services.page"
        )
        return

    out, kinds = _dedupe(recs)
    payload = {
        "traced": datetime.date.today().isoformat(),
        "days": a.days,
        "method": "lender-name",
        "kinds": dict(kinds),
        "per_party": dict(per),
        "filings": out,
        "leads": _to_leads(out),
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"\nDONE: {len(out)} fresh LIS PENDENS ({dict(kinds)}) -> lis_pendens.json")
    print("ALL kinds kept — BANK-1st / HOA/JUNIOR / OTHER/PRIVATE. make_tracker merges stage=LP.")


if __name__ == "__main__":
    main()
