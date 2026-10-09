"""access_codes.rebuild_and_push runs healthcheck + publish_guard before any push (CLAUDE.md
"Publish gates -- never bypass"). No network, no git, no real board: subprocess and make_tracker
are stubbed. Run: python test_access_codes_gate.py  (exit 0 = pass)."""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import access_codes as AC

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  -- %r' % (detail,)))
    if not cond:
        FAILS.append(name)


def run_with(rc_health, rc_guard, rc_site=0):
    calls = []

    def fake_run(cmd, cwd=None, **kw):
        calls.append(cmd)
        script = cmd[1] if cmd and cmd[0] == sys.executable else ' '.join(cmd[:2])
        rc = {'healthcheck.py': rc_health, 'publish_guard.py': rc_guard, 'publish_site.py': rc_site}.get(script, 0)
        return types.SimpleNamespace(returncode=rc)

    real_run, real_open = AC.subprocess.run, AC.json.load
    AC.subprocess.run = fake_run
    sys.modules['foreclosure_leads'] = types.SimpleNamespace(make_tracker=lambda rows: None)
    AC.json.load = lambda f: []
    real_leads, AC.LEADS = AC.LEADS, os.devnull
    try:
        ok = AC.rebuild_and_push('test')
    finally:
        AC.subprocess.run, AC.json.load, AC.LEADS = real_run, real_open, real_leads
        sys.modules.pop('foreclosure_leads', None)
    return ok, calls


def pushed(calls):
    return [c for c in calls if c[:2] == ['git', 'push'] or (len(c) > 1 and c[1] == 'publish_site.py')
            or c[:2] == ['git', 'commit'] or c[:2] == ['git', 'add']]


ok, calls = run_with(0, 0)
names = [c[1] if c[0] == sys.executable else ' '.join(c[:2]) for c in calls]
check('both gates pass -> published, gates ran before any git add/commit/push/publish_site',
      ok and names.index('healthcheck.py') < names.index('publish_guard.py') < names.index('git add')
      < names.index('git push') < names.index('publish_site.py'), names)
ok, calls = run_with(2, 0)
check('healthcheck exit 2 (compliance) -> nothing added, committed, pushed or mirrored; returns False',
      ok is False and not pushed(calls) and not any(len(c) > 1 and c[1] == 'publish_guard.py' for c in calls), calls)
ok, calls = run_with(1, 0)
check('healthcheck exit 1 (advisory) + guard pass -> published', ok and pushed(calls), calls)
ok, calls = run_with(1, 2)
check('publish_guard blocks -> nothing pushed or mirrored; returns False', ok is False and not pushed(calls), calls)
ok, calls = run_with(0, 0, rc_site=1)
check('gates pass but the mirror fails -> returns False', ok is False, calls)

print()
print('==== %d FAIL(S) ====' % len(FAILS) if FAILS else '==== all access-code gate checks passed ====')
sys.exit(1 if FAILS else 0)
