"""Build design-preview.html: a PLAINTEXT, FAKE-DATA copy of the tracker for design work.

Why: the real docs/index.html is encrypted (gated) and leads_final.json is gitignored, so a
designer / a fresh Claude session can't actually SEE the rendered UI. This renders the full UI
with made-up leads (no gate, no real people) so the look can be iterated freely.

Design lives in tracker_template.html (the <style> blocks + render()). Edit THAT, then re-run
`python build_preview.py` and refresh design-preview.html. Never edit docs/index.html by hand
(it is generated + encrypted by foreclosure_leads.make_tracker).

All names/addresses/cases below are INVENTED for layout only.
"""
import json, os
import foreclosure_leads as F

HERE = os.path.dirname(os.path.abspath(__file__))

# Fake leads chosen to exercise every visual state: STRONG / MARGINAL / PASS / VERIFY,
# bank vs HOA vs tax-deed, homestead, phones+emails present vs missing, no-value fallback.
FAKE = [
    {"tier":"A","score":91,"st":"FC","case":"2024-000101-CA-01","auction":"08/10/2026","days":27,
     "filed":2024,"bought":2008,"bprice":205000,"owners":"ROBERT A JOHNSON","oname":"Robert Johnson",
     "addr":"842 NW 9TH ST, MIAMI, FL 33136","mail":"842 NW 9TH ST, MIAMI, FL 33136",
     "value":655000,"judg":286000,"eq":56,"hs":True,"condo":True,"ctype":"Bank/Mortgage",
     "plaintiff":"Wells Fargo Bank, National Association","defs":"Robert A Johnson; Unknown Tenant #1",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "etax":6550,"phones":["3055550101","7865550102","3055550103"],"phdnc":[False,False,True],
     "emails":["rjohnson@example.com","r.johnson@example.net"]},

    {"tier":"A","score":84,"st":"FC","case":"2023-000212-CA-01","auction":"08/03/2026","days":20,
     "filed":2023,"bought":1998,"bprice":92000,"owners":"MARIA C GONZALEZ","oname":"Maria Gonzalez",
     "addr":"1450 SW 27TH AVE, MIAMI, FL 33145","mail":"1450 SW 27TH AVE, MIAMI, FL 33145",
     "value":720000,"judg":41000,"eq":94,"eqfake":True,"hs":True,"ctype":"HOA/Condo","mr":True,
     "plaintiff":"Brickell Bay Condominium Association, Inc.","defs":"Maria C Gonzalez; Mortgage Electronic Registration Systems",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "etax":7200,"phones":["3055550140"],"phdnc":[False],"emails":["mgonzalez@example.com"]},

    {"tier":"A","score":80,"st":"FC","case":"2025-000318-CA-01","auction":"08/17/2026","days":34,
     "filed":2025,"bought":2016,"bprice":330000,"owners":"DAVID R WILLIAMS","oname":"David Williams",
     "addr":"3120 CORAL WAY, MIAMI, FL 33145","mail":"PO BOX 33-1120, MIAMI, FL 33233",
     "value":540000,"judg":250000,"eq":54,"ip":True,"mr":True,"ctype":"Bank/Mortgage",
     "plaintiff":"Gabriel Herrera (individual)","defs":"David R Williams; Susan Williams",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "etax":5400,"phones":[],"phdnc":[],"emails":[]},

    {"tier":"B","score":72,"st":"TD","case":"2026A00219","auction":"08/06/2026","days":23,
     "filed":0,"bought":2004,"bprice":18000,"owners":"GROVER T JACKSON","oname":"Grover Jackson",
     "addr":"18320 SW 117TH AVE, MIAMI, FL 33177","mail":"18320 SW 117TH AVE, MIAMI, FL 33177",
     "value":312000,"obid":15899,"eq":95,"condo":True,"cert":"2023-04871","ctype":"","st":"TD",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","recqs":"sample","ocsqs":"sample",
     "etax":3900,"phones":["7865550188","3055550189"],"phdnc":[False,False],"emails":["gjackson@example.com"]},

    {"tier":"B","score":63,"st":"TD","case":"2026A00244","auction":"08/06/2026","days":23,
     "filed":0,"bought":1991,"bprice":47000,"owners":"ELENA M RIVERA","oname":"Elena Rivera",
     "addr":"725 NE 82ND TER, MIAMI, FL 33138","mail":"725 NE 82ND TER, MIAMI, FL 33138",
     "value":268000,"obid":22400,"eq":58,"hs":True,"cert":"2023-05120","st":"TD",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","recqs":"sample","ocsqs":"sample",
     "etax":3100,"phones":[],"phdnc":[],"emails":[]},

    {"tier":"B","score":58,"st":"FC","case":"2022-000455-CA-01","auction":"08/24/2026","days":41,
     "filed":2022,"bought":2019,"bprice":410000,"owners":"JAMES P OCONNOR","oname":"James Oconnor",
     "addr":"9955 SW 88TH ST, MIAMI, FL 33176","mail":"9955 SW 88TH ST, MIAMI, FL 33176",
     "value":455000,"judg":398000,"eq":13,"ctype":"Bank/Mortgage",
     "plaintiff":"JPMorgan Chase Bank, National Association","defs":"James P Oconnor",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "etax":4550,"phones":["3055550170"],"phdnc":[False],"emails":[]},

    {"tier":"C","score":40,"st":"FC","case":"2021-000560-CA-01","auction":"08/31/2026","days":48,
     "filed":2021,"owners":"UNKNOWN HEIRS OF FRANK MILLER","oname":"","addr":"MULTIPLE PARCELS - SEE CASE","mail":"",
     "value":0,"judg":0,"ju":True,"warn":"parcel not linked - verify property & value via the docket",
     "ctype":"Bank/Mortgage","plaintiff":"Deutsche Bank National Trust Company","defs":"Unknown Heirs; Estate of Frank Miller",
     "docket":"#","ocsqs":"sample","phones":[],"phdnc":[],"emails":[]},
]

# give every human-owner fake lead an address-search link too (mirrors the pipeline's people_addr_url)
for _f in FAKE:
    if _f.get("people") and _f.get("addr") and "," in _f["addr"]:
        _f["peopleaddr"] = "#"

tpl = open(os.path.join(HERE, "tracker_template.html"), encoding="utf-8").read().replace("__UPDATED__", "2026-07-14 12:00")
html = tpl.replace("__DATA__", F._esc_json(FAKE))
out = os.path.join(HERE, "design-preview.html")
open(out, "w", encoding="utf-8").write(html)
print(f"wrote {out}  ({len(FAKE)} sample leads, plaintext, no gate)")
