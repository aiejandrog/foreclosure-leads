"""bridge_auth.py - per-device keys for the send bridge (send_server.py).

WHY (2026-10-13, Alejandro: "any device can send and use the tools through the running bridge"):
the bridge listens on 127.0.0.1 and has no login; the loopback bind was the only boundary. Letting
a phone, the desktop or Carlos's seat reach it means a remote /send, so a request that does not
come from this machine itself must present a key.

  * One key per device, shown ONCE when created. Only a SHA-256 of it is stored, in
    DEALFLOW_DIR/bridge_keys.json (outside the repo, never committed). Revoking a device takes
    effect on the next request: the file is re-read whenever its mtime changes.
  * A key is required when the peer address is not loopback, when the request carries a proxy
    marker (X-Forwarded-*, Forwarded, Tailscale-*: a reverse proxy such as `tailscale serve`
    reaches the bridge FROM loopback, so the peer address alone would call it local), or when
    DEALFLOW_BRIDGE_REQUIRE_KEY=1 (recommended once any device is added: then nothing is keyless).
  * Fail closed: an unreadable keys file accepts no key at all.
  * /pid and OPTIONS (browser preflight) stay open; /health answers only {"ok":true} without a key.

Every send gate (opt-out, bounce, stay, caps, text hold) still runs on the bridge machine. This
module only decides who may ask.

CLI (run on the bridge machine):
  python bridge_auth.py add <name> [--base https://laptop.tail1234.ts.net]
  python bridge_auth.py revoke <name>
  python bridge_auth.py list
"""
import argparse
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sys
import tempfile
import threading

try:
    import paths as _P
    _DIR = _P.DEALFLOW_DIR
except Exception:
    _DIR = os.path.join(os.path.expanduser('~'), 'DEALFLOW')

KEYS_FILE = os.environ.get('DEALFLOW_BRIDGE_KEYS') or os.path.join(_DIR, 'bridge_keys.json')
KEY_HEADER = 'X-Dealflow-Key'
_PROXY_MARKERS = ('x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded',
                  'tailscale-user-login', 'tailscale-user-name')
_NAME_OK = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ .')

_LOCK = threading.Lock()
_CACHE = {'mtime': None, 'ok': True, 'devices': {}}


def _hash(key):
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()


def _load(path=None):
    """(readable, {name: {'hash','revoked',...}}). A missing file is readable and empty; a file
    that exists but cannot be parsed is NOT readable and accepts no key."""
    path = path or KEYS_FILE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return True, {}
    with _LOCK:
        if path == KEYS_FILE and _CACHE['mtime'] == mtime:
            return _CACHE['ok'], _CACHE['devices']
    ok, devices = True, {}
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        devices = d.get('devices') if isinstance(d, dict) else None
        if not isinstance(devices, dict):
            ok, devices = False, {}
    except Exception:
        ok, devices = False, {}
    if path == KEYS_FILE:
        with _LOCK:
            _CACHE.update(mtime=mtime, ok=ok, devices=devices)
    return ok, devices


def active_count(path=None):
    ok, devices = _load(path)
    return sum(1 for v in devices.values() if isinstance(v, dict) and not v.get('revoked') and v.get('hash')) if ok else 0


def verify(key, path=None):
    """Device name for a valid, unrevoked key, else None. Constant-time per stored hash."""
    key = str(key or '').strip()
    if not key or len(key) > 200:
        return None
    ok, devices = _load(path)
    if not ok:
        return None
    h = _hash(key)
    found = None
    for name, v in devices.items():
        if isinstance(v, dict) and v.get('hash') and not v.get('revoked'):
            if hmac.compare_digest(str(v['hash']), h):
                found = name
    return found


def is_loopback(ip):
    try:
        return ipaddress.ip_address(str(ip).split('%')[0]).is_loopback
    except ValueError:
        return False


def needs_key(peer_ip, headers, env=None):
    """True when this request must carry a valid key. `headers` is any mapping with .get/keys."""
    env = os.environ if env is None else env
    if str(env.get('DEALFLOW_BRIDGE_REQUIRE_KEY', '')).strip() == '1':
        return True
    if not is_loopback(peer_ip):
        return True
    try:
        names = {str(k).lower() for k in headers.keys()}
    except Exception:
        return True
    return any(m in names for m in _PROXY_MARKERS)


def check(peer_ip, headers, env=None, path=None):
    """(allowed, device, why). device is the key's name, or 'local' for a keyless loopback caller."""
    if not needs_key(peer_ip, headers, env):
        return True, 'local', ''
    dev = verify(headers.get(KEY_HEADER) or headers.get(KEY_HEADER.lower()), path)
    if dev:
        return True, dev, ''
    return False, '', 'a device key is required (header %s)' % KEY_HEADER


def host_is_safe(host):
    """May the bridge bind this address? Loopback always; a wildcard never; anything else only
    when at least one active device key exists."""
    h = str(host or '').strip()
    if h in ('', '0.0.0.0', '::', '[::]'):
        return False, 'bind a specific address, not a wildcard (a wildcard exposes every network this computer is on)'
    if h == 'localhost' or is_loopback(h):
        return True, ''
    if active_count() < 1:
        return False, 'binding %s needs at least one device key first: python bridge_auth.py add <name>' % h
    return True, ''


def _write(devices, path=None):
    path = path or KEYS_FILE
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump({'devices': devices}, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def add(name, path=None):
    """Create a key for `name`; returns it. The only time it exists in the clear."""
    name = str(name or '').strip()
    if not name or len(name) > 40 or any(c not in _NAME_OK for c in name):
        raise ValueError('device name: 1-40 letters, digits, space, - _ .')
    ok, devices = _load(path)
    if not ok:
        raise RuntimeError('bridge_keys.json exists but cannot be read; fix or move it, nothing was changed')
    cur = devices.get(name)
    if isinstance(cur, dict) and not cur.get('revoked'):
        raise ValueError('%r already has a key; revoke it first to issue a new one' % name)
    key = secrets.token_urlsafe(32)
    devices = dict(devices)
    devices[name] = {'hash': _hash(key), 'created': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'revoked': False}
    _write(devices, path)
    return key


def revoke(name, path=None):
    ok, devices = _load(path)
    if not ok:
        raise RuntimeError('bridge_keys.json exists but cannot be read; nothing was changed')
    if name not in devices or not isinstance(devices[name], dict):
        return False
    devices = dict(devices)
    devices[name] = dict(devices[name], revoked=True,
                         revoked_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    _write(devices, path)
    return True


def listing(path=None):
    ok, devices = _load(path)
    if not ok:
        return None
    return [(n, bool(isinstance(v, dict) and v.get('revoked')), str(v.get('created', ''))[:19]) for n, v in sorted(devices.items())]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('add')
    a.add_argument('name')
    a.add_argument('--base', default='', help='the bridge address devices will use, to print a one-tap setup link')
    r = sub.add_parser('revoke')
    r.add_argument('name')
    sub.add_parser('list')
    args = ap.parse_args(argv)
    if args.cmd == 'add':
        key = add(args.name)
        print('device %r created. This key is shown once; it is not stored anywhere readable.' % args.name)
        print('  key: %s' % key)
        if args.base:
            print('  setup link (open it once on that device, on the board or Call Mode page):')
            print('    #bridge=%s&key=%s' % (args.base.rstrip('/'), key))
            print('  append that to the page address, e.g. .../call#bridge=...&key=...')
        return 0
    if args.cmd == 'revoke':
        print('revoked' if revoke(args.name) else 'no such device')
        return 0
    rows = listing()
    if rows is None:
        print('bridge_keys.json cannot be read')
        return 1
    for n, rv, cr in rows:
        print('%-24s %-8s %s' % (n, 'REVOKED' if rv else 'active', cr))
    if not rows:
        print('no devices')
    return 0


if __name__ == '__main__':
    sys.exit(main())
