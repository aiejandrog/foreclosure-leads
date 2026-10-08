"""DEALFLOW access codes — create / list / revoke, then rebuild + publish the gated site.

Access is per-person envelope encryption: every code in site.codes (gitignored, NEVER pushed) gets
its own wrapped key baked into docs/index.html. Adding a code = add a line -> rebuild -> push. The
code itself never leaves this machine; only the encrypted site is public.

Usage:
  python access_codes.py                 # create a code (prompts for the name)
  python access_codes.py "Maria Broker"  # create a code for that name
  python access_codes.py list            # show who currently has access
  python access_codes.py revoke "Maria"  # remove their access, republish
"""
import os, sys, secrets, json, subprocess, time
from board_url import BOARD_URL as URL   # one definition -- see board_url.py

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.join(HERE, 'site.codes')
LEADS = os.path.join(HERE, 'leads_final.json')
ALPH = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'   # no I O 0 1 L -> easy to read aloud / text
BAR = '=' * 48


def gen():
    return 'DEALFLOW-' + ''.join(secrets.choice(ALPH) for _ in range(8))


def read_lines():
    return open(CODES, encoding='utf-8').read().splitlines() if os.path.exists(CODES) else []


def parse(lines):
    out = []
    for ln in lines:
        t = ln.strip()
        if not t or t.startswith('#') or '=' not in t:
            continue
        lbl, rest = t.split('=', 1)
        code = rest.split('|', 1)[0].strip()
        phrase = rest.split('|', 1)[1].strip() if '|' in rest else ''
        out.append((lbl.strip(), code, phrase))
    return out


def publish_gates_pass():
    """healthcheck.py + publish_guard.py on the freshly built board, before ANY push -- the same two
    gates, in the same order and with the same exit-code rules, as refresh-dealflow.bat's [gate] step
    (CLAUDE.md "Publish gates -- never bypass"). healthcheck exit 2 = compliance hard block (lost
    s.362 stay flags, sources down); exit 1 = coverage advisory, publish_guard decides; publish_guard
    non-zero = a board materially poorer than live. A blocked rebuild leaves the live site on its last
    good build, so a revoked code keeps opening it until a publish passes -- say so, loudly."""
    h = subprocess.run([sys.executable, 'healthcheck.py'], cwd=HERE)
    if h.returncode >= 2:
        print('  !! GATE: healthcheck COMPLIANCE fail - publish SKIPPED (nothing pushed).')
    elif subprocess.run([sys.executable, 'publish_guard.py'], cwd=HERE).returncode != 0:
        print('  !! GATE: publish_guard BLOCKED the build - publish SKIPPED (nothing pushed).')
    else:
        if h.returncode == 1:
            print('  note: healthcheck coverage below floor - advisory, publish_guard passed.')
        return True
    print('  !! The code change is saved in site.codes but is NOT live: the old code set still opens')
    print('  !! the board until a publish passes both gates (fix the gate, then re-run, or wait for')
    print('  !! the next refresh-dealflow.bat).')
    return False


def rebuild_and_push(msg):
    """Rebuild docs/index.html (re-encrypts with the current code set) and make it LIVE.

    The live gate is the SEPARATE public repo dealflow-board (2026-09-17 split); this (engine) repo
    is private and does NOT serve the board. So an engine-repo push alone never changes what a
    homeowner/teammate loads -- publish_site.py is the step that mirrors docs/ to the live site, and
    its exit code is the real "is the code change live?" signal. Before 2026-10-08 this function
    pushed only to the engine repo and reported success, so every revoke/create was silently live
    only after the next refresh-dealflow.bat. Always mirror here; never report success on the engine
    push alone."""
    print('  rebuilding the encrypted site...')
    import foreclosure_leads as F
    F.make_tracker(json.load(open(LEADS, encoding='utf-8')))
    if not publish_gates_pass():
        return False
    subprocess.run(['git', 'add', 'docs/index.html'], cwd=HERE)
    subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=HERE)   # may be a no-op; the live mirror below is what counts
    # Engine-repo push: best effort only, for the publish_guard baseline. It can 408 on the large
    # board or be rejected non-fast-forward by an auto-committer; the nightly refresh reconciles it.
    # It is NOT the live gate, so its result never decides this function's return value.
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=HERE)
    # LIVE publish -- THIS is what makes the new code set open (and the revoked one stop opening) the
    # board people actually load. publish_site.py runs its own encrypted-payload + no-PII guards.
    print('  publishing to the LIVE site (publish_site.py -> dealflow-board)...')
    p = subprocess.run([sys.executable, 'publish_site.py'], cwd=HERE)
    return p.returncode == 0


def card(name, code, ok):
    print('\n' + BAR)
    print('  NEW DEALFLOW ACCESS CODE')
    print(BAR)
    print(f'  For:   {name}')
    print(f'  Code:  {code}')
    print(f'  Link:  {URL}')
    print(BAR)
    if ok:
        print('  Live in ~1-2 min. Text them the link + code.')
        print('  They enter the code once; their device stays unlocked.')
    else:
        print('  ! Saved locally but NOT published (a publish gate blocked it, or no internet; see above).')
        print('    Re-run this when online, or run refresh-dealflow.bat.')
    print('  Revoke anytime:  python access_codes.py revoke "%s"' % name)
    print(BAR + '\n')


def create(name):
    name = (name or '').strip() or input('Who is this access code for? (name) ').strip()
    if not name:
        print('No name given — cancelled.')
        return
    entries = parse(read_lines())
    if any(l.lower() == name.lower() for l, _, _ in entries):
        print(f'Note: "{name}" already has a code — adding a second, separate one.')
    used = {c for _, c, _ in entries}
    code = gen()
    while code in used:
        code = gen()
    txt = open(CODES, encoding='utf-8').read() if os.path.exists(CODES) else ''
    if txt and not txt.endswith('\n'):
        txt += '\n'
    open(CODES, 'w', encoding='utf-8').write(txt + f'{name} = {code}\n')
    ok = rebuild_and_push(f'access: add code for {name}')
    card(name, code, ok)


def show():
    e = parse(read_lines())
    if not e:
        print('No access codes yet. Create one:  python access_codes.py "Their Name"')
        return
    print('\n' + BAR)
    print('  WHO HAS ACCESS TO DEALFLOW')
    print(BAR)
    for l, c, p in e:
        print(f'  {l:<18} {c}' + ('   (+ secret phrase)' if p else ''))
    print(BAR)
    print(f'  {len(e)} code(s).  Live site: {URL}\n')


def revoke(name):
    name = (name or '').strip() or input('Revoke whose access? (name) ').strip()
    if not name:
        print('No name given — cancelled.')
        return
    lines = read_lines()
    keep, removed = [], []
    for ln in lines:
        t = ln.strip()
        if t and not t.startswith('#') and '=' in t and t.split('=', 1)[0].strip().lower() == name.lower():
            removed.append(t)
        else:
            keep.append(ln)
    if not removed:
        print(f'No code found for "{name}". Run  python access_codes.py list  to see names.')
        return
    open(CODES, 'w', encoding='utf-8').write('\n'.join(keep) + ('\n' if keep else ''))
    print('Removed: ' + '  |  '.join(removed))
    ok = rebuild_and_push(f'access: revoke {name}')
    if ok:
        print(f'\nAccess for "{name}" REVOKED. Their old code no longer opens the site.\n')
    else:
        print(f'\nAccess for "{name}" removed from site.codes ONLY. NOT LIVE: their old code still opens the')
        print('live board until a publish goes through (see the gate / publish messages above).\n')


if __name__ == '__main__':
    args = sys.argv[1:]
    head = args[0].lower() if args else ''
    if head in ('list', 'ls', 'show', 'who'):
        show()
    elif head in ('revoke', 'remove', 'rm', 'delete', 'kill'):
        revoke(' '.join(args[1:]))
    elif head in ('create', 'new', 'add'):
        create(' '.join(args[1:]))
    else:
        create(' '.join(args))   # bare `access_codes.py "Name"` = create
