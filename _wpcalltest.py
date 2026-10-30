#!/usr/bin/env python3
"""_wpcalltest.py: POST /wp/lookup on the send bridge (Call Mode's "Look up on Whitepages" button).

Run: python _wpcalltest.py   (exit 0 = pass). Invented 2099 cases and 555 numbers, a temp
DEALFLOW_DIR, a scratch port. The bridge runs in a temp copy of the code with urllib's urlopen
replaced by a fake Whitepages (and a guard that logs any other network host), so nothing leaves this
machine, no real key is read and no money moves.

Covers: owner + likely numbers come back, household and relatives never do, a cached case costs $0,
the daily cap stops a lookup, a 403 gives blocked:'key' with no key text in the answer, a case on the
ledger / never_contact / Dead / DNC / stay-held (or with no stay verdict) is refused before any
request leaves, a number on the do-not-contact ledger is dropped, a request without a device key is
refused 401, and no request reached any host except the fake Whitepages.
"""
import datetime as dt
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
bad = []
KEYTEXT = 'FAKEKEY-do-not-echo-0123456789abcdef'


def rec(n, c, d=''):
    print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)[:300]) if d and not c else ''))
    if not c:
        bad.append(n)


def call(port, path, body=None, key=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {'Content-Type': 'application/json'}
    if key:
        h['X-Dealflow-Key'] = key
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), data=data, headers=h,
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, json.dumps({'err': str(e)})


def spent_today(work, today):
    try:
        return json.loads((work / 'batchdata_spend.json').read_text(encoding='utf-8'))[today]['total']
    except Exception:
        return -1.0


def js(t):
    try:
        return json.loads(t)
    except Exception:
        return {}


RUNNER = r'''
import sys, json, io, socket, urllib.request, urllib.error, urllib.parse
sys.argv = ["send_server.py", "--port", "%(port)d", "--limit", "50"]
LOG = "wp_requests.log"
def _log(s):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")
class _Resp:
    def __init__(self, obj): self._b = json.dumps(obj).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def _err(url, code):
    # the 403 body carries the key text on purpose: nothing of it may reach the page
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(("denied for %(key)s").encode() if code == 403 else b"{}"))
def _fake(req, timeout=None, *a, **k):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    u = urllib.parse.urlparse(url)
    if u.hostname in ("127.0.0.1", "localhost"):
        return _orig(req, timeout=timeout, *a, **k)      # the bridge talking to itself
    if u.netloc != "api.whitepages.com":
        _log("EGRESS " + url)
        raise urllib.error.URLError("blocked by the test")
    q = urllib.parse.parse_qs(u.query)
    _log("WP " + u.path + " " + (q.get("street") or q.get("name") or [""])[0])
    if u.path.endswith("/property"):
        st = (q.get("street") or [""])[0]
        if st.startswith("300 "):
            raise _err(url, 403)
        if st.startswith("200 "):
            raise _err(url, 404)
        return _Resp({"result": {"property_address": {"line1": st},
            "ownership_info": {"person_owners": [
                {"name": "Alpha Zztesta", "phones": [{"number": "3055550111", "type": "mobile"},
                                                    {"number": "3055550199", "type": "landline"}]},
                {"name": "Beta Zztesta", "phones": [{"number": "3055550122", "type": "mobile"}]}]},
            "residents": [{"name": "Delta Resident", "phones": [{"number": "3055550133"}]}]}})
    if u.path.endswith("/person"):
        nm = (q.get("name") or [""])[0]
        return _Resp([
            {"name": nm, "phones": [{"number": "3055550301", "type": "mobile"}],
             "current_addresses": [{"line1": "200 NW 2 ST, Miami, FL 33101"}],
             "relatives": [{"name": "Gamma Kin", "relation": "sibling", "phones": [{"number": "3055550303"}]}]},
            {"name": nm, "phones": [{"number": "3055550302", "type": "landline"}],
             "current_addresses": [{"line1": "9 SW 9 AVE, Orlando, FL 32801"}]}])
    return _Resp({})
_orig = urllib.request.urlopen
urllib.request.urlopen = _fake
_gai = socket.getaddrinfo
def _guard(host, *a, **k):
    if host not in (None, "127.0.0.1", "localhost", "::1", ""):
        _log("EGRESS-DNS " + str(host))
        raise OSError("blocked by the test")
    return _gai(host, *a, **k)
socket.getaddrinfo = _guard
exec(open("send_server.py", encoding="utf-8").read())
'''


def lead(n, addr, owners, county='MIAMI-DADE'):
    return {'Case #': '2099-%06d-CA-01' % n, 'Address': addr, 'owners': owners, 'County': county,
            'folio': '30-0000-000-%04d' % n}


def main():
    sk = socket.socket(); sk.bind(('127.0.0.1', 0)); port = sk.getsockname()[1]; sk.close()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfwpcall_'))
    proc = None
    try:
        for f in ('send_server.py', 'bridge_auth.py', 'bounce_scan.py', 'stay_gate.py', 'mail_guard.py',
                  'deliverability.py', 'sync_gate.py', 'text_hold.py', 'optout_sync.py',
                  'whitepages_lookup.py', 'wp_contacts.py', 'ownership_gate.py', 'bd_budget.py'):
            shutil.copy(HERE / f, work / f)
        today = dt.date.today().isoformat()
        (work / 'sync_status.json').write_text(json.dumps({
            'date': today, 'state': 'finished', 'ok': True,
            'started_at': time.time() - 120, 'finished_at': time.time() - 60, 'steps': []}), encoding='utf-8')
        leads = [
            lead(1, '100 NW 1 ST, Miami, FL 33101', 'ZZTESTA ALPHA'),        # property finds owner + household
            lead(2, '200 NW 2 ST, Miami, FL 33101', 'ZZTESTB ALPHA'),        # property 404 -> person layer
            lead(3, '300 NW 3 ST, Miami, FL 33101', 'ZZTESTC ALPHA'),        # 403
            lead(4, '100 NW 4 ST, Miami, FL 33101', 'ZZTESTD ALPHA'),        # ledgered
            lead(5, '100 NW 5 ST, Miami, FL 33101', 'ZZTESTE ALPHA'),        # never_contact.json
            lead(6, '100 NW 6 ST, Miami, FL 33101', 'ZZTESTF ALPHA'),        # worker notes: Dead
            lead(7, '100 NW 7 ST, Miami, FL 33101', 'ZZTESTG ALPHA'),        # active stay
            lead(8, '100 NW 8 ST, Miami, FL 33101', 'ZZTESTH ALPHA'),        # no stay entry at all
            lead(9, '100 NW 9 ST, Miami, FL 33101', 'ZZTESTI ALPHA'),        # worker notes: DNC
            lead(10, '100 NW 10 ST, Miami, FL 33101', 'ZZTESTJ ALPHA'),      # ok, one paid lookup (cap test)
            lead(11, '100 NW 11 ST, Miami, FL 33101', 'ZZTESTK ALPHA'),      # over the cap
            lead(12, '100 NW 12 ST, Miami, FL 33101', 'ZZTESTL ALPHA'),      # a number on the ledger
        ]
        (work / 'leads_final.json').write_text(json.dumps(leads), encoding='utf-8')
        (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
            '2099-000004-CA-01': {'optout': '2099-01-01'}}}), encoding='utf-8')
        (work / 'never_contact.json').write_text(json.dumps(['2099-000005']), encoding='utf-8')
        (work / 'worker_notes.json').write_text(json.dumps({'notes': {
            '2099-000006-CA-01': {'status': 'Dead'},
            '2099-000009-CA-01': {'status': 'Do Not Contact'}}}), encoding='utf-8')
        cache = {'2099-%06d-CA-01' % i: {'a': False, 'bd': '', 'sl': '', 'b': 0, 'v': 5, 't': 0}
                 for i in range(1, 13) if i != 8}
        cache['2099-000007-CA-01'] = {'a': True, 'bd': '2099-01-01', 'sl': '', 'b': 1, 'v': 5, 't': 0}
        (work / 'sale_history_cache.json').write_text(json.dumps(cache), encoding='utf-8')
        (work / 'bounced_emails.json').write_text('{}', encoding='utf-8')
        (work / 'whitepages.key').write_text(KEYTEXT, encoding='utf-8')
        (work / '_run.py').write_text(RUNNER % {'port': port, 'key': KEYTEXT}, encoding='utf-8')
        # the device-key file for the keyed bridge
        keys = work / 'bridge_keys.json'
        env = dict(os.environ, DEALFLOW_DIR=str(work), DEALFLOW_BRIDGE_KEYS=str(keys),
                   DEALFLOW_BRIDGE_REQUIRE_KEY='1', DEALFLOW_BRIDGE_RELOAD_POLL_S='0',
                   WP_DAILY_CAP='1.00', WP_MAX_SPEND='2', WP_THROTTLE_S='0')
        sys.path.insert(0, str(HERE))
        os.environ['DEALFLOW_BRIDGE_KEYS'] = str(keys)
        import bridge_auth as BA
        dev_key = BA.add('wptest')
        proc = subprocess.Popen([sys.executable, '_run.py'], cwd=str(work), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        up = any(call(port, '/health', key=dev_key)[0] == 200 or time.sleep(0.25) for _ in range(80))
        rec('bridge starts', up)
        if not up:
            return
        reqlog = work / 'wp_requests.log'
        nreq = lambda: len(reqlog.read_text(encoding='utf-8').splitlines()) if reqlog.exists() else 0
        look = lambda c, k=dev_key: js(call(port, '/wp/lookup', {'case': c}, key=k)[1])

        print('-- device key')
        s, t = call(port, '/wp/lookup', {'case': '2099-000001-CA-01'})
        rec('no device key: 401 and nothing looked up', s == 401 and js(t).get('blocked') == 'device_key' and nreq() == 0, (s, t))
        s, t = call(port, '/wp/lookup', {'case': '2099-000001-CA-01'}, key='not-a-key')
        rec('wrong device key: 401', s == 401 and nreq() == 0, (s, t))

        print('-- refusals leave before any request')
        for c, want in (('2099-000004-CA-01', 'ledger'), ('2099-000005-CA-01', 'never_contact'),
                        ('2099-000006-CA-01', 'dead'), ('2099-000009-CA-01', 'ledger'),
                        ('2099-000007-CA-01', 'stay'), ('2099-000008-CA-01', 'stay'),
                        ('2099-000099-CA-01', 'no_lead')):
            j = look(c)
            rec('%s refused as %s' % (c, want), j.get('ok') is False and j.get('blocked') == want and nreq() == 0, j)
        j = js(call(port, '/wp/lookup', raw=b'not json', key=dev_key)[1])
        rec('junk body refused', j.get('ok') is False and nreq() == 0, j)
        j = look('')
        rec('empty case refused', j.get('ok') is False and nreq() == 0, j)
        j = look('x;rm -rf')
        rec('odd characters in the case refused', j.get('ok') is False and nreq() == 0, j)
        rec('no spend ledger written by refusals', not (work / 'batchdata_spend.json').exists())

        print('-- owner + household: only the owner comes back')
        j = look('2099-000001-CA-01')
        nums = {x['n']: x for x in j.get('numbers') or []}
        rec('ok, paid once', j.get('ok') is True and j.get('cached') is False and j.get('spent') == 0.22, j)
        rec('owner numbers returned with kind and type',
            nums.get('3055550111', {}).get('kind') == 'owner' and nums.get('3055550111', {}).get('type') == 'mobile'
            and nums.get('3055550199', {}).get('kind') == 'owner', j)
        rec('a household member on the parcel is not returned', '3055550122' not in nums and '3055550133' not in nums, j)
        rec('every returned number has n, kind, type',
            all(set(x) == {'n', 'kind', 'type'} and x['kind'] in ('owner', 'likely') for x in j.get('numbers') or []), j)
        rec('the shared ledger shows $0.22', abs(spent_today(work, today) - 0.22) < 1e-6, spent_today(work, today))

        print('-- cached: $0')
        before = nreq()
        j2 = look('2099-000001-CA-01')
        rec('second ask is cached, $0, same numbers, no request',
            j2.get('ok') and j2.get('cached') is True and j2.get('spent') == 0.0
            and j2.get('numbers') == j.get('numbers') and nreq() == before, j2)

        print('-- likely owner; relatives never')
        j = look('2099-000002-CA-01')
        nums = {x['n']: x for x in j.get('numbers') or []}
        rec('property miss then person search: $0.44', j.get('ok') and j.get('spent') == 0.44, j)
        rec('a person record not placed at the property is a likely owner',
            nums.get('3055550301', {}).get('kind') == 'owner' or nums.get('3055550301', {}).get('kind') == 'likely', j)
        rec('the placed record is owner, the other is likely',
            nums.get('3055550301', {}).get('kind') == 'owner' and nums.get('3055550302', {}).get('kind') == 'likely', j)
        rec('relatives are never returned', '3055550303' not in nums and 'Kin' not in json.dumps(j), j)

        print('-- 403 -> key')
        n0 = nreq()
        s, t = call(port, '/wp/lookup', {'case': '2099-000003-CA-01'}, key=dev_key)
        j = js(t)
        rec('403: blocked key, http 403', j.get('ok') is False and j.get('blocked') == 'key' and j.get('http') == 403, t)
        rec('the answer never contains the key, a header name or the host',
            KEYTEXT not in t and 'x-api-key' not in t.lower() and 'whitepages.com' not in t.lower(), t)
        rec('a 403 is not billed', abs(spent_today(work, today) - 0.66) < 1e-6)
        rec('the bridge survived the 403 (no os._exit)', call(port, '/health', key=dev_key)[0] == 200)
        rec('a 403 is not cached', '2099-000003-CA-01' not in (json.loads((work / 'whitepages_lookup.json').read_text()) if (work / 'whitepages_lookup.json').exists() else {}) and (work / 'whitepages_lookup.json').exists())

        print('-- the daily cap')
        j = look('2099-000010-CA-01')
        rec('under the cap: paid ($0.88 today)', j.get('ok') and j.get('spent') == 0.22, j)
        n0 = nreq()
        j = look('2099-000011-CA-01')
        rec('over the cap: blocked budget, no request', j.get('ok') is False and j.get('blocked') == 'budget' and nreq() == n0, j)
        rec('the cap is the existing WP_DAILY_CAP (still $0.88 spent)',
            abs(spent_today(work, today) - 0.88) < 1e-6)
        j = look('2099-000001-CA-01')
        rec('a cached case still answers when the cap is spent', j.get('ok') and j.get('cached') is True, j)

        print('-- no key file')
        os.rename(work / 'whitepages.key', work / 'whitepages.key.off')
        j = look('2099-000012-CA-01')
        rec('no key on the bridge PC: blocked key, no request', j.get('blocked') == 'key' and nreq() == n0, j)
        os.rename(work / 'whitepages.key.off', work / 'whitepages.key')

        print('-- nothing left the machine')
        lines = reqlog.read_text(encoding='utf-8').splitlines() if reqlog.exists() else []
        rec('no request to any host but the fake Whitepages', not any(l.startswith('EGRESS') for l in lines), lines[:5])
        rec('every request was a Whitepages /v2 property or person call',
            all(l.startswith('WP /v2/property ') or l.startswith('WP /v2/person ') for l in lines), lines)
        rec('nothing in the refusal log carries the key or a number',
            KEYTEXT not in ((work / 'send_refusals.jsonl').read_text(encoding='utf-8') if (work / 'send_refusals.jsonl').exists() else ''))
        rec('no text or mail ledger was written', not (work / 'text_sent.json').exists() and not (work / 'mail_sent.json').exists())

        print('-- a ledgered number is dropped')
        # stop the bridge, plant a ledgered number matching case 12's owner number, ask again
        proc.terminate(); proc.wait(10)
        (work / 'optouts.json').write_text(json.dumps({'_dealflow_notes': True, 'notes': {
            '2099-000004-CA-01': {'optout': '2099-01-01'}, '#3055550199': {'optout': '2099-01-01'}}}), encoding='utf-8')
        proc = subprocess.Popen([sys.executable, '_run.py'], cwd=str(work), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        any(call(port, '/health', key=dev_key)[0] == 200 or time.sleep(0.25) for _ in range(80))
        j = look('2099-000001-CA-01')
        got = {x['n'] for x in j.get('numbers') or []}
        rec('a number on the do-not-contact ledger is not returned', '3055550199' not in got and '3055550111' in got
            and j.get('dropped_dnc') == 1, j)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)


main()
print('\n%d failed' % len(bad) if bad else '\nALL PASS')
sys.exit(1 if bad else 0)
