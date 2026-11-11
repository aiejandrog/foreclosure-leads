"""Call Mode equity re-route (2026-11-05, Alex: "for the properties with equity on the call mode
section i want a re route section after the call to either prep this person up for mailing cards to
them or to route them to knocking").

Pins: the after-call "Next step" panel shows only on a Has-equity lead; routeBlock() refuses opted
out / DNC / hard no / Dead / soft no / attorney / off the call list / not equity; the two lists
re-apply those at render time; knock groups by city with Miami-Dade first and Miami first inside it;
a letter is refused inside 7 days of the sale; "Mailed" / "Knocked" write one ch:'letter' / ch:'door'
touch; the route unit merges newest-ts-wins on the board's _mergeLead and the bridge's _merge_note,
and atty is sticky. Fake leads only. JS is extracted by name and run under node.
"""
import io, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
TPL = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name, src=SRC):
    i = src.find('function ' + name + '(')
    j = src.find('\n}', i)
    k = src.find('\n', j + 2)
    rec('function present: ' + name, i >= 0 and j > 0)
    return src[i:k + 1] if i >= 0 and j > 0 else ''


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


def mdy(days):
    import datetime
    d = datetime.date.today() + datetime.timedelta(days=days)
    return '%d/%d/%d' % (d.month, d.day, d.year)


EQ = {'q': 1, 'f': 1, 'n': 40000}
ROWS = [
    {'c': 'M1', 'a': '100 NW 1st Ave, MIAMI, FL 33128', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'M2', 'a': '200 SW 2nd St, HIALEAH, FL 33010', 'ct': 'MI', 'x': mdy(10), 'eqc': EQ},
    {'c': 'M3', 'a': '300 NE 3rd Ct, MIAMI, 33130', 'ct': 'MI', 'x': mdy(30), 'eqc': EQ},
    {'c': 'B1', 'a': '400 Oak Ln, CORAL SPRINGS, FL 33065', 'ct': 'BR', 'x': mdy(15), 'eqc': EQ},
    {'c': 'A1', 'a': '500 Pine Rd, DORAL, FL 33178', 'ct': 'MI', 'x': mdy(12), 'eqc': EQ},
    {'c': 'SOON', 'a': '600 Elm St, MIAMI, FL 33150', 'ct': 'MI', 'x': mdy(3), 'eqc': EQ},
    {'c': 'OPT', 'a': '700 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'DEAD', 'a': '800 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'NOTINT', 'a': '900 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'ATTY', 'a': '1000 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'NOEQ', 'a': '1100 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': {'q': 0, 'f': 1}},
    {'c': 'DNCST', 'a': '1200 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
    {'c': 'HARD', 'a': '1300 Bay Dr, MIAMI, FL 33137', 'ct': 'MI', 'x': mdy(20), 'eqc': EQ},
]


def R(k, ts=1):
    return {'k': k, 'd': '2026-11-05', 'ts': ts, 'by': 'Alejandro'}


NOTES = {
    'M1': {'route': R('knock')}, 'M2': {'route': R('knock')}, 'M3': {'route': R('knock')},
    'B1': {'route': R('knock')}, 'A1': {'route': R('mail')}, 'SOON': {'route': R('mail')},
    'OPT': {'route': R('knock'), 'optout': '2026-11-05'},
    'DEAD': {'route': R('mail'), 'status': 'Dead'},
    'NOTINT': {'route': R('knock'), 'no': 'soft', 'noWasLp': False, 'resurf': 0},
    'ATTY': {'route': R('knock'), 'atty': '2026-11-05'},
    'NOEQ': {'route': R('mail')},
    'DNCST': {'route': R('knock'), 'status': 'DO NOT CONTACT'},
    'HARD': {'route': R('knock'), 'no': 'hard'},
    'GONE': {'route': R('knock')},                      # stay / diligence / removed: not in ROWS
    'NONE': {'route': R('none')},
    'DONE': {'route': dict(R('mail'), done='2026-11-04')},
}

FNS = ('isBalloon', 'liveDays', 'isEquity', 'hardSuppressed', '_wasLp', 'noState', 'routeOf', 'routeAtty',
       'routeBlock', 'setRoute', 'setRouteAtty', 'routeMark', 'routeCity', 'routeLists', 'routeOpenN',
       'routePanelHtml', 'esc', '_rowsByC', 'routeLeftWhy', 'reachedAt', 'bookOpen', '_ymd', '_ymdAdd')
JS = ('function _heldNow(){ return false; }\nfunction optPhones(){ return {}; }\n'
      'function today(){ return "2026-11-05"; }\nfunction nowTS(){ return "2026-11-05T10:00"; }\n'
      'function caller(){ return "Carlos"; }\nvar SAVED = 0; function saveNotes(){ SAVED++; }\nfunction queueSync(){}\nvar _FCGEN = 0;\n'
      + grab_var('ROUTE_KINDS') + grab_var('ROUTE_MAIL_MIN_DAYS') + grab_var('ROUTE_CO_ORDER')
      + grab_var('MAIL_FOLLOWUP_DAYS') + grab_var('REACHED_OC')
      + ''.join(grab_fn(n) for n in FNS)
      + 'var ROWS = ' + json.dumps(ROWS) + ';\nvar notes = ' + json.dumps(NOTES) + ';\n'
      + r"""
var byC = {}; ROWS.forEach(function(r){ byC[r.c] = r; });
var out = {};
var L = routeLists();
out.mail = L.mail.map(function(it){ return it.r.c; });
out.knock = L.knock.map(function(G){ return G.co + ':' + G.city + ':' + G.items.map(function(it){ return it.r.c; }).join(','); });
out.blocked = L.blocked.map(function(b){ return b.c; }).sort();
out.openN = routeOpenN();
out.cityRaw = routeCity({a:'9 OLD RD, MIAMI, 33130'}) + '|' + routeCity({a:'10 EAST ST', z:'33133'}) + '|' + routeCity({a:'1 A St, NORTH MIAMI BEACH, FL 33162'});
/* panel */
out.panelEq = routePanelHtml(byC.M1);
out.panelNoEq = routePanelHtml(byC.NOEQ);
out.panelOpt = routePanelHtml(byC.OPT);
out.panelAtty = routePanelHtml(byC.ATTY);
out.panelSoon = routePanelHtml(byC.SOON);
/* writes */
out.setOpt = setRoute(byC.OPT, 'mail');
out.setBad = setRoute(byC.M1, 'bogus');
notes.FRESH = undefined; ROWS.push({c:'FRESH', a:'1 X St, MIAMI, FL 33101', ct:'MI', x:'""" + mdy(25) + r"""', eqc:{q:1,f:1,n:5}});
out.setFresh = setRoute(ROWS[ROWS.length-1], 'mail');
out.freshRoute = notes.FRESH.route;
routeMark(ROWS[ROWS.length-1], 'letter');
out.afterMail = {route: notes.FRESH.route, touches: notes.FRESH.touches};
out.mailAfterMark = routeLists().mail.map(function(it){ return it.r.c; });
routeMark(byC.M1, 'door'); routeMark(byC.M1, 'door');
out.knockedM1 = notes.M1.route;
out.m1Touches = notes.M1.touches.length;
routeMark(byC.M1, 'doordone');
out.knockAfterDone = routeLists().knock.map(function(G){ return G.items.map(function(it){ return it.r.c; }).join(','); }).join(',');
setRouteAtty(byC.M2);
out.m2 = {atty: notes.M2.atty, route: notes.M2.route.k, block: routeBlock(byC.M2)};
console.log(JSON.stringify(out));
""")

rc, so, se = node(JS)
rec('node ran', rc == 0, se[-600:])
o = json.loads(so.strip().splitlines()[-1]) if rc == 0 and so.strip() else {}

if o:
    rec('mail queue = open, unblocked, soonest sale first', o['mail'] == ['SOON', 'A1'], o['mail'])
    rec('knock grouped: Miami-Dade first, Miami first, then A-Z, then Broward',
        o['knock'] == ['MI:Miami:M1,M3', 'MI:Hialeah:M2', 'BR:Coral Springs:B1'],
        o['knock'])
    rec('every exclusion dropped at list time',
        o['blocked'] == sorted(['OPT', 'DEAD', 'NOTINT', 'ATTY', 'NOEQ', 'DNCST', 'HARD', 'GONE']), o['blocked'])
    rec('none / done routes are not on a list', 'NONE' not in o['blocked'] and 'DONE' not in o['mail'])
    rec('open count', o['openN'] == 6, o['openN'])
    rec('city parse', o['cityRaw'] == 'Miami|ZIP 33133|North Miami Beach', o['cityRaw'])
    rec('panel shows on an equity lead', 'data-rt="mail"' in o['panelEq'] and 'data-rt="knock"' in o['panelEq']
        and 'data-rt="none"' in o['panelEq'] and 'data-rt="atty"' in o['panelEq'])
    rec('panel absent on a non-equity lead', o['panelNoEq'] == '')
    rec('panel absent on an opted-out lead', o['panelOpt'] == '')
    rec('attorney lead: panel says not routed, no buttons', 'Not routed' in o['panelAtty'] and 'data-rt=' not in o['panelAtty'])
    rec('sale inside 7 days: mail button disabled', 'data-rt="mail" disabled' in o['panelSoon'] and 'Knock instead' in o['panelSoon'])
    rec('setRoute refuses a blocked lead', o['setOpt'] is False)
    rec('setRoute refuses an unknown kind', o['setBad'] is False)
    rec('setRoute writes the unit', o['setFresh'] is True and o['freshRoute']['k'] == 'mail'
        and o['freshRoute']['by'] == 'Carlos' and o['freshRoute']['ts'] > 0)
    t = (o['afterMail']['touches'] or [{}])[-1]
    rec('Mailed writes one letter touch and closes the route', t.get('ch') == 'letter' and t.get('src') == 'route'
        and o['afterMail']['route'].get('done') == '2026-11-05' and o['mailAfterMark'] == ['SOON', 'A1'], t)
    rec('No answer counts attempts and keeps the door', o['knockedM1'].get('kn') == 2 and not o['knockedM1'].get('done')
        and o['m1Touches'] == 2)
    rec('Done takes the door off the route', 'M1' not in o['knockAfterDone'], o['knockAfterDone'])
    rec('attorney: sticky date, route none, blocked', o['m2'] == {'atty': '2026-11-05', 'route': 'none', 'block': 'has an attorney'}, o['m2'])

# ---- board _mergeLead: route newest-ts wins, atty sticky -------------------------------------------
MJS = ('function _DNC(s){ return s === "DO NOT CONTACT"; }\nfunction _DEAD(s){ return s === "Dead" || _DNC(s); }\n'
       'function _lastTouchD(n){ return ""; }\nfunction _today(){ return "2026-11-05"; }\nfunction _nowTS(){ return ""; }\n'
       'function _devSrc(){ return ""; }\nvar _AY_STAGES = [];\n'
       + grab_fn('_mergeLead', TPL) + r'''
var a = _mergeLead({route:{k:'mail',ts:5}, atty:'2026-11-04'}, {route:{k:'knock',ts:9}, atty:'2026-11-05'});
var b = _mergeLead({route:{k:'knock',ts:9}}, {route:{k:'mail',ts:5}});
var c = _mergeLead({atty:'2026-11-01'}, {});
a=a[0]; b=b[0]; c=c[0];
console.log(JSON.stringify({a:a.route, aa:a.atty, b:b.route, c:c.atty}));
''')
rc, so, se = node(MJS)
rec('board _mergeLead ran', rc == 0, se[-600:])
if rc == 0:
    m = json.loads(so.strip().splitlines()[-1])
    rec('board: newer route wins whole', m['a'] == {'k': 'knock', 'ts': 9}, m['a'])
    rec('board: atty earliest wins', m['aa'] == '2026-11-04', m['aa'])
    rec('board: older route never replaces newer', m['b'] == {'k': 'knock', 'ts': 9}, m['b'])
    rec('board: atty never cleared', m['c'] == '2026-11-01', m['c'])
sys.path.insert(0, HERE)
try:
    import send_server as SS
    cur = {'route': {'k': 'mail', 'ts': 5}, 'atty': '2026-11-04'}
    inc = {'route': {'k': 'knock', 'ts': 9}, 'atty': '2026-11-05'}
    m, _ = SS._merge_note(cur, inc)
    rec('bridge: newer route wins whole', m.get('route') == {'k': 'knock', 'ts': 9}, m.get('route'))
    rec('bridge: atty earliest wins', m.get('atty') == '2026-11-04', m.get('atty'))
    m2, _ = SS._merge_note(inc, cur)
    rec('bridge: older route never replaces newer', m2.get('route') == {'k': 'knock', 'ts': 9}, m2.get('route'))
    m3, _ = SS._merge_note({'atty': '2026-11-01'}, {})
    rec('bridge: atty never cleared', m3.get('atty') == '2026-11-01')
except Exception as e:
    rec('send_server importable', False, e)

print('\n%d failure(s)' % len(fails))
sys.exit(1 if fails else 0)
