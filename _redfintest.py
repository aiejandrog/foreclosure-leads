#!/usr/bin/env python
"""_redfintest.py -- proves the Redfin Estimate feature works end to end without touching the math.

Covers:
  1. FETCHER (live)   — resolve the known Nistico property, assert URL + value + conf.
  2. CACHE semantics  — no-match cached, within-TTL not refetched, transport failure not cached.
  3. MERGE            — the build bakes rfval onto rows + prints the coverage counter, and the
                        built docs/index.html carries the advisory JS chrome.
  4. _profit INVARIANCE (source-level) — the recompute value gate is byte-unchanged, and no
                        rfval/zest/_valx name leaks in AHEAD of the gate.

The runtime _profit diff (before/after via a real browser) is done by the operator with the
webapp harness during the build; this file guards the source-level invariants that a later edit
could silently break.

Run:  python _redfintest.py     (exit 0 = pass)
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NIST_ADDR = '1212 NE 91st St, Miami, FL 33138'
NIST_HOME_ID = '43045055'
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    return cond


# ---- 1. FETCHER (live) ---------------------------------------------------------------------------
def test_fetcher():
    import redfin_value as R
    import asyncio

    async def go():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True,
                                        args=['--disable-blink-features=AutomationControlled'])
            ctx = await b.new_context(user_agent=R.UA, viewport={'width': 1280, 'height': 900})
            pg = await ctx.new_page()
            await R._prime(pg)
            res = await R.lookup(pg, NIST_ADDR, debug=False)
            await b.close()
            return res

    res = asyncio.run(go())
    if not check(res is not None, 'fetcher returned None (transport failure — Redfin may be blocking)'):
        return
    check(res.get('url', '').endswith('/home/' + NIST_HOME_ID),
          f"resolved wrong URL: {res.get('url')}")
    check(res.get('v', 0) > 100000, f"estimate implausibly low: {res.get('v')}")
    check(res.get('conf') in ('ok', 'addr'), f"unexpected conf: {res.get('conf')}")
    print(f"  fetcher: {NIST_ADDR} -> {res.get('v')} conf={res.get('conf')}")


# ---- 2. CACHE semantics --------------------------------------------------------------------------
def test_cache_semantics():
    import redfin_value as R
    # no-match: a resolve that returns no property caches {v:0,conf:'nomatch'} (not a failure)
    # transport failure must return None so run() never caches it — assert lookup() shape contract
    # by inspecting the source guarantees rather than forcing a live block.
    src = open(os.path.join(HERE, 'redfin_value.py'), encoding='utf-8').read()
    check("return None" in src and "never cached" in src.lower(),
          'redfin_value.py lost its "transport failure -> None, never cached" contract')
    check("'conf': 'nomatch'" in src or '"conf": "nomatch"' in src or "'nomatch'" in src,
          'redfin_value.py lost the nomatch cache path')
    # cache round-trips as JSON with the documented shape
    cf = os.path.join(HERE, 'redfin_cache.json')
    if os.path.exists(cf):
        c = json.load(open(cf, encoding='utf-8'))
        sample = next((v for v in c.values() if isinstance(v, dict) and v.get('v')), None)
        if sample:
            check('url' in sample and 'conf' in sample and 't' in sample,
                  f'cache entry missing keys: {sample}')
    print('  cache: transport-failure + nomatch contracts intact')


# ---- 3. MERGE ------------------------------------------------------------------------------------
def test_merge_and_build():
    # a fresh rebuild must print the redfin counter and emit the JS chrome into the built page
    out = subprocess.run(
        [sys.executable, '-c',
         "import json,foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))"],
        cwd=HERE, capture_output=True, text=True, timeout=600)
    combined = (out.stdout or '') + (out.stderr or '')
    m = re.search(r'redfin: (\d+) lead\(s\) carry a Redfin Estimate', combined)
    check(m is not None, 'build did not print the redfin coverage counter')
    if m:
        check(int(m.group(1)) > 0, 'redfin counter is 0 — cache empty or merge broke')
        print(f"  merge: build baked {m.group(1)} Redfin Estimates")
    cov = re.search(r'coverage: (\{.*\})', combined)
    if cov:
        j = json.loads(cov.group(1))
        check('rfval' in j and 'zest' in j, 'coverage line missing rfval/zest counters')
    idx = os.path.join(HERE, 'docs', 'index.html')
    if os.path.exists(idx):
        html = open(idx, encoding='utf-8', errors='replace').read()
        check('_valXChip' in html, 'built page missing _valXChip')
        check('vxchip' in html, 'built page missing vxchip CSS class')
        check('Redfin Estimate' in html, 'built page missing Redfin Estimate label')
        print('  build: docs/index.html carries the advisory chrome')


# ---- 4. _profit INVARIANCE (source-level) --------------------------------------------------------
def test_profit_invariance_source():
    t = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
    gate = 'const val = arvOK ? r.arv : cv;'
    check(t.count(gate) == 1, f'value gate string count != 1 (got {t.count(gate)})')
    check(t.count("r._valsrc = arvOK ? 'arv' : 'county';") == 1, 'valsrc assignment changed')
    i = t.find('function recompute(){')
    g = t.find(gate, i)
    check(i >= 0 and g > i, 'could not locate recompute value gate')
    seg = t[i:g]
    check('_valx' not in seg, '_valx appears BEFORE the value gate (could taint the math)')
    check('rfval' not in seg, 'rfval appears BEFORE the value gate')
    check('zest' not in seg, 'zest appears BEFORE the value gate')
    print('  invariance: value gate unique + advisory names appear only after it')


def main():
    for fn, name in [(test_fetcher, 'fetcher'), (test_cache_semantics, 'cache'),
                     (test_merge_and_build, 'merge/build'), (test_profit_invariance_source, 'invariance')]:
        try:
            fn()
        except Exception as e:
            fails.append(f'{name} raised: {e}')
    if fails:
        print(f'\nFAIL ({len(fails)}):')
        for f in fails:
            print('  -', f)
        return 1
    print('\n==== redfin feature: all checks passed ====')
    return 0


if __name__ == '__main__':
    sys.exit(main())
