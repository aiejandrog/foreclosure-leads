"""_mailletterlinestest.py - every mailed letter carries the three federally mandated MARS sentences
and the approved opt-out line (EN + ES), taken from disclaimer.py and outreach_copy.py, not retyped.

bsg_mail_campaign's own docstring said both were in the letter; the body had neither until 2026-10-15.
Offline, synthetic rows only. Also pins that a letter does not silently lose them if the source text moves.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bsg_mail_campaign as B           # noqa: E402
import disclaimer as D                  # noqa: E402
import outreach_copy as OC              # noqa: E402

fails = []


def chk(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


ROW = {'case': 'T-1', 'owner': 'DOE,JANE', 'first': 'Jane', 'addr': '1 NW 2 ST, Miami', 'city': 'Miami',
       'zip': '33100', 'age': 3, 'plaintiff': 'TEST BANK'}
t = B.letter(ROW)
mars = D.mars_part('govt', 'may_agree', 'may_stop', company=B.SENDER['co'], as_html=False)

chk('letter carries the three mandated MARS sentences from disclaimer.py', mars and mars in t)
chk('mars text names the sender company, not a placeholder', '{co}' not in t and B.SENDER['co'] in mars)
chk('letter carries the English opt-out line from outreach_copy', OC.OPTOUT_LINE_EN in t)
chk('letter carries the Spanish opt-out line from outreach_copy', OC.OPTOUT_LINE_ES in t)
chk('opt-out comes after the MARS block and before the signature',
    t.index(mars) < t.index(OC.OPTOUT_LINE_EN) < t.index(B.SENDER['name'], t.index(OC.OPTOUT_LINE_ES)))
chk('the original offer and street sentence are unchanged',
    "I saw the court filing on 1 NW 2 St." in t and "I buy houses in this situation" in t)
chk('printed variant is the same words', B.letter_printed(ROW) == t)
import html as H                      # noqa: E402
book = B.build([ROW], 80, 14, {}, 1)
chk('handwritten book carries them too (HTML-escaped)',
    H.escape(OC.OPTOUT_LINE_EN) in book and H.escape(OC.OPTOUT_LINE_ES) in book and H.escape(mars) in book)

# the detector must not treat our own opt-out sentence, quoted back, as a stop
try:
    import replies
    chk('quoting our opt-out line back is not itself a stop', not replies.is_stop_text(OC.OPTOUT_LINE_EN))
except Exception as e:                                   # replies may need live data on this machine
    print('skip replies check: %s' % str(e)[:80])

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
