#!/usr/bin/env python
"""bsg_mail_variants.py -- three mail pieces from the handwrite pool, split evenly and measured.

WHAT IT BUILDS
The same pool as bsg_mail_campaign's handwrite book (pool(): opt-out ledger, stay check, entities,
estates, low confidence), then ONE print-ready PDF, one piece per US Letter sheet, English and
Spanish on every sheet:

  A  the existing short note, bsg_mail_campaign.letter(), word for word (the control arm)
  B  a calendar page: the month of the sale date with that day circled
  C  "your file": the public-record facts the data carries, then three options in plain words

plus a private page per homeowner (encrypted with a short per-lead code, written under DEALFLOW_DIR,
never published by this module) and mail_variants.json, the assignment ledger.

WHAT IT NEVER DOES
  * It never marks anything as mailed and never touches mail_route_printed.json. The ledger records
    which arm a case was assigned to, nothing else, and carries no name and no address.
  * It prints no dollar figure. None of the three pieces has a place for one.
  * It never writes compliance text. The three MARS sentences come from disclaimer.mars_part(), the
    opt-out lines from outreach_copy, and every piece is checked for both before a file is written.
  * It never names a court event the data does not carry. A date that does not parse is no date: the
    lead gets no event line and no calendar, it does not get a guessed one.
  * QR codes print only with --with-qr, because the pages are not live until someone publishes them.

READING DATA FROM ANOTHER FOLDER
--data-dir points the READS at another checkout (a worktree's tracked lead files are older than the
operator's). The inputs are copied to a temp folder first, each one read twice-stat'd and parsed, so
a file that is being rewritten stops the run with a clear message instead of a partial letter run.

Usage:
  python bsg_mail_variants.py --days 30 --limit 90 --signer-phone "(305) 555-0100"
  python bsg_mail_variants.py --days 30 --limit 90 --data-dir C:/path/to/operator/checkout
  python bsg_mail_variants.py --trigger-days 7          # only leads with a case event in the last 7 days
  python bsg_mail_variants.py --with-qr --page-base-url https://example.org/p/
  python bsg_mail_variants.py --sample                  # invented people, BSG_Mail_Variants_SAMPLE.pdf
"""
import argparse
import base64
import calendar
import datetime as dt
import glob
import hashlib
import html as H
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time

import bsg_mail_campaign as M
import disclaimer as D
import outreach_copy as OC
import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))

VARIANTS = ('A', 'B', 'C')
VARIANT_NAME = {'A': 'short note', 'B': 'calendar page', 'C': 'your file'}
LEDGER = 'mail_variants.json'
PAGES_DIR = 'mail_pages'
MARS_KEYS = ('govt', 'may_agree', 'may_stop')          # the three federally mandated sentences
ACCENT = '#c8102e'                                     # the URGENT letter's red (outreach_mail jesse)
BAND_EDGE = '#7a0a1c'
DARK = '#1A1A1A'
INK = '#1b1f27'
VOICE_EXT = ('.mp3', '.m4a', '.wav', '.ogg')
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'      # no I, L, O, 0, 1: read off paper, typed on a phone

# Lead files read from the data folder. The first three are the pool's own inputs and must be there.
REQUIRED_INPUTS = ('lp_addresses.json', 'lis_pendens.json', 'optouts.json')
OPTIONAL_INPUTS = ('sale_history_cache.json', 'pacer_stay_cache.json', 'pacer_newfiler_hits.json',
                   'leads_final.json', 'judgment_dates.json')
_SKIP_LEADS = ('leads_raw.json', 'balloon_leads.json')


class DataError(Exception):
    """An input file is missing, half-written or not what it should be. Nothing is built."""


# ════════════════════════════ reading the lead data ════════════════════════════
def _stable_read(path, tries=3, wait=2.0):
    """Bytes of a COMPLETE json file, or DataError. A refresh rewrites these files in place; a read
    that lands in the middle of a write must stop the run, never shorten it."""
    name, last = os.path.basename(path), 'could not be read'
    for n in range(tries):
        if n:
            time.sleep(wait)
        try:
            s1 = os.stat(path)
            with open(path, 'rb') as fh:
                raw = fh.read()
            s2 = os.stat(path)
        except OSError as e:
            last = 'could not be read (%s)' % (e.strerror or e)
            continue
        if (s1.st_size, s1.st_mtime_ns) != (s2.st_size, s2.st_mtime_ns) or len(raw) != s2.st_size:
            last = 'changed while it was being read'
            continue
        try:
            json.loads(raw.decode('utf-8'))
        except ValueError as e:
            last = 'is not complete JSON (%s)' % str(e)[:80]
            continue
        return raw
    raise DataError('%s %s after %d tries. It is probably being rewritten by a refresh. Nothing was '
                    'built; run again in a minute.' % (name, last, tries))


def lead_files(data_dir):
    """Every <county>_leads.json the board merges, minus the lanes that never get a homeowner letter."""
    out = []
    for p in sorted(glob.glob(os.path.join(data_dir, '*_leads.json'))):
        b = os.path.basename(p)
        if b.startswith('_') or b in _SKIP_LEADS:
            continue
        out.append(b)
    return out


def snapshot(data_dir, tries=3, wait=2.0):
    """Copy the inputs to a private temp folder and return it. Caller deletes it."""
    if not os.path.isdir(data_dir):
        raise DataError('data folder %r does not exist' % (data_dir,))
    for name in REQUIRED_INPUTS:
        if not os.path.exists(os.path.join(data_dir, name)):
            raise DataError('%s is missing from %s. Nothing was built.' % (name, data_dir))
    snap = tempfile.mkdtemp(prefix='mailvariants_')
    try:
        for name in list(REQUIRED_INPUTS) + list(OPTIONAL_INPUTS) + lead_files(data_dir):
            src = os.path.join(data_dir, name)
            if not os.path.exists(src):
                continue
            with open(os.path.join(snap, name), 'wb') as fh:
                fh.write(_stable_read(src, tries=tries, wait=wait))
    except Exception:
        shutil.rmtree(snap, ignore_errors=True)
        raise
    return snap


def _json(folder, name, default):
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def _rows(d):
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        inner = d.get('rows') if isinstance(d.get('rows'), list) else list(d.values())
        return [x for x in inner if isinstance(x, dict)]
    return []


# ════════════════════════════ dates and court events ════════════════════════════
def parse_date(s, today=None):
    """A real calendar date or None. Never a guess: an empty, malformed or absurd value is None, and
    None means 'the record does not say', which prints nothing."""
    v = str(s or '').strip().split(' ')[0].split('T')[0]
    if not v:
        return None
    for f in ('%m/%d/%Y', '%Y-%m-%d'):
        try:
            d = dt.datetime.strptime(v, f).date()
        except ValueError:
            continue
        today = today or dt.date.today()
        if d.year < 2000 or d > today + dt.timedelta(days=3 * 365):
            return None
        return d
    return None


def facts_index(folder):
    """What the data folder verifiably says about each case, keyed by the exact case id.

    filed     lis_pendens.json `date`      the day the notice of the case was recorded
    sale      a lead row's sale date       leads_final.json `AuctionDate`, <county>_leads.json `auction`
    judgment  judgment_dates.json `d`      the day a final judgment was entered
    county    lp_addresses.json `county`
    closed    <county>_leads.json          lpDismissed / lpClosed: the case is over, no piece is built
    Raw strings here; lead_facts() parses them and drops what does not parse."""
    idx = {'filed': {}, 'sale': {}, 'judgment': {}, 'county': {}, 'closed': set()}
    for x in _rows(_json(folder, 'lis_pendens.json', [])):
        c, d = str(x.get('case') or ''), str(x.get('date') or '')
        if c and d:
            idx['filed'][c] = d
            if x.get('county') and c not in idx['county']:
                idx['county'][c] = str(x['county'])
    for x in _rows(_json(folder, 'lp_addresses.json', [])):
        c = str(x.get('case') or '')
        if c and x.get('county'):
            idx['county'][c] = str(x['county'])
    for x in _rows(_json(folder, 'leads_final.json', [])):
        c = str(x.get('Case #') or x.get('case') or '')
        if c and x.get('AuctionDate'):
            idx['sale'][c] = str(x['AuctionDate'])
    for name in lead_files(folder):
        for x in _rows(_json(folder, name, [])):
            c = str(x.get('case') or '')
            if not c:
                continue
            for k in ('auction', 'saleDate', 'sale_date', 'AuctionDate'):
                if x.get(k):
                    idx['sale'].setdefault(c, str(x[k]))
                    break
            if x.get('lpDismissed') or x.get('lpClosed'):
                idx['closed'].add(c)
            if x.get('county') and c not in idx['county']:
                idx['county'][c] = str(x['county'])
    jd = _json(folder, 'judgment_dates.json', {})
    if isinstance(jd, dict):
        for c, v in jd.items():
            if isinstance(v, dict) and v.get('d'):
                idx['judgment'][str(c)] = str(v['d'])
    return idx


def lead_facts(case, idx, today):
    """The printable facts for one case. A sale date counts only when it parses AND is still ahead;
    a filing or judgment date only when it parses and is not in the future."""
    filed = parse_date(idx['filed'].get(case), today)
    sale = parse_date(idx['sale'].get(case), today)
    judg = parse_date(idx['judgment'].get(case), today)
    if filed and filed > today:
        filed = None
    if judg and judg > today:
        judg = None
    if sale and sale <= today:
        sale = None
    county = re.sub(r'[^A-Za-z .\-]', '', str(idx['county'].get(case) or '')).strip().title()
    county = {'Miami Dade': 'Miami-Dade', 'Miamidade': 'Miami-Dade', 'Palmbeach': 'Palm Beach',
              'Md': 'Miami-Dade', 'Pb': 'Palm Beach'}.get(county, county)
    return {'case': case, 'county': county, 'filed': filed, 'sale': sale, 'judgment': judg,
            'closed': case in idx['closed']}


# The court events this module can name, and the only ones. Each needs a real date in the past.
EVENTS = ('judgment', 'filed')


def recent_event(facts, days, today):
    """(kind, date, days_ago) for the newest supported event inside the window, else None."""
    best = None
    for kind in EVENTS:
        d = facts.get(kind)
        if not d:
            continue
        ago = (today - d).days
        if 0 <= ago <= days and (best is None or ago < best[2]):
            best = (kind, d, ago)
    return best


_MONTH_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
             'October', 'November', 'December')
_MONTH_ES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
             'octubre', 'noviembre', 'diciembre')
_DAY_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_DAY_ES = ('lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo')
_DOW_HEAD = (('Sun', 'dom'), ('Mon', 'lun'), ('Tue', 'mar'), ('Wed', 'mié'), ('Thu', 'jue'),
             ('Fri', 'vie'), ('Sat', 'sáb'))


def date_en(d, weekday=False):
    s = '%s %d, %d' % (_MONTH_EN[d.month - 1], d.day, d.year)
    return '%s, %s' % (_DAY_EN[d.weekday()], s) if weekday else s


def date_es(d, weekday=False):
    s = '%d de %s de %d' % (d.day, _MONTH_ES[d.month - 1], d.year)
    return '%s, %s' % (_DAY_ES[d.weekday()], s) if weekday else s


def _n(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


def event_lines(facts, today, kind=None):
    """(english, spanish) plain sentences for one court event, or None. Only what the record shows:
    the date, how long ago, and what comes next. No deadline is computed that the data cannot give."""
    kind = kind or ('judgment' if facts.get('judgment') else 'filed' if facts.get('filed') else None)
    if kind == 'filed' and facts.get('filed'):
        d = facts['filed']
        ago = (today - d).days
        where_en = (' in %s County' % facts['county']) if facts.get('county') else ''
        where_es = (' en el condado de %s' % facts['county']) if facts.get('county') else ''
        en = ('On %s, a notice of a court case involving your property was recorded%s. That was %s '
              'before the date of this letter. When you are served, the papers say how many days you '
              'have to answer in writing. That is the clock that matters most right now.'
              % (date_en(d), where_en, _n(ago, 'day', 'days')))
        es = ('El %s se registró%s un aviso de un caso en la corte relacionado con su propiedad. Eso '
              'fue %s antes de la fecha de esta carta. Cuando le entreguen los papeles, ahí dice '
              'cuántos días tiene para responder por escrito. Ese es el plazo que más importa ahora.'
              % (date_es(d), where_es, _n(ago, 'día', 'días')))
        return en, es
    if kind == 'judgment' and facts.get('judgment'):
        d = facts['judgment']
        ago = (today - d).days
        en = ('On %s, the court entered a final judgment in your case. That was %s before the date '
              'of this letter. ' % (date_en(d), _n(ago, 'day', 'days')))
        es = ('El %s la corte dictó una sentencia final en su caso. Eso fue %s antes de la fecha de '
              'esta carta. ' % (date_es(d), _n(ago, 'día', 'días')))
        if facts.get('sale'):
            left = (facts['sale'] - today).days
            en += ('The court calendar shows a sale date of %s, %s after the date of this letter.'
                   % (date_en(facts['sale']), _n(left, 'day', 'days')))
            es += ('El calendario de la corte muestra una fecha de venta del %s, %s después de la '
                   'fecha de esta carta.' % (date_es(facts['sale']), _n(left, 'día', 'días')))
        else:
            en += 'A sale date is what usually comes next.'
            es += 'Lo que normalmente sigue es una fecha de venta.'
        return en, es
    return None


def event_meaning(facts, kind=None):
    """(english, spanish): what the newest event means, without repeating its date. For the file
    page, which already prints the dates."""
    kind = kind or ('judgment' if facts.get('judgment') else 'filed' if facts.get('filed') else None)
    if kind == 'filed' and facts.get('filed'):
        return ('The notice is the newest entry in your case. When you are served, the papers say how '
                'many days you have to answer in writing. That is the clock that matters most right now.',
                'El aviso es lo más reciente en su caso. Cuando le entreguen los papeles, ahí dice '
                'cuántos días tiene para responder por escrito. Ese es el plazo que más importa ahora.')
    if kind == 'judgment' and facts.get('judgment'):
        if facts.get('sale'):
            return ('The final judgment is the newest entry in your case. The sale date is the next '
                    'date on the court calendar.',
                    'La sentencia final es lo más reciente en su caso. La fecha de venta es la próxima '
                    'fecha en el calendario de la corte.')
        return ('The final judgment is the newest entry in your case. A sale date is what usually '
                'comes next.',
                'La sentencia final es lo más reciente en su caso. Lo que normalmente sigue es una '
                'fecha de venta.')
    return None


# ════════════════════════════ assignment (the measured split) ════════════════════════════
def _h(case):
    return hashlib.sha256(('mail-variants|' + str(case)).encode('utf-8')).hexdigest()


def assign(cases, prior=None):
    """{case: 'A'|'B'|'C'}, even and repeatable. A case already in the ledger keeps its arm; new
    cases are taken in the order of a hash of the case id and each goes to the arm that is smallest
    so far, so the same run always gives the same answer and 90 leads land 30 / 30 / 30."""
    prior = prior or {}
    out, count = {}, {v: 0 for v in VARIANTS}
    for c in cases:
        v = prior.get(c)
        if v in VARIANTS:
            out[c] = v
            count[v] += 1
    for c in sorted((c for c in cases if c not in out), key=_h):
        v = min(VARIANTS, key=lambda k: (count[k], k))
        out[c] = v
        count[v] += 1
    return out


def printed_variants(rows):
    """Set r['variant'] / r['fallback'] on every row: what is actually printed. The calendar needs a
    real sale date still ahead; a calendar assignment without one falls to A or C. Those are taken in
    the order of the case-id hash and each goes to whichever of A and C has fewer printed so far, so
    the printed split stays even and a rerun of the same run prints the same thing."""
    count = {v: 0 for v in VARIANTS}
    for r in rows:
        r['fallback'] = ''
        if r['assigned'] != 'B' or r['facts'].get('sale'):
            r['variant'] = r['assigned']
            count[r['variant']] += 1
    for r in sorted((r for r in rows if 'variant' not in r), key=lambda r: _h(r['case'])):
        r['variant'] = 'A' if count['A'] <= count['C'] else 'C'
        r['fallback'] = 'no sale date on record'
        count[r['variant']] += 1
    return count


def new_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def show_code(code):
    return '%s-%s' % (code[:4], code[4:])


def new_slug():
    return ''.join(secrets.choice('abcdefghjkmnpqrstuvwxyz23456789') for _ in range(12))


def load_ledger(path):
    """{} for a missing ledger. An unreadable one refuses the run: a lost ledger would re-deal the
    arms and re-issue page codes that are already printed."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        cases = d.get('cases')
        if not isinstance(cases, dict):
            raise ValueError('no "cases" map')
        return cases
    except (OSError, ValueError, AttributeError) as e:
        raise DataError('%s is unreadable (%s). Fix or move it; the split is not re-dealt over a '
                        'broken ledger.' % (os.path.basename(path), str(e)[:80]))


def save_ledger(path, cases):
    doc = {'_about': 'Mail variant assignment only. A = short note, B = calendar page, C = your file. '
                     'Not a record of anything mailed. No names, no addresses.',
           'updated': dt.datetime.now().isoformat(timespec='seconds'),
           'cases': cases}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ════════════════════════════ words ════════════════════════════
def _co():
    return M.SENDER.get('co') or 'Biscayne Solutions Group'


def mars(lang):
    """The three mandated sentences, from disclaimer.py and nowhere else."""
    return D.mars_part(*MARS_KEYS, lang=lang, company=_co(), as_html=False)


def optout(lang):
    return OC.OPTOUT_LINE_ES if lang == 'es' else OC.OPTOUT_LINE_EN


def band_lines(f):
    """(english, spanish) for the red band. Only what the record shows: a sale date when one is on
    record and still ahead, otherwise the date the notice of the case was recorded, otherwise neither."""
    en, es = 'URGENT · TIME-SENSITIVE', 'URGENTE · EL TIEMPO CUENTA'
    if f.get('sale'):
        return ('%s · SALE DATE %s' % (en, date_en(f['sale'])),
                '%s · FECHA DE VENTA %s' % (es, date_es(f['sale'])))
    if f.get('filed'):
        d = f['filed']                       # short month: the line has to fit the band on one row
        return ('%s · CASE NOTICE RECORDED %s %d, %d' % (en, _MONTH_EN[d.month - 1][:3], d.day, d.year),
                '%s · AVISO DEL CASO REGISTRADO EL %d %s %d' % (es, d.day, _MONTH_ES[d.month - 1][:3], d.year))
    return en, es


def _band_pt(text, top, spacing):
    """Type size that keeps a band line on one row of the 7in sheet width (heavy caps run about
    0.72em a letter plus the letter-spacing)."""
    return max(8.0, min(top, round(498.0 / (max(len(text), 1) * (0.72 + spacing)), 1)))


def fact_headline(f):
    """Variant A's red headline: the same fact as the band, as a sentence. Not a sales line."""
    if f.get('sale'):
        return ('A sale date of %s is on the court calendar.' % date_en(f['sale']),
                'Hay una fecha de venta del %s en el calendario de la corte.' % date_es(f['sale']))
    if f.get('filed'):
        return ('A notice of a court case was recorded on %s.' % date_en(f['filed']),
                'El %s se registró un aviso de un caso en la corte.' % date_es(f['filed']))
    return None


LEAD_EN = "I'm not trying to buy your house."
LEAD_ES = 'No estoy tratando de comprarle la casa.'


def intro(lang):
    if lang == 'es':
        return ('Soy %s, de %s, aquí en el sur de la Florida. No soy del gobierno ni de su banco, no '
                'soy abogado y no le estoy pidiendo dinero.' % (M.SENDER['name'], _co()))
    return ("I'm %s with %s, here in South Florida. I'm not with the government or your lender, "
            "I'm not a lawyer, and I'm not asking you for any money." % (M.SENDER['name'], _co()))


NOTE_B = {
    'en': ("The court calendar shows a sale date for your home; I marked it above. Before then you may "
           "still have choices: keep the home, sell on your own terms, or ask the court for more time. "
           "Call me and we'll talk them through. No pressure."),
    'es': ('El calendario de la corte muestra una fecha de venta para su casa; se la marqué arriba. '
           'Antes de esa fecha usted todavía puede tener opciones: quedarse con la casa, venderla en '
           'sus propios términos o pedirle más tiempo a la corte. Llámeme y las conversamos. Sin '
           'presión.'),
}

OPTIONS = {
    'en': ('Owners in this spot usually look at three paths:',
           (('Keep the home.', 'Some owners work out a payment plan or a loan change. A HUD-approved '
             'housing counselor can help at no cost.'),
            ('Sell on your terms.', 'Before a court sale, you choose the buyer, the price and the '
             'timing.'),
            ('Buy time.', 'A Florida attorney or a legal aid office can tell you how to ask the '
             'court for more time.')),
           "Call me and I'll walk you through all three. No pressure."),
    'es': ('Los dueños en esta situación suelen considerar tres caminos:',
           (('Quedarse con la casa.', 'Algunos dueños logran un plan de pago o un cambio en el '
             'préstamo. Un consejero de vivienda aprobado por HUD puede ayudarle sin costo.'),
            ('Vender en sus términos.', 'Antes de una venta judicial, usted escoge el comprador, el '
             'precio y el momento.'),
            ('Ganar tiempo.', 'Un abogado de la Florida o una oficina de ayuda legal le puede decir '
             'cómo pedirle más tiempo a la corte.')),
           'Llámeme y le explico los tres. Sin presión.'),
}


def letter_es(r):
    """bsg_mail_campaign.letter() in Spanish: same sentences, same order, same compliance lines."""
    street = M.street_case(r['addr'].split(',')[0])
    return ('%s:\n\n'
            'Soy %s, de %s, aquí en el sur de la Florida. No soy del gobierno ni de su banco, no soy '
            'abogado y no le estoy pidiendo dinero.\n\n'
            'Vi el caso que se presentó en la corte sobre %s. Si vender es algo que usted '
            'consideraría, yo compro casas en esta situación, o le puedo ayudar a vender antes de que '
            'le pongan fecha de venta.\n\n'
            '%s\n\n'
            '%s\n\n'
            '%s\n%s'
            % (r['first'], M.SENDER['name'], _co(), street, mars('es'), optout('es'),
               M.SENDER['name'], M.contact_phone()))


# ════════════════════════════ the pieces ════════════════════════════
def _e(s):
    return H.escape(str(s), quote=True)


def _logo_uri():
    """The real BSG logo, through the URGENT letter's own loader (brand/bsg-logo-letterhead.png)."""
    try:
        import outreach_mail
        return outreach_mail._jesse_logo_uri()
    except Exception:
        return ''


def qr_svg(url):
    """Inline SVG from the business card's QR helper (segno). '' when no QR library is installed."""
    try:
        import bsg_cards
        return bsg_cards.qr_svg(url, dark='#000000', cls='qr')
    except Exception:
        return ''


# The look is the owner's URGENT letter (outreach_mail.build_letter_html_jesse): red band first, the
# real logo, a heavy red headline, Georgia body with tight leading, a centred red call line. Nothing
# depends on colour alone: the band is a solid dark bar with a border in grayscale, the sale day is
# bold and ringed, and every label is also text.
CSS = """
@page{size:letter;margin:0}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font:10.5pt/1.3 Georgia,"Times New Roman",serif;color:%(ink)s;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.pc{width:8.5in;height:11in;padding:.42in .62in .6in;position:relative;page-break-after:always;overflow:hidden}
.pc:last-child{page-break-after:auto}
.urgent,.uhead,.cta,.lhr,.lang,.ft,.cal,.capbox,.facts,.qrb,.ev,.sigwho,.cover,.src{font-family:"Helvetica Neue",Helvetica,Arial,sans-serif}
.urgent{background:%(accent)s;color:#fff;text-align:center;margin:0 0 .12in;padding:7px 8px 6px;font-weight:900;font-size:13pt;line-height:1.2;letter-spacing:.07em;text-transform:uppercase;white-space:nowrap;border:2px solid %(edge)s;box-shadow:0 1px 0 %(edge)s}
.urgent span{display:block;letter-spacing:.06em;margin-top:1px}
.lh{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.09in}
.logo{height:.85in;width:.85in;background:url("%(logo)s") left top/contain no-repeat}
.markword{font:900 15pt "Helvetica Neue",Helvetica,Arial,sans-serif;color:%(dark)s;letter-spacing:.04em}
.lhr{text-align:right;font-size:9.5pt;line-height:1.35;color:#444}
.to{font:11pt/1.32 Georgia,serif;color:%(ink)s;margin-top:.06in}
.uhead{margin:0 0 3px;font-weight:900;font-size:17pt;line-height:1.12;color:%(accent)s;letter-spacing:.015em}
.uhead.es{font-size:14pt;margin-bottom:.1in}
.cols{display:flex}
.col{flex:1 1 0;min-width:0;padding-right:.25in}
.col+.col{padding:0 0 0 .25in;border-left:1px solid #cfd2d8}
.lang{font-weight:700;font-size:7pt;letter-spacing:.16em;color:%(dark)s;text-transform:uppercase;margin:0 0 .05in}
.pc p{margin:0 0 7px}
.pa .col{font-size:11.5pt;line-height:1.4}
.pa p{margin:0 0 .12in}
.opts{margin:0 0 7px;padding:0;list-style:none}
.opts li{margin:0 0 5px}
.opts b{color:%(dark)s}
.ev{display:flex;border-left:3px solid %(dark)s;background:#f2f2f3;padding:.08in .13in;margin:0 0 .13in;font-size:9.5pt;line-height:1.36}
.ev div{flex:1 1 0}
.ev div+div{margin-left:.24in}
.calrow{display:flex;align-items:stretch;margin:.04in 0 .15in}
.cal{width:4.45in;flex:none}
.calh{font-weight:900;font-size:14pt;color:%(accent)s;margin:0 0 .05in}
.calh span{font-weight:700;color:%(dark)s}
.calt{width:100%%;border-collapse:collapse;table-layout:fixed}
.calt th{font-weight:700;font-size:7.5pt;color:#333;padding:.03in 0;border-bottom:2px solid %(dark)s;text-transform:uppercase;letter-spacing:.05em}
.calt th i{font-style:normal;font-weight:400;color:#666;margin-left:.04in}
.calt td{height:.35in;border:1px solid #c9ccd2;text-align:center;vertical-align:middle;font-size:11.5pt;color:#333;position:relative}
.calt td.x{border-color:#e6e7ea;background:#f7f7f8}
.calt td.hit{font-weight:900;font-size:13.5pt;color:%(accent)s}
.ring{position:absolute;left:-7%%;top:-17%%;width:114%%;height:134%%;overflow:visible}
.capbox{flex:1 1 0;margin-left:.26in;padding-left:.2in;border-left:3px solid %(dark)s;display:flex;flex-direction:column;justify-content:center;font-size:9.5pt;line-height:1.34;color:#333}
.capbox small{display:block;font-size:7.5pt;letter-spacing:.1em;text-transform:uppercase;font-weight:700;color:%(accent)s}
.capbox b{display:block;font-size:13pt;line-height:1.2;font-weight:700;color:%(dark)s;margin:.02in 0}
.capbox .gap{height:.13in}
.capbox .evs{margin-top:.13in;padding-top:.09in;border-top:1px solid #cfd2d8;font-size:8.8pt;line-height:1.33}
.capbox .evs div+div{margin-top:.05in}
.filet{font:700 12.5pt/1.2 "Helvetica Neue",Helvetica,Arial,sans-serif;color:%(dark)s;margin:0}
.filet span{display:block;font-weight:400;font-size:10pt;color:#444}
.facts{width:100%%;border-collapse:collapse;margin:.06in 0 .05in}
.facts th{text-align:left;font-weight:700;font-size:9.5pt;color:#333;padding:.038in .1in .038in .09in;border-bottom:1px solid #cfd2d8;width:36%%;vertical-align:top;border-left:3px solid transparent}
.facts th i{font-style:normal;font-weight:400;color:#666}
.facts td{font-weight:700;font-size:10.5pt;color:%(ink)s;padding:.038in 0;border-bottom:1px solid #cfd2d8;vertical-align:top}
.facts td i{font-style:normal;font-weight:400;color:#444}
.facts td small{display:block;font-weight:400;font-size:9pt;color:#444}
.facts tr.new th{border-left-color:%(dark)s;color:%(dark)s}
.src{font-size:8.3pt;line-height:1.35;color:#666;margin:0 0 .12in}
.src b{color:#333;font-weight:700}
.cta{margin:.07in 0 .02in;font-weight:900;font-size:16pt;line-height:1.2;color:%(accent)s;text-align:center;letter-spacing:.02em}
.sig{display:flex;justify-content:space-between;align-items:flex-end;margin-top:.02in}
.sigline{width:2.9in;height:.38in;border-bottom:1px solid %(ink)s}
.sigwho{font-size:10.5pt;line-height:1.3;margin-top:.04in}
.sigwho b{font-size:11.5pt}
.qrb{display:flex;align-items:center;border:1.5px solid %(dark)s;border-radius:6px;padding:.05in .13in .05in .06in}
.qrb .qrbox{width:.9in;height:.9in;background:#fff;margin-right:.12in;flex:none}
.qrb svg.qr{width:100%%;height:100%%;display:block}
.qrb small{display:block;font-size:7.5pt;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:%(dark)s}
.qrb span{display:block;font-size:8pt;line-height:1.3;color:#444}
.qrb .code{font:700 13pt Consolas,"Courier New",monospace;letter-spacing:.06em;color:%(ink)s}
.ft{position:absolute;left:.62in;right:.62in;bottom:.3in;font-size:7.5pt;color:#666;display:flex;justify-content:space-between;border-top:1px solid #dcdde1;padding-top:.045in}
.t1{font-size:10pt;line-height:1.31}
.t1 .uhead{font-size:15.5pt}
.t1 .uhead.es{font-size:13.5pt}
.t1 .calt td{height:.33in}
.t1.pa .col{font-size:10.8pt;line-height:1.36}
.t2{font-size:9.5pt;line-height:1.28}
.t2.pa .col{font-size:10.2pt;line-height:1.33}
.t2 .urgent{margin-bottom:.08in;padding:5px 8px 4px}
.t2 .calrow{margin-bottom:.1in}
.t2 .filet{font-size:11.5pt}
.t2 .facts td{font-size:10pt}
.t2 .uhead{font-size:14.5pt}
.t2 .uhead.es{font-size:12.5pt;margin-bottom:.08in}
.t2 .logo{height:.72in;width:.72in}
.t2 .calt td{height:.31in}
.t2 .pc p,.t2 p{margin-bottom:5px}
.t2 .opts li{margin-bottom:3px}
.t2 .lh{margin-bottom:.07in}
.t2 .sigline{height:.32in}
.t2 .facts th,.t2 .facts td{padding-top:.026in;padding-bottom:.026in}
.t2 .src{margin-bottom:.08in}
.t2 .ev{margin-bottom:.09in}
.t2 .cta{font-size:15pt;margin:.06in 0 .02in}
.cover{font-size:11.5pt}
.cover h1{font-size:23pt;color:%(dark)s;margin:0 0 .04in}
.cover .sub{color:#444;font-size:11pt;margin-bottom:.22in}
.cover h2{font-size:12.5pt;color:%(accent)s;margin:.24in 0 .08in;text-transform:uppercase;letter-spacing:.08em}
.cover table{border-collapse:collapse;width:100%%;font-size:11.5pt}
.cover td,.cover th{border-bottom:1px solid #cfd2d8;padding:.07in .08in;text-align:left}
.cover td.n,.cover th.n{text-align:right;width:1.1in;font-variant-numeric:tabular-nums}
.cover ul{list-style:none;padding:0;margin:0;font-size:11.5pt;line-height:1.4}
.cover li{margin:0 0 .085in;padding-left:.34in;position:relative}
.cover li:before{content:"";position:absolute;left:0;top:.03in;width:.17in;height:.17in;border:1.5px solid %(dark)s;border-radius:3px}
.cover .note{font-size:10pt;color:#444;margin-top:.14in;line-height:1.45}
"""


def month_grid(year, month):
    """Weeks of the month, Sunday first, 0 for a blank cell."""
    return calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)


_RING = ('<svg class="ring" viewBox="0 0 100 60" preserveAspectRatio="none" aria-hidden="true">'
         '<path d="M13,33 C9,13 39,4 61,6 C86,9 95,23 89,38 C82,54 51,58 31,53 C13,48 7,37 14,25 '
         'C21,12 45,5 63,9" fill="none" stroke="%s" stroke-width="2.6" stroke-linecap="round" '
         'vector-effect="non-scaling-stroke"/></svg>' % ACCENT)


def calendar_html(sale):
    head = ''.join('<th>%s<i>%s</i></th>' % (_e(a), _e(b)) for a, b in _DOW_HEAD)
    body = []
    for week in month_grid(sale.year, sale.month):
        tds = []
        for day in week:
            if not day:
                tds.append('<td class="x"></td>')
            elif day == sale.day:
                tds.append('<td class="hit">%s%d</td>' % (_RING, day))
            else:
                tds.append('<td>%d</td>' % day)
        body.append('<tr>%s</tr>' % ''.join(tds))
    return ('<div class="cal"><div class="calh">%s %d <span>&middot; %s de %d</span></div>'
            '<table class="calt"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>'
            % (_MONTH_EN[sale.month - 1], sale.year, _e(_MONTH_ES[sale.month - 1]), sale.year,
               head, ''.join(body)))


def _paras(text):
    return ''.join('<p>%s</p>' % _e(p).replace('\n', '<br>') for p in text.split('\n\n') if p.strip())


def _legal(lang):
    return '<p>%s</p><p>%s</p>' % (_e(mars(lang)), _e(optout(lang)))


def _col(lang, inner):
    return '<div class="col" lang="%s"><div class="lang">%s</div>%s</div>' % (
        lang, 'Español' if lang == 'es' else 'English', inner)


def headline(r):
    """The red headline, English then Spanish, full width. B and C: the house line with the first
    name. A (the control): the band's fact as a sentence, so the letter text itself is untouched."""
    if r['variant'] == 'A':
        h = fact_headline(r['facts'])
        if not h:
            return ''
        en, es = h
    else:
        en, es = '%s, %s' % (r['first'], LEAD_EN), '%s: %s' % (r['first'], LEAD_ES)
    return ('<div class="uhead" lang="en">%s</div><div class="uhead es" lang="es">%s</div>'
            % (_e(en), _e(es)))


def body_a(r, today, event=None):
    """The control arm. English is bsg_mail_campaign.letter() verbatim; the closing name and phone
    move to the signature row so the page is signed once, by hand."""
    en = M.letter(r).rsplit('\n\n', 1)[0]
    es = letter_es(r).rsplit('\n\n', 1)[0]
    strip = ''
    ev = event_lines(r['facts'], today, kind=event) if event else None
    if ev:
        strip = '<div class="ev"><div lang="en">%s</div><div lang="es">%s</div></div>' % (
            _e(ev[0]), _e(ev[1]))
    return strip + '<div class="cols">%s%s</div>' % (_col('en', _paras(en)), _col('es', _paras(es)))


def body_b(r, today, event=None):
    f = r['facts']
    sale = f['sale']
    left = (sale - today).days
    cap = ('<small>Sale date on the court calendar</small><b>%s</b>%s after the date of this letter'
           '<div class="gap"></div>'
           '<small>Fecha de venta en el calendario de la corte</small><b>%s</b>Faltan %s a partir de '
           'la fecha de esta carta'
           % (_e(date_en(sale, True)), _n(left, 'day', 'days'),
              _e(date_es(sale, True)), _n(left, 'día', 'días')))
    ev = event_lines(dict(f, sale=None), today, kind=event) if event else None
    if ev:
        # The sale date is already the page; the note beside it names the event and stops there.
        cap += '<div class="evs"><div lang="en">%s</div><div lang="es">%s</div></div>' % (
            _e(ev[0].split('. ')[0] + '.'), _e(ev[1].split('. ')[0] + '.'))
    cols = ''
    for lang in ('en', 'es'):
        cols += _col(lang, '<p>%s</p><p>%s</p>%s' % (
            _e(intro(lang)), _e(NOTE_B[lang]), _legal(lang)))
    return ('<div class="calrow">%s<div class="capbox">%s</div></div><div class="cols">%s</div>'
            % (calendar_html(sale), cap, cols))


def fact_rows(f, today):
    """[(key, label_en, label_es, value_en, value_es, small)] -- only rows the record backs."""
    rows = []
    if f.get('county'):
        rows.append(('case', 'Case and county', 'Caso y condado', f['case'],
                     '%s County · Condado de %s' % (f['county'], f['county']), ''))
    else:
        rows.append(('case', 'Case number', 'Número de caso', f['case'], '', ''))
    if f.get('filed'):
        ago = (today - f['filed']).days
        rows.append(('filed', 'Notice of the case recorded', 'Aviso del caso registrado',
                     date_en(f['filed']), date_es(f['filed']),
                     '%s before the date of this letter · %s antes de esta carta'
                     % (_n(ago, 'day', 'days'), _n(ago, 'día', 'días'))))
    if f.get('judgment'):
        rows.append(('judgment', 'Final judgment entered', 'Sentencia final dictada',
                     date_en(f['judgment']), date_es(f['judgment']), ''))
    if f.get('sale'):
        left = (f['sale'] - today).days
        rows.append(('sale', 'Sale date on the court calendar', 'Fecha de venta en el calendario',
                     date_en(f['sale'], True), date_es(f['sale'], True),
                     '%s after the date of this letter · faltan %s desde esta carta'
                     % (_n(left, 'day', 'days'), _n(left, 'día', 'días'))))
    return rows


def body_c(r, today, event=None):
    trs = ''.join('<tr%s><th>%s <i>&middot; %s</i></th><td>%s%s%s</td></tr>'
                  % (' class="new"' if event and k == event else '', _e(a), _e(b), _e(c),
                     (' <i>&middot; %s</i>' % _e(d)) if d else '',
                     ('<small>%s</small>' % _e(sm)) if sm else '')
                  for k, a, b, c, d, sm in fact_rows(r['facts'], today))
    mean = event_meaning(r['facts'], kind=event) if event else None
    src = ('From public records. The clerk of court has anything newer. &middot; Tomado de registros '
           'p&uacute;blicos. La secretar&iacute;a de la corte tiene lo m&aacute;s reciente.')
    if mean:
        src = '<b>%s</b> &middot; <b>%s</b><br>%s' % (_e(mean[0]), _e(mean[1]), src)
    top = ('<div class="filet">Your file: what the public court record shows'
           '<span>Su expediente: lo que muestra el registro p&uacute;blico de la corte</span></div>'
           '<table class="facts">%s</table><div class="src">%s</div>' % (trs, src))
    cols = ''
    for lang in ('en', 'es'):
        head, opts, close = OPTIONS[lang]
        lis = ''.join('<li><b>%s</b> %s</li>' % (_e(t), _e(x)) for t, x in opts)
        cols += _col(lang, '<p>%s</p><ul class="opts">%s</ul><p>%s</p>%s' % (
            _e(intro(lang)), lis, _e(close), _legal(lang)))
    return top + '<div class="cols">%s</div>' % cols


def cta_html():
    """The centred red call line, both languages, with the callback number."""
    return '<div class="cta">CALL NOW &middot; LLAME AHORA: %s</div>' % _e(M.contact_phone())


def qr_block(r, with_qr, base_url):
    """The private-page QR and code, beside the signature. '' unless --with-qr and a QR can be drawn."""
    if not (with_qr and r.get('code') and r.get('slug')):
        return ''
    svg = qr_svg(page_url(base_url, r['slug'], r['code']))
    if not svg:
        return ''
    return ('<div class="qrb"><div class="qrbox">%s</div><div>'
            '<small>Your private page &middot; Su p&aacute;gina privada</small>'
            '<span>Scan, then enter this code &middot; Escanee y escriba este c&oacute;digo</span>'
            '<div class="code">%s</div></div></div>' % (svg, _e(show_code(r['code']))))


def piece_html(r, today, with_qr=False, base_url='', event=None):
    """One sheet: red band, logo and recipient, red headline, the variant's body in two languages,
    the red call line, the signature. `event` ('filed' / 'judgment') names that court event on it."""
    v = r['variant']
    event = event if isinstance(event, str) and r['facts'].get(event) else None
    city = r['city'] if r['city'].lower() not in ('unincorporated county', '') else ''
    place = ('%s, ' % city.title() if city else '') + '%s %s' % (r.get('to_state') or 'FL', r['zip'])
    mark = '<div class="logo"></div>' if _logo_uri() else '<div class="markword">%s</div>' % _e(_co())
    band = band_lines(r['facts'])
    body = (body_a if v == 'A' else body_b if v == 'B' else body_c)(r, today, event)
    return ('<section class="pc p%s" data-case="%s" data-variant="%s"><div class="in">'
            '<div class="urgent" style="font-size:%spt">%s<span style="font-size:%spt">%s</span></div>'
            '<div class="lh">%s<div class="lhr">%s<br>%s<div class="to">%s<br>%s<br>%s</div></div></div>'
            '%s%s%s'
            '<div class="sig"><div><div class="sigline"></div><div class="sigwho"><b>%s</b><br>%s</div>'
            '</div>%s</div></div>'
            '<div class="ft"><span>%s</span><span>Ref. %s &middot; %s</span></div></section>'
            % (v.lower(), _e(r['case']), v, _band_pt(band[0], 13.5, .07), _e(band[0]),
               _band_pt(band[1], 10.5, .06), _e(band[1]),
               mark, _e(date_en(today)), _e(date_es(today)),
               _e(M.person_name(r['owner'])), _e(M.street_case(r['addr'])), _e(place),
               headline(r), body, cta_html(),
               _e(M.SENDER['name']), _e(_co()), qr_block(r, with_qr, base_url),
               _e(_co()), _e(r['case']), v))


def cover_html(rows, today, info):
    n = len(rows)
    printed = {v: sum(1 for r in rows if r['variant'] == v) for v in VARIANTS}
    assigned = {v: sum(1 for r in rows if r['assigned'] == v) for v in VARIANTS}
    fell = sum(1 for r in rows if r['assigned'] != r['variant'])
    trs = ''.join('<tr><td>%s &middot; %s</td><td class="n">%d</td><td class="n">%d</td></tr>'
                  % (v, VARIANT_NAME[v], assigned[v], printed[v]) for v in VARIANTS)
    notes = []
    if fell:
        notes.append('%d assigned to the calendar page had no sale date on record and print as A or C '
                     'instead (the ledger keeps both).' % fell)
    if info.get('trigger'):
        notes.append('Court-triggered run: only leads with a case event in the last %d days '
                     '(%s).' % (info['trigger'], info.get('trigger_counts') or 'none'))
    notes.append('Private-page QR codes: %s.' % ('printed on each piece' if info.get('with_qr') else
                                                 'off, each piece carries the phone number'))
    notes.append('%d of %d eligible in this run. Assignment ledger: %s. Nothing here is recorded as '
                 'mailed.' % (n, info.get('eligible', n), info.get('ledger') or LEDGER))
    cost = n * M.PER_LETTER
    return ('<section class="pc cover"><div class="in"><h1>Mail variants: %d pieces</h1>'
            '<div class="sub">%s &middot; operator sheet, do not mail this page &middot; pieces follow '
            'on pages 2 to %d</div>'
            '<h2>The split</h2><table><tr><th>Variant</th><th class="n">Assigned</th>'
            '<th class="n">Printed</th></tr>%s<tr><th>Total</th><th class="n">%d</th>'
            '<th class="n">%d</th></tr></table>'
            '<h2>Postage estimate</h2><table>'
            '<tr><td>Stamp $%.2f + envelope $%.2f + paper $%.2f</td><td class="n">$%.2f each</td></tr>'
            '<tr><th>%d pieces</th><th class="n">$%.2f</th></tr></table>'
            '<h2>Checklist</h2><ul>'
            '<li>Print pages 2 to %d single-sided on plain white letter paper, at 100%% scale.</li>'
            '<li>Sign each one by hand on the line above the name. Blue or black pen.</li>'
            '<li>Calendar pages: go over the circle on the date by hand with the same pen.</li>'
            '<li>Hand-address the envelope from the name and address at the top right of the piece.</li>'
            '<li>Return address on the envelope: %s, the name only.</li>'
            '<li>Real stamp. No meter, no label.</li>'
            '<li>Do not change the wording. If someone says stop, tell Alex the same day.</li>'
            '</ul><div class="note">%s</div></div>'
            '<div class="ft"><span>%s</span><span>Operator sheet</span></div></section>'
            % (n, _e(date_en(today)), n + 1, trs, n, n, M.STAMP, M.ENVELOPE, M.PAPER, M.PER_LETTER,
               n, cost, n + 1, _e(_co()), '<br>'.join(_e(x) for x in notes), _e(_co())))


def build_html(rows, today, info):
    css = CSS % {'ink': INK, 'accent': ACCENT, 'edge': BAND_EDGE, 'dark': DARK, 'logo': _logo_uri()}
    pieces = [piece_html(r, today, with_qr=r.get('with_qr', info.get('with_qr')),
                         base_url=info.get('base_url', ''),
                         event=r.get('event')) for r in rows]
    check_pieces(pieces, rows, info.get('never') or ())
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Mail variants %s</title>'
           '<style>%s</style></head><body>%s%s</body></html>'
           % (today.isoformat(), css, cover_html(rows, today, info), ''.join(pieces)))
    return doc, pieces


def check_pieces(pieces, rows, never=()):
    """Refuse to write a run in which any piece is missing a mandated line or carries something it
    must not. `never` is a list of strings that may not appear (the return address's second line)."""
    need = [_e(mars('en')), _e(mars('es')), _e(optout('en')), _e(optout('es')), _e(M.contact_phone())]
    bad = []
    for r, p in zip(rows, pieces):
        text = re.sub(r'<svg.*?</svg>', '', p, flags=re.S)
        why = []
        if any(x not in text for x in need):
            why.append('a MARS or opt-out line or the phone is missing')
        if '$' in text:
            why.append('a dollar sign')
        if re.search(r'\bPMB\b|\bP\.?\s?O\.?\s+BOX\b', text, re.I):
            why.append('a mailbox number')
        if any(s and s.strip() and s.strip().lower() in text.lower() for s in never):
            why.append('a string that must never print')
        if r['variant'] in ('B', 'C') and not (_e(LEAD_EN) in text and _e(LEAD_ES) in text):
            why.append('the opening line')
        band = band_lines(r['facts'])
        if not (text.count('class="urgent"') == 1 and _e(band[0]) in text and _e(band[1]) in text):
            why.append('the red band')
        if ('SALE DATE' in text or 'FECHA DE VENTA' in text) and not r['facts'].get('sale'):
            why.append('a sale date the record does not carry')
        if re.search(r'FINAL\s+NOTICE|AVISO\s+FINAL|NOTICE\s+OF\s+SALE|CLERK\s+OF\s+COURT\s+NOTICE', text, re.I):
            why.append('wording that reads as an official notice')
        if why:
            bad.append('%s (%s)' % (r['case'], '; '.join(why)))
    if bad:
        raise DataError('REFUSED, nothing written. %d piece(s) failed the copy check: %s'
                        % (len(bad), ', '.join(bad[:8])))


# ════════════════════════════ private pages ════════════════════════════
def page_url(base_url, slug, code=''):
    return '%s/%s/%s' % (str(base_url).rstrip('/'), slug, ('#' + code) if code else '')


def default_base_url():
    """Where the pages WOULD live if someone published them. Nothing here publishes."""
    try:
        import board_url
        return board_url.BOARD_URL + 'p/'
    except Exception:
        return 'https://example.invalid/p/'


def _tel(phone):
    d = re.sub(r'\D', '', str(phone))
    return 'tel:+1' + d[-10:] if len(d) >= 10 else ''


def page_payload(r, today, voice=None):
    """The part of the page that is encrypted: the first name, the case facts, the options."""
    f = r['facts']
    out = {'tel': _tel(M.contact_phone()), 'phone': M.contact_phone(), 'who': M.SENDER['name'],
           'voice': voice or {}}
    for lang in ('en', 'es'):
        es = lang == 'es'
        fmt = date_es if es else date_en
        tl = []
        if f.get('filed'):
            tl.append((fmt(f['filed']), 'Se registró un aviso del caso en la corte.' if es else
                       'A notice of the court case was recorded.', ''))
        if f.get('judgment'):
            tl.append((fmt(f['judgment']), 'La corte dictó una sentencia final.' if es else
                       'The court entered a final judgment.', ''))
        if f.get('sale'):
            tl.append((fmt(f['sale'], True), 'Fecha de venta en el calendario de la corte.' if es else
                       'Sale date on the court calendar.', f['sale'].isoformat()))
        lis = ''.join('<li><b>%s</b><span>%s</span>%s</li>'
                      % (_e(a), _e(b), ('<em data-until="%s"></em>' % c) if c else '') for a, b, c in tl)
        head, opts, close = OPTIONS[lang]
        ol = ''.join('<li><b>%s</b> %s</li>' % (_e(t), _e(x)) for t, x in opts)
        meta = ' &middot; '.join(_e(x) for x in (
            (('Condado de %s' if es else '%s County') % f['county']) if f.get('county') else '',
            ('Caso %s' if es else 'Case %s') % f['case']) if x)
        out[lang] = (
            '<p class="lead">%s%s %s</p><p>%s</p>'
            '<h2>%s</h2><p class="meta">%s</p><ol class="tl">%s</ol><p class="src">%s</p>'
            '<h2>%s</h2><ul class="opts">%s</ul><p>%s</p>'
            '<div class="legal"><p>%s</p><p>%s</p></div><p class="by">%s<br>%s</p>'
            % (_e(r['first']), ':' if es else ',', _e(LEAD_ES if es else LEAD_EN), _e(intro(lang)),
               'Su línea de tiempo' if es else 'Your timeline', meta, lis,
               _e(('Tomado de registros públicos al %s. La secretaría de la corte tiene lo más '
                   'reciente.' if es else 'From public records as of %s. The clerk of court has '
                   'anything newer.') % fmt(today)),
               _e(head), ol, _e(close), _e(mars(lang)), _e(optout(lang)),
               _e(M.SENDER['name']), _e(_co())))
    return out


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<meta name="referrer" content="no-referrer">
<title>__CO__</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f8;color:#1b1f27;font:17px/1.55 Georgia,"Times New Roman",serif}
.wrap{max-width:560px;margin:0 auto;padding:22px 20px 96px}
.urgent{background:#c8102e;color:#fff;text-align:center;padding:9px 10px;font:900 14px/1.2 "Helvetica Neue",Helvetica,Arial,sans-serif;letter-spacing:.14em;text-transform:uppercase;border:2px solid #7a0a1c}
.urgent span{display:block;font-size:12px;margin-top:2px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
.top img{height:64px;width:auto;display:block}
.lead{color:#c8102e!important;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;font-weight:900!important}
.callbar a{background:#c8102e!important}
.co{font:700 14px/1.2 "Segoe UI",Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#16294d}
.tog{flex:none;white-space:nowrap}
.tog button{font:600 14px "Segoe UI",Arial,sans-serif;border:1.5px solid #16294d;background:#fff;color:#16294d;padding:8px 13px;border-radius:999px;margin-left:6px}
.tog button[aria-pressed=true]{background:#16294d;color:#fff}
.card{background:#fff;border-radius:14px;padding:22px 20px;box-shadow:0 1px 3px rgba(22,41,77,.12)}
h1{font-size:22px;margin:0 0 8px}
h2{font:700 13px "Segoe UI",Arial,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:#16294d;margin:26px 0 8px}
.lead{font-weight:700;font-size:21px;line-height:1.3;color:#16294d;margin:0 0 10px}
p{margin:0 0 12px}
.meta,.src{font:14px/1.45 "Segoe UI",Arial,sans-serif;color:#5a6272}
.tl{list-style:none;margin:0 0 10px;padding:0 0 0 18px;border-left:3px solid #16294d}
.tl li{margin:0 0 14px;position:relative}
.tl li:before{content:"";position:absolute;left:-26px;top:7px;width:13px;height:13px;border-radius:50%;background:#16294d}
.tl b,.tl span,.tl em{display:block}
.tl em{font:600 15px "Segoe UI",Arial,sans-serif;font-style:normal;color:#16294d}
.opts{list-style:none;padding:0;margin:0 0 12px}
.opts li{margin:0 0 12px}
.opts b{color:#16294d}
.legal{font-size:16px;border-top:1px solid #d9dde5;margin-top:18px;padding-top:14px}
.by{font:15px/1.4 "Segoe UI",Arial,sans-serif;color:#5a6272}
audio{width:100%;margin:4px 0 14px}
.callbar{position:fixed;left:0;right:0;bottom:0;padding:12px 16px;background:rgba(244,245,248,.96);border-top:1px solid #d9dde5}
.callbar a{display:block;max-width:528px;margin:0 auto;text-align:center;background:#16294d;color:#fff;text-decoration:none;font:700 18px "Segoe UI",Arial,sans-serif;padding:15px;border-radius:12px}
.gate label{display:block;font:15px/1.45 "Segoe UI",Arial,sans-serif;margin-bottom:10px}
.gate input{width:100%;font:700 22px Consolas,"Courier New",monospace;letter-spacing:.12em;text-transform:uppercase;padding:13px;border:1.5px solid #16294d;border-radius:10px;text-align:center}
.gate button{width:100%;margin-top:12px;font:700 17px "Segoe UI",Arial,sans-serif;background:#16294d;color:#fff;border:0;border-radius:10px;padding:14px}
.err{color:#8a1c1c;font:15px "Segoe UI",Arial,sans-serif;min-height:22px;margin-top:10px}
[hidden]{display:none!important}
body[data-lang=en] [data-l=es],body[data-lang=es] [data-l=en]{display:none}
</style></head>
<body data-lang="en">
<div class="urgent">Urgent &middot; Time-sensitive<span>Urgente &middot; El tiempo cuenta</span></div>
<div class="wrap">
  <div class="top">__LOGO__
    <div class="tog"><button type="button" id="b-en" aria-pressed="true">English</button><button type="button" id="b-es" aria-pressed="false">Espa&ntilde;ol</button></div></div>
  <div class="card gate" id="gate">
    <label for="code"><span data-l="en">This page is private. Enter the code printed on your letter.</span><span data-l="es">Esta p&aacute;gina es privada. Escriba el c&oacute;digo que viene en su carta.</span></label>
    <input id="code" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="12" inputmode="text">
    <button type="button" id="open"><span data-l="en">Open</span><span data-l="es">Abrir</span></button>
    <div class="err" id="err" role="alert"></div>
  </div>
  <div class="card" id="page" hidden>
    <div id="voice"></div>
    <div data-l="en" id="en"></div><div data-l="es" id="es"></div>
  </div>
</div>
<div class="callbar" id="callbar" hidden><a id="call" href="#"></a></div>
<script>
var ENC=__ENC__;
function $(i){return document.getElementById(i);}
function b2u(s){var b=atob(s),u=new Uint8Array(b.length);for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u;}
function lang(){return document.body.getAttribute('data-lang');}
function setLang(l){document.body.setAttribute('data-lang',l);document.documentElement.lang=l;
  $('b-en').setAttribute('aria-pressed',l==='en');$('b-es').setAttribute('aria-pressed',l==='es');
  try{localStorage.setItem('pgLang',l);}catch(e){} if(window.DATA)paint();}
/* Same envelope as foreclosure_leads._encrypt_multi: {enc:2,it,iv,ct,keys:[{salt,iv,ct}]}. The
   wrapped blob is JSON {mk,name}; mk is the base64 master key. */
async function unwrap(code){
  var te=new TextEncoder();
  var km=await crypto.subtle.importKey('raw',te.encode(code),'PBKDF2',false,['deriveKey']);
  var keys=ENC.keys||[];
  for(var n=0;n<keys.length;n++){var w=keys[n];
    try{
      var wk=await crypto.subtle.deriveKey({name:'PBKDF2',salt:b2u(w.salt),iterations:ENC.it||200000,hash:'SHA-256'},
        km,{name:'AES-GCM',length:256},false,['decrypt']);
      var blob=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(w.iv)},wk,b2u(w.ct));
      var meta=JSON.parse(new TextDecoder().decode(blob));
      var mk=await crypto.subtle.importKey('raw',b2u(meta.mk),{name:'AES-GCM'},false,['decrypt']);
      var pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b2u(ENC.iv)},mk,b2u(ENC.ct));
      return JSON.parse(new TextDecoder().decode(pt));
    }catch(e){}
  }
  return null;
}
function days(iso){var p=iso.split('-'),d=new Date(+p[0],+p[1]-1,+p[2]),t=new Date();
  t=new Date(t.getFullYear(),t.getMonth(),t.getDate());return Math.round((d-t)/864e5);}
function paint(){
  var D=window.DATA,l=lang();
  $('en').innerHTML=D.en;$('es').innerHTML=D.es;
  var els=document.querySelectorAll('[data-until]');
  for(var i=0;i<els.length;i++){var n=days(els[i].getAttribute('data-until')),es=els[i].closest('#es');
    els[i].textContent=n<0?'':(es?(n===0?'Es hoy.':'Faltan '+n+(n===1?' día.':' días.')):(n===0?'That is today.':n+(n===1?' day':' days')+' from today.'));}
  var src=D.voice&&D.voice[l];
  $('voice').innerHTML=src?'<audio controls preload="none" src="'+src.replace(/"/g,'')+'"></audio>':'';
  if(D.tel){$('call').href=D.tel;$('call').textContent=(l==='es'?'Llamar a ':'Call ')+D.who+' · '+D.phone;$('callbar').hidden=false;}
}
async function go(raw){
  var code=(raw||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
  if(!code)return;
  if(!(window.crypto&&crypto.subtle)){$('err').textContent='Open this page with https.';return;}
  $('err').textContent='';
  var d=await unwrap(code);
  if(!d){$('err').textContent=lang()==='es'?'Ese código no abre esta página. Revise la carta.':'That code does not open this page. Check your letter.';return;}
  window.DATA=d;$('gate').hidden=true;$('page').hidden=false;paint();
  try{history.replaceState(null,'',location.pathname);}catch(e){}
}
$('b-en').onclick=function(){setLang('en');};$('b-es').onclick=function(){setLang('es');};
$('open').onclick=function(){go($('code').value);};
$('code').addEventListener('keydown',function(e){if(e.key==='Enter')go($('code').value);});
(function(){var l=null;try{l=localStorage.getItem('pgLang');}catch(e){}
  if(!l)l=/^es/i.test(navigator.language||'')?'es':'en';setLang(l);
  if(location.hash.length>1)go(decodeURIComponent(location.hash.slice(1)));})();
</script>
</body></html>
"""


def page_html(r, today, voice=None, encrypt=None):
    """One self-contained page. Everything about the homeowner is inside the encrypted envelope; the
    clear shell carries the company name and the lock, nothing else."""
    if encrypt is None:
        import foreclosure_leads
        encrypt = foreclosure_leads._encrypt_multi
    env = encrypt(json.dumps(page_payload(r, today, voice), ensure_ascii=False), [('', r['code'])])
    logo = _logo_uri()
    head = ('<img src="%s" alt="%s">' % (logo, _e(_co()))) if logo else '<div class="co">%s</div>' % _e(_co())
    return (PAGE_TEMPLATE.replace('__LOGO__', head).replace('__CO__', _e(_co()))
            .replace('__ENC__', json.dumps(env).replace('<', '\\u003c')))


def copy_voice(folder, voice_en='', voice_es=''):
    """Copy the supplied recordings beside the pages; {} when none were supplied."""
    out = {}
    for lang, src in (('en', voice_en), ('es', voice_es)):
        if not src:
            continue
        ext = os.path.splitext(src)[1].lower()
        if not os.path.isfile(src) or ext not in VOICE_EXT:
            raise DataError('--voice-%s %r is not an audio file (%s)' % (lang, src, ' '.join(VOICE_EXT)))
        name = 'voice-%s%s' % (lang, ext)
        shutil.copyfile(src, os.path.join(folder, name))
        out[lang] = '../' + name
    return out


def write_pages(rows, today, folder, voice_en='', voice_es='', encrypt=None):
    os.makedirs(folder, exist_ok=True)
    voice = copy_voice(folder, voice_en, voice_es)
    for r in rows:
        d = os.path.join(folder, r['slug'])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'index.html'), 'w', encoding='utf-8') as fh:
            fh.write(page_html(r, today, voice, encrypt))
    return voice


# ════════════════════════════ PDF ════════════════════════════
# A piece that runs past its sheet is set one step smaller (10.4pt), then one more (9.8pt, the floor).
# Returns [tightened once, tightened twice, [cases that still do not fit]].
_FIT_JS = """() => {
  var over = function(s){
    var a = s.querySelector('.in').getBoundingClientRect(), f = s.querySelector('.ft').getBoundingClientRect();
    return a.bottom > f.top - 4;
  };
  var t1 = 0, t2 = 0, bad = [];
  Array.from(document.querySelectorAll('section.pc')).forEach(function(s){
    if (!over(s)) return;
    s.classList.add('t1');
    if (!over(s)) { t1++; return; }
    s.classList.remove('t1'); s.classList.add('t2');
    if (!over(s)) { t2++; return; }
    bad.push(s.getAttribute('data-case') || 'cover');
  });
  return [t1, t2, bad];
}"""


def render_pdf(p_html, p_pdf, expect_pages):
    """HTML -> PDF with Playwright, the way this repo renders every PDF. A piece that does not fit
    on its sheet even at the smallest type stops the run; so does a page count that is not one cover
    plus one page a piece. Returns (bytes, pages, pieces set smaller)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={'width': 816, 'height': 1056})
        pg.goto('file:///' + p_html.replace(os.sep, '/'))
        pg.emulate_media(media='print')
        pg.wait_for_timeout(500)
        t1, t2, over = pg.evaluate(_FIT_JS)
        if over:
            b.close()
            raise DataError('REFUSED: %d piece(s) do not fit on one sheet: %s'
                            % (len(over), ', '.join(over[:8])))
        pdf = pg.pdf(prefer_css_page_size=True, print_background=True,
                     margin={'top': '0', 'bottom': '0', 'left': '0', 'right': '0'})
        b.close()
    n = pdf_pages(pdf)
    if n is not None and n != expect_pages:
        raise DataError('REFUSED: the PDF has %d pages, expected %d (one cover, one page a piece)'
                        % (n, expect_pages))
    tmp = p_pdf + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(pdf)
    os.replace(tmp, p_pdf)
    return len(pdf), n, t1 + t2


def pdf_pages(data):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return None
    with pymupdf.open(stream=data, filetype='pdf') as doc:
        return doc.page_count


# ════════════════════════════ selection ════════════════════════════
def _signer_from(data_dir):
    """Signer and company from the DATA folder's sender file, through entity's own loader, when this
    checkout has none (a worktree never does). Values are used, never printed."""
    try:
        import entity
    except Exception:
        return
    keep = (entity.SENDER_FILE, entity.STATUS_FILE)
    try:
        entity.SENDER_FILE = os.path.join(data_dir, 'sender.json')
        entity.STATUS_FILE = os.path.join(data_dir, 'entity_status.json')
        s = entity.sender()
        co = entity.display_llc()[0]
        if co:
            M.SENDER['co'] = co
        if not M.SENDER.get('name') and (s.get('name') or '').strip():
            M.SENDER['name'] = s['name'].strip()
        if not M.SENDER.get('phone') and (s.get('phone') or '').strip():
            M.SENDER['phone'] = s['phone'].strip()
        return [str(s.get(k) or '') for k in ('addr_line2',)]
    except Exception:
        return
    finally:
        entity.SENDER_FILE, entity.STATUS_FILE = keep


def _never_strings():
    """The return address's second line, read through entity so it is never typed here."""
    try:
        import entity
        return [str(entity.sender().get('addr_line2') or '')]
    except Exception:
        return []


def select(days, data_dir, today, trigger_days=0):
    """(rows, drops, eligible, event_counts). The handwrite book's pool(), read from a stable copy of
    the data folder, with each row's verified facts attached."""
    snap = snapshot(data_dir)
    prev = M.set_data_dir(snap)
    try:
        opt = M._optout_keys()
        if not opt:
            raise DataError('REFUSED: the opt-out ledger is missing or empty. Nothing was built.')
        rows, drops = M.pool(days, opt)
        idx = facts_index(snap)
    finally:
        M.set_data_dir(prev)
        shutil.rmtree(snap, ignore_errors=True)
    out = []
    drops['case dismissed or closed'] = 0
    for r in rows:
        r['facts'] = lead_facts(r['case'], idx, today)
        if r['facts']['closed']:
            drops['case dismissed or closed'] += 1
            continue
        out.append(r)
    counts = {'filed (notice of the case recorded)': sum(1 for r in out if r['facts']['filed']),
              'final judgment entered': sum(1 for r in out if r['facts']['judgment']),
              'sale date set (ahead)': sum(1 for r in out if r['facts']['sale'])}
    eligible = len(out)
    if trigger_days:
        kept = []
        for r in out:
            ev = recent_event(r['facts'], trigger_days, today)
            if ev:
                r['event'] = ev[0]
                kept.append(r)
        out = kept
    return out, drops, eligible, counts


def sample_rows(today):
    """Invented people only. Three plain pieces, three with the QR and the court-event strip (the
    fullest each layout gets), and one calendar assignment with no sale date, which falls back."""
    def d(n):
        return today + dt.timedelta(days=n)

    def six_row_month():
        # A month that needs six calendar rows, the tallest the calendar page gets.
        y, m = today.year, today.month
        for _ in range(24):
            m += 1
            if m > 12:
                y, m = y + 1, 1
            if len(month_grid(y, m)) == 6:
                return dt.date(y, m, 21)
        return d(60)

    def row(k, owner, addr, city, zipc, county, assigned, filed, sale=None, judgment=None, **kw):
        case = 'SAMPLE-26-%06d' % k
        r = {'case': case, 'owner': owner, 'first': M._first_name(owner), 'addr': addr, 'city': city,
             'zip': zipc, 'age': (today - filed).days, 'plaintiff': '', 'assigned': assigned,
             'facts': {'case': case, 'county': county, 'filed': filed, 'sale': sale,
                       'judgment': judgment, 'closed': False},
             'code': 'SAMPLE%02d' % (20 + k), 'slug': 'sample-%02d' % k}
        r.update(kw)
        return r

    return [
        row(1, 'SAMPLE,MARIA', '100 NW 1ST AVE', 'Fort Lauderdale', '33301', 'Broward', 'A', d(-9),
            with_qr=False),
        row(2, 'EJEMPLO,CARLOS', '200 SW 2ND ST', 'Miami', '33130', 'Miami-Dade', 'B', d(-210), sale=d(32),
            judgment=d(-40), with_qr=False),
        row(3, 'MUESTRA,ANA', '300 NE 3RD CT', 'Hollywood', '33020', 'Broward', 'C', d(-12), with_qr=False),
        row(4, 'INVENTADO,JOSE', '400 SE 4TH TER', 'Pompano Beach', '33060', 'Broward', 'A', d(-5),
            with_qr=True, event='filed'),
        row(5, 'FICTICIA,ROSA', '500 NW 5TH PL APT 12', 'Hialeah', '33010', 'Miami-Dade', 'B', d(-300),
            sale=six_row_month(), judgment=d(-6), with_qr=True, event='judgment'),
        row(6, 'PRUEBA,LUIS', '600 SW 6TH AVE', 'Homestead', '33030', 'Miami-Dade', 'C', d(-260),
            sale=d(45), judgment=d(-20), with_qr=True, event='judgment'),
        row(7, 'DEMO,ELENA', '700 NE 7TH ST', 'Plantation', '33317', 'Broward', 'B', d(-14), with_qr=False),
    ]


# ════════════════════════════ run ════════════════════════════
def run(a):
    today = dt.date.today()
    sample = bool(a.sample)
    tag = 'SAMPLE' if sample else today.isoformat()
    data_dir = os.path.abspath(a.data_dir) if a.data_dir else M.data_dir()

    never = []
    if sample:
        M.SENDER['name'] = a.signer_name or 'Sam Sample'
        M.SENDER['phone'] = a.signer_phone or '(305) 555-0100'
    else:
        if a.data_dir:
            never = _signer_from(data_dir) or []
        never = never or _never_strings()
        if a.signer_name:
            M.SENDER['name'] = a.signer_name
        if a.signer_phone:
            M.SENDER['phone'] = a.signer_phone
    if a.tracking_phone:
        M.MAIL_TRACKING_PHONE = a.tracking_phone.strip()
    if not (M.SENDER.get('name') and M.contact_phone()):
        print('REFUSED: no signer name or phone (sender.json, --signer-name / --signer-phone). '
              'Nothing printed.')
        return 2

    base_url = a.page_base_url or default_base_url()
    ledger_path = P.out('mail_variants_SAMPLE.json' if sample else LEDGER)
    pages_dir = P.out(PAGES_DIR + ('_SAMPLE' if sample else ''))
    info = {'with_qr': bool(a.with_qr), 'base_url': base_url, 'trigger': a.trigger_days,
            'never': never, 'ledger': os.path.basename(ledger_path)}
    try:
        if sample:
            rows, drops, counts = sample_rows(today), {}, {}
            info['eligible'] = len(rows)
            ledger = {}
        else:
            ledger = load_ledger(ledger_path)
            rows, drops, eligible, counts = select(a.days, data_dir, today, a.trigger_days)
            info['eligible'] = eligible
            n = a.limit if a.limit else int(a.budget // M.PER_LETTER)
            rows = rows[:n]
            if not rows:
                print('no eligible pieces: widen --days%s' % (' or --trigger-days' if a.trigger_days else ''))
                return 0
            arms = assign([r['case'] for r in rows],
                          {c: v.get('assigned') for c, v in ledger.items() if isinstance(v, dict)})
            for r in rows:
                old = ledger.get(r['case']) if isinstance(ledger.get(r['case']), dict) else {}
                r['assigned'] = arms[r['case']]
                r['code'] = old.get('code') or new_code()
                r['slug'] = old.get('page') or new_slug()
        info['trigger_counts'] = ', '.join(
            '%s %d' % (k, sum(1 for r in rows if r.get('event') == k)) for k in EVENTS
            if any(r.get('event') == k for r in rows))
        printed_variants(rows)
        if a.with_qr and not qr_svg('https://example.invalid/'):
            print('WARNING: --with-qr asked for but no QR library is importable (segno). Pieces carry '
                  'the phone number instead.')
        doc, pieces = build_html(rows, today, info)

        if not a.no_pages:
            write_pages(rows, today, pages_dir, a.voice_en, a.voice_es)
        p_html = P.out('BSG_Mail_Variants_%s.html' % tag)
        p_pdf = P.out('BSG_Mail_Variants_%s.pdf' % tag)
        with open(p_html, 'w', encoding='utf-8') as fh:
            fh.write(doc)
        pdf_note = 'skipped (--no-pdf)'
        if not a.no_pdf:
            size, pages, small = render_pdf(p_html, p_pdf, len(rows) + 1)
            pdf_note = '%s (%.0f KB, %s pages%s)' % (
                p_pdf, size / 1024.0, pages if pages else '?',
                ', %d set in smaller type to fit' % small if small else '')
        for r in rows:
            ledger[r['case']] = {'assigned': r['assigned'], 'variant': r['variant'],
                                 'date': today.isoformat(), 'code': r['code'], 'page': r['slug'],
                                 **({'fallback': r['fallback']} if r['fallback'] else {}),
                                 **({'event': r['event']} if isinstance(r.get('event'), str) else {})}
        save_ledger(ledger_path, ledger)
    except DataError as e:
        print(str(e))
        return 2

    printed = {v: sum(1 for r in rows if r['variant'] == v) for v in VARIANTS}
    assigned = {v: sum(1 for r in rows if r['assigned'] == v) for v in VARIANTS}
    print('%d pieces (of %d eligible%s)' % (len(rows), info['eligible'],
                                            '' if sample else ', filed within %dd' % a.days))
    print('  assigned: ' + '  '.join('%s %d' % (v, assigned[v]) for v in VARIANTS))
    print('  printed : ' + '  '.join('%s %d' % (v, printed[v]) for v in VARIANTS))
    fell = sum(1 for r in rows if r['fallback'])
    if fell:
        print('  %d calendar assignments had no sale date on record and print as A or C' % fell)
    if counts:
        print('  court events in the eligible pool: ' + ', '.join('%s %d' % kv for kv in counts.items()))
    if a.trigger_days:
        print('  court-triggered, last %d days: %s' % (a.trigger_days, info['trigger_counts'] or 'none'))
    if drops:
        print('  dropped: ' + (', '.join('%s %d' % (k, v) for k, v in sorted(drops.items()) if v) or 'none'))
    print('  postage estimate: $%.2f at $%.2f a piece' % (len(rows) * M.PER_LETTER, M.PER_LETTER))
    print('  QR codes: %s' % ('printed' if a.with_qr else 'off (phone number on every piece)'))
    print('wrote', p_html)
    print('wrote', pdf_note)
    if not a.no_pages:
        print('wrote %d private pages under %s (not published)' % (len(rows), pages_dir))
    print('wrote', ledger_path, '(assignment only; nothing is marked as mailed)')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--days', type=int, default=14, help='max age of the filing, same as the handwrite book')
    ap.add_argument('--budget', type=float, default=80.0, help='postage budget in dollars')
    ap.add_argument('--limit', type=int, default=0, help='hard cap on pieces (overrides budget)')
    ap.add_argument('--trigger-days', type=int, default=0,
                    help='only leads with a court event in the last N days; the piece names the event')
    ap.add_argument('--data-dir', default='', help='read the lead files from this folder (read-only)')
    ap.add_argument('--signer-name', default='')
    ap.add_argument('--signer-phone', default='', help='callback number printed on every piece')
    ap.add_argument('--tracking-phone', default='', help='a number used only on this campaign')
    ap.add_argument('--with-qr', action='store_true',
                    help='print the private-page QR and code (only once the pages are published)')
    ap.add_argument('--page-base-url', default='', help='where the private pages will be served from')
    ap.add_argument('--voice-en', default='', help='recording played on the English page')
    ap.add_argument('--voice-es', default='', help='recording played on the Spanish page')
    ap.add_argument('--no-pages', action='store_true', help='skip the private pages')
    ap.add_argument('--no-pdf', action='store_true', help='write the HTML only')
    ap.add_argument('--sample', action='store_true', help='invented people; BSG_Mail_Variants_SAMPLE.pdf')
    return run(ap.parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
