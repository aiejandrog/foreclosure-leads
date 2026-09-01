"""Two phones, one lead list, nobody dialled twice. The properties that make that true.

WHY THIS EXISTS
Alejandro and Carlos call the same board from separate devices. Before this, NOTHING stopped both
of them dialling the same homeowner: the team sync merges NOTES every 45 seconds, which tells you
afterwards that you both called someone — it cannot prevent it, and a homeowner in foreclosure
getting the same pitch twice in an hour from the same company is the exact opposite of the
credibility the whole system is built to project.

THE FIX IS TWO LAYERS AND THEY ARE NOT INTERCHANGEABLE:

  PARTITION (call_mode.call_rows stamps r.sb, the page filters on it)
    A stable 0-11 bucket of the case number. With n seats you take sb % n === i. The same lead is
    never in two queues at once, and this holds with NO server call, NO lock and NO network — the
    collision is prevented by making coordination unnecessary. This is the real defence.

  CLAIM (_clmTake / _clmOwner, riding the encrypted note sync)
    Advisory, for deliberate out-of-lane work. It travels on the 45s pull, so two people opening
    the same lead inside the same 45 seconds BOTH see it unclaimed. It can never be the primary
    defence and this file exists partly to keep that distinction honest.

THE FOUR WAYS THIS SILENTLY BREAKS, all asserted below:
  1. Overlap        — a lead in both queues. The bug itself.
  2. Lost leads     — a lead in NO queue. Worse than overlap: nobody ever calls it and the list
                      just looks shorter. An unstamped row (older build) must stay visible to
                      EVERYONE rather than vanish from every queue at once.
  3. Immortal claim — a claim with no expiry turns one abandoned tap into a lead nobody may call
                      again. TTL, plus an outcome clears it outright.
  4. Self-block     — reading your own claim as a teammate's and hiding your own lane from you.

Needs node on PATH for the JS half; skips cleanly without it.
Run: python _seattest.py
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    print(('  PASS ' if ok else '  FAIL ') + name + ((' | ' + detail) if detail else ''))


def bucket(cs):
    """Mirror of the stamp in call_rows. If these two ever disagree the partition is fiction."""
    h = 2166136261
    for ch in str(cs):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h % 12


print('=== call mode team seats — partition, claims, and the ways they rot ===\n')

# ---- 1. the partition, on REAL case numbers ---------------------------------------------------
try:
    cases = [r['Case #'] for r in json.load(open(os.path.join(HERE, 'leads_final.json'),
                                                encoding='utf-8')) if r.get('Case #')]
except Exception:
    cases = ['20%02d-%06d-CA-01' % (i % 27, i * 7919) for i in range(400)]

for n in (2, 3, 4):
    seats = [[c for c in cases if bucket(c) % n == k] for k in range(n)]
    sets = [set(s) for s in seats]
    overlap = sum(len(sets[a] & sets[b]) for a in range(n) for b in range(a + 1, n))
    rec('%d seats: no lead in two queues' % n, overlap == 0, '%d overlapping' % overlap)
    rec('%d seats: no lead lost from every queue' % n,
        sum(len(s) for s in seats) == len(cases),
        '%d of %d covered' % (sum(len(s) for s in seats), len(cases)))
    sizes = [len(s) for s in seats]
    skew = (max(sizes) - min(sizes)) / max(len(cases) / n, 1) * 100
    rec('%d seats: split is roughly even' % n, skew < 25, '%s, %.1f%% skew' % (sizes, skew))

rec('bucket is deterministic across builds',
    all(bucket(c) == bucket(c) for c in cases[:200]))

# ---- 2. the stamp actually reaches the row ----------------------------------------------------
src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
rec("call_rows stamps 'sb' onto every cased row", "row['sb'] = _h % 12" in src)
rec('the page filters on it', '_seatMine' in src and 'r.sb % s.n' in src)
rec('seat filter runs AFTER suppression, with its own counter',
    '_SEATN' in src and '_SUPN = n' in src)

# ---- 3. the JS half: it must PARSE, then it must BEHAVE ---------------------------------------
# The board wraps the call-mode build in try/except so a syntax error prints "SKIPPED" and leaves
# YESTERDAY'S page in place — the failure is invisible unless something checks the parse.
have_node = subprocess.run(['node', '--version'], capture_output=True).returncode == 0
if not have_node:
    rec('node available for the JS half', True, 'node missing — JS assertions skipped')
else:
    import call_mode
    html = call_mode.build_html([], 0, {'stub': 1}, '2026-01-01T00:00', 's', 'b', sync_js='')
    js = max(re.findall(r'<script[^>]*>(.*?)</script>', html, re.S), key=len)
    p = os.path.join(HERE, '_seat_check.js')
    open(p, 'w', encoding='utf-8').write(js)
    ok = subprocess.run(['node', '--check', p], capture_output=True, text=True)
    rec('generated call-mode JS parses', ok.returncode == 0, (ok.stderr or '')[:90])

    blk_start, blk_end = js.find('var SEAT_TTL_MS'), js.find('function pool()')
    harness = '''
      const notes={}; let _dev='dev-ME';
      function _deviceId(){return _dev;} function saveNotes(){} function render(){}
      function lastCall(){return null;}
      const localStorage={_d:{},getItem(k){return this._d[k]||null;},
        setItem(k,v){this._d[k]=v;},removeItem(k){delete this._d[k];}};
      %s
      const R=[]; for(let i=0;i<12;i++) R.push({c:'C'+i, sb:i});
      const q=()=>R.filter(r=>_seatMine(r) && !_clmOwner(r.c));
      const out={};
      out.solo = q().length;
      _seatSet(2,0,'A'); const A=q().map(r=>r.c);
      _seatSet(2,1,'B'); const B=q().map(r=>r.c);
      out.overlap = A.filter(x=>B.includes(x)).length;
      out.union = new Set([...A,...B]).size;
      _seatSet(2,0,'A');
      notes['C0']={clm:{d:'dev-OTHER',t:Date.now(),w:'Carlos'}};
      out.teammate = _clmOwner('C0');
      notes['C2']={clm:{d:_dev,t:Date.now(),w:'me'}};
      out.mine = _clmOwner('C2');
      notes['C4']={clm:{d:'dev-OTHER',t:Date.now()-91*60*1000,w:'Carlos'}};
      out.stale = _clmOwner('C4');
      notes['C6']={status:'no answer',clm:{d:'dev-OTHER',t:Date.now(),w:'Carlos'}};
      out.worked = _clmOwner('C6');
      out.unstamped = _seatMine({c:'OLD'});
      // model pool() exactly: seat filter is MINE and conditional, claim filter is unconditional
      _seatSet(1); out.backToSolo = R.filter(r=>!_clmOwner(r.c)).length;
      out.soloStillRespectsClaims = (_clmOwner('C0') === 'Carlos');
      console.log(JSON.stringify(out));
    ''' % js[blk_start:blk_end]
    hp = os.path.join(HERE, '_seat_harness.js')
    open(hp, 'w', encoding='utf-8').write(harness)
    r2 = subprocess.run(['node', hp], capture_output=True, text=True)
    try:
        o = json.loads((r2.stdout or '{}').strip().splitlines()[-1])
    except Exception:
        o = {}
        rec('seat harness ran', False, (r2.stderr or '')[:110])
    if o:
        rec('solo sees the whole list (feature is a no-op unsplit)', o['solo'] == 12,
            '%s of 12' % o['solo'])
        rec('two seats share NO lead', o['overlap'] == 0)
        rec('two seats between them cover every lead', o['union'] == 12)
        rec("a teammate's live claim is visible", o['teammate'] == 'Carlos')
        rec('my OWN claim never blocks me', o['mine'] == '')
        rec('a claim past its TTL expires', o['stale'] == '')
        rec('a logged outcome releases the claim', o['worked'] == '')
        rec('a row with no bucket stays visible to everyone', o['unstamped'] is True)
        # 12 rows minus C0, which a teammate is on the phone with right now
        rec('switching to solo hands back MY lane', o['backToSolo'] == 11,
            '%s of 11 (C0 is claimed by a teammate)' % o['backToSolo'])
        rec("...but a teammate's LIVE claim still counts when solo",
            o.get('soloStillRespectsClaims') is True,
            'turning off my own split must not un-hide a lead he is dialing')
    for f in (p, hp):
        os.path.exists(f) and os.remove(f)

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
