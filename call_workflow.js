/* call_workflow.js -- pure event contract, reducers and metrics for the Call Workflow spec
 * (sections 4, 5 and 7). 2026-10-08.
 *
 * PURE: no DOM, no storage, no network, no clock (every time is passed in), no ledger writer, no
 * safety verdict. It cannot send, text or email, and it cannot clear a hold. It is NOT yet wired into
 * the page; call_mode.py embeds it in a later change. Works under node (module.exports) and as an
 * inline <script> (window.CW). When call_mode.py embeds it, READ THE FILE AS TEXT and substitute it into
 * the page; never paste it into a non-raw Python string literal, which would turn the \n and \d
 * escapes below into real characters and break the script.
 *
 * THE RULES THIS ENFORCES
 *  - Events are immutable and keyed by event_id. Union is commutative, associative and idempotent.
 *    The same id with a different payload is a CONFLICT: both versions are quarantined, the id counts
 *    nowhere, and the affected metric is flagged partial. No clock decides a winner.
 *  - A child whose parent has not arrived is PENDING and counts nowhere until the parent does.
 *  - An unknown schema version is kept losslessly and flagged unsupported; it is never dropped and
 *    never read as "no activity".
 *  - Verified metrics come only from the typed events below. Legacy Talked, APPOINTMENT SET, tel
 *    clicks and reminders never reach this module.
 *  - A zero is only a zero when coverage is complete. Otherwise the result says partial or unknown.
 */
(function (root) {
  'use strict';
  var CW = {};
  CW.SCHEMA = 1;
  CW.TYPES = ['launch_recorded', 'attempt_confirmed', 'launch_cancelled', 'conversation_confirmed',
    'callback_requested', 'callback_rescheduled', 'callback_cancelled', 'callback_completed',
    'appointment_booked', 'appointment_qualified', 'appointment_cancelled', 'session_started',
    'active_interval_confirmed', 'session_paused', 'session_stopped', 'event_retracted', 'event_corrected'];

  /* Prototype-free maps: an event_id or launch_id of "constructor" must be data, not a hit on Object. */
  function dict() { return Object.create(null); }
  function isStr(v) { return typeof v === 'string' && v.length > 0; }
  function ms(iso) { var t = Date.parse(iso); return isFinite(t) ? t : NaN; }

  /* Canonical JSON: sorted keys, no spaces. The Python twin (call_workflow_events.canon) must produce
     the same bytes; _callworkflowtest.py checks it on shared fixtures. */
  function canon(v) {
    if (v === null || typeof v !== 'object') return JSON.stringify(v);
    if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
    return '{' + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ':' + canon(v[k]); }).join(',') + '}';
  }
  CW.canon = canon;

  /* ---------------------------------------------------------------- validation */
  var NEED = {
    launch_recorded: ['launch_id', 'attempt_id'],
    attempt_confirmed: ['launch_id', 'attempt_id'],
    launch_cancelled: ['launch_id'],
    conversation_confirmed: ['conversation_id', 'attempt_id'],
    callback_requested: ['request_id', 'evidence_ref', 'tz', 'local_time', 'due_utc'],
    callback_rescheduled: ['request_id', 'due_utc'],
    callback_cancelled: ['request_id'],
    callback_completed: ['request_id', 'conversation_id'],
    appointment_booked: ['appointment_id', 'conversation_id', 'attempt_id', 'agreed_utc', 'purpose'],
    appointment_qualified: ['appointment_id', 'policy_id'],
    appointment_cancelled: ['appointment_id'],
    session_started: ['session_id'], session_paused: ['session_id'], session_stopped: ['session_id'],
    active_interval_confirmed: ['session_id', 'start_utc', 'end_utc'],
    event_retracted: ['target_event_id'],
    event_corrected: ['target_event_id', 'expected_revision']
  };

  /* -> {ok, unsupported, errors[]}. unsupported=true means "keep it, do not count it". */
  CW.validate = function (ev) {
    var errs = [];
    if (!ev || typeof ev !== 'object') return { ok: false, unsupported: false, errors: ['not an object'] };
    if (!isStr(ev.event_id)) errs.push('event_id');
    if (ev.schema_version !== CW.SCHEMA) {
      return { ok: false, unsupported: isStr(ev.event_id) && typeof ev.schema_version === 'number', errors: ['schema_version ' + ev.schema_version + ' unsupported'] };
    }
    if (CW.TYPES.indexOf(ev.event_type) < 0) errs.push('event_type');
    if (!isFinite(ms(ev.occurred_at_utc))) errs.push('occurred_at_utc');
    if (!isFinite(ms(ev.recorded_at_utc))) errs.push('recorded_at_utc');
    if (!isStr(ev.device_id)) errs.push('device_id');
    var p = ev.payload;
    if (!p || typeof p !== 'object') { errs.push('payload'); return { ok: false, unsupported: false, errors: errs }; }
    (NEED[ev.event_type] || []).forEach(function (k) { if (p[k] === undefined || p[k] === null || p[k] === '') errs.push('payload.' + k); });
    var t = ev.event_type;
    if (t === 'attempt_confirmed') {
      if (p.attempted !== 'yes') errs.push('attempted must be yes');
      if (p.eligibility !== 'pass') errs.push('eligibility must be pass');
    }
    if (t === 'conversation_confirmed') {
      if (p.role !== 'owner' && p.role !== 'authorized_decision_maker') errs.push('role');
      if (p.role_verification !== 'caller_attested') errs.push('role_verification');
      if (p.two_way !== true) errs.push('two_way');
    }
    if (t === 'callback_requested') {
      if (p.requested_by_owner !== true) errs.push('requested_by_owner');
      if (!isFinite(ms(p.due_utc))) errs.push('due_utc');
    }
    if (t === 'appointment_booked' && p.booking_state !== 'confirmed') errs.push('booking_state must be confirmed');
    if (t === 'appointment_qualified' && (!p.checklist || typeof p.checklist !== 'object')) errs.push('checklist');
    if (t === 'active_interval_confirmed') {
      if (!(ms(p.end_utc) > ms(p.start_utc))) errs.push('interval end must follow start');
      if (!isStr(ev.caller_id)) errs.push('caller_id');
    }
    if ((t === 'attempt_confirmed' || t === 'callback_completed' || t === 'conversation_confirmed' ||
         t === 'appointment_booked' || t === 'appointment_qualified') && !isStr(ev.caller_id)) errs.push('caller_id');
    return { ok: errs.length === 0, unsupported: false, errors: errs };
  };

  /* ---------------------------------------------------------------- store + union */
  CW.newStore = function () { return { byId: dict(), conflicts: dict(), unsupported: dict(), invalid: dict() }; };
  /* add(): idempotent. Same id + same payload = no-op. Same id + different payload = conflict. */
  function addUnique(map, id, ev) {
    var l = map[id] || (map[id] = []), c = canon(ev);
    for (var i = 0; i < l.length; i++) if (canon(l[i]) === c) return;
    l.push(ev);
  }
  CW.add = function (store, ev) {
    var v = CW.validate(ev);
    if (!v.ok) {
      if (v.unsupported) addUnique(store.unsupported, ev.event_id, ev);       // lossless, every version
      else store.invalid[(ev && ev.event_id) || ('anon:' + canon(ev))] = { ev: ev, errors: v.errors };
      return store;
    }
    var have = store.byId[ev.event_id], c = canon(ev);
    if (!have) {
      if (store.conflicts[ev.event_id]) addUnique(store.conflicts, ev.event_id, ev); else store.byId[ev.event_id] = ev;
      return store;
    }
    if (canon(have) === c) return store;
    delete store.byId[ev.event_id];
    store.conflicts[ev.event_id] = [have, ev];
    return store;
  };
  CW.union = function (a, b) {
    var out = CW.newStore();
    [a, b].forEach(function (s) {
      Object.keys(s.byId).forEach(function (k) { CW.add(out, s.byId[k]); });
      Object.keys(s.conflicts).forEach(function (k) { s.conflicts[k].forEach(function (e) { CW.add(out, e); }); });
      Object.keys(s.unsupported).forEach(function (k) { s.unsupported[k].forEach(function (e) { addUnique(out.unsupported, k, e); }); });
      Object.keys(s.invalid).forEach(function (k) { out.invalid[k] = s.invalid[k]; });
    });
    return out;
  };
  /* Order-independent fingerprint of everything held, for the round-trip tests. */
  CW.digest = function (store) {
    var ids = Object.keys(store.byId).sort();
    var parts = ids.map(function (k) { return canon(store.byId[k]); });
    var cf = Object.keys(store.conflicts).sort().map(function (k) { return k + '=' + store.conflicts[k].map(canon).sort().join('~'); });
    var us = Object.keys(store.unsupported).sort().map(function (k) { return k + '=' + store.unsupported[k].map(canon).sort().join('~'); });
    return ids.length + ':' + cf.join('|') + ':' + us.join('|') + ':' + parts.join('\n');
  };

  /* ---------------------------------------------------------------- live view */
  function live(store) {
    var retracted = dict(), ev = [];
    Object.keys(store.byId).forEach(function (k) { var e = store.byId[k]; if (e.event_type === 'event_retracted') retracted[e.payload.target_event_id] = 1; });
    Object.keys(store.byId).forEach(function (k) { var e = store.byId[k]; if (!retracted[e.event_id] && e.event_type !== 'event_retracted') ev.push(e); });
    ev.sort(function (a, b) { return (ms(a.occurred_at_utc) - ms(b.occurred_at_utc)) || (a.event_id < b.event_id ? -1 : 1); });
    return ev;
  }
  function inWin(e, w) { var t = ms(e.occurred_at_utc); return t >= w.from && t < w.to; }

  /* ---------------------------------------------------------------- intervals + time */
  CW.unionIntervals = function (iv) {
    var a = iv.map(function (x) { return [x[0], x[1]]; }).filter(function (x) { return x[1] > x[0]; }).sort(function (p, q) { return p[0] - q[0]; });
    var out = [];
    a.forEach(function (x) { var l = out[out.length - 1]; if (l && x[0] <= l[1]) l[1] = Math.max(l[1], x[1]); else out.push(x); });
    return out;
  };
  /* A foreground stretch ends at the LAST ACTUAL INTERACTION, never at the moment idleness was
     noticed. Time after it is discarded, not left as confirmed work. */
  CW.settleIdle = function (startMs, lastInteractionMs, detectedMs, idleMs) {
    if (detectedMs - lastInteractionMs < idleMs) return { idle: false, end: null };
    return { idle: true, end: Math.max(startMs, lastInteractionMs), discarded: detectedMs - Math.max(startMs, lastInteractionMs) };
  };
  /* Recover after a crash: only durable confirmed intervals count; unsettled provisional time is
     reported as unknown seconds and credited to nobody. */
  CW.recover = function (confirmed, provisionalSeconds) {
    var s = 0; confirmed.forEach(function (x) { s += (x[1] - x[0]) / 1000; });
    return { confirmedSeconds: s, unknownSeconds: provisionalSeconds || 0 };
  };
  function parts(t, tz) {
    var f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
    var o = {}; f.formatToParts(new Date(t)).forEach(function (p) { o[p.type] = +p.value; });
    return o;
  }
  function offsetMs(t, tz) { var p = parts(t, tz); return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(t / 1000) * 1000; }
  CW.offsetMs = offsetMs;
  /* First instant after t at which the LOCAL calendar day changes. Found by search on the day key, so
     it is correct in every zone: 23/25-hour days, half-hour offsets, and zones where local midnight
     does not exist on a DST day (the day then starts at 01:00). Always > t, so callers cannot loop. */
  function dayKey(t, tz) { var p = parts(t, tz); return p.year * 10000 + p.month * 100 + p.day; }
  function nextMidnight(t, tz) {
    var k = dayKey(t, tz), lo = Math.floor(t / 1000), hi = lo + 49 * 3600;
    while (hi - lo > 1) { var mid = Math.floor((lo + hi) / 2); if (dayKey(mid * 1000, tz) === k) lo = mid; else hi = mid; }
    return hi * 1000;
  }
  CW.splitByLocalDay = function (startMs, endMs, tz) {
    var out = [], t = startMs;
    while (t < endMs) { var n = Math.min(endMs, nextMidnight(t, tz)); out.push([t, n]); t = n; }
    return out;
  };
  /* Local wall time + IANA zone -> UTC. Refuses a nonexistent (spring-forward) time and asks for the
     offset on an ambiguous (fall-back) one. */
  CW.resolveLocal = function (local, tz, offsetMinutes) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local || '');
    if (!m) return { ok: false, error: 'format' };
    var asUtc = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]), seen = {}, ok = [], chk = new Date(asUtc);
    if (chk.getUTCFullYear() !== +m[1] || chk.getUTCMonth() !== +m[2] - 1 || chk.getUTCDate() !== +m[3] ||
        chk.getUTCHours() !== +m[4] || chk.getUTCMinutes() !== +m[5]) return { ok: false, error: 'range' };
    [-1, 0, 1].forEach(function (d) {
      var off = offsetMs(asUtc + d * 6 * 3600000, tz), u = asUtc - off;
      if (offsetMs(u, tz) === off && !seen[u]) { seen[u] = 1; ok.push({ utc: u, offMin: off / 60000 }); }
    });
    if (!ok.length) return { ok: false, error: 'nonexistent' };
    if (ok.length > 1) {
      var pick = ok.filter(function (x) { return x.offMin === offsetMinutes; })[0];
      if (!pick) return { ok: false, error: 'ambiguous', options: ok.map(function (x) { return x.offMin; }) };
      return { ok: true, utc: pick.utc };
    }
    return { ok: true, utc: ok[0].utc };
  };

  /* ---------------------------------------------------------------- active seconds */
  CW.activeIntervals = function (store, callerId, win) {
    var iv = [];
    live(store).forEach(function (e) {
      if (e.event_type !== 'active_interval_confirmed' || e.caller_id !== callerId) return;
      var s = Math.max(ms(e.payload.start_utc), win.from), t = Math.min(ms(e.payload.end_utc), win.to);
      if (t > s) iv.push([s, t]);
    });
    return CW.unionIntervals(iv);
  };
  CW.activeSeconds = function (store, callerId, win) {
    var s = 0; CW.activeIntervals(store, callerId, win).forEach(function (x) { s += (x[1] - x[0]) / 1000; }); return s;
  };

  /* ---------------------------------------------------------------- metrics */
  /* opts: {caller_id, from, to (epoch ms, to exclusive), coverage: 'complete'|'partial'|'unknown',
            approved_policy_ids: [..], asof (epoch ms, cohort observation cutoff; default to)}.
     Returns the four headline counts plus every denominator and flag a screen needs. */
  CW.metrics = function (store, opts) {
    var w = { from: opts.from, to: opts.to }, cid = opts.caller_id, E = live(store);
    var asof = opts.asof || opts.to;
    /* a quarantined id or an orphan child means the books are not complete, whatever the caller declared */
    var cov = opts.coverage || 'unknown';
    var approved = dict(); (opts.approved_policy_ids || []).forEach(function (k) { approved[k] = 1; });
    var launches = dict(), cancelledLaunch = dict(), pending = 0, review = [];
    E.forEach(function (e) {
      if (e.event_type === 'launch_recorded') launches[e.payload.launch_id] = e;
      if (e.event_type === 'launch_cancelled') cancelledLaunch[e.payload.launch_id] = 1;
    });
    /* attempts: confirmed, linked to a recorded launch, by this caller */
    var attemptsAll = dict(), attempts = [];
    E.forEach(function (e) {
      if (e.event_type !== 'attempt_confirmed' || e.caller_id !== cid) return;
      var ln = launches[e.payload.launch_id];
      if (!ln) { pending++; return; }                                      // parent not here yet
      if (ln.payload.attempt_id !== e.payload.attempt_id || ln.caller_id !== cid) { review.push({ kind: 'launch_mismatch', attempt_id: e.payload.attempt_id }); return; }
      if (cancelledLaunch[e.payload.launch_id]) { review.push({ kind: 'attempt_on_cancelled_launch', attempt_id: e.payload.attempt_id }); return; }
      if (attemptsAll[e.payload.attempt_id]) return;                       // retransmission
      attemptsAll[e.payload.attempt_id] = e;
      if (inWin(e, w)) attempts.push(e);
    });
    var owners = dict(), unresolved = dict();
    attempts.forEach(function (e) {
      if (e.owner_id) owners[e.owner_id] = 1;
      else unresolved[(e.case_refs && e.case_refs[0]) || e.event_id] = 1;
    });
    /* the cohort is VERIFIED owners only; unresolved attempts never feed a per-100-owner numerator */
    var cohortAttempt = dict(); attempts.forEach(function (e) { if (e.owner_id) cohortAttempt[e.payload.attempt_id] = e; });
    /* conversations: one per attempt_id and per conversation_id */
    var convByAttempt = dict(), convs = dict();
    E.forEach(function (e) {
      if (e.event_type !== 'conversation_confirmed' || e.caller_id !== cid) return;
      var at = attemptsAll[e.payload.attempt_id];
      if (!at) { pending++; return; }
      if (convByAttempt[e.payload.attempt_id] || convs[e.payload.conversation_id]) {
        if (convByAttempt[e.payload.attempt_id] && convByAttempt[e.payload.attempt_id].event_id !== e.event_id &&
            convByAttempt[e.payload.attempt_id].payload.conversation_id !== e.payload.conversation_id) review.push({ kind: 'second_conversation_same_attempt', attempt_id: e.payload.attempt_id });
        return;
      }
      convByAttempt[e.payload.attempt_id] = e; convs[e.payload.conversation_id] = e;
    });
    /* callbacks */
    var req = dict(), cancelledReq = dict(), completed = dict(), convUsed = dict();
    E.forEach(function (e) {
      if (e.event_type === 'callback_requested' && !req[e.payload.request_id]) req[e.payload.request_id] = e;
      if (e.event_type === 'callback_cancelled' && !cancelledReq[e.payload.request_id]) cancelledReq[e.payload.request_id] = e;
    });
    E.forEach(function (e) {
      if (e.event_type !== 'callback_completed' || e.caller_id !== cid) return;
      var r = req[e.payload.request_id];
      if (!r) { pending++; return; }
      /* a cancel voids only a completion that is NOT earlier than it; a cancel dated after a real
         completion cannot retroactively undo it */
      if (cancelledReq[e.payload.request_id] && ms(cancelledReq[e.payload.request_id].occurred_at_utc) <= ms(e.occurred_at_utc)) {
        review.push({ kind: 'completion_after_cancel', request_id: e.payload.request_id }); return; }
      var cv = convs[e.payload.conversation_id];
      if (!cv) { pending++; return; }
      /* right owner: the conversation's verified owner must be the owner who asked */
      var cvOwner = attemptsAll[cv.payload.attempt_id] && attemptsAll[cv.payload.attempt_id].owner_id;
      if (!r.owner_id || !cvOwner || r.owner_id !== cvOwner) { review.push({ kind: 'callback_owner_not_verified', request_id: e.payload.request_id }); return; }
      if (completed[e.payload.request_id]) return;                          // duplicate completion
      if (convUsed[e.payload.conversation_id]) { review.push({ kind: 'conversation_closes_one_request', conversation_id: e.payload.conversation_id }); return; }
      completed[e.payload.request_id] = e; convUsed[e.payload.conversation_id] = 1;
    });
    /* appointments */
    var booked = dict(), cancelledAppt = dict(), qualified = dict();
    E.forEach(function (e) {
      if (e.event_type === 'appointment_booked' && !booked[e.payload.appointment_id]) booked[e.payload.appointment_id] = e;
      if (e.event_type === 'appointment_cancelled') cancelledAppt[e.payload.appointment_id] = e;
    });
    var notAssessed = 0;
    E.forEach(function (e) {
      if (e.event_type !== 'appointment_qualified' || e.caller_id !== cid) return;
      var b = booked[e.payload.appointment_id];
      if (!b) { pending++; return; }
      var bc = convs[b.payload.conversation_id];
      if (!bc) { pending++; return; }
      if (bc.payload.attempt_id !== b.payload.attempt_id) { review.push({ kind: 'booking_attempt_mismatch', appointment_id: b.payload.appointment_id }); return; }
      if (!approved[e.payload.policy_id]) { notAssessed++; return; }        // no approved rubric: booked, not qualified
      var c = e.payload.checklist, pass = Object.keys(c).length > 0 && Object.keys(c).every(function (k) { return c[k] === true; });
      if (!pass) return;
      if (!qualified[e.payload.appointment_id]) qualified[e.payload.appointment_id] = e;
    });
    function count(map, pred) { var n = 0; Object.keys(map).forEach(function (k) { if (pred(map[k])) n++; }); return n; }
    var nConv = count(convByAttempt, function (e) { return inWin(e, w); });
    var nCb = count(completed, function (e) { return inWin(e, w); });
    var nAp = count(qualified, function (e) { return inWin(e, w); });
    var convOwners = dict(); Object.keys(convByAttempt).forEach(function (k) {
      var a = attemptsAll[k]; if (a && a.owner_id && cohortAttempt[k] && inWin(convByAttempt[k], { from: w.from, to: asof })) convOwners[a.owner_id] = 1;
    });
    /* tracked vs untracked: an outcome is "tracked" when it falls inside a confirmed active interval */
    var iv = CW.activeIntervals(store, cid, w), secs = 0; iv.forEach(function (x) { secs += (x[1] - x[0]) / 1000; });
    function tracked(e) { var t = ms(e.occurred_at_utc); return iv.some(function (x) { return t >= x[0] && t < x[1]; }); }
    function split(map, pred) { var tr = 0, un = 0; Object.keys(map).forEach(function (k) { var e = map[k]; if (!pred(e)) return; if (tracked(e)) tr++; else un++; }); return { tracked: tr, untracked: un }; }
    /* per-hour "owners attempted" counts DISTINCT verified owners attempted inside confirmed time. */
    var tAtt = { tracked: 0, untracked: 0 }, trOwn = dict();
    attempts.forEach(function (e) { if (tracked(e)) { if (e.owner_id) trOwn[e.owner_id] = 1; } else tAtt.untracked++; });
    tAtt.tracked = Object.keys(trOwn).length;
    var tConv = split(convByAttempt, function (e) { return inWin(e, w); });
    var tCb = split(completed, function (e) { return inWin(e, w); });
    var tAp = split(qualified, function (e) { return inWin(e, w); });
    var hours = secs / 3600;
    function perHour(n) { return hours > 0 && cov === 'complete' ? n / hours : null; }
    /* cohort rate: only events whose ATTEMPT is in the cohort, observed up to asof */
    function cohortN(map, attemptOf) { var n = 0; Object.keys(map).forEach(function (k) { var e = map[k], t = ms(e.occurred_at_utc); if (t >= w.from && t < asof && attemptOf(e)) n++; }); return n; }
    var cConv = cohortN(convByAttempt, function (e) { return !!cohortAttempt[e.payload.attempt_id]; });
    var cCb = cohortN(completed, function (e) { var c = convs[e.payload.conversation_id]; return c && cohortAttempt[c.payload.attempt_id]; });
    var cAp = cohortN(qualified, function (e) { var b = booked[e.payload.appointment_id]; return b && cohortAttempt[b.payload.attempt_id]; });
    if (cov === 'complete' && (Object.keys(store.conflicts).length > 0 || pending > 0)) cov = 'partial';
    var nOwners = Object.keys(owners).length;
    function per100(n) { return nOwners ? (n / nOwners) * 100 : null; }
    function state(n) { return n > 0 ? 'confirmed' : (cov === 'complete' ? 'zero' : (cov === 'partial' ? 'zero_partial' : 'unknown')); }
    return {
      coverage: cov,
      owners_attempted: nOwners, unresolved_owner_attempts: Object.keys(unresolved).length,
      attempts: attempts.length, conversations: nConv, callbacks_completed: nCb, appointments_qualified: nAp,
      states: { owners: state(nOwners), conversations: state(nConv), callbacks: state(nCb), appointments: state(nAp) },
      appointments_not_assessed: notAssessed,
      appointments_cancelled_after_qualified: count(qualified, function (e) { return !!cancelledAppt[e.payload.appointment_id]; }),
      conversation_owner_yield_pct: nOwners ? (Object.keys(convOwners).length / nOwners) * 100 : null,
      active_seconds: secs, hours: hours,
      per_hour: { owners: perHour(tAtt.tracked), conversations: perHour(tConv.tracked), callbacks: perHour(tCb.tracked), appointments: perHour(tAp.tracked) },
      untracked: { attempts: tAtt.untracked, conversations: tConv.untracked, callbacks: tCb.untracked, appointments: tAp.untracked },
      per_100_owners: { conversations: per100(cConv), callbacks: per100(cCb), appointments: per100(cAp) },
      cohort_asof: asof,
      conflicts: Object.keys(store.conflicts).length, pending_parents: pending, review: review,
      partial: cov !== 'complete'
    };
  };

  /* ---------------------------------------------------------------- callbacks view
     Requested callbacks only (an owner-asked call with a concrete due instant). Reminders from a
     no-answer, next/nextTs and inbound replies are NOT here. Sorted by due instant, overdue first.
     A request is closed only by a cancel, or by a completion whose conversation exists and whose
     verified owner is the owner who asked. A completion that fails that stays OPEN and is flagged.
     `held` maps a case id to the existing hold reason: a held request stays visible and pending with
     dial_enabled=false. Nothing here enables contact or lifts a hold.
     -> {due:[], future:[], closed:{completed,cancelled}, review:[]} */
  CW.callbackQueue = function (store, opts) {
    opts = opts || {};
    var asof = opts.asof != null ? opts.asof : Date.now(), heldIn = opts.held || {}, held = dict();
    Object.keys(heldIn).forEach(function (k) { if (heldIn[k]) held[k] = String(heldIn[k]); });
    var E = live(store), req = dict(), due = dict(), cancelled = dict(), done = dict(), convs = dict(), att = dict(), review = [], convUsed = dict();
    E.forEach(function (e) {
      var p = e.payload;
      if (e.event_type === 'callback_requested') {
        if (!req[p.request_id]) { req[p.request_id] = e; due[p.request_id] = { at: p.due_utc, rev: 0 }; }
        else if (req[p.request_id].owner_id !== e.owner_id || req[p.request_id].payload.due_utc !== p.due_utc) review.push({ kind: 'duplicate_request_id_differs', request_id: p.request_id });
      }
      if (e.event_type === 'attempt_confirmed') att[p.attempt_id] = e;
      if (e.event_type === 'conversation_confirmed') convs[p.conversation_id] = e;
    });
    E.forEach(function (e) {                                  // E is time-ordered, so the last reschedule wins
      var p = e.payload;
      if (e.event_type === 'callback_rescheduled' && req[p.request_id] && ms(e.occurred_at_utc) < ms(req[p.request_id].occurred_at_utc)) review.push({ kind: 'reschedule_before_request', request_id: p.request_id });
      else if (e.event_type === 'callback_rescheduled' && req[p.request_id] && isFinite(ms(p.due_utc))) { due[p.request_id] = { at: p.due_utc, rev: due[p.request_id].rev + 1 }; }
      if (e.event_type === 'callback_cancelled' && req[p.request_id] && !cancelled[p.request_id]) cancelled[p.request_id] = e;
    });
    E.forEach(function (e) {
      if (e.event_type !== 'callback_completed') return;
      var p = e.payload, r = req[p.request_id];
      if (!r || done[p.request_id]) return;
      if (cancelled[p.request_id] && ms(cancelled[p.request_id].occurred_at_utc) <= ms(e.occurred_at_utc)) { review.push({ kind: 'completion_after_cancel', request_id: p.request_id }); return; }
      var cv = convs[p.conversation_id], a = cv && att[cv.payload.attempt_id];
      if (!cv || !a || !r.owner_id || a.owner_id !== r.owner_id) { review.push({ kind: 'completion_unverified', request_id: p.request_id }); return; }
      /* one conversation closes one request: the second is flagged and stays open */
      if (convUsed[p.conversation_id]) { review.push({ kind: 'conversation_closes_one_request', conversation_id: p.conversation_id, request_id: p.request_id }); return; }
      convUsed[p.conversation_id] = 1; done[p.request_id] = e;
    });
    var byOwner = dict();
    Object.keys(req).forEach(function (id) { var o = req[id].owner_id; if (o) (byOwner[o] = byOwner[o] || []).push(req[id]); });
    var out = { due: [], future: [], closed: { completed: Object.keys(done).length, cancelled: 0 }, review: review };
    Object.keys(req).forEach(function (id) {
      if (done[id]) return;                                   // a completion that stood is never undone by a later cancel
      if (cancelled[id]) { out.closed.cancelled++; return; }
      var r = req[id], d = due[id], t = ms(d.at);
      if (!isFinite(t)) { review.push({ kind: 'bad_due', request_id: id }); return; }
      var later = (r.owner_id && byOwner[r.owner_id] || []).filter(function (x) { return x.event_id !== r.event_id && ms(x.occurred_at_utc) >= ms(r.occurred_at_utc) && !cancelled[x.payload.request_id] && !done[x.payload.request_id]; });
      var hold = '';
      (r.case_refs || []).forEach(function (c) { if (!hold && held[c]) hold = held[c]; });
      if (!(r.case_refs || []).length) hold = 'no case on this request, cannot check holds';
      var item = { request_id: id, owner_id: r.owner_id || null, case_refs: r.case_refs || [], due_utc: d.at, tz: r.payload.tz, revision: d.rev,
                   overdue: t <= asof, held_reason: hold, dial_enabled: !hold, superseded_by_later_request: later.length > 0 };
      (t <= asof ? out.due : out.future).push(item);
    });
    function byDue(a, b) { return (ms(a.due_utc) - ms(b.due_utc)) || (a.request_id < b.request_id ? -1 : 1); }
    out.due.sort(byDue); out.future.sort(byDue);
    return out;
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = CW; else root.CW = CW;
})(typeof window !== 'undefined' ? window : this);
