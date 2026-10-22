"""Build isolated UI fixtures from production templates. No data files, keys, or APIs."""
import datetime as dt
import json
from pathlib import Path
import disclaimer
import foreclosure_leads as F
import call_mode as C

ROOT = Path(__file__).resolve().parent
FIXTURE_CODE = 'synthetic-ui-only'


def leads():
    out = []
    for i, days in enumerate((2, 5, 20, 40, None), 1):
        date = (dt.date.today() + dt.timedelta(days=days)).strftime('%m/%d/%Y') if days else ''
        out.append(dict(case=f'SYNTHETIC-UI-{i}', owners=f'SYNTHETIC PERSON {i}',
                        oname='Synthetic', addr=f'{i} Example Street, Example City, FL',
                        county='MIAMI-DADE', tier='A', score=80, st='FC', auction=date,
                        days=days if days else 9999, lp=days is None, value=400000,
                        judg=100000, eq=75, hs=True, ctype='Bank/Mortgage',
                        phones=['3055550101', '3055550102'], phdnc=[False, False],
                        phsrc=['st', 'st'], phtype=['mobile', 'landline'], emails=[]))
    return out


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows = leads()
    tpl = F.subst_build_facts((ROOT / 'tracker_template.html').read_text(), dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
    replacements = {'__DATA__': F._esc_json(rows), '__MOTIONJS__': F._motion_js(),
                    '__OPTOUTS__': '[]', '__REPLIES__': '{}', '__MAILLOG__': '{}',
                    '__MAILTO__': '{}', '__TEXTLOG__': '{}', '__TEXTPERSON__': '{}',
                    '__DEADS__': '[]', '__ZIPCENT__': '{}', '__BUILT__': 'synthetic',
                    '__BUILTAT__': dt.datetime.now().isoformat(timespec='seconds'),
                    '__IDENT_EN__': F._esc_js(disclaimer.identity('en', as_html=False)),
                    '__IDENT_ES__': F._esc_js(disclaimer.identity('es', as_html=False))}
    for token, value in replacements.items():
        tpl = tpl.replace(token, value)
    tpl = F._bake_alex_email(tpl, 'synthetic-ui.html')
    F.assert_no_placeholders(tpl, 'synthetic-ui.html')
    (directory / 'board.html').write_text(tpl, encoding='utf-8')
    # Already-slim synthetic rows test the renderer, not production eligibility decisions.
    calls = [dict(c=r['case'], o=r['owners'], on='Synthetic', a=r['addr'],
                  p=r['phones'], r=['M', 'L'], x=r['auction'], d=r['days'],
                  lp=int(r['lp']), eq=75, v=400000, j=100000, county='MIAMI-DADE') for r in rows]
    payload = F._encrypt_multi(json.dumps(calls), [('Synthetic', FIXTURE_CODE)])
    page = C.build_html(calls, len(calls), payload, dt.date.today().isoformat(),
                        'synthetic', 'synthetic',
                        funnel_js=C.extract_funnel_js(tpl), text_js=C.extract_text_js(tpl),
                        histcov=json.dumps({'ledgers_ok': True, 'why': '', 'capped': False,
                                            'total': 5, 'shipped': 5}))
    (directory / 'call.html').write_text(page, encoding='utf-8')
    return directory / 'board.html', directory / 'call.html'
