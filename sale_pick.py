"""sale_pick -- which of a parcel's recorded sales is the LAST sale.

Split out of foreclosure_leads.py for the same reason phone_src.py is: make_tracker's module cannot
be imported without Playwright, so logic inside it is only reachable by a full build against real
homeowner data. Guarded by _boardaccuracytest.py (no network, no browser, no real data).

THE BUG (2026-09-23 Miami accuracy audit, Elharrar and Schiraldi cases). enrich() took
`SalesInfos[0]` from the Miami-Dade appraiser as the last sale, trusting the API to list newest
first. It does not always: on both cases the first entry was an OLDER sale, so the board's "last
sale" (and everything that reads it -- owner tenure, the elder-tenure proxy, the purchase price
beside the equity) described a previous owner's purchase. Pick by DATE, never by position.
"""
import datetime

_FMTS = ('%m/%d/%Y', '%Y-%m-%d', '%m-%d-%Y', '%Y/%m/%d')


def sale_date(s):
    """A sale's date as a real date, or None when it cannot be read.

    Accepts the appraiser's 'M/D/YYYY', ISO 'YYYY-MM-DD' and ISO with a time part
    ('2004-03-23T00:00:00'). String comparison is not an option: '1/10/2006' sorts above
    '10/31/2006' (records_liens._parse_recd carries the same scar).
    """
    txt = str(s or '').strip()
    if not txt:
        return None
    txt = txt.split('T')[0].split()[0]
    for fmt in _FMTS:
        try:
            return datetime.datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def newest_sale(sales, date_key='DateOfSale'):
    """The entry with the latest readable date; {} when there are none.

    An entry whose date cannot be read never outranks one that can. When NO entry has a readable
    date the first entry is returned, which is exactly what the old `sales[0]` did -- so this can
    only ever change the answer when there is a dated sale that is newer.
    """
    rows = [s for s in (sales or []) if isinstance(s, dict)]
    if not rows:
        return {}
    best, best_d = rows[0], sale_date(rows[0].get(date_key))
    for s in rows[1:]:
        d = sale_date(s.get(date_key))
        if d is not None and (best_d is None or d > best_d):
            best, best_d = s, d
    return best
