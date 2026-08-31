"""The morning digest must not lie on a quiet day, a busy day, or a broken one.

WHY THIS EXISTS
morning_digest.py makes three promises that all fail SILENTLY -- the output looks completely normal
in every failure mode, which is the whole reason they need a test rather than a glance:

  1. DELTAS ARE REAL.       A digest whose deltas collapse to zero reads as "nothing happened",
                            which is indistinguishable from a good quiet morning.
  2. SAME-DAY RERUN.        The single-slot version FAILED this. The first run of a new day wrote
                            today into the only baseline slot, so every later run compared today
                            against itself. Deltas went to zero on the busiest day of the week and
                            nothing about the output looked wrong. Two slots fixed it; this test is
                            what caught it and what keeps it caught.
  3. MISSING != ZERO.       The recurring failure in this repo is a stage that does not run and
                            reports success -- sale_history stamps vanished and the board published
                            0 active stays while the cache held 97. An absent input must ALERT, and
                            must never render as a 0 that looks like a measurement.

Run: python _digesttest.py
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'digest_state.json')
BAK = STATE + '.testbak'
R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    print(('  PASS ' if ok else '  FAIL ') + name + ((' | ' + detail) if detail else ''))


def run(*extra):
    p = subprocess.run([sys.executable, 'morning_digest.py', '--no-save'] + list(extra),
                       capture_output=True, text=True, cwd=HERE,
                       encoding='utf-8', errors='replace')
    return p.stdout or '', p.returncode


print('=== morning digest — deltas, rerun safety, missing inputs ===\n')
if os.path.exists(STATE):
    shutil.copy(STATE, BAK)
try:
    Y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    TODAY = dt.date.today().isoformat()

    # ---- 1. deltas against a real prior day -------------------------------------------------
    # BASELINE IS DERIVED FROM TODAY, NOT HARD-CODED. The first version pinned tierA:31 and
    # asserted a "no change" line appeared — which only held while the live board happened to
    # carry 31. It shifted to 30 and the suite went red with nothing actually broken, which is
    # the stale-fixture failure this repo has burned days on before. Building the baseline by
    # copying current metrics and bumping a chosen few makes both branches deterministic: the
    # copied keys MUST say "no change", the bumped ones MUST show a delta, whatever the data does.
    if os.path.exists(STATE):
        os.remove(STATE)
    cur = json.loads(run('--json')[0]).get('metrics') or {}
    BUMP = {'leads': -6, 'auc30': -4, 'bk': -3}            # these must show a delta
    base = dict(cur)
    for k, d in BUMP.items():
        if k in base:
            base[k] += d
    json.dump({'current': {'date': Y, 'metrics': base}}, open(STATE, 'w', encoding='utf-8'))
    a, rc = run()
    rec('a changed metric prints a signed delta', '(+' in a or '(-' in a)
    rec('an unchanged metric says "no change", never "(+0)"',
        '(no change)' in a and '(+0)' not in a)
    rec('the header names the date being compared against', Y in a)
    rec('exit code is 0', rc == 0)

    # ---- 2. THE SAME-DAY RERUN TRAP ---------------------------------------------------------
    # Assert on the BODY, not the header: the header is identical either way, so comparing whole
    # output would pass even with the deltas gone.
    b, _ = run()
    c, _ = run()
    body = lambda s: s.split('BOARD', 1)[-1]
    rec('rerun #2 is byte-identical to run #1', body(a) == body(b))
    rec('rerun #3 is byte-identical to run #1', body(a) == body(c))
    rec('deltas SURVIVE the rerun (did not collapse to no-change)',
        body(c).count('(no change)') == body(a).count('(no change)'))
    st = json.load(open(STATE, encoding='utf-8'))
    rec('yesterday is preserved in the previous slot',
        (st.get('previous') or {}).get('date') == Y, 'previous=%s' % (st.get('previous') or {}).get('date'))
    rec('today occupies the current slot',
        (st.get('current') or {}).get('date') == TODAY)

    # ---- 3. crossing midnight: today becomes the new baseline -------------------------------
    json.dump({'current': {'date': TODAY, 'metrics': {'leads': 999}},
               'previous': {'date': Y, 'metrics': {'leads': 1}}},
              open(STATE, 'w', encoding='utf-8'))
    d, _ = run()
    rec('same-day run diffs against PREVIOUS, not against today', Y in d and '999' not in d.split('BOARD')[0])

    # ---- 4. a missing input alerts and is omitted, never zeroed ------------------------------
    src = os.path.join(HERE, 'optouts.json')
    tmp = os.path.join(HERE, 'optouts.testbak')
    if os.path.exists(src):
        os.rename(src, tmp)
        try:
            e, rc2 = run()
            rec('missing input raises an ATTENTION block', 'ATTENTION' in e)
            rec('missing input is named in the alert', 'optouts.json is MISSING' in e)
            rec('missing metric prints "not available", not 0', 'not available' in e)
            rec('opt-out ledger is NOT rendered as a zero',
                'opt-out ledger                 0' not in e)
            rec('still exits 0 with an input missing', rc2 == 0)
        finally:
            os.rename(tmp, src)
    else:
        rec('missing-input path', True, 'optouts.json absent already — skipped')

    # ---- 5. it never raises, whatever the state ---------------------------------------------
    open(STATE, 'w', encoding='utf-8').write('{ this is not json')
    f, rc3 = run()
    rec('a corrupt state file does not crash the digest', rc3 == 0 and 'BOARD' in f)
    rec('corrupt state degrades to first-run, not to fake deltas',
        'first run' in f or '(no change)' not in f)

    # ---- 6. json mode stays machine-readable ------------------------------------------------
    os.path.exists(STATE) and os.remove(STATE)
    g, rc4 = run('--json')
    try:
        parsed = json.loads(g)
        rec('--json emits parseable JSON', isinstance(parsed, dict) and 'metrics' in parsed)
        rec('--json carries the alert list', 'alerts' in parsed)
    except Exception as ex:
        rec('--json emits parseable JSON', False, str(ex)[:50])
finally:
    for p in (STATE,):
        os.path.exists(p) and os.remove(p)
    if os.path.exists(BAK):
        shutil.move(BAK, STATE)

print('\n%d/%d passed' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
