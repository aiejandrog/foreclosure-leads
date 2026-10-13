"""Call Mode voicemail script (2026-10-12).

Alejandro, 10-12: "create a voice message option when they dont pick up that we can leave and its a
whole script". The Left voicemail outcome already existed (logs like No answer, 24h); this pins the
new wording, the live days-to-sale sentence, and the guard that stops Carlos's page from leaving
Alejandro's name and callback number.

The JS is extracted by name from call_mode.py and run under node. No browser, no board.
Run: python _voicemailtest.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import call_mode as CM  # noqa: E402

SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()
fails = []


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:400]) if extra else ''))
    if not ok:
        fails.append(name)


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}\n', i)
    rec('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] if i >= 0 and j > 0 else ''


# ---- copy law, Python side --------------------------------------------------------------------
both = CM.VOICEMAIL_EN + CM.VOICEMAIL_ES + json.dumps(CM.VM_SALE, ensure_ascii=False)
low = both.lower()
for bad in ('stop the sale', 'stop the foreclosure', 'detener la venta', 'fee', 'cargo', 'free',
            'gratis', 'guarantee', 'garant', 'offer', 'oferta', '$', 'remediate', 'solutions in place',
            'foreclosure', 'ejecución hipotecaria', 'reply stop'):
    rec('voicemail never says %r' % bad, bad not in low)
rec('EN carries the let-me-know line', "If now's not a good time, just let me know and I won't reach out again." in CM.VOICEMAIL_EN)
rec('ES carries the let-me-know line', 'Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.' in CM.VOICEMAIL_ES)
rec('ES is usted, never tú', ' tú ' not in CM.VOICEMAIL_ES and 'usted' in CM.VOICEMAIL_ES)
for lg, t in (('en', CM.VOICEMAIL_EN), ('es', CM.VOICEMAIL_ES)):
    rec(lg + ': callback number said twice', t.count('{phone}') == 2)
    rec(lg + ': name comes from the dialing phone', '{sender}' in t and 'Alejandro' not in t)
    rec(lg + ': sale sentence is a token', ('{vmsale_es}' if lg == 'es' else '{vmsale}') in t)
    rec(lg + ': VM_SALE has every state', set(CM.VM_SALE[lg]) == {'days', 'tomorrow', 'today', 'none'})
    rec(lg + ': only days carries {n}', all(('{n}' in v) == (k == 'days') for k, v in CM.VM_SALE[lg].items()))
rec('outcome still logs as a miss, 24h, not permanent',
    ('voicemail', 'Left voicemail', 24, False) in CM.CALL_OUTCOMES)
rec('miss set still includes voicemail', "o.k==='voicemail'" in SRC)

# ---- JS side ----------------------------------------------------------------------------------
if subprocess.run(['node', '--version'], capture_output=True).returncode != 0:
    rec('node available', True, 'node missing; JS assertions skipped')
else:
    FNS = ''.join(grab_fn(n) for n in ('_vmSale', 'fillScript', 'liveDays', '_vmVoiceWrong', '_vmWarnHtml'))
    script = {'vmsale': CM.VM_SALE}
    js = ('var SCRIPT_ALL=' + json.dumps(script, ensure_ascii=False) + ';\n'
          'var VMEN=' + json.dumps(CM.VOICEMAIL_EN, ensure_ascii=False) + ', VMES='
          + json.dumps(CM.VOICEMAIL_ES, ensure_ascii=False) + ';\n'
          "var SENDER={name:'Alejandro Gonzalez', phone:'(786) 631-1823', bsigner:''}, SEAT=null;\n"
          "var _SENDER_DEF={name:SENDER.name, phone:SENDER.phone};\n"
          "function esc(x){ return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }\n"
          "function firstName(r){ return (r&&r.f)||''; }\n"
          "function isBalloon(r){ return r.st==='BAL'; }\n"
          + FNS + r"""
function md(off){ var d=new Date(); d.setHours(12,0,0,0); d.setDate(d.getDate()+off);
  return (d.getMonth()+1)+'/'+d.getDate()+'/'+d.getFullYear(); }
var out = {};
var base = {f:'Maria', a:'123 NW 5 ST, MIAMI, FL 33142'};
function row(x){ return Object.assign({}, base, x); }
out.days_en = fillScript(VMEN, row({x: md(12), d: 99}));
out.days_es = fillScript(VMES, row({x: md(12), d: 99}));
out.tom_en = fillScript(VMEN, row({x: md(1), d: 1}));
out.tom_es = fillScript(VMES, row({x: md(1), d: 1}));
out.lp_en = fillScript(VMEN, row({x: md(-30), d: 9999, lp: 1}));
out.lp_es = fillScript(VMES, row({x: md(-30), d: 9999, lp: 1}));
out.past_en = fillScript(VMEN, row({x: md(-2), d: -2}));
out.noaddr_en = fillScript(VMEN, {f:'', x: md(5), d: 5});
out.other_en = fillScript('Hi {first}, about {st1}.', row({x: md(5), d: 5}));
out.wrong0 = _vmVoiceWrong();
SEAT = {n:2, i:0, w:'Alejandro'}; out.wrongA = _vmVoiceWrong();
SEAT = {n:2, i:1, w:'Carlos'}; out.wrongC = _vmVoiceWrong();
SENDER.name = 'Carlos Gonzalez'; out.wrongCdefphone = _vmVoiceWrong();
out.warnhtml = _vmWarnHtml();
SENDER.phone = '(305) 555-0100'; out.wrongC2 = _vmVoiceWrong(); out.warnnone = _vmWarnHtml();
SEAT = {n:2, i:0, w:'Alejandro'}; SENDER.name='Alejandro Gonzalez'; SENDER.phone='(786) 631-1823'; out.wrongA2 = _vmVoiceWrong();
out.noaddr_es = fillScript(VMES, {f:'', x: md(5), d: 5});
console.log(JSON.stringify(out));
""")
    fd, path = tempfile.mkstemp(suffix='.js')
    os.close(fd)
    io.open(path, 'w', encoding='utf-8').write(js)
    try:
        p = subprocess.run(['node', path], capture_output=True, text=True, encoding='utf-8', timeout=60)
    finally:
        os.remove(path)
    rec('node ran', p.returncode == 0, p.stderr)
    o = json.loads(p.stdout or '{}')
    EXP_EN = ("Hi Maria, this is Alejandro Gonzalez with Biscayne Solutions Group. Public records show a "
              "court sale scheduled on your property at 123 NW 5 ST in about 12 days. We work with "
              "homeowners in this situation, and there may be more than one way to handle it, depending "
              "on what you want to do with the house. If you'd like to talk it through, call me at "
              "(786) 631-1823. Again, that's (786) 631-1823. If now's not a good time, just let me know "
              "and I won't reach out again. Thank you.")
    EXP_ES = ("Hola Maria, le habla Alejandro Gonzalez de Biscayne Solutions Group. Según los registros "
              "públicos, hay una venta judicial programada para su propiedad en 123 NW 5 ST en unos 12 "
              "días. Trabajamos con propietarios en esta situación, y puede haber más de una manera de "
              "manejarlo, según lo que usted quiera hacer con la casa. Si desea conversarlo, llámeme al "
              "(786) 631-1823. Repito, (786) 631-1823. Si ahora no es buen momento, solo dígamelo y no lo "
              "vuelvo a contactar. Gracias.")
    rec('EN rendered exactly', o.get('days_en') == EXP_EN, o.get('days_en'))
    rec('ES rendered exactly', o.get('days_es') == EXP_ES, o.get('days_es'))
    rec('live days, not frozen r.d', 'in about 12 days' in o.get('days_en', ''))
    rec('tomorrow EN', 'at 123 NW 5 ST for tomorrow.' in o.get('tom_en', ''), o.get('tom_en'))
    rec('tomorrow ES', 'en 123 NW 5 ST para mañana.' in o.get('tom_es', ''), o.get('tom_es'))
    rec('no sale date EN', 'a court case filed on your property at 123 NW 5 ST.' in o.get('lp_en', ''), o.get('lp_en'))
    rec('no sale date ES', 'un caso judicial presentado sobre su propiedad en 123 NW 5 ST.' in o.get('lp_es', ''), o.get('lp_es'))
    rec('passed sale reads as case filed', 'court case filed' in o.get('past_en', ''), o.get('past_en'))
    rec('no name, no address still reads', o.get('noaddr_en', '').startswith('Hi, this is ')
        and 'scheduled on your property in about 5 days.' in o.get('noaddr_en', ''), o.get('noaddr_en'))
    rec('no address ES drops the clause', 'para su propiedad en unos 5 días.' in o.get('noaddr_es', '')
        and 'your property' not in o.get('noaddr_es', ''), o.get('noaddr_es'))
    rec('no token survives', all('{' not in str(v) for v in o.values()), o)
    rec('other scripts untouched', o.get('other_en') == 'Hi Maria, about 123 NW 5 ST.', o.get('other_en'))
    rec('whole-list page: no voice warning', o.get('wrong0') is False)
    rec('Alejandro seat with default sender: no warning', o.get('wrongA') is False)
    rec('Carlos seat with Alejandro default: warning', o.get('wrongC') is True)
    rec('Carlos name but Alejandro number: warning', o.get('wrongCdefphone') is True)
    rec('warning html names the page', 'Carlos' in o.get('warnhtml', '') and '&#9888;' in o.get('warnhtml', ''))
    rec('Carlos with his own name and number: no warning', o.get('wrongC2') is False and o.get('warnnone') == '')
    rec('Alejandro seat, own number: no warning', o.get('wrongA2') is False)
rec('guided vm step shows the warning too', "if(s.id==='vm') b+=_vmWarnHtml();" in SRC)
rec('full sheet shows the warning', SRC.count('_vmWarnHtml()') >= 3)

print('\n%s: %d failure(s)' % ('FAIL' if fails else 'PASS', len(fails)))
sys.exit(1 if fails else 0)
