"""miami_ranking: fresh auction, appraiser, tax and contact facts, then an evidence-qualified list.

Priority 7 of the Miami automation goal. Everything before this step reads the court and recorded
record; this step asks what is true TODAY and holds every case the evidence does not carry.

    python -u miami_ranking.py --all                  # every Miami case with a saved dossier
    python -u miami_ranking.py --case 2025-023462-CA-01
    python -u miami_ranking.py --all --refresh-appraiser --refresh-tax

FACTS, each with the date it was read and whether that is fresh enough to act on
  auction    the auction calendar (auction_archive last_seen against the newest scrape) joined to
             the docket status. A past sale date is 'past_date_outcome_unknown', never sold: only a
             certificate on the docket says a sale happened. A future date missing from the newest
             calendar is 'dropped_from_calendar_unknown' (cancelled or reset; we cannot tell which).
  appraiser  ownership_gate's live Miami-Dade appraiser owner and sales history (7-day cache),
             compared with the present-title grantees.
  tax        county_taxes' Tax Collector read for the folio (amount due, unpaid years, a sold
             certificate). Reported as facts. No priority or survival conclusion is drawn here.
  contact    the skip-trace record for the case, and whether the name traced is a person on the
             current deed. An entity-only owner is never call-ready.

QUALIFIED means every fact is fresh and none of them holds the case. Everything else is HELD with
the reasons, sorted so the case closest to qualifying comes first. Qualified cases rank by sale
date, soonest first, then by fewest open evidence gaps. Equity is not an input: #51 owns equity,
and a figure from a document is not an equity input until the 12-case review passes.

"Qualified" is an evidence statement, not permission to contact: the board's opt-out, DNC and
send-time gates still decide every call and message. Nothing here reads or changes them.

CHANGE ALERTS: each run is compared with the previous report and every changed fact is listed
(sale date, calendar state, appraiser owner, tax due or certificate, docket status, controlling
judgment, qualification).

Every source is a free public page; there is no paid call in this module. Without --refresh-*
it reads caches only and makes no request.
"""
import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
COUNTY = 'MIAMI-DADE'
CALENDAR_MAX_AGE = 2      # the calendar is scraped nightly; two days covers one missed night
APPRAISER_MAX_AGE = 7     # ownership_gate.TTL_DAYS
TAX_MAX_AGE = 30
TRACE_MAX_AGE = 180
_CLOSED = {'sold': 'sold_per_docket', 'dismissed': 'case_dismissed_per_docket',
           'satisfied_redeemed': 'satisfied_per_docket', 'sale_cancelled': 'cancelled_per_docket'}


def _day(value):
    if isinstance(value, date):
        return value
    text = str(value or '').strip()
    for parse in (lambda t: date.fromisoformat(t[:10]),
                  lambda t: date(int(t.split('/')[2][:4]), int(t.split('/')[0]), int(t.split('/')[1]))):
        try:
            return parse(text)
        except (ValueError, IndexError):
            pass
    return None


def _age(when, as_of):
    when = _day(when)
    return None if when is None else (as_of - when).days


def auction_fact(lead, archive_entry, calendar_day, timeline, as_of):
    lead = lead or {}
    archive_entry = archive_entry or {}
    sale = _day(lead.get('AuctionDate') or lead.get('auction') or archive_entry.get('auction'))
    age = _age(calendar_day, as_of)
    on_calendar = bool(archive_entry and calendar_day and archive_entry.get('last_seen') == str(calendar_day))
    status = (timeline or {}).get('status') or {}
    fact = {'sale_date': sale.isoformat() if sale else None, 'calendar_read': str(calendar_day) if calendar_day else None,
            'calendar_age_days': age, 'on_newest_calendar': on_calendar,
            'docket_status': status.get('kind'), 'fresh': age is not None and age <= CALENDAR_MAX_AGE}
    if status.get('kind') in _CLOSED:
        state = _CLOSED[status['kind']]
    elif (timeline or {}).get('stay_in_effect') is True:
        state = 'stayed'
    elif not fact['fresh']:
        state = 'stale_calendar'
    elif sale is None:
        state = 'no_sale_date'
    elif sale < as_of:
        state = 'past_date_outcome_unknown'
    elif on_calendar:
        state = 'scheduled'
    else:
        state = 'dropped_from_calendar_unknown'
    fact['state'] = state
    if sale:
        fact['days_to_sale'] = (sale - as_of).days
    return fact


def appraiser_fact(check, grantees, as_of):
    """`check` is ownership_gate.check_lead's result (or its cache entry), or None."""
    if not check:
        return {'state': 'not_checked', 'fresh': False}
    import ownership_gate as OG
    owner = str(check.get('current_owner') or '').strip()
    age = _age(check.get('ts'), as_of)
    relations = [OG.owner_relation(owner, g) for g in grantees or []] if owner else []
    agrees = ('agrees' if 'same' in relations else 'unsure' if 'unsure' in relations
              else 'differs' if relations else 'unknown')
    return {'state': check.get('title_status') or 'unverified', 'current_owner': owner or None,
            'flip_date': check.get('flip_date') or None, 'read': check.get('ts'), 'age_days': age,
            'fresh': age is not None and age < APPRAISER_MAX_AGE,
            'agrees_with_deed_grantee': agrees, 'evidence': check.get('evidence')}


def tax_fact(record, as_of):
    if not record:
        return {'state': 'not_checked', 'fresh': False}
    age = _age(record.get('checked'), as_of)
    cert = record.get('cert')
    state = 'certificate_sold' if cert else 'delinquent' if record.get('due') else 'nothing_due'
    return {'state': state, 'due': record.get('due') or 0, 'unpaid_years': [y.get('year') for y in record.get('years') or []],
            'certificate': cert, 'read': record.get('checked'), 'age_days': age,
            'fresh': age is not None and age <= TAX_MAX_AGE,
            'note': 'Tax Collector read. No priority or survival conclusion is drawn from it here.'}


def contact_fact(trace, present, as_of):
    from miami_title_parties import name_key
    import ownership_gate as OG
    present = present or {}
    persons = present.get('owner_persons') or []
    entities = present.get('owner_entities') or []
    fact = {'owner_persons': persons, 'owner_entities': [e.get('name') for e in entities]}
    if not persons and entities:
        fact.update(state='entity_only', fresh=True, call_ready=False,
                    reason='the current deed names only an entity; no person is established as able to act for it')
        return fact
    if not persons:
        fact.update(state='no_owner_candidate', fresh=False, call_ready=False)
        return fact
    if not trace:
        fact.update(state='not_traced', fresh=False, call_ready=False)
        return fact
    age = _age(trace.get('traced'), as_of)
    name = str(trace.get('name') or '')
    on_deed = any(name_key(name) == name_key(p) or (name and OG.owner_relation(name, p) == 'same')
                  for p in persons)
    phones = trace.get('phones') or []
    fact.update(traced_name=name, traced=trace.get('traced'), age_days=age, phones=len(phones),
                fresh=age is not None and age <= TRACE_MAX_AGE,
                traced_entity=trace.get('entity') or None)
    if trace.get('entity'):
        fact.update(state='traced_entity_officer', call_ready=False,
                    reason='the number belongs to an officer of %s; an officer is not the owner' % trace['entity'])
    elif not phones:
        fact.update(state='traced_no_phone', call_ready=False)
    elif not on_deed:
        fact.update(state='phone_for_name_not_on_deed', call_ready=False,
                    reason='traced %r, who is not a grantee on the current deed candidate' % name)
    else:
        fact.update(state='phone_for_person_on_deed', call_ready=fact['fresh'])
    return fact


def qualify(case, facts, timeline, present):
    """-> (qualified, reasons). Every reason is a sentence a person can act on."""
    reasons = []
    auction = facts['auction']
    if auction['state'] not in ('scheduled', 'no_sale_date'):
        reasons.append('auction: %s' % auction['state'].replace('_', ' '))
    if timeline is None:
        reasons.append('no whole-case timeline saved')
    else:
        kind = (timeline.get('status') or {}).get('kind')
        if kind in (None, 'unclear'):
            reasons.append('docket status unclear')
        if timeline.get('stay_in_effect') is None and timeline.get('stay_history'):
            reasons.append('stay state unknown')
        judgments = timeline.get('judgments') or {}
        if kind in ('judgment_entered', 'sale_scheduled') and not judgments.get('controlling_entry'):
            reasons.append('no single controlling judgment')
    if present is None:
        reasons.append('no present-title summary (run title discovery)')
    else:
        status = (present.get('ownership') or {}).get('status')
        if status != 'candidate':
            reasons.append('ownership: %s' % str(status).replace('_', ' '))
    appraiser = facts['appraiser']
    if not appraiser['fresh']:
        reasons.append('appraiser owner not read in the last %d days' % APPRAISER_MAX_AGE)
    elif appraiser['state'] != 'clear':
        reasons.append('appraiser: %s' % appraiser['state'])
    if appraiser.get('agrees_with_deed_grantee') not in (None, 'agrees') and appraiser['state'] != 'not_checked':
        reasons.append('appraiser owner vs deed grantee: %s' % appraiser['agrees_with_deed_grantee'])
    if not facts['tax']['fresh']:
        reasons.append('taxes not read in the last %d days' % TAX_MAX_AGE)
    contact = facts['contact']
    if not contact.get('call_ready'):
        reasons.append('contact: %s' % contact['state'].replace('_', ' '))
    return not reasons, reasons


def _gaps(timeline, present):
    return len((timeline or {}).get('gaps') or []) + len((present or {}).get('held_because') or [])


def rank(cases, as_of):
    """`cases`: [{'case', 'facts', 'timeline', 'present'}] -> the report's case list, ranked."""
    out = []
    for item in cases:
        ok, reasons = qualify(item['case'], item['facts'], item.get('timeline'), item.get('present'))
        timeline = item.get('timeline') or {}
        out.append({'case': item['case'], 'qualified': ok, 'held_because': reasons,
                    'facts': item['facts'], 'open_gaps': _gaps(item.get('timeline'), item.get('present')),
                    'docket_status': (timeline.get('status') or {}).get('kind'),
                    'controlling_judgment': (timeline.get('judgments') or {}).get('controlling_entry')})
    far = date.max.toordinal()

    def key(row):
        sale = _day(row['facts']['auction'].get('sale_date'))
        return (not row['qualified'], len(row['held_because']),
                sale.toordinal() if sale else far, row['open_gaps'], row['case'])
    out.sort(key=key)
    for n, row in enumerate(out, 1):
        row['rank'] = n if row['qualified'] else None
    return out


def changes(previous, current):
    """Every changed fact between two reports, per case."""
    before = {r['case']: r for r in (previous or {}).get('cases') or []}
    fields = (('sale_date', ('facts', 'auction', 'sale_date')), ('auction', ('facts', 'auction', 'state')),
              ('appraiser_owner', ('facts', 'appraiser', 'current_owner')),
              ('appraiser', ('facts', 'appraiser', 'state')), ('tax_due', ('facts', 'tax', 'due')),
              ('tax_certificate', ('facts', 'tax', 'certificate')),
              ('docket_status', ('docket_status',)), ('controlling_judgment', ('controlling_judgment',)),
              ('qualified', ('qualified',)))

    def pick(row, path):
        for part in path:
            row = (row or {}).get(part) if isinstance(row, dict) else None
        return row
    out = []
    for row in current.get('cases') or []:
        old = before.get(row['case'])
        if old is None:
            out.append({'case': row['case'], 'field': 'case', 'was': None, 'now': 'first seen'})
            continue
        for name, path in fields:
            was, now = pick(old, path), pick(row, path)
            if was != now:
                out.append({'case': row['case'], 'field': name, 'was': was, 'now': now})
    for case in set(before) - {r['case'] for r in current.get('cases') or []}:
        out.append({'case': case, 'field': 'case', 'was': 'listed', 'now': 'not in this run'})
    return out


# ---------------------------------------------------------------------------------------------
# Loading from disk (caches only unless a --refresh flag asks for a free public read)
# ---------------------------------------------------------------------------------------------
def _json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def load_case(case, lead, sources, as_of, refresh_appraiser=False):
    import run_documents as RD
    import case_review
    import document_store as DS
    dossier = RD.dossier_path(COUNTY, case)
    timeline = DS.pipeline_load(Path(dossier).with_name(Path(dossier).stem + '-timeline.json'), None)
    report = _json(case_review.output_path('title_discovery') / (case + '.json'), None)
    present = None
    if report:
        present = report.get('present_title')
        if present is None:
            import miami_present_title as MPT
            present = MPT.present_title(report)
    grantees = ((present or {}).get('ownership') or {}).get('grantees') or []
    folio = str((lead or {}).get('Folio') or (report or {}).get('folio') or '')
    owner = str((lead or {}).get('owner_clean') or (report or {}).get('owner') or '')
    import ownership_gate as OG
    if refresh_appraiser and folio and owner:
        check = OG.check_lead(folio, owner, county=COUNTY, cache=sources['ownership'])
    else:
        check = sources['ownership'].get(OG._cache_key(folio, owner, None)) if folio and owner else None
    import re
    facts = {'auction': auction_fact(lead, sources['archive'].get(case), sources['calendar_day'], timeline, as_of),
             'appraiser': appraiser_fact(check, grantees, as_of),
             'tax': tax_fact(sources['taxes'].get(re.sub(r'\D', '', folio)), as_of),
             'contact': contact_fact(sources['traces'].get(case), present, as_of)}
    return {'case': case, 'facts': facts, 'timeline': timeline, 'present': present, 'folio': folio}


def load_sources():
    import auction_archive as AA
    import county_taxes as CT
    import ownership_gate as OG
    archive = AA.load()
    seen = [e.get('last_seen') for e in archive.values() if e.get('last_seen')]
    return {'archive': archive, 'calendar_day': max(seen) if seen else None,
            'ownership': OG._load_cache(), 'taxes': CT._load(CT.CACHE, {}),
            'traces': _json(os.path.join(HERE, 'skiptrace_results.json'), {})}


def refresh_taxes(folios, sources, conc=2):
    """Free Tax Collector reads for the folios whose read is missing or stale."""
    import asyncio
    import county_taxes as CT
    got = asyncio.run(CT._run([(COUNTY, f) for f in folios], conc))
    stamp = date.today().isoformat()
    for folio, rec in got.items():
        rec['checked'] = stamp
        sources['taxes'][folio] = rec
    tmp = CT.CACHE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(sources['taxes'], handle, indent=0)
    os.replace(tmp, CT.CACHE)
    return len(got)


def render_markdown(report):
    lines = ['# Miami evidence-qualified list, %s' % report['as_of'], '',
             '%d qualified, %d held. Equity is not an input. Qualified is an evidence statement; the '
             "board's opt-out, DNC and send-time gates still decide every contact." %
             (report['summary']['qualified'], report['summary']['held']), '']
    if report['changes']:
        lines += ['## Changed since %s' % (report.get('previous') or 'the last run'), '']
        lines += ['- %s: %s %s -> %s' % (c['case'], c['field'], c['was'], c['now']) for c in report['changes']]
        lines.append('')
    lines += ['## Qualified', '']
    for row in report['cases']:
        if row['qualified']:
            a = row['facts']['auction']
            lines.append('%d. %s: sale %s (%s days), %d open gaps' % (
                row['rank'], row['case'], a.get('sale_date') or 'none', a.get('days_to_sale', '-'), row['open_gaps']))
    lines += ['', '## Held', '']
    for row in report['cases']:
        if not row['qualified']:
            lines.append('- %s: %s' % (row['case'], '; '.join(row['held_because'])))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    import case_review
    import document_store as DS
    import run_documents as RD
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--all', action='store_true', help='every Miami case with a saved dossier')
    parser.add_argument('--refresh-appraiser', action='store_true',
                        help='re-read stale appraiser owners (free public page, 7-day cache)')
    parser.add_argument('--refresh-tax', action='store_true',
                        help='re-read missing or stale Tax Collector bills (free, needs playwright)')
    args = parser.parse_args(argv)
    as_of = date.today()
    cases = list(args.case)
    if args.all:
        folder = Path(RD.dossier_path(COUNTY, 'x')).parent
        cases += sorted(p.stem for p in folder.glob('*.json')
                        if not p.stem.endswith('-timeline')) if folder.is_dir() else []
    cases = list(dict.fromkeys(cases))
    if not cases:
        parser.error('name --case or --all')
    leads = {str(r.get('Case #') or ''): r for r in RD._lead_rows() if isinstance(r, dict)}
    for case in cases:
        if case not in leads:
            leads.update({str(r.get('Case #') or ''): r for r in RD._lead_rows(case) if isinstance(r, dict)})
    sources = load_sources()
    if args.refresh_tax:
        import re
        stale = []
        for case in cases:
            folio = re.sub(r'\D', '', str((leads.get(case) or {}).get('Folio') or ''))
            if folio and not tax_fact(sources['taxes'].get(folio), as_of)['fresh']:
                stale.append(folio)
        if stale:
            print('tax: reading %d folio(s) from the Tax Collector (free)' % len(stale), flush=True)
            refresh_taxes(stale, sources)
    loaded = [load_case(c, leads.get(c), sources, as_of, args.refresh_appraiser) for c in cases]
    folder = case_review.output_path('reports')
    previous_path = max((p for p in Path(folder).glob('miami-ranking-*.json')
                         if p.stem != 'miami-ranking-%s' % as_of), default=None) if Path(folder).is_dir() else None
    ranked = rank(loaded, as_of)
    report = {'as_of': as_of.isoformat(), 'cases': ranked,
              'summary': {'cases': len(ranked), 'qualified': sum(r['qualified'] for r in ranked),
                          'held': sum(not r['qualified'] for r in ranked)},
              'previous': previous_path.stem.replace('miami-ranking-', '') if previous_path else None,
              'thresholds_days': {'calendar': CALENDAR_MAX_AGE, 'appraiser': APPRAISER_MAX_AGE,
                                  'tax': TAX_MAX_AGE, 'trace': TRACE_MAX_AGE},
              'spent_usd': 0}
    report['changes'] = changes(_json(previous_path, {}) if previous_path else {}, report)
    Path(folder).mkdir(parents=True, exist_ok=True)
    target = Path(folder) / ('miami-ranking-%s.json' % as_of)
    DS._atomic_write_text(str(target), json.dumps(report, indent=2, default=str) + '\n')
    DS._atomic_write_text(str(target.with_suffix('.md')), render_markdown(report))
    print('miami ranking: %d case(s), %d qualified, %d held, %d change(s) since %s; $0'
          % (len(ranked), report['summary']['qualified'], report['summary']['held'],
             len(report['changes']), report['previous'] or 'no earlier run'))
    print('  report: %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
