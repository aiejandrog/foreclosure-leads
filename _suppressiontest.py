"""Every outbound channel must refuse a person who said stop. One suite, all six.

WHY THIS EXISTS
On 2026-08-26 the same defect was found in FOUR surfaces on the same afternoon, one at a time, by
looking at whatever system sat next to the last one:

    morning worker   _workerEligible checked only the opt-out on THIS case
    call mode        call_rows checked only `case in optouts`
    doors            _live_lead promised "no door either" and checked only the case
    lob mail         suppression was an OPTIONAL --suppress flag; no flag, no suppression

Each surface carries its OWN copy of the gate, so fixing one told the others nothing. Nothing in
the repo would have failed if a seventh surface shipped without it, or if one of these six quietly
regressed. That is what this file is for. It is not a unit test of a function — it is the question
"can any channel still reach this person" asked of every channel at once.

TWO KEY SHAPES, BOTH REAL — a fix that handles one and not the other is a no-op in production:
    optouts.json  stores '@someone@gmail.com'   RAW      (every server-side caller reads this)
    the board bake stores '@'+_addr_key(email)  HASHED   (so the public page carries no address)
Matching hashed-only is exactly the mistake that made two of the four fixes above do nothing on
real data, and every synthetic test passed anyway because the fixture used the assumed shape.
So this asserts BOTH, and the last check asserts the real ledger file directly.

Gitignored-by-default (_*.py); add a !negation to .gitignore to carry it across machines. The
access code is read from site.codes at runtime, never hardcoded — see _live_code().
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    line = ('  PASS ' if ok else '  FAIL ') + name + (' | ' + detail if detail else '')
    print(line.encode('ascii', 'replace').decode('ascii'))


def _live_code():
    """Access code from the gitignored site.codes. NEVER hardcode one in a file that may be
    tracked — a real code was committed to this PUBLIC repo that way (69884b5)."""
    p = os.path.join(HERE, 'site.codes')
    try:
        for line in open(p, encoding='utf-8'):
            m = re.search(r'(DEALFLOW-[A-Z0-9]{6,})', line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


EMAIL = 'stoptest@example.com'
PHONE = '3055550199'
CASE = 'ZZ-OPTOUT-TEST'

import foreclosure_leads as F                                    # noqa: E402  (needs HERE first)

# Both shapes, exactly as the two producers write them.
LEDGER_RAW = {CASE: {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'},
              '@' + EMAIL: {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'},
              '#' + PHONE: {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'}}
LEDGER_HASHED = {CASE: {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'},
                 '@' + F._addr_key(EMAIL): {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'},
                 '#' + F._addr_key(PHONE): {'optout': '2026-01-01', 'status': 'DO NOT CONTACT'}}

print('=== outbound suppression — every channel, both ledger shapes ===\n')

# ---- 1. DOORS (_carlos_route._live_lead) -----------------------------------------------------
import _carlos_route as CR                                       # noqa: E402

# Go through _live_lead(), the gate every door caller actually uses — NOT _identity_opted(),
# the helper it calls. Written the helper way first, and a mutation test that disabled the gate
# inside _live_lead still passed 17/17: the suite was asserting a function nobody had broken while
# the real guard was gone. Test the door, not the lock it happens to contain.
_FUTURE = {'case': 'ZZ-DOOR', 'AuctionDate': '12/31/2026', 'sale_type': 'FC'}


def _door_allows(skip, ledger):
    return CR._live_lead(dict(_FUTURE), skip, siblings={}, optouts=ledger)


for shape, ledger in (('raw', LEDGER_RAW), ('hashed', LEDGER_HASHED)):
    by_email = {'emails': [EMAIL.upper() + ' '], 'phones': []}
    by_phone = {'emails': [], 'phones': [{'number': '(305) 555-0199'}]}
    clean = {'emails': ['fine@example.com'], 'phones': [{'number': '7865550000'}]}
    rec('doors: identity email opt-out gets NO door (%s)' % shape,
        _door_allows(by_email, ledger) is False)
    rec('doors: identity phone opt-out gets NO door (%s)' % shape,
        _door_allows(by_phone, ledger) is False)
    rec('doors: clean lead still gets a door (%s)' % shape,
        _door_allows(clean, ledger) is True)

# a DNC-flagged number must still match — it is exactly the kind that carries an opt-out
rec('doors: DNC-flagged phone still blocks the door',
    _door_allows({'emails': [], 'phones': [{'number': PHONE, 'dnc': True}]}, LEDGER_RAW) is False)
# and the case-keyed path must keep working through the same gate
rec('doors: case-keyed opt-out gets NO door',
    CR._live_lead({'case': CASE, 'AuctionDate': '12/31/2026', 'sale_type': 'FC'},
                  {'emails': [], 'phones': []}, siblings={}, optouts=LEDGER_RAW) is False)

# ---- 2. CALL MODE (call_mode.call_rows) ------------------------------------------------------
import call_mode                                                 # noqa: E402

for shape, ledger in (('raw', LEDGER_RAW), ('hashed', LEDGER_HASHED)):
    leads = [
        {'case': 'A-EMAIL', 'emails': [EMAIL], 'phones': ['7865551111'], 'eq': 20},
        {'case': 'B-PHONE', 'emails': [], 'phones': [PHONE], 'eq': 20},
        {'case': CASE, 'emails': [], 'phones': ['7865552222'], 'eq': 20},
    ]
    rows, _ = call_mode.call_rows(leads, optouts=ledger, deads={})
    got = {str(r.get('c') or r.get('case') or '') for r in rows}
    rec('call mode: no opted-out lead survives (%s)' % shape,
        not (got & {'A-EMAIL', 'B-PHONE', CASE}), 'survivors: %s' % (sorted(got) or 'none'))

# ---- 3. LETTERS (carlos_letter_packet) -------------------------------------------------------
import carlos_letter_packet as CLP                               # noqa: E402

_opt_cases = CLP._optout_cases()
rec('letters: reads the real ledger and returns case keys only',
    isinstance(_opt_cases, set) and not any(str(k).startswith(('@', '#')) for k in _opt_cases),
    '%d case(s)' % len(_opt_cases))
# the lane is DEFINED as leads with no phone and no email, so identity cannot apply there —
# assert that framing still holds rather than pretending to test a match that cannot exist
try:
    _rows = json.load(open(os.path.join(HERE, '_letter_rows.json'), encoding='utf-8'))
    rec('letters: lane still has zero contactable rows (identity is moot)',
        not any((r.get('phones') or r.get('emails')) for r in _rows), '%d rows' % len(_rows))
except FileNotFoundError:
    rec('letters: lane still has zero contactable rows (identity is moot)', True,
        '_letter_rows.json absent — nothing to generate from')

# ---- 4. LOB MAIL (outreach_mail.load_suppress) -----------------------------------------------
import outreach_mail as OM                                       # noqa: E402

# Call it the way main() does — with the operator's flag value, i.e. None when they forgot it.
# Testing load_suppress(optouts.json) instead would prove only that the function CAN read the
# ledger, not that a run without the flag is suppressed; the union used to live in main(), where
# no test could see it. It lives in the function now, so this asserts the real default.
_srv = OM.load_suppress(None)
rec('lob: no --suppress flag, server ledger still suppresses',
    bool(_srv) and any(not str(c).startswith(('@', '#')) for c in _srv),
    '%d key(s) with path=None' % len(_srv))

# ---- 5. THE REAL LEDGER — the check that would have caught the hashed-only mistake ------------
_real = {}
try:
    _raw = json.load(open(os.path.join(HERE, 'optouts.json'), encoding='utf-8')) or {}
    _real = _raw.get('notes') if isinstance(_raw.get('notes'), dict) else _raw
except Exception:
    pass
_real_ident = [k for k in _real if str(k).startswith(('@', '#'))]
if _real_ident:
    _ok = True
    for k in _real_ident:
        val = str(k)[1:].strip().lower()
        skip = ({'emails': [val], 'phones': []} if str(k).startswith('@')
                else {'emails': [], 'phones': [val]})
        if not CR._identity_opted(skip, _real):
            _ok = False
    rec('REAL optouts.json: every identity key actually suppresses', _ok,
        '%d identity key(s)' % len(_real_ident))
else:
    rec('REAL optouts.json: every identity key actually suppresses', True,
        'no identity keys in the ledger today')

# ---- 6. BOARD + MORNING WORKER (browser) ------------------------------------------------------
CODE = _live_code()
if not CODE:
    rec('board/worker: person-level gate (needs site.codes)', False, 'no site.codes on this machine')
else:
    import functools
    import http.server
    import socketserver
    import threading
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sync_playwright = None
    if sync_playwright is None:
        rec('board/worker: person-level gate (needs playwright)', False, 'playwright not installed')
    else:
        DOCS = os.path.join(HERE, 'docs')
        PORT = 8901
        Handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DOCS)

        class _Q(socketserver.TCPServer):
            allow_reuse_address = True

        srv = _Q(('127.0.0.1', PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context().new_page()
            pg.goto('http://127.0.0.1:%d/index.html' % PORT, wait_until='domcontentloaded')
            pg.wait_for_selector('#gatepw', timeout=20000)
            pg.fill('#gatepw', CODE)
            pg.click('#gatego')
            pg.wait_for_selector('#tbl tbody tr[data-case]', timeout=30000)
            out = pg.evaluate("""(a) => {
                // seed BOTH shapes into notes, the way each producer would
                notes['@' + a.email] = {status:'DO NOT CONTACT', optout:'2026-01-01'};
                notes['@' + a.hash]  = {status:'DO NOT CONTACT', optout:'2026-01-01'};
                _OOP = null;                       // bust the identity-set cache
                var lead  = {case:'ZZ-1', emails:[a.email.toUpperCase()+' '], phones:[]};
                var hlead = {case:'ZZ-2', emails:['other@example.com'], phones:[]};
                var clean = {case:'ZZ-3', emails:['fine@example.com'], phones:['7865550000']};
                return {
                  person_raw   : _isOptedOutPerson(lead),
                  worker_raw   : _workerEligible(lead),
                  text_blocked : _textContactBlocked(lead),
                  clean_person : _isOptedOutPerson(clean),
                  clean_worker : _workerEligible(clean)
                };
            }""", {'email': EMAIL, 'hash': F._addr_key(EMAIL)})
            rec('board: _isOptedOutPerson matches a raw identity key', out['person_raw'] is True)
            rec('worker: _workerEligible REFUSES an opted-out person', out['worker_raw'] is False)
            rec('board: every channel blocked via _textContactBlocked',
                out['text_blocked'] == 'optout', str(out['text_blocked']))
            rec('board: a clean lead is not suppressed', out['clean_person'] is False)
            # ---- FTSA SEND WINDOW (FS 501.059) ------------------------------------------------
            # Same question as the rest of this file — "can this channel reach the person right
            # now" — with time as the gate instead of consent. 8am-8pm ET, $500-$1,500 per message
            # outside it. Lives here rather than in _dnctest because _dnctest is gitignored AND
            # hardcodes a live access code, so coverage put there survives on exactly one laptop
            # and could never be tracked without publishing the code.
            # FTSA_HR_END is EXCLUSIVE: 19:00 sends, 20:00 does not.
            win = pg.evaluate("""() => {
                const lead = DATA.find(x => (x.phones||[]).length && !x.saleBkAct);
                if(!lead) return null;
                const real = _flHour, o = {};
                [7, 8, 10, 19, 20, 23].forEach(h => {
                    _flHour = () => h;
                    const html = textCardHtml(lead);
                    o[h] = { live: /<a class="txsend"/.test(html) && /href="sms:/.test(html),
                             wa:   /<a class="txwa"/.test(html),
                             off:  /<span class="txsend off"/.test(html) };
                });
                _flHour = real;
                return o;
            }""")
            if not win:
                rec('FTSA window: a textable lead exists to test', False, 'none on this board')
            else:
                rec('FTSA window: 8am / 10am / 7pm CAN send',
                    all(win[str(h)]['live'] for h in (8, 10, 19)),
                    str({h: win[str(h)]['live'] for h in (8, 10, 19)}))
                rec('FTSA window: 7am / 8pm / 11pm CANNOT send',
                    not any(win[str(h)]['live'] for h in (7, 20, 23)),
                    str({h: win[str(h)]['live'] for h in (7, 20, 23)}))
                rec('FTSA window: no sms: href survives outside it',
                    all(win[str(h)]['off'] for h in (7, 20, 23)))
                rec('FTSA window: WhatsApp rides the same gate (no side door)',
                    not any(win[str(h)]['wa'] for h in (7, 20, 23)),
                    'WA live outside hours: %s' % [h for h in (7, 20, 23) if win[str(h)]['wa']])

            # ---- FTSA 3-TOUCH BURST CAP (FTSA_MAX_24H) ----------------------------------------
            # Third limit on the same question, and the third with no coverage: FS 501.059 caps
            # telephonic solicitation, and 4+ contacts inside 24 hours is the burst pattern that
            # reads as harassment regardless of the hour or the consent. _recentTeleCount counts
            # call+text touches in a rolling 24h; at FTSA_MAX_24H the composer stops offering send.
            # Seeded on a COPY of the note so the board's real notes are untouched.
            cap = pg.evaluate("""(maxN) => {
                const lead = DATA.find(x => (x.phones||[]).length && !x.saleBkAct);
                if(!lead) return null;
                const key = lead.case, saved = notes[key];
                const mk = n => ({touches: Array.from({length:n}, () => ({ch:'call', tsu: Date.now()-3600000}))});
                const realHour = _flHour; _flHour = () => 10;      // inside the window, isolate the cap
                const out = {};
                [0, maxN - 1, maxN, maxN + 2].forEach(n => {
                    notes[key] = mk(n);
                    const html = textCardHtml(lead);
                    out[n] = { counted: _recentTeleCount(lead),
                               live: /<a class="txsend"/.test(html) && /href="sms:/.test(html) };
                });
                notes[key] = saved; _flHour = realHour;
                return {max: maxN, rows: out};
            }""", pg.evaluate('() => FTSA_MAX_24H'))
            if not cap:
                rec('FTSA 24h cap: a textable lead exists to test', False, 'none on this board')
            else:
                m = cap['max']
                rec('FTSA 24h cap: counts the seeded telephonic touches',
                    cap['rows'][str(m)]['counted'] == m, str({k: v['counted'] for k, v in cap['rows'].items()}))
                rec('FTSA 24h cap: under the cap, sending is still offered',
                    cap['rows']['0']['live'] and cap['rows'][str(m - 1)]['live'],
                    '0 and %d touches' % (m - 1))
                rec('FTSA 24h cap: AT the cap, sending is withheld',
                    not cap['rows'][str(m)]['live'], '%d touches in 24h' % m)
                rec('FTSA 24h cap: over the cap, still withheld',
                    not cap['rows'][str(m + 2)]['live'], '%d touches in 24h' % (m + 2))

            # ---- LIFETIME LADDER (TEXT_MAX_TOTAL) ---------------------------------------------
            # The last named contact limit. Distinct from the 24h burst cap above: that one is a
            # rate, this one is a TOTAL. Three texts to a human about their foreclosure is the
            # whole relationship — after that the ladder retires them permanently, and touch 3's
            # copy literally says "this is my last message", so a fourth send makes that a lie.
            # The touches carry ch:'text' with no inbound marker; an inbound one ends the ladder
            # early and must NOT be counted as a send, which is asserted separately.
            lad = pg.evaluate("""(maxN) => {
                const lead = DATA.find(x => (x.phones||[]).length && !x.saleBkAct);
                if(!lead) return null;
                const key = lead.case, saved = notes[key];
                const realHour = _flHour; _flHour = () => 10;   // isolate: window and burst cap open
                const sends = n => ({touches: Array.from({length:n}, () => ({ch:'text', d:'2026-01-0'+1}))});
                const out = {};
                [0, 1, 2, maxN, maxN + 1].forEach(n => {
                    notes[key] = sends(n);
                    out[n] = { stage: _textStage(lead),
                               live: /<a class="txsend"/.test(textCardHtml(lead)) };
                });
                // an inbound reply is not a send: 1 text + 1 "replied" must read as replied, not 2 sends
                notes[key] = {touches: [{ch:'text', d:'2026-01-01'},
                                        {ch:'text', d:'2026-01-02', out:'replied'}]};
                out.inbound = { stage: _textStage(lead), sends: _textPersonHist(lead).sends };
                notes[key] = saved; _flHour = realHour;
                return {max: maxN, rows: out};
            }""", pg.evaluate('() => TEXT_MAX_TOTAL'))
            if not lad:
                rec('lifetime ladder: a textable lead exists to test', False, 'none on this board')
            else:
                M = lad['max']
                rec('lifetime ladder: climbs cold -> follow -> final',
                    [lad['rows'][str(i)]['stage'] for i in (0, 1, 2)] == ['cold', 'follow', 'final'],
                    str([lad['rows'][str(i)]['stage'] for i in (0, 1, 2)]))
                rec('lifetime ladder: at TEXT_MAX_TOTAL the person is RETIRED',
                    lad['rows'][str(M)]['stage'] == 'retired', '%d sends -> %s' % (M, lad['rows'][str(M)]['stage']))
                rec('lifetime ladder: a retired person can no longer be texted',
                    not lad['rows'][str(M)]['live'] and not lad['rows'][str(M + 1)]['live'],
                    'live at %d/%d sends: %s/%s' % (M, M + 1, lad['rows'][str(M)]['live'], lad['rows'][str(M + 1)]['live']))
                rec('lifetime ladder: an inbound reply ends it and is not counted as a send',
                    lad['rows']['inbound']['stage'] == 'replied' and lad['rows']['inbound']['sends'] == 1,
                    str(lad['rows']['inbound']))
            b.close()
        srv.shutdown()

ok = sum(R)
print('\n==== %d/%d suppression checks passed ====' % (ok, len(R)))
raise SystemExit(0 if ok == len(R) else 1)
