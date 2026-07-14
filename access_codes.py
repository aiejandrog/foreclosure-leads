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

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.join(HERE, 'site.codes')
LEADS = os.path.join(HERE, 'leads_final.json')
URL = 'https://aiejandrog.github.io/foreclosure-leads/'
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


def rebuild_and_push(msg):
    """Rebuild docs/index.html (re-encrypts with the current code set) and push it live."""
    print('  rebuilding the encrypted site...')
    import foreclosure_leads as F
    F.make_tracker(json.load(open(LEADS, encoding='utf-8')))
    subprocess.run(['git', 'add', 'docs/index.html'], cwd=HERE)
    c = subprocess.run(['git', 'commit', '-q', '-m', msg], cwd=HERE)
    if c.returncode != 0:
        print('  (nothing changed to publish)')
        return True
    print('  publishing...')
    for attempt in (1, 2):
        p = subprocess.run(['git', 'push', 'origin', 'main'], cwd=HERE)
        if p.returncode == 0:
            return True
        if attempt == 1:
            time.sleep(6)
    return False


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
        print('  ! Saved locally but NOT published (no internet?).')
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
    print(f'\nAccess for "{name}" REVOKED' + ('' if ok else ' locally (publish pending — run when online)') +
          '. Their old code no longer opens the site.\n')


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
