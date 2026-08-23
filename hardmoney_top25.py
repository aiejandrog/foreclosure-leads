#!/usr/bin/env python
"""hardmoney_top25 — the call sheet. The N best refi candidates, one card each, ready to dial.

WHY A SEPARATE ARTIFACT FROM THE BALLOON BOOK
The book is inventory: ~2,500 loans, LTV-graded, sorted fundable-first. Nobody works a 150-row
table. This is the top of that book turned into something a person actually holds while talking:
one card per borrower, the human's name at the top, the exact opening line underneath, and space
to write what they said.

SELECTION, IN ORDER
 1. Must FIT the DSCR box  — verdict DSCR-STRONG / DSCR-EDGE / DSCR-ANY (<=80% LTV vs county roll)
 2. Must have a NAMED HUMAN — a Sunbiz officer or registered agent. An LLC with no human is not a
    call, it is a research task; it belongs in the book, not on the call sheet.
 3. Urgency first          — past the 2-year wall (20-30mo) outranks everything, then loan size.
 4. ONE CARD PER LLC       — the same borrower with three mortgages is one phone call, not three
    slots. Their other loans are printed on their card so the caller sees the whole relationship.

THE PHONE PROBLEM, STATED PLAINLY
Sunbiz publishes officer NAMES and ADDRESSES. It does not publish phone numbers — verified live:
0 of 25 on the first build. So this ships mail-ready and knock-ready by default, and phones are an
opt-in `--phones` run through the same Tracerfy path skiptrace.py uses (~$0.10 a head, misses free,
metered by the shared bd_budget ledger). Default is OFF: no run of this script spends money unless
somebody typed --phones.

Run:  python hardmoney_top25.py                      # build the sheet (free)
      python hardmoney_top25.py --n 40               # a bigger sheet
      python hardmoney_top25.py --phones --dry-run   # what it would spend, spends nothing
      python hardmoney_top25.py --phones             # ALSO skip-trace them (~$0.10 each)
"""
import argparse
import datetime
import html as H
import json
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hardmoney_values as HV                              # noqa: E402

ENR = os.path.join(HERE, 'hardmoney_enriched.json')
PHONES = os.path.join(HERE, '_hm_phones.json')             # gitignored sidecar, same posture as skiptrace


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


# A Sunbiz "officer" is very often ANOTHER COMPANY — a manager-LLC holding the managing seat. The
# first build greeted one of them "Hi Peoples," (PEOPLES CHOICE APARTMENTS LLC). An entity is not a
# person to say hello to, so it fails the named-human gate and the card falls through to the agent.
ENTITY = re.compile(r'\b(LLC|L\.L\.C|INC|CORP|CO|LP|LLP|LTD|TRUST|HOLDINGS|PROPERTIES|GROUP|'
                    r'ENTERPRISES|INVESTMENTS?|VENTURES?|REALTY|CAPITAL|PARTNERS|MANAGEMENT|'
                    r'ASSOCIATES|FUND)\b', re.I)


def _person(name):
    """(display, first_name) for a real human, or None if this is an entity.

    Sunbiz writes people as 'LAST, FIRST MIDDLE'. Taking token[0] as the first name greets
    Erica Yoonsun Lee as 'Hi Lee' — the surname — which is exactly how a cold call dies in the
    first two seconds. Split on the comma when there is one.
    """
    n = ' '.join(str(name or '').split())
    if not n or ENTITY.search(n):
        return None
    if ',' in n:
        last, given = [p.strip() for p in n.split(',', 1)]
        first = (given.split() or [''])[0]
        disp = ' '.join(x for x in (given.title(), last.title()) if x)
    else:
        parts = n.split()
        first = parts[0]
        disp = n.title()
    if not first:
        return None
    return disp, first.title()


def human_of(b, enr):
    """(display_name, title, address) for the person to call, or ('','','') when there is none."""
    sb = (enr.get(b) or {}).get('sunbiz') or {}
    for key, addr_key, title in (('officer', 'officer_addr', sb.get('title') or 'Officer'),
                                 ('agent', 'agent_addr', 'Registered agent')):
        p = _person(sb.get(key))
        if p:
            return p[0], title, (sb.get(addr_key) or '')
    return '', '', ''


def first_name_of(b, enr):
    sb = (enr.get(b) or {}).get('sunbiz') or {}
    for key in ('officer', 'agent'):
        p = _person(sb.get(key))
        if p:
            return p[1]
    return 'there'


def select(n=25):
    """The top N, deduped by LLC, with every other loan that borrower carries attached."""
    enr = _load(ENR, {})
    priced = HV.priced(zone_only=True)

    by_borrower = {}
    for r in priced:
        by_borrower.setdefault(r['borrower'], []).append(r)

    fits = [r for r in priced if r['fits'] and human_of(r['borrower'], enr)[0]]
    fits.sort(key=lambda r: ((20 <= r['age_mo'] <= 30), r['amt']), reverse=True)

    out, seen = [], set()
    for r in fits:
        b = r['borrower']
        if b in seen:
            continue
        seen.add(b)
        name, title, addr = human_of(b, enr)
        others = [o for o in by_borrower.get(b, []) if o.get('origin') != r.get('origin')
                  or o.get('amt') != r.get('amt')]
        row = dict(r)
        row.update({'human': name, 'title': title, 'human_addr': addr,
                    'first': first_name_of(b, enr),
                    'others': others, 'save_yr': int(r['amt'] * 0.04)})
        out.append(row)
        if len(out) >= n:
            break
    return out


def opener(r):
    """The first ten seconds, written out. A caller who has to improvise this will not make 25 calls."""
    first = r.get('first') or 'there'
    return ('Hi %s, this is Alejandro with Biscayne Solutions Group. I work with investors on Broward '
            'rental property. I pulled the public record on %s and it looks like the %s loan on %s '
            'is coming up on its balloon. I place those into a 30-year fixed around 6 to 8 percent, '
            'no income documents, underwritten on the property\'s own rent. On a loan your size '
            'that is roughly $%s a year. Is that loan still outstanding?'
            % (first, r['borrower'].title(), (r['lender'] or 'private').title(),
               (r.get('addr') or 'the property').title(), format(r['save_yr'], ',')))


CSS = """
body{font:13.5px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#0e1b33;margin:0;padding:22px}
h1{font-size:22px;margin:0 0 2px} .sub{color:#5a6577;margin-bottom:14px;font-size:12.5px}
.how{background:#f3f6fb;border:1px solid #dbe3ef;border-radius:10px;padding:12px 15px;margin:0 0 18px}
.how b{display:block;margin-bottom:5px} .how li{margin:3px 0}
.card{border:1px solid #d8e0ee;border-radius:11px;padding:13px 15px;margin:0 0 13px;
      page-break-inside:avoid;position:relative}
.rank{position:absolute;top:11px;right:13px;font-size:25px;font-weight:800;color:#e2e8f2}
.nm{font-size:17px;font-weight:800} .ttl{font-size:11.5px;color:#5a6577;text-transform:uppercase;
     letter-spacing:.04em;margin-bottom:3px}
.ph{font-size:14px;margin:2px 0} .adr{font-size:12px;color:#5a6577;margin-bottom:8px}
table.facts{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}
.facts th{text-align:left;color:#69748a;font-weight:600;font-size:11px;text-transform:uppercase;
          padding:3px 8px 3px 0;white-space:nowrap;width:1%}
.facts td{padding:3px 16px 3px 0} .big{font-size:15px;font-weight:800}
.grn{color:#0b5d1e;font-weight:800}
.v{background:#dff5e3;color:#0b5d1e;padding:2px 7px;border-radius:99px;font-size:11px;font-weight:700}
.oth{font-size:11.5px;color:#5a6577;background:#fafcff;border-left:3px solid #dbe3ef;
     padding:5px 9px;margin:5px 0}
.say{background:#fffbf0;border:1px solid #f0e2bd;border-radius:8px;padding:9px 12px;margin:7px 0 0;
     font-size:12.5px}
.notes{margin-top:8px;font-size:11.5px;color:#69748a}
.lines{border-bottom:1px solid #d8e0ee;height:17px;margin-top:3px}
.lines+.lines{margin-top:0} .mut{color:#9aa4b6}
"""


def build_html(rows, phones):
    today = datetime.date.today().isoformat()
    cards = []
    for i, r in enumerate(rows, 1):
        phl = ((phones.get(r['borrower']) or {}).get('phones')) or []
        phone_html = (' &middot; '.join('<b>%s</b>' % H.escape(str(p)) for p in phl[:3]) if phl
                      else '<span class="mut">no number yet &mdash; mail or knock it, '
                           'or run --phones</span>')
        others = ''
        if r['others']:
            others = ('<div class="oth"><b>Also carries:</b> ' + ' &nbsp;|&nbsp; '.join(
                '$%s %s (%.0fmo &middot; %s)' % (format(o['amt'], ','), H.escape(o['lender'][:24]),
                                                 o['age_mo'], H.escape(o['verdict']))
                for o in r['others'][:4]) + '</div>')
        prop = ', '.join(x for x in ((r.get('addr') or ''), (r.get('city') or '')) if x)
        cards.append(
            '<div class="card"><div class="rank">%d</div>'
            '<div class="nm">%s</div><div class="ttl">%s &middot; %s</div>'
            '<div class="ph">%s</div><div class="adr">%s</div>'
            '<table class="facts">'
            '<tr><th>Loan</th><td class="big">$%s</td><th>LTV</th><td class="big">%s%%</td>'
            '<th>Verdict</th><td><span class="v">%s</span></td></tr>'
            '<tr><th>Lender</th><td>%s</td><th>Originated</th><td>%s &middot; %.0f mo old</td>'
            '<th>Saves/yr</th><td class="grn">$%s</td></tr>'
            '<tr><th>Property</th><td colspan="5">%s%s</td></tr></table>%s'
            '<div class="say"><b>Open with this:</b><br>%s</div>'
            '<div class="notes"><b>What they said:</b>'
            '<div class="lines"></div><div class="lines"></div></div></div>'
            % (i, H.escape(r['human']), H.escape(r['title']), H.escape(r['borrower']),
               phone_html, H.escape(r['human_addr'] or ''),
               format(r['amt'], ','), ('%.0f' % r['ltv']) if r['ltv'] else '&mdash;',
               H.escape(r['verdict']), H.escape(r['lender']), H.escape(r['origin']), r['age_mo'],
               format(r['save_yr'], ','), H.escape(prop or 'address not pinned'),
               (' &middot; county roll $%s' % format(r['jv'], ',')) if r.get('jv') else '',
               others, H.escape(opener(r))))

    tot = sum(r['amt'] for r in rows)
    doc = """<!doctype html><html><head><meta charset="utf-8">
<title>Hard-Money Refi Call Sheet</title><style>%s</style></head><body>
<h1>Refi Call Sheet &mdash; top %d</h1>
<div class="sub">Biscayne Solutions Group &middot; built %s &middot; $%s of loans &middot;
about $%s/yr of interest on the table at 11%%&rarr;7%%</div>
<div class="how"><b>How to work this list</b><ul>
<li>These are <b>business loans to LLCs</b>, not consumer mortgages. The homeowner-rescue script and
the FS 501.1377 disclosure do not belong here &mdash; this is an investor talking to an investor.</li>
<li><b>Dial manually.</b> No autodialer, no text blast, no prerecorded message.</li>
<li>The LTV is against the county&rsquo;s <b>certified roll</b>, not an appraisal. The roll trails the
market, so these run conservative. Say &ldquo;looks like you&rsquo;d qualify, let&rsquo;s get it
appraised&rdquo; &mdash; never quote a number as final.</li>
<li>The balloon date is <b>inferred from the origination date</b>, because the real maturity date
lives inside the mortgage image. If they say the loan is already paid or refinanced, that is a
clean answer, not a bad lead &mdash; mark it and move on.</li>
<li>One card per borrower. If they carry other loans they are listed under <i>Also carries</i>
&mdash; that is the whole relationship, worth more than the one loan.</li>
</ul></div>
%s</body></html>""" % (CSS, len(rows), today, format(tot, ','),
                       format(int(tot * 0.04), ','), ''.join(cards))
    return doc, today


def run_phones(rows, dry=False):
    """Opt-in Tracerfy pass over the Sunbiz humans. Reuses skiptrace's provider plumbing so there is
    one billing path and one shared budget ledger, not a second spender."""
    import requests
    import skiptrace as ST

    prov = ST.pick_provider()
    key = ST.load_key(prov)
    cost = ST.PROVIDERS[prov]['cost']
    cache = _load(PHONES, {})

    todo = []
    for r in rows:
        if r['borrower'] in cache:
            continue
        addr = ST.parse_addr(r['human_addr'] or '')
        if not addr:
            continue                                        # no address = nothing to trace against
        todo.append((r, addr))

    print('%s · %d to trace · est $%.2f (misses are free) · %d cached'
          % (prov, len(todo), len(todo) * cost, len(rows) - len(todo)))
    if dry:
        for r, a in todo[:8]:
            print('   would trace %-26s %s' % (r['human'][:26], a))
        if len(todo) > 8:
            print('   ... and %d more' % (len(todo) - 8))
        return cache

    s = requests.Session()
    hit = 0
    for i, (r, addr) in enumerate(todo, 1):
        lead = {'_trace_addr': addr, '_trace_name': r['human'], 'case': r['borrower']}
        try:
            ph, em = ST.trace_one(s, prov, key, lead)
        except ST.TraceAborted as e:
            print('  ABORTED at %d/%d: %s' % (i, len(todo), e))
            break                                           # auth/balance — stop, do not burn more
        except Exception as e:
            print('  %s: %s' % (r['human'][:22], str(e)[:60]))
            continue
        cache[r['borrower']] = {'phones': ph or [], 'emails': em or [],
                                'who': r['human'], 'ts': datetime.date.today().isoformat()}
        if ph:
            hit += 1
        if i % 5 == 0 or i == len(todo):
            print('  %d/%d traced, %d with a number' % (i, len(todo), hit))
    json.dump(cache, open(PHONES, 'w', encoding='utf-8'), indent=1)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=25)
    ap.add_argument('--phones', action='store_true', help='ALSO skip-trace the humans (costs money)')
    ap.add_argument('--dry-run', action='store_true', help='with --phones: show spend, spend nothing')
    a = ap.parse_args()

    rows = select(a.n)
    print('%d card(s) selected — every one fits the DSCR box AND has a named human' % len(rows))

    phones = _load(PHONES, {})
    if a.phones:
        phones = run_phones(rows, dry=a.dry_run)

    doc, today = build_html(rows, phones)
    out = os.path.join(HERE, 'Refi_Call_Sheet_%s.html' % today)
    open(out, 'w', encoding='utf-8').write(doc)
    withph = sum(1 for r in rows if (phones.get(r['borrower']) or {}).get('phones'))
    print('  %d of %d have a phone number' % (withph, len(rows)))
    print('-> %s' % out)
    print('PDF:  python _mkpdf.py "%s"' % os.path.basename(out))


if __name__ == '__main__':
    main()
