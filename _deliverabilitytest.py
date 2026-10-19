"""deliverability.py: content score, header alignment, the seed-test pause, and where it is enforced.

WHY THIS EXISTS (2026-10-19). Outreach is paused, the bounce rate hit 20% on 09-22, and 5 of 1,387
emailed cases replied. Nothing in the send path measured whether mail reached an inbox at all. The
seed test sends the real first-touch copy to mailboxes we can log into and pauses a sender whose
probe lands in spam. These checks pin the pause to every sender (/send, the CLI, cadence via
send_server._smtp_send), keep replies to people who wrote in out of it, and keep it fail-closed.

No network, no SMTP: smtplib and IMAP are stubbed. Run: python _deliverabilitytest.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_TMP = tempfile.mkdtemp(prefix='dv_')
os.environ['DEALFLOW_DIR'] = _TMP
for _k in ('DEALFLOW_PLACEMENT_HOLD', 'DEALFLOW_PLACEMENT_REQUIRED', 'DEALFLOW_SPAM_HOLD_SCORE',
           'DEALFLOW_PLACEMENT_MAX_AGE_DAYS'):
    os.environ.pop(_k, None)

import smtplib
import paths as P
import mail_guard as MG
import deliverability as DV

PASS, FAIL = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name + ('' if ok else '  -> %r' % (detail,)))


STATUS = DV._status_path()


def raises(fn):
    try:
        fn()
        return None
    except DV.PlacementHold as e:
        return e
    except Exception as e:
        return e


def put_status(by_from, raw=None):
    with open(STATUS, 'w', encoding='utf-8') as f:
        f.write(raw if raw is not None else json.dumps({'ts': '2026-10-19T12:00:00+00:00', 'by_from': by_from}))


def clear_status():
    if os.path.exists(STATUS):
        os.remove(STATUS)


rec('status file lives under DEALFLOW_DIR, not the repo', STATUS.startswith(_TMP), STATUS)

# ---------------------------------------------------------------- content score
subj, body = DV.sample_first_touch()
sc = DV.score_content(subj, body)
# 2026-10-19: the first touch was rewritten against this scorer (18.8 high -> ok). Keep it there.
rec('the current first-touch copy scores ok', sc['level'] == 'ok', (sc['score'], [i['detail'] for i in sc['issues']]))
# The 08-28 copy's trigger lines, kept as a fixture so the content hold still has a high scorer to refuse.
spam_subj = 'URGENT: Foreclosure sale 11/04/2026 - Regarding your property at 12 Oak St'
spam_body = ('Hi Maria,\n\nThere is still time to protect and save your home, but the clock is ticking. '
             'Please don\'t wait. I want to offer you something completely free and without any obligation. '
             'There are no tricks, no hidden fees, and no pressure.\n\nReply now or call me today.\n'
             'Reply now.\n\nAlejandro')
rules = {i['rule'] for i in DV.score_content(spam_subj, spam_body)['issues']}
rec('it names urgency and free triggers', {'urgency', 'free'} <= rules, rules)
rec('the old copy\'s lines still score high', DV.score_content(spam_subj, spam_body)['level'] == 'high')
calm = ('Hi Ana,\n\nI saw the case filed on 12 Oak St and wanted to ask whether you already have a '
        'plan for it. If you would like a second opinion from our senior advisor, reply with a good '
        'number and a time and I will call. Either way, I hope it works out.\n\nAlex\nBiscayne Solutions Group')
c2 = DV.score_content('About 12 Oak St', calm)
rec('a plain note scores ok', c2['level'] == 'ok', c2)
import disclaimer as D
ident = DV.score_content('About 12 Oak St', calm + '\n' + D.identity('en', as_html=False))
rec('the mandated identity sentence does not add a rescue hit',
    not any(i['rule'] == 'rescue' for i in ident['issues']), ident['issues'])
shout = DV.score_content('FREE CASH OFFER!!', 'Hi,\nCLICK bit.ly/x NOW!!! ' + calm)
rec('shouting subject, shortener and bangs score high', shout['level'] == 'high', shout)
rec('Re: on a cold subject is flagged',
    any('Re:' in i['detail'] for i in DV.score_content('Re: your home', calm)['issues']))

# ---------------------------------------------------------------- headers
GOOD = (b'Authentication-Results: mx.google.com;\r\n'
        b'       dkim=pass header.i=@bsgfl.com header.s=google header.b=abc;\r\n'
        b'       spf=pass (google.com: domain of alejandro@bsgflorida.com designates 1.2.3.4) '
        b'smtp.mailfrom=alejandro@bsgflorida.com;\r\n'
        b'       dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=bsgfl.com\r\n'
        b'DKIM-Signature: v=1; a=rsa-sha256; d=bsgfl.com; s=google; b=xyz\r\n'
        b'From: Alejandro <alejandro@bsgfl.com>\r\nSubject: hi\r\n\r\nbody')
r = DV.analyze_headers(GOOD)
rec('alias DKIM-signed with its own domain passes strict', r['verdict'] == 'pass' and r['dkim_aligned'], r)
rec('alias SPF does not align (return-path is bsgflorida.com)', r['spf'] == 'pass' and not r['spf_aligned'], r)
rec('strict is applied to bsgfl.com', r['strict'] is True)
BAD = GOOD.replace(b'header.i=@bsgfl.com', b'header.i=@bsgflorida.com').replace(
    b'dmarc=pass', b'dmarc=fail').replace(b'd=bsgfl.com', b'd=bsgflorida.com')
r = DV.analyze_headers(BAD)
rec('alias signed by bsgflorida.com fails DMARC', r['verdict'] == 'fail' and not r['dkim_aligned'], r)
NOAR = b'From: alejandro@bsgfl.com\r\nSubject: x\r\n\r\nb'
rec('no Authentication-Results reads unknown', DV.analyze_headers(NOAR)['verdict'] == 'unknown')
SUB = (b'Authentication-Results: mx.google.com; dkim=pass header.i=@mail.bsgflorida.com\r\n'
       b'From: a@bsgflorida.com\r\n\r\nb')
rec('relaxed: a subdomain signature aligns on bsgflorida.com', DV.analyze_headers(SUB)['verdict'] == 'pass')
rec('strict forced: the same subdomain does not', DV.analyze_headers(SUB, strict=True)['verdict'] == 'fail')
rec('headers_lines renders', any('Verdict' in x for x in DV.headers_lines(DV.analyze_headers(GOOD))))

# ---------------------------------------------------------------- dns (stubbed DoH)
def fake_fetch(url):
    if '_dmarc.' in url:
        return {'Answer': [{'type': 16, 'data': '"v=DMARC1; p=none; adkim=s; aspf=s"'}]}
    if 'google._domainkey.' in url:
        return {'Answer': [{'type': 16, 'data': '"v=DKIM1; k=rsa; p=MIIB"'}]}
    return {'Answer': [{'type': 16, 'data': '"v=spf1 include:_spf.google.com ~all"'}]}
rows = DV.dns_report(('bsgfl.com',), fetch=fake_fetch)
rec('dns: SPF, DMARC and DKIM read', rows[0]['spf'] and rows[0]['dmarc'] and rows[0]['dkim'], rows)
rec('dns: p=none is noted', any('p=none' in n for n in rows[0]['notes']), rows)

# ---------------------------------------------------------------- the pause
A, B = 'alejandro@bsgfl.com', 'alejandro@biscaynesolutionsgroup.com'
clear_status()
rec('no test ever run: no hold', DV.placement_hold(A) == '')
os.environ['DEALFLOW_PLACEMENT_REQUIRED'] = '1'
rec('no test + required: hold', 'required' in DV.placement_hold(A))
os.environ.pop('DEALFLOW_PLACEMENT_REQUIRED')
put_status({A: {'verdict': 'hold', 'reasons': ['spam at external seed x@gmail.com'], 'ts': '2026-10-01T00:00:00+00:00'},
            B: {'verdict': 'clear', 'reasons': [], 'ts': '2026-10-01T00:00:00+00:00'}})
rec('a paused sender holds', 'spam at external seed' in DV.placement_hold(A), DV.placement_hold(A))
rec('a paused sender holds at any age (never expires)', DV.placement_hold(A) != '')
rec('a clean sender does not', DV.placement_hold(B) == '')
rec('an untested sender does not', DV.placement_hold('alejandro@bsgflorida.com') == '')
os.environ['DEALFLOW_PLACEMENT_REQUIRED'] = '1'
rec('required: a clean test older than the max age holds', 'older than' in DV.placement_hold(B))
os.environ.pop('DEALFLOW_PLACEMENT_REQUIRED')
os.environ['DEALFLOW_PLACEMENT_HOLD'] = '0'
rec('DEALFLOW_PLACEMENT_HOLD=0 turns the pause off', DV.placement_hold(A) == '')
os.environ.pop('DEALFLOW_PLACEMENT_HOLD')
put_status({}, raw='{"by_from": {')
rec('a torn status file holds every sender', 'unreadable' in DV.placement_hold(B), DV.placement_hold(B))
put_status({A: {'verdict': 'hold', 'reasons': [{'odd': 1}], 'ts': 7}})
rec('a malformed row is a hold sentence, not an exception', DV.placement_hold(A).startswith('placement hold'),
    DV.placement_hold(A))
put_status({A: 'not a dict', B: {'verdict': 'hold', 'reasons': 'x'}})
rec('a row that is not a dict does not raise', isinstance(DV.placement_hold(A), str))
_orig = DV._placement_hold
DV._placement_hold = lambda *a, **k: 1 / 0
rec('placement_hold never raises', DV.placement_hold(A).startswith('placement hold'))
DV._placement_hold = _orig
_orig_rc = DV._require_clear
DV._require_clear = lambda *a, **k: [][1]
e = raises(lambda: DV.require_clear(A, to='owner@example.com'))
rec('an unexpected error in require_clear is a PlacementHold', isinstance(e, DV.PlacementHold), e)
DV._require_clear = _orig_rc
put_status({A: {'verdict': 'hold', 'reasons': ['missing at internal seed'], 'ts': '2026-10-19T00:00:00+00:00'}})

# require_clear: who it stops

e = raises(lambda: DV.require_clear(A, subj, body, to='owner@example.com', login='alejandro@bsgflorida.com'))
rec('outreach from a paused sender raises PlacementHold', isinstance(e, DV.PlacementHold), e)
rec('PlacementHold is an UnsendableError (callers already skip on it)', isinstance(e, MG.UnsendableError))
with MG.gated('reply_send'):
    rec('reply_send is exempt', raises(lambda: DV.require_clear(A, to='owner@example.com')) is None)
with MG.gated('send_server'):
    rec('a /send without meta.test is not', isinstance(raises(lambda: DV.require_clear(A, to='owner@example.com')), DV.PlacementHold))
    rec('mail only to our own domains is exempt', raises(lambda: DV.require_clear(A, to='help@bsgflorida.com')) is None)
    rec('one outside bcc makes it outreach again',
        isinstance(raises(lambda: DV.require_clear(A, to='help@bsgflorida.com', bcc='x@example.com')), DV.PlacementHold))
clear_status()
os.environ['DEALFLOW_SPAM_HOLD_SCORE'] = '8'
rec('content hold: a high-scoring body is refused at 8',
    isinstance(raises(lambda: DV.require_clear(B, spam_subj, spam_body, to='o@example.com')), DV.PlacementHold))
rec('content hold: the current template passes at 8',
    raises(lambda: DV.require_clear(B, subj, body, to='o@example.com')) is None)
rec('content hold: a plain note passes', raises(lambda: DV.require_clear(B, 'About 12 Oak St', calm, to='o@example.com')) is None)
os.environ.pop('DEALFLOW_SPAM_HOLD_SCORE')
rec('content score is advisory by default', raises(lambda: DV.require_clear(B, subj, body, to='o@example.com')) is None)

# ---------------------------------------------------------------- enforced in both _smtp_send
class _NoSMTP:
    opened = 0

    def __init__(self, *a, **k):
        _NoSMTP.opened += 1
        raise AssertionError('SMTP must not be opened for a paused sender')

_real = smtplib.SMTP_SSL
smtplib.SMTP_SSL = _NoSMTP
try:
    import send_server as SS
    import outreach_email as OE
    put_status({A: {'verdict': 'hold', 'reasons': ['spam at external seed'], 'ts': '2026-10-19T00:00:00+00:00'}})
    with MG.gated('send_server'):
        e = raises(lambda: SS._smtp_send('alejandro@bsgflorida.com', 'pw', 'Alex', 'owner@example.com',
                                         subj, body, from_addr=A))
    rec('send_server._smtp_send refuses a paused sender before SMTP',
        isinstance(e, DV.PlacementHold) and _NoSMTP.opened == 0, e)
    with MG.gated('cadence'):
        e = raises(lambda: SS._smtp_send('alejandro@bsgflorida.com', 'pw', 'Alex', 'owner@example.com',
                                         subj, body, from_addr=A))
    rec('cadence (through send_server._smtp_send) is held too', isinstance(e, DV.PlacementHold), e)
    with MG.gated('outreach_email'):
        e = raises(lambda: OE._smtp_send('alejandro@bsgflorida.com', 'pw', 'Alex', 'owner@example.com',
                                         subj, body, from_addr=A))
    rec('outreach_email._smtp_send refuses it too', isinstance(e, DV.PlacementHold) and _NoSMTP.opened == 0, e)
    with MG.gated('outreach_email'):
        e = raises(lambda: OE._smtp_send('alejandro@bsgflorida.com', 'pw', 'Alex', 'owner@example.com',
                                         subj, body, from_addr=B))
    rec('a clean sender gets past the pause (to SMTP)', isinstance(e, AssertionError) and _NoSMTP.opened == 1, e)

    # first-touch rotation skips the paused address
    cfg = {'main_domain': 'bsgflorida.com', 'ramp_start': '2026-09-21',
           'ramp': [{'through_day': 9999, 'per_day': 100}],
           'lanes': {'default': 'alejandro@bsgflorida.com'},
           'first_touch': {'from': [A, B], 'ramp_start': '2026-09-27',
                           'ramp': [{'through_day': 9999, 'per_day': 40}]}}
    pick, _st = SS._pick_first_touch_from(cfg, ceiling=None)
    rec('first-touch rotation skips the paused sender', pick == B, pick)
    put_status({A: {'verdict': 'hold', 'reasons': ['x'], 'ts': 't'}, B: {'verdict': 'hold', 'reasons': ['y'], 'ts': 't'}})
    pick, _st = SS._pick_first_touch_from(cfg, ceiling=None)
    rec('both paused: no first-touch sender', pick == '', pick)
finally:
    smtplib.SMTP_SSL = _real

# ---------------------------------------------------------------- the seed run (stubbed)
clear_status()
LOGIN = 'alejandro@bsgflorida.com'
seeds = [{'addr': 'seed1@gmail.com', 'key_file': os.path.join(_TMP, 's1.key')},
         {'addr': LOGIN, 'key_file': os.path.join(_TMP, 's2.key')}]
for s, fn in ((seeds[0], 's1.key'), (seeds[1], 's2.key')):
    with open(os.path.join(_TMP, fn), 'w') as f:
        f.write(s['addr'] + ':pw')
with open(DV._seeds_path(), 'w') as f:
    json.dump(seeds, f)

SENT = []


def fake_smtp(user, pw, disp, to, subj, body, from_addr=None):
    rec('probe goes through the placement_probe gate', MG.current_gate() == 'placement_probe')
    frm = from_addr or user
    if frm == 'alejandro@broken.com':
        raise smtplib.SMTPSenderRefused(553, b'not allowed', frm)
    mid = '<%d.%s>' % (len(SENT), frm)
    SENT.append((frm, to, mid))
    return mid


# Where each probe "landed": A's probe to the external seed is in spam.
def landing(frm, to):
    if frm == A and to == 'seed1@gmail.com':
        return 'spam'
    return 'inbox'


class FakeIMAP:
    def __init__(self, seed):
        self.seed = seed['addr']
        self.sel = None

    def list(self):
        return 'OK', [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"']

    def select(self, folder, readonly=True):
        self.sel = folder.strip('"')
        return 'OK', [b'1']

    def search(self, charset, *crit):
        if crit[0] == 'X-GM-RAW':
            return 'OK', [b'']
        mid = crit[2]
        for i, (frm, to, m) in enumerate(SENT):
            if m.strip('<>') == mid and to == self.seed:
                where = landing(frm, to)
                want = {'inbox': 'INBOX', 'spam': '[Gmail]/Spam'}[where]
                return 'OK', [str(i + 1).encode() if self.sel == want else b'']
        return 'OK', [b'']

    def fetch(self, num, what):
        frm, to, m = SENT[int(num) - 1]
        dom = frm.split('@')[1]
        return 'OK', [(b'1', ('Authentication-Results: mx.google.com; dkim=pass header.i=@%s; '
                              'dmarc=pass\r\nFrom: %s\r\n\r\n' % (dom, frm)).encode())]

    def logout(self):
        pass


out = []
rc = DV.run_placement(send=True, wait=0, froms=[A, B, 'alejandro@broken.com'],
                      smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0, cred=(LOGIN, 'pw'))
st, err = DV.load_status()
rec('run writes a readable status', st is not None and not err, err)
rec('spam at the external seed pauses that sender', st['by_from'][A]['verdict'] == 'hold', st['by_from'].get(A))
rec('the clean sender is clear', st['by_from'][B]['verdict'] == 'clear', st['by_from'].get(B))
rec('a sender whose probe could not be sent gets no verdict', 'alejandro@broken.com' not in st['by_from'])
rec('exit 1 when a sender is paused', rc == 1, rc)
rec('the pause is now enforced', DV.placement_hold(A) != '' and DV.placement_hold(B) == '')
rec('status carries no message bodies', 'Maria' not in open(STATUS).read())

# a later clean retest of A alone lifts A and keeps B's row
SENT.clear()
landing = lambda frm, to: 'inbox'  # noqa: E731
rc = DV.run_placement(send=True, wait=0, froms=[A], smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0, cred=(LOGIN, 'pw'))
st, _ = DV.load_status()
rec('a clean retest lifts the pause', st['by_from'][A]['verdict'] == 'clear' and rc == 0, st['by_from'][A])
rec('an address not retested keeps its row', st['by_from'].get(B, {}).get('verdict') == 'clear')

# a probe that never shows up is missing, and missing holds
SENT.clear()
class GoneIMAP(FakeIMAP):
    def search(self, charset, *crit):
        return 'OK', [b'']
rc = DV.run_placement(send=True, wait=0, froms=[B], smtp=fake_smtp, imap=GoneIMAP, out=out.append, pace=0, cred=(LOGIN, 'pw'))
st, _ = DV.load_status()
rec('a missing probe pauses the sender', st['by_from'][B]['verdict'] == 'hold'
    and any('missing' in x for x in st['by_from'][B]['reasons']), st['by_from'][B])

# DMARC failure on an inbox landing still holds
rec('inbox + DMARC fail holds', DV.verdict_for([{'from': A, 'seed': 's', 'folder': 'inbox',
    'auth': {'verdict': 'fail', 'dkim': [], 'spf': 'pass', 'dmarc': 'fail'}}])[A]['verdict'] == 'hold')
rec('promotions alone does not hold', DV.verdict_for([{'from': A, 'seed': 's', 'folder': 'promotions',
    'auth': {'verdict': 'pass'}}])[A]['verdict'] == 'clear')

# a pause from the external seed is not lifted by a retest that only reached the internal one
clear_status()
SENT.clear()
landing = lambda frm, to: 'spam' if to == 'seed1@gmail.com' else 'inbox'  # noqa: E731
DV.run_placement(send=True, wait=0, froms=[A], smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0,
                 cred=(LOGIN, 'pw'))
landing = lambda frm, to: 'inbox'  # noqa: E731
class HalfIMAP(FakeIMAP):
    def __init__(self, seed):
        if seed['addr'] == 'seed1@gmail.com':
            raise OSError('login refused')
        FakeIMAP.__init__(self, seed)
SENT.clear()
DV.run_placement(send=True, wait=0, froms=[A], smtp=fake_smtp, imap=HalfIMAP, out=out.append, pace=0,
                 cred=(LOGIN, 'pw'))
st, _ = DV.load_status()
rec('internal-only retest keeps the external spam pause', st['by_from'][A]['verdict'] == 'hold'
    and any('external' in x for x in st['by_from'][A]['reasons']), st['by_from'][A])
SENT.clear()
DV.run_placement(send=True, wait=0, froms=[A], smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0,
                 cred=(LOGIN, 'pw'))
st, _ = DV.load_status()
rec('a full clean retest lifts it', st['by_from'][A]['verdict'] == 'clear', st['by_from'][A])

# a transient IMAP failure on the first poll does not turn a missing probe into "untested"
clear_status()
SENT.clear()
_calls = {'n': 0}
class FlakyGone(GoneIMAP):
    def __init__(self, seed):
        _calls['n'] += 1
        if _calls['n'] == 1:
            raise OSError('timeout')
        GoneIMAP.__init__(self, seed)
_t = DV.time.time
_clock = {'t': 0}
def _fake_time():
    _clock['t'] += 100
    return _clock['t']
DV.time.time = _fake_time
try:
    DV.run_placement(send=True, wait=250, froms=[B], smtp=fake_smtp, imap=FlakyGone, out=out.append,
                     pace=0, cred=(LOGIN, 'pw'))
finally:
    DV.time.time = _t
st, _ = DV.load_status()
rec('imap error then a clean login: the probe reads missing and holds',
    st and any('seed1@gmail.com' in x and 'missing' in x for x in st['by_from'].get(B, {}).get('reasons', [])),
    (st or {}).get('by_from'))

# a torn status is not replaced by a run that skipped a sender
put_status({}, raw='{"by_from": {')
SENT.clear()
out = []
rc = DV.run_placement(send=True, wait=0, froms=[A], smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0,
                      cred=(LOGIN, 'pw'))
rec('a narrow run leaves a torn status in place', rc == 1 and DV.load_status()[1] != ''
    and any('NOT WRITTEN' in x for x in out), out[-2:])
try:
    st = DV.write_status([{'from': A, 'seed': 's', 'folder': 'inbox'}, {'from': B, 'seed': 's', 'folder': 'inbox'}],
                         0, all_froms=[A, B])
    ok = DV.load_status()[1] == '' and any(f.startswith('placement_status.json.torn-') for f in os.listdir(_TMP))
except Exception as ex:
    ok = ex
rec('a run covering every sender replaces it and keeps the torn copy', ok is True, ok)

# a seed removed from the list stops holding; a non-dict row holds
prev = {A: {'verdict': 'hold', 'per_seed': {'gone@gmail.com': {'reasons': ['spam at external seed gone@gmail.com']},
                                            'seed1@gmail.com': 'junk'}}}
v = DV.verdict_for([{'from': A, 'seed': 'seed1@gmail.com', 'folder': 'inbox'}], prev, 't', seeds=['seed1@gmail.com'])
rec('a removed seed no longer holds; a clean retest of the rest lifts', v[A]['verdict'] == 'clear', v[A])
v = DV.verdict_for([], prev, 't', seeds=['seed1@gmail.com', 'gone@gmail.com'])
rec('an untested address keeps its row', v[A] is prev[A], v[A])
v = DV.verdict_for([{'from': A, 'seed': 'x', 'folder': 'inbox'}], prev, 't', seeds=['seed1@gmail.com', 'x'])
rec('a non-dict per-seed result holds, does not crash', v[A]['verdict'] == 'hold', v[A])
put_status({A: 'hold'})
rec('a non-dict row holds', 'not readable' in DV.placement_hold(A), DV.placement_hold(A))

# dry run sends nothing
SENT.clear()
out = []
rc = DV.run_placement(send=False, froms=[A], smtp=fake_smtp, imap=FakeIMAP, out=out.append, pace=0, cred=(LOGIN, 'pw'))
rec('dry run sends nothing', not SENT and rc == 0 and any('DRY RUN' in x for x in out), out)


print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
