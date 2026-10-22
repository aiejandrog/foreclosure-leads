// Execute shipped Worker functions with fake responses/timers; no network or real contacts.
const fs = require('fs');
const vm = require('vm');
const lines = fs.readFileSync(process.argv[2], 'utf8').split('\n');
const a = lines.findIndex(l => l.startsWith('function genMorningWorker('));
let b = a + 1; while (b < lines.length && !/^function /.test(lines[b])) b++;
const src = lines.slice(a,b).map(l => {
  const m=l.match(/^\s*\+\s*('.*')\s*$/); if(!m)return ''; try{return vm.runInNewContext(m[1]);}catch(e){return '';}
}).join('');
function fn(name){
 const start=src.indexOf('function '+name+'(');
 if(start<0) throw new Error('missing '+name);
 for(let j=src.indexOf('}',start);j>=0;j=src.indexOf('}',j+1)){
  const part=src.slice(start,j+1); try{new Function(part);return part;}catch(e){}
 }
 throw new Error('unbalanced '+name);
}
const vars=(src.match(/var sending=false[^;]*;/)||[])[0];
const replies=[
 [200,{ok:true,message_id:'synthetic'}],
 [409,{skip:true}],
 [403,{blocked:'evidence',skip:true}],
 [500,{err:'synthetic failure'}]
];
let failed=0;
(async()=>{
 for(const [status,body] of replies) for(const action of ['stay','lane','stop']){
  let timers=[],advanced=0,marks=[];
  const lead={c:'SYNTHETIC-A',first:'Synthetic',mailTo:'test@example.invalid',mailSubj:'x',mailBody:'x'};
  const env={Q:[lead],i:0,lane:'urgent',auto:true,autoAll:false,BRIDGE_HOLD:'',BOUNCE:null,
   SYNCWAIT:null,SYNCTRY:0,SYNC_MAX:3,STATS:{},tbOn:false,ASTIMER:null,ASBOX:null,
   tick:null,healing:null,nextStep:null,awaitingReturn:false,TBSENT:0,TBSKIP:0,TEXTQ:[],TBI:0,
   LMETA:{urgent:{t:'Urgent'}},window:{},Date,
   setTimeout:(f,ms)=>{const t={f,ms};timers.push(t);return t;},
   clearTimeout:t=>{timers=timers.filter(x=>x!==t);},clearInterval:()=>{},
   fetch:async()=>({status,json:async()=>body}),
   addLog:()=>{},runlog:()=>{},bridgeUp:()=>{},renderBridge:()=>{},_cacheLive:()=>{},
   cancelSyncWait:()=>{},render:()=>{},post:(kind,extra)=>marks.push([kind,extra.qc]),
   advance:()=>advanced++,openHere:()=>{throw Error('unexpected composer');},confirmSend:()=>{}
  };
  vm.createContext(env);
  vm.runInContext(vars+fn('sendInFlight')+fn('doSend')+fn('stopRun'),env);
  env.doSend(lead);
  for(let n=0;n<6;n++)await new Promise(r=>setImmediate(r));
  if(action==='lane'){env.Q=[{c:'SYNTHETIC-B'}];env.i=0;env.lane='active';}
  if(action==='stop')env.stopRun('test');
  const due=timers.filter(t=>t.ms<=1000);timers=timers.filter(t=>t.ms>1000);due.forEach(t=>t.f());
  const want=action==='stay'?1:0;
  const ok=advanced===want&&marks.length===1&&marks[0][1]===lead.c;
  console.log((ok?'PASS':'FAIL')+' '+status+' '+action+': advanced '+advanced+', expected '+want);
  if(!ok)failed++;
 }
 process.exitCode=failed?1:0;
})().catch(e=>{console.error(e);process.exitCode=1;});
