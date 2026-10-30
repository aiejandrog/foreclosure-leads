"""Call Mode's first-email pause card says how many bounced, not the raw reason code (2026-10-26:
the card read 'bounce rate: day_rate'). The pause JSON carries aggregate counts; mailPauseWhy words them."""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode as C
import send_server as S

FAILS = []


def check(name, ok, why=''):
    print(('PASS ' if ok else 'FAIL ') + name + ('' if ok else '  :: ' + str(why)))
    if not ok:
        FAILS.append(name)


S._bounce_health = lambda: {'ft_rule': 'paused', 'ft_why': 'day_rate', 'day_dead': 5, 'day_sent': 42,
                            'day_date': '2026-10-21'}
j = json.loads(C._mail_pause_json())
check('pause json carries rule, why and counts', j == {'rule': 'paused', 'why': 'day_rate', 'dead': 5, 'sent': 42,
                                                     'day': '2026-10-21'}, j)

src = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
m = re.search(r'function mailPauseWhy\(\)\{.*?\n\}\n', src, re.S)
check('mailPauseWhy is in the page', bool(m))


def run(pause):
    js = 'var MAIL_PAUSE=%s;\n%s\nconsole.log(mailPauseWhy());' % (json.dumps(pause), m.group(0))
    return subprocess.run(['node', '-e', js], capture_output=True, text=True).stdout.strip()


out = run({'rule': 'paused', 'why': 'day_rate', 'dead': 5, 'sent': 42, 'day': '2026-10-21'})
check('counts are worded', out == '5 of 42 first emails sent 2026-10-21 bounced (12%).', out)
out = run({'rule': 'paused', 'why': 'day_rate'})
check('no counts: a sentence, never the raw code', 'day_rate' not in out and 'one day' in out, out)
out = run({'rule': 'paused', 'why': 'whatever_new'})
check('unknown reason is kept in brackets after a sentence', out == 'the bounce rate is over the limit (whatever_new).', out)
out = run({'rule': 'paused', 'why': ''})
check('no reason at all: a plain sentence', out == 'the bounce rate is over the limit.', out)
check('card no longer prints MAIL_PAUSE.why raw', "MAIL_PAUSE.why ? ': '" not in src)
print('\nFAILED: %s' % FAILS if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
