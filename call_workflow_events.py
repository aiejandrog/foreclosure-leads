"""call_workflow_events.py -- atomic backup validation and union for call-workflow events.

The Python twin of the event rules in call_workflow.js (Call Workflow spec sections 5 and 8). It exists
for one job: the localhost notes backup carries a separate `workflow_v1` envelope, and that field must
be validated and unioned on its own, independent of the legacy note merge.

It only ever touches the new event envelope. It never reads, writes or rewrites a touch, a dial, an
opt-out ledger or any suppression file, and it holds no homeowner data of its own.

  canon(v)            canonical JSON, byte-identical to call_workflow.js canon()
  shape_ok(ev)        minimal gate: schema_version == 1, event_id, known event_type
  union(a, b)         commutative, associative, idempotent by event_id
                      -> {'events': {id: ev}, 'conflicts': {id: [ev, ev]}, 'unsupported': {id: ev}, 'invalid': n}
                      The same id with a different payload is quarantined (both kept), never resolved by clock.
  to_envelope(u)      -> a sorted, JSON-ready list for atomic write
  from_envelope(x)    tolerant read: anything that is not a list of dicts is treated as empty and reported
"""
import json

SCHEMA = 1
TYPES = ('launch_recorded', 'attempt_confirmed', 'launch_cancelled', 'conversation_confirmed',
         'callback_requested', 'callback_rescheduled', 'callback_cancelled', 'callback_completed',
         'appointment_booked', 'appointment_qualified', 'appointment_cancelled', 'session_started',
         'active_interval_confirmed', 'session_paused', 'session_stopped', 'event_retracted',
         'event_corrected')


def canon(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def shape_ok(ev):
    """(ok, unsupported). An unknown future schema is kept (unsupported), not dropped."""
    if not isinstance(ev, dict) or not isinstance(ev.get('event_id'), str) or not ev.get('event_id'):
        return False, False
    if ev.get('schema_version') != SCHEMA:
        sv = ev.get('schema_version')
        return False, isinstance(sv, int) and not isinstance(sv, bool)
    if ev.get('event_type') not in TYPES or not isinstance(ev.get('payload'), dict):
        return False, False
    return True, False


def empty():
    return {'events': {}, 'conflicts': {}, 'unsupported': {}, 'invalid': 0}


def add(u, ev):
    ok, unsupported = shape_ok(ev)
    if not ok:
        if unsupported:
            u['unsupported'][ev['event_id']] = ev
        else:
            u['invalid'] += 1
        return u
    eid = ev['event_id']
    if eid in u['conflicts']:
        if all(canon(x) != canon(ev) for x in u['conflicts'][eid]):
            u['conflicts'][eid].append(ev)
        return u
    have = u['events'].get(eid)
    if have is None:
        u['events'][eid] = ev
    elif canon(have) != canon(ev):
        del u['events'][eid]
        u['conflicts'][eid] = [have, ev]
    return u


def from_envelope(x):
    """-> (events list, readable bool). A torn or non-list field is reported, never guessed at."""
    if x is None:
        return [], True
    if not isinstance(x, list):
        return [], False
    return [e for e in x if isinstance(e, dict)], all(isinstance(e, dict) for e in x)


def union(a, b):
    out = empty()
    for src in (a, b):
        for ev in list(src['events'].values()):
            add(out, ev)
        for evs in src['conflicts'].values():
            for ev in evs:
                add(out, ev)
        out['unsupported'].update(src['unsupported'])
        out['invalid'] = max(out['invalid'], src['invalid'])
    return out


def from_list(events):
    u = empty()
    for ev in events:
        add(u, ev)
    return u


def to_envelope(u):
    evs = [u['events'][k] for k in sorted(u['events'])]
    evs += [e for k in sorted(u['conflicts']) for e in sorted(u['conflicts'][k], key=canon)]
    evs += [u['unsupported'][k] for k in sorted(u['unsupported'])]
    return evs
