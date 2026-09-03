"""opaque_img_names.py -- rename docs/img/{folio}[_sv|_pa].jpg -> {hash16}[_sv|_pa].jpg

WHY THIS EXISTS
docs/img/ was named by FOLIO in plaintext on a PUBLIC repo. `git ls-files docs/img/` returned
all 1,772 folios, each resolving on the county appraiser to owner + address + value. The
encrypted row payload was defeated by its own image URLs.

This is the one-time migration. property_photos.py is already updated to WRITE hashed names on
new photos; this script converts the 1,772 existing ones to match, so a rebuild does not have to
re-download every photo (Google Street View + Zillow scraping = real money + hours).

BY DEFAULT THIS IS A DRY RUN. Pass --apply to actually rename. Uses `git mv` so history is
preserved as renames rather than delete+add pairs.

RESIDUAL EXPOSURE: purging git history is NOT done here. Six weeks of public exposure means old
folios are in commits already cloned, forked and cached; rewriting history would rewrite every
commit hash and break the two-engine push architecture. Current TIP stops the leak; historical
exposure is a separate decision (Alejandro's).
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMGDIR = os.path.join(HERE, 'docs', 'img')

# Import the SAME hash function property_photos.py uses. If we redefined it here the two could
# drift and every file this script renamed would become orphaned on the next rebuild.
sys.path.insert(0, HERE)
import property_photos as PP

# Files already in hashed form. `^[0-9a-f]{16}([_-][a-z]+)?\.jpg$` matches the new format
# exactly; existing folios (17 digits, or Broward alphanumerics) never satisfy it.
_ALREADY_HASHED = re.compile(r'^[0-9a-f]{16}(_[a-z]{2})?\.jpg$')

# Split '{stem}.jpg', '{stem}_sv.jpg', '{stem}_pa.jpg' -> (stem, suffix)
_PARTS = re.compile(r'^(.+?)(_sv|_pa)?\.jpg$', re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='actually rename files (default: dry run, prints what would change)')
    ap.add_argument('--limit', type=int, default=0,
                    help='cap the number of renames (for a smoke test — default: no cap)')
    a = ap.parse_args()

    if not os.path.isdir(IMGDIR):
        print('No docs/img directory here — nothing to do.')
        return 0

    files = sorted(os.listdir(IMGDIR))
    plan = []          # (old_path, new_path)
    skipped_hashed = 0
    skipped_unparseable = 0
    would_collide = []
    for name in files:
        if not name.lower().endswith('.jpg'):
            continue
        if _ALREADY_HASHED.match(name):
            skipped_hashed += 1
            continue
        m = _PARTS.match(name)
        if not m:
            skipped_unparseable += 1
            continue
        stem, suffix = m.group(1), (m.group(2) or '').lower()
        # property_photos'  _fname_hash reads the salt from imghash.key. Same function, so a
        # rebuild will look for the exact name this script writes. A missing salt file at this
        # point would generate a new one and hash accordingly — safe, but WARN the operator
        # since the other engine must copy that key.
        h = PP._fname_hash(stem)
        if not h:
            skipped_unparseable += 1
            continue
        new_name = h + suffix + '.jpg'
        if new_name == name:
            continue
        old_path = os.path.join(IMGDIR, name)
        new_path = os.path.join(IMGDIR, new_name)
        if os.path.exists(new_path):
            would_collide.append((name, new_name))
            continue
        plan.append((old_path, new_path))

    if a.limit:
        plan = plan[:a.limit]

    print('docs/img files scanned : %d' % len(files))
    print('already hashed         : %d (unchanged)' % skipped_hashed)
    print('unparseable            : %d (unchanged)' % skipped_unparseable)
    print('would collide          : %d (unchanged; see below)' % len(would_collide))
    print('renames planned        : %d' % len(plan))
    if would_collide[:5]:
        print()
        print('collision samples (target already exists on disk):')
        for a_, b_ in would_collide[:5]:
            print('  %s -> %s' % (a_, b_))

    if not plan:
        print('\nNothing to do.')
        return 0

    print()
    print('samples of planned renames:')
    for old_p, new_p in plan[:5]:
        print('  %s  ->  %s' % (os.path.basename(old_p), os.path.basename(new_p)))
    if len(plan) > 5:
        print('  ... and %d more' % (len(plan) - 5))

    if not a.apply:
        print('\nDRY RUN. Re-run with --apply to actually rename.')
        return 0

    print('\nrenaming...')
    ok = fail = 0
    for old_p, new_p in plan:
        # `git mv` preserves history as a rename. Fall back to os.rename for untracked files.
        try:
            r = subprocess.run(['git', 'mv', '--', old_p, new_p], cwd=HERE,
                               capture_output=True, text=True)
            if r.returncode == 0:
                ok += 1
                continue
            # untracked or already-mv-in-index case: rename on disk directly
            os.rename(old_p, new_p)
            ok += 1
        except Exception as e:
            print('  FAIL %s -> %s : %s' % (os.path.basename(old_p),
                                             os.path.basename(new_p), str(e)[:70]))
            fail += 1
    print('renamed: %d   failed: %d' % (ok, fail))
    print()
    print('NEXT: rebuild the board so the encrypted payload references the new names --')
    print('  python -c "import json, foreclosure_leads as F; F.make_tracker('
          'json.load(open(\'leads_final.json\',encoding=\'utf-8\')))"')
    return 0 if not fail else 1


if __name__ == '__main__':
    sys.exit(main())
