"""What Alejandro removed on 2026-11-13 stays removed, and what must still stop a send still does.

Three removals, one file, so none of them can come back by accident in a later merge:
  1. the paid Whitepages API (key, /wp/lookup, the paid button, the nightly line);
  2. the Whitepages text gate in Call Mode (likely owner / pasted number were call-only);
  3. the first-email gates on the send bridge (bounce pause, slow-restart cap, verified-only,
     the 10-a-day unverified cap), with their copies in Call Mode, cadence and outreach_email.

And the other half, which matters more: the opt-out ledger, the bankruptcy stay, mail_guard, an
unreadable bounce list, a known-dead address and the daily caps all still refuse.

Source checks plus a few direct calls. No network, no SMTP, no real data: example.com only.
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ok, bad = [], []


def rec(name, cond, detail=''):
    (ok if cond else bad).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | ' + str(detail)[:300]) if detail and not cond else ''))


def src(name):
    return io.open(os.path.join(HERE, name), encoding='utf-8', errors='replace').read()


import send_server as S  # noqa: E402
import call_mode  # noqa: E402

ss, cm, wl = src('send_server.py'), src('call_mode.py'), src('whitepages_lookup.py')
bat = src('refresh-dealflow.bat')

# ---- 1. Whitepages paid API ----------------------------------------------------------------------
rec('no whitepages.key on disk', not os.path.exists(os.path.join(HERE, 'whitepages.key')))
try:
    import whitepages_lookup as WL
    try:
        WL._load_key()
        rec('whitepages_lookup._load_key() refuses', False, 'returned a key')
    except SystemExit as e:
        rec('whitepages_lookup._load_key() refuses', 'retired' in str(e).lower(), e)
    rec('the module still serves the cache helpers the board imports', callable(getattr(WL, 'normalize_prop_id', None)))
except Exception as e:  # pragma: no cover
    rec('whitepages_lookup imports', False, e)
rec('the bridge has no /wp/lookup route or handler', '/wp/lookup' not in ss and '_wp_lookup_case' not in ss)
rec('Call Mode has no paid lookup button', 'wpLookup(' not in cm and 'about $0.22)' not in cm
    and "bridgeFetch('/wp/lookup'" not in cm)
rec('the nightly batch has no paid Whitepages line, held or live',
    'whitepages_lookup.py --pick-callable' not in bat and 'whitepages_lookup.py --gap' not in bat)
rec('the free path is still there: Whitepages links and the paste box',
    "'Whitepages addr'" in cm and 'lknum' in cm and 'function lkSave' in cm)

# ---- 2. Whitepages text gate ---------------------------------------------------------------------
rec('Call Mode no longer holds a text on a Whitepages likely owner or a pasted number',
    'Likely owner per Whitepages, not confirmed yet' not in cm
    and 'Pasted from Whitepages, not confirmed yet' not in cm
    and "charAt(phIdx)==='l' && !sitKeys.length" not in cm)
rec('the holds that stay are still in the after-call panel',
    all(x in cm for x in ('do-not-text list', 'FTSA cap', 'Household member, not the owner',
                          'Name-only match', 'Someone other than the owner asked for this callback')))

# ---- 3. first-email gates ------------------------------------------------------------------------
rec('the pause, restart-cap and probe helpers are gone from the bridge',
    not any(hasattr(S, n) for n in ('first_touch_paused', '_first_touch_bounce_paused', '_ft_restart_ceiling',
                                    '_restart_ceiling_on', '_probe_quota_left', 'PROBE_DAILY_CAP')))
rec('/send no longer contains the pause, verified-only or the unverified cap',
    not any(x in ss for x in ("'first_touch_paused': True", 'Verified-only mode is ON',
                              "'blocked': 'probe_quota_exhausted'", '_ft_exempt',
                              'no warm-up sender configured during slow restart')))
rec("every verdict but 'dead' is mailable", 'dead' not in S.GATE_PASS
    and {'unverified', 'catch_all', 'risky', 'awaiting_delivery', 'zerobounce_valid', 'delivered', 'replied'} == set(S.GATE_PASS),
    S.GATE_PASS)
ev = {'bounced': {'dead@example.com'}, 'ver': {}, 'proven': set(), 'replied': set(), 'last_mailed': {}}
rec('an address with no evidence is picked', S._pick_recipient(['new@example.com'], ev)[0] == 'new@example.com')
rec('a bounced address is never picked', S._pick_recipient(['dead@example.com'], ev)[0] == '')
rec('a live address beats a dead one whatever the order',
    S._pick_recipient(['dead@example.com', 'new@example.com'], ev)[0] == 'new@example.com')

tmp = tempfile.mkdtemp()
_here, _led = S.HERE, S._load_ledger
try:
    S.HERE = tmp
    today = S.dt.date.today().isoformat()
    rows = [{'ch': 'email', 'message_id': '<%d>' % i, 'd': today, 'to': 'a%d@example.com' % i, 'touch': 'first'}
            for i in range(20)]
    json.dump(['a0@example.com', 'a1@example.com'], open(os.path.join(tmp, 'bounced_emails.json'), 'w'))
    S._load_ledger = lambda: rows
    m, h = S._bounce_measure(), S._bounce_health()
    rec('2 of 20 bounced: still MEASURED as a pause-worthy day', m.get('ft_rule') == 'paused' and m.get('day_blocked') is True, m.get('ft_rule'))
    rec('...and enforced as nothing: clear, not blocked, with the measurement carried alongside',
        h.get('ft_rule') == 'clear' and h.get('blocked') is False and h.get('day_blocked') is False
        and h.get('measured_rule') == 'paused' and h.get('enforced') is False, h)
    io.open(os.path.join(tmp, 'bounced_emails.json'), 'w', encoding='utf-8').write('{ not json')
    u = S._bounce_health()
    rec('an unreadable bounce list is the one reading that still blocks',
        u.get('list_unreadable') is True and u.get('blocked') is True, u)
finally:
    S.HERE, S._load_ledger = _here, _led

rec('Call Mode has no first-email pause on the card',
    'MAIL_PAUSE' not in cm and '__MAILPAUSE__' not in cm and not hasattr(call_mode, '_mail_pause_json'))
rec('cadence and outreach_email do not ask for a pause',
    'first_touch_paused' not in src('cadence.py') and 'first_touch_paused' not in src('outreach_email.py'))

# ---- what must still refuse ----------------------------------------------------------------------
rec('/send still refuses on the opt-out ledger, the stay, a broken merge, an unreadable bounce list, the caps',
    all(x in ss for x in ("'blocked': 'optout'", '_stay_gate', "'blocked': 'merge_fields'",
                          'bounce list is UNREADABLE', 'daily RECIPIENT cap reached', 'daily cap reached',
                          'first-touch capacity used up today')))
rec('/send still holds a send whose every address is known dead',
    "'hold': 'deliverability'" in ss and 'known dead' in ss)

print('\n%d passed, %d failed' % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
