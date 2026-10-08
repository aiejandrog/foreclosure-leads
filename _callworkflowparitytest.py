"""JS/Python parity for the call-workflow event contract (Call Workflow spec sections 5 and 8).

call_workflow.js is the reference; call_workflow_events.py is what the localhost notes backup uses to
union the `workflow_v1` envelope on its own. They must agree on canonical bytes, on what a union keeps,
and on what it quarantines. Fake, fictional events only; nothing is read from disk but the two modules.
Also pins the backup rules: a legacy-style push that omits the field must not erase stored events, and
the same id with a different payload is quarantined, never resolved by clock.
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_workflow_events as P

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:200]) if (extra and not ok) else ''))
    if not ok:
        fails.append(name)


def E(i, t='launch_recorded', payload=None, **kw):
    e = {'schema_version': 1, 'event_id': i, 'event_type': t, 'occurred_at_utc': '2026-10-08T13:00:00Z',
         'recorded_at_utc': '2026-10-08T13:00:01Z', 'device_id': 'devA', 'caller_id': 'C1',
         'payload': payload or {'launch_id': 'L' + i, 'attempt_id': 'A' + i}}
    e.update(kw)
    return e


a = [E('e1'), E('e2'), E('e3', payload={'launch_id': 'L3', 'attempt_id': 'A3', 'z': 1, 'a': [1, 2, {'k': 'ü'}]})]
b = [E('e3', payload={'a': [1, 2, {'k': 'ü'}], 'z': 1, 'attempt_id': 'A3', 'launch_id': 'L3'}), E('e4'), E('e2', payload={'launch_id': 'OTHER', 'attempt_id': 'X'})]
future = E('e9', schema_version=2)
bad = {'event_id': 'e10'}

JS = r"""
const CW = require('./call_workflow.js');
const inp = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const mk = l => l.reduce((s, e) => CW.add(s, e), CW.newStore());
const u = CW.union(mk(inp.a), mk(inp.b));
console.log(JSON.stringify({canon: inp.a.map(CW.canon), events: Object.keys(u.byId).sort(),
  conflicts: Object.keys(u.conflicts).sort(), unsupported: Object.keys(u.unsupported).sort()}));
"""
r = subprocess.run(['node', '-e', JS], input=json.dumps({'a': a + [future, bad], 'b': b}), capture_output=True, text=True, cwd=HERE)
rec('node runs', r.returncode == 0, r.stderr)
js = json.loads(r.stdout) if r.returncode == 0 else {}

ua, ub = P.from_list(a + [future, bad]), P.from_list(b)
u = P.union(ua, ub)
rec('canonical bytes match JS', (js.get('canon') or [])[:len(a)] == [P.canon(e) for e in a], js.get('canon'))
rec('kept ids match JS', sorted(u['events']) == js.get('events'), (sorted(u['events']), js.get('events')))
rec('conflicts match JS', sorted(u['conflicts']) == js.get('conflicts'), sorted(u['conflicts']))
rec('unsupported match JS', sorted(u['unsupported']) == js.get('unsupported'))
rec('key order does not change identity (e3 sent two ways is one event)', 'e3' in u['events'] and 'e3' not in u['conflicts'])
rec('same id, different payload: both versions quarantined, neither kept as the winner',
    'e2' not in u['events'] and len(u['conflicts']['e2']) == 2)
rec('malformed input is counted invalid, not stored', u['invalid'] >= 1 and 'e10' not in u['events'])
u2 = P.union(ub, ua)
rec('union is commutative', P.to_envelope(u) == P.to_envelope(u2))
rec('union is idempotent', P.to_envelope(P.union(u, u)) == P.to_envelope(u))
rec('union is associative', P.to_envelope(P.union(P.union(ua, ub), P.from_list([E('e5')])))
    == P.to_envelope(P.union(ua, P.union(ub, P.from_list([E('e5')])))))

# backup rule: a legacy push omits the field entirely -> stored events must survive
stored = P.from_list(a[:2])
omitted, ok = P.from_envelope(None)
rec('a push that omits workflow_v1 reads as empty and readable', omitted == [] and ok)
rec('...and the union with the stored events loses nothing', sorted(P.union(stored, P.from_list(omitted))['events']) == ['e1', 'e2'])
torn, ok = P.from_envelope('{torn')
rec('a non-list field is reported unreadable, never guessed at', torn == [] and ok is False)

# source guard: this module must never touch the legacy arrays or suppression files
src = open(os.path.join(HERE, 'call_workflow_events.py'), encoding='utf-8').read()
code = src.split('"' * 3, 2)[2]          # everything after the module docstring
for banned in ('optouts', 'bounced_emails', 'touches', 'dials', 'worker_notes', 'open('):
    rec('events module never references ' + banned, banned not in code)

print('\n%s (%d failed)' % ('ALL PASS' if not fails else 'FAILED', len(fails)))
sys.exit(1 if fails else 0)
