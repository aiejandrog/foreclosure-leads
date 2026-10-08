/* _callworkflowtest.js -- fake-data fixtures for call_workflow.js (spec matrix M, E, T, R and the
 * numerical oracle). Every id is fictional. Exits 1 on any failed assertion; it does not rely on the
 * process exit alone being meaningful elsewhere. Run: node _callworkflowtest.js */
const CW = require('./call_workflow.js');
let fails = 0;
function rec(name, ok, extra) { console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok ? '' : ' | ' + JSON.stringify(extra))); if (!ok) fails++; }
const T0 = Date.parse('2026-10-08T13:00:00Z'), H = 3600000, MIN = 60000;
const iso = t => new Date(t).toISOString();
let seq = 0;
function ev(type, payload, o) {
  o = o || {}; seq++;
  return Object.assign({ schema_version: 1, event_id: o.id || ('e' + seq), event_type: type, occurred_at_utc: iso(o.t != null ? o.t : T0),
    recorded_at_utc: iso(o.t != null ? o.t : T0), device_id: 'devA', caller_id: o.caller === undefined ? 'C1' : o.caller,
    owner_id: o.owner === undefined ? null : o.owner, case_refs: o.cases || ['X' + seq], payload: payload }, o.extra || {});
}
const launch = (id, at, o) => ev('launch_recorded', { launch_id: id, attempt_id: at }, o);
const attempt = (id, at, owner, t) => ev('attempt_confirmed', { launch_id: id, attempt_id: at, attempted: 'yes', eligibility: 'pass' }, { owner, t });
const conv = (cid, at, t) => ev('conversation_confirmed', { conversation_id: cid, attempt_id: at, role: 'owner', role_verification: 'caller_attested', two_way: true }, { t });
const W = { caller_id: 'C1', from: T0 - H, to: T0 + 24 * H, coverage: 'complete' };
const build = list => list.reduce((s, e) => CW.add(s, e), CW.newStore());

// M01 launch then cancel
{ let s = build([launch('L1', 'A1', { t: T0 }), ev('launch_cancelled', { launch_id: 'L1' })]);
  const m = CW.metrics(s, W);
  rec('M01 cancelled launch: attempted owners 0 with complete coverage, nothing else counts',
      m.owners_attempted === 0 && m.attempts === 0 && m.conversations === 0 && m.states.owners === 'zero', m); }

// M02 two attempts on two numbers for owner A, retransmit
{ const L = [launch('L1', 'A1'), attempt('L1', 'A1', 'ownA', T0 + 1), launch('L2', 'A2'), attempt('L2', 'A2', 'ownA', T0 + 2)];
  let s = build(L); const m1 = CW.metrics(s, W); const d1 = CW.digest(s);
  L.forEach(e => CW.add(s, JSON.parse(JSON.stringify(e))));            // retransmit every event
  rec('M02 two attempts = 2 attempts, 1 unique owner; retransmit changes nothing',
      m1.attempts === 2 && m1.owners_attempted === 1 && CW.digest(s) === d1, m1); }

// unresolved identity stays separate
{ let s = build([launch('L1', 'A1'), attempt('L1', 'A1', null, T0 + 1)]);
  const m = CW.metrics(s, W);
  rec('singleton case without verified owner: unresolved, not in the verified numerator',
      m.owners_attempted === 0 && m.unresolved_owner_attempts === 1, m); }

// M04 conversation needs attestation; generic answered stays out (validation)
{ const bad = ev('conversation_confirmed', { conversation_id: 'V1', attempt_id: 'A1', role: 'owner', two_way: true });
  rec('M04 conversation without caller attestation is invalid', !CW.validate(bad).ok, CW.validate(bad));
  let s = build([launch('L1', 'A1'), attempt('L1', 'A1', 'ownA', T0 + 1), conv('V1', 'A1', T0 + 2), conv('V1b', 'A1', T0 + 3)]);
  const m = CW.metrics(s, W);
  rec('M04 one right-owner conversation per attempt; duplicate confirmation counts once', m.conversations === 1, m); }

// M05 callback request, miss, completion, duplicate completion
{ const req = ev('callback_requested', { request_id: 'R1', requested_by_owner: true, evidence_ref: 'note-1', tz: 'America/New_York', local_time: '2026-10-09T10:00', due_utc: iso(T0 + 24 * H) }, { t: T0, owner: 'ownA' });
  const done = (id) => ev('callback_completed', { request_id: 'R1', conversation_id: 'V1' }, { t: T0 + 3, id });
  let s = build([req, launch('L1', 'A1'), attempt('L1', 'A1', 'ownA', T0 + 1), launch('L2', 'A2'), attempt('L2', 'A2', 'ownA', T0 + 2), conv('V1', 'A2', T0 + 3)]);
  rec('M05 request still open after a miss (no completion yet)', CW.metrics(s, W).callbacks_completed === 0);
  CW.add(s, done('d1')); CW.add(s, done('d2'));
  rec('M05 completion counts once; duplicate completion changes nothing', CW.metrics(s, W).callbacks_completed === 1, CW.metrics(s, W)); }

// M06 rescheduled, cancelled, then late completion
{ const req = ev('callback_requested', { request_id: 'R1', requested_by_owner: true, evidence_ref: 'n', tz: 'America/New_York', local_time: '2026-10-09T10:00', due_utc: iso(T0 + H) });
  let s = build([req, ev('callback_rescheduled', { request_id: 'R1', due_utc: iso(T0 + 2 * H), revision: 2 }), ev('callback_rescheduled', { request_id: 'R1', due_utc: iso(T0 + 3 * H), revision: 3 }),
    ev('callback_cancelled', { request_id: 'R1' }), launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'),
    ev('callback_completed', { request_id: 'R1', conversation_id: 'V1' })]);
  const m = CW.metrics(s, W);
  rec('M06 completion after cancellation does not count and is flagged for review', m.callbacks_completed === 0 && m.review.length === 1, m); }

// M07 / M08 appointments
{ const bk = ev('appointment_booked', { appointment_id: 'P1', conversation_id: 'V1', attempt_id: 'A1', booking_state: 'confirmed', agreed_utc: iso(T0 + 48 * H), purpose: 'advisor_call' });
  const ql = (pass, id) => ev('appointment_qualified', { appointment_id: 'P1', policy_id: 'pol1', checklist: { owner: true, need: true, time: true, fit: pass } }, { id });
  let s = build([launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'), bk, ql(false, 'q0')]);
  let m = CW.metrics(s, Object.assign({ approved_policy_ids: ['pol1'] }, W));
  rec('M07 booked, rubric incomplete: no qualified appointment', m.appointments_qualified === 0, m);
  const q1 = ql(true, 'q1');
  s = build([launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'), bk, q1]);
  m = CW.metrics(s, Object.assign({ approved_policy_ids: ['pol1'] }, W));
  rec('M08 approved rubric passed: 1 qualified', m.appointments_qualified === 1, m);
  m = CW.metrics(s, W);
  rec('no approved rubric: booked, qualification not assessed, count 0', m.appointments_qualified === 0 && m.appointments_not_assessed === 1, m);
  CW.add(s, JSON.parse(JSON.stringify(q1)));
  rec('M08 replaying the qualification event changes nothing', CW.metrics(s, Object.assign({ approved_policy_ids: ['pol1'] }, W)).appointments_qualified === 1);
  CW.add(s, ev('event_retracted', { target_event_id: 'q1' }));
  rec('M08 retraction removes it from the restated total, original evidence kept', CW.metrics(s, Object.assign({ approved_policy_ids: ['pol1'] }, W)).appointments_qualified === 0 && !!s.byId['q1']);
  CW.add(s, ev('appointment_cancelled', { appointment_id: 'P1' }));
  s = build([launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'), bk, q1, ev('appointment_cancelled', { appointment_id: 'P1' })]);
  m = CW.metrics(s, Object.assign({ approved_policy_ids: ['pol1'] }, W));
  rec('a later cancellation stays qualified with an annotation', m.appointments_qualified === 1 && m.appointments_cancelled_after_qualified === 1, m); }

// E01 order independence; E02 conflict + orphan
{ const list = [launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'), launch('L2', 'A2'), attempt('L2', 'A2', 'ownB')];
  const perms = [[0, 1, 2, 3, 4], [4, 3, 2, 1, 0], [2, 0, 4, 1, 3], [1, 3, 0, 2, 4], [3, 4, 1, 0, 2]];
  const ds = perms.map(p => CW.digest(build(p.map(i => list[i]))));
  rec('E01 five different arrival orders give one digest', ds.every(d => d === ds[0]), ds);
  const a = build(list.slice(0, 3)), b = build(list.slice(2));
  const u1 = CW.union(a, b), u2 = CW.union(b, a), u3 = CW.union(u1, a);
  rec('E01 union is commutative and idempotent', CW.digest(u1) === CW.digest(u2) && CW.digest(u1) === CW.digest(u3) && CW.digest(u1) === ds[0]); }
{ const x = ev('launch_recorded', { launch_id: 'L1', attempt_id: 'A1' }, { id: 'same' }), y = ev('launch_recorded', { launch_id: 'L9', attempt_id: 'A9' }, { id: 'same' });
  let s = build([x, y]);
  rec('E02 same id with different payload: both quarantined, neither counted', !s.byId.same && s.conflicts.same.length === 2 && CW.metrics(s, W).partial === true);
  let o = build([attempt('L1', 'A1', 'ownA')]);
  rec('E02 child before parent is pending and counts nowhere', CW.metrics(o, W).attempts === 0 && CW.metrics(o, W).pending_parents === 1);
  CW.add(o, launch('L1', 'A1'));
  rec('E02 the parent arriving releases the child', CW.metrics(o, W).attempts === 1);
  let f = CW.add(CW.newStore(), Object.assign(ev('launch_recorded', { launch_id: 'L1', attempt_id: 'A1' }, { id: 'fut' }), { schema_version: 2 }));
  rec('unknown future schema is kept losslessly and counted as nothing', !!f.unsupported.fut && Object.keys(f.byId).length === 0); }

// T01-T06 time
{ const s0 = Date.parse('2026-10-08T09:00:00-04:00'), last = s0 + 3 * MIN, det = s0 + 8 * MIN;
  const r = CW.settleIdle(s0, last, det, 5 * MIN);
  rec('T01 idle at 09:08 settles to 09:03; 09:03-09:08 discarded', r.idle && r.end === last && r.discarded === 5 * MIN, r);
  const c = CW.recover([[s0, s0 + 60000]], 15);
  rec('T03 crash: 60 confirmed seconds recovered, 15 provisional unknown, none credited', c.confirmedSeconds === 60 && c.unknownSeconds === 15, c);
  const u = CW.unionIntervals([[s0, s0 + 10 * MIN], [s0 + 5 * MIN, s0 + 15 * MIN]]);
  rec('T04 overlapping tabs for one caller: 15 minutes, not 20', u.length === 1 && (u[0][1] - u[0][0]) === 15 * MIN, u);
  let s = build([ev('active_interval_confirmed', { session_id: 'S1', start_utc: iso(s0), end_utc: iso(s0 + 10 * MIN) }, { caller: 'C1' }),
    ev('active_interval_confirmed', { session_id: 'S2', start_utc: iso(s0 + 5 * MIN), end_utc: iso(s0 + 15 * MIN) }, { caller: 'C1' }),
    ev('active_interval_confirmed', { session_id: 'S3', start_utc: iso(s0), end_utc: iso(s0 + 10 * MIN) }, { caller: 'C2' })]);
  const win = { from: s0 - H, to: s0 + H };
  rec('T04 different callers stay separate and add for crew hours', CW.activeSeconds(s, 'C1', win) === 900 && CW.activeSeconds(s, 'C2', win) === 600);
  const sp = CW.splitByLocalDay(Date.parse('2026-10-08T23:30:00-04:00'), Date.parse('2026-10-09T00:30:00-04:00'), 'America/New_York');
  rec('T06 interval over local midnight splits there, duration preserved', sp.length === 2 && sp[0][1] === Date.parse('2026-10-09T00:00:00-04:00') && (sp[0][1] - sp[0][0] + sp[1][1] - sp[1][0]) === H, sp);
  const dst = CW.splitByLocalDay(Date.parse('2026-11-01T00:00:00-04:00'), Date.parse('2026-11-02T00:00:00-05:00'), 'America/New_York');
  rec('T06 fall-back day is 25 hours', dst.length === 1 && (dst[0][1] - dst[0][0]) === 25 * H, dst);
  rec('T06 nonexistent local time is rejected', CW.resolveLocal('2026-03-08T02:30', 'America/New_York').error === 'nonexistent');
  const amb = CW.resolveLocal('2026-11-01T01:30', 'America/New_York');
  rec('T06 ambiguous local time asks for the offset', amb.error === 'ambiguous' && amb.options.sort((a, b) => a - b).join() === '-300,-240', amb);
  rec('T06 an explicit offset resolves it', CW.resolveLocal('2026-11-01T01:30', 'America/New_York', -300).utc === Date.parse('2026-11-01T01:30:00-05:00'));
  rec('an ordinary local time resolves', CW.resolveLocal('2026-10-09T10:00', 'America/New_York').utc === Date.parse('2026-10-09T10:00:00-04:00')); }

// R01 / oracle: C1 3600 s, 10 owners, 4 conversations on 3 owners, 2 callbacks, 1 appointment
{ const L = [], t = k => T0 + k * MIN, S = Date.parse('2026-10-08T13:00:00Z');
  for (let k = 1; k <= 10; k++) { L.push(launch('L' + k, 'A' + k, { t: t(k) })); L.push(attempt('L' + k, 'A' + k, 'own' + k, t(k) + 1000)); }
  L.push(conv('V1', 'A1', t(1) + 2000), conv('V2', 'A2', t(2) + 2000), conv('V3', 'A3', t(3) + 2000), conv('V4', 'A3b', t(3) + 3000));
  L.push(launch('L3b', 'A3b', { t: t(3) + 2500 }), attempt('L3b', 'A3b', 'own3', t(3) + 2600));
  const rq = (r, o) => ev('callback_requested', { request_id: r, requested_by_owner: true, evidence_ref: 'n', tz: 'America/New_York', local_time: '2026-10-09T10:00', due_utc: iso(T0) }, { t: S - H, owner: o });
  L.push(rq('R1', 'own1'), rq('R2', 'own2'));
  L.push(ev('callback_completed', { request_id: 'R1', conversation_id: 'V1' }, { t: t(1) + 3000 }), ev('callback_completed', { request_id: 'R2', conversation_id: 'V2' }, { t: t(2) + 3000 }));
  L.push(ev('appointment_booked', { appointment_id: 'P1', conversation_id: 'V3', attempt_id: 'A3', booking_state: 'confirmed', agreed_utc: iso(T0 + 48 * H), purpose: 'advisor_call' }, { t: t(3) + 4000 }),
         ev('appointment_qualified', { appointment_id: 'P1', policy_id: 'pol1', checklist: { a: true } }, { t: t(3) + 5000 }));
  L.push(ev('active_interval_confirmed', { session_id: 'S1', start_utc: iso(S), end_utc: iso(S + H) }, { t: S + H }));
  let s = build(L);
  const m = CW.metrics(s, { caller_id: 'C1', from: S, to: S + 2 * H, coverage: 'complete', approved_policy_ids: ['pol1'] });
  rec('oracle: 10 attempted owners; 4 conversations; 2 callbacks; 1 appointment (4 conv incl. a 2nd attempt on own3)',
      m.owners_attempted === 10 && m.conversations === 4 && m.callbacks_completed === 2 && m.appointments_qualified === 1, m);
  rec('oracle: per hour attempted owners 10', m.per_hour.owners === 10 && m.attempts === 11, m.per_hour);
  rec('oracle: per hour conversations 4, callbacks 2, appointments 1', m.per_hour.conversations === 4 && m.per_hour.callbacks === 2 && m.per_hour.appointments === 1, m.per_hour);
  rec('oracle: per 100 owners conversations 40, callbacks 20, appointments 10', m.per_100_owners.conversations === 40 && m.per_100_owners.callbacks === 20 && m.per_100_owners.appointments === 10, m.per_100_owners);
  rec('R01 distinct-owner conversation yield is 30 percent and never above 100', Math.abs(m.conversation_owner_yield_pct - 30) < 1e-9, m.conversation_owner_yield_pct);
  // two more conversations outside the session on two other owners
  L.push(launch('Lx', 'Ax', { t: S + 3 * H }), attempt('Lx', 'Ax', 'ownX', S + 3 * H + 1), conv('Vx', 'Ax', S + 3 * H + 2),
         launch('Ly', 'Ay', { t: S + 3 * H + 5 }), attempt('Ly', 'Ay', 'ownY', S + 3 * H + 6), conv('Vy', 'Ay', S + 3 * H + 7));
  s = build(L);
  const m2 = CW.metrics(s, { caller_id: 'C1', from: S, to: S + 4 * H, coverage: 'complete', approved_policy_ids: ['pol1'], asof: S + 2 * H });
  rec('oracle: event-date conversations 6 with 2 untracked; tracked rate stays 4 per hour', m2.conversations === 6 && m2.untracked.conversations === 2 && m2.per_hour.conversations === 4, m2);
  const m3 = CW.metrics(s, { caller_id: 'C1', from: S, to: S + 2 * H, coverage: 'complete', approved_policy_ids: ['pol1'] });
  rec('R02 an outcome outside the cohort window is excluded from the cohort rate', m3.per_100_owners.conversations === 40, m3.per_100_owners);
  // R02: today's late outcome for yesterday's attempt
  const m4 = CW.metrics(s, { caller_id: 'C1', from: S + 2.5 * H, to: S + 4 * H, coverage: 'complete', approved_policy_ids: ['pol1'] });
  rec('zero hours in the window: per-hour is unavailable, never 0', m4.per_hour.conversations === null, m4.per_hour);
  const m5 = CW.metrics(s, { caller_id: 'C1', from: S, to: S + 2 * H, coverage: 'partial', approved_policy_ids: ['pol1'] });
  rec('partial coverage: per-hour unavailable and the result says partial', m5.per_hour.conversations === null && m5.partial === true);
  const m6 = CW.metrics(build([]), { caller_id: 'C1', from: S, to: S + H, coverage: 'partial' });
  const m7 = CW.metrics(build([]), { caller_id: 'C1', from: S, to: S + H, coverage: 'unknown' });
  const m8 = CW.metrics(build([]), { caller_id: 'C1', from: S, to: S + H, coverage: 'complete' });
  rec('zero states differ: complete=zero, partial=zero_partial, unknown=unknown',
      m8.states.callbacks === 'zero' && m6.states.callbacks === 'zero_partial' && m7.states.callbacks === 'unknown', [m8.states, m6.states, m7.states]); }


// ---- review fixes (independent review of d394256) ----
{ // zones where local midnight does not exist must not hang, and must still sum to the duration
  const a = Date.parse('2010-10-16T12:00:00Z'), b = Date.parse('2010-10-18T12:00:00Z');
  ['America/Sao_Paulo', 'America/Santiago', 'America/Havana', 'Asia/Kolkata', 'Europe/London'].forEach(z => {
    const sp = CW.splitByLocalDay(a, b, z), tot = sp.reduce((n, x) => n + x[1] - x[0], 0);
    rec('splitByLocalDay terminates and preserves duration in ' + z, sp.length >= 2 && tot === b - a, sp.length);
  });
  const ap = CW.splitByLocalDay(Date.parse('2011-12-29T12:00:00Z'), Date.parse('2011-12-31T12:00:00Z'), 'Pacific/Apia');
  rec('Pacific/Apia (skipped a whole day) terminates', ap.length >= 2 && ap[ap.length - 1][1] === Date.parse('2011-12-31T12:00:00Z'));
  const ko = CW.splitByLocalDay(Date.parse('2026-10-08T00:00:00Z'), Date.parse('2026-10-08T20:00:00Z'), 'Asia/Kolkata');
  rec('half-hour zone splits at local midnight (18:30Z)', ko.length === 2 && ko[0][1] === Date.parse('2026-10-08T18:30:00Z'), ko);
  rec('resolveLocal rejects Feb 31 and hour 24', CW.resolveLocal('2026-02-31T10:00', 'America/New_York').error === 'range' && CW.resolveLocal('2026-03-01T24:10', 'America/New_York').error === 'range'); }
{ // attempt on a cancelled launch, mismatched attempt id, other caller's launch
  let s = build([launch('L1', 'A1'), ev('launch_cancelled', { launch_id: 'L1' }), attempt('L1', 'A1', 'ownA')]);
  let m = CW.metrics(s, W);
  rec('an attempt on a cancelled launch does not count', m.attempts === 0 && m.review.some(r => r.kind === 'attempt_on_cancelled_launch'), m);
  s = build([launch('L1', 'A1'), attempt('L1', 'A9', 'ownA')]); m = CW.metrics(s, W);
  rec('an attempt whose id differs from its launch does not count', m.attempts === 0, m);
  s = build([launch('L1', 'A1', { caller: 'C2' }), attempt('L1', 'A1', 'ownA')]); m = CW.metrics(s, W);
  rec('an attempt on another caller\'s launch does not count', m.attempts === 0, m); }
{ // zero needs clean books: a quarantined id blocks a verified zero
  const x = ev('launch_recorded', { launch_id: 'L1', attempt_id: 'A1' }, { id: 'same' }), y = ev('launch_recorded', { launch_id: 'L9', attempt_id: 'A9' }, { id: 'same' });
  const m = CW.metrics(build([x, y]), W);
  rec('conflicts make a zero partial, not a verified zero', m.states.owners === 'zero_partial' && m.per_hour.owners === null && m.coverage === 'partial', m); }
{ // callbacks: cancel after a real completion cannot undo it; wrong owner is refused
  const req = (o) => ev('callback_requested', { request_id: 'R1', requested_by_owner: true, evidence_ref: 'n', tz: 'America/New_York', local_time: '2026-10-09T10:00', due_utc: iso(T0) }, { t: T0, owner: o });
  const base = o => [req(o), launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1', T0 + 5), ev('callback_completed', { request_id: 'R1', conversation_id: 'V1' }, { t: T0 + 10 })];
  let s = build(base('ownA').concat([ev('callback_cancelled', { request_id: 'R1' }, { t: T0 + 20 })]));
  rec('a cancel dated after a completion does not undo it', CW.metrics(s, W).callbacks_completed === 1);
  s = build(base('ownB'));
  const m = CW.metrics(s, W);
  rec('a conversation with a different owner cannot complete the request', m.callbacks_completed === 0 && m.review.some(r => r.kind === 'callback_owner_not_verified'), m);
  s = build(base(null)); rec('an unresolved request owner cannot be completed', CW.metrics(s, W).callbacks_completed === 0); }
{ // booking must point at the conversation's own attempt
  const bk = ev('appointment_booked', { appointment_id: 'P1', conversation_id: 'V1', attempt_id: 'A2', booking_state: 'confirmed', agreed_utc: iso(T0 + 48 * H), purpose: 'x' });
  const q = ev('appointment_qualified', { appointment_id: 'P1', policy_id: 'pol1', checklist: { a: true } });
  const m = CW.metrics(build([launch('L1', 'A1'), attempt('L1', 'A1', 'ownA'), conv('V1', 'A1'), bk, q]), Object.assign({ approved_policy_ids: ['pol1'] }, W));
  rec('a booking naming a different attempt than its conversation does not qualify', m.appointments_qualified === 0 && m.review.some(r => r.kind === 'booking_attempt_mismatch'), m); }
{ // unresolved attempts never feed per-100-owner numerators
  const m = CW.metrics(build([launch('L1', 'A1'), attempt('L1', 'A1', null), conv('V1', 'A1'), launch('L2', 'A2'), attempt('L2', 'A2', 'ownB')]), W);
  rec('unresolved attempts stay out of the per-100 numerator', m.per_100_owners.conversations === 0 && m.conversations === 1, m); }
{ // prototype-looking ids are data
  const e = ev('launch_recorded', { launch_id: 'constructor', attempt_id: 'A1' }, { id: 'toString' });
  let s = build([e]);
  rec('event_id "toString" is stored once, not a self-conflict', !!s.byId.toString && Object.keys(s.conflicts).length === 0);
  const m = CW.metrics(build([attempt('constructor', 'A1', 'ownA')]), W);
  rec('launch_id "constructor" does not satisfy a missing launch', m.attempts === 0 && m.pending_parents === 1, m); }
{ // idempotence and symmetry of union with conflicts and unsupported versions
  const x = ev('launch_recorded', { launch_id: 'L1', attempt_id: 'A1' }, { id: 'same' }), y = ev('launch_recorded', { launch_id: 'L9', attempt_id: 'A9' }, { id: 'same' });
  const u1 = Object.assign(ev('launch_recorded', { launch_id: 'f', attempt_id: 'f' }, { id: 'fut' }), { schema_version: 2 }), u2 = Object.assign({}, u1, { payload: { launch_id: 'g', attempt_id: 'g' } });
  const p = build([x, y, u1]), q = build([u2]);
  rec('union(a,a) does not duplicate quarantined versions', CW.union(build([x, y]), build([x, y])).conflicts.same.length === 2);
  rec('union is symmetric with conflicts and several unsupported versions', CW.digest(CW.union(p, q)) === CW.digest(CW.union(q, p)) && CW.union(p, q).unsupported.fut.length === 2); }

console.log(fails ? ('\nFAILED (' + fails + ')') : '\nALL PASS');
process.exit(fails ? 1 : 0);
