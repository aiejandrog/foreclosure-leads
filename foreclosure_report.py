#!/usr/bin/env python
"""foreclosure_report.py -- the monthly South Florida Foreclosure Report, from the board, no PII.

WHY THIS EXISTS (2026-09-06). bsgflorida.com competes against law firms with ten years of
backlinks and 2,000-word pages. Nothing we write about "how foreclosure works" beats that on
authority. What nobody else publishes is NUMBERS: how many sales are on the calendar this month
in each county, how many are HOA cases, how many are new filings with no date yet, the median
judgment, the share that already survived one sale date. DealFlow already has all of it in the
board twin, refreshed nightly. Publishing the aggregate every month is the one piece of content
on the site that is (a) unique, (b) fresh on a schedule, (c) citable by reporters and Reddit,
and (d) impossible for a competitor to copy without building the pipeline.

PRIVACY, load-bearing. This file reads homeowner rows and must emit ONLY aggregates. No case
numbers, no names, no addresses, no folios, no plaintiff below MIN_BUCKET, no city bucket below
MIN_BUCKET. If you add a metric, ask whether a single row could be recovered from it. The
generated files are safe to commit and to paste into the public site; the input is not.

Reads:   P.TWIN (the unified board twin that ledger_sync carries to both machines)
Writes:  P.out('reports/foreclosure-report-YYYY-MM.json') and .md (EN + ES page copy)

Run:     python foreclosure_report.py            # current month
         python foreclosure_report.py --print    # also print the markdown
Never raises past main(): a broken report must not break the nightly.
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics
import sys

import paths as P

MIN_BUCKET = 5          # smallest group we will name (cities, plaintiffs)
TOP_N = 8


def load_board(path):
    src = open(path, encoding='utf-8').read()
    m = re.search(r'(?:var|const|let)\s+RAW\s*=\s*', src)
    if not m:
        raise RuntimeError('RAW not found in %s' % path)
    rows, _ = json.JSONDecoder().raw_decode(src[m.end():])
    return rows


def _sale_date(r):
    try:
        return dt.datetime.strptime(r.get('auction') or '', '%m/%d/%Y').date()
    except ValueError:
        return None


def _city(r):
    a = r.get('addr') or ''
    if ',' not in a:
        return ''
    return a.split(',')[1].strip().title()


def _bucket(counter, n=TOP_N):
    """Only groups big enough that no single household is identifiable."""
    return [(k, v) for k, v in counter.most_common() if k and v >= MIN_BUCKET][:n]


def compute(rows, today):
    C = collections.Counter
    sched = [r for r in rows if _sale_date(r) and _sale_date(r) >= today]
    next30 = [r for r in sched if (_sale_date(r) - today).days <= 30]
    fc_sched = [r for r in sched if r.get('st') == 'FC']
    counties = ('MIAMI-DADE', 'BROWARD', 'PALM BEACH')
    by_month = collections.defaultdict(lambda: C())
    for r in sched:
        d = _sale_date(r)
        by_month['%04d-%02d' % (d.year, d.month)][r.get('county')] += 1
    vals = [r['value'] for r in sched if isinstance(r.get('value'), (int, float)) and r['value'] > 0]
    judg = [r['judg'] for r in fc_sched if isinstance(r.get('judg'), (int, float)) and r['judg'] > 0]
    priced = [r['eq'] for r in fc_sched if r.get('eqstate') == 'priced' and isinstance(r.get('eq'), (int, float))]
    out = {
        'as_of': today.isoformat(),
        'source': 'DealFlow board twin (public clerk auction calendars + lis pendens filings, three counties)',
        'tracked_total': len(rows),
        'tracked_by_county': {c: sum(1 for r in rows if r.get('county') == c) for c in counties},
        'scheduled_sales': len(sched),
        'scheduled_by_county': {c: sum(1 for r in sched if r.get('county') == c) for c in counties},
        'next_30_days': len(next30),
        'next_30_by_county': {c: sum(1 for r in next30 if r.get('county') == c) for c in counties},
        'by_month': {m: dict(cnt) for m, cnt in sorted(by_month.items())},
        'new_filings_no_date': sum(1 for r in rows if r.get('st') == 'LP'),
        'tax_deed_sales': sum(1 for r in sched if r.get('st') == 'TD'),
        'hoa_condo_plaintiff': sum(1 for r in sched if r.get('ctype') in ('HOA', 'HOA/Condo')),
        'homestead_scheduled': sum(1 for r in sched if r.get('hs')),
        'survived_prior_sale': sum(1 for r in rows if (r.get('saleSurv') or 0) >= 1),
        'bankruptcy_stay_active': sum(1 for r in rows if r.get('saleBkAct')),
        'median_value': int(statistics.median(vals)) if vals else None,
        'median_value_n': len(vals),
        'median_judgment': int(statistics.median(judg)) if judg else None,
        'median_judgment_n': len(judg),
        'equity_priced_n': len(priced),
        'equity_30pct_share': (round(100.0 * sum(1 for e in priced if e >= 30) / len(priced)) if priced else None),
        'top_cities': _bucket(C(_city(r) for r in sched)),
        'top_plaintiffs': _bucket(C((r.get('plaintiff') or '').strip().upper() for r in fc_sched)),
    }
    return out


def _pct(a, b):
    return int(round(100.0 * a / b)) if b else 0


def render_md(m):
    d = dt.date.fromisoformat(m['as_of'])
    month = d.strftime('%B %Y')
    sb, nb = m['scheduled_by_county'], m['next_30_by_county']
    md = []
    A = md.append
    A('# South Florida Foreclosure Report: %s' % month)
    A('')
    A('*Miami-Dade, Broward and Palm Beach counties. Counts are cases Biscayne Solutions Group '
      'tracks from the public clerk auction calendars and lis pendens filings as of %s. They are '
      'not official county totals, and they change daily as sales are cancelled, reset, or added.*'
      % d.strftime('%B %d, %Y'))
    A('')
    A('## The numbers this month')
    A('')
    A('| | Miami-Dade | Broward | Palm Beach | All three |')
    A('|---|---:|---:|---:|---:|')
    A('| Foreclosure and tax-deed sales on the calendar | %d | %d | %d | **%d** |' % (sb['MIAMI-DADE'], sb['BROWARD'], sb['PALM BEACH'], m['scheduled_sales']))
    A('| Sales inside the next 30 days | %d | %d | %d | **%d** |' % (nb['MIAMI-DADE'], nb['BROWARD'], nb['PALM BEACH'], m['next_30_days']))
    A('')
    A('- **%d new foreclosure filings** (lis pendens) with no sale date yet. These owners have the most time and the most options.' % m['new_filings_no_date'])
    A('- **%d of the scheduled sales are homesteaded** properties (%d%%): a family home, not an investment.' % (m['homestead_scheduled'], _pct(m['homestead_scheduled'], m['scheduled_sales'])))
    A('- **%d sales were filed by a homeowner or condo association**, not a bank (%d%%). An association foreclosure often leaves the first mortgage in place, which changes everything about the math.' % (m['hoa_condo_plaintiff'], _pct(m['hoa_condo_plaintiff'], m['scheduled_sales'])))
    A('- **%d are tax-deed sales**, a different process with a different clock.' % m['tax_deed_sales'])
    if m['median_judgment']:
        A('- **Median final judgment: $%s** (%d bank cases with a judgment on record).' % (format(m['median_judgment'], ','), m['median_judgment_n']))
    if m['median_value']:
        A('- **Median property value: $%s** (%d scheduled sales with a county value).' % (format(m['median_value'], ','), m['median_value_n']))
    if m['equity_30pct_share'] is not None:
        A('- Of the %d cases where we traced the recorded debt, **%d%% still show at least 30%% owner equity**. That equity is what an auction takes.' % (m['equity_priced_n'], m['equity_30pct_share']))
    A('- **%d properties have already survived at least one sale date** (cancelled or reset), and %d have an active bankruptcy stay.' % (m['survived_prior_sale'], m['bankruptcy_stay_active']))
    A('')
    A('## Sales by month')
    A('')
    A('| Month | Miami-Dade | Broward | Palm Beach |')
    A('|---|---:|---:|---:|')
    for mo, cnt in m['by_month'].items():
        A('| %s | %d | %d | %d |' % (dt.date.fromisoformat(mo + '-01').strftime('%b %Y'), cnt.get('MIAMI-DADE', 0), cnt.get('BROWARD', 0), cnt.get('PALM BEACH', 0)))
    A('')
    if m['top_cities']:
        A('## Where the sales are')
        A('')
        A(', '.join('%s (%d)' % (c, n) for c, n in m['top_cities']) + '.')
        A('')
    if m['top_plaintiffs']:
        A('## Who is foreclosing')
        A('')
        A('The plaintiffs with the most scheduled bank sales this month: ' + ', '.join('%s (%d)' % (p.title(), n) for p, n in m['top_plaintiffs']) + '.')
        A('')
    A('## What the numbers mean if one of them is you')
    A('')
    A('A sale date is a deadline, not a verdict. Between the filing and the sale there is a docket, a recorded '
      'lien chain, a judgment amount, and usually more than one way out: catch the loan up, rework it, sell on '
      'your terms before the date, or, if the sale happens, claim any surplus that belongs to you. Which one fits '
      'depends on what the county file actually says. We read it with you, free, in English or Spanish. '
      'We are not attorneys, not a lender, and not affiliated with your lender, servicer, or any government agency.')
    A('')
    A('**Book a free records review** or call or text (786) 631-1823.')
    A('')
    A('## Methodology')
    A('')
    A('Counts come from the three county clerks\' online foreclosure and tax-deed auction calendars and from '
      'recorded lis pendens filings, refreshed nightly. Values are county-appraiser market values; judgments are '
      'the final judgment amounts on the clerk record; equity is priced only where the recorded mortgage chain '
      'was traced in the official records. Groups smaller than %d are not shown. No individual case is identified.' % MIN_BUCKET)
    return '\n'.join(md)


def render_md_es(m):
    d = dt.date.fromisoformat(m['as_of'])
    meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
    month = '%s de %d' % (meses[d.month - 1], d.year)
    sb, nb = m['scheduled_by_county'], m['next_30_by_county']
    md = []
    A = md.append
    A('# Informe de ejecuciones hipotecarias en el Sur de Florida: %s' % month)
    A('')
    A('*Condados de Miami-Dade, Broward y Palm Beach. Son los casos que Biscayne Solutions Group sigue en los '
      'calendarios públicos de subastas de los clerks y en las presentaciones de lis pendens al %d de %s de %d. '
      'No son totales oficiales del condado y cambian a diario.*' % (d.day, meses[d.month - 1], d.year))
    A('')
    A('## Las cifras de este mes')
    A('')
    A('| | Miami-Dade | Broward | Palm Beach | Los tres |')
    A('|---|---:|---:|---:|---:|')
    A('| Subastas hipotecarias y de tax deed en el calendario | %d | %d | %d | **%d** |' % (sb['MIAMI-DADE'], sb['BROWARD'], sb['PALM BEACH'], m['scheduled_sales']))
    A('| Subastas en los próximos 30 días | %d | %d | %d | **%d** |' % (nb['MIAMI-DADE'], nb['BROWARD'], nb['PALM BEACH'], m['next_30_days']))
    A('')
    A('- **%d casos nuevos** (lis pendens) todavía sin fecha de subasta. Estos dueños tienen más tiempo y más opciones.' % m['new_filings_no_date'])
    A('- **%d de las subastas programadas son propiedades con homestead** (%d%%): la casa de una familia.' % (m['homestead_scheduled'], _pct(m['homestead_scheduled'], m['scheduled_sales'])))
    A('- **%d subastas las presentó una asociación de condominio o de vecinos**, no un banco (%d%%). En esos casos la primera hipoteca casi siempre sigue viva, y eso cambia toda la cuenta.' % (m['hoa_condo_plaintiff'], _pct(m['hoa_condo_plaintiff'], m['scheduled_sales'])))
    A('- **%d son subastas de tax deed**, un proceso distinto con otro reloj.' % m['tax_deed_sales'])
    if m['median_judgment']:
        A('- **Sentencia final mediana: $%s** (%d casos bancarios con sentencia registrada).' % (format(m['median_judgment'], ','), m['median_judgment_n']))
    if m['median_value']:
        A('- **Valor mediano de la propiedad: $%s** (%d subastas con valor del condado).' % (format(m['median_value'], ','), m['median_value_n']))
    if m['equity_30pct_share'] is not None:
        A('- De los %d casos donde rastreamos la deuda registrada, **el %d%% todavía muestra al menos 30%% de equidad del dueño**. Esa equidad es lo que se pierde en la subasta.' % (m['equity_priced_n'], m['equity_30pct_share']))
    A('- **%d propiedades ya sobrevivieron al menos una fecha de subasta** (cancelada o reprogramada), y %d tienen una suspensión activa por bancarrota.' % (m['survived_prior_sale'], m['bankruptcy_stay_active']))
    A('')
    A('## Subastas por mes')
    A('')
    A('| Mes | Miami-Dade | Broward | Palm Beach |')
    A('|---|---:|---:|---:|')
    for mo, cnt in m['by_month'].items():
        dd = dt.date.fromisoformat(mo + '-01')
        A('| %s %d | %d | %d | %d |' % (meses[dd.month - 1].title(), dd.year, cnt.get('MIAMI-DADE', 0), cnt.get('BROWARD', 0), cnt.get('PALM BEACH', 0)))
    A('')
    if m['top_cities']:
        A('## Dónde están las subastas')
        A('')
        A(', '.join('%s (%d)' % (c, n) for c, n in m['top_cities']) + '.')
        A('')
    A('## Qué significan estas cifras si una de ellas es usted')
    A('')
    A('Una fecha de subasta es un plazo, no un veredicto. Entre la demanda y la subasta hay un expediente, una '
      'cadena de gravámenes registrada, un monto de sentencia y casi siempre más de una salida: ponerse al día, '
      'reestructurar el préstamo, vender en sus términos antes de la fecha o, si la subasta ocurre, reclamar el '
      'excedente que le pertenece. Cuál le sirve depende de lo que dice el expediente del condado. Lo leemos con '
      'usted, gratis, en español o en inglés. No somos abogados, no somos prestamistas y no estamos afiliados a su '
      'prestamista, a su administrador ni a ninguna agencia del gobierno.')
    A('')
    A('**Reserve su revisión de registros gratis** o llame o escriba al (786) 631-1823.')
    A('')
    A('## Metodología')
    A('')
    A('Los conteos provienen de los calendarios de subastas hipotecarias y de tax deed de los tres clerks y de los '
      'lis pendens registrados, actualizados cada noche. Los valores son los del tasador del condado; las '
      'sentencias son los montos finales del registro del clerk; la equidad se calcula solo donde se rastreó la '
      'cadena hipotecaria en los registros oficiales. No se muestran grupos menores de %d. Ningún caso se identifica.' % MIN_BUCKET)
    return '\n'.join(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--print', action='store_true')
    ap.add_argument('--twin', default=None, help='override board twin path')
    a = ap.parse_args()
    today = dt.date.today()
    twin = a.twin or str(P.TWIN)
    rows = load_board(twin)
    m = compute(rows, today)
    outdir = P.out('reports')
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, 'foreclosure-report-%s' % today.strftime('%Y-%m'))
    json.dump(m, open(stem + '.json', 'w', encoding='utf-8'), indent=1)
    md = render_md(m)
    open(stem + '.md', 'w', encoding='utf-8').write(md)
    open(stem + '.es.md', 'w', encoding='utf-8').write(render_md_es(m))
    print('wrote %s.json / .md / .es.md  (%d rows -> %d scheduled sales, %d in 30 days)'
          % (stem, m['tracked_total'], m['scheduled_sales'], m['next_30_days']))
    if a.print:
        try:
            print(md)
        except UnicodeEncodeError:
            print(md.encode('ascii', 'replace').decode('ascii'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('foreclosure_report failed: %s' % e)
        sys.exit(0)
