# -*- coding: utf-8 -*-
"""buybox.py — standing acquisition filters ("I want THIS kind of house, in THIS city").

WHY THIS EXISTS
Jose asked for Miami Gardens, 4+ bed / 2+ bath, for his son to live in. That was answered once, by
hand, as a one-off HTML sheet dated 2026-08-26. Two more matching cases have been filed since and
were on nobody's radar, because a hand-built sheet is a photograph, not a filter. This makes the
buy-box a STANDING criterion the nightly build re-evaluates, so a new 4-bedroom in Miami Gardens
shows up in call mode by itself.

THE RULE THIS FILE ENFORCES, AND IT IS THE WHOLE POINT
  VALUE MINUS NOTHING IS NOT EQUITY.
Six of the eight Miami Gardens matches carry NO judgment. Their rows print value $417,956,
$375,822, $367,731 — and a closer reading that sheet fast sees "equity". There is no debt figure on
those rows at all. That is exactly how the 8/26 West Palm inbound got dialed: a big value, a
missing payoff, and a
board that printed 91%. So every row here lands in one of three buckets and the bucket is stated in
words, never inferred from a number:

  CONFIRMED   value AND debt both known, debt < value      -> a real number, quote it
  UNKNOWN     no judgment / no payoff on the row           -> equity is NOT KNOWN. Say so. Pull it.
  UNDERWATER  debt >= value                                -> not an acquisition, at all

RANKING IS FOR A LIVE-IN BUYER, not a flipper, because that is who asked:
  1. CONFIRMED room, ranked by dollars of room
  2. UNKNOWN, ranked by RUNWAY (a fresh lis pendens with no sale date = months to work, the owner
     is still in the house, and nothing is scheduled) — these are LEADS, not deals, until the
     mortgage is pulled
  3. UNDERWATER last, and flagged as not-an-acquisition

CONTRACT: pure data in, data out. No network. Never raises — a malformed row is skipped, never a
traceback, because this runs inside the nightly bake loop.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# A buy-box is data, not code. Add an entry; the nightly build picks it up.
BOXES = {
    'mg4': {
        'label': 'Miami Gardens 4+bd / 2+ba',
        'for': "Jose's son - live-in, not a flip",
        'city': ['MIAMI GARDENS'],
        'min_beds': 4,
        'min_baths': 2,
        'max_value': 0,        # 0 = no ceiling
    },
    'ml4': {
        # Asked for on 2026-08-18 and answered by buybox_miamilakes.py, a one-off script that has
        # to be run by hand. Same failure the mg4 docstring describes: a hand-run script is a
        # photograph, not a filter. As a standing box the nightly re-checks it, and on the board of
        # 2026-08-31 that is 5 outright 4BRs plus 4 convertible 3BRs nobody was looking at.
        'label': 'Miami Lakes 4BR (or 3BR convertible)',
        'for': 'live-in buyer, three kids - each wants a room',
        'zips': ['33014', '33015', '33016', '33018'],
        'max_value': 0,
        # A 4BR outright, OR a 3BR big enough that the den/garage becomes bedroom four. The 1,300
        # floor is buybox_miamilakes.py's own, and its comment records why it is not 1,700: that
        # line excluded a 1,693 sf Miami Lakes house by SEVEN FEET. No bath minimum, because the
        # script this box replaces never had one and inventing criteria loses real houses.
        'any_of': [
            {'min_beds': 4},
            {'min_beds': 3, 'max_beds': 3, 'min_sqft': 1300},
        ],
    },
}

EQ_CONFIRMED = 'CONFIRMED'
EQ_UNKNOWN = 'UNKNOWN'
EQ_UNDERWATER = 'UNDERWATER'


def _n(v):
    """Number out of anything. 0.0 when it isn't one."""
    try:
        if isinstance(v, bool):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        t = re.sub(r'[^\d.\-]', '', str(v or ''))
        return float(t) if t not in ('', '-', '.', '-.') else 0.0
    except Exception:
        return 0.0


def _s(v):
    try:
        return '' if v is None else str(v)
    except Exception:
        return ''


def _first(row, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ''):
            return row[k]
    return ''


def addr_of(row):
    return _s(_first(row, 'addr', 'Address', 'address', 'situs'))


def case_of(row):
    return _s(_first(row, 'case', 'Case #', 'case_no', 'caseNumber'))


def value_of(row):
    return _n(_first(row, 'value', 'market_value', 'val', 'assessed_value'))


_CHAINS = None


def _chains():
    """records_liens.json, loaded once. {} when absent — never raises."""
    global _CHAINS
    if _CHAINS is None:
        try:
            p = os.path.join(HERE, 'records_liens.json')
            _CHAINS = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
        except Exception:
            _CHAINS = {}
    return _CHAINS


def debt_of(row):
    """(amount, kind, note). kind: 'judgment' | 'recorded' | ''.

    'judgment' is a court-ordered number and can be quoted. 'recorded' is the sum of OPEN recorded
    mortgage principals off the county chain — it is INDICATIVE, never a payoff, because a 2019
    mortgage recorded at $405,000 is not $405,000 owed today. Both beat the old behaviour, which was
    to see no judgment and print nothing, leaving a $367,731 value looking like $367,731 of equity
    on a house with $589,570 of open recorded mortgages behind it (measured on a live Miami Gardens
    row, 2026-08-27 — a first plus a stacked HUD partial claim, neither of them on the lead row).

    CONFIDENCE IS PART OF THE ANSWER. records_liens sets conf='low' when it could not anchor the
    parcel, and in that state ZERO OPEN MORTGAGES MEANS "could not isolate", NOT "free and clear".
    Returning 0 with kind '' for that case is deliberate — it keeps the row in UNKNOWN, which is
    the truth, instead of promoting a failed search into a clean title.
    """
    j = _n(_first(row, 'payoff', 'judg', 'judgment', 'Final Judgment Amount', 'obid'))
    if j > 0:
        return j, 'judgment', 'court-ordered judgment on the row'
    try:
        ch = _chains().get(case_of(row)) or {}
        opens = [l for l in (ch.get('liens') or []) if str(l.get('st', '')).upper() == 'OPEN']
        if opens:
            total = sum(_n(l.get('amt')) for l in opens)
            if total > 0:
                conf = str(ch.get('conf') or '')
                return total, 'recorded', (
                    '%d OPEN recorded mortgage(s) totalling $%s — RECORDED PRINCIPAL, not a payoff%s'
                    % (len(opens), format(int(total), ','),
                       '. Chain confidence LOW: the parcel could not be anchored, so treat the '
                       'match itself as unconfirmed.' if conf == 'low' else '.'))
    except Exception:
        pass
    return 0.0, '', ''


def _junior_lien(row):
    """True when the debt figure on this row is a JUNIOR lien, so value-minus-debt is NOT room.

    An HOA or condo-association judgment sits behind a first mortgage that SURVIVES the
    association's sale. Subtracting only the association's lien answers a question nobody asked.

    foreclosure_leads.py:677 already derives exactly this -- `eq_fake = is_hoa or mortgage_risk` --
    so read that flag rather than re-deriving the taxonomy and letting the two drift. The plaintiff
    and case-type tests below are a floor for rows that reach this file without the flag set.
    """
    if row.get('eq_fake') or row.get('eqfake') or row.get('mortgage_risk'):
        return True
    ct = '%s %s' % (row.get('case_type') or '', row.get('clerk_case_type') or '')
    if re.search(r'HOA|CONDO|ASSOC', ct, re.I):
        return True
    return bool(re.search(r'(CONDOMINIUM|HOMEOWNERS?|PROPERTY\s+OWNERS?|MASTER)[^,]{0,40}ASSOC',
                          str(row.get('plaintiff') or ''), re.I))


def is_tax_deed(row):
    """True when this row is a TAX DEED sale, where the money figure is an OPENING BID."""
    u = ' '.join(_s(row.get(k)) for k in ('sale_type', 'case_type', 'Auction Type',
                                          'auction_type', 'clerk_case_type')).upper()
    return 'TAX DEED' in u or 'TAXDEED' in u.replace(' ', '') or bool(re.search(r'\bTD\b', u))


def equity_state(row):
    """(state, room_or_None, why). The three-bucket rule, stated in words."""
    try:
        val = value_of(row)
        if val <= 0:
            return EQ_UNKNOWN, None, 'no value on this row'
        # A TAX-DEED OPENING BID IS NOT THE DEBT. Third appearance of this file's founding rule:
        # value minus nothing is not equity (top of file), value minus a JUNIOR lien is not equity
        # (below), and value minus an OPENING BID is not equity either. The bid is the tax
        # certificate plus fees and it lands in the same 'judgment' field a court-ordered payoff
        # does, so debt_of() returns kind='judgment' and the row graduates straight to CONFIRMED.
        # Caught 2026-08-31 the moment a second buy-box ran: 8300 NW 166 TER (2026A00260) printed
        # "CONFIRMED $983,501 of room, both figures on the row" at rank 1 — an LLC-owned house that
        # sold for $1,290,000 in 2023, so there is certainly a mortgage and it is NOT on this row.
        # Board-wide that was 27 rows advertising $23,002,278 of room that does not exist.
        # It is also not a lead in the sense the rest of this file means: nobody calls that owner
        # and buys the house. It is a competitive cash auction that bids toward market, and the
        # winner still needs quiet title. The row's own `warning` field already said so.
        if is_tax_deed(row):
            bid = _n(_first(row, 'opening_bid', 'Opening Bid', 'judg', 'judgment', 'payoff'))
            amt = ('The $%s on this row is the OPENING BID (tax certificate plus fees), not a '
                   'payoff and not the debt' % format(int(bid), ',')) if bid > 0 else (
                   'The figure on this row is an opening bid, not a payoff')
            return (EQ_UNKNOWN, None,
                    'TAX DEED sale. %s — the mortgage is not on this row. This is not a doorstep '
                    'lead either: it is a competitive cash auction that bids toward market, and '
                    'the winner still needs quiet title. Equity is UNKNOWN.' % amt)
        debt, kind, note = debt_of(row)
        if debt <= 0:
            return (EQ_UNKNOWN, None,
                    'NO judgment, payoff or open recorded mortgage found — the $%s is what it is '
                    'WORTH, not what is left after the loan. Equity is unknown. (A chain pull that '
                    'returns nothing is "could not isolate", not "free and clear".)'
                    % format(int(val), ','))
        room = val - debt
        if room <= 0:
            return (EQ_UNDERWATER, room,
                    'owes $%s against a $%s house — $%s UNDER. Not an acquisition. %s'
                    % (format(int(debt), ','), format(int(val), ','), format(int(-room), ','), note))
        if kind == 'recorded':
            # Room against RECORDED PRINCIPAL is not confirmed equity. A 2019 loan has paid down;
            # a 2023 loan has not. Either way nobody ordered a payoff, so this stays UNKNOWN with
            # the arithmetic shown, rather than graduating to CONFIRMED on an indicative number.
            return (EQ_UNKNOWN, room,
                    'value $%s minus $%s of open RECORDED principal leaves about $%s — but that is '
                    'principal, not a payoff, so treat it as a direction, not a number. %s'
                    % (format(int(val), ','), format(int(debt), ','), format(int(room), ','), note))
        # A JUNIOR LIEN IS NOT THE DEBT -- and this is the same failure the docstring at the top of
        # this file was written to prevent, reappearing one bucket over. The rule there is "value
        # minus nothing is not equity"; the rule here is that value minus the WRONG debt is not
        # equity either, and it is more dangerous because it prints a confident number.
        # Caught on 2025-019128-CA-01: a $23,924 condo-association judgment against a $290,272
        # unit printed "CONFIRMED $266,347 of room, both figures on the row" and ranked #1 in the
        # box -- while that very row carried mortgage_risk=True, eq_fake=True, tier C and the
        # warning "HOA/assoc case - verify senior mortgage on docket". The flag was already in
        # this file's own output dict and nothing read it.
        if _junior_lien(row):
            return (EQ_UNKNOWN, room,
                    'value $%s minus the $%s judgment looks like $%s — but this is an association '
                    'case and that judgment is JUNIOR. The first mortgage survives the sale and is '
                    'NOT on this row, so the senior debt is unknown and the real room may be zero. '
                    'Pull the mortgage before anyone says the word equity.'
                    % (format(int(val), ','), format(int(debt), ','), format(int(room), ',')))
        return (EQ_CONFIRMED, room,
                'value $%s minus the $%s owed = $%s of room, both figures on the row.'
                % (format(int(val), ','), format(int(debt), ','), format(int(room), ',')))
    except Exception:
        return EQ_UNKNOWN, None, 'row could not be read'


def runway_of(row):
    """Days of working time, roughly. A scheduled auction is a hard clock; a fresh LP is months.

    Returns (days:int|None, words:str). None = no sale scheduled, which for a live-in buyer is the
    GOOD state — the owner is still in the house and nothing is on a countdown.
    """
    try:
        auc = _s(_first(row, 'auction', 'AuctionDate', 'sale_date')).strip()
        if not auc:
            return None, 'no sale date set — most runway'
        d = _n(_first(row, 'days'))
        if d:
            return int(d), 'auction %s, about %d days' % (auc, int(d))
        return 0, 'auction %s' % auc
    except Exception:
        return None, ''


def _geo_ok(row, box):
    """City name or ZIP. A box with neither is county-wide."""
    hay = (_s(_first(row, 'city')) + ' ' + addr_of(row)).upper()
    want = [c.upper() for c in (box.get('city') or [])]
    zips = [str(z) for z in (box.get('zips') or [])]
    if not want and not zips:
        return True
    if want and any(c in hay for c in want):
        return True
    # NOT row['zip'] — no board row has that field (0 of 2,050 checked 2026-08-31). The ZIP is
    # inside the address string, so it is matched there, word-bounded so 33014 cannot be hit by a
    # house number.
    return bool(zips and any(re.search(r'\b%s\b' % re.escape(z), hay) for z in zips))


def _clause_ok(row, cl):
    """One size clause. Missing beds/baths is a MISS, not a maybe — we are not going to put a house
    in a 4-bedroom list because the bedroom count was blank."""
    beds = _n(row.get('beds'))
    if beds < _n(cl.get('min_beds') or 0):
        return False
    if _n(row.get('baths')) < _n(cl.get('min_baths') or 0):
        return False
    top = _n(cl.get('max_beds'))
    if top and beds > top:
        return False
    floor = _n(cl.get('min_sqft'))
    return not (floor and _n(row.get('sqft')) < floor)


def matches(row, box):
    """Does this row fit the box?

    A box states its size test either directly (min_beds/min_baths/min_sqft/max_beds) or as
    'any_of': a list of clauses where matching ONE is enough — that is how "a 4BR, or a 3BR big
    enough to convert" is expressed without a second box or a second code path.
    """
    try:
        if not _geo_ok(row, box):
            return False
        cap = _n(box.get('max_value'))
        if cap and value_of(row) > cap:
            return False
        return any(_clause_ok(row, c) for c in (box.get('any_of') or [box]))
    except Exception:
        return False


_STATE_RANK = {EQ_CONFIRMED: 0, EQ_UNKNOWN: 1, EQ_UNDERWATER: 2}


def _sort_key(item):
    st, room, days = item['eqstate'], item['room'], item['days']
    if st == EQ_CONFIRMED:
        return (0, -(room or 0))
    if st == EQ_UNKNOWN:
        # most runway first: no sale date beats a scheduled one
        return (1, -(10 ** 6 if days is None else days))
    return (2, 0)


def scan(rows, box_key='mg4'):
    """[rows] -> ranked [dict] for one buy-box. Never raises."""
    box = BOXES.get(box_key) or {}
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            if not matches(r, box):
                continue
            key = case_of(r) or addr_of(r)
            if not key or key in seen:
                continue
            seen.add(key)
            st, room, why = equity_state(r)
            days, rw = runway_of(r)
            out.append({
                'case': case_of(r), 'addr': addr_of(r),
                'beds': int(_n(r.get('beds'))), 'baths': _n(r.get('baths')),
                'sqft': int(_n(r.get('sqft'))), 'value': value_of(r), 'debt': debt_of(r)[0], 'debtkind': debt_of(r)[1],
                'eqstate': st, 'room': room, 'eqwhy': why,
                'days': days, 'runway': rw,
                'plaintiff': _s(_first(r, 'plaintiff', 'pl'))[:60],
                'eqfake': bool(r.get('eqfake') or r.get('eq_fake')),
                'taxdeed': is_tax_deed(r),
                'box': box_key, 'boxlabel': box.get('label', box_key),
            })
        except Exception:
            continue
    out.sort(key=_sort_key)
    for i, it in enumerate(out, 1):
        it['rank'] = i
    return out


def annotate(row, box_key=None):
    """Stamp for the nightly bake: {} when the row is in NO box, else the tag the board, call mode
    and the morning worker lane read. Never raises.

    RUNS EVERY BOX. The signature used to be `box_key='mg4'`, and the nightly called it with no
    argument, so the board evaluated exactly ONE category no matter how many were defined — while
    refresh-dealflow.bat sat above its own call reading "Edit BOXES in buybox.py to add a box;
    nothing else changes". Adding a box changed nothing: it was never scanned, and it failed
    silently, because a box that matches nothing and a box that is never run look identical
    downstream. Pass box_key only to test one box in isolation.

    A row can sit in more than one box, so 'bb' carries the first match (the scalar every existing
    consumer already reads) and 'bball' carries all of them, comma-joined.
    """
    try:
        keys = [box_key] if box_key else list(BOXES.keys())
        hits = [k for k in keys if matches(row, BOXES.get(k) or {})]
        if not hits:
            return {}
        st, room, why = equity_state(row)
        first = BOXES.get(hits[0]) or {}
        return {'bb': hits[0], 'bblabel': first.get('label', hits[0]),
                'bball': ','.join(hits),
                'bbstate': st, 'bbroom': room if room is not None else '',
                'bbtd': is_tax_deed(row), 'bbwhy': why}
    except Exception:
        return {}


def _load_board():
    rows = []
    for f in ('lp_leads.json', 'leads_final.json', 'palmbeach_leads.json', 'broward_leads.json'):
        p = os.path.join(HERE, f)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
            rows.extend(d if isinstance(d, list) else list(d.values()))
        except Exception:
            continue
    return rows


def _report(key, hits):
    box = BOXES.get(key, {})
    print('%s  -  %s' % (box.get('label', key), box.get('for', '')))
    print('%d match%s\n' % (len(hits), '' if len(hits) == 1 else 'es'))
    for h in hits:
        room = ('$%s' % format(int(h['room']), ',')) if h['room'] is not None else '—'
        print('%2d. %-42s %db/%.0fba  val $%-9s  %s %s' % (
            h['rank'], h['addr'][:42], h['beds'], h['baths'],
            format(int(h['value']), ','), h['eqstate'], room))
        print('    %s' % h['eqwhy'])
        print('    %s | %s | %s\n' % (h['runway'], h['plaintiff'], h['case']))
    n_unk = sum(1 for h in hits if h['eqstate'] == EQ_UNKNOWN)
    if n_unk:
        print('%d of %d have NO debt figure. Their equity is UNKNOWN, not large. Pull the mortgage '
              'before anyone says the word equity on a call.' % (n_unk, len(hits)))


def main():
    import argparse
    # Only in main(), never at import: this module is imported by the nightly bake and by
    # morning_digest, and a library that reconfigures its importer's stdout is a side effect
    # nobody asked for. The em-dashes in eqwhy hit a cp1252 console when the bat redirects to
    # the log, and an unhandled UnicodeEncodeError there kills the scan while exiting 0.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description='Standing acquisition buy-boxes.')
    ap.add_argument('--box', default='', help='one box key; default is EVERY box')
    ap.add_argument('--json', default='', help='only valid with --box; all-box mode always writes '
                                               'buybox_<key>.json per box')
    a = ap.parse_args()
    rows = _load_board()

    if a.box:
        hits = scan(rows, a.box)
        _report(a.box, hits)
        if a.json:
            json.dump(hits, open(os.path.join(HERE, a.json), 'w', encoding='utf-8'), indent=1)
            print('-> %s' % a.json)
        return

    # DEFAULT IS ALL. morning_digest.buyboxes() loops BOXES and loads buybox_<key>.json for each,
    # so any box the nightly does not write shows up as "MISSING -- the nightly scan did not run".
    # Writing every box here is what keeps that report honest as boxes are added.
    for i, key in enumerate(BOXES):
        if i:
            print('')
        hits = scan(rows, key)
        _report(key, hits)
        out = 'buybox_%s.json' % key
        json.dump(hits, open(os.path.join(HERE, out), 'w', encoding='utf-8'), indent=1)
        print('-> %s' % out)


if __name__ == '__main__':
    main()
