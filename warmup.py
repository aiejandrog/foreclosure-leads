#!/usr/bin/env python
"""warmup.py -- two-week send ramp for a brand-new sending alias, to mailboxes we own.

WHY. A domain with zero history that opens with 100 cold sends a day gets throttled or junked by
Gmail, Yahoo and Outlook within days; the reputation systems want to see low, rising, replied-to
volume first. Real warm-up networks (Smartlead, Instantly, Warmup Inbox) do this against thousands
of third-party inboxes and are better than this file. This is the free, honest floor: each new
alias sends a few ordinary messages a day to the company's own mailboxes, the count rises on a
fixed schedule, and a human (or the Gmail connector in a session) replies to some of them.

SCOPE, load-bearing. Recipients are ONLY addresses this company owns. No homeowner, no lead, no
list. This script must never learn to read leads_final.json. It logs to ~/DEALFLOW/warmup_log.json,
not to mail_sent.json, so the outreach ledger stays clean.

Ramp (sends per alias per day): day 1 -> 4, +1 per day, capped at 15 from day 12 on. After day 14
keep running at 15; stop the task when the real cadence moves to the domain.

Run:   python warmup.py            # today's batch (idempotent per calendar day)
       python warmup.py --dry-run  # show what would go out
       python warmup.py --status   # show day number and history
Credential: bsg_gmail.key  (alejandro@bsgflorida.com:<app password>; aliases send through it)
"""
import argparse
import datetime as dt
import json
import os
import random
import smtplib
import ssl
import sys
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(HERE, 'bsg_gmail.key')
LOG = P.out('warmup_log.json')
START_DATE = dt.date(2026, 9, 7)

ALIASES = ['alejandro@biscaynesolutionsgroup.com', 'alejandro@bsgfl.com']
# Company-owned only. Mixed providers on purpose: Workspace, consumer Gmail.
RECIPIENTS = [
    'alejandro@bsgflorida.com', 'jesse@bsgflorida.com', 'carlos@bsgflorida.com',
    'help@bsgflorida.com', 'biscaynesolutionsgroup@gmail.com', 'agonzalez0311707@gmail.com',
]
SENDER_NAME = 'Alejandro Gonzalez'

SUBJECTS = [
    'Quick one about tomorrow', 'Notes from this morning', 'Re: the Miami Lakes file',
    'Following up on the county numbers', 'Two things before the call', 'Checking in',
    'Schedule for this week', 'That report we talked about', 'Re: booking link',
    'Question about the door route', 'Numbers for September', 'Before I forget',
]
BODIES = [
    'Hey,\n\nJust moving this to email so it is written down. Can you look at the calendar for '
    'Thursday and tell me which slot works? I want to keep the morning open for calls.\n\nThanks,\nAlejandro',
    'Hi,\n\nThe September report is done. 965 sales on the calendar across the three counties, '
    '517 inside 30 days. I will send the page link once it is live.\n\nAlejandro',
    'Hello,\n\nReminder that the records review for the Miami Lakes property is at 3. Bring the '
    'notice if you have it handy. Nothing else needed.\n\nAlejandro',
    'Hey,\n\nDid the confirmation email come through on your side? Reply with a yes so I know '
    'the new address is landing where it should.\n\nAlejandro',
    'Hi,\n\nShort one. The door route for Wednesday is Hialeah first, then Miami Lakes. Carlos '
    'has the letters. Let me know if you want to swap the order.\n\nAlejandro',
    'Hello,\n\nI moved the booking link to the top of the page. If you get a minute, open it on '
    'your phone and tell me if the calendar loads fast enough.\n\nThanks,\nAlejandro',
    'Hey,\n\nFor the call list: three people asked to be called back after 5:30. I put them '
    'first. Everything else is in the usual order.\n\nAlejandro',
    'Hi,\n\nNothing urgent. Just confirming the new email address is working from this side. '
    'Reply when you see it.\n\nAlejandro',
]


def _load_log():
    if os.path.exists(LOG):
        try:
            return json.load(open(LOG, encoding='utf-8'))
        except Exception:
            pass
    return {'days': {}}


def _save_log(d):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(d, open(LOG, 'w', encoding='utf-8'), indent=1)


def day_number(today):
    return max(1, (today - START_DATE).days + 1)


def quota(day):
    return min(15, 3 + day)


def creds():
    raw = open(KEY, encoding='utf-8').read().strip().strip('"').strip("'")
    user, pw = raw.split(':', 1)
    user, pw = user.strip(), pw.strip()
    if not user.lower().endswith('@bsgflorida.com'):
        sys.exit('refusing: key is not a bsgflorida.com account')
    return user, pw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()
    today = dt.date.today()
    day = day_number(today)
    log = _load_log()
    key = today.isoformat()
    done = log['days'].get(key, {})
    if a.status:
        print('day %d of ramp, quota %d per alias' % (day, quota(day)))
        for k in sorted(log['days']):
            print(' ', k, {al: len(v) for al, v in log['days'][k].items()})
        return 0
    plan = []
    for alias in ALIASES:
        already = len(done.get(alias, []))
        need = quota(day) - already
        for i in range(max(0, need)):
            rcpt = RECIPIENTS[(already + i + ALIASES.index(alias)) % len(RECIPIENTS)]
            plan.append((alias, rcpt, random.choice(SUBJECTS), random.choice(BODIES)))
    print('day %d, quota %d/alias, sending %d now (%s)' % (day, quota(day), len(plan), 'DRY RUN' if a.dry_run else 'LIVE'))
    for alias, rcpt, subj, _ in plan:
        print('  %s -> %s  | %s' % (alias, rcpt, subj))
    if a.dry_run or not plan:
        return 0
    user, pw = creds()
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=60) as s:
        s.login(user, pw)
        for alias, rcpt, subj, body in plan:
            msg = MIMEText(body, 'plain', 'utf-8')
            msg['Subject'] = subj
            msg['From'] = formataddr((SENDER_NAME, alias))
            msg['To'] = rcpt
            msg['Reply-To'] = alias
            mid = make_msgid(domain=alias.split('@')[1])
            msg['Message-ID'] = mid
            s.sendmail(alias, [rcpt], msg.as_string())
            done.setdefault(alias, []).append({'to': rcpt, 'subj': subj, 'mid': mid,
                                               'ts': dt.datetime.now().isoformat(timespec='seconds')})
            log['days'][key] = done
            _save_log(log)
    print('sent %d; log %s' % (len(plan), LOG))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('warmup failed: %s' % e)
        sys.exit(1)
