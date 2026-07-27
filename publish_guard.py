"""Refuse to publish a board that is materially POORER than the one already live.

WHY THIS EXISTS
The cloud runner cannot regenerate most of the enrichment. It has no 2Captcha key (so Miami-Dade
records/lien chains are `--cached-only`), and the BatchData balance is exhausted (so skip-trace
phones and Palm Beach liens return nothing). When its restore cache is cold it therefore builds a
CORRECT but GUTTED board — right lead count, zero lien chains, zero phones — and published it
straight over the enrichment-rich build Alejandro produces locally.

Measured on real commits: local builds ship at ~3.6MB, the CI builds that overwrote them shipped at
2.7MB and 1.5MB. Every daily run was quietly stripping lien chains and phone numbers off the live
site. A green workflow that degrades the product is worse than a red one that does nothing.

HOW
make_tracker() writes a plaintext `<!-- DEALFLOW-COVERAGE {...} -->` census on the first line of
docs/index.html (counts only — never a name, number or address). This compares the freshly built
file against the copy currently committed on origin/main and blocks the publish when enrichment
collapsed. Growth, small wobble, and first-ever builds all pass untouched.

Exit codes:  0 = safe to publish   ·   2 = REGRESSION, caller must skip the publish

Usage:
    python publish_guard.py                     # compare against origin/main
    python publish_guard.py --base HEAD         # compare against the local checkout
    python publish_guard.py --strict            # any drop at all fails (default: material drops)
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, 'docs', 'index.html')
MARK = re.compile(r'<!--\s*DEALFLOW-COVERAGE\s*(\{.*?\})\s*-->')

# A field only trips the guard when it falls by BOTH a ratio and an absolute amount — so a build that
# legitimately drops a couple of stale leads never blocks, but a wipeout always does.
FIELDS = {
    'liens':  (0.50, 20),   # lien chains: the most expensive data and the first thing CI loses
    'phones': (0.50, 25),   # dialable numbers
    'wp':     (0.50, 10),   # Whitepages households
    'arv':    (0.50, 25),   # comps
    'leads':  (0.70, 40),   # the board itself (scrape already has its own <20 guard)
}


def cov_of_text(txt):
    m = MARK.search(txt[:4000])
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def cov_of_git(ref):
    """Coverage of docs/index.html at a git ref, or None when absent/unmarked."""
    try:
        blob = subprocess.run(['git', 'show', f'{ref}:docs/index.html'],
                              cwd=HERE, capture_output=True, timeout=120)
        if blob.returncode != 0:
            return None
        return cov_of_text(blob.stdout[:4000].decode('utf-8', 'replace'))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='origin/main', help='git ref to compare against')
    ap.add_argument('--strict', action='store_true', help='fail on ANY drop')
    args = ap.parse_args()

    if not os.path.exists(DOCS):
        print('publish-guard: docs/index.html missing — nothing to check'); return 0
    new = cov_of_text(open(DOCS, encoding='utf-8', errors='replace').read(4000))
    if not new:
        # An unmarked build predates the marker. Never block on that — just say so.
        print('publish-guard: new build carries no coverage marker (older template?) — allowing'); return 0

    subprocess.run(['git', 'fetch', 'origin', '--quiet'], cwd=HERE, capture_output=True, timeout=180)
    old = cov_of_git(args.base)
    if not old:
        print(f'publish-guard: no coverage marker on {args.base} (first marked build) — allowing')
        print('  new:', json.dumps(new, separators=(',', ':')))
        return 0

    print('publish-guard: comparing enrichment')
    print('  live:', json.dumps(old, separators=(',', ':')))
    print('  new :', json.dumps(new, separators=(',', ':')))

    regressions = []
    for f, (ratio, floor) in FIELDS.items():
        o, n = int(old.get(f) or 0), int(new.get(f) or 0)
        if o <= 0 or n >= o:
            continue
        drop = o - n
        if args.strict or (n < o * ratio and drop >= floor):
            regressions.append(f'{f}: {o} -> {n}  (-{drop}, -{round(100*drop/o)}%)')

    if not regressions:
        print('publish-guard: OK — no material enrichment loss, safe to publish')
        return 0

    print('')
    print('publish-guard: BLOCKED — this build would STRIP data from the live board:')
    for r in regressions:
        print('   ' + r)
    print('')
    print('  Most likely cause: the runner could not regenerate enrichment (no 2Captcha key, or the')
    print('  BatchData balance is exhausted) AND its restore cache was cold, so it built a gutted')
    print('  board. The live site has been left untouched — that is the correct outcome.')
    print('  It will self-heal once the cache carries the chains again, or once a local run publishes.')
    return 2


if __name__ == '__main__':
    sys.exit(main())
