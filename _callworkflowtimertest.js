/* _callworkflowtimertest.js -- timer controller against fake clocks. Run: node _callworkflowtimertest.js */
const T = require('./call_workflow_timer.js'), CW = require('./call_workflow.js');
let fails = 0;
function rec(n, ok, x) { console.log((ok ? 'ok   ' : 'FAIL ') + n + (ok ? '' : ' | ' + JSON.stringify(x))); if (!ok) fails++; }
const W0 = Date.parse('2026-10-08T13:00:00Z'), MIN = 60000;
function mk(extra) {
  const c = { wall: W0, mono: 5000 }; const evs = []; let saved = null, n = 0;
  const t = T.create(Object.assign({ deviceId: 'devA', tz: 'America/New_York', now: () => c.wall, mono: () => c.mono,
    emit: e => evs.push(e), persist: s => { saved = s; }, newId: () => 'S' + (++n) }, extra || {}));
  c.adv = ms => { c.wall += ms; c.mono += ms; };
  return { c, evs, t, saved: () => saved };
}
const CTX = { callerId: 'C1', caseId: 'X1' };
function store(evs) { return evs.reduce((s, e) => CW.add(s, e), CW.newStore()); }
const WIN = { from: W0 - 3600000, to: W0 + 86400000 };
// start needs caller and case
{ const { t } = mk();
  rec('start refuses with no caller', t.start({ caseId: 'X' }).ok === false);
  rec('start refuses with no case', t.start({ callerId: 'C1' }).ok === false); }
// manual pause confirms up to the action; events validate
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(7 * MIN); t.interaction(); c.adv(2 * MIN); const r = t.pause();
  rec('pause confirms 9 minutes', r.confirmedSeconds === 540, r);
  rec('all emitted events validate', evs.every(e => CW.validate(e).ok), evs.map(e => CW.validate(e).errors));
  rec('metrics sees 540 s', CW.activeSeconds(store(evs), 'C1', WIN) === 540); }
// idle settles to last interaction
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(3 * MIN); t.interaction(); c.adv(4 * MIN); let r = t.tick();
  rec('4 min after interaction is not idle yet', r.idle === false);
  c.adv(2 * MIN); r = t.tick();
  rec('idle pauses at the last interaction (3 min), later time discarded', r.idle === true && r.confirmedSeconds === 180, r);
  rec('state is paused', t.state().status === 'paused'); }
// background pauses at visibility loss; resume is a new segment, no catch-up
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(2 * MIN); t.hidden(); c.adv(60 * MIN); t.start(CTX); c.adv(1 * MIN); t.pause();
  const s = CW.activeSeconds(store(evs), 'C1', WIN);
  rec('background hour not credited: 3 min total', s === 180, s);
  rec('resume made a second interval id', new Set(evs.filter(e => e.event_type === 'active_interval_confirmed').map(e => e.event_id)).size === 2); }
// crash: provisional is unknown, never confirmed
{ const m = mk(); m.t.start(CTX); m.c.adv(20000); m.t.tick(); m.c.adv(10 * MIN - 20000);
  const saved = m.saved(); const m2 = mk(); const r = m2.t.recover(saved);
  rec('crash recovery returns unknown seconds, emits nothing', m2.evs.length === 0 && r.unknownSeconds >= 0 && !('confirmedSeconds' in r), r);
  rec('nothing from the crashed run is confirmed', CW.activeSeconds(store(m.evs), 'C1', WIN) === 0); }
// heartbeat throttle
{ const { c, t, saved } = mk(); let writes = 0; const m = mk({ persist: () => { writes++; } });
  m.t.start(CTX); const base = writes; for (let i = 0; i < 10; i++) { m.c.adv(5000); m.t.interaction(); m.t.tick(); }
  rec('heartbeat persists at most every 15 s (50 s -> <=4 writes)', writes - base <= 4, writes - base); }
// dialer
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(MIN); t.launchDialer(); c.adv(10 * MIN);
  let r = t.returnFromDialer({ kind: 'cancelled' });
  rec('cancelled dialer launch credits nothing', r.credited === 0);
  const m = mk(); m.t.start(CTX); m.c.adv(MIN); m.t.launchDialer(); m.c.adv(10 * MIN);
  r = m.t.returnFromDialer({ kind: 'correct', seconds: 4 * 60 });
  rec('corrected call credits 240 s labelled self-reported', r.credited === 240 && r.label === 'self-reported', r);
  const iv = m.evs.filter(e => e.payload.reason === 'dialer')[0];
  rec('dialer interval is flagged self_reported', iv && iv.payload.self_reported === true);
  const m3 = mk(); m3.t.start(CTX); m3.t.launchDialer(); m3.c.adv(MIN);
  rec('claim longer than time away is clamped to the time away', m3.t.returnFromDialer({ kind: 'confirm', seconds: 3600 }).credited === 60);
  rec('unknown credits nothing', mk().t.returnFromDialer({ kind: 'unknown' }).credited === 0); }
// caller switch ends the session; two devices union once
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(5 * MIN); t.switchCaller();
  rec('caller switch stops the session', t.state().status === 'stopped' && evs[evs.length - 1].event_type === 'session_stopped');
  const a = mk(), b = mk(); a.t.start(CTX); b.t.start(CTX); a.c.adv(10 * MIN); b.c.adv(10 * MIN); a.t.pause(); b.t.pause();
  const u = CW.union(store(a.evs), store(b.evs));
  rec('same caller on two devices over the same minutes counts once (600 s)', CW.activeSeconds(u, 'C1', WIN) === 600, CW.activeSeconds(u, 'C1', WIN)); }
{ const { t } = mk(); t.start(CTX);
  rec('lease warns when another device is timing the same caller',
      t.leaseWarning({ deviceId: 'devB', callerId: 'C1', ts: 1000 }, 2000) !== '' && t.leaseWarning({ deviceId: 'devA', callerId: 'C1', ts: 1000 }, 2000) === ''); }
console.log(fails + ' failure(s)'); process.exit(fails ? 1 : 0);
