#!/usr/bin/env python
"""palmbeach_resolve.py -- the property behind a Palm Beach filing, from the owner's NAME. $0.

WHY (2026-11-16). Palm Beach filings now come from the court's case search (pb_court_filings.py),
and a court row is a case number, a plaintiff and a defendant's name: no parcel, no legal
description. The chain's Palm Beach address step keys on the recorder's legal text, so all 555
court filings landed on the board with no address and no value. Broward had the same hole and
fl_lp/broward_resolve.py closed it with a name ladder against the property appraiser. This is the
same ladder for Palm Beach against the Florida DOR statewide roll (fl_cadastral, CO_NO=60), which
answers an owner-name query for free with no key, no login and no captcha.

THE RULE (identical to Broward's, and for the same reason: a wrong house is worse than no house)
  * the query only controls RECALL; a parcel counts only when lp_resolve2.owner_agrees says the
    owner of record carries EVERY token of the defendant's name;
  * exactly one such parcel + homestead on the roll  -> 'high'   (the owner lives there)
  * exactly one such parcel, no homestead            -> 'medium' (needsHuman; one-parcel match)
  * several parcels                                   -> 'low' with the candidates listed
  * none                                              -> 'none'
  * a last-name query that returns a full page is too common to trust and is not used.

Writes lp_addresses.json entries in the shape broward_resolve writes ('rung': 'pb-name'). A case
already in the file is never overwritten.

    python fl_lp/palmbeach_resolve.py              resolve every unresolved Palm Beach row
    python fl_lp/palmbeach_resolve.py --limit 50
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import fl_cadastral as FC                              # noqa: E402
from lp_resolve2 import owner_agrees, toks             # noqa: E402  (the wrong-Garcia guard)

SRC = os.path.join(REPO, 'lis_pendens.json')
OUT = os.path.join(REPO, 'lp_addresses.json')
CO_NO = 60
PAGE = 60                 # rows asked per query; a full page on a bare last name = too common
PAUSE_S = 0.35
ENTITY_RE = re.compile(r'\b(LLC|INC|CORP|TRUST|ASSN|ASSOC|CO|LP|LTD|DMCC|PLLC|BANK|ESTATE)\b')
RETRY_NONE_DAYS = 30      # a name the roll did not carry is asked again after this (new deeds)


def agrees(lp_owner, roll_owner):
    """owner_agrees, plus one thing the court makes necessary: the court writes a middle NAME
    ('SMITH, JOHN MICHAEL') where the roll writes an INITIAL ('SMITH JOHN M'). A middle name whose
    initial stands as a lone letter on the roll counts as carried. Last and first name must still
    both be there in full, so a shared surname is never agreement."""
    if owner_agrees(lp_owner, roll_owner, ''):
        return True
    o = ' '.join(str(lp_owner or '').upper().split())
    if ',' not in o or ENTITY_RE.search(o):
        return False
    last, given = o.split(',', 1)
    given = re.sub(r'[^A-Z ]', ' ', given).split()
    last_t = toks(last)
    if not last_t or not given or len(given[0]) < 3:
        return False
    roll_words = re.sub(r'[^A-Z ]', ' ', str(roll_owner or '').upper()).split()
    roll_t = toks(roll_owner)
    if not last_t.issubset(roll_t) or given[0] not in roll_t:
        return False
    initials = {w for w in roll_words if len(w) == 1}
    for mid in given[1:]:
        if len(mid) <= 2:
            continue                                   # an initial or a suffix on the court side
        if mid not in roll_t and mid[0] not in initials:
            return False
    return True


def _sql(s):
    return re.sub(r'[^A-Z0-9 &\-]', ' ', str(s or '').upper()).strip().replace("'", "''")


def queries(owner):
    """Roll-shaped LIKE prefixes, widest recall first-name-first. The DOR roll writes people as
    'LAST FIRST M' with no comma.
      'MARTIN, SHAWN E'    -> ['MARTIN SHAWN', 'MARTIN']
      'PAULA ANNE DRALUCK' -> ['DRALUCK PAULA', 'DRALUCK']
      'K HOLDING LLC'      -> ['K HOLDING LLC', 'K HOLDING']"""
    o = ' '.join(str(owner or '').upper().split())
    if not o:
        return []
    if ENTITY_RE.search(o):
        base = re.sub(r'\b(LLC|INC|CORP|LTD|PLLC)\.?\s*$', '', o).strip(' ,')
        qs = [o.replace(',', ' '), base.replace(',', ' ')]
    elif ',' in o:
        last, given = o.split(',', 1)
        first = (given.split() or [''])[0]
        qs = [('%s %s' % (last.strip(), first)).strip(), last.strip()]
    else:
        parts = o.split()
        qs = ['%s %s' % (parts[-1], parts[0]), parts[-1]] if len(parts) > 1 else [o]
    out = []
    for q in qs:
        q = ' '.join(_sql(q).split())
        if len(q) >= 3 and q not in out:
            out.append(q)
    return out


def roll_name(prefix, fetch=None):
    """Parcels in Palm Beach whose owner of record starts with `prefix`. None = the roll did not
    answer (unknown, never 'no such owner')."""
    fetch = fetch or FC._q
    try:
        got = fetch("CO_NO=%d AND OWN_NAME LIKE '%s%%'" % (CO_NO, prefix), PAGE)
    except Exception:
        return None
    return got if isinstance(got, list) else None


def _site(h):
    return (' '.join(str(h.get('PHY_ADDR1') or '').split()),
            str(h.get('PHY_CITY') or '').strip().title(), str(h.get('PHY_ZIPCD') or '').strip()[:5])


def resolve_row(rec, fetch=None, pause=PAUSE_S):
    owner = rec.get('owner') or ''
    qs = queries(owner)
    hits, tried, unreachable = [], [], True
    for i, q in enumerate(qs):
        got = roll_name(q, fetch)
        tried.append(q)
        if got is None:
            continue
        unreachable = False
        if i > 0 and len(got) >= PAGE:
            # a bare last name that fills the page: the one right parcel may be on a page not read
            return {'confidence': 'none', 'candidates': [],
                    'evidence': 'state roll: %r is too common to search by last name alone' % q}
        if got:
            hits = got
            break
        if pause:
            time.sleep(pause)
    if unreachable:
        return {'confidence': 'none', 'evidence': 'state roll unreachable', 'candidates': [],
                'unreachable': True}
    seen, matches = set(), []
    for h in hits:
        if agrees(owner, h.get('OWN_NAME', '')):
            f = str(h.get('PARCEL_ID') or '').strip()
            if f and f not in seen:
                seen.add(f)
                matches.append(h)
    if not matches:
        return {'confidence': 'none', 'candidates': [],
                'evidence': 'state roll: %d parcel(s) under %s, none carrying every name token'
                            % (len(hits), tried)}
    if len(matches) > 1:
        return {'confidence': 'low', 'needsHuman': True,
                'evidence': 'state roll: %d Palm Beach parcels carry the full name; the name cannot '
                            'pick one' % len(matches),
                'candidates': [{'folio': str(h.get('PARCEL_ID') or ''), 'addr': _site(h)[0],
                                'city': _site(h)[1], 'owner': (h.get('OWN_NAME') or '').strip()}
                               for h in matches[:8]]}
    h = matches[0]
    cad = FC._norm(h)
    a1, city, zc = _site(h)
    row = {'folio': str(h.get('PARCEL_ID') or '').strip(), 'addr': a1, 'city': city, 'zip': zc,
           'paOwner': (h.get('OWN_NAME') or '').strip(), 'candidates': [], 'ownerMismatch': False,
           'value': int(cad.get('market_value') or 0), 'hs': bool(cad.get('homestead')),
           'rung': 'pb-name'}
    mail = ' '.join(str(cad.get('mail_addr') or '').split())
    if mail:
        row['mailAddr'] = mail
    ev = 'state roll: exactly one Palm Beach parcel carries every token of %r' % owner
    if not a1:
        row.update(confidence='low', needsHuman=True,
                   evidence=ev + ', but the roll lists no street address for it (vacant land?)')
        return row
    if row['hs']:
        row.update(confidence='high', needsHuman=False,
                   evidence=ev + ' and it is HOMESTEAD (the owner lives at the parcel)')
    else:
        row.update(confidence='medium', needsHuman=True,
                   evidence=ev + ' (no homestead: absentee or second home)')
    return row


def _due(prev_entry, today):
    """A Palm Beach case is (re)asked when it has no entry, or a 'none' older than the retry wait."""
    if not isinstance(prev_entry, dict):
        return True
    if prev_entry.get('rung') != 'pb-name' and prev_entry.get('confidence') in ('high', 'medium', 'low'):
        return False                                   # another rung settled it: never overwritten
    if prev_entry.get('confidence') != 'none':
        return False
    if prev_entry.get('unreachable'):
        return True
    try:
        import datetime
        age = (today - datetime.date.fromisoformat(str(prev_entry.get('asked') or '')[:10])).days
    except ValueError:
        return True
    return age >= RETRY_NONE_DAYS


def run(limit=0, src=SRC, out=OUT, fetch=None, pause=PAUSE_S, today=None, say=print):
    import datetime
    today = today or datetime.date.today()
    recs = [r for r in json.load(open(src, encoding='utf-8'))
            if isinstance(r, dict) and (r.get('county') or '').upper() == 'PALM BEACH'
            and r.get('case') and r.get('owner')]
    prev = {}
    if os.path.exists(out):
        try:
            prev = json.load(open(out, encoding='utf-8'))
        except Exception:
            prev = {}
    todo = [r for r in recs if _due(prev.get(r['case']), today)]
    if limit:
        todo = todo[:limit]
    say('%d PALM BEACH LP rows, %d to resolve by owner name' % (len(recs), len(todo)))
    counts, dead = {}, 0
    for i, rec in enumerate(todo, 1):
        res = resolve_row(rec, fetch=fetch, pause=pause)
        if res.get('unreachable'):
            dead += 1
            if dead >= 8:
                say('  state roll unreachable 8 times in a row: stopping, nothing guessed')
                break
        else:
            dead = 0
        res.update({'case': rec['case'], 'lpOwner': rec.get('owner') or '',
                    'legal': rec.get('legal') or '', 'county': 'PALM BEACH',
                    'asked': today.isoformat()})
        prev[rec['case']] = res
        counts[res['confidence']] = counts.get(res['confidence'], 0) + 1
        if i % 25 == 0 or i == len(todo):
            say('  [%d/%d] %s' % (i, len(todo), counts))
            tmp = out + '.tmp'
            json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, out)
        if pause:
            time.sleep(pause)
    tmp = out + '.tmp'
    json.dump(prev, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, out)
    say('DONE: %s -> lp_addresses.json (merged)' % counts)
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    run(limit=a.limit)
    return 0


if __name__ == '__main__':
    sys.exit(main())
