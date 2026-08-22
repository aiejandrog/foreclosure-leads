#!/usr/bin/env bash
# Chunked force-push of the rewritten (PII-purged) history.
# A history rewrite makes EVERY object new to the remote, so main goes up as one ~600MB pack and
# GitHub times it out with HTTP 408. Pushing intermediate commits first splits that into pieces
# small enough to survive, and each chunk that lands is progress the next one builds on.
# Safe to re-run: chunks already on the remote become no-ops.
set -u
cd "$(dirname "$0")"
CHUNKS=(
  c76b65a25b393805c494ade661aff0c1cf3d3722
  988e9d9b4f9d8e3a5a6a9743cd32dfdaf7f85927
  5e60d96dd422c1182f96e2fcb922a9597bd62a36
  51fd15f0ce8c5b1704e50504f8c79fda2e71362f
  4fe44fa2da61066867d02467cfd5582bfb256b89
)
i=0
for sha in "${CHUNKS[@]}"; do
  i=$((i+1))
  echo ""
  echo "=== chunk $i/5 -> ${sha:0:12} ==="
  for try in 1 2 3; do
    if git push --force origin "$sha:refs/heads/main"; then
      echo "chunk $i OK"; break
    fi
    echo "chunk $i attempt $try failed; retrying in 15s..."
    sleep 15
    [ "$try" = 3 ] && { echo "CHUNK $i FAILED 3x - stopping."; exit 1; }
  done
done
echo ""
echo "=== remote main is now: ==="
git ls-remote origin main
