"""The two ways a .bat in this repo has actually broken, as checks that run in a second.

WHY THIS EXISTS

1. PARENTHESES INSIDE A BLOCK. cmd.exe parses a whole `if ... ( ... )` block before it runs any
   of it, and an unescaped `)` in the ARGUMENT TEXT of echo/set/title ends the block early. The
   rest of the line is then left in command position.

   On 2026-09-18 the 13:58 refresh died at `[gate]` with "- was unexpected at this time." and exit
   255 -- after a full four-hour enrichment, stranding a finished 2,394-lead board that never
   published. The text was `(&sect;362 stays / sources down)` on lines 454 and 463 of
   refresh-dealflow.bat. It had been harmless since 08-20 and became fatal the day e310696 wrapped
   those messages in parenthesized blocks. Because cmd parses the block before evaluating it, the
   final `[5/5] publish` stage failed on EVERY run from 08-20 until 7b4d61b fixed it on 09-19 --
   a month in which docs/hm-balloon-q7v3n8, staged only in [5/5], never published.

   Nothing caught it because nothing reads these files but cmd, and cmd only reads them at 05:30.

2. AN UNGATED PUBLISH PATH. CLAUDE.md: "every path that publishes the board" runs healthcheck.py
   and publish_guard.py, and `grep -l publish_guard *.bat` is the check. It stayed a manual check,
   so run-phones.bat -- which rebuilds the board, rebases onto main with `-X theirs` and pushes --
   sat ungated for a month after the other four were gated, and was absent from CLAUDE.md's own
   publish table, which is what made it invisible to an audit against that table. On 09-15 an
   ungated path put a 709-phone board over a live 1,148, and because the bad publish became
   origin/main it moved the baseline every later gate compared against.

THE SCANNER IS ITSELF TESTED. The first version written for this passed the whole repo clean while
missing the real bug: it treated a balanced `(...)` on one line as harmless, and cmd does not --
it ends the block on the first unescaped `)`. So the fixture below is the real pre-fix text from
`7b4d61b^`, and a scanner that stops flagging it fails this suite. A clean sweep from an unproven
scanner is worse than no sweep, because it is believed.

Run: python _batsyntaxtest.py
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []
PASS = []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  - ' + str(detail)[:160]) if detail else ''))


# ---- the scanner ------------------------------------------------------------------------------
# A line opens a block when an if/for ends in `(`, or on a `) else (` continuation. Inside a block,
# the argument text of echo/set/title may not carry a bare paren: `^(` / `^)` escape it, and a paren
# inside a double-quoted token is protected by the quotes (which is why `>> "%LOG%"` is fine).
OPENS = re.compile(r'(?:^|[\s&|(])(?:if|for)\b.*\($', re.I)
ELSEOPEN = re.compile(r'^\)?\s*else\b.*\($', re.I)
TEXTCMD = re.compile(r'^(?:@\s*)?(echo|set|title)\b(.*)$', re.I)


def _strip_quoted(s):
    out, inq = [], False
    for ch in s:
        if ch == '"':
            inq = not inq
            out.append(' ')
            continue
        out.append(' ' if inq else ch)
    return ''.join(out)


def scan_lines(lines):
    """-> (findings [(lineno, text)], block depth left open at EOF)."""
    findings, depth = [], 0
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.lower().startswith('rem ') or line.startswith('::'):
            continue
        closes = line.startswith(')')
        opens = bool(OPENS.search(line)) or bool(ELSEOPEN.match(line))
        if depth > 0:
            m = TEXTCMD.match(re.sub(r'^\)\s*', '', line))
            if m:
                arg = _strip_quoted(m.group(2)).replace('^(', ' ').replace('^)', ' ')
                if '(' in arg or ')' in arg:
                    findings.append((n, line))
        if closes:
            depth = max(0, depth - 1)
        if opens:
            depth += 1
    return findings, depth


def scan_file(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return scan_lines(fh.read().splitlines())


# ---- 1. the scanner catches the outage it was written for --------------------------------------
print('\nSCANNER SELF-TEST (the 2026-09-18 gate lines, verbatim from 7b4d61b^)')
BAD = [
    'python -u healthcheck.py >> "%LOG%" 2>&1',
    'if errorlevel 2 (',
    '  echo     ^!^! GATE: healthcheck COMPLIANCE fail (^&sect;362 stays / sources down) - publish SKIPPED.>> "%LOG%"',
    '  goto :end',
    ')',
]
bad_hits, bad_depth = scan_lines(BAD)
rec('flags the real pre-fix gate line', [n for n, _ in bad_hits] == [3], bad_hits)

GOOD = list(BAD)
GOOD[2] = GOOD[2].replace('(^&sect;362 stays / sources down)', '^(^&sect;362 stays / sources down^)')
good_hits, good_depth = scan_lines(GOOD)
rec('accepts the fix that shipped as 7b4d61b', good_hits == [], good_hits)
rec('the block closes in both', bad_depth == 0 and good_depth == 0, (bad_depth, good_depth))

# The protections the scanner must NOT flag, or it cries wolf and gets deleted.
rec('a quoted paren is protected by its quotes',
    scan_lines(['if errorlevel 1 (', '  echo msg>> "C:\\dir (x)\\log.txt"', ')'])[0] == [])
rec('a paren at top level is nobody\'s problem',
    scan_lines(['echo plain (parenthesised) text'])[0] == [])
rec('a nested single-line if is code, not text',
    scan_lines(['if errorlevel 1 (', '  if exist x.json (echo found & pause)', ')'])[0] == [])
rec('an unclosed block is reported as depth',
    scan_lines(['if errorlevel 1 (', '  echo oops'])[1] == 1)

# ---- 2. every .bat in the repo, swept ----------------------------------------------------------
print('\nEVERY .BAT ON DISK')
bats = sorted(glob.glob(os.path.join(HERE, '*.bat')))
rec('found the runners to scan', len(bats) >= 10, '%d files' % len(bats))
for path in bats:
    name = os.path.basename(path)
    hits, depth = scan_file(path)
    rec('%s: no bare paren in a block' % name, hits == [],
        '; '.join('L%d %s' % (n, t[:70]) for n, t in hits))
    rec('%s: every block closes' % name, depth == 0, 'depth %d at EOF' % depth)

# ---- 3. no publish path is ungated -------------------------------------------------------------
# CLAUDE.md, "Publish gates - never bypass": anything that does `git add docs/` and pushes, and is
# not in `grep -l publish_guard *.bat`, is a hole. That sentence is the check; this makes it run.
print('\nPUBLISH PATHS ARE GATED')
for path in bats:
    name = os.path.basename(path)
    body = open(path, encoding='utf-8', errors='replace').read()
    # strip rem/:: comments first: a comment mentioning publish_guard is not a call to it
    code = '\n'.join(l for l in body.splitlines()
                     if not l.strip().lower().startswith('rem ') and not l.strip().startswith('::'))
    stages = bool(re.search(r'git\s+add\b[^\n]*docs', code))
    pushes = bool(re.search(r'git\s+push\b', code))
    if not (stages and pushes):
        continue
    rec('%s publishes, so it runs healthcheck.py' % name, 'healthcheck.py' in code)
    rec('%s publishes, so it runs publish_guard.py' % name, 'publish_guard.py' in code)
    rec('%s publishes, so it calls repo_guard.bat' % name,
        bool(re.search(r'call\s+repo_guard\.bat', code, re.I)))

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
