"""The RealForeclose pager hook against overlapping responses (PR #270 follow-up).

Codex on #270: a `tickII` Area_W refresh already in flight when the pager is armed
cannot be cancelled by clearing its timer. When it lands after we ask for page N,
the old LoadNewArea hook stamped it as page N's paint. It repaints the rows already
on screen, so the walk saw "confirmed paint, same rows" and called the date
finished on page 1. The same thing happens with the late answer to one of our own
retried page requests.

This suite does not fake the hook. It runs the real ARM_PAGER_JS,
REQUEST_WAITING_PAGE_JS, EXTRACT_JS and CLICK_NEXT_JS strings from
foreclosure_leads.py inside Node, on a stand-in for auction.js: loadArea sends an
XMLHttpRequest, its onreadystatechange calls the global LoadNewArea, and a fake
clock decides when each response lands. scrape_date and walk_waiting_pages are
the real Python. Only the server, the DOM and the clock are fake.

Case numbers only. No browser, no network. Needs `node` (CI installs it).
Run: python _auctionracetest.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import foreclosure_leads as F

NODE = shutil.which('node')

# A fake auction.js page in a Node vm context. One JSON command per stdin line.
HARNESS = r"""
const vm = require('vm');
const readline = require('readline');

let ctx = null, S = null;

function el(attrs) {
  const e = Object.assign({attrs: {}, children: [], textContent: '', value: ''}, attrs || {});
  e.getAttribute = (k) => (k in e.attrs ? e.attrs[k] : null);
  e.setAttribute = (k, v) => { e.attrs[k] = String(v); };
  e.querySelectorAll = (sel) => {
    if (sel === '.AUCTION_DETAILS tr') return e.rows || [];
    if (sel === 'td,th') return e.cells || [];
    return [];
  };
  e.querySelector = () => null;
  return e;
}

function setup(cfg) {
  S = {
    pages: cfg.pages, indicator: cfg.indicator || 'lag', transport: cfg.transport || 'xhr',
    now: 0, timers: [], seq: 0, cursor: 0, shown: 0,
    latencies: (cfg.latencies || []).slice(), defaultLatency: cfg.default_latency || 300,
    log: [],
  };
  const area = el({items: []});
  const cur = el(); const max = el();
  const next = el(); next.click = () => { ctx.loadArea('W', 1, 0, 0); };
  const document = {
    getElementById: (id) => ({curPWA: cur, maxWA: max})[id] || null,
    querySelectorAll: (sel) => (sel === '#Area_W .AUCTION_ITEM' ? area.items : []),
    querySelector: (sel) => (sel === '.Head_W .PageRight' ? next : null),
  };
  function schedule(at, fn) { const id = ++S.seq; S.timers.push({id, at, fn}); return id; }

  function FakeXHR() { this.readyState = 0; this.responseText = ''; this._ls = []; }
  FakeXHR.prototype.open = function (m, url) { this._url = url; this.readyState = 1; };
  FakeXHR.prototype.addEventListener = function (t, fn) { if (t === 'readystatechange') this._ls.push(fn); };
  FakeXHR.prototype.send = function () {
    const q = /bypassPage=(\d+)/.exec(this._url); const n = q ? parseInt(q[1], 10) : 0;
    // The server answers from its session cursor at the moment the request reaches it.
    if (n > 0) S.cursor = Math.min(n, S.pages.length) - 1;
    const body = JSON.stringify({page: S.cursor});
    const lat = S.latencies.length && n > 0 ? S.latencies.shift() : (this._lat || S.defaultLatency);
    S.log.push({at: S.now, bypassPage: n, answers: S.cursor + 1, lands: S.now + lat});
    const x = this;
    schedule(S.now + lat, () => {
      x.readyState = 4; x.status = 200; x.responseText = body;
      if (typeof x.onreadystatechange === 'function') x.onreadystatechange();
      x._ls.forEach((fn) => fn.call(x));
    });
  };

  ctx = {
    document, XMLHttpRequest: FakeXHR, JSON, parseInt, String, Math,
    setTimeout: (fn, ms) => schedule(S.now + (ms || 0), fn),
    clearTimeout: (id) => { S.timers = S.timers.filter((t) => t.id !== id); },
    __paint: (idx) => {
      S.shown = idx;
      area.items = S.pages[idx].map((c, i) => {
        const cell0 = el({textContent: 'Case #:'}); const cell1 = el({textContent: c});
        const tr = el({cells: [cell0, cell1]});
        const it = el({rows: [tr]}); it.attrs.aid = String(800000 + idx * 1000 + i);
        return it;
      });
      const mx = String(S.pages.length);
      if (S.indicator === 'blank') { cur.attrs = {}; cur.value = ''; max.textContent = ''; }
      else { cur.setAttribute('curPG', S.indicator === 'lag' ? '1' : String(idx + 1)); max.textContent = mx; }
    },
    __transport: S.transport,
    __clamp: (n) => (n > 0 ? Math.min(n, S.pages.length) - 1 : S.shown),
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  // The parts of auction.js the hook relies on: loadArea -> request -> LoadNewArea(area, data),
  // tickII refreshing the current waiting page on sysTimer, isLoading while a request is out.
  vm.runInContext(`
    var isLoading = 'N', isPageReset = false, sysTimer = null;
    function LoadNewArea(area, data) {
      isLoading = 'N';
      if (area === 'W') __paint(JSON.parse(data).page);
    }
    function loadArea(area, PageDir, doReset, bypassPage) {
      isLoading = 'Y';
      if (__transport === 'timer') {
        // Not an XMLHttpRequest (fetch, a script tag): the hook cannot see the request.
        var n = bypassPage;
        setTimeout(function () { LoadNewArea(area, JSON.stringify({page: __clamp(n)})); }, 300);
        return;
      }
      var x = new XMLHttpRequest();
      x.open('GET', '/index.cfm?zaction=AUCTION&Zmethod=UPDATE&FNC=LOAD&AREA=' + area +
             '&PageDir=' + PageDir + '&doR=' + doReset + '&bypassPage=' + bypassPage);
      x.onreadystatechange = function () { if (x.readyState === 4) LoadNewArea(area, x.responseText); };
      x.send();
    }
    function tickII() { loadArea('W', 0, 0, 0); sysTimer = setTimeout(tickII, 5000); }
  `, ctx);
  ctx.__paint(0);
  ctx.sysTimer = schedule(5000, () => ctx.tickII());
  if (cfg.preflight) {
    // A refresh that left before the walker armed the pager and lands `preflight` ms later.
    const x = new FakeXHR(); x._lat = cfg.preflight;
    x.open('GET', '/index.cfm?zaction=AUCTION&Zmethod=UPDATE&FNC=LOAD&AREA=W&PageDir=0&doR=0&bypassPage=0');
    x.onreadystatechange = function () { if (x.readyState === 4) ctx.LoadNewArea('W', x.responseText); };
    // Whether the real refresh raises isLoading is not verified; when it does, the walk's
    // quiet wait already sits it out. preflight_loading=false is the case that wait misses.
    if (cfg.preflight_loading !== false) ctx.isLoading = 'Y';
    x.send();
  }
}

function advance(ms) {
  const until = S.now + ms;
  for (;;) {
    S.timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = S.timers[0];
    if (!t || t.at > until) break;
    S.timers.shift();
    S.now = t.at;
    t.fn();
  }
  S.now = until;
}

const rl = readline.createInterface({input: process.stdin});
rl.on('line', (line) => {
  const cmd = JSON.parse(line); let out;
  try {
    if (cmd.op === 'setup') { setup(cmd); out = {ok: true}; }
    else if (cmd.op === 'eval') {
      const fn = vm.runInContext('(' + cmd.js + ')', ctx);
      const v = cmd.has_arg ? fn(cmd.arg) : fn();
      out = {ok: true, value: v === undefined ? null : v};
    }
    else if (cmd.op === 'advance') { advance(cmd.ms); out = {ok: true}; }
    else if (cmd.op === 'log') { out = {ok: true, value: {log: S.log, shown: S.shown + 1, now: S.now}}; }
    else out = {ok: false, error: 'bad op'};
  } catch (e) { out = {ok: false, error: String(e && e.stack || e)}; }
  process.stdout.write(JSON.stringify(out) + '\n');
});
"""


class Site:
    """A Playwright-shaped page over the Node harness."""

    def __init__(self, harness_path, **cfg):
        self.proc = subprocess.Popen([NODE, harness_path], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, encoding='utf-8')
        self._call(dict(op='setup', **cfg))

    def _call(self, cmd):
        self.proc.stdin.write(json.dumps(cmd) + '\n')
        self.proc.stdin.flush()
        out = json.loads(self.proc.stdout.readline())
        if not out.get('ok'):
            raise AssertionError(out.get('error'))
        return out.get('value')

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    # --- what scrape_date uses ---
    def goto(self, url, timeout=None):
        self.url = url

    def wait_for_selector(self, sel, timeout=None, state=None):
        return True

    def evaluate(self, js, *arg):
        cmd = {'op': 'eval', 'js': js, 'has_arg': bool(arg)}
        if arg:
            cmd['arg'] = arg[0]
        return self._call(cmd)

    def sleep(self, seconds):
        self._call({'op': 'advance', 'ms': int(round(seconds * 1000))})

    def log(self):
        return self._call({'op': 'log'})


def cases(n, start):
    return ['2099-%06d-CA-01' % i for i in range(start, start + n)]


DAY_17 = [cases(10, 1), cases(7, 11)]
DAY_22 = [cases(10, 100), cases(10, 110), cases(2, 120)]


@unittest.skipUnless(NODE, 'node is not installed; the pager hook runs in Node')
class OverlappingResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='auctionrace')
        cls.harness = os.path.join(cls.tmp, 'auction_site.js')
        with open(cls.harness, 'w', encoding='utf-8') as f:
            f.write(HARNESS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self._saved = list(F.PAGING_UNPROVEN)
        F.PAGING_UNPROVEN[:] = []
        self._sleep = F.time.sleep
        self.sites = []

    def tearDown(self):
        F.time.sleep = self._sleep
        F.PAGING_UNPROVEN[:] = self._saved
        for s in self.sites:
            s.close()

    def site(self, **cfg):
        s = Site(self.harness, **cfg)
        self.sites.append(s)
        F.time.sleep = s.sleep
        return s

    def scrape(self, site, d='12/07/2026'):
        rows = F.scrape_date(site, d, 'FC')
        return [r.get('Case #') for r in rows]

    # --- the race ---
    def test_refresh_in_flight_at_arm_does_not_end_the_day_on_page_one(self):
        # Refresh of page 1 left before arming, lands at 1.5s. Our page-2 request is slow
        # (3s). The old hook stamped the refresh as page 2's paint: same rows, "confirmed",
        # so the 22-row day ended at 10 rows with end=last page.
        s = self.site(pages=DAY_22, indicator='lag', preflight=1500, preflight_loading=False,
                      latencies=[3000])
        got = self.scrape(s)
        self.assertEqual(got, cases(10, 100) + cases(10, 110) + cases(2, 120), s.log())
        self.assertEqual(F.PAGING_UNPROVEN, [])

    def test_refresh_that_raises_isloading_is_waited_out(self):
        s = self.site(pages=DAY_22, indicator='lag', preflight=1500, latencies=[3000])
        self.assertEqual(len(self.scrape(s)), 22, s.log())
        self.assertEqual(F.PAGING_UNPROVEN, [])

    def test_refresh_in_flight_on_a_two_page_day(self):
        s = self.site(pages=DAY_17, indicator='blank', preflight=1200, preflight_loading=False,
                      latencies=[2500])
        got = self.scrape(s)
        self.assertEqual(got, cases(10, 1) + cases(7, 11), s.log())
        self.assertEqual(F.PAGING_UNPROVEN, [])

    def test_late_answer_to_our_own_retry_is_not_the_next_page(self):
        # Page-2 request #1 takes 7s, past the 6s poll window, so the walker asks again
        # (#2, fast) and advances. #1's late answer is page 2 again; it lands while page 3
        # is out and must not be stamped as page 3's paint.
        s = self.site(pages=DAY_22, indicator='blank', latencies=[7000, 200, 2500])
        got = self.scrape(s)
        self.assertEqual(got, cases(10, 100) + cases(10, 110) + cases(2, 120), s.log())
        self.assertEqual(F.PAGING_UNPROVEN, [])

    def test_stale_page_after_our_paint_is_not_accepted_as_a_new_page(self):
        # On page 2, a refresh of page 1 that left before arming lands after page 2
        # painted. Page 1's rows are not page 3.
        s = self.site(pages=DAY_22, indicator='blank', preflight=1300, preflight_loading=False,
                      latencies=[200, 3000])
        got = self.scrape(s)
        self.assertEqual(got, cases(10, 100) + cases(10, 110) + cases(2, 120), s.log())
        self.assertEqual(F.PAGING_UNPROVEN, [])

    # --- no regression on clean days ---
    def test_clean_days_still_prove_the_end(self):
        for pages, n in ((DAY_17, 17), (DAY_22, 22)):
            for ind in ('lag', 'accurate', 'blank'):
                F.PAGING_UNPROVEN[:] = []
                s = self.site(pages=pages, indicator=ind)
                got = self.scrape(s)
                self.assertEqual(len(got), n, (ind, s.log()))
                self.assertEqual(len(set(got)), n)
                self.assertEqual(F.PAGING_UNPROVEN, [], ind)

    def test_single_page_day_is_proven(self):
        s = self.site(pages=[cases(4, 1)], indicator='blank')
        self.assertEqual(self.scrape(s), cases(4, 1))
        self.assertEqual(F.PAGING_UNPROVEN, [])

    def test_request_the_hook_cannot_see_is_unproven_not_last(self):
        # If loadArea stops using XMLHttpRequest, a repeat of the same rows can no longer
        # be tied to our request. That has to be logged as unproven, never as a finished day.
        s = self.site(pages=[cases(4, 1)], indicator='blank', transport='timer')
        old = F.PAGE_POLLS
        F.PAGE_POLLS = 8
        try:
            got = self.scrape(s)
        finally:
            F.PAGE_POLLS = old
        self.assertEqual(got, cases(4, 1))
        self.assertEqual(len(F.PAGING_UNPROVEN), 1, F.PAGING_UNPROVEN)
        self.assertIn('unproven:', F.PAGING_UNPROVEN[0])


if __name__ == '__main__':
    unittest.main()
