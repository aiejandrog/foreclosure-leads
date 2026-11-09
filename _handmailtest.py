"""_handmailtest.py - the hand-mailed letter run (bsg_mail_campaign --board / --touch 2 / --mark-mailed).

What is pinned:
  * the follow-up letter carries the same MARS sentences, opt-out line (EN + ES) and signer as letter 1
  * neither letter promises to stop the sale, save the house, or names a fee or deadline
  * --board selects through outreach_mail.build_selection, so the stay gate, the opt-out ledger and the
    letter ledger hold a lead exactly as they do on the paid path; clients and estates are dropped too
  * the envelope goes to the parsed mailing address, the letter names the property street
  * --mark-mailed writes only mail_letters_sent.json (never optouts.json); touch 2 adds a date
  * touch 2 re-runs every gate on touch 1's cases, so an opt-out in between drops the follow-up
Offline, synthetic rows, temp folder.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bsg_mail_campaign as B           # noqa: E402
import disclaimer as D                  # noqa: E402
import outreach_copy as OC              # noqa: E402
import outreach_mail as OM              # noqa: E402

fails = []


def chk(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


ROW = {'case': 'T-1', 'owner': 'DOE,JANE', 'first': 'Jane', 'addr': '1 NW 2 ST, Miami', 'city': 'Miami',
       'zip': '33100', 'age': 3, 'plaintiff': 'TEST BANK'}
mars = D.mars_part('govt', 'may_agree', 'may_stop', company=B.SENDER['co'], as_html=False)
l1, l2 = B.letter(ROW), B.letter_followup(ROW)
chk('follow-up carries the three MARS sentences', mars in l2)
chk('follow-up carries the EN and ES opt-out lines', OC.OPTOUT_LINE_EN in l2 and OC.OPTOUT_LINE_ES in l2)
chk('follow-up names the street and is signed by the same person', '1 NW 2 St' in l2 and B.SENDER['name'] in l2)
chk('follow-up says it is the one follow-up', 'my one follow-up' in l2)
BAD = ('stop the foreclosure', 'stop your foreclosure', 'stop the sale', 'save your home', 'save your house',
       'guarantee', 'fee', 'deadline', 'act now', 'last chance', 'final notice')
for t, lbl in ((l1, 'letter 1'), (l2, 'follow-up')):
    low = t.lower()
    chk('%s makes no stop-the-sale / save-the-house promise, fee or deadline' % lbl,
        not any(b in low for b in BAD))
B.TOUCH = 2
chk('--touch 2 switches the printed body', B.body(ROW) == l2)
B.TOUCH = 1
chk('touch 1 prints letter()', B.body(ROW) == l1)

# ---------------------------------------------------------------- board selection, in a temp folder
tmp = tempfile.mkdtemp(prefix='handmail_')
leads = [
    {'case': 'OK-1', 'owners': 'SMITH,ANA', 'addr': '10 SW 3 AVE', 'mail': '77 OTHER ST, HOMESTEAD, FL 33030',
     'days': 30, 'tier': 'A'},
    {'case': 'OK-2', 'owners': 'LOPEZ,LUIS', 'addr': '20 NE 4 ST', 'mail': '20 NE 4 ST, MIAMI, FL 33101',
     'days': 15, 'tier': 'B'},
    {'case': 'OPT-1', 'owners': 'OPT,OUT', 'addr': '1 A ST', 'mail': '1 A ST, MIAMI, FL 33101', 'days': 30, 'tier': 'A'},
    {'case': 'STAY-1', 'owners': 'HELD,HAL', 'addr': '2 B ST', 'mail': '2 B ST, MIAMI, FL 33101', 'days': 30, 'tier': 'A'},
    {'case': 'SENT-1', 'owners': 'DONE,DAN', 'addr': '3 C ST', 'mail': '3 C ST, MIAMI, FL 33101', 'days': 30, 'tier': 'A'},
    {'case': 'SOON-1', 'owners': 'SOON,SAM', 'addr': '4 D ST', 'mail': '4 D ST, MIAMI, FL 33101', 'days': 8, 'tier': 'A'},
    {'case': 'EST-1', 'owners': 'ESTATE OF ROE,RICK', 'addr': '5 E ST', 'mail': '5 E ST, MIAMI, FL 33101', 'days': 30, 'tier': 'A'},
    {'case': 'CLI-1', 'owners': 'CLIENT,CARA', 'addr': '6 F ST', 'mail': '6 F ST, MIAMI, FL 33101', 'days': 30, 'tier': 'A'},
]
json.dump(leads, open(os.path.join(tmp, 'leads_final.json'), 'w'))
json.dump({'OPT-1': {'optout': True}}, open(os.path.join(tmp, 'optouts.json'), 'w'))
ledger = os.path.join(tmp, 'mail_letters_sent.json')
json.dump({'SENT-1': {'date': '2026-01-01'}}, open(ledger, 'w'))

import bk_lookup as BKL                 # noqa: E402
import clients as CL                    # noqa: E402
import diligence_gate as DG             # noqa: E402
_orig = (OM.HERE, OM.SENT_LEDGER, BKL.contact_blocked_reason, CL.is_client, DG.gate, B.P.out)
OM.HERE, OM.SENT_LEDGER = tmp, ledger
BKL.contact_blocked_reason = lambda c, here=None: (c == 'STAY-1', 'stay' if c == 'STAY-1' else '')
CL.is_client = lambda r, path=None: (r.get('case') if isinstance(r, dict) else r) == 'CLI-1'
DG.gate = lambda r: {'hold': False, 'unchecked': False, 'code': '', 'why': '', 'warn': False}
B.P.out = lambda name: os.path.join(tmp, 'out', name)
try:
    rows, drops = B.pool_board(None, B.BOARD_MIN_DAYS)
    got = [r['case'] for r in rows]
    chk('board run keeps only the clean leads, soonest sale first', got == ['OK-2', 'OK-1'])
    chk('opt-out ledger drops a lead', 'OPT-1' not in got)
    chk('stay gate drops a lead', 'STAY-1' not in got and drops.get('bankruptcy-stay-check') == 1)
    chk('letter ledger drops an already-mailed lead', 'SENT-1' not in got)
    chk('a sale inside 11 days is not mailed', 'SOON-1' not in got)
    chk('estates and clients are dropped', 'EST-1' not in got and 'CLI-1' not in got)
    ok1 = [r for r in rows if r['case'] == 'OK-1'][0]
    nm, a1, a2 = B.env_lines(ok1)
    chk('envelope goes to the mailing address', a1 == '77 Other St' and a2.startswith('Homestead, FL 33030'))
    chk('letter names the property street, not the mailing address', '10 SW 3 Ave' in B.letter(ok1))
    chk('envelope name reads First Last', nm == 'Ana Smith')
    book = B.build(rows, 80, 14, drops, len(rows))
    chk('handwrite book renders board rows with the sale clock', 'sale in 15 days' in book)

    mp = B.write_manifest(rows, 1)
    man = json.load(open(mp))
    chk('manifest carries case numbers only', man['cases'] == ['OK-2', 'OK-1'] and 'owners' not in json.dumps(man))
    opt_before = open(os.path.join(tmp, 'optouts.json')).read()
    B.mark_mailed(mp)
    led = json.load(open(ledger))
    chk('--mark-mailed ledgers touch 1', led['OK-1']['src'] == 'hand' and led['OK-1']['touch'] == 1)
    chk('--mark-mailed never touches optouts.json', open(os.path.join(tmp, 'optouts.json')).read() == opt_before)
    chk('a second board run does not re-mail them', B.pool_board(None, B.BOARD_MIN_DAYS)[0] == [])

    # opt-out between touches: OK-2 says stop
    json.dump({'OPT-1': {'optout': True}, 'OK-2': {'status': 'DO NOT CONTACT'}},
              open(os.path.join(tmp, 'optouts.json'), 'w'))
    rows2, _ = B.pool_board(None, B.FOLLOWUP_MIN_DAYS, cases=man['cases'], remail=True)
    chk('touch 2 re-checks: an opt-out after letter 1 drops the follow-up', [r['case'] for r in rows2] == ['OK-1'])
    json.dump({'touch': 2, 'printed': man['printed'], 'cases': ['OK-1']}, open(mp + '.2', 'w'))
    B.mark_mailed(mp + '.2')
    led = json.load(open(ledger))
    chk('touch 2 adds a date and keeps the touch 1 entry', led['OK-1'].get('touch2') and led['OK-1']['src'] == 'hand')

    open(ledger, 'w').write('{torn')
    try:
        B.pool_board(None, B.BOARD_MIN_DAYS)
        chk('an unreadable letter ledger stops the run', False)
    except SystemExit:
        chk('an unreadable letter ledger stops the run', True)
finally:
    OM.HERE, OM.SENT_LEDGER, BKL.contact_blocked_reason, CL.is_client, DG.gate, B.P.out = _orig

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
