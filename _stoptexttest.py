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

print('== SMS keywords ==')
for t, want in [('STOP', True), ('Stop.', True), ('stopall', True), ('CANCEL', True), ('End', True),
                ('quit', True), ('stop the sale', False), ('ok stop', True), ('PARE', True),
                ('can you stop the auction', False)]:
    T('sms %-25r -> %s' % (t, want), bool(R.is_sms_stop(t)) == want, R.is_sms_stop(t))

print('\n%d passed, %d failed' % (pass_n, fail_n))
sys.exit(1 if fail_n else 0)
