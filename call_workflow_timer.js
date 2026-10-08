/* call_workflow_timer.js -- the Start work / Pause / End work controller (call workflow spec section 7).
 *
 * PURE and INERT: clocks, persistence and event sink are injected, nothing here touches the page,
 * the bridge, a ledger or a suppression file. It only EMITS call_workflow.js events
 * (session_started, active_interval_confirmed, session_paused, session_stopped).
 *
 *   - Elapsed time is measured on a MONOTONIC clock; wall time is only the label on the interval.
 *   - A heartbeat persists the provisional range at most every 15 s. A provisional range is NEVER
 *     confirmed time: after a crash, close, eviction or caller switch it is reported as unknown
 *     seconds and credited to nobody. Resume is always a new segment.
 *   - Idle (default 5 min with no interaction) pauses AT THE LAST INTERACTION; later time is discarded.
 *   - Backgrounding pauses at visibility loss. A manual dialer launch is not credited by itself; on
 *     return the caller confirms, corrects or marks unknown, and the interval is labelled self-reported.
 *   - Start needs a selected caller and a current case. Switching caller ends the session first.
 */
(function (root) {
  'use strict';
  var T = {};
  var SCHEMA = 1;
  T.create = function (o) {
    var idleMs = o.idleMs || 5 * 60000, hbMs = o.heartbeatMs || 15000;
    var st = { status: 'stopped', callerId: null, session: null, seg: 0, wall0: 0, mono0: 0, last: 0, hbAt: null, hbMono: 0, caseId: null,
               away: null };
    var seqN = 0;
    function iso(t) { return new Date(t).toISOString(); }
    function emit(type, payload, at, callerId) {
      o.emit({ schema_version: SCHEMA, event_id: payload._id, event_type: type, occurred_at_utc: iso(at), recorded_at_utc: iso(o.now()),
               device_id: o.deviceId, caller_id: callerId === undefined ? st.callerId : callerId, owner_id: null, case_refs: st.caseId ? [st.caseId] : [],
               payload: (function () { var p = {}; Object.keys(payload).forEach(function (k) { if (k !== '_id') p[k] = payload[k]; }); p.tz = o.tz; return p; })() });
    }
    function persist() {
      if (!o.persist) return;
      /* provisional only: a recoverable HINT of unsettled time, never an interval */
      o.persist({ status: st.status, callerId: st.callerId, session: st.session, seg: st.seg, wall0: st.wall0,
                  provisionalEndWall: st.status === 'running' ? st.wall0 + (st.hbMono - st.mono0) : null, ts: o.now() });
    }
    /* settle [segment start, segment start + elapsedMono] as an immutable confirmed interval */
    function settle(endMono, reason, selfReported) {
      var dur = Math.max(0, endMono - st.mono0);
      var out = { confirmedSeconds: 0 };
      if (dur > 0) {
        var s = st.wall0, e = st.wall0 + dur;
        emit('active_interval_confirmed', { _id: 'ai:' + st.session + ':' + st.seg, session_id: st.session, start_utc: iso(s), end_utc: iso(e), reason: reason }, e);
        out.confirmedSeconds = dur / 1000;
      }
      return out;
    }
    function endSegment(endMono, reason, stopped) {
      var r = settle(endMono, reason);
      emit(stopped ? 'session_stopped' : 'session_paused', { _id: (stopped ? 'ss:' : 'sp:') + st.session + ':' + st.seg, session_id: st.session, reason: reason }, st.wall0 + Math.max(0, endMono - st.mono0));
      st.status = stopped ? 'stopped' : 'paused';
      st.hbAt = null; persist();
      return r;
    }
    /* Idle is enforced on EVERY action, not only on tick(): timers get throttled and machines sleep, so a
       pause or interaction long after the last real one must still settle at that last interaction. */
    function idleGuard() {
      if (st.status === 'running' && o.mono() - st.last >= idleMs) { endSegment(st.last, 'idle', false); return true; }
      return false;
    }
    var api = {};
    api.state = function () { return { status: st.status, session: st.session, callerId: st.callerId, seg: st.seg }; };
    api.start = function (ctx) {
      if (!ctx || !ctx.callerId) return { ok: false, error: 'select a caller first' };
      if (!ctx.caseId) return { ok: false, error: 'open a lead first' };
      idleGuard();
      if (st.status === 'running' && st.callerId === ctx.callerId) { st.caseId = ctx.caseId; return { ok: true, already: true }; }
      /* a different caller ends the previous caller's session (running or paused) before new work begins */
      if (st.status !== 'stopped' && st.callerId !== ctx.callerId) api.stop('caller_switch');
      var fresh = st.status === 'stopped';
      st.callerId = ctx.callerId; st.caseId = ctx.caseId;
      /* ids are unique per device AND per run, so two devices or two page loads can never collide on an id with different payloads */
      if (fresh) { st.session = o.newId ? o.newId() : (o.deviceId + '-' + o.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8)); st.seg = 0; }
      st.seg++; st.wall0 = o.now(); st.mono0 = o.mono(); st.last = st.mono0; st.hbMono = st.mono0; st.status = 'running';
      if (fresh) emit('session_started', { _id: 'st:' + st.session, session_id: st.session }, st.wall0);
      persist();
      return { ok: true, session_id: st.session };
    };
    api.interaction = function () { if (idleGuard()) return; if (st.status === 'running') st.last = o.mono(); };
    api.tick = function () {
      if (st.status !== 'running') return { idle: false };
      var m = o.mono();
      if (m - st.last >= idleMs) { var r = endSegment(st.last, 'idle', false); r.idle = true; return r; }
      if (st.hbAt === null || m - st.hbAt >= hbMs) { st.hbAt = m; st.hbMono = m; persist(); }
      return { idle: false };
    };
    api.pause = function (reason) { if (idleGuard() || st.status !== 'running') return { confirmedSeconds: 0 }; return endSegment(o.mono(), reason || 'manual', false); };
    api.stop = function (reason) {
      idleGuard();
      st.away = null;
      if (st.status === 'running') return endSegment(o.mono(), reason || 'manual', true);
      if (st.status === 'paused') { emit('session_stopped', { _id: 'ss:' + st.session + ':' + st.seg + ':p', session_id: st.session, reason: reason || 'manual' }, o.now()); st.status = 'stopped'; persist(); }
      return { confirmedSeconds: 0 };
    };
    /* visibility / screen / queue gates: all pause at the moment they happen */
    api.hidden = function () { return api.pause('background'); };
    api.setActiveScreen = function (on) { return on ? { ok: true } : api.pause('inactive_screen'); };
    api.switchCaller = function () { return api.stop('caller_switch'); };
    api.logout = function () { return api.stop('logout'); };
    /* dialer: the stretch away is NOT credited. On return the caller says what happened. */
    api.launchDialer = function () { idleGuard(); if (st.status === 'running') { st.away = { wall: o.now(), mono: o.mono(), session: st.session, seg: st.seg, callerId: st.callerId, caseId: st.caseId }; api.pause('dialer_launch'); } };
    api.returnFromDialer = function (a) {
      var aw = st.away; st.away = null;
      if (!aw || !a || a.kind === 'cancelled' || a.kind === 'unknown') return { credited: 0, label: a && a.kind === 'cancelled' ? 'cancelled' : 'unknown' };
      var awayMs = o.mono() - aw.mono, sec = Math.max(0, Math.min(+a.seconds || 0, awayMs / 1000));
      if (sec <= 0) return { credited: 0, label: 'unknown' };
      emit('active_interval_confirmed', { _id: 'ai:' + aw.session + ':d' + aw.seg + ':' + aw.wall, session_id: aw.session, start_utc: iso(aw.wall), end_utc: iso(aw.wall + sec * 1000), reason: 'dialer', self_reported: true }, aw.wall + sec * 1000, aw.callerId);
      return { credited: sec, label: 'self-reported' };
    };
    /* After a crash / close / eviction: provisional time becomes UNKNOWN seconds, never an interval. */
    api.recover = function (saved) {
      if (st.status === 'running') return { unknownSeconds: 0, ignored: 'a timer is running on this page' };
      if (!saved || saved.status !== 'running' || saved.provisionalEndWall == null) return { unknownSeconds: 0 };
      var u = Math.max(0, (saved.provisionalEndWall - saved.wall0) / 1000);
      /* close the dead session so it is not open forever; this credits NO time (the stop event carries none) */
      emit('session_stopped', { _id: 'ss:' + saved.session + ':recovered', session_id: saved.session, reason: 'recovered', unknown_seconds: u }, saved.provisionalEndWall, saved.callerId);
      st.status = 'stopped'; st.away = null;
      return { unknownSeconds: u, session: saved.session };
    };
    /* A lease only discourages two timers. It is advisory, not a distributed lock. */
    api.leaseWarning = function (other, nowMs) {
      return other && other.deviceId !== o.deviceId && other.callerId === st.callerId && nowMs - other.ts < 2 * hbMs ? 'another device is timing this caller' : '';
    };
    return api;
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = T; else root.CWTimer = T;
})(typeof window !== 'undefined' ? window : this);
