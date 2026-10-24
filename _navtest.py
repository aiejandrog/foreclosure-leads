"""The shared page strip (dealflow_nav.py) is the same list on every surface. No network, no board.

  * the board template's <nav class="dfnav"> carries dealflow_nav.ITEMS, same labels, same order
  * the Morning Worker modal carries the same labels in the same order
  * Call Mode bakes the strip with RELATIVE links that resolve from docs/call/ and docs/call/carlos/
  * the current page is marked and is not a link anywhere else
  * no strip carries an absolute aiejandrog.github.io address (board_url.py owns the only one)
  * the ids the board's JS wires by name all survived the header restructure, exactly once each
"""
import io, os, re, sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dealflow_nav as N

PASS, FAIL = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  PASS ' if ok else '  FAIL ') + name + ((' | ' + str(detail)) if detail else ''))


tpl = io.open(os.path.join(HERE, 'tracker_template.html'), encoding='utf-8').read()
cm = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()

print('BOARD HEADER')
labels = N.board_nav_labels(tpl)
rec('the board nav carries ITEMS in order', labels == N.LABELS, labels)
head = tpl[tpl.index('<header>'):tpl.index('</header>')]
ids = re.findall(r'\bid="([^"]+)"', head)
rec('no header id is duplicated', len(ids) == len(set(ids)))
for i in ['callmodebtn', 'callmodebtn2', 'workerbtn', 'mcapbadge', 'hmbtn', 'playbookbtn', 'refreshbtn', 'lockbtn',
          'upd', 'upddate', 'stalemark', 'nextup', 'closers', 'due', 'plan', 'replybtn', 'freshlp', 'mislist',
          'nearmebtn', 'q', 'sortby', 'mfilt', 'ctygroup', 'wk', 'hd', 'hv', 'newonly', 'hf', 'eq30', 'td', 'nc',
          'nearhome', 'nearrad', 'funnelbar', 'stats', 'proofbtn', 'savedbtn', 'mailbatch', 'bulktext',
          'cadencebtn', 'exp', 'syncbtn', 'advtog', 'denstog', 'moremenu']:
    if i not in ids:
        rec('header keeps #%s' % i, False)
rec('every wired header id is present', all(i in ids for i in ['callmodebtn', 'workerbtn', 'q', 'mfilt', 'stats', 'exp']))
rec('the four tiers exist', all(x in head for x in ['class="brand"', 'class="controls work"', 'class="controls filt mcol"', 'class="more"']))
rec('the More menu is a <details>, no JS', '<details class="more" id="moremenu">' in head)
rec('Filters stays the first thing in the collapsible row', head.index('id="mfilt"') < head.index('aria-label="Tier"'))
rec('the board nav marks itself current', 'class="dfn on" href="#" aria-current="page"' in head)

print('\nMORNING WORKER')
m = re.search(r"<nav class=\"dfnav mwnav\"[^>]*>'(.*?)'\s*\+\s*'</nav>'", tpl, re.S)
rec('the worker modal carries the strip', m is not None)
if m:
    wl = [re.sub(r'<[^>]+>', '', x).strip() for x in re.findall(r'>(?:[^<\'"]+)</a>', m.group(0))]
    wl = re.findall(r'>([A-Z][A-Za-z ]+)</a>', m.group(0))
    rec('worker strip labels match ITEMS', wl == N.LABELS, wl)
    rec('worker strip marks Worker current and nothing else', m.group(0).count('aria-current="page"') == 1 and 'aria-current="page" onclick="return false">Worker' in m.group(0))
rec('#playbook deep link is handled at boot', "playbook(=|&|$)" in tpl and 'openPlaybook();' in tpl)
rec('_balloonUrlAbs exists for the worker strip', 'function _balloonUrlAbs' in tpl)

print('\nCALL MODE')
rec('call_mode registers both placeholders with the exact-count guard', "('__DFNAV__', 1), ('__DFNAVCSS__', 1)" in cm)
rec('call_mode substitutes them from dealflow_nav', ".replace('__DFNAV__', json.dumps(_nav_html))" in cm and ".replace('__DFNAVCSS__', _nav.CSS_DARK)" in cm)
rec('head() prints the strip first', "return DFNAV + _qh + '<div class=\"top\">" in cm)
rec('the page declares the DFNAV var once', cm.count('var DFNAV=__DFNAV__;') == 1)
rec('build_html takes subdir and make_callmode passes it', "subdir=''):" in cm and 'subdir=subdir,' in cm)
for cur, depth, back in (('call', 1, '../'), ('carlos', 2, '../../')):
    h = N.strip(cur, depth)
    rec('%s strip links back to the board at %s' % (cur, back), 'href="%s"' % back in h or 'href="%s"' % (back or './') in h)
    rec('%s strip marks itself current' % cur, h.count('aria-current="page"') == 1)
    rec('%s strip carries no absolute board address' % cur, 'github.io' not in h and 'http' not in h)
    rec('%s strip has every label' % cur, all(('>%s</a>' % l) in h for l in N.LABELS))
    rec('%s strip opens the worker manually (=open), never the scheduler auto-send hash' % cur, ('href="%s#worker=open"' % back) in h and 'worker=morning' not in h)
h1 = N.strip('call', 1)
rec('from docs/call/ Carlos is a sibling folder', 'href="../call/carlos/"' in h1)
rec('the board strip (depth 0) links Call Mode under call/', 'href="call/"' in N.strip('board', 0))
rec('CSS_DARK styles .dfnav and the current page', '.dfnav .dfn.on{' in N.CSS_DARK)

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
for f in FAIL:
    print('  FAILED: ' + f)
sys.exit(1 if FAIL else 0)
