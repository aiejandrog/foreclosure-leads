"""_ledgerownertest -- ONE writer of the do-not-contact list (decided 2026-09-26).

DECISION: Foreclosure Bot's opt-out pipeline -- the 07:15 sync (morning_sync.py: replies.py's inbox
scan -> optout_sync.py -> ledger_sync.py's add-only union) plus optout_sync.ledger_add -- is the
ONLY code allowed to write optouts.json. Everything else (the send bridge, the board build, the
workers, the sync gate itself) reads it. A second writer is how a list goes wrong quietly: two
copies, two formats, one of them non-atomic, and nobody can say which one the bridge read.

This test reads every tracked, non-test .py file, finds each place that WRITES optouts.json
(open(...,'w'/'a'/'x'/'+'), os.replace/rename/shutil.copy/move onto it, Path.write_text/bytes,
and the repo's own json-save helpers), following names per function so `tmp = OPTOUTS + '.tmp'`
in one function does not taint a `tmp` in another, and fails if a module outside ALLOWED writes it.

    ALLOWED            the pipeline: optout_sync.py (the sync + ledger_add), ledger_sync.py (the
                       add-only union, the sync's last step), replies.py (the sync's first step; on
                       PR #70 it only stamps optout_verified and routes STOPs through ledger_add).
    LEGACY_PENDING_70  cadence.py still writes the ledger itself on main. PR #70 routes it through
                       optout_sync.ledger_add. Tolerated with a WARNING until #70 lands; once cadence
                       stops writing, the entry is stale and this test says to delete it.

It also proves the detector works (a synthetic writer in each shape is caught; readers are not)
and that the 07:15 sync's own new code (morning_sync.py, sync_gate.py) never writes the list.

    python _ledgerownertest.py
"""
import ast
import os
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = pathlib.Path(__file__).resolve().parent
TARGET = 'optouts.json'
ALLOWED = {'optout_sync.py', 'ledger_sync.py', 'replies.py'}
LEGACY_PENDING_70 = {'cadence.py'}
SAVE_HELPERS = {'_save', 'save_json', '_save_json', 'write_json', '_write_json', 'atomic_write',
                '_atomic_write', 'write_if_changed', 'dump_json', '_dump_json'}
ok, bad, warn = [], [], []


def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)[:240]) if d else ''))


PATH_CALLS = {'join', 'Path', 'PurePath', 'PosixPath', 'WindowsPath', 'fspath', 'abspath', 'realpath',
              'normpath', 'expanduser', 'str', 'with_suffix', 'with_name', 'joinpath', 'resolve', 'format'}


def _mentions(node, names):
    """Does this expression evaluate to the ledger's PATH? Follows path building (joins, '+ .tmp',
    f-strings, Path(), tuples of names) but NOT data read out of the file, so `opt = json.load(...)`
    does not make everything derived from the ledger's contents look like the ledger itself."""
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and TARGET in node.value
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr in names
    if isinstance(node, ast.BinOp):
        return _mentions(node.left, names) or _mentions(node.right, names)
    if isinstance(node, ast.JoinedStr):
        return any(_mentions(v.value if isinstance(v, ast.FormattedValue) else v, names) for v in node.values)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_mentions(e, names) for e in node.elts)
    if isinstance(node, ast.Subscript):
        return _mentions(node.value, names)
    if isinstance(node, ast.IfExp):
        return _mentions(node.body, names) or _mentions(node.orelse, names)
    if isinstance(node, ast.Starred):
        return _mentions(node.value, names)
    if isinstance(node, ast.Call):
        fn = node.func
        fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else '')
        if fname in PATH_CALLS:
            return (any(_mentions(a, names) for a in node.args)
                    or (isinstance(fn, ast.Attribute) and _mentions(fn.value, names)))
    return False


def _own_nodes(scope):
    """Nodes in this scope, not descending into nested function/class bodies."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            todo.extend(ast.iter_child_nodes(n))


def _taint(scope, names, attrs=None):
    names = set(names)
    for _ in range(4):                                  # to a fixed point, name -> name -> name
        before = len(names)
        for n in _own_nodes(scope):
            if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                value = n.value
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if value is None:
                    continue
                for t in targets:
                    if isinstance(t, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
                            and len(t.elts) == len(value.elts):
                        pairs = zip(t.elts, value.elts)
                    else:
                        pairs = [(t, value)]
                    for tt, vv in pairs:
                        if _mentions(vv, names):
                            for x in ast.walk(tt):
                                if isinstance(x, ast.Name):
                                    names.add(x.id)
                                elif isinstance(x, ast.Attribute):
                                    names.add(x.attr)
                                    if attrs is not None:       # self.path = ... is seen by every method
                                        attrs.add(x.attr)
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)) and _mentions(n.iter, names):
                for x in ast.walk(n.target):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
            elif isinstance(n, ast.withitem) and n.optional_vars is not None and _mentions(n.context_expr, names):
                for x in ast.walk(n.optional_vars):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        if len(names) == before:
            break
    return names


def _writes(scope, names):
    hits = []
    for n in _own_nodes(scope):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        fname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else '')
        if fname == 'open' and n.args:
            mode = None
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                mode = n.args[1].value
            for k in n.keywords:
                if k.arg == 'mode' and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if isinstance(fn, ast.Name) or (isinstance(fn.value, ast.Name) and fn.value.id in ('io', 'codecs', 'builtins')):
                target = n.args[0]                                      # open(PATH, 'w')
            else:                                                       # Path(...).open('w')
                target = fn.value
                mode = n.args[0].value if isinstance(n.args[0], ast.Constant) else mode
            if target is not None and mode and any(c in str(mode) for c in 'wax+') and _mentions(target, names):
                hits.append((n.lineno, 'open(%s)' % mode))
        elif fname in ('replace', 'rename', 'renames', 'copy', 'copy2', 'copyfile', 'move', 'link', 'symlink') \
                and len(n.args) >= 2 and _mentions(n.args[1], names) \
                and (isinstance(fn, ast.Name) or (isinstance(fn.value, ast.Name) and fn.value.id.lstrip('_') in ('os', 'shutil'))):
            hits.append((n.lineno, fname))
        elif fname in ('replace', 'rename') and isinstance(fn, ast.Attribute) and len(n.args) == 1 \
                and not isinstance(fn.value, ast.Constant) \
                and _mentions(n.args[0], names):                           # Path(tmp).replace(OPTOUTS)
            hits.append((n.lineno, fname))
        elif fname in ('write_text', 'write_bytes') and isinstance(fn, ast.Attribute) and _mentions(fn.value, names):
            hits.append((n.lineno, fname))
        elif fname in SAVE_HELPERS and n.args and _mentions(n.args[0], names):
            hits.append((n.lineno, fname))
    return hits


def writers_in_source(src):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        tree = ast.parse(src)
    attrs = set()
    hits = []
    for _pass in range(2):                  # pass 1 learns attribute names, pass 2 uses them everywhere
        hits = []
        mod_names = _taint(tree, set(attrs), attrs)
        hits.extend(_writes(tree, mod_names))

        def visit(scope, inherited):
            for n in _own_nodes(scope):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    names = _taint(n, inherited | attrs, attrs)
                    hits.extend(_writes(n, names))
                    visit(n, names)
        visit(tree, mod_names)
    return sorted(set(hits))


def tracked_modules(root):
    out = subprocess.run(['git', '-C', str(root), 'ls-files', '*.py'], capture_output=True, text=True).stdout.split()
    return [f for f in out
            if not os.path.basename(f).startswith(('_', 'test_'))   # tests write fixtures in temp dirs
            and not f.startswith(('tests/', 'desktop-setup/tests/'))]


def scan(root):
    found = {}
    for f in tracked_modules(root):
        try:
            src = (root / f).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if TARGET not in src:
            continue
        try:
            h = writers_in_source(src)
        except SyntaxError as e:
            found[f] = [(0, 'UNPARSEABLE: %s' % e)]             # cannot prove it does not write
            continue
        if h:
            found[f] = h
    return found


# ---- 1. the detector itself
SYN = {
    'open w':        "OPT = 'optouts.json'\ndef f(d):\n    open(OPT, 'w').write('{}')\n",
    'open a kw':     "import os\nP = os.path.join('x', 'optouts.json')\ndef f():\n    with open(P, mode='a') as fh: fh.write('')\n",
    'tmp + replace': "import os, json\nL = 'optouts.json'\ndef f(o):\n    tmp = L + '.tmp'\n    json.dump(o, open(tmp, 'w'))\n    os.replace(tmp, L)\n",
    'path write':    "import pathlib\ndef f():\n    pathlib.Path('optouts.json').write_text('{}')\n",
    'path open w':   "import pathlib\nP = pathlib.Path('optouts.json')\ndef f():\n    with P.open('w') as fh: fh.write('')\n",
    'shutil copy':   "import shutil\ndef f(src):\n    shutil.copy2(src, 'optouts.json')\n",
    'save helper':   "LEDGER = 'optouts.json'\ndef f(o):\n    _save_json(LEDGER, o)\n",
    'loop var':      "import os\nFILES = ('optouts.json', 'mail_sent.json')\ndef f(o):\n    for n in FILES:\n        p = os.path.join('d', n)\n        write_if_changed(p, o, None)\n",
    'self attr':     "class S:\n    def __init__(self):\n        self.path = 'optouts.json'\n    def save(self):\n        open(self.path, 'w').write('')\n",
}
for k, src in SYN.items():
    rec('detector catches a writer: %s' % k, bool(writers_in_source(src)), writers_in_source(src))
READERS = {
    'read':           "import json\ndef f():\n    return json.load(open('optouts.json', encoding='utf-8'))\n",
    'read r mode':    "def f():\n    return open('optouts.json', 'r').read()\n",
    'mtime':          "import os\ndef f():\n    return os.path.getmtime('optouts.json')\n",
    'other fn tmp':   "import os\nOPT = 'optouts.json'\nSTATE = 'state.json'\ndef a():\n    tmp = OPT + '.tmp'\n    return tmp\ndef b(s):\n    tmp = STATE + '.tmp'\n    open(tmp, 'w').write(s)\n    os.replace(tmp, STATE)\n",
    'unrelated file': "def f(p):\n    open('Carlos_Letters.html', 'w').write(p)\n",
    'str.replace':    "import json\nOPT = 'optouts.json'\ndef f(tpl):\n    opt = json.load(open(OPT))\n    n = len(opt)\n    return tpl.replace('__N__', str(n)).replace('__O__', OPT)\n",
    'ledger data':    "import json\nOPT = 'optouts.json'\ndef f():\n    opt = json.load(open(OPT))\n    out = 'board.html'\n    open(out, 'w').write(json.dumps(opt))\n",
}
for k, src in READERS.items():
    rec('detector ignores a reader: %s' % k, not writers_in_source(src), writers_in_source(src))

# ---- 2. the repo
found = scan(HERE)
print('  writers of %s found: %s' % (TARGET, ', '.join('%s@%s' % (f, ','.join(str(l) for l, _ in h)) for f, h in sorted(found.items())) or 'none'))
rogue = {f: h for f, h in found.items() if f not in ALLOWED and f not in LEGACY_PENDING_70}
rec('no module outside the opt-out pipeline writes %s' % TARGET, not rogue,
    'NEW WRITER(S): %s -- route it through optout_sync.ledger_add instead (decision 2026-09-26)' % rogue if rogue else '')
rec('the pipeline is found writing it (the detector is live on this tree)', 'optout_sync.py' in found, sorted(found))
for f in sorted(LEGACY_PENDING_70):
    if f in found:
        warn.append('%s still writes %s directly (lines %s); PR #70 routes it through optout_sync.ledger_add'
                    % (f, TARGET, ','.join(str(l) for l, _ in found[f])))
    else:
        warn.append('%s no longer writes %s -- delete it from LEGACY_PENDING_70 so it cannot start again' % (f, TARGET))
for f in ('morning_sync.py', 'sync_gate.py', 'send_server.py'):
    if (HERE / f).exists():
        rec('%s never writes %s' % (f, TARGET), f not in found, found.get(f))
rec('ALLOWED and LEGACY are disjoint and name real files',
    not (ALLOWED & LEGACY_PENDING_70) and all((HERE / f).exists() for f in ALLOWED))

for w in warn:
    print('  WARN ' + w)
print('\n==== %d/%d ledger-owner checks passed%s ====' % (len(ok), len(ok) + len(bad), (' (%d warning)' % len(warn)) if warn else ''))
sys.exit(1 if bad else 0)
