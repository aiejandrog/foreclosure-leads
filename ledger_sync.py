#!/usr/bin/env python
"""ledger_sync.py — one shared DO-NOT-CONTACT + sent-mail ledger for every machine.

WHY (2026-09-05 incident): two machines each trusted their own copy of optouts.json. The desktop's
copy froze on 08-22 while the laptop kept collecting STOPs; the desktop then sent 746 emails
through the stale copy across three send-days. Zero opted-out people were hit — by luck, not by
design. The permanent fix is ONE ledger both machines pull before sending and push after writing.

WHERE: private GitHub repo `aiejandrog/dealflow-ledgers-private`, cloned OUTSIDE this public repo
at ~/DEALFLOW/ledgers-private (homeowner PII never rides a public remote; .gitignore here keeps
optouts.json/mail_sent.json untracked as a second fence).

SEMANTICS — union-merge, never overwrite:
  * optouts.json is SAFETY-ONE-WAY: the union only ever ADDS suppression entries. A key present
    in either copy survives; a key present in both keeps the richer (dict beats scalar) value.
    Nothing in this tool can clear an opt-out.
  * mail_sent.json is append-only: union of entries, deduped on (ts_utc, to). Order restored by
    ts_utc so cadence logic reads one true timeline.

FLOW (same command on both machines):  python ledger_sync.py
  1. clone repo if missing, else `git pull --rebase`
  2. union-merge repo copies with the local working files (both directions, in memory)
  3. write the union to BOTH places only when it changed
  4. commit + push when the repo copy changed; pull-rebase-push retry on a race
FAIL-LOUD: any step that cannot complete raises and exits non-zero. A sync that cannot evaluate
must never print ok (the wrong-engine lesson). Exit 0 = both files proven identical to the repo.

MIGRATION ORDER: the USB union (desktop export -> laptop mail_sent; laptop ledgers -> desktop)
happens FIRST, by hand. First `ledger_sync.py` run on the laptop seeds the repo; first run on the
desktop unions its history in. After both, every machine reads the same two files.

send_server.py keeps its own staleness backstop (d0f9044): if nobody has synced for >2 days the
bridge refuses owner sends even though this tool exists. Guards stack; they do not replace.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_URL = 'https://github.com/aiejandrog/dealflow-ledgers-private.git'
REPO_DIR = os.path.join(os.path.expanduser('~'), 'DEALFLOW', 'ledgers-private')
FILES = ('optouts.json', 'mail_sent.json')


def run(args, cwd=None, ok_codes=(0,)):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if p.returncode not in ok_codes:
        raise SystemExit('FAIL-LOUD: %r rc=%d\n%s%s' % (' '.join(args), p.returncode, p.stdout[-800:], p.stderr[-800:]))
    return p.stdout


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)          # a CORRUPT ledger raises — never silently treated as empty


def write_if_changed(path, obj, current):
    new = json.dumps(obj, indent=2, ensure_ascii=False)
    if current is not None and json.dumps(current, indent=2, ensure_ascii=False) == new:
        return False
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(new)
    os.replace(tmp, path)
    return True


def union_optouts(a, b):
    """SAFETY-ONE-WAY union of two optouts envelopes. Only ever adds."""
    a, b = a or {}, b or {}
    out = {'_dealflow_notes': 1,
           'exported': max(str(a.get('exported') or ''), str(b.get('exported') or '')) or time.strftime('%Y-%m-%d'),
           'device': a.get('device') or b.get('device') or '',
           'notes': {}}
    for src in (a.get('notes') or {}), (b.get('notes') or {}):
        for k, v in src.items():
            cur = out['notes'].get(k)
            if cur is None or (not isinstance(cur, dict) and isinstance(v, dict)):
                out['notes'][k] = v
            elif isinstance(cur, dict) and isinstance(v, dict):
                merged = dict(v); merged.update({kk: vv for kk, vv in cur.items() if vv not in (None, '', 0, False)})
                out['notes'][k] = merged
    # legacy bare-list shape tolerated on input, never produced on output
    for src in (a, b):
        if isinstance(src, list):
            for x in src:
                out['notes'].setdefault(str(x), True)
    return out


def union_mail(a, b):
    """Append-only union deduped on (ts_utc, to); one true timeline by ts_utc."""
    seen, out = set(), []
    for entry in (a or []) + (b or []):
        key = (str(entry.get('ts_utc') or ''), str(entry.get('to') or '').strip().lower())
        if key in seen:
            continue
        seen.add(key); out.append(entry)
    out.sort(key=lambda e: str(e.get('ts_utc') or ''))
    return out


def main():
    if not os.path.isdir(os.path.join(REPO_DIR, '.git')):
        os.makedirs(os.path.dirname(REPO_DIR), exist_ok=True)
        print('cloning ledgers repo ->', REPO_DIR)
        run(['git', 'clone', REPO_URL, REPO_DIR])
    else:
        run(['git', 'pull', '--rebase', 'origin', 'main'], cwd=REPO_DIR)

    changed_repo = changed_local = False
    for name in FILES:
        local_p, repo_p = os.path.join(HERE, name), os.path.join(REPO_DIR, name)
        default = {} if name == 'optouts.json' else []
        local, remote = load(local_p, default), load(repo_p, default)
        union = union_optouts(local, remote) if name == 'optouts.json' else union_mail(local, remote)
        if write_if_changed(local_p, union, local):
            changed_local = True; print('local  updated:', name)
        if write_if_changed(repo_p, union, remote):
            changed_repo = True; print('repo   updated:', name)
        n = len(union['notes']) if name == 'optouts.json' else len(union)
        print('  %s: %d entries in union' % (name, n))

    if changed_repo:
        run(['git', 'add', '-A'], cwd=REPO_DIR)
        run(['git', 'commit', '-m', 'ledger union %s' % time.strftime('%Y-%m-%d %H:%M')], cwd=REPO_DIR)
        for attempt in (1, 2, 3):
            p = subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=REPO_DIR, capture_output=True, text=True)
            if p.returncode == 0:
                break
            run(['git', 'pull', '--rebase', 'origin', 'main'], cwd=REPO_DIR)
        else:
            raise SystemExit('FAIL-LOUD: push failed after 3 rebase retries')
    # PROOF, not vibes: local files must now equal the repo copies byte-for-byte semantics
    for name in FILES:
        a = load(os.path.join(HERE, name), None)
        b = load(os.path.join(REPO_DIR, name), None)
        if json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True):
            raise SystemExit('FAIL-LOUD: %s differs between local and repo AFTER sync' % name)
    # Freshness stamp (2026-09-05, desktop first run): send_server's staleness guard reads the
    # optouts.json mtime. A sync that just PROVED local == repo is a fresh verification even when
    # nothing changed - without this an unchanged-but-current ledger trips the guard (it did:
    # 13.9 days, seconds after the repo was seeded) and a quiet week would do the same.
    for name in FILES:
        os.utime(os.path.join(HERE, name), None)
    print('SYNCED: local == repo for %s (local %s, repo %s)' % (', '.join(FILES), 'changed' if changed_local else 'unchanged', 'pushed' if changed_repo else 'unchanged'))


if __name__ == '__main__':
    main()
