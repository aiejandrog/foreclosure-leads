#!/usr/bin/env python
"""_bridgeauthtest.py -- per-device keys on the send bridge (bridge_auth.py + send_server.py).

Run: python _bridgeauthtest.py   (exit 0 = pass). Synthetic keys file in a temp dir; the bridge is
spawned on spare ports and only /health, /notes (refused) and /text (refused) are touched. Nothing
is sent and no real ledger is written.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix='bridgeauth')
KEYS = os.path.join(TMP, 'bridge_keys.json')
os.environ['DEALFLOW_BRIDGE_KEYS'] = KEYS
import bridge_auth as BA  # noqa: E402

fails = []


def chk(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


# ---- unit: keys ----
chk('no file: nothing verifies', BA.verify('x') is None and BA.active_count() == 0)
k1 = BA.add('desktop')
k2 = BA.add('phone')
chk('keys are long and distinct', len(k1) >= 40 and k1 != k2)
raw = open(KEYS, encoding='utf-8').read()
chk('the key itself is never stored', k1 not in raw and k2 not in raw and 'hash' in raw)
chk('valid key names its device', BA.verify(k1) == 'desktop' and BA.verify(k2) == 'phone')
chk('wrong / empty / huge key refused', BA.verify('nope') is None and BA.verify('') is None and BA.verify('a' * 500) is None)
try:
    BA.add('desktop')
    chk('duplicate active name refused', False)
except ValueError:
    chk('duplicate active name refused', True)
try:
    BA.add('bad;name')
    chk('odd device name refused', False)
except ValueError:
    chk('odd device name refused', True)
BA.revoke('phone')
chk('revoked key stops working at once', BA.verify(k2) is None and BA.verify(k1) == 'desktop')
chk('unknown revoke is a no-op', BA.revoke('ghost') is False)

# ---- unit: who needs a key ----
E = {}
chk('loopback, no proxy: keyless', BA.needs_key('127.0.0.1', {}, E) is False)
chk('ipv6 loopback: keyless', BA.needs_key('::1', {}, E) is False)
chk('tailnet / LAN peer needs a key', BA.needs_key('100.64.1.2', {}, E) and BA.needs_key('192.168.1.5', {}, E))
chk('proxy header from loopback needs a key', all(BA.needs_key('127.0.0.1', {h: 'x'}, E) for h in
    ('X-Forwarded-For', 'Forwarded', 'Tailscale-User-Login', 'x-forwarded-proto')))
chk('REQUIRE_KEY=1 makes loopback need one', BA.needs_key('127.0.0.1', {}, {'DEALFLOW_BRIDGE_REQUIRE_KEY': '1'}))
chk('garbage peer address needs a key', BA.needs_key('not-an-ip', {}, E))
ok, dev, _ = BA.check('100.64.1.2', {'X-Dealflow-Key': k1}, E)
chk('check: remote with a good key is allowed', ok and dev == 'desktop')
ok, dev, _ = BA.check('100.64.1.2', {}, E)
chk('check: remote without a key is refused', not ok)
ok, dev, _ = BA.check('127.0.0.1', {}, E)
chk('check: local keyless is allowed as local', ok and dev == 'local')

# ---- unit: fail closed + bind safety ----
bad = os.path.join(TMP, 'bad.json')
open(bad, 'w').write('{not json')
chk('unreadable keys file accepts nothing', BA.verify(k1, bad) is None)
open(os.path.join(TMP, 'k2.json'), 'w').write(json.dumps({'devices': 'oops'}))
chk('malformed keys file accepts nothing', BA.verify(k1, os.path.join(TMP, 'k2.json')) is None)
chk('wildcard bind never safe', not BA.host_is_safe('0.0.0.0')[0] and not BA.host_is_safe('::')[0] and not BA.host_is_safe('')[0])
chk('loopback bind safe', BA.host_is_safe('127.0.0.1')[0] and BA.host_is_safe('localhost')[0])
chk('non-loopback bind safe with an active key', BA.host_is_safe('100.64.1.2')[0])

# ---- integration: the real bridge ----
def spawn(port, extra_env=None, args=()):
    env = dict(os.environ, **(extra_env or {}))
    return subprocess.Popen([sys.executable, os.path.join(HERE, 'send_server.py'), '--port', str(port)] + list(args),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE, env=env)


def req(port, path, key=None, method='GET', body=None, headers=None):
    h = dict(headers or {})
    if key:
        h['X-Dealflow-Key'] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h['Content-Type'] = 'application/json'
    r = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or '{}'), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or '{}'), dict(e.headers)


def wait(port):
    for _ in range(60):
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/pid' % port, timeout=1).read()
            return True
        except Exception:
            time.sleep(0.25)
    return False


# 1) REQUIRE_KEY=1: even this computer needs a key
P1 = 8831
s1 = spawn(P1, {'DEALFLOW_BRIDGE_REQUIRE_KEY': '1', 'DEALFLOW_BRIDGE_RELOAD_POLL_S': '0'})
try:
    chk('bridge (require-key) starts', wait(P1))
    c, j, _ = req(P1, '/health')
    chk('keyless /health: liveness only, texting held', c == 200 and j.get('ok') is True and j.get('text_hold') is True
        and j.get('ready') is False and 'sent_today' not in j and 'user' not in j)
    c, j, _ = req(P1, '/health', key='wrong')
    chk('wrong key /health: still minimal', 'sent_today' not in j)
    c, j, _ = req(P1, '/health', key=k1)
    chk('good key /health: the real answer', c == 200 and 'sent_today' in j)
    for path in ('/send', '/text', '/text/check', '/notes', '/mark', '/retrace', '/marks/consumed'):
        c, j, _ = req(P1, path, method='POST', body={'case': 'X'})
        chk('keyless POST %s refused 401' % path, c == 401 and j.get('blocked') == 'device_key')
    c, j, _ = req(P1, '/marks')
    chk('keyless GET /marks refused', c == 401)
    c, j, _ = req(P1, '/text/check', key=k1, method='POST', body={'case': 'TEST-0-CA-01', 'to': '3055550101'})
    chk('good key reaches the real handler (its own gates still answer)', c == 200 and j.get('blocked') != 'device_key')
    c, j, _ = req(P1, '/text/check', key=k2, method='POST', body={'case': 'TEST-0-CA-01', 'to': '3055550101'})
    chk('revoked key refused', c == 401)
    c, _, h = req(P1, '/send', method='OPTIONS')
    chk('preflight open and allows the key header', c == 204 and 'X-Dealflow-Key' in h.get('Access-Control-Allow-Headers', '')
        and h.get('Access-Control-Allow-Private-Network') == 'true')
    c, j, _ = req(P1, '/pid')
    chk('/pid stays open', c == 200 and 'pid' in j)
finally:
    s1.kill()

# 2) default mode: loopback keyless works; a proxied request needs the key
P2 = 8832
s2 = spawn(P2, {'DEALFLOW_BRIDGE_RELOAD_POLL_S': '0', 'DEALFLOW_BRIDGE_REQUIRE_KEY': ''})
try:
    chk('bridge (default) starts', wait(P2))
    c, j, _ = req(P2, '/health')
    chk('local keyless /health is the full answer (nothing changes on the laptop)', 'sent_today' in j)
    c, j, _ = req(P2, '/health', headers={'X-Forwarded-For': '100.64.1.2'})
    chk('proxied keyless /health is minimal', 'sent_today' not in j and j.get('text_hold') is True)
    c, j, _ = req(P2, '/text/check', method='POST', body={'case': 'X', 'to': '3055550101'}, headers={'Tailscale-User-Login': 'a@b'})
    chk('proxied keyless POST refused', c == 401)
    c, j, _ = req(P2, '/text/check', key=k1, method='POST', body={'case': 'X', 'to': '3055550101'}, headers={'X-Forwarded-For': '100.64.1.2'})
    chk('proxied POST with a key goes through', c == 200 and j.get('blocked') != 'device_key')
finally:
    s2.kill()

# 3) startup refuses unsafe binds
r = subprocess.run([sys.executable, os.path.join(HERE, 'send_server.py'), '--port', '8833', '--host', '0.0.0.0'],
                   capture_output=True, text=True, timeout=30, cwd=HERE, env=dict(os.environ, DEALFLOW_BRIDGE_RELOAD_POLL_S='0'))
chk('wildcard bind refused at startup', r.returncode == 2 and 'wildcard' in r.stdout)
os.environ['DEALFLOW_BRIDGE_KEYS'] = os.path.join(TMP, 'none.json')
r = subprocess.run([sys.executable, os.path.join(HERE, 'send_server.py'), '--port', '8834', '--host', '100.64.1.2'],
                   capture_output=True, text=True, timeout=30, cwd=HERE, env=dict(os.environ, DEALFLOW_BRIDGE_RELOAD_POLL_S='0'))
chk('non-loopback bind with no key refused at startup', r.returncode == 2 and 'device key' in r.stdout)

print()
print('FAILED: %d' % len(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
