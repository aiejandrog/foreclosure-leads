"""Gate regression suite — the access door, tested against real two-build fixtures.

WHY THIS EXISTS
On 2026-07-27 Alejandro was locked out of his own board minutes before a closing. Nothing was
broken: a code had been added for "Alex 2" at 05:32, and his browser was holding a copy of
index.html cached from before that code existed. Access codes are baked into the encrypted payload
at BUILD time, so a newly issued code cannot open an older page — and the gate responded by telling
him his code was wrong. That is the worst possible message, because it sends the person to go
re-verify a code that is perfectly correct.

This happens on every onboarding (Jose, Carlos, Jesse each get a code and hit it on first try), so
the gate now checks whether the page itself is stale before accusing anyone.

Run:  python _gatetest.py
Builds two fixtures that differ ONLY by one access code, restores site.codes byte-for-byte, and
asserts the three behaviours that matter. site.codes is never left modified — the restore is in a
finally block and the suite verifies the hash matches before reporting.
"""
import os, re, sys, json, shutil, hashlib, subprocess, threading, functools
import http.server
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.environ.get('TEMP', HERE)
TEST_CODE = 'ZZTESTHEALCODE01'
# PORT 0 = let the OS hand out a free ephemeral port, read back after bind.
# A FIXED port made this suite lie. HTTPServer sets allow_reuse_address, and on Windows that lets a
# second server bind a port a previous run still holds, so the browser could be answered by an
# EARLIER run's server still serving EARLIER fixtures. That produced "no message" / "0 navigations"
# on the three stale-page checks — a false alarm on the one guard that protects against a real
# lockout (2026-07-27, minutes before a closing). A test that cries wolf here is worse than no test.
PORT = 0

_r = []
def rec(name, ok, note=''):
    _r.append(ok)
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + note if note else ''))

def _sha(p): return hashlib.sha256(open(p, 'rb').read()).hexdigest()

def _abort(why):
    """A precondition this suite cannot test around. Say exactly what is wrong and STOP — never run
    the browser phase on fixtures already known to be wrong. Exit 2 (not 1) so a caller can tell
    "the gate is broken" from "this suite could not be run here"."""
    print('\nABORT: ' + why)
    raise SystemExit(2)


def _preflight():
    """Both inputs are gitignored and live only in the engine root, so this suite cannot run from a
    worktree or a fresh clone — and every way it fails there is misleading. Without leads_final.json
    make_tracker dies outright; without site.codes _load_codes() returns [] and the board builds
    PLAINTEXT with no gate at all. Say so once, here, instead of letting it surface as a gate bug."""
    missing = [n for n in ('site.codes', 'leads_final.json')
               if not os.path.exists(os.path.join(HERE, n))]
    if missing:
        _abort('missing ' + ' and '.join(missing) + ' in ' + HERE + '.\n'
               'Both are gitignored and exist only in the engine root, so the gate suite cannot run '
               'from a worktree or a fresh clone. Run it from the engine checkout.')


def _build(restoring=False):
    """Rebuild docs/index.html from leads_final.json — and PROVE a board was actually written.

    THE SECOND TIME THIS FILE CRIED WOLF. This ran with capture_output=True and neither the return
    code nor stderr was ever read. On 2026-09-18 it was run from a test worktree, where
    leads_final.json does not exist: make_tracker died on FileNotFoundError, docs/index.html was
    left exactly as git checked it out, and BOTH fixtures became byte-identical copies of the
    committed board. The suite said nothing about that and reported instead:

        build signature distinguishes two builds made in the same minute | 87f4cec09e16 vs 87f4cec09e16
        stale page tells the truth instead of blaming the code           | Wrong code…
        stale page reloads itself exactly once                           | 0 navigation(s)
        …then a 15s Playwright timeout waiting for the new code to unlock

    87f4cec09e16 is the signature of the board committed to git at 05:41 that morning — the suite
    was comparing the checked-in page against itself. The signature and the gate were both fine.
    Every one of those four reds was this function returning quietly.

    Same lesson as the fixed-port bug documented at PORT above: a test that cries wolf on the one
    guard protecting against a real lockout is worse than no test. So: fail loud, with the child's
    own stderr, and never hand a caller a fixture it did not get.

    restoring=True is the rebuild in make_fixtures' finally block, which exists to leave
    docs/index.html matching the RESTORED site.codes. It warns instead of aborting, for two
    reasons: an abort raised from a finally silently replaces the real diagnosis that sent us
    there, and the hazard it leaves behind needs saying out loud rather than as an exit code —
    a failed restore leaves the fixture board, with ZZTESTHEALCODE01 baked into it, sitting in
    docs/index.html where the next publish path would ship it.
    """
    out = os.path.join(HERE, 'docs', 'index.html')
    before = _sha(out) if os.path.exists(out) else None
    e = dict(os.environ); e['DEALFLOW_NO_DESKTOP'] = '1'
    # sys.executable, not 'python': the interpreter running this suite is the one that has
    # playwright and cryptography installed, and on Windows bare 'python' can resolve to the Store
    # shim or another venv — a build that fails for a reason that has nothing to do with the gate.
    r = subprocess.run([sys.executable, '-c',
        "import json, foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json', encoding='utf-8')))"],
        cwd=HERE, env=e, capture_output=True, text=True, errors='replace')
    if r.returncode != 0:
        tail = '\n'.join((r.stderr or '').strip().splitlines()[-12:]) or '(no stderr)'
        why = ('make_tracker exited %d — no board was built, so there are no fixtures to test.\n'
               '--- make_tracker stderr (last 12 lines) ---\n%s' % (r.returncode, tail))
        if restoring:
            print('\nWARNING: site.codes is restored, but the rebuild that follows it failed:\n'
                  + why + '\n>>> docs/index.html still holds the fixture board, which has '
                  + TEST_CODE + ' baked in. Rebuild before publishing.')
            return
        _abort(why)
    if not os.path.exists(out):
        _abort('make_tracker exited 0 but docs/index.html does not exist.')
    if restoring:
        return
    if before is not None and _sha(out) == before:
        # _preflight() guarantees site.codes, so every build takes the ENCRYPTED path, whose
        # envelope carries a fresh random master key, IV and per-code salt. Two real builds can
        # never be byte-identical. If they are, make_tracker returned without rewriting the file.
        _abort('make_tracker exited 0 and left docs/index.html byte-identical. No new board was '
               'written, so both fixtures would be the same page.')

def _sig(path):
    txt = open(path, encoding='utf-8', errors='replace').read(400)
    m = re.search(r'DEALFLOW-COVERAGE\s+(\{[^}]*\})', txt)
    return json.loads(m.group(1)).get('sig') if m else None

def make_fixtures():
    """pageA = today's access list. pageB = pageA plus one newly issued code."""
    _preflight()          # before we touch site.codes, not after
    codes = os.path.join(HERE, 'site.codes')
    bak = os.path.join(T, 'site.codes.gatetest.bak')
    before = _sha(codes)
    shutil.copy(codes, bak)
    try:
        _build(); shutil.copy(os.path.join(HERE, 'docs/index.html'), os.path.join(T, 'pageA.html'))
        with open(codes, 'a', encoding='utf-8') as f:
            f.write('\nTest Heal = ' + TEST_CODE + '\n')
        _build(); shutil.copy(os.path.join(HERE, 'docs/index.html'), os.path.join(T, 'pageB.html'))
    finally:
        shutil.copy(bak, codes); os.remove(bak); _build(restoring=True)
    rec('site.codes restored byte-identical after fixture build', _sha(codes) == before)
    return _sig(os.path.join(T, 'pageA.html')), _sig(os.path.join(T, 'pageB.html'))


class _H(SimpleHTTPRequestHandler):
    serve = 'pageA.html'
    def do_GET(self):
        p = os.path.join(T, _H.serve); body = open(p, 'rb').read()
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes=0-'):          # the gate reads only the leading marker
            e = int(rng.split('-')[1]); body = body[:e + 1]
            self.send_response(206)
            self.send_header('Content-Range', f'bytes 0-{e}/{os.path.getsize(p)}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


def main():
    print('building two fixtures that differ by exactly one access code…')
    sigA, sigB = make_fixtures()
    # A minute-resolution timestamp cannot tell these apart — that is why the marker carries a
    # content signature. If this ever collapses, the stale check silently stops working.
    rec('build signature distinguishes two builds made in the same minute',
        bool(sigA) and bool(sigB) and sigA != sigB, f'{sigA} vs {sigB}')
    # THE FIXTURES ARE A PRECONDITION, NOT A RESULT. Every check below asks what the gate does when
    # a tab holds an OLDER build than the server serves. If pageA and pageB are the same build there
    # is no such thing to observe, and the stale-page checks report the fixture bug wearing the
    # gate's name — which is exactly what happened on 2026-09-18 (see _build). Stop here instead.
    if not (sigA and sigB):
        _abort('a fixture has no DEALFLOW-COVERAGE signature on its first line. docs/index.html was '
               'built by an older make_tracker, or is not a generated board at all.')
    if sigA == sigB:
        _abort('the two fixtures are the same build (both sig %s), so there is no stale page to '
               'test. The added access code did not change the payload, or docs/index.html was not '
               'rebuilt between the two copies. Nothing below this line would be about the gate.'
               % sigA)

    srv = ThreadingHTTPServer(('127.0.0.1', PORT), _H)
    port = srv.server_address[1]          # the port actually bound, never a guess
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)

            # ---- the real lockout: page cached before the code was issued
            _H.serve = 'pageA.html'
            pg = b.new_context().new_page(); navs = []; seen = []
            pg.on('framenavigated', lambda f: navs.append(f.url))
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            pg.wait_for_selector('#gatepw')
            before = pg.evaluate('() => BUILT')
            pg.fill('#gatepw', TEST_CODE)
            _H.serve = 'pageB.html'                      # server moved on; this tab has not
            pg.click('#gatego')
            for _ in range(40):                          # poll fast: the message is transient
                try:
                    t = pg.text_content('#gateerr') or ''
                    if t and (not seen or seen[-1] != t): seen.append(t)
                except Exception: pass
                pg.wait_for_timeout(50)
            pg.wait_for_timeout(1500)
            after = pg.evaluate('() => BUILT')
            said_stale = any('out of date' in s for s in seen)
            blamed     = any('Wrong code' in s for s in seen)
            rec('stale page tells the truth instead of blaming the code', said_stale and not blamed,
                (seen[0][:58] if seen else 'no message'))
            rec('stale page reloads itself exactly once', len(navs) - 1 == 1, f'{len(navs)-1} navigation(s)')
            rec('page actually advanced to the newer build', before == sigA and after == sigB)
            pg.fill('#gatepw', TEST_CODE); pg.click('#gatego')
            pg.wait_for_function("document.getElementById('gate') && getComputedStyle(document.getElementById('gate')).display==='none'", timeout=15000)
            rec('newly issued code then unlocks the board', pg.evaluate('() => DATA.length') > 0,
                f"{pg.evaluate('() => DATA.length')} leads")
            pg.context.close()

            # ---- a genuinely wrong code on a CURRENT page must still be refused
            _H.serve = 'pageB.html'
            pg = b.new_context().new_page(); navs = []
            pg.on('framenavigated', lambda f: navs.append(f.url))
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            pg.wait_for_selector('#gatepw'); pg.fill('#gatepw', 'NOT-A-REAL-CODE-X'); pg.click('#gatego')
            pg.wait_for_timeout(3000)
            msg = pg.text_content('#gateerr') or ''
            rec('wrong code on a current page is refused, not "healed"',
                'Wrong code' in msg and len(navs) - 1 == 0, f'{len(navs)-1} navigation(s)')
            rec('wrong code never unlocks', pg.evaluate("() => typeof DATA==='undefined' || !DATA || !DATA.length"))
            pg.context.close()

            # ---- stale page + wrong code: heal once, then stop. Never trap someone in a refresh loop
            # minutes before an auction.
            _H.serve = 'pageA.html'
            pg = b.new_context().new_page()
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            pg.wait_for_selector('#gatepw')
            _H.serve = 'pageB.html'
            navs = []; pg.on('framenavigated', lambda f: navs.append(f.url))
            for _ in range(3):
                try:
                    pg.wait_for_selector('#gatepw', timeout=8000)
                    pg.fill('#gatepw', 'NOT-A-REAL-CODE-X'); pg.click('#gatego'); pg.wait_for_timeout(2200)
                except Exception: pass
            rec('repeated failure on a stale page cannot become a refresh loop', len(navs) == 1,
                f'{len(navs)} reload(s)')
            rec('after healing once it gives the honest error',
                'Wrong code' in (pg.text_content('#gateerr') or ''))

            # ---- the UNLOCKED path. The gate heal only runs on a failed unlock — but a remembered
            # device never sees the gate, so it could sit on a cached build for weeks and quietly
            # miss every fix ("the Seen filter doesn't work" = the phone running last week's page).
            import json as _json
            codeA = TEST_CODE if False else None
            base_code = open(os.path.join(HERE, 'site.codes'), encoding='utf-8').readline()
            # use the first real code for the remembered-device secret
            import foreclosure_leads as _F
            real = _F._load_codes()[0][1].partition(chr(31))[0]
            seed = f"try{{localStorage.setItem('fcPw', {_json.dumps(real)});}}catch(e){{}}"
            # stale + untouched -> silent swap
            _H.serve = 'pageA.html'
            ctx = b.new_context(); pg = ctx.new_page(); navs = []
            pg.add_init_script(seed)
            pg.on('framenavigated', lambda f: navs.append(f.url))
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            _H.serve = 'pageB.html'
            pg.wait_for_timeout(9000)
            rec('remembered device on a stale page silently swaps to the new build',
                len(navs) - 1 == 1 and pg.evaluate("() => BUILT") == sigB
                and pg.evaluate("() => typeof DATA!=='undefined' && DATA && DATA.length > 0"),
                f'{len(navs)-1} nav(s), sig {pg.evaluate("() => BUILT")}')
            ctx.close()
            # stale + already in use -> pill, never a forced reload mid-work
            _H.serve = 'pageA.html'
            ctx = b.new_context(); pg = ctx.new_page(); navs = []
            pg.add_init_script(seed)
            pg.on('framenavigated', lambda f: navs.append(f.url))
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            pg.mouse.click(400, 300)
            _H.serve = 'pageB.html'
            pg.wait_for_timeout(9000)
            pill = pg.query_selector('#freshpill')
            rec('in-use stale page gets a tap-to-load pill, never a forced reload',
                bool(pill) and len(navs) - 1 == 0, '')
            if pill:
                pill.click(); pg.wait_for_timeout(2500)
                rec('tapping the pill loads the newest build', pg.evaluate("() => BUILT") == sigB, '')
            ctx.close()
            # fresh page -> nothing
            _H.serve = 'pageB.html'
            ctx = b.new_context(); pg = ctx.new_page(); navs = []
            pg.add_init_script(seed)
            pg.on('framenavigated', lambda f: navs.append(f.url))
            pg.goto(f'http://127.0.0.1:{port}/index.html', wait_until='domcontentloaded')
            pg.wait_for_timeout(8000)
            rec('fresh page: no pill, no reload',
                not pg.query_selector('#freshpill') and len(navs) - 1 == 0, '')
            ctx.close()
            b.close()
    finally:
        srv.shutdown()

    print(f'==== {sum(_r)}/{len(_r)} gate checks passed ====')
    raise SystemExit(0 if all(_r) else 1)

if __name__ == '__main__':
    main()
