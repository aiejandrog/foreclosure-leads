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
    """One authed GET. Quo auth is the bare key in Authorization -- no 'Bearer' prefix.

    429 comes back as `{'_retry': 429}` so a caller can back off. It is not a page of data."""
    r = requests.get(BASE + path, headers={'Authorization': key}, params=params or {}, timeout=30)
    if r.status_code == 402 or r.status_code == 403:
        return {'_denied': r.status_code}          # plan-gated (transcripts below Business) or scope
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        return {'_retry': 429}
    r.raise_for_status()
    return r.json()


# Four tries, then the 429 stands. The pause is its own function so a fixture can skip the wait.
RETRY_LIMIT = 4
RETRY_BASE_S = 1.0


def _pause(seconds):
    time.sleep(seconds)


def _get_retry(key, path, params=None):
    """`_get`, retried on 429. The sentinel is returned only after the budget is spent."""
    res = None
    for attempt in range(RETRY_LIMIT):
        res = _get(key, path, params)
        if not (isinstance(res, dict) and res.get('_retry') == 429):
            return res
        if attempt + 1 >= RETRY_LIMIT:
            return res
        _pause(RETRY_BASE_S * (2 ** attempt))
    return res


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
        # UNION, not fallback. The board's card can show skiptrace numbers alongside the lead's own,
        # and the dial log only keeps last-4 -- resolving it needs every number the card could have
        # offered. Fallback-only left 111 of 186 logged dials unresolvable on the first live run.
        have = {re.sub(r'\D', '', str(p))[-10:] for p in (d.get('phones') or [])}
        extra = [p for p in ((st.get(c) or {}).get('phones') or [])
                 if re.sub(r'\D', '', str(p))[-10:] not in have]
        d['phones'] = (d.get('phones') or []) + extra
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
        # The phone's export wraps the ledger in an envelope: {_dealflow_notes, exported, device,
        # notes:{case: {...}}, workerLog, sentArchive}. The per-case dials live under 'notes'.
        # First version iterated the ENVELOPE and found zero dials in a file holding 238 cases
        # with them -- unwrap when the marker key is present, accept the bare shape otherwise.
        if '_dealflow_notes' in notes and isinstance(notes.get('notes'), dict):
            notes = notes['notes']
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

    pn = _get_retry(key, '/phone-numbers')
    if not isinstance(pn, dict) or pn.get('_denied') or pn.get('_retry'):
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
                    res = _get_retry(key, '/calls', {'phoneNumberId': pid, 'participants': '+1' + e164,
                                                     'maxResults': 10, 'createdAfter': after})
                except Exception as ex:
                    print('  %s: %s' % (e164, str(ex)[:70]))
                    continue
                if isinstance(res, dict) and res.get('_retry'):
                    print('  %s: 429' % e164)
                    continue
                for call in ((res or {}).get('data') or []):
                    cid = call.get('id')
                    if not cid or cid in ledger['calls']:
                        continue
                    if call.get('status') not in ('completed', 'answered', 'no-answer', 'missed'):
                        continue
                    tr = _get_retry(key, '/call-transcripts/%s' % cid)
                    sm = _get_retry(key, '/call-summaries/%s' % cid)
                    rec_ = _get_retry(key, '/call-recordings/%s' % cid)
                    denied = isinstance(tr, dict) and (tr.get('_denied') or tr.get('_retry'))
                    dur = call.get('duration') or 0
                    flags = [] if denied else coach(tr, dur, record_on)
                    summary = []
                    if isinstance(sm, dict) and not sm.get('_denied') and not sm.get('_retry'):
                        summary = (sm.get('data') or {}).get('summary') or []
                        if isinstance(summary, str):
                            summary = [summary]
                    rec_url = ''
                    if isinstance(rec_, dict) and not rec_.get('_denied') and not rec_.get('_retry'):
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


# ---- inbound texts: STOP handling ---------------------------------------------------------------
# WHY (2026-09-25 audit): every outbound text is a 1:1 handset SMS from the Quo line, so no
# carrier/10DLC STOP handling exists for it, and until now NOTHING read inbound texts: an owner who
# replied STOP to a text was honoured only if a human noticed and tapped DNC. This pass pulls
# inbound messages for every number we dialled or texted, runs each through the SAME detector the
# email path uses (replies.is_sms_stop: carrier keywords + is_stop_text), and ledgers a hit as a
# '#digits' person key -- the shape call_rows()/optPhones() already read back, so the number
# disappears from the phone and the board on the next build.
#
# ENDPOINT NOTE: GET /v1/conversations and GET /v1/messages were checked against the live
# account on 2026-09-26. Conversations come back as {data, nextPageToken, totalItems}; each
# item has participants, phoneNumberId, lastActivityAt. A message body is in `text` and an
# inbound direction is `incoming`. /calls query names were verified against the live docs on
# 2026-09-01. This tree does not call the API; fixtures stub `_get`.
MESSAGES_PATH = '/messages'
CONVERSATIONS_PATH = '/conversations'
# Written after every inbound scan, success or failure. text_hold() is what Call Mode and the
# send bridge read. A missing or unreadable file is a hold: we have not proved we saw today's
# STOP texts. Email does not read this file.
INBOUND_STATUS = os.path.join(HERE, 'quo_inbound_status.json')
# The nightly refresh runs this around 05:30. 36h covers that cycle and still goes stale if the
# next night's scan never lands. A failed read holds immediately, whatever the age.
INBOUND_MAX_AGE_H = 36
# Who to ask about is NOT the message window. `days` is how far back each thread is read.
# The number set is every Quo participant whose conversation was active in this window,
# unioned with dials and texts from the same window. The first live check had 21
# conversations and none of them were in the dial/text set; one of those threads held a STOP.
INBOUND_NUMBER_LOOKBACK_DAYS = 60
# text_sent.json is the bridge SMS ledger. mail_sent.json can carry the same text rows.
# A missing file is an empty list. A file that exists and will not parse is an error:
# those numbers are skipped and texting is held.
TEXT_SENT = os.path.join(HERE, 'text_sent.json')
MAIL_SENT = os.path.join(HERE, 'mail_sent.json')
# OpenPhone's listing returns nextPageToken and takes pageToken. Ten pages is the cap.
# Hitting it with a token still outstanding is a partial read, which holds texting.
MESSAGE_PAGE_CAP = 10
# Live /conversations defaults to about 10 per page, so the page cap is ~100 threads and
# will eventually stick texting on hold. Ask for 100. The list is newest-activity first;
# a page that is entirely older than the lookback ends the read.
CONV_MAX_RESULTS = 100
# Overlap so a STOP that arrived just before the last ok scan is not missed when the
# next run's --days window starts later.
SCAN_OVERLAP_H = 12


def _write_inbound_status(ok, why='', checked=0, stops=0, errors=0, truncated=False,
                          pages=0, conversations=0, window=0):
    """Record the scan. Counts only — never a phone number or a message body (the repo is public
    and this file sits next to the code). `window` is how many days createdAfter reached back."""
    rec = {
        'ok': bool(ok),
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'checked': int(checked), 'stops': int(stops), 'errors': int(errors),
        'truncated': bool(truncated),
        'pages': int(pages), 'conversations': int(conversations),
        'window': int(window),
        'why': str(why or '')[:240],
    }
    try:
        tmp = INBOUND_STATUS + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(rec, fh)
        os.replace(tmp, INBOUND_STATUS)
    except Exception as e:
        print('!! could not record inbound-scan status (%s) — texting will hold' % str(e)[:80])
    return rec


def text_hold(path=None, now=None, max_age_h=None):
    """(held, why). Fail closed: missing, unreadable, a failed scan, or a scan older than max_age_h.

    This holds TEXTING only. The 07:15 opt-out sync gate is what holds email, and this function
    does not touch it."""
    path = path or INBOUND_STATUS
    max_age_h = INBOUND_MAX_AGE_H if max_age_h is None else max_age_h
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:
        return True, ('HOLD texting — the Quo inbound STOP scan has no readable status. '
                      'Run python quo_sync.py. Email is not held by this.')
    if isinstance(d, dict) and d.get('truncated'):
        return True, ('HOLD texting — the Quo inbound STOP scan stopped at the page cap, so a '
                      'later STOP may be unread. Texting stays off until a complete scan. '
                      'Email is not held by this.')
    if not isinstance(d, dict) or not d.get('ok'):
        why = d.get('why') if isinstance(d, dict) else ''
        return True, ('HOLD texting — the last Quo inbound STOP scan failed%s. '
                      'Texting stays off until a clean scan. Email is not held by this.'
                      % ((' (%s)' % why) if why else ''))
    try:
        when = datetime.datetime.fromisoformat(str(d.get('ts') or '').replace('Z', '+00:00'))
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return True, ('HOLD texting — the Quo inbound STOP scan status has no timestamp. '
                      'Email is not held by this.')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    age_h = (now - when).total_seconds() / 3600.0
    if age_h > float(max_age_h):
        return True, ('HOLD texting — the Quo inbound STOP scan is %.0fh old (max %dh). '
                      'Texting stays off until a fresh scan. Email is not held by this.'
                      % (age_h, max_age_h))
    return False, ''


def _inbound(m):
    d = str(m.get('direction') or '').lower()
    return d in ('incoming', 'inbound', 'received')


def _e164(raw):
    """'+1' and ten digits, or '' when the value is not a NANP number."""
    d = _digits(raw)
    if len(d) == 11 and d.startswith('1'):
        d = d[1:]
    if len(d) == 10:
        return '+1' + d
    return ''


def _response_error(res):
    """'' when `res` is a listing object. Otherwise a short kind: no body, no key, no number.

    `_get` maps 402/403 to `{'_denied': code}`, 404 to None, and an exhausted 429 to
    `{'_retry': 429}`. Any of those used to be readable as an empty page, which released the hold."""
    if res is None:
        return '404'
    if isinstance(res, dict) and res.get('_denied'):
        code = str(res.get('_denied') or '')
        return 'denied %s' % code if code.isdigit() else 'denied'
    if isinstance(res, dict) and res.get('_retry') == 429:
        return '429'
    if not isinstance(res, dict) or not isinstance(res.get('data'), list):
        return 'malformed'
    return ''


def _page_token(res):
    """Next page, if the listing says there is one. Names follow the OpenPhone v1 shape and
    have not been confirmed against a live key."""
    if not isinstance(res, dict):
        return ''
    for key in ('nextPageToken', 'next_page_token'):
        token = res.get(key)
        if token:
            return str(token)
    meta = res.get('meta')
    if isinstance(meta, dict):
        token = meta.get('nextPageToken') or meta.get('next_page_token') or ''
        if token:
            return str(token)
    return ''


def _list_file_problem(path, label):
    """'' when `path` is missing or a JSON list. '<label> unreadable' when it exists and will
    not parse, or parsed as something other than a list."""
    if not path or not os.path.exists(path):
        return ''
    try:
        with open(path, encoding='utf-8') as fh:
            got = json.load(fh)
    except Exception:
        return '%s unreadable' % label
    if not isinstance(got, list):
        return '%s unreadable' % label
    return ''


def _text_sent_problem():
    """'' when text_sent.json is missing or a list. Otherwise an error the caller holds on."""
    return _list_file_problem(TEXT_SENT, 'text_sent')


def _mail_sent_problem():
    """Same rule as text_sent.json. A torn mail_sent.json used to be skipped with no hold."""
    return _list_file_problem(MAIL_SENT, 'mail_sent')


def _inbound_text_rows():
    """Bridge text rows. text_sent.json is the SMS ledger; mail_sent.json can carry the same
    shape. A missing file is an empty list. An unreadable file contributes no rows; the caller
    counts that and holds texting."""
    rows = []
    if not _text_sent_problem():
        got = _load(TEXT_SENT, [])
        if isinstance(got, list):
            rows.extend(x for x in got if isinstance(x, dict))
    if not _mail_sent_problem():
        got = _load(MAIL_SENT, [])
        if isinstance(got, list):
            rows.extend(x for x in got if isinstance(x, dict))
    return rows


def _parse_when(stamp):
    """Aware UTC timestamp, or None."""
    if stamp is None or not str(stamp).strip():
        return None
    try:
        when = datetime.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except Exception:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when


def _message_window(days, path=None, now=None):
    """(createdAfter iso, window days).

    The earlier of (now − days) and (last ok scan − 12h), and never further back than 60 days.
    No ok scan, or a status file that will not parse, uses the 60-day lookback. `window` is
    that span in whole days, a count, not a timestamp."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cap = now - datetime.timedelta(days=INBOUND_NUMBER_LOOKBACK_DAYS)
    try:
        asked = int(days)
    except (TypeError, ValueError):
        asked = 0
    if asked < 0:
        asked = 0
    requested = now - datetime.timedelta(days=asked)
    since = cap
    path = INBOUND_STATUS if path is None else path
    try:
        with open(path, encoding='utf-8') as fh:
            prev = json.load(fh)
    except Exception:
        prev = None
    if isinstance(prev, dict) and prev.get('ok') is True:
        when = _parse_when(prev.get('ts'))
        if when is not None:
            since = min(requested, when - datetime.timedelta(hours=SCAN_OVERLAP_H))
            if since < cap:
                since = cap
    span = (now - since).total_seconds() / 86400.0
    window = int(span)
    if span - window > 1e-6:
        window += 1
    if window > INBOUND_NUMBER_LOOKBACK_DAYS:
        window = INBOUND_NUMBER_LOOKBACK_DAYS
    if window < 0:
        window = 0
    return since.isoformat(), window


def _in_lookback(stamp):
    """True when lastActivityAt is inside the 60-day window.

    No timestamp, or one that will not parse, is included: a STOP on a thread we cannot date
    is the miss this scan exists to close. A timestamp we can read that is older than the
    window is left out."""
    if stamp is None or not str(stamp).strip():
        return True
    try:
        when = datetime.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except Exception:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - when) <= datetime.timedelta(days=INBOUND_NUMBER_LOOKBACK_DAYS)


def _participant_raw(value):
    """Phone-like values on one participant. Live items use a list of E.164 strings; a dict
    with a phone field is accepted so a shape change does not drop the thread."""
    if isinstance(value, dict):
        for key in ('phoneNumber', 'phone', 'e164', 'number', 'value'):
            if value.get(key):
                return [value.get(key)]
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_participant_raw(item))
        return out
    if value:
        return [value]
    return []


def _list_conversations(key):
    """(numbers, pages, scanned, error, truncated).

    `numbers` is every participant on a thread active in the lookback. `scanned` counts
    conversation items examined, including ones outside the window. A failed or truncated
    read is an error the caller must hold on; numbers already collected are still returned
    so a STOP on an earlier page is not dropped."""
    found = set()
    pages = 0
    scanned = 0
    token = ''
    truncated = False
    while True:
        if pages >= MESSAGE_PAGE_CAP:
            if token:
                truncated = True
            break
        params = {'maxResults': CONV_MAX_RESULTS}
        if token:
            params['pageToken'] = token
        try:
            res = _get_retry(key, CONVERSATIONS_PATH, params or None)
        except json.JSONDecodeError:
            return found, pages, scanned, 'conversations malformed', truncated
        except Exception as e:
            return found, pages, scanned, 'conversations %s' % type(e).__name__, truncated
        err = _response_error(res)
        if err:
            return found, pages, scanned, 'conversations %s' % err, truncated
        pages += 1
        data = []
        for item in res.get('data') or []:
            if not isinstance(item, dict):
                continue
            data.append(item)
            scanned += 1
            if not _in_lookback(item.get('lastActivityAt')):
                continue
            for raw in _participant_raw(item.get('participants')):
                n = _e164(raw)
                if n:
                    found.add(n)
        token = _page_token(res)
        if not token:
            break
        # Newest activity first. Once a whole page is older than the lookback, later pages
        # are older too. An undated thread is not "older", so that page does not end the read.
        if data and all(not _in_lookback(item.get('lastActivityAt')) for item in data):
            break
    return found, pages, scanned, '', truncated


def _numbers_to_scan(phones, extra=()):
    """E.164 numbers. Quo conversation participants (`extra`) are always included.

    An explicit `phones=` list is that list plus `extra`. Otherwise 60 days of dials
    (tuples from dialed_numbers) plus text touches in the same window — not the message window."""
    found = set()

    def add(raw):
        n = _e164(raw)
        if n:
            found.add(n)

    for raw in extra or ():
        add(raw)
    if phones:
        for p in phones:
            add(p)
        return found
    for row in dialed_numbers(INBOUND_NUMBER_LOOKBACK_DAYS) or []:
        # dialed_numbers returns (number, case, ts). It is not a dict.
        if isinstance(row, (list, tuple)) and row:
            add(row[0])
        elif isinstance(row, str):
            add(row)
    cutoff = (datetime.date.today() - datetime.timedelta(days=INBOUND_NUMBER_LOOKBACK_DAYS)).isoformat()
    for row in _inbound_text_rows():
        if row.get('ch') != 'text' or not row.get('to'):
            continue
        stamp = str(row.get('d') or row.get('ts_utc') or '')[:10]
        if len(stamp) == 10 and stamp < cutoff:
            continue
        add(row.get('to'))
    return found


def sync_messages(days=7, phones=None, verbose=False, dry_run=False):
    try:
        return _sync_messages(days=days, phones=phones, verbose=verbose, dry_run=dry_run)
    except Exception as e:
        # Status is written even when the scan raises, including `quo_sync.py --messages`.
        # The kind is the exception type. The message can carry a number or a key, so it stays
        # off the status file.
        kind = type(e).__name__
        print('!! inbound STOP scan crashed (%s) -- texting is HELD until a clean scan' % kind)
        _write_inbound_status(False, 'scan crashed: %s' % kind)
        return 1


def _sync_messages(days=7, phones=None, verbose=False, dry_run=False):
    # Read the previous status before this run replaces it. A failed scan must not be what
    # decides how far back the next attempt looks.
    since, window_days = _message_window(days)
    key = _key()
    if not key:
        print('quo.key missing -- inbound STOP scan skipped')
        _write_inbound_status(False, 'quo.key missing', window=window_days)
        return 1
    try:
        from replies import is_sms_stop
        from optout_sync import ledger_add
    except Exception as e:
        print('!! inbound STOP scan cannot run (%s) -- replies/optout_sync import failed' % type(e).__name__)
        _write_inbound_status(False, 'import failed: %s' % type(e).__name__, window=window_days)
        return 1
    try:
        pn = _get_retry(key, '/phone-numbers')
    except json.JSONDecodeError:
        _write_inbound_status(False, 'phone-numbers malformed', window=window_days)
        return 1
    except Exception as e:
        _write_inbound_status(False, 'phone-numbers %s' % type(e).__name__, window=window_days)
        return 1
    pn_err = _response_error(pn)
    if pn_err:
        print('phone-numbers %s -- nothing scanned, texting held' % pn_err)
        _write_inbound_status(False, 'phone-numbers %s' % pn_err, window=window_days)
        return 1
    pids = [x.get('id') for x in (pn.get('data') or []) if isinstance(x, dict) and x.get('id')]
    if not pids:
        print('no Quo phone numbers visible to this key -- nothing to scan')
        _write_inbound_status(False, 'no Quo phone numbers visible to this key', window=window_days)
        return 1
    conv_nums, conv_pages, conv_scanned, conv_err, conv_trunc = _list_conversations(key)
    pages_read = conv_pages
    stops, scanned, errors, truncated = [], 0, 0, bool(conv_trunc)
    kinds = []

    def note(kind):
        if kind and kind not in kinds:
            kinds.append(kind)

    if conv_err:
        errors += 1
        note(conv_err)
    if conv_trunc:
        note('conversations truncated')
    text_problem = _text_sent_problem()
    if text_problem:
        errors += 1
        note(text_problem)
    mail_problem = _mail_sent_problem()
    if mail_problem:
        errors += 1
        note(mail_problem)
    nums = _numbers_to_scan(phones, extra=conv_nums)

    for e164 in sorted(nums):
        n10 = e164[-10:]
        for pid in pids:
            token = ''
            page_n = 0
            while True:
                if page_n >= MESSAGE_PAGE_CAP:
                    if token:
                        truncated = True
                        note('truncated')
                    break
                params = {'phoneNumberId': pid, 'participants': e164,
                          'createdAfter': since, 'maxResults': 50}
                if token:
                    params['pageToken'] = token
                try:
                    res = _get_retry(key, MESSAGES_PATH, params)
                except json.JSONDecodeError:
                    errors += 1
                    note('messages malformed')
                    break
                except Exception as e:
                    errors += 1
                    note('messages %s' % type(e).__name__)
                    break
                err = _response_error(res)
                if err:
                    errors += 1
                    note('messages %s' % err)
                    break
                page_n += 1
                pages_read += 1
                saw_stop = False
                for m in res.get('data') or []:
                    if not isinstance(m, dict) or not _inbound(m):
                        continue
                    scanned += 1
                    # Live messages carry the body in `text`. `content` / `body` stay as fallbacks.
                    body = str(m.get('content') or m.get('text') or m.get('body') or '')
                    if is_sms_stop(body):
                        stops.append((n10, body[:120], str(m.get('createdAt') or '')[:19]))
                        if verbose:
                            print('  STOP from %s: %r' % (n10, body[:80]))
                        saw_stop = True
                        break
                if saw_stop:
                    break
                token = _page_token(res)
                if not token:
                    break
    for n10, body, when in stops:
        a, b = ledger_add(['#' + n10], 'inbound TEXT said stop. Covers ALL channels: no email, no '
                                       'call, no text, no door.', 'sms STOP (quo_sync --messages)',
                          when=when.replace('T', ' ') if when else None, dry_run=dry_run, excerpt=body)
        print('  %s %s -> %s' % ('WOULD LEDGER' if dry_run else 'LEDGERED' if a else 'already ledgered',
                                 n10, body[:60]))
    print('inbound texts: %d number(s) checked, %d inbound message(s) read, %d STOP(s), %d fetch error(s)'
          % (len(nums), scanned, len(stops), errors))
    # Any fetch error or a page cap holds texting. A partial conversations read is the same
    # kind of miss as a partial message read: the one STOP can be on the page we did not get.
    # `why` is the kind only. pages and conversations are counts, never numbers.
    if errors or truncated:
        _write_inbound_status(False, '; '.join(kinds) or 'message fetch error',
                              checked=len(nums), stops=len(stops), errors=errors, truncated=truncated,
                              pages=pages_read, conversations=conv_scanned, window=window_days)
        return 2
    _write_inbound_status(True, checked=len(nums), stops=len(stops), errors=0, truncated=False,
                          pages=pages_read, conversations=conv_scanned, window=window_days)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--phone', action='append')
    ap.add_argument('--case')
    ap.add_argument('--watch', action='store_true')
    ap.add_argument('--messages', action='store_true',
                    help='ONLY the inbound-text STOP scan (it also runs after every normal sync)')
    ap.add_argument('--no-messages', action='store_true', help='skip the inbound-text STOP scan')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    phones = a.phone or []
    if a.case:
        for d in _leads():
            if str(d.get('case') or '').strip() == a.case.strip():
                phones += [p for p in (d.get('phones') or [])]
    if a.messages:
        # sync_messages writes the status file itself, including when it catches an exception.
        return sync_messages(days=a.days, phones=phones or None, verbose=True, dry_run=a.dry_run)
    rc = sync(days=a.days, phones=phones or None, watch=a.watch)
    if not a.watch and not a.no_messages:
        # STOP scan is ON by default: a text opt-out that nobody reads is the FTSA fact pattern.
        # sync_messages records ok:false if it raises, so a crash cannot look like a clean scan.
        sync_messages(days=a.days, phones=phones or None, dry_run=a.dry_run)
    return rc


if __name__ == '__main__':
    sys.exit(main())
