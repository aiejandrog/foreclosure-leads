"""Call Mode "I'm already selling it" branch (2026-10-22 field call). Pins: the button is reachable from
every step that already offers the realtor branch, a SIGNED seller is never pitched (no path to the ask
or to booking), and none of the new lines carry a number, a fee or a stop-the-sale promise."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call_mode as C

fails = []


def rec(name, ok):
    print(('ok   ' if ok else 'FAIL ') + name)
    if not ok:
        fails.append(name)


S = {s['id']: s for s in C.FLOW}
for sid in ('selling', 'sellsigned', 'sellopen'):
    rec('step %s exists' % sid, sid in S)
for sid in [s['id'] for s in C.FLOW if s['id'] != 'selling' and any(t == 'realtor' for _l, t in s['br'])]:
    rec('%s offers the already-selling button' % sid, any(t == 'selling' for _l, t in S[sid]['br']))
rec('selling splits signed / not signed',
    {t for _l, t in S['selling']['br']} >= {'sellsigned', 'sellopen'})
rec('a signed seller is never pitched',
    all(t.startswith('end:') for _l, t in S['sellsigned']['br']) and S['sellsigned']['ret'] == 'sellsigned')
rec('not signed goes to the ask', any(t == 'q6' for _l, t in S['sellopen']['br']))
rec('five minutes, same as every homeowner script', 'five minutes' in C.SELLOPEN_EN[-1] and 'cinco minutos' in C.SELLOPEN_ES[-1])
rec('three questions + second number, EN and ES',
    len(C.SELLOPEN_EN) == 4 and len(C.SELLOPEN_ES) == 4)
text = ' '.join([C.SELLING_EN, C.SELLING_ES, C.SELLSIGNED_EN, C.SELLSIGNED_ES] + C.SELLOPEN_EN + C.SELLOPEN_ES).lower()
for bad in ('$', 'stop the sale', 'fee', 'bankrupt', 'foreclos', 'rent', 'buy it back', 'detener la subasta'):
    rec('no %r in the lines' % bad, bad not in text)

print('\n%d failure(s)' % len(fails) if fails else '\nall ok')
raise SystemExit(1 if fails else 0)
