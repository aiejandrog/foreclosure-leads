# READ-ONLY. Diligence gate before/after on the LIVE board twin. Counts only: no names, numbers, addresses.
# python gate_compare.py   (compares diligence_* at GATE_BASE vs GATE_BRANCH, default pre-#389 main vs origin/main)
# Writes nothing, spends nothing, does not switch the repo's branch. Run from the repo folder.
import json, os, re, sys, io, contextlib, collections, subprocess, tempfile
REPO = os.getcwd(); BR = os.environ.get('GATE_BRANCH') or 'origin/main'
BASE = os.environ.get('GATE_BASE') or 'e8f169b'   # main just before #389
sys.path.insert(0, REPO)
import paths as P
mode = sys.argv[1] if len(sys.argv) > 1 else 'driver'
def jl(p, d):
    try: return json.load(open(p, encoding='utf-8')) or d
    except Exception: return d
if mode == 'driver':
    dirs = {}
    for m, ref in (('before', BASE), ('after', BR)):
        dirs[m] = tempfile.mkdtemp()
        for f in ('diligence_flags.py', 'diligence_gate.py'):
            src = subprocess.run(['git', '--no-pager', 'show', ref + ':' + f], capture_output=True, text=True, encoding='utf-8').stdout
            if not src.strip(): sys.exit('could not read %s from %s - did git fetch run?' % (f, ref))
            open(os.path.join(dirs[m], f), 'w', encoding='utf-8').write(src)
    res = {}
    for m in ('before', 'after'):
        env = dict(os.environ, GATE_TMP=dirs[m])
        out = subprocess.run([sys.executable, __file__ if os.path.exists(__file__) else sys.argv[0], m], capture_output=True, text=True, encoding='utf-8', env=env)
        if out.returncode: print(out.stdout[-1500:], out.stderr[-1500:]); sys.exit('%s run failed' % m)
        res[m] = json.loads(out.stdout.strip().splitlines()[-1])
    b, a = res['before'], res['after']
    print('board twin rows:', b['rows'], '| tax-deed rows:', b['td'], b['td_by'])
    print('\n%-26s %7s %7s %7s' % ('', 'before', 'after', 'delta'))
    for k in ('held', 'warned', 'taxdeed_uncounted', 'qualifying', 'dialable_rows', 'find_a_number'):
        print('%-26s %7s %7s %+7d' % (k, b[k], a[k], a[k] - b[k]))
    for s in b['seats']: print('%-26s %7s %7s %+7d' % ('seat ' + s, b['seats'][s], a['seats'][s], a['seats'][s] - b['seats'][s]))
    print('\nheld by code')
    codes = sorted(set(b['codes']) | set(a['codes']), key=lambda c: -b['codes'].get(c, 0))
    up = []
    for c in codes:
        x, y = b['codes'].get(c, 0), a['codes'].get(c, 0)
        print('  %-24s %6d %6d %+6d%s' % (c, x, y, y - x, '   <-- UP' if y > x else ''))
        if y > x: up.append(c)
    print('\nrows held after that were NOT held before (must be 0):', len(set(a['held_idx']) - set(b['held_idx'])))
    print('rows released by this change:', len(set(b['held_idx']) - set(a['held_idx'])))
    print('one-parcel rows given their parcel address (simulated, lands on next refresh):', a['promoted'])
    print('miami rows whose last sale is a certificate of title (simulated lsi):', a['cot'])
    print('\nNUMBERLESS leads that pass every gate (Find a number list), skiptrace_results.json coverage:')
    for k, v in sorted(a['numberless_cache'].items()): print('  %-34s %d' % (k, v))
    print('\nUP codes are recodes when "rows newly held" is 0 (a row lost its first code and shows its next).' if up else '')
    sys.exit(0)
# ---------- one side: 'before' = code on disk (main), 'after' = branch diligence_* + simulated inputs ----------
sys.path.insert(0, os.environ['GATE_TMP'])
import diligence_flags as DF, diligence_gate as DG
DG.HERE = REPO; DG._OWN = None
with contextlib.redirect_stdout(io.StringIO()):
    import call_mode as CM
h = open(P.TWIN, encoding='utf-8').read()
RAW, _ = json.JSONDecoder().raw_decode(h, h.index('RAW = ') + 6)
promoted = cot = 0
if mode == 'after':
    la = jl('lp_addresses.json', {})
    for d in RAW:                      # change 2 as lp_leads will bake it on the next refresh
        if d.get('mailTrace') and not (d.get('addr') or '').strip():
            a = la.get(d.get('case') or '') or {}
            if str(a.get('confidence')) == 'medium' and str(a.get('addr') or '').strip():
                d['addr'] = str(a['addr']).strip(); d['addrConf'] = 'medium'; promoted += 1
    lsi = {}
    def _newest(refs):
        best, key = '', (0, 0, 0)
        for x in refs if isinstance(refs, list) else []:
            m = re.match(r'\s*(\d{1,2})/(\d{1,2})/(\d{4})', str((x or {}).get('d') or '')) if isinstance(x, dict) else None
            k = (int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
            if k and k >= key: best, key = str(x.get('i') or ''), k
        return best
    for r in jl('leads_final.json', []):  # change 4 input (lsi) as the next refresh will bake it
        if isinstance(r, dict) and r.get('sale_refs'): lsi[r.get('Case #', '')] = _newest(r['sale_refs'])
    for d in RAW:
        if d.get('case') in lsi and lsi[d['case']]:
            d['lsi'] = lsi[d['case']]
            if DF.last_sale_is_cert_of_title(d): cot += 1
oo = jl('optouts.json', {}); oo = oo.get('notes', oo) if isinstance(oo, dict) else {}
dd = {c: 1 for c, n in jl('deads.json', {}).items() if isinstance(n, dict) and str(n.get('status') or '').upper() in ('DEAD', 'CLOSED', 'LOST - SOLD AT AUCTION')}
t = DG.Tally(); held_cases = []
for i, d in enumerate(RAW):
    if t.check(d)['hold']: held_cases.append(i)
td = [d for d in RAW if DF.is_tax_deed_row(d)]
lk = []
with contextlib.redirect_stdout(io.StringIO()):
    rows, total = CM.call_rows(RAW, optouts=oo, deads=dd, cap=10 ** 6, lookup=lk)
st = jl('skiptrace_results.json', {})
nc = collections.Counter()
for x in lk:
    e = st.get(x.get('c')) if isinstance(st, dict) else None
    nc['no cache entry (needs a paid lookup)' if not e else ('CACHED HIT, not on board (bug?)' if e.get('phones') else 'cached miss (Tracerfy found nothing)')] += 1
print(json.dumps({'rows': len(RAW), 'td': len(td), 'td_by': dict(collections.Counter(d.get('county') or '?' for d in td)),
                  'held': t.n_held, 'warned': t.warned, 'taxdeed_uncounted': getattr(t, 'taxdeed', 0), 'codes': t.held,
                  'qualifying': total, 'dialable_rows': len(rows), 'find_a_number': len(lk),
                  'seats': {s[2]: len(CM.seat_rows(rows, s[0], s[1])) for s in CM.CALL_SEATS},
                  'held_idx': held_cases, 'promoted': promoted, 'cot': cot, 'numberless_cache': dict(nc)}))
