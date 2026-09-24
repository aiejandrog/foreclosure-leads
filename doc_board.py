"""doc_board — what the Miami document dossiers put on the board, and nothing more.

WHY THIS EXISTS
run_documents.py (#50) writes one dossier per case to DEALFLOW_DIR/dossiers/miami-dade/<case>.json.
Until this module, nothing read them back: a case could have its judgment fetched, OCR'd and
classified and the board row looked exactly the same. This is the one reader. make_tracker calls
`attach()` and each Miami row that has a dossier gets a small `docs` summary.

WHAT IT REFUSES TO DO
- Touch equity. A document-read amount is not verified until Alejandro's 12-case review, so it is
  carried as `amt` next to the row and never written to judg, payoff, eq or anything they feed.
  case_dossier._d already says the equity verdict rests on the recorded chain alone; this keeps it so.
- Ship text a dossier holds about people. open_gaps can name a party ("recorded against <name>"),
  so only the COUNT of gaps travels. The dossier on the machine keeps the words.
- Guess which judgment controls. Two judgment candidates stay `several` with no amount, exactly as
  case_dossier._operative_judgment reports them.
- Show a paid judgment as owed. A read satisfaction makes it `sat` and drops the amount.

SHAPE (one row, short keys because it rides in every build):
    {'n': documents fully read, 'f': documents fetched,
     'j': 'one' | 'several' | 'none' | 'sat' | 'part',
     'amt': printed judgment amount, only when j is 'one' or 'part' and one figure was printed,
     'ref': the operative document's source_ref, when there is one,
     'g': number of open gaps, 'at': YYYY-MM-DD the dossier was built,
     'p': documents read only partially (present when > 0),
     'stay': True / False / 'unclear', from the whole-case timeline (<case>-timeline.json beside
             the dossier); absent when there is no timeline or it says nothing about a stay}
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# run_documents.dossier_path slugs COUNTY='MIAMI-DADE' as-is, so the folder is upper case (and case
# matters off Windows).
COUNTY_DIR = 'MIAMI-DADE'
TIMELINE_SUFFIX = '-timeline.json'


def case_key(case):
    """'2024-009959-CA-01' and '2024 009959 ca 01' are the same case."""
    return re.sub(r'[^A-Z0-9]', '', str(case or '').upper())


def dossier_dir():
    import paths as P
    return os.path.join(P.DEALFLOW_DIR, 'dossiers', COUNTY_DIR)


def summarize(dossier, timeline=None):
    """One dossier (and its timeline, if any) -> the board summary, or None when not a dossier."""
    if not isinstance(dossier, dict) or not dossier.get('case') or 'c_documents' not in dossier:
        return None
    c = dossier.get('c_documents') or {}
    jd = c.get('judgment') or {}
    if jd.get('satisfied'):
        j = 'sat'
    elif jd.get('operative') and jd.get('certain'):
        j = 'part' if jd.get('partially_satisfied_by') else 'one'
    elif len(jd.get('candidates') or []) > 1:
        j = 'several'
    else:
        j = 'none'
    out = {'n': int(c.get('fully_read') or 0), 'f': int(c.get('fetched') or 0), 'j': j,
           'g': len(dossier.get('open_gaps') or []),
           'at': str(dossier.get('built_at') or '')[:10]}
    amt = jd.get('amount')
    if j in ('one', 'part') and isinstance(amt, (int, float)) and amt > 0:
        out['amt'] = round(float(amt), 2)
    if jd.get('operative'):
        out['ref'] = str(jd['operative'])[:60]
    partial = sum(1 for d in (c.get('documents') or [])
                  if isinstance(d, dict) and d.get('read_status') == 'partial')
    if partial:
        # A partial reading can still carry a judgment or satisfaction classification, so a zero
        # fully-read count must not read as "nothing was read".
        out['p'] = partial
    stay = _stay(timeline)
    if stay is not None:
        out['stay'] = stay
    return out


def _stay(timeline):
    """True / False / 'unclear' from the whole-case timeline, or None when it says nothing.

    #53 writes an explicit stay_in_effect from the stay history, and that wins. Without it (the
    timeline on main) the case status carries the answer: 'stayed_by_bankruptcy' is a stay, and an
    'unclear' status whose reason is about a stay stays unclear rather than becoming a yes or no."""
    if not isinstance(timeline, dict):
        return None
    explicit = timeline.get('stay_in_effect')
    if isinstance(explicit, bool):
        return explicit
    status = timeline.get('status') or {}
    if not isinstance(status, dict):
        return None
    if status.get('kind') == 'stayed_by_bankruptcy':
        return True
    if status.get('kind') == 'unclear' and 'stay' in str(status.get('reason') or '').lower():
        return 'unclear'
    return None


def _read(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load(folder=None):
    """{case_key: summary} for every readable case dossier. A missing folder is an empty map."""
    folder = folder or dossier_dir()
    out = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        if not name.endswith('.json') or name.startswith('_') or name.endswith(TIMELINE_SUFFIX):
            continue            # _nightly.json is a run summary; <case>-timeline.json rides below
        dossier = _read(os.path.join(folder, name))
        if dossier is None:
            continue
        s = summarize(dossier, _read(os.path.join(folder, name[:-5] + TIMELINE_SUFFIX)))
        if s:
            out[case_key(dossier['case'])] = s
    return out


def attach(rows, summaries):
    """Put `docs` on each row whose case has a dossier. Returns how many rows got one."""
    if not summaries:
        return 0
    n = 0
    for r in rows:
        k = case_key(r.get('case'))
        if k and k in summaries:
            r['docs'] = summaries[k]
            n += 1
    return n
