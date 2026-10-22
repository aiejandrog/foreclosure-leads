#!/usr/bin/env python
"""badnum_wp_compare.py -- is the bad-number problem the lead source, or every source?

Jesse's test (2026-10-21): take every lead Call Mode logged as a bad or wrong number, look it up on
Whitepages, and compare. If Whitepages gives back the same numbers we dialed, Whitepages is no
better. If it gives two or three numbers we never had, the source that gave us the dead ones is
the problem.

This reads what is already on disk and prints counts only: no names, numbers, addresses or case
ids, so the output is safe to paste into chat. Leads already in whitepages_lookup.json are compared
for free. Leads not yet looked up are listed as a count and a dollar estimate; nothing is bought
unless --lookup is given WITH an explicit --max-spend, and whitepages_lookup's own day cap
(WP_DAILY_CAP) and contact holds still apply.

Per bad-number lead it answers:
  - did Whitepages return anything at all
  - does Whitepages list the number we already found dead (it agrees with the bad source)
  - does Whitepages have a number we never had, and is that number graded the owner's
  - which source (phsrc) gave us the number that turned out bad

It also sizes the mail lane Jesse described (bad-number leads with a verified equity figure and a
tax-roll mailing address). Sizing only: nothing is printed, queued or sent.

Reads:  worker_notes.json (dials[].oc in badnum/wrong), the board twin (P.TWIN), *_leads.json,
        skiptrace_results.json, whitepages_lookup.json, optouts.json
Writes: P.out('badnum_wp_compare.json'), counts only. --lookup also writes whitepages_lookup.json
        through whitepages_lookup.enrich_one, the same path every other lookup uses.
Run:    python badnum_wp_compare.py                          # compare what is cached, $0
        python badnum_wp_compare.py --all                    # EVERY WP-looked-up lead, our numbers vs WP, $0
        python badnum_wp_compare.py --days 14                # only bad numbers logged in 14 days
        python badnum_wp_compare.py --lookup --max-spend 5   # buy up to $5 of lookups, then compare
"""
import argparse
import collections
import math
import datetime as dt
import json
import os
import sys

import paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
BADNUM = {'badnum', 'wrong'}
CONVO = {'talked', 'callback', 'appt'}
FACT = ('clear', 'priced')              # equity_state: the only states that are a fact
# Property, then up to two Person searches when Property names no owner (whitepages_lookup).
WORST_CALLS_PER_LEAD = 3


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _last10(x):
    d = ''.join(ch for ch in str(x or '') if ch.isdigit())
    if len(d) == 11 and d.startswith('1'):
        d = d[1:]
    return d if len(d) == 10 else ''


def _nums(phones):
    out = set()
    for p in phones or []:
        n = _last10(p.get('number') if isinstance(p, dict) else p)
        if n:
            out.add(n)
    return out


def bad_dials(notes, days=None, today=None):
    """{case: {'bad4': set(ph4), 'convo': bool, 'last': date}} for every case with a bad/wrong dial."""
    today = today or dt.date.today()
    cut = today - dt.timedelta(days=days) if days else None
    out = {}
    for case, n in (notes or {}).items():
        if not isinstance(n, dict):
            continue
        dials = [d for d in (n.get('dials') or []) if isinstance(d, dict) and isinstance(d.get('oc'), (str, type(None)))]
        bad = []
        for d in dials:
            if d.get('oc') not in BADNUM:
                continue
            try:
                day = dt.datetime.strptime(str(d.get('d') or '')[:10], '%Y-%m-%d').date()
            except ValueError:
                day = None
            if cut and (not day or day < cut):
                continue
            bad.append((day, str(d.get('ph4') or '')))
        if not bad:
            continue
        out[case] = {'bad4': {p for _, p in bad if p},
                     'convo': any(d.get('oc') in CONVO for d in dials),
                     'oc': collections.Counter(d.get('oc') for d in dials if d.get('oc') in BADNUM)}
    return out


def load_leads():
    """case -> lead row. Lead files first, the board twin wins (it carries phsrc, eqstate, mail)."""
    import whitepages_lookup as WL
    leads = {}
    for r in WL.load_all_leads():
        k = WL._lead_key(r)
        if k:
            leads[k] = r
    try:
        from foreclosure_report import load_board
        for r in load_board(P.TWIN):
            if r.get('case'):
                leads[r['case']] = dict(leads.get(r['case']) or {}, **r)
    except Exception as e:
        print('board twin unreadable (%s): phone sources and equity come from lead files only'
              % e.__class__.__name__)
    return leads


def known_numbers(lead, skip):
    return _nums((lead or {}).get('phones')) | _nums((skip or {}).get('phones'))


def bad_source(lead, bad4):
    """phsrc of each dialed-bad number, read off the board row; 'not on board' if it has gone."""
    phs = (lead or {}).get('phones') or []
    src = (lead or {}).get('phsrc') or []
    out = []
    for p4 in bad4:
        hit = 'not on board'
        for i, p in enumerate(phs):
            if _last10(p.get('number') if isinstance(p, dict) else p).endswith(p4):
                hit = str(src[i] if i < len(src) else '') or 'unknown'
                break
        out.append(hit)
    return out


def _eq_verified(lead):
    try:
        return (lead or {}).get('eqstate') in FACT and float(lead.get('eq') or 0) > 0
    except (TypeError, ValueError):
        return False


def _optout_keys():
    raw = _load(os.path.join(HERE, 'optouts.json'), None)
    if not isinstance(raw, dict):
        return None                      # unreadable: the mail sizing says so instead of guessing
    notes = raw.get('notes') if isinstance(raw.get('notes'), dict) else raw
    return {str(k) for k, v in notes.items() if v}


def compare(bad, leads, cache, skiptrace, opt):
    import whitepages_lookup as WL
    t = collections.Counter()
    src = collections.Counter()
    oc = collections.Counter()
    todo = []
    mail = collections.Counter()
    for case, b in bad.items():
        t['bad-number leads'] += 1
        oc.update(b['oc'])
        lead = leads.get(case)
        for s in bad_source(lead, b['bad4']):
            src[s] += 1
        if b['convo']:
            t['  of those, a conversation is also logged on the lead'] += 1
        if lead is None:
            t['lead no longer in any lead file'] += 1
            continue
        held = WL.contact_hold(lead)
        company = WL._is_company_owner(WL._lead_owners(lead))
        opted = opt is not None and case in opt

        # mail lane sizing (Jesse: bad numbers with equity get a letter at the tax-roll mailing address)
        mail['bad-number leads on file'] += 1
        if held or opted or company:
            mail['held / opted out / company owner'] += 1
        elif not _eq_verified(lead):
            mail['equity not verified (not clear/priced, or no/0% figure)'] += 1
        elif not str(lead.get('mail') or lead.get('mailing_address') or '').strip():
            mail['verified equity, no tax-roll mailing address'] += 1
        else:
            mail['MAILABLE: verified equity + mailing address'] += 1
            a = ''.join(str(lead.get('addr') or lead.get('Address') or '').upper().split())[:12]
            m = ''.join(str(lead.get('mail') or lead.get('mailing_address') or '').upper().split())[:12]
            if a and m and a != m:
                mail['  of those, mailing address differs from the property'] += 1

        entry = cache.get(case)
        if not WL._cache_looked(entry):
            if held or opted:
                t['not looked up: contact hold or opted out (no spend)'] += 1
            elif company:
                t['not looked up: company owner (WP is person-only)'] += 1
            elif b['convo']:
                t['not looked up: a conversation is already logged (no spend)'] += 1
            else:
                t['not looked up: eligible to buy'] += 1
                todo.append(lead)
            continue
        t['looked up on Whitepages'] += 1
        k = WL.lead_contacts(lead, entry)
        wp_owner = set(k['owner'])
        wp_all = wp_owner | set(k['household']) | set(k['name_only'])
        have = known_numbers(lead, skiptrace.get(case))
        if not wp_all:
            t['  WP returned no number'] += 1
            continue
        t['  WP returned at least one number'] += 1
        if any(n.endswith(p4) for n in wp_all for p4 in b['bad4']):
            t['  WP lists the number we found dead'] += 1
        new_all, new_owner = wp_all - have, wp_owner - have
        if not new_all:
            t['  WP had only numbers we already had'] += 1
        else:
            t['  WP had a number we never had'] += 1
            t['    new numbers, any grade'] += len(new_all)
            if new_owner:
                t['  WP had a NEW number graded the owner\'s'] += 1
                t['    new owner-graded numbers'] += len(new_owner)
            if len(new_all) >= 2:
                t['  WP had 2+ new numbers'] += 1
    return {'totals': dict(t), 'bad_number_source': dict(src), 'outcomes': dict(oc),
            'mail_lane': dict(mail), 'optouts_readable': opt is not None}, todo


# ---- ALL LEADS: how many of our numbers does Whitepages agree with? (2026-10-22) ----------------
# Alejandro, 10-22: "how much of the numbers actually match?" The bad-number compare above only
# looks at leads that already failed. This one takes every lead with a Whitepages lookup on file and
# lines up the numbers the skip trace gave us against the numbers Whitepages lists for the same
# lead, graded the way make_tracker bakes them (parcel gate first, then the owner-name bar).

WP_TAGS = ('wp', 'hh', 'nm')         # phsrc tags that came FROM Whitepages, not from the skip trace


def trace_numbers(lead, skip):
    """The skip trace's numbers for a lead: the trace cache, plus every number on the row that the
    Whitepages merge did not add. The board twin's row already carries Whitepages' numbers, so
    without the phsrc filter Whitepages would be compared against itself."""
    out = _nums((skip or {}).get('phones'))
    phs = (lead or {}).get('phones') or []
    src = (lead or {}).get('phsrc') or []
    for i, p in enumerate(phs):
        if str(src[i] if i < len(src) else '') in WP_TAGS:
            continue
        n = _last10(p.get('number') if isinstance(p, dict) else p)
        if n:
            out.add(n)
    return out


def wp_sets(lead, entry):
    """(verdict, {'owner','household','name_only'}) for one cached entry, as the bake grades it:
    a /property record for a different parcel contributes nothing from its property half, an
    unverifiable parcel only an owner on our street, residents only from a matching parcel. One
    difference from the bake, the same one Call Mode's card makes: a resident whose name clears
    the owner bar counts as the owner (the bake calls every resident household)."""
    import wp_contacts as W
    import whitepages_lookup as WL
    owners, addr = WL._lead_owners(lead), WL._lead_addr(lead)
    res = (entry or {}).get('result') or {}
    verdict = W.wp_parcel_verdict(res.get('apn'), WL._lead_folio(lead)) if res else 'ok'
    out = {'owner': set(), 'household': set(), 'name_only': set()}
    for o in W.parcel_owners(verdict, owners, addr, res) if res else []:
        if not isinstance(o, dict):
            continue
        tag = W.grade_property_owner(owners, o.get('name'))
        for p in W._phones_of(o):
            (out['owner'] if tag == 'wp' else out['household']).add(p['n'])
    if verdict == 'ok':
        for o in (res.get('residents') or []):
            if isinstance(o, dict):
                # Whitepages often files the deed owner under Resident; the name bar decides
                tag = W.grade_property_owner(owners, o.get('name'))
                for p in W._phones_of(o):
                    (out['owner'] if tag == 'wp' else out['household']).add(p['n'])
    for pr in ((entry or {}).get('_person') or []):
        if not isinstance(pr, dict):
            continue
        for rec in (pr.get('response') or []):
            if not isinstance(rec, dict):
                continue
            g = W.grade_person_record(owners, addr, rec)
            if g is None:
                continue
            (out['owner'] if g == 'wp' else out['name_only']).update(p['n'] for p in W._phones_of(rec))
    out['household'] -= out['owner']
    out['name_only'] -= out['owner'] | out['household']
    return verdict, out


def match_all(leads, cache, skiptrace, bad=None):
    """Counts only. `bad` is bad_dials() output, to say whether Whitepages-confirmed numbers die too."""
    import whitepages_lookup as WL
    t = collections.Counter()
    bad = bad or {}
    for case, entry in (cache or {}).items():
        if not WL._cache_looked(entry):
            continue
        lead = leads.get(case)
        if lead is None:
            t['WP lookup on file, lead no longer in any lead file'] += 1
            continue
        t['leads with a Whitepages lookup on file'] += 1
        verdict, wp = wp_sets(lead, entry)
        if verdict != 'ok':
            t['  WP property record is %s for this parcel' % verdict] += 1
        wp_any = wp['owner'] | wp['household'] | wp['name_only']
        ours = trace_numbers(lead, skiptrace.get(case))
        if not wp_any:
            t['  WP gave no usable number (after the parcel gate and name bar)'] += 1
        if not ours:
            t['  we have no skip-trace number for it'] += 1
        if not (wp_any and ours):
            continue
        t['COMPARABLE: both WP and the skip trace have numbers'] += 1
        t['  our numbers on these leads'] += len(ours)
        t['    WP lists it as the OWNER\'s (match)'] += len(ours & wp['owner'])
        t['    WP lists it for someone else at the address'] += len(ours & wp['household'])
        t['    WP lists it on a name-only match'] += len(ours & wp['name_only'])
        t['    not on Whitepages at all (unconfirmed)'] += len(ours - wp_any)
        t['  WP numbers on these leads'] += len(wp_any)
        t['    WP owner numbers we did not have (new)'] += len(wp['owner'] - ours)
        t['    WP household / name-only numbers we did not have'] += len((wp['household'] | wp['name_only']) - ours)
        if ours & wp['owner']:
            t['  leads where 1+ of our numbers is WP-confirmed the owner\'s'] += 1
        elif wp['owner']:
            t['  leads where WP has owner numbers and NONE of ours is one'] += 1
        if not (ours & wp_any):
            t['  leads with zero overlap'] += 1
        b4 = (bad.get(case) or {}).get('bad4') or set()
        for p4 in b4:
            hit = [n for n in ours if n.endswith(p4)]
            if not hit:
                continue
            t['  numbers tapped bad/wrong in Call Mode'] += 1
            if any(n in wp['owner'] for n in hit):
                t['    of those, WP had listed it as the owner\'s'] += 1
    return dict(t)


def render_all(t, generated):
    out = ['ALL LEADS: OUR NUMBERS vs WHITEPAGES  %s  (counts only, $0)' % generated]
    for k, v in t.items():
        out.append('  %-66s %5d' % (k, v))
    ours = t.get('  our numbers on these leads', 0)
    m = t.get("    WP lists it as the OWNER's (match)", 0)
    looked = t.get('COMPARABLE: both WP and the skip trace have numbers', 0)
    out.append('')
    if ours:
        out.append('READ: %d of %d of our numbers (%.0f%%) on %d comparable leads are the owner\'s per Whitepages.'
                   % (m, ours, 100.0 * m / ours, looked))
    if looked < 100:
        out.append('      %d comparable lead(s): a rough floor, not a rate for the whole board.' % looked)
    out.append('      "Not on Whitepages" means unconfirmed, not wrong. A match is not a working number')
    out.append('      until it is dialed; the bad-tap line says whether confirmed numbers die too.')
    return '\n'.join(out)


PROVIDER_TAGS = ('AUTH ERROR', 'RATE LIMIT', 'BUDGET', 'HTTP ', 'network error', 'unparseable')


def _provider_log(msg):
    """WL._log replacement for --lookup: provider/budget lines only, never the per-lead ok/miss line."""
    if any(t in str(msg) for t in PROVIDER_TAGS):
        print(str(msg).strip(), flush=True)


def _todo_order(todo):
    import whitepages_lookup as WL
    # Verified equity first (that is where a dead number costs a deal), then soonest sale.
    return sorted(todo, key=lambda r: (0 if _eq_verified(r) else 1, WL._days_to_sale(r), WL._lead_key(r)))


def render(res, todo, est):
    out = ['BAD NUMBERS vs WHITEPAGES  %s  (worker_notes received %s)%s'
           % (res['generated'], res['notes_received_utc'] or 'unknown',
              ('  last %d days' % res['window_days']) if res['window_days'] else '  whole log')]
    out.append('outcomes counted: ' + ', '.join('%s %d' % kv for kv in sorted(res['outcomes'].items())))
    for k, v in res['totals'].items():
        out.append('  %-58s %5d' % (k, v))
    out.append('')
    out.append('which source gave the number that turned out bad (per dialed-bad number):')
    for k, v in sorted(res['bad_number_source'].items(), key=lambda x: -x[1]):
        out.append('  %-20s %5d' % (k, v))
    out.append('')
    out.append('to buy: %d lead(s), up to $%.2f at $%.2f/request (worst case %d requests each; '
               'a Property hit with an owner is 1 request)' % (len(todo), est['worst'], est['price'],
                                                            WORST_CALLS_PER_LEAD))
    out.append('')
    out.append('MAIL LANE (sizing only, nothing built or sent)%s'
               % ('' if res['optouts_readable'] else '  WARNING: optouts.json unreadable, opt-outs NOT excluded'))
    for k, v in res['mail_lane'].items():
        out.append('  %-58s %5d' % (k, v))
    looked = res['totals'].get('looked up on Whitepages', 0)
    if looked:
        new = res['totals'].get("  WP had a NEW number graded the owner's", 0)
        out.append('')
        out.append('READ: %d of %d looked-up bad-number leads got a new owner-graded number from Whitepages.'
                   % (new, looked))
        if looked < 20:
            out.append('      Under 20 looked up: a direction, not a verdict.')
        out.append('      A new number is not a working number until it is dialed.')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=None, help='only bad numbers logged in the last N days')
    ap.add_argument('--lookup', action='store_true', help='buy Whitepages lookups for eligible leads first')
    ap.add_argument('--max-spend', type=float, default=None, metavar='USD',
                    help='required with --lookup: dollar cap for this run')
    ap.add_argument('--limit', type=int, default=60, help='most leads to look up in one run')
    ap.add_argument('--all', action='store_true',
                    help='every lead with a Whitepages lookup on file: our numbers vs Whitepages ($0)')
    a = ap.parse_args(argv)
    if a.all:
        if a.lookup:
            print('--all compares what is cached and buys nothing; drop --lookup.')
            return 2
        import whitepages_lookup as WL
        wn = _load(os.path.join(HERE, 'worker_notes.json'), {})
        wn = wn if isinstance(wn, dict) else {}
        notes = wn.get('notes') if isinstance(wn.get('notes'), dict) else {}
        t = match_all(load_leads(), _load(WL.CACHE, {}), WL.load_skiptrace(), bad_dials(notes, a.days))
        gen = dt.datetime.now().isoformat(timespec='seconds')
        print(render_all(t, gen))
        try:
            with open(P.out('wp_match_all.json'), 'w', encoding='utf-8') as f:
                json.dump({'generated': gen, 'totals': t}, f, indent=1)
        except Exception as e:
            print('could not write wp_match_all.json: %s' % e.__class__.__name__)
        return 0
    if a.lookup and (a.max_spend is None or not math.isfinite(a.max_spend) or a.max_spend <= 0):
        print('--lookup needs an explicit, finite --max-spend (dollars). Nothing bought.')
        return 2

    import whitepages_lookup as WL
    wn = _load(os.path.join(HERE, 'worker_notes.json'), {})
    wn = wn if isinstance(wn, dict) else {}
    notes = wn.get('notes') if isinstance(wn.get('notes'), dict) else {}
    bad = bad_dials(notes, a.days)
    if not bad:
        print('no bad/wrong-number dials in worker_notes.json%s' % (' in that window' if a.days else ''))
        return 0
    leads = load_leads()
    skiptrace = WL.load_skiptrace()
    opt = _optout_keys()
    cache = _load(WL.CACHE, {})
    res, todo = compare(bad, leads, cache, skiptrace, opt)
    todo = _todo_order(todo)[:max(0, a.limit)]

    if a.lookup and todo and opt is None:
        print('optouts.json unreadable: not buying lookups for leads that may have opted out.')
    elif a.lookup and todo:
        WL._run['max'] = float(a.max_spend)
        print('buying: %d lead(s), run cap $%.2f, today $%.2f spent of $%.2f (WP_DAILY_CAP)'
              % (len(todo), WL._run['max'], WL.wp_spent_today(), WL.WP_DAILY_CAP), flush=True)
        key = WL._load_key()             # exits on a missing key
        # enrich_one logs case ids and street addresses through WL._log; only provider and budget
        # lines reach the console, so this output stays pasteable. A 401/403 calls os._exit, so its
        # line has to be printed as it happens, not collected for later.
        WL._log = _provider_log
        for lead in todo:
            if WL._stop_run.is_set():
                print('stopped early: %s' % (WL._stop_why['why'] or 'stop requested'))
                break
            WL.enrich_one(lead, key, False, cache, False)
        print('spent $%.2f this run on %d billed request(s)' % (WL._run['spent'], WL._run['calls']))
        cache = _load(WL.CACHE, {})
        res, todo = compare(bad, leads, cache, skiptrace, opt)
        todo = _todo_order(todo)[:max(0, a.limit)]

    res['generated'] = dt.datetime.now().isoformat(timespec='seconds')
    res['window_days'] = a.days
    res['notes_received_utc'] = wn.get('received_utc')
    est = {'price': WL.WP_EST_COST, 'worst': round(len(todo) * WORST_CALLS_PER_LEAD * WL.WP_EST_COST, 2)}
    res['to_buy'] = {'leads': len(todo), **est}
    print(render(res, todo, est))
    try:
        with open(P.out('badnum_wp_compare.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=1)
    except Exception as e:
        print('could not write badnum_wp_compare.json: %s' % e.__class__.__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
