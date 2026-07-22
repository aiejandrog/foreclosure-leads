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
     "plaintiff":"BANK OF NEW YORK MELLON (THE)","defs":"Robert A Johnson; Unknown Tenant #1; Solarcity Corporation",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"ok","orftype":"MTG","orsurv":225000,"orjuniors":45000,"orjunior":225000,
     "orliens":[
       {"d":"01/15/2005","amt":180000,"party":"JP MORGAN CHASE BANK NA","bp":"MORTGAGE","st":"OPEN","_dt":"2005-01-15","role":"senior"},
       {"d":"06/01/2018","amt":295000,"party":"BANK OF NEW YORK MELLON (THE)","bp":"MORTGAGE","st":"OPEN","_dt":"2018-06-01","role":"fore"},
       {"d":"09/12/2019","amt":45000,"party":"SOLARCITY CORPORATION","bp":"MORTGAGE","st":"OPEN","_dt":"2019-09-12","role":"junior"},
       {"d":"03/01/2005","amt":50000,"party":"OLD HELOC LLC","bp":"MORTGAGE","st":"SATISFIED","_dt":"2005-03-01"}
     ],
     "etax":6550,"phones":["3055550101","7865550102","3055550103"],"phdnc":[False,False,True],
     "emails":["rjohnson@example.com","r.johnson@example.net"],
     # real Zillow CDN photos so the preview exercises thumbnails / multi-photo badge / gallery
     "photo_kind":"zillow","zlisting":"https://www.zillow.com/homedetails/525-W-79th-Pl-Hialeah-FL-33014_zpid/",
     "photos":["https://photos.zillowstatic.com/fp/7d087b8613e1f4e9ffb3d0e5d0b73cbe-cc_ft_1536.jpg",
               "https://photos.zillowstatic.com/fp/be6d9716af6e4624fc6c277602cd0812-cc_ft_1536.jpg",
               "https://photos.zillowstatic.com/fp/b7ecf3eaa28e02be4a75d7c3c11f4312-cc_ft_1536.jpg",
               "https://photos.zillowstatic.com/fp/a45b6c7cb46ae5f08d740408729a8de5-cc_ft_1536.jpg",
               "https://photos.zillowstatic.com/fp/81cb77dc08a83445a33fc0c76476827a-cc_ft_1536.jpg"]},

    {"tier":"A","score":84,"st":"FC","case":"2023-000212-CA-01","auction":"08/03/2026","days":20,
     "filed":2023,"bought":1998,"bprice":92000,"owners":"MARIA C GONZALEZ","oname":"Maria Gonzalez",
     "addr":"1450 SW 27TH AVE, MIAMI, FL 33145","mail":"1450 SW 27TH AVE, MIAMI, FL 33145",
     "value":720000,"judg":41000,"eq":94,"eqfake":True,"hs":True,"ctype":"HOA/Condo","mr":True,
     "plaintiff":"Brickell Bay Condominium Association, Inc.","defs":"Maria C Gonzalez; Mortgage Electronic Registration Systems",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"ok","orftype":"HOA","orsurv":520000,"orsurvfirst":420000,"orjuniors":0,"orjunior":520000,
     "orliens":[
       {"d":"04/02/2001","amt":420000,"party":"WELLS FARGO BANK NA","bp":"MORTGAGE","st":"OPEN","_dt":"2001-04-02","role":"senior"},
       {"d":"11/18/2012","amt":100000,"party":"BANK OF AMERICA NA","bp":"HELOC","st":"OPEN","_dt":"2012-11-18","role":"senior"}
     ],
     "etax":7200,"phones":["3055550140"],"phdnc":[False],"emails":["mgonzalez@example.com"],
     # single satellite aerial served from docs/img/ (relative path resolves beside design-preview.html)
     "photo_kind":"aerial","photos":["docs/img/00404213000006120.jpg"]},

    {"tier":"A","score":80,"st":"FC","case":"2025-000318-CA-01","auction":"08/17/2026","days":34,
     "filed":2025,"bought":2016,"bprice":330000,"owners":"DAVID R WILLIAMS","oname":"David Williams",
     "addr":"3120 CORAL WAY, MIAMI, FL 33145","mail":"PO BOX 33-1120, MIAMI, FL 33233",
     "value":540000,"judg":250000,"eq":54,"ip":True,"mr":True,"ctype":"Bank/Mortgage",
     "plaintiff":"Gabriel Herrera (individual)","defs":"David R Williams; Susan Williams",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     # STRESS: no orliens — empty chain / must pull records by hand
     "etax":5400,"phones":[],"phdnc":[],"emails":[]},

    {"tier":"B","score":72,"st":"TD","case":"2026A00219","auction":"08/06/2026","days":23,
     "filed":0,"bought":2004,"bprice":18000,"owners":"GROVER T JACKSON","oname":"Grover Jackson",
     "addr":"18320 SW 117TH AVE, MIAMI, FL 33177","mail":"18320 SW 117TH AVE, MIAMI, FL 33177",
     "value":312000,"obid":15899,"eq":95,"condo":True,"cert":"2023-04871","ctype":"","st":"TD",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","recqs":"sample","ocsqs":"sample",
     "etax":3900,"phones":["7865550188","3055550189"],"phdnc":[False,False],"emails":["gjackson@example.com"],
     "photo_kind":"street","photos":["docs/img/3049120530370_sv.jpg"]},

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
     # STRESS: foreclosing loan ONLY — surviving senior must read $0 / none found
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"ok","orftype":"MTG","orsurv":0,"orjuniors":0,"orjunior":0,
     "orliens":[
       {"d":"02/14/2019","amt":410000,"party":"JPMORGAN CHASE BANK NATIONAL ASSOCIATION","bp":"MORTGAGE","st":"OPEN","_dt":"2019-02-14","role":"fore"},
       {"d":"05/01/2015","amt":380000,"party":"QUICKEN LOANS INC","bp":"MORTGAGE","st":"SATISFIED","_dt":"2015-05-01"}
     ],
     "etax":4550,"phones":["3055550170"],"phdnc":[False],"emails":[]},

    {"tier":"A","score":77,"st":"FC","case":"2024-000777-CA-01","auction":"08/12/2026","days":29,
     "filed":2024,"bought":2010,"bprice":275000,"owners":"ANITA L PEREZ","oname":"Anita Perez",
     "addr":"610 NE 57TH ST, MIAMI, FL 33137","mail":"610 NE 57TH ST, MIAMI, FL 33137",
     "value":610000,"judg":195000,"eq":68,"hs":True,"ctype":"Bank/Mortgage",
     "plaintiff":"U.S. BANK NATIONAL ASSOCIATION","defs":"Anita L Perez; Unknown Tenant",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     # STRESS: junior NOT named in suit → TITLE RISK
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"ok","orftype":"MTG","orsurv":0,"orjuniors":62000,"orjunior":62000,
     "orliens":[
       {"d":"08/20/2017","amt":210000,"party":"U.S. BANK NATIONAL ASSOCIATION","bp":"MORTGAGE","st":"OPEN","_dt":"2017-08-20","role":"fore"},
       {"d":"01/09/2020","amt":62000,"party":"SUNRUN INC","bp":"MORTGAGE","st":"OPEN","_dt":"2020-01-09","role":"junior"}
     ],
     "etax":6100,"phones":["3055550199"],"phdnc":[False],"emails":["aperez@example.com"]},

    {"tier":"A","score":74,"st":"FC","case":"2023-000888-CA-01","auction":"08/19/2026","days":36,
     "filed":2023,"bought":2005,"bprice":190000,"owners":"CARLOS M RUIZ","oname":"Carlos Ruiz",
     "addr":"2211 SW 16TH ST, MIAMI, FL 33145","mail":"2211 SW 16TH ST, MIAMI, FL 33145",
     "value":580000,"judg":240000,"eq":59,"hs":True,"ctype":"Bank/Mortgage",
     "plaintiff":"DEUTSCHE BANK NATIONAL TRUST COMPANY","defs":"Carlos M Ruiz; GreenSky LLC; Unknown Tenant",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     # STRESS: two seniors + named junior + unnamed junior (mixed)
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"low","orftype":"MTG","orsurv":265000,"orjuniors":90000,"orjunior":265000,
     "orliens":[
       {"d":"03/10/2005","amt":200000,"party":"CITIMORTGAGE INC","bp":"MORTGAGE","st":"OPEN","_dt":"2005-03-10","role":"senior"},
       {"d":"06/22/2007","amt":65000,"party":"COUNTRYWIDE HOME LOANS","bp":"HELOC","st":"OPEN","_dt":"2007-06-22","role":"senior"},
       {"d":"09/01/2016","amt":255000,"party":"DEUTSCHE BANK NATIONAL TRUST COMPANY","bp":"MORTGAGE","st":"OPEN","_dt":"2016-09-01","role":"fore"},
       {"d":"02/14/2018","amt":38000,"party":"GREENSKY LLC","bp":"MORTGAGE","st":"OPEN","_dt":"2018-02-14","role":"junior"},
       {"d":"07/01/2019","amt":52000,"party":"ORANGE SOLAR HOLDINGS LLC","bp":"MORTGAGE","st":"OPEN","_dt":"2019-07-01","role":"junior"}
     ],
     "etax":5800,"phones":["7865550111"],"phdnc":[False],"emails":[]},

    {"tier":"B","score":55,"st":"FC","case":"2022-000999-CA-01","auction":"08/26/2026","days":43,
     "filed":2022,"bought":2011,"bprice":155000,"owners":"PATRICIA S LEE","oname":"Patricia Lee",
     "addr":"8840 NW 17TH AVE, MIAMI, FL 33147","mail":"8840 NW 17TH AVE, MIAMI, FL 33147",
     "value":390000,"judg":175000,"eq":55,"ctype":"Bank/Mortgage",
     "plaintiff":"NEWREZ LLC DBA SHELLPOINT MORTGAGE SERVICING","defs":"Patricia S Lee",
     "pa":"#","zillow":"#","tax":"#","auc":"#","people":"#","docket":"#","recqs":"sample","ocsqs":"sample",
     # STRESS: unstamped roles — UI must infer fore from plaintiff name match
     "orurl":"https://onlineservices.miamidadeclerk.gov/officialrecords/StandardSearch",
     "orconf":"ok","orftype":"MTG","orsurv":90000,"orjuniors":25000,"orjunior":90000,
     "orliens":[
       {"d":"12/01/2008","amt":90000,"party":"HSBC BANK USA","bp":"MORTGAGE","st":"OPEN","_dt":"2008-12-01"},
       {"d":"05/15/2015","amt":180000,"party":"NEWREZ LLC DBA SHELLPOINT MORTGAGE SERVICING","bp":"MORTGAGE","st":"OPEN","_dt":"2015-05-15"},
       {"d":"11/02/2018","amt":25000,"party":"SYNCHRONY BANK","bp":"MORTGAGE","st":"OPEN","_dt":"2018-11-02"}
     ],
     "etax":3900,"phones":["3055550122"],"phdnc":[False],"emails":[]},

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
