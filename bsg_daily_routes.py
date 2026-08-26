#!/usr/bin/env python
"""bsg_daily_routes -- the everyday two-person door plan, anchored on the house (33172).

WHAT THIS IS. Alejandro's ask (2026-08-24, voice): a standing daily schedule for him and Carlos --
leave time, per-stop ETAs, when to abandon a section, 12-15 doors each, stops minutes apart, neither
route longer than the other, all of it radiating out from 33172 and grouped by neighborhood, from
EVERY pre-foreclosure pool (fresh filing, county-verified, auction clock), as a PDF that exists
every morning without being asked. Scheduled as "DealFlow Daily Routes" at 06:30, after the 05:30
refresh has landed geocodes and the 06:00 phones pass has finished.

THE SCHEDULE IT PRINTS (locked with him, one correction):
  * Weekdays: depart 16:15, knock ~16:40-19:00, HARD STOP 19:00. He picked 19:30; the repo's own
    compliance standard on every door log / field kit / packet is 9AM-7PM no Sundays (FS 501.062 +
    the FTSA evidence posture), so the last half hour moved to the front of the run instead.
  * Saturday: depart 09:40, knock 10:00-13:00.  * Sunday: this generator refuses to build.
  * 12-15 doors + 3 spares each; abandon-rule: >60% no-answer through 6 doors before 18:00 ->
    jump to spares. Routes balanced by MINUTES (drive + knocks), within ~10 min of each other.

SUSTAINABILITY. The clean pool is small (~40s) and refills slowly, so an everyday schedule lives
or dies on the re-knock cycle: routed_ledger.py gives every no-answer door a 3-day cooldown, a
different time-class on the next try, 3 attempts max, then a 30-day rest. Fresh doors always fill
first; re-knocks fill the remainder and are labelled "attempt #N" on the card.

POOLS (Miami-Dade only -- Broward volume stays on the on-demand sheets):
  auction   leads_final.json rows through CR._live_lead, coords from geocode_cache.json. The only
            pool where equity is REAL (judgment entered). Shown with its basis; the ARV-only
            underwater warning from the 08-24 audit carries over.
  fresh     lp_addresses.json confidence=high, owner-verified, non-condo.
  verified  medium rows adjudicated against the live PA record by lp_upgrade.py.
  Estate / life-estate owners are split out to a "Jose first" section -- right street, wrong script.
LP rows carry no coordinates; their folios go through property_photos._parcel_centroids (free
statewide cadastral layer) cached in lp_geo_cache.json.

Run:    python bsg_daily_routes.py            # today's plan (HTML + PDF, repo + ~/DEALFLOW)
        python bsg_daily_routes.py --date 2026-08-25 --dry-run   # preview, no ledger writes
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _carlos_route as CR          # anchor, haversine, live-lead + condo gates
import carlos_pre_packet as PP      # clustering, tour ordering, ZIP->neighborhood names, maps links
import lp_upgrade as LU             # medium-row adjudication against the PA record
import routed_ledger as RL          # issuance memory + re-knock engine
import paths as P

HOME = CR.CARLOS                    # 10839 NW 7th St, 33172 -- the center point he asked for
import html as H
from urllib.parse import quote_plus

# ---------------------------------------------------------------- knobs (the locked schedule)
DOORS_MIN, DOORS_MAX, SPARES = 12, 15, 3
WD_DEPART, WD_LAST_KNOCK = (16, 15), (18, 55)     # weekday: leave 4:15pm, last knock starts 6:55pm
SA_DEPART, SA_LAST_KNOCK = (9, 40), (12, 50)      # saturday block
KNOCK_MIN = 5                                     # minutes at the door
DRIVE_MIN_PER_MI = 3.0                            # urban surface-street average
DRIVE_FLOOR = 2                                   # even next door costs park+walk minutes
CHAIN_MAX_MIN = 15                                # his "never more than ~15 minutes apart"
BALANCE_TOL = 10                                  # routes within this many est. minutes
_DEAL_STAGES = {'APPOINTMENT', 'OFFER MADE', 'UNDER CONTRACT', 'ASSIGNED', 'CLOSED'}

GEO_CACHE = os.path.join(HERE, 'lp_geo_cache.json')


def _load(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def _drive_min(mi):
    return max(DRIVE_FLOOR, mi * DRIVE_MIN_PER_MI)


def _phones(entry):
    """Up to 3 numbers WITH their DNC flag. Not CR._phones_from_skip: that helper silently drops
    DNC numbers, which is right for a call sheet and wrong here — the team rule is Carlos gets
    every number, and a DNC number on a DOOR card is legal to knock about but not to cold-dial,
    so the card must show the number and the warning together."""
    out = []
    for p in ((entry or {}).get('phones') or [])[:3]:
        n = re.sub(r'\D', '', str(p.get('number') or ''))
        if len(n) == 11 and n.startswith('1'):
            n = n[1:]
        if len(n) == 10:
            out.append({'digits': n, 'fmt': '(%s) %s-%s' % (n[:3], n[3:6], n[6:]),
                        'type': p.get('type') or '', 'dnc': bool(p.get('dnc'))})
    return out


# ---------------------------------------------------------------- pool building
def _lp_coords(rows):
    """Attach lat/lng to LP rows via their folio -> parcel centroid (free cadastral layer),
    cached so the county is asked about each folio exactly once, ever."""
    cache = _load('lp_geo_cache.json', {})
    need = [str(r.get('folio') or '') for r in rows
            if str(r.get('folio') or '').isdigit() and str(r['folio']) not in cache]
    if need:
        import requests
        import property_photos as PH
        sess = requests.Session()
        sess.headers['User-Agent'] = PH.UA
        got = PH._parcel_centroids(need, sess)
        for f, (lat, lon) in got.items():
            cache[f] = {'lat': lat, 'lng': lon}
        for f in need:                      # cache misses too -- never re-ask a dead folio daily
            cache.setdefault(f, {})
        json.dump(cache, open(GEO_CACHE, 'w', encoding='utf-8'), indent=0)
    n = 0
    for r in rows:
        g = cache.get(str(r.get('folio') or '')) or {}
        if g.get('lat'):
            r['lat'], r['lng'] = g['lat'], g['lng']
            n += 1
    return n


def build_pool(drop):
    """-> (doors, specials). Each door: {case, addr, city, zip, lat, lng, kind, owner, hs, money-ish
    fields, phones}. Every gate increments `drop` so the morning log shows where inventory went."""
    leads = _load('leads_final.json', [])
    geo = _load('geocode_cache.json', {})
    skip = _load('skiptrace_results.json', {})
    sibs = _load('sibling_cases.json', {})
    _oo = _load('optouts.json', {})
    optouts = set((_oo.get('notes') or _oo or {}).keys()) if isinstance(_oo, dict) else set()
    notes = (_load('worker_notes.json', {}) or {}).get('notes') or {}
    addrs = _load('lp_addresses.json', {})

    def _notes_block(case, addr_u):
        n = notes.get(case) or {}
        st = str(n.get('status') or '')
        if n.get('optout') or st == 'DO NOT CONTACT':
            return 'optout / DNC'
        if st.upper() in _DEAL_STAGES:
            return 'in a deal stage -- a knock mid-negotiation reads as pressure'
        if st == 'Dead':
            return 'dead'
        if n.get('wrongown'):
            return 'wrong owner on file'
        return None

    doors, specials = [], []
    seen = set()

    def _push(row, kind):
        a = RL.norm(row['addr'])
        if not a:
            drop['no street address'] += 1
            return
        if a in seen:
            drop['duplicate address across pools'] += 1
            return
        why = _notes_block(row['case'], a)
        if why:
            drop[why] += 1
            return
        if row['case'] in optouts:
            drop['optout / DNC'] += 1
            return
        seen.add(a)
        row['kind'] = kind
        _sk = skip.get(row['case']) or {}
        row['phones'] = _phones(_sk)
        # DOOR-ONLY: no phone and no live email anywhere. For these the door (or a letter) is the
        # ONLY channel that exists — every other lead on the sheet could have been reached from a
        # chair. Measured 2026-08-26: 109 live leads are unreachable digitally and 58 of those
        # model over $50k equity, topping out at $2.09M with an auction 21 days out. Driving past
        # one of those to knock someone you could have phoned is the most expensive mistake this
        # tool can make, so it ranks first (see build_routes' sort).
        _dead = {str(e).lower() for e in (_sk.get('emails_dead') or [])}
        _live_mail = [e for e in (_sk.get('emails') or []) if str(e).lower() not in _dead]
        row['emails'] = _live_mail
        row['door_only'] = not row['phones'] and not _live_mail
        doors.append(row)

    # -- auction pool: the only place equity is real ------------------------------------------
    for r in leads:
        c = r.get('case') or r.get('Case #')
        if not CR._live_lead(r, skip, siblings=sibs, optouts=optouts):
            drop['auction: live-lead gate (BK/claimed/optout/passed/TD)'] += 1
            continue
        if CR._condo_ish(r):
            drop['condo / co-op / unit'] += 1
            continue
        eq, fake = (r.get('equity') or 0), bool(r.get('eq_fake'))
        if eq <= 0 or fake:
            drop['auction: no reliable equity'] += 1
            continue
        d = CR._days_to(CR._sale_date(r))
        if d is not None and d <= 7:
            drop['auction: sale within 7d -- too late for a door'] += 1
            continue
        g = geo.get(c) or {}
        if not g.get('lat'):
            drop['auction: no geocode'] += 1
            continue
        mkt = r.get('market_value') or 0
        full = CR._addr_of(r)
        parts = [p.strip() for p in full.split(',')]
        zm = re.search(r'(\d{5})(?:-\d{4})?\s*$', full)
        _push({'case': c, 'addr': parts[0],
               'city': parts[1].title() if len(parts) > 1 and parts[1] else 'Miami',
               'zip': zm.group(1) if zm else '',
               'lat': g['lat'], 'lng': g['lng'], 'owner': r.get('owner_clean') or r.get('owners') or '',
               'hs': bool(r.get('homestead')), 'equity': int(eq), 'eq_pct': r.get('equity_pct') or 0,
               'judg': r.get('judgment') or 0, 'value': mkt, 'basis': r.get('basis') or 0,
               'basis_src': r.get('basis_src') or '', 'arv_only': (mkt - (r.get('judgment') or 0)) < 0,
               'sale': CR._sale_date(r), 'sale_days': d}, 'auction')

    # -- LP pools ------------------------------------------------------------------------------
    lp_rows = []
    for k, v in addrs.items():
        if v.get('county') == 'BROWARD':
            continue
        c = v.get('case') or k
        base = {'case': c, 'addr': v.get('addr') or '', 'city': v.get('city') or '',
                'zip': str(v.get('zip') or '')[:5], 'owner': v.get('paOwners') or v.get('paOwner') or '',
                'hs': bool(v.get('hs')), 'value': v.get('value') or 0, 'folio': v.get('folio') or ''}
        if v.get('confidence') == 'high':
            if v.get('ownerMismatch') or v.get('needsHuman'):
                drop['fresh: owner mismatch / needs human'] += 1
                continue
            dor = (v.get('dor') or '').upper()
            al = ' ' + base['addr'].lower()
            if ('CONDOMINIU' in dor or 'COOPERATIVE' in dor or 'TOWNHOUSE' in dor
                    or ' #' in al or ' apt ' in al or ' unit ' in al):
                drop['condo / co-op / unit'] += 1
                continue
            lp_rows.append((base, 'fresh'))
    up = LU.upgrade(addrs, county='DADE')
    for r in up['knockable']:
        lp_rows.append(({'case': r['case'], 'addr': r.get('addr') or '', 'city': r.get('city') or '',
                         'zip': str(r.get('zip') or '')[:5], 'owner': r.get('paOwners') or '',
                         'hs': True, 'value': r.get('value') or 0, 'folio': r.get('folio') or ''},
                        'verified'))
    for kk, n in up['dropped'].items():
        drop[f'verified: {kk}'] += n
    # LOUD when the county-verified pool collapses for a FIXABLE reason. 'no-pa-data' means the
    # rows were never priced — lp_values.py --all did not run (the nightly refresh rewrites
    # lp_addresses.json and prices high-confidence rows ONLY). Without this the pool just comes
    # back empty and the sheet looks thin with no explanation. See run-daily-routes.bat.
    _nopa = up['dropped'].get('no-pa-data', 0)
    if _nopa and not up['knockable']:
        print(f'!! COUNTY-VERIFIED POOL EMPTY: all {_nopa} medium row(s) lack PA data — '
              f'run  python lp_values.py --all  (the routes bat does this before every build).')
    flat = [b for b, _ in lp_rows]
    _lp_coords(flat)
    for base, kind in lp_rows:
        if not base.get('lat'):
            drop[f'{kind}: no parcel centroid'] += 1
            continue
        _push(base, kind)

    # -- specials: right street, wrong script --------------------------------------------------
    for r in up['special']:
        srow = {'case': r['case'], 'addr': r.get('addr') or '', 'city': r.get('city') or '',
                'zip': str(r.get('zip') or '')[:5], 'owner': r.get('paOwners') or '',
                'value': r.get('value') or 0, 'flag': r['upgrade'], 'why': r['upgrade_why']}
        if not _notes_block(r['case'], RL.norm(srow['addr'])) and r['case'] not in optouts:
            specials.append(srow)
    return doors, specials


# ---------------------------------------------------------------- route assembly
def _mi_home(r):
    return CR._hav_mi(HOME['lat'], HOME['lng'], r['lat'], r['lng'])


def _tour_minutes(tour):
    """Total minutes: drive out from home, the chain, knocks, and the drive back."""
    if not tour:
        return 0.0
    m = _drive_min(_mi_home(tour[0])) + 3          # +3 park/first-house buffer
    for a, b in zip(tour, tour[1:]):
        m += KNOCK_MIN + _drive_min(CR._hav_mi(a['lat'], a['lng'], b['lat'], b['lng']))
    m += KNOCK_MIN + _drive_min(_mi_home(tour[-1]))
    return m


def _etas(tour, day):
    """Stamp r['eta'] / r['eta_class'] on each stop; truncate at the last-knock wall."""
    hh, mm = SA_DEPART if day.weekday() == 5 else WD_DEPART
    wall_h, wall_m = SA_LAST_KNOCK if day.weekday() == 5 else WD_LAST_KNOCK
    wall = dt.datetime.combine(day, dt.time(wall_h, wall_m))
    t = dt.datetime.combine(day, dt.time(hh, mm))
    t += dt.timedelta(minutes=_drive_min(_mi_home(tour[0])) + 3)
    kept = []
    for i, r in enumerate(tour):
        if i:
            t += dt.timedelta(minutes=KNOCK_MIN
                              + _drive_min(CR._hav_mi(tour[i - 1]['lat'], tour[i - 1]['lng'],
                                                      r['lat'], r['lng'])))
        if t > wall:
            break
        r['eta'] = t.strftime('%-I:%M %p') if os.name != 'nt' else t.strftime('%I:%M %p').lstrip('0')
        r['eta_class'] = 'sat' if day.weekday() == 5 else ('wd-early' if t.hour < 18 else 'wd-late')
        kept.append(r)
    return kept


def build_routes(doors, led, day, drop):
    """Cluster -> rank zones close-and-heavy-first -> fill two minute-balanced tours."""
    elig = []
    for r in doors:
        e, att, last_cls, note = RL.state(led, r['addr'], day)
        if not e:
            drop[f'ledger: {note.split("(")[0].strip()}'] += 1
            continue
        r['attempt'] = att
        r['last_class'] = last_cls
        elig.append(r)

    # fresh first, then re-knocks; then DOOR-ONLY leads (a door is their only channel — everyone
    # else on this sheet could have been phoned); then auction (a real clock); then distance.
    elig.sort(key=lambda r: (r['attempt'], 0 if r.get('door_only') else 1,
                             0 if r['kind'] == 'auction' else 1, _mi_home(r)))

    for r in elig:
        r['sec'] = None
    groups = PP.cluster([dict(r, i=i) for i, r in enumerate(elig)])
    # PP.cluster copies rows; map back by index so ledger/attempt state stays on OUR objects
    zones = []
    for g in groups:
        rows = [elig[r['i']] for r in g]
        clat = sum(r['lat'] for r in rows) / len(rows)
        clng = sum(r['lng'] for r in rows) / len(rows)
        d = CR._hav_mi(HOME['lat'], HOME['lng'], clat, clng)
        zones.append({'rows': rows, 'name': PP.name_of(g), 'mi': d,
                      'score': len(rows) / (1.0 + d)})
    zones.sort(key=lambda z: -z['score'])

    def _take(zone_rows, want):
        tour, pool = [], list(zone_rows)
        cur = None
        while pool and len(tour) < want:
            if cur is None:
                nxt = min(pool, key=_mi_home)
            else:
                nxt = min(pool, key=lambda r: CR._hav_mi(cur['lat'], cur['lng'], r['lat'], r['lng']))
                if _drive_min(CR._hav_mi(cur['lat'], cur['lng'], nxt['lat'], nxt['lng'])) > CHAIN_MAX_MIN:
                    break                      # his rule: never chain a 15+ minute hop
            tour.append(nxt)
            pool.remove(nxt)
            cur = nxt
        return tour, pool

    def _cen(rows):
        return (sum(r['lat'] for r in rows) / len(rows), sum(r['lng'] for r in rows) / len(rows))

    ADJACENT_MI = 8.0     # a top-up zone must be a NEIGHBOR of where the route already ENDS —
                          # the first dry run chained Fontainebleau + Palmetto Bay + Miami Gardens
                          # (20-mile hops) into one "route"; the second left A at 5 doors while B
                          # ran 12 because centroid-to-centroid at 6mi was too blunt. Measured from
                          # the tour tail, one ≤8mi reposition continues the outward drive naturally.
    want = DOORS_MAX + SPARES
    if len(zones) >= 2:
        a_rows, _ = _take(zones[0]['rows'], want)
        b_rows, _ = _take(zones[1]['rows'], want)
        a_name, b_name = zones[0]['name'], zones[1]['name']
        remaining = list(zones[2:])
        # AT MOST ONE top-up zone per route. Chaining is transitive — each hop was <=8mi from the
        # previous TAIL, so a route walked Fontainebleau -> Hialeah Gardens -> Miami Gardens -> NMB,
        # ~20 miles end to end (measured 2026-08-26). That is a county march, not the "stays all
        # fresh within that area / one city, little subsections" run he asked for. One neighboring
        # zone is a reposition; three is a different day.
        _tops = {'a': 0, 'b': 0}
        for _ in range(len(remaining)):
            if len(a_rows) >= DOORS_MIN + SPARES and len(b_rows) >= DOORS_MIN + SPARES:
                break
            # always feed the thinner route, from the zone nearest ITS current tail
            thin = a_rows if len(a_rows) <= len(b_rows) else b_rows
            _k = 'a' if thin is a_rows else 'b'
            if _tops[_k] >= 1:                 # this route already took its one reposition
                other = 'b' if _k == 'a' else 'a'
                if _tops[other] >= 1:
                    break
                thin = b_rows if _k == 'a' else a_rows
                _k = other
            tail = thin[-1] if thin else None
            if tail is None:
                break
            cand = min(remaining, default=None,
                       key=lambda z: CR._hav_mi(tail['lat'], tail['lng'], *_cen(z['rows'])))
            if cand is None:
                break
            d = CR._hav_mi(tail['lat'], tail['lng'], *_cen(cand['rows']))
            if d > ADJACENT_MI:
                break
            remaining.remove(cand)
            extra, _ = _take(cand['rows'], want - len(thin))
            if extra:
                thin += extra
                _tops[_k] += 1
                if thin is a_rows:
                    a_name += ' + ' + cand['name']
                else:
                    b_name += ' + ' + cand['name']
    elif zones:
        # only one zone has inventory: split its single tour into two contiguous arcs at the
        # minute-midpoint, so both people work the same neighborhood without crossing paths
        whole, _ = _take(zones[0]['rows'], 2 * want)
        cut, best = 1, 1e9
        for i in range(1, max(2, len(whole))):
            gap = abs(_tour_minutes(whole[:i]) - _tour_minutes(whole[i:]))
            if gap < best:
                best, cut = gap, i
        a_rows, b_rows = whole[:cut], whole[cut:]
        a_name = b_name = zones[0]['name']
    else:
        return None

    # balance by minutes: move boundary doors from the heavier tour while it helps
    def _bal(x, y):
        for _ in range(6):
            mx, my = _tour_minutes(x), _tour_minutes(y)
            if abs(mx - my) <= BALANCE_TOL or min(len(x), len(y)) <= DOORS_MIN:
                break
            src, dst = (x, y) if mx > my else (y, x)
            mv = min(src, key=lambda r: min(
                (CR._hav_mi(r['lat'], r['lng'], s['lat'], s['lng']) for s in dst), default=99))
            src.remove(mv)
            dst.append(mv)
        return x, y
    a_rows, b_rows = _bal(a_rows, b_rows)

    # time-class discipline: a re-knock must not repeat its last slot. Weekday tours span
    # early->late, so swap offenders toward the half that differs; unsatisfiable ones defer.
    def _classfix(rows):
        rows_sorted, out = list(rows), []
        for r in rows_sorted:
            if r['attempt'] > 1 and r.get('last_class') == 'sat' and day.weekday() == 5:
                drop['ledger: needs a non-Saturday slot'] += 1
                continue
            out.append(r)
        if day.weekday() != 5:
            half = max(1, len(out) // 2)
            early, late = out[:half], out[half:]
            for r in list(early):
                if r['attempt'] > 1 and r.get('last_class') == 'wd-early' and late:
                    early.remove(r)
                    late.insert(0, r)
            for r in list(late):
                if r['attempt'] > 1 and r.get('last_class') == 'wd-late' and early:
                    late.remove(r)
                    early.append(r)
            out = early + late
        return out
    a_rows, b_rows = _classfix(a_rows), _classfix(b_rows)

    a_main, a_sp = a_rows[:DOORS_MAX], a_rows[DOORS_MAX:DOORS_MAX + SPARES]
    b_main, b_sp = b_rows[:DOORS_MAX], b_rows[DOORS_MAX:DOORS_MAX + SPARES]
    a_main = _etas(a_main, day)
    b_main = _etas(b_main, day)
    # FINAL fairness pass, after truncation. Tolerance is proportional on thin days: when one
    # zone has 5 doors near home and the other 8 farther out, forcing ±10 absolute minutes just
    # deletes inventory — drive-out overhead dominates tiny tours. The proportional band plus the
    # DAY ROTATION below (who takes the farther route alternates daily) is what actually delivers
    # "nobody travels more distant than another" — over the week, not by crippling single days.
    for _ in range(8):
        ma, mb = _tour_minutes(a_main), _tour_minutes(b_main)
        if abs(ma - mb) <= max(BALANCE_TOL, 0.15 * max(ma, mb)):
            break
        heavy, light = (a_main, b_main) if ma > mb else (b_main, a_main)
        # Floor only. The old guard also refused to shave whenever heavy had <= light+1 DOORS —
        # which is exactly the case this balancer exists for: a route heavy in MINUTES but light
        # in doors (measured 2026-08-26: A 12 doors/199min vs B 13 doors/165min, 34min apart,
        # shave declined because 12 <= 14). Door count is not the quantity being balanced.
        if len(heavy) <= 8:
            break
        heavy.pop()
    # DAY ROTATION: whoever drew the longer route today gets the shorter one tomorrow. Parity of
    # the ordinal date is deterministic — both phones compute the same answer with no state.
    t1 = {'zone': a_name, 'main': a_main, 'spares': a_sp, 'min': round(_tour_minutes(a_main))}
    t2 = {'zone': b_name, 'main': b_main, 'spares': b_sp, 'min': round(_tour_minutes(b_main))}
    if t1['min'] != t2['min']:
        longer_first = day.toordinal() % 2 == 0
        if (t1['min'] < t2['min']) == longer_first:
            t1, t2 = t2, t1
    return {'A': dict(t1, who='ALEJANDRO'), 'B': dict(t2, who='CARLOS')}


# ---------------------------------------------------------------- render
BADGE = {'auction': ('AUCTION', '#b3372f'), 'fresh': ('FRESH FILING', '#1e7a3c'),
         'verified': ('COUNTY-VERIFIED', '#2b5fa8')}

CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font:13.5px/1.42 'Segoe UI',Arial,sans-serif;color:#12203f;background:#fff;padding:20px}
.wrap{max-width:900px;margin:0 auto}
h1{font-size:21px;color:#0f1d3d}h1 span{color:#b58a1f}
.top{color:#5d6782;font-size:12px;margin:2px 0 12px}
h2{font-size:12.5px;letter-spacing:.14em;text-transform:uppercase;color:#b58a1f;margin:22px 0 4px}
h2 .who{background:#12204a;color:#fff;font-size:10px;padding:3px 9px;border-radius:20px;margin-left:7px}
h2 .tot{color:#5d6782;font-size:10.5px;font-weight:600;letter-spacing:0;margin-left:8px;text-transform:none}
.sub{color:#5d6782;font-size:11.5px;margin-bottom:8px}
table.tt{border-collapse:collapse;margin:8px 0 4px}
table.tt td{border:1px solid #dfe3ec;padding:4px 10px;font-size:12px}
table.tt td:first-child{font-weight:800;background:#f4f6fa;white-space:nowrap}
.gate{background:#fff8e6;border:1px solid #e3c675;border-left:4px solid #b58a1f;padding:10px 13px;
      border-radius:8px;margin:12px 0;font-size:12px;line-height:1.5}
.gate b{color:#7a5a10}
.run{display:inline-block;background:#12204a;color:#fff;text-decoration:none;font-weight:700;
     font-size:12px;padding:7px 12px;border-radius:7px;margin:2px 6px 10px 0}
.card{border:1px solid #dfe3ec;border-radius:9px;padding:9px 12px;margin-bottom:7px;break-inside:avoid}
.card.sp{border-color:#e7b3ae;background:#fffbfa}
.hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.eta{background:#12204a;color:#fff;font-weight:800;font-size:11.5px;border-radius:6px;padding:2px 8px;min-width:64px;text-align:center}
.addr{font-size:15.5px;font-weight:800;color:#12204a;text-decoration:none}
.kind{color:#fff;font-size:9px;font-weight:800;padding:2px 7px;border-radius:20px;letter-spacing:.05em}
.att{background:#f1e9d2;color:#7a5a10;font-size:9.5px;font-weight:800;padding:2px 7px;border-radius:20px}
.donly{background:#12204a;color:#fff;font-size:9.5px;font-weight:800;padding:2px 8px;border-radius:20px;letter-spacing:.04em}
.meta{font-size:11.5px;color:#3d4761;margin-top:2px}
.meta2{font-size:10.5px;color:#79839c;margin-top:1px}
.urg{color:#b3372f}.noeq{color:#8f6a1f;font-weight:700}
.warn{background:#fdecea;border:1px solid #e7b3ae;color:#8f2b23;padding:6px 8px;border-radius:6px;
      margin-top:5px;font-size:10.5px;line-height:1.4;font-weight:600}
.ph{margin-top:4px;font-size:13px;font-weight:800}.ph a{color:#12204a;text-decoration:none}
.pt{font-size:9.5px;font-weight:600;color:#79839c;margin-left:6px}
.dnc{background:#fdecea;color:#8f2b23;font-size:9px;font-weight:800;padding:1px 6px;border-radius:20px;margin-left:6px}
.none{margin-top:4px;font-size:11px;color:#8f2b23;font-weight:700}
.log{margin-top:6px;padding-top:5px;border-top:1px dashed #dfe3ec;font-size:10.5px;color:#5d6782}
.ln{display:inline-block;width:1.9in;border-bottom:1px solid #b9c0d0}
.rules{border:1.5px solid #12204a;border-radius:9px;padding:11px 14px;margin-top:18px;font-size:11.5px;line-height:1.55}
.rules b{color:#8f2b23}
.foot{color:#8b93a7;font-size:10px;margin-top:18px;line-height:1.5}
@media print{.run{display:none}h2{break-after:avoid}}"""


def _card(r, i):
    ps = ''.join('<div class="ph"><a href="tel:%s">%s</a><span class="pt">%s</span>%s</div>'
                 % (p['digits'], p['fmt'], H.escape(p.get('type') or ''),
                    '<span class="dnc">DNC — no cold call</span>' if p.get('dnc') else '')
                 for p in (r.get('phones') or [])[:3]) \
         or '<div class="none">no traced phone — door only</div>'
    q = quote_plus('%s, %s FL %s' % (r['addr'], r.get('city') or 'Miami', r.get('zip') or ''))
    lbl, col = BADGE[r['kind']]
    att = ('<span class="att">attempt #%d</span>' % r['attempt']) if r.get('attempt', 1) > 1 else ''
    if r.get('door_only'):
        att += ('<span class="donly" title="No phone and no live email anywhere in the file — '
                'this door is the only way to reach this owner">DOOR IS THE ONLY WAY</span>')
    if r['kind'] == 'auction':
        money = ('<b>Equity $%s</b> (%.0f%%) = %s $%s &minus; judgment $%s &middot; '
                 '<b class="urg">sale in %sd (%s)</b>'
                 % (format(r['equity'], ','), r['eq_pct'], 'ARV est.' if r['basis_src'] == 'arv'
                    else 'county value', format(int(r['basis']), ','),
                    format(int(r['judg']), ','), r['sale_days'], r['sale']))
        if r.get('arv_only'):
            money += ('<div class="warn">&#9888; Equity exists ONLY on the ARV estimate; on county '
                      'value ($%s) this owner is underwater. Verify before pitching equity.</div>'
                      % format(int(r['value']), ','))
    else:
        money = ('County value $%s &middot; <span class="noeq">no judgment yet — equity is '
                 'Jose&rsquo;s call</span>' % format(int(r.get('value') or 0), ','))
    return ('<div class="card"><div class="hd"><span class="eta">%s</span>'
            '<a class="addr" href="https://www.google.com/maps/search/?api=1&query=%s" target="_blank">%s</a>'
            '<span class="kind" style="background:%s">%s</span>%s</div>'
            '<div class="meta">%s %s &middot; %s%s</div><div class="meta2">%s</div>%s'
            '<div class="log">#%d &nbsp; Answered? <b>Y</b>/<b>N</b> &nbsp; Sign posted? <b>Y</b>/<b>N</b>'
            ' &nbsp; Flyer? <b>Y</b>/<b>N</b> &nbsp; Notes: <span class="ln"></span></div></div>'
            % (r.get('eta') or 'SPARE', q, H.escape(r['addr']), col, lbl, att,
               H.escape(r.get('city') or ''), H.escape(r.get('zip') or ''),
               H.escape(str(r.get('owner'))[:44]),
               ' &middot; <b>HOMESTEAD</b>' if r.get('hs') else '', money, ps, i))


def render(routes, specials, day, drop, thin):
    sat = day.weekday() == 5
    dep = '%d:%02d %s' % (SA_DEPART[0], SA_DEPART[1], 'AM') if sat \
        else '%d:%02d PM' % (WD_DEPART[0] - 12, WD_DEPART[1])
    stop_t = '1:00 PM' if sat else '7:00 PM'
    tt = ('<table class="tt">'
          '<tr><td>%s</td><td>Both leave 33172 — separate cars, separate zones</td></tr>'
          '<tr><td>first ETA</td><td>on each card below — drive time is already counted</td></tr>'
          '<tr><td>%s SHARP</td><td><b>hard stop</b> — 9AM–7PM window, no Sundays (FS 501.062; '
          'the door log is the proof we honored it)</td></tr>'
          '<tr><td>abandon rule</td><td>&gt;60%% no-answer through your first 6 doors AND it&rsquo;s '
          'before 6PM &rarr; jump to your SPARE doors. After 6PM stay — answer rates climb.</td></tr>'
          '</table>' % (dep, stop_t))

    def _route(k):
        r = routes[k]
        stops = ['%s, %s FL' % (x['addr'], x.get('city') or 'Miami') for x in r['main']]
        runs = ''.join('<a class="run" target="_blank" href="%s">&#9654; Maps leg %d (%d stops)</a>'
                       % (PP.maps(ch), i + 1, len(ch))
                       for i, ch in enumerate(stops[j:j + 8] for j in range(0, len(stops), 8)))
        cards = ''.join(_card(x, i + 1) for i, x in enumerate(r['main']))
        spares = ''.join(_card(x, len(r['main']) + i + 1) for i, x in enumerate(r['spares']))
        return ('<h2>Route %s — %s <span class="who">%s</span>'
                '<span class="tot">%d doors &middot; ~%d min door-to-door</span></h2>%s%s'
                % (k, H.escape(r['zone']), r['who'], len(r['main']), r['min'], runs, cards)
                + (('<div class="sub"><b>SPARES</b> — the abandon-rule jump targets:</div>' + spares)
                   if r['spares'] else ''))

    sp = ''
    if specials:
        rows = ''.join('<div class="card sp"><div class="hd"><a class="addr" target="_blank" '
                       'href="https://www.google.com/maps/search/?api=1&query=%s">%s</a></div>'
                       '<div class="meta">%s %s &middot; %s &middot; $%s</div>'
                       '<div class="warn">%s</div></div>'
                       % (quote_plus('%s, %s FL' % (s['addr'], s.get('city') or 'Miami')),
                          H.escape(s['addr']), H.escape(s.get('city') or ''),
                          H.escape(s.get('zip') or ''), H.escape(str(s['owner'])[:44]),
                          format(int(s.get('value') or 0), ','),
                          ('<b>Owner of record is deceased.</b> Do not knock cold and ask for them '
                           'by name — probate conversation, Jose first.') if s['flag'] == 'special-estate'
                          else ('<b>Life estate + remainderman.</b> Both signatures needed to sell — '
                                'worth a visit, never promise a clean sale. Jose first.'))
                       for s in specials)
        sp = ('<h2>Do not knock cold <span class="who">JOSE FIRST</span></h2>'
              '<div class="sub">Ownership verified — which is exactly why the normal script is '
              'wrong at these doors.</div>' + rows)

    thin_note = ('<div class="gate"><b>THIN DAY:</b> the clean pool could not fill both routes to %d. '
                 'Knock what is here; the re-knock engine refills as cooldowns expire and fresh '
                 'filings land.</div>' % DOORS_MIN) if thin else ''

    fair = abs(routes['A']['min'] - routes['B']['min'])
    body = ('<div class="wrap"><h1>BSG FLORIDA <span>DAILY DOOR PLAN</span></h1>'
            '<div class="top">%s &middot; anchor 33172 &middot; Route A %d min vs Route B %d min '
            '(&Delta; %d — balanced by drive+knock time, not door count) &middot; every door '
            'checked against the routed ledger (%s doors on record)</div>%s%s'
            '<div class="gate"><b>Equity is only REAL on AUCTION cards</b> (judgment entered). '
            'FRESH FILING / COUNTY-VERIFIED cards show county value only — no payoff exists yet; '
            '<b>Jose signs off before anyone pitches equity</b>. Ownership is verified on every '
            'card. Attempt badges mean a re-knock — different time slot than last try, 3 max, '
            'then the door rests.</div>'
            '%s%s%s'
            '<div class="rules"><b>AT THE DOOR:</b> Titled owner only. Someone else answers &rarr; '
            '&ldquo;I&rsquo;m looking for [name] about the property — is there a good time or '
            'number?&rdquo; and WALK. Never say foreclosure, case, bank, or money to anyone but the '
            'owner. NO-SOLICITING sign &rarr; do not knock, mark NS, next door (FS 501.062). Never '
            '&ldquo;I can stop the foreclosure&rdquo; / &ldquo;save your house&rdquo; (FS 501.1377). '
            'No fees, no signatures, no legal advice at a door. They say stop &rarr; done '
            'permanently, text Alex. <b>Log every door in the board&rsquo;s &#128682; QUICKLOG '
            'buttons the same evening</b> — that is what feeds tomorrow&rsquo;s plan and the '
            'compliance record.</div>'
            '<div class="foot">bsg_daily_routes.py &middot; pools: auction + fresh LP (high) + '
            'county-verified (lp_upgrade) &middot; gates counted in the run log &middot; re-knock: '
            '3-day cooldown, different slot, 3 attempts, 30-day rest &middot; stops chained '
            'nearest-neighbor, never &gt;%d est. minutes apart.</div></div>'
            % (day.strftime('%A, %B %d, %Y'), routes['A']['min'], routes['B']['min'], fair,
               format(len(RL.load()['doors']), ','), thin_note, tt,
               _route('A'), _route('B'), sp, CHAIN_MAX_MIN))
    return ('<!doctype html><html><head><meta charset="utf-8"><title>BSG Daily Door Plan — %s'
            '</title><style>%s</style></head><body>%s</body></html>' % (day, CSS, body))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--date', default='', help='YYYY-MM-DD (default today)')
    ap.add_argument('--dry-run', action='store_true', help='no ledger writes, no PDF')
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()

    if day.weekday() == 6:
        print('Sunday — no door plan is ever built (9AM-7PM / no-Sunday house rule).')
        return 0

    import collections
    drop = collections.Counter()
    led = RL.load()
    notes = (_load('worker_notes.json', {}) or {}).get('notes') or {}
    synced = RL.sync_outcomes(led, notes)

    doors, specials = build_pool(drop)
    routes = build_routes(doors, led, day, drop)

    print('=== GATE DROPS ===')
    for k, n in drop.most_common():
        print(f'  {n:>4}  {k}')
    if not routes or not (routes['A']['main'] or routes['B']['main']):
        print('NO ELIGIBLE DOORS TODAY — nothing issued. (Cooldowns + fresh filings refill the pool.)')
        return 0

    thin = len(routes['A']['main']) < DOORS_MIN or len(routes['B']['main']) < DOORS_MIN
    for k, who in (('A', 'A'), ('B', 'C')):
        for r in routes[k]['main']:
            if not a.dry_run:
                RL.record_issue(led, r['addr'], r['case'], who,
                                r.get('eta_class') or RL.timeclass(), day=day.isoformat())
    if not a.dry_run:
        RL.save(led)
    if synced:
        print(f'ledger: {synced} outcome(s) synced from door touches')

    html_doc = render(routes, specials, day, drop, thin)
    hp = os.path.join(HERE, 'BSG_Daily_Routes_%s.html' % day.isoformat())
    open(hp, 'w', encoding='utf-8').write(html_doc)
    print('wrote %s' % hp)
    print('Route A: %d doors, ~%d min (%s) | Route B: %d doors, ~%d min (%s) | specials: %d'
          % (len(routes['A']['main']), routes['A']['min'], routes['A']['zone'],
             len(routes['B']['main']), routes['B']['min'], routes['B']['zone'], len(specials)))

    if a.dry_run:
        print('(dry run — no PDF, no ledger writes)')
        return 0

    from playwright.sync_api import sync_playwright
    outs = [os.path.join(HERE, 'BSG_Daily_Routes_%s.pdf' % day.isoformat())]
    if not os.environ.get('DEALFLOW_NO_DESKTOP'):
        outs.append(P.out('BSG_Daily_Routes_%s.pdf' % day.isoformat()))
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.goto('file:///' + hp.replace(os.sep, '/'))
        pg.wait_for_timeout(800)
        pdf = pg.pdf(format='Letter', print_background=True,
                     margin={'top': '10mm', 'bottom': '10mm', 'left': '9mm', 'right': '9mm'})
        b.close()
    for o in outs:
        os.makedirs(os.path.dirname(o), exist_ok=True)
        open(o, 'wb').write(pdf)
        print('wrote %s (%.0f KB)' % (o, len(pdf) / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main())
