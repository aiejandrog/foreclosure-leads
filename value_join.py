"""Join free value sources onto board rows, and count a row that has any of them.

The nightly healthcheck was reading the published census as the BEST SINGLE of comps ARV,
Redfin Estimate and Zestimate. On the 2026-10-18 board that was Redfin at 535/3880 (14%),
under the 20% floor, while county just/market value — the number the board already uses
when no comps ARV is in band — was not in the check at all. Redfin itself covered 62% of
folio leads in its cache; the bake only looked the cache up by digit-stripped folio, so a
lettered Broward parcel id and a case-keyed estimate never joined.

What this module writes
  rfval, rfurl, rfchk   Redfin Estimate. Advisory. Never copied into `value`.
  zest                  Zestimate, same rule, and only when the row does not already have one.
  value, vsrc           County just/market value, only when the row's value is 0:
                        the FDOR JV saved on a comps subject (`jv`), or a pa_values cache
                        hit. This is the same field county_leads / the Miami scrape already
                        store as market value.

What this module does not write
  equity, equity percent, tier, score, verdict, stay, opt-out. Those stay where they are.
  A row that gains a `value` here is priceable by the board's EXISTING client math
  (`recompute` / `_basisOf` read `r.value`). That is an input change, not a new formula.
  Last-sale price (`bprice`) is not promoted: an old deed price is not today's just value.

Caches are counts and parcel ids only at the call site. This module never prints a
folio, an address, or an owner.
"""
import json
import os
import re

# Broward condo parcel, 12 characters: digits, two letters, digits. '514205AA0390'.
# A Miami case slug is the same shape one size up ('2025011165ca01', 14) and must
# NOT be aliased onto its digits — that digit string can be a different parcel.
_PARCEL_LETTER = re.compile(r'^\d{5,8}[A-Z]{2}\d{3,5}$')


def money(v):
    """Dollars as int, or 0. Strings like '$281,290' parse; junk is 0."""
    if v is None or v is False:
        return 0
    if isinstance(v, str):
        v = v.replace(',', '').replace('$', '').strip()
        if not v:
            return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def parcel_forms(folio):
    """PARCEL_ID candidates, lettered form first.

    The FDOR centroid and cadastral layers store Broward condo ids WITH the letters.
    Probed 2026-10-18: '514205AA0390' hits (sqft 1670, JV 281290); the digit-stripped
    '5142050390' misses. Palm Beach keeps a 17-digit PCN including leading zeros;
    stripping those misses too ('00424632090005670' hits, '424632090005670' misses).
    """
    raw = re.sub(r'[^A-Z0-9]', '', str(folio or '').upper())
    if not raw:
        return []
    digits = re.sub(r'\D', '', raw)
    out = []
    if re.search(r'[A-Z]', raw):
        out.append(raw)
    if digits and digits not in out:
        out.append(digits)
    return out


def _pa_token(pa):
    m = re.search(r'(?:URL_Folio|parcelId)=([A-Za-z0-9]+)', str(pa or ''), re.I)
    return m.group(1) if m else ''


def row_forms(row):
    """Every parcel id worth trying for one lead: folio, Folio, and the PA deep link."""
    if not isinstance(row, dict):
        return []
    forms = []
    for src in (row.get('folio'), row.get('Folio'), _pa_token(row.get('pa') or '')):
        for f in parcel_forms(src or ''):
            if f not in forms:
                forms.append(f)
    # Lettered id first. The digit-stripped form of a Broward condo is a different
    # (usually missing) parcel; trying it first would accept a wrong hit.
    forms.sort(key=lambda f: 0 if re.search(r'[A-Z]', f) else 1)
    return forms


def row_case(row):
    if not isinstance(row, dict):
        return ''
    return str(row.get('case') or row.get('Case #') or '').strip()


def cache_keys(folio='', case=''):
    """Lookup order: lettered parcel, its digits, then the case slug redfin/listing use
    when a lead had no folio at fetch time."""
    keys = list(row_forms({'folio': folio}))
    slug = re.sub(r'[^a-z0-9]', '', str(case or '').lower())
    if slug and slug not in keys:
        keys.append(slug)
    return keys


def index_entries(cache):
    """Map cache keys to entries. A lettered parcel also answers its digit form when
    that digit key is not already its own entry (a crippled folio must not hide a
    different parcel that really has that digit id)."""
    idx = {}
    if not isinstance(cache, dict):
        return idx
    items = [(k, v) for k, v in cache.items() if isinstance(v, dict)]
    for k, v in items:
        ks = str(k).strip()
        if not ks:
            continue
        idx.setdefault(ks, v)
        idx.setdefault(ks.lower(), v)
        idx.setdefault(ks.upper(), v)
    for k, v in items:
        up = re.sub(r'[^A-Z0-9]', '', str(k).upper())
        if len(up) == 12 and _PARCEL_LETTER.match(up):
            idx.setdefault(up, v)
            idx.setdefault(re.sub(r'\D', '', up), v)
    return idx


def index_pa(cache):
    """pa_values cache → parcel id / folio → entry. Misses and $0 are dropped.
    Street is not an index key: two units on one street must not share a value."""
    idx = {}
    if not isinstance(cache, dict):
        return idx
    for k, v in cache.items():
        if not isinstance(v, dict) or v.get('miss'):
            continue
        if money(v.get('market_value') or v.get('jv')) <= 0:
            continue
        parts = str(k).split('|')
        folio = parts[1] if len(parts) > 1 else ''
        for src in (v.get('parcel_id'), folio):
            for a in parcel_forms(src or ''):
                idx.setdefault(a, v)
    return idx


def _hit(idx, folio, case):
    for k in cache_keys(folio, case):
        if k in idx:
            return idx[k]
    return None


def note_roll_value(row, comp):
    """Copy FDOR JV off a comps entry onto a row whose county value is still 0.

    Returns True when it wrote. Does not touch arv, equity, tier, or verdict fields.
    """
    if not isinstance(row, dict) or not isinstance(comp, dict):
        return False
    if money(row.get('value')) > 0:
        return False
    jv = money(comp.get('jv'))
    if jv <= 0:
        return False
    row['value'] = jv
    if not row.get('vsrc'):
        row['vsrc'] = 'cadastral-jv'
    return True


def attach(rows, redfin=None, listing=None, pa=None):
    """Fill advisory AVMs and a missing county value. Returns counts only."""
    rf_idx = index_entries(redfin)
    ls_idx = index_entries(listing)
    pa_idx = index_pa(pa)
    n_rf = n_z = n_pa = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        folio = row.get('folio') or row.get('Folio') or ''
        case = row_case(row)
        if money(row.get('rfval')) <= 0:
            h = _hit(rf_idx, folio, case)
            if h and money(h.get('v')) > 0 and h.get('conf') in ('ok', 'addr'):
                row['rfval'] = money(h.get('v'))
                row['rfurl'] = h.get('url') or ''
                try:
                    import time
                    row['rfchk'] = time.strftime('%Y-%m-%d', time.localtime(h.get('t') or 0))
                except (TypeError, ValueError, OSError):
                    row['rfchk'] = ''
                n_rf += 1
        if money(row.get('zest')) <= 0:
            h = _hit(ls_idx, folio, case)
            z = money(h.get('z')) if h else 0
            if z > 0:
                row['zest'] = z
                n_z += 1
        if money(row.get('value')) <= 0:
            h = _hit(pa_idx, folio, case)
            mv = money(h.get('market_value') or h.get('jv')) if h else 0
            if mv > 0:
                row['value'] = mv
                if not row.get('vsrc'):
                    row['vsrc'] = 'pa-cache'
                n_pa += 1
    return {'rfval': n_rf, 'zest': n_z, 'pval': n_pa}


def census_bits(rows):
    """pval = rows with a county just/market value. valued = any of arv, rfval, zest, value."""
    pval = valued = 0
    for d in rows or []:
        if not isinstance(d, dict):
            continue
        has_v = money(d.get('value')) > 0
        if has_v:
            pval += 1
        if has_v or money(d.get('arv')) > 0 or money(d.get('rfval')) > 0 or money(d.get('zest')) > 0:
            valued += 1
    return {'pval': pval, 'valued': valued}


def board_valued(cov):
    """(count, kind) from a DEALFLOW-COVERAGE object.

    kind 'union' when this build published `valued`. Older builds only have the three
    single-source counters; those stay on the max-of-one reading so a stale page is not
    reported as a union it never measured.
    """
    if isinstance(cov, dict) and cov.get('valued') is not None:
        return money(cov.get('valued')), 'union'
    n = 0
    if isinstance(cov, dict):
        for k in ('arv', 'rfval', 'zest'):
            n = max(n, money(cov.get(k)))
    return n, 'single'


def valued_detail(cov, shipped):
    """The healthcheck line for shipped-board value coverage."""
    n, kind = board_valued(cov)
    shipped = money(shipped)
    pct = round(100 * n / shipped) if shipped else 0
    if kind == 'union':
        bits = 'arv %s, rfval %s, zest %s, county %s' % (
            cov.get('arv', '?'), cov.get('rfval', '?'), cov.get('zest', '?'), cov.get('pval', '?'))
        text = 'any value source covers %d/%d (%d%%) [%s]' % (n, shipped, pct, bits)
    else:
        text = 'best single value source covers %d/%d (%d%%)' % (n, shipped, pct)
    return n, text


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_sidecars(here):
    """The three free caches the bake reads. Missing files are empty. No network."""
    return {
        'redfin': load_json(os.path.join(here, 'redfin_cache.json')),
        'listing': load_json(os.path.join(here, 'listing_status_cache.json')),
        'pa': load_json(os.path.join(here, 'pa_values_cache.json')),
    }
