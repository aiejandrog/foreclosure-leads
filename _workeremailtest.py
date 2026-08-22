"""Morning Worker EMAIL — intent must never be recorded as delivery.

REWRITTEN 2026-08-02. The previous version asserted one thing: that tapping Email opened a real
Gmail tab and that the log never claimed success over a blocked popup. That was the right fight
for its day (the operator caught the worker logging sends that never happened). But it was written
before the SMTP bridge, and it passed only because the bridge is offline in a test runner and the
fallback happened to emit a matching log string. It never once exercised a successful send.

What actually broke the business was subtler, and this file now guards it:

    Opening a composer is NOT contacting someone.

The old code called autoLog() the moment a composer opened. _workerEligible drops any lead touched
inside its cooldown -- so fifty opened-but-unsent composers silently drained fifty leads out of the
queue for three days, while the daily cap (which counted the same 'email' log lines, twice per
bridge attempt) announced "you have sent 50 today". Nine commits of worker work, zero real emails,
and every log line said it was fine.

The invariant under test: ONLY confirmed delivery may charge the cap, archive to the Proof Sheet,
or retire a lead. Three paths reach the send:
    bridge 200      -> 'sent'  (counts, archives, retires)
    bridge offline  -> 'email' (attempt) + an explicit "I sent it" confirm
    no payload      -> 'email' (attempt) + the same confirm
"""
import asyncio, json, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
import paths as P

SRC = pathlib.Path(P.TWIN)
BRIDGE = 'http://127.0.0.1:8823/**'
ok, bad = [], []
def rec(n, cond, d=''):
    (ok if cond else bad).append(n)
    print(('  PASS ' if cond else '  FAIL ') + n + ((' | ' + str(d)) if d else ''))


async def open_worker(ctx, pg):
    async with ctx.expect_page() as np:
        await pg.evaluate("openMorningWorker('active')")
    w = await np.value
    await w.wait_for_timeout(1900)
    return w


async def to_emailable(w, tries=60):
    for _ in range(tries):
        live = await w.evaluate(
            "() => { const b=document.getElementById('mw-email');"
            "        return !!(b && !b.classList.contains('disabled')); }")
        if live:
            return True
        if not await w.evaluate("() => !!document.getElementById('mw-skip')"):
            return False
        await w.click('#mw-skip')
        await w.wait_for_timeout(300)
    return False


async def counters(pg):
    return await pg.evaluate("""() => {
      const s = _wlogStats();
      return {cap:_sentTodayCount(), sent:s.sent||0, email:s.email||0,
              archive:(typeof _sentAll==='function' ? _sentAll().length : -1)};
    }""")


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()

        # ============================ PATH 1 — bridge 200 =================================
        ctx = await b.new_context()
        pg = await ctx.new_page()
        berr, werr = [], []
        pg.on('pageerror', lambda e: berr.append(str(e)))
        # Route the bridge to a canned success. The real send_server may or may not be running on
        # this machine; routing makes the result deterministic either way.
        async def _ok(route):
            await route.fulfill(status=200, content_type='application/json',
                                body=json.dumps({'ok': True, 'message_id': '<test-abc123@gmail.com>',
                                                 'sent_today': 1, 'cap': 50, 'ready': True}))
        await ctx.route(BRIDGE, _ok)
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(2600)
        before = await counters(pg)
        w = await open_worker(ctx, pg)
        w.on('pageerror', lambda e: werr.append(str(e)))

        badge = await w.evaluate("() => (document.getElementById('mwbridge')||{}).className||''")
        rec('bridge health badge reports UP when /health answers', 'up' in badge, badge)

        rec('found an emailable lead', await to_emailable(w))
        await w.click('#mw-email')
        await w.wait_for_timeout(1600)
        after = await counters(pg)
        log1 = await w.evaluate("() => (document.getElementById('mwlog')||{}).textContent||''")

        rec('bridge 200 logs DELIVERED', 'DELIVERED' in log1, log1[:80])
        rec('bridge 200 charges the cap exactly ONCE',
            after['cap'] - before['cap'] == 1, {'before': before['cap'], 'after': after['cap']})
        rec("bridge 200 writes a 'sent' entry",
            after['sent'] - before['sent'] == 1, {'before': before['sent'], 'after': after['sent']})
        rec('bridge 200 archives to the Proof Sheet',
            after['archive'] > before['archive'],
            {'before': before['archive'], 'after': after['archive']})
        arch = await pg.evaluate(
            "() => { const a=_sentAll(); const e=a[a.length-1]||{};"
            "        return {via:e.via||'', mid:e.mid||'', ch:e.ch||''}; }")
        rec('archive records HOW it was sent + the Message-ID',
            arch['via'] == 'bridge' and 'test-abc123' in arch['mid'], arch)
        retired = await pg.evaluate("""() => {
          const s = _wlogToday().filter(e=>e.a==='sent'); const last=s[s.length-1]||{};
          const n = notes[last.c]||{}; const t=n.touches||[];
          return {ch:(t[t.length-1]||{}).ch||'', c:last.c||''};
        }""")
        rec('a DELIVERED email retires the lead (email touch written)',
            retired['ch'] == 'email', retired)
        await ctx.close()

        # ============================ PATH 2 — bridge offline =============================
        ctx2 = await b.new_context()
        pg2 = await ctx2.new_page()
        berr2, werr2 = [], []
        pg2.on('pageerror', lambda e: berr2.append(str(e)))
        # Bridge refuses -> the worker must degrade to Gmail compose, and must NOT count it.
        async def _dead(route):
            await route.abort()
        async def _gmail(route):
            await route.fulfill(status=200, content_type='text/html', body='<html>gmail stub</html>')
        await ctx2.route(BRIDGE, _dead)
        await ctx2.route('**://mail.google.com/**', _gmail)
        await pg2.goto(SRC.as_uri()); await pg2.wait_for_timeout(2600)
        b2 = await counters(pg2)
        w2 = await open_worker(ctx2, pg2)
        w2.on('pageerror', lambda e: werr2.append(str(e)))

        badge2 = await w2.evaluate("() => (document.getElementById('mwbridge')||{}).className||''")
        rec('badge reports DOWN when the bridge refuses', 'down' in badge2, badge2)

        await to_emailable(w2)
        gmail_opened = False
        try:
            async with ctx2.expect_page(timeout=6000) as gp:
                await w2.click('#mw-email')
            g = await gp.value
            gmail_opened = 'mail.google.com' in (g.url or '')
        except Exception:
            await w2.wait_for_timeout(1200)
        await w2.wait_for_timeout(900)
        a2 = await counters(pg2)

        rec('bridge offline still opens Gmail compose', gmail_opened, {'opened': gmail_opened})
        rec('offline fallback does NOT charge the cap',
            a2['cap'] == b2['cap'], {'before': b2['cap'], 'after': a2['cap']})
        rec('offline fallback does NOT archive a phantom send',
            a2['archive'] == b2['archive'],
            {'before': b2['archive'], 'after': a2['archive']})
        rec('offline fallback logs an ATTEMPT, not a send',
            a2['email'] > b2['email'] and a2['sent'] == b2['sent'],
            {'email_delta': a2['email'] - b2['email'], 'sent_delta': a2['sent'] - b2['sent']})
        # THE bug this whole rewrite exists for.
        not_retired = await pg2.evaluate("""() => {
          const at = _wlogToday().filter(e=>e.a==='email'); const last=at[at.length-1]||{};
          const row = DATA.find(x=>x.case===last.c);
          const n = notes[last.c]||{}; const t = n.touches||[];
          return {c:last.c||'', touches:t.length, eligible: row ? _workerEligible(row) : null};
        }""")
        rec('an UNSENT lead is NOT retired — it stays in the queue',
            not_retired['eligible'] is not False, not_retired)

        # ---- the confirm card promotes the attempt to a real send -------------------------
        has_confirm = await w2.evaluate("() => !!document.getElementById('mwsentyes')")
        rec('offline fallback shows the "I sent it" confirm', has_confirm)
        if has_confirm:
            await w2.click('#mwsentyes')
            await w2.wait_for_timeout(1100)
            a3 = await counters(pg2)
            rec('confirming promotes the attempt to a counted send',
                a3['cap'] == b2['cap'] + 1 and a3['sent'] == b2['sent'] + 1,
                {'cap': a3['cap'], 'sent': a3['sent']})
            arch2 = await pg2.evaluate(
                "() => { const a=_sentAll(); return (a[a.length-1]||{}).via||''; }")
            rec('a hand-sent email is archived as manual-confirmed, not as a bridge send',
                arch2 == 'manual-confirmed', arch2)

        rec('no JS errors (bridge-up run)', not werr and not berr, (werr + berr)[:2])
        rec('no JS errors (bridge-down run)', not werr2 and not berr2, (werr2 + berr2)[:2])
        await ctx2.close()
        await b.close()

        total = len(ok) + len(bad)
        print(f'\n==== {len(ok)}/{total} worker-email checks passed ====')
        return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
