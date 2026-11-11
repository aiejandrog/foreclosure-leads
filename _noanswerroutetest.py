"""No-answer to mail pipeline in Call Mode (2026-11-11, Alex: "if we press no answer, like it sends that
prospect directly towards a pipeline where we mail ... mark it up as a prompt so we can send it to you or
any other agentic model").

Pins Alex's decisions of the same day: the FIRST No answer or voicemail on a verified-equity lead routes it
to mail (inside 7 days of the sale: Knock, owner-occupied only); a person's route is never overridden;
one letter per lead; a relative's line does not count; reached / booked leads leave; absentee owners are
mailed at the mailing address and never knocked; Mailed sets a follow-up call date; 10 days unreached
moves to Knock; the prospect record's AI copy leaves out full phone numbers, the owner's last name and
any equity figure that may not be quoted; the weekly scorecard counts dials, conversations, letters and
doors per seat. Also the bake: lookup rows carry eqc / ab / ma, and ma is only for absentee equity rows.
Fake leads and invented values only. JS is extracted by name and run under node.
"""
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}', i)
    k = SRC.find('\n', j + 2)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:k + 1] if i >= 0 and j > 0 else ''


def grab_var(name):
    i = SRC.find('var ' + name + ' ')
    j = SRC.find(';\n', i)
    rec('var present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 else ''


def node(js):
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    finally:
        os.remove(path)


TODAY = datetime.date.today()


def mdy(days):
    d = TODAY + datetime.timedelta(days=days)
    return '%d/%d/%d' % (d.month, d.day, d.year)


def ymd(days):
    return (TODAY + datetime.timedelta(days=days)).isoformat()


EQ = {'q': 1, 'f': 1, 'n': 40000, 'lb': 'Clear equity'}
NOEQ = {'q': 0, 'f': 0, 'st': 'unpriced'}
ROWS = [
    {'c': 'EQ1', 'on': 'Ana Gomez Ruiz', 'a': '100 NW 1st Ave, MIAMI, FL 33128', 'ct': 'MI', 'x': mdy(25), 'eqc': EQ,
     'p': ['3055550101', '3055550102'], 'sx': 'tw'},
    {'c': 'SOON', 'on': 'Ben Lee', 'a': '200 SW 2nd St, MIAMI, FL 33130', 'ct': 'MI', 'x': mdy(4), 'eqc': EQ, 'p': ['3055550201']},
    {'c': 'ABS', 'on': 'Cal Diaz', 'a': '300 NE 3rd Ct, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(30), 'eqc': EQ, 'ab': 1,
     'ma': '9 Elm Rd, ORLANDO, FL 32801', 'p': ['3055550301']},
    {'c': 'ABSSOON', 'on': 'Dee Fox', 'a': '400 Oak Ln, MIAMI, FL 33150', 'ct': 'MI', 'x': mdy(3), 'eqc': EQ, 'ab': 1, 'p': ['3055550401']},
    {'c': 'ABSNOMA', 'on': 'Eve Hall', 'a': '500 Pine Rd, DORAL, FL 33178', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'ab': 1, 'p': ['3055550501']},
    {'c': 'NOEQ', 'on': 'Fay Kim', 'a': '600 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': NOEQ, 'p': ['3055550601']},
    {'c': 'MANUAL', 'on': 'Gus Moe', 'a': '700 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'p': ['3055550701']},
    {'c': 'TALKED', 'on': 'Hal Ng', 'a': '800 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'p': ['3055550801']},
    {'c': 'REL', 'on': 'Ivy Ow', 'a': '900 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'p': ['3055550901']},
    {'c': 'LETTERED', 'on': 'Jo Pi', 'a': '1000 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'p': ['3055551001']},
    {'c': 'OPT', 'on': 'Kay Qu', 'a': '1100 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ, 'p': ['3055551101']},
    {'c': 'OLDMAIL', 'on': 'Lu Ro', 'a': '1200 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(40), 'eqc': EQ, 'p': ['3055551201']},
    {'c': 'LATE', 'on': 'Mo Su', 'a': '1300 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(5), 'eqc': EQ, 'p': ['3055551301']},
    {'c': 'PASTE', 'on': 'Ned Tu', 'a': '1400 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(22), 'eqc': EQ,
     'p': ['3055551401'], 'ps': ['wpm']},
]
DAY = 86400000
NOTES = {
    'MANUAL': {'route': {'k': 'none', 'd': ymd(-1), 'ts': 1, 'by': 'Carlos'}},
    'TALKED': {'dials': [{'d': ymd(-2), 'tsu': 5, 'oc': 'talked', 'ph4': '0801'}]},
    'REL': {'wpr': [{'n': '3055550901', 'k': 'r'}]},
    'LETTERED': {'touches': [{'d': ymd(-30), 'ch': 'letter', 'out': 'Mailed'}]},
    'OPT': {'optout': ymd(-1)},
    'OLDMAIL': {'route': {'k': 'mail', 'd': ymd(-12), 'ts': 10, 'by': 'Alex', 'done': ymd(-11), 'dk': 'letter', 'how': 'hand'}},
    'LATE': {'route': {'k': 'mail', 'd': ymd(-3), 'ts': 10, 'by': 'Alex', 'auto': 'na', 'mc': 1}},
}

FNS = ('isBalloon', 'liveDays', 'isEquity', 'hardSuppressed', '_wasLp', 'noState', 'routeOf', 'routeAtty',
       'routeBlock', 'setRoute', 'routeMark', 'routeCity', 'routeLists', 'esc', '_lkDigits', 'wpmNums', 'wprList',
       'relKindNum', 'lkAsDial', '_eqUsd', '_tele24n', '_ymd', '_ymdAdd', '_ymdDiff', '_md', '_hm', 'numSrc',
       'ownerMisses', 'reachedAt', 'bookOpen', 'letterSent', '_rowsByC', 'noAnswerRoute', 'routeLeftWhy', 'mailTo',
       'routeAdvance', '_whyLine', 'mailLists', '_firstLast', '_ocWord', '_countyWord', 'recTimeline', 'recHolds',
       'nextAct', 'prospectFacts', 'prospectPrompt', 'pipelineItems', 'pipelinePrompt', '_weekStart', 'weekStats',
       'weekText')
VARS = ('ROUTE_KINDS', 'ROUTE_MAIL_MIN_DAYS', 'ROUTE_CO_ORDER', 'MAIL_AFTER_MISSES', 'MAIL_FOLLOWUP_DAYS',
        'MAIL_KNOCK_AFTER_DAYS', 'MAIL_LEFT_DAYS', 'MAIL_POSTAGE', 'WEEK_TARGETS', 'SX_WORD', 'REACHED_OC',
        'WEEK_COLS', 'WEEK_NOTE')
JS = ('function _heldNow(){ return false; }\nfunction optPhones(){ return {}; }\n'
      'function today(){ return "' + TODAY.isoformat() + '"; }\nfunction nowTS(){ return "now"; }\n'
      'function caller(){ return "Carlos"; }\nvar SAVED = 0; function saveNotes(){ SAVED++; }\nfunction queueSync(){}\nvar _FCGEN = 0;\n'
      'var OUTCOMES = [{k:"noanswer",t:"No answer"},{k:"voicemail",t:"Left voicemail"},{k:"talked",t:"Talked"},{k:"appt",t:"APPOINTMENT SET"}];\n'
      + ''.join(grab_var(v) for v in VARS)
      + ''.join(grab_fn(n) for n in FNS)
      + 'var ROWS = ' + json.dumps(ROWS) + ';\nvar COV = [{c:"LK1", lk:1, on:"Ola Vu", a:"1500 Bay Dr, MIAMI, FL 33137", x:"' + mdy(30)
      + '", d:30, eqc:' + json.dumps(EQ) + '}];\nvar notes = ' + json.dumps(NOTES) + ';\n'
      + r"""
var byC = {}; ROWS.forEach(function(r){ byC[r.c] = r; });
function miss(c, digits, oc){
  var n = notes[c] = notes[c] || {status:'', note:''};
  n.dials = n.dials || [];
  n.dials.push({d:today(), tsu:Date.now(), ph4:String(digits).slice(-4), oc:oc || 'noanswer', by:'Carlos'});
  var r = byC[c] || lkAsDial(COV[0], digits);
  return noAnswerRoute(r, oc || 'noanswer', digits);
}
var o = {};
o.src = [numSrc(byC.EQ1, '3055550101'), numSrc(byC.EQ1, '(305) 555-0102'), numSrc(byC.PASTE, '3055551401'),
         numSrc(lkAsDial(COV[0], '3055559999'), '3055559999')].join('');
o.eq1 = miss('EQ1', '3055550102'); o.eq1r = notes.EQ1.route;
o.eq1again = (function(){ notes.EQ1.dials.push({d:today(), tsu:Date.now(), oc:'noanswer'}); return noAnswerRoute(byC.EQ1, 'noanswer', '3055550101'); })();
o.vm = miss('PASTE', '3055551401', 'voicemail'); o.pasteSx = notes.PASTE.route.sx;
o.soon = miss('SOON', '3055550201');
o.abs = miss('ABS', '3055550301');
o.absSoon = miss('ABSSOON', '3055550401');
o.absNoMa = miss('ABSNOMA', '3055550501');
o.noeq = miss('NOEQ', '3055550601');
o.manual = miss('MANUAL', '3055550701'); o.manualK = notes.MANUAL.route.k;
o.talked = miss('TALKED', '3055550801');
o.rel = miss('REL', '3055550901');
o.lettered = miss('LETTERED', '3055551001');
o.opt = miss('OPT', '3055551101');
o.talk = (function(){ notes.X = {}; ROWS.push({c:'X', a:'1 X St, MIAMI, FL 33101', x:'""" + mdy(25) + r"""', eqc:{q:1,f:1,n:5}}); byC.X = ROWS[ROWS.length-1];
  return noAnswerRoute(byC.X, 'talked', '3055550000'); })();
o.lk = miss('LK1', '3055559999');
o.setKnockAbs = setRoute(byC.ABS, 'knock');
/* advance: LATE mail -> knock (sale in 5 days); OLDMAIL mailed 11 days ago -> knock */
o.moved = routeAdvance();
o.late = notes.LATE.route; o.old = notes.OLDMAIL.route;
var M = mailLists();
o.ready = M.ready.map(function(it){ return it.c; }).sort();
o.readyTo = (M.ready.filter(function(it){ return it.c === 'ABS'; })[0] || {}).to;
o.blocked = M.blocked.map(function(it){ return it.c + ':' + it.why; }).sort();
o.unver = M.unverified;
/* mark mailed: follow-up date, how, mby; then it sits in Mailed */
routeMark(byC.EQ1, 'letter', 'lob');
o.eq1Mailed = {route:notes.EQ1.route, next:notes.EQ1.next, t:notes.EQ1.touches[notes.EQ1.touches.length-1].ch};
o.mailed = mailLists().mailed.map(function(it){ return it.c + ':' + it.fu; });
/* reached after routing: leaves the pipeline */
notes.PASTE.dials.push({d:today(), tsu:Date.now() + 1000, oc:'talked', ph4:'1401'});
/* a gatekeeper's callback is not the owner: EQ2-like check on reachedAt */
o.gk = reachedAt({dials:[{oc:'callback', gk:1, tsu:5}, {oc:'talked', rel:'r', tsu:6}]});
o.gkReal = reachedAt({dials:[{oc:'callback', tsu:7}]});
var M2 = mailLists();
o.left = M2.left.map(function(it){ return it.c + ':' + it.why; });
o.readyAfterReach = M2.ready.map(function(it){ return it.c; }).sort();
/* next action + record + prompt */
o.naABS = nextAct(byC.ABS, notes.ABS);
o.naOPT = nextAct(byC.OPT, notes.OPT);
o.naSOON = nextAct(byC.SOON, notes.SOON);
o.naEQ1 = nextAct(byC.EQ1, notes.EQ1);
o.prompt = prospectPrompt('EQ1');
o.promptNoEq = prospectPrompt('NOEQ');
o.pipe = pipelinePrompt();
o.items = pipelineItems().map(function(it){ return it.c + ':' + it.a.s; });
/* week */
var W = weekStats(notes, Date.now());
o.week = W.seats.Carlos ? W.seats.Carlos.cur : null;
o.weekTxt = weekText(W);
console.log(JSON.stringify(o));
""")

rc, so, se = node(JS)
rec('node ran', rc == 0, se[-800:])
o = json.loads(so.strip().splitlines()[-1]) if rc == 0 and so.strip() else {}

if o:
    rec('number source: trace, Whitepages, pasted, lookup paste', o['src'] == 'twpp', o['src'])
    rec('first No answer on an equity lead routes to mail', o['eq1'] == 'mail', o['eq1'])
    r = o['eq1r']
    rec('auto route unit: mail, auto na, 1 miss, number source, who called',
        r.get('k') == 'mail' and r.get('auto') == 'na' and r.get('mc') == 1 and r.get('sx') == 'w' and r.get('by') == 'Carlos', r)
    rec('a second miss does not re-route', o['eq1again'] == '')
    rec('voicemail counts and a pasted number routes', o['vm'] == 'mail' and o['pasteSx'] == 'p', [o['vm'], o['pasteSx']])
    rec('sale inside 7 days, owner lives there: knock', o['soon'] == 'knock', o['soon'])
    rec('absentee: mail (to the mailing address)', o['abs'] == 'mail', o['abs'])
    rec('absentee inside 7 days: no route, never knock', o['absSoon'] == '', o['absSoon'])
    rec('absentee with no mailing address still routes, then shows Blocked', o['absNoMa'] == 'mail', o['absNoMa'])
    rec('not verified equity: no route', o['noeq'] == '', o['noeq'])
    rec('a person\'s choice (Neither) is never overridden', o['manual'] == '' and o['manualK'] == 'none')
    rec('reached before: no route', o['talked'] == '', o['talked'])
    rec('relative line: no route', o['rel'] == '', o['rel'])
    rec('one letter per lead: already lettered, no route', o['lettered'] == '', o['lettered'])
    rec('opted out: no route', o['opt'] == '', o['opt'])
    rec('a Talked outcome does not route', o['talk'] == '', o['talk'])
    rec('a gatekeeper callback (gk) and a relative line do not count as reaching the owner', o['gk'] == 0, o['gk'])
    rec('an owner callback does', o['gkReal'] == 7, o['gkReal'])
    rec('Whitepages-pasted lookup lead with equity routes (eqc carried)', o['lk'] == 'mail', o['lk'])
    rec('absentee: manual Knock refused', o['setKnockAbs'] is False)
    rec('advance moved the late mail lead and the 11-day mailed lead to knock', o['moved'] == 2
        and o['late'].get('k') == 'knock' and o['late'].get('auto') == 'late'
        and o['old'].get('k') == 'knock' and o['old'].get('auto') == 'unreached', [o['moved'], o['late'], o['old']])
    rec('Ready to mail', o['ready'] == sorted(['ABS', 'EQ1', 'LK1', 'PASTE']), o['ready'])
    rec('absentee letter goes to the mailing address', o['readyTo'] == '9 Elm Rd, ORLANDO, FL 32801', o['readyTo'])
    rec('Blocked: absentee with no mailing address',
        o['blocked'] == ['ABSNOMA:absentee owner, no mailing address on file'], o['blocked'])
    rec('Unreachable, equity not verified is a count', o['unver'] == 1, o['unver'])
    m = o['eq1Mailed']
    rec('Mailed: letter touch, printed, marked by, follow-up in 6 days',
        m['t'] == 'letter' and m['route'].get('how') == 'lob' and m['route'].get('mby') == 'Carlos'
        and m['route'].get('done') == TODAY.isoformat() and m['next'] == ymd(6), m)
    rec('Mailed, waiting lists it with the follow-up date', 'EQ1:' + ymd(6) in o['mailed'], o['mailed'])
    rec('reached after routing: left the pipeline, off Ready',
        'PASTE:reached on the phone' in o['left'] and 'PASTE' not in o['readyAfterReach'], [o['left'], o['readyAfterReach']])
    rec('next action: absentee on Ready is MAIL to the mailing address',
        o['naABS']['s'] == 'MAIL' and 'mailing address' in o['naABS']['why'], o['naABS'])
    rec('next action: opted out is CLOSED', o['naOPT']['s'] == 'CLOSED', o['naOPT'])
    rec('next action: sale in 4 days, not reached is URGENT', o['naSOON']['s'] == 'URGENT', o['naSOON'])
    rec('next action: just mailed is WAIT', o['naEQ1']['s'] == 'WAIT' and 'follow-up' in o['naEQ1']['why'], o['naEQ1'])
    p = o['prompt']
    rec('prompt: first name and last initial only', 'Ana R.' in p and 'Gomez' not in p and 'Ruiz' not in p, p[:400])
    rec('prompt: no full phone number', '3055550101' not in p and '305' not in p.replace('Case', ''), p)
    rec('prompt: last four and source', '...0102' in p and 'Whitepages owner number' in p)
    rec('prompt: quotable figure marked internal only', 'internal only, never said or written to the homeowner' in p)
    rec('prompt: carries the rules and the questions', 'never contacted again on any channel' in p and 'biggest risk' in p)
    rec('prompt: a non-quotable lead shows no dollar figure', '$' not in o['promptNoEq'], o['promptNoEq'][:300])
    rec('pipeline prompt: urgent first, no full phones', o['items'][0].endswith(':URGENT') and '305555' not in o['pipe'], o['items'])
    w = o['week'] or {}
    rec('week: letters and dials counted for the seat', w.get('letters') == 1 and w.get('dials', 0) >= 10, w)
    rec('week text: targets and the Friday question', 'dials 625' in o['weekTxt'] and 'let slip' in o['weekTxt'])

# ---- no em or en dashes anywhere in what this build added -----------------------------------------------
blk_i = SRC.find('NO-ANSWER TO MAIL PIPELINE (2026-11-11)')
blk_j = SRC.find('function _afterMail(r){', blk_i)
rec('no em or en dash in the pipeline block', blk_i > 0 and '\u2014' not in SRC[blk_i:blk_j] and '\u2013' not in SRC[blk_i:blk_j])

# ---- the bake: lookup rows and mailing address ----------------------------------------------------------
sys.path.insert(0, HERE)
try:
    import call_mode as CM
    eq = {'state': 'clear', 'fact': True, 'may_quote': True, 'net': 50000, 'label': 'Clear'}
    rec('_mail_to: absentee equity row gets the mailing address',
        CM._mail_to({'addr': '1 A St, MIAMI, FL 33101', 'mail': '9 Elm Rd, ORLANDO, FL 32801', 'eqc': eq}) == '9 Elm Rd, ORLANDO, FL 32801')
    rec('_mail_to: owner-occupied gets nothing',
        CM._mail_to({'addr': '1 A St, MIAMI, FL 33101', 'mail': '1 A ST, MIAMI FL 33101', 'eqc': eq}) is None)
    rec('_mail_to: no quotable equity gets nothing',
        CM._mail_to({'addr': '1 A St', 'mail': '9 Elm Rd', 'eqc': dict(eq, may_quote=False)}) is None)
    cov = [{'c': 'L1'}]
    n = CM.mark_lookup(cov, [{'c': 'L1', 'sb': 1, 'pk': 'P1', 'eqc': {'q': 1, 'n': 5.0}, 'ab': 1, 'ma': '9 Elm Rd'}])
    rec('mark_lookup copies eqc, ab and ma', n == 1 and cov[0].get('eqc') == {'q': 1, 'n': 5.0}
        and cov[0].get('ab') == 1 and cov[0].get('ma') == '9 Elm Rd', cov[0])
except Exception as e:
    rec('call_mode importable', False, repr(e))

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
