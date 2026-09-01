
var ENC={"stub": 1}, OUTCOMES=[{"k": "noanswer", "t": "No answer", "h": 24, "s": false}, {"k": "voicemail", "t": "Left voicemail", "h": 24, "s": false}, {"k": "talked", "t": "Talked", "h": 72, "s": false}, {"k": "appt", "t": "APPOINTMENT SET", "h": 72, "s": false}, {"k": "wrong", "t": "Wrong number", "h": 0, "s": true}, {"k": "notint", "t": "Not interested", "h": 72, "s": false}, {"k": "dnc", "t": "DNC \u2014 do not contact", "h": 0, "s": true}], BUILT="2026-09-01T00:00", SIG="sig", BSIG="bsig";
var BOOKURL="https://cal.com/bsgflorida/free-records-review";
/* Person-keyed send counts from the server ledger. Authoritative across DEVICES and across cases
   that never shipped to this phone — without it a fresh phone reads every owner as never-texted and
   restarts the 3-touch ladder at touch 1, which is exactly the shape of the August email incident. */
var TEXTPERSON={};
var SHOWN=0, TOTAL=0, VMEN="Hi {first}, this is {sender} with Biscayne Solutions Group, about {st1}. You may have a plan. Keep it. A plan can land a day late, and here a day is everything. Our senior advisor, thirty plus years, maps your free backup in five minutes. Call me any hour at {phone}. Thanks.", VMES="Hola {first}, le habla {sender} de Biscayne Solutions Group, por {st1}. Si tiene un plan, s\u00edgalo. Un plan puede llegar un d\u00eda tarde, y aqu\u00ed un d\u00eda lo es todo. Nuestro asesor principal, m\u00e1s de treinta a\u00f1os, le arma su respaldo gratis en cinco minutos. Ll\u00e1meme a cualquier hora al {phone}. Gracias.";
var LS='fcLeadNotes', ROWS=[], PHIDX=null, lane='soon', i=0, cur=null, phIdx=0, notes={};
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function fmt(d){d=String(d||'');return d.length===10?'('+d.slice(0,3)+') '+d.slice(3,6)+'-'+d.slice(6):d;}
/* WHICH LINE THE CALL GOES OUT ON (2026-09-01). 'gv' routes every dial through the Google Voice
   app so the call originates from (786) 490-7825 — the DIALING line — instead of the phone's own
   carrier line, (786) 631-1823. That split is deliberate and load-bearing: 631-1823 is the number
   printed on the site, the cards and every letter, and 25-50 cold dials a day is exactly the
   traffic pattern that gets a number tagged "Spam Likely" by the carriers. If the GV line gets
   burned, Google swaps it for $10 and nothing printed changes; if the PUBLISHED line got burned,
   every card already handed out would be dialing a poisoned number. Set to 'tel' to fall back to
   the plain dialer (one character, next build).
   The GV deep link opens the Voice app on a phone that has it installed and signed into the
   agonzalez account; the number after nc, is the DESTINATION — Voice supplies the caller id.
   SWITCHED TO 'tel' 2026-09-01, SAME DAY, for Quo (786) 502-9550 — and the reasoning moved, not
   died. Quo (OpenPhone rebranded) registers as the PHONE'S default calling app, so a plain tel:
   link routes through Quo natively and the call originates from the Quo line. That beats a
   per-vendor deep link three ways: no URL scheme to break when a vendor renames itself (Quo just
   did), the outcome-logging page never navigates away (tel: opens the dialer OVER the page), and
   swapping dialing vendors becomes a SETTING ON THE PHONE instead of a build. The spam-split
   architecture is unchanged: published line (786) 631-1823 stays clean; the dialing line eats the
   volume risk. Requires: Quo app installed + set as the phone's default calling app — without
   that, tel: falls back to the carrier dialer and dials expose the published line, so CHECK THE
   DEFAULT-APP SETTING before a dial session. Set 'gv' to route via Google Voice deep links. */
var DIALER='tel';
function dialHref(d){return DIALER==='gv' ? 'https://voice.google.com/u/0/calls?a=nc,%2B1'+String(d) : 'tel:+1'+String(d);}
/* tel: opens the dialer OVER the page; an https link would navigate AWAY from it — and the whole
   outcome-logging flow (screenOutcome, the after-call bar) lives on this page. So GV dials open in
   a separate tab/app and this page stays put, same as tel: always behaved. */
function dialTarget(){return DIALER==='gv' ? ' target="_blank" rel="noopener"' : '';}
function today(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function nowTS(){return new Date().toLocaleString();}
/* WHO IS SPEAKING. The board keeps identity in SENDER_DEFAULTS + localStorage.fcSender; this page is
   the SAME ORIGIN so it reads the same store rather than baking a second copy that could drift.
   Two heals copied from the board because both were real: a company name saved into `name` renders
   "this is Biscayne Solutions Group with Biscayne Solutions Group", and the legacy auto-injected "Jose"
   must not win over the real identity. Empty saved values must never clobber the default. */
var SENDER = {name:'Alejandro Gonzalez', phone:'(786) 631-1823'};
try{
  var _sv = JSON.parse(localStorage.getItem('fcSender')||'{}');
  if(_sv.name === 'Jose') delete _sv.name;
  if(typeof _sv.name === 'string' && /^\s*miami\s+solutions\s+group\b/i.test(_sv.name)) delete _sv.name;
  ['name','phone'].forEach(function(k){ if(_sv[k]) SENDER[k] = _sv[k]; });
}catch(e){}

/* Fill a script template. Global replace — String.replace with a string argument only swaps the
   FIRST match, which is how {sender} survived into a live read-aloud script. */
/* A USABLE HUMAN FIRST NAME, or ''. Mirrors the board's _smsFirst.
   Returns '' when the "owner" is a company, a placeholder, or a name we have already been told not
   to use — greeting someone by the wrong name is worse than not greeting them at all, and on a
   mismatch lead the card explicitly says do not open with it. */
var _NOTNAME = ['UNKNOWN','OWNER','TENANT','OCCUPANT','ESTATE','TRUST','TRUSTEE','HEIRS','TITLE',
                'SEARCH','VIA','THE','LLC','INC','CORP','COMPANY','PROPERTIES','HOLDINGS'];
function firstName(r){
  if(!r) return '';
  if(has(r,'C')) return '';                 // company / trust owner
  if(r.po) return '';                       // roll owner changed — the card says do not use this name
  /* Prefer `on` (owner_clean), which the pipeline has ALREADY flipped from the county roll's
     "LAST,FIRST" to "First Last". Reading r.o directly took the token before the comma — the
     surname — on 32% of leads. Fall back to r.o only when `on` is absent, and strip anything before
     a comma first so the fallback cannot reintroduce the same bug. */
  var src = String(r.on || '').trim();
  if(!src){
    src = String(r.o||'').split(';')[0];
    if(src.indexOf(',') >= 0) src = src.split(',').slice(1).join(' ');   // "LAST,FIRST" -> "FIRST"
  }
  var raw = src.replace(/[^A-Za-z '-]/g,' ').trim();
  if(!raw) return '';
  var tok = raw.split(/\s+/)[0].toUpperCase();
  if(tok.length < 2 || _NOTNAME.indexOf(tok) >= 0) return '';
  return tok.charAt(0) + tok.slice(1).toLowerCase();
}

function fillScript(t, r){
  var first = firstName(r);
  return String(t||'')
    .split('{first}').join(first ? (' ' + first).trimEnd() : '')
    .split('{sender}').join(SENDER.name || '')
    .split('{street}').join((r && r.a) || 'your property')
    /* Street line only. A text is 160-char segments and reads aloud in the recipient's head — the
       full "1240 NW 54 ST, MIAMI, FL 33142" costs a segment and sounds like a mail merge. Falls back
       to the whole string when there is no comma, and to 'your property' when there is nothing. */
    .split('{st1}').join(((r && r.a) || '').split(',')[0].trim() || 'your property')
    /* {date} — the email's strongest line is the owner's own sale date, so the call says it too.
       Renders the whole clause (with its leading space) or NOTHING: an LP lead has no auction date
       and 9999 is the no-date sentinel, so a bare "{date}" would read as "sale date of ." aloud. */
    .split('{date}').join(
      (r && r.x && r.d != null && r.d < 9000) ? (' with a sale date of ' + r.x) : '')
    .split('{phone}').join(SENDER.phone || '')
    .replace(/\s{2,}/g, ' ').replace(/\s+,/g, ',').trim();
}

function loadNotes(){try{notes=JSON.parse(localStorage.getItem(LS)||'{}');}catch(e){notes={};}}
function saveNotes(){try{localStorage.setItem(LS,JSON.stringify(notes));}catch(e){
  /* A swallowed setItem failure (private mode, quota) lost every log while the UI kept stamping
     green checks. Say it, loudly, and put it in the error chip. */
  logErr(e,'saveNotes');
  try{ toast('⚠ NOT SAVED — phone storage is full or blocked'); }catch(_e){}
}}
/* NAME BRIDGE for the extracted board code. The block below is lifted verbatim from
   tracker_template.html and calls the board's helper names — `save()`, `_nowTS()`, `_today()`. This
   page had its own `nowTS`/`today`, so without these aliases `_mergeLead` throws ReferenceError the
   first time a teammate's DO-NOT-CONTACT merges in: the merge dies, the opt-out never lands, and the
   only symptom is a lead that stays dialable. Caught by exercising _mergeLead in a browser — it is
   invisible to `node --check`, which is why the parse guard alone is not enough here. */
function save(){ saveNotes(); }
function _nowTS(){ return nowTS(); }
function _today(){ return today(); }
/* syncFreshness paints a staleness class onto the board's #syncbtn. The phone has no such button,
   and the board's own implementation returns early when it is absent — so a no-op here is not a stub
   that loses behaviour, it IS the behaviour. It must still EXIST: mergeNotes calls it unconditionally
   as its last statement, so an undefined name threw even when there was nothing to merge. */
function syncFreshness(){}
/* ══════ MERGE + TEAM SYNC — extracted VERBATIM from tracker_template.html at build time ══════
   Not copied. Copying would drift: the phone would merge by last year's rules while the board moved
   on, and the divergence would surface as outcomes that quietly fail to reconcile — precisely the
   silent failure this page exists to delete. The build fails loud if the anchors move.
   Storage key is the board's own `fcLeadNotes` on the same origin, so there is one store, not two. */

/* ══════════════════════════════════════════════════════════════════════════════════════════ */
/* syncStatus writes a one-line status into the page. The board targets its own element; here it goes
   to the same #sync line the gate and queueSync already use, so a sync failure is VISIBLE rather than
   swallowed. Guarded because syncPull calls it before #sync exists on the very first paint.
   🔴 DECLARED AFTER the extracted block ON PURPOSE. The block ships its OWN syncStatus (targeting a
   #syncstat element this page does not have), and with two same-name function declarations in one
   script the LATER one wins — this bridge sat above the injection point and was silently dead, the
   same override class as _dialedAfter. Moving it back above the injection point kills it again
   (and _assert_no_dead_overrides now fails the build if anyone tries).
   NB: never write the literal injection placeholder token inside a comment here — Python's
   str.replace substitutes EVERY occurrence, so the whole 18KB block got injected into the middle
   of this very comment once, shredding the page's syntax. */
function syncStatus(msg){ var el=$('sync'); if(el && msg) el.textContent=msg; }

/* ---- gate: same code, same envelope as the board. fcPw is SHARED (same origin) so a phone that
   already unlocked the board never sees this screen. ---- */
/* Envelope must match foreclosure_leads._encrypt_multi EXACTLY:
     {enc:2, it, iv, ct, keys:[{salt, iv, ct}]}
   One random master key encrypts the payload once; that master key is then wrapped under each
   person's code. The wrapped blob is NOT raw key bytes — it is JSON {mk:<base64>, name:<label>},
   so it must be parsed and the mk base64-decoded before it can be imported.
   ⚠️ Codes written in site.codes as `Label = CODE | PHRASE` wrap under CODE + \x1f + PHRASE. This
   single-field gate therefore opens individual codes only; a phrase-protected team code has to be
   entered on the board first (which shares fcPw with this page anyway). */
async function unwrap(code){
  var te=new TextEncoder();
  var km=await crypto.subtle.importKey('raw',te.encode(code),'PBKDF2',false,['deriveKey']);
  var keys=ENC.keys||[];
  for(var n=0;n<keys.length;n++){
    var w=keys[n];
    try{
      var wk=await crypto.subtle.deriveKey(
        {name:'PBKDF2',salt:b2u(w.salt),iterations:ENC.it||200000,hash:'SHA-256'},
        km,{name:'AES-GCM',length:256},false,['decrypt']);
      var blob=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(w.iv)},wk,b2u(w.ct));
      var meta=JSON.parse(new TextDecoder().decode(blob));      // {mk, name}
      /* WHO IS CALLING. Each teammate has their OWN access code, and the code's blob carries their
         name — so the page already knows whether this is Alejandro, Jose or Carlos without asking.
         Stamped onto every call/text this device logs, which is what makes the shared registry
         able to say "Carlos called this one 2h ago" instead of an anonymous timestamp. */
      try{ if(meta.name) localStorage.setItem('fcCaller', meta.name); }catch(e){}
      var mk=await crypto.subtle.importKey('raw',b2u(meta.mk),{name:'AES-GCM'},false,['decrypt']);
      var pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(ENC.iv)},mk,b2u(ENC.ct));
      return JSON.parse(new TextDecoder().decode(pt));
    }catch(e){}
  }
  return null;
}
function b2u(b64){var s=atob(b64),a=new Uint8Array(s.length);for(var i=0;i<s.length;i++)a[i]=s.charCodeAt(i);return a;}

async function boot(){
  loadNotes();
  /* PAYLOAD SHAPE. It used to be a bare rows array; it is now {r:rows, x:phoneIndex} so the lookup
     can resolve numbers that are NOT among the dialable rows (67% of them). Accept BOTH shapes —
     an older decrypted copy can still be sitting in this device's cache. */
  var take=function(p){ if(!p) return null;
    if(Array.isArray(p)){ ROWS=p; PHIDX=null; return p; }
    ROWS=p.r||[]; PHIDX=p.x||null; return ROWS; };
  var saved=null; try{saved=localStorage.getItem('fcPw');}catch(e){}
  if(saved){ var r=await unwrap(saved); if(take(r)){ return start(); } }
  $('go').onclick=async function(){
    var c=$('code').value.trim(); if(!c) return;
    $('gmsg').textContent='Checking…';
    var r=await unwrap(c);
    if(!r){ $('gmsg').textContent='That code did not open this page. If it is new, the page may be cached — close the tab fully and reopen.'; return; }
    try{localStorage.setItem('fcPw',c);}catch(e){}   // unlocking either page unlocks both
    take(r); start();
  };
}
/* THE MORNING WORKER'S QUEUE. `fcCallQueue` is a durable localStorage ledger the worker fills when a
   lead is phone-only — same origin, so this page reads it directly with no new transport. Its own
   comment in the board records why it is durable: "before this, 192 queued calls evaporated with the
   tab and the week produced exactly one dial."
   Entries retire only on a logged call outcome, which is exactly what this page does.

   THIS WAS STILL DEVICE-LOCAL (found 2026-08-23). fcCallQueue lives in the browser that built it —
   the unattended Morning Worker tab on the laptop — and team sync only ever carried `notes`, never
   this array. 333 leads queued in 7 days, 1 dialled: the queue this function reads was empty on
   every phone, every time, because nothing had ever written to ITS localStorage. `.wq` on a case's
   own (synced) note is the fix — see _mergeLead in the extracted sync block below — so union both
   sources here: fcCallQueue for same-device continuity, `.wq` for whatever arrived from a teammate
   or the laptop. */
function workerQ(){
  var s = {};
  try{ (JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[]).forEach(function(x){ s[x.c]=1; }); }catch(e){}
  /* wqx = wq's retire TOMBSTONE (2026-08-26). The first fix DELETED .wq on retire, but _mergeLead
     is add-only for wq — the other device's surviving copy re-infected this lane on the next pull,
     so a lead someone had already worked came back as "queued" after its cooldown. A deletion
     cannot win an add-only merge; a newer tombstone can. Show a case only when its wq is strictly
     newer than any wqx: re-queueing later still works (new wq > old wqx), and a same-day tie goes
     to the retire — worked today is not offered again today. */
  for(var k in notes){
    var n = notes[k];
    if(k.charAt(0)!=='#' && n && n.wq && !(n.wqx && n.wqx >= n.wq)) s[k]=1;
  }
  return Object.keys(s);
}
function retireFromWorkerQ(caseId){
  try{
    var q = JSON.parse(localStorage.getItem('fcCallQueue')||'[]')||[];
    var n = q.filter(function(x){ return x.c !== caseId; });
    if(n.length !== q.length) localStorage.setItem('fcCallQueue', JSON.stringify(n));
  }catch(e){}
  var nn = notes[caseId];
  if(nn && nn.wq) nn.wqx = today();
}
/* CLIENT-SIDE SUPPRESSION — the read-back the first version was missing.
   Build-time filtering catches opt-outs and stays that existed when the page was BUILT. Everything
   decided since then lives only in notes: a wrong-number logged this morning, a DNC a teammate
   synced an hour ago, a person-level opt-out keyed to an email or phone. Without reading these back,
   the page happily re-serves a lead somebody already disproved — which is how a stranger gets called
   twice and how an opt-out gets violated by the device that recorded it. */
/* Digits of every phone recorded as opted out under a '#number' note key rather than a case.
   The board writes these (optout_sync.py, the STOP-reply loop) and they match no case on their own,
   so without this lookup they suppress nothing at all.

   Deliberately NOT memoized on the notes object's identity, the way the board memoizes
   _optedOutIdentities: mergeNotes assigns `notes[caseId] = merged` IN PLACE, so the object is
   identical after a teammate syncs an opt-out and an identity memo would never invalidate. A stale
   index here means the phone keeps dialing someone who just opted out — precisely what it exists to
   prevent. pool() rebuilds it on every call instead; that is O(notes) once, not per row. */
var _OPTPH = null;
function optPhones(force){
  if(_OPTPH && !force) return _OPTPH;
  var s = {};
  for(var k in notes){
    if(k.charAt(0) !== '#') continue;
    var nn = notes[k] || {};
    if(nn.optout || nn.status === 'DO NOT CONTACT') s[k.slice(1).replace(/\D/g,'')] = 1;
  }
  _OPTPH = s;
  return s;
}
/* Who is on this phone — set from the access-code blob at unlock (each teammate has their own
   code). Falls back to a neutral label so a touch is never stamped with the wrong person. */
function caller(){
  try{ return localStorage.getItem('fcCaller') || ''; }catch(e){ return ''; }
}
/* THE SHARED REGISTRY, read side. Newest CALL touch on this lead from ANY device, with who made
   it. Team sync merges teammates' notes into the same store, so this sees Carlos's dials too. */
function lastCall(n){
  var best = null;
  ((n && n.touches) || []).forEach(function(t){
    if((t.ch||'') !== 'call') return;
    var ts = +t.tsu || +new Date(t.ts || t.d || 0) || 0;
    if(!ts) return;
    if(!best || ts > best.ts) best = {ts: ts, out: t.out || '', by: t.by || ''};
  });
  ((n && n.dials) || []).forEach(function(d){
    var ts = +d.tsu || 0;
    if(ts && (!best || ts > best.ts)) best = {ts: ts, out: d.oc || '', by: d.by || ''};
  });
  return best;
}
function agoTxt(ms){
  var m = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if(m < 60) return m + 'm ago';
  var h = Math.round(m / 60);
  if(h < 36) return h + 'h ago';
  return Math.round(h / 24) + 'd ago';
}
/* HARD suppression — relationship-ending reasons ONLY (opt-out, DNC, wrong number, dead lead,
   opted-out phone). These block EVERYTHING, including the after-call text. Kept separate from
   the queue cooldown below because the two answer different questions: "may we contact this
   person at all?" versus "is this lead due to be dialled again?". */
function hardSuppressed(r){
  var n = notes[r.c] || {};
  if(n.wrongown) return 'wrong number reported';
  if(n.optout || n.status === 'DO NOT CONTACT') return 'opted out';
  if(n.status === 'Dead') return 'dead';
  var ph = optPhones(), p = r.p || [];
  for(var j=0;j<p.length;j++) if(ph[p[j]]) return 'this number opted out';
  return '';
}
function suppressed(r){
  var h = hardSuppressed(r);
  if(h) return h;
  var n = notes[r.c] || {};
  /* ALREADY WORKED — by me OR by a teammate. Without this, Carlos logs a call, the note syncs to
     this phone, and the lead still sits in the queue waiting to be dialled a second time by the
     other guy. The cooldown is the SAME outcome-aware one the board honours (logOutcome writes
     n.cooldownH: no-answer comes back tomorrow, a real conversation waits longer), so the two
     surfaces can never disagree about whether a lead is due.
     QUEUE-ONLY. This branch must never gate the after-call text: the call HE JUST LOGGED is
     inside its own cooldown by definition, so using suppressed() there read back "called 0m ago
     by Alejandro · Do not text" — his own dial blocking the missed-call text, the single best
     moment to send one (2026-08-19 field report). afterCall gates on hardSuppressed(). */
  var lc = lastCall(n);
  if(lc){
    var coolH = (typeof n.cooldownH === 'number' && n.cooldownH >= 0) ? n.cooldownH : 24;
    if((Date.now() - lc.ts) < coolH * 3600000){
      return 'called ' + agoTxt(lc.ts) + (lc.by ? ' by ' + lc.by : '')
           + (lc.out ? ' · ' + lc.out : '');
    }
  }
  return '';
}
/* Do-Not-Text is text-only and must NOT hide a lead from CALLING — but a number on it should not be
   the one we put first. Kept separate from suppressed() on purpose. */
function dntSet(){
  try{ return new Set(JSON.parse(localStorage.getItem('fcDNT')||'[]')); }catch(e){ return new Set(); }
}

/* pool() is the ONE place suppression is evaluated. Everything else reads what it left behind.

   Two reasons this is not just tidiness. First, `suppressed()` depends on the opt-out phone index,
   which must be rebuilt from live notes — a caller that skipped the rebuild would silently judge a
   lead against a stale index and keep dialing someone who just opted out. Second, head() used to run
   its own full pass over ROWS to count hidden leads, doubling the per-paint cost for a number pool()
   already knew. Both problems disappear if there is exactly one evaluator. */
var _SUPN = 0;
/* Case ids that got at least one logged outcome this session. Deduped, because a lead dialled on
   three numbers is one lead worked, not three. */
var _WORKED = [];
/* THE buy-box predicate — one function, used by both the lane and the button count. Those were
   two identical inline copies with a comment promising they could never disagree; a promise kept
   by hand is a promise that breaks the first time only one copy is edited, and adding the
   tax-deed test was exactly that edit. */
function isBuyBox(r){ return !!r.bb && r.bbs !== 'UNDERWATER' && !r.bbtd; }
/* ══════════════════ TEAM SEATS — two phones, one list, nobody dialled twice ══════════════════
   fcSeat = {n: <how many callers>, i: <which one am I, 0-based>, w: <my name>}. Absent or n<=1
   means solo and every filter below is a no-op, so a lone caller sees exactly what he saw before.

   THE PARTITION IS THE DEFENCE. r.sb is a stable 0-11 bucket of the case number stamped at build
   (see call_rows). With n seats I take the buckets where sb % n === i, so with two phones the
   same lead is NEVER in both queues at once. Nothing is negotiated, so there is nothing to race
   and nothing to be offline for.

   THE CLAIM IS THE EXCEPTION HANDLER. It rides the existing encrypted note sync, which pulls
   every 45s, so it CANNOT be the primary defence — two people opening the same lead inside the
   same 45 seconds both see it unclaimed. It exists for deliberate out-of-lane work and for
   "he's already on the phone with this one", and it is advisory: it greys and warns, it does not
   lock anyone out.

   CLAIMS EXPIRE. A claim with no TTL turns one abandoned tap into a lead nobody may ever call
   again — the silent-suppression failure this codebase keeps re-learning. 90 minutes, and an
   outcome clears it outright. */
var SEAT_TTL_MS = 90*60*1000, SEAT_ALL = false;
function _seat(){ try{ var s=JSON.parse(localStorage.getItem('fcSeat')||'{}');
    return (s && s.n>1 && s.i>=0 && s.i<s.n) ? s : null; }catch(e){ return null; } }
function _seatSet(n, i, w){
  if(!n || n<=1){ localStorage.removeItem('fcSeat'); }
  else localStorage.setItem('fcSeat', JSON.stringify({n:n, i:i, w:(w||'').slice(0,18)}));
  i=0; try{ render(); }catch(e){}
}
function _seatMine(r){ var s=_seat(); if(!s || SEAT_ALL) return true;
  if(typeof r.sb !== 'number') return true;    // unstamped row (older build) -> visible to all,
  return (r.sb % s.n) === s.i; }               // never silently dropped from every queue at once
/* Who holds a live claim on this case, or '' — MY OWN claim never counts against me. */
function _clmOwner(c){
  var nt = notes[c]; if(!nt || !nt.clm) return '';
  var k = nt.clm; if(!k.d || k.d === (typeof _deviceId==='function' ? _deviceId() : '')) return '';
  /* AN OUTCOME ENDS THE CLAIM. Checked against the fields this page actually writes — n.status
     and a logged call via lastCall() — not against invented ones. Written as nt.out/nt.outcome
     first, which no code path ever sets, so the branch could never fire and the claim would have
     hung on for the full TTL after the lead was already worked. suppressed() would have hidden
     the row anyway, so nothing would have LOOKED wrong; it would just have quietly held a lead
     out of the other caller's "show all" for 90 minutes. */
  if(nt.status) return '';
  try{ if(typeof lastCall==='function' && lastCall(nt)) return ''; }catch(e){}
  var age = Date.now() - (+k.t || 0);
  if(age < 0 || age > SEAT_TTL_MS) return '';               // stale, or a skewed clock
  return k.w || 'a teammate';
}
/* Stake a claim the moment a lead is opened, and let the normal debounced push carry it. */
function _clmTake(c){
  if(!c || !_seat()) return;
  var s=_seat(); notes[c] = notes[c] || {};
  var mine = notes[c].clm;
  if(mine && mine.d === _deviceId() && (Date.now()-(+mine.t||0)) < 60000) return;  // already fresh
  if(_clmOwner(c)) return;                                  // do not steal a live claim
  notes[c].clm = {d:_deviceId(), t:Date.now(), w:(s.w||'seat '+(s.i+1))};
  try{ saveNotes(); }catch(e){}
  try{ if(typeof syncPushSoon==='function') syncPushSoon(); }catch(e){}
}
function pool(){
  /* Rebuilt here rather than in render() so EVERY caller gets a fresh index — advance() and
     screenOutcome() both call pool() outside a render, and a teammate's opt-out landing between
     paints would otherwise be missed. 0.05ms for 900 note keys; the O(rows x notes) version this
     replaced measured 36.8ms per pass at 400 rows, twice per paint. */
  optPhones(true);
  var base;
  if(lane==='wq'){ var s={}; workerQ().forEach(function(c){ s[c]=1; }); base = ROWS.filter(function(r){ return s[r.c]; }); }
  /* BUY-BOX LANE. Everything matching a standing acquisition criterion, in either board state —
     a 4-bedroom in Miami Gardens is worth the call whether its sale is next week or unscheduled,
     so this deliberately does NOT split on r.lp the way the other two lanes do.
     UNDERWATER rows are dropped from this lane specifically. They stay reachable everywhere else
     (an upside-down owner still deserves the advisor call, and short sales are real work) — but
     this lane answers "which of these could we ACQUIRE", and a house worth less than its liens is
     not a candidate. Showing it here is how a $222k-underwater lead got ranked #2 on a hand-built
     sheet under the heading "most runway". */
  else if(lane==='bb'){ base = ROWS.filter(isBuyBox); }
  else base = ROWS.filter(function(r){ return lane==='soon' ? !r.lp : !!r.lp; });
  var n = 0;
  var keep = base.filter(function(r){ if(suppressed(r)){ n++; return false; } return true; });
  _SUPN = n;
  /* SEAT FILTER LAST, so _SUPN keeps meaning "suppressed for a compliance reason" and does not
     silently absorb "belongs to the other caller" — two very different facts that must never
     share a counter. Counted separately and shown separately. */
  _SEATN = 0; _CLMN = 0;
  if(_seat() && !SEAT_ALL){
    keep = keep.filter(function(r){
      if(!_seatMine(r)){ _SEATN++; return false; }
      if(_clmOwner(r.c)){ _CLMN++; return false; }
      return true; });
  }
  return keep;
}
var _SEATN = 0, _CLMN = 0;
function start(){
  /* The worker's queue is the DEFAULT when it has anything in it. Those leads were triaged this
     morning and are phone-only — the worker could not reach them any other way, so they are the
     highest-intent list on the device. Sale-soon and Fresh-filings stay one tap away. */
  var _wq = workerQ();
  if(_wq.length && ROWS.some(function(r){ return _wq.indexOf(r.c) >= 0; })) lane = 'wq';
  // the lookup only exists once the payload is open — show it here, not in the gate
  try{
    var _lk = $('lkbtn');
    if(_lk){ _lk.style.display = 'block'; _lk.onclick = function(){ screenLookup(); }; }
  }catch(e){}
  i=0; render(); freshCheck();
  var k=null; try{k=localStorage.getItem('fcTeamKey');}catch(e){}
  if(!k){ $('sync').textContent='Team sync is off — outcomes log to this phone only. Turn it on in the board to reach the laptop.'; }
  else { $('sync').textContent='Team sync on'; try{ syncPull().then(function(){ loadNotes(); }); }catch(e){} }
}

/* ADVANCE BY IDENTITY, NEVER BY `i++`.

   `i` indexes into pool(), and pool() is recomputed from live notes on every render. Logging an
   outcome can REMOVE the current lead from it — do-not-contact, wrong number and not-interested all
   become suppressed the moment they are written, and a worker-queue entry is retired on any outcome.
   When that happens the successor slides down into slot `i`, so `i++` lands one past them and a
   real person is silently skipped. (Suppression is new; this is a regression it introduced, and the
   worker lane had the same hole before it.)

   So: remember who was next BEFORE mutating, and go find them afterwards. Falls back to the worked
   lead's own position when the intended successor is also gone, and holds position when both
   vanished — because then everything at `i` has already shifted down. */
function advance(workedC, nextC){
  SCREEN='lead';                    // leaving the interactive screen ON PURPOSE — render may paint
  var P = pool(), k;
  if(nextC) for(k=0;k<P.length;k++) if(P[k].c===nextC){ i=k; return render(); }
  for(k=0;k<P.length;k++) if(P[k].c===workedC){ i=k+1; return render(); }
  render();
}
/* WHICH SCREEN IS UP. The board's extracted mergeNotes ends with `render()` — harmless on the
   board, where render repaints a static list, and CATASTROPHIC here, where the page is a wizard.
   Returning from the tel: dialer or sms: composer fires visibilitychange -> syncPull -> mergeNotes
   -> render(), which replaced the outcome screen / after-call panel / "did it send?" confirm 1-2s
   after he got back, and reset phIdx to 0. That is why the FIRST call of a session worked (nothing
   to merge yet) and everything after it "went faulty": his own first push guaranteed every later
   return had a change to merge. The data must land; the REPAINT must wait. */
var SCREEN='lead';
function render(){
  if(SCREEN!=='lead'){ return; }   // never stomp an interactive screen — advance() repaints fresh
  var P=pool();
  /* An emptied lane and a never-populated lane are NOT the same event, and until now they printed the
     same words. Work every lead and the pool drains to zero, so a finished session was reporting
     "Nothing in this lane" — indistinguishable from a broken build or the wrong lane. Same rule as
     the fail-silent one on the auction horizon: an empty result must never look like missing data. */
  if(!P.length){
    var done = _WORKED.length
      ? '<b>Lane cleared.</b><div class="sub">'+_WORKED.length+' lead'+(_WORKED.length===1?'':'s')+' worked. Switch lanes above, or reopen tomorrow.</div>'
      : '<b>Nothing in this lane.</b><div class="sub">Switch lanes above.</div>';
    $('app').innerHTML=head()+'<div class="card">'+done+'</div><div class="sheetpad"></div>'; wire(); return;
  }
  /* Count what was ACTUALLY worked, not what is left in the pool. `P.length` was standing in for it,
     but the two diverge the moment an outcome removes a lead: log 5 do-not-contacts and the pool is
     empty, so it reported "0 worked" for a full session. A number on screen that is not the thing it
     is labelled is the same defect class as the "0% equity" and "$0 owed" bugs. */
  if(i>=P.length){ $('app').innerHTML=head()+'<div class="card"><b>Queue clear.</b><div class="sub">'
      +_WORKED.length+' lead'+(_WORKED.length===1?'':'s')+' worked this session. Reopen tomorrow.</div></div>'
      +'<div class="sheetpad"></div>'; wire(); return; }
  /* Keep the NUMBER position when the lead is unchanged. A legitimate lead-screen render (sync merge
     landing while he reads the card on number 2) must not snap him back to number 1. */
  var pc=cur&&cur.c, pp=phIdx;
  cur=P[i]; phIdx=(cur&&cur.c===pc&&pp<cur.p.length)?pp:0;
  /* Stake the claim on ARRIVAL at the lead, not on tapping dial. He reads the card, checks the
     file and rehearses the open before he dials — that whole stretch is exactly when the other
     phone must be told this one is taken. Claiming at dial-time would leave the most likely
     collision window completely unguarded. */
  _clmTake(cur.c);
  screenLead();
}
function head(){
  var wq = workerQ().length;
  /* Buy-box count. Same predicate as the lane filter, so the number on the button and the list
     behind it can never disagree — a count derived a second way is a count that drifts. */
  var bbn = ROWS.filter(isBuyBox).length;
  /* NO SILENT CAPS. Suppression is correct, but a list that quietly shrank looks identical to a list
     that was always that size — the exact confusion that hid 466 callable leads behind call_list's
     --max 30. Say the number out loud.
     Read from pool()'s last count rather than re-deriving it: this is THIS LANE's hidden count, and
     head() always renders downstream of a pool() call. */
  var sup = _SUPN;
  return '<div class="top"><div class="lane">'
    +(wq?('<button data-l="wq" class="'+(lane==='wq'?'on':'')+'">Worker &middot; '+wq+'</button>'):'')
    +'<button data-l="soon" class="'+(lane==='soon'?'on':'')+'">Sale soon</button>'
    +'<button data-l="lp" class="'+(lane==='lp'?'on':'')+'">Fresh filings</button>'
    /* Count in the label, same rule as the Worker lane: a lane whose size is invisible gets
       assumed to be whatever it was last time. Hidden only when the box matches nothing, so an
       empty buy-box never reads as a broken build. */
    +(bbn?('<button data-l="bb" class="'+(lane==='bb'?'on':'')+'">Buy-box &middot; '+bbn+'</button>'):'')
    +'</div>'
    +(sup?('<div class="supn">'+sup+' hidden &mdash; wrong number, opted out, dead, or <b>already called</b> '
          +'(by you or a teammate) &middot; <a href="#" id="reglink" style="color:var(--gold)">see the call log</a></div>'):'')
    /* SEAT CHIP. Always rendered, even solo, because "am I splitting the list right now" is a
       question the caller must be able to answer without opening a menu — a partition you cannot
       see is one you assume is on when it is off, and that is the whole bug he asked to fix.
       The two hidden counts are printed SEPARATELY and never folded into `sup`: "carlos has it"
       and "opted out" are different facts and a caller must be able to tell them apart. */
    +seatChip()
    /* The build stamp, in the TOP bar on purpose. BUILT was baked into the page and rendered
       nowhere, so a phone serving last week's list had no visible tell — the operator's only signal
       was leads that felt stale. The bottom of the screen belongs to the sheet (fixed, z-40), which
       covers anything placed there; the top bar is the one strip nothing ever overlays. */
    +'<div class="supn">list built '+esc(BUILT.replace('T',' '))+errChip()+'</div>'
    +'</div>';
}
function seatChip(){
  var s=_seat();
  if(!s) return '<div class="supn">Solo &mdash; whole list &middot; '
    + '<a href="#" onclick="seatMenu();return false" style="color:var(--gold)">split with a teammate</a></div>';
  var bits = [];
  if(_SEATN) bits.push(_SEATN+' on the other phone'+(s.n>2?'s':''));
  if(_CLMN)  bits.push(_CLMN+' being worked now');
  return '<div class="supn">'
    + (SEAT_ALL ? '<b style="color:var(--gold)">Showing EVERYONE\'S leads</b>'
                : '<b>'+esc(s.w||('Seat '+(s.i+1)))+'</b> &mdash; seat '+(s.i+1)+' of '+s.n)
    + (bits.length ? ' &middot; '+bits.join(' &middot; ') : '')
    + ' &middot; <a href="#" onclick="seatMenu();return false" style="color:var(--gold)">change</a>'
    + (s ? ' &middot; <a href="#" onclick="SEAT_ALL=!SEAT_ALL;i=0;render();return false" style="color:var(--gold)">'
           + (SEAT_ALL?'back to mine':'show all')+'</a>' : '')
    + '</div>';
}
/* Deliberately a prompt() and not a styled modal: this is set once per phone and then never
   touched, and a wizard for it would be more code to break than the feature itself. */
function seatMenu(){
  var s=_seat()||{n:1,i:0,w:''};
  var n = parseInt(prompt('How many people are calling this list?\n\n1 = solo (you get everything)\n2 = you and one teammate\n3 or 4 also work.\n\nEVERY caller must enter the SAME number.', String(s.n)), 10);
  if(!n || n<1){ return; }
  if(n===1){ _seatSet(1); alert('Solo. You now get the whole list.'); return; }
  var idx = parseInt(prompt('Which seat is THIS phone?\n\nEnter 1 for the first caller, 2 for the second, and so on.\nEvery phone must pick a DIFFERENT number.', String((s.i||0)+1)), 10);
  if(!idx || idx<1 || idx>n){ alert('Seat must be between 1 and '+n+'.'); return; }
  var w = (prompt('Your first name (shows on your teammate\'s phone when you are working a lead):', s.w||'')||'').trim();
  _seatSet(n, idx-1, w);
  alert('Seat '+idx+' of '+n+'.\n\nYou now see about 1/'+n+' of the list and your teammate sees the rest — '
      + 'the same lead is never on both phones at once.\n\nUse "show all" if you finish early and want to work outside your lane.');
}
function errChip(){
  var n=0; try{ n=(JSON.parse(localStorage.getItem('fcErrLog')||'[]')).length; }catch(e){}
  return n ? (' &middot; <span class="errchip" onclick="showErrs()">'+n+' error'+(n===1?'':'s')+' logged &mdash; tap</span>') : '';
}
function showErrs(){
  var log=[]; try{ log=JSON.parse(localStorage.getItem('fcErrLog')||'[]'); }catch(e){}
  // alert() so it can be screenshotted whole, then offer to clear
  alert(log.map(function(e){ return e.t+' ['+e.w+'] '+e.m+'\n'+e.s; }).join('\n\n') || 'empty');
  if(confirm('Clear the error log?')){ try{ localStorage.removeItem('fcErrLog'); }catch(e){} render(); }
}
/* FTSA calling window, 8am-8pm EASTERN. WARN, never block — same call the board makes: a hard block
   would stop him documenting a callback the homeowner themselves asked for, and the statute governs
   solicitation hours, not every dial. The banner has to state the time THERE, not here. */
function flClock(){
  try{
    var p = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',minute:'2-digit',hour12:true}).format(new Date());
    var h = +new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'numeric',hour12:false}).format(new Date());
    return {txt:p, ok:(h>=8 && h<20)};
  }catch(e){ return {txt:'', ok:true}; }
}
/* NOT CHECKED vs ZERO. Payoff is absent on 54% of leads, back taxes on 98%. A blank reads as
   "it is fine" and a $0 reads as "they owe nothing" - both are lies about data nobody gathered. */
function money(n){ return (n==null) ? '<span class="nc">not checked</span>' : '$'+Math.round(n).toLocaleString(); }
function kv(k,v,sm){ return '<div class="kv"><div class="k">'+k+'</div><div class="v'+(sm?' sm':'')+'">'+v+'</div></div>'; }
function has(r,ch){ return (r.f||'').indexOf(ch)>=0; }
function band(lbl,inner){ return '<div class="band"><div class="blab">'+lbl+'</div>'+inner+'</div>'; }

/* What we have already said to this person, from the SAME notes store the board writes.
   n.dials is the only thing separating "3 dials, no answer" from "never tried". */
function histLine(r){
  var n=notes[r.c]||{}, ts=n.touches||[], dl=n.dials||[], out=[], byCh={};
  ts.forEach(function(t){ byCh[t.ch]=(byCh[t.ch]||0)+1; });
  ['call','text','email','letter','door'].forEach(function(ch){ if(byCh[ch]) out.push(byCh[ch]+'x '+ch); });
  if(dl.length) out.push(dl.length+' dial'+(dl.length>1?'s':''));
  if(n.status) out.push('status: '+esc(n.status));
  var last = ts.length ? ts[ts.length-1] : null;
  return '<div class="hist">'+(out.length ? ('Already: '+out.join(' &middot; ')+(last?(' &middot; last '+esc(last.d)):''))
                                          : 'No contact logged yet.')+'</div>';
}

/* ===== THE CALL REGISTRY =========================================================================
   Every call this team has logged, newest first, across every device — because the touches live in
   notes and team sync merges teammates' notes into the same store. Durable by construction: it is
   the same data the queue reads to decide what to skip, so the log and the skipping can never
   disagree. Reachable from the hidden-count line in the top bar. */
function screenRegistry(){
  SCREEN='reg';
  var rows = [];
  Object.keys(notes || {}).forEach(function(c){
    var n = notes[c] || {};
    var lc = lastCall(n);
    if(!lc) return;
    var row = ROWS.filter(function(x){ return x.c === c; })[0] || null;
    rows.push({
      c: c, ts: lc.ts, by: lc.by, out: lc.out,
      who: (row && (row.o || row.f)) || (n.owner || ''),
      addr: (row && row.a) || '',
      n: (((n.dials||[]).length) || ((n.touches||[]).filter(function(t){return t.ch==='call';}).length) || 1),
      status: n.status || ''
    });
  });
  rows.sort(function(a,b){ return b.ts - a.ts; });
  var mine = 0, team = 0, me = caller();
  rows.forEach(function(x){ if(x.by && me && x.by !== me) team++; else mine++; });
  var list = rows.length ? rows.slice(0, 300).map(function(x){
    return '<div class="regrow">'
      + '<div class="regtop"><b>' + esc(x.who || x.c) + '</b>'
      + '<span class="regago">' + agoTxt(x.ts) + '</span></div>'
      + (x.addr ? '<div class="regsub">' + esc(x.addr) + '</div>' : '')
      + '<div class="regsub">' + esc(x.out || 'called')
      + (x.n > 1 ? ' &middot; ' + x.n + ' dials' : '')
      + (x.status ? ' &middot; ' + esc(x.status) : '')
      + '<span class="regby">' + esc(x.by || 'this phone') + '</span></div>'
      + '</div>';
  }).join('') : '<div class="regsub" style="padding:14px 2px">No calls logged yet. Every outcome you '
      + 'tap lands here, and so does every call your teammates log on their phones.</div>';
  $('app').innerHTML = head()
    + '<div class="card">'
    +   '<div class="band"><div class="bl">THE CALL LOG</div>'
    +     '<div class="regsum"><b>' + rows.length + '</b> people called &middot; '
    +       mine + ' by you &middot; ' + team + ' by teammates</div>'
    +     '<div class="regsub" style="margin-top:6px">Anyone on this list inside their cooldown is '
    +       'skipped in the queue &mdash; that is how two people never dial the same owner.</div>'
    +   '</div>'
    +   '<div class="reglist">' + list + '</div>'
    +   '<button class="big" id="regback" style="margin-top:14px">&larr; Back to the queue</button>'
    + '</div><div class="sheetpad"></div>';
  $('regback').onclick = function(){ SCREEN='lead'; render(); };
}
/* ===== WHO TEXTED ME? ===========================================================================
   An inbound text is the highest-value event in this business — a reply outranks every score and
   tier — and it arrives as an anonymous number. Before this, the only way to identify it was to
   scroll the 400-lead queue hoping to spot it, which could not work: only 1,202 of our 3,638
   numbers are on this page at all. PHIDX carries every lead that has a phone (inside the encrypted
   payload — these are phone numbers and the repo is public).                                   */
function digitsOf(s){ return String(s == null ? '' : s).replace(/\D/g, ''); }
function phLookup(q){
  var d = digitsOf(q);
  if(!PHIDX || !PHIDX.d || d.length < 4) return [];
  var t = PHIDX.t || [], map = PHIDX.d, hits = [], seenIdx = {};
  var push = function(num, idx){
    if(idx == null || seenIdx[idx + '|' + num]) return;
    seenIdx[idx + '|' + num] = 1;
    var row = t[idx] || [];
    hits.push({num:num, owner:row[0]||'', street:row[1]||'', c:row[2]||'',
               d:row[3], eq:row[4], fo:row[5], ct:row[6]});
  };
  var ten = d.slice(-10);
  if(d.length >= 10 && map[ten] != null){ push(ten, map[ten]); return hits; }
  // 4-9 digits: suffix match, because he often only has the tail of a number in front of him
  for(var k in map){ if(k.slice(-d.length) === d) push(k, map[k]); if(hits.length >= 12) break; }
  return hits;
}
/* What we already sent this person. He should never call back blind — three emails and a text in
   the last two weeks changes the opening line completely. Reads the same notes the board writes. */
function contactHistory(caseId){
  var n = notes[caseId] || {}, out = [];
  (n.doclog || []).forEach(function(e){
    out.push({t:e.ts || '', w:({'worker-email':'email sent','worker-text':'text sent',
      'worker-callq':'queued to call','worker-letter':'letter'}[e.key] || e.key || 'contact')});
  });
  (n.touches || []).forEach(function(e){
    out.push({t:e.ts || e.d || '', w:(e.ch || '') + (e.out ? ' — ' + e.out : '')});
  });
  out.sort(function(a,b){ return String(b.t).localeCompare(String(a.t)); });
  return out.slice(0, 6);
}
function screenLookup(prefill){
  SCREEN = 'lookup';
  try{ document.getElementById('sheet').classList.add('hid'); }catch(e){}
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:18px">&#128269; Who texted me?</div>'
    + '<div class="mut" style="font-size:12.5px;margin-top:4px">Paste the number, or just the last 4 digits.</div>'
    + '<input id="lkq" inputmode="tel" autocomplete="off" placeholder="305-801-1800  or  1800" '
    +   'style="width:100%;margin-top:12px;padding:14px;border-radius:12px;border:1px solid #2a3f6b;'
    +   'background:#0f1d3a;color:var(--ink);font-size:17px;-webkit-user-select:text;user-select:text">'
    + '<div id="lkout" style="margin-top:14px"></div>'
    + '<button id="lkback" class="ghost" style="margin-top:14px">&larr; Back to the queue</button>'
    + '</div><div class="sheetpad"></div>';
  $('lkback').onclick = function(){ SCREEN='lead'; render(); };
  var run = function(){
    var q = $('lkq').value, hits = phLookup(q), box = $('lkout');
    if(digitsOf(q).length < 4){ box.innerHTML = ''; return; }
    if(!hits.length){
      box.innerHTML = '<div class="nc">No lead in the database has this number.<br>'
        + 'It may be a wrong number, a new person, or someone we have not traced yet.</div>'
        + '<a class="flinks" style="display:block;margin-top:10px" href="https://www.truepeoplesearch.com/resultphone?phoneno='
        + esc(digitsOf(q).slice(-10)) + '" target="_blank" rel="noopener">'
        + '<span style="display:inline-flex;min-height:44px;align-items:center;padding:10px 14px;'
        + 'border-radius:10px;background:#12213f;border:1px solid #2a3f6b;color:var(--ink);font-weight:600">'
        + 'Reverse-search this number &rarr;</span></a>';
      return;
    }
    box.innerHTML = hits.map(function(h){
      var r = null;
      for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === h.c){ r = ROWS[j]; break; } }
      var hist = contactHistory(h.c);
      var money = (h.eq != null)
        ? ('equity <b style="color:' + (h.eq > 0 ? '#7ad48f' : '#ff8a80') + '">$'
           + Math.abs(Math.round(h.eq/1000)) + 'k' + (h.eq < 0 ? ' UNDERWATER' : '') + '</b>')
        : '';
      var clock = (h.d != null && h.d < 9000)
        ? (h.d < 0 ? '<b style="color:#ff8a80">sale PASSED</b>'
                   : '<b style="color:' + (h.d <= 7 ? '#ff8a80' : '#F4E5A7') + '">sale in ' + h.d + ' day' + (h.d===1?'':'s') + '</b>')
        : '<span class="mut">no auction date yet</span>';
      return '<div class="refbox" style="margin-top:10px">'
        + '<div class="ltag" style="margin:0 0 6px">' + fmt(h.num) + '</div>'
        + '<div style="font:800 16px \'Segoe UI\',Arial;color:var(--ink)">' + esc(h.owner || 'name unknown') + '</div>'
        + '<div style="margin-top:3px">' + esc(h.street) + '</div>'
        + '<div style="margin-top:6px">' + clock + (money ? ' &middot; ' + money : '') + '</div>'
        + (hist.length
            ? '<div class="ltag" style="margin:10px 0 4px">WHAT WE ALREADY SENT THEM</div>'
              + hist.map(function(e){ return '<div class="mut" style="font-size:12px">'
                  + esc(String(e.t).slice(0,16)) + ' &middot; ' + esc(e.w) + '</div>'; }).join('')
            : '<div class="mut" style="margin-top:8px;font-size:12px">no contact logged yet</div>')
        + '<div class="flinks" style="margin-top:10px">'
        +   '<a href="'+dialHref(esc(h.num))+'"'+dialTarget()+'>&#128222; Call back</a>'
        +   '<a href="sms:' + esc(h.num) + '">&#128172; Text</a>'
        +   fileLinks({fo:h.fo, ct:h.ct, o:h.owner, a:h.street, c:h.c}).map(function(x){
              return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>'; }).join('')
        + '</div>'
        + '<div class="brhost" data-c="' + esc(h.c) + '"></div>'
        + '<button class="lkrep" data-c="' + esc(h.c) + '" style="margin-top:10px;background:#286c34;'
        +   'border-color:#286c34;color:#fff">&#10003; They REPLIED — flag it</button>'
        + (r ? '<button class="lkgo" data-c="' + esc(h.c) + '" class="ghost" style="margin-top:8px">Open the full card &rarr;</button>' : '')
        + '<div class="fcase">case ' + esc(h.c) + '</div></div>';
    }).join('');
    /* Brief buttons per hit — use the FULL row when the lead is on this page (money, flags,
       plaintiff all present); fall back to the index's slice, whose brief states its unknowns. */
    Array.prototype.forEach.call(box.querySelectorAll('.brhost'), function(host){
      var c = host.dataset.c, full = null;
      for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === c){ full = ROWS[j]; break; } }
      var hh = null;
      hits.forEach(function(x){ if(x.c === c) hh = x; });
      var rr = full || {a: (hh && hh.street) || '', o: (hh && hh.owner) || '', c: c,
                        d: hh && hh.d, fo: hh && hh.fo, ct: hh && hh.ct,
                        p: hh ? [hh.num] : []};
      host.innerHTML = briefButtons(rr);
      wireBrief(host, rr);
    });
    /* DELIBERATE TAP, never automatic on lookup: a reply retires the cold ladder and changes the
       cadence, so merely looking someone up must not do it. */
    Array.prototype.forEach.call(box.querySelectorAll('.lkrep'), function(b){
      b.onclick = function(){
        var c = b.dataset.c, n = notes[c] = notes[c] || {status:'',note:''};
        n.replied = today(); n.status = n.status || 'Contacted';
        n.touches = n.touches || [];
        n.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text', out:'THEY REPLIED (inbound)'});
        touched = true; saveNotes(); queueSync();
        b.textContent = '✓ flagged as replied'; b.disabled = true;
        toast('Flagged — they jump the queue now');
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll('.lkgo'), function(b){
      b.onclick = function(){
        var c = b.dataset.c;
        for(var j=0;j<ROWS.length;j++){ if(ROWS[j].c === c){ cur = ROWS[j]; phIdx = 0; SCREEN='lead'; return screenLead(); } }
        toast('That lead is not in this page’s call list');
      };
    });
  };
  $('lkq').oninput = run;
  if(prefill){ $('lkq').value = prefill; run(); }
  try{ $('lkq').focus(); }catch(e){}
}
/* ===== HIS FILE — the research links, built on the page ==========================================
   Call Mode shipped with ZERO links to a lead's records: standing on a call you had to leave the
   page and hunt the county sites by hand (2026-08-18 field report: "I have to struggle to find
   this guy"). Every URL below is DERIVED from two seed fields (folio + county) instead of shipping
   five long strings per lead — 400 rows x ~450 chars of URL is ~180 KB on a page that has to open
   on a phone at a door. Deep docket links can't be derived (the MD clerk uses an opaque token), so
   those go to the county's case-search page, which is one paste away. */
function fileLinks(r){
  // alphanumeric — Broward parcel ids contain letters (494213BA0140)
  var fo = String(r.fo || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase();
  var ct = String(r.ct || 'MI').toUpperCase().slice(0, 2);
  var L = [];
  var dash = function(s, groups){                    // 40434504140060130 -> 40-43-45-04-14-006-0130
    var out = [], p = 0;
    for(var i = 0; i < groups.length && p < s.length; i++){ out.push(s.substr(p, groups[i])); p += groups[i]; }
    if(p < s.length) out.push(s.substr(p));
    return out.join('-');
  };
  if(fo){
    if(ct === 'PA'){                                  // PALM BEACH
      L.push(['Appraiser', 'https://pbcpao.gov/Property/Details?parcelId=' + fo]);
      L.push(['Taxes', 'https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=ParcelID%3A'
        + encodeURIComponent(dash(fo, [2,2,2,2,2,3,4])) + '&pg=1&g=-1&moduleId=449']);
    } else if(ct === 'BR'){                           // BROWARD
      L.push(['Appraiser', 'https://bcpa.net/RecInfo.asp?URL_Folio=' + fo]);
      L.push(['Taxes', 'https://broward.county-taxes.com/public/real_estate/parcels/'
        + dash(fo, [6,2,4]) + '/bills']);
    } else {                                          // MIAMI-DADE
      L.push(['Appraiser', 'https://apps.miamidadepa.gov/PropertySearch/#/?folio=' + fo]);
      L.push(['Taxes', 'https://miamidade.county-taxes.com/public/real_estate/parcels/' + fo]);
    }
  } else {
    /* NO FOLIO RESOLVED. These two links used to simply VANISH — so on a folio-less lead (the
       'no cadastral match' class) he had no way to check taxes at all, mid-call, with nothing on
       screen explaining why. Fall back to the county's OWN address search, pre-filled where the
       site accepts it. Never a web search: the board's old Google punt is exactly the complaint
       that started this (2026-08-22). Street number + street only — a unit designator is the very
       thing the county roll disagrees with (court '#107' vs county '#L7'), so including one turns
       a working search into zero results. */
    var st = String(r.a || '').split(',')[0]
               .replace(/\s+(?:APT|UNIT|STE|#)\s*[\w-]+\s*$/i, '').trim();
    var qs = encodeURIComponent(st);
    if(ct === 'PA'){
      L.push(['Appraiser (search)', 'https://pbcpao.gov/']);
      L.push(['Taxes (search)', 'https://pbctax.publicaccessnow.com/PropertyTax.aspx?s=' + qs]);
    } else if(ct === 'BR'){
      L.push(['Appraiser (search)', 'https://web.bcpa.net/BcpaClient/#/Record-Search']);
      L.push(['Taxes (search)', 'https://broward.county-taxes.com/public/search?search_query=' + qs]);
    } else {
      L.push(['Appraiser (search)', 'https://apps.miamidadepa.gov/propertysearch/#/?address=' + qs]);
      L.push(['Taxes (search)', 'https://miamidade.county-taxes.com/public/search?search_query=' + qs]);
    }
  }
  L.push(['Court docket', ct === 'PA' ? 'https://appsgp.mypalmbeachclerk.com/eCaseView/'
        : ct === 'BR' ? 'https://www.browardclerk.org/Web2/CaseSearchECA/'
        : 'https://www2.miamidadeclerk.gov/ocs/Search.aspx']);
  var nm = String(r.o || '').replace(/[,&]/g, ' ').replace(/\s+/g, ' ').trim();
  if(nm) L.push(['People search', 'https://www.truepeoplesearch.com/results?name='
        + encodeURIComponent(nm) + (r.z ? '&citystatezip=' + encodeURIComponent(r.z) : '')]);
  if(r.a) L.push(['Map', 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(r.a)]);
  return L;
}
function fileBand(r){
  var L = fileLinks(r);
  if(!L.length) return '';
  return '<div class="band"><div class="bl">HIS FILE</div><div class="flinks">'
    + L.map(function(x){
        return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>';
      }).join('')
    + '</div>'
    + briefButtons(r)
    + '<div class="fcase">case ' + esc(r.c || '') + (r.fo ? ' &middot; folio ' + esc(r.fo) : '')
    + '</div></div>';
}
/* ===== BRIEF JESSE — one tap mid-call ============================================================
   The live-call flow: owner on the line, Jesse joining, and he fires his standard questions —
   plaintiff? bank or HOA? what's owed? sale when? listed? — while Alejandro scrambles. Every answer
   is ALREADY on the row; this assembles them IN JESSE'S ORDER and hands them off. mailto: keeps a
   human on the send button (a page can't verify delivery, so it must not pretend to send).
   Recipient is HARDCODED — this can never mail an owner. */
var JESSE = 'celusa13@gmail.com';
function advisorBrief(r){
  var L = [];
  var money = function(v){ v = +v || 0;
    return v >= 1e6 ? '$' + (v/1e6).toFixed(2) + 'M' : '$' + Math.round(v).toLocaleString(); };
  var fl = String(r.f || '');
  L.push('LIVE CALL BRIEF — ' + (r.a || 'address unknown'));
  L.push('Owner: ' + (r.o || 'unknown')
    + (r.hs ? ' — lives there (homestead)' : (r.ab ? ' — ABSENTEE (mails elsewhere)' : ''))
    + (fl.indexOf('C') >= 0 ? ' [company/trust]' : ''));
  var hasSale = (r.d != null && r.d < 9000);
  L.push('Sale: ' + (!hasSale
      ? ((r.x || 'n/a') + ' filed — NO auction date yet (months of runway)')
      : (r.d < 0 ? (r.x + ' — PASSED ' + (-r.d) + 'd ago (surplus talk only)')
                 : (r.x + ' — in ' + r.d + ' day' + (r.d === 1 ? '' : 's'))))
    + (r.sv ? ' | survived ' + r.sv + ' prior sale date' + (r.sv === 1 ? '' : 's') + ' (staller)' : ''));
  /* ja = TOTAL interest accrued (FS 55.03), not per-day — and a payoff already CONTAINS it, so
     it only prints beside a bare judgment (first draft said "+$X/day": a 384%/yr absurdity). */
  var owed = (r.py || r.jg);
  L.push('Money: value ' + (r.v ? money(r.v) + ' (county)' : 'UNKNOWN')
    + ' | owed ' + (owed ? money(owed)
                            + (r.py ? ' payoff (incl. interest' + (r.jd ? ', as of ' + r.jd : '') + ')'
                                    : ' judgment' + (r.jd ? ' (' + r.jd + ')' : '')
                                      + (r.ja ? ' + ' + money(r.ja) + ' interest accrued' : ''))
                  : (fl.indexOf('J') >= 0 ? 'NOT POSTED yet' : 'unknown')));
  if(r.v && owed){
    var eq = r.v - owed;
    L.push('  -> equity ~' + money(Math.abs(eq)) + (eq < 0 ? ' UNDERWATER' : '')
      + (fl.indexOf('E') >= 0 ? ' (gross upper bound — liens unverified)' : ''));
  }
  L.push('Foreclosing: ' + (r.pl || 'plaintiff not on file') + (r.ft ? ' [' + r.ft + ']' : ''));
  if(fl.indexOf('H') >= 0) L.push('HOA co-defendant: YES — check for a second case');
  if(fl.indexOf('S') >= 0) L.push('SECOND CASE EXISTS on this person — pull it before advising');
  if(fl.indexOf('M') >= 0) L.push('A FIRST MORTGAGE SURVIVES this sale');
  if(r.ss) L.push('Surviving senior lien: ' + money(r.ss));
  if(fl.indexOf('T') >= 0) L.push('Tax certificate sold — second clock running');
  if(r.td) L.push('Back taxes due: ' + money(r.td));
  L.push(r.zs ? ('Listing: ' + r.zs + (r.zap ? ' — agent ' + (r.zag || '') + ' ' + fmt(r.zap) : (r.zag ? ' — agent ' + r.zag : '')))
              : 'Listing: not listed');
  if(fl.indexOf('D') >= 0) L.push('Condo — estoppel letter needed');
  L.push('Case ' + (r.c || '?') + (r.ct ? ' (' + (r.ct === 'BR' ? 'BROWARD' : r.ct === 'PA' ? 'PALM BEACH' : 'MIAMI-DADE') + ')' : '')
    + (r.fo ? ' | folio ' + r.fo : ''));
  fileLinks(r).forEach(function(x){ if(x[0] !== 'Map' && x[0] !== 'People search') L.push(x[0] + ': ' + x[1]); });
  if(r.p && r.p.length) L.push('Phones: ' + r.p.map(fmt).join(' | '));
  return L.join('\n').slice(0, 1500);
}
function briefButtons(r){
  return '<div class="flinks" style="margin-top:10px">'
    + '<a href="#" class="briefjesse" style="background:#3d2c08;border-color:#A8720C;color:#F6E9C8;font-weight:800">&#9889; Brief Jesse</a>'
    + '<a href="#" class="briefcopy">&#128203; Copy brief</a></div>';
}
function wireBrief(root, r){
  var mark = function(){
    var n = notes[r.c] = notes[r.c] || {status:'',note:''};
    n.doclog = n.doclog || [];
    n.doclog.push({ts: nowTS(), tsu: Date.now(), key: 'advisor-brief', src: 'call-mode'});
    touched = true; saveNotes(); queueSync();
  };
  Array.prototype.forEach.call(root.querySelectorAll('.briefjesse'), function(b){
    b.onclick = function(ev){
      ev.preventDefault();
      var body = advisorBrief(r);
      var url = 'mailto:' + JESSE + '?subject=' + encodeURIComponent((r.a || r.c || 'lead') + ' — live call brief')
              + '&body=' + encodeURIComponent(body);
      var a = document.createElement('a');
      a.href = url; a.style.display = 'none';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      mark(); toast('Gmail opened — hit Send and keep talking');
    };
  });
  Array.prototype.forEach.call(root.querySelectorAll('.briefcopy'), function(b){
    b.onclick = function(ev){
      ev.preventDefault();
      var body = advisorBrief(r);
      var done = function(){ mark(); toast('Brief copied — paste to Jose'); };
      if(navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(body).then(done, function(){ prompt('Copy this:', body); done(); });
      } else { prompt('Copy this:', body); done(); }
    };
  });
}
function screenLead(){
  SCREEN='lead';
  document.getElementById('sheet').classList.remove('hid');   // the script belongs to the call screen
  var r=cur, d=r.p[phIdx], rk=r.r[phIdx]||'';
  var when = r.lp ? ('lis pendens filed '+esc(r.x||''))
                  : ((r.d===0?'auction TODAY':(r.d===1?'auction TOMORROW':'auction in '+r.d+' days'))+(r.x?' &middot; '+esc(r.x):''));

  var who = '<div class="addr">'+esc(r.a||'(no address on file)')+'</div>'
          + '<div class="own">'+esc(r.o||'(owner unknown)')+'</div>';
  /* LAST QUO CALL -- what happened last time, in front of him BEFORE he redials. Summary from
     Quo's AI, flags from quo_sync's coach pass. Flags render red because every one of them is a
     sentence that must not be said again on the call he is about to make. */
  if(r.qc){
    who += '<div style="margin-top:8px;padding:8px 10px;border:1px solid #2a3f6b;border-radius:10px;background:#0f1d3a">'
        +  '<div class="ltag">LAST CALL &middot; '+esc(r.qc.w||'')+' &middot; '+(r.qc.du||0)+'s</div>'
        +  (r.qc.s ? '<div class="mut" style="font-size:13px;margin-top:3px">'+esc(r.qc.s)+'</div>' : '')
        +  ((r.qc.fl||[]).map(function(f){
             return '<div style="color:#e07b6a;font-size:12.5px;margin-top:3px">&#9873; '+esc(f)+'</div>';
           }).join(''))
        +  '</div>';
  }
  var wc='';
  if(r.ab) wc += '<span class="chip">absentee &middot; call, do not knock</span>';
  if(r.hs) wc += '<span class="chip ok">homestead &middot; they live there</span>';
  if(has(r,'C')) wc += '<span class="chip">company owner &middot; ask for the manager</span>';
  if(has(r,'W')) wc += '<span class="chip">elder signal</span>';
  if(wc) who += '<div class="chips">'+wc+'</div>';
  if(r.po) who += '<div class="warnbar">Roll owner is now <b>'+esc(r.po)+'</b> &mdash; do NOT open with the name above. Ask who you are speaking with.</div>';

  var clock = '<div class="when">'+when+'</div><div class="chips">';
  if(r.sv!=null && r.sv>=2) clock += '<span class="chip bad">STALLER &middot; dodged '+r.sv+' sales</span>';
  else if(r.sv===0)         clock += '<span class="chip ok">FRESH &middot; first sale</span>';
  /* Sales SCHEDULED, the counterpart to sales survived. `sc` shipped on every lead and nothing read
     it. Only shown when it exceeds `sv`, because then it means a sale is on the calendar that has
     not happened yet — which is the difference between "they have dodged three" and "they have
     dodged three and a fourth is booked". Equal values would just restate the chip above. */
  if(r.sc!=null && r.sv!=null && r.sc>r.sv)
                     clock += '<span class="chip">'+r.sc+' sales scheduled, '+(r.sc-r.sv)+' still ahead</span>';
  if(r.sw==='bank')  clock += '<span class="chip">bank keeps postponing</span>';
  if(r.sw==='owner') clock += '<span class="chip">owner fights</span>';
  if(r.bk)           clock += '<span class="chip bad">'+r.bk+' bankruptcy filing'+(r.bk>1?'s':'')+'</span>';
  if(r.sl)           clock += '<span class="chip hot">stay LIFTED '+esc(r.sl)+'</span>';
  if(r.cs)           clock += '<span class="chip">case '+esc(r.cs)+'</span>';
  clock += '</div>';

  var mny = '<div class="grid">'
    + kv('They owe (with interest)', money(r.py))
    + kv('Property value', money(r.v))
    + kv('Equity', r.e==null ? '<span class="nc">not known</span>' : (Math.round(r.e)+'%'+(has(r,'E')?' <span class="nc">gross</span>':'')
         // Say which kind of number this is, on the surface where it gets spoken out loud. A
         // traced chain is the difference between "you have equity" and "you might".
         + (r.eqv ? ' <span class="ok">VERIFIED</span>' : ' <span class="nc">unverified — chain not traced</span>')))
    + kv('Surviving 1st', r.ss==null ? '<span class="nc">not checked</span>' : (r.ss===0?'none':money(r.ss)), 1)
    + '</div>';
  var mc='';
  if(r.py!=null && r.ja) mc += '<span class="chip">includes $'+Math.round(r.ja).toLocaleString()+' interest'+(r.jd?(' since '+esc(r.jd)):'')+'</span>';
  if(r.py==null && r.jg!=null) mc += '<span class="chip">judgment as entered $'+Math.round(r.jg).toLocaleString()+'</span>';
  if(has(r,'J')) mc += '<span class="chip bad">judgment not posted</span>';
  if(has(r,'M')) mc += '<span class="chip bad">a 1st mortgage SURVIVES this sale</span>';
  if(r.td)       mc += '<span class="chip bad">back taxes $'+Math.round(r.td).toLocaleString()+'</span>';
  /* The ESTIMATE, only when the real delinquent balance is unknown — which is 98% of leads, where
     the taxes line was simply blank. `et` was serialized on every lead and read by nothing.
     Labelled "est. annual" and never "owed": it is a millage estimate of the yearly bill, not a
     delinquent balance, and presenting it as debt would be the same lie money() exists to prevent. */
  else if(r.et)  mc += '<span class="chip">est. annual tax $'+Math.round(r.et).toLocaleString()
                     + (has(r,'e')?' (estimate)':'')+'</span>';
  if(has(r,'T')) mc += '<span class="chip bad">tax certificate sold</span>';
  if(r.arv)      mc += '<span class="chip">ARV $'+Math.round(r.arv).toLocaleString()+(r.an?(' &middot; '+r.an+' comps'):'')+'</span>';
  /* orconf is the CONFIDENCE of the lien-chain SEARCH, not a statement about liens. Rendering it raw
     printed "lien chain: none" — which mid-call reads as "there are no liens", the opposite of what
     it means ("the chain was never resolved"), on the screen he uses to decide what to tell someone
     about their equity. 'bd' rendered as "lien chain: bd", pure jargon. Same rule as money(): a
     not-checked state must announce itself, never pose as a clean result. */
  var _OCONF = {ok:  ['',    'lien chain verified'],
                low: ['bad', 'lien chain LOW confidence &mdash; common name, may include a stranger'],
                bd:  ['',    'lien chain from property data, not the records'],
                none:['bad', 'lien chain NOT RESOLVED &mdash; do not treat it as clear']};
  var _oc = r.oc ? (_OCONF[r.oc] || ['', 'lien chain: '+esc(r.oc)]) : null;
  if(_oc)        mc += '<span class="chip '+_oc[0]+'">'+_oc[1]+'</span>';
  if(mc) mny += '<div class="chips">'+mc+'</div>';

  var whoFc = '<div class="grid">'
    + kv('Plaintiff', r.pl ? esc(r.pl) : '<span class="nc">not resolved</span>', 1)
    + kv('Type', r.ft ? esc(r.ft) : '<span class="nc">unknown</span>', 1)
    + '</div>';
  var fc='';
  if(has(r,'S')) fc += '<span class="chip bad">SECOND CASE on this property</span>';
  if(has(r,'H')) fc += '<span class="chip">open HOA lien</span>';
  if(has(r,'X')) fc += '<span class="chip">code enforcement</span>';
  if(has(r,'I')) fc += '<span class="chip">individual plaintiff</span>';
  if(has(r,'D')) fc += '<span class="chip">condo &middot; estoppel</span>';
  if(fc) whoFc += '<div class="chips">'+fc+'</div>';

  /* MISCALCULATED DEAL banner (8/17 masterclass): actively listed with the sale weeks out — the
     agent is burning the client's clock protecting a fantasy price. This call may be a GATEKEEPER
     call: drill card 12 (same property pays the agent twice). Agent's number dials from here. */
  var mlBan = '';
  if(r.ml){
    mlBan = '<div style="margin:8px 0;padding:9px 12px;border-radius:8px;background:#3d2c08;border:1px solid #A8720C;color:#F6E9C8;font:700 12.5px/1.5 -apple-system,Segoe UI,Arial">'
      + '🏷 LISTED with the sale '+(r.d!=null?r.d+'d':'weeks')+' out — MISCALCULATED DEAL. '
      + 'Gatekeeper play: <b>card 12</b> — full commission on the buy + the re-listing. Same property pays the agent twice.'
      + (r.zag ? '<br>Agent: <b>'+esc(r.zag)+'</b>' : '')
      + (r.zap ? ' &middot; <a style="color:#F4E5A7;font-weight:800" href="'+dialHref(String(r.zap).replace(/^1/,''))+'"'+dialTarget()+'>&#128222; call the agent</a>' : '')
      + '</div>';
  }

  var prop = (r.dd||r.bd||r.sf||r.zs) ? ('<div class="hist">'+(r.dd?esc(r.dd):'')
      + (r.bd?(' &middot; '+r.bd+'bd'):'') + (r.ba?('/'+r.ba+'ba'):'')
      + (r.sf?(' &middot; '+Number(r.sf).toLocaleString()+' sqft'):'')
      + (r.zs?(' &middot; listed '+esc(r.zs)):'')+'</div>') : '';

  var fl = flClock();
  var ftsaBar = fl.ok ? '' :
    '<div class="warnbar">FL FTSA &mdash; solicitation calling hours are 8:00 AM to 8:00 PM Eastern. '
    + 'It is ' + esc(fl.txt) + ' there. Dialing anyway is your call; a callback they asked for is different from a cold dial.</div>';

  $('app').innerHTML = head()
    + '<div class="card">'
    +   ftsaBar
    +   mlBan
    +   band('WHO', who)
    +   band('THE CLOCK', clock)
    +   band('THE MONEY', mny)
    +   band('WHO IS FORECLOSING', whoFc + prop + histLine(r))
    +   fileBand(r)
    +   '<a class="dial" href="'+dialHref(d)+'"'+dialTarget()+' id="dial">'+fmt(d)+'</a>'
    +   '<div class="sub">number '+(phIdx+1)+' of '+r.p.length
    +     (rk?(' &middot; '+(rk==='C'?'call first':rk==='O'?'ok':'last resort')):'')
    +     (r.k?(' &middot; '+r.k+' withheld, do-not-call flag on file'):'')+'</div>'
    +   '<button class="big" id="skip" style="background:#2a3f6b">Skip</button>'
    + '</div>'
    + '<div class="sub">'+(i+1)+' of '+pool().length+' &middot; showing '+SHOWN+' of '+TOTAL+' that qualify</div>'
    + '<div class="sheetpad"></div>';
  // Render the outcome screen SYNCHRONOUSLY on tap and let the tel: navigation proceed. iOS
  // backgrounds the tab the instant the dialer opens; painting after would never happen.
  $('dial').addEventListener('click', function(){
    // A dial IS work in progress. `touched` used to be set only on the first LOGGED outcome, so a
    // deploy landing during the first call of a session made freshCheck location.reload() the page
    // he was mid-call on. Three deploys shipped today while he was dialing.
    touched = true;
    setTimeout(screenOutcome,0);
  });
  /* Skip was the last raw i++ in the file — the same bug class already fixed for advance(), and
     reachable without any teammate involvement: in the worker lane retireFromWorkerQ() shrinks the
     pool on the first logged number, so i++ from there lands one past the next person. Skipping is
     also the one action that must NOT count as work, so it does not touch _WORKED. */
  $('skip').onclick=function(){ advance(cur.c, null); };
  wireBrief(document, cur);
  wire();
  // Refresh the sheet for THIS lead. It is not re-created — it lives outside #app — so its
  // open/closed state and the chosen CIOC beat survive advancing to the next person.
  renderSheet(r);
}
/* ---- text ladder, person-keyed --------------------------------------------------------------
   Mirrors the board's _textStage. The cap is per HUMAN, not per case: three messages about property
   A plus three about property B is six messages to one phone. WITHIN a case we take max(local,
   ledger) because a send made in this browser is also in the next bake and summing would retire
   someone a stage early; ACROSS cases we sum, because those really are separate messages. */
var TEXT_MAX_TOTAL = 3;
/* Every case belonging to this person that this device can see.
   🔴 ROWS ALONE IS NOT ENOUGH. ROWS is the 400 DIALABLE leads of 783 qualifying, out of ~1,700 in
   the pipeline — so a person's other property, or a case whose auction already passed, is simply not
   in it. Counting texts over that subset lets a 4th message go to someone the ladder thinks is on
   their 2nd, which is the exact FTSA exposure the cap exists to prevent.
   `r.pcs` is the person's FULL case list from the build's own grouping, shipped inside the encrypted
   payload for exactly this lookup. (A previous version scanned notes for a `pkey` field instead —
   dead code: nothing ever writes pkey into a note, and the test that "proved" it passed on a
   hand-fabricated one. notes carries the TOUCHES for these cases; pcs carries WHICH cases.)
   TEXTPERSON (the server ledger) remains the authoritative backstop in textStage for cases that have
   left the pipeline entirely. */
function personCases(r){
  var k = r.pk, seen = {}, out = [];
  function add(c){ if(c && !seen[c]){ seen[c]=1; out.push(c); } }
  ROWS.forEach(function(x){ if(x.pk === k) add(x.c); });
  (r.pcs||[]).forEach(add);
  add(r.c);
  return out;
}
function textStage(r){
  var sends = 0, replied = false;
  personCases(r).forEach(function(c){
    var n = notes[c] || {}, local = 0;
    (n.touches||[]).forEach(function(t){
      if((t.ch||'') !== 'text') return;
      if(/replied|inbound|answered|said stop/i.test(t.out||'')){ replied = true; return; }
      local++;
    });
    sends += local;
  });
  var P = TEXTPERSON[r.pk] || 0;
  if(P > sends) sends = P;           // authoritative — includes cases that never shipped here
  if(replied) return 'replied';
  if(sends >= TEXT_MAX_TOTAL) return 'retired';
  return ['cold','follow','final'][sends] || 'retired';
}
/* Bodies are the board's t1/t2/t3 shapes. Compliance rides along: identify, one ask, opt-out on
   every message (the FTSA 15-day cure safe harbor is worthless if the STOP line is missing).

   Written as TEMPLATES and run through fillScript rather than interpolated here. SENDER is an
   OBJECT — a raw `+ SENDER +` renders "this is [object Object] with Biscayne Solutions Group", which is
   the same failure as the {sender} bug that already reached a live read-aloud script. Going through
   fillScript also inherits the Jose heal and the company-name heal for free. */
/* 8/17 masterclass voice (overnight 2026-08-18): cushion + parachute, one ask, no em dashes
   (reads as AI in a text), 'save this number' = drill card 14's read-back close at SMS size.
   ES drafts exist (workflow wf_50663fa7) but TEXT_T is EN-only until a language path lands. */
var TEXT_T = {
  cold:   'Hi{first}, this is {sender} with Biscayne Solutions Group. I just tried calling about {st1}. '
        + 'I am not selling anything and not trying to buy the house. If you have a plan, keep it. '
        + 'A free 5 minutes with our senior advisor, 30 plus years, gets you every option. '
        + 'Reply YES, or STOP to opt out.',
  follow: 'Hi{first}, {sender} with Biscayne Solutions Group again about {st1}. If your plan is moving, '
        + 'good, keep it. One question. Do you have it in writing yet? If not, our senior advisor '
        + 'can be the backup, free, 5 minutes. Reply YES, or STOP to opt out.',
  final:  'Hi{first}, last text from me, {sender} with Biscayne Solutions Group about {st1}. I hope your '
        + 'plan lands on time. If anything slips, one free call with our senior advisor maps what '
        + 'still works. Save this number even if you delete this text. Reply YES, or STOP to opt out.'
};
function textBody(r, stage){
  return fillScript(TEXT_T[stage] || TEXT_T.cold, r);
}
/* Callback. Writes n.next, the same field the board reads to re-surface a lead — so a promise made
   on the phone shows up on the laptop instead of living in his head. */
function setCallback(r, hours, label){
  var n = notes[r.c] = notes[r.c] || {status:'',note:''};
  var t = new Date(Date.now() + hours*3600*1000);
  /* LOCAL date parts, mirroring the board's _setNext. toISOString() is UTC, and Miami is UTC-4 —
     so any callback set between 8pm and midnight lands on TOMORROW's date, which is precisely the
     window he works. The board renders n.next as "due today"/"overdue Nd" against a local today(),
     so a UTC string is an off-by-one-day that only shows up in the evening. */
  n.next = t.getFullYear() + '-' + String(t.getMonth()+1).padStart(2,'0') + '-' + String(t.getDate()).padStart(2,'0');
  n.nextTs = t.getTime();
  /* NO SYNTHETIC TOUCH. A touch is a CONTACT record; scheduling a callback is not a contact, and the
     board has no 'note' channel anyway (call/door/email/letter/worker/text/surplus). Inventing one
     would inflate the very counts this system exists to make trustworthy — and `_lastTouchD` uses
     the last touch to break merge ties, so a fake one would let a reminder outrank a real outcome.
     The board itself sets follow-ups this way: _setNext writes n.next and pushes nothing.
     The human-readable trace goes in n.note, which is free text and is what n.note is for. */
  var line = today() + ' callback set ' + label;
  n.note = n.note ? (n.note.indexOf(line) >= 0 ? n.note : (n.note + '\n' + line)) : line;
  saveNotes(); queueSync();
  toast('Callback ' + label);
}

function screenOutcome(){
  SCREEN='outcome';
  /* THE SHEET STAYS. This screen is not "the logging screen" — it paints the instant he taps dial
     and is what he looks at for the WHOLE call, so hiding the script here stripped the dialogue
     from the exact minutes it exists for ("you forgot to put the whole dialogue up on that screen
     when I'm dialing" — correct, I had misread the workflow). The burial bug the hiding was meant
     to fix is already solved by the full-size sheetpad on every screen; collapsed, the sheet no
     longer steals taps. afterCall still hides it — there the call is genuinely over. */
  document.getElementById('sheet').classList.remove('hid');
  var r=cur, d=r.p[phIdx];
  // Captured BEFORE any outcome is written — see advance(). Once logOutcome runs, pool() may no
  // longer contain either this lead or the same neighbours, so there is nothing left to read it from.
  var nextC = (function(){ var P=pool(); for(var k=0;k<P.length;k++) if(P[k].c===r.c) return (P[k+1]||{}).c; return null; })();
  var btns=OUTCOMES.map(function(o){
    var cls=o.k==='dnc'?'dnc':(o.k==='wrong'||o.k==='notint')?'warn':'';
    return '<button class="'+cls+'" data-oc="'+o.k+'">'+esc(o.t)+'</button>';
  }).join('');
  // Use the resolved first name, not the raw roll string — `(r.o).split(' ')[0]` on the county's
  // "GORDON,STEVE" has no space to split on, so the question read "How did it go with GORDON,STEVE?"
  /* THE DIALOGUE, ON the screen he stares at for the whole call — not behind a tap. Same named/
     anonymous opener choice as the sheet (greeting a company or a name the card said not to use is
     worse than asking for the owner). EN with ES stacked, the board's proven pattern: in South
     Florida you do not know which language you need until they pick up. The full apparatus — CIOC,
     objections, MARS — stays one tap away in the sheet below. */
  var named=!!firstName(r);
  var talk = (SCRIPT.rec ? '<div class="nc" style="border-color:#e07b6a;color:#f2b8ad">'+esc(SCRIPT.rec)+'</div>' : '')
    + '<div class="ltag" style="margin-top:12px">WHEN THEY PICK UP '+langChips()+'</div>'
    + say(named?SCRIPT.op.en:SCRIPT.op.aen, named?SCRIPT.op.es:SCRIPT.op.aes, r)
    + '<div class="mut" style="font-size:12px;margin-top:4px">Close with: <b>'
    + (lang()==='es' ? '&iquest;Verdad que s&iacute;?' : 'That&rsquo;s fair, right?')
    + '</b> &middot; CIOC + objections in the script drawer below.</div>';
  /* THE REFERENCE BLOCK. This screen is up for the WHOLE call and carried only a first name and
     the number he dialled — so mid-conversation he could not see the address, the owner, what is
     owed, or when the sale is, and had to leave the page to look it up (2026-08-18: "add all of
     the information about the address etc so i can have the reference"). Everything below is
     already on the row; none of it costs a byte more of payload. */
  var _money = function(v){
    v = +v || 0;
    return v >= 1e6 ? '$' + (v/1e6).toFixed(2) + 'M' : v ? '$' + Math.round(v/1000) + 'k' : '';
  };
  var refRows = '';
  var addRef = function(k, v){ if(v) refRows += '<tr><td class="rk">'+k+'</td><td>'+v+'</td></tr>'; };
  addRef('Property', esc(r.a || ''));
  addRef('Owner', esc(r.o || ''));
  if(r.v || r.jg || r.py){
    var eqv = (r.v && (r.py || r.jg)) ? (+r.v - (+r.py || +r.jg)) : 0;
    /* UNDERWATER MUST SHOW. Showing equity only when positive quietly hid the single fact that
       changes the whole call: BARBOSA BRADS is owed $537k on a $473k house with the sale TODAY —
       there is no equity to protect, so cash-for-keys and surplus are off the table and the
       honest conversation is a short sale or the bank's own workout. Silence there reads as
       "fine". */
    addRef('Money', (r.v ? 'worth <b>' + _money(r.v) + '</b>' : '')
      + ((r.py || r.jg) ? ' &middot; owed ' + _money(r.py || r.jg) : ' &middot; owed <b>not posted</b>')
      + (eqv > 0 ? ' &middot; equity <b style="color:#7ad48f">' + _money(eqv) + '</b>'
         : (eqv < 0 ? ' &middot; <b style="color:#ff8a80">UNDERWATER ' + _money(-eqv)
                      + '</b> <span class="mut">(no equity &mdash; short sale / lender workout, not cash-for-keys)</span>'
            : '')));
  }
  /* THE DATE LINE — three honest states, because r.x and r.d don't mean one thing. r.x is
     auction OR filed date; r.d uses 9999 as the NO-AUCTION sentinel. The first version tested
     only d>=0 and rendered "SALE 8/7/2026 in 9999 days" on AMLONG's LP — the FILED date wearing
     a SALE label with the sentinel as a countdown, live, mid-call (2026-08-19). Same class as
     the worker's old 9999 burn: a sentinel must never cross a rendering boundary unlabeled. */
  if(r.x){
    var hasSale = (r.d != null && r.d < 9000);   // 9999 = no-auction sentinel, never a countdown
    if(!hasSale){
      addRef('Filed', esc(r.x) + ' <span class="mut">&middot; no auction date yet &mdash; months of runway</span>');
    } else if(r.d < 0){
      addRef('SALE', esc(r.x) + ' <b style="color:#ff8a80">PASSED ' + (-r.d) + ' day' + (r.d===-1?'':'s')
        + ' ago</b> <span class="mut">(surplus talk only)</span>');
    } else {
      addRef('SALE', esc(r.x) + ' <b style="color:' + (r.d <= 7 ? '#ff8a80' : '#F4E5A7') + '">in '
        + r.d + ' day' + (r.d===1?'':'s') + '</b>');
    }
  }
  if(r.dd || r.bd || r.sf) addRef('Property type', esc([r.dd || '', (r.bd ? r.bd + 'BR' : ''),
      (r.ba ? r.ba + 'BA' : ''), (r.sf ? r.sf + ' sf' : '')].filter(Boolean).join(' &middot; ')));
  if(r.pl) addRef('Foreclosing', esc(r.pl));
  addRef('Case', esc(r.c || '') + (r.fo ? ' &middot; folio ' + esc(r.fo) : ''));
  if(r.p && r.p.length > 1) addRef('Their numbers',
    r.p.map(function(x, i){ return (i === phIdx ? '<b>' + fmt(x) + ' (dialing)</b>' : fmt(x)); }).join(' &middot; '));
  var refBlock = refRows
    ? '<div class="refbox"><div class="ltag" style="margin:0 0 6px">THE FILE &mdash; for reference on this call</div>'
      + '<table class="reft">' + refRows + '</table>'
      + '<div class="flinks" style="margin-top:9px">'
      + fileLinks(r).map(function(x){
          return '<a href="' + esc(x[1]) + '" target="_blank" rel="noopener">' + esc(x[0]) + '</a>'; }).join('')
      + '</div>'
      + briefButtons(r)
      + '</div>'
    : '';
  $('app').innerHTML='<div class="card"><div class="addr" style="font-size:18px">How did it go with '+esc(firstName(r)||'them')+'?</div>'
    +'<div class="own">'+fmt(d)+'</div>'
    + refBlock
    /* REDIAL — same number, no outcome logged, place kept. For the dropped call, the accidental
       hang-up, the straight-to-voicemail retry. An ANCHOR, not a JS navigation: tel: via href is
       the proven path (the main dial button), and returning from the dialer lands right back on
       this screen because the SCREEN guard defers any sync repaint. */
    +'<a class="redial" href="'+dialHref(d)+'"'+dialTarget()+' id="redial">&#8635;&nbsp; Redial '+fmt(d)+'</a>'
    + talk
    +'<div class="oc" style="margin-top:10px">'+btns+'</div>'
    +'<div class="vm" id="vm" style="display:none"></div></div><div class="sheetpad"></div>';
  /* A redial IS a dial — record it, or the dial-through count undercounts the actual work (the
     exact logging gap this page exists to close). oc:'redial' marks it as outcome-pending; the
     outcome he eventually taps logs its own entry for that attempt. */
  wireBrief(document, r);
  $('redial').addEventListener('click', function(){
    var n=notes[r.c]=notes[r.c]||{status:'',note:''};
    n.dials=n.dials||[];
    n.dials.push({d:today(), ts:nowTS(), tsu:Date.now(), ph4:String(d).slice(-4), oc:'redial'});
    touched=true; saveNotes(); queueSync();
  });
  Array.prototype.forEach.call(document.querySelectorAll('.oc button'), function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=true;});
      /* EVERYTHING inside try/catch, and the catch RE-ENABLES the buttons. The handler's first act
         is disabling all seven buttons; any throw after that left a screen of dead buttons with no
         message — "sometimes those buttons do not work at all", reported from the field. A dead
         screen is now impossible: on any error the buttons come back, the error is toasted, and
         the details land in the error log (top bar) for a screenshot. */
      try{
      var o=OUTCOMES.filter(function(z){return z.k===b.dataset.oc;})[0];
      /* The voicemail script must SURVIVE for him to read it. It renders into #vm, which lives
         inside #app — and on a single-phone lead the flow fell straight through to afterCall(),
         whose first statement replaces #app.innerHTML. The script he was told to read aloud was
         destroyed in the same synchronous handler that created it, before the browser ever painted.
         So: show it, and wait for him to say he is done. ONE language at a time (fcLang) — the
         stacked EN+ES block was part of the "too many transcripts" pile-up. */
      var go;
      /* The vm block repaints ITSELF on a language toggle — going through setLang() would rebuild
         the whole outcome screen and wipe the script he is mid-way through reading aloud. */
      function paintVM(){
        $('vm').style.display='block';
        $('vm').innerHTML='<b>Read this. Do not use a recording. '+langChips()+'</b>'
          + say(VMEN, VMES, r)
          + (go ? '<button id="vmdone" style="margin-top:14px">Done reading &rarr;</button>' : '');
        if(go && $('vmdone')) $('vmdone').onclick = go;
        Array.prototype.forEach.call($('vm').querySelectorAll('.lchip'), function(c){
          c.onclick=function(ev){ ev.stopPropagation();
            try{ localStorage.setItem('fcLang', c.dataset.lang); }catch(e){}
            paintVM(); };
        });
      }
      if(o.k==='voicemail') paintVM();
      if(o.k==='dnc' && !confirm('They asked to stop. This closes every channel, permanently, on every device. Continue?')){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;}); return;
      }
      var _fresh = logOutcome(r,o,d);
      var _okmsg = _fresh ? ('✓ '+o.t+' — logged') : ('✓ dial counted — '+o.t+' already logged today');
      /* The shield arms INSIDE go() — at the actual screen swap — not at outcome-tap time. On the
         voicemail path the swap happens seconds later (the Done-reading button), and arming early
         both missed that swap and ate legitimate taps on the just-painted voicemail block. */
      var _shield = function(){ window._tapShieldUntil = Date.now() + 400; };
      if(o.k==='dnc'||o.k==='wrong'||o.k==='notint'){
        /* An outcome that ENDS the relationship gets no follow-up offer — showing a Text button
           after someone says do-not-contact is how a compliance breach happens by muscle memory. */
        go = function(){ _shield(); toast(_okmsg); advance(r.c,nextC); };
      } else {
        /* EVERY other outcome — including NO ANSWER and VOICEMAIL — lands on the after-call panel,
           which is where the follow-up text lives.
           THE BUG THIS FIXES (2026-08-18, reported from the field): no-answer/voicemail used to
           jump straight to the lead's NEXT number and skip afterCall entirely. Skiptrace returns
           3-4 numbers on most leads and no-answer is far and away the most common outcome, so the
           follow-up text was effectively unreachable — the one moment a text matters most (they
           just saw a missed call from you) was the one moment the button never appeared.
           Cycling numbers is not lost: afterCall now carries a "try the next number" button. */
        go = function(){ _shield(); toast(_okmsg); afterCall(r,o,nextC); };
      }
      // A voicemail script he has not finished reading must not be replaced out from under him.
      // paintVM re-runs now that `go` exists, adding the Done button (and keeping it across
      // language toggles).
      if(o.k==='voicemail'){ paintVM(); } else go();
      /* go() runs IMMEDIATELY now. The 650ms hold showed a screen of disabled buttons between every
         outcome and the next action — pure dead time, times a hundred dials a day. The toast (now
         z-60, above the sheet) is the confirmation, and it overlaps the next screen harmlessly. */
      }catch(err){
        Array.prototype.forEach.call(document.querySelectorAll('.oc button'),function(x){x.disabled=false;});
        logErr(err, 'outcome:'+(b.dataset.oc||''));
        toast('Error logging that — buttons re-enabled, try again');
      }
    };
  });
  wireLang($('app'));
}
/* MAKE THE CONFIRM TRUE. The do-not-contact dialog promises "this closes every channel, permanently,
   on every device" — and the code behind it wrote optout+status to ONE case and nothing else.
   The person said stop about THEMSELVES: their second property stayed dialable, their number stayed
   textable, and nothing suppressed a case keyed differently. A promise the code does not keep is
   worse than no promise, because he stops checking.
   Mirrors the board's _stopEverywhere (tracker_template.html:5666-5677): every case for the person,
   a '#digits' person-level key that optPhones()/suppressed() actually read back, and the number on
   the do-not-text list. Never gated on hours, cadence or retire state — "they told me to stop"
   outranks every other condition. */
function stopEverywhere(r, digits){
  var stamp = today(), cases = personCases(r);
  cases.forEach(function(c){
    var n = notes[c] = notes[c] || {status:'',note:''};
    n.optout = n.optout || stamp;
    n.status = 'DO NOT CONTACT';
    n.optlog = n.optlog || [];
    n.optlog.push({ts:nowTS(), tsu:Date.now(), act:'set-local', src:'call-mode'});
    // n.dntph is the field _mergeLead carries laptop->phone precisely so a do-not-text survives the
    // device. Union only — a suppression list may only ever grow.
    var d = n.dntph = n.dntph || [];
    (r.p||[]).forEach(function(p){ if(d.indexOf(p) < 0) d.push(p); });
    if(digits && d.indexOf(String(digits)) < 0) d.push(String(digits));
  });
  // Person-level keys, the shape optout_sync.py and the STOP-reply loop write. Without these the
  // opt-out suppresses only cases we happen to know about today.
  (r.p||[]).forEach(function(p){
    var k = '#' + p, n = notes[k] = notes[k] || {status:'',note:''};
    n.optout = n.optout || stamp;
    n.status = 'DO NOT CONTACT';
    n.optlog = n.optlog || [];
    n.optlog.push({ts:nowTS(), tsu:Date.now(), act:'set-local', src:'call-mode'});
  });
  // Mirror onto the device Do-Not-Text set too, so the board sees it without waiting for a sync.
  try{
    var s = JSON.parse(localStorage.getItem('fcDNT')||'[]')||[];
    (r.p||[]).forEach(function(p){ if(s.indexOf(p) < 0) s.push(p); });
    localStorage.setItem('fcDNT', JSON.stringify(s));
  }catch(e){}
  return cases.length;
}

/* The 20 seconds after a call is where the follow-up is either captured or lost forever. This is the
   screen that exists because he dials from the phone, writes the number on paper, and never comes
   back to the laptop to log it. Everything here writes to the SAME notes the board reads. */
function afterCall(r, o, nextC){
  SCREEN='after';
  document.getElementById('sheet').classList.add('hid');   // same tap-thief reasoning as screenOutcome
  var st = textStage(r), miss = (o.k==='noanswer'||o.k==='voicemail');
  var num = r.p[phIdx];
  /* THREE GATES, all of which were missing. The text button shipped with only the ladder check.
     - Do-Not-Text: dntSet() was written and then never called anywhere, and n.dntph — the field
       _mergeLead carries laptop->phone for exactly this — was never read. So a number marked
       do-not-text on the board was still one tap from an SMS on the device that sends them.
     - FTSA hours: flClock() was consumed in exactly one place, screenLead's banner, which afterCall
       destroys when it replaces #app. Florida's FTSA covers "telephonic sales calls" and the statute
       defines those to include text messages, so the window applies to this button too.
     - Opt-out: belt and braces. suppressed() should already have removed the lead, but this button
       can be reached from a card rendered before a sync landed. */
  var dnt = false;
  try{ dnt = (JSON.parse(localStorage.getItem('fcDNT')||'[]')||[]).indexOf(num) >= 0; }catch(e){}
  if(!dnt) dnt = ((notes[r.c]||{}).dntph||[]).indexOf(num) >= 0;
  var fl = flClock();
  var txt = '';
  if(hardSuppressed(r))     txt = '<div class="nc">This lead is suppressed ('+esc(hardSuppressed(r))+'). Do not text.</div>';
  else if(dnt)              txt = '<div class="nc">This number is on the do-not-text list. Call only.</div>';
  else if(st === 'retired') txt = '<div class="nc">Three messages already sent to this person. The ladder is closed — call only.</div>';
  else if(st === 'replied') txt = '<div class="nc">They have replied before. Do not send a cold-ladder text; talk to them.</div>';
  else if(!fl.ok)           txt = '<div class="nc">It is '+esc(fl.txt)+' in Florida. FTSA texting hours are 8:00 AM to 8:00 PM Eastern — this will be here in the morning.</div>';
  else {
    var lbl = st==='cold' ? 'Send 1st text' : st==='follow' ? 'Send follow-up (2 of 3)' : 'Send final text (3 of 3)';
    txt = '<button id="tx" class="'+(miss?'':'ghost')+'">'+lbl+'</button>';
  }
  /* NAME THE NUMBER. The text button targets r.p[phIdx] — whichever number he actually dialled —
     but the panel never said which, so on a 3-number lead he was approving a message to an unnamed
     recipient. If it is going to someone's phone, he gets to see whose. */
  /* BOOK IT WHILE THEY ARE STILL ON THE LINE. Only on APPOINTMENT SET, and first on the screen,
     because this is the one action with an expiry: the moment they agree is the moment to put it
     on a calendar. "I'll email you a link" is where booked calls go to die.
     Prefilled with the name and THE NUMBER HE ACTUALLY DIALLED (r.p[phIdx], not phones[0]) so he is
     not retyping a phone number while someone waits on the other end. attendeePhoneNumber is the
     field slug Cal.com uses for the "Attendee phone number" location, which is how both event types
     are configured -- the owner never joins a video call, we ring them. */
  var book = '';
  if(o.k === 'appt'){
    var bq = 'name=' + encodeURIComponent(firstName(r) || r.o || '')
           + '&attendeePhoneNumber=' + encodeURIComponent('+1' + String(num||'').replace(/\D/g,'').slice(-10))
           + '&notes=' + encodeURIComponent((r.a||'') + (r.c ? (' | case ' + r.c) : ''));
    book = '<div class="afterlab">Put it on the calendar</div>'
         + '<a class="btn" id="bk" href="' + BOOKURL + '?' + bq + '" target="_blank" rel="noopener">'
         + '&#128197; Book it now &mdash; they are still on the line</a>'
         + '<div class="mut" style="font-size:12px;margin-top:6px">Their name and this number are '
         + 'already filled in. Pick the slot, confirm, done.</div>';
  }
  $('app').innerHTML = '<div class="card">'
    + '<div class="addr" style="font-size:17px">Logged: '+esc(o.t)+'</div>'
    + '<div class="own">'+esc(firstName(r)||r.o||'')+' &middot; '+fmt(num)+'</div>'
    + book
    + '<div class="afterlab">Follow-up text</div>' + txt
    + '<div class="afterlab">Call them back</div>'
    + '<div class="cbrow">'
    +   '<button class="cb" data-h="3">In 3 hours</button>'
    +   '<button class="cb" data-h="20">Tomorrow</button>'
    +   '<button class="cb" data-h="72">In 3 days</button>'
    + '</div>'
    /* THE OTHER NUMBERS. no-answer/voicemail used to auto-jump here; now it is a deliberate tap,
       so the text offer above is never skipped past. Only shown when a number is actually left. */
    + ((miss && phIdx + 1 < (r.p||[]).length)
        ? '<button id="nph" class="ghost" style="margin-top:14px">&#128222; Try their next number ('
          + (phIdx + 2) + ' of ' + r.p.length + ')</button>'
        : '')
    + '<button id="nx" style="margin-top:14px">Next lead &rarr;</button>'
    + '</div><div class="sheetpad"></div>';
  var go = function(){ advance(r.c, nextC); };
  $('nx').onclick = go;
  if($('nph')) $('nph').onclick = function(){
    window._tapShieldUntil = Date.now() + 400;
    phIdx++; toast('Next number'); screenLead();
  };
  Array.prototype.forEach.call(document.querySelectorAll('.cb'), function(b){
    b.onclick = function(){
      setCallback(r, +b.dataset.h, b.textContent.toLowerCase());
      Array.prototype.forEach.call(document.querySelectorAll('.cb'),function(x){x.disabled=true;});
      b.classList.add('on');
      /* Setting a callback IS choosing what happens next — making him also tap "Next lead" was one
         more mandatory tap in a flow he runs a hundred times a day. Brief hold so the chip's
         confirmation state is visible, then advance. EXCEPT while the "did it actually send?"
         confirm is up — auto-advancing there would destroy the unanswered confirm and the text
         send would never be logged. He answers that first; Next lead is still one tap away. */
      if(!$('txy')) setTimeout(go, 450);
    };
  });
  if($('tx')) $('tx').onclick = function(){
    var body = textBody(r, st);
    /* Log the OPEN, not a send. Opening a composer is not a delivery — the board draws this exact
       line (the worker's textopen posts confirmed:false) and blurring it is how the ladder burns a
       touch on a message that was never sent. He confirms below once it is actually gone. */
    var n = notes[r.c] = notes[r.c] || {status:'',note:''};
    n.textopen = today(); saveNotes(); queueSync();
    var openComposer = function(){
      location.href = 'sms:' + r.p[phIdx] + (/iPhone|iPad|Mac/.test(navigator.userAgent) ? '&' : '?')
        + 'body=' + encodeURIComponent(body);
    };
    openComposer();
    /* THE PANEL. Before: "did it send?" with Yes / No — and No just toasted and DEAD-ENDED, so a
       message that failed to send had no way back except leaving the lead. Now it shows the exact
       text that went out (the output) and keeps a RESEND button alive on every path. */
    $('tx').outerHTML = '<div class="txconf" id="txconf0">Composer opened. Did it actually send?</div>'
      + '<div class="txbody" id="txbody"><span class="lbl">what was sent &middot; ' + esc(st) + '</span>'
      + esc(body) + '</div>'
      + '<button id="txy">&#10003; Yes, it sent</button>'
      + '<button id="txr" class="ghost">&#8635; Re-open composer (send again)</button>'
      + '<button id="txn" class="ghost">No, I did not send it</button>';
    $('txy').onclick = function(){
      var nn = notes[r.c] = notes[r.c] || {status:'',note:''};
      nn.touches = nn.touches || [];
      nn.touches.push({d:today(), ts:nowTS(), tsu:Date.now(), ch:'text', out:'Text sent — ' + st, by:caller()});
      saveNotes(); queueSync();
      if(!_ftsaCapToast(nn)) toast('Text logged');
      go();
    };
    /* RESEND. Same body, same number — re-fires the composer. Does NOT log anything: a second open
       is still not a delivery, and the Yes button remains the only thing that writes the touch. */
    $('txr').onclick = function(){
      openComposer();
      toast('Composer re-opened — press send in Messages');
      var c = $('txconf0'); if(c) c.textContent = 'Re-opened. Did it send this time?';
    };
    $('txn').onclick = function(){
      toast('Not logged as sent');
      var c = $('txconf0');
      if(c) c.innerHTML = 'Not logged. Tap <b>Re-open composer</b> to try again, or move on '
        + '&mdash; this lead stays in the queue.';
    };
  };
}
/* FTSA caps telephonic (call/text) contact at 3 per lead per 24h. Before this, the only place that
   counted same-day touches was analyst.py's WEEKLY scan reading ALL-TIME touches -- a 4th touch
   surfaced days later, by which point a 5th and 6th could already be sitting in the ledger too. The
   dial or text has already happened by the time either caller below runs, so this cannot un-ring the
   phone; it exists to make the cap-crossing impossible to miss RIGHT NOW and to mark the exact touch
   an audit would need to find, instead of leaving that to a future re-scan of timestamps. */
function _telephonicToday(n){
  return (n.touches||[]).filter(function(x){ return x.d===today() && (x.ch==='call'||x.ch==='text'); }).length;
}
function _ftsaCapToast(n, extra){
  var cnt = _telephonicToday(n);
  if(cnt <= 3) return false;
  n.touches[n.touches.length-1].capExceeded = true;
  toast('⚠ FTSA cap: touch #' + cnt + ' today for this lead (max 3/24h).' + (extra ? ' ' + extra : '') +
        ' Stop contacting them until tomorrow.', {bad:true, ms:8000});
  return true;
}
/* Mirrors tracker_template.html's `callout` dispatcher so the phone and the laptop write the SAME
   shape. n.dials is additive and never deduped — it is the dial-through count the whole logging
   problem exists to recover; the touch itself stays deduped for cooldown purposes. */
function logOutcome(r,o,digits){
  var n=notes[r.c]=notes[r.c]||{status:'',note:''};
  n.touches=n.touches||[];
  var last=n.touches[n.touches.length-1];
  /* Returns whether a NEW touch was written. The same-day dedupe is correct (cooldown math), but
     silently collapsing the second identical tap while stamping "✓ logged" read as a broken
     button — the caller now tells the truth: "dial counted, already logged today". */
  var fresh = !(last && last.d===today() && last.ch==='call' && last.out===o.t);
  // `by` = who made this call (from their access code). It is what lets the other phone say
  // "called 2h ago by Carlos" instead of skipping a lead for no visible reason.
  if(fresh){
    n.touches.push({d:today(),ts:nowTS(),tsu:Date.now(),ch:'call',out:o.t,by:caller()});
    _ftsaCapToast(n);
  }
  n.dials=n.dials||[];
  n.dials.push({d:today(),ts:nowTS(),tsu:Date.now(),ph4:String(digits).slice(-4),oc:o.k,by:caller()});
  n.cooldownH=o.h;
  if(o.k==='appt') n.status='Appointment';
  else if(o.k==='dnc'){ stopEverywhere(r, digits); }
  else if(o.k==='wrong'){ n.wrongown=n.wrongown||today(); n.status=n.status||'Wrong number'; }
  else if(o.k==='talked') n.status=n.status||'Contacted';
  else if(o.k==='notint') n.status=n.status||'Not interested';
  // Retire it from the worker's queue on ANY logged outcome — same rule as the board's `callout`
  // dispatcher. Without this a lead worked here reappears in tomorrow's worker queue.
  retireFromWorkerQ(r.c);
  if(_WORKED.indexOf(r.c) < 0) _WORKED.push(r.c);
  /* THE FLAG freshCheck READS. It was declared `var touched=false`, read as `if(!touched) reload()`,
     and never assigned anywhere — so the guard its own comment promises ("never auto-reload once an
     outcome has been logged, that would lose his position mid-sequence") did the exact opposite.
     iOS returning from the dialer fires the freshness check; if a new build had landed, the page
     reloaded and dropped him back to lead 1 mid-sequence. Set it the moment work exists to lose. */
  touched = true;
  saveNotes();
  queueSync();
  return fresh;
}
/* Write-back rides the board's existing Supabase team sync.
   PUSH IMMEDIATELY — do NOT use the board's 1.5s syncPushSoon debounce. iOS backgrounds this tab the
   instant the dialer opens, and a debounced push scheduled at tap time simply never fires. The cost
   of pushing on every outcome is one small request; the cost of missing one is the logged call.
   If fcTeamKey is absent we still log LOCALLY and say so — never block dialing on sync setup. */
var _pushRetryT=null, _pushRetryN=0;
function queueSync(){
  var k=null; try{k=localStorage.getItem('fcTeamKey');}catch(e){}
  if(!k){ $('sync').textContent='Logged to this phone only — team sync is off'; return; }
  /* RETRY. The push fired right before tel:/sms: navigation dies when iOS backgrounds the tab,
     and a failed push used to be simply gone — the outcome lived on this phone only.
     syncPush() NEVER rejects (it swallows failures internally and stamps fcLastPush only on a
     real 2xx), so a .catch-based retry is dead code — success is detected by the stamp moving. */
  var _before=null; try{ _before=localStorage.getItem('fcLastPush'); }catch(e){}
  var _resched=function(){
    if(_pushRetryN>=4) return;
    clearTimeout(_pushRetryT); _pushRetryN++;
    _pushRetryT=setTimeout(queueSync, 4000*_pushRetryN);
  };
  try{
    syncPush().then(function(){
      var _after=null; try{ _after=localStorage.getItem('fcLastPush'); }catch(e){}
      if(_after && _after!==_before){ clearTimeout(_pushRetryT); _pushRetryT=null; _pushRetryN=0; return; }
      _resched();
    }).catch(_resched);
  }catch(e){ _resched(); }
}
/* One shared dismissal timer, cleared on every show. Without this, back-to-back toasts (the norm
   now that the 650ms screen-holds are gone) let the FIRST toast's 1400ms timer strip the class off
   the SECOND — the confirmation he most needs flashes for a few ms and dies. */
var _toastT=null;
function toast(t,opts){
  var el=$('toast'); el.textContent=t; el.classList.add('on');
  el.classList.toggle('bad', !!(opts && opts.bad));
  clearTimeout(_toastT);
  _toastT=setTimeout(function(){el.classList.remove('on');},(opts && opts.ms) || 1400);
}

/* ON-DEVICE ERROR LOG. I cannot see his phone; "sometimes the buttons do not work" is a symptom
   with no traceback. Every caught error and every uncaught one lands in a small ring buffer
   (fcErrLog, last 20), and when any exist the top bar shows a red chip — tapping it shows the log
   for a screenshot. Field reports become tracebacks. */
function logErr(err, where){
  try{
    var log = JSON.parse(localStorage.getItem('fcErrLog')||'[]');
    log.push({t: nowTS(), w: where||'', m: String(err && err.message || err).slice(0,200),
              s: String(err && err.stack || '').slice(0,300)});
    localStorage.setItem('fcErrLog', JSON.stringify(log.slice(-20)));
  }catch(e){}
}
window.addEventListener('error', function(ev){ logErr(ev.error||ev.message, 'window'); });
/* Tap OUTSIDE an open sheet closes it. An open drawer covers most of the card; taps on covered
   buttons hit the drawer body and did nothing visible — one of the shapes behind "sometimes the
   buttons do not work at all". Tap-outside-to-close is the behaviour every sheet UI trains. */
/* Post-swap tap shield (see the outcome handler): a click arriving <400ms after a screen swap is
   the tail of a double-tap aimed at the OLD screen — swallow it before any handler sees it. */
document.addEventListener('click', function(ev){
  if(window._tapShieldUntil && Date.now() < window._tapShieldUntil){
    /* stopImmediatePropagation, not stopPropagation: the sheet-close listener below is on the
       SAME node (document, capture) and would otherwise still run — a shielded ghost tap could
       close the script sheet mid-call. */
    ev.stopImmediatePropagation(); ev.preventDefault();
  }
}, true);
document.addEventListener('click', function(ev){
  var sh = document.getElementById('sheet');
  if(sh && sh.classList.contains('open') && !sh.contains(ev.target)) sh.classList.remove('open');
}, true);
window.addEventListener('unhandledrejection', function(ev){ logErr(ev.reason, 'promise'); });
function wire(){
  Array.prototype.forEach.call(document.querySelectorAll('.lane button'), function(b){
    b.onclick=function(){ lane=b.dataset.l; i=0; render(); };
  });
  // "see the call log" on the hidden-count line — the registry of who has been called, by whom
  var rl = $('reglink');
  if(rl) rl.onclick = function(e){ e.preventDefault(); screenRegistry(); };
}
/* Stale-cache heal. iPhone Safari is the named worst offender and a phone quietly serving last
   week's list is the failure most likely to go unnoticed. Never auto-reload once an outcome has
   been logged — that would lose his position mid-sequence. Offer the pill instead. */
var touched=false;
async function freshCheck(){
  try{
    /* BOTH URL FORMS. This assumed the path was always a DIRECTORY (/call/ -> /call/index.html),
       so opening the page at its explicit file URL — a bookmark, an iOS Add-to-Home-Screen, a
       pasted link — built /call/index.html/index.html, 404'd, and the catch below swallowed it in
       silence. The whole job of this function is to notice a stale cache ("a phone quietly serving
       last week's list is the failure most likely to go unnoticed", per the comment above it), and
       for anyone on the file URL it had been doing nothing at all. Caught by the first runtime test
       of this page, 2026-08-26. */
    var _p=location.pathname;
    var u=/\.html?$/i.test(_p) ? _p : _p.replace(/\/$/,'')+'/index.html';
    var res=await fetch(u+'?_='+Date.now(),{headers:{'Range':'bytes=0-1200'}});
    var t=await res.text();
    /* [A-Za-z0-9]+, not [a-f0-9]+. The signature is a sha256 hex prefix today, so hex-only works —
       until the day it does not, and then this check silently goes back to never firing. The whole
       job of this function is to notice staleness; it must not be one format change from dead. */
    var m=t.match(/SIG="([A-Za-z0-9]+)"/);
    if(m && m[1]!==SIG){ if(!touched) location.reload(); else $('pill').style.display='block'; }
  }catch(e){}
}
/* ══════════════════════ SCRIPT SHEET ══════════════════════
   Collapsed by default: one line of peek. Tap the grip (or the peek) to expand, tap again to close.
   The lead's numbers stay visible behind it — that is the whole point of a sheet rather than an
   overlay. It lives outside #app so advancing a lead never destroys it mid-sentence. */
var SCRIPT={"op": {"en": "Hi, is this {first}? My name is {sender} with Biscayne Solutions Group. I am not your lender, not the government, and I am not calling to buy your house. There is a court case on {st1}{date}, and I am probably the only person calling you this week who is NOT trying to take the property. Two minutes, then I will get out of your hair. Fair?", "es": "Hola, ¿hablo con {first}? Mi nombre es {sender}, de Biscayne Solutions Group. No soy su prestamista, no soy del gobierno, y no le vengo a comprar la casa. Hay un caso en la corte sobre {st1}{date}, y posiblemente soy el único que le llama esta semana que NO anda detrás de la propiedad. Dos minutos y lo dejo tranquilo. ¿Le parece?", "aen": "Hi, am I speaking with the owner of {st1}? My name is {sender} with Biscayne Solutions Group. I am not your lender, not the government, and I am not calling to buy your house. There is a court case on {st1}{date}, and I am probably the only person calling you this week who is NOT trying to take the property. Two minutes, then I will get out of your hair. Fair?", "aes": "Hola, ¿hablo con el dueño de {st1}? Mi nombre es {sender}, de Biscayne Solutions Group. No soy su prestamista, no soy del gobierno, y no le vengo a comprar la casa. Hay un caso en la corte sobre {st1}{date}, y posiblemente soy el único que le llama esta semana que NO anda detrás de la propiedad. Dos minutos y lo dejo tranquilo. ¿Le parece?"}, "cioc": [{"k": "CUSHION", "w": "Agree, normalize, include them in the majority. Never argue — and never make them feel stupid for the plan they already have (the mod, the lawyer, the realtor, the check).", "s": "I can understand that — the majority of people we speak to feel exactly the same way. Everybody hits that wall at some point, and that's fair."}, {"k": "ISOLATE", "w": "Make the objection the ONLY thing in the way. Force open-or-closed. The 8/17 scalpel: ask what they TRULY want — the answer tells you which program to pitch.", "s": "What is it that you truly want to do with this property?  ·  If talking to me couldn't interfere with your plan at all, is there any other reason not to spend five minutes?"}, {"k": "OVERCOME", "w": "One reframe + one analogy + one what-if. Not three. ONE. When they have a plan they believe in: INSURE the hope, never fight it — you are the parachute, not the enemy.", "s": "I don't want to be your bank or your middleman — I want to be your parachute. You're betting your house on the timing; keep your plan, and let us work on postponing the sale in parallel. If it works, I did you a favor and you owe me one."}, {"k": "CLOSE", "w": "Fairness micro-agreements, then MEASURE what they DID, not what they said: the paperwork trial close, the read-back test, the five-minute advisor handoff.", "s": "That's fair, right?  ·  'Let me get the paperwork together — if we agree we shake hands, if not I wasted fifteen minutes and we part friends.'  ·  'Save my number right now… now read it all back to me.' Clean read-back = in; 'pen ran out of ink' = don't count the deal."}], "f15": "Totally understand. One thing and I am gone: our senior advisor — over 30 years in mortgages and foreclosure workouts — reviews your case free, five minutes, on the phone. If nothing fits, we part friends. Fair?", "mars": "Before we start, a few things I am required to tell you: Biscayne Solutions Group is not associated with the government, and our service is not approved by the government or by your lender. Even if you use our service, your lender may not agree to change your loan. You may stop doing business with us at any time. This consultation is free, and you will never be asked to pay us a fee before you get results.", "never": ["Any promised outcome or date. \"Options,\" never \"we will save your home.\"", "Anything about the attorney's case or its merits. Money side only.", "\"We place loans\" or any rate as ours. Licensed lenders lend; we INTRODUCE.", "\"My supervisor\" · \"our attorneys\" · \"my clients\" · company + \"30 years.\"", "Money up front. No fees, ever (MARS + FS 501.1377).", "\"Foreclosure\" to someone who is not the owner.", "An argument. Calls end warm."], "obj": [{"n": 1, "t": "The Bank Mod Shield", "say": "“The bank is already working with me on a modification. I don't want to mess that up by talking to anyone else.”", "reb": ["Good — you should absolutely keep working that modification, and nothing we do touches it. Let me ask you one thing: if talking to me couldn't interfere with the mod at all, is there any other reason not to spend five minutes?", "Here's what most people don't know: in Florida the mod review and the foreclosure case run on separate tracks. The court date keeps moving while the bank 'reviews,' and denial letters routinely land two or three weeks before the sale — with no time left to react. It's like driving with a spare in the trunk: you don't need it until the tire blows on the highway. What if that denial comes 20 days out — what's your move that day? The consult is free, no fees ever, no commitment. If the mod comes through, we shake hands and part friends."], "one": "The mod review and the sale date run on separate tracks — the court doesn't pause while the bank 'reviews.'", "es": {"say": "El banco ya está trabajando conmigo en una modificación. No quiero dañar eso hablando con otra persona.", "reb": ["Muy bien, y siga con esa modificación — nada de lo que hacemos la toca. Déjeme preguntarle una sola cosa: si hablar conmigo no pudiera afectar la modificación en lo absoluto, ¿habría alguna otra razón para no escuchar cinco minutos, o es solamente eso?", "Esto es lo que casi nadie sabe: en la Florida la revisión de la modificación y el caso de la corte corren por carriles separados. La fecha de la corte sigue avanzando mientras el banco \"revisa\", y las cartas de negación llegan a menudo pocos días antes de la subasta. Es como reparar el motor mientras el carro sigue rodando hacia el semáforo: la reparación es real, pero el semáforo no espera. ¿Y si la modificación le llega negada con dos semanas por delante — quiere estar viendo sus opciones ese día, o tenerlas ya listas? Sin costo y sin compromiso. ¿Verdad que sí?"], "one": "La revisión de la modificación y la fecha de la subasta corren por carriles separados — la corte no se detiene mientras el banco \"revisa\"."}}, {"n": 2, "t": "The Lawyer Shield", "say": "“My attorney is handling it. Talk to him, not me.”", "reb": ["Keep your attorney — seriously, we work WITH counsel, never around them, and having defense in place puts you ahead of most people I talk to. One question though: your lawyer is defending the case. Has anyone sat down with you and shown what happens to your equity when the defense runs out?", "Defense buys time; it doesn't decide what you do with the time. Meanwhile the bank's fees and daily interest run against your equity every month the case drags. Your attorney is playing defense — but nobody's playing offense on your money. What if the judge rules against you at the next hearing and you've got weeks, not months — is there a plan for that day, or just the appeal? Five free minutes with a senior advisor, 30-plus years, coordinated with your lawyer, no fees ever. If your counsel says we're not needed, we shake hands and part friends."], "one": "Your lawyer is playing defense — nobody is playing offense on your equity while the meter runs.", "es": {"say": "Mi abogado se está encargando. Hable con él, no conmigo.", "reb": ["Quédese con su abogado — en serio, nosotros trabajamos CON los abogados, nunca por encima de ellos, y tener defensa puesta lo pone por delante de la mayoría de la gente con la que hablo. Una pregunta nada más: su abogado está defendiendo el caso. ¿Alguien se ha sentado con usted a mostrarle qué pasa con su equidad cuando la defensa se acabe?", "La defensa compra tiempo; no decide qué hace usted con ese tiempo. Mientras tanto los honorarios del banco y los intereses diarios corren contra su equidad cada mes que el caso se alarga. Su abogado juega a la defensa, pero nadie está jugando a la ofensiva con su dinero. ¿Y si el juez falla en contra en la próxima audiencia y le quedan semanas y no meses — hay un plan para ese día, o solamente la apelación? Cinco minutos gratis con nuestro asesor principal, más de treinta años en esto, coordinado con su abogado, sin honorarios nunca. Si su abogado dice que no hacemos falta, nos damos la mano y quedamos como amigos."], "one": "Su abogado juega a la defensa — nadie está jugando a la ofensiva con su equidad mientras el reloj corre."}}, {"n": 3, "t": "The Postponement Gambler", "say": "“That sale date doesn't mean anything. It's been cancelled twice already. It always gets pushed.”", "reb": ["You're right that sales get reset — you've lived it twice, so I'm not going to tell you deadlines never move. But let me ask: do you know WHY those two got pushed? Because each cancellation had a reason — a review, a motion — and those reasons run out.", "And here's the part nobody mentions: every push was billed to you. Post-judgment interest runs daily — call it $70-plus a day on a $300k judgment — plus new attorney fees each reset, all coming out of your equity. When one finally holds, the certificate of title can issue about ten days after the auction, and every option collapses to zero. It's the hurricane that turned away twice — the third one doesn't care about your track record. What if this is the date that sticks and you've got ten days? Having the plan ready costs you nothing — free, no fees, no commitment. If it pushes again, we've lost nothing and we part friends."], "one": "Every postponement was billed to your equity at $70 a day — and the hurricane that turned twice doesn't care about your track record.", "es": {"say": "Esa fecha de subasta no significa nada. Ya la cancelaron dos veces. Siempre la empujan.", "reb": ["Tiene razón en que las subastas se posponen — usted lo ha vivido dos veces, así que no le voy a decir que las fechas nunca se mueven. Pero déjeme preguntarle: ¿sabe POR QUÉ se pospusieron esas dos? Porque cada cancelación tuvo un motivo — una revisión, una moción — y esos motivos se agotan.", "Y esta es la parte que nadie menciona: cada posposición se la cobraron a usted. El interés después del fallo corre a diario — ponga setenta dólares o más por día sobre un fallo de trescientos mil — más honorarios nuevos de abogado en cada reprogramación, todo saliendo de su equidad. Cuando una por fin se sostiene, el certificado de título puede salir unos diez días después de la subasta, y ahí todas las opciones se van a cero. Es el huracán que se desvió dos veces: el tercero no sabe de su historial. ¿Y si esta es la fecha que se queda y le quedan diez días? Tener el plan listo no le cuesta nada — gratis, sin honorarios, sin compromiso. Si se pospone otra vez, no perdimos nada y quedamos como amigos."], "one": "Cada posposición se la cobraron a su equidad a setenta dólares por día — y el huracán que se desvió dos veces no sabe de su historial."}}, {"n": 4, "t": "The Incoming Check", "say": "“I'm waiting on my tax refund and a settlement check. Once that comes in, I'll catch everything up.”", "reb": ["Money on the way is a real asset — I'm not dismissing it. Let me isolate one thing: if that check clears before the sale date, you're fine. So the only question is what happens if it doesn't. Fair?", "Here's the problem: your check runs on the IRS's clock or an insurance adjuster's clock — no deadline, slips for months, can get offset. The foreclosure runs on a court clock with a judge attached. You're racing a train to the crossing, and only one of you has a schedule. And remember, the payoff grows daily while you wait, so the check has to beat the clock AND cover a bigger number. What if it lands two weeks after the sale? At that point the money arrives and the house is already gone — after the sale it can't buy it back. Let's map the backup for free, no fees ever. If the check wins the race, use it and we part friends."], "one": "Your check runs on the IRS's clock; the foreclosure runs on a judge's clock — only one of those has a schedule.", "es": {"say": "Estoy esperando mi reembolso de impuestos y un cheque de un acuerdo. Cuando entre, pongo todo al día.", "reb": ["Dinero en camino es un activo real — no se lo estoy quitando. Déjeme aislar una sola cosa: si ese cheque entra antes de la fecha de la subasta, usted está bien. Entonces la única pregunta es qué pasa si no entra. ¿Le parece justo?", "El problema es este: su cheque corre en el reloj del IRS o en el reloj de un ajustador de seguros — sin fecha límite, se atrasa meses, y hasta se lo pueden descontar. La ejecución corre en el reloj de la corte, con un juez y una fecha. Solamente uno de esos dos relojes tiene horario. ¿Y si el cheque entra tres semanas después de la subasta? Cinco minutos gratis y tiene un plan B que no le cuesta nada tener guardado. ¿No le parece?"], "one": "Su cheque corre en el reloj del IRS; la ejecución corre en el reloj del juez — solamente uno de los dos tiene horario."}}, {"n": 5, "t": "The Family Money", "say": "“My brother is lending me the money. We're handling it inside the family.”", "reb": ["That's the best kind of help — family money with no strings. One question: is the only thing standing between you and fixing this the money arriving, or has anyone actually pulled the exact reinstatement figure from the bank yet?", "Here's the trap: that number isn't your missed payments. It's payments plus late fees plus the bank's attorney fees plus daily interest — on a $300k judgment that's roughly $70 to $80 a day — and the quote itself takes the bank 5 to 10 business days to issue and then expires. It's like a flight: being at the airport doesn't matter if the doors close before you're in the seat. What if the real number comes back $10k higher than your brother planned for, two weeks before the sale? Let's get the real figure and a backup option on paper — free, no fees ever. If your brother's wire clears in time, we shake hands and part friends."], "one": "The reinstatement number isn't your missed payments — it's payments plus fees plus $70 a day, and the quote takes the bank a week just to issue.", "es": {"say": "Mi hermano me está prestando el dinero. Lo estamos resolviendo en familia.", "reb": ["Esa es la mejor ayuda que hay — dinero de familia y sin condiciones. Una pregunta: ¿lo único que falta entre usted y arreglar esto es que llegue el dinero, o alguien ya pidió la cifra exacta de reinstalación por escrito al banco?", "Aquí está la trampa: esa cifra no son sus pagos atrasados. Son los pagos, más los cargos por mora, más los honorarios del abogado del banco, más el interés diario — sobre un fallo de trescientos mil, unos setenta u ochenta dólares por día. Es como pedir el precio del pasaje una semana antes de volar: el número que usted tiene en la cabeza no es el número de hoy. Y la carta oficial con esa cifra el banco se demora una semana o más en darla. ¿Y si el dinero de su hermano alcanza para el número viejo pero no para el nuevo? Cinco minutos gratis con nuestro asesor principal y usted sabe la cifra real antes de pedirle nada a su familia. ¿Verdad que sí?"], "one": "La cifra de reinstalación no son sus pagos atrasados — son pagos más cargos más setenta dólares diarios, y el banco se demora una semana en darle esa carta."}}, {"n": 6, "t": "The Bankruptcy Pause Button", "say": "“My lawyer is filing bankruptcy. That stops all of this, so there's nothing to talk about.”", "reb": ["Smart move to have that option ready — the automatic stay is real and it does pause the sale. Just so I understand: if the bankruptcy only pauses this instead of ending it, would it be worth five minutes to know what happens on the other side?", "Bankruptcy is a pause button, not an eraser. The lender typically files a motion for relief from stay, and those get heard in 30 to 60 days — then the clock restarts exactly where it stopped, with the arrears still owed. In a Chapter 13 you're paying the full arrears back on top of the regular payment. It's like pausing a movie: when you hit play, you're at the same scene. What if the stay lifts in 45 days — do you want your equity options mapped before or after that? Free consult, no fee ever. If the plan holds, great — we part friends."], "one": "Bankruptcy is a pause button, not an eraser — when the stay lifts, you're at the same scene with the same arrears.", "es": {"say": "Mi abogado va a radicar bancarrota. Eso detiene todo esto, así que no hay nada de qué hablar.", "reb": ["Bien pensado tener esa opción lista — la parada automática es real y sí detiene la subasta. Nada más para entenderlo bien: si la bancarrota solamente pausa esto en vez de terminarlo, ¿valdrían cinco minutos saber qué pasa el día que se levante la pausa?", "La bancarrota es un botón de pausa, no un borrador. El prestamista normalmente radica una moción para levantar la parada, y esas se ven en treinta a sesenta días — y ahí el reloj arranca exactamente donde se quedó, con los mismos atrasos y ahora con honorarios nuevos encima. Es pausar la película: cuando le da play, la escena sigue igualita. ¿Y si le levantan la parada en cuarenta días y usted no tiene nada preparado para ese día? La consulta es gratis y no interfiere con su abogado. ¿No le parece justo?"], "one": "La bancarrota es un botón de pausa, no un borrador — cuando se levanta la parada usted está en la misma escena y con los mismos atrasos."}}, {"n": 7, "t": "The Scam Fatigue Wall", "say": "“I already talked to three of you people. You all want the same thing. Get off my porch.”", "reb": ["Honestly? You should be suspicious — most of the people knocking want to buy your house cheap, today, with a contract in their hand. Let me ask you straight: if I'm not here to buy your house and there's nothing to sign, is there another reason not to talk?", "We start with a free consultation with a senior advisor — 30-plus years doing this — and we lay out three to five options, including ones where you keep the house: refinance, modification through counsel, selling with your equity. No fees, ever, by law and by policy. Three bad mechanics doesn't mean the engine fixes itself. What if the fourth conversation is the one where someone finally shows you the option that protects your equity instead of taking it? Five minutes, nothing to sign. Worst case, we shake hands and part friends — that's the actual deal."], "one": "Three bad mechanics doesn't mean the engine fixes itself — and I'm not here with a contract.", "es": {"say": "Ya hablé con tres de ustedes. Todos quieren lo mismo. Váyase de mi puerta.", "reb": ["¿Honestamente? Usted debería desconfiar — la mayoría de los que tocan quieren comprarle la casa barata, hoy, con un contrato en la mano. Se lo pregunto directo: si yo no vengo a comprarle la casa y no le voy a pedir ni una firma ni un centavo hoy, ¿hay alguna otra razón para no escuchar dos minutos?", "Nosotros empezamos con una consulta gratis con nuestro asesor principal — más de treinta años haciendo esto — y le ponemos sobre la mesa de tres a cinco opciones, incluyendo las que le dejan la casa. Nunca le cobramos un centavo por adelantado, y eso no es un favor, es la ley. Tres mecánicos malos no quieren decir que el motor se arregla solo. ¿Y si en cinco minutos le señalamos algo que ninguno de esos tres vio? Usted no pierde nada — ¿verdad?"], "one": "Tres mecánicos malos no quieren decir que el motor se arregla solo — y yo no vengo con un contrato."}}, {"n": 8, "t": "The Wrong House (Denial)", "say": "“You've got the wrong house. We're not in any foreclosure.”", "reb": ["I hope I do have the wrong house — honestly, that would be the best news of my day. Can I ask you one thing, though, just so I can leave you in peace: if a case had been filed at the courthouse under this address, would you want to be the first to know, or the last?", "Because a filing isn't a verdict on you — it's a clock, and the people who look at it early are the ones who keep choices. It's like a lab result sitting in a drawer: reading it doesn't make you sick, it tells you what's treatable. What if five free minutes with an advisor who's done this for thirty years showed you three or four ways this ends on your terms? If nothing's filed, wonderful — we shake hands and part friends. Is it fair to at least look together?"], "one": "If something had been filed under this address, would you rather be the first to know or the last?", "es": {"say": "Usted tiene la casa equivocada. Aquí no hay ninguna ejecución.", "reb": ["Ojalá tenga la casa equivocada — de verdad, sería la mejor noticia de mi día. ¿Le puedo preguntar una sola cosa, nada más para dejarlo tranquilo? Si se hubiera radicado un caso en la corte con esta dirección, ¿usted querría saberlo, o preferiría que no le dijera nada?", "Porque un caso radicado no es un veredicto sobre usted — es un reloj, y los que lo miran temprano son los que se quedan con opciones. Es como un resultado de laboratorio guardado en una gaveta: leerlo no lo enferma, y no leerlo no lo cura. ¿Y si le doy el número del caso y usted mismo lo verifica en el récord de la corte, sin hablar conmigo nunca más? No le cuesta nada y queda tranquilo de una forma u otra. ¿No le parece?"], "one": "Si se hubiera radicado algo bajo esta dirección, ¿preferiría ser el primero en enterarse o el último?"}}, {"n": 9, "t": "It's Too Late (Hopelessness)", "say": "“There's a sale date already. It's over — there's nothing anybody can do now.”", "reb": ["I hear you — and after months of this, being tired makes all the sense in the world. But let me ask just one thing: is it that nothing can be done, or that you're done? Those are two different things, and only one of them is true.", "A sale date is a deadline, not a verdict — sales get postponed, cases get reworked, and even on the hardest path there's a world of difference between walking out with cash for your keys and time to land, versus being put out with nothing. It's like the two-minute warning in a game: that's not when you leave the stadium, that's when the plays matter most. What if the last ten minutes you spend on this house are the ones that decide how your family walks out of it? You've fought this long — isn't it fair to yourself to hear what's still on the table?"], "one": "A sale date is a deadline, not a verdict — and the last ten minutes are when the plays matter most.", "es": {"say": "Ya hay fecha de subasta. Se acabó, ya no hay nada que nadie pueda hacer.", "reb": ["Lo escucho — y después de meses con esto, estar cansado tiene todo el sentido del mundo. Pero déjeme preguntarle una sola cosa: ¿es que no se puede hacer nada, o es que usted ya no puede más? Porque esas son dos cosas distintas.", "Una fecha de subasta es una fecha límite, no un veredicto — las subastas se posponen, los casos se reestructuran, y hasta en el camino más duro hay una diferencia enorme entre salir con dinero en la mano y salir sin nada. Es el partido en el último minuto: ahí es cuando las jugadas importan más, no menos. ¿Y si en cinco minutos gratis nuestro asesor principal le muestra una jugada que todavía queda? Si no queda ninguna, se lo decimos de frente y quedamos como amigos."], "one": "Una fecha de subasta es una fecha límite, no un veredicto — y los últimos diez minutos son cuando las jugadas más importan."}}, {"n": 10, "t": "She Doesn't Know (Family Secret)", "say": "“My wife doesn't know how bad it is. You have to go before she comes out here.”", "reb": ["I'll step back — and I want you to know I understand why you've carried this alone; you were trying to protect her. One quiet question before I go: would you rather she hear it from you, with a plan in your hand — or from a notice taped to the door?", "Because a secret like this has a deadline, and the court sets it, not you. Telling her with options is like a pilot announcing turbulence along with the route around it — scary for a second, then everyone breathes. Telling her at the end is the crash landing. What if ten free, private minutes — just you and an advisor, before any conversation at home — gave you the words and the plan to bring to her? Isn't it fair that when she finds out, she finds out you were already fighting for her?"], "one": "She's going to find out — the only choice left is whether she hears it from you with a plan, or from a notice on the door.", "es": {"say": "Mi esposa no sabe lo mal que está esto. Tiene que irse antes de que ella salga.", "reb": ["Me hago para atrás — y quiero que sepa que entiendo por qué ha cargado esto solo; usted la estaba protegiendo. Una pregunta tranquila antes de irme: ¿preferiría que ella lo escuche de usted, con un plan en la mano, o de un papel pegado en la puerta?", "Porque un secreto así tiene fecha de vencimiento, y la pone la corte, no usted. Decírselo con opciones es como el piloto que anuncia la turbulencia junto con la ruta para esquivarla: da miedo un segundo y después todo el mundo respira. Decírselo al final es el aterrizaje de emergencia. ¿Y si cinco minutos gratis y privados — usted y el asesor, antes de cualquier conversación en la casa — le dieran las palabras y el plan para llevárselo a ella? ¿No es justo que cuando ella se entere, se entere de que usted ya estaba peleando por ella?"], "one": "Ella se va a enterar — lo único que queda por decidir es si lo escucha de usted con un plan, o de un aviso en la puerta."}}, {"n": 11, "t": "Worn Out — \"Just take it, I'm done\"", "say": "“Honestly I'm fed up. I'm worn out. I don't think I can afford this even if a miracle came down from heaven. I'd be better off walking away from all this stress.”", "reb": ["I hear you, and the majority of people we speak to hit that exact wall — they just want to get away from all the problems, and that's fair. Everybody gets there at some point. Let me ask you one thing: what is it that you TRULY want to do with this property?", "If you've truly had it, then rather than the bank taking the house AND whatever equity you have, I'm sure you'd rather walk out with cash in your pocket, on your own timeline, with dignity — instead of a sheriff putting your things on the street on a date you don't control. We have a program for exactly this — Cash for Keys. We agree on a set figure; you get half the money up front when we sign, and you pick your exit window: 30, 60 or 90 days. The longer you stay the number adjusts a little — not a lot — because our money sits parked. At the end you hand over the keys, the house is clear of your personal things, and you receive the other half that same day. You pick the date. You pick the time. You run the show.", "Let me review the file — what's owed, what it's worth, what condition it's in — and give me fifteen, twenty minutes. I'll come back with a real figure, and if it makes sense to both of us I'll have one of our people at your door and cash moving today."], "one": "Walk out with cash and a date YOU picked — or let the sheriff pick it for you.", "es": {"say": "Sinceramente estoy agotado. No puedo con esto ni aunque bajara un milagro del cielo. Mejor lo dejo todo.", "reb": ["Lo entiendo, y la mayoria de las personas con las que hablamos llegan a ese mismo punto — quieren alejarse de todos los problemas, y es justo. Dejeme preguntarle una sola cosa: que es lo que usted VERDADERAMENTE quiere hacer con esta propiedad?", "Si de verdad ya no puede mas, en vez de que el banco se quede con la casa Y con su plusvalia, mejor salga con dinero en el bolsillo, en su propia fecha, con dignidad — y no con el sheriff poniendo sus cosas en la calle un dia que usted no controla. Tenemos un programa exactamente para esto — Cash for Keys. Acordamos una cifra; le damos la MITAD por adelantado al firmar, y usted escoge su plazo: 30, 60 o 90 dias. Al final entrega las llaves, la casa queda libre de sus cosas personales, y recibe la otra mitad ese mismo dia. Usted escoge la fecha. Usted manda.", "Deme quince o veinte minutos para revisar el expediente — lo que se debe, lo que vale — y regreso con una cifra real. Si nos hace sentido a los dos, hoy mismo movemos el dinero."], "one": "Salga con dinero y una fecha que USTED escogio — o deje que el sheriff la escoja."}}, {"n": 12, "t": "The Overpriced Listing (Realtor gatekeeper)", "say": "The agent has it listed at $429k with ten days to the sale, zero showings — and shields the owner from every call.", "reb": ["I'm not here to point fingers — whether the 429 was your idea or your client's, I honestly don't care. I'm here to stop a foreclosure, make this work for your client, make it work for you, and maybe for one of my investors. But the first thing we all have to do is take a big bite of reality pie on that price with a sale date ten days out.", "Here's the good part for you: if we work together and my investor buys it, you keep the FULL commission on this sale — I'll hand my side to you. And when we resell it after the work, you get the listing AGAIN. The same property pays you twice, and nobody else calling on this file is offering you that. Now — your client needs to hear the truth about the number. If you want backup, I'll do it with you on the line. If you'd rather I call him independently, I will. Or you handle it yourself. Any of the three works, but one of them happens this week."], "one": "Same property, two commissions — but only if the price meets reality this week.", "es": {"say": "El agente lo tiene listado en $429 mil, a diez dias de la subasta, sin visitas — y no deja pasar ninguna llamada al dueno.", "reb": ["No vengo a senalar culpables — si el precio fue idea suya o de su cliente, sinceramente no me importa. Vengo a detener una ejecucion, a que esto funcione para su cliente, para usted, y quizas para uno de mis inversionistas. Pero lo primero es aceptar la realidad de ese precio con una subasta a diez dias.", "Y esto es lo bueno para usted: si trabajamos juntos y mi inversionista compra, usted se queda con la comision COMPLETA de esta venta — yo le cedo mi parte. Y cuando la revendamos despues del trabajo, usted vuelve a tener el listing. La misma propiedad le paga dos veces, y nadie mas en este expediente le esta ofreciendo eso. Ahora — su cliente necesita oir la verdad del numero. Si quiere respaldo, lo hago con usted en la linea; si prefiere, lo llamo yo aparte; o lo maneja usted solo. Cualquiera de las tres funciona, pero una de las tres pasa esta semana."], "one": "La misma propiedad, dos comisiones — si el precio acepta la realidad esta semana."}}, {"n": 13, "t": "The Parachute Frame (universal — any rescue plan they believe in)", "say": "“I've got it handled — the bank's reviewing my modification / my money arrives tomorrow / my cousin is wiring it.” Any plan they're emotionally invested in, 7–10 days out.", "reb": ["I'm applauding you — you did the right thing, and I commend you for getting the ball rolling. Keep doing exactly what you're doing. One question, though: do you have the approval in writing? Because a lot of banks drag the review out and deny two or three days before the sale — and the denial can go straight to their attorneys without you ever seeing it. As much as neither of us wants to look at it, you'd agree that's a real possibility, right?", "So here's what I'm proposing — I don't want to be your bank, and I don't want to be your middleman. I want to be your parachute. You're betting your house on that plan landing on time; let us work on postponing the sale in parallel, so if the money or the approval comes a day late, the extra time is already in motion and your plan still saves the house. If your plan works, beautiful — I did you a favor and you owe me one. And the no-cost version: give me a third-party authorization — just permission to speak to your bank, nothing else, no fees — I'll make one courtesy call and get the bottom line on where your file really is. Based on that, we decide if we work together at all. Fair?"], "one": "You're betting your house on the timing — keep the plan, and let me be the parachute.", "es": {"say": "Ya lo tengo resuelto — el banco esta revisando mi modificacion / el dinero me llega manana / mi primo me lo manda.", "reb": ["Lo felicito — hizo lo correcto, y le aplaudo que haya puesto la bola en movimiento. Siga haciendo exactamente lo que esta haciendo. Una pregunta nada mas: tiene la aprobacion por escrito? Porque muchos bancos alargan la revision y niegan dos o tres dias antes de la subasta — y la negacion puede irse directo a sus abogados sin que usted la vea. Por mucho que no queramos mirarlo, usted estaria de acuerdo en que esa posibilidad existe, verdad?", "Entonces esto es lo que le propongo — yo no quiero ser su banco, ni su intermediario. Quiero ser su paracaidas. Usted esta apostando su casa a que ese plan llegue a tiempo; dejenos trabajar el aplazamiento de la venta en paralelo, para que si el dinero o la aprobacion llega un dia tarde, la subasta ya este detenida y su plan todavia salve la casa. Si su plan funciona, perfecto — le hice un favor y me debe uno. Y la version sin costo: deme una autorizacion de tercero — solo permiso para hablar con su banco, nada mas, sin honorarios — hago una llamada de cortesia y le traigo la verdad de donde esta su expediente. Con eso decidimos si trabajamos juntos o no. Justo?"], "one": "Usted esta apostando su casa al calendario — quedese con su plan, y dejeme ser el paracaidas."}}, {"n": 14, "t": "CLOSE — Save My Number (the read-back test)", "say": "Nothing — this is YOUR move at the end of any call that went well, before you hang up.", "reb": ["Do me one favor before we hang up. I've invested my time, and I'm going to keep investing it after this call — so save my number right now. My name is Jesse. The company is Miami Solutions Group. This is my personal cell, and let me give you the office number too. Now read it all back to me — I want to make sure you got everything right.", "If they read it back clean, they're in. If \"my pen ran out of ink\" or they ask you to repeat it, they're tepid — tighten the follow-up and don't count the deal. Same muscle, bigger size: the paperwork trial close. \"Let me get the paperwork together — I'll email it, we go over it, and if we agree we shake hands. If not, I wasted fifteen minutes of my time and we part friends. Nothing gambled on your part. Sound fair?\" Their reaction to concrete paperwork IS the temperature reading. And when they're warm but need authority: \"Just get on the phone with our senior advisor for five minutes and we'll find a solution for this property.\""], "one": "If they read it back clean they're in — if the pen 'ran out of ink,' they're not.", "es": {"say": "Nada — este es SU movimiento al final de toda llamada que fue bien, antes de colgar.", "reb": ["Hagame un favor antes de colgar. Yo he invertido mi tiempo y voy a seguir invirtiendolo despues de esta llamada — asi que guarde mi numero ahora mismo. Mi nombre es Jesse. La compania es Biscayne Solutions Group. Este es mi celular personal, y le doy tambien el de la oficina. Ahora leamelo todo de vuelta — quiero asegurarme de que lo tiene bien.", "Si lo lee de vuelta completo, esta DENTRO. Si \"se le acabo la tinta\" o le pide que se lo repita, esta tibio — apriete el seguimiento y no cuente el negocio. El mismo musculo en tamano grande: el cierre de papeleria. \"Dejeme preparar los papeles — se los mando por correo, los repasamos, y si estamos de acuerdo nos damos la mano. Si no, perdi quince minutos de mi tiempo y quedamos como amigos. Usted no arriesga nada. Le parece justo?\" Y cuando estan tibios pero necesitan autoridad: \"Pongase al telefono con nuestro asesor principal cinco minutos y le encontramos una solucion a esta propiedad.\""], "one": "Si lo lee de vuelta completo esta dentro — si \"se acabo la tinta\", no lo esta."}}], "rec": "RECORDING IS ON. Before anything else: \"Quick thing before we start, I record my calls so I have your file right. Is that okay with you?\" A no means STOP RECORDING, not hang up."}, ciocIdx=-1, objIdx=-1;
function sheetToggle(){ $('sheet').classList.toggle('open'); }

/* ONE LANGUAGE AT A TIME. Every script block used to render EN and ES stacked — on the call screen
   that stacked the opener twice, the close cue, the drawer peek and (on voicemail) two more blocks:
   his words, "too many transcripts, keep one on the screen." The board's both-languages instinct
   was right for a LIST you scan; on a phone mid-call it is double the reading at the worst moment.
   The chosen language persists (fcLang); the other is ONE tap away on the EN|ES chips. */
function lang(){ try{ return localStorage.getItem('fcLang')==='es' ? 'es' : 'en'; }catch(e){ return 'en'; } }
function setLang(v){
  try{ localStorage.setItem('fcLang', v); }catch(e){}
  // repaint whichever script surfaces are up, without touching flow state.
  // NOT while a voicemail script is visible: that block repaints itself, and a full rebuild here
  // would wipe the script mid-read and re-enable buttons for an outcome already logged.
  var vmUp = $('vm') && $('vm').style.display === 'block';
  if(SCREEN==='outcome' && cur && !vmUp){ screenOutcome(); }
  else if(SCREEN==='lead' && cur){ screenLead(); }
  if(cur) renderSheet(cur);
}
function langChips(){
  var L=lang();
  return '<span class="lchips"><button class="lchip'+(L==='en'?' on':'')+'" data-lang="en">EN</button>'
       + '<button class="lchip'+(L==='es'?' on':'')+'" data-lang="es">ES</button></span>';
}
function wireLang(root){
  Array.prototype.forEach.call((root||document).querySelectorAll('.lchip'), function(b){
    b.onclick=function(ev){ ev.stopPropagation(); setLang(b.dataset.lang); };
  });
}
function say(en, es, r){
  if(lang()==='es' && es) return '<div class="say es">'+esc(fillScript(es, r))+'</div>';
  return '<div class="say">'+esc(fillScript(en, r))+'</div>'
       + (lang()==='es' && !es ? '<div class="noes">No Spanish version of this line yet.</div>' : '');
}

function renderSheet(r){
  // Named vs anonymous opener — see firstName(). No usable name means ask for the owner instead of
  // greeting a company, a placeholder, or a name the card just told him not to use.
  var named = !!firstName(r);
  var opEN = named ? SCRIPT.op.en : SCRIPT.op.aen;
  var opES = named ? SCRIPT.op.es : SCRIPT.op.aes;
  // PEEK — the first thing out of his mouth, plus the close cue, always one glance away.
  var op = fillScript(opEN, r);
  $('peek').innerHTML = '<b>'+esc(op.split('.')[0])+'.</b> '
    + '<span class="mut">&hellip; tap for the full script</span>'
    + '<div class="mut" style="margin-top:4px">Close with: <b>That&rsquo;s fair, right?</b></div>';

  var b = '<div class="ltag">THE OPENER '+langChips()+'</div>'
        + say(opEN, opES, r)
        + (named ? '' : '<div class="noes">No usable first name on this lead &mdash; ask for the owner rather than guessing at one.</div>');

  // CIOC as nav — tap a beat, get its words.
  b += '<div class="cioc">';
  SCRIPT.cioc.forEach(function(c,ix){ b += '<button data-cioc="'+ix+'"'+(ciocIdx===ix?' class="on"':'')+'>'+esc(c.k)+'</button>'; });
  b += '</div>';
  if(ciocIdx>=0){
    var c=SCRIPT.cioc[ciocIdx];
    b += '<div class="mut" style="font-size:12px">'+esc(c.w)+'</div><div class="say">'+esc(c.s)+'</div>';
  }

  b += '<div class="ltag">IF YOU ONLY GET 15 SECONDS</div><div class="say">'+esc(fillScript(SCRIPT.f15, r))+'</div>';

  // OBJECTIONS — tap what you are hearing.
  b += '<div class="ltag">THEY PUSHED BACK &mdash; tap what you heard</div>';
  if(!SCRIPT.obj.length){
    b += '<div class="noes">Objection cards unavailable in this build (drill pack not readable at build time).</div>';
  } else {
    b += '<div class="objs">';
    SCRIPT.obj.forEach(function(o,ix){ b += '<button data-obj="'+ix+'"'+(objIdx===ix?' class="on"':'')+'>'+esc(o.t)+'</button>'; });
    b += '</div>';
    if(objIdx>=0){
      var o=SCRIPT.obj[objIdx];
      b += '<div class="mut" style="font-size:12px;margin-top:6px">They say: &ldquo;'+esc(o.say)+'&rdquo;</div>';
      b += '<div class="ltag">EN</div>';
      o.reb.forEach(function(p){ b += '<div class="say">'+esc(p)+'</div>'; });
      if(o.one) b += '<div class="ltag">IF YOU ONLY GET ONE SENTENCE</div><div class="say">'+esc(o.one)+'</div>';
      if(o.es){
        b += '<div class="mut" style="font-size:12px;margin-top:12px">Dicen: &ldquo;'+esc(o.es.say)+'&rdquo;</div>';
        b += '<div class="ltag es">ES</div>';
        o.es.reb.forEach(function(p){ b += '<div class="say es">'+esc(p)+'</div>'; });
        if(o.es.one) b += '<div class="ltag es">SI SOLO LE DA TIEMPO A UNA FRASE</div><div class="say es">'+esc(o.es.one)+'</div>';
      } else {
        // never a blank card — say plainly that the Spanish does not exist for this one
        b += '<div class="noes">No Spanish version of this card yet.</div>';
      }
    }
  }

  b += '<div class="ltag">MARS &mdash; say this at the TOP of any advisor consult</div>'
     + '<div class="say">'+esc(SCRIPT.mars)+'</div>';

  b += '<div class="never"><b>NEVER SAY</b><br>';
  SCRIPT.never.forEach(function(n){ b += '&bull; '+esc(n)+'<br>'; });
  b += '</div>';

  $('sbody').innerHTML = b;
  // delegated so the handlers survive every re-render of the body
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-cioc]'), function(el){
    el.onclick=function(){ ciocIdx = (ciocIdx===+el.dataset.cioc) ? -1 : +el.dataset.cioc; renderSheet(cur); };
  });
  Array.prototype.forEach.call($('sbody').querySelectorAll('[data-obj]'), function(el){
    el.onclick=function(){ objIdx = (objIdx===+el.dataset.obj) ? -1 : +el.dataset.obj; renderSheet(cur); };
  });
  wireLang($('sbody'));
}
$('grip').onclick=sheetToggle;
$('peek').onclick=sheetToggle;

$('pill').onclick=function(){
  /* The pill sits above the sheet grip and used to reload INSTANTLY — mid-call, unconfirmed.
     Off the lead screen (call in progress), reloading needs a deliberate yes. */
  if(SCREEN!=='lead' && !confirm('Load the newer list now? Your logs are saved, but the screen resets to the top of the queue.')) return;
  location.reload();
};
/* Returning to the page means he just finished a call. Pull then (teammate opt-outs matter before
   the next dial) and re-check freshness. Do NOT poll on a 45s timer here — 200k-iteration PBKDF2
   every 45s on a backgrounded phone is pure battery burn for a page used in bursts. */
document.addEventListener('visibilitychange',function(){
  if(document.hidden) return;
  freshCheck();
  /* Pull first (teammate opt-outs before the next dial), then PUSH: the push fired just before
     the dialer opened may have died when the tab backgrounded — returning is the retry moment. */
  try{ if(localStorage.getItem('fcTeamKey')){ syncPull().then(function(){ loadNotes(); if(touched) return syncPush(); }).catch(function(){}); } }catch(e){}
});
boot();
