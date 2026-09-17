"""_wplinktest -- the lead deep-links and the Whitepages links, asserted on the SHIPPED bytes.

Run:  python _wplinktest.py     (exit 0 = safe; no network, no browser, no board, no real data)

WHAT BROKE (2026-09-17, reported by Alejandro: "the links and deep links to leads and the
whitepages links not sending me to their actual links its a placeholder").

  1. wp_prop_ids.json stores every id HEX-ENCODED -- "70726f2d52564d4b45706135587a4b" is the hex of
     "pro-RVMKEpa5XzK" -- and nothing in the repo decoded it. make_tracker baked the hex blob onto
     the lead as wpPropId, so the ONE link advertised as landing directly on the owner's Property
     Intel page shipped /property/70726f2d... on all 60 leads that had an id. Dead on every one.

  2. The board ROW built the clerk deep-links as esc(r.recqs) / esc(r.ocsqs) while the Call Sheet
     built the same two with encodeURIComponent(). recqs/ocsqs are RAW clerk tokens (records_liens
     unquotes the qs off the wire before caching it), so a '+' in a token arrives at the clerk as a
     space and the "direct to this owner's records" link opens an EMPTY search -- a placeholder.

  3. The worker tab's Lookup step read only TruePeopleSearch, so on a lead with no TPS URL it
     rendered disabled ("no people-search URL available"), and Call Mode's HIS FILE band shipped no
     Whitepages link at all.

HOW THIS TESTS. The JS is EXTRACTED BY NAME from tracker_template.html and call_mode.py and run
under node, so what is asserted is the code that ships, not a copy of it that can drift. Anything
this file cannot execute is asserted as an anchored source shape, and the anchor is named in the
failure so a rename fails loudly here instead of silently on the board.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse

import whitepages_lookup as W

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, 'tracker_template.html')
CM = os.path.join(HERE, 'call_mode.py')
IDS = os.path.join(HERE, 'wp_prop_ids.json')

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)) if extra else ''))
    if not ok:
        fails.append(name)


def extract(src, name):
    """`function NAME(...){...}` by balanced-brace scan -- same trick _cm_sourcecheck.js uses."""
    i = src.find('function ' + name + '(')
    if i < 0:
        raise SystemExit('ANCHOR GONE: function %s() is no longer in the source. '
                         'It was renamed or removed -- update this test with it.' % name)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise SystemExit('unbalanced braces extracting ' + name)


def node(js):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(js)
        p = f.name
    try:
        r = subprocess.run(['node', p], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SystemExit('node failed:\n' + (r.stderr or '')[:2000])
        return json.loads(r.stdout)
    finally:
        os.unlink(p)


TPL_SRC = open(TPL, encoding='utf-8').read()
CM_SRC = open(CM, encoding='utf-8').read()

# ---------------------------------------------------------------- 1. the hex-encoded property ids
ids = json.load(open(IDS, encoding='utf-8'))
rec('wp_prop_ids.json: every value decodes to a real pro- id',
    all(W.normalize_prop_id(v).startswith('pro-') for v in ids.values()),
    '%d entries' % len(ids))
rec('normalize_prop_id: hex -> id',
    W.normalize_prop_id('70726f2d52564d4b45706135587a4b') == 'pro-RVMKEpa5XzK')
rec('normalize_prop_id: idempotent (safe on an already-fixed map)',
    W.normalize_prop_id(W.normalize_prop_id('70726f2d52564d4b45706135587a4b')) == 'pro-RVMKEpa5XzK')
rec('normalize_prop_id: a plain id is returned untouched',
    W.normalize_prop_id('pro-RyMdk9ngN38') == 'pro-RyMdk9ngN38')
# Conservative decode: hex that is NOT a Whitepages id must survive, or a future id format that
# happens to be hex-shaped would be silently mangled into garbage.
rec('normalize_prop_id: hex that is not a WP id is left alone',
    W.normalize_prop_id('deadbeef') == 'deadbeef' and W.normalize_prop_id('abc') == 'abc')
rec('normalize_prop_id: empty / None stay empty',
    W.normalize_prop_id('') == '' and W.normalize_prop_id(None) == '')

# make_tracker must run the map THROUGH the normalizer -- the whole bug was that it did not.
FL_SRC = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
rec('foreclosure_leads: wpPropId goes through normalize_prop_id at bake',
    "_wp_norm_id(((_wp.get(_case) or {}).get('_prop_id') or _wp_ids.get(_case) or ''))" in FL_SRC,
    'anchor: the _pid assignment in the Whitepages merge')
rec('whitepages_lookup: the cache re-stamp normalizes too',
    'pid = normalize_prop_id(pid)' in open(os.path.join(HERE, 'whitepages_lookup.py'),
                                           encoding='utf-8').read())

# ---------------------------------------------------- 2. the board's Whitepages URL builders (JS)
WP_JS = '\n'.join(extract(TPL_SRC, n) for n in
                  ('_wpSlug', '_wpCity', '_wpCleanAddr', '_wpNameUrl', '_wpAddrUrl', '_wpPropUrl'))
# Fixture only. The name, case and address are INVENTED (same convention as build_preview.py) --
# no homeowner from the real book appears in a committed file.
LEAD = {'case': '2099-000000-CA-01', 'addr': '842 NW 9TH ST, MIAMI, FL 33136',
        'owners': 'ROBERT A JOHNSON; MARIA C JOHNSON', 'oname': 'ROBERT A JOHNSON', 'co': 0}
out = node(
    WP_JS
    + '\nfunction _ownerName(n, r){ return String(n || "").trim(); }\n'
    + 'const r = ' + json.dumps(LEAD) + ';\n'
    + 'const withId = Object.assign({}, r, {wpPropId: "pro-RVMKEpa5XzK"});\n'
    + 'const withHex = Object.assign({}, r, {wpPropId: "70726f2d52564d4b45706135587a4b"});\n'
    + 'console.log(JSON.stringify({addr:_wpAddrUrl(r), name:_wpNameUrl(r),'
      ' prop:_wpPropUrl(withId), propHex:_wpPropUrl(withHex), propNone:_wpPropUrl(r),'
      ' noAddr:_wpAddrUrl({addr:"MULTIPLE PARCELS - SEE CASE"})}));')

rec('_wpAddrUrl: reverse-ADDRESS page for the property',
    out['addr'] == 'https://www.whitepages.com/address/842-NW-9TH-ST/MIAMI-FL', out['addr'])
rec('_wpNameUrl: NAME page in the property city',
    out['name'] == 'https://www.whitepages.com/name/ROBERT-A-JOHNSON/MIAMI-FL', out['name'])
rec('_wpPropUrl: a normalized id gives the real /property/{id} page',
    out['prop'] == 'https://property.whitepages.com/property/pro-RVMKEpa5XzK#owner', out['prop'])
# THE REGRESSION GUARD. If the decode is ever dropped again, the board goes back to shipping this
# exact URL -- so pin it as WRONG rather than trusting a comment to hold the line.
rec('_wpPropUrl: an UNDECODED hex id would ship a dead page (why the bake must normalize)',
    out['propHex'] == 'https://property.whitepages.com/property/70726f2d52564d4b45706135587a4b#owner'
    and out['propHex'] != out['prop'], 'hex != real id')
rec('_wpPropUrl: no id -> the ?address= search deep-link, never an empty href',
    out['propNone'].startswith('https://property.whitepages.com/?address='), out['propNone'])
rec('_wpAddrUrl: an unparseable address yields NO link rather than a broken one',
    out['noAddr'] == '', repr(out['noAddr']))

# ------------------------------------------------- 3. the clerk deep-links on the row (recqs/ocsqs)
ROW_REC = 'href="https://onlineservices.miamidadeclerk.gov/officialrecords/SearchResults?qs=\'+esc(encodeURIComponent(r.recqs))+\''
ROW_OCS = 'href="https://www2.miamidadeclerk.gov/ocs/searchResults?qs=\'+esc(encodeURIComponent(r.ocsqs))+\''
rec('row Records link URL-encodes recqs', ROW_REC in TPL_SRC, 'anchor: the gEval Records push')
rec('row Cases link URL-encodes ocsqs', ROW_OCS in TPL_SRC, 'anchor: the gEval Cases push')
rec('row clerk deep-links no longer interpolate a RAW token',
    "esc(r.recqs)+" not in TPL_SRC and "esc(r.ocsqs)+" not in TPL_SRC)
rec('row clerk deep-links are gated to Miami-Dade (the only county that mints these tokens)',
    "r.recqs && _qsCty === 'MIAMI-DADE'" in TPL_SRC and "r.ocsqs && _qsCty === 'MIAMI-DADE'" in TPL_SRC)
# Why it mattered: a '+' in a raw token is a SPACE once the clerk parses the query string.
TOK = 'aB+cD/eF=gh'
rec('a clerk token survives encodeURIComponent round-trip (a raw one does not)',
    urllib.parse.parse_qs('qs=' + urllib.parse.quote(TOK, safe=''))['qs'][0] == TOK
    and urllib.parse.parse_qs('qs=' + TOK)['qs'][0] != TOK)
# The Call Sheet built it correctly all along -- make sure it still does, so the two cannot diverge.
rec('Call Sheet still URL-encodes recqs (the half that was always right)',
    "SearchResults?qs='+encodeURIComponent(r.recqs)" in TPL_SRC)

# --------------------------------------------------------- 4. the worker tab's Lookup step is live
rec('worker card Lookup falls back to Whitepages when there is no TPS URL',
    'wpUrl: r.peopleaddr || r.people || _wpAddrUrl(r) || _wpNameUrl(r)' in TPL_SRC,
    'anchor: _workerCard')

# --------------------------------------- 5. Call Mode HIS FILE carries the SAME Whitepages shapes
FILE_JS = extract(CM_SRC, 'fileLinks')
cm = node(FILE_JS
          + '\nconst r = {fo:"0131190150010", ct:"MI", a:"842 NW 9TH ST, MIAMI, FL 33136",'
            ' o:"ROBERT A JOHNSON", z:"33136", c:"2099-000000-CA-01"};\n'
          + 'console.log(JSON.stringify(fileLinks(r)));')
cm_map = {lab: url for lab, url in cm}
rec('Call Mode HIS FILE now carries a Whitepages reverse-address link',
    'Whitepages addr' in cm_map, ' / '.join(cm_map))
rec('Call Mode HIS FILE now carries a Whitepages name link', 'Whitepages' in cm_map)
# ONE URL SHAPE, not a third vocabulary: the phone's address link must equal the board's for the
# same lead. If someone edits one builder and not the other, this is what says so.
rec('Call Mode / board parity: identical reverse-address URL for the same lead',
    cm_map.get('Whitepages addr') == out['addr'],
    '%s vs %s' % (cm_map.get('Whitepages addr'), out['addr']))
rec('Call Mode / board parity: identical name URL for the same owner',
    cm_map.get('Whitepages') == out['name'],
    '%s vs %s' % (cm_map.get('Whitepages'), out['name']))
# A folio-less, address-less lead must not mint a /address//-FL stub.
cm2 = node(FILE_JS + '\nconsole.log(JSON.stringify(fileLinks({fo:"", ct:"BR", a:"", o:""})));')
rec('Call Mode: no address, no owner -> no half-built Whitepages URL',
    not any(l.startswith('Whitepages') for l, _ in cm2), json.dumps(cm2))
# Every URL the phone hands the operator must be absolute http(s) -- a relative one opens Call Mode
# itself, which is the "it just reloads the page" class of placeholder.
rec('Call Mode: every HIS FILE link is an absolute http(s) URL',
    all(re.match(r'^https?://', u) for _, u in cm), json.dumps([u for _, u in cm if not re.match(r'^https?://', u)]))

print('\nFAILED: ' + (' | '.join(fails) if fails else '(none)'))
sys.exit(1 if fails else 0)
