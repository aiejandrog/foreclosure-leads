#!/usr/bin/env node
/*
 * _watchdogtest.js — contract test for the runner-behaviour checks in
 * .github/workflows/freshness-watchdog.yml
 *
 * WHY IT EXTRACTS THE SCRIPT INSTEAD OF REIMPLEMENTING IT
 * A test that re-types the logic tests the copy, and the copy is exactly what drifts. So this
 * reads the YAML, lifts the "Check the runners are behaving" script out of it, and runs THE
 * SHIPPED BYTES against stubbed octokit fixtures. Same reason tracker_template.html's classifier
 * is extracted rather than duplicated into call_mode.py.
 *
 * WHAT IT PINS
 * The workflow was written against a real incident (2026-09-14 to 09-17). It must fire on that
 * shape and stay silent on a healthy one — a watchdog that cannot go quiet is ignored within a
 * week, and one that cannot fire is the "succeeds while doing nothing" class this repo keeps
 * paying for.
 *
 *     node _watchdogtest.js          # red/green
 *     node _watchdogtest.js --live   # also report what the repo's real log says right now
 *     node _watchdogtest.js --live --now=2026-09-25T19:07:00Z   # ...or as it stood then
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const HERE = __dirname;
const YML = path.join(HERE, '.github', 'workflows', 'freshness-watchdog.yml');
const STEP = 'Check the runners are behaving';
const MORNING_STEP = "Morning check - did last night's refresh publish";

// ---------------------------------------------------------------- extract the shipped script
function extractScript(STEP) {
  const lines = fs.readFileSync(YML, 'utf8').split('\n');
  const at = lines.findIndex(l => l.includes(`- name: ${STEP}`));
  if (at < 0) throw new Error(`step "${STEP}" not found in ${YML} — did it get renamed?`);
  const start = lines.findIndex((l, i) => i > at && /^\s*script:\s*\|\s*$/.test(l));
  if (start < 0) throw new Error(`no "script: |" under "${STEP}"`);
  const indent = lines[start + 1].match(/^\s*/)[0].length;
  const body = [];
  for (let i = start + 1; i < lines.length; i++) {
    const l = lines[i];
    if (l.trim() && l.match(/^\s*/)[0].length < indent) break;
    body.push(l.slice(indent));
  }
  const src = body.join('\n').trimEnd();
  if (!src) throw new Error('extracted an empty script');
  return src;
}

// ---------------------------------------------------------------- stubs
// opts: now (ISO string -> WATCHDOG_NOW), eventName, html (live board body, or an Error to throw),
//       openIssue (an already-open alert), listThrows (the commit log cannot be read)
async function run(src, commits, opts = {}) {
  const out = { failed: null, info: [], issues: [], fetched: 0, listed: 0 };
  const core = {
    setFailed: m => { out.failed = m; },
    info: m => out.info.push(m),
  };
  const github = {
    rest: {
      repos: { listCommits: async () => {
        out.listed++;
        if (opts.listThrows) throw new Error('API rate limit exceeded');
        return { data: commits };
      } },
      issues: {
        listForRepo: async () => ({ data: opts.openIssue ? [{ number: 99 }] : [] }),
        create: async a => { out.issues.push({ kind: 'create', ...a }); },
        createComment: async a => { out.issues.push({ kind: 'comment', ...a }); },
        update: async a => { out.issues.push({ kind: 'update', ...a }); },
      },
    },
  };
  const context = { repo: { owner: 'aiejandrog', repo: 'foreclosure-leads' },
                    eventName: opts.eventName || 'schedule' };
  const fetchStub = async () => {
    out.fetched++;
    if (opts.html instanceof Error) throw opts.html;
    return { ok: true, status: 200, text: async () => (opts.html || '') };
  };
  const fn = new Function('github', 'core', 'context', 'fetch',
                          `return (async () => {\n${src}\n})();`);
  const saved = process.env.WATCHDOG_NOW;
  if (opts.now) process.env.WATCHDOG_NOW = opts.now; else delete process.env.WATCHDOG_NOW;
  try { await fn(github, core, context, fetchStub); }
  finally { if (saved === undefined) delete process.env.WATCHDOG_NOW; else process.env.WATCHDOG_NOW = saved; }
  return out;
}

const iso = (d, hh, mm) => `2026-09-${String(d).padStart(2, '0')}T${hh}:${mm}:00Z`;
const commit = (sha, msg, authored, committed, bot) => ({
  sha: sha.padEnd(7, '0'),
  author: { login: bot ? 'github-actions[bot]' : 'aiejandrog' },
  commit: { message: msg, author: { date: authored }, committer: { date: committed || authored } },
});

const PHONES = 'phones: nightly refresh (phones refreshed)';
const REPLIES = 'replies: morning scan baked into board (auto)';
const REFRESH = 'refresh: auto lead + phone update';

// Dates are relative to "now" so the fixtures never expire.
const day = n => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
const at = (n, hhmm) => `${day(n)}T${hhmm}:00Z`;

// ---------------------------------------------------------------- fixtures
const HEALTHY = [];
for (let n = 0; n <= 3; n++) {
  HEALTHY.push(commit(`h${n}r`, REFRESH, at(n, '09:42')));
  HEALTHY.push(commit(`h${n}p`, PHONES, at(n, '10:00')));
  HEALTHY.push(commit(`h${n}y`, REPLIES, at(n, '10:45')));
}
// the cloud balloon book is not a runner and must never be counted as one
HEALTHY.push(commit('hbot00', 'refresh: hard-money balloon book (auto)', at(0, '18:00'), null, true));
// nor is a human's ordinary work, whatever it is called
HEALTHY.push(commit('hman00', 'board: seal the owner text on the LIVE page', at(0, '17:36')));

// TWO RUNNERS: both machines fire the same task on the same day.
const TWO_RUNNERS = HEALTHY.concat([
  commit('t2p000', PHONES, at(1, '13:29')),
  commit('t2y000', REPLIES, at(1, '10:45')),
]);

// PUSH NOT LANDING: built on time, reached origin days later (committer date >> author date).
const STACKED = HEALTHY.concat([
  commit('st1p00', PHONES, at(3, '10:00'), at(0, '20:42')),
  commit('st1y00', REPLIES, at(3, '10:45'), at(0, '20:42')),
]);

// ENRICHMENT QUIET: phones and replies keep republishing, refresh never publishes.
const NO_REFRESH = HEALTHY.filter(c => !/^refresh: auto/.test(c.commit.message));

// EMPTY: nothing at all reached origin.
const SILENT = [];

// ---- issue #28: one refresh run publishes twice (early "fresh leads" + final "auto lead") ----
const EARLY = 'refresh: fresh leads';
// The real 2026-09-24 shape, moved to "yesterday": c4246bd 05:40 ET + 2d58ad3 08:45 ET. ONE run.
const EARLY_PLUS_FINAL = HEALTHY.filter(c => !/^refresh: auto/.test(c.commit.message)).concat(
  [0, 1, 2, 3].flatMap(n => [commit(`ef${n}e`, EARLY, at(n, '09:40')), commit(`ef${n}f`, REFRESH, at(n, '12:45'))]));
// 2026-09-22's shape: early 05:41, final 07:40, then a second early 09:30 ET. TWO runs.
const RERUN_SAME_DAY = EARLY_PLUS_FINAL.concat([commit('rr1e00', EARLY, at(1, '13:30'))]);
// Two machines racing: early, early, final, final on one day. TWO runs.
const OVERLAP = EARLY_PLUS_FINAL.concat([commit('ov1e00', EARLY, at(1, '09:55')), commit('ov1f00', REFRESH, at(1, '12:58'))]);
// A night whose early push was gated: only the final publish. ONE run.
const FINAL_ONLY = HEALTHY;

// ---- check 4: today's refresh started and never finished (fixed clock, noon ET on a summer day) ----
const NOON_ET = '2026-07-15T16:00:00Z';                     // 12:00 EDT
const fixedDay = (d, hhmm) => `2026-07-${String(d).padStart(2, '0')}T${hhmm}:00Z`;
const FIXED_HEALTHY = [];
for (const d of [12, 13, 14, 15]) {
  FIXED_HEALTHY.push(commit(`fx${d}e`, EARLY, fixedDay(d, '09:40')));
  FIXED_HEALTHY.push(commit(`fx${d}f`, REFRESH, fixedDay(d, '12:30')));
  FIXED_HEALTHY.push(commit(`fx${d}p`, PHONES, fixedDay(d, '13:30')));
}
// 2026-09-25's shape: started, pushed the early board at 05:40 ET, died at 06:53 on sleep.
const KILLED_TODAY = FIXED_HEALTHY.filter(c => c.sha !== 'fx15f00');

// ---- the morning check (about 08:30 ET) ----
const board = (day) => `<html><span id="upddate">${day} 05:52</span></html>`;
const AT_0830_EDT = '2026-07-15T12:31:00Z';               // the summer cron, 08:31 in New York
const AT_0730_EDT = '2026-07-15T11:31:00Z';               // not a cron, but a 07:xx clock
const AT_0930_EDT = '2026-07-15T13:31:00Z';               // the WINTER cron firing in summer -> skip
const AT_0830_EST = '2026-01-15T13:31:00Z';               // the winter cron, 08:31 in New York
const AT_0730_EST = '2026-01-15T12:31:00Z';               // the SUMMER cron firing in winter -> skip
const MORNING_OK = [commit('m1e000', EARLY, fixedDay(15, '09:41')), commit('m1f000', REFRESH, fixedDay(15, '12:20')),
                    commit('m0f000', REFRESH, fixedDay(14, '12:25'))];
const MORNING_EARLY_ONLY = [commit('m1e000', EARLY, fixedDay(15, '09:41')), commit('m0f000', REFRESH, fixedDay(14, '12:25'))];
const MORNING_MISSED = [commit('m0f000', REFRESH, fixedDay(14, '12:25')),
                        // yesterday evening's hand run carries yesterday's ET date, not today's
                        commit('m0x000', REFRESH, fixedDay(15, '01:30')),
                        commit('m0p000', PHONES, fixedDay(15, '10:00'))];
const WINTER_OK = [commit('w1e000', EARLY, '2026-01-15T10:41:00Z'), commit('w1f000', REFRESH, '2026-01-15T13:10:00Z')];

// ---------------------------------------------------------------- assertions
const cases = [
  { name: 'healthy week stays silent', fx: HEALTHY, fire: false },
  { name: 'two runners on one day fires', fx: TWO_RUNNERS, fire: true, want: /Two runners/ },
  { name: 'stacked unpushed commits fires', fx: STACKED, fire: true, want: /push was not landing/i },
  { name: 'refresh job gone quiet fires', fx: NO_REFRESH, fire: true, want: /No new leads/i },
  { name: 'total silence fires', fx: SILENT, fire: true, want: /Every runner is silent/i },
  // issue #28
  { name: '#28: early + final publish of ONE refresh run stays silent', fx: EARLY_PLUS_FINAL, fire: false },
  { name: '#28: a final-only refresh night is one run, silent', fx: FINAL_ONLY, fire: false },
  { name: '#28: a second refresh run the same day still fires', fx: RERUN_SAME_DAY, fire: true, want: /ran \*\*2 times/ },
  { name: '#28: two overlapping refresh runs fire', fx: OVERLAP, fire: true, want: /ran \*\*2 times/ },
  // check 4 (fixed clock)
  { name: 'fixed-clock healthy week stays silent', fx: FIXED_HEALTHY, fire: false, now: NOON_ET },
  { name: 'refresh started today and never finished fires', fx: KILLED_TODAY, fire: true, now: NOON_ET,
    want: /started and did not finish/ },
  { name: 'an early push only 2h old is not judged yet', fx: KILLED_TODAY, fire: false, now: '2026-07-15T11:45:00Z' },
];

// The morning job's step. `calls` pins that a skipped run touches neither the API nor the page.
const morningCases = [
  { name: 'morning: 08:31 EDT, early+final today, board built today -> silent',
    fx: MORNING_OK, now: AT_0830_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: an open alert is closed on the first clean morning',
    fx: MORNING_OK, now: AT_0830_EDT, html: board('2026-07-15'), openIssue: true, fire: false, closes: true },
  { name: 'morning: early push only (final still running) -> silent, not an alarm',
    fx: MORNING_EARLY_ONLY, now: AT_0830_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: no refresh publish today -> MISSED (the 09-26 sleep)',
    fx: MORNING_MISSED, now: AT_0830_EDT, html: board('2026-07-14'), fire: true, want: /MISSED: no refresh published today/ },
  { name: 'morning: refresh commit landed but live board is yesterday -> STALE',
    fx: MORNING_OK, now: AT_0830_EDT, html: board('2026-07-14'), fire: true, want: /STALE: the live board was last built 2026-07-14/ },
  { name: 'morning: live board unreadable -> alarms (fail-loud)',
    fx: MORNING_OK, now: AT_0830_EDT, html: new Error('getaddrinfo ENOTFOUND'), fire: true, want: /Cannot read the live board/ },
  { name: 'morning: commit log unreadable -> alarms (fail-loud)',
    fx: MORNING_OK, now: AT_0830_EDT, html: board('2026-07-15'), listThrows: true, fire: true, want: /Cannot read the commit log/ },
  { name: 'morning: an existing alert gets a comment, not a second issue',
    fx: MORNING_MISSED, now: AT_0830_EDT, html: board('2026-07-14'), openIssue: true, fire: true, want: /"kind":"comment"/ },
  { name: 'morning: summer, the 13:30 UTC (winter) cron skips without any call',
    fx: MORNING_MISSED, now: AT_0930_EDT, html: board('2026-07-14'), fire: false, noCalls: true },
  { name: 'morning: winter, the 12:30 UTC (summer) cron skips without any call',
    fx: MORNING_MISSED, now: AT_0730_EST, html: board('2026-01-14'), fire: false, noCalls: true },
  { name: 'morning: winter, 08:31 EST, healthy -> silent',
    fx: WINTER_OK, now: AT_0830_EST, html: board('2026-01-15'), fire: false },
  { name: 'morning: a manual run at 07:31 is not skipped by the hour gate',
    fx: MORNING_MISSED, now: AT_0730_EDT, html: board('2026-07-14'), eventName: 'workflow_dispatch', fire: true, want: /MISSED/ },
];

(async () => {
  const src = extractScript(STEP);
  const msrc = extractScript(MORNING_STEP);
  console.log(`extracted ${src.split('\n').length} + ${msrc.split('\n').length} lines from ${path.relative(HERE, YML)}\n`);

  let pass = 0, fail = 0;
  for (const c of morningCases) {
    let r;
    try {
      r = await run(msrc, c.fx, c);
    } catch (e) {
      console.log(`  FAIL  ${c.name} — threw: ${e.message}`);
      fail++; continue;
    }
    const fired = !!r.failed;
    const text = (r.failed || '') + JSON.stringify(r.issues);
    let ok = fired === c.fire;
    if (ok && c.want) ok = c.want.test(text);
    if (ok && c.noCalls) ok = r.fetched === 0 && r.listed === 0 && r.issues.length === 0;
    if (ok && c.closes) ok = r.issues.some(i => i.kind === 'update' && i.state === 'closed');
    if (ok) { console.log(`  pass  ${c.name}`); pass++; }
    else {
      console.log(`  FAIL  ${c.name} — expected ${c.fire ? 'an alarm' : 'silence'}, got ${fired ? `"${r.failed}"` : 'silence'}` +
                  ` (fetched ${r.fetched}, listed ${r.listed}, issues ${JSON.stringify(r.issues).slice(0, 300)})`);
      fail++;
    }
  }
  for (const c of cases) {
    let r;
    try {
      r = await run(src, c.fx, c);
    } catch (e) {
      console.log(`  FAIL  ${c.name} — threw: ${e.message}`);
      fail++; continue;
    }
    const fired = !!r.failed;
    const text = (r.failed || '') + JSON.stringify(r.issues);
    let ok = fired === c.fire;
    if (ok && c.want) ok = c.want.test(text);
    if (ok) { console.log(`  pass  ${c.name}`); pass++; }
    else {
      console.log(`  FAIL  ${c.name} — expected ${c.fire ? 'an alarm' : 'silence'}, got ${fired ? `"${r.failed}"` : 'silence'}`);
      fail++;
    }
  }

  // A watchdog you cannot make go red is not protection. Prove the healthy fixture is one edit
  // away from firing, so "silent" above means "looked and found nothing", not "never looks".
  const mutant = HEALTHY.concat([commit('mut000', PHONES, at(1, '15:00'))]);
  const m = await run(src, mutant);
  if (m.failed) { console.log('  pass  mutation check (one duplicate publish turns the healthy fixture red)'); pass++; }
  else { console.log('  FAIL  mutation check — healthy fixture stayed green with a duplicate publish added'); fail++; }

  if (process.argv.includes('--live')) {
    console.log('\n--- this repo, right now ---');
    // --now=<ISO> replays the log as it stood at that moment (e.g. the morning an alert fired).
    const nowArg = (process.argv.find(a => a.startsWith('--now=')) || '').slice(6) || null;
    const until = nowArg ? new Date(nowArg) : new Date();
    const raw = execFileSync('git', ['log', 'origin/main', '--format=%H%x1f%an%x1f%aI%x1f%cI%x1f%s%x1e',
      `--since=${new Date(until - 4 * 86400000).toISOString()}`, `--until=${until.toISOString()}`],
      { cwd: HERE, encoding: 'utf8', maxBuffer: 1 << 24 });
    const live = raw.split('\x1e').filter(s => s.trim()).map(rec => {
      const [sha, an, aI, cI, msg] = rec.replace(/^\n/, '').split('\x1f');
      return commit(sha.slice(0, 7), msg, aI, cI, an === 'github-actions[bot]');
    });
    const r = await run(src, live, { now: nowArg || undefined });
    console.log(r.failed ? `ALARM: ${r.failed}` : 'clean');
    for (const i of r.issues) console.log((i.body || '').replace(/^/gm, '  '));
  }

  console.log(`\n${pass} pass / ${fail} fail`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
