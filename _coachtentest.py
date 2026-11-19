"""Call Coach 11-19: the never-say filter, the core/situational prompt split, the token budget, the re-ask
rules, the reply-size and usage logging, and the read-aloud speaker fix. Offline only.

The model and the network are faked (ai_line's _ai_post, or urllib.request.urlopen), keys are dummies, and the
playbook is a small invented note written to a temp folder, so no paid API is ever called and nothing private
is in this file. Invented lines only.
"""
import io
import json
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix='coachten_')
PB = os.path.join(TMP, 'pb.md')
with open(PB, 'w', encoding='utf-8') as f:
    f.write('---\ndate: x\n---\n\n# For people\nNOT-FOR-THE-MODEL\n\n---\n\n'
            '## What this call sells\nSELLS-MARKER five minutes with the senior advisor.\n\n'
            'Who we are, if asked: WHOWE-MARKER an independent consulting company.\n\n'
            '## Never say\n'
            '- A promised outcome or date. "Options," never "we\'ll save your home." Never "we stop the sale";\n'
            '  it is "ask the court for more time."\n'
            '- Any figure about THIS homeowner. The general figures in "The numbers" section are allowed.\n'
            '- "My supervisor," "my clients," "our attorneys," or "the lender pays us". The answer to "how do you\n'
            '  get paid" is the one under "Nobody works for free" below.\n'
            '- "Are you interested in selling." See "Questions that shut them down" below.\n\n'
            '## How the caller talks\n- TALKS-MARKER slow, one point per sentence.\n\n'
            '## Objections: CIOC\nCIOC-MARKER cushion, isolate, overcome, close.\n\n'
            '## The objections and the move for each\n'
            '- "I have an attorney / my lawyer is handling it." ATTY-MARKER keep him.\n'
            '- "I\'m busy / call me later." BUSY-MARKER lock a time.\n'
            '- "Let me talk to my wife." WIFE-MARKER bring her a list.\n\n'
            '## Questions that open them up (NEPQ order)\n1. Situation: QUESTIONS-MARKER what do you want to happen?\n\n'
            'Probes when they go vague: PROBES-MARKER what do you mean by that?\n\n'
            'After a yes: AFTERYES-MARKER save my number and read it back.\n\n'
            '## Questions that shut them down, and what to ask instead\nSHUT-MARKER never ask for a yes or no.\n\n'
            '## The numbers (said in words)\n- NUMBERS-MARKER the payment can go up thirty to fifty percent.\n'
            '- Catching it up is usually somewhere between five and fifteen thousand.\n\n'
            '## Witty ways to say it\nWIT-MARKER not the bank, not a buyer.\n\n'
            '## Sharp lines (one per call)\nSHARP-MARKER we are not picking wallpaper here.\n\n'
            '## A section the code does not know\nNEWSECTION-MARKER his own new notes.\n')
os.environ['VENICE_API_KEY'] = 'test-not-a-real-key'
os.environ['DEEPGRAM_API_KEY'] = 'test-not-a-real-key'
os.environ['COACH_PLAYBOOK'] = PB
os.environ['COACH_AI_URL'] = 'http://127.0.0.1:9/never-called'
sys.path.insert(0, HERE)
import call_coach_server as cs  # noqa: E402

bad = []


def chk(c, m, extra=None):
    print(('ok   ' if c else 'FAIL ') + m + ('' if c or extra is None else '  | %s' % (extra,)))
    if not c:
        bad.append(m)


def reply(say, kind='answer', step='situation', heard='x'):
    return 'HEARD: %s\nTYPE: %s\nSTEP: %s\nSAY: %s' % (heard, kind, step, say)


class Model:
    """Stands in for _ai_post: hands out queued replies and keeps every request body."""
    def __init__(self, *outs, finish=''):
        self.outs, self.bodies, self.finish = list(outs), [], finish

    def __call__(self, body, key, st):
        self.bodies.append(body)
        st['tries'] = st.get('tries', 0) + 1
        st['finish'] = self.finish
        return self.outs.pop(0) if self.outs else ''


def run(lines, model, asked=None, shown='', lane='auction'):
    st, err = {}, io.StringIO()
    with mock.patch.object(cs, '_ai_post', model), mock.patch.object(cs, 'venice_key', lambda: 'k'), \
            mock.patch.object(cs.sys, 'stderr', err):
        res = cs.ai_line(lines, '', shown, asked or [], 7, lane, '', stat=st)
    return res, st, err.getvalue()


T = lambda t, spk='': {'spk': spk, 'text': t}

# ------------------------------------------------------------------ 1. the never-say filter
HITS = [
    ('We will save your home, I promise. How does ten tomorrow sound?', 'promise'),
    ("He'll stop the sale for you.", 'promise'),
    ('Our advisor can definitely keep you in your house.', 'promise'),
    ('We stop the sale, that is what we do.', 'promise'),
    ('I guarantee he finds you something.', 'guarantee'),
    ("I'm calling from the bank about your loan.", 'authority'),
    ("We're with your lender's attorneys.", 'authority'),
    ('We represent the court on these.', 'authority'),
    ('The lender pays us, so it is free for you.', 'pay'),
    ('He gets a referral fee from the attorney.', 'pay'),
    ('Let me check with my supervisor.', 'insider'),
    ('Our attorneys look at every case.', 'insider'),
    ("We've got over thirty years doing this.", 'insider'),
    ('We place loans for people like you.', 'we_lend'),
    ('You should file bankruptcy before the date.', 'legal_advice'),
    ('You have a strong case, honestly.', 'legal_advice'),
    ('My advice is to answer the complaint.', 'legal_advice'),
    ('Just stop paying the bank for now.', 'stop_paying'),
    ('Ignore the letters, they are noise.', 'stop_paying'),
    ('It would cost you about five thousand dollars.', 'figure'),
    ('They usually take ten percent.', 'figure'),
    ('So are you interested in selling?', 'playbook'),
]
ALLOWED = [
    'He can ask the court for more time while your plan plays out.',
    'He lays out the options to keep the house, and you pick.',
    "So you're not interested in saving your property, or the equity you've got in it?",
    "I'm not from the bank, and I'm not one of those guys trying to buy your house.",
    "Before you ask, no, I'm not collecting for the bank.",
    "Nobody can guarantee that, but he can tell you what's on the table.",
    'He can help you keep your home if that is the option you pick.',
    'Our senior advisor has over thirty years in this.',
    'We work alongside licensed lenders and independent attorneys.',
    'The payment can jump thirty to fifty percent after a modification. Could you carry that?',
    'Catching it up usually runs five to fifteen thousand dollars.',
    "I'm not one of those wholesalers buying for pennies on the dollar.",
    'Has your attorney talked to the lender to actually stop the sale?',
    'If there was a way to stop the sale and buy you time, would you want to see it?',
    'Is he a foreclosure specialist, or a general attorney?',
]
for line, rule in HITS:
    got = cs.never_say(line)[0]
    chk(got == rule, 'never-say catches [%s]: %s' % (rule, line[:50]), got)
for line in ALLOWED:
    got = cs.never_say(line)
    chk(got[0] == '', 'allowed by the playbook wording: %s' % line[:60], got)
never = cs.playbook_parts()['never']
chk('well save your home' in never and 'our attorneys' in never and 'are you interested in selling' in never,
    "the note's own never-say quotes are parsed", never)
chk(not any(p in never for p in ('ask the court for more time', 'the numbers', 'nobody works for free', 'options',
                                 'questions that shut them down', 'how do you get paid')),
    'quotes that name what to say instead, or point at a section, are not treated as forbidden', never)
chk(cs.never_say('Catching it up runs about five thousand dollars.')[0] == 'figure',
    'one end of an allowed range on its own is a figure, not the general range')
# every fixed line the code itself hands the model passes its own filter
fixed = list(cs.AI_CHALLENGE) + list(cs.AI_CLOSES)
chk(all(cs.never_say(x)[0] == '' for x in fixed), 'the fixed challenge and closing lines pass the filter',
    [x for x in fixed if cs.never_say(x)[0]])

# ------------------------------------------------------------------ 2. core and situational
m = Model(reply('Okay, so keep him. Who is talking to the bank about the payment?', 'objection', 'problem'))
run([T('This is Alejandro with Biscayne.', 'you'), T('I have a lawyer handling it.')], m, asked=['a', 'b'])
sys1, user1 = m.bodies[0]['messages'][0]['content'], m.bodies[0]['messages'][1]['content']
for mk in ('SELLS-MARKER', 'TALKS-MARKER', 'QUESTIONS-MARKER', 'NEWSECTION-MARKER', 'LISTEN FIRST', 'CHECKMATE.'):
    chk(mk in sys1, 'core in the system message: %s' % mk)
chk('NOT-FOR-THE-MODEL' not in sys1 + user1, 'the part of the note above the last --- never goes out')
for mk in ('ATTY-MARKER', 'CIOC-MARKER', "money problem, not a legal problem"):
    chk(mk in user1 and mk not in sys1, 'a lawyer line brings, in the user message: %s' % mk)
for mk in ('BUSY-MARKER', 'WIFE-MARKER', 'SHARP-MARKER', 'AFTERYES-MARKER', 'WIT-MARKER', 'Giving up:'):
    chk(mk not in user1 + sys1, 'a lawyer line leaves out: %s' % mk)
chk('PROBES-MARKER' not in sys1 and 'WHOWE-MARKER' not in sys1, 'one-moment paragraphs of a core section are not in the core')

m = Model(reply('Ten tomorrow morning it is. Save my number and read it back to me.', 'time', 'booked'))
run([T('Okay, tomorrow works.')], m, asked=['a', 'b', 'c', 'd', 'e'])
sys2, user2 = m.bodies[0]['messages'][0]['content'], m.bodies[0]['messages'][1]['content']
chk(sys2 == sys1, 'the system message is byte-for-byte the same on every line (a cacheable prefix)')
chk('AFTERYES-MARKER' in user2 and 'ATTY-MARKER' not in user2, 'a yes brings the after-a-yes paragraph, not the lawyer')

m = Model(reply('Hey, it is Alejandro. Did you know about the date?', 'unclear', 'opener'))
run([T('Hello?')], m)
u3 = m.bodies[0]['messages'][1]['content']
chk('WIT-MARKER' in u3 and 'WHOWE-MARKER' in u3, 'the first line of a call brings the wit and who-we-are pieces')

m = Model(reply('What are you not interested in, though?', 'objection', 'close'))
run([T('Not interested.'), T('Hey are you there?', 'you'), T('I said no.'), T('So the date is close.', 'you'),
     T('No thanks, I am all set.')], m, asked=["So you're not interested in saving your property, or the equity you've got in it?", 'b'])
u4 = m.bodies[0]['messages'][1]['content']
chk('SHARP-MARKER' in u4, 'waved off twice: the sharp lines ride along')
chk('SHARP-MARKER' not in user1 + u3, 'and not before that')

on = cs.live_topics(['THEM: we are working on a modification with the bank'], ['a', 'b', 'c'])
txt, picked = cs.situational(['THEM: we are working on a modification with the bank'], ['a', 'b', 'c'])
chk('bank' in on and 'NUMBERS-MARKER' in txt and 'The bank is helping' in txt and 'A lawyer:' not in txt,
    'a modification brings the numbers and the bank reframe only', picked)
chk(len(txt) <= cs.SIT_BUDGET, 'situational text stays inside its budget (%d <= %d)' % (len(txt), cs.SIT_BUDGET))

# ------------------------------------------------------------------ 3. token budget
core_tok = len(sys1) // 4
chk(len(cs.AI_SYSTEM) // 4 < 3200, 'the fixed rules are under 3,200 tokens (%d)' % (len(cs.AI_SYSTEM) // 4))
chk((len(sys1) + len(user1)) // 4 < 5000, 'a lawyer line with this note is under 5,000 tokens (%d)' % ((len(sys1) + len(user1)) // 4))
REAL = os.path.join(os.path.expanduser('~'), 'projects', 'obsidian-vault', '5-projects', 'MSG Sales', 'Call Coach AI Playbook.md')
if os.path.exists(REAL):
    with mock.patch.object(cs, 'PLAYBOOK', REAL):
        cs._playbook['mtime'] = None
        m = Model(reply('Okay, so keep him. Who is talking to the bank?', 'objection', 'problem'))
        run([T('This is Alejandro.', 'you'), T('I have a lawyer handling it.')], m, asked=['a', 'b'])
        tok = sum(len(x['content']) for x in m.bodies[0]['messages']) // 4
        chk(tok < 5600, 'with the real playbook a lawyer line is about %d tokens (was about 13,600 before the split)' % tok)
        cs._playbook['mtime'] = None
    cs._playbook['mtime'] = None
    cs.playbook_parts()
else:
    print('skip the real-playbook size check (note not on this machine)')

# ------------------------------------------------------------------ 4. re-ask behaviour
m = Model(reply('We will save your home. How does ten tomorrow sound?', 'objection', 'close'),
          reply('He lays out the options. How does ten tomorrow sound?', 'objection', 'close'))
res, st, log = run([T('I have it handled.')], m)
chk(res.get('line', '').startswith('He lays out the options') and len(m.bodies) == 2,
    'a never-say line is asked once more, and the clean one is shown', res)
chk('could not be shown because it promises an outcome' in m.bodies[1]['messages'][1]['content'],
    'the re-ask says why')
chk('never-say rule=promise fixed' in log and 'save your home' not in log, 'the block is logged by rule, without the words', log)

m = Model(reply('We will save your home.', 'objection', 'close'), reply('He guarantees we stop the sale.', 'objection', 'close'))
res, st, log = run([T('I have it handled.')], m)
chk(res == {} and st.get('why') == 'never:promise' and len(m.bodies) == 2, 'a second bad line is dropped', (res, st.get('why')))
chk('never-say rule=promise dropped' in log, 'and logged as dropped', log)

long_one = ('Okay so here is the thing about all of this and why it matters to you right now with that date coming up '
            'so fast and the bank not telling you anything in writing and the lawyer busy with the court side and nobody '
            'watching the money side at all which is exactly where people get hurt the most')
m = Model(reply(long_one, 'objection', 'problem'), reply('I guarantee he can fix it. Ten tomorrow?', 'objection', 'close'))
res, st, log = run([T('I have it handled.')], m)
chk(res == {} and st.get('why', '').startswith('never:'), 'a never-say line that comes back from a later re-ask is still dropped', (res, st.get('why')))

trimmable = ('Okay, so the bank is looking at it. And by the way, the bank and the court are two different offices. '
             'They do not talk to each other, and the sale does not wait for the bank to finish its review of your file. '
             'If the date gets here first, the house is sold, and nobody at the bank will have called you about it. '
             'How does ten tomorrow morning sound?')
m = Model(reply(trimmable, 'objection', 'problem'))
res, st, log = run([T('The bank is working with me.')], m)
chk(len(m.bodies) == 1 and res.get('line', '').endswith('How does ten tomorrow morning sound?')
    and len(res['line'].split()) <= 45 and st.get('trimmed'),
    'a long line that trims cleanly to whole sentences is cut in code, no second model call', res.get('line'))

# ------------------------------------------------------------------ 5. reply size, cut replies, usage and timing
chk(m.bodies[0]['max_tokens'] == cs.AI_MAX_TOKENS <= 300, 'max_tokens is fitted to the reply form (%s)' % m.bodies[0]['max_tokens'])
m = Model(reply('Okay, so the date is close. What happens if the bank says no a week bef', 'question', 'problem'), finish='length')
res, st, _ = run([T('We are fine.')], m)
chk(res.get('line') == 'Okay, so the date is close.', 'a reply cut off by max_tokens keeps only whole sentences', res.get('line'))


class Resp:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode('utf-8')

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


payload = {'choices': [{'message': {'content': reply('Okay, so what do you want to happen with the house?')},
                        'finish_reason': 'stop'}],
           'usage': {'prompt_tokens': 4800, 'completion_tokens': 40,
                     'prompt_tokens_details': {'cached_tokens': 3900, 'cache_creation_input_tokens': 0}}}
st, err = {}, io.StringIO()
with mock.patch.object(cs.urllib.request, 'urlopen', lambda req, timeout=None: Resp(payload)), \
        mock.patch.object(cs, 'venice_key', lambda: 'k'), mock.patch.object(cs.sys, 'stderr', err):
    res = cs.ai_line([T('We got a letter.')], '', '', [], 7, 'auction', '', stat=st)
chk(res.get('line') and st.get('ptok') == 4800 and st.get('cached') == 3900 and st.get('ctok') == 40
    and isinstance(st.get('ttfb'), int) and isinstance(st.get('total'), int) and st.get('ptok_est', 0) > 0,
    "Venice's usage (prompt, cached, completion tokens) and the timing are kept for the log", st)
chk('prompt_cache_key' not in json.dumps(Model().bodies) and not cs.AI_CACHE_KEY, 'prompt_cache_key is off unless set')

# ------------------------------------------------------------------ 6. speaker: a read-aloud fragment is the caller
coach = 'Okay, so the bank looking at it and the court selling it are two different offices. How does ten tomorrow sound?'
chk(cs._reading_line('the court selling it are two different offices', [coach]), 'a piece of the coach line, read aloud, is the caller')
chk(not cs._reading_line('two different offices? what do you mean', [coach]), 'a homeowner echoing a few of its words is not')
chk(not cs._reading_line('ten tomorrow sounds fine', [coach]), 'a short answer that shares words is not')
m = Model(reply('Okay, so what would you do if the date came first?'))
run([T('Okay, so the bank looking at it and the court selling it', 'them'), T('We are just waiting on them.')], m, asked=[coach])
u6 = m.bodies[0]['messages'][1]['content']
chk('CALLER: Okay, so the bank looking at it' in u6 and '?: We are just waiting on them.' in u6,
    'a fragment labelled THEM that is the coach line read aloud goes out as CALLER; the answer after it keeps its own label', u6.split('TRANSCRIPT SO FAR:')[-1])

print('\nALL OK' if not bad else '\nFAIL %d' % len(bad))
sys.exit(1 if bad else 0)
