#!/usr/bin/env python3
"""equity_inbox.py - a local receiver so the browser worker can hand county searches to the equity queue.

WHY (2026-11-22). The worker copies a county name search out of the real Chrome. Two exits failed:
the browser tool cuts an answer at ~1,000 characters (103 round trips for one 982-row name), and
Chrome's multiple-download guard swallows files after about five automatic downloads from a page.
So the page POSTs the search here instead, and this hands it to equity_queue.accept_search, the
same checks as `equity_queue.py rows --file`: checksums, row count equals the county's total, rows
belong to the search that was asked for. Nothing is stored that those checks refuse.

    python equity_inbox.py            # listens on 127.0.0.1:8791 until Ctrl+C

SAFETY
  - binds 127.0.0.1 only; never reachable from another machine
  - accepts POST /eq only from the two county recorder origins below (Origin header checked)
  - body cap 8 MB; body must be JSON {"<case>_<search index>": [header, chunk, ...]}
  - writes nothing itself: equity_queue decides what is stored, under DEALFLOW_DIR
  - answers counts and refusal reasons only, never names
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import equity_queue as Q

HOST, PORT = '127.0.0.1', 8791
ORIGINS = ('https://officialrecords.broward.org', 'https://erec.mypalmbeachclerk.com')
MAX_BODY = 8 * 1024 * 1024


class H(BaseHTTPRequestHandler):
    server_version = 'EquityInbox/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('[inbox] %s\n' % (fmt % args))

    def _cors(self):
        o = self.headers.get('Origin') or ''
        if o in ORIGINS:
            self.send_header('Access-Control-Allow-Origin', o)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        return o in ORIGINS

    def _send(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        if self.path.split('?')[0] != '/eq':
            return self._send(404, {'ok': False, 'err': 'not found'})
        if (self.headers.get('Origin') or '') not in ORIGINS:
            return self._send(403, {'ok': False, 'err': 'origin not allowed'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BODY:
            return self._send(413, {'ok': False, 'err': 'empty or too large'})
        try:
            batch = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return self._send(400, {'ok': False, 'err': 'body is not JSON'})
        if not isinstance(batch, dict):
            return self._send(400, {'ok': False, 'err': 'body must be an object of "<case>_<index>": payload'})
        stored, refused, why = [], 0, {}
        for key, payload in batch.items():
            case, _, idx = str(key).rpartition('_')
            try:
                idx = int(idx)
            except ValueError:
                case, idx = str(key), 0
            try:
                good, msg = Q.accept_search(case, payload, idx)
            except Exception as e:      # noqa: BLE001 - one bad search must not lose the rest
                good, msg = False, 'error: %s' % type(e).__name__
            if good:
                stored.append(key)
            else:
                refused += 1
                k = str(msg).split(':')[0][:70]
                why[k] = why.get(k, 0) + 1
        return self._send(200, {'ok': True, 'stored': stored, 'refused': refused, 'why': why})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print('equity inbox listening on http://%s:%d/eq  (Ctrl+C to stop)' % (HOST, PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
