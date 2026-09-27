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
const ALERTS_STEP = 'Sync pipeline alerts';

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
  // listCommits pages like the real endpoint (per_page, page; newest first as the fixture lists
  // them), and paginate walks pages until a short one, like Octokit's. A script that reads only
  // the first page of a long window sees exactly what production saw on 09-26 (issue #87).
  const github = {
    paginate: async (method, params) => {
      const all = [];
      for (let page = 1; ; page++) {
        const r = await method({ ...params, page });
        all.push(...r.data);
        if (r.data.length < (params.per_page || 30)) return all;
      }
    },
    rest: {
      repos: { listCommits: async (p = {}) => {
        out.listed++;
        if (opts.listThrows) throw new Error('API rate limit exceeded');
        const per = p.per_page || 30, page = p.page || 1;
        return { data: commits.slice((page - 1) * per, page * per) };
      } },
        issues: {
        listForRepo: async () => ({ data: opts.openIssues || (opts.openIssue ? [{ number: 99 }] : []) }),
        create: async a => { out.issues.push({ kind: 'create', ...a }); },
        createComment: async a => { out.issues.push({ kind: 'comment', ...a }); },
        update: async a => { out.issues.push({ kind: 'update', ...a }); },
        createLabel: async a => { out.issues.push({ kind: 'label', ...a }); },
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
  const fn = new Function('github', 'core', 'context', 'fetch', 'require',
                          `return (async () => {\n${src}\n})();`);
  const saved = process.env.WATCHDOG_NOW;
  const savedAlerts = process.env.WATCHDOG_ALERTS_JSON;
  const savedFile = process.env.WATCHDOG_ALERTS_FILE;
  if (opts.now) process.env.WATCHDOG_NOW = opts.now; else delete process.env.WATCHDOG_NOW;
  if (Object.prototype.hasOwnProperty.call(opts, 'alertsJson')) {
    if (opts.alertsJson == null) delete process.env.WATCHDOG_ALERTS_JSON;
    else process.env.WATCHDOG_ALERTS_JSON = opts.alertsJson;
  }
  if (opts.alertsFile) process.env.WATCHDOG_ALERTS_FILE = opts.alertsFile;
  else if (opts.alertsMissing) process.env.WATCHDOG_ALERTS_FILE = path.join(HERE, 'no-such-pipeline-alerts.json');
  try { await fn(github, core, context, fetchStub, require); }
  finally {
    if (saved === undefined) delete process.env.WATCHDOG_NOW; else process.env.WATCHDOG_NOW = saved;
    if (savedAlerts === undefined) delete process.env.WATCHDOG_ALERTS_JSON; else process.env.WATCHDOG_ALERTS_JSON = savedAlerts;
    if (savedFile === undefined) delete process.env.WATCHDOG_ALERTS_FILE; else process.env.WATCHDOG_ALERTS_FILE = savedFile;
  }
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

// ---- the morning check (about 07:15 ET, before the 08:00 Morning Worker) ----
const board = (day) => `<html><span id="upddate">${day} 05:52</span></html>`;
const AT_0715_EDT = '2026-07-15T11:15:00Z';               // the summer cron, 07:15 in New York
const AT_0815_EDT = '2026-07-15T12:15:00Z';               // the WINTER cron firing in summer -> 08:15 EDT, skip
const AT_0715_EST = '2026-01-15T12:15:00Z';               // the winter cron, 07:15 in New York
const AT_0615_EST = '2026-01-15T11:15:00Z';               // the SUMMER cron firing in winter -> 06:15 EST, skip
const MORNING_OK = [commit('m1e000', EARLY, fixedDay(15, '09:41')), commit('m1f000', REFRESH, fixedDay(15, '12:20')),
                    commit('m0f000', REFRESH, fixedDay(14, '12:25'))];
const MORNING_EARLY_ONLY = [commit('m1e000', EARLY, fixedDay(15, '09:41')), commit('m0f000', REFRESH, fixedDay(14, '12:25'))];
// Final publish only, landed at 07:10 EDT, before the check. A gated early push is still a publish.
const MORNING_FINAL_ONLY = [commit('m1f000', REFRESH, fixedDay(15, '11:10')), commit('m0f000', REFRESH, fixedDay(14, '12:25'))];
const MORNING_MISSED = [commit('m0f000', REFRESH, fixedDay(14, '12:25')),
                        // yesterday evening's hand run carries yesterday's ET date, not today's
                        commit('m0x000', REFRESH, fixedDay(15, '01:30')),
                        commit('m0p000', PHONES, fixedDay(15, '10:00'))];
const WINTER_OK = [commit('w1e000', EARLY, '2026-01-15T10:41:00Z'), commit('w1f000', REFRESH, '2026-01-15T13:10:00Z')];

// ---- issue #87: more than one page of commits in the window ----
// 150 ordinary merges (newest, so they fill page 1 and half of page 2) ahead of the runners'
// commits. Reading only page 1 finds no refresh at all and cries "No new leads".
const pad = (n, stamp) => Array.from({ length: n }, (_, i) =>
  commit(`pd${String(i).padStart(4, '0')}`, `Merge pull request #${1000 + i} from aiejandrog/some-branch`, stamp));
const PAGED_HEALTHY = pad(150, at(0, '18:30')).concat(HEALTHY);
const PAGED_NO_REFRESH = pad(150, at(0, '18:30')).concat(NO_REFRESH);
const MORNING_PAGED_OK = pad(150, fixedDay(15, '12:25')).concat(MORNING_OK);

// ---------------------------------------------------------------- assertions
const cases = [
  { name: 'healthy week stays silent', fx: HEALTHY, fire: false },
  { name: 'alerts publishes are not a second runner',
    fx: HEALTHY.concat([
      commit('al0000', 'alerts: pipeline status', at(1, '01:00')),
      commit('al0001', 'alerts: pipeline status', at(1, '13:00')),
    ]), fire: false },
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
  // issue #87
  { name: '#87: a refresh behind 150 newer commits is still found (pages past 100)', fx: PAGED_HEALTHY, fire: false },
  { name: '#87: a quiet refresh behind 150 newer commits still fires', fx: PAGED_NO_REFRESH, fire: true, want: /No new leads/i },
];

// The morning job's step. `calls` pins that a skipped run touches neither the API nor the page.
const morningCases = [
  { name: 'morning: 07:15 EDT, early+final today, board built today -> silent',
    fx: MORNING_OK, now: AT_0715_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: an open alert is closed on the first clean morning',
    fx: MORNING_OK, now: AT_0715_EDT, html: board('2026-07-15'), openIssue: true, fire: false, closes: true },
  { name: 'morning: 07:15, early push only (final often lands 07:40-08:45) -> silent',
    fx: MORNING_EARLY_ONLY, now: AT_0715_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: 07:15, final publish only, before the check -> silent',
    fx: MORNING_FINAL_ONLY, now: AT_0715_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: no refresh publish today -> MISSED (the 09-26 sleep)',
    fx: MORNING_MISSED, now: AT_0715_EDT, html: board('2026-07-14'), fire: true, want: /MISSED: no refresh published today/ },
  { name: 'morning: the miss says the 08:00 worker has not run yet',
    fx: MORNING_MISSED, now: AT_0715_EDT, html: board('2026-07-14'), fire: true, want: /has not run yet/ },
  { name: 'morning: refresh commit landed but live board is yesterday -> STALE',
    fx: MORNING_OK, now: AT_0715_EDT, html: board('2026-07-14'), fire: true, want: /STALE: the live board was last built 2026-07-14/ },
  { name: 'morning: live board unreadable -> alarms (fail-loud)',
    fx: MORNING_OK, now: AT_0715_EDT, html: new Error('getaddrinfo ENOTFOUND'), fire: true, want: /Cannot read the live board/ },
  { name: 'morning: commit log unreadable -> alarms (fail-loud)',
    fx: MORNING_OK, now: AT_0715_EDT, html: board('2026-07-15'), listThrows: true, fire: true, want: /Cannot read the commit log/ },
  { name: 'morning: an existing alert gets a comment, not a second issue',
    fx: MORNING_MISSED, now: AT_0715_EDT, html: board('2026-07-14'), openIssue: true, fire: true, want: /"kind":"comment"/, noCreate: true },
  { name: 'morning: a new nightly-missed issue is titled for the Gmail filter',
    fx: MORNING_MISSED, now: AT_0715_EDT, html: board('2026-07-14'), fire: true,
    want: /DEALFLOW ALERT: nightly refresh missed or stale/ },
  { name: 'morning: an old nightly-missed title is renamed, not duplicated',
    fx: MORNING_MISSED, now: AT_0715_EDT, html: board('2026-07-14'),
    openIssues: [{ number: 7, title: '⚠️ DEALFLOW nightly refresh missed or stale (2026-09-25)', labels: ['nightly-missed'] }],
    fire: true, noCreate: true, retitle: 'DEALFLOW ALERT: nightly refresh missed or stale' },
  { name: 'morning: summer, the 12:15 UTC (winter) cron skips without any call',
    fx: MORNING_MISSED, now: AT_0815_EDT, html: board('2026-07-14'), fire: false, noCalls: true },
  { name: 'morning: winter, the 11:15 UTC (summer) cron skips without any call',
    fx: MORNING_MISSED, now: AT_0615_EST, html: board('2026-01-14'), fire: false, noCalls: true },
  { name: 'morning: winter, 07:15 EST, healthy -> silent',
    fx: WINTER_OK, now: AT_0715_EST, html: board('2026-01-15'), fire: false },
  { name: 'morning: #87 today\'s refresh behind 150 newer commits is still found',
    fx: MORNING_PAGED_OK, now: AT_0715_EDT, html: board('2026-07-15'), fire: false },
  { name: 'morning: a manual run at 08:15 is not skipped by the hour gate',
    fx: MORNING_MISSED, now: AT_0815_EDT, html: board('2026-07-14'), eventName: 'workflow_dispatch', fire: true, want: /MISSED/ },
];

(async () => {
  const ymlText = fs.readFileSync(YML, 'utf8');
  const src = extractScript(STEP);
  const msrc = extractScript(MORNING_STEP);
  console.log(`extracted ${src.split('\n').length} + ${msrc.split('\n').length} lines from ${path.relative(HERE, YML)}\n`);

  let pass = 0, fail = 0;
  // The deadline is the cron, which the extracted script cannot see. 11:15 UTC is 07:15 EDT;
  // 12:15 UTC is 07:15 EST. The old 08:30 pair must not still be scheduled.
  const cronsOk = ymlText.includes("cron: '15 11 * * *'") && ymlText.includes("cron: '15 12 * * *'")
    && !ymlText.includes("cron: '30 12 * * *'") && !ymlText.includes("cron: '30 13 * * *'");
  if (cronsOk) { console.log('  pass  morning crons are 11:15 and 12:15 UTC (07:15 ET, both DSTs)'); pass++; }
  else { console.log('  FAIL  morning crons are not the 07:15 ET pair'); fail++; }
  const nameOk = /^name:\s*DEALFLOW\b/m.test(ymlText);
  if (nameOk) { console.log('  pass  workflow name starts with DEALFLOW (failure mail subject)'); pass++; }
  else { console.log('  FAIL  workflow name does not start with DEALFLOW'); fail++; }
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
    if (ok && c.noCreate) ok = !r.issues.some(i => i.kind === 'create');
    if (ok && c.retitle) ok = r.issues.some(i => i.kind === 'update' && String(i.title || '').startsWith(c.retitle));
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

  const ALERT_NOW = '2026-09-26T15:00:00Z';
  const freshDoc = (alerts, published_at) => JSON.stringify({
    version: 1, published_at: published_at || '2026-09-26T14:00:00Z', alerts,
  });
  const openAlert = (key, severity) => ({
    number: 41, title: '⚠️ [' + severity + '] [' + key + '] old',
    body: 'alert-key: ' + key + '\nseverity: ' + severity + '\n\nold text',
  });
  const alertCases = [
    { name: 'alerts: a new fail opens one issue and fails the run',
      alertsJson: freshDoc([{ key: 'tracerfy-credits', severity: 'fail', text: 'Tracerfy balance 0 credits.' }]),
      fire: true, creates: 'tracerfy-credits' },
    { name: 'alerts: the same severity comments and does not fail again',
      alertsJson: freshDoc([{ key: 'tracerfy-credits', severity: 'warn', text: 'Tracerfy balance 400 credits, warn under 500.' }]),
      openIssues: [openAlert('tracerfy-credits', 'warn')], fire: false, comments: true },
    { name: 'alerts: an unchanged alert is not commented again the same Eastern day',
      alertsJson: freshDoc([{ key: 'tracerfy-credits', severity: 'warn', text: 'Tracerfy balance 400 credits, warn under 500.' }]),
      openIssues: [{
        number: 41,
        title: '⚠️ [warn] [tracerfy-credits] old',
        body: 'alert-key: tracerfy-credits\nseverity: warn\n\nTracerfy balance 400 credits, warn under 500.',
        updated_at: '2026-09-26T14:30:00Z',
      }],
      now: ALERT_NOW, fire: false, comments: false, noCreate: true,
      retitle: 'DEALFLOW ALERT: Tracerfy credits low' },
    { name: 'alerts: an unchanged alert comments once the next Eastern day',
      alertsJson: freshDoc([{ key: 'tracerfy-credits', severity: 'warn', text: 'Tracerfy balance 400 credits, warn under 500.' }]),
      openIssues: [{
        number: 41,
        title: '⚠️ [warn] [tracerfy-credits] old',
        body: 'alert-key: tracerfy-credits\nseverity: warn\n\nTracerfy balance 400 credits, warn under 500.',
        updated_at: '2026-09-25T14:30:00Z',
      }],
      now: ALERT_NOW, fire: false, comments: true },
    { name: 'alerts: warn to fail is worsened and fails the run',
      alertsJson: freshDoc([{ key: 'tracerfy-credits', severity: 'fail', text: 'Tracerfy balance 0 credits.' }]),
      openIssues: [openAlert('tracerfy-credits', 'warn')], fire: true, comments: true },
    { name: 'alerts: a cleared key closes the issue and does not fail',
      alertsJson: freshDoc([]),
      openIssues: [openAlert('tracerfy-credits', 'fail')], fire: false, closes: true },
    { name: 'alerts: an address in the text is withheld',
      alertsJson: freshDoc([{ key: 'bounce-rate', severity: 'fail', text: 'see owner@example.com now' }]),
      fire: true, creates: 'bounce-rate', noAt: true },
    { name: 'alerts: invalid JSON fails and does not close',
      alertsJson: '{',
      openIssues: [openAlert('tracerfy-credits', 'fail')], fire: true, noClose: true },
    { name: 'alerts: a file with no alerts array does not close',
      alertsJson: JSON.stringify({ published_at: '2026-09-26T14:00:00Z', alerts: { bad: true } }),
      openIssues: [openAlert('tracerfy-credits', 'fail')], fire: true, noClose: true },
    { name: 'alerts: missing file, nothing open, is not armed yet',
      alertsJson: null, alertsMissing: true, fire: false },
    { name: 'alerts: missing file while an issue is open fails and does not close it',
      alertsJson: null, alertsMissing: true,
      openIssues: [openAlert('tracerfy-credits', 'fail')], fire: true, noClose: true },
    { name: 'alerts: a fresh empty file stays quiet',
      alertsJson: freshDoc([]), fire: false },
    { name: 'alerts: published_at 31h ago is its own alert',
      alertsJson: freshDoc([], '2026-09-25T08:00:00Z'), now: ALERT_NOW, fire: true, creates: 'alerts-unpublished' },
    { name: 'alerts: published_at 29h ago is not stale',
      alertsJson: freshDoc([], '2026-09-25T10:00:00Z'), now: ALERT_NOW, fire: false },
  ];
  const asrc = extractScript(ALERTS_STEP);
  for (const c of alertCases) {
    let r;
    try { r = await run(asrc, [], c); }
    catch (e) {
      console.log(`  FAIL  ${c.name} — threw: ${e.message}`);
      fail++; continue;
    }
    const fired = !!r.failed;
    const created = r.issues.find(i => i.kind === 'create' && String(i.body || '').includes('alert-key: ' + (c.creates || '___none___')));
    let good = fired === c.fire;
    if (good && c.creates) good = !!created && String(created.title || '').startsWith('DEALFLOW ALERT: ');
    if (good && c.noCreate) good = !r.issues.some(i => i.kind === 'create');
    if (good && c.retitle) good = r.issues.some(i => i.kind === 'update' && String(i.title || '').startsWith(c.retitle));
    if (good && c.comments) good = r.issues.some(i => i.kind === 'comment');
    if (good && c.comments === false) good = !r.issues.some(i => i.kind === 'comment');
    if (good && c.closes) good = r.issues.some(i => i.kind === 'update' && i.state === 'closed');
    if (good && c.noClose) good = !r.issues.some(i => i.kind === 'update' && i.state === 'closed');
    if (good && c.noAt) {
      const blob = JSON.stringify(r.issues);
      good = !blob.includes('@') && blob.includes('withheld');
    }
    if (good) { console.log(`  pass  ${c.name}`); pass++; }
    else {
      console.log(`  FAIL  ${c.name} — expected ${c.fire ? 'an alarm' : 'silence'}, got ${fired ? JSON.stringify(r.failed) : 'silence'} issues ${JSON.stringify(r.issues).slice(0, 400)}`);
      fail++;
    }
  }

  const ALERT_KEYS = [
    'tracerfy-credits', 'captcha-balance', 'paid-reads-cap', 'bounce-rate',
    'optout-sync', 'morning-sends', 'laptop-readiness', 'healthcheck-fail',
    'alerts-unpublished', 'alerts-redacted', 'stale-refresh-flag',
  ];
  for (const key of ALERT_KEYS) {
    const name = 'alerts: title prefix for ' + key;
    try {
      const r = await run(asrc, [], {
        alertsJson: freshDoc([{ key, severity: 'fail', text: 'count 1' }]),
        now: ALERT_NOW,
      });
      const created = r.issues.filter(i => i.kind === 'create');
      const title = created.length === 1 ? String(created[0].title || '') : '';
      const ok = created.length === 1
        && title.startsWith('DEALFLOW ALERT: ')
        && title.slice('DEALFLOW ALERT: '.length).trim().length > 0
        && String(created[0].body || '').includes('alert-key: ' + key);
      if (ok) { console.log('  pass  ' + name); pass++; }
      else { console.log('  FAIL  ' + name + ' — ' + JSON.stringify(created).slice(0, 300)); fail++; }
    } catch (e) {
      console.log('  FAIL  ' + name + ' — threw: ' + e.message);
      fail++;
    }
  }
  {
    const name = 'alerts: an open issue is matched by its key, not its title';
    try {
      const r = await run(asrc, [], {
        alertsJson: freshDoc([{ key: 'optout-sync', severity: 'fail', text: 'count 1' }]),
        openIssues: [{
          number: 8,
          title: 'unrelated subject with no key',
          body: 'alert-key: optout-sync\nseverity: fail\n\ncount 1',
        }],
        now: ALERT_NOW,
      });
      const created = r.issues.filter(i => i.kind === 'create');
      const renamed = r.issues.some(i => i.kind === 'update' && String(i.title || '').startsWith('DEALFLOW ALERT: '));
      if (!created.length && renamed) { console.log('  pass  ' + name); pass++; }
      else { console.log('  FAIL  ' + name + ' — ' + JSON.stringify(r.issues).slice(0, 400)); fail++; }
    } catch (e) {
      console.log('  FAIL  ' + name + ' — threw: ' + e.message);
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
