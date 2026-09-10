"""No outreach email leaves with a hole in it.

WHY THIS EXISTS
Two messages that actually reached homeowners, found by reading the Sent folder on 2026-09-10:

  2026-09-07   Subject: "Re: Regarding your property at"
               Body:    "Hi Figueroa, This is my last note about . If the case is already handled..."
  2026-08-01   Body:    "Hi Milagros, My name is [YOUR NAME]. I am a local Miami home buyer..."

Neither is a bounce or a delivery failure. Both are a SUCCESSFUL send of a broken message, so every
existing check — SMTP result, the ledger, the daily cap, the bounce list — reported success. The
only place the defect existed was in the words the owner read.

That matters more here than in most products. These people are in foreclosure and are already being
buried in rescue-scam mail. A letter about no address at all, signed by nobody, is that silhouette
exactly, and the entire outreach posture ("I am not your lender, not the government, not a rescue
company") is credibility we spend one message at a time.

THE TWO ROOT CAUSES, fixed upstream and re-asserted here:
  1. `addr or 'your property'` ran BEFORE the address was cleaned. A whitespace- or comma-only
     address is TRUTHY, so the fallback never fired, and the cleaner's own `if p` filter then
     dropped every part and returned ''. safe_addr() decides AFTER cleaning.
  2. `snd.get('name') or '[YOUR NAME]'` is a fine default for a board preview and is never a
     thing to transmit.

AND THE THIRD BUG FOUND WITH THEM: cadence.py advanced each lead's step in MEMORY and wrote
cadence_state.json once, after the loop. A run that died, was killed, or hit an SMTP error threw
away the record of every mail it had already sent, and the next run re-sent them. One owner got the
identical step-2 follow-up on 2026-08-25 16:14 and again on 2026-08-26 12:39. State is written after
every send now, atomically.

Run: python _mailguardtest.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mail_guard as G  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  — ' + str(detail)[:130]) if detail else ''))


# ---- 1. the two messages that actually shipped -------------------------------------------------
print('\nTHE REAL ONES')
real_bad = [
    ('the 09-07 blank address',
     'Re: Regarding your property at',
     'Hi Figueroa, This is my last note about . If the case is already handled, ignore this and '
     'I truly wish you well. If it is not, the door stays open.'),
    ('the 08-01 unfilled name',
     'Regarding your property at 1101 SW 122 AVE 307',
     'Hi Milagros, My name is [YOUR NAME]. I am a local Miami home buyer -- I buy houses.'),
]
for name, subj, body in real_bad:
    bad = G.check(subj, body, 'owner@example.com')
    rec('REFUSED: ' + name, bool(bad), (bad or ['NOT CAUGHT'])[0])

# ---- 2. real messages that must still go out ---------------------------------------------------
print('\nTHE GOOD ONES (verbatim from the Sent folder)')
real_ok = [
    ('Re: Regarding your property at 13950 SW 276 ST',
     'Hi Gamboa, This is my last note about 13950 SW 276 ST. If the case is already handled, '
     'ignore this and I truly wish you well. If it is not, the door stays open. Our senior '
     'advisor will still go over your options.'),
    ('Sale date 09/22/2026 - Regarding your property at 12535 SW 33 ST',
     'Hi Lim, Alejandro Gonzalez with Biscayne Solutions Group LLC again. I am not your lender, '
     'not the government, not a foreclosure-rescue company, and not an attorney. I wrote you a '
     'few days back about 7921 EAST DR 6.'),
    ('3 options before 09/22/2026 for 2081 UTOPIA DR',
     'Hi Manuel, Which one fits comes down to your numbers. I can walk you through all three '
     'against your actual property at 2081 UTOPIA DR, and it costs you nothing.'),
    # a hand-typed reply from the board — these go through the same choke point and must not trip
    ('Re: Regarding your property at 1533 W RIVER DR',
     'Hey Toni, no worries at all, hope you are doing ok. Sounds like today was a lot. I do not '
     'want to pile on but your sale date is Wednesday so we are on the clock either way. '
     'The payoff came to US$ 240,000 and the offer is $175,000. Are you free Thursday?'),
]
for subj, body in real_ok:
    bad = G.check(subj, body, 'owner@example.com')
    rec('SENDS: ' + subj[:52], not bad, bad)

# ---- 3. the classes, exhaustively --------------------------------------------------------------
print('\nEVERY HOLE')
holes = [
    ('unfilled name', 'Subject here', 'Hi, My name is [YOUR NAME].'),
    ('unfilled company', 'Subject here', 'I am with [YOUR COMPANY] and I buy houses.'),
    ('unfilled phone', 'Subject here', 'Call me at [YOUR PHONE] any time.'),
    ('unrendered slot', 'About {addr} before the auction', 'Following up on {addr}.'),
    ('printf slot', 'Subject here', 'Public records show %s is in foreclosure.'),
    ('empty value mid-sentence', 'Subject here', 'This is my last note about . The date is close.'),
    ('empty value after "at"', 'Subject here', 'your actual property at , and it costs nothing.'),
    ('subject ends on a preposition', 'Regarding your property at', 'A perfectly fine body here.'),
    ('empty money', 'Subject here', 'We can offer $. for the property.'),
    ('empty subject', '', 'A perfectly fine body here.'),
    ('empty body', 'A fine subject', '   '),
]
for name, subj, body in holes:
    rec(name, bool(G.check(subj, body, 'o@e.com')), (G.check(subj, body, 'o@e.com') or ['NOT CAUGHT'])[0])
rec('a bad recipient is refused', bool(G.check('s', 'b', 'not-an-address')))

# ---- 4. safe_addr never returns empty ----------------------------------------------------------
print('\nsafe_addr / safe_street')
for raw in ('', '   ', ',', ' , , ', ' - ', None, 0, [], '  ,  ,  '):
    a, st = G.safe_addr(raw), G.safe_street(raw)
    rec('degenerate %r -> a real string' % (raw,), a == 'your property' and st == 'your property',
        '%r / %r' % (a, st))
rec('the board format is tidied, not mangled',
    G.safe_addr('455 NE 210 TER, MIAMI, FL- 33179') == '455 NE 210 TER, Miami, FL 33179',
    G.safe_addr('455 NE 210 TER, MIAMI, FL- 33179'))
rec('street is the first line only', G.safe_street('455 NE 210 TER, MIAMI, FL- 33179') == '455 NE 210 TER',
    G.safe_street('455 NE 210 TER, MIAMI, FL- 33179'))
rec('a one-line address survives whole', G.safe_addr('13950 SW 276 ST') == '13950 SW 276 ST')
# the exact shape that produced the 09-07 send
rec('THE 09-07 INPUT: a rendered-empty address can no longer reach a template',
    G.safe_addr(' , ') == 'your property' and G.safe_street(' , ') == 'your property')

# ---- 5. the guard is actually wired into every send path ---------------------------------------
print('\nWIRING')
for mod in ('outreach_email.py', 'send_server.py'):
    src = io.open(os.path.join(HERE, mod), encoding='utf-8').read()
    body = src.split('def _smtp_send(', 1)[1] if 'def _smtp_send(' in src else ''
    head = body.split('sender = (from_addr or user)', 1)[0]
    rec('%s: _smtp_send refuses before it transmits' % mod, '_MG.assert_sendable(' in head)
src_c = io.open(os.path.join(HERE, 'cadence.py'), encoding='utf-8').read()
src_o = io.open(os.path.join(HERE, 'outreach_email.py'), encoding='utf-8').read()
rec('cadence builds its address through safe_addr', '_MG.safe_addr(lead.get(\'addr\'))' in src_c)
rec('cadence builds its subject through safe_street', '_MG.safe_street(' in src_c)
rec('outreach_email builds both through the shared helpers',
    '_MG.safe_addr(' in src_o and '_MG.safe_street(' in src_o)
rec('the old fallback-before-clean is gone from cadence',
    "addr = lead.get('addr') or 'your property'" not in src_c)

# ---- 6. cadence remembers a send the moment it happens -----------------------------------------
print('\nDUPLICATE SENDS')
rec('state is written after every send, not once at the end',
    src_c.count('_save_state(state)') >= 2, src_c.count('_save_state(state)'))
_loop = src_c.split('sent += 1', 1)[1].split('for a, n in sorted(capped', 1)[0] if 'sent += 1' in src_c else ''
rec('...and that write is INSIDE the send loop', '_save_state(state)' in _loop)
rec('the write is atomic (tmp + replace)',
    'os.replace(tmp, STATE)' in src_c and "STATE + '.tmp'" in src_c)
rec('a failed state write is announced, not swallowed',
    'WAS EMAILED but cadence_state.json could not be written' in src_c)
rec('the old single end-of-run dump is gone',
    "json.dump(state, open(STATE, 'w', encoding='utf-8'), indent=1)" not in src_c)

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
