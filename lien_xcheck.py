#!/usr/bin/env python3
"""lien_xcheck.py -- a second source for the recorded-lien chain, used only to DISAGREE.

The website name search places about 1 mortgage in 4 on the parcel and a clean-looking chain
from it proves little. The Clerk CDS folio search (10-01 recall test: 2 of 9 mortgages) is a
poor lien source too, so neither may supply a 'clear'. What two sources CAN do is catch each
other: any open mortgage or lien the second source shows that the chain does not carry means
the chain is incomplete, and `equity_state` must then stop calling it a FACT.

Rows live in DEALFLOW_DIR/xsource_liens.json  {case: {"ts": iso, "rows": [{cat, d, bp, amt}]}}
(never in the repo; no names). `stamp()` adds `xs_conflict` (a list of book/page) to a copy of
the chain. No cache entry for a case leaves the chain exactly as it was.

Free source (the paid daily records file, laptop only):
    python lien_xcheck.py --from-daily

Fetch (laptop only, needs CLERK_CDS_AUTHKEY and the registered IP, $0.20 a lookup, paid_reads
ledger charged first and refunded on a rejection):
    python lien_xcheck.py --fetch --max 10
"""
import json, os, re, sys, datetime
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Codes verified in or_daily_file.CODE_CATEGORY; only these can contradict a chain.
OPEN_CATS = {'MOR': 'mortgage', 'LIE': 'lien', 'JUD': 'judgment'}
RELEASE_CODES = {'REL', 'SAT'}
CACHE_NAME = 'xsource_liens.json'
URL = 'https://www2.miamidadeclerk.gov/Developers/api/OfficialRecords'


def _bp(book, page):
    parts = [str(x).strip().lstrip('0') or '0' for x in (book, page) if str(x or '').strip()]
    return '/'.join(parts) if len(parts) == 2 else ''


def _norm_bp(s):
    p = re.findall(r'\d+', str(s or ''))[:2]
    return '/'.join(x.lstrip('0') or '0' for x in p) if len(p) == 2 else ''


def parse_cds_xml(raw):
    """CDS OfficialRecords XML -> deduped rows [{cat, d (m/d/yyyy), bp, amt}]. One XML row is one
    PARTY, so dedupe by book/page. Codes outside MOR/LIE/JUD/REL/SAT are dropped."""
    root = ET.fromstring(raw)
    s = lambda t: t.split('}')[-1]
    out, seen = [], set()
    for e in root.iter():
        if s(e.tag) != 'OfficialRecords':
            continue
        r = {s(c.tag): (c.text or '').strip() for c in e}
        code = r.get('DOC_TYPE', '').upper()
        if code not in OPEN_CATS and code not in RELEASE_CODES:
            continue
        bp = _bp(r.get('REC_BOOK'), r.get('REC_PAGE'))
        if not bp or (code, bp) in seen:
            continue
        seen.add((code, bp))
        d = r.get('REC_DATE', '')[:10]
        m = re.match(r'(\d{4})-(\d\d)-(\d\d)', d)
        d = '%d/%d/%s' % (int(m.group(2)), int(m.group(3)), m.group(1)) if m else ''
        amt = 0
        try:
            if float(r.get('INTANGIBLE') or 0) > 0:
                amt = round(float(r['INTANGIBLE']) / 0.002)
            elif float(r.get('CONSIDERATION_1') or 0) > 0:
                amt = round(float(r['CONSIDERATION_1']))
        except ValueError:
            pass
        row = {'cat': 'release' if code in RELEASE_CODES else OPEN_CATS[code], 'd': d, 'bp': bp, 'amt': amt}
        ob = _bp(r.get('ORIG_REC_BOOK'), r.get('ORIG_REC_PAGE'))
        if ob:
            row['orig'] = ob            # the instrument this one releases
        out.append(row)
    return out


def conflicts(chain, rows):
    """Book/pages of open mortgage/lien/judgment rows the second source shows that the chain does
    not carry (as a surviving lien, a satisfied one, or a release target). [] = no disagreement."""
    known = set()
    for l in [x for x in ((chain or {}).get('liens') or []) if isinstance(x, dict)]:
        if isinstance(l, dict):
            known.add(_norm_bp(l.get('bp')))
    # liens and judgments (association, code, IRS, the case's own judgment) live in chain['other'],
    # open or released; the chain already accounts for every one of them.
    for l in [x for x in ((chain or {}).get('other') or []) if isinstance(x, dict)]:
        if isinstance(l, dict):
            known.add(_norm_bp(l.get('bp')))
    released = {_norm_bp(r.get('orig')) for r in rows if r.get('cat') == 'release' and r.get('orig')}
    out = []
    for r in rows:
        if r.get('cat') == 'release':
            continue
        bp = _norm_bp(r.get('bp'))
        if r.get('cat') == 'mortgage' and not r.get('amt'):
            continue        # records_liens skips modification/assignment rows and a bare $0 mortgage is counted unpriced
        if bp and bp not in known and bp not in released:
            out.append(bp)
    return sorted(set(out))


def _cache_path():
    import paths as P
    return os.path.join(P.DEALFLOW_DIR, CACHE_NAME)


def load_cache(path=None):
    p = path or _cache_path()
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        print('lien_xcheck: %s is unreadable; second-source demotions are OFF this build' % CACHE_NAME)
        return {}


def stamp(chain, case, cache):
    """Copy of `chain` carrying `xs_conflict` when the second source disagrees. Unchanged when
    there is no cache entry for the case."""
    ent = (cache or {}).get(case)
    if not chain or not isinstance(chain, dict) or not isinstance(ent, dict):
        return chain
    try:
        rows = [r for r in (ent.get('rows') or []) if isinstance(r, dict)]
        c = conflicts(chain, rows)
    except Exception:
        return chain
    if not c:
        return chain
    out = dict(chain)
    out['xs_conflict'] = c
    return out


def rows_from_daily(conn, folio):
    """Rows for one folio out of the paid daily records file (or_daily.sqlite): free, no API units.
    Same row shape as parse_cds_xml. Covers recent recordings only (the file keeps ~30 days), which
    are exactly the mortgages the name search tends to miss."""
    import or_daily_file as O
    nf = O.norm_folio(folio)
    if not nf:
        return []
    out, seen = [], set()
    for r in conn.execute('SELECT * FROM rows WHERE folio_norm = ? AND deleted = 0', (nf,)):
        cat = O.category_of(r['doc_type'], r['doc_desc'])
        if cat not in ('mortgage', 'lien', 'judgment', 'release'):
            continue
        bp = O.norm_bp(r['book'], r['page'])
        if not bp or (cat, bp) in seen:
            continue
        seen.add((cat, bp))
        d = r['rec_date'] or ''
        m = re.match(r'(\d{4})-(\d\d)-(\d\d)', d)
        d = '%d/%d/%s' % (int(m.group(2)), int(m.group(3)), m.group(1)) if m else d
        amt = 0
        try:
            if O.money_amount(r['intangible']):
                amt = round(O.money_amount(r['intangible']) / 0.002)
            elif O.money_amount(r['consideration_1']):
                amt = round(O.money_amount(r['consideration_1']))
        except (TypeError, ValueError):
            pass
        row = {'cat': cat, 'd': d, 'bp': '/'.join(bp), 'amt': amt, 'src': 'daily'}
        ob = O.norm_bp(r['orig_book'], r['orig_page'])
        if ob:
            row['orig'] = '/'.join(ob)
        out.append(row)
    return out


def from_daily(data_dir=None):
    """Merge daily-file rows into the cache for every Miami lead folio. Free. Existing cached rows stay."""
    import or_daily_file as O
    data_dir = O.resolve_data_dir(data_dir)
    dbp = os.path.join(data_dir, 'or_daily.sqlite')
    if not os.path.exists(dbp):
        raise SystemExit('no or_daily.sqlite at ' + dbp)
    if os.path.exists(_cache_path()) and not load_cache() and os.path.getsize(_cache_path()) > 2:
        raise SystemExit('%s exists but is unreadable; fix or move it first' % CACHE_NAME)
    cache = load_cache()
    with open(os.path.join(HERE, 'leads_final.json'), encoding='utf-8') as fh:
        L = json.load(fh)
    conn = O.open_db(dbp)
    n = 0
    try:
        for r in L:
            f, c = str(r.get('Folio') or ''), r.get('Case #')
            if not re.fullmatch(r'\d{13}', f) or r.get('sale_type') == 'TD' or not c:
                continue
            new = rows_from_daily(conn, f)
            if not new:
                continue
            ent = cache.get(c) if isinstance(cache.get(c), dict) else {'rows': []}
            have = {(x.get('cat'), x.get('bp')) for x in ent.get('rows') or [] if isinstance(x, dict)}
            add = [x for x in new if (x['cat'], x['bp']) not in have]
            if add:
                ent['rows'] = list(ent.get('rows') or []) + add
                ent.setdefault('ts', datetime.datetime.now().isoformat(timespec='seconds'))
                cache[c] = ent
                n += 1
    finally:
        conn.close()
    tmp = _cache_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(cache, fh)
    os.replace(tmp, _cache_path())
    print('daily file: %d leads gained second-source rows' % n)


def fetch(max_leads, per_call=0.20):
    import urllib.request, urllib.parse, time
    import paid_reads as PR
    key = os.environ.get('CLERK_CDS_AUTHKEY')
    if not key:
        raise SystemExit('CLERK_CDS_AUTHKEY not set in this shell')
    with open(os.path.join(HERE, 'leads_final.json'), encoding='utf-8') as fh:
        L = json.load(fh)
    with open(os.path.join(HERE, 'records_liens.json'), encoding='utf-8') as fh:
        R = json.load(fh)
    if os.path.exists(_cache_path()) and not load_cache():
        if os.path.getsize(_cache_path()) > 2:
            raise SystemExit('%s exists but is unreadable; fix or move it before fetching (it would be overwritten)' % CACHE_NAME)
    cache = load_cache()
    todo = []
    for r in L:
        f = str(r.get('Folio') or '')
        c = r.get('Case #')
        if re.fullmatch(r'\d{13}', f) and r.get('sale_type') != 'TD' and isinstance(R.get(c), dict) and c not in cache:
            todo.append((c, f, bool(R[c].get('liens'))))
    todo.sort(key=lambda t: not t[2])     # leads that look priced first: the ones a false 'priced' hurts
    n = 0
    for c, f, _ in todo[:max_leads]:
        ok, why = PR.debit(per_call, 'clerk-cds-xcheck')
        if not ok:
            print('ledger refused:', why)
            break
        q = urllib.parse.urlencode({'parameter1': f, 'parameter2': 'FN', 'authKey': key})
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                URL + '?' + q, headers={'Accept': 'application/xml'}), timeout=60).read()
        except Exception as e:
            # no response body arrived (HTTP error, refused): the clerk did not bill it
            PR.adjust(-per_call, 'clerk-cds-xcheck')
            print('no response, refunded:', str(e)[:80])
            continue
        try:
            root = ET.fromstring(raw)
            st = {t.tag.split('}')[-1]: (t.text or '').strip() for t in root if len(list(t)) == 0}
        except Exception as e:
            print('unparseable response, NOT refunded (it may have been billed):', str(e)[:60])
            continue
        if st.get('Status', '').lower() == 'failed':
            PR.adjust(-per_call, 'clerk-cds-xcheck')
            print('rejected, refunded:', st.get('StatusDesc', '')[:80])
            continue
        rows = parse_cds_xml(raw)
        cache[c] = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'rows': rows}
        n += 1
        tmp = _cache_path() + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(cache, fh)
        os.replace(tmp, _cache_path())
        time.sleep(1)
    print('cross-checked %d leads' % n)


if __name__ == '__main__':
    if '--from-daily' in sys.argv:
        from_daily()
    elif '--fetch' in sys.argv:
        m = int(sys.argv[sys.argv.index('--max') + 1]) if '--max' in sys.argv else 10
        fetch(m)
    else:
        print(__doc__)
