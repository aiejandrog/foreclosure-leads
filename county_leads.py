"""Generic Florida county lead pipeline — one module, any county.

Every FL county's foreclosure auctions live on the same RealAuction platform (countyname.realforeclose.com),
and the statewide FDOR cadastral (fl_cadastral) enriches owner+value for all of them. So a new county is
just a config row. Outputs <county>_leads.json in the tracker's slim shape, county-tagged, for make_tracker.

    python county_leads.py --county broward
    python county_leads.py --county "palm beach" --dates 3
"""
import argparse, json, os, re, urllib.parse
from datetime import date, datetime
from playwright.sync_api import sync_playwright
import foreclosure_leads as F
import fl_cadastral

HERE = os.path.dirname(os.path.abspath(__file__))
COMPANY_RE = re.compile(r'\b(LLC|CORP|INC|TRUST|ASSOC|ASSN|BANK|COMPANY|HOLDINGS|LP|LTD|USA|COUNTY|CITY OF|CHURCH|MINISTR)\b', re.I)

# One row per county. subdomain -> RealForeclose; co_no -> FDOR cadastral; links -> county PA/tax/clerk.
# Per-county DIRECT deep-links, verified against real parcels (2026-07-16). PA links open the exact
# parcel by folio; tax/records/cases are the correct county portals (some are search-only — see notes).
COUNTIES = {
    'BROWARD': {
        'sub': 'broward', 'td_sub': 'broward', 'co_no': 16,
        # BCPA's Angular SPA can't deep-link; the legacy RecInfo.asp endpoint opens the parcel directly (12-digit folio, no dashes).
        'pa': lambda f: 'https://bcpa.net/RecInfo.asp?URL_Folio=' + f,
        # DIRECT tax bill — the exact URL BCPA's own "Tax Collector" link opens (2026-07-20, Alejandro's
        # find). broward.county-taxes.com 403s header-less bots (Cloudflare), but resolves to HTTP 200 with
        # the live bill in a REAL browser (verified in headless Chromium — CF passes when JS runs). Account =
        # 12-digit folio formatted 6-2-4 (514214012810 -> 514214-01-2810).
        'tax': lambda f: ('https://broward.county-taxes.com/public/real_estate/parcels/'
                          + f[0:6] + '-' + f[6:8] + '-' + f[8:12] + '/bills') if len(f) >= 12 else 'https://broward.county-taxes.com/public/real_estate/search',
        'records': 'https://officialrecords.broward.org/AcclaimWeb/search/SearchTypeName',   # lands on the NAME search form (via disclaimer), not the generic portal
        'cases': 'https://www.browardclerk.org/Web2/CaseSearchECA/',    # court case search
    },
    'PALM BEACH': {
        'sub': 'palmbeach', 'td_sub': 'palmbeach', 'co_no': 60,
        'pa': lambda f: 'https://pbcpao.gov/Property/Details?parcelId=' + f,   # direct parcel (17-digit PCN, no dashes)
        # DIRECT tax bill — the exact URL PBCPAO's "Tax Collector" button opens (2026-07-20, Alejandro's
        # find): onClickTaxCollector() -> PropertyTax.aspx?s=ParcelID:{PCN}. PCN = 17-digit folio formatted
        # 2-2-2-2-2-3-4 (38434415060062990 -> 38-43-44-15-06-006-2990). Verified HTTP 200 with the PCN echoed.
        'tax': lambda f: ('https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=ParcelID%3A'
                          + f[0:2]+'-'+f[2:4]+'-'+f[4:6]+'-'+f[6:8]+'-'+f[8:10]+'-'+f[10:13]+'-'+f[13:17]
                          + '&pg=1&g=-1&moduleId=449') if len(f) >= 17 else 'https://pbctax.publicaccessnow.com/',
        'records': 'https://erec.mypalmbeachclerk.com/search/index?theme=.blue&section=searchCriteriaName&quickSearchSelection=',   # Landmark NAME search
        'cases': 'https://appsgp.mypalmbeachclerk.com/eCaseView/',       # court case search
    },
}


def _rec_name(owner):
    """'Last, First' for the Official-Records / court-case NAME search (AcclaimWeb + Landmark want last-first)
    — the OPPOSITE of the TruePeopleSearch order. Copied to the clipboard when the Records/Cases button is clicked."""
    s = re.sub(r'\s*&.*$', '', owner or '')
    s = re.sub(r'\b(H/[EW]|ET\s?UX|ET\s?AL|TRS?|JR|SR|II+|III|IV|LE|REM)\b', '', s, flags=re.I).strip()
    if ',' in s:
        last, _, first = s.partition(',')
    else:
        toks = s.split()
        if len(toks) < 2:
            return s.strip()
        last, first = toks[0], ' '.join(toks[1:])
    return (last.strip() + ', ' + first.strip()).strip(', ')


def _people_name(owner):
    """'First Last' for a TruePeopleSearch NAME query from an FDOR owner name (stored as LAST FIRST[,] MIDDLE)."""
    s = re.sub(r'\s*&.*$', '', owner or '')
    s = re.sub(r'\b(H/[EW]|ET\s?UX|ET\s?AL|TRS?|JR|SR|II+|III|IV|LE|REM|EST|ESTATE)\b', '', s, flags=re.I)
    if ',' in s:
        last, _, first = s.partition(',')
    else:
        toks = [t for t in s.split() if len(t.strip('.')) > 1]
        if len(toks) < 2:
            return ''
        last, first = toks[0], toks[1]
    last = last.strip('. '); first = (first.split() or [''])[0].strip('. ')
    return (first + ' ' + last).strip() if (first and last) else ''


def _clean_owner(name):
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', name or '', flags=re.I)
    s = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|TRS|JR|SR|II|III|IV|ETAL|ET AL|LE|REM)\b', '', s, flags=re.I)
    # DANGLING '&' = the state cadastral (FDOR OWN_NAME, single line) dropped the co-owner line the
    # county roll actually holds — 'HERNANDEZ NELSON &' is really Nelson + Florencia Hernandez.
    # Strip it BEFORE the comma flip: afterwards the flip welds it mid-string ('RAINES,JAMES A &'
    # -> 'JAMES A & RAINES'), which reads like a complete two-party name and is how a half-name
    # ends up in a skip-trace. Mirrors foreclosure_leads.py's Miami-Dade path, which already did this.
    s = re.sub(r'\s*&\s*$', '', s)
    if ',' in s:
        a, _, b = s.partition(','); s = (b + ' ' + a).strip()
    return re.sub(r'\s{2,}', ' ', s).strip()


def _owner_partial(raw):
    """Is this owner name KNOWN-incomplete? Two shapes: a dangling '&' (co-owner line dropped), or a
    value sitting at the roll's 30-char ceiling ('FLORES MARTINEZ MARIA DEL CARM') which carries no
    marker at all. Either way the operator must not skip-trace it as if it were a full name."""
    o = (raw or '').strip()
    return bool(re.search(r'&\s*$', o)) or len(o) >= 30


def scrape_county(cfg, max_dates=0):
    """Scrape BOTH RealAuction platforms this county runs on: <sub>.realforeclose.com (mortgage
    foreclosures) AND <sub>.realtaxdeed.com (tax-deed sales — Jose's lane). Same DOM, same scraper;
    each item is tagged with its origin base so the auction deep-link points to the right site."""
    fc_base = f"https://{cfg['sub']}.realforeclose.com/index.cfm"
    td_base = f"https://{cfg['td_sub']}.realtaxdeed.com/index.cfm" if cfg.get('td_sub') else None
    leads = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_context(user_agent=F.UA, viewport={"width": 1400, "height": 1000}).new_page()
        for base in [fc_base] + ([td_base] if td_base else []):
            try:
                dates = F.discover_dates(page, base=base)
            except Exception as e:
                print(f"  {base}: calendar failed ({str(e)[:70]}) — skipping this platform")
                continue
            if max_dates:
                dates = dates[:max_dates]
            for d, st in dates:
                for r in F.scrape_date(page, d, st, base=base):
                    r['_base'] = base
                    leads += [r]
        b.close()
    seen, out = set(), []
    for r in leads:
        k = (r.get('Case #') or r.get('Address', '')) + r.get('AuctionDate', '')
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out, fc_base


def to_slim(county, cfg, base, items):
    today = date.today()
    slim = []
    for r in items:
        folio = re.sub(r'\D', '', r.get('Folio', '') or '')
        judg = F.money(r.get('Final Judgment Amount', ''))
        # TRUE type from the auction plaintiff (bank-charter guard wins first); the case-number prefix is
        # only the fallback when the auction gives no plaintiff. The recorded-chain plaintiff later overrides
        # this in make_tracker() for any lead we actually trace (broward_liens).
        ftype = F._fc_type_plaintiff(r.get('Plaintiff', '')) or F._fc_type(r.get('Case #', ''))
        addr = F._clean_addr(r.get('Address', ''))
        val = 0; owner = ''; hs = False; mail = ''; bprice = 0; bought = 0; condo = False; oname = ''; vac = False; opart = False
        if folio:
            try: info = fl_cadastral.enrich(parcel_id=folio)
            except Exception: info = None
            if info:
                val, owner, hs = info['market_value'], info['owner'], info['homestead']
                mail, bprice, bought = info['mail_addr'], info['last_sale_price'], info['last_sale_year']
                condo = bool(re.search(r'CONDO', info.get('legal', ''), re.I)) or str(info.get('use_code', '')) in ('0400', '400', '04')
                # VACANT LAND: FDOR use code 0 = vacant residential (e.g. '000'/'0000'), 10 vacant
                # commercial, 40 vacant industrial, 70 vacant institutional. No homeowner + speculative
                # land value = a systematic false-positive for the homeowner-rescue model.
                _uc = str(info.get('use_code', '') or '').strip()
                vac = (_uc.lstrip('0') == '') and _uc != '' or _uc in ('10', '1000', '40', '4000', '70', '7000')
                oname = _clean_owner(owner)
                opart = _owner_partial(owner)   # co-owner dropped / 30-char roll clip -> never treat as a full name
        try:
            days = (datetime.strptime(r.get('AuctionDate', ''), '%m/%d/%Y').date() - today).days
        except Exception:
            days = -1
        st = r.get('sale_type', 'FC')
        # TAX DEED: there is no court judgment — the OPENING BID (delinquent certs + fees) is the deal
        # basis. Mirror Miami-Dade (foreclosure_leads.py sets judg=opening_bid for TD) so the equity
        # spread, the 'owed' cell and the deal model all read the bid, not a fake $0-judgment -> 100%
        # -equity / VERIFY. Without this, every county tax deed rendered VERIFY with no profit.
        obid_val = F.money(r.get('Opening Bid', '')) if st == 'TD' else 0
        if st == 'TD' and obid_val:
            judg = obid_val
        eqp = round((val - judg) / val * 100) if val else 0
        is_co = bool(COMPANY_RE.search(owner))
        # FANTASY-EQUITY GUARD — plaintiff-free mirror of foreclosure_leads.py suspect_equity (line ~543).
        # County scrapes rarely carry a plaintiff, so MD's plaintiff-gated guard can't fire here, which
        # let $29k-judgment / $1.2M-value HOA cases render as "98% equity STRONG bank deals". A judgment
        # that is a small fraction of value is the signature of a JUNIOR lien (HOA/COA/junior note) with a
        # senior 1st mortgage surviving unshown -> the shown equity is gross/fake. Exempt a confirmed bank
        # plaintiff (its judgment IS the senior debt) and tax deeds (no mortgage survives a tax sale).
        _bank_pl = F._fc_type_plaintiff(r.get('Plaintiff', '')) == 'MORTGAGE'
        suspect_equity = (st != 'TD') and (not _bank_pl) and bool(val) and judg > 0 and (judg / val) < 0.20 and eqp >= 40
        mr = (ftype == 'HOA') or suspect_equity
        eqfake = mr
        # Fake equity must NOT rank a junior-lien lead as a 98%-equity Tier-A deal. Zero it for score/tier
        # (mirrors MD, which awards equity points only when 'not is_hoa'); the gross % still shows in the
        # cell, muted, and the UI verdict engine forces VERIFY off mr=True.
        # A MISSING judgment ($0 from an unposted 'Final Judgment Amount') is NOT $0 owed — it makes
        # eqp read a fantasy 100%. The suspect-equity guard needs judg>0, so it can't catch this; mirror
        # Miami-Dade's judgment_unknown handling and never credit equity when the debt is unknown.
        judg_unknown = (st != 'TD') and (judg <= 0)
        eff_eq = 0 if (eqfake or judg_unknown) else eqp
        score = max(0, min(100, round(eff_eq) + (10 if hs else 0) + (10 if 0 <= days <= 30 else 0))) if val else 0
        tier = 'A' if (val and eff_eq >= 40 and 0 <= days <= 45) else ('B' if val and eff_eq >= 15 else 'C')
        if judg_unknown:
            tier = 'C'; score = min(score, 40)
        # city-only address — can't be mailed/driven/knocked; cap at C (mirrors the MD disqualifier)
        no_street = not re.match(r'^\s*(?:\d[\d-]*|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\s+\S', addr or '', re.I)
        if no_street:
            tier = 'C'; score = min(score, 40)
        z = 'https://www.zillow.com/homes/' + urllib.parse.quote((addr or folio) + ' FL') + '_rb/'
        # deep-link to the platform this item actually came from (realforeclose vs realtaxdeed)
        _ab = r.get('_base', base)
        auc = _ab + '?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=' + r.get('AuctionDate', '') + ('#AITEM_' + r['AID'] if r.get('AID') else '')
        # People NAME search — TruePeopleSearch wants "First Last". FDOR owner names are "LAST FIRST[,] MIDDLE",
        # so build the query with _people_name() (handles both comma + space forms). zip is at the END of the
        # address (not the street number). Skip companies/trusts and address-named entities ("...LAND TR").
        zip5 = (re.search(r'(\d{5})(?:-\d{4})?\s*$', addr) or [None, ''])[1] if addr else ''
        _nm = _people_name(owner)
        _ent = bool(re.search(r'\b(TR|TRS|EST|ESTATE|FUND|PROPERT|REALTY|HOMES|GROUP|INVEST|ENTERPRISE|LAND|ASSN|ASSOC)\b', owner, re.I))
        if _nm and not is_co and not _ent and not re.match(r'^\s*\d', owner or ''):
            people = 'https://www.truepeoplesearch.com/results?name=' + urllib.parse.quote(_nm) + ('&citystatezip=' + zip5 if zip5 else '')
        else:
            people = ''
        # People-by-ADDRESS (pinpoints the owner among same-name strangers) — reuse the Miami-Dade builder.
        peopleaddr = F.people_addr_url(mail, addr, is_co or _ent)
        # CyberBackgroundChecks NAME search (free detail page: phones w/ last-reported date, emails,
        # relatives+associates — verified 2026-07-17 to out-return BatchData on both a Broward and a
        # Sunrise lead). Same gate as the TPS name search: skip companies/trusts/address-named entities.
        cyberbg = F.cyberbg_url(_nm, addr) if (_nm and not is_co and not _ent) else ''
        cyberbgaddr = F.cyberbg_addr_url(mail, addr, is_co or _ent)
        slim.append({
            'county': county, 'tier': tier, 'score': score, 'auction': r.get('AuctionDate', ''), 'days': days,
            'case': r.get('Case #', ''), 'owners': owner or '(owner via title search)', 'oname': oname, 'rname': _rec_name(owner),
            'addr': addr, 'mail': mail, 'value': val, 'judg': judg, 'eq': eqp, 'eqfake': eqfake, 'hs': hs, 'condo': condo,
            'vac': vac, 'co': bool(COMPANY_RE.search(owner or '')), 'opart': opart,
            # TAX DEED: the opening bid (certs + fees) and certificate number are the deal inputs —
            # map them so the TD branch of the deal model (winbid off obid) and the row's Certificate #
            # both work for BW/PB just like Miami-Dade. FC leads have neither and stay 0/''.
            'st': st, 'obid': obid_val, 'folio': folio, 'zillow': z, 'pa': cfg['pa'](folio) if folio else '',
            'tax': cfg['tax'](folio) if folio else '', 'auc': auc, 'people': people, 'peopleaddr': peopleaddr, 'cyberbg': cyberbg, 'cyberbgaddr': cyberbgaddr,
            'ctype': ('HOA' if ftype == 'HOA' else 'Bank/Mortgage'), 'ftype': ftype, 'plaintiff': r.get('Plaintiff', ''), 'defs': '', 'named': [],
            # county leads have no per-case docket token (no clerk enrichment) -> no Docket button; the
            # Records/Cases buttons point to THIS county's official-records + court-case search portals.
            'docket': '', 'records': cfg['records'], 'cases': cfg['cases'],
            'cstatus': '', 'mr': mr, 'ip': False, 'ju': judg_unknown,
            'bought': bought, 'bprice': bprice, 'filed': 0, 'etax': 0,
            'warn': (('no street address - verify parcel first' if no_street else '') if val else 'no cadastral match - verify parcel + value'), 'recqs': '', 'ocsqs': '', 'cert': r.get('Certificate #', ''),
        })
    return slim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--county', required=True)
    ap.add_argument('--dates', type=int, default=0)
    a = ap.parse_args()
    key = a.county.upper().strip()
    if key not in COUNTIES:
        raise SystemExit(f"unknown county '{a.county}'. Have: {', '.join(COUNTIES)}")
    cfg = COUNTIES[key]
    print(f"scraping {key} auctions ({cfg['sub']}.realforeclose.com + {cfg.get('td_sub', cfg['sub'])}.realtaxdeed.com)...")
    items, base = scrape_county(cfg, a.dates)
    print(f"scraped {len(items)}; enriching via statewide cadastral (CO_NO={cfg['co_no']})...")
    slim = to_slim(key, cfg, base, items)
    got = sum(1 for s in slim if s['value']); aN = sum(1 for s in slim if s['tier'] == 'A')
    out = os.path.join(HERE, key.lower().replace(' ', '') + '_leads.json')
    # Safety guard (matches foreclosure_leads.py): a blocked/thin scrape must NEVER overwrite a good
    # file with an empty one. Bail and leave the last good snapshot in place so the site stays populated.
    MIN = 10
    if len(slim) < MIN:
        prev = 0
        if os.path.exists(out):
            try: prev = len(json.load(open(out, encoding='utf-8')))
            except Exception: prev = 0
        print(f"ABORT: only {len(slim)} {key} leads scraped (< {MIN}). Keeping the existing {prev}-lead file.")
        raise SystemExit(1)
    # Preserve photos across the refresh (see photo_carry): carry from the previous county snapshot
    # BEFORE overwriting it, so returning leads keep their images even if the photo pass never runs.
    from photo_carry import carry_photos
    _carried = carry_photos(slim, out)
    if _carried: print(f"carried photos forward for {_carried} returning {key} leads")
    json.dump(slim, open(out, 'w', encoding='utf-8'), indent=1)
    print(f"DONE: {len(slim)} {key} leads ({got} enriched, {aN} Tier A) -> {os.path.basename(out)}")


if __name__ == '__main__':
    main()
