"""bsg_mail_campaign --from-route (2026-11-11): letters for Call Mode's mail list print on the laptop and
every check runs again at print time. Pins: only open route k 'mail' leads; opt-out ledger case and phone
keys; the person's note (DNC, hard / soft no, wrong owner, Dead, attorney, reached after routing, booking
open, already lettered); verified equity; 7-day runway; stay and diligence holds; the print ledger; absentee
owners go to the mailing address while the body names the property; the letter carries the MARS lines and
the opt-out and no dollar or equity figure; the Lob queue keeps outreach_mail's shape; the run refuses with
no opt-out ledger or no signer. Invented leads only, temp DEALFLOW_DIR.
"""
import datetime
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = TMP
sys.path.insert(0, HERE)
import bsg_mail_campaign as B  # noqa: E402
import outreach_copy as OC  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def mdy(days):
    d = datetime.date.today() + datetime.timedelta(days=days)
    return '%02d/%02d/%d' % (d.month, d.day, d.year)


EQ = {'state': 'clear', 'fact': True, 'may_quote': True, 'net': 40000}


def L(case, **k):
    x = {'case': case, 'paOwner': 'GOMEZ,ANA', 'addr': '100 NW 1st Ave, MIAMI, FL 33128',
         'mail': '100 NW 1st Ave, MIAMI, FL 33128', 'auction': mdy(25), 'eqc': EQ, 'phones': ['3055550100']}
    x.update(k)
    return x


R = {'k': 'mail', 'd': '2026-11-11', 'ts': 1000, 'by': 'Carlos', 'auto': 'na', 'mc': 1}
LEADS = [L('OK1'), L('ABS1', mail='9 Elm Rd, ORLANDO, FL 32801'), L('SOON', auction=mdy(3)), L('NOEQ', eqc={'may_quote': False}),
         L('OPT'), L('OPTPH', phones=['3055550199']), L('DNC'), L('HARD'), L('ATTY'), L('REACH'), L('LET'), L('STAY'),
         L('DILI'), L('PRINTED'), L('LLC', paOwner='ACME HOLDINGS LLC'), L('NOADDR', addr='', mail=''), L('DONE'), L('KNOCK')]
NOTES = {c: {'route': dict(R)} for c in ('OK1', 'ABS1', 'SOON', 'NOEQ', 'OPT', 'OPTPH', 'STAY', 'DILI', 'PRINTED', 'LLC', 'NOADDR', 'GONE')}
NOTES.update({
    'DNC': {'route': dict(R), 'status': 'DO NOT CONTACT'},
    'HARD': {'route': dict(R), 'no': 'hard'},
    'ATTY': {'route': dict(R), 'atty': '2026-11-01'},
    'REACH': {'route': dict(R), 'dials': [{'oc': 'talked', 'tsu': 2000}]},
    'LET': {'route': dict(R), 'touches': [{'ch': 'letter'}]},
    'DONE': {'route': dict(R, done='2026-11-10', dk='letter')},
    'KNOCK': {'route': dict(R, k='knock')},
    '#3055550100': {'optout': '2026-11-01'},
})
OPT = {'OPT', '#3055550199'}
B.SENDER.update({'name': 'Test Signer', 'phone': '(305) 555-0000', 'co': 'Test Co'})
rows, held = B.route_selection(NOTES, LEADS, OPT, {'PRINTED': '2026-11-10'},
                               stay=lambda c: c == 'STAY',
                               dg=lambda lead: {'hold': lead['case'] == 'DILI', 'code': 'title'})
got = sorted(r['case'] for r in rows)
H = dict(held)
rec('printed only open, clean mail routes', got == ['ABS1', 'OK1'], got)
for c, w in [('SOON', 'too late'), ('NOEQ', 'equity not verified'), ('OPT', 'opted out'), ('OPTPH', 'opted out'),
             ('DNC', 'do not contact'), ('HARD', 'hard no'), ('ATTY', 'attorney'), ('REACH', 'reached'),
             ('LET', 'already mailed'), ('STAY', 'stay'), ('DILI', 'diligence'), ('PRINTED', 'already printed'),
             ('LLC', 'entity'), ('NOADDR', 'no parseable'), ('GONE', 'not in the lead files')]:
    rec('held %s: %s' % (c, w), w in H.get(c, ''), H.get(c))
rec('done and knock routes are not listed at all', 'DONE' not in H and 'KNOCK' not in H and 'DONE' not in got)
a = [r for r in rows if r['case'] == 'ABS1'][0]
rec('absentee: envelope goes to the mailing address', a['to_line1'] == '9 Elm Rd' and a['city'] == 'ORLANDO' and a['zip'] == '32801', a)
body = B.letter(a)
rec('absentee: the letter body still names the property', '100 NW 1ST Ave' in body and 'Elm' not in body, body[:200])
rec('letter carries the opt-out line EN and ES', OC.OPTOUT_LINE_EN in body and OC.OPTOUT_LINE_ES in body)
rec('letter carries no dollar figure and no equity word', '$' not in body and 'equity' not in body.lower())
html = B.build_route(rows, held)
rec('hand-write book: absentee envelope labelled', "OWNER'S MAILING ADDRESS" in html and '9 Elm Rd' in html)
rec('hand-write book: no em dash', '\u2014' not in html)
q = B.route_queue(rows)
rec('Lob queue keeps outreach_mail shape', q['_dealflow_mailqueue'] == 1 and len(q['leads']) == 2
    and all('case' in x and 'mail' in x for x in q['leads']), q)
try:
    import outreach_mail as OM
    fd, qp = tempfile.mkstemp(suffix='.json', dir=TMP)
    os.close(fd)
    json.dump(q, open(qp, 'w', encoding='utf-8'))
    ld, snd = OM.load_queue(qp)
    rec('outreach_mail.load_queue reads it', len(ld) == 2 and snd == {})
except Exception as e:
    rec('outreach_mail.load_queue reads it', False, repr(e))

# run_from_route refuses without a ledger or a signer, writes nothing into the repo
B.SENDER.update({'name': '', 'phone': ''})
B.MAIL_TRACKING_PHONE = ''
rec('refuses with no signer', B.run_from_route() == 2)
B.SENDER.update({'name': 'Test Signer', 'phone': '(305) 555-0000'})
_orig = B._optout_keys
B._optout_keys = lambda: set()
rec('refuses with an empty opt-out ledger', B.run_from_route() == 2)
B._optout_keys = _orig
rec('no literal identity default left in the module', "SENDER = {'name': '', 'phone': ''}" in io.open(
    os.path.join(HERE, 'bsg_mail_campaign.py'), encoding='utf-8').read())

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
