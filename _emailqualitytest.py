"""_emailqualitytest.py -- reports/email_quality.py against an invented fixture in a temp dir.
Run: python _emailqualitytest.py   (exit 0 = pass; no network, no real data, example.test only)"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'reports'))
import email_quality as EQ  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (name, got, want))


def w(d, n, o):
    with open(os.path.join(d, n), 'w') as f:
        json.dump(o, f)


with tempfile.TemporaryDirectory() as d:
    w(d, 'leads_final.json', [
        {'case': 'T-1', 'emails': ['a@example.test', 'b@example.test']},
        {'case': 'T-2', 'emails': ['c@example.test', 'not-an-email']},
        {'case': 'T-3', 'emails': ['A@EXAMPLE.TEST']},            # dup of a, case-folded
    ])
    w(d, 'skiptrace_results.json', {
        'T-4': {'emails': ['d@example.test', 'e@example.test'], 'source': 'ProvX'},
        'T-5': {'emails': ['f@example.test']},
    })
    w(d, 'mail_sent.json', [
        {'ch': 'email', 'to': 'a@example.test'},
        {'ch': 'email', 'to': 'x@example.test', 'bcc': 'd@example.test'},
        {'ch': 'email', 'to': 'b@example.test', 'error': 'boom'},      # failed send: not mailed
        {'ch': 'sms', 'to': 'e@example.test'},                         # not email
    ])
    w(d, 'bounced_emails.json', {'b@example.test': {}})
    w(d, 'optouts.json', {'notes': {'@c@example.test': True, 't-4': True, '@zz@example.test': False}})
    w(d, 'verified_emails.json', {'f@example.test': {'v': 'dead'}, 'a@example.test': {'v': 'ok'}})
    st = EQ.collect([d])
    check('unique', st['unique'], 6)                  # a b c d e f
    check('bounced', st['prior_bounces'], 1)          # b
    check('opted', st['opted_out'], 3)                # c by address, d+e by case T-4
    check('vdead', st['verified_dead'], 1)            # f
    check('already_mailed', st['already_mailed'], 2)  # a, d
    check('never_sent', st['never_sent'], 4)
    check('clean', st['clean_sendable'], 1)           # a only
    check('clean_never_sent', st['clean_never_sent'], 0)
    check('src_provx', st['by_source'].get('skiptrace:provx', {}).get('total'), 2)
    check('src_skip', st['by_source'].get('skiptrace', {}).get('total'), 1)
    check('src_leads', st['by_source'].get('leads:leads_final.json', {}).get('total'), 3)
    out = '\n'.join(EQ.render(st))
    check('no address printed', '@' in out, False)
    check('no warning', 'WARNING' in out, False)

with tempfile.TemporaryDirectory() as d:     # empty dir: all missing, loud warning, no crash
    st = EQ.collect([d])
    check('empty unique', st['unique'], 0)
    check('empty warns', 'WARNING' in '\n'.join(EQ.render(st)), True)

with tempfile.TemporaryDirectory() as d:     # torn file reported unreadable
    with open(os.path.join(d, 'optouts.json'), 'w') as f:
        f.write('{nope')
    check('torn', EQ.collect([d])['files']['optouts'], 'unreadable')

if fails:
    print('FAIL\n' + '\n'.join(fails))
    sys.exit(1)
print('PASS _emailqualitytest')
