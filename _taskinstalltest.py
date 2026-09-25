"""One-shot: the DealFlow Cadence scheduled task is now something install-tasks.ps1 can create,
arm and stand down, and the task it creates cannot mail homeowners at 3am. Gitignored _*.py.
No network, no Task Scheduler, no PowerShell — this reads the four files and asserts their contract.

THE GAP THIS EXISTS FOR (found 2026-09-18). `DealFlow Cadence` — the daily job that is the ONLY
unattended outreach sender in this project — was registered by hand, outside install-tasks.ps1.
The installer's two halves were asymmetric about it and nothing said so:

  * `-DisableLocal` enumerates live tasks (`-match '^(DEALFLOW|DealFlow)'`), so it DID catch it.
  * `-Enable` walks `desktop-setup/tasks/*.xml`, so it did NOT bring it back up.

Disarming complete, arming silently incomplete: every handoff done exactly as documented left
outreach off, and the operator had no reason to look. It had been disabled on the desktop since
2026-08-26 and the laptop's send ledger has been empty since 09-13.

It could not simply be exported into `tasks/` — that directory is gitignored on purpose, because a
Windows task export embeds the exporting machine's principal SID. `task-templates/` is the tracked,
SID-free half, and most of what is asserted below is the boundary between those two directories.

The hour-window assertions are the other half. The task is StartWhenAvailable=true so a run missed
while the laptop slept is caught up rather than lost — which, without a guard, is a machine waking
at 22:40 and mailing a batch of homeowner follow-ups at 22:40. cadence-daily.bat holds that guard,
NOT cadence.py, which is the CLAUDE.md-reserved suppression surface.
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
    return io.open(p, 'rb').read() if os.path.exists(p) else None

TPL_REL = 'desktop-setup/task-templates/DealFlow_Cadence.xml'
raw_tpl = read(TPL_REL)
raw_ps1 = read('desktop-setup/install-tasks.ps1')
raw_bat = read('cadence-daily.bat')
raw_gi  = read('.gitignore')

print('\n-- the template exists, is tracked-safe, and is valid task XML --')
rec('the Cadence task template exists', raw_tpl is not None, TPL_REL)
if raw_tpl is None:
    print('\nFAILED — no template to check.'); sys.exit(1)
tpl = raw_tpl.decode('utf-8')

rec('template is UTF-8 with no BOM', not raw_tpl.startswith(b'\xef\xbb\xbf'))
rec('template parses as XML', True if ET.fromstring(tpl) is not None else False)
root = ET.fromstring(tpl)

# The reason this file can be committed at all: an EXPORT cannot, and these are what make it one.
rec('no principal SID in the template', 'S-1-5-' not in tpl)
rec('no literal user path in the template', not re.search(r'[A-Za-z]:\\\\?Users\\\\?', tpl), 'C:\\Users\\... would pin it to one box')
rec('no literal drive path at all', not re.search(r'\b[A-Za-z]:\\', tpl.replace('__REPO__', '')))

used = set(re.findall(r'__([A-Z]+)__', tpl))
rec('every placeholder is one the installer substitutes', used <= {'REPO', 'PROFILE', 'USER'}, ','.join(sorted(used)) or '(none)')
rec('the template does use placeholders for its paths', 'REPO' in used)

print('\n-- the task Windows would create --')
uri = (root.findtext('t:RegistrationInfo/t:URI', '', NS) or '').lstrip('\\')
rec("task name is 'DealFlow Cadence'", uri == 'DealFlow Cadence', uri)
rec('name matches the -DisableLocal regex', bool(re.match(r'^(DEALFLOW|DealFlow)', uri)),
    'or a handoff stands the pipeline down and leaves outreach running')

cmd = (root.findtext('t:Actions/t:Exec/t:Command', '', NS) or '')
wd = (root.findtext('t:Actions/t:Exec/t:WorkingDirectory', '', NS) or '')
rec('the action runs cadence-daily.bat', cmd.endswith('\\cadence-daily.bat'), cmd)
rec('the action does NOT run cadence-run.bat', 'cadence-run.bat' not in cmd,
    'cadence-run.bat ends in `pause`; a scheduled task cannot answer one')
rec('WorkingDirectory is the repo', wd == '__REPO__', wd)
rec('cadence-daily.bat is present in the repo', raw_bat is not None)

start = (root.findtext('t:Triggers/t:CalendarTrigger/t:StartBoundary', '', NS) or '')
daily = root.findtext('t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval', '', NS)
rec('trigger fires at 10:00', start.endswith('T10:00:00'), start)
rec('trigger is daily', daily == '1', daily)

# THE ORDERING IS THE ONLY THING ENFORCING THIS. replies.py then optout_sync.py is what carries a
# detected STOP into optouts.json, and cadence re-reads that ledger every run -- so cadence must
# fire AFTER the reply bake finishes. It was 09:00 against a 06:45 Replies. Replies moved to 08:45
# on 2026-09-22 and Phones to 09:30 (both confirmed on the laptop), which left cadence fifteen
# minutes behind a job that also rebuilds and publishes the board. cadence has no ledger-staleness
# gate of its own, so a stale read is a send to someone who said stop this morning.
REPLIES_AT = 8 * 60 + 45          # DealFlow Replies, confirmed on the laptop 2026-09-22
MIN_GAP_MIN = 60                  # Replies rebuilds and publishes; 15 minutes was not slack
_h, _m = (int(x) for x in start.split('T')[1].split(':')[:2])
rec('fires at least %d min after the %02d:%02d reply bake' % (MIN_GAP_MIN, REPLIES_AT // 60, REPLIES_AT % 60),
    (_h * 60 + _m) - REPLIES_AT >= MIN_GAP_MIN,
    '%02d:%02d, gap %d min' % (_h, _m, (_h * 60 + _m) - REPLIES_AT))
rec('and still inside the 08:00-20:00 outreach window', 8 <= _h < 20, _h)

S = {e.tag.split('}')[-1]: (e.text or '') for e in root.find('t:Settings', NS)}
rec('StartWhenAvailable is true', S.get('StartWhenAvailable') == 'true', 'a missed step must not be lost')
rec('WakeToRun is true', S.get('WakeToRun') == 'true')
rec('DisallowStartIfOnBatteries is false', S.get('DisallowStartIfOnBatteries') == 'false', 'the armed machine is a laptop')
rec('StopIfGoingOnBatteries is false', S.get('StopIfGoingOnBatteries') == 'false', 'killed mid-run = some owners mailed, some not')
rec('MultipleInstancesPolicy is IgnoreNew', S.get('MultipleInstancesPolicy') == 'IgnoreNew', 'two runs would double-send the same due steps')
rec('ExecutionTimeLimit is bounded and not the PT6H publish slack', S.get('ExecutionTimeLimit') == 'PT2H', S.get('ExecutionTimeLimit'))

print('\n-- the hour window, which is what makes StartWhenAvailable safe --')
if raw_bat is None:
    rec('cadence-daily.bat exists', False)
else:
    bat = raw_bat.decode('utf-8')
    rec('no `pause` anywhere in the unattended runner', not re.search(r'(?mi)^\s*pause\b', bat))
    rec('repo_guard.bat runs first', 'call repo_guard.bat' in bat, 'a run from the wrong checkout mails a stale queue')
    rec('it invokes cadence.py unbuffered', 'python -u cadence.py' in bat)
    rec('the lower bound is 08:00', bool(re.search(r'if\s+%HOUR%\s+LSS\s+8\b', bat)))
    rec('the upper bound is 20:00', bool(re.search(r'if\s+%HOUR%\s+GEQ\s+20\b', bat)))
    rec('an unreadable clock fails CLOSED', bool(re.search(r'if not defined HOUR', bat)), 'no hour, no mail')
    rec('both bounds jump to a no-send exit', bat.count('goto :outside') == 2 and ':outside' in bat)
    rec('the skip path exits 0', bool(re.search(r'(?s):outside.*?exit /b 0', bat)), 'a skipped run is not a failure; the step stays due')
    log = re.search(r'set "LOG=([^"]+)"', bat)
    rec('the run log lives outside the repo', bool(log) and log.group(1).startswith('%USERPROFILE%\\DEALFLOW'),
        log.group(1) if log else '(none)', )
    rec('the run log is not written under the checkout', '%~dp0cadence' not in bat,
        'it carries homeowner email addresses')
    rec('the window is NOT enforced inside cadence.py',
        'HOUR' not in (read('cadence.py') or b'').decode('utf-8', 'replace'),
        'cadence.py is the CLAUDE.md-reserved suppression surface')

print('\n-- the installer can now create, arm and stand it down --')
if raw_ps1 is None:
    rec('install-tasks.ps1 exists', False)
else:
    ps1 = raw_ps1.decode('utf-8')
    rec('the installer reads task-templates/', "'task-templates'" in ps1)
    rec('it still reads the gitignored exports in tasks/', "'tasks'" in ps1)
    rec('an export of the same task wins over a template', '$exported -notcontains $_.Name' in ps1)
    for ph in ('__REPO__', '__PROFILE__', '__USER__'):
        rec('it substitutes %s' % ph, ".Replace('%s'" % ph in ps1)
    rec('it refuses a leftover placeholder', "__(REPO|PROFILE|USER)__" in ps1,
        'better a loud throw than a task pointing at a literal __REPO__ path')
    rec('templates are read as UTF8, exports as Unicode',
        '-Encoding UTF8' in ps1 and '-Encoding Unicode' in ps1,
        'a UTF-8 template read as UTF-16 registers garbage')
    rec('-Only scopes the run to one task', re.search(r'\[string\]\$Only', ps1) is not None,
        'so Cadence can be added to the armed laptop without re-registering the live eight')
    rec('-Only applies to -DisableLocal too', ps1.count('$Only') >= 4)
    rec('-DisableLocal still enumerates live tasks', "-match '^(DEALFLOW|DealFlow)'" in ps1,
        'that is why it caught the hand-registered task when -Enable could not')
    rec('the prerequisite check knows cadence-daily.bat', "'cadence-daily.bat'" in ps1)
    rec('the prerequisite check knows cadence.py', "'cadence.py'" in ps1)
    rec('bsg_gmail.key is checked before arming', "'bsg_gmail.key'" in ps1,
        'missing it, every follow-up leaves as the login instead of the lane alias')
    rec('it no longer throws when only templates are present',
        'and no templates in' in ps1)

print('\n-- the two directories stay on opposite sides of git --')
gi = (raw_gi or b'').decode('utf-8', 'replace')
rec('desktop-setup/tasks/ is still gitignored', 'desktop-setup/tasks/' in gi, 'exports carry a SID')
rec('desktop-setup/task-templates/ is NOT gitignored', 'desktop-setup/task-templates' not in gi)

print('\n%d/%d passed.' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
