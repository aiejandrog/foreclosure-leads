"""Carry property photos forward across refreshes.

Every scrape rewrites leads_final.json / <county>_leads.json from scratch, producing fresh
lead objects with NO photo fields. Without this, the photo pass (property_photos.py) has to
re-fetch ALL ~640 leads on every run — slow, API-costly, and (worst) if the refresh is killed
before that step runs (e.g. a hung Broward scrape times out the scheduled task, as on
2026-07-18), every lead goes live showing a placeholder house icon.

This preserves photos for any property that was already photographed on the previous run,
keyed by folio (falling back to case #), so that:
  * returning leads show their photos IMMEDIATELY — even if the photo pass never runs — because
    the carried `photos` list includes the persistent local img/ paths (and the absolute aerial
    `aurl` fallback), and
  * property_photos.py then only has real work to do for genuinely NEW leads.

Fail-soft by construction: a missing or corrupt previous snapshot simply carries nothing.
"""
import os
import re
import json

# The photo-related fields property_photos.py writes onto a lead.
_PHOTO_TRIPLET = ('photos', 'zlisting', 'photo_kind')  # move together: they describe one photo source


def _pkey(r):
    """Stable per-property key. Mirrors property_photos._folio: folio digits, else case alnum."""
    return (re.sub(r'\D', '', str(r.get('folio') or r.get('Folio') or ''))
            or re.sub(r'[^a-z0-9]', '', str(r.get('case') or r.get('Case #') or '').lower()))


def carry_photos(new_leads, prev_path, prefer=()):
    """Fill photo fields on ``new_leads`` from the previous JSON snapshot at ``prev_path``,
    matched by :func:`_pkey`. Only fills leads that lack photos, only from prev leads that have
    them. ``aurl`` (the location-derived aerial fallback) is carried whenever it is missing.
    ``prefer``: optional tuple of photo_kind values (e.g. ('zillow',)) that WIN even when the
    lead already has photos from a lesser source — the committed Zillow seed rides in this way,
    because listing photos outrank a Street View / aerial fallback every time.
    Returns the number of leads that received a photo set. Never raises."""
    if not prev_path or not os.path.exists(prev_path):
        return 0
    try:
        prev = json.load(open(prev_path, encoding='utf-8'))
    except Exception:
        return 0
    if not isinstance(prev, list):
        return 0

    idx = {}
    for r in prev:
        if not isinstance(r, dict):
            continue
        k = _pkey(r)
        if k and (r.get('photos') or r.get('aurl') or r.get('zstatus')):
            idx.setdefault(k, r)  # first (highest-score) wins on the off-chance of a key collision

    carried = 0
    for r in new_leads:
        src = idx.get(_pkey(r))
        if not src:
            continue
        preferred = src.get('photo_kind') in prefer and src.get('photos')
        if (not r.get('photos') and src.get('photos')) or preferred:
            for f in _PHOTO_TRIPLET:
                r[f] = src.get(f, '' if f != 'photos' else [])
            carried += 1
        if not r.get('aurl') and src.get('aurl'):
            r['aurl'] = src['aurl']
        # Listing status rides along too: a fresh scrape wipes zstatus/zprice/zdoz just like
        # photos, and listing_status.py's 7-day TTL means most leads aren't re-fetched on any
        # given day. Carrying last-known keeps the chip on the site between re-checks.
        if not r.get('zstatus') and src.get('zstatus'):
            r['zstatus'] = src['zstatus']
            r['zprice'] = src.get('zprice', 0)
            r['zdoz'] = src.get('zdoz', 0)
    return carried
