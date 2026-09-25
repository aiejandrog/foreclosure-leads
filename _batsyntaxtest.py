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

2. TWO RUNNERS PUBLISHING AT ONCE. Five .bat files rebuild `docs/` and push it, and until
   2026-09-22 not one took a lock: the only thing keeping two of them apart was the clock on their
   Task Scheduler triggers. On 2026-09-15 at 19:11 run-replies-daily.bat published 709 phones over
   a live 1,148 and run-phones-nightly.bat published 714 over the same board ONE MINUTE LATER, and
   the poorer build became origin/main, moving the baseline every later publish_guard compared
   against. `publish_lock.py` is the mechanism; this suite is what keeps it wired. A lock acquired
   and not released on some exit path is worse than no lock - it wedges the machine until a human
   deletes a dotfile - so the check here is not "does it call acquire" but "does every exit below
   the acquire funnel through the release".

3. AN UNGATED PUBLISH PATH. CLAUDE.md: "every path that publishes the board" runs healthcheck.py
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
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import publish_lock as PL

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

# ---- 4. every publish path takes the lock, and releases it on EVERY exit path -------------------
# The acquire is the easy half. The half that has actually gone wrong in guards in this repo is the
# release: run-phones-nightly.bat had seven `exit /b` sites and run-phones.bat six, so a release
# bolted onto the happy path alone would have left the lock behind on every rebuild failure, every
# blocked gate and every "nothing to commit" night - and the next morning's run would then refuse
# for six hours until the stale budget aged it out. The invariant below is what forbids that:
# exactly one release, and every exit under the acquire is either the rc=9 refusal or below it.
def lock_wiring(name, code_lines):
    """-> [(label, ok, detail)] for one runner's lock wiring. A function, not inline, so the
    fixtures below can prove it actually fails - the same discipline as the paren scanner above."""
    out = []
    acq = [i for i, l in enumerate(code_lines) if re.search(r'publish_lock\.py\s+acquire\b', l)]
    rel = [i for i, l in enumerate(code_lines) if re.search(r'publish_lock\.py\s+release\b', l)]
    out.append(('%s acquires the publish lock' % name, len(acq) == 1, '%d acquire calls' % len(acq)))
    out.append(('%s releases it exactly once' % name, len(rel) == 1, '%d release calls' % len(rel)))
    if len(acq) != 1 or len(rel) != 1:
        return out
    ai, ri = acq[0], rel[0]
    # the name it passes must be its OWN name, or release matches no holder and the lock is permanent
    for verb, i in (('acquire', ai), ('release', ri)):
        out.append(('%s passes its own filename to %s' % (name, verb),
                    re.search(r'publish_lock\.py\s+%s\s+%s(\s|$)' % (verb, re.escape(name)),
                              code_lines[i]) is not None, code_lines[i].strip()[:90]))
    out.append(('%s releases AFTER it acquires' % name, ri > ai,
                'acquire line %d, release line %d' % (ai, ri)))
    # the refusal: rc=9, and it must NOT release - the lock belongs to the other runner. The
    # refusal path may jump to a label rather than exiting inline: run-replies-daily.bat takes the
    # lock below its inbox scan, so a held lock there is a degraded publish-skip rather than a dead
    # run, and it lands on :nolock. Follow one hop so the check reads the path, not just the branch.
    refusal_label = None
    refusal_lines = list(code_lines[ai:ai + 8])
    hop = re.search(r'goto\s+:(\w+)', '\n'.join(refusal_lines))
    if hop and not re.search(r'exit /b 9\b', '\n'.join(refusal_lines)):
        refusal_label = label = ':' + hop.group(1)
        at = [i for i, l in enumerate(code_lines) if l.strip().lower() == label.lower()]
        if at:
            for l in code_lines[at[0]:]:
                refusal_lines.append(l)
                if 'exit /b' in l:
                    break
    refusal = '\n'.join(refusal_lines)
    out.append(('%s refuses with the documented rc=9' % name,
                re.search(r'exit /b 9\b', refusal) is not None,
                refusal.replace('\n', ' / ')[:120]))
    out.append(('%s does not release a lock it failed to take' % name,
                'publish_lock.py release' not in refusal, ''))
    # THE INVARIANT: every exit between the acquire and the release is the rc=9 refusal
    stranded = []
    for i in range(ai + 1, ri + 1):
        for m in re.finditer(r'exit /b\s*(\S*)', code_lines[i]):
            if m.group(1).strip(')').strip() != '9':
                stranded.append('L%d %s' % (i, code_lines[i].strip()[:70]))
    out.append(('%s leaves no exit path that skips the release' % name, stranded == [],
                '; '.join(stranded)))
    # ...AND every `goto` between them lands at or above the release. Greptile's 2026-09-25 point:
    # the scan above reads literal `exit /b` lines, so a future branch could `goto :somewhere` that
    # sits BELOW the release, exit there, and pass a check whose whole job is to forbid exactly
    # that. cmd.exe does not care that the jump is indirect and neither does the lock: the file
    # stays on disk and the next runner refuses until the six-hour budget breaks it. The one
    # legitimate jump past the release is the rc=9 refusal, which must not release, and a `goto` to
    # a label that does not exist is its own bug - cmd.exe falls through to the end of the file.
    jumps = []
    for i in range(ai + 1, ri):
        for m in re.finditer(r'goto\s+:?(\w+)', code_lines[i]):
            label = ':' + m.group(1)
            if refusal_label and label.lower() == refusal_label.lower():
                continue
            at = [j for j, l in enumerate(code_lines) if l.strip().lower() == label.lower()]
            if not at:
                jumps.append('L%d %s -> no such label' % (i, label))
            elif at[0] > ri:
                jumps.append('L%d %s -> L%d, past the release at L%d' % (i, label, at[0], ri))
    out.append(('%s has no goto that jumps past the release' % name, jumps == [],
                '; '.join(jumps)))
    # ...and it NEVER breaks the lock itself. `publish_lock.py break` is the procedure a human runs
    # on a lock nothing will release; a publish path that can break its way past the guard does not
    # have a guard. Confirmed needed on 2026-09-25: a holder whose cmd.exe dies leaves the lock, and
    # the tempting "fix" is for the next runner to clear it, which is the whole race back again.
    out.append(('%s never breaks the lock itself' % name,
                not any(re.search(r'publish_lock\.py\s+break', l) for l in code_lines),
                'break is for a human, not a runner'))
    return out


def bat_code_lines(body):
    return [l for l in body.splitlines()
            if not l.strip().lower().startswith('rem ') and not l.strip().startswith('::')]


print('\nPUBLISH LOCK IS WIRED INTO EVERY PUBLISH PATH')
publishers = []
for path in bats:
    name = os.path.basename(path)
    code_lines = bat_code_lines(open(path, encoding='utf-8', errors='replace').read())
    code = '\n'.join(code_lines)
    if not (re.search(r'git\s+add\b[^\n]*docs', code) and re.search(r'git\s+push\b', code)):
        continue
    publishers.append(name)
    for label, ok, detail in lock_wiring(name, code_lines):
        rec(label, ok, detail)

rec('all five known publishers were found', len(publishers) == 5, publishers)
rec('publish_lock.PUBLISHERS matches what is on disk',
    sorted(PL.PUBLISHERS) == sorted(publishers),
    'module says %s' % (sorted(PL.PUBLISHERS),))

# The wiring check has to FAIL on the two mistakes it exists to prevent, or it is decoration.
print('\nWIRING-CHECK SELF-TEST')
GOOD = ['call repo_guard.bat "%~dp0" "x.log"',
        'if errorlevel 1 exit /b 1',
        'python -u publish_lock.py acquire x.bat >> "x.log" 2>&1',
        'if errorlevel 1 (',
        '  echo held', '  exit /b 9', ')',
        'python build.py',
        'if errorlevel 1 (set "NEXIT=1" & goto :end)',
        'git add docs/index.html', 'git push origin main',
        ':end',
        'python -u publish_lock.py release x.bat >> "x.log" 2>&1',
        'exit /b %NEXIT%']
rec('a correctly wired runner passes', all(ok for _, ok, _ in lock_wiring('x.bat', GOOD)),
    [l for l, ok, _ in lock_wiring('x.bat', GOOD) if not ok])

HOP = ['call repo_guard.bat "%~dp0" "x.log"',
       'python scan.py',
       'python -u publish_lock.py acquire x.bat >> "x.log" 2>&1',
       'if errorlevel 1 (', '  echo held', '  goto :nolock', ')',
       'git add docs/index.html', 'git push origin main',
       ':end',
       'python -u publish_lock.py release x.bat >> "x.log" 2>&1',
       'exit /b 0',
       ':nolock', 'exit /b 9']
rec('a refusal that jumps to a label is followed, not failed',
    all(ok for _, ok, _ in lock_wiring('x.bat', HOP)),
    [l for l, ok, _ in lock_wiring('x.bat', HOP) if not ok])

HOPBAD = list(HOP)
HOPBAD[13] = 'exit /b 0'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', HOPBAD))
rec('a refusal label that does not exit 9 is caught',
    bad['x.bat refuses with the documented rc=9'] is False,
    'rc=9 is the code the runners and the log reader both key on')

HOPRELEASE = list(HOP)
HOPRELEASE[13] = 'python -u publish_lock.py release x.bat & exit /b 9'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', HOPRELEASE))
rec('a refusal label that drops the holder\'s lock is caught',
    bad.get('x.bat does not release a lock it failed to take', True) is False
    or bad.get('x.bat releases it exactly once', True) is False)

STRANDED = list(GOOD)
STRANDED[8] = 'if errorlevel 1 (echo build failed & exit /b 1)'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', STRANDED))
rec('an exit that skips the release is caught',
    bad['x.bat leaves no exit path that skips the release'] is False,
    'this is the mistake that wedges the machine for six hours')

WRONGNAME = list(GOOD)
WRONGNAME[12] = 'python -u publish_lock.py release y.bat >> "x.log" 2>&1'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', WRONGNAME))
rec('a release under the wrong runner name is caught',
    bad['x.bat passes its own filename to release'] is False,
    'release matches on the holder name, so a copied line releases nothing')

RELEASESFIRST = list(GOOD)
RELEASESFIRST[5] = '  python -u publish_lock.py release x.bat & exit /b 9'
res = lock_wiring('x.bat', RELEASESFIRST)
bad = dict((l, ok) for l, ok, _ in res)
rec('a refusal that drops the other runner\'s lock is caught',
    bad.get('x.bat does not release a lock it failed to take', True) is False
    or bad.get('x.bat releases it exactly once', True) is False,
    'rc=9 means the lock is someone else\'s')

STRANDEDJUMP = list(GOOD)
STRANDEDJUMP[8] = 'if errorlevel 1 (set "NEXIT=1" & goto :bail)'
STRANDEDJUMP = STRANDEDJUMP + [':bail', 'exit /b 1']
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', STRANDEDJUMP))
rec('a goto that jumps past the release is caught',
    bad['x.bat has no goto that jumps past the release'] is False,
    'the exit is at :bail, so scanning for a literal `exit /b` between the two lines misses it')

GHOSTJUMP = list(GOOD)
GHOSTJUMP[8] = 'if errorlevel 1 (set "NEXIT=1" & goto :finish)'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', GHOSTJUMP))
rec('a goto to a label that does not exist is caught',
    bad['x.bat has no goto that jumps past the release'] is False,
    'cmd.exe falls through to the end of the file, so the release never runs')

rec('the legitimate jump to the refusal label is still allowed',
    dict((l, ok) for l, ok, _ in lock_wiring('x.bat', HOP))['x.bat has no goto that jumps past the release'],
    'run-replies-daily.bat:nolock sits below the release on purpose and must not release')

# ---- 4b. what the refusal PATHS tell a human (review 2026-09-25) -----------------------------
# rc=9 is "the lock was not obtained" and covers two causes the .bat cannot tell apart. A refusal
# line that names contention sends whoever reads the one unattended signal hunting a run log for a
# run that never existed - the same defect the headers carried and 13a120c fixed.
print('\nTHE REFUSAL PATHS DO NOT GUESS A CAUSE')
for _name in ('refresh-dealflow.bat', 'run-leads.bat', 'run-phones-nightly.bat',
              'run-replies-daily.bat', 'run-phones.bat'):
    _text = io.open(os.path.join(HERE, _name), encoding='utf-8').read()
    _echoes = [l.strip() for l in _text.splitlines()
               if not l.strip().lower().startswith('rem ')
               and ('echo' in l.lower() and ('REFUSED' in l or 'PUBLISH LOCK' in l))]
    rec('%s refusal output names no cause rc=9 cannot know' % _name,
        [l for l in _echoes if 'mid-run' in l or 'lock held' in l] == [],
        [l[:80] for l in _echoes if 'mid-run' in l or 'lock held' in l])

# refresh-dealflow.bat is the only runner whose unattended signal is written 650 lines below the
# acquire, so its refusal has to write that signal itself or the Desktop keeps yesterday's OK for a
# morning on which nothing ran. run-phones-nightly.bat already writes its %STATUS% on refusal.
_ref = io.open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8').read()
_head = _ref[:_ref.index('publish_lock.py acquire') + 1200]
rec('refresh-dealflow.bat refusal writes the Desktop status itself',
    'run_report.py --refused' in _head,
    'DEALFLOW-STATUS.txt is the only unattended signal and run_report is its only writer')
_rr = io.open(os.path.join(HERE, 'run_report.py'), encoding='utf-8').read()
rec('and run_report.py has that mode', "'--refused' in sys.argv" in _rr)
rec('and it claims no counts for a run that never started',
    'DID NOT RUN' in _rr and 'No counts are reported here' in _rr)

BREAKER = list(GOOD)
BREAKER[3] = 'if errorlevel 1 (python -u publish_lock.py break --force & goto :end)'
bad = dict((l, ok) for l, ok, _ in lock_wiring('x.bat', BREAKER))
rec('a runner that breaks its way past the lock is caught',
    bad['x.bat never breaks the lock itself'] is False,
    'that is the 2026-09-15 race with an extra step, not a fix for it')

# ---- 5. the lock itself does what the runners assume ------------------------------------------
# No network, no cmd.exe: this drives publish_lock.py directly. A wiring check over a lock that
# does not actually exclude anything is the "clean sweep from an unproven scanner" this file's
# header warns about.
print('\nTHE LOCK ACTUALLY EXCLUDES')
tmp = tempfile.mkdtemp(prefix='dealflow-lock-')
try:
    real_lock, PL.LOCK = PL.LOCK, os.path.join(tmp, '.publish.lock')

    rec('a free lock is acquired', PL.acquire('refresh-dealflow.bat') == 0)
    rec('a second runner is refused with 9', PL.acquire('run-replies-daily.bat') == 9)
    rec('the same runner twice is refused too', PL.acquire('refresh-dealflow.bat') == 9,
        'a double-click while the task is running is the common case')
    rec('a non-owner release leaves the lock alone',
        PL.release('run-replies-daily.bat') == 0 and os.path.exists(PL.LOCK))
    rec('the owner releases it', PL.release('refresh-dealflow.bat') == 0
        and not os.path.exists(PL.LOCK))
    rec('releasing twice is not an error', PL.release('refresh-dealflow.bat') == 0)
    rec('and the lock is free again', PL.acquire('run-leads.bat') == 0)
    PL.release('run-leads.bat')

    # stale: a runner killed mid-flight must not wedge the machine forever
    held = {'runner': 'run-phones-nightly.bat', 'pid': 1, 'host': 'gone',
            'started_at': 'earlier', 'started_epoch': time.time() - (PL.STALE_AFTER + 60)}
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(held))
    rec('a lock past the %s budget is broken' % PL._ago(PL.STALE_AFTER),
        PL.acquire('refresh-dealflow.bat') == 0
        and json.load(io.open(PL.LOCK, encoding='utf-8'))['runner'] == 'refresh-dealflow.bat')
    PL.release('refresh-dealflow.bat')

    held['started_epoch'] = time.time() - (PL.STALE_AFTER - 600)
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(held))
    rec('a lock just inside the budget is still honoured',
        PL.acquire('refresh-dealflow.bat') == 9,
        'the 3h08m refresh chain must not have its lock stolen at hour five')
    os.remove(PL.LOCK)

    # a corrupt lock is a LOCK, not an absence. "I cannot read it so I will ignore it" is how a
    # guard becomes a no-op.
    io.open(PL.LOCK, 'w', encoding='utf-8').write('not json')
    rec('an unreadable lock still refuses', PL.acquire('run-leads.bat') == 9)
    os.utime(PL.LOCK, (time.time() - PL.STALE_AFTER - 60,) * 2)
    rec('an unreadable lock is aged off its mtime', PL.acquire('run-leads.bat') == 0)
    PL.release('run-leads.bat')

    rec('the stale budget is the longest runner budget, 6h',
        PL.STALE_AFTER == 6 * 60 * 60, '%s - DEALFLOW Refresh carries ExecutionTimeLimit PT6H'
        % PL._ago(PL.STALE_AFTER))
    rec('a mistyped verb fails CLOSED', PL.main(['publish_lock.py', 'aquire', 'x.bat']) == 9,
        'a caller that cannot spell the verb must not sail past the guard')

    # and the create is genuinely atomic: copy the module out and race eight processes at it
    shutil.copy(os.path.join(HERE, 'publish_lock.py'), tmp)
    procs = [subprocess.Popen([sys.executable, os.path.join(tmp, 'publish_lock.py'),
                               'acquire', 'racer%d.bat' % i],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for i in range(8)]
    codes = [p.wait() for p in procs]
    rec('eight runners racing a free lock: exactly one wins',
        codes.count(0) == 1 and codes.count(9) == 7, codes)
    os.remove(os.path.join(tmp, '.publish.lock'))

    io.open(os.path.join(tmp, '.publish.lock'), 'w', encoding='utf-8').write(json.dumps(
        {'runner': 'dead.bat', 'pid': 1, 'host': 'gone', 'started_at': 'earlier',
         'started_epoch': time.time() - (PL.STALE_AFTER + 60)}))
    procs = [subprocess.Popen([sys.executable, os.path.join(tmp, 'publish_lock.py'),
                               'acquire', 'racer%d.bat' % i],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for i in range(8)]
    codes = [p.wait() for p in procs]
    rec('eight runners racing one STALE lock: exactly one wins',
        codes.count(0) == 1 and codes.count(9) == 7,
        '%s - breaking by rename, not by delete, is what makes this hold' % (codes,))
    rec('breaking a stale lock leaves no litter behind',
        [f for f in os.listdir(tmp) if '.stale.' in f] == [],
        [f for f in os.listdir(tmp) if '.stale.' in f])

    # -- a stale break must not carry off a FRESH lock (Greptile P1, 2026-09-25) --
    # The race: runner A reads the lock and judges it stale; its holder releases and runner C takes
    # a fresh one; A's rename then moves C's live lock aside and A acquires alongside C. Both
    # publish, which is the whole failure this file exists to stop. It is reproduced here by
    # swapping the file underneath the break, which is what that interleaving amounts to.
    stale = {'runner': 'run-phones-nightly.bat', 'pid': 1, 'ppid': 2, 'host': 'H',
             'started_at': 'earlier', 'started_epoch': time.time() - (PL.STALE_AFTER + 60)}
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(stale))
    sig = PL._identity(PL._raw_lock())
    fresh = dict(stale, runner='run-leads.bat', pid=3, ppid=4, started_epoch=time.time())
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(fresh))
    broke = PL._break_stale(stale, PL.STALE_AFTER + 60, sig)
    rec('a stale break refuses once the lock has been replaced', broke is False,
        'rename is an interlock over WHO breaks, not over WHICH file it moved')
    rec('and the fresh lock is still there afterwards',
        os.path.exists(PL.LOCK)
        and json.load(io.open(PL.LOCK, encoding='utf-8'))['runner'] == 'run-leads.bat',
        'restored by an O_EXCL create, so it can never overwrite a newer holder')
    rec('so the runner that lost the break is refused',
        PL.acquire('run-phones-nightly.bat') == 9)
    rec('and no .stale litter is left by the aborted break',
        [f for f in os.listdir(tmp) if '.stale.' in f] == [],
        [f for f in os.listdir(tmp) if '.stale.' in f])
    os.remove(PL.LOCK)

    # -- release must not drop a DIFFERENT run of the same runner's lock (Greptile P1) --
    # The hole this file shipped documented: one run overruns six hours, a second run of the SAME
    # .bat breaks its stale lock and takes a fresh one, and then the first one's release removes
    # the second one's live lock. Matching on the runner filename alone cannot tell them apart;
    # the recorded ppid - the cmd.exe that ran the .bat - can.
    rec('the lock records the cmd.exe that ran the runner, not just the runner',
        PL.acquire('run-phones.bat') == 0
        and json.load(io.open(PL.LOCK, encoding='utf-8'))['ppid'] == os.getppid())
    other = json.load(io.open(PL.LOCK, encoding='utf-8'))
    other['ppid'] = os.getppid() + 90001          # a different run of the same file
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(other))
    rec('an earlier run will not release the replacement lock',
        PL.release('run-phones.bat') == 0 and os.path.exists(PL.LOCK),
        'same filename, different run - this is the hole Greptile refused')
    rec('and a third publisher is still shut out',
        PL.acquire('refresh-dealflow.bat') == 9)
    del other['ppid']                              # a lock written before this change
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(other))
    rec('a lock with no ppid falls back to the filename match',
        PL.release('run-phones.bat') == 0 and not os.path.exists(PL.LOCK),
        'a new key must not wedge a lock written by the previous version')

    # -- `break` is the procedure for a lock nothing will release (desktop run, 2026-09-25) --
    # A holder whose cmd.exe died leaves its lock behind and every publisher refuses until the
    # six-hour budget. Waiting six hours, or working out unaided that the fix is deleting a file,
    # is not a procedure - and a person who has worked that out deletes the file during a live run
    # just as readily as after a dead one. `break` checks the age first.
    rec('break on a free lock is a no-op', PL.main(['x', 'break']) == 0)
    PL.acquire('refresh-dealflow.bat')
    rec('break REFUSES a lock still inside its budget',
        PL.main(['x', 'break']) == 9 and os.path.exists(PL.LOCK),
        'the holder may well be working - a 3h08m refresh must not be broken at hour one')
    rec('break --force takes it anyway, and says what that costs',
        PL.main(['x', 'break', '--force']) == 0 and not os.path.exists(PL.LOCK))
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(
        {'runner': 'run-phones.bat', 'pid': 1, 'ppid': 2, 'host': 'H', 'started_at': 'earlier',
         'started_epoch': time.time() - (PL.STALE_AFTER + 60)}))
    rec('break needs no --force once the lock is past its budget',
        PL.main(['x', 'break']) == 0 and not os.path.exists(PL.LOCK))

    # -- the different-run refusal must not assert a cause it cannot know --
    PL.acquire('run-leads.bat')
    other = json.load(io.open(PL.LOCK, encoding='utf-8'))
    other['ppid'] = os.getppid() + 90001
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(other))
    saidbuf = io.StringIO()
    real_say2, PL._say = PL._say, lambda m: saidbuf.write(m + '\n')
    try:
        PL.release('run-leads.bat')
    finally:
        PL._say = real_say2
    said2 = saidbuf.getvalue()
    rec('it offers both causes, not just the overrun',
        'died without' in said2 and 'overran' in said2,
        'on the 2026-09-25 desktop run the real cause was a holder whose cmd.exe exited')
    rec('and it tells the reader how to clear it',
        'publish_lock.py break' in said2, said2.strip().splitlines()[-1][:90])
    os.remove(PL.LOCK)

    # -- rc=9 for an unusable lock says so, instead of blaming a runner that never ran --
    unusable = io.StringIO()
    real_say, PL._say = PL._say, lambda m: unusable.write(m + '\n')
    try:
        os.mkdir(PL.LOCK)                          # a directory where the lock file goes
        rc = PL.acquire('run-leads.bat')
    finally:
        PL._say = real_say
        os.rmdir(PL.LOCK)
    said = unusable.getvalue()
    rec('a lock that cannot be created still refuses with 9', rc == 9)
    rec('and it is reported as UNUSABLE, not as another runner mid-run',
        'UNUSABLE' in said and 'another publishing runner is mid-run' not in said,
        said.strip().replace('\n', ' / ')[:110])

    # -- a create that succeeds and a write that fails must leave NO lock behind (review 2026-09-25)
    # O_EXCL creates the file and THEN the payload is written. If the write dies - a full disk, a
    # revoked handle - the file on disk is zero bytes and nobody's, and _read_holder honours an
    # unreadable lock by design, so all five publishers would refuse for six hours over a lock no
    # run ever held, and _refuse would report it as contention.
    zero = io.StringIO()
    real_say3, PL._say = PL._say, lambda m: zero.write(m + '\n')
    real_fdopen = PL.os.fdopen
    def _full_disk(fd, *a, **k):
        real_fdopen(fd, 'w').close()       # take the descriptor, write nothing
        raise IOError('no space left on device')
    PL.os.fdopen = _full_disk
    try:
        rc = PL.acquire('run-phones.bat')
    finally:
        PL.os.fdopen = real_fdopen
        PL._say = real_say3
    rec('a write that fails after the create refuses with 9', rc == 9)
    rec('and leaves no zero-byte lock to wedge the next six hours',
        not os.path.exists(PL.LOCK), 'a lock nobody owns is still a lock to every reader')
    rec('and reports it as UNUSABLE rather than as contention',
        'UNUSABLE' in zero.getvalue()
        and 'another publishing runner is mid-run' not in zero.getvalue(),
        zero.getvalue().strip().replace('\n', ' / ')[:110])

    # -- an aborted stale break that cannot put the lock back must KEEP it, not delete it ---------
    # Same race as above, one step worse: the live lock has been renamed aside and the restore
    # fails for a reason that is NOT "somebody already holds it" - a permission error, a full disk.
    # Deleting the renamed file there leaves a publishing run with no lock at all, which is the
    # double publish this whole file exists to stop. `except OSError: pass` could not tell the two
    # apart and deleted either way.
    # The swap has to happen BETWEEN the pre-rename identity check and the rename itself, or the
    # check catches it first and the post-rename path - the only one that cannot be raced - never
    # runs. Hooking os.rename is that interleaving exactly.
    stale2 = {'runner': 'run-phones-nightly.bat', 'pid': 1, 'ppid': 2, 'host': 'H',
              'started_at': 'earlier', 'started_epoch': time.time() - (PL.STALE_AFTER + 60)}
    fresh2 = json.dumps(dict(stale2, runner='run-leads.bat', pid=3, ppid=4,
                             started_epoch=time.time()))
    real_rename = PL.os.rename

    def _swap_then_rename(src, dst):
        if src == PL.LOCK:
            io.open(PL.LOCK, 'w', encoding='utf-8').write(fresh2)
        return real_rename(src, dst)

    def _run_break(deny_restore):
        io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(stale2))
        sig = PL._identity(PL._raw_lock())
        buf = io.StringIO()
        real_say4, PL._say = PL._say, lambda m: buf.write(m + '\n')
        real_osopen = PL.os.open
        PL.os.rename = _swap_then_rename
        if deny_restore:
            def _denied(path, *a, **k):
                if path == PL.LOCK:
                    raise OSError(13, 'permission denied')
                return real_osopen(path, *a, **k)
            PL.os.open = _denied
        try:
            return PL._break_stale(stale2, PL.STALE_AFTER + 60, sig), buf.getvalue()
        finally:
            PL.os.open = real_osopen
            PL.os.rename = real_rename
            PL._say = real_say4

    broke2, kept = _run_break(deny_restore=True)
    litter = [f for f in os.listdir(tmp) if '.stale.' in f]
    rec('a break that cannot restore the live lock still refuses', broke2 is False)
    rec('and keeps the renamed lock instead of deleting it', len(litter) == 1,
        '%s - a live publisher with no lock file is the collision, not the cure' % litter)
    rec('and says where it is', '.stale.' in kept, kept.strip().replace('\n', ' / ')[:120])
    for f in litter:
        os.remove(os.path.join(tmp, f))
    if os.path.exists(PL.LOCK):
        os.remove(PL.LOCK)

    # and the same interleaving with the restore ALLOWED puts the live lock back byte-for-byte.
    # This is the post-rename check doing its job, which the earlier fixture never reaches.
    broke3, putback = _run_break(deny_restore=False)
    rec('the same race with the restore available refuses too', broke3 is False)
    rec('and the live lock is back byte-for-byte',
        os.path.exists(PL.LOCK)
        and io.open(PL.LOCK, encoding='utf-8').read() == fresh2,
        'restored by an O_EXCL create, so it can never overwrite a newer holder')
    rec('and no .stale litter is left behind',
        [f for f in os.listdir(tmp) if '.stale.' in f] == [], putback[:80])
    os.remove(PL.LOCK)

    # -- the line a human reads must name the process that is actually alive ---------------------
    # 'pid' is the acquire python and is dead the moment the lock exists; the run is the recorded
    # ppid. break_lock tells whoever is deciding about --force to check that process, so printing
    # the dead one was advice to break a live lock.
    shown = PL._describe({'runner': 'run-leads.bat', 'pid': 111, 'ppid': 222, 'host': 'H',
                          'started_at': 'earlier'}, 60)
    rec('the holder line names the run, not the dead acquire pid',
        'run 222' in shown and '111' not in shown, shown)
    rec('and a pre-ppid lock file still says which pid it is showing',
        'acquired by pid 111' in PL._describe({'runner': 'run-leads.bat', 'pid': 111, 'host': 'H',
                                               'started_at': 'earlier'}, 60))

    # -- `break` must delete the lock it JUDGED, not whatever is at the path now (Greptile P1) -----
    # The holder of an expired lock can release, and a new runner can take a fresh one, between the
    # read and the remove - and a plain os.remove would then delete the NEW runner's lock and report
    # success, leaving it publishing unguarded. Same read-then-act gap as the stale break, and it is
    # reproduced the same way: by swapping the file underneath the rename.
    expired = {'runner': 'run-phones-nightly.bat', 'pid': 1, 'ppid': 2, 'host': 'H',
               'started_at': 'earlier', 'started_epoch': time.time() - (PL.STALE_AFTER + 60)}
    newlock = json.dumps(dict(expired, runner='run-leads.bat', pid=3, ppid=4,
                              started_epoch=time.time()))
    io.open(PL.LOCK, 'w', encoding='utf-8').write(json.dumps(expired))
    swapped = io.StringIO()
    real_say5, PL._say = PL._say, lambda m: swapped.write(m + '\n')
    real_rename2 = PL.os.rename

    def _swap2(src, dst):
        if src == PL.LOCK:
            io.open(PL.LOCK, 'w', encoding='utf-8').write(newlock)
        return real_rename2(src, dst)

    PL.os.rename = _swap2
    try:
        rc_break = PL.break_lock()
    finally:
        PL.os.rename = real_rename2
        PL._say = real_say5
    rec('break refuses once the lock has been replaced mid-break', rc_break == 9)
    rec('and the new runner still has its lock',
        os.path.exists(PL.LOCK) and io.open(PL.LOCK, encoding='utf-8').read() == newlock,
        'deleting it by pathname would leave that runner publishing with no lock at all')
    rec('and no .stale litter is left by the refused break',
        [f for f in os.listdir(tmp) if '.stale.' in f] == [],
        [f for f in os.listdir(tmp) if '.stale.' in f])
    os.remove(PL.LOCK)

    # -- an UNREADABLE lock is refused by break without --force too (Greptile P1) ------------------
    # _read_holder reports a problem with no holder and no age, which used to fall straight through
    # the budget check to the remove - so the one case where nothing is knowable about the holder was
    # the one case break cleared silently. An unknown lock is assumed live, as everywhere else here.
    os.mkdir(PL.LOCK)                              # unreadable: a directory where the file goes
    blind = io.StringIO()
    real_say6, PL._say = PL._say, lambda m: blind.write(m + '\n')
    try:
        rc_blind = PL.break_lock()
        rec('break refuses an unreadable lock without --force', rc_blind == 9)
        rec('and says why rather than clearing it silently',
            'assumed LIVE' in blind.getvalue(),
            blind.getvalue().strip().replace('\n', ' / ')[:110])
        rec('and the lock is still there', os.path.exists(PL.LOCK))
    finally:
        PL._say = real_say6
        os.rmdir(PL.LOCK)

finally:
    PL.LOCK = real_lock
    shutil.rmtree(tmp, ignore_errors=True)

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
