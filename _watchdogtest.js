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
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const HERE = __dirname;
const YML = path.join(HERE, '.github', 'workflows', 'freshness-watchdog.yml');
const STEP = 'Check the runners are behaving';

// ---------------------------------------------------------------- extract the shipped script
function extractScript() {
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
function run(src, commits) {
  const out = { failed: null, info: [], issues: [] };
  const core = {
    setFailed: m => { out.failed = m; },
    info: m => out.info.push(m),
  };
  const github = {
    rest: {
      repos: { listCommits: async () => ({ data: commits }) },
      issues: {
        listForRepo: async () => ({ data: [] }),
        create: async a => { out.issues.push({ kind: 'create', ...a }); },
        createComment: async a => { out.issues.push({ kind: 'comment', ...a }); },
        update: async a => { out.issues.push({ kind: 'update', ...a }); },
      },
    },
  };
  const context = { repo: { owner: 'aiejandrog', repo: 'foreclosure-leads' } };
  const fn = new Function('github', 'core', 'context', `return (async () => {\n${src}\n})();`);
  return fn(github, core, context).then(() => out);
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

// ---------------------------------------------------------------- assertions
const cases = [
  { name: 'healthy week stays silent', fx: HEALTHY, fire: false },
  { name: 'two runners on one day fires', fx: TWO_RUNNERS, fire: true, want: /Two runners/ },
  { name: 'stacked unpushed commits fires', fx: STACKED, fire: true, want: /push was not landing/i },
  { name: 'refresh job gone quiet fires', fx: NO_REFRESH, fire: true, want: /No new leads/i },
  { name: 'total silence fires', fx: SILENT, fire: true, want: /Every runner is silent/i },
];

(async () => {
  const src = extractScript();
  console.log(`extracted ${src.split('\n').length} lines from ${path.relative(HERE, YML)}\n`);

  let pass = 0, fail = 0;
  for (const c of cases) {
    let r;
    try {
      r = await run(src, c.fx);
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
    const raw = execFileSync('git', ['log', '--format=%H%x1f%an%x1f%aI%x1f%cI%x1f%s%x1e',
      '--since=4 days ago'], { cwd: HERE, encoding: 'utf8', maxBuffer: 1 << 24 });
    const live = raw.split('\x1e').filter(s => s.trim()).map(rec => {
      const [sha, an, aI, cI, msg] = rec.replace(/^\n/, '').split('\x1f');
      return commit(sha.slice(0, 7), msg, aI, cI, an === 'github-actions[bot]');
    });
    const r = await run(src, live);
    console.log(r.failed ? `ALARM: ${r.failed}` : 'clean');
    for (const i of r.issues) console.log((i.body || '').replace(/^/gm, '  '));
  }

  console.log(`\n${pass} pass / ${fail} fail`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
