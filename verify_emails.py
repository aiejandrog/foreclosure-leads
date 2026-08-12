#!/usr/bin/env python3
"""verify_emails.py -- decide whether an address is safe to send to BEFORE it can enter a queue.

WHY THIS EXISTS (2026-08-08). An audit of mail_sent.json against bounced_emails.json found a
SUSTAINED hard-bounce rate of 28-33% every day for a week -- 478 of 1,712 recipients confirmed
dead. Mailbox providers treat that as proof of a purchased list; tolerance is ~2% and blocking
starts near 5%. The realistic failure mode is not lost mail, it is Gmail suppressing the sending
account, which takes every lead on the board dark at once including the live conversations.

WHAT WAS TESTED AND REJECTED, so nobody re-tries it:
  * MX lookup. Useless for this data. netzero.net, cs.com and juno.com ALL still publish valid MX
    records -- the domains run mail servers, the individual mailboxes were purged years ago. An MX
    gate would have caught zero of them. (Verified live via DNS-over-HTTPS 2026-08-08.)
  * SMTP RCPT TO probing. Gmail/Yahoo/AOL either accept-all or greylist connections from a
    residential IP, so the answer is meaningless, and the probing itself damages sender reputation.

WHAT ACTUALLY WORKS, cheapest first. First verdict wins:
  1. SUPPRESSED  -- already in bounced_emails.json. Authoritative: we watched it bounce.
  2. SYNTAX      -- malformed, or a role/no-reply mailbox no homeowner reads.
  3. DEAD DOMAIN -- LEARNED from our own ledger, never assumed. A domain is retired only once we
                    have mailed it enough times to be sure (>= MIN_SAMPLES) and nearly all of it
                    bounced (>= DEAD_RATE).
  4. PROVIDER    -- optional address-level API, active only when a key file is present. This is the
                    only thing that can settle yahoo.com (47% dead) and aol.com (49% dead), which
                    are far too big to blanket-block and far too dirty to trust.
  5. UNKNOWN     -- allowed through, but counted, so the gap stays visible.

Run:
    python verify_emails.py                 # classify every address on the board
    python verify_emails.py --stats         # just print the current picture
    python verify_emails.py --addr a@b.com  # check one address

Writes verified_emails.json: addr -> {"v": ok|dead|risky|unknown, "why": str, "d": YYYY-MM-DD}
make_tracker reads it and strips anything marked dead, exactly like the bounce guard.
"""
import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'verified_emails.json')
LEDGER = os.path.join(HERE, 'mail_sent.json')
BOUNCED = os.path.join(HERE, 'bounced_emails.json')
LEADS = os.path.join(HERE, 'leads_final.json')
# Contact data lives in the skiptrace + per-county merges, never in leads_final.json.
SKIPTRACE = os.path.join(HERE, 'skiptrace_results.json')
BROWARD = os.path.join(HERE, 'broward_leads.json')
PALMBEACH = os.path.join(HERE, 'palmbeach_leads.json')
LP = os.path.join(HERE, 'lp_leads.json')

# A domain is only retired on evidence. 3 mailed is thin, but at a 90% death rate the expected
# number of live mailboxes we give up is well under one, and every one of these is a legacy ISP
# whose consumer mail was mass-purged. The bar rises automatically as volume grows.
MIN_SAMPLES = 3
DEAD_RATE = 0.90
# Between RISKY_RATE and DEAD_RATE the domain is real but filthy (yahoo, aol, comcast, bellsouth).
# Those are marked 'risky', not dead: blanket-blocking them would throw away ~300 working mailboxes.
RISKY_RATE = 0.35

SYNTAX = re.compile(r'^[^@\s,;<>()\[\]\\"]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
# Mailboxes a homeowner does not read, or that reply-bomb. Sending here is pure reputation cost.
ROLE = re.compile(r'^(no-?reply|do-?not-?reply|postmaster|abuse|mailer-daemon|bounce|notification'
                  r'|admin|webmaster|support|info|sales|billing|help|contact|donotreply)([+.-]|$)',
                  re.I)


def _load(p, default):
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def _addrs_from_ledger():
    """Every address we have actually sent to, TO and BCC alike."""
    out = set()
    for e in _load(LEDGER, []):
        if e.get('ch') != 'email' or not e.get('message_id'):
            continue
        for a in [e.get('to') or ''] + str(e.get('bcc') or '').split(','):
            a = a.strip().lower()
            if a:
                out.add(a)
    return out


def domain_rates():
    """domain -> (mailed, dead, rate) measured on our OWN send history. This is the only honest
    source: it reflects the exact list we bought, not the internet at large."""
    bounced = {str(k).lower() for k in _load(BOUNCED, {})}
    rates = {}
    for a in _addrs_from_ledger():
        d = a.rsplit('@', 1)[-1]
        m, b = rates.get(d, (0, 0))
        rates[d] = (m + 1, b + (1 if a in bounced else 0))
    return {d: (m, b, b / m) for d, (m, b) in rates.items() if m}


def dead_domains(rates=None):
    rates = rates if rates is not None else domain_rates()
    return {d for d, (m, b, r) in rates.items() if m >= MIN_SAMPLES and r >= DEAD_RATE}


def risky_domains(rates=None):
    rates = rates if rates is not None else domain_rates()
    return {d for d, (m, b, r) in rates.items()
            if m >= MIN_SAMPLES and RISKY_RATE <= r < DEAD_RATE}


# ---------------------------------------------------------------- optional provider
def _provider():
    """(name, key) for an address-level verification API, or (None, None).

    Provider-agnostic on purpose and INERT until a key file exists -- same posture as lob.key and
    batchdata.key. Nothing here signs anyone up or spends money on its own.
        zerobounce.key   -> https://api.zerobounce.net/v2/validate
        neverbounce.key  -> https://api.neverbounce.com/v4/single/check
    """
    for name in ('zerobounce', 'neverbounce'):
        p = os.path.join(HERE, f'{name}.key')
        if os.path.exists(p):
            k = open(p, encoding='utf-8').read().strip()
            if k:
                return name, k
    return None, None


def provider_check(addr, name, key, timeout=20):
    """-> (verdict, why). Any failure returns ('unknown', ...) so a flaky API never silently
    blocks real leads -- a false 'dead' costs a deal, and that is the more expensive mistake."""
    import urllib.parse
    import urllib.request
    try:
        if name == 'zerobounce':
            q = urllib.parse.urlencode({'api_key': key, 'email': addr, 'ip_address': ''})
            url = f'https://api.zerobounce.net/v2/validate?{q}'
            with urllib.request.urlopen(url, timeout=timeout) as r:
                j = json.loads(r.read().decode())
            s = str(j.get('status') or '').lower()
            sub = str(j.get('sub_status') or '')
            if s == 'valid':
                return 'ok', f'zerobounce:{s}'
            if s in ('invalid', 'spamtrap', 'abuse', 'do_not_mail'):
                return 'dead', f'zerobounce:{s}/{sub}'
            return 'risky', f'zerobounce:{s}/{sub}'      # catch-all, unknown
        if name == 'neverbounce':
            q = urllib.parse.urlencode({'key': key, 'email': addr})
            url = f'https://api.neverbounce.com/v4/single/check?{q}'
            with urllib.request.urlopen(url, timeout=timeout) as r:
                j = json.loads(r.read().decode())
            s = str(j.get('result') or '').lower()
            if s == 'valid':
                return 'ok', 'neverbounce:valid'
            if s in ('invalid', 'disposable'):
                return 'dead', f'neverbounce:{s}'
            return 'risky', f'neverbounce:{s}'
    except Exception as e:
        return 'unknown', f'{name} error: {str(e)[:60]}'
    return 'unknown', f'{name}: no verdict'


# ---------------------------------------------------------------- the gate
def classify(addr, bounced, dead_doms, risky_doms, prov=(None, None), use_api=False):
    a = (addr or '').strip().lower()
    if not a:
        return 'dead', 'empty'
    if a in bounced:
        return 'dead', 'hard-bounced (observed)'
    if not SYNTAX.match(a):
        return 'dead', 'malformed'
    local, _, dom = a.partition('@')
    if ROLE.match(local):
        return 'dead', 'role/no-reply mailbox'
    if dom in dead_doms:
        return 'dead', f'domain retired ({dom})'
    if use_api and prov[0]:
        return provider_check(a, prov[0], prov[1])
    if dom in risky_doms:
        return 'risky', f'domain {int(100 * domain_rates().get(dom, (0, 0, 0))[2])}% dead, unverified'
    return 'unknown', 'no evidence either way'


def board_addresses():
    """Every address currently attached to a lead -- the set that actually matters, because these
    are the ones a run would send to tomorrow.

    They live in skiptrace_results.json (case -> {emails: [...]}), NOT in leads_final.json.
    make_tracker merges them at build time; leads_final.json is the county/auction layer and has no
    contact data at all. Reading the wrong file here returns 0 addresses and the gate silently
    passes everything, which is the worst possible failure for a suppression tool."""
    out = set()
    for src in (SKIPTRACE, BROWARD, PALMBEACH, LP):
        d = _load(src, None)
        if d is None:
            continue
        rows = d.values() if isinstance(d, dict) else d
        for r in rows:
            if not isinstance(r, dict):
                continue
            for a in (r.get('emails') or []):
                a = str(a).strip().lower()
                if a:
                    out.add(a)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stats', action='store_true', help='print the picture and exit')
    ap.add_argument('--addr', help='classify a single address')
    ap.add_argument('--api', action='store_true',
                    help='use the provider API for addresses no free layer settles (costs money)')
    ap.add_argument('--limit', type=int, default=0, help='cap API calls this run')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    rates = domain_rates()
    dead_doms, risky_doms = dead_domains(rates), risky_domains(rates)
    bounced = {str(k).lower() for k in _load(BOUNCED, {})}
    prov = _provider()

    if a.stats or a.addr:
        print(f'suppression list : {len(bounced)} addresses')
        print(f'provider API     : {prov[0] or "NONE (no key file) -- free layers only"}')
        print(f'\nretired domains  ({MIN_SAMPLES}+ mailed, {int(DEAD_RATE*100)}%+ dead):')
        for d in sorted(dead_doms, key=lambda x: -rates[x][0]):
            m, b, r = rates[d]
            print(f'   {d:24} {b}/{m} dead')
        print(f'\nrisky domains    ({int(RISKY_RATE*100)}-{int(DEAD_RATE*100)}% dead — need the API):')
        for d in sorted(risky_doms, key=lambda x: -rates[x][0]):
            m, b, r = rates[d]
            print(f'   {d:24} {b}/{m} = {int(100*r)}% dead')
        if a.addr:
            v, why = classify(a.addr, bounced, dead_doms, risky_doms, prov, a.api)
            print(f'\n{a.addr} -> {v.upper()}  ({why})')
        return

    todo = sorted(board_addresses())
    print(f'{len(todo)} address(es) attached to board leads')
    print(f'provider API: {prov[0] or "NONE -- free layers only, yahoo/aol stay unverified"}')
    res, counts, api_used = _load(OUT, {}), {}, 0
    today = date.today().isoformat()
    # PAID VERDICTS ARE NEVER OVERWRITTEN BY A FREE GUESS (fixed 2026-08-12).
    # The old "never re-bill" guard only skipped the API call -- it still fell through to
    # classify(use_api=False), which returns a HEURISTIC verdict and wrote it over the paid one.
    # So a plain `python verify_emails.py` (no --api) silently destroyed every settled result:
    # 1,006 'ok' rows bought with 1,394 ZeroBounce credits were downgraded to unknown/risky on
    # 2026-08-11, which halved send_server's proven_pool (1,058 -> 639) and starved the
    # verified-only send lane. A paid verdict is EVIDENCE; a heuristic is a guess. Evidence wins.
    #
    # Exception: an observed hard bounce always wins, even over a paid 'ok' -- the mailbox
    # demonstrably rejected real mail after the check, and reality outranks a stale API opinion.
    kept = 0
    for addr in todo:
        prior = res.get(addr) or {}
        paid = str(prior.get('why', '')).startswith((prov[0] or '~none~') + ':')
        if paid and addr not in bounced:
            kept += 1
            continue                      # settled by a paid check -- leave it exactly as it is
        use_api = bool(a.api and prov[0] and (not a.limit or api_used < a.limit))
        v, why = classify(addr, bounced, dead_doms, risky_doms, prov, use_api)
        if use_api and why.startswith((prov[0] or '') + ':'):
            api_used += 1
        res[addr] = {'v': v, 'why': why, 'd': today}
        counts[v] = counts.get(v, 0) + 1
    if kept:
        print(f'{kept} address(es) left untouched -- already settled by a paid check')

    tmp = OUT + '.tmp'
    json.dump(res, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    os.replace(tmp, OUT)

    n = max(1, len(todo))
    print()
    for k in ('ok', 'unknown', 'risky', 'dead'):
        if counts.get(k):
            print(f'  {k.upper():8} {counts[k]:>5}  ({100*counts[k]/n:.0f}%)')
    if api_used:
        print(f'  provider calls billed this run: {api_used}')
    print(f'\n-> {OUT}')
    sendable = counts.get('ok', 0) + counts.get('unknown', 0) + counts.get('risky', 0)
    print(f'{counts.get("dead",0)} address(es) will be stripped at build; {sendable} remain queueable.')
    if not prov[0]:
        print('\nNOTE: without a provider key, yahoo.com and aol.com stay "risky" and unverified.')
        print('      They are ~48% dead in our own data and are the bulk of what is still hurting')
        print('      the sending reputation. Drop a key in zerobounce.key or neverbounce.key and')
        print('      re-run with --api to settle them address by address.')
    print('Rebuild so the board honours this: python -c "import json,foreclosure_leads as F; '
          'F.make_tracker(json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')


if __name__ == '__main__':
    main()
