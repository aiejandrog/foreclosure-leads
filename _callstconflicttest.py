"""Call Mode: the dial card says so when the skip trace and Whitepages disagree about a number.

A number the skip trace tagged to the owner (ps '') that Whitepages lists under another person at the
address (pw 'h') showed no note on the main dial card; the conflict was only a chip in the number list,
which is hidden on a one-number lead. Label only: ranking, dialability and texting are unchanged.
Runs the real _phSrcNote under node, on rows built by the real bake + call_rows (invented data).
Run: python _callstconflicttest.py
"""
import ast, copy, io, json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='stconf_')
os.environ['DEALFLOW_DIR'] = _T
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')
import foreclosure_leads as F  # noqa: E402
import call_mode  # noqa: E402

fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra and not ok else ''))
    if not ok:
        fails.append(name)


tree = ast.parse(Path(F.__file__).read_text(encoding='utf-8'))
build = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'make_tracker')
merge = next(n for n in build.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'os.path.exists(_wpf) or _wp_ids')
rankblk = next(n for n in build.body if isinstance(n, ast.Try) and 'phone_rank' in ast.unparse(n.body[0]))


def row_for(trace, wp):
    lead = {'case': '2026-000777-CA-01', 'owners': 'JOSE GARCIA', 'oname': 'JOSE GARCIA',
            'addr': '100 NW 1st Ave, Miami, FL 33130', 'folio': '0101010101010', 'days': 20,
            'auction': '2026-11-25', 'st': 'FC', 'pkey': 'pk777',
            'phones': trace, 'phtype': ['mobile'] * len(trace), 'phsrc': ['st'] * len(trace), 'phdnc': [False] * len(trace)}
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / 'whitepages_lookup.json'
        p.write_text(json.dumps({lead['case']: wp}), encoding='utf-8')
        env = dict(vars(F), HERE=t, _wpf=str(p), _wp_ids={}, _wp_norm_id=lambda v: v, slim=[lead])
        exec(compile(ast.Module(body=[copy.deepcopy(merge)], type_ignores=[]), '<m>', 'exec'), env)
        exec(compile(ast.Module(body=[copy.deepcopy(rankblk)], type_ignores=[]), '<r>', 'exec'), dict(vars(F), slim=[lead]))
    rows, _ = call_mode.call_rows([lead])
    return rows[0]


def res(name, phones):
    return {'name': name, 'phones': [{'number': n, 'type': 'mobile'} for n in phones]}


WP = {'result': {'apn': '01-0101-010-1010', 'ownership_info': {'person_owners': [res('Jose Garcia', ['3055550129'])]},
                 'residents': [res('Maria Lopez', ['3055550119'])]}}
row = row_for(['3055550101', '3055550119'], WP)
i_conf = row['p'].index('3055550119')
i_ok = row['p'].index('3055550101')
rec('setup: scenario 19 gives pw h and an empty ps on the conflicted number',
    row['pw'][i_conf] == 'h' and not (row.get('ps') or [''] * 9)[i_conf], (row['pw'], row.get('ps')))

PAGE = call_mode._PAGE
a = PAGE.index('function _phSrcNote(')
fn = PAGE[a:PAGE.index('\n}\n', a) + 3]
js = ("function esc(s){return String(s==null?'':s);}\n" + fn
      + "\nvar r=%s;console.log(JSON.stringify([_phSrcNote(r,%d),_phSrcNote(r,%d)]));" % (json.dumps(row), i_conf, i_ok))
p = os.path.join(_T, 'n.js')
io.open(p, 'w', encoding='utf-8').write(js)
q = subprocess.run([os.environ.get('NODE_BIN', 'node'), p], capture_output=True, text=True, timeout=60)
out = json.loads(q.stdout) if q.returncode == 0 and q.stdout.strip() else [None, None]
rec('node ran _phSrcNote', q.returncode == 0, q.stderr[-200:])
rec('conflicted number carries a note on the main card', bool(out[0]), out)
rec('note states the evidence (skip trace vs Whitepages)', bool(out[0]) and 'skip trace' in out[0].lower() and 'Whitepages' in out[0], out)
rec('note does not conclude who they are', bool(out[0]) and 'not the owner' not in out[0].lower(), out)
rec('uncontradicted skip-trace number: still no note', out[1] == '', out)

# nothing else changed: the number stays dialable, in the same position, with no new ps tag
base = row_for(['3055550101', '3055550119'], WP)
rec('ranking and tags unchanged by the label (label is JS-only)', base['p'] == row['p'] and base.get('ps') == row.get('ps'))
# other notes unchanged
tests = [{'ps': ['hh'], 'pw': 'h'}, {'ps': ['nm'], 'pw': 'n'}, {'ps': [''], 'pw': 'w', 'wo': ['X Y']}, {'ps': ['hh'], 'pw': 'o'},
         {'ps': ['hh'], 'pw': 'l'}, {'ps': ['wpm'], 'pw': ''}, {'ps': [''], 'pw': 'o'}, {'ps': [''], 'pw': '-'}, {'ps': [''], 'pw': 'n'}]
js2 = ("function esc(s){return String(s==null?'':s);}\n" + fn + "\nvar T=%s;console.log(JSON.stringify(T.map(function(r){r.on='Jose';return _phSrcNote(r,0);})));" % json.dumps(tests))
io.open(p, 'w', encoding='utf-8').write(js2)
q = subprocess.run([os.environ.get('NODE_BIN', 'node'), p], capture_output=True, text=True, timeout=60)
o2 = json.loads(q.stdout)
rec('hh note unchanged', 'Do not discuss the case' in o2[0])
rec('nm note unchanged', 'NAME only' in o2[1])
rec('w note unchanged', 'as the OWNER' in o2[2])
rec('hh+owner stays silent', o2[3] == '')
rec('likely note unchanged', 'LIKELY the owner' in o2[4])
rec('pasted note unchanged', 'pasted' in o2[5].lower())
rec('owner / not-on-WP / name-only-on-st stay silent', o2[6] == '' and o2[7] == '' and o2[8] == '', o2)
print('\n%d failed' % len(fails) if fails else '\nALL PASS')
sys.exit(1 if fails else 0)
