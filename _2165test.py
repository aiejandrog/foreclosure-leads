"""Regression fixture for the DEALFLOW-fixes-2165NW19 spec (2165 NW 19 ST, folio 494229040400,
CACE-23-021795 — a use-48 warehouse that rendered a false STRONG on the residential model).

Covers the underwriting-integrity fixes that were actually MISSING (per the audit, most of the spec
was already built — FS 55.03 payoff accrual, ARV confidence+county gate, lien-netted equity, AVM-split
chip all pre-existed). What this guards:

  FIX 3  commercial/industrial parcels (r.comm) never run the residential model -> verdict VERIFY,
         no residential profit/ROI printed.  <-- the root cause of the 2165 false STRONG.
  FIX 4  serial bankruptcy (saleBK>=2) / serial staller (saleSurv>=2) / judgment-under-attack
         (setAside) can never be STRONG.
  FIX 1  the "owed" the card prints now equals the accrued payoff the math uses (_payoffOf).
  regression: an otherwise-identical clean residential lead STILL reaches STRONG, so the caps are
         real gates and the residential path is intact.

Runs against design-preview.html (fake leads, no gate). Rebuild it first: python build_preview.py
"""
import pathlib, sys, subprocess
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
# Always test the CURRENT template, not a stale preview.
subprocess.run([sys.executable, 'build_preview.py'], cwd=HERE, check=True,
               capture_output=True, text=True)
URL = (HERE / 'design-preview.html').resolve().as_uri()

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ")+name+((" | "+extra) if extra else "")); ok = ok and bool(cond)

# A fully-formed FC row that pencils out to STRONG on the residential model (value 700k, owed 200k):
# offer .75*700=525k, debt 200k, cash 525k+70k costs=595k, profit 105k, ROI ~17.6% -> STRONG.
# Every override the spec cares about is layered on top of this same base so each cap is tested in
# isolation against a row that WOULD otherwise be STRONG.
BASE = {
  "st":"FC","mr":False,"ju":False,"condo":False,"vac":False,"comm":False,"sibclaimed":False,
  "value":700000,"judg":200000,"payoff":0,"arv":0,"arvconf":"","rfval":0,"zest":0,
  "orconf":"ok","orjuniors":0,"orsurv":0,"orsurvsen":0,"orhoa":0,"orcode":0,"orirs":0,"taxDue":0,
  "hs":False,"mail":"","addr":"100 TEST ST","ctype":"Bank/Mortgage","ip":False,
  "obid":0,"saleBK":0,"saleSurv":0,"setAside":False,"owners":"TEST HOLDINGS LLC",
}

with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); pg.goto(URL)
    # inject a row = BASE + overrides, recompute the whole board, return this row's computed state
    def run(case, over):
        row = dict(BASE); row.update(over); row["case"] = case
        return pg.evaluate("""(row)=>{
            const i = DATA.findIndex(x=>x.case===row.case);
            if(i>=0) DATA.splice(i,1);
            delete notes[row.case];
            DATA.push(row); recompute();
            const r = DATA.find(x=>x.case===row.case);
            return {verdict:r._verdict, profit:r._profit, roi:r._roi, equity:r._equity,
                    whyShort:r._whyShort||'', ptype:_ptype(r)};
        }""", row)

    # 0) CONTROL — the base row really does reach STRONG (proves the caps below are meaningful gates
    #    and the residential path is not broken).
    c = run("__TEST_OK", {})
    check("clean residential lead still reaches STRONG", c["verdict"]=="STRONG",
          "verdict="+str(c["verdict"])+" profit="+str(c["profit"]))

    # 1) FIX 3 — commercial parcel: no residential profit, verdict VERIFY, manual-underwrite note.
    cm = run("__TEST_COMM", {"comm":True})
    check("commercial -> VERIFY (not STRONG)", cm["verdict"]=="VERIFY", "verdict="+str(cm["verdict"]))
    check("commercial -> no residential profit printed", cm["profit"] is None, "profit="+str(cm["profit"]))
    check("commercial -> no residential equity printed", cm["equity"] is None, "equity="+str(cm["equity"]))
    check("commercial -> manual-underwrite note", "COMMERCIAL" in cm["whyShort"], cm["whyShort"])
    check("commercial -> _ptype='Commercial'", cm["ptype"]=="Commercial", "ptype="+str(cm["ptype"]))

    # 2) FIX 4 — serial bankruptcy filer can never be STRONG.
    bk = run("__TEST_BK", {"saleBK":4})
    check("serial bankruptcy (4) -> not STRONG", bk["verdict"]!="STRONG", "verdict="+str(bk["verdict"]))

    # 3) FIX 4 — serial staller (dodged multiple sales) can never be STRONG.
    sv = run("__TEST_STALL", {"saleSurv":3})
    check("serial staller (3 sales dodged) -> not STRONG", sv["verdict"]!="STRONG", "verdict="+str(sv["verdict"]))

    # 4) FIX 4 — judgment under attack (motion to set aside / stay pending) -> not STRONG.
    sa = run("__TEST_SETASIDE", {"setAside":True})
    check("judgment under attack -> not STRONG", sa["verdict"]!="STRONG", "verdict="+str(sa["verdict"]))

    # 5) FIX 1 — the "owed" figure the card prints equals the accrued payoff the math uses.
    pay = pg.evaluate("""()=>({acc:_payoffOf({payoff:677000,judg:579000}),
                               frozen:_payoffOf({judg:579000}),
                               none:_payoffOf({})})""")
    check("_payoffOf prefers accrued payoff over frozen judgment", pay["acc"]==677000, "got="+str(pay["acc"]))
    check("_payoffOf falls back to judgment when no payoff baked", pay["frozen"]==579000, "got="+str(pay["frozen"]))

    b.close()

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
