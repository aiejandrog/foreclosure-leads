"""_stoptexttest -- the stop detector's contract, both languages, plus the quote guard.

Run: python _stoptexttest.py        (no data, no network, no browser)

WHY (2026-09-25 audit): the Spanish verbs quitar/detener/parar were bare in OPTOUT_PHRASES, so
"¿Pueden detener la venta?" -- the most motivated Spanish reply an owner can send -- was a
permanent DO NOT CONTACT. And cadence scanned the raw message with the quoted original inside,
where our own "reply 'stop'" sentence lives. Both verdicts are add-only ledger writes.
"""
import sys
import replies as R

CASES = [
    # English explicit
    ('STOP', True), ('stop', True), ('Please stop.', True), ('please stop emailing me', True),
    ('unsubscribe', True), ('remove me', True), ('take me off your list', True),
    ('do not contact me again', True), ('leave me alone', True),
    # English aimed at the CASE, not at us
    ('Can you stop the foreclosure?', False), ('Is there any way to stop the sale?', False),
    ('I need to stop the auction', False), ('what can i do to stop this', False),
    # Spanish aimed at the case
    ('¿Pueden detener la venta?', False), ('¿Se puede parar la subasta?', False),
    ('Me van a quitar la casa, ayuda', False), ('¿Cómo detengo el proceso?', False),
    ('quiero parar el remate', False), ('necesito detener la ejecución', False),
    # Spanish opt-outs
    ('PARE', True), ('Pare de llamarme', True), ('no me llames', True), ('no me llame más', True),
    ('No me escriban más', True), ('no me contacten', True), ('Quíteme de su lista', True),
    ('Basta ya', True), ('deje de mandar mensajes', True), ('no quiero más correos', True),
    ('darme de baja', True),
    # neither
    ('no gracias', False), ('who is this?', False), ('Yes call me tomorrow', False),
    ('Yes my number is 3059093711 u can call me after 5:30', False),
    ('gracias, ya vendí la casa', False),
    # 2026-09-26: the approved email line is "If now's not a good time, just tell me and I won't
    # reach out again." Whatever it invites is an opt-out, both languages.
    ("Now's not a good time", True), ('not a good time, maybe next month', True),
    ('not a good time, try next month', True),
    ('try next month', True),
    ('the sale is next month', False),
    ("please don't reach out again", True), ('stop reaching out', True),
    ('No es buen momento', True), ('ahora no es un buen momento', True),
    # ...and our own sentence, quoted back with no quote marker, is not
    ("Yes! You wrote: If now's not a good time, just tell me and I won't reach out again. It is.", False),
    ("Si ahora no es buen momento, solo digamelo y no lo vuelvo a contactar. Llámeme mañana", False),
    ('Is now a good time? call me', False),
    # 2026-09-26: the text line replaced "Reply STOP". Quoting it back is not a stop.
    ("If now's not a good time, just let me know and I won't text you again.", False),
    ("If now’s not a good time, just let me know and I won’t text you again", False),
    ('Si ahora no es buen momento, solo dígamelo y no le vuelvo a escribir.', False),
    ('Si ahora no es buen momento, solo digamelo y no le vuelvo a escribir. Llame mañana', False),
    ("Not a good time. If now's not a good time, just let me know and I won't text you again.", True),
    ("don't text", True), ("don't text me", True), ('remove me', True),
]

pass_n = fail_n = 0
def T(name, ok, got=None):
    global pass_n, fail_n
    if ok:
        pass_n += 1; print('  PASS', name)
    else:
        fail_n += 1; print('  FAIL', name, '' if got is None else '[got %r]' % (got,))

print('== is_stop_text ==')
for text, want in CASES:
    got = bool(R.is_stop_text(text))
    T('%-45r -> %s' % (text, want), got == want, got)

print('== quote guard ==')
quoted = ('Yes, call me tomorrow after 5.\n\nOn Mon, Sep 22, 2026 at 2:48 PM Alejandro Gonzalez '
          '<a@example.com> wrote:\n> If you would rather not hear from me, reply stop and you '
          "won't hear from me again.")
T('raw quoted reply reads as stop (the trap)', R.is_stop_text(quoted) is True)
T('strip_quotes removes the quoted block', R.is_stop_text(R.strip_quotes(quoted)) is False)
T('strip_quotes keeps the fresh text', R.strip_quotes(quoted).strip().startswith('Yes, call me'))
raw = (b'From: Owner <owner@example.com>\r\nSubject: Re: Regarding your property at 1 MAIN ST\r\n'
       b'Content-Type: text/plain; charset=utf-8\r\n\r\n' + quoted.encode('utf-8'))
subj, fresh = R.reply_text(raw)
T('reply_text parses RFC822 and strips the quote', 'stop' not in fresh.lower() and 'MAIN ST' in subj)
outlook = 'STOP\r\n\r\n-----Original Message-----\r\nFrom: x@y.com\r\nSubject: hi'
T('a real STOP above an Outlook quote still counts', R.is_stop_text(R.strip_quotes(outlook)) is True)

print('== the approved lines are what the detector honours ==')
import outreach_copy as OC
import mail_guard as MG
T('outreach_copy EN line is the approved wording',
  OC.OPTOUT_LINE_EN == "If now's not a good time, just tell me and I won't reach out again.")
T('email _unsub() carries it (no URL set)', OC._unsub(lang='en') == OC.OPTOUT_LINE_EN)
T('Spanish _unsub() carries the ES line', OC._unsub(lang='es') == OC.OPTOUT_LINE_ES)
T('the ES line is the approved accented wording',
  OC.OPTOUT_LINE_ES == 'Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.')
T('the line alone does not read as a stop (our words)', R.is_stop_text(OC.OPTOUT_LINE_EN) is False)
T('the ES line alone does not read as a stop', R.is_stop_text(OC.OPTOUT_LINE_ES) is False)
T('mail_guard sees the EN line as the body opt-out',
  not MG.check('Subject here', 'Ordinary letter.\n\n' + OC.OPTOUT_LINE_EN, 'a@b.com', unsub=''))
T('mail_guard sees the ES line as the body opt-out',
  not MG.check('Asunto', 'Carta.\n\n' + OC.OPTOUT_LINE_ES, 'a@b.com', unsub=''))
_sms = OC.sms('Maria')
_EN = "If now's not a good time, just let me know and I won't text you again."
_ES = 'Si ahora no es buen momento, solo dígamelo y no le vuelvo a escribir.'
T('outreach_copy.sms carries the new EN and ES lines',
  _sms.endswith(_EN + ' ' + _ES) and 'Reply STOP' not in _sms, _sms[-160:])
T('the text line alone is not a stop', R.is_stop_text(_EN) is False and R.is_stop_text(_ES) is False)
T('a real not-a-good-time still is', R.is_stop_text('not a good time') is True)
T('STOP replies still opt out', R.is_sms_stop('STOP') is True and R.is_sms_stop('PARE') is True)
src_cad = open('cadence.py', encoding='utf-8').read()
T('cadence touches use the shared line, not the old keyword sentence',
  "OPTOUT_LINE_EN as _OPTOUT_LINE" in src_cad and "reply 'stop' and you won't" not in src_cad)
T('cadence legacy fallback sets List-Unsubscribe and runs the guard',
  "msg['List-Unsubscribe'] = _unsub_hdr" in src_cad and '_MG.check(subj, body, s[\'email\'], unsub=_unsub_hdr)' in src_cad)
tpl = open('tracker_template.html', encoding='utf-8').read()
T('board batch texts carry the new EN line',
  'const stopEN = " ' + _EN + '";' in tpl and 'Reply STOP' not in tpl)
T('board batch texts carry the new ES line',
  "const stopES = ' " + _ES + "';" in tpl and 'Responda STOP' not in tpl)
cm = open('call_mode.py', encoding='utf-8').read()
T('Call Mode texts carry the new lines and not Reply STOP',
  _EN in cm and _ES in cm and 'Reply STOP to opt out.' not in cm)
T('investor-lane email uses the approved line', "won't write again" not in tpl
  and "If now's not a good time, just tell me and I won't reach out again." in tpl)

print('== SMS keywords ==')
for t, want in [('STOP', True), ('Stop.', True), ('stopall', True), ('CANCEL', True), ('End', True),
                ('quit', True), ('stop the sale', False), ('ok stop', True), ('PARE', True),
                ('BASTA', True), ('NO MAS', True), ('NO MÁS', True), ('cancelar', True),
                ('can you stop the auction', False)]:
    T('sms %-25r -> %s' % (t, want), bool(R.is_sms_stop(t)) == want, R.is_sms_stop(t))

print('\n%d passed, %d failed' % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
