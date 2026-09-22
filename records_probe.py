"""records_probe — can the Miami-Dade Official Records search be addressed by something OTHER
than a party name?

WHY THIS EXISTS
The coverage map of 2026-09-22 found one structural hole under every county: recorded liens are
reached by an OWNER-NAME search (`records_liens.py:166`, `searchtype=Name/Document`), and the
analyzer then narrows the result set to the subject parcel by folio or subdivision
(`records_liens.py:410-414`). Narrowing is the wrong direction for the problem. A mortgage the
county recorded against a prior owner, a trust, an LLC or a misspelling was never in the result
set to be narrowed, so it cannot be found by making the filter smarter. It can only be found by
changing what we ASK the county.

WHAT IS ALREADY KNOWN, AND IS NOT A GUESS
`lis_pendens.py` sweeps the same `api/home/standardsearch` endpoint nightly with `partyName=`
BLANK, a `documentType` and a recorded-date range, and it works. So the endpoint is demonstrably
not name-bound; a non-name query is accepted today. What is NOT known is whether it also accepts a
BOOK/PAGE, an instrument number, a folio or a legal description — the keys that would let us ask
for a specific instrument a document cited, or for everything recorded against a parcel.

WHY THIS IS A PROBE AND NOT AN IMPLEMENTATION
Nobody here has seen the answer. The clerk hosts are unreachable from the cloud container (every
request returns a proxy refusal, not even a 403), so this file cannot be run where it was written.
Guessing the parameter names and shipping a resolver built on the guess would produce exactly the
failure this project keeps writing post-mortems about: code that looks like it works, returns
nothing, and reports the nothing as "no lien found".

So: this enumerates candidate shapes, tries each against the live county, and writes down what the
county actually answered. `document_walk` reads that verdict and uses a capability ONLY if a probe
observed it. Until a probe runs, the walk says so in words and falls back to the routes that are
already proven.

Run it on a machine with clerk access (the laptop, or the desktop):

    python records_probe.py --book 35287 --page 4642        # probe with a known-good instrument
    python records_probe.py --book 35287 --page 4642 --json  # ...and dump every raw response
    python records_probe.py --show                           # print the cached verdict, no network

The `--book/--page` pair must be an instrument you KNOW exists, otherwise an empty result is
ambiguous: a shape that is rejected and a shape that is accepted-but-matches-nothing look the same
from the outside. The 2026-09-22 pilot judgment (book 35287, page 4642, case 2026-020206-CC-25) is
the obvious choice — it is already on disk, so its existence is not in question.
"""
import argparse
import json
import os
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CAPS = os.path.join(HERE, 'records_search_caps.json')

# Every shape below is a HYPOTHESIS. None has been observed. The names come from the parameters the
# live endpoint already takes (`partyName`, `documentType`, `dateRangeFrom`, `dateRangeTo`,
# `searchT`, `searchtype`) plus the obvious spellings a .NET app of this vintage uses. `searchT` is
# the interesting one: it is sent on every existing call and is always EMPTY, which is what an
# unused "search term" field looks like.
#
# Each candidate is (capability, label, param builder). The builder takes the probe subject and
# returns the query dict.
CANDIDATES = (
    ('book_page', 'searchtype=Book/Page with searchT="book/page"',
     lambda s: {'partyName': '', 'searchT': '%s/%s' % (s['book'], s['page']),
                'searchtype': 'Book/Page'}),
    ('book_page', 'searchtype=BookPage with explicit book/page params',
     lambda s: {'partyName': '', 'bookNumber': s['book'], 'pageNumber': s['page'],
                'searchtype': 'BookPage'}),
    ('book_page', 'searchtype=Book/Page with sBook/sPage (the DocumentImage spelling)',
     lambda s: {'partyName': '', 'sBook': s['book'], 'sPage': s['page'],
                'searchtype': 'Book/Page'}),
    ('cfn', 'searchtype=CFN with searchT=cfn',
     lambda s: {'partyName': '', 'searchT': s.get('cfn') or '', 'searchtype': 'CFN'}),
    ('legal', 'searchtype=Legal with searchT=folio',
     lambda s: {'partyName': '', 'searchT': s.get('folio') or '', 'searchtype': 'Legal'}),
    ('legal', 'searchtype=Legal/Document with a folio parameter',
     lambda s: {'partyName': '', 'folioNumber': s.get('folio') or '',
                'searchtype': 'Legal/Document'}),
    ('legal', 'Name/Document with a folio parameter riding along',
     lambda s: {'partyName': '', 'folioNumber': s.get('folio') or '',
                'searchtype': 'Name/Document'}),
)

# Sent on every call the app makes; kept verbatim so a probe differs from a working search in
# exactly one respect — the thing being probed.
BASE_PARAMS = {'dateRangeFrom': '', 'dateRangeTo': '', 'documentType': '',
               'searchT': '', 'firstQuery': 'y'}


def _endpoint(params):
    import records_liens as R
    merged = dict(BASE_PARAMS)
    merged.update(params)
    return R.OR_BASE + 'api/home/standardsearch?' + urllib.parse.urlencode(merged)


def mint_token():
    """One Turnstile token, by the cheapest route records_liens already trusts.

    Deliberately NOT a new token path. If the token minting here drifted from the one the live
    tracer uses, a probe failure would be ambiguous between "the county rejects this shape" and
    "this file mints tokens wrong", and the first is the only answer worth having.
    """
    import records_liens as R
    try:
        from captcha_solver import solve_turnstile
    except Exception as exc:
        return None, 'captcha_solver unavailable (%s)' % type(exc).__name__
    token = solve_turnstile(R.TS_SITE_KEY, R.OR_BASE)
    if not token:
        return None, '2Captcha returned no token'
    return token, ''


def try_shape(subject, params, token):
    """POST one candidate shape. Returns a verdict dict — never raises, never interprets.

    The three outcomes are DIFFERENT and are kept apart on purpose:
      accepted_with_hits  the county issued a qs AND getStandardRecords returned rows
      accepted_no_hits    the county issued a qs and returned nothing. AMBIGUOUS: the shape may be
                          valid and the subject absent, or the shape may be ignored and an empty
                          name search run instead. Only a subject we KNOW exists disambiguates it.
      rejected            no qs. The county did not accept the query.
    """
    import records_liens as R
    url = _endpoint(params)
    out = {'params': dict(params), 'url': url}
    try:
        response = R.S.post(url, headers={'x-recaptcha-token': token,
                                          'content-type': 'application/json; charset=utf-8'},
                            data='', timeout=30)
        out['http'] = response.status_code
        body = response.json()
    except Exception as exc:
        out.update({'outcome': 'rejected', 'why': '%s: %s' % (type(exc).__name__, str(exc)[:160])})
        return out
    qs = body.get('qs') if isinstance(body, dict) else None
    out['is_valid_search'] = body.get('isValidSearch') if isinstance(body, dict) else None
    if not qs:
        out.update({'outcome': 'rejected', 'why': 'no qs issued', 'body': str(body)[:300]})
        return out
    models = R.records_by_qs(qs) or []
    out['records'] = len(models)
    # Did the subject itself come back? That is the only thing that separates "this shape works"
    # from "this shape was ignored and something else ran".
    want = (str(subject['book']).lstrip('0'), str(subject['page']).lstrip('0'))
    hit = [m for m in models
           if (str(m.get('reC_BOOK') or '').lstrip('0'),
               str(m.get('reC_PAGE') or '').lstrip('0')) == want]
    out['subject_returned'] = bool(hit)
    if hit:
        out['subject_keys'] = {k: hit[0].get(k) for k in
                               ('reC_BOOK', 'reC_PAGE', 'booK_TYPE', 'cfN_MASTER_ID',
                                'doC_TYPE', 'doC_PAGES', 'foliO_NUMBER')}
    out['outcome'] = 'accepted_with_hits' if models else 'accepted_no_hits'
    return out


def probe(subject, pause=2.0, keep_raw=False):
    """Run every candidate shape once. Returns the verdict document that gets cached."""
    results = []
    for capability, label, build in CANDIDATES:
        params = build(subject)
        # A shape whose only distinguishing value is empty proves nothing; skip it and say so,
        # rather than recording a rejection the subject caused.
        if not any(str(v).strip() for k, v in params.items() if k != 'searchtype'):
            # SAID OUT LOUD. The 2026-09-22 desktop run printed three lines and a headline of
            # NOTHING CONFIRMED, while four shapes had been skipped for want of a CFN and a folio.
            # A shape nobody tried is not a shape that failed, and a summary that cannot tell them
            # apart overstates its own result.
            print('  %-58s -> not probed (%s)' % (label[:58], 'no subject value for this shape'))
            results.append({'capability': capability, 'label': label, 'outcome': 'not_probed',
                            'why': 'no subject value available for this shape'})
            continue
        token, why = mint_token()
        if not token:
            print('  %-58s -> not probed (%s)' % (label[:58], why))
            results.append({'capability': capability, 'label': label, 'outcome': 'not_probed',
                            'why': why})
            continue
        verdict = try_shape(subject, params, token)
        verdict.update({'capability': capability, 'label': label})
        if not keep_raw:
            verdict.pop('body', None)
        print('  %-58s -> %s%s' % (label[:58], verdict['outcome'],
                                   '' if not verdict.get('records')
                                   else ' (%d records, subject %s)'
                                   % (verdict['records'],
                                      'RETURNED' if verdict.get('subject_returned') else 'absent')))
        results.append(verdict)
        time.sleep(pause)
    return {'probed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'subject': dict(subject), 'shapes': results,
            'capabilities': _capabilities(results)}


def _capabilities(results):
    """A capability is CONFIRMED only when the probe got the subject back.

    `accepted_no_hits` is not a capability. The endpoint answering 200 with a search token and
    zero rows is exactly what an IGNORED parameter looks like, and a resolver built on an ignored
    parameter returns "no such instrument" for every instrument in the county.
    """
    caps = {}
    for row in results:
        name = row.get('capability')
        if not name:
            continue
        current = caps.get(name) or {'confirmed': False}
        if row.get('subject_returned'):
            caps[name] = {'confirmed': True, 'shape': row.get('params'), 'label': row.get('label')}
        elif not current['confirmed']:
            caps[name] = {'confirmed': False, 'best_outcome': row.get('outcome'),
                          'label': row.get('label')}
    return caps


def load_caps(path=CAPS):
    """What a probe observed, or an empty verdict. Never invents a capability."""
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {'probed_at': None, 'capabilities': {}}
    if not isinstance(data, dict):
        return {'probed_at': None, 'capabilities': {}}
    data.setdefault('capabilities', {})
    return data


def confirmed(capability, path=CAPS):
    entry = (load_caps(path).get('capabilities') or {}).get(capability) or {}
    return bool(entry.get('confirmed')), entry.get('shape') or {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--book', default='', help='book of an instrument you KNOW exists')
    parser.add_argument('--page', default='', help='page of that instrument')
    parser.add_argument('--cfn', default='', help='its CFN, if you have it')
    parser.add_argument('--folio', default='', help='the folio it sits on, if you have it')
    parser.add_argument('--show', action='store_true', help='print the cached verdict and stop')
    parser.add_argument('--json', action='store_true', help='keep raw response bodies')
    args = parser.parse_args(argv)

    if args.show:
        caps = load_caps()
        if not caps.get('probed_at'):
            print('No probe has run. Nothing is known about non-name search on this endpoint.')
            return 1
        print('probed %s against book %s page %s' % (caps['probed_at'],
                                                     (caps.get('subject') or {}).get('book'),
                                                     (caps.get('subject') or {}).get('page')))
        for name, entry in sorted((caps.get('capabilities') or {}).items()):
            print('  %-12s %s  %s' % (name, 'CONFIRMED' if entry.get('confirmed') else 'no',
                                      entry.get('label') or ''))
        return 0

    if not (args.book and args.page):
        parser.error('--book and --page are required: an empty result against an instrument that '
                     'may not exist proves nothing')
    subject = {'book': args.book, 'page': args.page, 'cfn': args.cfn, 'folio': args.folio}
    print('probing api/home/standardsearch for non-name search shapes')
    print('subject: book %s page %s%s' % (args.book, args.page,
                                          (' folio ' + args.folio) if args.folio else ''))
    verdict = probe(subject, keep_raw=args.json)
    with open(CAPS, 'w', encoding='utf-8') as fh:
        json.dump(verdict, fh, indent=1, sort_keys=True)
    caps = verdict['capabilities']
    good = [k for k, v in caps.items() if v.get('confirmed')]
    tried = [r for r in verdict['shapes'] if r.get('outcome') != 'not_probed']
    skipped = [r for r in verdict['shapes'] if r.get('outcome') == 'not_probed']
    print('\n-> %s' % CAPS)
    print('%d shape(s) tried, %d not probed.' % (len(tried), len(skipped)))
    for row in skipped:
        print('  NOT PROBED: %s - %s' % (row['label'], row.get('why')))
    if good:
        print('CONFIRMED: %s. document_walk can now resolve a cited instrument directly.'
              % ', '.join(sorted(good)))
        return 0
    if not tried:
        print('NOTHING WAS PROBED. This run proves nothing about the endpoint.')
        return 3
    print('NOTHING CONFIRMED among the %d shape(s) tried. That is a real answer for those shapes '
          'and only those: the walk stays on the index and the name-expansion route.' % len(tried))
    if skipped:
        print('The shapes above were never tried. Re-run with --cfn and --folio from a recorded '
              'instrument that HAS both (the pilot judgment is indexed at folio 0, so it cannot '
              'answer the folio question - use a recorded MORTGAGE off the same parcel).')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
