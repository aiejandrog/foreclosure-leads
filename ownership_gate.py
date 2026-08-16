#!/usr/bin/env python
"""ownership_gate.py — the last check before a human knocks a door or dials a homeowner.

THE FAILURE THIS EXISTS TO STOP (Milouse Joseph, 8208 NW 57 PL Tamarac, CACE-24-006635)
A lead reached the urgent door/call list showing ~$56k equity. She had ALREADY been foreclosed
out months earlier by her HOA (a SEPARATE case, CONO-23-008222) — a Certificate of Title moved
the house to a third party (Cynthia Sewell, $20,100). She owned nothing and had nothing to sell.

Why every existing gate missed it:
  * value/equity is priced off the FDOR cadastral just-value — pure phantom equity on a sold house;
  * the cadastral OWN_NAME field LAGS and still showed the OLD owner (== the foreclosure
    defendant), so an owner-vs-defendant check ON CADASTRAL DATA would have matched and passed too;
  * the transfer was recorded under a DIFFERENT case, so the lead's own docket looked clean.

The only reliable tells, both confirmed live for this exact folio:
  1. a LIVE property-appraiser owner lookup returns the CURRENT owner (Sewell) even while the
     annual cadastral roll still shows the old one;
  2. the appraiser Sales History shows a 'Certificate of Title' (BCPA code CET) dated AFTER
     the case was filed.

So this module, for the SMALL set of leads that reach a call sheet / door book (dozens a night,
not thousands), hits the county appraiser LIVE per folio and answers ONE question: does the person
we are about to contact still own this house?

TRI-STATE CONTRACT (never silent):
  'clear'       -> live current owner still matches the defendant, no post-filing Certificate of
                   Title. ONLY this may reach a live call/door.
  'transferred' -> live owner is a DIFFERENT person, OR (owner unreadable) a Certificate-of-Title /
                   tax-deed dated >= case filing. The house is gone. HOLD it.
  'unverified'  -> appraiser unreachable / parse failed / no folio / ambiguous name match (shared
                   surname only — could be a spouse or a different person). NEVER silently pass,
                   NEVER silently drop — STAMP 'OWNERSHIP UNVERIFIED — CHECK' and cap it.

Red-team fixes baked in (2026-08-14):
  * cache key is folio|defendant|filed, NOT folio alone — a stranger-defendant on the same parcel
    must be compared live, never served a cached 'clear' (that WAS the Milouse fail-open);
  * two shared given names is NOT a match — a SURNAME must be shared (wrong-Garcia guard);
  * compound/apostrophe surnames are glued before compare (DE LA CRUZ == DELACRUZ, O'CONNOR ==
    OCONNOR) so the two counties' different spacing does not read as a transfer;
  * a post-filing deed does NOT flag 'transferred' when the live owner is unchanged (routine
    quitclaim-into-own-trust) — it downgrades to 'unverified';
  * shared-surname-only (spouse vs different-person) -> 'unverified', never a silent drop.

Broward is read from the LEGACY folio page bcpa.net/RecInfo.asp?URL_Folio= (owner + Sales-History
codes), confirmed live. Miami-Dade uses the MDCPA proxy (OwnerInfos + SalesInfos). Palm Beach and
others are not wired -> 'unverified' (fail safe).

Cache: ownership_cache.json, 7-day TTL (ownership changes; short TTL, not permanent). Written once
per batch, and always persisted even on a zero-fetch warm-cache night.

CLI:
  python ownership_gate.py --folio 494109022120 --owner "JOSEPH, MILOUSE" --county BROWARD --filed 12/12/2023
  python ownership_gate.py --selftest        # name-matcher truth table, no network
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'ownership_cache.json')
TTL_DAYS = 7

MD_PROXY = ('https://apps.miamidadepa.gov/PApublicServiceProxy/PaServicesProxy.ashx'
            '?Operation=GetPropertySearchByFolio&clientAppName=PropertySearch&folioNumber=%s')
BCPA_RECINFO = 'https://bcpa.net/RecInfo.asp?URL_Folio=%s'   # legacy folio page: owner + Sales History
PBPAO = 'https://pbcpao.gov/Property/Details?parcelId=%s'    # server-rendered folio page: owner + Sales

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/126.0 Safari/537.36')

# ---------------------------------------------------------------------------------------------
# NAME NORMALISATION / COMPARE
# ---------------------------------------------------------------------------------------------
_STOP = {
    'THE', 'OF', 'AND', 'A', 'AN',
    'LLC', 'INC', 'CORP', 'CO', 'LP', 'LLP', 'PLLC', 'LTD', 'DMCC',
    'TRUST', 'TR', 'TRS', 'TRUSTEE', 'TTEE', 'REV', 'LIV', 'LIVING', 'FAM', 'FAMILY',
    'EST', 'ESTATE', 'ESTATES', 'HEIRS', 'HRS', 'ETAL', 'ETUX', 'ETVIR', 'ET',
    'AL', 'UX', 'VIR', 'H', 'W', 'HE', 'WH', 'E',
    'JR', 'SR', 'II', 'III', 'IV', 'V',
    'MR', 'MRS', 'MS', 'DR',
    'HOLDINGS', 'PROPERTIES', 'GROUP', 'ENTERPRISES', 'INVESTMENTS', 'INVESTMENT',
    'VENTURES', 'VENTURE', 'REALTY', 'CAPITAL', 'PARTNERS', 'MANAGEMENT', 'ASSOCIATES',
    'FUND', 'ASSN', 'ASSOC', 'ASSOCIATION', 'CONDOMINIUM', 'CONDO',
}

# Surname particles that the two county sources space differently. Glue them to the next token so
# "DE LA CRUZ" == "DELACRUZ" and "O CONNOR" == "OCONNOR" instead of reading as two different people.
_PARTICLES = {'DE', 'DEL', 'DELA', 'DELAS', 'DELOS', 'LA', 'LAS', 'LOS', 'LE', 'VAN', 'VON', 'DER',
              'DEN', 'DI', 'DA', 'DOS', 'DAS', 'ST', 'SAN', 'SANTA', 'MC', 'MAC', 'O', 'D'}

_ENTITY = re.compile(r'\b(LLC|L\.L\.C|INC|CORP|LP|LLP|PLLC|LTD|TRUST|HOLDINGS|PROPERTIES|GROUP|'
                     r'ENTERPRISES|INVESTMENTS?|VENTURES?|REALTY|CAPITAL|PARTNERS|MANAGEMENT|'
                     r'ASSOCIATES|FUND)\b', re.I)


def _norm_words(name):
    """Uppercase word list with surname particles glued to the following token."""
    s = re.sub(r"[',.]", '', str(name or '').upper())
    s = re.sub(r'[^A-Z ]', ' ', s)
    words = s.split()
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w in _PARTICLES and i + 1 < len(words):
            glue = w
            i += 1
            while i < len(words) and words[i] in _PARTICLES and i + 1 < len(words):
                glue += words[i]
                i += 1
            glue += words[i]
            out.append(glue)
        else:
            out.append(w)
        i += 1
    return out


# Strings the pipeline uses when it has NO owner name. These are not people; comparing them to a live
# owner manufactured a "transferred" verdict on real leads (e.g. "(owner via title search)").
_PLACEHOLDER_RX = re.compile(
    r'^\s*[\(\[]?\s*(owner\b.*|unknown|unk|n/?a|none|null|see\s+title|title\s+search|tbd|pending)'
    r'\s*[\)\]]?\s*$', re.I)


def _is_placeholder(name):
    n = str(name or '').strip()
    return (not n) or bool(_PLACEHOLDER_RX.match(n))


def _tokens(name, glue=True):
    """Significant, order-free tokens. Middle initials and entity/estate noise dropped.
    glue=False skips surname-particle gluing — see owner_relation for why both are tried."""
    words = _norm_words(name) if glue else re.sub(r"[',.]", ' ', str(name or '').upper()).split()
    return {re.sub(r'[^A-Z]', '', w) for w in words
            if len(re.sub(r'[^A-Z]', '', w)) > 1 and re.sub(r'[^A-Z]', '', w) not in _STOP}


def _surnames(name):
    """Candidate surname tokens. County appraiser rolls write "LASTNAME FIRSTNAME" with NO comma
    ("ZELAZNIK FLORALEE", "PUTNEY SALLY EST"), while court/other sources write "FIRST LAST". Guessing
    one convention mislabels the surname and makes a same-family transfer (estate -> heir, husband ->
    wife) read as a STRANGER, which silently kills a live lead. So for a comma-less name BOTH ends are
    treated as candidate surnames — over-inclusive on purpose: a shared surname only downgrades the
    verdict to 'unsure' (stamped for a human), it never clears anything by itself.

    Returns (confident, ambiguous): 'confident' surnames come from an explicit "LAST, FIRST" comma form
    or the trailing token of a natural-order name; 'ambiguous' are the leading tokens of comma-less names
    that are only a surname IF the source used county-roll order. A match resting on an ambiguous
    surname is downgraded to 'unsure' instead of 'same', so "RODRIGUEZ, JOSE LUIS" vs "JOSE LUIS
    MARTINEZ" (two different people sharing given names) is never silently cleared."""
    confident, ambiguous = set(), set()
    for part in re.split(r'[&/]| AND ', str(name or '').upper()):
        part = part.strip()
        if not part:
            continue
        if ',' in part:
            confident |= _tokens(part.split(',')[0])
        else:
            toks = [w for w in _norm_words(part) if len(w) > 1 and w not in _STOP]
            if toks:
                confident.add(toks[-1])   # natural order:     FIRST LAST
                ambiguous.add(toks[0])    # county-roll order: LAST FIRST
    return confident, ambiguous


def _is_entity(name):
    return bool(_ENTITY.search(str(name or '')))


def owner_relation(a, b):
    """'same' | 'different' | 'unsure' — is a the same owner as b?

    'same'      : one name's significant tokens are a subset of the other's, OR they share >=2
                  tokens INCLUDING a surname, OR two entities share a core token.
    'unsure'    : they share ONLY a surname (spouse? or a different person with the same surname?)
                  -> caller must verify, never silently drop.
    'different' : no meaningful overlap -> a real transfer.
    Empty/one-sided input -> 'same' (never manufacture a transfer from missing data).
    """
    # A placeholder ("(owner via title search)", "unknown") is NOT a name — never a transfer.
    if _is_placeholder(a) or _is_placeholder(b):
        return 'same'

    # Surname-particle gluing (DE LA CRUZ -> DELACRUZ) is right for compound Spanish/Irish surnames but
    # WRONG for real short surnames that collide with the particle list — "LE, CHRIS" (Vietnamese LE)
    # glued to "LECHRIS" and stopped matching plain "CHRIS", flagging the same person as a stranger.
    # So evaluate under BOTH tokenizations and keep the most favourable verdict. Bias is deliberate:
    # a false 'different' KILLS a live lead, a false 'same' only fails to catch one — and the
    # independent Certificate-of-Title check still covers the latter.
    best, rank = 'different', {'same': 2, 'unsure': 1, 'different': 0}
    ca, aa = _surnames(a)
    cb, ab = _surnames(b)
    surn_conf, surn_amb = (ca | cb), (aa | ab)
    for glue in (True, False):
        ta, tb = _tokens(a, glue), _tokens(b, glue)
        if not ta or not tb:
            return 'same'
        if ta <= tb or tb <= ta:
            return 'same'                 # every significant token of one is present in the other
        shared = ta & tb
        if len(shared) >= 2 and (shared & surn_conf):
            r = 'same'                    # confident surname + at least one given name
        elif _is_entity(a) and _is_entity(b) and shared:
            r = 'same'
        elif shared & (surn_conf | surn_amb):
            # Only a surname in common (spouse/heir/estate), or the match rests on a county-roll
            # position guess — either way a human decides, and the lead is stamped, never dropped.
            r = 'unsure'
        else:
            r = 'different'
        if rank[r] > rank[best]:
            best = r
    return best


def same_owner(a, b):
    """Back-compat boolean: True unless a real, unambiguous transfer. 'unsure' counts as same so a
    spouse/ambiguous match is never dropped (the gate re-reads 'unsure' as its own verdict)."""
    return owner_relation(a, b) != 'different'


# ---------------------------------------------------------------------------------------------
# DATES + FLIP-DEED DETECTION
# ---------------------------------------------------------------------------------------------
def _parse_date(s):
    s = str(s or '').strip().split('T')[0].split(' ')[0]
    if not s:
        return None
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y', '%m/%d/%y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    if re.fullmatch(r'\d{12,13}', s):
        try:
            return datetime.datetime.utcfromtimestamp(int(s) / 1000).date()
        except Exception:
            return None
    return None


# A "flip" deed = the property changed hands through foreclosure/tax, not a normal owner-controlled
# transfer. BCPA sale codes: CET/CT = Certificate of Title, TXD/TD = Tax Deed. Also match the spelled
# forms from the Miami-Dade proxy. Deliberately NOT quitclaims/warranty deeds (owner-controlled).
_FLIP_RX = re.compile(r'\bCET\b|\bCT\b|\bTXD\b|\bTD\b|CERT\w*\s*(OF\s*)?TITLE|TAX\s*DEED', re.I)


def _flip_from_sales(sales, filed_date):
    """First foreclosure/tax-deed sale dated on/after the case filing -> (date_iso, why)."""
    if not filed_date:
        return None, ''
    for s in sales or []:
        d = _parse_date(s.get('date'))
        if not d or d < filed_date:
            continue
        typ = str(s.get('type') or '')
        if _FLIP_RX.search(typ):
            label = 'Certificate of Title' if re.search(r'CET|CT|TITLE', typ, re.I) else 'Tax Deed'
            return d.isoformat(), '%s dated %s, after the case was filed %s' % (
                label, d.isoformat(), filed_date.isoformat())
    return None, ''


# ---------------------------------------------------------------------------------------------
# LIVE FETCHERS  -> (current_owner:str, sales:list[{date,type,price}])  or raise
# ---------------------------------------------------------------------------------------------
_TIMEOUT = 12
_RETRIES = 2


def _session():
    import requests
    s = requests.Session()
    s.headers.update({'User-Agent': _UA})
    return s


def _fetch_miamidade(folio):
    """MDCPA proxy: OwnerInfos (LIVE current owner) + SalesInfos (folio sale history)."""
    s = _session()
    url = MD_PROXY % re.sub(r'\D', '', str(folio))
    last = None
    for _ in range(_RETRIES):
        try:
            j = s.get(url, timeout=_TIMEOUT).json()
            owner = '; '.join(o.get('Name') for o in (j.get('OwnerInfos') or []) if o.get('Name'))
            sales = []
            for si in (j.get('SalesInfos') or []):
                # verified keys (2026-08-14): SaleInstrument holds the deed type, e.g. a foreclosure
                # 'Certificate of Title'; QualificationDescription carries the qualified/disqualified
                # reason. Grantee is the buyer, useful as a second read.
                typ = ' '.join(x for x in (si.get('SaleInstrument'),
                                           si.get('QualificationDescription')) if x)
                sales.append({'date': si.get('DateOfSale'), 'type': typ,
                              'price': si.get('SalePrice'), 'grantee': si.get('GranteeName1')})
            return owner, sales
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise last or RuntimeError('MD proxy unreachable')


# RecInfo.asp: "Property Owner" label then the owner line; sales rows are  DATE  CODE  $PRICE
# (e.g. "5/22/2026 ... CET-D ... $20,100"). Owner-name chars may include , & ' - and spaces.
#
# 🔴 THE "BILL OF RIGHTS" BUG (found 2026-08-14 by scanning the whole board before trusting this).
# BCPA's page carries a footer link literally titled "Property Owner Bill of Rights". When a folio does
# NOT resolve (bad/short folio — Broward is 12 digits and the lead files carry some 10-digit ones), the
# site still returns a generic page containing that phrase, so this regex matched the FOOTER, returned
# the owner as "Bill of", compared it to the real defendant, and declared the property TRANSFERRED.
# 23 of 250 leads were killed that way in the first full scan. A parse failure must NEVER read as
# evidence of a sale, so: (a) the label match explicitly refuses "Bill of Rights", and (b) the caller
# corroborates that the page is a real parcel record before trusting any owner at all.
_BW_OWNER_RX = re.compile(
    r'Property\s*Owner\s*(?!Bill\s*of\s*Rights)(?:</[^>]+>\s*)*'
    r'((?!Bill\s+of\b)[A-Z][A-Z0-9 ,&\'\.\-/]{3,60})', re.I)
# Phrases that are page furniture, never an owner name.
_BW_NOT_OWNER = re.compile(r'\bBILL\s+OF\s+RIGHTS?\b|\bRIGHTS\b|\bSEARCH\b|\bHOME\b|\bCONTACT\b', re.I)
_BW_SALE_RX = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})'                       # sale date
    r'(?:\s|&nbsp;|<[^>]+>)+?'
    r'([A-Z]{2,4}(?:-[A-Z])?)'                       # BCPA code: CET-D, WD-Q, TXD-D, ...
    r'(?:\s|&nbsp;|<[^>]+>)*?'
    r'(?:\$?\s*([\d,]+))?', re.I | re.S)


def _fetch_broward(folio):
    """Legacy BCPA folio page -> (current owner, sales rows). One GET, no browser, no captcha —
    verified live for folio 494109022120 (owner SEWELL, CYNTHIA; CET-D $20,100 on 5/22/2026)."""
    s = _session()
    fdig = re.sub(r'\D', '', str(folio))
    last = None
    for _ in range(_RETRIES):
        try:
            html = s.get(BCPA_RECINFO % fdig, timeout=_TIMEOUT).text
            flat = re.sub(r'&nbsp;', ' ', html)
            txt = re.sub(r'<[^>]+>', ' ', flat)
            # CORROBORATION: only trust an owner off a page that is genuinely this parcel's record —
            # it must carry a Sales History block AND echo the folio we asked for. A generic/error page
            # satisfies neither, so we return no owner and the caller marks the lead 'unverified'
            # (never 'transferred'). See the Bill-of-Rights note above.
            is_record = ('SALES HISTORY' in txt.upper()) and (fdig in re.sub(r'\D', '', txt))
            om = _BW_OWNER_RX.search(txt) if is_record else None
            owner = re.sub(r'\s+', ' ', om.group(1)).strip(' ,') if om else ''
            if owner and _BW_NOT_OWNER.search(owner):
                owner = ''                                  # page furniture, not a person
            # confine sale parsing to the Sales History block so we don't catch stray dates
            j = flat.upper().find('SALES HISTORY') if is_record else -1
            if j < 0:
                return owner, []          # not a parcel record -> no owner, no sales, caller -> unverified
            seg = re.sub(r'<[^>]+>', ' ', flat[j:j + 2500])
            sales = []
            for m in _BW_SALE_RX.finditer(seg):
                sales.append({'date': m.group(1), 'type': m.group(2),
                              'price': (m.group(3) or '').replace(',', '') or None})
            return owner, sales
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise last or RuntimeError('BCPA RecInfo unreachable')


def _fetch_palmbeach(folio):
    """PBC PAO detail page (server-rendered, keyless) -> (current owner, sales rows). Verified live:
    pbcpao.gov/Property/Details?parcelId=<17-digit PCN> carries an OWNER INFORMATION block and a
    SALES INFORMATION table (DATE / PRICE / OR BOOK-PAGE / SALE TYPE / OWNER)."""
    s = _session()
    fdig = re.sub(r'\D', '', str(folio))
    last = None
    for _ in range(_RETRIES):
        try:
            html = s.get(PBPAO % fdig, timeout=_TIMEOUT).text
            # OWNER: name lines between the OWNER INFORMATION header and the mailing address / action.
            i = html.upper().find('OWNER INFORMATION')
            names = []
            if i >= 0:
                flat = re.sub(r'<[^>]+>', '\n', html[i:i + 1200]).replace('&amp;', '&')
                for ln in flat.split('\n'):
                    ln = re.sub(r'\s+', ' ', ln).strip()
                    if not ln or ln.upper() in ('OWNER(S)', 'MAILING ADDRESS', 'ACTIONS',
                                                'OWNER INFORMATION', '-', '+'):
                        continue
                    if re.match(r'\d', ln) or 'CHANGE OF MAILING' in ln.upper():
                        break
                    if re.match(r"[A-Z][A-Z .,'&/-]{2,}$", ln):
                        names.append(ln.rstrip(' &'))
                    if len(names) >= 4:
                        break
            owner = ' & '.join(names)
            # SALES: for each sale date, the deed type sits a few cells later on the same row.
            j = html.upper().find('SALES INFORMATION')
            sales = []
            if j >= 0:
                seg = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html[j:j + 3000]))
                for m in re.finditer(r'\d{1,2}/\d{1,2}/\d{4}', seg):
                    win = seg[m.end():m.end() + 130]
                    tm = re.search(r'CERTIFICATE OF TITLE|TAX DEED|CERT\w*\s*TITLE|QUIT ?CLAIM|'
                                   r'WARRANTY DEED|SPECIAL WARRANTY|PERS\w*\s*REP', win, re.I)
                    sales.append({'date': m.group(0), 'type': (tm.group(0) if tm else ''),
                                  'price': None})
            return owner, sales
        except Exception as e:
            last = e
            time.sleep(1.0)
    raise last or RuntimeError('PBC PAO unreachable')


# ---------------------------------------------------------------------------------------------
# CACHE  (keyed by folio|defendant|filed — the verdict is per-(parcel, who-we-think-owns-it))
# ---------------------------------------------------------------------------------------------
def _cache_key(folio, defendant, filed):
    fdig = re.sub(r'\W', '', str(folio or ''))
    dn = ''.join(sorted(_tokens(defendant)))
    fd = (_parse_date(filed).isoformat() if _parse_date(filed) else '')
    return '%s|%s|%s' % (fdig, dn, fd)


def _load_cache():
    try:
        return json.load(open(CACHE, encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(cache):
    tmp = CACHE + '.tmp'
    try:
        json.dump(cache, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, CACHE)
    except Exception:
        pass


def _fresh(entry):
    d = _parse_date((entry or {}).get('ts'))
    return bool(d and (datetime.date.today() - d).days < TTL_DAYS)


# ---------------------------------------------------------------------------------------------
# THE GATE
# ---------------------------------------------------------------------------------------------
def check_lead(folio, defendant, filed=None, county='MIAMI-DADE', cache=None, force=False, save=True):
    """Tri-state title verdict for ONE lead. Never raises; a live failure becomes 'unverified'."""
    if not re.sub(r'\W', '', str(folio or '')):
        return {'title_status': 'unverified', 'current_owner': '', 'flip_date': '',
                'evidence': 'no folio on the lead — cannot run a live owner check', 'cached': False}
    if not str(defendant or '').strip():
        return {'title_status': 'unverified', 'current_owner': '', 'flip_date': '',
                'evidence': 'no defendant/owner name to compare against', 'cached': False}

    owns = cache if cache is not None else _load_cache()
    key = _cache_key(folio, defendant, filed)
    if not force and _fresh(owns.get(key)):
        c = dict(owns[key])
        c['cached'] = True
        return c

    filed_date = _parse_date(filed)
    cty = re.sub(r'[^A-Z ]', '', str(county or '').upper()).strip() or 'MIAMI-DADE'
    current_owner, sales = '', []
    try:
        if cty.startswith('MIAMI') or cty == 'DADE':
            current_owner, sales = _fetch_miamidade(folio)
        elif cty.startswith('BROWARD'):
            current_owner, sales = _fetch_broward(folio)
        elif cty.startswith('PALM') or 'PALM BEACH' in cty:
            current_owner, sales = _fetch_palmbeach(folio)
        else:
            return _finalise(owns, key, {'title_status': 'unverified', 'current_owner': '',
                'flip_date': '', 'evidence': 'no live appraiser wired for county %r' % county}, save)
    except Exception as e:
        return _finalise(owns, key, {'title_status': 'unverified', 'current_owner': '',
            'flip_date': '', 'evidence': 'appraiser lookup failed (%s) — VERIFY title before contact'
            % str(e)[:80]}, save)

    flip_date, flip_why = _flip_from_sales(sales, filed_date)
    rel = owner_relation(current_owner, defendant) if current_owner else 'nolive'

    # 1) Live owner is a DIFFERENT person -> the house is gone. Authoritative. (The Milouse catch.)
    if rel == 'different':
        return _finalise(owns, key, {'title_status': 'transferred', 'current_owner': current_owner,
            'flip_date': flip_date or '', 'evidence': 'LIVE appraiser owner %r is not the foreclosure '
            'defendant %r%s' % (current_owner, defendant, ('; also ' + flip_why) if flip_why else '')}, save)

    # 2) No live owner read, but a foreclosure/tax deed after filing -> gone (sales is the only signal).
    if rel == 'nolive' and flip_date:
        return _finalise(owns, key, {'title_status': 'transferred', 'current_owner': '',
            'flip_date': flip_date, 'evidence': 'appraiser owner unreadable but sales history shows '
            + flip_why}, save)

    # 3) Only a surname matches (spouse? wrong person?) -> verify, never silently drop.
    if rel == 'unsure':
        return _finalise(owns, key, {'title_status': 'unverified', 'current_owner': current_owner,
            'flip_date': flip_date or '', 'evidence': 'live owner %r shares only a surname with the '
            'defendant %r — verify it is the same household' % (current_owner, defendant)}, save)

    # 4) Live owner unchanged BUT a post-filing foreclosure/tax deed -> contradiction, verify (do NOT
    #    auto-drop: a routine quitclaim-to-own-trust also lands here and must not be dropped).
    if rel == 'same' and flip_date:
        return _finalise(owns, key, {'title_status': 'unverified', 'current_owner': current_owner,
            'flip_date': flip_date, 'evidence': 'live owner still matches the defendant yet ' + flip_why
            + ' — verify before contact'}, save)

    # 5) Live owner matches, no flip deed -> clear.
    if rel == 'same':
        return _finalise(owns, key, {'title_status': 'clear', 'current_owner': current_owner,
            'flip_date': '', 'evidence': 'LIVE appraiser owner still matches the defendant; no '
            'post-filing Certificate of Title'}, save)

    # 6) Reached the appraiser, no owner, no flip -> ambiguous, not a clearance.
    return _finalise(owns, key, {'title_status': 'unverified', 'current_owner': current_owner,
        'flip_date': '', 'evidence': 'appraiser returned no current owner for the folio — VERIFY '
        'before contact'}, save)


def _finalise(cache, key, res, save=True):
    res['ts'] = datetime.date.today().isoformat()
    res['cached'] = False
    cache[key] = {k: res[k] for k in ('title_status', 'current_owner', 'flip_date', 'evidence', 'ts')}
    if save:
        _save_cache(cache)
    return res


# ---------------------------------------------------------------------------------------------
# BATCH HELPER for the call-sheet generators
# ---------------------------------------------------------------------------------------------
def gate_rows(rows, folio_key='folio', owner_key='owner', filed_key='filed',
              county_key='county', default_county='MIAMI-DADE', throttle=0.4, budget_s=180, log=True):
    """Run the gate over a SMALL list of call-sheet rows (dozens). Mutates each row:
        r['title_status'] 'clear'|'transferred'|'unverified'; r['title_owner']; r['title_evidence'];
        r['title_flag'] '' | 'OWNERSHIP CHANGED — VERIFY' | 'OWNERSHIP UNVERIFIED — CHECK'.
    Returns (kept, held). held == 'transferred' (property gone) — drop from the outreach artifact.
    'unverified' stays in kept carrying title_flag for a loud stamp. A wall-clock budget bails the
    remaining rows to 'unverified' rather than stalling the whole nightly build on an appraiser outage.
    """
    cache = _load_cache()
    kept, held = [], []
    counts = {'clear': 0, 'transferred': 0, 'unverified': 0}
    start = time.time()
    for i, r in enumerate(rows, 1):
        if time.time() - start > budget_s:
            res = {'title_status': 'unverified', 'current_owner': '',
                   'evidence': 'ownership-gate wall-clock budget exceeded — not checked', 'cached': True}
        else:
            res = check_lead(r.get(folio_key), r.get(owner_key), filed=r.get(filed_key),
                             county=(r.get(county_key) or default_county), cache=cache, save=False)
        st = res['title_status']
        counts[st] = counts.get(st, 0) + 1
        r['title_status'] = st
        r['title_owner'] = res.get('current_owner') or ''
        r['title_evidence'] = res.get('evidence') or ''
        if st == 'transferred':
            r['title_flag'] = 'OWNERSHIP CHANGED — VERIFY'
            held.append(r)
        elif st == 'unverified':
            r['title_flag'] = 'OWNERSHIP UNVERIFIED — CHECK'
            kept.append(r)
        else:
            r['title_flag'] = ''
            kept.append(r)
        if not res.get('cached'):
            time.sleep(throttle)
        if log and (i % 10 == 0 or i == len(rows)):
            print('  ownership gate %d/%d  %s' % (i, len(rows), counts))
    _save_cache(cache)                    # write ONCE per batch, always
    if log:
        print('ownership gate: %d clear, %d TRANSFERRED (held), %d unverified (stamped)'
              % (counts['clear'], counts['transferred'], counts['unverified']))
    return kept, held


# ---------------------------------------------------------------------------------------------
# CLI / SELF-TEST
# ---------------------------------------------------------------------------------------------
def _selftest():
    cases = [
        ('MARTIN, SHAWN E', 'SHAWN MARTIN', 'same'),
        ('SMITH JOHN & MARY H/E', 'SMITH, JOHN', 'same'),
        ('ABC HOLDINGS LLC', 'ABC HOLDINGS', 'same'),
        ('JONES, ROBERT L JR', 'ROBERT JONES', 'same'),
        ('JOSEPH, MILOUSE', 'SEWELL, CYNTHIA', 'different'),        # the real flip
        ('DE LA CRUZ, JOSE', 'JOSE DELACRUZ', 'same'),              # compound surname spacing
        ("O CONNOR, SEAN", "SEAN OCONNOR", 'same'),                 # apostrophe surname
        # Different people who share given names. The surname position is ambiguous (one side is
        # comma-less), so the honest verdict is 'unsure' -> stamped CHECK TITLE for a human, never
        # silently cleared and never silently killed.
        ('RODRIGUEZ, JOSE LUIS', 'JOSE LUIS MARTINEZ', 'unsure'),
        ('GARCIA, JOSE', 'GARCIA, MARIA', 'unsure'),                # shared surname only
        ('SMITH, JOHN', 'SMITH, MARY', 'unsure'),                   # spouse -> verify, not drop
        ('K HOLDING LLC', 'K HOLDING GROUP LLC', 'same'),
        ('202 EQUITY PARTNERS LLC', 'CYNTHIA SEWELL', 'different'),
        ('', 'SMITH, JOHN', 'same'),                                # nothing to compare
        # --- real rows that produced FALSE 'transferred' on the first full-board scans (2026-08-14) ---
        ('(owner via title search)', 'RCF 2 ACQUISITION TR', 'same'),   # placeholder, not a name
        ('CHRIS', 'LE, CHRIS', 'same'),                                 # particle-glue broke surname LE
        ('ZELAZNIK FLORALEE', 'ZELAZNIK HOWARD', 'unsure'),             # county-roll LAST FIRST, family
        ('PUTNEY SALLY EST', 'PUTNEY JORDAN C', 'unsure'),              # estate -> heir, same surname
        ('COFFIE CALTON S EST', 'COFFIE TAFARI & SPENCER AZELIO', 'unsure'),
        ("GOD'S SHELTER MISSIONARY CHURCH INC", 'GODS SHELTER MISSIONARY CHURCH INC', 'same'),
        ('MILOUSE H/E JOSEPH', 'SEWELL, CYNTHIA', 'different'),         # the real flip, county-roll form
        ('DEBORA SILVA', 'BR AMERICAN INVEST LLC', 'different'),        # real: person -> investor LLC
    ]
    ok = 0
    for a, b, want in cases:
        got = owner_relation(a, b)
        ok += got == want
        print('%s owner_relation(%-26r,%-22r)=%-10s want %s'
              % ('OK ' if got == want else 'XX ', a, b, got, want))
    print('\n%d/%d name cases pass' % (ok, len(cases)))
    return ok == len(cases)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folio', default='')
    ap.add_argument('--owner', default='', help='the foreclosure defendant / believed owner')
    ap.add_argument('--filed', default='', help='case filing date, MM/DD/YYYY')
    ap.add_argument('--county', default='MIAMI-DADE')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if _selftest() else 1)
    if not a.folio or not a.owner:
        ap.error('need --folio and --owner (or --selftest)')
    print(json.dumps(check_lead(a.folio, a.owner, filed=a.filed or None, county=a.county,
                                force=a.force), indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
