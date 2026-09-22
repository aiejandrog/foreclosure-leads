"""document_walk — follow the instruments a read document NAMES, and widen the search past the
current owner's name.

THE HOLE THIS CLOSES
`records_liens` asks Miami-Dade Official Records for one owner's name and then narrows the answer
to the subject parcel by folio or subdivision (`records_liens.py:410-414`). Narrowing cannot
recover a record that was never in the answer. A mortgage recorded against a PRIOR owner, a trust,
an LLC, an ex-spouse or a misspelling is invisible to that design, and no amount of better
filtering will find it. It is the one gap in the 2026-09-22 coverage map that is structural rather
than a missing county.

Two routes out, and this module builds both:

  1. WHAT A DOCUMENT CITES. `document_classify.cited_instruments` already pulls every book/page a
     read document names out of its own text — a satisfaction recites the mortgage it kills, a
     judgment recites the mortgage it forecloses, an assignment recites what was assigned. Those
     citations do not care whose name the instrument was recorded under. PR #47 reported them as
     `cited_but_not_fetched` and stopped there. This fetches them.

  2. WHOSE NAMES APPEAR ON THE PARCEL. Every deed on the subject parcel names a grantor and a
     grantee; the docket names its parties. Searching those names instead of only the current
     owner's is the same endpoint, the same transport, the same cost per search — and it reaches
     the prior owner whose mortgage is still open.

WHAT THIS MODULE MAY AND MAY NOT DO
It may fetch, store, read and classify. It may NOT decide anything about equity. Everything it
finds lands in the dossier's `c` rung as a document with its own provenance, and `c` still does
not feed `d` — `equity_state`'s five states and its two FACT states are untouched, and a walked
document cannot promote a lead to `clear` or `priced`. Whether it ever should is Alejandro's
decision, written down in the dossier as an open question rather than taken here.

THE HONESTY RULES, inherited from the collector and not relaxed
  * a cited book/page we cannot ADDRESS is an unresolved citation with a named reason, never a
    silent drop, and never "no such instrument";
  * `records_probe` decides whether a book/page search exists. This module never assumes one. If
    no probe has run, it says so in the result and uses the routes that are proven;
  * every budget is checked BEFORE the work, and a walk that stops on its budget says which
    citations it never reached.

Run it through `run_documents.py --walk-cites`; the CLI here is for proving one case by hand:

    python document_walk.py --case 2026-020206-CC-25 --depth 2 --budget 12
"""
import argparse
import json
import os
import re

import document_classify
import document_collectors as DC

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, 'records_index.json')
COUNTY = 'MIAMI-DADE'

# A walk is bounded three ways, and each bound answers a different runaway.
DEFAULT_DEPTH = 2          # a document cites a document that cites a document. Two hops is the
                           # satisfaction -> mortgage -> assignment chain; past that the citations
                           # are usually legal boilerplate about unrelated instruments.
DEFAULT_BUDGET = 12        # documents fetched per case per run. The pilot judgment cites 4.
DEFAULT_NAME_BUDGET = 0    # extra owner-name searches per case. ZERO by default: each one may cost
                           # a Camoufox run or a 2Captcha solve, and a nightly that quietly triples
                           # its captcha spend is a bill nobody agreed to.


def _norm(value):
    """Book and page compare as numbers-without-leading-zeros. The clerk is inconsistent about
    zero-padding between the index, the recording stamp and the body text of a document, and
    '04642' != '4642' is the kind of mismatch that reads as 'not found'."""
    return str(value or '').strip().lstrip('0') or '0'


def key_of(book, page):
    return '%s/%s' % (_norm(book), _norm(page))


# ---------------------------------------------------------------------------------------------
# 1. The index: everything we have ever seen the county say about a book/page.
# ---------------------------------------------------------------------------------------------
class RecordIndex:
    """book/page -> the recording keys needed to FETCH that instrument.

    `MiamiCollector._recorded_instrument` needs `reC_BOOK`, `reC_PAGE` and `cfN_MASTER_ID`, and a
    book/page alone is not enough to address a document. Every `recordingModels` list the pipeline
    pulls carries those keys for dozens of instruments we were not looking for. Throwing them away
    after one owner's analysis and re-fetching the county later is the waste this closes: the index
    compounds exactly like `records_qs.json` does, so a citation resolved for one case is resolved
    for every later case for free.

    It stores recording keys and document types — the county's own index metadata. It does not
    store document text, owner contact data, or anything about a lead.
    """

    FIELDS = ('reC_BOOK', 'reC_PAGE', 'booK_TYPE', 'cfN_MASTER_ID', 'doC_TYPE', 'doC_PAGES',
              'foliO_NUMBER', 'subdiV_NAME', 'reC_DATE')

    def __init__(self, path=INDEX):
        self.path = path
        self.rows = {}
        self.dirty = False
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.rows = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self.rows = {}

    def add_models(self, models):
        """Index every addressable row. Returns how many were new."""
        added = 0
        for model in models or []:
            if not isinstance(model, dict):
                continue
            book, page = model.get('reC_BOOK'), model.get('reC_PAGE')
            if not (str(book or '').strip() and str(page or '').strip()):
                continue
            # No CFN means no fetch. Indexing it would make `resolve` claim a hit and then hand
            # the collector a record it refuses — a resolution that fails one layer later reads
            # like a transport fault instead of a missing key.
            if not str(model.get('cfN_MASTER_ID') or '').strip():
                continue
            key = key_of(book, page)
            if key in self.rows:
                continue
            self.rows[key] = {f: model.get(f) for f in self.FIELDS}
            added += 1
        self.dirty = self.dirty or bool(added)
        return added

    def get(self, book, page):
        return self.rows.get(key_of(book, page))

    def save(self):
        if not self.dirty:
            return False
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(self.rows, fh, sort_keys=True)
        os.replace(tmp, self.path)
        self.dirty = False
        return True


# ---------------------------------------------------------------------------------------------
# 2. Whose names to search, when the current owner's is not enough.
# ---------------------------------------------------------------------------------------------
# A party string from the county index carries entity noise that would make two spellings of the
# same company look like two parties. Same normalisation idea as records_liens._inst, kept local
# because this one is about DEDUPING names to search, not about matching lenders.
_NAME_NOISE_RE = re.compile(
    r'\b(INC|CORP|CO|LLC|L\.L\.C|LP|LLP|LTD|PA|PLLC|NA|N\.A|THE|AND|OF|A|AN|ET\s?AL|ET\s?UX|'
    r'ET\s?VIR|JR|SR|II|III|IV|TRS|TRUSTEE|H/W|W/H)\b', re.I)
# Parties that are never worth a search: they hold liens, they do not own the house, and their
# name search returns the whole county.
_INSTITUTIONAL_RE = re.compile(
    r'BANK|MORTGAGE|SERVICING|FEDERAL|NATIONAL ASSOC|FANNIE|FREDDIE|MERS|MORTGAGE ELECTRONIC|'
    r'\bIRS\b|INTERNAL REVENUE|UNITED STATES|STATE OF|CITY OF|COUNTY OF|CLERK OF|TAX COLLECTOR|'
    r'DEPARTMENT OF|UNKNOWN|TENANT|OCCUPANT|JOHN DOE|JANE DOE|ANY AND ALL', re.I)

_DEED_RE = re.compile(r'^(DEED|WARRANTY DEED|QUIT ?CLAIM|QUITCLAIM|SPECIAL WARRANTY|'
                      r'CERTIFICATE OF TITLE|TRUSTEE.?S DEED|PERSONAL REP)', re.I)


def _name_key(name):
    return re.sub(r'[^A-Z ]', '', _NAME_NOISE_RE.sub(' ', (name or '').upper())).split()


def party_candidates(models, folio, subdivision='', docket=None, known=()):
    """Names worth searching BESIDES the current owner, best first.

    Where they come from, in order of how strongly they tie to this parcel:
      1. both sides of every DEED that carries the subject folio — the grantor is the prior owner,
         and a prior owner's unreleased mortgage is the exact instrument the name search misses;
      2. both sides of every deed on the subject SUBDIVISION, when the deed carries no folio
         (most newer instruments do not — the same reason `analyze` anchors on subdivision);
      3. the docket's own parties, which name trusts and estates the recorder may index
         differently from the appraiser's owner string.

    Institutional parties are dropped: a lender's name search returns the county, not a chain.
    Returns [{'name', 'why', 'source_ref'}], deduped against `known` and against itself.
    """
    import records_liens as R
    fol = R.norm_folio(folio)
    sub = (subdivision or '').strip().upper()
    seen = {tuple(_name_key(n)) for n in known if n}
    out = []

    def offer(name, why, ref):
        name = re.sub(r'\s{2,}', ' ', str(name or '').strip())
        if len(name) < 4 or _INSTITUTIONAL_RE.search(name):
            return
        if R.untraceable_owner(name):
            return
        tokens = tuple(_name_key(name))
        if len(tokens) < 2 or tokens in seen:
            return
        seen.add(tokens)
        out.append({'name': name, 'why': why, 'source_ref': ref})

    for tier, want_folio in ((1, True), (2, False)):
        for model in models or []:
            if not isinstance(model, dict):
                continue
            if not _DEED_RE.match(str(model.get('doC_TYPE') or '').strip()):
                continue
            on_folio = bool(fol and R.norm_folio(model.get('foliO_NUMBER')) == fol)
            on_sub = bool(sub and str(model.get('subdiV_NAME') or '').strip().upper() == sub)
            if want_folio and not on_folio:
                continue
            if not want_folio and (on_folio or not on_sub):
                continue
            ref = 'official_records/%s-%s' % (model.get('reC_BOOK'), model.get('reC_PAGE'))
            why = ('deed on the subject folio' if tier == 1
                   else 'deed on the subject subdivision')
            offer(model.get('firsT_PARTY'), why + ' (grantor)', ref)
            offer(model.get('seconD_PARTY'), why + ' (grantee)', ref)

    for index, party in enumerate((docket or {}).get('parties') or []):
        if not isinstance(party, dict):
            offer(party, 'named on the court docket', 'dockets/parties/%d' % index)
            continue
        # The PLAINTIFF is the bank or the association foreclosing. Searching its name returns
        # every filing it has ever made in the county and nothing about this parcel, so it is
        # dropped by role and not only by the institutional pattern — an HOA plaintiff carries no
        # lender word and would otherwise sail through.
        kind = str(party.get('partyTypeDesc') or party.get('partyType') or '').upper()
        if 'PLAINTIFF' in kind:
            continue
        offer(party.get('partyName'), 'named on the court docket', 'dockets/parties/%d' % index)
    return out


# ---------------------------------------------------------------------------------------------
# 3. Resolving one citation into something the collector can fetch.
# ---------------------------------------------------------------------------------------------
class Resolution(dict):
    """Either `record` (fetchable) or `reason` (why not). Never both, never neither."""


def resolve(book, page, index, caps_path=None, searcher=None):
    """book/page -> a recordingModel-shaped record the collector can address, or a named gap.

    Three routes, cheapest first:
      index    we have already seen this instrument in some owner's result set. Free.
      search   a book/page search on the live endpoint. Used ONLY when `records_probe` has
               CONFIRMED that such a search exists — see that module for why an assumed parameter
               is worse than no parameter.
      (none)   an honest unresolved citation naming which route was unavailable.
    """
    import records_probe
    hit = index.get(book, page)
    if hit:
        return Resolution(record=dict(hit), via='index')
    ok, shape = records_probe.confirmed('book_page',
                                        caps_path or records_probe.CAPS)
    if not ok:
        probed = records_probe.load_caps(caps_path or records_probe.CAPS).get('probed_at')
        return Resolution(reason=(
            'not in the index, and no book/page search is available%s'
            % ('' if probed else ' (records_probe has never run on a machine with clerk access)')),
            via='unresolved')
    if searcher is None:
        return Resolution(reason='book/page search is confirmed but no searcher was supplied',
                          via='unresolved')
    try:
        models = searcher(book, page, shape) or []
    except Exception as exc:
        return Resolution(reason='book/page search failed: %s: %s'
                                 % (type(exc).__name__, str(exc)[:140]), via='unresolved')
    index.add_models(models)
    hit = index.get(book, page)
    if hit:
        return Resolution(record=dict(hit), via='search')
    return Resolution(reason='book/page search ran and the county returned no such instrument',
                      via='unresolved')


# ---------------------------------------------------------------------------------------------
# 4. The walk.
# ---------------------------------------------------------------------------------------------
def pending_citations(rows, already):
    """Citations on these rows that we have not addressed yet, in the order they were read."""
    out = []
    for row in rows or []:
        for cite in row.get('cited_instruments') or []:
            key = key_of(cite.get('book'), cite.get('page_no'))
            if key in already:
                continue
            already.add(key)
            out.append({'book': cite.get('book'), 'page_no': cite.get('page_no'),
                        'cited_by': row.get('source_ref'),
                        'cited_on_page': cite.get('cited_on_page'),
                        'passage': cite.get('passage')})
    return out


def walk(case, rows, models=None, collector=None, queue=None, ocr=None, county=COUNTY,
         depth=DEFAULT_DEPTH, budget=DEFAULT_BUDGET, index=None, searcher=None,
         caps_path=None, gray_cutoff=None, keep_images=False):
    """Follow what the already-read documents cite, `depth` hops deep, `budget` documents wide.

    `rows` are `collect_recorded` result rows that have been through
    `case_dossier.classify_documents`, so they carry `cited_instruments`. Returns
    `(new_rows, report)`. `new_rows` are the same shape as `rows` and are meant to be appended to
    the case's document list; `report` says what was followed, what was not, and why.
    """
    import case_dossier
    import miami_judgment as MJ
    index = index if index is not None else RecordIndex()
    index.add_models(models or [])
    # Everything already fetched is addressed. Without this the first hop re-fetches the document
    # that did the citing, because a recorded instrument's own stamp cites its own book and page.
    seen = {key_of(r.get('doc_book'), r.get('doc_page')) for r in rows or []}
    for row in rows or []:
        match = re.search(r'official_records/(\d+)-(\d+)', str(row.get('source_ref') or ''))
        if match:
            seen.add(key_of(match.group(1), match.group(2)))

    followed, unresolved, new_rows = [], [], []
    frontier, spent, stopped = list(rows or []), 0, ''
    for hop in range(1, max(1, int(depth)) + 1):
        citations = pending_citations(frontier, seen)
        if not citations:
            break
        batch = []
        for cite in citations:
            if spent >= budget:
                stopped = ('budget: %d document(s) is this run\'s cap, reached at hop %d'
                           % (budget, hop))
                unresolved.append(dict(cite, reason=stopped, via='not_attempted'))
                continue
            outcome = resolve(cite['book'], cite['page_no'], index,
                              caps_path=caps_path, searcher=searcher)
            if not outcome.get('record'):
                unresolved.append(dict(cite, reason=outcome['reason'], via=outcome['via']))
                continue
            spent += 1
            batch.append((cite, outcome))
        if not batch:
            continue
        fetched = MJ.collect_recorded(case, [o['record'] for _c, o in batch],
                                      collector=collector, queue=queue, county=county, ocr=ocr,
                                      keep_images=keep_images, gray_cutoff=gray_cutoff)
        for (cite, outcome), row in zip(batch, fetched):
            # Provenance travels with the document. A reader looking at the dossier must be able to
            # see that this instrument is here because another document named it, and which line of
            # which page did the naming — that passage is the whole argument for trusting it.
            row['walked'] = {'hop': hop, 'resolved_via': outcome['via'],
                             'cited_by': cite['cited_by'],
                             'cited_on_page': cite['cited_on_page'],
                             'passage': cite['passage'],
                             'book': cite['book'], 'page_no': cite['page_no']}
            followed.append({'book': cite['book'], 'page_no': cite['page_no'], 'hop': hop,
                             'resolved_via': outcome['via'], 'status': row.get('status'),
                             'doc_type': row.get('doc_type'), 'cited_by': cite['cited_by']})
            new_rows.append(row)
        case_dossier.classify_documents(new_rows[-len(batch):])
        frontier = new_rows[-len(batch):]
        if spent >= budget:
            break
    index.save()
    return new_rows, {
        'depth': int(depth), 'budget': int(budget), 'documents_fetched': spent,
        'followed': followed, 'unresolved': unresolved, 'stopped_because': stopped,
        # Said in words rather than left to be inferred from an empty list. A walk that resolved
        # nothing and a walk that was never able to resolve anything look identical otherwise.
        'note': ('Citations are what a document says about another instrument. A followed document '
                 'is evidence at rung c only; nothing here changes the equity verdict.'),
    }


def name_search_plan(models, folio, subdivision='', docket=None, owner='', limit=0):
    """The names worth searching besides the owner, capped, with the reason for each.

    Returned as a PLAN, not executed. Each name costs a Camoufox run or a 2Captcha solve, and the
    nightly's captcha spend is a number Alejandro watches. A caller that wants to spend it says so
    explicitly and says how much; a caller that does not still gets to see, in the dossier, which
    names it declined to search and why that matters.
    """
    candidates = party_candidates(models, folio, subdivision=subdivision, docket=docket,
                                  known=[owner] if owner else ())
    plan = candidates[:limit] if limit else []
    return {'candidates': candidates, 'planned': plan, 'limit': int(limit),
            'skipped': max(0, len(candidates) - len(plan)),
            'why': ('Each name is one more Official Records search. The owner-name search cannot '
                    'see a lien recorded against a prior owner; these names can.')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', required=True)
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH)
    parser.add_argument('--budget', type=int, default=DEFAULT_BUDGET)
    parser.add_argument('--index-only', action='store_true',
                        help='print what the index knows and stop; no network')
    args = parser.parse_args(argv)
    index = RecordIndex()
    if args.index_only:
        print('%s: %d instrument(s) addressable' % (INDEX, len(index.rows)))
        return 0
    print('document_walk is driven by run_documents.py --walk-cites; this CLI proves one case.')
    print('case %s, depth %d, budget %d, index holds %d instrument(s)'
          % (args.case, args.depth, args.budget, len(index.rows)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
