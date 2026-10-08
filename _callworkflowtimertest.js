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
// work(m, ms): advance in 1-minute steps with an interaction each step, like a caller actually working
function work(m, ms) { for (let t = 0; t < ms; t += MIN) { m.c.adv(Math.min(MIN, ms - t)); m.t.interaction(); } }
const CTX = { callerId: 'C1', caseId: 'X1' };
function store(evs) { return evs.reduce((s, e) => CW.add(s, e), CW.newStore()); }
const WIN = { from: W0 - 3600000, to: W0 + 86400000 };
// ---- review regressions (idle without tick, caller switch, id collisions, recover) ----
function mk2(dev, extra) { const m = mk(Object.assign({ deviceId: dev, newId: undefined }, extra || {})); return m; }
{ const m = mk2('devA'); m.t.start({ callerId: 'C1', caseId: 'X' }); m.c.adv(3 * MIN); m.t.interaction(); m.c.adv(3 * 3600000);
  const r = m.t.pause();
  rec('pause 3 h after the last interaction with NO tick credits only 3 min', CW.activeSeconds(store(m.evs), 'C1', { from: W0 - 1e9, to: W0 + 1e9 }) === 180, r); }
{ const m = mk2('devA'); m.t.start({ callerId: 'C1', caseId: 'X' }); m.c.adv(3 * MIN); m.t.interaction(); m.c.adv(3 * 3600000); m.t.interaction(); m.t.pause();
  rec('interaction after an idle gap does not resurrect the stretch', CW.activeSeconds(store(m.evs), 'C1', { from: W0 - 1e9, to: W0 + 1e9 }) === 180); }
{ const m = mk2('devA'); m.t.start({ callerId: 'C1', caseId: 'X' }); m.c.adv(MIN); m.t.interaction();
  const r = m.t.start({ callerId: 'C2', caseId: 'Y' });
  rec('start with another caller while running stops the first and starts the second', m.t.state().callerId === 'C2' && m.t.state().status === 'running' && r.ok);
  m.c.adv(2 * MIN); m.t.pause();
  const st2 = store(m.evs);
  rec('time is credited to each caller separately (C1 60 s, C2 120 s)', CW.activeSeconds(st2, 'C1', { from: W0 - 1e9, to: W0 + 1e9 }) === 60 && CW.activeSeconds(st2, 'C2', { from: W0 - 1e9, to: W0 + 1e9 }) === 120);
  rec('first caller session was closed with session_stopped', m.evs.some(e => e.event_type === 'session_stopped' && e.caller_id === 'C1')); }
{ const m = mk2('devA'); m.t.start({ callerId: 'C1', caseId: 'X' }); m.c.adv(MIN); m.t.pause(); m.t.start({ callerId: 'C2', caseId: 'Y' });
  rec('start with another caller while paused closes the paused session', m.evs.some(e => e.event_type === 'session_stopped' && e.caller_id === 'C1')); }
{ const a = mk2('devA'), b = mk2('devB'); a.t.start(CTX); b.t.start(CTX); work(a, 10 * MIN); work(b, 10 * MIN); a.t.pause(); b.t.pause();
  const u = CW.union(store(a.evs), store(b.evs));
  rec('two devices with default ids: no conflicts, 600 s counted once', Object.keys(u.conflicts).length === 0 && CW.activeSeconds(u, 'C1', WIN) === 600, Object.keys(u.conflicts));
  const a2 = mk2('devA'), a3 = mk2('devA'); a2.t.start(CTX); a3.t.start(CTX);
  rec('two page loads on one device get different session ids', a2.evs[0].payload.session_id !== a3.evs[0].payload.session_id); }
{ const m = mk2('devA'); m.t.start(CTX); m.c.adv(20000); m.t.tick(); m.c.adv(30000); m.t.tick(); const saved = m.saved();
  const m2 = mk2('devA'); const r = m2.t.recover(saved);
  rec('crash after ~50 s of heartbeats reports about 50 s unknown, strictly > 0', r.unknownSeconds >= 20 && r.unknownSeconds <= 50, r);
  rec('recover closes the dead session without crediting time', m2.evs.length === 1 && m2.evs[0].event_type === 'session_stopped' && CW.activeSeconds(store(m2.evs), 'C1', WIN) === 0 && CW.validate(m2.evs[0]).ok);
  const live = mk2('devA'); live.t.start(CTX); const rr = live.t.recover(saved);
  rec('recover on a live timer is ignored and does not kill it', rr.ignored && live.t.state().status === 'running'); }
{ const m = mk2('devA'); m.t.start(CTX); m.c.adv(MIN); m.t.launchDialer(); m.c.adv(5 * MIN); m.t.stop('logout');
  const r = m.t.returnFromDialer({ kind: 'confirm', seconds: 120 });
  rec('dialer return after logout credits nothing', r.credited === 0);
  const m2 = mk2('devA'); m2.t.start({ callerId: 'C1', caseId: 'X' }); m2.t.launchDialer(); m2.c.adv(5 * MIN);
  m2.t.returnFromDialer({ kind: 'confirm', seconds: 120 });
  rec('dialer interval is credited to the caller who launched it', m2.evs.filter(e => e.payload.self_reported).every(e => e.caller_id === 'C1')); }
{ let writes = 0; const m = mk({ persist: () => { writes++; } }); m.t.start(CTX); const base = writes; for (let i = 0; i < 10; i++) { m.c.adv(5000); m.t.interaction(); m.t.tick(); }
  rec('heartbeat persists at least once and at most every 15 s over 50 s (1..4 writes)', writes - base >= 1 && writes - base <= 4, writes - base); }
// start needs caller and case
{ const { t } = mk();
  rec('start refuses with no caller', t.start({ caseId: 'X' }).ok === false);
  rec('start refuses with no case', t.start({ callerId: 'C1' }).ok === false); }
// manual pause confirms up to the action; events validate
{ const { c, evs, t } = mk(); t.start(CTX); c.adv(4 * MIN); t.interaction(); c.adv(4 * MIN); t.interaction(); c.adv(1 * MIN); const r = t.pause();
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
  rec('crash recovery never confirms time', !('confirmedSeconds' in r) && CW.activeSeconds(store(m2.evs), 'C1', WIN) === 0, r);
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
  const a = mk(), b = mk(); a.t.start(CTX); b.t.start(CTX); work(a, 10 * MIN); work(b, 10 * MIN); a.t.pause(); b.t.pause();
  const u = CW.union(store(a.evs), store(b.evs));
  rec('same caller on two devices over the same minutes counts once (600 s)', CW.activeSeconds(u, 'C1', WIN) === 600, CW.activeSeconds(u, 'C1', WIN)); }
{ const { t } = mk(); t.start(CTX);
  rec('lease warns when another device is timing the same caller',
      t.leaseWarning({ deviceId: 'devB', callerId: 'C1', ts: 1000 }, 2000) !== '' && t.leaseWarning({ deviceId: 'devA', callerId: 'C1', ts: 1000 }, 2000) === ''); }
console.log(fails + ' failure(s)'); process.exit(fails ? 1 : 0);
