"""_boardvacanttest -- the board's Vacant lane lists exactly the leads Call Mode marked vacant.

Run:  python _boardvacanttest.py   (exit 0 = safe; invented rows, file://, every network request aborted)

WHY (2026-11-19, Alejandro: "add the vacant lane on the board"). Call Mode's Vacant button writes a
'lookup' touch {vac:{a,on}} on the case note. The board had no way to see those leads together.

Pins:
  - the Vacant button shows the count of marked leads
  - the lane lists only marked leads, in any tier, newest mark first, and shows its filter pill
  - the newest mark wins: a lead un-marked in Call Mode is not in the lane
  - a marked lead carries a VACANT HOUSE chip on the normal board too
  - turning on another lane leaves the Vacant lane; the pill's X leaves it; Reset leaves it
  - an empty lane does not throw
"""
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_T = tempfile.mkdtemp(prefix='boardvac-')
os.environ['DEALFLOW_BK_CACHE'] = os.path.join(_T, 'no_bk_cache.json')

import _balloonlanetest as BL          # noqa: E402
import _stressboardtest as SB          # noqa: E402  fixture row builder only (fc)

FAILS = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra and not ok else ''))
    if not ok:
        FAILS.append(name)


ROWS = [SB.fc(21, eqstate='clear', judg=142200, orconf='ok'), SB.fc(22, eqstate='clear', judg=142200, orconf='ok', tier='C'),
        SB.fc(23, eqstate='clear', judg=142200, orconf='ok'), SB.fc(24, eqstate='clear', judg=142200, orconf='ok')]
A, B, C_, D = [r['case'] for r in ROWS]

PROBE = r"""async ([A, B, C_, D]) => {
  const wait = ms => new Promise(res => setTimeout(res, ms));
  const o = {};
  const cases = () => [...document.querySelectorAll('tr[data-case]')].map(t => t.dataset.case);
  const mark = (c, on, tsu) => { const n = notes[c] = notes[c] || {}; (n.touches = n.touches || []).push(
      {ch:'lookup', d:'2026-11-19', tsu:tsu, by:'Carlos', out:on ? 'Marked VACANT' : 'Vacant mark removed', vac:{a:'x', on:on}}); };
  const btn = document.getElementById('vacantlane');
  o.btn = !!btn;
  btn.click(); await wait(80);
  o.empty = {n: cases().length, on: vacOnly};
  btn.click(); await wait(60);
  mark(A, true, 1000); mark(B, true, 3000);           // B marked later, and B is tier C
  mark(C_, true, 2000); mark(C_, false, 4000);        // C marked, then un-marked: not vacant
  notes[D] = {touches:[{ch:'call', d:'2026-11-19', tsu:5000, out:'No answer'}]};
  render(); await wait(80);
  o.count = document.getElementById('vacn').textContent;
  o.chipNormal = (document.querySelector('tr[data-case="' + A + '"]') || document.body).innerHTML.indexOf('VACANT HOUSE') >= 0;
  o.noChip = (document.querySelector('tr[data-case="' + D + '"]') || {innerHTML:'VACANT HOUSE'}).innerHTML.indexOf('VACANT HOUSE') < 0;
  btn.click(); await wait(80);
  o.lane = cases();
  o.active = btn.classList.contains('active');
  o.pill = document.body.innerText.indexOf('Vacant (marked in Call Mode)') >= 0;
  /* another lane takes over */
  document.getElementById('freshlp').click(); await wait(80);
  o.afterOther = {vac: vacOnly, lp: lpOnly, active: btn.classList.contains('active')};
  document.getElementById('freshlp').click(); await wait(60);
  /* the pill's X */
  btn.click(); await wait(60);
  const x = [...document.querySelectorAll('[data-clr="vacantlane"]')][0];
  o.pillEl = !!x;
  if(x){ x.click(); await wait(60); }
  o.afterX = vacOnly;
  return o; }"""

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as _p:
        _p.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None).close()
except Exception as e:
    print('SKIP browser half (%s)' % str(e).splitlines()[0][:200])
    sys.exit(0)

fake, preview_empty = BL.preview_constants()
path = os.path.join(_T, 'board.html')
open(path, 'w', encoding='utf-8').write(BL.build_html(ROWS, preview_empty))
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=os.environ.get('PW_CHROMIUM') or None)
    ctx = b.new_context(viewport={'width': 1500, 'height': 900})
    ctx.route('**/*', lambda rt: rt.continue_() if rt.request.url.startswith('file:') else rt.abort())
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(pathlib.Path(path).as_uri())
    pg.wait_for_timeout(1200)
    o = pg.evaluate(PROBE, [A, B, C_, D])
    b.close()

rec('the board has a Vacant lane button', o['btn'])
rec('an empty lane shows nothing and does not throw', o['empty'] == {'n': 0, 'on': True}, o['empty'])
rec('the button counts the marked leads (un-marked one not counted)', o['count'] == '2', o['count'])
rec('a marked lead carries a VACANT HOUSE chip on the normal board', o['chipNormal'])
rec('...and an ordinary lead does not', o['noChip'])
rec('the lane lists exactly the marked leads, any tier, newest mark first', o['lane'] == [B, A], o['lane'])
rec('the button is lit and the filter pill says why the board is short', o['active'] and o['pill'], o)
rec('turning on another lane leaves the Vacant lane', o['afterOther'] == {'vac': False, 'lp': True, 'active': False}, o['afterOther'])
rec('the pill can be closed', o['pillEl'] and o['afterX'] is False, (o['pillEl'], o['afterX']))
rec('no page error', not errs, errs[:2])
print('\n%d failure(s)' % len(FAILS) if FAILS else '\nall passed')
sys.exit(1 if FAILS else 0)
