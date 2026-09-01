#!/usr/bin/env python3
"""quo_sync.py -- pull Quo (OpenPhone) calls, transcripts and AI summaries onto the leads.

WHAT THIS IS
Alejandro dials from Call Mode through the Quo app (2026-09-01: "i am going to operate off quo
now"). Quo records the call, transcribes it and writes an AI summary on the Business plan. This
module closes the loop: it finds the Quo call for every number we dialled, attaches the
transcript/summary/recording to the LEAD, and runs a deterministic coach pass over the transcript
so the next session opens knowing what happened on the last one -- including the exact sentence
that broke the language law, if one did.

WHY THE SYNC IS DRIVEN FROM OUR DIAL LOG, NOT FROM QUO
The API has no "list all recent calls". GET /v1/calls REQUIRES phoneNumberId AND participants
(exactly one E.164 number) -- verified against the live docs 2026-09-01, it is not an optional
filter. So enumeration must start from the numbers WE called. Call Mode already logs every dial
(logOutcome -> notes[case].dials[{tsu, ph4}]) and that ledger lands here as worker_notes.json.
ph4 is only the last four digits, so the full E.164 is recovered by matching against the lead's
own phone list -- the lead that logged the dial knows which numbers it holds.

WHY "LIVE COACHING" IS POST-CALL, AND HONESTLY SO
Quo's transcript exists only after the call completes; there is no mid-call stream on this plan.
True in-call coaching is Call Mode's job (the CIOC sheet on the dial screen). What this adds is
the tight loop around it: --watch polls during a calling block and prints the coach card within
~a minute of hangup, and the nightly bakes the last call + flags onto the lead card itself, so
the "coaching" is in front of him at the exact moment he redials.

Run:  python quo_sync.py                    # sync calls for every dial logged in the last 7 days
      python quo_sync.py --days 2           # narrower window
      python quo_sync.py --phone 7865550142 # one number, verbose
      python quo_sync.py --watch            # poll during a calling session, coach card per call
Setup: quo.key (gitignored) = the API key from Quo > Settings > API. One line, no quotes.
       Transcripts + AI summaries need the Business plan; everything else degrades gracefully.

quo_calls.json is CLIENT MATERIAL (homeowner conversations) -- gitignored, never committed, the
repo is PUBLIC. The board bake ships it only inside the encrypted payload.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'quo.key')
LEDGER = os.path.join(HERE, 'quo_calls.json')
BASE = 'https://api.quo.com/v1'

# ---- coach rules -------------------------------------------------------------------------------
# Deterministic, offline, and each one traces to a rule that already exists elsewhere in the repo:
# the language law (playbook), FS 501.1377 (no stop-the-sale promises), the 8/29 copy pass (no
# guarantee / thousands), and the five-minute promise unified 8/29. EN + ES because South Florida.
COACH_BANNED = [
    ('said "supervisor" - house language is SENIOR ADVISOR',
     re.compile(r'\b(my|our|the)\s+supervisors?\b|\bsupervisor\b', re.I)),
    ('promised to stop the sale - FS 501.1377 trigger. Say "ask the court for more time"',
     re.compile(r'\b(stop|stopping|halt)\s+(the\s+|your\s+)?(sale|foreclosure|auction)\b'
                r'|\b(paramos|parar|detener)\s+la\s+(venta|subasta)\b', re.I)),
    ('said "guarantee" - no promised outcomes, ever',
     re.compile(r'\bguarant|\bgarantiz', re.I)),
    ('quantified track-record claim ("thousands...") - unsupported, FDUTPA bait',
     re.compile(r'\bthousands\s+of\b|\bmiles\s+de\b', re.I)),
]
# Beats the call should hit. Absence is a flag, not a felony - surfaced as "missed", not "said".
COACH_BEATS = [
    ('never said the identity nots ("not your lender...")',
     re.compile(r'not\s+your\s+lender|no\s+soy\s+su\s+prestamista', re.I)),
    ('never made the five-minute ask',
     re.compile(r'\bfive\s+minutes\b|\b5\s+minutes\b|\bcinco\s+minutos\b', re.I)),
]
CONSENT_RE = re.compile(r'\brecord(ing|ed)?\b|\bgrabar\b|\bgrabando\b|\bgrabada\b', re.I)


def _key():
    if os.path.exists(KEY_FILE):
        k = open(KEY_FILE, encoding='utf-8').read().strip()
        if k:
            return k
    return None


def _get(key, path, params=None):
    """One authed GET. Quo auth is the bare key in Authorization -- no 'Bearer' prefix."""
    r = requests.get(BASE + path, headers={'Authorization': key}, params=params or {}, timeout=30)
    if r.status_code == 402 or r.status_code == 403:
        return {'_denied': r.status_code}          # plan-gated (transcripts below Business) or scope
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _digits(s):
    return re.sub(r'\D', '', str(s or ''))[-10:]


def _load(p, default):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


# ---- who did we dial? --------------------------------------------------------------------------
def _leads():
    """leads + skiptrace phones, same merge the other CLI tools do."""
    L = []
    for f in ('leads_final.json', 'lp_leads.json'):
        L += _load(os.path.join(HERE, f), [])
    st = _load(os.path.join(HERE, 'skiptrace_results.json'), {})
    for d in L:
        c = str(d.get('case') or d.get('cert') or '').strip()
        if not (d.get('phones') or []) and c in st:
            d['phones'] = (st.get(c) or {}).get('phones') or []
    return L


def dialed_numbers(days):
    """[(e164, case, when_epoch)] from every dial Call Mode logged in the window.

    Sources worker_notes.json AND its snapshots -- the export is manual and irregular, so the
    freshest dial may only exist in a snapshot. ph4 -> full number via the lead's own phone list;
    a lead whose ph4 matches nothing (number edited off the lead since) is reported, not dropped
    silently."""
    cutoff = time.time() - days * 86400
    by_case = {}
    for d in _leads():
        c = str(d.get('case') or d.get('cert') or '').strip()
        if c:
            by_case[c] = d
    out, unmatched = {}, 0
    files = [os.path.join(HERE, 'worker_notes.json')] + \
        sorted(glob.glob(os.path.join(HERE, 'worker_notes_snapshots', '*.json')), reverse=True)[:3]
    for f in files:
        notes = _load(f, {})
        if not isinstance(notes, dict):
            continue
        for case, n in notes.items():
            if not isinstance(n, dict):
                continue
            lead = by_case.get(str(case).strip())
            for dial in (n.get('dials') or []):
                tsu = float(dial.get('tsu') or 0) / 1000.0
                if tsu < cutoff:
                    continue
                ph4 = str(dial.get('ph4') or '')
                full = ''
                for p in ((lead or {}).get('phones') or []):
                    if _digits(p).endswith(ph4) and len(_digits(p)) == 10:
                        full = _digits(p)
                        break
                if not full:
                    unmatched += 1
                    continue
                k = (full, str(case).strip())
                if k not in out or tsu > out[k]:
                    out[k] = tsu
    if unmatched:
        print('  %d dial(s) whose last-4 match no phone on the lead any more -- cannot sync those'
              % unmatched)
    return [(n, c, t) for (n, c), t in sorted(out.items(), key=lambda x: -x[1])]


# ---- coach -------------------------------------------------------------------------------------
def _speech(transcript):
    """(full_text, ours_text, our_share) from a Quo transcript, defensively.

    Dialogue segments carry userId when a workspace user is speaking; the homeowner's segments
    carry only the external identifier. When the shape is unexpected, coach on the whole text and
    report share as None rather than inventing a number."""
    try:
        segs = (transcript or {}).get('data', {}).get('dialogue') or []
        full, ours = [], []
        for s in segs:
            c = str(s.get('content') or '').strip()
            if not c:
                continue
            full.append(c)
            if s.get('userId'):
                ours.append(c)
        ft, ot = ' '.join(full), ' '.join(ours)
        share = (len(ot.split()) / max(1, len(ft.split()))) if ours else None
        return ft, (ot or ft), share
    except Exception:
        return '', '', None


def coach(transcript, dur, record_on):
    """-> list of plain-sentence flags. Empty list = clean call."""
    ft, ours, share = _speech(transcript)
    if not ft:
        return ['no transcript (plan below Business, or still processing)']
    flags = []
    for label, rx in COACH_BANNED:
        m = rx.search(ours)
        if m:
            flags.append(label)
    # Beats only make sense on a call long enough to have them. A 20-second no-answer that
    # "never made the five-minute ask" is noise, and noisy coaching gets ignored.
    if (dur or 0) >= 45:
        for label, rx in COACH_BEATS:
            if not rx.search(ours):
                flags.append(label)
    if record_on and (dur or 0) >= 30 and not CONSENT_RE.search(ft):
        flags.append('RECORDING ON but no consent heard - FS 934.03 is all-party consent, a felony statute')
    if share is not None and share > 0.75 and (dur or 0) >= 60:
        flags.append('you spoke %d%% of the call - the script says ask, then listen' % round(share * 100))
    return flags


# ---- sync --------------------------------------------------------------------------------------
def sync(days=7, phones=None, watch=False, verbose=False):
    key = _key()
    if not key:
        print('quo_sync: no quo.key. Create it: Quo > Settings > API > generate key, paste the')
        print('one line into quo.key in this folder (gitignored). Nothing synced.')
        return 0

    pn = _get(key, '/phone-numbers')
    if not pn or '_denied' in (pn if isinstance(pn, dict) else {}):
        print('quo_sync: /phone-numbers failed -- key invalid or lacks scope. Nothing synced.')
        return 1
    pn_ids = [p.get('id') for p in (pn.get('data') or []) if p.get('id')]
    if not pn_ids:
        print('quo_sync: the workspace has no phone numbers yet (port still pending?).')
        return 0

    try:
        import entity
        record_on = bool(entity.sender().get('quo_record'))
    except Exception:
        record_on = False

    ledger = _load(LEDGER, {'calls': {}, 'lastSync': 0})
    targets = ([(p, None, time.time()) for p in ([_digits(x) for x in phones] if phones else [])]
               or dialed_numbers(days))
    if not targets:
        print('quo_sync: no dials found in the last %d day(s) (worker_notes not exported yet?).' % days)
        return 0
    print('quo_sync: %d number(s) to check against %d Quo line(s)' % (len(targets), len(pn_ids)))

    def one_pass():
        new = 0
        for e164, case, since_ts in targets[:120]:      # hard bound; a session is ~50-100 dials
            after = datetime.datetime.utcfromtimestamp(max(0, since_ts - 3600)) \
                                     .strftime('%Y-%m-%dT%H:%M:%SZ')
            for pid in pn_ids:
                try:
                    res = _get(key, '/calls', {'phoneNumberId': pid, 'participants': '+1' + e164,
                                               'maxResults': 10, 'createdAfter': after})
                except Exception as ex:
                    print('  %s: %s' % (e164, str(ex)[:70]))
                    continue
                for call in ((res or {}).get('data') or []):
                    cid = call.get('id')
                    if not cid or cid in ledger['calls']:
                        continue
                    if call.get('status') not in ('completed', 'answered', 'no-answer', 'missed'):
                        continue
                    tr = _get(key, '/call-transcripts/%s' % cid)
                    sm = _get(key, '/call-summaries/%s' % cid)
                    rec_ = _get(key, '/call-recordings/%s' % cid)
                    denied = isinstance(tr, dict) and tr.get('_denied')
                    dur = call.get('duration') or 0
                    flags = [] if denied else coach(tr, dur, record_on)
                    summary = []
                    if isinstance(sm, dict) and not sm.get('_denied'):
                        summary = (sm.get('data') or {}).get('summary') or []
                        if isinstance(summary, str):
                            summary = [summary]
                    rec_url = ''
                    if isinstance(rec_, dict) and not rec_.get('_denied'):
                        rd = rec_.get('data')
                        if isinstance(rd, list) and rd:
                            rec_url = rd[0].get('url') or ''
                    ledger['calls'][cid] = {
                        'case': case, 'phone': e164, 'at': call.get('createdAt'),
                        'dir': call.get('direction'), 'status': call.get('status'), 'dur': dur,
                        'summary': summary[:4], 'flags': flags, 'rec': rec_url,
                        'transcript_denied': bool(denied),
                    }
                    new += 1
                    who = case or e164
                    print('\n  CALL %s  %s  %ss  %s' % (call.get('createdAt', '')[:16], who, dur,
                                                        call.get('status')))
                    for s in summary[:3]:
                        print('     - %s' % s)
                    if flags:
                        for f in flags:
                            print('     ! %s' % f)
                    elif not denied and dur >= 45:
                        print('     OK clean call - language law held')
            time.sleep(0.15)
        return new

    total_new = one_pass()
    if watch:
        print('\nwatch mode: polling every 45s. Ctrl-C to stop.')
        try:
            while True:
                time.sleep(45)
                n = one_pass()
                if n:
                    json.dump(ledger, open(LEDGER, 'w', encoding='utf-8'), indent=0)
                    total_new += n
        except KeyboardInterrupt:
            pass

    ledger['lastSync'] = time.time()
    json.dump(ledger, open(LEDGER, 'w', encoding='utf-8'), indent=0)
    print('\nquo_sync: %d new call(s) -> quo_calls.json (%d total). Rebuild the board to bake them '
          'onto the cards.' % (total_new, len(ledger['calls'])))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--phone', action='append')
    ap.add_argument('--case')
    ap.add_argument('--watch', action='store_true')
    a = ap.parse_args()
    phones = a.phone or []
    if a.case:
        for d in _leads():
            if str(d.get('case') or '').strip() == a.case.strip():
                phones += [p for p in (d.get('phones') or [])]
    return sync(days=a.days, phones=phones or None, watch=a.watch)


if __name__ == '__main__':
    sys.exit(main())
