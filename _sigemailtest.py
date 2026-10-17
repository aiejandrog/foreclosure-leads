"""The signature contact line is the business inbox, never the retired personal Gmail.

outreach_email, cadence follow-ups, and letterheads that show an email all print
outreach_copy.signature_email(). A blank and the retired Gmail both render
alejandro@bsgflorida.com. An operator who saved a different address keeps it.
The From address is not this line.

Run: python _sigemailtest.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cadence as C                 # noqa: E402
import outreach_copy as OC          # noqa: E402
import outreach_email as OE         # noqa: E402
import outreach_mail as OM          # noqa: E402

BUSINESS = 'alejandro@bsgflorida.com'
RETIRED = 'agonzalez0311707@gmail.com'
CUSTOM = 'ops@example.com'

fails = []


def chk(name, cond, got=''):
    print(('ok   ' if cond else 'FAIL ') + name + (('  ' + got) if (got and not cond) else ''))
    if not cond:
        fails.append(name)


def no_gmail(label, text):
    chk(label + ' shows ' + BUSINESS, BUSINESS in text)
    chk(label + ' never shows the retired Gmail', RETIRED not in text.lower())


# --- the one rule -----------------------------------------------------------
chk('blank becomes the business inbox', OC.signature_email('') == BUSINESS)
chk('missing becomes the business inbox', OC.signature_email(None) == BUSINESS)
chk('retired Gmail, any case or space, becomes the business inbox',
    OC.signature_email('  ' + RETIRED.upper() + '  ') == BUSINESS)
chk('a different saved address is left alone', OC.signature_email(CUSTOM) == CUSTOM)

# --- outreach_email signature (cold, follow, portfolio all use _sig) --------
for label, snd in (
    ('outreach email, no email key', {}),
    ('outreach email, blank email', {'email': '   ', 'name': 'Alejandro Gonzalez'}),
    ('outreach email, retired Gmail', {'name': 'Alejandro Gonzalez', 'email': RETIRED,
                                       'phone': '(786) 631-1823'}),
):
    no_gmail(label, OE._sig(snd))
kept = OE._sig({'name': 'Alejandro Gonzalez', 'email': CUSTOM})
chk('outreach email keeps a saved address that is not the Gmail', 'Email: ' + CUSTOM in kept)
chk('that saved address is not rewritten to the business inbox', BUSINESS not in kept)

# --- cadence follow-ups ------------------------------------------------------
lead = {'owner': 'Jane Doe', 'addr': '1 MAIN ST, MIAMI, FL 33101', 'auction': '2026-11-01'}
for label, snd in (
    ('cadence, no email', {'name': 'Alejandro Gonzalez', 'phone': '(786) 631-1823'}),
    ('cadence, retired Gmail', {'name': 'Alejandro Gonzalez', 'email': ' ' + RETIRED + ' '}),
):
    bodies = C.steps(lead, snd)
    chk(label + ' renders four touches', len(bodies) == 4)
    for subj, body in bodies:
        no_gmail(label + ' / ' + subj[:24], body)
kept_steps = C.steps(lead, {'name': 'Alejandro Gonzalez', 'email': CUSTOM})
chk('cadence keeps a saved address that is not the Gmail',
    all(CUSTOM in body and BUSINESS not in body and RETIRED not in body.lower()
        for _s, body in kept_steps))

# --- letters that show an email (letterhead + footer) -----------------------
for label, snd in (
    ('letterhead, no email', {'name': 'Alejandro Gonzalez'}),
    ('letterhead, retired Gmail', {'name': 'Alejandro Gonzalez', 'email': RETIRED}),
):
    no_gmail(label + ' header', OM._lh_header(snd))
    no_gmail(label + ' footer', OM._lh_sign(snd))
custom_head = OM._lh_header({'name': 'Alejandro Gonzalez', 'email': CUSTOM})
chk('letterhead keeps a saved address that is not the Gmail', CUSTOM in custom_head and BUSINESS not in custom_head)

# --- tracked defaults --------------------------------------------------------
tpl = json.load(open(os.path.join(HERE, 'sender.json.template'), encoding='utf-8'))
chk('sender.json.template email is the business inbox', tpl.get('email') == BUSINESS)
board = open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
chk('board default signature email is the business inbox',
    "email:'%s'" % BUSINESS in board)
# The heal regex escapes the dot, so the raw address is not a contiguous string in the file.
chk('board still drops a saved copy of the retired Gmail',
    'agonzalez0311707@gmail\\.com' in board and 'delete saved.email' in board)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
