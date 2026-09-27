#!/usr/bin/env python
"""notice_letter.py -- logic behind `outreach_mail.py --variant notice` (EN front / ES back, duplex).

The LOOK and the WORDS live in notice_letter_template.py, which is a PLACEHOLDER until the new design
lands. This file holds only what does not depend on the design:

  * the hard refusal: no letter is built -- not a preview, not a PDF, not a send -- unless a
    MAIL-ONLY phone number, call hours, and a return address / PMB are configured. There is NO
    fallback to the main line (786) 631-1823, to sender.json, or to any apartment address.
  * the per-lead fields (owner, property address + city/ZIP, lender, case, sale date, mail date,
    reference BSG-<case>) read from the lead record the same way the default letter reads them.
  * the county gate: Miami-Dade only by default. Broward / Palm Beach only with an explicit
    --notice-include-county, and even then every existing hold in build_selection() (bankruptcy
    stay, federal bankruptcy check, suppression/opt-out, diligence, already-mailed) still applies,
    because this gate runs BEFORE build_selection and never replaces it.
  * the duplex two-page document: page 1 English, page 2 Spanish.

CONFIG (never committed, never printed):
    BSG_NOTICE_PHONE            mail-only phone number, e.g. (305) 555-0100
    BSG_NOTICE_CALL_HOURS       e.g. Mon-Sat 9am-7pm
    BSG_NOTICE_RETURN_ADDRESS   lines separated by '|' or newlines, ending in 'City, FL 33xxx',
                                e.g. '1234 Example Ave Ste 100 PMB 000 | Miami, FL 33100'
  or the same keys (phone / call_hours / return_address) in the gitignored notice_letter.local.json.
  Environment variables win over the file. BSG_NOTICE_LOCAL_CONFIG may point at another file.
"""
import datetime
import html
import json
import os
import re

import notice_letter_template as TPL

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_CONFIG = os.path.join(HERE, 'notice_letter.local.json')

ENV_PHONE = 'BSG_NOTICE_PHONE'
ENV_HOURS = 'BSG_NOTICE_CALL_HOURS'
ENV_RETURN = 'BSG_NOTICE_RETURN_ADDRESS'
_KEYS = (('phone', ENV_PHONE), ('call_hours', ENV_HOURS), ('return_address', ENV_RETURN))

# The published main line. The notice letter must carry a separate mail-only number so response to
# this piece is measurable and the main line is not printed on it. Refused, not just "not defaulted".
MAIN_LINE_DIGITS = '7866311823'

# Duplex: EN on the front of the sheet, ES on the back. Click2Mail's value for two-sided printing.
C2M_PRINT_OPTION = 'Printing both sides'

DEFAULT_COUNTIES = ('MIAMI-DADE',)
OPTIONAL_COUNTIES = {'broward': 'BROWARD', 'palm-beach': 'PALM BEACH'}

REQUIRED_LEAD_FIELDS = ('owner', 'prop_addr', 'plaintiff', 'case', 'sale')

_APT_RE = re.compile(r'\b(APT|APARTMENT)\b|#\s*\d', re.I)

_MONTHS_ES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
              'septiembre', 'octubre', 'noviembre', 'diciembre')
_MONTHS_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
              'September', 'October', 'November', 'December')


class NoticeConfigError(SystemExit):
    """Raised when the notice letter may not be built. A SystemExit with a non-zero code so a CLI
    run stops cold; the message names the missing SETTINGS, never their values."""

    def __init__(self, msg):
        super().__init__(2)
        self.msg = msg

    def __str__(self):
        return self.msg


# ---- config -------------------------------------------------------------------------------------

def _read_local(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path, encoding='utf-8-sig'))
    except Exception:
        raise NoticeConfigError('notice letter: %s is not valid JSON; fix it or delete it.'
                                % os.path.basename(path))
    return d if isinstance(d, dict) else {}


def return_lines(raw):
    return [ln.strip() for ln in re.split(r'[|\n]', str(raw or '')) if ln.strip()]


def load_config(env=None, local_path=None):
    """Return {'phone','call_hours','return_address'} or raise NoticeConfigError.

    Validation (all fail closed):
      * each of the three must be set and non-blank;
      * the phone must be a 10-digit US number and must NOT be the main line;
      * the return address must end in 'City, ST 12345' (parseable, like any Lob/C2M address)
        and must not look like an apartment (APT / APARTMENT / #12)."""
    env = os.environ if env is None else env
    # BSG_NOTICE_LOCAL_CONFIG points at a different file (tests use it to prove the refusal on a
    # machine that has a real notice_letter.local.json).
    local_path = local_path or env.get('BSG_NOTICE_LOCAL_CONFIG') or LOCAL_CONFIG
    local = _read_local(local_path)
    cfg, missing = {}, []
    for key, var in _KEYS:
        v = str(env.get(var) or local.get(key) or '').strip()
        if not v:
            missing.append('%s (or "%s" in %s)' % (var, key, os.path.basename(local_path or LOCAL_CONFIG)))
        cfg[key] = v
    if missing:
        raise NoticeConfigError(
            'REFUSED: the notice letter needs a mail-only phone number, call hours and a return '
            'address / PMB. Not set: ' + '; '.join(missing) + '. Nothing was built.')
    digits = re.sub(r'\D', '', cfg['phone'])
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        raise NoticeConfigError('REFUSED: %s is not a 10-digit phone number. Nothing was built.' % ENV_PHONE)
    if digits == MAIN_LINE_DIGITS:
        raise NoticeConfigError('REFUSED: %s is the main line; the notice letter needs its own '
                                'mail-only number. Nothing was built.' % ENV_PHONE)
    lines = return_lines(cfg['return_address'])
    if _APT_RE.search(' '.join(lines)):
        raise NoticeConfigError('REFUSED: %s looks like an apartment address; use the PMB / box. '
                                'Nothing was built.' % ENV_RETURN)
    if not parse_return(cfg['return_address']):
        raise NoticeConfigError('REFUSED: %s must end with "City, ST 12345". Nothing was built.' % ENV_RETURN)
    return cfg


def parse_return(raw):
    """The configured return address as outreach_mail.parse_address() parts, or None."""
    import outreach_mail as OM
    lines = return_lines(raw)
    if not lines:
        return None
    return OM.parse_address(', '.join(lines))


# ---- county gate --------------------------------------------------------------------------------

def county_of(r):
    """'MIAMI-DADE' / 'BROWARD' / 'PALM BEACH' / '' -- the lead's own county tag, else the county the
    case number's dialect encodes (diligence_flags.county_of). '' (unknown) is never mailed."""
    c = ''
    try:
        import diligence_flags as DF
        c = DF.county_of(r) or ''
    except Exception:
        c = str(r.get('county') or '').upper().strip()
        c = 'PALM BEACH' if c.startswith('PALM') else c
    if not c:
        # leads_final.json (the Miami-Dade file) predates the county tag; its case numbers carry the
        # Miami-Dade stem ('2099-000123-CA-01'), which stay_gate.case_stem() recognises.
        try:
            import stay_gate as SG
            import outreach_mail as OM
            if SG.case_stem(OM._case(r)):
                c = 'MIAMI-DADE'
        except Exception:
            pass
    return c


def allowed_counties(include=()):
    out = set(DEFAULT_COUNTIES)
    for name in include or ():
        key = str(name).strip().lower().replace('_', '-').replace(' ', '-')
        if key in ('palmbeach', 'pb'):
            key = 'palm-beach'
        if key not in OPTIONAL_COUNTIES:
            raise NoticeConfigError('notice letter: unknown county %r (choose from: %s)'
                                    % (name, ', '.join(sorted(OPTIONAL_COUNTIES))))
        out.add(OPTIONAL_COUNTIES[key])
    return out


def filter_counties(leads, include=()):
    """(kept_leads, Counter of skip reasons). Runs BEFORE outreach_mail.build_selection(), which
    still applies every existing hold to whatever is kept."""
    from collections import Counter
    allow = allowed_counties(include)
    kept, skips = [], Counter()
    for r in leads:
        c = county_of(r)
        if not c:
            skips['notice:county-unknown'] += 1
        elif c not in allow:
            skips['notice:county-excluded(%s)' % c] += 1
        else:
            kept.append(r)
    return kept, skips


# ---- per-lead fields ----------------------------------------------------------------------------

def _parse_date(s):
    s = str(s or '').strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except Exception:
            continue
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', s)
    if m:
        try:
            return datetime.date.fromisoformat(m.group(1))
        except Exception:
            pass
    return None


def fmt_date(d, lang):
    if lang == 'es':
        return '%d de %s de %d' % (d.day, _MONTHS_ES[d.month - 1], d.year)
    return '%s %d, %d' % (_MONTHS_EN[d.month - 1], d.day, d.year)


def lead_fields(r, today=None):
    """Raw (unescaped) per-lead values, read the way build_letter_html() reads them."""
    import outreach_mail as OM
    case = OM._case(r)
    prop = str(OM._g(r, 'addr', 'Address')).strip()
    city = str(OM._g(r, 'city')).strip()
    zipc = str(OM._g(r, 'zip')).strip()[:5]
    city_zip = ''
    if city or zipc:
        city_zip = ((city.title() + ', FL') if city else 'FL') + ((' ' + zipc) if zipc else '')
    mail = OM.parse_address(OM._mailing(r)) or {}
    mail_city_zip = ('%s, %s %s' % (mail.get('address_city', '').title(), mail.get('address_state', ''),
                                    mail.get('address_zip', ''))) if mail else ''
    return {
        'owner': OM._owner_name(r),
        'prop_addr': prop,
        'prop_city_zip': city_zip,
        'mail_line1': mail.get('address_line1', ''),
        'mail_city_zip': mail_city_zip,
        'plaintiff': str(OM._g(r, 'plaintiff')).strip(),
        'case': case,
        'sale': str(OM._g(r, 'auction', 'AuctionDate')).strip(),
        'ref': ('BSG-' + case) if case else '',
        'today': today or datetime.date.today(),
    }


def missing_fields(r):
    f = lead_fields(r)
    return [k for k in REQUIRED_LEAD_FIELDS if not f.get(k)]


def filter_complete(queue):
    """Drop (lead, parsed_addr) pairs missing a field the letter states as fact. A letter with a
    hole in it is worse than no letter (see mail_guard.py)."""
    from collections import Counter
    kept, skips = [], Counter()
    for item in queue:
        miss = missing_fields(item[0])
        if miss:
            skips['notice:missing(%s)' % ','.join(miss)] += 1
        else:
            kept.append(item)
    return kept, skips


# ---- rendering ----------------------------------------------------------------------------------

def _page(lang, f, cfg):
    e = html.escape
    sale_d = _parse_date(f['sale'])
    sale = fmt_date(sale_d, lang) if sale_d else f['sale']
    subs = {'ADDR': e(f['prop_addr']), 'PLAINTIFF': e(f['plaintiff']), 'CASE': e(f['case']),
            'SALE': e(sale), 'HOURS': e(cfg['call_hours'])}
    fields = {'lang': lang, 'owner': e(f['owner']), 'prop_addr': e(f['prop_addr']),
              'prop_city_zip': e(f['prop_city_zip']), 'prop_city_zip_sep': ', ' if f['prop_city_zip'] else '',
              'mail_line1': e(f['mail_line1']), 'mail_city_zip': e(f['mail_city_zip']),
              'plaintiff': e(f['plaintiff']), 'case': e(f['case']), 'sale': e(sale),
              'date': e(fmt_date(f['today'], lang)), 'ref': e(f['ref']),
              'phone': e(cfg['phone']), 'hours': e(cfg['call_hours']),
              'ret_html': '<br>'.join(e(x) for x in return_lines(cfg['return_address']))}
    for k, v in TPL.STRINGS[lang].items():
        s = v
        for sk, sv in subs.items():
            s = s.replace('{%s}' % sk, sv)
        fields['t_' + k] = s
    return TPL.PAGE.format(**fields)


def build_notice_html(r, snd=None, lang=None, cfg=None, today=None):
    """Two-page HTML for one lead: page 1 English, page 2 Spanish. `snd` and `lang` are accepted so
    this fits outreach_mail's builder signature; the notice letter never reads sender.json and is
    always bilingual. `cfg` defaults to load_config(), which refuses when anything is missing."""
    cfg = cfg if cfg is not None else load_config()
    f = lead_fields(r, today=today)
    pages = _page('en', f, cfg) + '\n' + _page('es', f, cfg)
    return TPL.DOCUMENT.format(title=html.escape('Notice ' + f['ref']), css=TPL.CSS, pages=pages)


def render_pdf(html_doc, out_pdf):
    """Local PDF render through the same Chrome path click2mail.py uses. No network, no vendor."""
    import click2mail as C
    return C.html_to_pdf(html_doc, out_pdf)
