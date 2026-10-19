#!/usr/bin/env python
"""deliverability.py -- keep outreach out of the spam folder, with no paid service.

WHAT THIS IS (2026-10-19). Instantly-style deliverability checks, rebuilt for a local Gmail SMTP
sender. Four parts, all free:

  score      content scoring. Spam-trigger phrases, shouting, links, subject shape. The legally
             required sentences (disclaimer.identity(), the opt-out line) are stripped before
             scoring: they are not ours to change, so they must not move the number.
  headers    reads a RECEIVED message's Authentication-Results and says whether SPF, DKIM and
             DMARC passed and whether each one ALIGNS with the From domain (strict or relaxed).
  dns        the published SPF, DMARC and Google DKIM records for every sending domain.
  placement  seed test. Sends the real first-touch template from every sending address to seed
             mailboxes, then logs into each seed over IMAP and finds the probe by Message-ID:
             inbox, promotions, spam, or missing. A sender whose probe lands in spam, goes
             missing, or fails DMARC is PAUSED (placement_status.json). The pause is read by
             send_server._smtp_send and outreach_email._smtp_send, so /send, the CLI and cadence
             all stop mailing from that address. It lifts only when a later test comes back clean.

WHAT THE SEED TEST DOES AND DOES NOT PROVE. A seed that is one of our own Workspace mailboxes
(bsgflorida.com, biscaynesolutionsgroup.com, bsgfl.com) is INTRA-ORG delivery: Google skips most
of the filtering a homeowner's mailbox applies, and often adds no Authentication-Results at all.
A spam or missing result there is damning; an inbox result there proves routing, not placement.
Real placement needs EXTERNAL seeds: free consumer Gmail / Yahoo accounts with an
App Password, listed in DEALFLOW_DIR/placement_seeds.json. Results say which kind each one was.

Exempt from the pause: reply_send (a homeowner who wrote to us), mail only to our own domains or
the advisor, and the probes themselves. A board send to a homeowner, reply or not, is held.

RUN
    python deliverability.py score --template           # score the current first-touch email
    python deliverability.py score --subject S --body-file F
    python deliverability.py headers message.eml        # 'Show original' saved from Gmail
    python deliverability.py dns
    python deliverability.py placement                  # DRY RUN: who would be probed, from where
    python deliverability.py placement --send           # send probes, find them, write the pause
    python deliverability.py status                     # what is paused right now, and why

SEEDS (DEALFLOW_DIR/placement_seeds.json, never the repo):
    [{"addr": "bsgseed1@gmail.com", "key_file": "C:/Users/olqbb/DEALFLOW/seed1.key"},
     {"addr": "bsgseed2@yahoo.com", "key_file": "...", "imap_host": "imap.mail.yahoo.com"}]
  Gmail and Yahoo App Passwords work over IMAP. Consumer Outlook.com dropped App Password IMAP
  (believed, not tested here), so an Outlook seed will likely read 'error' and count for nothing.
  key_file holds 'user:APP_PASSWORD'. imap_host is guessed from the domain when absent. With no
  seeds file, the one seed is the sending login's own mailbox (internal), from bsg_gmail.key.

SWITCHES
    DEALFLOW_PLACEMENT_HOLD=0          the pause is off (reported, never enforced)
    DEALFLOW_PLACEMENT_REQUIRED=1      no test, or a clean test older than the max age, also holds
    DEALFLOW_PLACEMENT_MAX_AGE_DAYS    default 7. A PAUSE never expires by age; only a clean test lifts it
    DEALFLOW_SPAM_HOLD_SCORE           unset = content score is advisory. A number = refuse a send
                                       scoring at or above it

EXIT CODES: 0 ok / clean; 1 a finding (score high, auth fail, a sender paused); 2 usage or setup.
"""
import argparse
import datetime as dt
import email
import email.policy
import imaplib
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mail_guard as _MG
try:
    import paths as P
except Exception:   # a sandbox that copied only the send modules
    P = None


def _out(name):
    if P is not None:
        return P.out(name)
    d = os.environ.get('DEALFLOW_DIR') or os.path.join(os.path.expanduser('~'), 'DEALFLOW')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)

OUR_DOMAINS = ('bsgflorida.com', 'biscaynesolutionsgroup.com', 'bsgfl.com')
# Measured 2026-09-17 against public DNS: the two alias domains publish adkim=s; aspf=s.
STRICT_DOMAINS = ('biscaynesolutionsgroup.com', 'bsgfl.com')
EXEMPT_GATES = frozenset({'reply_send', 'placement_probe'})
PROBE_GATE = 'placement_probe'

WARN_SCORE = 4.0
HIGH_SCORE = 8.0


def _status_path():
    return _out('placement_status.json')


def _seeds_path():
    return _out('placement_seeds.json')


class PlacementHold(_MG.UnsendableError):
    """Raised before SMTP. A subclass of UnsendableError so every caller that already releases its
    claim and skips on a merge refusal does the same here: nothing was sent, nothing is lost."""


# ------------------------------------------------------------------ content scoring
# (weight, label, pattern). Lowercased text. Weights are heuristics in the shape SpamAssassin and
# the warm-up vendors publish, not a provider's real model: a high score is a reason to rewrite, a
# low one is not a promise of the inbox. The seed test is the measurement; this is the lint.
_PHRASES = [
    # urgency / pressure
    (1.5, 'urgency', r'\b(?:act|reply|respond|call)(?: me)? (?:now|today|immediately)\b'),
    (1.5, 'urgency', r"\bdon'?t wait\b|\bplease don'?t wait\b"),
    (1.5, 'urgency', r'\b(?:the )?clock is ticking\b|\btime is running out\b|\blast chance\b'),
    (1.0, 'urgency', r'\burgent\b'),
    (1.0, 'urgency', r'\blimited time\b|\bonly a few days\b|\bbefore it\'?s too late\b'),
    # free / money / no-risk
    (1.5, 'free', r'\b(?:completely|totally|100%|absolutely) free\b'),
    (1.0, 'free', r'\bfree\b'),
    (1.0, 'no-risk', r'\bno[- ]obligation\b|\bwithout (?:any )?obligation\b'),
    (1.0, 'no-risk', r'\bno (?:hidden )?(?:fees?|cost|charge)s?\b|\bno fee,? ever\b'),
    (1.5, 'no-risk', r'\brisk[- ]free\b|\bguarantee[ds]?\b'),
    (1.0, 'money', r'\bcash (?:offer|for your|buyer)\b|\bwe buy houses\b'),
    (0.5, 'money', r'\$\d'),
    # reassurance that reads as spam
    (1.5, 'reassure', r'\bno tricks?\b|\bnot a scam\b|\bthis is not spam\b|\bno catch\b'),
    (1.0, 'reassure', r'\bno pressure\b'),
    # foreclosure-rescue vocabulary (the identity disclaimer is stripped before this runs)
    (1.5, 'rescue', r'\b(?:stop|avoid|prevent) (?:the |your )?(?:foreclosure|auction|sale)\b'),
    (1.5, 'rescue', r'\bsave your (?:home|house|equity)\b|\bprotect and save\b'),
    (0.5, 'topic', r'\bforeclosure\b'),
]
_SHORTENERS = re.compile(r'\b(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly|is\.gd|buff\.ly|rb\.gy)/', re.I)
_URL = re.compile(r'(?:https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|me|co)/)\S*', re.I)
_IP_URL = re.compile(r'https?://\d{1,3}(?:\.\d{1,3}){3}')
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")
_EMOJI = re.compile('[\U0001F300-\U0001FAFF\u2600-\u27BF]')


def _mandated_sentences():
    """Text we are required to send. Scored as absent, never as a trigger."""
    out = []
    try:
        import disclaimer as D
        for lang in ('en', 'es'):
            out.append(D.identity(lang, as_html=False))
            out.append(D.sig_tag(lang, as_html=False))
    except Exception:
        pass
    try:
        import outreach_copy as OC
        for k in ('OPTOUT_LINE_EN', 'OPTOUT_LINE_ES', 'SMS_OPTOUT_EN', 'SMS_OPTOUT_ES'):
            v = getattr(OC, k, '')
            if v:
                out.append(v)
    except Exception:
        pass
    return [s for s in out if s]


def _strip_mandated(text):
    for s in _mandated_sentences():
        text = text.replace(s, ' ')
    return text


def score_content(subject='', body='', from_addr=''):
    """-> {'score', 'level', 'issues': [{'w', 'rule', 'detail'}], 'words'}. Pure, no network."""
    subj = str(subject or '')
    raw = str(body or '')
    text = _strip_mandated(raw)
    low = text.lower()
    issues = []

    def hit(w, rule, detail):
        issues.append({'w': round(float(w), 2), 'rule': rule, 'detail': detail})

    for w, rule, pat in _PHRASES:
        for src, where in ((low, 'body'), (subj.lower(), 'subject')):
            found = re.findall(pat, src)
            if found:
                # The subject is read first and weighs more: it is what the filter and the reader
                # both see before anything else.
                n = len(found)
                weight = w * (1.5 if where == 'subject' else 1.0) * min(n, 3)
                hit(weight, rule, '%s: "%s"%s' % (where, found[0], (' x%d' % n) if n > 1 else ''))

    words = _WORD.findall(text)
    nwords = len(words)
    caps = [x for x in words if len(x) >= 4 and x.isupper()]
    if words and len(caps) / float(nwords) > 0.05:
        hit(1.5, 'caps', '%d ALL-CAPS words' % len(caps))
    title = [x for x in _WORD.findall(subj) if len(x) > 3]
    if title and sum(1 for x in title if x.isupper()) >= 2:
        hit(2.0, 'caps', 'subject shouts')
    bangs = raw.count('!') + subj.count('!')
    if bangs >= 2:
        hit(min(bangs, 5) * 0.5, 'punctuation', '%d exclamation marks' % bangs)
    if '!' in subj:
        hit(1.0, 'punctuation', 'exclamation mark in subject')
    if re.match(r'\s*(?:re|fwd?):', subj, re.I):
        hit(2.0, 'subject', 'subject starts with Re:/Fwd: on mail that is not a reply')
    if len(subj) > 70:
        hit(0.5, 'subject', 'subject is %d characters' % len(subj))
    if not subj.strip():
        hit(2.0, 'subject', 'empty subject')
    urls = _URL.findall(raw)
    if len(urls) > 2:
        hit(1.0 * (len(urls) - 2), 'links', '%d links' % len(urls))
    if _SHORTENERS.search(raw):
        hit(3.0, 'links', 'link shortener')
    if _IP_URL.search(raw):
        hit(3.0, 'links', 'link to a bare IP address')
    if _EMOJI.search(raw + subj):
        hit(0.5, 'format', 'emoji')
    if nwords < 25:
        hit(1.0, 'length', 'very short body (%d words)' % nwords)
    elif nwords > 300:
        hit(1.0, 'length', 'long body (%d words); cold first touches read best under ~150' % nwords)
    if '<html' in raw.lower() or '<img' in raw.lower():
        hit(1.0, 'format', 'HTML or image in a plain-text send')
    if from_addr and urls:
        fd = from_addr.rsplit('@', 1)[-1].lower()
        other = sorted({re.sub(r'^(?:https?://)?(?:www\.)?', '', u).split('/')[0].lower() for u in urls}
                       - {fd, 'www.' + fd})
        if other:
            hit(0.5, 'links', 'links to a domain other than the sender: %s' % ', '.join(other[:3]))

    score = round(sum(i['w'] for i in issues), 1)
    level = 'high' if score >= HIGH_SCORE else ('warn' if score >= WARN_SCORE else 'ok')
    issues.sort(key=lambda i: -i['w'])
    return {'score': score, 'level': level, 'issues': issues, 'words': nwords}


def sample_first_touch(days_out=14, lang='en'):
    """(subject, body) of the first-touch email outreach_email.py and the board send for a dated
    sale, rendered for a made-up owner. The probe sends exactly this, so the seed test measures the
    copy a homeowner gets, not a 'test' message no filter would ever see."""
    import outreach_copy as OC
    sale = dt.date.today() + dt.timedelta(days=days_out)
    return OC.email_subject(sale), OC.email_body('Maria', sale)


def content_hold_score():
    raw = str(os.environ.get('DEALFLOW_SPAM_HOLD_SCORE') or '').strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


# ------------------------------------------------------------------ header analysis
def _org(domain):
    """Organizational domain. Two labels is right for every domain this company sends from; a
    public-suffix list would be needed for co.uk-style domains, which nothing here uses."""
    parts = str(domain or '').lower().strip('.').split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else '.'.join(parts)


def _aligned(a, b, strict):
    a, b = str(a or '').lower(), str(b or '').lower()
    if not a or not b:
        return False
    return a == b if strict else _org(a) == _org(b)


def _addr_domain(v):
    m = re.search(r'@([A-Za-z0-9.\-]+)', str(v or ''))
    return m.group(1).lower().rstrip('.') if m else ''


def analyze_headers(raw, strict=None):
    """Parse a received message (bytes or str). -> dict with spf / dkim / dmarc results, the
    domains each one authenticated, and whether each ALIGNS with From.

    verdict: 'pass'  DMARC passed, or an aligned SPF or DKIM pass was found
             'fail'  DMARC failed, or nothing aligned passed
             'unknown' no Authentication-Results header (typical of intra-Workspace delivery)
    strict: None = strict for the domains that publish adkim=s; aspf=s (STRICT_DOMAINS)."""
    if isinstance(raw, str):
        raw = raw.encode('utf-8', 'replace')
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    from_dom = _addr_domain(msg.get('From', ''))
    if strict is None:
        strict = from_dom in STRICT_DOMAINS
    ar = msg.get_all('Authentication-Results') or []
    # The receiver puts its own header on top. Take the first, which is the last hop's verdict.
    res = {'from_domain': from_dom, 'strict': bool(strict), 'auth_results': bool(ar),
           'spf': '', 'spf_domain': '', 'dkim': [], 'dmarc': '', 'dmarc_policy': ''}
    if ar:
        top = re.sub(r'\s+', ' ', str(ar[0]))
        m = re.search(r'\bspf=(\w+)', top)
        res['spf'] = m.group(1).lower() if m else ''
        m = re.search(r'smtp\.mailfrom=([^\s;]+)', top)
        res['spf_domain'] = (_addr_domain(m.group(1)) or m.group(1).lower().strip('<>')) if m else ''
        for dm in re.finditer(r'\bdkim=(\w+)([^;]*)', top):
            d = re.search(r'header\.(?:i|d)=@?([^\s;]+)', dm.group(2))
            res['dkim'].append({'result': dm.group(1).lower(),
                                'domain': (d.group(1).lower().lstrip('@').split('@')[-1] if d else '')})
        m = re.search(r'\bdmarc=(\w+)(?:\s*\(([^)]*)\))?', top)
        if m:
            res['dmarc'] = m.group(1).lower()
            pm = re.search(r'\bp=(\w+)', m.group(2) or '')
            res['dmarc_policy'] = pm.group(1).upper() if pm else ''
    if not res['spf_domain']:
        res['spf_domain'] = _addr_domain(msg.get('Return-Path', ''))
    signed = []
    for sig in msg.get_all('DKIM-Signature') or []:
        m = re.search(r'\bd=([^;\s]+)', re.sub(r'\s+', '', str(sig)).replace(';', '; '))
        if m:
            signed.append(m.group(1).lower())
    res['dkim_signed'] = signed
    res['spf_aligned'] = res['spf'] == 'pass' and _aligned(res['spf_domain'], from_dom, strict)
    res['dkim_aligned'] = any(d['result'] == 'pass' and _aligned(d['domain'], from_dom, strict)
                              for d in res['dkim'])
    if not ar:
        res['verdict'] = 'unknown'
    elif res['dmarc'] == 'fail':
        res['verdict'] = 'fail'
    elif res['dmarc'] == 'pass' or res['spf_aligned'] or res['dkim_aligned']:
        res['verdict'] = 'pass'
    else:
        res['verdict'] = 'fail'
    return res


def headers_lines(r):
    out = ['From domain: %s (%s alignment)' % (r['from_domain'] or '?', 'strict' if r['strict'] else 'relaxed')]
    if not r['auth_results']:
        out.append('No Authentication-Results header. Intra-Workspace mail usually has none; '
                   'test against an external seed to see a real verdict.')
    out.append('SPF  %-8s mail-from %s  aligned: %s' % (r['spf'] or '-', r['spf_domain'] or '-',
                                                       'yes' if r['spf_aligned'] else 'no'))
    if r['dkim']:
        for d in r['dkim']:
            out.append('DKIM %-8s d=%s  aligned: %s' % (d['result'], d['domain'] or '-',
                       'yes' if _aligned(d['domain'], r['from_domain'], r['strict']) else 'no'))
    else:
        out.append('DKIM -        signed by: %s' % (', '.join(r['dkim_signed']) or 'nothing'))
    out.append('DMARC %-7s policy %s' % (r['dmarc'] or '-', r['dmarc_policy'] or '-'))
    out.append('Verdict: %s' % r['verdict'].upper())
    return out


# ------------------------------------------------------------------ DNS (DNS-over-HTTPS, free)
def _txt(name, fetch=None):
    url = 'https://dns.google/resolve?' + urllib.parse.urlencode({'name': name, 'type': 'TXT'})
    if fetch is None:
        def fetch(u):
            with urllib.request.urlopen(u, timeout=15) as r:
                return json.loads(r.read().decode('utf-8'))
    data = fetch(url)
    out = []
    for a in data.get('Answer') or []:
        if a.get('type') == 16:
            out.append(''.join(re.findall(r'"([^"]*)"', a.get('data', ''))) or a.get('data', '').strip('"'))
    return out


def dns_report(domains=OUR_DOMAINS, fetch=None):
    rows = []
    for d in domains:
        row = {'domain': d, 'spf': '', 'dmarc': '', 'dkim': False, 'notes': []}
        try:
            spf = [t for t in _txt(d, fetch) if t.lower().startswith('v=spf1')]
            row['spf'] = spf[0] if spf else ''
            if len(spf) > 1:
                row['notes'].append('more than one SPF record: receivers treat that as a permerror')
            if row['spf'] and '_spf.google.com' not in row['spf']:
                row['notes'].append('SPF does not include _spf.google.com')
            if not row['spf']:
                row['notes'].append('no SPF record')
            dm = [t for t in _txt('_dmarc.' + d, fetch) if t.lower().startswith('v=dmarc1')]
            row['dmarc'] = dm[0] if dm else ''
            if not dm:
                row['notes'].append('no DMARC record')
            elif re.search(r'\bp=none\b', dm[0], re.I):
                row['notes'].append('DMARC p=none: monitoring only, receivers get no instruction')
            row['dkim'] = any('p=' in t for t in _txt('google._domainkey.' + d, fetch))
            if not row['dkim']:
                row['notes'].append('no google._domainkey record: Gmail cannot DKIM-sign as this domain')
        except Exception as e:
            row['notes'].append('lookup failed: %s' % str(e)[:80])
        rows.append(row)
    return rows


# ------------------------------------------------------------------ pause store
def _env_on(name, default):
    v = str(os.environ.get(name, '')).strip().lower()
    if not v:
        return default
    return v not in ('0', 'false', 'no', 'off')


def _max_age_days():
    try:
        return float(os.environ.get('DEALFLOW_PLACEMENT_MAX_AGE_DAYS') or 7)
    except ValueError:
        return 7.0


def load_status(path=None):
    """-> (status dict or None, error str). None + '' = no test has ever run."""
    path = path or _status_path()
    if not os.path.exists(path):
        return None, ''
    try:
        with open(path, encoding='utf-8') as f:
            st = json.load(f)
        if not isinstance(st, dict) or not isinstance(st.get('by_from'), dict):
            return None, 'placement_status.json has no by_from table'
        return st, ''
    except Exception as e:
        return None, 'placement_status.json unreadable: %s' % str(e)[:80]


def _age_days(ts, now=None):
    try:
        t = dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        now = now or dt.datetime.now(dt.timezone.utc)
        return (now - t).total_seconds() / 86400.0
    except Exception:
        return None


def placement_hold(addr, path=None, now=None):
    """A sentence when mail from `addr` must not go out, else ''. Never raises: a status it cannot
    make sense of is a hold, so a refusal can never surface as a maybe-sent SMTP error.

    Unreadable status = hold (fail closed, like every other gate here). No status = not measured:
    no hold, unless DEALFLOW_PLACEMENT_REQUIRED=1. A paused sender stays paused at any age."""
    try:
        return _placement_hold(addr, path, now)
    except Exception as e:
        return 'placement hold: status could not be evaluated (%s)' % str(e)[:80]


def _placement_hold(addr, path=None, now=None):
    if not _env_on('DEALFLOW_PLACEMENT_HOLD', True):
        return ''
    a = str(addr or '').strip().lower()
    required = _env_on('DEALFLOW_PLACEMENT_REQUIRED', False)
    try:
        st, err = load_status(path)
    except Exception as e:
        st, err = None, 'placement status could not be read: %s' % str(e)[:80]
    if err:
        return 'placement hold: %s' % err
    if st is None:
        return 'placement hold: no seed test has run and one is required' if required else ''
    row = st['by_from'].get(a)
    if not isinstance(row, dict):
        return ('placement hold: %s has never been seed-tested and a test is required' % a) if required else ''
    if row.get('verdict') == 'hold':
        why = '; '.join(str(x) for x in (row.get('reasons') or [])) or 'last seed test failed'
        return 'placement hold on %s since %s: %s. Run deliverability.py placement --send to retest.' % (
            a, str(row.get('ts') or '?')[:16], why)
    if required:
        age = _age_days(row.get('ts'), now)
        if age is None or age > _max_age_days():
            return 'placement hold: %s last tested clean %s, older than %g days' % (
                a, str(row.get('ts') or '?')[:10], _max_age_days())
    return ''


def _internal_only(to, bcc, login):
    rc = [x.strip().lower() for x in re.split(r'[,;]', '%s,%s' % (to or '', bcc or '')) if x.strip()]
    if not rc:
        return False
    try:
        own = _MG._operator_addrs(login)
    except Exception:
        own = {str(login or '').lower()}
    return all(a in own or _addr_domain(a) in OUR_DOMAINS for a in rc)


def require_clear(sender, subj='', body='', to='', bcc='', login=''):
    """Called by both _smtp_send functions right after mail_guard.require_gated. Raises
    PlacementHold before SMTP when this sender is paused, or when DEALFLOW_SPAM_HOLD_SCORE is set
    and the content scores at or above it. Mail only to ourselves, reply_send (a homeowner who
    wrote in) and the probes pass. Anything unexpected in here is a PlacementHold too: a plain
    exception would read as a maybe-sent in /send and abort a cadence run."""
    try:
        return _require_clear(sender, subj, body, to, bcc, login)
    except PlacementHold:
        raise
    except Exception as e:
        raise PlacementHold('placement check failed (%s). The message was NOT sent.' % str(e)[:80])


def _require_clear(sender, subj, body, to, bcc, login):
    if _MG.current_gate() in EXEMPT_GATES:
        return True
    if _internal_only(to, bcc, login):
        return True
    why = placement_hold(sender)
    if why:
        raise PlacementHold('%s The message was NOT sent.' % why)
    lim = content_hold_score()
    if lim is not None:
        sc = score_content(subj, body, sender)
        if sc['score'] >= lim:
            top = '; '.join(i['detail'] for i in sc['issues'][:3])
            raise PlacementHold('content scores %.1f (hold at %.1f): %s. The message was NOT sent.'
                                % (sc['score'], lim, top))
    return True


# ------------------------------------------------------------------ placement test
_IMAP_HOSTS = {
    'gmail.com': 'imap.gmail.com', 'googlemail.com': 'imap.gmail.com',
    'outlook.com': 'outlook.office365.com', 'hotmail.com': 'outlook.office365.com',
    'live.com': 'outlook.office365.com', 'msn.com': 'outlook.office365.com',
    'yahoo.com': 'imap.mail.yahoo.com', 'ymail.com': 'imap.mail.yahoo.com',
    'aol.com': 'imap.aol.com', 'icloud.com': 'imap.mail.me.com', 'me.com': 'imap.mail.me.com',
}
_JUNK_NAMES = ('[Gmail]/Spam', 'Junk', 'Junk Email', 'Junk E-mail', 'Spam', 'Bulk Mail', 'Bulk')


def _cred_file(fn):
    p = fn if os.path.isabs(fn) else os.path.join(HERE, fn)
    try:
        # PowerShell `echo "user:pw" > file` keeps the quotes, as send_server tolerates.
        raw = open(p, encoding='utf-8').read().strip().strip('"').strip("'")
    except Exception:
        return '', ''
    if ':' not in raw:
        return '', ''
    u, pw = raw.split(':', 1)
    return u.strip().lower(), pw.strip()


def _login_cred():
    """The outreach login exactly as the bridge reads it (GMAIL_APP_PASSWORD override included)."""
    try:
        import send_server as _SS
        u, pw = _SS._load_credentials()
        if u and pw:
            return u, pw
    except Exception:
        pass
    return _cred_file('bsg_gmail.key')


def sending_addresses(cfg=None):
    """Every distinct From address outreach can use: the lane map, then the first-touch rotation."""
    if cfg is None:
        try:
            cfg = json.load(open(os.path.join(HERE, 'senders.json'), encoding='utf-8'))
        except Exception:
            cfg = {}
    out = []
    lanes = cfg.get('lanes') or {}
    ft = (cfg.get('first_touch') or {}).get('from') or []
    for a in list(lanes.values()) + list(ft):
        a = str(a or '').strip().lower()
        if '@' in a and a not in out:
            out.append(a)
    return out


def load_seeds(login=''):
    """-> list of {'addr','user','pw','host','internal'} with credentials resolved. [] plus a note
    when none can be checked."""
    path = _seeds_path()
    raw = None
    if os.path.exists(path):
        try:
            raw = json.load(open(path, encoding='utf-8'))
        except Exception as e:
            raise SystemExit('placement_seeds.json unreadable: %s' % e)
    if raw is None:
        u, pw = _cred_file('bsg_gmail.key')
        raw = [{'addr': u or login, 'key_file': 'bsg_gmail.key'}] if (u or login) else []
    seeds = []
    for s in raw or []:
        addr = str(s.get('addr') or '').strip().lower()
        if '@' not in addr:
            continue
        u, pw = _cred_file(s['key_file']) if s.get('key_file') else ('', '')
        u = u or addr
        dom = addr.rsplit('@', 1)[-1]
        host = s.get('imap_host') or _IMAP_HOSTS.get(dom) or ('imap.gmail.com' if dom in OUR_DOMAINS else '')
        seeds.append({'addr': addr, 'user': u, 'pw': pw, 'host': host,
                      'internal': dom in OUR_DOMAINS, 'gmail': host == 'imap.gmail.com'})
    return seeds


def _junk_folders(M):
    names = []
    try:
        typ, data = M.list()
        for line in data or []:
            s = line.decode('utf-8', 'replace') if isinstance(line, bytes) else str(line)
            m = re.match(r'\((?P<flags>[^)]*)\)\s+"?[^"\s]*"?\s+(?P<name>.+)$', s)
            if m and '\\Junk' in m.group('flags'):
                names.append(m.group('name').strip().strip('"'))
    except Exception:
        pass
    for n in _JUNK_NAMES:
        if n not in names:
            names.append(n)
    return names


def _search(M, folder, mid, gm_raw=''):
    try:
        typ, _ = M.select('"%s"' % folder, readonly=True)
        if typ != 'OK':
            return []
        if gm_raw:
            typ, data = M.search(None, 'X-GM-RAW', '"%s"' % gm_raw)
        else:
            typ, data = M.search(None, 'HEADER', 'Message-ID', mid.strip('<>'))
        return (data[0].split() if typ == 'OK' and data and data[0] else [])
    except Exception:
        return []


def locate(M, mid, gmail=False):
    """-> (folder, raw header bytes). folder: inbox / promotions / spam / other / missing."""
    bare = mid.strip('<>')

    def headers(num):
        try:
            typ, data = M.fetch(num, '(BODY.PEEK[HEADER])')
            for part in data or []:
                if isinstance(part, tuple):
                    return part[1]
        except Exception:
            pass
        return b''

    ids = _search(M, 'INBOX', mid)
    if ids:
        folder = 'inbox'
        if gmail and _search(M, 'INBOX', mid, gm_raw='rfc822msgid:%s category:promotions' % bare):
            folder = 'promotions'
        _search(M, 'INBOX', mid)
        return folder, headers(ids[-1])
    for jf in _junk_folders(M):
        ids = _search(M, jf, mid)
        if ids:
            return 'spam', headers(ids[-1])
    if gmail:
        ids = _search(M, '[Gmail]/All Mail', mid)
        if ids:
            return 'other', headers(ids[-1])
    return 'missing', b''


def _imap(seed):
    M = imaplib.IMAP4_SSL(seed['host'], 993, ssl_context=ssl.create_default_context(), timeout=30)
    M.login(seed['user'], seed['pw'])
    return M


def _seed_reasons(r):
    """Why one probe row holds its sender: [] when it landed fine."""
    out = []
    kind = 'internal' if r.get('internal') else 'external'
    if r.get('folder') in ('spam', 'missing'):
        out.append('%s at %s seed %s' % (r['folder'], kind, r['seed']))
    auth = r.get('auth') or {}
    if auth.get('verdict') == 'fail':
        out.append('DMARC/alignment fail at %s (spf %s, dkim %s, dmarc %s)' % (
            r['seed'], auth.get('spf') or '-',
            ','.join('%s@%s' % (d.get('result'), d.get('domain')) for d in auth.get('dkim') or []) or '-',
            auth.get('dmarc') or '-'))
    return out


def verdict_for(results, prev_by_from=None, ts=''):
    """Per From address -> {'verdict', 'reasons', 'per_seed', 'ts'}.

    Kept PER SEED. A seed this run did not reach (send or IMAP error) keeps its last result, so a
    pause from an external seed cannot be lifted by a retest that only reached our own mailbox.
    A sender is clear only when every seed it has a result for landed fine."""
    by = {}
    for a, row in (prev_by_from or {}).items():
        if isinstance(row, dict) and isinstance(row.get('per_seed'), dict):
            by[a] = {'per_seed': dict(row['per_seed'])}
    for r in results:
        if r.get('folder') == 'error':
            continue
        cur = by.setdefault(r['from'], {'per_seed': {}})
        cur['per_seed'][r['seed']] = {'folder': r['folder'], 'internal': bool(r.get('internal')),
                                      'auth': (r.get('auth') or {}).get('verdict', ''),
                                      'reasons': _seed_reasons(r), 'ts': ts}
        cur['tested'] = True
    out = {}
    for a, cur in by.items():
        if not cur.get('tested') and a in (prev_by_from or {}):
            out[a] = prev_by_from[a]
            continue
        reasons = [x for v in cur['per_seed'].values() for x in (v.get('reasons') or [])]
        out[a] = {'verdict': 'hold' if reasons else 'clear', 'reasons': reasons,
                  'seeds': len(cur['per_seed']), 'per_seed': cur['per_seed'], 'ts': ts}
    return out


class TornStatus(RuntimeError):
    pass


def write_status(results, wait_s, path=None, now=None, all_froms=None):
    """Merge this run into placement_status.json. Addresses not tested this run keep their row.

    An unreadable status holds every sender. It is replaced only by a run that tested every
    sending address (the torn file is kept beside it); a narrower run raises TornStatus, so it
    cannot release the senders it did not test."""
    path = path or _status_path()
    now = now or dt.datetime.now(dt.timezone.utc)
    ts = now.isoformat(timespec='seconds')
    prev, err = load_status(path)
    if err:
        tested = {r['from'] for r in results if r.get('folder') != 'error'}
        need = set(all_froms if all_froms is not None else sending_addresses())
        if not need or not need <= tested:
            raise TornStatus('%s; this run did not test %s, so the file is left in place and every '
                             'sender stays held. Run placement --send with no --from.'
                             % (err, ', '.join(sorted(need - tested)) or 'every sender'))
        try:
            os.replace(path, path + '.torn-' + now.strftime('%Y%m%d%H%M%S'))
        except Exception:
            pass
    by_from = verdict_for(results, (prev or {}).get('by_from') if not err else None, ts)
    st = {'ts': ts, 'wait_s': wait_s, 'by_from': by_from,
          # Addresses only, no message content: seeds are company or test mailboxes.
          'last_run': [{k: r.get(k) for k in ('from', 'seed', 'internal', 'folder')}
                       | {'auth': (r.get('auth') or {}).get('verdict', '')} for r in results]}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1)
    # Windows: the bridge may have the file open for a read at this instant.
    for i in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if i == 4:
                raise
            time.sleep(0.5)
    return st


def run_placement(send=False, wait=180, froms=None, smtp=None, imap=None, out=print, pace=3.0, cred=None):
    """Dry run unless send=True. smtp, imap and cred (login, password) are injectable for tests."""
    login, pw = cred or _login_cred()
    if not login and send:
        out('bsg_gmail.key missing: the probes must leave from the outreach login.')
        return 2
    froms = froms or sending_addresses()
    seeds = load_seeds(login)
    subj, body = sample_first_touch()
    sc = score_content(subj, body)
    out('Probe content: the first-touch email (subject "%s"), content score %.1f (%s).' % (
        subj, sc['score'], sc['level']))
    out('From %d address(es): %s' % (len(froms), ', '.join(froms)))
    if not seeds:
        out('No seeds: add DEALFLOW_DIR/placement_seeds.json or bsg_gmail.key.')
        return 2
    for s in seeds:
        out('  seed %-36s %-9s %s%s' % (s['addr'], 'internal' if s['internal'] else 'EXTERNAL',
                                        s['host'] or '(no IMAP host)', '' if s['pw'] else '  (NO PASSWORD: cannot be checked)'))
    if not any(not s['internal'] for s in seeds):
        out('Every seed is one of our own mailboxes. A pass proves routing, not inbox placement; '
            'add a free external Gmail/Outlook/Yahoo seed for a real test.')
    seeds = [s for s in seeds if s['pw'] and s['host']]
    if not seeds:
        out('No seed has a password and an IMAP host, so nothing could be checked.')
        return 2
    n = len(froms) * len(seeds)
    if not send:
        out('DRY RUN: %d probe(s) would be sent. Add --send to send them.' % n)
        return 0
    if smtp is None:
        import send_server as _SS
        smtp = _SS._smtp_send
    sent = []
    for fa in froms:
        for s in seeds:
            row = {'from': fa, 'seed': s['addr'], 'internal': s['internal'], 'folder': 'error', 'mid': ''}
            try:
                with _MG.gated(PROBE_GATE):
                    row['mid'] = smtp(login, pw, 'Alejandro Gonzalez', s['addr'], subj, body,
                                      from_addr=(fa if fa != login else None))
            except Exception as e:
                row['err'] = '%s: %s' % (type(e).__name__, str(e)[:120])
                out('  SEND FAILED %s -> %s: %s' % (fa, s['addr'], row['err']))
            sent.append(row)
            if pace:
                time.sleep(pace)
    pending = [r for r in sent if r['mid']]
    deadline = time.time() + wait
    while pending:
        for s in seeds:
            mine = [r for r in pending if r['seed'] == s['addr']]
            if not mine or not s['pw'] or not s['host']:
                continue
            try:
                M = (imap or _imap)(s)
            except Exception as e:
                for r in mine:
                    r['imap_err'] = str(e)[:120]
                continue
            for r in mine:
                r.pop('imap_err', None)
            try:
                for r in mine:
                    folder, hdr = locate(M, r['mid'], gmail=s['gmail'])
                    if folder != 'missing':
                        r['folder'] = folder
                        r['auth'] = analyze_headers(hdr) if hdr else {}
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
        pending = [r for r in pending if r['folder'] == 'error']
        if not pending or time.time() >= deadline:
            break
        time.sleep(10 if pace else 0)
    for r in pending:
        r['folder'] = 'error' if r.get('imap_err') else 'missing'
    try:
        st = write_status(sent, wait, all_froms=sending_addresses())
    except TornStatus as e:
        out('STATUS NOT WRITTEN: %s' % e)
        return 1
    out('')
    for r in sent:
        a = r.get('auth') or {}
        out('  %-38s -> %-30s %-10s auth %s' % (r['from'], r['seed'], r['folder'].upper(), a.get('verdict', '-')))
    paused = [a for a, row in st['by_from'].items() if row.get('verdict') == 'hold']
    out('')
    out('Paused: %s' % (', '.join(paused) if paused else 'none'))
    return 1 if paused else 0


# ------------------------------------------------------------------ CLI
def _print_score(sc):
    print('Content score %.1f (%s; warn at %.0f, high at %.0f). %d words.' % (
        sc['score'], sc['level'], WARN_SCORE, HIGH_SCORE, sc['words']))
    for i in sc['issues']:
        print('  +%-4.1f %-11s %s' % (i['w'], i['rule'], i['detail']))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('score')
    s.add_argument('--template', action='store_true')
    s.add_argument('--subject', default='')
    s.add_argument('--body-file', default='')
    s.add_argument('--from', dest='from_addr', default='')
    h = sub.add_parser('headers')
    h.add_argument('file')
    sub.add_parser('dns')
    p = sub.add_parser('placement')
    p.add_argument('--send', action='store_true')
    p.add_argument('--wait', type=int, default=180)
    p.add_argument('--from', dest='froms', action='append', default=[])
    sub.add_parser('status')
    a = ap.parse_args(argv)
    if a.cmd == 'score':
        if a.template:
            subj, body = sample_first_touch()
        elif a.body_file:
            subj, body = a.subject, open(a.body_file, encoding='utf-8').read()
        else:
            ap.error('score needs --template or --body-file')
            return 2
        sc = score_content(subj, body, a.from_addr)
        _print_score(sc)
        return 1 if sc['level'] == 'high' else 0
    if a.cmd == 'headers':
        r = analyze_headers(open(a.file, 'rb').read())
        print('\n'.join(headers_lines(r)))
        return 1 if r['verdict'] == 'fail' else 0
    if a.cmd == 'dns':
        bad = False
        for row in dns_report():
            print('%s\n  SPF   %s\n  DMARC %s\n  DKIM  %s' % (
                row['domain'], row['spf'] or '-', row['dmarc'] or '-',
                'google._domainkey present' if row['dkim'] else 'MISSING'))
            for n in row['notes']:
                print('  note: ' + n)
            bad = bad or not (row['spf'] and row['dmarc'] and row['dkim'])
        return 1 if bad else 0
    if a.cmd == 'placement':
        return run_placement(send=a.send, wait=a.wait, froms=[x.lower() for x in a.froms] or None)
    if a.cmd == 'status':
        st, err = load_status()
        if err:
            print('UNREADABLE: %s. Every outreach send holds until it parses or is deleted.' % err)
            return 1
        if st is None:
            print('No seed test has run. Nothing is paused%s.' % (
                ' (but DEALFLOW_PLACEMENT_REQUIRED=1, so every outreach send holds)'
                if _env_on('DEALFLOW_PLACEMENT_REQUIRED', False) else ''))
            return 0
        held = False
        for addr in sorted(set(sending_addresses()) | set(st['by_from'])):
            why = placement_hold(addr)
            held = held or bool(why)
            row = st['by_from'].get(addr) or {}
            print('%-40s %-6s %s' % (addr, 'HOLD' if why else 'ok',
                                     why or ('tested clean %s' % str(row.get('ts', ''))[:16] if row else 'never tested')))
        return 1 if held else 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
