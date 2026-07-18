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
import argparse, csv, io, json, math, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor
import requests
try:
    from PIL import Image                      # optional: recompresses aerials (Esri ignores
except Exception:                              # compressionQuality on export) ~390KB -> ~150KB
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.path.join(HERE, 'docs', 'img')      # committed static images -> instant, no live dependency in the demo
LEAD_FILES = ['leads_final.json', 'broward_leads.json', 'palmbeach_leads.json']
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
CENSUS_BATCH = 'https://geocoding.geo.census.gov/geocoder/locations/addressbatch'
CENSUS_ONE = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'
ESRI = ('https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
        '?bbox={bbox}&bboxSR=4326&imageSR=4326&size=1280,840&compressionQuality=70&format=jpg&f=image')

# Google Street View Static API — the tranchi.ai photo model: front-of-house shot keyed by ADDRESS.
# The key is OPTIONAL and never ships to the browser (images are baked to static jpgs at build time).
# Key sources: env GOOGLE_STREET_VIEW_KEY, or a one-line streetview.key file next to this script.
SV_META = 'https://maps.googleapis.com/maps/api/streetview/metadata'
SV_IMG = 'https://maps.googleapis.com/maps/api/streetview'

def _sv_key():
    k = os.environ.get('GOOGLE_STREET_VIEW_KEY', '').strip()
    if not k:
        p = os.path.join(HERE, 'streetview.key')
        if os.path.exists(p):
            k = open(p, encoding='utf-8').read().strip()
    return k

# FDOR statewide cadastral (same layer fl_cadastral.py uses) — parcel POLYGONS by folio. The polygon
# centroid is the true parcel location (rooftop-grade, keyless), which lets us aim the Street View
# camera at the house instead of trusting Google's address auto-aim (which loves lawns and bushes).
CADASTRAL = ('https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/'
             'Florida_Statewide_Cadastral/FeatureServer/0/query')

def _bearing(lat1, lon1, lat2, lon2):
    """Compass bearing (deg) from point 1 -> point 2. This is the Street View 'heading' param."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def _dist_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(h))

def _parcel_centroids(folios, sess):
    """{folio(digits): (lat, lon)} — batched IN() queries against the FDOR cadastral, with backoff."""
    out = {}
    todo = [f for f in dict.fromkeys(folios) if f and f.isdigit()]
    for i in range(0, len(todo), 80):
        batch = todo[i:i+80]
        where = "PARCEL_ID IN (%s)" % ','.join("'%s'" % f for f in batch)
        for t in range(3):
            try:
                r = sess.get(CADASTRAL, params={'where': where, 'outFields': 'PARCEL_ID',
                             'returnGeometry': 'true', 'outSR': 4326, 'f': 'json'}, timeout=60)
                j = r.json()
                if j.get('error'):
                    raise RuntimeError(j['error'].get('message', 'query error'))
                for f in j.get('features', []):
                    rings = (f.get('geometry') or {}).get('rings') or []
                    if not rings or len(rings[0]) < 2: continue
                    pts = rings[0][:-1] if rings[0][0] == rings[0][-1] else rings[0]
                    lon = sum(p[0] for p in pts) / len(pts)
                    lat = sum(p[1] for p in pts) / len(pts)
                    out[str(f['attributes']['PARCEL_ID'])] = (lat, lon)
                break
            except Exception:
                time.sleep(2 * (t + 1))
    return out


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

SV_BLOCK = os.path.join(HERE, 'sv_blocked.json')   # folios whose street shot is a green wall — don't re-buy it
_blk_lock = threading.Lock()
try:
    _blocked = set(json.load(open(SV_BLOCK, encoding='utf-8')))
except Exception:
    _blocked = set()

def _too_green(path):
    """True when the center band of the shot is mostly vegetation — a hedge/tree wall between the
    camera and the house. Those shots are worse than the satellite, so we reject them."""
    if not Image:
        return False
    try:
        im = Image.open(path).convert('RGB').resize((64, 42))
        px = list(im.getdata())
        w, h = 64, 42
        band = [px[y*w + x] for y in range(h//4, 3*h//4) for x in range(w)]
        green = sum(1 for r, g, b in band if g > r + 12 and g > b + 12)
        return green / len(band) > 0.55
    except Exception:
        return False

def _is_vacant(r):
    """Vacant land gets the AERIAL, always — a street-level shot of an empty lot is asphalt/grass.
    Miami-Dade raw leads carry dor_desc; Broward/Palm Beach files are pre-slimmed with a 'vac' flag."""
    return bool(r.get('vac')) \
        or bool(re.search(r'VACANT', str(r.get('dor_desc') or ''), re.I)) \
        or str(r.get('use_code') or '').strip() in ('0', '00', '000', '0000')

def _download_appraiser_bcpa(folio, fname, sess):
    """Broward Property Appraiser (BCPA) building photo — the ONLY county appraiser we found that
    serves real front-of-house photos (MDCPA has aerials only, PBCPA has building sketches only).
    Photo index: bcpa.net/Photographs.asp?Folio=<folio> lists all photos ever taken. We grab the
    most recent by filename date. Returns 'img/<fname>_pa.jpg' or ''. Public records, no bot walls."""
    if not folio or not folio.isdigit():
        return ''
    os.makedirs(IMGDIR, exist_ok=True)
    path = os.path.join(IMGDIR, fname + '_pa.jpg')
    rel = 'img/' + fname + '_pa.jpg'
    if os.path.exists(path) and os.path.getsize(path) > 3000:
        return rel
    try:
        r = sess.get(f'https://bcpa.net/Photographs.asp?Folio={folio}', timeout=20,
                     headers={'Referer': f'https://bcpa.net/RecInfo.asp?URL_Folio={folio}'})
        if r.status_code != 200:
            return ''
        urls = re.findall(r'/Photographs/[0-9]+/[0-9]+/[0-9]+/[^"\']+\.jpg', r.text)
        if not urls:
            return ''
        # sort by embedded timestamp when present (YYYYMMDD_HHMMSS) — newest first
        def _ts(u):
            m = re.search(r'_(\d{8})_(\d{6})', u)
            if m: return m.group(1) + m.group(2)
            m = re.search(r'-(\d{14})', u)
            if m: return m.group(1)
            return '0'
        best = sorted(urls, key=_ts, reverse=True)[0]
        p = sess.get('https://bcpa.net' + best, timeout=20,
                     headers={'Referer': f'https://bcpa.net/Photographs.asp?Folio={folio}'})
        if p.status_code != 200 or 'image' not in (p.headers.get('content-type') or '') or len(p.content) < 3000:
            return ''
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, 'wb') as f: f.write(p.content)
        if Image:
            try:
                Image.open(tmp).save(tmp, 'JPEG', quality=75, optimize=True)
            except Exception:
                pass
        os.replace(tmp, path)
        return rel
    except Exception:
        return ''

def _download_streetview(addr, fname, key, sess, target=None):
    """Front-of-house photo, AIM-LOCKED when we know the parcel's true location (FDOR centroid,
    else Census coords): find the nearest outdoor pano, compute the camera->parcel compass bearing,
    and request that exact pano with an explicit heading — Google's address auto-aim faces lawns/
    bushes too often to trust. Distance-gated: a pano >150m out can't see the house (gated
    community), so fall through to the aerial instead of shipping a useless shot.
    Free metadata check first so 'no imagery here' never burns a paid request.
    Returns 'img/<fname>_sv.jpg' or ''. Idempotent like the aerial download."""
    os.makedirs(IMGDIR, exist_ok=True)
    path = os.path.join(IMGDIR, fname + '_sv.jpg')
    rel = 'img/' + fname + '_sv.jpg'
    if fname in _blocked:
        return ''
    if os.path.exists(path) and os.path.getsize(path) > 3000:
        return rel
    try:
        loc = f"{target[0]},{target[1]}" if target else addr
        m = sess.get(SV_META, params={'location': loc, 'source': 'outdoor', 'radius': 80, 'key': key}, timeout=15)
        if m.status_code != 200 or m.json().get('status') != 'OK':
            return ''
        md = m.json()
        pl = md.get('location') or {}
        params = {'size': '640x420', 'fov': 72, 'pitch': 0, 'source': 'outdoor', 'key': key}
        if target and md.get('pano_id') and pl.get('lat') is not None:
            if _dist_m(pl['lat'], pl['lng'], target[0], target[1]) > 150:
                return ''
            params['pano'] = md['pano_id']
            params['heading'] = round(_bearing(pl['lat'], pl['lng'], target[0], target[1]), 1)
        else:
            params['location'] = loc
        r = sess.get(SV_IMG, params=params, timeout=25)
        if r.status_code == 200 and 'image' in (r.headers.get('content-type') or '') and len(r.content) > 3000:
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, 'wb') as f:
                f.write(r.content)
            if _too_green(tmp):
                os.remove(tmp)
                with _blk_lock:
                    _blocked.add(fname)
                return ''
            os.replace(tmp, path)
            return rel
    except Exception:
        pass
    return ''

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
            # write+recompress to a per-thread temp then atomically replace — duplicate folios can
            # put two workers on the same target path, and a half-written jpg must never be visible
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, 'wb') as f:
                f.write(r.content)
            if Image:
                try:
                    Image.open(tmp).save(tmp, 'JPEG', quality=72, optimize=True)
                except Exception:
                    pass
            os.replace(tmp, path)
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
    # one-line fallback for misses; retry once with the unit number stripped ('1101 SW 122 AVE 307'
    # -> '1101 SW 122 AVE') — trailing condo/apt units are the #1 cause of Census misses.
    def _one(q):
        g = requests.get(CENSUS_ONE, params={'address': q, 'benchmark': 'Public_AR_Current', 'format': 'json'},
                         headers={'User-Agent': UA}, timeout=20)
        m = (g.json().get('result', {}) or {}).get('addressMatches', [])
        return (float(m[0]['coordinates']['y']), float(m[0]['coordinates']['x'])) if m else None
    miss = [a for a in uniq if a not in out]
    for a in miss:
        try:
            c = _one(a)
            if not c:
                parts = a.split(',', 1)
                street = re.sub(r'\s+(?:APT|UNIT|STE|LOT|#)\s*\S+\s*$', '', parts[0], flags=re.I)
                # NOTE: no HWY/PKWY here — 'US HWY 1' etc. legitimately carry a route number
                street = re.sub(r'^(.*?\b(?:AVE|ST|RD|DR|CT|LN|PL|TER|TERR|WAY|BLVD|CIR|TRL)\b)\s+\d+\s*$',
                                r'\1', street, flags=re.I)
                simp = street + (',' + parts[1] if len(parts) > 1 else '')
                if simp.strip() != a.strip():
                    c = _one(simp)
            if c:
                out[a] = c
        except Exception:
            pass
        time.sleep(0.05)
    return out


# ---- Zillow listing photos (best-effort, fail-soft) ------------------------------------------------
_ZHDRS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Sec-Ch-Ua': '"Chromium";v="128", "Not;A=Brand";v="24"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'none',
    'Upgrade-Insecure-Requests': '1',
}

def zillow_photos(addr, sess):
    """Real MLS listing photos when the property is currently listed on Zillow.
    Two-step: address search → resolve the specific zpid → open homedetails → extract THAT listing's
    photos only (the search page mixes listings, so scraping it directly leaks neighbors' photos).
    The default python-requests UA gets a 403 CAPTCHA; a realistic Chrome fingerprint + Referer chain
    passes the same page. Return ([], '') when the property isn't MLS-listed (Zestimate-only page —
    no listing photos exist to fetch, no scraper can fix that)."""
    try:
        sess.headers.update(_ZHDRS)
        # 1) search — resolves the exact homedetails URL and its zpid
        url = 'https://www.zillow.com/homes/' + requests.utils.quote(addr) + '_rb/'
        r = sess.get(url, timeout=20)
        if r.status_code != 200:
            return [], ''
        lm = re.search(r'https://www\.zillow\.com/homedetails/[^"\'<>\s]+/(\d+)_zpid/', r.text)
        if not lm:
            return [], ''
        listing = lm.group(0)
        # 2) homedetails — every zillowstatic.com photo on this page belongs to this one listing
        h2 = dict(_ZHDRS); h2['Referer'] = url; h2['Sec-Fetch-Site'] = 'same-origin'
        r2 = sess.get(listing, headers=h2, timeout=20)
        if r2.status_code != 200:
            return [], listing
        # 3) dedupe by hash prefix + upgrade every URL to cc_ft_1536 (Zillow's largest variant)
        raw = re.findall(r'https://photos\.zillowstatic\.com/fp/[a-f0-9]+-[a-z_]+_ft_(?:\d+)\.jpg', r2.text)
        seen, photos = set(), []
        for u in raw:
            m = re.match(r'(https://photos\.zillowstatic\.com/fp/[a-f0-9]+)-', u)
            if not m: continue
            base = m.group(1)
            if base in seen: continue
            seen.add(base)
            photos.append(re.sub(r'-[a-z_]+_ft_\d+\.jpg', '-cc_ft_1536.jpg', u))
            if len(photos) >= 8: break
        return photos, listing
    except Exception:
        return [], ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--zillow', action='store_true', help='also attempt the Zillow listing-photo layer (slower)')
    ap.add_argument('--tier', default='', help='restrict the SLOW Zillow layer to tiers, e.g. --tier A or --tier A,B')
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

    n_aerial = n_zillow = n_sv = n_none = 0
    tiers = {t.strip().upper() for t in a.tier.split(',') if t.strip()}
    def _folio(r): return re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or '')) or re.sub(r'[^a-z0-9]', '', (r.get('case') or r.get('Case #') or '').lower())
    # 1) Zillow bonus layer (serial, anti-ban) — only when --zillow, optionally tier-gated.
    # The new scraper does 2 requests per lead (search -> homedetails), so pace at 1.2s and warm up
    # the session with a landing-page hit to seed the anti-bot cookies.
    if a.zillow:
        try:
            zsess = requests.Session(); zsess.headers.update(_ZHDRS)
            zsess.get('https://www.zillow.com/', timeout=20)
        except Exception:
            zsess = sess
    for r in all_leads:
        # preserve last run's Zillow hit — zillowstatic URLs are stable, no need to re-scrape and
        # burn ~2s per lead re-verifying what we already have. Street View/aerial are always cheap
        # to re-derive from the coords, so those reset freely.
        prev_zillow = (r.get('photo_kind') == 'zillow' and (r.get('photos') or []) and r.get('zlisting'))
        prev = list(r.get('photos') or []), r.get('zlisting', '')
        r['photos'], r['zlisting'], r['photo_kind'] = [], '', ''
        # every geocoded lead gets an absolute aerial URL — the tracker uses it as an onerror
        # fallback so a bare emailed HTML (no img/ folder) still shows photos when online.
        c = coords.get(_addr_of(r))
        r['aurl'] = _aerial_url(c[0], c[1]) if c else ''
        if a.zillow and _addr_of(r) and (not tiers or str(r.get('tier') or r.get('Tier') or '').upper() in tiers):
            if prev_zillow:
                r['photos'], r['zlisting'], r['photo_kind'] = prev[0], prev[1], 'zillow'
                n_zillow += 1
            else:
                ph, zl = zillow_photos(_addr_of(r), zsess)
                if ph: r['photos'], r['zlisting'], r['photo_kind'] = ph, zl, 'zillow'; n_zillow += 1
                time.sleep(1.2)
    # 2) Street View layer (tranchi.ai-style front-of-house) — PARALLEL, only when a key exists
    svkey = _sv_key()
    if svkey:
        todo = [(r, _folio(r), _addr_of(r)) for r in all_leads if not r['photos'] and not _is_vacant(r)]
        cents = _parcel_centroids([f for _, f, _ in todo], sess)
        print(f"  parcel centroids for camera aim: {len(cents)} (FDOR cadastral)")
        # Backfill aurl for leads whose Census geocode failed but FDOR knows the parcel — that
        # gives the tracker's <img data-fb=> onerror an actual satellite URL instead of an empty
        # attribute, so bare-emailed HTMLs still show aerials for these leads too.
        _backfilled = 0
        for r, folio, _addr in todo:
            if not r.get('aurl') and folio in cents:
                r['aurl'] = _aerial_url(cents[folio][0], cents[folio][1])
                _backfilled += 1
        if _backfilled: print(f"  backfilled aurl for {_backfilled} leads via FDOR")
        def _dsv(job):
            r, folio, addr = job
            if not (addr and folio): return (r, '')
            s = requests.Session(); s.headers.update({'User-Agent': UA})
            return (r, _download_streetview(addr, folio, svkey, s, cents.get(folio) or coords.get(addr)))
        with ThreadPoolExecutor(max_workers=8) as ex:
            for i, (r, rel) in enumerate(ex.map(_dsv, todo), 1):
                if rel: r['photos'] = [rel]; r['photo_kind'] = 'street'; n_sv += 1
                if i % 80 == 0: print(f"  ...streetview {i}/{len(todo)}")
    else:
        print('  (no Street View key — set GOOGLE_STREET_VIEW_KEY or create streetview.key to enable house photos)')
    # 3) BCPA appraiser photo — Broward-only fallback for leads Zillow/StreetView didn't cover.
    # MDCPA has no photos (aerial maps only) and PBCPA has sketches only, so Broward is the whole game.
    n_bcpa = 0
    todo = [(r, _folio(r)) for r in all_leads
            if not r['photos']
            and str(r.get('county','')).upper() == 'BROWARD'
            and not _is_vacant(r)]
    def _dpa(job):
        r, folio = job
        s = requests.Session(); s.headers.update({'User-Agent': UA})
        return (r, _download_appraiser_bcpa(folio, folio, s))
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (r, rel) in enumerate(ex.map(_dpa, todo), 1):
            if rel: r['photos'] = [rel]; r['photo_kind'] = 'appraiser'; n_bcpa += 1
            if i % 40 == 0: print(f"  ...bcpa {i}/{len(todo)}")

    # 4) Aerial fallback — PARALLEL download (Esri is a public tile service, safe to hit concurrently).
    # Coordinate priority: Census geocode first, else the FDOR parcel centroid we fetched for camera
    # aim (works for leads with a folio but no successful Census hit — recovers a handful of misses).
    cents_all = locals().get('cents') or {}
    todo = [(r, _folio(r), coords.get(_addr_of(r)) or cents_all.get(_folio(r))) for r in all_leads if not r['photos']]
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
    try:
        json.dump(sorted(_blocked), open(SV_BLOCK, 'w', encoding='utf-8'))
    except Exception:
        pass
    print(f"DONE: {n_zillow} zillow-photo, {n_sv} street-view, {n_bcpa} bcpa-appraiser, {n_aerial} aerial, {n_none} no-image"
          + (f" ({len(_blocked)} hedge-blocked)" if _blocked else "") + f"  ->  {', '.join(files)}")


if __name__ == '__main__':
    main()
