"""bk_said.py: a reply that says bankruptcy puts the case on never_contact.json, and the stay gate reads it."""
import json, os, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['DEALFLOW_DIR'] = tempfile.mkdtemp(prefix='bksaid-')
import bk_said as B
import stay_gate as SG

ok = []; bad = []
def rec(n, c, d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))

HOLD = ['We filed chapter 13 last week, our attorney said not to talk to anyone',
        'I am in bankruptcy', 'my bankruptcy attorney will call you', 'Talk to my BK attorney',
        'estoy en bancarrota', 'Me declaré en quiebra', 'ya radicamos el capítulo 13',
        'Case 25-12345-RAM was filed', 'there is an automatic stay', 'Chapter 7 discharged',
        'No thanks. We filed bankruptcy.', 'I did not call you. We are in chapter 13.']
PASS = ['Not interested', 'Can you stop the foreclosure?', 'call me tomorrow',
        'We never filed bankruptcy', "I don't want to file bankruptcy", 'no bankruptcy here',
        'no estoy en bancarrota', 'nunca me declaré en quiebra', 'Case 2025-012345-CA-01',
        'If now is not a good time, just tell me']
for t in HOLD:
    rec('holds: ' + t, B.says_bankruptcy(t))
for t in PASS:
    rec('passes: ' + t, not B.says_bankruptcy(t))

d = os.environ['DEALFLOW_DIR']
nc = os.path.join(d, 'never_contact.json')
st = os.path.join(d, 'bk_said_state.json')
rp = os.path.join(d, 'replies.json')
replies = {
    '2099-000123-CA-01': {'email': 'a@x.com', 'subject': 'Re: Regarding your property', 'when': 'Mon, 1 Oct',
                          'excerpt': 'We filed chapter 13, please stop', 'stop': False},
    '@a@x.com': {'email': 'a@x.com', 'excerpt': 'We filed chapter 13, please stop', 'when': 'Mon, 1 Oct'},
    'CACE-99-012345': {'email': 'b@x.com', 'excerpt': 'call me tomorrow', 'when': 'Tue'},
    '@c@x.com': {'email': 'c@x.com', 'excerpt': 'estoy en bancarrota', 'when': 'Wed'},
    'CASE': {'email': 'd@x.com', 'excerpt': 'bankruptcy', 'when': 'Thu'},
}
json.dump(replies, open(rp, 'w'))
lines = []
c = B.from_replies(rp, apply=False, state_path=st, nc_path=nc, out=lines.append)
rec('dry run writes nothing', not os.path.exists(nc) and not os.path.exists(st), c)
c = B.from_replies(rp, apply=True, state_path=st, nc_path=nc, out=lines.append)
rec('one case added, unkeyable refused, address with no case reported',
    c['added'] == 1 and c['refused'] == 1 and c['no_case'] == 1, c)
rec('file is a list stay_gate reads', SG.never_contact('2099-000123-CA-01', nc) and
    not SG.never_contact('CACE-99-012345', nc))
rec('built-in never-contact cases still read', SG.never_contact('2025-000201', nc))

# A person clears it by hand; the same reply does not put it back. A new reply does.
json.dump([], open(nc, 'w'))
c = B.from_replies(rp, apply=True, state_path=st, nc_path=nc, out=lines.append)
rec('hand-cleared case stays cleared for the same reply', not SG.never_contact('2099-000123-CA-01', nc)
    and c['seen_before'] == 1, c)
replies['2099-000123-CA-01']['when'] = 'Fri, 5 Oct'
json.dump(replies, open(rp, 'w'))
c = B.from_replies(rp, apply=True, state_path=st, nc_path=nc, out=lines.append)
rec('a new bankruptcy reply holds it again', SG.never_contact('2099-000123-CA-01', nc), c)

# Existing entries are kept; a torn file is never replaced.
json.dump(['2024-008515-CA-01'], open(nc, 'w'))
B.add_never_contact(['CACE-25-009539'], nc)
rec('existing entries kept', json.load(open(nc)) == ['2024-008515-CA-01', 'CACE-25-009539'])
open(nc, 'w').write('[ "2024-0')
c = B.from_replies(rp, apply=True, state_path=os.path.join(d, 's2.json'), nc_path=nc, out=lines.append)
rec('torn never_contact.json is left as is', open(nc).read() == '[ "2024-0' and c['error'], c)
try:
    SG.never_contact('2099-000999', nc); raised = False
except SG.NeverContactError:
    raised = True
rec('and the gate still refuses on it', raised)

# Call Mode: "already filed" ends the call as Do Not Contact; "thinking about it" keeps card 6.
import call_mode as CM
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'call_mode.py'), encoding='utf-8').read()
rec('Call Mode q4 has a filed-bankruptcy branch to end:dnc', "'Already filed bankruptcy', 'end:dnc'" in src)
rec('Call Mode q4 keeps card 6 only for planning to file', "'Planning to file bankruptcy', 'obj:6'" in src)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
