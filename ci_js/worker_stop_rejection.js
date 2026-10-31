// Production Worker functions, fake clock and fetch: no network or real outbound actions.
const fs = require('fs');
const lines = fs.readFileSync(process.argv[2], 'utf8').split('\n');
const begin = lines.findIndex(l => l.startsWith('function genMorningWorker('));
let end = begin + 1;
while (end < lines.length && !/^function /.test(lines[end])) end++;
const source = lines.slice(begin, end).map(line => {
  const match = line.match(/^\s*\+\s*('.*')\s*$/);
  if (!match) return '';
  try { return eval(match[1]); } catch (_) { return ''; }
}).join('');
function fn(name) {
  const at = source.indexOf('function ' + name + '(');
  if (at < 0) throw Error('missing ' + name);
  for (let i = source.indexOf('}', at); i > 0; i = source.indexOf('}', i + 1)) {
    const body = source.slice(at, i + 1);
    try { new Function(body); return body; } catch (_) {}
  }
  throw Error('unbalanced ' + name);
}
const vars = (source.match(/var sending=false[^;]*;/) || [])[0];
const code = [vars, ...['sendInFlight', 'doSend', 'post', 'advance', 'stopRun', 'pause',
  'startRun', 'runOne', 'setLane'].map(fn)].join('\n');
const lead = (lane, i) => ({c:'SYNTHETIC-' + lane + i, first:'Synthetic',
  mailSubj:'Synthetic subject', mailBody:'Synthetic body',
  mailTo:lane + i + '@example.invalid', gurl:'https://example.invalid/compose', portfolio:[]});

async function scenario(action, answer = 'reject') {
  let now = 0, timers = [];
  const setT = (f, ms) => { const t = {at:now + (ms || 0), f}; timers.push(t); return t; };
  const clearT = t => { timers = timers.filter(x => x !== t); };
  const requests = [], marks = [], logs = [];
  const fetch = (url, opts) => {
    const body = JSON.parse(opts.body), index = requests.length;
    requests.push(body.meta.c);
    return new Promise((resolve, reject) => setT(() => {
      if (!index && answer === 'reject') return reject(Error('synthetic offline'));
      resolve({status:200, json:() => Promise.resolve({ok:true, message_id:'synthetic'})});
    }, 2000));
  };
  const env = `
    var LANES={urgent:U,active:V}, Q=LANES.urgent, i=0, auto=false, autoAll=false, lane='urgent',
        tbOn=false, ASTIMER=null, ASBOX=null, tick=null, healing=null, nextStep=null,
        awaitingReturn=false, MSEQ=0, BRIDGE_OK=true, BRIDGE_HOLD='', BOUNCE=null,
        TBSENT=0,TBSKIP=0,TEXTQ=[],TBI=0,STATS={};
    var LMETA={urgent:{t:'Urgent'},active:{t:'Active'}}, LANE_ORDER=['urgent','active'],CAP={max:999};
    var window={opener:{closed:false,postMessage:function(m){marks.push(m.workerAct);}},__cfWatch:null};
    var document={querySelectorAll:function(){return [];},querySelector:function(){return null;}};
    var SYNCWAIT=null,SYNCTRY=0,SYNC_MAX=3,SYNCALL=false;
    function cancelSyncWait(){} function schedSync(){} function probeBridge(){}
    function render(){} function renderRunBar(){} function bridgeUp(){} function renderBridge(){}
    function _cacheLive(){} function hopNextLane(){return false;} function sentToday(){return 0;}
    function _pendWatch(){} function _markBuf(){} function runlog(){}
    function openHere(){logs.push('COMPOSER');return true;} function confirmSend(){logs.push('CONFIRM');}
    function addLog(k,who,msg){logs.push(k+':'+msg);} function attempt(r){doSend(r);}
  `;
  const run = new Function('U','V','marks','logs','fetch','setTimeout','clearTimeout',
    'clearInterval','Date', env + code + `
    return {start:startRun,stop:stopRun,lane:setLane,manual:function(){doSend(Q[i]);},
      state:function(){return {lane:lane,i:i,auto:auto,c:(Q[i]||{}).c};}};`);
  const api = run([lead('U',0),lead('U',1)], [lead('V',0),lead('V',1)], marks,logs,
    fetch,setT,clearT,clearT,{now:() => now});
  const flush = async () => { for (let i=0;i<6;i++) await new Promise(r => setImmediate(r)); };
  const step = async to => {
    while(now < to) {
      now += 50;
      const due = timers.filter(t => t.at <= now);
      timers = timers.filter(t => t.at > now);
      due.forEach(t => t.f());
      await flush();
    }
  };
  if (action === 'manual') api.manual(); else api.start(false,'test');
  await step(100);
  if (action === 'stop' || action === 'repeat' || action === 'resume') api.stop('test');
  if (action === 'repeat') api.stop('test again');
  if (action === 'lane' || action === 'laneStart') api.lane('active');
  if (action === 'laneStart') api.start(false,'pending old request');
  await step(4000);
  const settled = api.state();
  if (action === 'resume' || action === 'laneStart') {
    api.start(false,'new run after rejection');
    await step(12000);
  }
  return {settled,state:api.state(),requests,marks,logs};
}

(async () => {
  let passed = 0, failed = 0;
  function check(label, ok, detail) {
    if(ok) passed++; else failed++;
    console.log((ok?'PASS ':'FAIL ') + label + (ok?'':' :: ' + JSON.stringify(detail)));
  }
  for (const action of ['stop','repeat','lane','laneStart','resume','active']) {
    const out = await scenario(action);
    check(action + ': rejected automatic request never opens a manual composer',
      !out.logs.includes('COMPOSER'), out);
    check(action + ': rejection never records an email attempt or delivery',
      out.marks.filter(m => m.k==='email').length===0 &&
      out.marks.filter(m => m.k==='sent').length ===
        (action==='resume'||action==='laneStart' ? 2 : 0), out.marks);
    if(action==='stop'||action==='repeat'||action==='active')
      check(action + ': run remains stopped on original queue entry',
        !out.settled.auto && out.settled.i===0, out.settled);
    if(action==='lane'||action==='laneStart')
      check(action + ': late error never advances or starts the replacement lane',
        out.settled.lane==='active' && out.settled.i===0 && !out.settled.auto, out.settled);
    if(action==='laneStart') check('new lane run starts once after old request settles',
      out.requests.join(',')==='SYNTHETIC-U0,SYNTHETIC-V0,SYNTHETIC-V1',out.requests);
    if(action==='resume') check('resume after rejected request works each remaining lead once',
      out.requests.join(',')==='SYNTHETIC-U0,SYNTHETIC-U0,SYNTHETIC-U1',out.requests);
  }
  const manual = await scenario('manual');
  check('genuine manual failure keeps the composer fallback', manual.logs.includes('COMPOSER') &&
    manual.marks.filter(m=>m.k==='email').length===1 && !manual.marks.some(m=>m.k==='sent'),manual);
  const delivered = await scenario('stop','success');
  check('in-flight success after Stop still records its truthful delivery exactly once',
    delivered.marks.filter(m=>m.k==='sent').length===1 && delivered.requests.length===1 &&
    !delivered.state.auto && delivered.state.i===1,delivered);
  console.log(passed + ' passed / ' + failed + ' failed');
  process.exit(failed?1:0);
})();
