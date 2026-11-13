"""bsg_mail_variants (2026-11-13): three mail pieces from the handwrite pool, split evenly.

Pins: the pool is bsg_mail_campaign.pool() read from a data folder (opt-out, entity and stay drops
still apply, and a failed stay check still holds); a half-written input stops the run; a date that
does not parse is never an event and never a calendar; the split is even, repeatable and sticky; the
calendar grid is the real month with the circle on the sale day; every piece carries the MARS lines
and the opt-out lines in both languages, the callback number, no dollar sign and no mailbox number;
QR codes print only when asked; the private page keeps the person inside the encrypted envelope; the
ledger carries no name or address and marks nothing as mailed. Invented people only, temp
DEALFLOW_DIR, no network, no browser.
"""
import base64
import calendar
import datetime
import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()
os.environ['DEALFLOW_DIR'] = TMP
os.environ.pop('DEALFLOW_DATA_DIR', None)
sys.path.insert(0, HERE)
import bsg_mail_campaign as M  # noqa: E402
import bsg_mail_variants as V  # noqa: E402
import bk_lookup  # noqa: E402
import disclaimer as D  # noqa: E402
import outreach_copy as OC  # noqa: E402

fails = []
TODAY = datetime.date.today()
PHONE = '(305) 555-0100'


def rec(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:300]) if extra else ''))
    if not ok:
        fails.append(name)


def mdy(days):
    d = TODAY + datetime.timedelta(days=days)
    return '%d/%d/%d' % (d.month, d.day, d.year)


def write(folder, name, obj):
    with open(os.path.join(folder, name), 'w', encoding='utf-8') as fh:
        json.dump(obj, fh)


def data_folder():
    """An invented data folder: six people who pass, one opted out, one entity, one stale filing."""
    d = tempfile.mkdtemp()
    people = [('T-26-000001', 'GOMEZ,ANA'), ('T-26-000002', 'PEREZ,LUIS'), ('T-26-000003', 'DIAZ,ROSA'),
              ('T-26-000004', 'RUIZ,JOSE'), ('T-26-000005', 'LOPEZ,ELENA'), ('T-26-000006', 'MORA,IVAN'),
              ('T-26-OPTOUT', 'SOTO,MARIA'), ('T-26-ENTITY', 'ACME HOLDINGS LLC'), ('T-26-STALE', 'VEGA,OMAR')]
    write(d, 'lp_addresses.json', {c: {'case': c, 'confidence': 'high', 'paOwner': o, 'county': 'BROWARD',
                                       'addr': '%d NW 1ST AVE' % (100 + i), 'city': 'Fort Lauderdale',
                                       'zip': '33301'} for i, (c, o) in enumerate(people)})
    write(d, 'lis_pendens.json', [{'case': c, 'date': mdy(-200 if c == 'T-26-STALE' else -5 - i)}
                                  for i, (c, o) in enumerate(people)])
    write(d, 'optouts.json', {'notes': {'T-26-OPTOUT': {'optout': '2026-11-01'}}})
    write(d, 'leads_final.json', [{'Case #': 'T-26-000002', 'AuctionDate': mdy(40)},
                                  {'Case #': 'T-26-000003', 'AuctionDate': 'soon'},
                                  {'Case #': 'T-26-000004', 'AuctionDate': mdy(-3)}])
    write(d, 'judgment_dates.json', {'T-26-000002': {'d': (TODAY - datetime.timedelta(days=4)).isoformat()},
                                     'T-26-000005': {'d': 'not a date'}})
    write(d, 'broward_leads.json', [{'case': 'T-26-000006', 'lpClosed': True}])
    return d


M.SENDER['name'], M.SENDER['phone'], M.SENDER['co'] = 'Sam Sample', PHONE, 'Sample Solutions Group'
M.MAIL_TRACKING_PHONE = ''

# ── the data folder switch ──
rec('data_dir() defaults to the module folder', M.data_dir() == M.HERE and M.DATA_DIR is None)
DD = data_folder()
prev = M.set_data_dir(DD)
rec('set_data_dir points reads at the folder and returns the old setting',
    prev is None and M.data_dir() == os.path.abspath(DD) and len(M._optout_keys()) == 1)
M.set_data_dir(None)
rec('set_data_dir(None) restores the default', M.data_dir() == M.HERE)

# ── pool through select(): same drops as the handwrite book ──
REAL_STAY = bk_lookup.contact_blocked_reason
bk_lookup.contact_blocked_reason = lambda case, here=None: (True, 'held')
rows, drops, eligible, counts = V.select(30, DD, TODAY)
rec('a held stay check holds every lead (fails closed)', rows == [] and drops['bankruptcy stay check'] == 8, drops)
seen_here = []


def _clear(case, here=None):
    seen_here.append(here)
    return (False, '')


bk_lookup.contact_blocked_reason = _clear
rows, drops, eligible, counts = V.select(30, DD, TODAY)
cases = [r['case'] for r in rows]
rec('opted-out, entity-owned and stale leads are dropped by pool()',
    drops['opted out'] == 1 and drops['entity-owned'] == 1 and drops['stale filing'] == 1
    and not {'T-26-OPTOUT', 'T-26-ENTITY', 'T-26-STALE'} & set(cases), drops)
rec('a dismissed or closed case is dropped', 'T-26-000006' not in cases and drops['case dismissed or closed'] == 1)
rec('five invented people are eligible', len(rows) == 5 and eligible == 5, cases)
rec('the stay check reads the snapshot folder, not this checkout',
    bool(seen_here) and all(h and h != M.HERE and h != DD for h in seen_here))
rec('the data folder setting is restored after the read', M.data_dir() == M.HERE)
by = {r['case']: r for r in rows}
rec('a real future sale date is a fact', by['T-26-000002']['facts']['sale'] == TODAY + datetime.timedelta(days=40))
rec('an unparseable sale date is no sale date', by['T-26-000003']['facts']['sale'] is None)
rec('a sale date already past is no sale date', by['T-26-000004']['facts']['sale'] is None)
rec('an unparseable judgment date is no judgment', by['T-26-000005']['facts']['judgment'] is None)
rec('event counts come from parsed dates only',
    counts['filed (notice of the case recorded)'] == 5 and counts['final judgment entered'] == 1
    and counts['sale date set (ahead)'] == 1, counts)

empty = tempfile.mkdtemp()
for n in ('lp_addresses.json', 'lis_pendens.json'):
    write(empty, n, [])
write(empty, 'optouts.json', {})
try:
    V.select(30, empty, TODAY)
    rec('an empty opt-out ledger refuses the run', False)
except V.DataError as e:
    rec('an empty opt-out ledger refuses the run', 'opt-out ledger' in str(e))

# ── half-written inputs ──
torn = data_folder()
with open(os.path.join(torn, 'lp_addresses.json'), 'w', encoding='utf-8') as fh:
    fh.write('{"T-26-000001": {"case": "T-26-0')
try:
    V.snapshot(torn, tries=2, wait=0)
    rec('a half-written input stops the run', False)
except V.DataError as e:
    rec('a half-written input stops the run', 'lp_addresses.json' in str(e) and 'Nothing was built' in str(e), e)
os.remove(os.path.join(torn, 'optouts.json'))
try:
    V.snapshot(torn, tries=1, wait=0)
    rec('a missing required input stops the run', False)
except V.DataError as e:
    rec('a missing required input stops the run', 'optouts.json is missing' in str(e))

# ── dates and events ──
rec('parse_date takes both formats', V.parse_date('11/02/2026', TODAY) == datetime.date(2026, 11, 2)
    and V.parse_date('2026-11-02 00:00', TODAY) == datetime.date(2026, 11, 2))
rec('parse_date never guesses', all(V.parse_date(x, TODAY) is None for x in
                                    ('', None, 'TBD', '13/45/2026', '2026-02-30', '1/1/1901', '1/1/2099', 'soon')))
F = {'case': 'X', 'county': 'Broward', 'filed': TODAY - datetime.timedelta(days=5), 'sale': None, 'judgment': None}
rec('a filing 5 days ago is an event inside 7 days', V.recent_event(F, 7, TODAY) == ('filed', F['filed'], 5))
rec('and is not an event inside 3 days', V.recent_event(F, 3, TODAY) is None)
rec('no parsed date, no event', V.recent_event(dict(F, filed=None), 30, TODAY) is None
    and V.event_lines(dict(F, filed=None), TODAY) is None)
en, es = V.event_lines(F, TODAY)
rec('the filing event is named in both languages with the day count',
    V.date_en(F['filed']) in en and '5 days' in en and V.date_es(F['filed']) in es and '5 días' in es)
rows_t, _, elig_t, _ = V.select(30, DD, TODAY, trigger_days=4)
rec('--trigger-days keeps only leads with an event in the window',
    elig_t == 5 and sorted(r['case'] for r in rows_t) == ['T-26-000002'] and rows_t[0]['event'] == 'judgment',
    [(r['case'], r.get('event')) for r in rows_t])
bk_lookup.contact_blocked_reason = REAL_STAY

# ── the split ──
C90 = ['CASE-%03d' % i for i in range(90)]
a1 = V.assign(C90)
rec('90 leads split 30 / 30 / 30', sorted(list(a1.values()).count(v) for v in 'ABC') == [30, 30, 30])
rec('the same run gives the same split', V.assign(list(reversed(C90))) == a1)
a2 = V.assign(C90 + ['NEW-1', 'NEW-2', 'NEW-3'], a1)
rec('a case in the ledger keeps its arm when new cases arrive', all(a2[c] == a1[c] for c in C90)
    and sorted(a2[c] for c in ('NEW-1', 'NEW-2', 'NEW-3')) == ['A', 'B', 'C'])
fr = [{'case': c, 'assigned': a1[c], 'facts': {'case': c, 'sale': None}} for c in C90]
cnt = V.printed_variants(fr)
rec('calendar assignments with no sale date fall evenly to A and C',
    cnt == {'A': 45, 'B': 0, 'C': 45} and sum(1 for r in fr if r['fallback']) == 30, cnt)
keep = {r['case']: r['variant'] for r in fr}
fr2 = [{'case': c, 'assigned': a1[c], 'facts': {'case': c, 'sale': None}} for c in reversed(C90)]
V.printed_variants(fr2)
rec('the fallback is repeatable', {r['case']: r['variant'] for r in fr2} == keep)

# ── the calendar ──
ok = True
for y, m in ((2026, 12), (2027, 1), (2027, 2), (2028, 2)):
    g = V.month_grid(y, m)
    days = [d for w in g for d in w if d]
    ok = ok and days == list(range(1, calendar.monthrange(y, m)[1] + 1)) and all(len(w) == 7 for w in g)
    first = datetime.date(y, m, 1)
    ok = ok and g[0].index(1) == (first.weekday() + 1) % 7          # Sunday-first column
rec('the grid is the real month, Sunday first', ok)
h = V.calendar_html(datetime.date(2027, 1, 21))
rec('exactly one day is circled and it is the sale day',
    h.count('class="hit"') == 1 and h.count('class="ring"') == 1 and bool(re.search(r'class="hit"><svg.*?</svg>21</td>', h))
    and 'January 2027' in h and 'enero de 2027' in h)

# ── the pieces ──
SR = V.sample_rows(TODAY)
V.printed_variants(SR)
INFO = {'with_qr': False, 'base_url': 'https://example.invalid/p/', 'eligible': len(SR)}
doc, pieces = V.build_html(SR, TODAY, INFO)
rec('the sample covers all three variants and the fallback',
    {r['variant'] for r in SR} == {'A', 'B', 'C'} and sum(1 for r in SR if r['fallback']) == 1)
need = [V._e(D.mars_part('govt', 'may_agree', 'may_stop', lang=l, company=M.SENDER['co'], as_html=False))
        for l in ('en', 'es')] + [V._e(OC.OPTOUT_LINE_EN), V._e(OC.OPTOUT_LINE_ES), PHONE]
rec('every piece carries the MARS lines, the opt-out lines and the phone, EN and ES',
    all(all(n in p for n in need) for p in pieces))
text = [re.sub(r'<svg.*?</svg>', '', p, flags=re.S) for p in pieces]
rec('no piece carries a dollar sign', not any('$' in t for t in text))
rec('no piece carries a mailbox designator', not any(re.search(r'\bPMB\b|P\.?\s?O\.?\s+BOX', t, re.I) for t in text))
rec('no outcome promise on any piece',
    not any(re.search(r'guarantee|stop (your|the) foreclosure|reverse|from the bank|save your (home|house)', t, re.I)
            for t in text))
rec('B and C open with the house line in both languages',
    all(V._e(V.LEAD_EN) in t and V._e(V.LEAD_ES) in t for r, t in zip(SR, text) if r['variant'] in 'BC'))
A = next(r for r in SR if r['variant'] == 'A' and not r.get('event'))
en_paras = M.letter(A).rsplit('\n\n', 1)[0].split('\n\n')
rec('A is bsg_mail_campaign.letter() word for word', all(V._e(p) in V.piece_html(A, TODAY) for p in en_paras))
rec('one sheet per piece and one cover', doc.count('<section class="pc') == len(SR) + 1)
rec('the cover carries the split, the postage and the checklist',
    'The split' in doc and '$%.2f' % (len(SR) * M.PER_LETTER) in doc and 'Real stamp' in doc)
has_qr = bool(V.qr_svg('https://example.invalid/'))
rec('a piece shows a QR only when asked', all(('class="qr"' in p) == bool(r.get('with_qr') and has_qr)
                                              for r, p in zip(SR, pieces)), 'qr library: %s' % has_qr)
plain = [dict(r, with_qr=None) for r in SR]
for r in plain:
    r.pop('with_qr')
_, off = V.build_html(plain, TODAY, INFO)
rec('QR is off by default', not any('class="qr"' in p for p in off))
if has_qr:
    _, on = V.build_html(plain, TODAY, dict(INFO, with_qr=True))
    rec('--with-qr prints the QR and the code on every piece',
        all('class="qr"' in p and V.show_code(r['code']) in p for r, p in zip(plain, on)))
B = next(r for r in SR if r['variant'] == 'B')
try:
    V.check_pieces([V.piece_html(B, TODAY).replace(V._e(OC.OPTOUT_LINE_ES), '')], [B])
    rec('a piece missing an opt-out line refuses the run', False)
except V.DataError:
    rec('a piece missing an opt-out line refuses the run', True)
try:
    V.check_pieces([V.piece_html(B, TODAY)], [B], never=['Sam Sample'])
    rec('a never-print string refuses the run', False)
except V.DataError:
    rec('a never-print string refuses the run', True)

# ── private pages ──
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # noqa: E402


def unwrap(env, code):
    b = base64.b64decode
    for w in env['keys']:
        try:
            wk = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b(w['salt']),
                            iterations=env['it']).derive(code.encode())
            meta = json.loads(AESGCM(wk).decrypt(b(w['iv']), b(w['ct']), None))
            return json.loads(AESGCM(b(meta['mk'])).decrypt(b(env['iv']), b(env['ct']), None))
        except Exception:
            pass
    return None


C6 = SR[5]
page = V.page_html(C6, TODAY)
env = json.loads(re.search(r'var ENC=(\{.*?\});', page).group(1))
clear = page.replace(json.dumps(env), '')
rec('the page shell carries no name, case, phone or address in clear text',
    not any(x in clear for x in (C6['first'], C6['case'], PHONE, 'Sam Sample', '600 SW')))
data = unwrap(env, C6['code'])
rec('the code opens the page: first name, case, both languages, the call number',
    bool(data) and C6['first'] in data['en'] and C6['case'] in data['es'] and data['tel'] == 'tel:+13055550100'
    and V._e(V.LEAD_ES) in data['es'] and V._e(OC.OPTOUT_LINE_EN) in data['en'])
rec('a wrong code does not open it', unwrap(env, 'WRONG123') is None)
rec('the page carries no dollar sign and no player without a recording',
    '$' not in data['en'] + data['es'] and data['voice'] == {})
vdir = tempfile.mkdtemp()
src = os.path.join(vdir, 'hello.mp3')
with open(src, 'wb') as fh:
    fh.write(b'ID3')
pages = os.path.join(TMP, 'pages_t')
voice = V.write_pages(SR[:2], TODAY, pages, voice_en=src)
rec('a supplied recording is copied beside the pages and only that language plays',
    voice == {'en': '../voice-en.mp3'} and os.path.exists(os.path.join(pages, 'voice-en.mp3'))
    and os.path.exists(os.path.join(pages, SR[0]['slug'], 'index.html')))
try:
    V.copy_voice(pages, voice_es=os.path.join(vdir, 'missing.txt'))
    rec('a recording that is not an audio file refuses', False)
except V.DataError:
    rec('a recording that is not an audio file refuses', True)
rec('the page address is base + slug, the code only after the #',
    V.page_url('https://example.invalid/p/', 'abc', 'CODE1234') == 'https://example.invalid/p/abc/#CODE1234')

# ── a whole run into the temp DEALFLOW_DIR (no PDF: no browser in this test) ──
bk_lookup.contact_blocked_reason = lambda case, here=None: (False, '')
before = set(os.listdir(HERE))
args = ['--days', '30', '--limit', '4', '--data-dir', DD, '--signer-name', 'Sam Sample', '--signer-phone', PHONE,
        '--no-pdf']
buf = io.StringIO()
with redirect_stdout(buf):
    rc = V.main(args)
out = buf.getvalue()
led_p = os.path.join(TMP, V.LEDGER)
rec('the run finishes and writes under DEALFLOW_DIR only',
    rc == 0 and os.path.exists(led_p) and os.path.isdir(os.path.join(TMP, V.PAGES_DIR))
    and set(os.listdir(HERE)) == before, out[-300:])
led = json.load(open(led_p, encoding='utf-8'))
raw = open(led_p, encoding='utf-8').read().upper()
rec('the ledger holds case, arm, date and page code and nothing else about the person',
    len(led['cases']) == 4 and all(set(v) <= {'assigned', 'variant', 'date', 'code', 'page', 'fallback', 'event'}
                                   and v['date'] == TODAY.isoformat() and len(v['code']) == 8
                                   for v in led['cases'].values()))
rec('no name and no address in the ledger',
    not any(x in raw for x in ('GOMEZ', 'PEREZ', 'DIAZ', 'RUIZ', 'LOPEZ', 'NW 1ST', '33301', 'LAUDERDALE')))
rec('nothing is marked as mailed and the route print ledger is untouched',
    'mailed' not in json.dumps(led['cases']).lower() and not os.path.exists(os.path.join(TMP, M.ROUTE_LEDGER)))
rec('the summary prints counts per variant', 'assigned: A' in out and 'printed : A' in out)
with redirect_stdout(io.StringIO()):
    V.main(args)
led2 = json.load(open(led_p, encoding='utf-8'))
rec('a rerun keeps every arm, page and code',
    {c: (v['assigned'], v['variant'], v['code'], v['page']) for c, v in led['cases'].items()}
    == {c: (v['assigned'], v['variant'], v['code'], v['page']) for c, v in led2['cases'].items()})
with open(led_p, 'w', encoding='utf-8') as fh:
    fh.write('{"cases": [')
buf = io.StringIO()
with redirect_stdout(buf):
    rc = V.main(args)
rec('an unreadable ledger refuses the run', rc == 2 and 'unreadable' in buf.getvalue())
os.remove(led_p)
M.SENDER['name'] = ''
buf = io.StringIO()
with redirect_stdout(buf):
    rc = V.main(['--days', '30', '--data-dir', DD, '--signer-phone', PHONE, '--no-pdf'])
rec('no signer name refuses the run', rc == 2 and 'REFUSED' in buf.getvalue() and not os.path.exists(led_p))
bk_lookup.contact_blocked_reason = REAL_STAY

print()
print('all checks passed' if not fails else '%d FAILED: %s' % (len(fails), '; '.join(fails)))
sys.exit(1 if fails else 0)
