"""send_server.py — attachment support (added 2026-08-06).

Case workups (docket PDFs, final judgments, deed copies) now go out as real attachments. Mailing a
partner a path to a file on Alejandro's own Desktop is useless to them, so this had to exist before
the "every case scrape gets emailed to Jesse" standing rule could actually be honored.

Runs the real server on a scratch port with SMTP monkeypatched to CAPTURE the composed message
instead of sending it, so the assertions are about what would really leave the machine:
  * a PDF attachment actually rides along, with the right filename and MIME type
  * the attachment bytes survive intact (base64 round-trip, not truncated)
  * multiple attachments work
  * a MISSING file fails LOUD (502) instead of silently mailing a workup with nothing attached --
    the failure mode that would matter most, since nobody would notice until the recipient asked
  * a malformed attach field is refused 400
  * no attachment = unchanged plain-text behavior (regression guard)
"""
import base64, email, json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
ok, bad = [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, payload=None, timeout=20):
    url = f'http://127.0.0.1:{port}{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'} if data else {},
        method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {'err': str(e)}


def main():
    port = free_port()
    work = pathlib.Path(tempfile.mkdtemp(prefix='dfattach_'))
    proc = None
    try:
        shutil.copy(HERE / 'send_server.py', work / 'send_server.py')
        (work / 'gmail.key').write_text('tester@example.com:abcdabcdabcdabcd\n', encoding='utf-8')
        (work / 'sender.json').write_text(json.dumps({'name': 'Test Sender'}), encoding='utf-8')

        # Two real files with known, verifiable bytes.
        pdf_bytes = b'%PDF-1.4\n% docket test payload \xde\xad\xbe\xef\n'
        (work / 'docket.pdf').write_bytes(pdf_bytes)
        txt_bytes = b'judgment text payload'
        (work / 'judgment.txt').write_bytes(txt_bytes)

        # Capture the composed message to disk instead of sending it.
        shim = work / '_run_bridge.py'
        shim.write_text(
            'import sys, smtplib\n'
            'class _FakeSMTP:\n'
            '    def __init__(self, *a, **k): pass\n'
            '    def __enter__(self): return self\n'
            '    def __exit__(self, *a): return False\n'
            '    def login(self, u, p): pass\n'
            '    def send_message(self, m):\n'
            '        open("captured.eml","w",encoding="utf-8").write(m.as_string())\n'
            '        return {}\n'
            'smtplib.SMTP_SSL = _FakeSMTP\n'
            'sys.argv = ["send_server.py", "--port", "%d", "--limit", "50"]\n'
            'exec(open("send_server.py", encoding="utf-8").read())\n' % port,
            encoding='utf-8')

        proc = subprocess.Popen([sys.executable, str(shim)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(40):
            st, _j = call(port, '/health')
            if st == 200:
                up = True
                break
            time.sleep(0.25)
        rec('server starts', up)
        if not up:
            raise SystemExit(1)

        # ---------- one PDF attachment -------------------------------------------------
        st, j = call(port, '/send', {
            'to': 'jesse@example.com', 'subj': 'Case workup', 'body': 'Brief in the body.',
            'attach': [str(work / 'docket.pdf')], 'meta': {'c': 'CASE-A'}})
        rec('/send 200 with an attachment', st == 200 and j.get('ok') is True, {'st': st, 'j': j})

        raw = (work / 'captured.eml').read_text(encoding='utf-8')
        m = email.message_from_string(raw)
        parts = [p for p in m.walk() if p.get_filename()]
        rec('exactly one attachment present', len(parts) == 1, [p.get_filename() for p in parts])
        rec('attachment keeps its real filename',
            parts and parts[0].get_filename() == 'docket.pdf',
            parts[0].get_filename() if parts else None)
        rec('attachment carries the right MIME type',
            parts and parts[0].get_content_type() == 'application/pdf',
            parts[0].get_content_type() if parts else None)
        rec('attachment BYTES survive intact (not truncated/corrupted)',
            parts and parts[0].get_payload(decode=True) == pdf_bytes,
            len(parts[0].get_payload(decode=True)) if parts else 0)
        rec('the written brief is still in the body', 'Brief in the body.' in raw)

        # ---------- multiple attachments ------------------------------------------------
        (work / 'captured.eml').unlink(missing_ok=True)
        st, j = call(port, '/send', {
            'to': 'jesse2@example.com', 'subj': 'Two docs', 'body': 'b',
            'attach': [str(work / 'docket.pdf'), str(work / 'judgment.txt')],
            'meta': {'c': 'CASE-B'}})
        m2 = email.message_from_string((work / 'captured.eml').read_text(encoding='utf-8'))
        names = sorted(p.get_filename() for p in m2.walk() if p.get_filename())
        rec('multiple attachments all ride along',
            st == 200 and names == ['docket.pdf', 'judgment.txt'], names)

        # ---------- MISSING file must fail LOUD -----------------------------------------
        (work / 'captured.eml').unlink(missing_ok=True)
        st, j = call(port, '/send', {
            'to': 'jesse3@example.com', 'subj': 'Missing', 'body': 'b',
            'attach': [str(work / 'does_not_exist.pdf')], 'meta': {'c': 'CASE-C'}})
        rec('a MISSING attachment fails loud (502), never mails an empty workup',
            st == 502, {'st': st, 'j': j})
        rec('nothing was sent when the attachment was missing',
            not (work / 'captured.eml').exists())

        # ---------- malformed attach field ----------------------------------------------
        st, j = call(port, '/send', {
            'to': 'jesse4@example.com', 'subj': 's', 'body': 'b',
            'attach': [123, {'x': 1}], 'meta': {'c': 'CASE-D'}})
        rec('malformed attach field refused 400', st == 400, {'st': st, 'j': j})

        # ---------- regression: no attachment behaves exactly as before -------------------
        (work / 'captured.eml').unlink(missing_ok=True)
        st, j = call(port, '/send', {
            'to': 'owner@example.com', 'subj': 'Plain letter', 'body': 'owner body',
            'meta': {'c': 'CASE-E'}})
        m3 = email.message_from_string((work / 'captured.eml').read_text(encoding='utf-8'))
        rec('no-attachment send still works (regression)',
            st == 200 and not [p for p in m3.walk() if p.get_filename()], {'st': st})

    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n==== {len(ok)}/{len(ok)+len(bad)} attachment checks passed ====")
    if bad:
        print('FAILED:', bad)
        sys.exit(1)


if __name__ == '__main__':
    main()
