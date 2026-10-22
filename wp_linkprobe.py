"""Which Whitepages link shape lands on the right property? Measured, not guessed.

    python wp_linkprobe.py                     # 10 condos + 8 houses, ~8 s per link
    python wp_linkprobe.py --condos 15 --houses 5 --gap 10

Opens each candidate link for each lead in your own default browser (signed in, nothing driving
it: this is the same as clicking the link yourself), then reads where the browser actually ended
up from the browser's own history file: the final URL Whitepages redirected to and the page title.
No copying, no typing. Prints a hit table per link shape; addresses are never printed (case
numbers are masked to the last 4). Details go to DEALFLOW_DIR/wp_linkprobe.json, outside the repo.

  unit      the page is this house AND this unit
  building  this house number and street, but no unit (the condo building page)
  house     this house (a lead with no unit)
  other     a different property, another unit in the building, or the city's property list
  blocked   Whitepages' bot / sign-in check
  none      no visit recorded, or the page kept our own URL and its title carries no address

Close the browser window it fills when it is done."""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wp_web as W                 # noqa: E402
import whitepages_lookup as WL     # noqa: E402

BASE = 'https://www.whitepages.com'


def _slug(t):
    return '-'.join(re.sub(r'[^A-Za-z0-9 ]', ' ', t).split())


def _street_unit(street):
    """(street without the unit, unit) using wp_web's own unit rules, plus a bare trailing
    "205-C" / "205C" after a street suffix, which those rules read as a number."""
    m = re.search(r'\b([A-Za-z]+)\s+(\d+-?[A-Za-z])$', street.strip())
    if m and m.group(1).lower() in W._SUFFIXES and not re.search(r'(?:\b(?:apt|unit|ste|suite)\b|#)', street, re.I):
        street = street.strip()[:m.start(2)] + '#' + m.group(2)
    apt = W._wp_street(street)
    m = re.search(r'\sApt\s(\S+)$', apt)
    if not m:
        return apt, ''
    return apt[:m.start()], m.group(1)


def _bare_unit(unit):
    m = re.match(r'^(\d+)-?([A-Za-z]?)$', unit)
    if not m:
        return unit
    return W._ordinal(m.group(1)) + (' ' + m.group(2).upper() if m.group(2) else '')


def candidates(street, city, full_addr):
    """name -> URL. The first entry is what Call Mode and the board build today."""
    base, unit = _street_unit(street)
    cty = '%s-FL' % _slug(city)
    plain = re.sub(r'(?:\b(?:apt|unit|ste|suite)\b\.?|#)\s*#?\s*', ' ', street, flags=re.I)
    out = {}
    if unit:
        out['apt (today)'] = '%s/address/%s/%s' % (BASE, _slug(base + ' Apt ' + unit), cty)
        out['unit'] = '%s/address/%s/%s' % (BASE, _slug(base + ' Unit ' + unit), cty)
        out['no label'] = '%s/address/%s/%s' % (BASE, _slug(base + ' ' + unit), cty)
        out['205th-C'] = '%s/address/%s/%s' % (BASE, _slug(base + ' ' + _bare_unit(unit)), cty)
        out['our spelling'] = '%s/address/%s/%s' % (BASE, _slug(plain), cty)
    else:
        out['ordinal (today)'] = '%s/address/%s/%s' % (BASE, _slug(base), cty)
        out['our spelling'] = '%s/address/%s/%s' % (BASE, _slug(plain), cty)
    out['property intel'] = 'https://property.whitepages.com/?address=' + urllib.parse.quote(full_addr)
    return out, unit


# ---- reading where the browser ended up -------------------------------------------------------------

def _history_files():
    la = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~/AppData/Local')
    pats = [os.path.join(la, 'Google', 'Chrome', 'User Data', '*', 'History'),
            os.path.join(la, 'Microsoft', 'Edge', 'User Data', '*', 'History'),
            os.path.join(la, 'BraveSoftware', 'Brave-Browser', 'User Data', '*', 'History')]
    return [f for p in pats for f in glob.glob(p)]


def _chrome_time(t):
    """Unix seconds -> Chromium visit_time (microseconds since 1601-01-01)."""
    return int((t + 11644473600) * 1000000)


def _visits_since(t0):
    """Every (unix_time, url, title) any Chromium profile recorded at or after t0."""
    rows = []
    for f in _history_files():
        if max(os.path.getmtime(x) for x in (f, f + '-wal', f + '-journal') if os.path.exists(x)) < t0 - 60:
            continue
        tmp = os.path.join(tempfile.mkdtemp(), 'History')
        try:
            shutil.copy2(f, tmp)
            for ext in ('-journal', '-wal'):
                if os.path.exists(f + ext):
                    shutil.copy2(f + ext, tmp + ext)
            con = sqlite3.connect(tmp)
        except Exception:
            try:
                con = sqlite3.connect('file:%s?mode=ro&immutable=1' % urllib.parse.quote(f.replace('\\', '/')),
                                      uri=True)
            except Exception:
                continue
        try:
            for vt, url, title in con.execute(
                    'SELECT v.visit_time, u.url, u.title FROM visits v JOIN urls u ON u.id = v.url '
                    'WHERE v.visit_time >= ? ORDER BY v.visit_time', (_chrome_time(t0),)):
                rows.append((vt / 1000000.0 - 11644473600, url or '', title or ''))
        except Exception:
            pass
        finally:
            con.close()
    rows.sort()
    return rows


_BLOCK = re.compile(r'just a moment|access denied|verify you are|are you a robot|captcha|attention required'
                    r'|sign in|log in', re.I)
_LIST = re.compile(r'property records|\bresults\b', re.I)


def _toks(t):
    t = urllib.parse.unquote(t or '').lower()
    t = re.sub(r'(\d+)(st|nd|rd|th)\b', r'\1', t)
    return re.findall(r'[a-z0-9]+', t)


_LABELS = ('apt', 'unit', 'ste', 'suite', 'no')


def _addr_part(url, title, opened):
    """The landed address as tokens: Whitepages' own slug when it redirected, else the title up to
    its first comma ("2634 SE 19th Ct Apt 205C, Homestead, FL 33035" -> the street line only, so a
    ZIP or city can never pass for a unit). None when the page kept our URL and its title says
    nothing (our own guess is not evidence)."""
    path = urllib.parse.urlparse(url).path
    seg = [x for x in path.split('/') if x]
    redirected = not (opened and path.rstrip('/').lower() == urllib.parse.urlparse(opened).path.rstrip('/').lower())
    if redirected and len(seg) >= 2 and seg[0] == 'address':
        return _toks(seg[1]), (_toks(seg[2].rsplit('-', 1)[0]) if len(seg) >= 3 else [])
    line = (title or '').split('|')[0]
    parts = line.split(',')
    if not re.match(r'\s*\d', parts[0]):
        return None
    return _toks(parts[0]), (_toks(parts[1]) if len(parts) > 1 else [])


def classify(url, title, street, unit, opened='', city=''):
    if not url:
        return 'none'
    if _BLOCK.search(title or '') and not re.match(r'\s*\d', title or ''):
        return 'blocked'
    part = _addr_part(url, title, opened)
    if part is None:
        return 'other' if _LIST.search(title or '') else 'none'
    got, got_city = part
    if city and got_city and not set(got_city) & set(_toks(city)):
        return 'other'                                     # same street line, another city (Miami ~ Miami Gardens passes)
    want = _toks(re.sub(r'(?:\b(?:apt|unit|ste|suite)\b\.?|#)\s*#?\s*\S+', ' ', street, flags=re.I))
    u = ''.join(_toks(unit))
    if unit and want and want[-1] == u:
        want = want[:-1]                                   # "... AVE 999": bare trailing unit
    if not want or got[:1] != want[:1]:
        return 'other'                                     # another house number (or a list)
    rest = list(got[1:])
    for t in want[1:]:                                     # take our street out, one token at a time
        if t in rest:
            rest.remove(t)
        elif t not in W._SUFFIXES:
            return 'other'                                 # a different street name or quadrant (NW vs SW)
    if any(t in W._DIRS for t in rest):
        return 'other'                                     # a quadrant we do not have
    rest = [t for t in rest if t not in _LABELS and t not in W._SUFFIXES]
    if not unit:
        return 'house' if not rest else 'other'
    if not rest:
        return 'building'
    return 'unit' if ''.join(rest) == u else 'other'      # exactly our unit, else another unit


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--condos', type=int, default=10)
    ap.add_argument('--houses', type=int, default=8)
    ap.add_argument('--gap', type=float, default=8.0, help='seconds between links (let each page load)')
    ap.add_argument('--settle', type=float, default=15.0, help='seconds to wait before reading history')
    a = ap.parse_args()

    if not _history_files():
        print('No Chrome / Edge / Brave history file found under LOCALAPPDATA; this needs one of them '
              'as the default browser.')
        return 2
    condos, houses = [], []
    for r in WL.load_all_leads():
        parts = WL._split_addr(WL._lead_addr(r))
        if not parts or not re.match(r'\s*\d', parts[0]):
            continue
        has_unit = bool(_street_unit(parts[0])[1])
        (condos if has_unit else houses).append(r)
    pick = condos[:a.condos] + houses[:a.houses]
    if not pick:
        print('no leads with a street address found')
        return 1
    n_links = sum(len(candidates(*WL._split_addr(WL._lead_addr(r))[:2], WL._lead_addr(r))[0]) for r in pick)
    print('%d condo lead(s), %d house lead(s), %d links, about %d min. Leave the browser alone; '
          'do not close tabs until it says done.' % (min(a.condos, len(condos)), min(a.houses, len(houses)),
                                                      n_links, round(n_links * a.gap / 60) + 1), flush=True)

    t_start = time.time()
    opened = []                             # (open_time, case, form, street, unit, url, city)
    for r in pick:
        addr = WL._lead_addr(r)
        street, city, _ = WL._split_addr(addr)
        forms, unit = candidates(street, city, addr)
        for form, url in forms.items():
            opened.append((time.time(), WL._lead_key(r), form, street, unit, url, city))
            webbrowser.open(url)
            time.sleep(a.gap)
        print('  ...%s: %d link(s) opened' % (WL._lead_key(r)[-4:], len(forms)), flush=True)
    print('waiting %ds for the browser to write its history...' % a.settle, flush=True)
    time.sleep(a.settle)
    visits = _visits_since(t_start - 2)

    table, detail = {}, []
    for i, (t, case, form, street, unit, url, city) in enumerate(opened):
        t_next = opened[i + 1][0] if i + 1 < len(opened) else t + a.gap
        mine = [v for v in visits if t <= v[0] < t_next]
        final = mine[-1] if mine else (0, '', '')
        verdict = classify(final[1], final[2], street, unit, url, city)
        kind = 'condo' if unit else 'house'
        table.setdefault((kind, form), {}).setdefault(verdict, 0)
        table[(kind, form)][verdict] += 1
        detail.append({'case': case, 'kind': kind, 'form': form, 'opened': url, 'landed': final[1],
                       'title': final[2], 'verdict': verdict, 'visits': len(mine)})

    cols = ['unit', 'building', 'house', 'other', 'blocked', 'none']
    print('\n%-6s %-17s ' % ('type', 'link shape') + ' '.join('%8s' % c for c in cols))
    for (kind, form), c in sorted(table.items()):
        print('%-6s %-17s ' % (kind, form) + ' '.join('%8d' % c.get(k, 0) for k in cols))
    print('\nper lead (u=unit b=building h=house o=other x=blocked -=none):')
    letter = {'unit': 'u', 'building': 'b', 'house': 'h', 'other': 'o', 'blocked': 'x', 'none': '-'}
    by = {}
    for d in detail:
        by.setdefault(d['case'], []).append('%s:%s' % (d['form'].split()[0], letter[d['verdict']]))
    for case, v in by.items():
        print('  ...%s  %s' % (case[-4:], '  '.join(v)))
    try:
        import paths as P
        dest = P.out('wp_linkprobe.json')
    except Exception:
        dest = os.path.join(os.path.expanduser('~'), 'DEALFLOW', 'wp_linkprobe.json')
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        json.dump(detail, open(dest, 'w', encoding='utf-8'), indent=1)
        print('\ndetails (addresses, landed URLs): %s' % dest)
    except Exception as e:
        print('\ncould not write details: %s' % e)
    print('done. You can close the browser window now.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
