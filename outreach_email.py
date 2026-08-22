#!/usr/bin/env python
"""outreach_email.py -- batch-send DealFlow outreach emails via Gmail SMTP + App Password.

The browser Morning Worker opens Gmail compose windows; a human has to press Send. That works
for 10-20/day but not for the 50/day cap and definitely not overnight. This is the CLI cousin:
same eligibility gates, same templates (EN + ES), same portfolio consolidation, same 24h
per-recipient dedupe -- but the send is a real SMTP call, so if the script says "sent", it went
out. mail_sent.json is the ledger.

SECURITY
--------
- Credentials come from `gmail.key` (gitignored), one line: `user@gmail.com:APP_PASSWORD`.
  The password is a 16-char Google App Password (myaccount.google.com -> Security -> App
  passwords). An ACCOUNT password will fail SMTP AUTH; use an App Password.
- Env var `GMAIL_APP_PASSWORD` overrides the file when set.
- The key is NEVER logged, NEVER echoed, NEVER written to mail_sent.json.
- Default is DRY-RUN. --send is required to actually SMTP. --test-to lets you smoke-test the
  whole path against your own inbox instead of a real owner.

USAGE
-----
    python outreach_email.py                            # dry-run: shows queue + would-send preview
    python outreach_email.py --limit 5                  # dry-run of top 5 only
    python outreach_email.py --lang es                  # Spanish body (default: English)
    python outreach_email.py --tier A                   # tier A only (default: A,B)
    python outreach_email.py --min-hours 48             # widen the recipient cooldown
    python outreach_email.py --test-to me@me.com --limit 1 --send   # smoke test to yourself
    python outreach_email.py --send                     # REAL SEND, top-N eligible

DAILY CAP: 50 real sends per calendar day (Gmail cold-mail practical ceiling; the primary
inbox eats the reputation damage if it's flapped). Counted from mail_sent.json for the current
UTC date; --send stops when the cap is hit even mid-batch.

PACING: a 2-3s jitter between sends looks less bursty than fire-hosing 50 in a minute.
"""
import argparse
import datetime
import glob
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'gmail.key')
SENDER_FILE = os.path.join(HERE, 'sender.json')
SENT_LEDGER = os.path.join(HERE, 'mail_sent.json')
OPTOUT_FILE = os.path.join(HERE, 'optouts.json')
PREVIEW_FILE = os.path.join(HERE, 'email_preview.html')

DAILY_MAX = 50
PACING_SEC_MIN = 2.0
PACING_SEC_MAX = 3.5

_COMPANY_RE = re.compile(
    r'\b(LLC|L\.?L\.?C|INC|CORP|CORPORATION|TRUST|LP|LLP|COMPANY|CO\.|ASSOC|ASSOCIATION|BANK|'
    r'HOLDINGS|PROPERTY|PROPERTIES|REALTY|REAL ESTATE|INVESTMENTS?|CAPITAL|FUND|ENTERPRISES|'
    r'GROUP|VENTURES|PARTNERS|SERVICING|MORTGAGE|REO|MANAGEMENT)\b', re.I)
_NAME_MARK_RE = re.compile(
    r'\b(H/E|H&W|W/E|ET\s*AL|ETAL|ETUX|ETVIR|TRUSTEES?|TRS?|REV(OCABLE)?|LIV(ING)?|JT(RS)?|'
    r'EST(ATE)?|LIFE\s*EST)\b', re.I)
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# ---------------------------------------------------------------- helpers (mirror outreach_mail)
def _g(r, *keys, default=''):
    for k in keys:
        v = r.get(k)
        if v not in (None, '', 0):
            return v
    return default


def _title_case(s):
    return re.sub(r'\b([a-z])', lambda m: m.group(1).upper(), str(s or '').lower())


def _clean_name_part(s):
    s = _NAME_MARK_RE.sub(' ', str(s or ''))
    s = s.split('&')[0]
    return re.sub(r'\s{2,}', ' ', s).strip(' ,')


def _owner_name(r):
    """LAST,FIRST -> First Last, title-cased. Matches the tracker's _ownerName()."""
    raw = str(_g(r, 'owners', 'oname', 'owner')).split(';')[0].strip()
    if not raw:
        return ''
    ci = raw.find(',')
    if ci == -1:
        return _title_case(_clean_name_part(raw))
    first = _clean_name_part(raw[ci + 1:])
    last = _clean_name_part(raw[:ci])
    return _title_case(re.sub(r'\s{2,}', ' ', (first + ' ' + last)).strip())


def _first_name(r):
    n = _owner_name(r)
    return n.split(' ')[0] if n else ''


def _primary_email(r):
    """First non-empty, syntactically-valid email in r.emails[], lowercased."""
    for e in (r.get('emails') or []):
        e = str(e or '').strip().lower()
        if _EMAIL_RE.match(e):
            return e
    return ''


def _case(r):
    return str(_g(r, 'case', 'Case #', 'cert')).strip()


def _tier(r):
    return str(r.get('tier') or '').upper()


def _days(r):
    try:
        return int(_g(r, 'days', 'days_to_auction', default=0))
    except Exception:
        return 0


def _sale_date(r):
    return str(_g(r, 'auction', 'AuctionDate')).strip()


def _plaintiff(r):
    return str(r.get('plaintiff') or '').strip()


def _is_tax_deed(r):
    return str(_g(r, 'st', 'sale_type')).upper() == 'TD'


def _is_company_owner(r):
    return bool(_COMPANY_RE.search(str(_g(r, 'owners', 'oname'))))


# ---------------------------------------------------------------- load side
def _load_credentials():
    """Returns (user_email, app_password) or (None, None) if unavailable. Never logs the pw."""
    env_pw = (os.environ.get('GMAIL_APP_PASSWORD') or '').strip()
    if os.path.exists(KEY_FILE):
        raw = open(KEY_FILE, encoding='utf-8').read().strip()
        if ':' in raw:
            user, pw = raw.split(':', 1)
            user = user.strip().lower()
            pw = pw.strip()
            if env_pw:
                pw = env_pw   # env wins if set
            if _EMAIL_RE.match(user) and pw:
                return user, pw
        elif env_pw:
            # file has just a user or password fragment; env supplies the rest
            if _EMAIL_RE.match(raw.lower()):
                return raw.lower(), env_pw
    if env_pw:
        # env-only path needs a --user arg on the CLI to work; caller handles that
        return None, env_pw
    return None, None


def _load_sender():
    if os.path.exists(SENDER_FILE):
        try:
            return json.load(open(SENDER_FILE, encoding='utf-8'))
        except Exception:
            pass
    return {}


def _load_leads():
    """leads_final.json + every *_leads.json, matching outreach_mail's merge order.

    CRITICAL MERGE: emails and phones live in skiptrace_results.json (keyed by case number), not
    on the raw lead rows. foreclosure_leads.make_tracker merges these at build time; we do the
    same here so an email-address filter has anything to match on. Without this step every lead
    reads as 'no primary email' and the batch queues zero recipients -- learned by running the
    CLI once and getting a 0-of-854 report."""
    leads = []
    mdf = os.path.join(HERE, 'leads_final.json')
    if os.path.exists(mdf):
        try:
            leads += json.load(open(mdf, encoding='utf-8'))
        except Exception:
            pass
    for xf in sorted(glob.glob(os.path.join(HERE, '*_leads.json'))):
        base = os.path.basename(xf)
        if base.startswith('_') or base in ('leads_raw.json',):
            continue
        try:
            leads += json.load(open(xf, encoding='utf-8'))
        except Exception:
            pass

    # skiptrace merge (emails + phones) -- key = case number in canonical form
    st_path = os.path.join(HERE, 'skiptrace_results.json')
    if os.path.exists(st_path):
        try:
            st = json.load(open(st_path, encoding='utf-8'))
        except Exception:
            st = {}
        for r in leads:
            key = _case(r)
            if not key:
                continue
            hit = st.get(key) or st.get(key.upper()) or st.get(key.lower())
            if not hit:
                continue
            # only overwrite if the lead has nothing yet -- respects any live-board patches
            if not (r.get('emails') or []):
                r['emails'] = list(hit.get('emails') or [])[:3]
            if not (r.get('phones') or []):
                r['phones'] = list(hit.get('phones') or [])[:3]

    # §362 ACTIVE-STAY MERGE — without this the stay gate below is DEAD CODE.
    # _eligible() tests r['saleBkAct'], which is the BAKED board field name. These rows come
    # straight off leads_final.json / *_leads.json, where sale_history.py writes the snake_case
    # 'sale_bk_active' instead — and the nightly scrape rewrites those files, so even that is
    # usually absent. Measured 2026-08-15 across all 1,456 loaded leads: saleBkAct on 0,
    # sale_bk_active on 0 — the gate could never fire even once, while the cache held 97 active
    # stays. Emailing someone under an automatic stay is a collection-contact violation, so this
    # reads the DURABLE cache and stamps BOTH spellings. Mapping is sale_history.py:246-250.
    try:
        _shc = json.load(open(os.path.join(HERE, 'sale_history_cache.json'), encoding='utf-8'))
    except Exception:
        _shc = {}
    if _shc:
        for r in leads:
            ent = _shc.get(_case(r) or '')
            if isinstance(ent, dict) and ent.get('a'):
                r['sale_bk_active'] = True
                r['saleBkAct'] = True
                r.setdefault('sale_bk_date', ent.get('bd', ''))
            if isinstance(ent, dict) and ent.get('sl') and not r.get('saleLift'):
                r['saleLift'] = ent['sl']
    return leads


def _load_optouts():
    """DO-NOT-CONTACT ledger, correctly UNWRAPPED. 2026-08-19 stress test CONFIRMED this was DEAD
    CODE: optouts.json is the envelope {_dealflow_notes, exported, device, notes:{...}} and this
    returned its TOP-LEVEL keys ({_dealflow_notes, exported, device, notes}) — never a case or an
    email — so `em in optouts` and `_case(r) in optouts` could never match, and a written STOP was
    followed by a fresh cold email. Every other consumer (foreclosure_leads.py:~2304) unwraps the
    envelope; this one did not. The `notes` dict is keyed by EITHER a case number OR '@'+email."""
    if not os.path.exists(OPTOUT_FILE):
        return set()
    try:
        data = json.load(open(OPTOUT_FILE, encoding='utf-8'))
    except Exception:
        return set()
    if isinstance(data, list):
        return {str(x).strip().lower().lstrip('@') for x in data}
    notes = data.get('notes') if isinstance(data, dict) else None
    if isinstance(notes, dict):
        return {str(k).strip().lower().lstrip('@') for k, v in notes.items() if v}
    return set()


def _load_ledger():
    if not os.path.exists(SENT_LEDGER):
        return []
    try:
        data = json.load(open(SENT_LEDGER, encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _append_ledger(entry):
    log = _load_ledger()
    log.append(entry)
    tmp = SENT_LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SENT_LEDGER)


def _recently_emailed_hours(ledger, addr, hours):
    """True if `addr` (lowercased) received an email inside the last `hours`. The ledger stores
    ISO 8601 timestamps -- read them, compare against now."""
    if not addr:
        return False
    addr = addr.strip().lower()
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    for e in ledger:
        if e.get('ch') != 'email':
            continue
        if str(e.get('to') or '').strip().lower() != addr:
            continue
        try:
            ts = datetime.datetime.fromisoformat(e.get('ts_utc') or '')
        except Exception:
            continue
        if ts >= cutoff:
            return True
    return False


def _sent_today_count(ledger):
    today = datetime.date.today().isoformat()
    return sum(1 for e in ledger
               if e.get('ch') == 'email' and str(e.get('d') or '') == today)


# ---------------------------------------------------------------- eligibility
_WORKER_HIST = None


def _worker_hist(ledger=None):
    """case -> {'sends': n, 'replied': bool}, from the DELIVERY LEDGER first and worker_notes second.

    The headless sender is COLD-ONLY: staged follow-ups belong to the morning worker, where
    confirmSend keeps every send honest. Without this guard the CLI fires the same cold email at a
    lead the worker already emailed -- or worse, at an owner mid-conversation.

    worker_notes.json alone was NOT enough, and this is not hypothetical. It is a backup of the
    BOARD's localStorage, so it only knows what some browser happened to tell it. On 2026-08-05
    this script sent 91 cold emails, 74 of them to cases already emailed, because those touches had
    never reached the file. mail_sent.json is the delivery record itself and cannot drift: a
    message_id came back or it did not. Union both sources, take the larger count.
    Missing/unreadable inputs degrade to empty (same posture as _load_optouts): the ledger's own
    per-address cooldown still applies."""
    global _WORKER_HIST
    if _WORKER_HIST is not None:
        return _WORKER_HIST
    out = {}
    for e in (ledger or []):
        # message_id gate: the ledger also records FAILURES (an 'error' row on an SMTP exception)
        # and those share ch='email'. A failed send reached nobody and must not retire a lead.
        if e.get('ch') != 'email' or not e.get('message_id'):
            continue
        c = str(e.get('case') or '').strip()
        if c:
            out.setdefault(c, {'sends': 0, 'replied': False})['sends'] += 1
    try:
        data = json.load(open(os.path.join(HERE, 'worker_notes.json'), encoding='utf-8'))
        for case, n in (data.get('notes') or {}).items():
            sends, replied = 0, False
            for t in (n.get('touches') or []):
                if (t.get('ch') or '') != 'email':
                    continue
                if 'replied' in str(t.get('out') or '').lower():
                    replied = True
                else:
                    sends += 1
            if sends or replied:
                # max(), not +=: a send recorded in BOTH stores is one email, not two.
                d = out.setdefault(str(case), {'sends': 0, 'replied': False})
                d['sends'] = max(d['sends'], sends)
                d['replied'] = d['replied'] or replied
    except Exception:
        pass          # keep whatever the ledger already gave us — it is the better source anyway
    _WORKER_HIST = out
    return out


def _eligible(r, ledger, optouts, min_hours):
    """Returns (ok:bool, why:str). Mirrors the tracker's _workerEligible + a few CLI extras."""
    if not _case(r):
        return False, 'no case id'
    w = _worker_hist(ledger).get(_case(r))
    if w:
        if w['replied']:
            return False, 'owner REPLIED - live thread, human only'
        if w['sends'] >= 1:
            return False, 'worker already emailed - follow-ups are the worker\'s job'
    if _is_company_owner(r):
        return False, 'company-owner'
    # Check BOTH spellings. Board-baked rows carry 'saleBkAct'; raw lead files and sale_history.py
    # carry 'sale_bk_active'. Testing only one silently disables the gate for the other source.
    if (r.get('saleBkAct') or r.get('sale_bk_active')) and not r.get('saleLift'):
        return False, 'active bankruptcy stay'
    if r.get('sibclaimed'):
        return False, 'sibling case sold'
    # opt-out is keyed by CASE or EMAIL in the ledger — check both (the case catches an owner who
    # opted out by phone/text with no email on the STOP record).
    if _case(r).strip().lower() in optouts:
        return False, 'opted out (case on DNC ledger)'
    em = _primary_email(r)
    if not em:
        return False, 'no primary email'
    if em.strip().lower() in optouts:
        return False, 'opted out'
    if _recently_emailed_hours(ledger, em, min_hours):
        return False, f'emailed within {min_hours}h'
    days = _days(r)
    if days and days < 0:
        return False, 'auction already passed'
    return True, ''


# ---------------------------------------------------------------- portfolio consolidation
def _group_by_recipient(leads):
    """Same shape as tracker's _workerQueue: same email -> ONE consolidated entry with the rest
    hanging off .portfolio. Sorts each group by soonest sale, best tier first."""
    tier_rank = {'A': 0, 'B': 1, 'C': 2}
    buckets = {}
    for r in leads:
        em = _primary_email(r)
        if not em:
            continue
        buckets.setdefault(em, []).append(r)
    grouped = []
    for em, rows in buckets.items():
        rows.sort(key=lambda r: (_days(r) if _days(r) > 0 else 99999,
                                 tier_rank.get(_tier(r), 3),
                                 -(int(r.get('filed') or 0))))
        head = dict(rows[0])
        head['_portfolio'] = rows[1:] if len(rows) > 1 else []
        head['_primary_email'] = em
        grouped.append(head)
    grouped.sort(key=lambda r: (_days(r) if _days(r) > 0 else 99999,
                                tier_rank.get(_tier(r), 3),
                                -(int(r.get('filed') or 0))))
    return grouped


# ---------------------------------------------------------------- composer
def _sig(snd):
    parts = [snd.get('name'), snd.get('title'), snd.get('llc'),
             ('Phone: ' + snd['phone']) if snd.get('phone') else '',
             ('Email: ' + snd['email']) if snd.get('email') else '',
             snd.get('addr'), snd.get('web')]
    return '\n'.join(x.strip() for x in parts if x and str(x).strip())


def _compose_single(r, snd, lang='en'):
    """Mirrors the tracker's genEmail() single-lead body."""
    owner = _owner_name(r) or 'Property Owner'
    first = _first_name(r) or owner
    addr = _g(r, 'addr', 'Address')
    dt = _sale_date(r)
    td = _is_tax_deed(r)
    plaintiff = _plaintiff(r)
    case_no = _case(r)
    case_tag_en = (f' (Certificate/Case No. {case_no})' if td else f' (Case No. {case_no})') if case_no else ''
    case_tag_es = (f' (Número de certificado/caso {case_no})' if td else f' (Caso Número {case_no})') if case_no else ''
    street = (addr.split(',')[0] or addr).strip()
    sig = _sig(snd)
    sN = snd.get('name') or '[YOUR NAME]'

    subj_en = f'Regarding your property at {street}'
    subj_es = f'Referente a su propiedad en {street}'

    # 2026-08-11 rewrite (Alejandro) -- mirrors tracker genEmail exactly (ASCII conventions:
    # -- for em dash, stripped accents). MSG identity everywhere ("local home buyer" is out),
    # the five-exits listicle is gone (options live in ONE flowing sentence), bodies ~40% shorter.
    # Kept: real case number, STOP line verbatim, sig, no rescue promises.
    sL = snd.get('llc') or '[YOUR COMPANY]'
    # "not a foreclosure-rescue company" is NOT optional padding — it is the affirmative disclaimer
    # the letters and the Lob mail have always carried and this module silently did not (added
    # 2026-08-22). FS 501.1377 turns on whether you hold yourself out as offering a service to
    # stop/delay/reverse a foreclosure for a fee; email is the highest-volume channel, so it was the
    # one surface making the denial only by omission. Keep this clause in both languages.
    intro_en = (f"I'm {sN} with {sL}. I'm not your lender, not the government, not a "
                f"foreclosure-rescue company, and not a lawyer.")
    intro_es = (f'Soy {sN}, de {sL}. No soy su prestamista, no soy del gobierno, no soy una '
                f'empresa de rescate de ejecuciones, y no soy abogado.')
    early = (not td) and (not dt)
    if early:
        # LP / no auction date. Without this branch the FC body rendered "a sale date of ." --
        # a blank where the date should be (the tracker grew this branch first; now mirrored).
        body_en = (
            f'Hi {first},\n\n{intro_en}\n\n'
            f'A foreclosure case was just filed against {addr}'
            f'{" by " + plaintiff if plaintiff else ""}{case_tag_en}. No auction date yet -- '
            f'which means right now you have the most options and the least pressure '
            f"you'll ever have.\n\n"
            f'Catch up the loan, rework it, sell on your terms, or fight the case -- they all '
            f"work better with time on the clock. That's the whole reason for this note.\n\n"
            f'If you want a straight rundown of where you stand, call or text me. No cost, '
            f'no obligation.\n\n{sig}\n\n'
            f'Reply STOP or ask me not to contact you again and I will honor it.'
        )
        body_es = (
            f'Hola {first},\n\n{intro_es}\n\n'
            f'Se acaba de presentar un caso de ejecucion hipotecaria contra {addr}'
            f'{" por parte de " + plaintiff if plaintiff else ""}{case_tag_es}. Todavia no hay '
            f'fecha de subasta -- o sea que ahora mismo usted tiene mas opciones y menos '
            f'presion que nunca.\n\n'
            f'Ponerse al dia con el prestamo, ajustarlo, vender en sus terminos, o pelear el '
            f'caso -- todo funciona mejor con tiempo en el reloj. Ese es el unico punto de '
            f'esta nota.\n\n'
            f'Si quiere un repaso directo de donde esta parado, llameme o mandeme un texto. '
            f'Sin costo, sin compromiso.\n\n{sig}\n\n'
            f'Responda STOP o pidame no contactarlo y lo respeto.'
        )
    elif td:
        body_en = (
            f'Hi {first},\n\n{intro_en}\n\n'
            f'County records show {addr} has a tax deed sale set for {dt}{case_tag_en} -- you '
            f"can verify that number yourself on the county's site.\n\n"
            f'Before that date you have three real paths: pay the back taxes and keep it, sell '
            f"before the date (we can buy as-is and close fast if that's the fit), or claim "
            f"the surplus if it sells for more than what's owed.\n\n"
            f'Want to know which one fits? Call or text me. No cost, and if another path beats '
            f"selling, I'll say so.\n\n{sig}\n\n"
            f'Reply STOP or ask me not to contact you again and I will honor it.'
        )
        body_es = (
            f'Hola {first},\n\n{intro_es}\n\n'
            f'Los registros del condado muestran que {addr} tiene subasta de tax deed el '
            f'{dt}{case_tag_es} -- usted mismo puede verificar ese numero en el sitio del '
            f'condado.\n\n'
            f'Antes de esa fecha tiene tres caminos reales: pagar los impuestos atrasados y '
            f'quedarse con la propiedad, vender antes de la fecha (podemos comprar tal cual y '
            f'cerrar rapido si eso le sirve), o reclamar el excedente si se vende por mas de '
            f'lo que se debe.\n\n'
            f'Quiere saber cual le conviene? Llameme o mandeme un texto. Sin costo, y si otro '
            f'camino es mejor que vender, se lo digo.\n\n{sig}\n\n'
            f'Responda STOP o pidame no contactarlo y lo respeto.'
        )
    else:
        plaint_bit_en = f' by {plaintiff}' if plaintiff else ''
        plaint_bit_es = f' por parte de {plaintiff}' if plaintiff else ''
        body_en = (
            f'Hi {first},\n\n{intro_en}\n\n'
            f'Public records show {addr} is in foreclosure{plaint_bit_en}{case_tag_en}, with a '
            f'sale date of {dt}. That case number is real -- look it up on the county '
            f"clerk's site. I include it so you know this isn't a mass mailer.\n\n"
            f'Most owners are never told they have real ways out: catch up the loan, rework it '
            f'with the bank, sell before the auction and keep your equity, or have an attorney '
            f'fight or pause the case. Every one of them works better with time left on the '
            f'clock.\n\n'
            f'If you want a straight rundown, call or text me. No cost, no obligation -- and '
            f"if selling isn't your best move, I'll tell you that straight.\n\n"
            f'{sig}\n\nReply STOP or ask me not to contact you again and I will honor it.'
        )
        body_es = (
            f'Hola {first},\n\n{intro_es}\n\n'
            f'Los registros publicos muestran que {addr} esta en ejecucion hipotecaria'
            f'{plaint_bit_es}{case_tag_es}, con fecha de subasta el {dt}. Ese numero de caso '
            f'es real -- puede verificarlo en el sitio del clerk del condado. Lo incluyo para '
            f'que sepa que esto no es correo masivo.\n\n'
            f'A la mayoria de los duenos nunca les explican que tienen salidas reales: ponerse '
            f'al dia con el prestamo, ajustarlo con el banco, vender antes de la subasta y '
            f'quedarse con su capital, o que un abogado pelee o pause el caso. Todas funcionan '
            f'mejor con tiempo en el reloj.\n\n'
            f'Si quiere un repaso directo, llameme o mandeme un texto. Sin costo, sin '
            f'compromiso -- y si vender no es su mejor opcion, se lo digo tal cual.\n\n'
            f'{sig}\n\nResponda STOP o pidame no contactarlo y lo respeto.'
        )
    return {
        'en': {'subj': subj_en, 'body': body_en},
        'es': {'subj': subj_es, 'body': body_es},
    }[lang]


def _compose_portfolio(head, siblings, snd, lang='en'):
    """Consolidated email covering head + siblings. Mirrors tracker's genPortfolioEmail()."""
    all_leads = [head] + siblings
    owner = _owner_name(head) or 'Property Owner'
    first = _first_name(head) or owner
    sig = _sig(snd)
    sN = snd.get('name') or '[YOUR NAME]'
    n = len(all_leads)

    def _line_en(r):
        a = r.get('addr') or ''
        p = _plaintiff(r)
        c = _case(r)
        d = _sale_date(r)
        dy = _days(r)
        when = (f' -- sale {d} ({dy} day{"" if dy == 1 else "s"})'
                if d else ' -- no auction date set yet')
        return f'  - {a}' + (f' (foreclosed by {p})' if p else '') + (f' [Case {c}]' if c else '') + when

    def _line_es(r):
        a = r.get('addr') or ''
        p = _plaintiff(r)
        c = _case(r)
        d = _sale_date(r)
        dy = _days(r)
        when = (f' -- subasta {d} ({dy} dia{"" if dy == 1 else "s"})'
                if d else ' -- sin fecha de subasta todavia')
        return f'  - {a}' + (f' (ejecutado por {p})' if p else '') + (f' [Caso {c}]' if c else '') + when

    subj_en = f'Regarding {n} properties in your name'
    subj_es = f'Referente a {n} propiedades a su nombre'
    # 2026-08-11 rewrite -- mirrors tracker genPortfolioEmail (MSG identity, no listicle, shorter).
    sL = snd.get('llc') or '[YOUR COMPANY]'
    body_en = (
        f"Hi {first},\n\nI'm {sN} with Miami Solutions Group. I'm not your lender, not the "
        f'government, not a foreclosure-rescue company, and not a lawyer.\n\n'
        f'Public records show {n} properties in your name are in foreclosure right now. Every '
        f"case number below is real -- you can look each one up on the county clerk's site:\n\n" +
        '\n'.join(_line_en(r) for r in all_leads) + '\n\n'
        f"I'm writing you once instead of once per property. Each one has its own way out -- "
        f'keep one, sell another, let a third go -- and it all works best as one '
        f'conversation.\n\n'
        f"If it'd help, I can walk the numbers on any of them. No cost, no obligation -- and "
        f"if selling isn't your best move on a given property, I'll tell you that "
        f'straight.\n\n{sig}\n\n'
        f'Reply STOP or ask me not to contact you again and I will honor it for every property above.'
    )
    body_es = (
        f'Hola {first},\n\nSoy {sN}, de Miami Solutions Group. No soy su prestamista, no soy '
        f'del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.\n\n'
        f'Los registros publicos muestran {n} propiedades a su nombre en ejecucion hipotecaria '
        f'ahora mismo. Cada numero de caso abajo es real -- puede verificarlos usted mismo en '
        f'el sitio del clerk del condado:\n\n' +
        '\n'.join(_line_es(r) for r in all_leads) + '\n\n'
        f'Le escribo una sola vez y no por cada propiedad. Cada una tiene su propia salida -- '
        f'quedarse con una, vender otra, dejar ir una tercera -- y todo se trabaja mejor en '
        f'una sola conversacion.\n\n'
        f'Si le sirve, puedo repasar los numeros de cualquiera de ellas. Sin costo, sin '
        f'compromiso -- y si vender no es su mejor opcion en alguna, se lo digo tal '
        f'cual.\n\n{sig}\n\n'
        f'Responda STOP o pidame no contactarlo y lo respetare para cada una de las propiedades arriba.'
    )
    return {
        'en': {'subj': subj_en, 'body': body_en},
        'es': {'subj': subj_es, 'body': body_es},
    }[lang]


# ---------------------------------------------------------------- SMTP
def _smtp_send(user, pw, from_display, to_addr, subj, body):
    """Sends a text/plain email via Gmail SMTP over SSL. Raises on failure.
    Returns (message_id, server_response)."""
    msg = EmailMessage()
    msg['From'] = f'{from_display} <{user}>' if from_display else user
    msg['To'] = to_addr
    msg['Subject'] = subj
    msg['Message-ID'] = make_msgid(domain=user.split('@', 1)[-1])
    msg['Date'] = formatdate(localtime=True)
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=30) as s:
        s.login(user, pw)
        resp = s.send_message(msg)
    return msg['Message-ID'], resp


def _write_preview(entries):
    """One HTML file with every queued email rendered as-would-be-sent."""
    def esc(s):
        return (str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    parts = ['<!doctype html><meta charset="utf-8"><title>Email queue preview</title>',
             '<style>body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;'
             'max-width:780px;margin:0 auto;padding:24px;color:#0e1b33}'
             '.card{border:1px solid #d5dae4;border-radius:8px;padding:14px 16px;margin:0 0 14px}'
             '.h{font-weight:800;color:#0B1730}.mut{color:#6b7280}'
             '.body{white-space:pre-wrap;background:#fbfaf6;border:1px solid #eef;padding:12px;'
             'border-radius:6px;margin-top:8px;font:13.5px/1.55 Georgia,serif}'
             '.pfchip{background:#f2ebfa;color:#452471;padding:3px 8px;border-radius:5px;'
             'font-size:11px;font-weight:700;margin-left:8px}</style>']
    parts.append(f'<h1>Email queue &mdash; {len(entries)} recipient(s)</h1>')
    for i, e in enumerate(entries, 1):
        chip = (f'<span class="pfchip">portfolio {len(e["portfolio"]) + 1}</span>'
                if e.get('portfolio') else '')
        parts.append(
            f'<div class="card"><div class="h">#{i} &mdash; {esc(e["owner"])}{chip}</div>'
            f'<div class="mut">to <b>{esc(e["to"])}</b> &middot; case {esc(e["case"])} '
            f'&middot; sale {esc(e["sale"])}</div>'
            f'<div style="margin-top:8px"><b>Subject:</b> {esc(e["subj"])}</div>'
            f'<div class="body">{esc(e["body"])}</div></div>'
        )
    open(PREVIEW_FILE, 'w', encoding='utf-8').write('\n'.join(parts))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tier', default='A,B',
                    help='comma tiers (default A,B; "all" for every tier)')
    ap.add_argument('--lang', choices=('en', 'es'), default='en')
    ap.add_argument('--limit', type=int, default=25,
                    help='cap the batch (0 = up to DAILY_MAX). Default 25.')
    ap.add_argument('--min-hours', type=int, default=24,
                    help='per-recipient cooldown in hours (default 24)')
    ap.add_argument('--test-to', default='',
                    help='send every email in the batch to THIS address instead of the owner '
                         '(smoke test the pipeline without hitting real people)')
    ap.add_argument('--send', action='store_true',
                    help='ACTUALLY send via SMTP (default is dry-run)')
    args = ap.parse_args()

    # ---- load
    creds = _load_credentials()
    snd = _load_sender()
    leads = _load_leads()
    optouts = _load_optouts()
    ledger = _load_ledger()
    sent_today = _sent_today_count(ledger)
    room = max(0, DAILY_MAX - sent_today)

    if args.limit and args.limit > 0:
        room = min(room, args.limit)

    tiers = None if args.tier.lower() == 'all' else {t.strip().upper() for t in args.tier.split(',') if t.strip()}

    # ---- filter + group
    if tiers:
        leads = [r for r in leads if _tier(r) in tiers]

    def _keep(r):
        ok, why = _eligible(r, ledger, optouts, args.min_hours)
        _keep.reasons[why if not ok else '_pass'] = _keep.reasons.get(why if not ok else '_pass', 0) + 1
        return ok
    _keep.reasons = {}
    eligible = [r for r in leads if _keep(r)]

    grouped = _group_by_recipient(eligible)
    grouped = grouped[:room] if room > 0 else grouped

    # ---- compose
    entries = []
    for head in grouped:
        siblings = head.get('_portfolio') or []
        msg = (_compose_portfolio(head, siblings, snd, args.lang)
               if siblings else _compose_single(head, snd, args.lang))
        entries.append({
            'to': args.test_to.strip().lower() if args.test_to else head['_primary_email'],
            'owner': _owner_name(head) or '?',
            'case': _case(head),
            'sale': _sale_date(head) or '(none)',
            'subj': msg['subj'],
            'body': msg['body'],
            'portfolio': [_case(x) for x in siblings],
            'lang': args.lang,
            '_actual_owner_email': head['_primary_email'],
        })

    # ---- report
    print(f'\n=== outreach_email ({"SEND" if args.send else "DRY-RUN"}) ===')
    print(f'  loaded          {len(leads):5}')
    print(f'  eligible        {len(eligible):5}')
    print(f'  portfolio-fold  {len(eligible) - len(grouped):5} rows folded into peers')
    print(f'  queued          {len(entries):5}')
    print(f'  daily-cap room  {room:5}  (already sent today: {sent_today}, cap {DAILY_MAX})')
    if args.test_to:
        print(f'  !! test mode: all sends will be re-routed to {args.test_to}')
    print()
    print('  excluded:')
    for why, n in sorted(_keep.reasons.items(), key=lambda kv: -kv[1]):
        if why != '_pass':
            print(f'    {n:5}  {why}')

    if entries:
        print('\n  top of queue:')
        for e in entries[:10]:
            pf = f' [+{len(e["portfolio"])}]' if e['portfolio'] else ''
            print(f'    -> {e["to"]:35}  {e["owner"][:26]:26} case {e["case"][:22]:22} sale {e["sale"]:12}{pf}')

    _write_preview(entries)
    print(f'\n  preview -> {PREVIEW_FILE}')

    # ---- send
    if not args.send:
        print('\nDRY RUN only. Re-run with --send to actually SMTP.')
        return 0

    if not entries:
        print('\nNothing to send.')
        return 0

    user, pw = creds
    if not (user and pw):
        print('\nSMTP send refused: no gmail.key credentials.')
        print(f'  Create {KEY_FILE} with one line:  you@gmail.com:APP_PASSWORD')
        print('  APP_PASSWORD is 16 chars from myaccount.google.com -> Security -> App passwords.')
        return 2

    from_display = (snd.get('name') or '').strip()
    ok, fail = 0, 0
    print(f'\nSending as {user}  (display: "{from_display or user}")')
    for i, e in enumerate(entries, 1):
        try:
            mid, resp = _smtp_send(user, pw, from_display, e['to'], e['subj'], e['body'])
            _append_ledger({
                'd': datetime.date.today().isoformat(),
                'ts_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'ch': 'email',
                'from': user,
                'to': e['to'],
                'owner': e['owner'],
                'case': e['case'],
                'subj': e['subj'],
                'lang': e['lang'],
                'portfolio': e['portfolio'],
                'message_id': mid,
                'smtp_resp': str(resp),
                'test_mode': bool(args.test_to),
                'body_len': len(e['body']),
            })
            ok += 1
            print(f'  [{i:3}/{len(entries)}] SENT -> {e["to"]}  ({e["owner"][:30]})  id={mid.split("@")[0][:16]}')
        except Exception as ex:
            fail += 1
            _append_ledger({
                'd': datetime.date.today().isoformat(),
                'ts_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'ch': 'email',
                'from': user,
                'to': e['to'],
                'owner': e['owner'],
                'case': e['case'],
                'error': str(ex)[:200],
                'test_mode': bool(args.test_to),
            })
            print(f'  [{i:3}/{len(entries)}] FAIL -> {e["to"]}  {ex}')
            # If auth failed we won't recover on the next one; bail early.
            if 'AUTH' in str(ex).upper() or 'authenticate' in str(ex).lower():
                print('  Aborting: SMTP AUTH failed -- App Password wrong or 2FA off.')
                break
        # pacing between sends
        if i < len(entries):
            time.sleep(random.uniform(PACING_SEC_MIN, PACING_SEC_MAX))

    print(f'\n== done: {ok} sent, {fail} failed ==')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
