"""_coachcallstest -- the coach page has a Call recordings page, and the coach says the caller's own name.

Run:  python _coachcallstest.py   (exit 0 = safe; temp folder, invented calls, local port only, Explorer not opened)

WHY (2026-11-19, Alejandro): "I need for coach page to have an obvious button to take me to the folder where I
store all of the logs of the calls recorded." And on Carlos's call the coach card read "It's Alejandro with
Biscayne Solutions Group" while Carlos was the one talking.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_coach_server as S  # noqa: E402

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


T = tempfile.mkdtemp(prefix='coachcalls_')
S.SESSIONS = T
for sid, meta, files in [
    ('2026-11-19_181008_CACE-26-000001', {'case': 'CACE-26-000001', 'address': '1 TEST ST', 'duration_s': 383, 'outcome': 'talked',
                                           'started_utc': '2026-11-19T23:10:08+00:00',
                                           'wrapup': {'picked': 'talked', 'note': 'Said they are selling with a lawyer.', 'ai': {'objection': 'Working with a lawyer'}}},
     {'transcript.txt': '[00:01] <b>hello</b>', 'audio.webm': b'\x1aE\xdf\xa3'}),
    ('2026-11-19_160529_call', {'duration_s': 54, 'started_utc': '2026-11-19T21:05:29+00:00'}, {'audio.webm': b'x'}),
]:
    d = os.path.join(T, sid); os.makedirs(d)
    json.dump(meta, open(os.path.join(d, 'meta.json'), 'w', encoding='utf-8'))
    for fn, body in files.items():
        open(os.path.join(d, fn), 'wb' if isinstance(body, bytes) else 'w', **({} if isinstance(body, bytes) else {'encoding': 'utf-8'})).write(body)
open(os.path.join(T, 'outcomes.jsonl'), 'w').write('{}\n')

ix = S.calls_index()
rec('the index lists every call folder, newest first, and skips loose files', [c['sid'] for c in ix] == ['2026-11-19_181008_CACE-26-000001', '2026-11-19_160529_call'], ix)
rec('...with outcome, note, objection and what is on disk', ix[0]['outcome'] == 'talked' and 'lawyer' in ix[0]['note'] and ix[0]['objection'] and ix[0]['transcript'] and ix[0]['audio']
    and not ix[1]['transcript'], ix)

opened = []
S.os.startfile = lambda path: opened.append(path)
S.PORT = 0
srv = S.ThreadingHTTPServer((S.HOST, 0), S.Handler)
S.PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % S.PORT


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.headers.get('Content-Type'), r.read()


def post(path, headers=None):
    req = urllib.request.Request(base + path, data=b'{}', method='POST', headers=dict({'Content-Type': 'application/json'}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


st, ct, body = get('/calls'); html = body.decode('utf-8')
rec('/calls is a page listing the calls', st == 200 and 'text/html' in ct and 'Call recordings' in html and 'CACE-26-000001' in html and html.count('<tr data-q') == 2, html[:200])
rec('...with an Open the folder button and the folder path', 'Open the folder' in html and '/api/open_sessions' in html and T.replace('\\', '\\') in html)
rec('...with a transcript link and an audio player per call', "/calls/2026-11-19_181008_CACE-26-000001/transcript" in html and "/calls/2026-11-19_181008_CACE-26-000001/audio" in html)
st, ct, body = get('/calls/2026-11-19_181008_CACE-26-000001/transcript')
rec('the transcript opens, escaped', st == 200 and b'&lt;b&gt;hello&lt;/b&gt;' in body and b'<b>hello' not in body, body[:200])
st, ct, body = get('/calls/2026-11-19_181008_CACE-26-000001/audio')
rec('the audio plays', st == 200 and ct == 'audio/webm' and body.startswith(b'\x1aE'), (st, ct))
for bad in ('/calls/..%5C..%5Cetc/transcript', '/calls/../transcript', '/calls/nope/audio', '/calls/2026-11-19_160529_call/transcript'):
    try:
        st = get(bad)[0]
    except urllib.error.HTTPError as e:
        st = e.code
    rec('no file outside a call folder, and no missing file: %s' % bad, st == 404, st)
st, j = post('/api/open_sessions', {'Origin': 'http://127.0.0.1:%d' % S.PORT, 'Sec-Fetch-Site': 'same-origin'})
rec('Open the folder opens Explorer on the recordings folder, nothing else', st == 200 and j.get('ok') and opened == [T], (st, j, opened))
st, j = post('/api/open_sessions', {'Origin': 'https://evil.example', 'Sec-Fetch-Site': 'cross-site'})
rec('another site cannot make it open Explorer', st == 403 and opened == [T], (st, j, opened))
srv.shutdown()

rec('the coach line says the caller typed in Your name', S.with_caller({'line': "It's Alejandro with Biscayne Solutions Group."}, 'carlos')['line'] == "It's Carlos with Biscayne Solutions Group.")
rec('...the wrap-up note too', S.with_caller({'note': 'Alejandro offered a call.', 'outcome': 'talked'}, 'Carlos Gonzalez')['note'] == 'Carlos offered a call.')
rec('...and nothing changes when the name is empty or Alejandro', S.with_caller({'line': "It's Alejandro."}, '')['line'] == "It's Alejandro." and S.with_caller({'line': "It's Alejandro."}, 'Alejandro')['line'] == "It's Alejandro.")
rec('...an empty answer stays empty', S.with_caller({}, 'Carlos') == {})

page = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
rec('the start screen has the Call recordings button', page.count('href="/calls"') >= 2 and 'Call recordings' in page)
rec('the page sends Your name with every coach ask, the wrap-up and the saved session', page.count("caller:($('f_sender')||{}).value||''") == 3)
rec('Your name is remembered on this computer', "localStorage.setItem('coachCaller'" in page and "localStorage.getItem('coachCaller')" in page)
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
