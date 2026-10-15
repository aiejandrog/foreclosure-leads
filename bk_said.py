"""bk_said.py - a homeowner who TELLS us they filed bankruptcy is held on every channel.

WHY (2026-10-15). Every bankruptcy hold in this repo came from a docket or a federal search
(sale_history saleBkAct, stay_gate, bk_lookup, PACER). A reply that said "we filed chapter 13" or
"estoy en bancarrota" was saved by replies.py as an ordinary non-stop reply, which the board then
ranks as the hottest lead in the REPLIED lane, and cadence only cancelled that one email
enrolment. Texts, letters, Call Mode and the owner's other channels stayed live. Contacting a
debtor under the §362 automatic stay is the exposure the stay gate exists for; the homeowner's
own words are the earliest evidence we ever get of it, often weeks before PACER or the docket.

What this does: reads replies.json (the file replies.py already writes), and for each case whose
newest reply mentions bankruptcy (EN or ES) and is not plainly a negation ("never filed
bankruptcy", "no bankruptcy"), ADDS the case to DEALFLOW_DIR/never_contact.json. That one file is
already read by the send bridge (/send, /text, reply_send through stay_gate.check), by send_hold
(letters, and cadence through outreach_email), by HoldIndex (Call Mode, the dial queue, the knock
planner) and by sale_history (the board), and the unchecked-bankruptcy waiver never releases a
case on it. So no gate changes here, and the opt-out ledger is not touched.

Rules:
  * Add-only. It never removes a case. A person takes a case off never_contact.json by hand.
  * A case taken off by hand is not re-added for the SAME reply: bk_said_state.json remembers the
    reply time it acted on. A new reply that mentions bankruptcy again holds the case again.
  * Every entry it writes is keyed the way stay_gate reads them (case_stem or pacer_key). A number
    stay_gate cannot key is not written: one bad entry would make the whole file unreadable,
    and an unreadable never_contact.json refuses every send.
  * A never_contact.json that exists but does not parse is not replaced (sends are already
    refused while it is unreadable, and a person has to look at it). Writes are temp + replace.
  * A reply with no case on file is printed for a person; there is nothing to key a hold on.

It reads only the reply excerpt replies.py keeps (the quote-stripped new text, first 220
characters) and the subject. A bankruptcy mention past those 220 characters is not seen.

    python bk_said.py                 # dry run: print what would be held
    python bk_said.py --apply         # add the cases to never_contact.json
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPLIES = os.path.join(HERE, 'replies.json')
STATE_NAME = 'bk_said_state.json'

# A mention of bankruptcy, in the words a homeowner uses. Kept broad on purpose: a false hold costs
# one lead until a person clears it; a missed one is a contact during an automatic stay.
_MENTION = [
    # bankrupt, bankruptcy, and the usual misspellings (bankrupcy, bankruptsy, bankrapt)
    r'\bbankr[aeiu]?p\w*',
    r'\b(?:in|filed|filing|file|declared)\s+(?:for\s+)?(?:a\s+)?bk\b',
    r'\bbanca\s+rota\b',
    r'\bbk\s+(?:case|attorney|lawyer|filing|trustee|court)\b',
    r'\bchapter\s*(?:7|11|13|seven|thirteen)\b',
    r'\bch\.?\s*(?:7|13)\b',
    r'\bautomatic\s+stay\b',
    r'\b(?:my|our|the)\s+(?:bankruptcy\s+)?trustee\b',
    r'\bbancarrota\b',
    r'\bquiebra\b',
    r'\bcap[ií]tulo\s*(?:7|11|13|siete|trece)\b',
    r'\bs[ií]ndico\b',
    # A bankruptcy court case number with the judge's initials: 25-12345-RAM, 2:24-bk-01234.
    r'\b\d{2}-\d{5}-[a-z]{2,4}\b',
    r'\b\d:\d{2}-bk-\d{4,5}\b',
]
_MENTION_RE = re.compile('|'.join('(?:%s)' % p for p in _MENTION), re.I)

# A plain denial right before the mention: "never filed bankruptcy", "not filing bankruptcy", "no
# bankruptcy", "I don't want to file bankruptcy", "no estoy en bancarrota", "nunca me declaré en
# quiebra". The words between the denial and the mention must all be filing words, joined by
# spaces only, so "No thanks, we filed bankruptcy", "Don't call me, I filed chapter 13" and
# "no gracias estamos en quiebra" are NOT denials (review 2026-10-15).
_FILLER = (r"(?:filed|filing|file|to|want|wanna|going|gonna|plan|planning|plans|in|for|a|any|an|"
           r"declared|declare|declaring|have|ever|been|be|of|considering|thinking|about|"
           r"estoy|estamos|me|nos|declar[eé]|declaramos|en|vamos|voy|a|ir|hemos|he|"
           r"radicado|radicar|radiqu[eé]|ninguna|ning[uú]n|una|un)")
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|don'?t|didn'?t|won'?t|haven'?t|hasn'?t|isn'?t|aren'?t|wasn'?t|without|"
    r"nunca|tampoco|sin)(?:\s+" + _FILLER + r"){0,4}\s+$", re.I)


def says_bankruptcy(text):
    """True when the text mentions bankruptcy and at least one mention is not a plain denial."""
    s = ' '.join(str(text or '').split())
    for m in _MENTION_RE.finditer(s):
        before = s[max(0, m.start() - 80):m.start()]
        if not _NEGATION_RE.search(before):
            return True
    return False


def _dealflow_dir():
    try:
        import paths as _P
        return _P.DEALFLOW_DIR
    except Exception:
        return os.environ.get('DEALFLOW_DIR') or os.path.join(os.path.expanduser('~'), 'DEALFLOW')


def _key(case):
    """The key stay_gate.never_contact reads for this number, or '' when it cannot key it."""
    import stay_gate as SG
    return SG.case_stem(case) or SG.pacer_key(case) or ''


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _write_json(path, data):
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.bk_said.', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_never_contact(cases, path=None):
    """Add case numbers to never_contact.json. Returns (added, already, refused).

    refused lists numbers stay_gate cannot key. Raises ValueError when the existing file does not
    parse or is not a list (it is left exactly as it is)."""
    import stay_gate as SG
    path = path or SG._never_contact_path()
    try:
        cur = _load_json(path, [])
    except ValueError as e:
        raise ValueError('%s does not parse, left as is: %s' % (SG.NEVER_CONTACT_NAME, e))
    if not isinstance(cur, list):
        raise ValueError('%s is not a list, left as is' % SG.NEVER_CONTACT_NAME)
    have = {_key(x) for x in cur if isinstance(x, str)} | set(SG.NEVER_CONTACT)
    added, already, refused = [], [], []
    for c in cases:
        k = _key(c)
        if not k:
            refused.append(c)
        elif k in have:
            already.append(c)
        else:
            cur.append(str(c).strip())
            have.add(k)
            added.append(c)
    if added:
        _write_json(path, cur)
    return added, already, refused


def from_replies(replies_path=REPLIES, apply=False, state_path=None, nc_path=None, out=print):
    """Scan replies.json; hold every case whose newest reply says bankruptcy. Returns a counts dict."""
    state_path = state_path or os.path.join(_dealflow_dir(), STATE_NAME)
    counts = {'replies': 0, 'said': 0, 'added': 0, 'already': 0, 'refused': 0, 'no_case': 0,
              'seen_before': 0, 'error': ''}
    try:
        found = _load_json(replies_path, {})
    except ValueError as e:
        counts['error'] = 'replies.json does not parse (%s)' % str(e)[:80]
        out('BK-SAID: ' + counts['error'])
        return counts
    try:
        state = _load_json(state_path, {})
    except ValueError:
        state = {}
    if not isinstance(state, dict):
        state = {}
    if not isinstance(found, dict):
        found = {}
    said_cases, said_addrs, acted = [], [], {}
    for k, rec in found.items():
        if not isinstance(rec, dict):
            continue
        counts['replies'] += 1
        if not says_bankruptcy(' '.join([str(rec.get('subject') or ''), str(rec.get('excerpt') or '')])):
            continue
        counts['said'] += 1
        # the reply's own date; 'checked' changes every scan, so it would undo a hand-clear daily
        when = str(rec.get('when') or ('excerpt:' + str(rec.get('excerpt') or '')[:80]))
        if str(k).startswith('@'):
            said_addrs.append((k, rec))
            continue
        if state.get(k) == when:
            # Already acted on this exact reply. If the case is no longer held, a person cleared it
            # by hand, and the same reply does not put it back.
            counts['seen_before'] += 1
            continue
        said_cases.append(k)
        acted[k] = when
    # An address that said it with no case on file: a person has to find the lead.
    cased_addrs = {str(r.get('email') or '').lower() for k, r in found.items()
                   if isinstance(r, dict) and not str(k).startswith('@')}
    for k, rec in said_addrs:
        if str(rec.get('email') or k[1:]).lower() not in cased_addrs:
            counts['no_case'] += 1
            out('BK-SAID no case on file: %s says bankruptcy - find the lead and add it to '
                'never_contact.json by hand' % k)
    for c in said_cases:
        out('BK-SAID %s: reply mentions bankruptcy -> never contact (%s)'
            % (c, 'adding' if apply else 'dry run'))
    if not apply or not said_cases:
        return counts
    try:
        added, already, refused = add_never_contact(said_cases, nc_path)
    except (ValueError, OSError) as e:
        counts['error'] = str(e)[:160]
        out('BK-SAID: NOT WRITTEN - ' + counts['error'])
        return counts
    counts.update(added=len(added), already=len(already), refused=len(refused))
    for c in refused:
        out('BK-SAID %s: not a case number stay_gate can key - hold it by hand' % c)
        acted.pop(c, None)
    state.update(acted)
    try:
        _write_json(state_path, state)
    except OSError as e:
        out('BK-SAID: state not saved (%s); the same replies will be re-added next run' % e)
    return counts


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    apply = '--apply' in argv
    c = from_replies(apply=apply)
    print('BK-SAID %s: %d replies read, %d mention bankruptcy, %d added, %d already held, '
          '%d refused, %d with no case, %d acted on before%s'
          % (datetime.now().strftime('%Y-%m-%d %H:%M'), c['replies'], c['said'], c['added'],
             c['already'], c['refused'], c['no_case'], c['seen_before'],
             (' - ERROR ' + c['error']) if c['error'] else ''))
    # A failed write exits 1 so morning_sync records the step as failed. Neither caller stops on it:
    # run-replies-daily.bat goes on to the rebuild, and morning_sync's AFTER steps are log-only.
    return 1 if c['error'] else 0


if __name__ == '__main__':
    sys.exit(main())
