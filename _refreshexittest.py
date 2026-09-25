"""The nightly refresh's exit code is a verdict, and the 05:30 task is a file you can read.

Gitignored `_*.py` with a `!` negation in .gitignore. No network, no Task Scheduler, no
PowerShell: this reads four files and asserts their contract.

THE GAP THIS EXISTS FOR (found 2026-09-21, auditing three mornings that published nothing)

`refresh-dealflow.bat` carries a RUNEXIT verdict precisely because, until 2026-09-18, it exited 0
no matter what happened — and the file's own header says so at length. Two branches were left out
of that work, and they are the two that decide whether the board goes live:

    healthcheck exit >= 2  -> "GATE: healthcheck COMPLIANCE fail - publish SKIPPED"  -> goto :end
    publish_guard exit 1   -> "GATE: publish_guard BLOCKED the build - publish SKIPPED" -> goto :end

Neither touched RUNEXIT. The healthcheck one survived by luck: the tail runs healthcheck a SECOND
time and sets 2 — so it only lied when a source came back up during the three-hour enrichment. The
publish_guard one had no such luck. publish_guard refuses a board materially poorer than the live
one, which is a CONTENT regression, and content regression is exactly what healthcheck does not
grade. So that morning printed "health OK", exited 0, and wrote a green DEALFLOW-STATUS.txt while
the live site stayed on yesterday's board. Byte-identical, in every unattended signal there is, to
a night that worked.

The second half is the task itself. Eight of the nine scheduled tasks are defined only by an export
in `desktop-setup/tasks/`, which is gitignored for a good reason (a Windows export embeds the
exporting machine's principal SID). The cost is that the settings deciding whether the nightly fires
at all — batteries, sleep, idle, the time limit — could not be read from anywhere but the laptop.
`desktop-setup/task-templates/DEALFLOW_Refresh.xml` is the tracked, SID-free half. It does not
override the laptop: install-tasks.ps1 applies an export over a template of the same name, always.

Third: three `setup-*-automation.ps1` scripts in the repo root still register these tasks the old
way — `New-ScheduledTaskSettingsSet` with no `-IdleSettings`, which restores StopOnIdleEnd=true,
the 2026-08-31 root cause — and setup-automation.ps1 additionally unregisters the live 05:30 export
and re-creates it at 9:00 AM with a 2-hour kill. They now refuse without -Force.
"""
import io, os, re, sys, xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}

R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

def read(rel):
    p = os.path.join(HERE, rel)
    return io.open(p, encoding='utf-8', errors='replace').read() if os.path.exists(p) else None

BAT = read('refresh-dealflow.bat')
TPL_REL = 'desktop-setup/task-templates/DEALFLOW_Refresh.xml'
TPL = read(TPL_REL)
GI  = read('.gitignore')
HANDOFF = read('MACHINE-HANDOFF.md')

print('\n-- every GATE that skips the publish carries it to the exit code --')
if BAT is None:
    print('\nFAILED - refresh-dealflow.bat is missing.'); sys.exit(1)
lines = BAT.split('\n')

# Enumerate every `goto :end`. A site is a GATE site when the branch it sits in announces itself
# with "GATE:" — that is the file's own word for "the publish did not happen". Those must set a
# non-zero RUNEXIT. The others (the [5/5] "nothing changed" case) are clean mornings and must not.
gate_sites, plain_sites = [], []
for i, ln in enumerate(lines):
    if ln.strip().lower() != 'goto :end':
        continue
    # walk back to the opening `(` of this branch, bounded
    start = max(0, i - 25)
    for j in range(i, start, -1):
        if re.match(r'^\s*(if|\)\s*else)\b.*\($', lines[j]):
            start = j
            break
    block = '\n'.join(lines[start:i + 1])
    (gate_sites if 'GATE:' in block else plain_sites).append((i + 1, block))

rec('the file still has gate branches that skip the publish', len(gate_sites) >= 2,
    'found %d' % len(gate_sites))
for lineno, block in gate_sites:
    m = re.search(r'set "RUNEXIT=(\d+)"', block)
    rec('the gate at line %d sets a non-zero RUNEXIT' % lineno,
        bool(m) and m.group(1) != '0',
        (m.group(1) if m else 'NOTHING - this branch skips the publish and exits 0'))

rec('the healthcheck COMPLIANCE gate uses the documented code 2',
    any(re.search(r'set "RUNEXIT=2"', b) for _, b in gate_sites if 'healthcheck COMPLIANCE' in b))
rec('the publish_guard gate has its own code',
    any(re.search(r'set "RUNEXIT=6"', b) for _, b in gate_sites if 'publish_guard BLOCKED' in b))
# The one branch that must NOT set a code: [5/5] found nothing to commit. A night with no new
# leads is a clean night, not a fault. (The scrape and network branches also lack "GATE:" and DO
# set a code - they are faults; only this one is a legitimate rc=0.)
nothing_changed = [b for _, b in plain_sites if 'Already current' in b]
rec('the "nothing changed" branch exists', len(nothing_changed) == 1)
rec('and it is still a clean rc=0',
    all('RUNEXIT' not in b for b in nothing_changed),
    'a night with no new leads is not a fault')
rec('every other goto :end either gates or sets a code',
    all('GATE:' in b or 'RUNEXIT' in b or 'Already current' in b for _, b in plain_sites + gate_sites))

print('\n-- every code the file can exit with is in the table at the top --')
header = BAT.split('set "RUNEXIT=0"')[0]
used = sorted({int(c) for c in re.findall(r'set "RUNEXIT=(\d+)"', BAT)} - {0})
# The table is prose: "Codes: 0 clean | 1 scrape failed | 2 healthcheck COMPLIANCE fail | ..."
# with later additions written as "6 = ...". Accept both spellings.
documented = {int(c) for c in re.findall(r'\b(\d)\s*=\s*\w', header)} | \
             {int(c) for c in re.findall(r'\b(\d) [a-z]', header)}
for c in used:
    rec('exit code %d is documented in the header' % c, c in documented)
rec('the codes are contiguous from 1', used == list(range(1, max(used) + 1)) if used else False,
    'uses %s' % (used,))

print('\n-- the publish lock is taken before any work and dropped on every exit --')
# 2026-09-22: five .bat files rebuild docs/ and push it and none of them took a lock, so the only
# thing keeping two apart was the clock on their triggers. THIS file is the one that makes the
# collision likely - its measured chain runs 3h08m from 05:30 and has run to about 4h, so it can
# still be building when DealFlow Replies fires and run-replies-daily.bat rebuilds and pushes the
# same two paths. The installed trigger times live in CLAUDE.md's publish table, deliberately not
# here: a time copied into a comment is stale the next time someone opens Task Scheduler.
# _batsyntaxtest.py owns the wiring invariant across all five; these are the parts specific to the
# nightly, where the ordering against net_ready.py and the RUNEXIT ladder both matter.
lock_i = [i for i, l in enumerate(lines) if 'publish_lock.py acquire' in l]
rel_i = [i for i, l in enumerate(lines) if 'publish_lock.py release' in l]
rec('the nightly acquires the publish lock', len(lock_i) == 1, '%d acquire calls' % len(lock_i))
rec('and releases it exactly once', len(rel_i) == 1, '%d release calls' % len(rel_i))
if lock_i and rel_i:
    net_i = [i for i, l in enumerate(lines) if 'net_ready.py' in l and l.strip().startswith('python')]
    rec('it is acquired before the four-minute network wait', bool(net_i) and lock_i[0] < net_i[0],
        'a run that is going to refuse should not spend four minutes first')
    scrape_i = [i for i, l in enumerate(lines) if 'foreclosure_leads.py' in l and l.strip().startswith('python')]
    rec('it is acquired before the scrape', bool(scrape_i) and lock_i[0] < min(scrape_i),
        'nothing is scraped, built or pushed under a lock this run does not hold')
    end_i = lines.index(':end')
    rec('the release sits below :end, where every goto lands', rel_i[0] > end_i,
        'release line %d, :end at line %d' % (rel_i[0], end_i))
    gotos = [i for i, l in enumerate(lines) if l.strip().lower() == 'goto :end']
    rec('every goto :end is above the release', all(g < rel_i[0] for g in gotos),
        '%d goto sites' % len(gotos))
    # the refusal must NOT release: rc=9 means another runner owns the lock
    refusal = '\n'.join(lines[lock_i[0]:lock_i[0] + 8])
    rec('the refusal exits 9', 'exit /b 9' in refusal, refusal.replace('\n', ' / ')[:110])
    rec('the refusal does not release a lock it failed to take',
        'publish_lock.py release' not in refusal,
        'dropping the holder\'s lock is worse than the race it was stopping')
    rec('rc=9 is NOT part of the RUNEXIT ladder',
        'set "RUNEXIT=9"' not in BAT,
        'a run that never started has no verdict to carry; contiguity above still holds')
    rec('rc=9 is documented in the header table', 9 in documented)

print('\n-- a run that stages nothing at the early publish says so --')
# `goto :afterearly` appears inside this stage, so split on the LABEL at the start of a line.
early = re.split(r'^:afterearly', BAT.split('Publishing fresh leads immediately')[-1], flags=re.M)[0]
rec('the early publish has an else branch', ') else (' in early)
rec('and it writes a line to the log',
    bool(re.search(r'\)\s*else\s*\(\s*\n\s*echo[^\n]*>>\s*"%LOG%"', early)),
    'otherwise a dead commit and a run that never got here read identically')

print('\n-- the 05:30 task is a tracked, SID-free, reviewable file --')
rec('the Refresh task template exists', TPL is not None, TPL_REL)
if TPL is None:
    print('\n%d passed, %d failed' % (sum(R), len(R) - sum(R))); sys.exit(1)
rec('template parses as task XML', ET.fromstring(TPL) is not None)
root = ET.fromstring(TPL)
rec('no principal SID in the template', 'S-1-5-' not in TPL)
rec('no literal user path', not re.search(r'[A-Za-z]:\\\\?Users\\\\?', TPL))
rec('no literal drive path at all', not re.search(r'\b[A-Za-z]:\\', TPL.replace('__REPO__', '')))
rec('it uses the installer placeholders', '__REPO__' in TPL and '__USER__' in TPL)
rec('desktop-setup/task-templates/ is not gitignored',
    GI is not None and 'desktop-setup/task-templates' not in GI)

def s(tag):
    e = root.find('.//t:Settings/t:' + tag, NS)
    return None if e is None else (e.text or '').strip()
def idle(tag):
    e = root.find('.//t:Settings/t:IdleSettings/t:' + tag, NS)
    return None if e is None else (e.text or '').strip()

print('\n-- and it carries the hardening this project already paid for --')
rec('ExecutionTimeLimit is PT6H, not PT2H', s('ExecutionTimeLimit') == 'PT6H',
    '%s | the measured chain on 2026-09-20 ran 3h08m' % s('ExecutionTimeLimit'))
rec('StopOnIdleEnd is false', idle('StopOnIdleEnd') == 'false',
    'true terminates the run the moment somebody sits down at the box - 2026-08-31')
rec('RunOnlyIfIdle is false', s('RunOnlyIfIdle') == 'false')
rec('DisallowStartIfOnBatteries is false', s('DisallowStartIfOnBatteries') == 'false',
    'true means the laptop skips 05:30 whenever it was unplugged')
rec('StopIfGoingOnBatteries is false', s('StopIfGoingOnBatteries') == 'false')
rec('StartWhenAvailable is true', s('StartWhenAvailable') == 'true')
rec('WakeToRun is true', s('WakeToRun') == 'true')
rec('MultipleInstancesPolicy is IgnoreNew', s('MultipleInstancesPolicy') == 'IgnoreNew')

rec('RunOnlyIfNetworkAvailable is false', s('RunOnlyIfNetworkAvailable') == 'false',
    'a task declined by NLA logs nothing; net_ready.py refuses with a reason and rc=3')
rec('the bat still opens with net_ready.py, which is what makes that safe',
    'net_ready.py' in BAT.split('[1/4]')[0])

rst_i = root.find('.//t:Settings/t:RestartOnFailure/t:Interval', NS)
rst_c = root.find('.//t:Settings/t:RestartOnFailure/t:Count', NS)
rec('it retries a failed run', rst_i is not None and rst_c is not None,
    'rc=3 - the network was not up in one four-minute window - should not cost the day')

trig = root.find('.//t:Triggers/t:CalendarTrigger/t:StartBoundary', NS)
rec('the trigger is 05:30 daily',
    trig is not None and trig.text.endswith('T05:30:00')
    and root.find('.//t:ScheduleByDay/t:DaysInterval', NS) is not None,
    trig.text if trig is not None else 'no trigger')
cmd = root.find('.//t:Actions/t:Exec/t:Command', NS)
rec('it runs refresh-dealflow.bat', cmd is not None and cmd.text.endswith('refresh-dealflow.bat'),
    cmd.text if cmd is not None else 'no command')

print('\n-- the superseded installers cannot quietly undo any of it --')
for f, only in (('setup-automation.ps1', 'Refresh'),
                ('setup-phones-automation.ps1', 'Phones'),
                ('setup-analyst-automation.ps1', 'Analyst')):
    raw = read(f)
    if raw is None:
        rec('%s is gone (also fine)' % f, True)
        continue
    head = raw.split('$ErrorActionPreference')[0]
    # strip the comment banner: it NAMES the cmdlets it is warning about
    code = '\n'.join(l for l in head.split('\n') if not l.lstrip().startswith('#'))
    rec('%s refuses without -Force' % f,
        'param([switch]$Force)' in code and re.search(r'if \(-not \$Force\)', code) is not None
        and 'exit 1' in code)
    rec('%s points at install-tasks.ps1' % f, 'install-tasks.ps1' in head, '-Only %s' % only)
    # the guard has to come FIRST: a refusal printed after Unregister-ScheduledTask is no refusal
    rec('%s registers nothing before the guard' % f,
        'Register-ScheduledTask' not in code and 'Unregister-ScheduledTask' not in code)
    rec('%s still builds its settings without -IdleSettings' % f,
        '-IdleSettings' not in raw.split('$ErrorActionPreference')[-1],
        'StopOnIdleEnd defaults to true - which is why this file is guarded, not trusted')

print('\n-- the handoff table names the tracked definition --')
if HANDOFF:
    row = [l for l in HANDOFF.split('\n') if l.startswith('| DEALFLOW Refresh')]
    rec('MACHINE-HANDOFF section 4 has a DEALFLOW Refresh row', bool(row))
    if row:
        rec('and it points at task-templates/, not only the gitignored export',
            'task-templates' in row[0], row[0].strip())

print('\n%d passed, %d failed' % (sum(R), len(R) - sum(R)))
sys.exit(0 if all(R) else 1)
