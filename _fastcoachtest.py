"""Fast coach in call_coach_live.html: the transcript drives the card, partial words included.

Runs the page's whole script in node against a small fake page, so what is tested is the code that
ships. Covers: a card on PARTIAL words; a mid-thought partial waits; a stronger read of the same
sentence replaces the first card; the lines a real recorded call (2026-11-15) said and nothing heard;
money answers never carry a number that is not verified; a stop request beats the rebuttal cards and
is not silenced by Pause; guardrail flags skip the homeowner's words; the script moves forward when
a stage line is read; the card goes dim when the conversation moves on. Invented lines only.
Needs node; skipped, loudly, without it.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(HERE, 'call_coach_live.html'), encoding='utf-8').read()
bad = []


def chk(c, m, extra=None):
    print(('ok   ' if c else 'FAIL ') + m + ('' if c or extra is None else '  | %s' % (extra,)))
    if not c:
        bad.append(m)


src = html[html.index('<script>') + 8:html.rindex('</script>')]
chk('/* FASTCOACH-BEGIN' in src and '/* FASTCOACH-END */' in src, 'fast coach block is in the page')
pure = src[src.index('/* FASTCOACH-BEGIN'):src.index('/* FASTCOACH-END */')]
for banned in ('document.', '$(', 'fetch(', 'XMLHttpRequest', 'WebSocket', 'localStorage'):
    chk(banned not in pure, 'the matcher touches no page and no network: %s' % banned)
chk('scanObjections' not in src and 'sitRespond(' not in src, 'one matcher: the old final-only paths are gone')
chk("fcHear(interim,false,'')" in src and "fcHear(last.text,false,dgTag(last.spk))" in src, 'both engines feed partial words to the coach')
srv = open(os.path.join(HERE, 'call_coach_server.py'), encoding='utf-8').read()
chk('"eqstate": r.get("eqstate")' in srv and 'm["coach_fires"]' in srv, 'server carries the verified state and saves prompt timings')
gi = open(os.path.join(HERE, '.gitignore'), encoding='utf-8').read().splitlines()
chk('!_fastcoachtest.py' in gi, 'this test is tracked')

HARNESS = r"""
var els = {};
function El(id){ this.id=id; this.textContent=''; this.className=''; this.value=''; this.style={}; this._h='';
  this.checked=false; this.disabled=false; this.offsetWidth=1; this.scrollTop=0; this.scrollHeight=0; this.children=[];
  var self=this; this.classList={add:function(c){ if((' '+self.className+' ').indexOf(' '+c+' ')<0) self.className=(self.className+' '+c).trim(); },
    remove:function(c){ self.className=(' '+self.className+' ').split(' '+c+' ').join(' ').trim(); }}; }
El.prototype.appendChild=function(c){ this.children.push(c); };
Object.defineProperty(El.prototype,'innerHTML',{get:function(){ return this._h || String(this.textContent).replace(/&/g,'&amp;').replace(/</g,'&lt;'); },
  set:function(v){ this._h=String(v); this.children=[]; }});
global.document={getElementById:function(id){ return els[id]||(els[id]=new El(id)); }, createElement:function(){ return new El(''); },
  addEventListener:function(){}};
global.window=global; global.location={search:'', reload:function(){}};
global.localStorage={getItem:function(){ return ''; }, setItem:function(){}, removeItem:function(){}};
global.alert=function(){}; global.prompt=function(){ return null; };
var VAULT={cards:[{n:2,t:'The Lawyer Wall',say:'I have a lawyer',reb:['Keep your attorney.','Second paragraph.']},
                  {n:7,t:'Scam fatigue',say:'three of you',reb:['You should be suspicious.']}]};
global.fetch=function(u){ return Promise.resolve({ok:true, json:function(){ return Promise.resolve(String(u).indexOf('objections')>=0?VAULT:{matches:[]}); }}); };
var _si=global.setInterval; global.setInterval=function(f,ms){ var h=_si(f,ms); if(h.unref) h.unref(); return h; };
__SRC__
var C=window.Coach, T=C._test, o={};
function $(id){ return document.getElementById(id); }
function wait(ms){ return new Promise(function(r){ setTimeout(r,ms); }); }
function card(){ var c=T.card(); return c?{id:c.id,kind:c.kind,say:c.say,ask:c.ask,act:c.act,heard:c.heard}:null; }
(async function(){
  $('f_sender').value='Alejandro'; $('f_first').value='Maria'; $('f_st1').value='100 NW 1st Ave';
  C.plain(); await wait(30);                      // vault cards load

  /* 1. partial words: the lawyer card is up before the sentence is over */
  T.interim('yeah i have a lawyer');
  o.partial_obj=[$('objection').className, $('objwho').textContent.indexOf('Lawyer Wall')>=0, T.fires().slice(-1)[0]];
  /* 2. the rest of the sentence changes the read: the first card is replaced */
  T.interim('yeah i have a lawyer but');
  o.mid_listen=$('clisten').className;
  T.interim('yeah i have a lawyer but he hasnt called me back');
  o.replaced=[card(), $('objection').className, $('coach').className.indexOf('on')===0];
  T.say('Yeah I have a lawyer but he hasn’t called me back');
  o.no_refire=T.fires().length;

  /* 3. mid-thought partial waits, then answers when nothing more comes */
  await wait(20); var n0=T.fires().length;
  T.interim('can you call me back because');
  o.held=[T.fires().length-n0, $('clisten').className];
  await wait(1050);
  o.held_then=[T.fires().length-n0, card(), T.fires().slice(-1)[0].held_ms>=850];

  /* 4. the lines the real call said and nothing heard */
  T.say('There’s been so many people calling us about this');
  o.crowd=T.fires().slice(-1)[0].id;
  T.say('Well listen I literally do not have time right now');
  o.notime=card();
  T.say('Um we already got it handled I promise our lawyer is the best');
  o.handled=[T.fires().slice(-1)[0].id, $('redflag').className];

  /* 5. guardrail flags are about the caller's words */
  T.say('Well keep your attorney, seriously');
  o.flag_your=$('redflag').className;
  T.say('our attorneys will take a look at it for you today');
  o.flag_our=$('redflag').className;

  /* 6. money questions: nothing unverified becomes a number */
  T.say('so how much do I owe on this thing');
  o.owe_none=card();
  T.say('what is my house worth right now');
  o.eq_none=card();

  /* 7. stop beats the rebuttal cards, even paused */
  C.pauseCoach();
  T.say('this is he');                       o.paused_quiet=T.card()&&T.card().id;
  T.interim('please stop calling');
  o.stop=[card(), $('coach').className, $('objection').className];
  C.pauseCoach();

  /* 8. the card goes dim once two more lines have gone by */
  T.say('I want to keep my house');          o.keep=card();
  T.say('it has been a rough year for us'); T.say('my husband lost his job in march');
  C.catchUp(); o.recap=T.card().say.split('\n').length;
  T.say('I just want out of this'); var before=$('coach').className;
  T.say('it has been a rough year for us'); T.say('nothing else to add really');
  C.coachMore(); o.stale=[before.indexOf('stale')<0, $('coach').className.indexOf('stale')>=0];

  /* 9. reading a stage line moves the script forward */
  C.jump(1); var s1=$('saytext').textContent;
  T.say(s1);
  o.stage=T.stage();

  /* 10. the 11-15 test call: "no time" is ONE card, then the day and time they give */
  var ids=function(n){ return T.fires().slice(n).map(function(x){ return x.id; }); };
  T.reset(); var f0=T.fires().length;
  T.interim('I have no time right now'); T.say('I have no time right now honestly');
  o.tc_one=ids(f0);
  T.say('And you’d have to call me back later to catch me what’s your time frame on getting me back');
  o.tc_quiet=ids(f0).length;
  T.interim('Tomorrow'); o.tc_day=card();
  T.say('Tomorrow at 7 A.M'); o.tc_when=card();
  T.say('Around 7 A.M'); o.tc_upd=[card().say, ids(f0), T.fires().slice(-1)[0].when];
  T.reset(); T.say('the sale is tomorrow at 9 am'); o.tc_unarmed=T.card()&&T.card().id;
  T.reset(); T.say('can you call me tomorrow around noon'); o.tc_same=card();
  T.reset(); T.say('call me later'); T.interim('please stop calling tomorrow'); o.tc_stop=card().kind+':'+card().id;

  /* 11. the 18:38 test call: you read the card, they cut in with the same objection, then a third time */
  T.reset(); C.jump(1);
  T.say('no thank you'); var b1=$('objbody').textContent;
  var fA=T.fires().length;
  T.interim('thats not a problem at all honestly i wasnt expecting you to be');
  T.say('That’s not a problem at all honestly I wasn’t expecting you to be');
  o.lad_read=[T.fires().length-fA, T.lines().slice(-1)[0].spk];
  T.cool();
  var cut='almost everybody I reach about a place like yours says the exact same thing when I first call no thank you seriously I’m not interested';
  T.interim(cut);
  o.lad_2=[$('objwho').textContent.indexOf('second pass')>=0, $('objbody').textContent!==b1, $('objbody').textContent.indexOf('plan')>=0, T.fires().slice(-1)[0].pass];
  T.say(cut); o.lad_tag=T.lines().slice(-1)[0].spk;
  T.cool(); T.say('I said I am not interested');
  o.lad_3=[$('objwho').textContent.indexOf('third time')>=0, $('objbody').textContent.indexOf('leave it there')>=0, T.fires().slice(-1)[0].pass];

  /* 12. Deepgram results: they speak first, you are whoever reads the screen, two voices in one result */
  T.reset(); T.dgReset(); C.jump(1); var st=$('saytext').textContent.split(' ').slice(0,12).join(' ');
  var W=function(txt,spk){ return txt.split(' ').map(function(x){ return {word:x.toLowerCase(), punctuated_word:x, speaker:spk}; }); };
  var M=function(words,fin){ return {is_final:fin, channel:{alternatives:[{transcript:words.map(function(w){ return w.punctuated_word; }).join(' '), words:words}]}}; };
  T.dg(M(W('Hello who is this and why are you calling me',0),true));      o.dg_first=[T.dgYou(), T.lines().slice(-1)[0].spk];
  T.dg(M(W(st,1),true));                       o.dg_you=[T.dgYou(), T.lines().slice(-1)[0].spk];
  T.cool(); var f1=T.fires().length;
  T.dg(M(W('I am not interested',1),false));   o.dg_you_quiet=T.fires().length-f1;
  T.dg(M(W('I am not interested',0),false));   o.dg_them_fire=T.fires().slice(f1).map(function(x){ return x.id; });
  var n0=T.lines().length;
  T.dg(M(W('so what I do is help owners',1).concat(W('please stop calling me',0)),true));
  o.dg_split=T.lines().slice(n0).map(function(l){ return l.spk; }); o.dg_stop=card()&&card().kind;
  o.dg_eng=$('eng').textContent;

  /* 13. Deepgram hears ONE voice (test call 11-15 22:00: 182 words on one number, 3 on the other).
     Its label is not believed, the coach still answers, and a sentence cut in two is one line. */
  T.reset(); T.dgReset(); C.jump(1);
  T.dg(M(W('Hey there',0),true));
  T.dg(M(W('Hi who is this',1),true));
  T.dg(M(W(st,1),true));                       o.one_you=[T.dgYou(), T.lines().slice(-1)[0].spk];
  T.cool(); var g1=T.fires().length;
  T.dg(M(W('I really don’t have time right now',1),false));
  T.dg(M(W('I really don’t have time right now, actually.',1),true));
  o.one_busy=T.fires().slice(g1).map(function(x){ return x.id; }); o.one_tag=T.lines().slice(-1)[0].spk;
  T.cool(); var g2=T.fires().length;
  T.dg(M(W('No. No. It’s alright. I’m already... I already have a lawyer on the case.',1),true));
  o.one_law=T.fires().slice(g2).map(function(x){ return x.id; }); o.one_eng=$('eng').textContent;
  var n1=T.lines().length;
  T.dg(M(W('I just',1),true)); T.dg(M(W('wanted to ask what he showed you.',1),true)); T.dg(M(W('Okay.',1),true));
  o.one_join=T.lines().slice(n1).map(function(l){ return l.text; });
  console.log('@@'+JSON.stringify(o));
  process.exit(0);
})().catch(function(e){ console.log('@@'+JSON.stringify({err:String(e&&e.stack||e).slice(0,600)})); process.exit(0); });
"""

PURE = r"""
function norm(s){ return String(s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'')
  .replace(/[’']/g,'').replace(/[^a-z0-9 ]+/g,' ').replace(/\s+/g,' ').trim(); }
var SIT=[{tag:'t',m:['this is he','speaking'],say:'x'}], CARDS=[{id:'busy',s:2,nm:['call me back'],body:'b'}], TRIG={2:[],7:[]};
__PURE__
var L=fcList(CARDS), o={};
function M(older, cur){ var w=norm(older+' '+cur), t=fcMatch(L, w, norm(cur).length, 1000, {}); return t?t.id:null; }
o.fresh_only=[M('i want to keep my house','and the weather is nice'), M('','i want to keep my house')];
o.gap=[M('','i literally do not really have time'), M('','people like you keep on calling me'), M('','do not under any circumstances whatsoever ever have time')];
o.whole=[M('','i was misspeaking'), M('','speaking')];
o.pri=[M('','dont call me at work'), M('','do not call me again'), M('','call me back later')];
o.also=[M('','my attorney is great'), M('my attorney is on it','he hasnt called me back')];
o.open=[fcOpen('i have an attorney but'), fcOpen('i have an attorney'), fcOpen('si')];
var none={}, rec={judgment:'$412,300.55', auction:'2099-01-02', days:12, equity:88000},
    ver={judgment:412300.55, equity:88000, eqstate:'priced'}, unk={judgment:'$5.00', judgment_unknown:true, equity:50000, eqstate:'unpriced'};
o.f_none=fcFacts(none); o.f_rec=fcFacts(rec); o.f_ver=fcFacts(ver); o.f_unk=fcFacts(unk);
var B=function(l,s){ var x=fcBoard(l,s); return [x.eqstate===undefined?null:x.eqstate, x.equity===undefined?'raw':x.equity, fcFacts(x).equity.ok, fcFactSay('equity',x,false).say.indexOf('$')>=0]; };
o.board=[B({case:'A-1',equity:50000},'?case=A-1&eq=priced&en=120000'), B({case:'A-1',equity:50000},'?case=A-1&eq=clear'),
         B({case:'A-1',equity:50000},'?case=A-1&eq=unpriced&en=120000'), B({case:'A-1',equity:50000},'?case=B-2&eq=priced&en=120000'),
         B({case:'A-1',equity:50000},'?case=A-1&eq=bogus&en=9'), B({case:'A-1',equity:50000},'?case=A-1'),
         B({case:'A-1',equity:50000},'?case=A-1&eq=priced&en=-4000')];
var W=function(s){ var w=fcWhen(norm(s)); return w?w.text:null; };
o.when=[W('Tomorrow at 7 A.M'), W('Around 7 A.M'), W('around 7:30 pm'), W('I have 3 kids'), W('el lunes a las 3 de la tarde'),
        W('next Tuesday after five'), W('give me a call at 25'), W('this afternoon'), W('seven o’clock tonight')];
o.when_merge=fcWhenMerge(fcWhen('tomorrow'), fcWhen('around 7 a m')).text;
var REF=norm('That’s not a problem at all, honestly, I wasn’t expecting you to be. Almost everybody I reach about a place like yours says the exact same thing when I first call');
var V=function(s){ var v=fcVoice(norm(s), REF); return v.all+'/'+v.tail; };
o.voice=[V('honestly I wasn’t expecting you to be'), V('no thank you seriously I’m not interested'),
         V('almost everybody I reach about a place like yours says the exact same thing no thank you seriously I’m not interested'),
         V('ok'), V('honestly I was not expecting him to be')];
var LD=function(id,n,l,es){ var x=fcLadder(id,n,l,es); return x?x.who+'|'+x.say:null; };
o.ladder=[LD('notint',1,{},false), LD('notint',2,{auction:'2099-01-02'},false), LD('notint',2,{},false), LD('obj:v2',2,{},false),
          LD('notint',3,{},false), LD('notint',2,{auction:'2099-01-02'},true)];
var RW=function(a){ return a.map(function(x){ return {word:x[0], speaker:x[1]}; }); };
o.runs=[fcRuns(RW([['hi',0],['there',0],['no',1],['thanks',1]])), fcRuns(RW([['i',0],['help',0],['uh',1],['owners',0],['see',0]])),
        fcRuns(RW([['hello',null]])), fcRuns([])];
o.youvoice=[fcYouVoice({}), fcYouVoice({'0':1,'1':3}), fcYouVoice({'2':1})];
o.board_v=fcFacts(fcBoard({case:'A-1',equity:50000},'?case=A-1&eq=clear')).equity.v;
o.say=[fcFactSay('owe',none,false).say, fcFactSay('owe',rec,false).say, fcFactSay('equity',rec,false).say, fcFactSay('equity',ver,false).say,
       fcFactSay('sale',rec,false).say, fcFactSay('sale',none,false).say, fcFactSay('equity',unk,true).say, fcFactSay('owe',unk,true).say];
o.stage=[fcStageAfter(norm('hi maria can you hear me its alejandro with biscayne'), ['consent line here','hi maria can you hear me its alejandro with biscayne solutions','next'], 0),
         fcStageAfter(norm('totally different words spoken here today'), ['a','b','c'], 0),
         fcStageAfter(norm('hi maria can you hear me its alejandro with biscayne'), ['x','hi maria can you hear me its alejandro with biscayne solutions','next'], 2)];
console.log('@@'+JSON.stringify(o));
"""


def run(js):
    # stdin, not -e: the page script is longer than a Windows command line may be
    r = subprocess.run([node, '-'], input=js, capture_output=True, text=True, timeout=90, encoding='utf-8')
    line = [x for x in (r.stdout or '').splitlines() if x.startswith('@@')]
    if r.returncode or not line:
        return {'err': ((r.stderr or '') + (r.stdout or ''))[-600:]}
    return json.loads(line[-1][2:])


node = shutil.which('node')
if not node:
    print('SKIP JS checks: node not on PATH')
else:
    p = run(PURE.replace('__PURE__', pure))
    chk(not p.get('err'), 'matcher runs alone in node', p.get('err'))
    if not p.get('err'):
        chk(p['fresh_only'] == [None, 'keep'], 'only the newest words can fire a card', p['fresh_only'])
        chk(p['gap'] == ['callback', 'obj:talked', None] or p['gap'][:1] == ['callback'] and p['gap'][2] is None,
            'a 3+ word phrase survives up to two extra words, not more', p['gap'])
        chk(p['whole'] == [None, 'sit0'], 'whole words only', p['whole'])
        chk(p['pri'] == ['cb_window', 'stop', 'callback'], '"not at work" is a callback, "do not call me again" is a stop', p['pri'])
        chk(p['also'] == [None, 'atty_silent'], 'the two-part read needs both parts, across two lines', p['also'])
        chk(p['open'] == [True, False, False], 'a partial ending in "but" is mid-thought; "si" is an answer', p['open'])
        chk(p['f_none']['equity']['ok'] is False and p['f_none']['payoff']['ok'] is False and p['f_none']['judgment']['ok'] is False,
            'no data: equity, payoff and judgment all read not verified')
        chk(p['f_rec']['equity'] == {'v': 'Not verified', 'ok': False, 'src': 'do not quote a number'},
            'an equity figure with no verified state is not shown', p['f_rec']['equity'])
        chk(p['f_ver']['equity']['ok'] is True and p['f_ver']['equity']['v'] == '$88,000', 'a verified state shows the figure')
        chk(p['f_unk']['judgment']['ok'] is False and p['f_unk']['equity']['ok'] is False, 'judgment_unknown and a ceiling-only state stay unverified')
        chk(p['f_rec']['payoff']['ok'] is False and p['f_ver']['payoff']['ok'] is False, 'payoff is never verified from our data')
        s = p['say']
        chk('$' not in s[0] and 'verified number' in s[0], 'owed, nothing posted: no number')
        chk('$412,301' in s[1] and 'not today’s payoff' in s[1], 'owed, judgment recorded: the recorded figure, called what it is', s[1])
        chk('$' not in s[2] and 'not going to guess' in s[2], 'equity, unverified: no number', s[2])
        chk('$88,000' in s[3], 'equity, verified: the figure')
        chk('2099-01-02' in s[4] and '12 days' in s[4] and 'no sale date' in s[5].lower(), 'sale date from the calendar, or none')
        chk('$' not in s[6] and '$' not in s[7], 'Spanish answers follow the same rule')
        chk(p['when'] == ['tomorrow at 7 AM', 'around 7 AM', 'around 7:30 PM', None, 'el lunes a las 3 de la tarde',
                          'next tuesday after 5', None, 'this afternoon', 'tonight at 7'],
            'a day and time are read off the line; a bare number is not a time', p['when'])
        chk(p['when_merge'] == 'tomorrow around 7 AM', 'a later line adds the time to the day', p['when_merge'])
        chk(p['voice'][:4] == ['you/you', 'them/them', 'you/them', '/'],
            'words read off the screen are you; unfamiliar words are them, even at the end of your sentence', p['voice'])
        ld = p['ladder']
        chk(ld[0] is None and 'second pass' in ld[1] and '2099-01-02' in ld[1] and ld[1].rstrip().endswith('?'),
            'the same objection a second time gets a different line with the court sale date', ld[1])
        chk('$' not in ''.join(x or '' for x in ld) and 'sale set for' not in ld[2] and 'One question' in ld[3],
            'no date on file: no date said; other cards get the general second pass', [ld[2], ld[3]])
        chk('third time' in ld[4] and 'leave it there' in ld[4] and 'subasta' in ld[5], 'the third time lets them go; Spanish follows', [ld[4], ld[5]])
        chk(p['runs'] == [[{'spk': '0', 'text': 'hi there'}, {'spk': '1', 'text': 'no thanks'}], [{'spk': '0', 'text': 'i help uh owners see'}],
                          [{'spk': '', 'text': 'hello'}], []],
            'Deepgram words split where the voice changes; a one-word blip is folded in', p['runs'])
        chk(p['youvoice'] == [None, '1', '2'], 'you are the voice with the most script reads, nobody until one is heard', p['youvoice'])
        b = p['board']
        chk(b[0] == ['priced', 120000, True, True], "the board's quotable net replaces the raw estimate", b[0])
        chk(b[1] == ['clear', None, False, False] and 'no figure' in p['board_v'], 'a verified state with no quotable net shows no number', [b[1], p['board_v']])
        chk(b[2] == ['unpriced', None, False, False], 'a ceiling-only verdict drops the figure even if one is sent', b[2])
        chk(b[3] == [None, 50000, False, False], 'a verdict for another case is ignored', b[3])
        chk(b[4] == [None, 50000, False, False] and b[5] == [None, 50000, False, False], 'an unknown state or no verdict leaves the lead not verified', [b[4], b[5]])
        chk(b[6][2] is False and b[6][3] is False, 'a negative net is never spoken', b[6])
        cm = open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
        chk("'&eq=' + encodeURIComponent(r.eqc.st)" in cm and "r.eqc.q && r.eqc.n != null" in cm,
            'Call Mode hands the coach the equity verdict, the net only when quotable')
        chk(p['stage'] == [2, -1, -1], 'a read stage line names the next stage; other talk and earlier stages do not', p['stage'])

    o = run(HARNESS.replace('__SRC__', src))
    chk(not o.get('err'), 'the page script runs end to end in node', o.get('err'))
    if not o.get('err'):
        chk(o['partial_obj'][0] == 'on' and o['partial_obj'][1] and o['partial_obj'][2]['partial'] is True,
            'objection card is up on partial words', o['partial_obj'])
        chk(o['replaced'][0] and o['replaced'][0]['id'] == 'atty_silent' and o['replaced'][1] == '' and o['replaced'][2],
            'the rest of the sentence replaces the first card', o['replaced'])
        chk(o['replaced'][0] and 'next step' in (o['replaced'][0]['ask'] or [''])[0], 'and offers the question to ask next')
        chk(o['no_refire'] == 2, 'the final wording of the same sentence does not fire again', o['no_refire'])
        chk(o['held'] == [0, 'on'], 'a mid-thought partial waits and says it is listening', o['held'])
        chk(o['held_then'][0] == 1 and o['held_then'][1]['kind'] == 'callback' and o['held_then'][2],
            'then answers 0.9 s later when nothing more comes', o['held_then'])
        chk(o['crowd'] == 'obj:v7', '"so many people calling us" reaches the scam-fatigue card', o['crowd'])
        chk(o['notime'] and o['notime']['kind'] == 'callback' and 'Callback' in o['notime']['act'],
            '"I literally do not have time right now" reaches the callback card', o['notime'])
        chk(o['handled'][0] == 'obj:plan' and o['handled'][1] == '', '"we already got it handled I promise" is the plan card and no red flag', o['handled'])
        chk(o['flag_your'] == '' and o['flag_our'] == 'on', '"your attorney" is not flagged, "our attorneys" is', [o['flag_your'], o['flag_our']])
        chk(o['owe_none'] and o['owe_none']['id'] == 'owe' and '$' not in o['owe_none']['say'], 'asked what is owed with no lead: no number', o['owe_none'])
        chk(o['eq_none'] and o['eq_none']['id'] == 'equity' and '$' not in o['eq_none']['say'], 'asked what it is worth: no number', o['eq_none'])
        chk(o['paused_quiet'] != 'sit3', 'Pause holds ordinary prompts back', o['paused_quiet'])
        chk(o['stop'][0] and o['stop'][0]['kind'] == 'stop' and 'DO NOT CONTACT' in o['stop'][0]['act'] and 'stop' in o['stop'][1] and o['stop'][2] == '',
            'a stop request shows the stop card on partial words, paused or not, and no rebuttal card', o['stop'])
        chk(o['keep'] and o['keep']['id'] == 'keep' and len(o['keep']['ask']) == 2, 'keep-the-house gets two questions to choose from')
        chk(o['recap'] == 3, 'Catch me up shows the last three lines', o['recap'])
        chk(o['stale'] == [True, True], 'the card goes dim after two more lines', o['stale'])
        chk(o['tc_one'] == ['callback'], '"I have no time right now" shows one card, not the callback and the busy card', o['tc_one'])
        chk(o['tc_quiet'] == 1, 'talking about the call back does not fire it again', o['tc_quiet'])
        chk(o['tc_day'] and o['tc_day']['id'] == 'cb_time' and 'What time' in o['tc_day']['say'], '"Tomorrow" on partial words asks for the time', o['tc_day'])
        chk(o['tc_when'] and 'tomorrow at 7 AM' in o['tc_when']['say'] and 'tomorrow at 7 AM' in o['tc_when']['act'],
            '"Tomorrow at 7 A.M" is read back and named in the log step', o['tc_when'])
        chk('tomorrow around 7 AM' in o['tc_upd'][0] and o['tc_upd'][1] == ['callback', 'cb_time'] and o['tc_upd'][2] == 'tomorrow around 7 AM',
            '"Around 7 A.M" updates the same card and the saved record', o['tc_upd'])
        chk(o['tc_unarmed'] != 'cb_time', 'a day and time with no call-back request stays quiet', o['tc_unarmed'])
        chk(o['tc_same'] and o['tc_same']['id'] == 'cb_time' and 'tomorrow at noon' in o['tc_same']['say'],
            'a call-back request with its own time goes straight to the time card', o['tc_same'])
        chk(o['tc_stop'] == 'stop:stop', 'a stop request still wins inside the time window', o['tc_stop'])
        chk(o['lad_read'] == [0, 'you?'], 'reading the card aloud fires nothing and is tagged as you', o['lad_read'])
        chk(o['lad_2'] == [True, True, True, 2], 'they cut in with the same objection mid-card: a new second-pass line, on partial words', o['lad_2'])
        chk(o['lad_tag'] == 'them?', 'that mixed line is treated as theirs', o['lad_tag'])
        chk(o['lad_3'] == [True, True, 3], 'the third time shows the let-them-go line', o['lad_3'])
        chk(o['dg_first'] == [None, 'them?'], 'Deepgram: their hello first does not make them you', o['dg_first'])
        chk(o['dg_you'] == ['1', 'you'], 'Deepgram: the voice that reads the opener is you', o['dg_you'])
        chk(o['dg_you_quiet'] == 0 and o['dg_them_fire'] == ['obj:notint'],
            'Deepgram: trigger words in your voice raise nothing; in theirs, the card, on partial words', [o['dg_you_quiet'], o['dg_them_fire']])
        chk(o['dg_split'] == ['you', 'them'] and o['dg_stop'] == 'stop', 'Deepgram: one result with two voices is two lines, and their half is answered', [o['dg_split'], o['dg_stop']])
        chk(o['one_you'] == ['1', 'you?'], 'one voice: the Deepgram "you" is not believed, the content guess labels the line', o['one_you'])
        chk(len(o['one_busy']) == 1 and o['one_tag'] in ('them?', ''), 'one voice: "I do not have time" still raises the card', [o['one_busy'], o['one_tag']])
        chk(len(o['one_law']) == 1, 'one voice: "I already have a lawyer" still raises a card', o['one_law'])
        chk('one voice heard' in o['one_eng'], 'the engine line says Deepgram hears one voice', o['one_eng'])
        chk(o['one_join'] == ['I just wanted to ask what he showed you.', 'Okay.'], 'a sentence Deepgram cut in two is one transcript line', o['one_join'])
        chk('live speaker labels' in o['dg_eng'], 'the engine line says when your voice is known', o['dg_eng'])
        chk(o['stage'] == 2, 'reading the opener moves the script to the next stage', o['stage'])

print()
print('FAIL %d' % len(bad) if bad else 'ALL OK')
raise SystemExit(1 if bad else 0)
