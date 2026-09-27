"""outreach_mail.py --variant notice: refusal, county gate, clean output, both languages, and the
existing variants left alone.

WHAT THIS PINS (the notice letter's wording and layout are a PLACEHOLDER -- notice_letter_template.py
-- so nothing here asserts a specific sentence beyond the disclosure/opt-out keys every design keeps):

  1. REFUSAL. No mail-only phone, no call hours, or no return address / PMB -> NoticeConfigError and,
     from the CLI, a non-zero exit before any lead is read or any preview is written. The main line
     (786) 631-1823 and apartment-style return addresses are refused, not defaulted to. The refusal
     message names settings, never their values.
  2. COUNTY. Miami-Dade only by default; Broward / Palm Beach only when named; unknown never.
     Opting a county in does NOT bypass build_selection's holds (suppression, bankruptcy stay).
  3. CLEAN OUTPUT. No "TEMPLATE PREVIEW" tag, no blue placeholder class/colour, no [bracket]
     placeholders, no unfilled {tokens}, main line absent, lead text HTML-escaped.
  4. BOTH LANGUAGES. Exactly two pages, English first (front), Spanish second (back).
  5. EXISTING VARIANTS UNCHANGED. The default and jesse builders, the selection gates and the Lob
     sender are byte-identical to main; click2mail's defaults are still one-sided / separate page;
     'default' is still the default variant.

Every name, address and number below is invented. Run: python _noticelettertest.py
"""
import ast
import hashlib
import inspect
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import click2mail as C2M  # noqa: E402
import notice_letter as NL  # noqa: E402
import notice_letter_template as TPL  # noqa: E402
import outreach_mail as OM  # noqa: E402

FAIL, PASS = [], []


def rec(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print(('  ok   ' if ok else '  FAIL ') + name + (('  -- ' + str(detail)[:160]) if detail else ''))


NOWHERE = os.path.join(tempfile.gettempdir(), 'no-such-notice-config-%d.json' % os.getpid())
FAKE_PHONE = '(305) 555-0142'
FAKE_HOURS = 'Mon-Sat 9am-7pm'
FAKE_RET = '100 Example Blvd Ste 1 PMB 000 | Miami, FL 33100'
GOOD = {NL.ENV_PHONE: FAKE_PHONE, NL.ENV_HOURS: FAKE_HOURS, NL.ENV_RETURN: FAKE_RET,
        'BSG_NOTICE_LOCAL_CONFIG': NOWHERE}


def refuses(env):
    try:
        NL.load_config(env=env)
        return False, ''
    except NL.NoticeConfigError as ex:
        return True, str(ex)


def lead(**kw):
    r = {'case': '2099-000123-CA-01', 'owners': 'TESTER,PAT Q', 'addr': '1 FAKE ST',
         'city': 'MIAMI', 'zip': '33100', 'mail': '1 FAKE ST, MIAMI, FL 33100',
         'plaintiff': 'EXAMPLE BANK NA', 'auction': '2099-11-05', 'tier': 'A', 'days': 30}
    r.update(kw)
    return r


print('\n1. refusal without the three settings')
for var in (NL.ENV_PHONE, NL.ENV_HOURS, NL.ENV_RETURN):
    env = dict(GOOD)
    env.pop(var)
    ok, msg = refuses(env)
    rec('refuses when %s is missing' % var, ok and var in msg, msg)
    leaked = [v for k, v in GOOD.items() if k != var and k.startswith('BSG_NOTICE_') and k != 'BSG_NOTICE_LOCAL_CONFIG' and v in msg]
    rec('refusal message for %s prints no configured value' % var, not leaked, leaked)
    env[var] = '   '
    rec('refuses when %s is blank' % var, refuses(env)[0])
rec('refuses with nothing set', refuses({'BSG_NOTICE_LOCAL_CONFIG': NOWHERE})[0])
rec('accepts all three set', not refuses(GOOD)[0], refuses(GOOD)[1])
for bad in ('(786) 631-1823', '786-631-1823', '+1 786 631 1823', '7866311823'):
    rec('refuses the main line as the mail-only number: %s' % bad, refuses(dict(GOOD, **{NL.ENV_PHONE: bad}))[0])
rec('refuses a phone that is not 10 digits', refuses(dict(GOOD, **{NL.ENV_PHONE: '555-0142'}))[0])
for bad in ('200 Fake Ave Apt 4 | Miami, FL 33100', '200 Fake Ave #4 | Miami, FL 33100',
            '200 Fake Ave Apartment 4, Miami, FL 33100'):
    rec('refuses an apartment return address: %s' % bad, refuses(dict(GOOD, **{NL.ENV_RETURN: bad}))[0])
rec('refuses a return address without City, ST ZIP', refuses(dict(GOOD, **{NL.ENV_RETURN: 'PMB 000'}))[0])

# local config file: works when env is empty, env wins over it
tmpd = tempfile.mkdtemp()
try:
    cfgp = os.path.join(tmpd, 'notice_letter.local.json')
    open(cfgp, 'w', encoding='utf-8').write(
        '{"phone": "(305) 555-0199", "call_hours": "Weekdays", "return_address": "%s"}' % FAKE_RET)
    c = NL.load_config(env={'BSG_NOTICE_LOCAL_CONFIG': cfgp})
    rec('local config file supplies all three', c['phone'] == '(305) 555-0199')
    c = NL.load_config(env={'BSG_NOTICE_LOCAL_CONFIG': cfgp, NL.ENV_PHONE: FAKE_PHONE})
    rec('env var wins over the local config file', c['phone'] == FAKE_PHONE)
finally:
    shutil.rmtree(tmpd, ignore_errors=True)

def refuses_build():
    saved = dict(os.environ)
    try:
        for k in (NL.ENV_PHONE, NL.ENV_HOURS, NL.ENV_RETURN):
            os.environ.pop(k, None)
        os.environ['BSG_NOTICE_LOCAL_CONFIG'] = NOWHERE
        NL.build_notice_html(lead())
        return False
    except NL.NoticeConfigError:
        return True
    finally:
        os.environ.clear()
        os.environ.update(saved)


rec('build_notice_html itself refuses when config is missing', refuses_build())

# CLI: exit non-zero, before leads are read and before a preview is written
preview = OM.PREVIEW_FILE
before = os.path.getmtime(preview) if os.path.exists(preview) else None
env = {k: v for k, v in os.environ.items() if not k.startswith('BSG_NOTICE_')}
env.update({NL.ENV_PHONE: FAKE_PHONE, NL.ENV_HOURS: FAKE_HOURS, 'BSG_NOTICE_LOCAL_CONFIG': NOWHERE})
p = subprocess.run([sys.executable, os.path.join(HERE, 'outreach_mail.py'), '--variant', 'notice'],
                   cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
out = p.stdout + p.stderr
rec('CLI --variant notice exits non-zero without a return address', p.returncode != 0, 'rc=%s' % p.returncode)
rec('CLI refusal names the missing setting', NL.ENV_RETURN in out, out.strip()[-160:])
rec('CLI refusal does not echo the configured phone / hours', FAKE_PHONE not in out and FAKE_HOURS not in out)
rec('CLI refused before reading leads', 'MAIL QUEUE' not in out and 'No leads found' not in out)
after = os.path.getmtime(preview) if os.path.exists(preview) else None
rec('CLI refusal wrote no preview', before == after)
p = subprocess.run([sys.executable, os.path.join(HERE, 'outreach_mail.py'), '--notice-include-county', 'broward'],
                   cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
rec('--notice-include-county without --variant notice is refused', p.returncode != 0)
env_ok = dict(env, **{NL.ENV_RETURN: FAKE_RET})
p = subprocess.run([sys.executable, os.path.join(HERE, 'outreach_mail.py'), '--variant', 'notice', '--send',
                    '--vendor', 'lob'], cwd=HERE, env=env_ok, capture_output=True, text=True, timeout=120)
rec('--variant notice --send --vendor lob is refused (duplex notice ships via c2m only)',
    p.returncode != 0 and 'c2m' in (p.stdout + p.stderr))

print('\n2. county gate')
md = lead()
md_tag = lead(case='X-1', county='MIAMI-DADE')
bw = lead(case='CACE-99-000001', county='BROWARD')
bw_untagged = lead(case='CACE-99-000002')
pb = lead(case='502099CA000001XXXXMB', county='PALM BEACH')
unk = lead(case='SOMETHING-ELSE')
kept, sk = NL.filter_counties([md, md_tag, bw, bw_untagged, pb, unk])
rec('default keeps Miami-Dade only (tagged and case-number inferred)', kept == [md, md_tag], [r['case'] for r in kept])
rec('default excludes Broward (tagged and case-number inferred)', sk.get('notice:county-excluded(BROWARD)') == 2, dict(sk))
rec('default excludes Palm Beach', sk.get('notice:county-excluded(PALM BEACH)') == 1, dict(sk))
rec('unknown county is never mailed', sk.get('notice:county-unknown') == 1, dict(sk))
kept, _ = NL.filter_counties([md, bw, pb], include=['broward'])
rec('--notice-include-county broward adds Broward, not Palm Beach', kept == [md, bw])
kept, _ = NL.filter_counties([md, bw, pb], include=['broward', 'palm-beach'])
rec('both counties opt in only when both are named', kept == [md, bw, pb])
rec('allowed_counties default is exactly Miami-Dade', NL.allowed_counties() == {'MIAMI-DADE'})
try:
    NL.allowed_counties(['orange'])
    rec('unknown county name is refused', False)
except NL.NoticeConfigError:
    rec('unknown county name is refused', True)

# opted-in county still goes through build_selection's holds
kept, _ = NL.filter_counties([bw], include=['broward'])
q, sk = OM.build_selection(kept, None, 0, {bw['case']}, {}, False, 0, trust_selection=True)
rec('opted-in Broward lead is still suppressed by opt-out', not q and sk.get('suppressed(DNC/opt-out)') == 1, dict(sk))
bk = lead(case='CACE-99-000003', county='BROWARD', sale_bk_active=True)
kept, _ = NL.filter_counties([bk], include=['broward'])
q, sk = OM.build_selection(kept, None, 0, set(), {}, False, 0, trust_selection=True)
rec('opted-in Broward lead is still held by an active bankruptcy stay', not q and sk.get('active-bankruptcy-stay') == 1, dict(sk))
q, sk = OM.build_selection([md], None, 0, set(), {md['case']: {'date': '2099-01-01'}}, False, 0, trust_selection=True)
rec('already-mailed ledger still dedupes', not q and sk.get('already-mailed') == 1, dict(sk))

print('\n3/4. rendering: clean output, both languages')
cfg = NL.load_config(env=GOOD)
import datetime  # noqa: E402
doc = NL.build_notice_html(lead(owners='<SCRIPT>,EVIL'), cfg=cfg, today=datetime.date(2099, 10, 1))
doc_ok = NL.build_notice_html(lead(), cfg=cfg, today=datetime.date(2099, 10, 1))
rec('no TEMPLATE PREVIEW tag', 'TEMPLATE PREVIEW' not in doc_ok and 'FILLED PER LEAD' not in doc_ok and 'class="tag"' not in doc_ok)
rec('no blue placeholder styling', 'class="ph"' not in doc_ok and '.ph{' not in doc_ok and '#1d4ed8' not in doc_ok.lower())
rec('template CSS carries no placeholder/tag rules', '.ph' not in TPL.CSS and '.tag' not in TPL.CSS)
rec('no [bracket] placeholders left', not re.search(r'\[[A-Z][^\]]*\]', doc_ok), re.findall(r'\[[^\]]*\]', doc_ok)[:3])
body = doc_ok.split('<body>', 1)[1]
rec('no unfilled {tokens}', not re.search(r'\{[A-Za-z_]+\}', body), re.findall(r'\{[A-Za-z_]+\}', body)[:3])
pages = re.findall(r'<div class="page" lang="(en|es)">', doc_ok)
rec('exactly two pages, English front then Spanish back', pages == ['en', 'es'], pages)
en_part, es_part = doc_ok.split('<div class="page" lang="es">', 1)
rec('English page carries the English heading', TPL.STRINGS['en']['head'] in en_part)
rec('Spanish page carries the Spanish heading', TPL.STRINGS['es']['head'] in es_part)
rec('English disclosure + opt-out on the front', TPL.STRINGS['en']['opt'] in en_part and 'advertisement' in en_part)
rec('Spanish disclosure + opt-out on the back', TPL.STRINGS['es']['opt'] in es_part and 'anuncio' in es_part)
for label, val in (('owner', 'Pat Q Tester'), ('property address', '1 FAKE ST'), ('property city/ZIP', 'Miami, FL 33100'),
                   ('lender', 'EXAMPLE BANK NA'), ('case', '2099-000123-CA-01'), ('reference', 'BSG-2099-000123-CA-01'),
                   ('phone', FAKE_PHONE), ('hours', FAKE_HOURS), ('return PMB', 'PMB 000'),
                   ('EN sale date', 'November 5, 2099'), ('ES sale date', '5 de noviembre de 2099'),
                   ('EN mail date', 'October 1, 2099'), ('ES mail date', '1 de octubre de 2099')):
    rec('per-lead field present: %s' % label, val in doc_ok, val)
rec('main line never printed', '631-1823' not in doc_ok and '6311823' not in re.sub(r'\D', '', doc_ok))
rec('lead text is HTML-escaped', '<SCRIPT>' not in doc.upper().replace('&LT;SCRIPT&GT;', '') and '&lt;' in doc)
rec('template is marked as a placeholder', TPL.IS_PLACEHOLDER is True and 'PLACEHOLDER' in (TPL.__doc__ or ''))
rec('template carries no FINAL NOTICE wording', 'FINAL NOTICE' not in doc_ok.upper())

# incomplete leads are dropped, not mailed with a hole
q, sk = NL.filter_complete([(lead(), {}), (lead(plaintiff=''), {}), (lead(auction=''), {})])
rec('leads missing lender / sale date are dropped', len(q) == 1 and sum(sk.values()) == 2, dict(sk))

# optional: real PDF render (needs Chrome; CI ubuntu images ship google-chrome)
chrome = os.environ.get('CHROME_BIN') or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
if chrome and os.path.exists(chrome):
    C2M.CHROME = chrome
    out_pdf = os.path.join(tempfile.mkdtemp(), 'notice.pdf')
    try:
        NL.render_pdf(doc_ok, out_pdf)
        raw = open(out_pdf, 'rb').read()
        n = len(re.findall(rb'/Type\s*/Page(?!s)', raw))
        rec('PDF renders to exactly 2 pages', n == 2, 'pages=%d' % n)
    except Exception as ex:
        rec('PDF renders to exactly 2 pages', False, ex)
else:
    print('  skip PDF render (no Chrome on this machine)')

print('\n5. existing variants and gates unchanged')


def _src_hash(module, name):
    src = inspect.getsource(module).replace('\r\n', '\n')
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return hashlib.sha256(ast.get_source_segment(src, n).encode()).hexdigest()[:16]
    return None


# Source hashes as of main at 7d713a5. If one of these changes ON PURPOSE in a later PR, update the
# hash here in that PR -- this check exists so the notice variant cannot quietly change them.
FROZEN = {'build_letter_html': 'a065458a963197ad', 'build_letter_html_jesse': 'f6a25e1a491138ec',
          'build_selection': '7af69eaa27fd23ac', 'load_suppress': '41783b52a48ad138',
          '_is_suppressed_note': 'b9498fdfc624e3a2', 'send_via_lob': '6b10f77226d6767d',
          'parse_address': 'dbb99db1b707fb2a', '_owner_name': '974177d22fb78595',
          'load_queue': '28fb44de64e4cb02'}
for name, want in FROZEN.items():
    got = _src_hash(OM, name)
    rec('outreach_mail.%s unchanged' % name, got == want, 'got %s' % got)

snd = {'name': 'Test Sender', 'llc': 'Biscayne Solutions Group', 'phone': '(305) 555-0100', 'addr': '1 Test Way, Miami, FL 33100'}
d = OM.build_letter_html(lead(), snd, 'en')
j = OM.build_letter_html_jesse(lead(), snd, 'en')
rec('default letter is still one page and has no notice wording', d.count('class="page"') == 1 and TPL.STRINGS['en']['head'] not in d)
rec('jesse letter still carries its own header and the main line', 'FINAL NOTICE' in j and OM.CALLBACK_PHONE in j)

src = inspect.getsource(OM.main)
rec("'default' is still the default variant", re.search(r"'--variant', choices=\[[^\]]*\], default='default'", src) is not None)
sig = inspect.signature(C2M.create_job).parameters
rec('click2mail create_job still defaults to one side / separate address page',
    sig['layout'].default == 'Address on Separate Page' and sig['print_option'].default == 'Printing One side')
sig = inspect.signature(C2M.send_letter).parameters
rec('click2mail send_letter defaults unchanged',
    sig['layout'].default == 'Address on Separate Page' and sig['print_option'].default == 'Printing One side')
rec('notice duplex uses Printing both sides', NL.C2M_PRINT_OPTION == 'Printing both sides')

print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
