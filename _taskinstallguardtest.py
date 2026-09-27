"""install-tasks.ps1 must not report a task it failed to create, and its documented -Only
commands must match the task they name. Text/contract checks, like _taskinstalltest.py: no
Task Scheduler, no PowerShell, runs on Linux CI.

WHAT HAPPENED (2026-09-26, laptop). The documented arming command
    pwsh .\\desktop-setup\\install-tasks.ps1 -Only OptoutSync -Enable
failed twice and said it had succeeded:
  1. `-Only OptoutSync` matched nothing: the task is `DealFlow Opt-out Sync`.
  2. With the pattern fixed, Register-ScheduledTask rejected the template:
         The task XML is malformed. (1,40)::ERROR: unable to switch the encoding
     (-Xml passes a UTF-16 string, and the template declares encoding="UTF-8"). The ScheduledTasks
     cmdlets are CDXML and ignore the script's $ErrorActionPreference, so the script kept going
     and printed "ENABLED", "1 task(s) installed." and "ARMED." about a task that did not exist.
     Its exit code was 0.
"""
import fnmatch, io, os, re, sys, xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
R = []
def rec(n, ok, d=''):
    R.append(bool(ok))
    print((('  PASS ' if ok else '  FAIL ') + n + (' | ' + str(d) if d else '')).encode('ascii', 'replace').decode())

ps1 = io.open(os.path.join(HERE, 'desktop-setup', 'install-tasks.ps1'), encoding='utf-8-sig').read()
tpl_dir = os.path.join(HERE, 'desktop-setup', 'task-templates')
tpls = {f: io.open(os.path.join(tpl_dir, f), encoding='utf-8').read()
        for f in sorted(os.listdir(tpl_dir)) if f.endswith('.xml')}
rec('there are templates to check', len(tpls) >= 2, ', '.join(tpls))

def task_name(xml):
    m = re.search(r'<URI>\\?([^<]+)</URI>', xml)
    return m.group(1) if m else None
names = {f: task_name(x) for f, x in tpls.items()}
rec('every template names its task in <URI>', all(names.values()), names)

print('\n-- the XML declaration is dropped before Register-ScheduledTask --')
m = re.search(r"\$body = \$body -replace '(\^[^']*<\\\?xml[^']*)', ''", ps1)
rec('the installer strips the <?xml ...?> declaration', bool(m))
i_strip = m.start() if m else -1
i_reg = ps1.find('Register-ScheduledTask -TaskName')
rec('...before Register-ScheduledTask is called', 0 <= i_strip < i_reg, (i_strip, i_reg))
if m:
    rx = re.compile(m.group(1))   # the .NET pattern is also a valid Python pattern
    for f, x in tpls.items():
        declared = re.match(r'\s*<\?xml[^>]*encoding="([^"]+)"', x)
        body = x.replace('__REPO__', r'C:\r').replace('__PROFILE__', r'C:\p').replace('__USER__', r'PC\u')
        out = rx.sub('', body, count=1)
        rec('%s: declaration (%s) removed, rest untouched' % (f, declared.group(1) if declared else 'none'),
            not out.lstrip().startswith('<?xml') and out.strip().endswith('</Task>'))
        rec('%s: still parses as task XML' % f, ET.fromstring(out).tag.endswith('Task'))
        # the same pattern with a BOM in front (a template saved by Notepad)
        rec('%s: stripped even behind a BOM' % f, not rx.sub('', '\ufeff' + body, count=1).lstrip().startswith('<?xml'))

print('\n-- a failed registration is a failure --')
reg_line = next((l for l in ps1.splitlines() if 'Register-ScheduledTask -TaskName' in l), '')
rec('Register-ScheduledTask uses -ErrorAction Stop', '-ErrorAction Stop' in reg_line, reg_line.strip())
for c in ('Enable-ScheduledTask', 'Disable-ScheduledTask -TaskName $name'):
    line = next((l for l in ps1.splitlines() if c in l and 'TaskPath' not in l), '')
    rec('%s uses -ErrorAction Stop' % c.split()[0], '-ErrorAction Stop' in line, line.strip())
i_try = ps1.rfind('try {', 0, i_reg)
i_catch = ps1.find('} catch {', i_reg)
rec('the register/enable calls sit inside try', 0 <= i_try < i_reg < i_catch)
i_enabled = ps1.find("'ENABLED'", i_reg)
rec("'ENABLED' is printed only after the try block succeeded", i_enabled > i_catch > 0, (i_catch, i_enabled))
rec('the catch records the failure and skips the task', re.search(r'\} catch \{\s*\$failed \+= \$name.{0,300}?\bcontinue\b', ps1, re.S) is not None)
rec('the resulting state is read back from Task Scheduler', 'Get-ScheduledTask -TaskName $name -ErrorAction Stop' in ps1)
m2 = re.search(r'if \(\$failed\) \{(.*?)\n\}', ps1, re.S)
rec('any failure ends the run with exit 1', bool(m2) and 'exit 1' in m2.group(1))
i_fail = m2.start() if m2 else 10**9
for s in ('task(s) installed."', 'ARMED.', 'Everything is registered but DISABLED'):
    j = ps1.find(s)
    rec('the failure exit comes before "%s"' % s, 0 <= i_fail < j, (i_fail, j))

print('\n-- -Only matches the documented names --')
km = re.search(r"function Get-TaskKey\(\[string\]\$s\) \{ return \(\$s -replace '([^']+)', ''\)\.ToLowerInvariant\(\) \}", ps1)
rec('Get-TaskKey normalises the name', bool(km))
rec('the raw -like match is tried first (old patterns keep matching)', '($name -like "*$pattern*") -or' in ps1)
rec('both -Only sites use Test-OnlyMatch', ps1.count('Test-OnlyMatch $_.') == 2)
rec('no raw -like "*$Only*" filter is left', '-like "*$Only*"' not in ps1)
key_rx = re.compile(km.group(1)) if km else re.compile(r'[\s_-]')
key = lambda s: key_rx.sub('', s).lower()
def only_match(name, pat):   # PowerShell -like is case-insensitive
    return fnmatch.fnmatch(name.lower(), '*%s*' % pat.lower()) or fnmatch.fnmatch(key(name), '*%s*' % key(pat))
rec("'OptoutSync' matches 'DealFlow Opt-out Sync'", only_match('DealFlow Opt-out Sync', 'OptoutSync'))
rec("'OptoutSync' does not match 'DealFlow Cadence'", not only_match('DealFlow Cadence', 'OptoutSync'))
rec("'opt-out sync' matches too", only_match('DealFlow Opt-out Sync', 'opt-out sync'))
docs = {'install-tasks.ps1': ps1, **tpls}
examples = sorted({(f, p) for f, t in docs.items() for p in re.findall(r'install-tasks\.ps1\s+-Only\s+([A-Za-z][\w-]*)', t)})
rec('the docs have -Only examples to check', len(examples) >= 2, examples)
for f, p in examples:
    hits = [n for n in names.values() if only_match(n, p)]
    rec("%s: documented '-Only %s' matches exactly one template" % (f, p), len(hits) == 1, hits)

print('\n%d/%d passed.' % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
