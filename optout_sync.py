#!/usr/bin/env python
"""optout_sync — every detected STOP becomes a ledgered opt-out. Automatically. Same night.

THE GAP THIS CLOSES (found live 2026-08-13)
replies.py already detects a STOP word and writes `stop: true` into replies.json. Nothing then
carried that into optouts.json, which is the ledger the board and the send path actually consult.
So the pipeline *knew* and still stayed armed:

  * Norma Hendy (CACE-25-005200) reached the ledger only because a human typed it in.
  * gil_sosa@hotmail.com wrote "I am not Virginia. This is my personal email. Please stop sending
    emails" on 08-08. replies.py flagged it 08-12. It was STILL not in optouts.json on 08-13 —
    five days armed, against a person who was never even the right contact.

A detected opt-out that never reaches the ledger is worse than no detection: it manufactures a
record showing we were told to stop and kept going. That is the FTSA/TCPA/FDUTPA fact pattern, and
it is also just wrong.

SAFETY POSTURE — ONE WAY, ALWAYS
This only ever ADDS. It never clears, never downgrades, never rewrites an existing entry's status
(same rule as the server-side opt-out merge). Re-running is a no-op. An entry already in the ledger
is left exactly as a human left it.

BOTH KEYS, BECAUSE THE LEDGER IS CASE-KEYED AND REPLIES ARE EMAIL-KEYED
replies.json holds a lead twice: once under the case, once under '@address'. Norma had both; Gil had
only the email (his case had already left the board). Suppressing one key and not the other is how a
"handled" opt-out comes back. So every stop writes EVERY key it appears under, plus the raw address
onto the hard-suppression list send_server enforces.

Run:  python optout_sync.py            # sync, write, report
      python optout_sync.py --dry-run  # show what it WOULD ledger, write nothing
"""
import argparse
import datetime
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPLIES = os.path.join(HERE, 'replies.json')
OPTOUTS = os.path.join(HERE, 'optouts.json')
SUPPRESS = os.path.join(HERE, 'bounced_emails.json')


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


def _truthy(v):
    # replies.py has written both a real bool and the STRING "True" (seen live on gil_sosa).
    # Treating the string as falsey is exactly how an opt-out silently fails to register.
    return str(v).strip().lower() in ('true', '1', 'yes')


NOTES = os.path.join(HERE, 'worker_notes.json')
_DNC_STATUS = {'do not contact', 'dnc'}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


class LedgerUnreadable(Exception):
    """optouts.json exists and does not parse. The original file was not rewritten."""


def _refuse_unreadable_ledger(path, err):
    """Copy the bad bytes aside and raise. Never replace the original.

    A parse failure used to come back as {} and the next add rewrote the file, which is how a
    torn ledger became an empty one and the send gate started passing. Sends stay blocked only
    while the unreadable file is still the file the gate reads."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = path + '.corrupt-' + ts
    copied = False
    try:
        shutil.copy2(path, backup)
        copied = True
    except Exception as ce:
        print('!! %s UNREADABLE and the backup copy FAILED (%s). '
              'The original was NOT modified. Sends stay blocked.'
              % (os.path.basename(path), str(ce)[:160]), file=sys.stderr)
    else:
        print('!! %s UNREADABLE (%s). Copied the bad file to %s and LEFT THE ORIGINAL '
              'UNTOUCHED. Refusing to write. Sends stay blocked.'
              % (os.path.basename(path), str(err)[:160], os.path.basename(backup)), file=sys.stderr)
    raise LedgerUnreadable('opt-out ledger unreadable; original left untouched%s'
                           % ((' (backup %s)' % os.path.basename(backup)) if copied else ''))


def _read_ledger_for_write(path):
    """The current ledger, or None when the file does not exist yet.

    Exists-but-unreadable raises LedgerUnreadable after copying a backup. The caller must not
    create a replacement."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception as e:
        _refuse_unreadable_ledger(path, e)


def ledger_add(keys, note, src, when=None, emails=(), dry_run=False, excerpt=''):
    """THE ONE WRITER of optouts.json (2026-09-25). Add-only, atomic, envelope-preserving.

    Before this there were three writers (replies._ledger_stops, cadence.main, this main) with
    three slightly different shapes, one of them non-atomic. Every detector now calls this and
    nothing else touches the file. Returns (added_keys, already_keys).

      keys    -- ledger keys to add: a case number, '@'+email, or '#'+digits. Each key that is
                 not already present gets a DO NOT CONTACT entry; existing entries are never
                 rewritten (a hand-written note with more context always survives).
      note    -- the human-readable reason stored on the entry.
      src     -- which detector/surface produced the verdict (goes in optlog).
      emails  -- addresses to put on the bridge's hard-suppression list (bounced_emails.json).

    The opt-out file's mtime moves only when a key is actually added. An empty add, including a
    /notes push of a note that is already ledgered, must not make a stale ledger look fresh.
    The 07:15 process refreshes that mtime itself after a completed sweep (see _main).
    """
    now = _now()
    when = (str(when) if when else now)[:32]
    keys = [str(k).strip() for k in keys if k and str(k).strip()]
    keys = [k.lower() if (k.startswith('@') or k.startswith('#')) else k for k in keys]
    # Read BEFORE any write, including a dry run. A file we cannot parse is not an empty ledger.
    opt = _read_ledger_for_write(OPTOUTS)
    if opt is None:
        opt = {}
    elif not isinstance(opt, dict):
        _refuse_unreadable_ledger(OPTOUTS, 'parsed but is not an object (%s)' % type(opt).__name__)
    if 'notes' not in opt or not isinstance(opt.get('notes'), dict):
        # legacy bare-dict shape: treat every top-level key as a note and re-wrap
        legacy = {k: v for k, v in opt.items() if k not in ('_dealflow_notes', 'exported', 'device')}
        opt = {'_dealflow_notes': 1, 'device': 'server-ledger', 'notes': legacy}
    opt.setdefault('_dealflow_notes', 1)
    opt.setdefault('device', 'server-ledger')
    notes = opt['notes']
    added, already = [], []
    for k in keys:
        if k in notes and notes[k]:
            already.append(k)
            continue
        added.append(k)
        if dry_run:
            continue
        notes[k] = {
            'status': 'DO NOT CONTACT',
            'optout': when[:10],
            'note': ('OPT-OUT %s - %s%s' % (now, note, (' Reply: %r.' % excerpt[:180]) if excerpt else '')),
            'optlog': [{'ts': when, 'act': 'opted-out', 'src': src},
                       {'ts': now, 'act': 'ledgered', 'src': 'optout_sync.ledger_add'}],
        }
    # Read the bounce list BEFORE either write, and only when this call would change it.
    # _load() turns a torn file into {} and the next add used to replace the real list with
    # only the new addresses, which also empties the #98 bounce block. Same refusal as the
    # opt-out ledger: backup, do not write, raise. A call with no new addresses does not open
    # the file, so a torn bounce list cannot block an unrelated opt-out key.
    want = [e.strip().lower() for e in set(emails or ()) if e and e.strip()]
    sup, sup_new = {}, []
    if want:
        sup = _read_ledger_for_write(SUPPRESS)
        if sup is None:
            sup = {}
        elif isinstance(sup, list):
            # Older files are a bare list of addresses. That parses; keep every address.
            sup = {str(x).strip().lower(): {'type': 'legacy'} for x in sup if x}
        elif not isinstance(sup, dict):
            _refuse_unreadable_ledger(SUPPRESS, 'parsed but is not an object or a list (%s)'
                                      % type(sup).__name__)
        sup_new = [e for e in want if e not in sup]
    if dry_run:
        return added, already
    if added:
        opt['exported'] = now[:10]
        tmp = OPTOUTS + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(opt, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, OPTOUTS)
    if sup_new:
        for e in sup_new:
            sup[e] = {'type': 'optout', 'when': now[:10], 'why': src}
        tmp = SUPPRESS + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(sup, fh, indent=0, ensure_ascii=False)
        os.replace(tmp, SUPPRESS)
    return added, already


def notes_dnc_keys(path=None):
    """Case keys (lowercased) and person keys ('#digits', '@email') that the REP-LOGGED notes mark
    DO NOT CONTACT / opted out / hard-no. Read from worker_notes.json (the bridge's backup of the
    board+phone notes) so a "stop calling me" logged in Call Mode gates email even when the
    /notes push that would have ledgered it never happened (bridge down, laptop asleep).

    Every email gate unions this with the ledger. It is a READ; the ledger stays the record."""
    out = set()
    d = _load(path or NOTES, {})
    notes = d.get('notes') if isinstance(d, dict) else None
    if not isinstance(notes, dict):
        return out
    for k, n in notes.items():
        if not isinstance(n, dict):
            continue
        st = str(n.get('status') or '').strip().lower()
        if st in _DNC_STATUS or n.get('optout') or str(n.get('no') or '').lower() == 'hard':
            out.add(str(k).strip().lower())
            for p in (n.get('dntph') or []):
                out.add('#' + str(p).strip())
    return out


def ledger_from_notes(payload, src='call-mode notes push', dry_run=False):
    """Push every DNC/opt-out/hard-no in a notes payload into the ledger. Called by send_server on
    every /notes POST, so a hard no tapped on the phone reaches optouts.json within one sync
    instead of never. Returns (added, already)."""
    notes = payload.get('notes') if isinstance(payload, dict) else None
    if not isinstance(notes, dict):
        return [], []
    keys, whens = [], {}
    for k, n in notes.items():
        if not isinstance(n, dict):
            continue
        st = str(n.get('status') or '').strip().lower()
        if not (st in _DNC_STATUS or n.get('optout') or str(n.get('no') or '').lower() == 'hard'):
            continue
        keys.append(str(k))
        for p in (n.get('dntph') or []):
            keys.append('#' + str(p).strip())
        whens[str(k)] = str(n.get('optout') or n.get('noAt') or '')
    if not keys:
        return [], []
    return ledger_add(keys, 'rep logged DO NOT CONTACT / hard no on the board or phone. Covers ALL '
                            'channels: no email, no call, no text, no door.', src, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    try:
        return _main(a)
    except LedgerUnreadable as e:
        print('!! %s' % e, file=sys.stderr)
        return 1


def _touch_ledger_mtime():
    """The 07:15 sweep's freshness mark. ledger_add does not do this on an empty add."""
    if os.path.exists(OPTOUTS):
        try:
            os.utime(OPTOUTS, None)
        except OSError:
            pass


def _main(a):
    # A torn opt-out file fails this process before any other work. This call does not refresh
    # the mtime: only an add does, inside ledger_add. The touch below is this process, after
    # the sweep, so a day with nothing new still counts as a fresh ledger.
    ledger_add([], 'readiness', 'optout_sync.main', dry_run=a.dry_run)

    rep = _load(REPLIES, {})
    stops = {k: v for k, v in rep.items() if isinstance(v, dict) and _truthy(v.get('stop'))}
    added, already, addrs = [], [], []
    now = _now()

    for key, v in stops.items():
        email = (v.get('email') or (key[1:] if key.startswith('@') else '')).strip().lower()
        if email:
            addrs.append(email)
        when = str(v.get('when') or v.get('checked') or '')[:32]
        excerpt = str(v.get('excerpt') or '').strip()[:200]
        _a, _b = ledger_add([key],
                            'AUTO-LEDGERED by optout_sync from replies.json. Covers ALL channels: no '
                            'email, no call, no text, no door. Subject: %s' % str(v.get('subject') or '')[:90],
                            'reply STOP word (replies.json)', when=when or now, emails=[email] if email else (),
                            dry_run=a.dry_run, excerpt=excerpt)
        added += _a
        already += _b

    # Also sweep the rep-logged notes: any DNC the phone or board holds that never reached the
    # ledger (bridge down at push time) lands here on the nightly pass.
    _na, _nb = ledger_from_notes(_load(NOTES, {}), src='worker_notes.json nightly sweep', dry_run=a.dry_run)
    added += _na
    already += _nb

    # This process, not ledger_add, is what makes "nobody new opted out" a fresh ledger. A
    # /notes push never reaches here, so it cannot refresh a stale file by adding nothing.
    if not a.dry_run:
        _touch_ledger_mtime()
    sup_new = []
    print('%d STOP-flagged reply key(s) in replies.json' % len(stops))
    print('  already ledgered : %d' % len(already))
    print('  %s: %d %s' % ('WOULD ledger' if a.dry_run else 'newly ledgered', len(added),
                           added or ''))
    if added and not a.dry_run:
        print('\n!! %d opt-out(s) were detected but NOT suppressed until this run. If any send went '
              'out to them in between, that is a real compliance event — check the Sent label.'
              % len(added))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
