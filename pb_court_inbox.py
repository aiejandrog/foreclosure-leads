#!/usr/bin/env python3
"""pb_court_inbox.py -- local receiver for Palm Beach COURT documents and new-filing lists. $0.

WHY (measured live 2026-11-16, Alex: "test it ... decipher whether it's actually the problem")
Palm Beach's court site (appsgp.mypalmbeachclerk.com/eCaseView) is free as a guest and lists every
new foreclosure by case type and filing date, with the complaint as a PDF on the docket. What it
will not do is talk to a scripted browser: Playwright driving real Chrome, fresh profile or saved
profile, gets "reCAPTCHA failed: Could not verify" at the guest login (the site scores the browser
and sees the automation flag). A person's own Chrome signs in as a guest with no challenge at all.

So the court route runs IN THE OPERATOR'S CHROME (the Claude extension or a person), and this is
the other half: a listener on 127.0.0.1 that the court tab posts what it read to. Nothing here
touches the county, a captcha or a paid service.

    python pb_court_inbox.py                 serve on 127.0.0.1:8831 until stopped
    python pb_court_inbox.py --once-idle 900 stop after 15 quiet minutes (what a session uses)

ENDPOINTS (only the court site's own origin may call them)
    POST /doc?case=<UCN>&din=<n>&kind=<COMPLAINT|...>     body = the PDF bytes
    POST /filings                                          body = JSON {window, rows:[...]}
    GET  /health

Files land in paths.DEALFLOW_DIR/documents/PALM BEACH/_court_inbox/ (outside the repo: court
filings name homeowners). complaint_reader.py reads the PDFs; pb_court_filings.py reads the lists.
"""
import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

HOST, PORT = '127.0.0.1', 8831
ORIGIN = 'https://appsgp.mypalmbeachclerk.com'
MAX_BYTES = 80 * 1024 * 1024
KIND_RE = re.compile(r'[A-Z0-9_]{1,40}$')
_last = [time.time()]


def inbox_dir():
    import paths
    d = os.path.join(paths.DEALFLOW_DIR, 'documents', 'PALM BEACH', '_court_inbox')
    os.makedirs(d, exist_ok=True)
    return d


def normalize_case(value):
    """A full Palm Beach civil UCN, digits and letters only, or ValueError."""
    v = re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    if not re.fullmatch(r'50\d{4}(?:CA|CC)\d{6}[A-Z0-9]{6}', v):
        raise ValueError('not a Palm Beach civil case number')
    return v


def doc_name(case, din, kind):
    """The only file name a posted document can take: no caller text reaches the path."""
    case = normalize_case(case)
    if not re.fullmatch(r'\d{1,5}', str(din or '')):
        raise ValueError('bad docket number')
    kind = re.sub(r'[^A-Z0-9]+', '_', str(kind or 'DOC').upper()).strip('_')[:40] or 'DOC'
    if not KIND_RE.match(kind):
        raise ValueError('bad kind')
    return '%s_DIN%s_%s.pdf' % (case, din, kind)


def save_doc(folder, case, din, kind, body):
    if not body.startswith(b'%PDF-'):
        raise ValueError('body is not a PDF')
    name = doc_name(case, din, kind)
    tmp = os.path.join(folder, name + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(body)
    os.replace(tmp, os.path.join(folder, name))
    return name


def save_filings(folder, payload):
    rows = payload.get('rows') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError('filings payload needs a rows list')
    name = 'filings_%s.json' % time.strftime('%Y%m%d_%H%M%S')
    tmp = os.path.join(folder, name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'received': time.strftime('%Y-%m-%dT%H:%M:%S'), 'window': payload.get('window'),
                   'searches': payload.get('searches'), 'rows': rows}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(folder, name))
    return name, len(rows)


class Handler(BaseHTTPRequestHandler):
    server_version = 'pbcourtinbox/1'

    def log_message(self, fmt, *args):       # counts only: never a case style or a name
        pass

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # Chrome asks before a public page may reach a loopback address
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Vary', 'Origin')

    def _answer(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        origin = self.headers.get('Origin')
        host = (self.headers.get('Host') or '').split(':')[0]
        return origin == ORIGIN and host in ('127.0.0.1', 'localhost')

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if urlsplit(self.path).path == '/health':
            return self._answer(200, {'ok': True, 'service': 'pb_court_inbox'})
        self._answer(404, {'ok': False})

    def do_POST(self):
        _last[0] = time.time()
        if not self._allowed():
            return self._answer(403, {'ok': False, 'error': 'origin not allowed'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > MAX_BYTES:
            return self._answer(413, {'ok': False, 'error': 'missing or oversized body'})
        body = self.rfile.read(n)
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == '/doc':
                name = save_doc(self.server.folder, q.get('case'), q.get('din'), q.get('kind'), body)
                print('  doc saved: %d bytes (%s)' % (len(body), name.split('_', 1)[1]), flush=True)
                return self._answer(200, {'ok': True, 'bytes': len(body)})
            if url.path == '/filings':
                _name, count = save_filings(self.server.folder, json.loads(body.decode('utf-8')))
                print('  filings saved: %d row(s)' % count, flush=True)
                return self._answer(200, {'ok': True, 'rows': count})
        except (ValueError, UnicodeDecodeError) as exc:
            return self._answer(400, {'ok': False, 'error': str(exc)[:120]})
        self._answer(404, {'ok': False})


def serve(folder=None, port=PORT, idle=None):
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.folder = folder or inbox_dir()
    srv.timeout = 5
    print('pb_court_inbox: listening on %s:%d' % (HOST, port), flush=True)
    try:
        while idle is None or time.time() - _last[0] < idle:
            srv.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--once-idle', type=int, default=None, help='stop after this many quiet seconds')
    a = ap.parse_args(argv)
    return serve(port=a.port, idle=a.once_idle)


if __name__ == '__main__':
    sys.exit(main())
