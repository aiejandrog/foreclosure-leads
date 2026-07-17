"""Per-lead property IMAGES for the tracker. Enriches leads_final.json + *_leads.json in place with:
    photos:     [image_url, ...]   (Zillow listing photos when the property IS listed, else 1 aerial)
    zlisting:   '<real Zillow listing url>'  (only when a listing was found; '' otherwise)
    photo_kind: 'zillow' | 'aerial'

Design (mirrors skiptrace.py — a separate enrichment pass, run before the final make_tracker rebuild):
  * GUARANTEED BASELINE, keyless: every lead gets a real satellite AERIAL from Esri World Imagery, centered
    on the parcel via the free US Census batch geocoder. No API key, no signup, nothing to break in a demo.
  * BONUS LAYER (best-effort): most foreclosures are NOT listed on Zillow, but where one exists we pull the
    real listing photos (zillowstatic.com CDN) + the real listing URL. Throttled + fail-soft: any block just
    falls back to the aerial. NEVER lets Zillow's bot-wall break the build.

Usage:
  python property_photos.py            # geocode + aerial for all leads (fast, guaranteed)
  python property_photos.py --zillow   # also attempt the Zillow listing-photo layer (slower, fail-soft)
"""
import argparse, csv, io, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.path.join(HERE, 'docs', 'img')      # committed static images -> instant, no live dependency in the demo
LEAD_FILES = ['leads_final.json', 'broward_leads.json', 'palmbeach_leads.json']
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
CENSUS_BATCH = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
CENSUS_ONE = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'
ESRI = ('https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
        '?bbox={bbox}&bboxSR=4326&imageSR=4326&size=640,420&format=jpg&f=image')


def _addr_of(r):
    return (r.get('Address') or r.get('addr') or '').strip()

def _parse(addr):
    """'300 NW 199 ST, MIAMI, FL- 33169' / '3698 NW 39 ST, LAUDERDALE LAKES, 33309' -> (street, city, state, zip)."""
    s = re.sub(r'\bFL[-,]\s*', 'FL ', addr or '')
    parts = [p.strip() for p in s.split(',') if p.strip()]
    if len(parts) < 2:
        return None
    street = parts[0]
    zc = ''
    mz = re.search(r'(\d{5})(?:-\d{4})?\s*$', parts[-1])
    if mz: zc = mz.group(1)
    tail = re.sub(r'\d{5}(?:-\d{4})?\s*$', '', parts[-1]).strip()
    st = 'FL'
    sm = re.search(r'\b([A-Za-z]{2})\s*$', tail)
    if sm: st = sm.group(1).upper()
    # city = the part before the state/zip tail (2nd field), or the tail if it held the city
    city = parts[1] if len(parts) >= 3 else re.sub(r'\bFL\b.*$', '', tail).strip() or parts[1] if len(parts) > 1 else ''
    if len(parts) >= 3:
        city = parts[1]
    else:
        city = re.sub(r'\b[A-Za-z]{2}\b\s*$', '', tail).strip() or parts[-2] if len(parts) > 1 else ''
    return (street, city, st, zc)


def _aerial_url(lat, lon, d=0.0009):
    bbox = f"{lon-d},{lat-d},{lon+d},{lat+d}"
    return ESRI.format(bbox=bbox)

def _download_aerial(lat, lon, fname, sess):
    """Fetch the Esri aerial ONCE at build time and save it as a static jpg in docs/img/. Returns the
    site-relative path ('img/<fname>.jpg') or '' on failure. Idempotent: skips an existing non-empty file.
    Static local files load instantly + reliably in the demo — no 200-concurrent live-request throttling."""
    os.makedirs(IMGDIR, exist_ok=True)
    path = os.path.join(IMGDIR, fname + '.jpg')
    rel = 'img/' + fname + '.jpg'
    if os.path.exists(path) and os.path.getsize(path) > 1500:
        return rel
    try:
        r = sess.get(_aerial_url(lat, lon), timeout=25)
        if r.status_code == 200 and 'image' in (r.headers.get('content-type') or '') and len(r.content) > 1500:
            with open(path, 'wb') as f:
                f.write(r.content)
            return rel
    except Exception:
        pass
    return ''


def geocode_all(addrs):
    """Census BATCH geocoder: one POST, up to 10k addresses. Returns {addr: (lat,lon)}. Falls back to the
    one-line endpoint for any the batch missed."""
    out = {}
    uniq = [a for a in dict.fromkeys(addrs) if a]
    # build the required CSV: id, street, city, state, zip
    rows = []
    for i, a in enumerate(uniq):
        p = _parse(a)
        if not p: continue
        rows.append([str(i), p[0], p[1], p[2], p[3]])
    if rows:
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        buf.seek(0)
        try:
            resp = requests.post(CENSUS_BATCH,
                files={'addressFile': ('addr.csv', buf.getvalue(), 'text/csv')},
                data={'benchmark': 'Public_AR_Current'}, timeout=180)
            if resp.status_code == 200:
                for line in csv.reader(io.StringIO(resp.text)):
                    # id, input, match, matchtype, matched_addr, lon,lat, tigerid, side
                    if len(line) >= 7 and line[2] == 'Match':
                        try:
                            idx = int(line[0]); lon, lat = line[5].split(',')
                            out[uniq[idx]] = (float(lat), float(lon))
                        except Exception:
                            pass
        except Exception as e:
            print('  batch geocode failed, will one-line:', str(e)[:80])
    # one-line fallback for misses
    miss = [a for a in uniq if a not in out]
    for a in miss:
        try:
            g = requests.get(CENSUS_ONE, params={'address': a, 'benchmark': 'Public_AR_Current', 'format': 'json'},
                             headers={'User-Agent': UA}, timeout=20)
            m = (g.json().get('result', {}) or {}).get('addressMatches', [])
            if m:
                c = m[0]['coordinates']; out[a] = (float(c['y']), float(c['x']))
        except Exception:
            pass
        time.sleep(0.05)
    return out


# ---- Zillow listing photos (best-effort, fail-soft) ------------------------------------------------
def zillow_photos(addr, sess):
    """Return (photos[list], listing_url) or ([], '') — never raises. Only zillowstatic.com CDN urls."""
    try:
        q = re.sub(r'\s+', '-', re.sub(r'[,]', '', addr)).strip('-')
        url = 'https://www.zillow.com/homes/' + requests.utils.quote(addr) + '_rb/'
        r = sess.get(url, timeout=15)
        if r.status_code != 200 or 'zillowstatic.com' not in r.text:
            return [], ''
        html = r.text
        # real listing url if the search resolved to one
        lm = re.search(r'https://www\.zillow\.com/homedetails/[^"\']+_zpid/', html)
        listing = lm.group(0) if lm else ''
        # collect full-size zillowstatic photo urls, de-duped, upgraded to _f (full)
        raw = re.findall(r'https://photos\.zillowstatic\.com/[^"\'\\ ]+\.(?:jpg|webp|png)', html)
        seen, photos = set(), []
        for u in raw:
            u = re.sub(r'_[a-z]_[a-z]\.', '_p_f.', u)
            k = re.sub(r'-\d+_[a-z]+\.', '.', u)
            if k in seen: continue
            seen.add(k); photos.append(u)
            if len(photos) >= 12: break
        return photos, listing
    except Exception:
        return [], ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--zillow', action='store_true', help='also attempt the Zillow listing-photo layer (slower)')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    files = {f: json.load(open(os.path.join(HERE, f), encoding='utf-8'))
             for f in LEAD_FILES if os.path.exists(os.path.join(HERE, f))}
    all_leads = [r for rows in files.values() for r in rows]
    addrs = [_addr_of(r) for r in all_leads]
    print(f"geocoding {len(set(a for a in addrs if a))} unique addresses (Census, keyless)...")
    coords = geocode_all(addrs)
    print(f"  got coords for {len(coords)} addresses")

    sess = requests.Session()
    sess.headers.update({'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9'})

    n_aerial = n_zillow = n_none = 0
    # 1) Zillow bonus layer (serial, anti-ban) — only when --zillow
    for r in all_leads:
        r['photos'], r['zlisting'], r['photo_kind'] = [], '', ''
        if a.zillow and _addr_of(r):
            ph, zl = zillow_photos(_addr_of(r), sess)
            if ph: r['photos'], r['zlisting'], r['photo_kind'] = ph, zl, 'zillow'; n_zillow += 1
            time.sleep(0.6)
    # 2) Aerial fallback — PARALLEL download (Esri is a public tile service, safe to hit concurrently)
    def _folio(r): return re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or '')) or re.sub(r'[^a-z0-9]', '', (r.get('case') or r.get('Case #') or '').lower())
    todo = [(r, _folio(r), coords.get(_addr_of(r))) for r in all_leads if not r['photos']]
    def _do(job):
        r, folio, c = job
        if not (c and folio): return (r, '')
        s = requests.Session(); s.headers.update({'User-Agent': UA})
        return (r, _download_aerial(c[0], c[1], folio, s))
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, (r, rel) in enumerate(ex.map(_do, todo), 1):
            if rel: r['photos'] = [rel]; r['photo_kind'] = 'aerial'; n_aerial += 1
            else: n_none += 1
            if i % 80 == 0: print(f"  ...aerial {i}/{len(todo)}")

    for f, rows in files.items():
        json.dump(rows, open(os.path.join(HERE, f), 'w', encoding='utf-8'), indent=1)
    print(f"DONE: {n_zillow} zillow-photo, {n_aerial} aerial, {n_none} no-image  ->  {', '.join(files)}")


if __name__ == '__main__':
    main()
