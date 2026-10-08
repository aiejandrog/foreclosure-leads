"""Find PACER clears that may have come from a backwards owner name, and hold them.

ce154cc (2026-10-08) let pacer_stay.load_leads() fall back to oname / paOwner when a lead's owners
field was empty, and read that fallback last-first. oname is often 'First Last', so 'Mary Roe' was
searched as first ROE last MARY, found nothing, and could record a clear that releases a Broward or
Palm Beach lead. pacer_stay now reads that fallback both ways ('either').

This lists every production 'clear' in pacer_stay_cache.json whose lead carries any oname / paOwner
fallback owner (or an owners value owner_repair.py copied from one) with a person written without a
comma, plus every clear recorded since ce154cc by code without the fix (the lead files may have
changed since), i.e. every clear that could have been recorded through the backwards reading. With --apply it turns each one into 'unverifiable' (held), keeps the
old verdict under 'quarantined_from', and writes a backup first. A fresh 'unverifiable' is not
re-searched automatically inside the max age, so this spends nothing. Re-search one by hand with:

    python pacer_stay.py --case <case>

Output is case numbers, counties and dates only. No owner name is printed or written.

    python pacer_fallback_audit.py            # list only
    python pacer_fallback_audit.py --apply    # hold them
"""
import datetime as dt
import json
import os
import re
import shutil
import sys

import pacer_stay as PS

REASON = ('held 2026-10-08: this clear may have searched the owner name backwards (oname/paOwner '
          'fallback, ce154cc); re-search with pacer_stay.py --case')


def _people(raw):
    """The single-person parts of one owner string ('A; B', 'A & B', 'A AND B')."""
    return [p for p in re.split(r'\s*[;&]\s*|\s+AND\s+', str(raw).upper()) if p.strip()]


# ce154cc (the one-way fallback) was committed 2026-10-08 18:20:58 -04:00. No lead could get a clear
# from a fallback name before it: an empty owners field was 'no owner name' (unverifiable).
CUTOFF = dt.datetime(2026, 10, 8, 22, 20, 58, tzinfo=dt.timezone.utc).timestamp()


def _since_bad_loader(ent):
    """Recorded on or after ce154cc by code that does not read fallback names both ways. The lead
    files may have changed since (owners filled in by pa_values / stub_resolve / a re-scrape), so
    today's files cannot clear such an entry; only its own name-reading stamp can."""
    try:
        t = float(ent.get('t') or 0)
    except (TypeError, ValueError):
        t = 0.0
    if not t:
        try:
            t = dt.datetime.fromisoformat(str(ent.get('q') or '')).timestamp()
        except ValueError:
            return True                       # no readable time: cannot rule it out
    return t >= CUTOFF and ent.get('names') != PS.NAME_READING


def suspects(cache, leads):
    """Clear entries that may rest on a one-way fallback name: recorded since ce154cc without the
    fixed reading's stamp, or for a lead with ANY fallback-sourced owner part written without a
    comma (that part was searched one way only before the fix, and every owner must clear)."""
    out = []
    for key, ent in sorted(cache.items()):
        if not isinstance(ent, dict) or ent.get('verdict') != 'clear':
            continue
        ld = leads.get(key)
        if _since_bad_loader(ent):
            out.append((key, ld or {'case': key, 'county': ent.get('county') or ''}, ent))
            continue
        if not ld or not ld.get('owners'):
            continue
        if any(order == 'either' and any(',' not in part for part in _people(raw))
               for raw, order in ld['owners']):
            out.append((key, ld, ent))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    apply = '--apply' in argv
    here = PS.HERE
    for a in argv:
        if a.startswith('--here='):
            here = a.split('=', 1)[1]
    cpath = PS.cache_path(here, 'prod')
    leads, _ = PS.load_leads(here)
    cache, prob = PS.load_cache_strict(cpath)
    if prob:
        print('REFUSED: %s (nothing changed)' % prob)
        return 2
    found = suspects(cache, leads)
    print('pacer cache: %d entries, %d clear; leads loaded: %d'
          % (len(cache), sum(1 for e in cache.values() if isinstance(e, dict) and e.get('verdict') == 'clear'),
             len(leads)))
    print('clears that may have used a backwards owner name: %d' % len(found))
    for key, ld, ent in found:
        print('  %-24s %-11s clear recorded %s' % (ld.get('case') or key, ld.get('county') or '?',
                                                   str(ent.get('q') or '?')[:16]))
    if not found:
        print('nothing to hold.')
        return 0
    if not apply:
        print('list only. Run again with --apply to hold these (no spend).')
        return 0
    import paid_reads
    with paid_reads._FileLock(cpath) as lk:
        if not lk.held:
            print('REFUSED: %s is locked by another writer (nothing changed)' % os.path.basename(cpath))
            return 2
        cache, prob = PS.load_cache_strict(cpath)
        if prob:
            print('REFUSED: %s (nothing changed)' % prob)
            return 2
        found = suspects(cache, leads)
        backup = cpath + '.bak-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2(cpath, backup)
        for key, _, ent in found:
            ent['quarantined_from'] = ent.get('verdict')
            ent['verdict'] = 'unverifiable'
            ent['a'] = False
            ent['why'] = REASON
        PS.save_cache(cpath, cache)
    print('held %d lead(s). backup: %s' % (len(found), os.path.basename(backup)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
