"""_netreadytest — guard for net_ready.py. Runs on ANY checkout: no network, no board, no keys.

It monkey-patches socket, so nothing here ever touches a real host. The point is to prove the three
behaviours the 09-14/16/17 outage needed and did not have:

  1. a DNS failure is REFUSED, not shrugged off (the whole bug)
  2. a DNS failure that clears on a later attempt is WAITED OUT (the whole point of retrying)
  3. an advisory host being down never blocks the run (publish_guard/healthcheck separation)

Run:  python _netreadytest.py
"""
import socket
import sys

import net_ready


class _Fake:
    """Scripted socket behaviour per host. `fails` counts how many attempts still fail before the
    host starts answering, so a value of 1 models 'came up on the second try'."""

    def __init__(self, fails):
        self.fails = dict(fails)

    def getaddrinfo(self, host, port, *a, **k):
        if self.fails.get(host, 0) > 0:
            self.fails[host] -= 1
            raise socket.gaierror(11001, 'getaddrinfo failed')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]

    def create_connection(self, addr, timeout=None):
        class _S:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *exc): return False
        return _S()


def _install(fake, no_sleep=True):
    net_ready.socket.getaddrinfo = fake.getaddrinfo
    net_ready.socket.create_connection = fake.create_connection
    if no_sleep:
        net_ready.time.sleep = lambda s: None


FAILS = []


def check(name, got, want):
    ok = got == want
    print(f'  {"pass" if ok else "FAIL"}  {name}' + ('' if ok else f'   got {got!r}, want {want!r}'))
    if not ok:
        FAILS.append(name)


def main():
    real_gai, real_conn = socket.getaddrinfo, socket.create_connection
    try:
        print()

        # 1. The outage itself: nothing resolves, ever. Must refuse.
        _install(_Fake({h: 99 for h, _ in net_ready.REQUIRED + net_ready.ADVISORY}))
        ok, req, _ = net_ready.wait_for_network()
        check('total DNS failure refuses the run', ok, False)
        check('  and names every required host as down',
              [h for h, host_ok, _ in req if not host_ok],
              [h for h, _ in net_ready.REQUIRED])

        # 2. Wi-Fi arriving late — the case the retry exists for. Fails twice, then answers.
        _install(_Fake({h: 2 for h, _ in net_ready.REQUIRED}))
        ok, _, _ = net_ready.wait_for_network()
        check('a host that comes up on attempt 3 is waited out', ok, True)

        # 3. --quick takes one pass, so a manual run is not held for four minutes.
        _install(_Fake({h: 2 for h, _ in net_ready.REQUIRED}))
        ok, _, _ = net_ready.wait_for_network(quick=True)
        check('--quick does not wait (same host, one pass, refused)', ok, False)

        # 4. Advisory down + required up must PASS. Duplicating healthcheck's upstream rule here
        #    would mean two files deciding the same thing, which is how one gets fixed and both
        #    are believed fixed.
        _install(_Fake({net_ready.ADVISORY[0][0]: 99}))
        ok, _, adv = net_ready.wait_for_network()
        check('an advisory source being down does NOT block', ok, True)
        check('  but is still reported', [h for h, host_ok, _ in adv if not host_ok],
              [net_ready.ADVISORY[0][0]])

        # 5. probe() must distinguish "did not resolve" from "resolved, no answer" — that
        #    distinction is what identified this outage as DNS rather than a blocked site.
        _install(_Fake({}))
        net_ready.socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(
            OSError('connection refused'))
        _ok, detail = net_ready.probe('github.com', 443)
        check('resolved-but-refused is not reported as a DNS failure',
              ('DNS' in detail, 'no answer' in detail), (False, True))

        print()
        print(f'{6 - len(FAILS)} pass / {len(FAILS)} fail')
        return 1 if FAILS else 0
    finally:
        net_ready.socket.getaddrinfo, net_ready.socket.create_connection = real_gai, real_conn


if __name__ == '__main__':
    sys.exit(main())
