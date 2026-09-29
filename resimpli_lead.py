"""resimpli_lead — the one Lead shape the REsimpli bridge speaks, built from a board row.

WHY A SEPARATE SHAPE
The board row (foreclosure_leads.make_tracker's `slim`, the RAW payload of the Desktop twin) is
the pipeline's truth, but it carries ~90 short keys shaped for the page. REsimpli wants a flat
person-plus-property record. This module is the translation, and nothing else: it reads board
fields and never computes a money figure. Underwriting stays where it is.

WHAT IS NOT DERIVED HERE, ON PURPOSE
  * verdict. STRONG / MARGINAL / VERIFY / PASS is computed in the browser (tracker_template.html
    setVerdict), not in Python. Re-implementing it here would be a second underwriting engine that
    drifts. The field exists and stays '' until that is decided.
  * est_equity is the board's `eq`, a PERCENT (see lp_leads.py: "eq IS A PERCENT"), passed through
    as-is. `equity_verified` is true only when equity_state traced the recorded chain (clear or
    priced); everywhere else the number is a guess and the export says so.
  * est_owed is the payoff figure when one was computed, else the judgment amount. 0 means
    "not checked" on the board, so it exports as blank, never as $0.
  * lis_pendens_amount: no lane records one (a lis pendens carries no amount, lp_leads.py), so
    it is blank until a source exists.

DEDUPE
One property, one REsimpli record: the key is the normalized street line (unit included) plus
the 5-digit ZIP. Two foreclosure cases on the same house (an HOA case and the bank's) collapse to
the first row in the caller's order, with the other rows' phones and emails merged in.
"""
import re
from dataclasses import dataclass, field, asdict
from typing import List

# USPS Publication 28, Appendix C1: the suffixes that actually occur in South Florida county
# data, plus the common long forms. Full-word -> abbreviation. Only the LAST street-name word is
# treated as a suffix (see _norm_street), so "PARK AVENUE" keeps PARK.
SUFFIX = {
    'ALLEY': 'ALY', 'AVENUE': 'AVE', 'AV': 'AVE', 'AVEN': 'AVE', 'BOULEVARD': 'BLVD', 'BOUL': 'BLVD',
    'CIRCLE': 'CIR', 'CIRC': 'CIR', 'COURT': 'CT', 'COVE': 'CV', 'CRESCENT': 'CRES',
    'CROSSING': 'XING', 'DRIVE': 'DR', 'DRV': 'DR', 'EXPRESSWAY': 'EXPY', 'HIGHWAY': 'HWY',
    'HWAY': 'HWY', 'ISLAND': 'IS', 'LANE': 'LN', 'LOOP': 'LOOP', 'MANOR': 'MNR', 'PARKWAY': 'PKWY',
    'PKY': 'PKWY', 'PASS': 'PASS', 'PATH': 'PATH', 'PIKE': 'PIKE', 'PLACE': 'PL', 'PLAZA': 'PLZ',
    'POINT': 'PT', 'ROAD': 'RD', 'ROW': 'ROW', 'RUN': 'RUN', 'SQUARE': 'SQ', 'STREET': 'ST',
    'STR': 'ST', 'TERRACE': 'TER', 'TERR': 'TER', 'TRAIL': 'TRL', 'TURNPIKE': 'TPKE', 'WAY': 'WAY',
    'CAUSEWAY': 'CSWY', 'ESTATES': 'ESTS', 'HARBOR': 'HBR', 'ISLE': 'ISLE', 'KEY': 'KY',
    'LANDING': 'LNDG', 'TRACE': 'TRCE', 'WALK': 'WALK',
}
DIRECTIONAL = {
    'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W', 'NORTHEAST': 'NE', 'NORTHWEST': 'NW',
    'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW', 'N.E.': 'NE', 'N.W.': 'NW', 'S.E.': 'SE', 'S.W.': 'SW',
}
# Secondary unit designators (Pub 28 C2). '#' becomes UNIT so "APT 4" and "# 4" do not dedupe apart
# from each other only by punctuation: both normalize to a designator plus the number.
UNIT = {
    'APARTMENT': 'APT', 'APT': 'APT', 'UNIT': 'UNIT', 'SUITE': 'STE', 'STE': 'STE', '#': 'UNIT',
    'BUILDING': 'BLDG', 'BLDG': 'BLDG', 'FLOOR': 'FL', 'ROOM': 'RM', 'LOT': 'LOT', 'SPACE': 'SPC',
    'TRAILER': 'TRLR', 'PENTHOUSE': 'PH', 'PH': 'PH',
}
STATES = {'FL', 'GA', 'AL', 'NY', 'NJ', 'CA', 'TX', 'NC', 'SC', 'PA', 'IL', 'MA', 'MD', 'VA', 'OH',
          'MI', 'CT', 'TN', 'CO', 'AZ', 'WA', 'DC', 'PR'}
_ZIP_RE = re.compile(r'\b(\d{5})(?:-?\d{4})?\b')
_NAME_SUFFIX = {'JR', 'SR', 'II', 'III', 'IV', 'ESQ'}
_COMPANY_RE = re.compile(r'\b(LLC|L L C|CORP|CORPORATION|INC|TRUST|TRUSTEE|ASSOC|ASSN|BANK|COMPANY|'
                         r'HOLDINGS|LP|LTD|PROPERT\w*|REALTY|CAPITAL|GROUP|INVEST\w*|EQUIT\w*|ESTATE OF)\b')


@dataclass
class Lead:
    owner_first: str = ''
    owner_last: str = ''
    property_address: str = ''       # normalized street line, unit included: "842 NW 9TH ST APT 4"
    city: str = ''
    state: str = ''
    zip: str = ''                    # 5 digits or ''
    county: str = ''
    mailing_address: str = ''        # '' when it is the property itself
    case_number: str = ''
    filing_date: str = ''
    auction_date: str = ''
    plaintiff: str = ''
    lis_pendens_amount: str = ''     # no source records one yet (module docstring)
    phones: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    list_name: str = ''
    tags: List[str] = field(default_factory=list)
    source: str = 'DealFlow'
    # DealFlow numbers, passed through, never computed here. None = not checked.
    est_arv: object = None
    est_owed: object = None
    est_equity: object = None        # PERCENT, the board's `eq`
    equity_verified: bool = False
    verdict: str = ''                # board-only today (module docstring)

    def dedupe_key(self):
        return dedupe_key(self.property_address, self.zip, self.city)

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- address normalization

def _tokens(s):
    s = str(s or '').upper()
    s = re.sub(r'\b([NS])\.\s?([EW])\.', r'\1\2', s)      # "N.W." -> "NW" before dots are stripped
    s = s.replace('#', ' # ')
    s = re.sub(r'[.,;]', ' ', s)
    s = re.sub(r'[^A-Z0-9#/\- ]', ' ', s)
    return s.split()


def _norm_street(line):
    """'842 Northwest 9th Street, Apt. 4' -> '842 NW 9TH ST APT 4'."""
    toks = _tokens(line)
    # split the unit off first: everything from the first designator on
    unit = []
    for i, t in enumerate(toks):
        if t in UNIT and i > 0:
            rest = toks[i + 1:]
            unit = [UNIT[t]] + rest
            toks = toks[:i]
            break
    out = [DIRECTIONAL.get(t, t) for t in toks]
    # the suffix is the last word that is a suffix and is not the house number or a directional
    for j in range(len(out) - 1, 0, -1):
        w = out[j]
        if w in ('N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'):
            continue
        if w in SUFFIX:
            out[j] = SUFFIX[w]
        break
    if unit:
        # "APT # 4" -> "APT 4"; a bare "#" with no number is noise
        unit = [u for k, u in enumerate(unit) if not (u == '#' and k > 0)]
        if len(unit) == 1 and unit[0] == 'UNIT':
            unit = []
    return ' '.join(out + unit).strip()


def parse_address(s):
    """'842 NW 9TH ST, MIAMI, FL 33136' -> (street, city, state, zip), all normalized.

    Board addresses are "street, city, ST zip" (foreclosure_leads._clean_addr). A line with no
    comma is treated as the street alone. ZIP+4 is cut to 5 digits; anything that is not five
    digits is dropped rather than guessed."""
    s = re.sub(r'\bFL[-,]\s*', 'FL ', str(s or ''))
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if not parts:
        return '', '', '', ''
    # "123 MAIN ST, APT 4, MIAMI, FL 33101": a unit written as its own comma part belongs to the street
    while len(parts) > 1 and (_tokens(parts[1]) or [''])[0] in UNIT:
        parts[0:2] = [parts[0] + ' ' + parts[1]]
    street = _norm_street(parts[0])
    city, state, zp = '', '', ''
    tail = parts[1:]
    if tail:
        last = tail[-1].upper()
        m = _ZIP_RE.search(last)
        if m:
            zp = m.group(1)
            last = (last[:m.start()] + last[m.end():]).strip()
        st = [t for t in re.split(r'\s+', last) if t in STATES]
        if st:
            state = st[-1]
            last = re.sub(r'\b%s\b' % state, '', last).strip()
        if len(tail) >= 2:
            city = tail[-2].upper() if not last else last
        else:
            city = last
        city = ' '.join(re.sub(r'[^A-Z0-9 ]', ' ', city.upper()).split())
    else:
        m = _ZIP_RE.search(parts[0])
        if m and m.start() > 0:
            zp = m.group(1)
    return street, city, state, zp


def normalize_address(s):
    """One comparable string: 'STREET, CITY, ST ZIP' (empty parts dropped)."""
    street, city, state, zp = parse_address(s)
    tail = ' '.join(x for x in (state, zp) if x)
    return ', '.join(x for x in (street, city, tail) if x)


def dedupe_key(street, zp, city=''):
    """normalized street + ZIP. With no ZIP, street + city (so one house still dedupes, and two
    same-numbered streets in different cities do not). '' when there is no street, or neither a
    ZIP nor a city (nothing safe to merge on)."""
    street = _norm_street(street) if street else ''
    if not street:
        return ''
    # APT 4 / UNIT 4 / # 4 / STE 4 are one unit written three ways; the key must not split them
    street = re.sub(r'\b(APT|STE)\b', 'UNIT', street)
    if zp:
        return '%s|%s' % (street, zp)
    # no ZIP and no city: the same street line can exist in two cities, so do not merge on it
    return '%s|~%s' % (street, city.upper()) if city else ''


# ---------------------------------------------------------------- names

def split_owner(owners, oname='', is_company=None):
    """(first, last). A company owner goes whole into `last`, with `first` blank.

    `oname` is owner_clean ("First Last", already flipped from the roll's "LAST, FIRST"), so it
    is preferred. Joint owners ("A & B", "A AND B", "A; B") keep the first person."""
    raw = str(owners or '').strip()
    if is_company is None:
        is_company = bool(_COMPANY_RE.search(raw.upper()))
    if is_company:
        return '', ' '.join(raw.upper().split())
    src = str(oname or '').strip() or raw
    src = re.split(r'\s*(?:&|;|/|\bAND\b)\s*', src, maxsplit=1, flags=re.I)[0].strip()
    if ',' in src:                                   # "LAST, FIRST M"
        last, _, first = src.partition(',')
        first_toks = first.split()
        return (first_toks[0].title() if first_toks else ''), last.strip().title()
    toks = [t for t in src.replace('.', ' ').split()]
    while len(toks) > 1 and toks[-1].upper() in _NAME_SUFFIX:
        toks.pop()
    if not toks:
        return '', ''
    if len(toks) == 1:
        return '', toks[0].title()
    return toks[0].title(), toks[-1].title()


# ---------------------------------------------------------------- board row -> Lead

def _num(v):
    """float or None. 0 / '' / garbage -> None: on the board a zero means nobody checked."""
    if isinstance(v, bool):
        return None
    try:
        f = float(str(v).replace('$', '').replace(',', '')) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    return f if f else None


def digits10(p):
    d = re.sub(r'\D', '', str(p or ''))
    if len(d) == 11 and d.startswith('1'):
        d = d[1:]
    return d if len(d) == 10 else ''


def from_board_row(row, phones, emails=(), list_name='', source='DealFlow'):
    """Build a Lead from one board row.

    `phones` is passed in, never read off the row: the caller hands over only the numbers that
    survived the dial gates (DNC and not-the-owner numbers already removed). Reading row['phones']
    here would put a DNC number back into the file."""
    street, city, state, zp = parse_address(row.get('addr'))
    mail = row.get('mail') or ''
    mail_n = normalize_address(mail) if mail else ''
    prop_n = normalize_address(row.get('addr'))
    if mail_n and mail_n == prop_n:
        mail_n = ''
    first, last = split_owner(row.get('owners') or row.get('oname'), row.get('oname'),
                              is_company=bool(row.get('co')) or None)
    owed = _num(row.get('payoff')) or _num(row.get('judg'))
    eq = row.get('eq')
    try:
        eq = float(eq) if eq not in (None, '') else None
    except (TypeError, ValueError):
        eq = None
    verified = str(row.get('eqstate') or '') in ('clear', 'priced')
    county = str(row.get('county') or '').upper()
    st = str(row.get('st') or 'FC').upper()
    tags = ['DealFlow', county.title() if county else '', {'FC': 'Foreclosure', 'LP': 'Lis Pendens',
            'TD': 'Tax Deed', 'BAL': 'Balloon'}.get(st, st),
            'Equity Verified' if verified else 'Equity Unverified']
    filed = row.get('filedDate') or row.get('filed') or ''
    if isinstance(filed, (int, float)):
        filed = str(int(filed)) if filed else ''
    return Lead(
        owner_first=first, owner_last=last,
        property_address=street, city=city, state=state or ('FL' if street else ''), zip=zp,
        county=county, mailing_address=mail_n,
        case_number=str(row.get('case') or ''), filing_date=str(filed or ''),
        auction_date=str(row.get('auction') or ''), plaintiff=' '.join(str(row.get('plaintiff') or '').split()),
        phones=[d for d in (digits10(p) for p in phones) if d],
        emails=[str(e).strip().lower() for e in emails if str(e or '').strip()],
        list_name=list_name, tags=[t for t in tags if t], source=source,
        est_arv=_num(row.get('arv')), est_owed=owed, est_equity=eq, equity_verified=verified,
    )


def dedupe(leads):
    """-> (kept, dropped). First occurrence wins; later rows on the same property merge their
    phones and emails into it (order kept, no repeats) and are counted as dropped. A lead with no
    usable key is kept as-is: the caller decides what to do with a row that has no address."""
    kept, by_key, dropped = [], {}, 0
    for ld in leads:
        k = ld.dedupe_key()
        if not k:
            kept.append(ld)
            continue
        first = by_key.get(k)
        if first is None:
            by_key[k] = ld
            kept.append(ld)
            continue
        dropped += 1
        for p in ld.phones:
            if p not in first.phones:
                first.phones.append(p)
        for e in ld.emails:
            if e not in first.emails:
                first.emails.append(e)
    return kept, dropped
