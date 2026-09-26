"""hc_ping.py - optional dead-man's-switch pings for the nightly (Healthchecks.io-style).

WHY (2026-09-26). The only observer neither PC can lie to is the cloud watchdog, and it can only
see what reached origin/main. It cannot see a run that is still going, a run that died at 06:53
because the laptop went to sleep (09-25), or a 05:30 that never fired at all (09-26) until it
looks at the commit log hours later. A dead-man's switch closes that gap from the other side: the
runner says "started" and "finished with rc=N", and the monitoring service alerts on its own
when the "finished" never arrives, or the "started" never arrives by the scheduled time.

OFF UNLESS CONFIGURED. With no URL this is a silent no-op that exits 0 and never touches the
network, so merging it changes nothing on either machine. To arm it on the runner:

    setx DEALFLOW_HEALTHCHECK_URL https://hc-ping.com/<your-check-uuid>

(or put that one line in a `healthcheck.url` file next to this script - it is gitignored by name,
see CLAUDE.md "Never commit"). The ping URL is a credential of sorts - anyone holding it can mark
the check healthy - so it is never printed in full and never committed.

PING PROTOCOL (https://healthchecks.io/docs/http_api/):
    <url>/start          the run began
    <url>/<exit-status>  0 = success, 1-255 = failure (the refresh's RUNEXIT ladder, 0-9)
    <url>/fail           explicit failure with a reason
    ?rid=<uuid>          pairs the start with its finish so the service measures duration
The rid for a run is written at `start` to hc-ping.rid (gitignored) and read back at `exit`.

WHAT IT SENDS: the verb, the exit code, the host name and the run's duration - never lead data and
never the run log, which can carry owner names and addresses.

IT CAN NEVER CHANGE THE RUN. Every path exits 0: a monitoring hiccup must not turn a good night into
a failed one, and the .bat does not read this script's errorlevel. Network errors are retried twice
and then reported in the run log as one line.

    python hc_ping.py start
    python hc_ping.py exit <code>
    python hc_ping.py fail "<short reason>"
    python hc_ping.py status          # prints whether a URL is configured (redacted)
"""
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = 'DEALFLOW_HEALTHCHECK_URL'
URL_FILE = os.path.join(HERE, 'healthcheck.url')
RID_FILE = os.path.join(HERE, 'hc-ping.rid')
TIMEOUT = 10
BACKOFF = (0, 2, 5)          # three attempts
# https only, a host, and a path - the ping URL is the check's identity, so a typo that points it
# somewhere else must be refused rather than "pinged".
_URL_RE = re.compile(r'^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~/-]+$')


def _say(msg):
    print('[hc-ping] ' + msg, flush=True)


def configured_url():
    """The ping URL, or None. Env var first, then the gitignored file. Never raises."""
    raw = (os.environ.get(ENV) or '').strip()
    if not raw:
        try:
            with open(URL_FILE, encoding='utf-8') as f:
                raw = f.read().strip()
        except OSError:
            raw = ''
    if not raw:
        return None
    raw = raw.rstrip('/')
    if not _URL_RE.match(raw):
        _say('%s is set but is not an https ping URL - ignoring it, no ping sent.' % ENV)
        return None
    return raw


def redact(url):
    """https://hc-ping.com/5bf6...a278 -> https://hc-ping.com/5bf6…(redacted)"""
    m = re.match(r'^(https://[^/]+/)(.*)$', url or '')
    if not m:
        return '(none)'
    tail = m.group(2)
    return m.group(1) + (tail[:4] + '…(redacted)' if len(tail) > 4 else '…')


def _read_rid():
    try:
        with open(RID_FILE, encoding='utf-8') as f:
            rid = f.read().split()[0]
        uuid.UUID(rid)
        return rid
    except (OSError, IndexError, ValueError):
        return None


def _new_rid():
    rid = str(uuid.uuid4())
    try:
        with open(RID_FILE, 'w', encoding='utf-8') as f:
            f.write('%s %d\n' % (rid, int(time.time())))
    except OSError:
        pass                       # no rid just means no duration pairing
    return rid


def _started_at():
    try:
        with open(RID_FILE, encoding='utf-8') as f:
            return int(f.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None


def ping(url, suffix, body, rid=None, opener=None):
    """POST body to url+suffix. Returns True on HTTP 2xx. Never raises."""
    opener = opener or urllib.request.urlopen
    full = url + suffix + (('?rid=' + rid) if rid else '')
    data = body.encode('utf-8', 'replace')[:10000]
    last = ''
    for wait in BACKOFF:
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(full, data=data, method='POST',
                                         headers={'Content-Type': 'text/plain; charset=utf-8',
                                                  'User-Agent': 'dealflow-hc-ping/1'})
            with opener(req, timeout=TIMEOUT) as r:
                code = getattr(r, 'status', None) or r.getcode()
                if 200 <= int(code) < 300:
                    return True
                last = 'HTTP %s' % code
        except urllib.error.HTTPError as e:
            last = 'HTTP %s' % e.code
            if 400 <= e.code < 500 and e.code != 429:
                break              # a wrong URL does not get better with retries
        except Exception as e:     # DNS down at 05:30, timeouts, TLS - all the same here
            last = type(e).__name__ + ': ' + str(e)[:120]
    _say('ping %s to %s did not land (%s). The run is unaffected.' % (suffix or '/', redact(url), last))
    return False


def main(argv, opener=None):
    verb = argv[1] if len(argv) > 1 else ''
    url = configured_url()
    if verb == 'status':
        _say('configured: %s' % (redact(url) if url else 'no - pings are a no-op'))
        return 0
    if verb not in ('start', 'exit', 'fail'):
        _say('usage: hc_ping.py start | exit <code> | fail "<reason>" | status')
        return 0
    if not url:
        return 0                   # the whole point: unconfigured means nothing happens
    host = socket.gethostname()
    if verb == 'start':
        rid = _new_rid()
        ok = ping(url, '/start', 'start on %s' % host, rid, opener)
        if ok:
            _say('start sent (%s).' % redact(url))
        return 0
    rid = _read_rid()
    started = _started_at()
    dur = (' after %dm' % ((time.time() - started) // 60)) if started else ''
    if verb == 'exit':
        try:
            code = int(argv[2])
        except (IndexError, ValueError):
            code = 1               # an unreadable rc is not a success
        code = max(0, min(255, code))
        ok = ping(url, '/%d' % code, 'rc=%d on %s%s' % (code, host, dur), rid, opener)
        if ok:
            _say('exit %d sent (%s).' % (code, redact(url)))
        return 0
    reason = ' '.join(argv[2:])[:500] or 'failed'
    ok = ping(url, '/fail', '%s on %s%s' % (reason, host, dur), rid, opener)
    if ok:
        _say('fail sent (%s).' % redact(url))
    return 0


if __name__ == '__main__':
    try:
        main(sys.argv)
    except Exception as e:         # belt and braces: this script never fails the caller
        _say('unexpected error, ignored: %s' % type(e).__name__)
    sys.exit(0)
