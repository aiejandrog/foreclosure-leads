"""doc_board — what the Miami document dossiers put on the board, and nothing more.

WHY THIS EXISTS
run_documents.py (#50) writes one dossier per case to DEALFLOW_DIR/dossiers/MIAMI-DADE/<case>.json.
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
- Show a paid judgment as owed. A satisfaction read from the court's copy makes it `sat` and drops
  the amount.

SHAPE (one row, short keys because it rides in every build):
    {'n': documents fully read, 'f': documents fetched,
     'j': 'one' | 'several' | 'none' | 'sat' | 'part',
     'src': 'court' when the judgment document read is the court's own copy (a 'court:' ref),
            'rec' when it is a recorded copy from Official Records,
     'amt': printed judgment amount, only when j is 'one' or 'part', one figure was printed AND
            src is 'court',
     'ctl': True when the refreshed timeline names this same docket entry as the controlling
            judgment, False when it names a different one; absent when there is no refreshed
            timeline (no 'judgments' block) or it could not name one,
     'ref': the operative document's source_ref, when there is one,
     'g': number of open gaps, 'at': YYYY-MM-DD the dossier was built,
     'p': documents read only partially (present when > 0)}

WHAT THE 12-CASE CHECK ALLOWS (verify-12, 2026-09-24, against the clerk's own records)
Only two things held up: a judgment amount read from the COURT's copy, and the controlling judgment
named by a REFRESHED timeline. So an amount from a recorded (Official Records) copy is not shipped,
a satisfaction counts only when the court's copy of it was read (the OR search pulled another
person's 2011 records into 2025-018660 and called it "no outstanding debt"), and the timeline's
stay reading is not shipped at all: it was right in 2 of 3 stay cases and misses bankruptcy
orders filed as "Notice of Filing:". Stays on the board come from the section-362 flag only.
The lien list, surviving debt and CLEAR also failed that check; none of them is read here.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# run_documents.dossier_path slugs COUNTY='MIAMI-DADE' as-is, so the folder is upper case (and case
# matters off Windows).
COUNTY_DIR = 'MIAMI-DADE'
# document_collectors names a court-docket image 'court:<entry>:<document>'; an Official Records copy
# is 'official_records/...' or 'recorded:...'.
COURT_PREFIX = 'court:'
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
    op = str(jd.get('operative') or '')
    court = op.startswith(COURT_PREFIX)
    amt = jd.get('amount')
    if jd.get('satisfied'):
        if all(str(r).startswith(COURT_PREFIX) for r in (jd.get('satisfied_by') or [])):
            j = 'sat'
        else:
            # The satisfaction came from Official Records, which verify-12 caught matching other
            # people's instruments. The court's judgment stands; its printed figure is the claim.
            j, amt = 'one', jd.get('printed_amount')
    elif op and jd.get('certain'):
        j = 'part' if jd.get('partially_satisfied_by') else 'one'
    elif len(jd.get('candidates') or []) > 1:
        j = 'several'
    else:
        j = 'none'
    out = {'n': int(c.get('fully_read') or 0), 'f': int(c.get('fetched') or 0), 'j': j,
           'g': len(dossier.get('open_gaps') or []),
           'at': str(dossier.get('built_at') or '')[:10]}
    if op:
        out['ref'] = op[:60]
        out['src'] = 'court' if court else 'rec'
    if j in ('one', 'part') and court and isinstance(amt, (int, float)) and amt > 0:
        out['amt'] = round(float(amt), 2)
    ctl = _controlling(timeline, op) if court and j in ('one', 'part', 'sat') else None
    if ctl is not None:
        out['ctl'] = ctl
    partial = sum(1 for d in (c.get('documents') or [])
                  if isinstance(d, dict) and d.get('read_status') == 'partial')
    if partial:
        # A partial reading can still carry a judgment or satisfaction classification, so a zero
        # fully-read count must not read as "nothing was read".
        out['p'] = partial
    return out


def _controlling(timeline, ref):
    """Does the refreshed timeline name this court document's docket entry as controlling?

    True / False, or None when the timeline has no 'judgments' block (the old format, which
    verify-12 found stale in 7 of 12 cases) or could not name one. A court ref is
    'court:<entry>:<document>', and the timeline names entries."""
    if not isinstance(timeline, dict) or not isinstance(timeline.get('judgments'), dict):
        return None
    entry = timeline['judgments'].get('controlling_entry')
    if not entry:
        return None
    parts = ref.split(':')
    return len(parts) > 1 and parts[1] == str(entry)


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
