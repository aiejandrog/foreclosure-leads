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

WHICH OF THE TWO CARRIES THE WEIGHT — ANSWERED, NOT ASSUMED
`records_probe` ran on the desktop on 2026-09-22 against book 35287 page 4642, an instrument known
to exist. All three book/page search shapes came back `accepted_no_hits`: the county issued a
search token and returned nothing, which is what an IGNORED parameter looks like. So there is no
confirmed way to ask this endpoint for an instrument by its book and page, and route 1 can only
address a citation whose instrument is already in `records_index.json`.

That makes route 2 load-bearing rather than supplementary, and it is why `run_name_searches`
executes instead of only planning. A name search returns the instrument AND indexes it on the way
past, so the names are run BEFORE the citation pass — running them after would leave a resolvable
citation unresolved for a whole run. The CFN and folio shapes were never probed (the pilot
judgment is indexed at folio 0 and its CFN was not to hand), so nothing here claims they do not
exist; re-run the probe with `--cfn` and `--folio` off a recorded mortgage to settle them.

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

# ONE definition of "what book/page is this", shared by every path that asks. These used to live
# here, and the dossier path did not call them: a case's own two-page judgment came back as two
# unfetched citations and an open gap that said a document we were standing on had not been
# fetched. A rule enforced in two of three places is not a rule.
from document_classify import key_of, own_spans, _norm      # noqa: F401  (re-exported)
from document_classify import stamp_run_pages, not_followed_reason  # noqa: F401  (re-exported)

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


def anchor_of(models, folio, subdivision=''):
    """What ties a record to THIS parcel: a folio, a subdivision, or nothing. -> a dict.

    WHY THIS IS ITS OWN FUNCTION AND WHY IT IS REPORTED
    On 2024-014334-CA-01 the name search returned 500 records and `on_parcel: 0`, which reads
    like a finding about the owner and is not one. The case had no cached chain, so `subdivision`
    arrived empty, and `party_candidates` asks for a deed on the subject FOLIO or the subject
    SUBDIVISION. With neither, tiers 1 and 2 can match nothing, exactly one candidate came from
    the docket, and every record the search returned was unanchorable BY CONSTRUCTION. More
    names would only have bought more unanchored records.

    `records_liens.analyze` solves this by reading the subdivision off a record that DOES carry
    the subject folio — usually the deed, since folio is blank on most newer mortgages. Same rule
    here, so the stage anchors itself instead of depending on a chain it may not have.
    """
    import records_liens as R
    fol = R.norm_folio(folio)
    sub = (subdivision or '').strip().upper()
    if not sub and fol:
        for model in models or []:
            if not isinstance(model, dict):
                continue
            if R.norm_folio(model.get('foliO_NUMBER')) != fol:
                continue
            sub = str(model.get('subdiV_NAME') or '').strip().upper()
            if sub:
                break
    if fol or sub:
        return {'folio': fol, 'subdivision': sub, 'anchored': True,
                'from': 'chain' if (subdivision or '').strip() else
                        ('a record carrying the subject folio' if sub else 'the lead folio')}
    return {'folio': '', 'subdivision': '', 'anchored': False,
            'why': ('this case has no folio and no subdivision, so no record can be tied to the '
                    'parcel: every search will report 0 on-parcel hits whatever it returns, and '
                    'the deed tiers of the candidate list cannot match anything')}


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
    ok, shape = records_probe.confirmed('book_page', caps_path)
    if not ok:
        # Say which of the two very different things is true. "No search is available" because
        # nobody has ever asked the county is a to-do; "no search is available" because the
        # county was asked on a named date and answered no is a finding, and a reader who cannot
        # tell them apart re-runs a probe that has already been paid for.
        verdict = records_probe.load_caps(caps_path)
        entry = (verdict.get('capabilities') or {}).get('book_page') or {}
        # The date on the FILE does not mean this shape was tried. A probe run that solved three
        # book/page shapes and skipped cfn for want of a CFN writes one `probed_at` and one
        # capability; reading the file-level date would report the skipped shapes as answered.
        if entry and verdict.get('probed_at'):
            detail = ('; probed %s and not found (%s)'
                      % (verdict['probed_at'][:10], entry.get('best_outcome') or 'no capability'))
        else:
            detail = ('; no probe verdict at %s — run records_probe.py --book B --page P from a '
                      'machine with clerk access' % records_probe.caps_path())
        return Resolution(reason='not in the index, and no book/page search is available' + detail,
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
def pending_citations(rows, already, skipped=None):
    """Citations on these rows that we have not addressed yet, in the order they were read.

    Page stamps after an exhibit's first page and declaration or plat recitals are not followed;
    when `skipped` is a list they are appended to it with the reason."""
    out = []
    for row in rows or []:
        cites = row.get('cited_instruments') or []
        stamps = stamp_run_pages(cites)
        for cite in cites:
            key = key_of(cite.get('book'), cite.get('page_no'))
            if key in already:
                continue
            already.add(key)
            reason = not_followed_reason(cite, stamps)
            if reason:
                if skipped is not None:
                    skipped.append({'book': cite.get('book'), 'page_no': cite.get('page_no'),
                                    'cited_by': row.get('source_ref'), 'reason': reason})
                continue
            out.append({'book': cite.get('book'), 'page_no': cite.get('page_no'),
                        'cited_by': row.get('source_ref'),
                        'cited_on_page': cite.get('cited_on_page'),
                        'passage': cite.get('passage')})
    return out


def walk(case, rows, models=None, collector=None, queue=None, ocr=None, county=COUNTY,
         depth=DEFAULT_DEPTH, budget=DEFAULT_BUDGET, index=None, searcher=None,
         caps_path=None, gray_cutoff=None, keep_images=False, name_plan=None,
         name_searcher=None, folio='', subdivision=''):
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
    # NAMES FIRST, and this order is the probe's doing. On 2026-09-22 every book/page search shape
    # came back accepted_no_hits, so a citation that is not already in the index has exactly one
    # way to become addressable: a search under some other name returns the instrument and indexes
    # it on the way past. Running the names after the citation pass would leave every one of those
    # citations unresolved on this run and resolvable only on the next.
    names = None
    if name_plan and name_searcher is not None:
        names = run_name_searches(name_plan, index, name_searcher, folio, subdivision,
                                  owner_models=models)
    # Everything already fetched is addressed. Without this the first hop re-fetches the document
    # that did the citing, because a recorded instrument's own stamp cites its own book and page.
    seen = own_spans(rows)

    followed, unresolved, new_rows, not_followed = [], [], [], []
    frontier, spent, stopped = list(rows or []), 0, ''
    for hop in range(1, max(1, int(depth)) + 1):
        citations = pending_citations(frontier, seen, not_followed)
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
        current_rows = []
        for cite, outcome in batch:
            fetched = MJ.collect_recorded(case, [outcome['record']],
                                      collector=collector, queue=queue, county=county, ocr=ocr,
                                      keep_images=keep_images, gray_cutoff=gray_cutoff)
            if not fetched:
                unresolved.append(dict(cite, reason='collector returned no document row', via='fetch_missing'))
                continue
            row = fetched[0]
            if row.get('status') != 'stored':
                unresolved.append(dict(cite, reason=row.get('reason') or 'document was not stored', via='fetch_failed'))
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
            current_rows.append(row)
        case_dossier.classify_documents(current_rows, case=case)
        frontier = current_rows
        if spent >= budget:
            break
    for cite in pending_citations(frontier, seen):
        reason = stopped or ('budget reached' if spent >= budget else 'depth limit reached')
        stopped = reason
        unresolved.append(dict(cite, reason=reason, via='not_attempted'))
    for gap in unresolved:
        gap['status'] = 'cited_but_not_fetched'
    index.save()
    return new_rows, {
        'depth': int(depth), 'budget': int(budget), 'documents_fetched': spent,
        'followed': followed, 'unresolved': unresolved, 'not_followed': not_followed,
        'stopped_because': stopped,
        'names': names,
        # Said in words rather than left to be inferred from an empty list. A walk that resolved
        # nothing and a walk that was never able to resolve anything look identical otherwise.
        'note': ('Citations are what a document says about another instrument. A followed document '
                 'is evidence at rung c only; nothing here changes the equity verdict.'),
    }


class NameSearcher:
    """Run one Official Records NAME search, by the same token ladder records_liens uses.

    PROBED AND ANSWERED 2026-09-22 (desktop, committed as `records_probe_findings.json`): all three
    book/page
    shapes came back `accepted_no_hits`, so there is no confirmed way to ask this endpoint for an
    instrument by its book and page. Names are therefore not a nice-to-have second route — with
    the index cold, they are the ONLY way to widen past the current owner, and that is why this
    executes rather than only planning.

    The ladder is records_liens': cached token -> Camoufox (free) -> 2Captcha (~$0.003). It is not
    reimplemented here; drifting from the tracer's own token path would make a failure ambiguous
    between "this name has no records" and "this file mints tokens wrong". A token it earns is
    written back to records_qs.json, so the same name costs nothing next time.
    """

    def __init__(self, qs_cache=None, use_camoufox=True, paid_search=None):
        import records_liens as R
        self.R = R
        self.qs_cache = qs_cache if qs_cache is not None else {}
        self.use_camoufox = use_camoufox
        self.paid_search = paid_search
        self._cm = self._browser = None
        self.spent_free = self.spent_paid = 0

    def _camoufox(self):
        if self._cm is None and self.use_camoufox:
            self._cm, self._browser = self.R.camoufox_session()
            if self._browser is None:
                self.use_camoufox = False
        return self._browser

    def search(self, name):
        """models for this party name, or None when the county could not be asked."""
        if name in self.qs_cache:
            models = self.R.records_by_qs(self.qs_cache[name])
            if models is not None:
                return models
        parts = self.R.split_owner(name)
        if not parts:
            return None
        browser = self._camoufox()
        if browser is not None:
            try:
                token = self.R.camoufox_qs(browser, parts)
            except Exception:
                token = None
            if token:
                models = self.R.records_by_qs(token)
                if models is not None:
                    self.spent_free += 1
                    self.qs_cache[name] = token
                    return models
        self.spent_paid += 1
        return (self.paid_search or self.R.fetch_via_turnstile)(parts)

    def close(self):
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
            self._cm = self._browser = None


_OPEN_KILLER_RE = re.compile(r'SATISF|RELEASE|TERMINAT|CANCELLATION|DISCHARGE', re.I)
_ENCUMBRANCE_RE = re.compile(r'^(MORTGAGE|LIEN|JUDGMENT|NOTICE OF (?:LIEN|COMMENCEMENT)|CLAIM|'
                             r'FINANCING STATEMENT|TAX)', re.I)


def _distinct_party(name):
    # One rule for "same party" across the paid-read tie and this filter: generic lender words
    # ("BANK", "NATIONAL") never make two lenders one party.
    import document_prioritizer as DP
    return DP._distinct_party(name)


def this_case_of(inventory):
    """What marks a recorded instrument as this foreclosure's own: the plaintiff's names, the
    book/page the docket says its own filings were recorded at, the docket's judgment dates and
    the date of its first entry (when its lis pendens is recorded)."""
    import document_prioritizer as DP
    import miami_judgment as MJ
    raw = (inventory or {}).get('raw') or {}
    pages, judgments, dates = set(), set(), []
    for entry in (inventory or {}).get('entries') or []:
        meta = entry.get('metadata') or {}
        m = re.search(r'(\d{3,6})\D+(\d{1,5})', str(meta.get('bookAndPage') or ''))
        if m:
            pages.add(key_of(m.group(1), m.group(2)))
        when = DP._record_date(meta.get('eventDate'))
        if not when:
            continue
        dates.append(when)
        text = '%s %s' % (meta.get('docketDescrition') or meta.get('docketDescription') or '',
                          meta.get('comments') or '')
        if DP.classify(text) == 'final_judgment':
            judgments.add(when)
    return {'plaintiffs': MJ.plaintiffs_of(raw), 'book_pages': pages,
            'judgment_dates': sorted(judgments), 'filed': min(dates) if dates else None}


def _recorded_with_this_case(model, this_case):
    """A plaintiff-party instrument is this case's only when it was recorded where this case would
    record it: a judgment near a docket judgment date, a lis pendens near the case's first entry.
    The same plaintiff's separate action against the owner stays a claim (Greptile on #61)."""
    import document_prioritizer as DP
    recorded = DP._record_date(model.get('reC_DATE'))
    if not recorded:
        return False
    before, after = DP.RECORDING_WINDOW_BEFORE, DP.RECORDING_WINDOW_AFTER
    if re.search(r'LIS PENDENS', str(model.get('doC_TYPE') or ''), re.I):
        filed = this_case.get('filed')
        return bool(filed) and filed - before <= recorded <= filed + after
    return any(d - before <= recorded <= d + after for d in this_case.get('judgment_dates') or ())


def own_case_basis(model, this_case):
    """'docket_book_page', 'plaintiff_party' or None. A judgment or lis pendens the docket itself
    recorded, or one between this case's plaintiff and anyone recorded when this case would record
    it, is this foreclosure's own filing (2024-014878's vacated judgment 34932/1256 was counted as
    a claim: 12-case verification, defect 7). A lender name that is only generic words never
    matches."""
    if not this_case:
        return None
    if key_of(model.get('reC_BOOK'), model.get('reC_PAGE')) in (this_case.get('book_pages') or ()):
        return 'docket_book_page'
    if not re.search(r'JUDGMENT|LIS PENDENS', str(model.get('doC_TYPE') or ''), re.I):
        return None
    if not _recorded_with_this_case(model, this_case):
        return None
    for plaintiff in this_case.get('plaintiffs') or ():
        want = _distinct_party(plaintiff)
        if not want:
            continue
        for field in ('firsT_PARTY', 'seconD_PARTY'):
            have = _distinct_party(model.get(field))
            if have and (want <= have or have <= want):
                return 'plaintiff_party'
    return None


def run_name_searches(plan, index, searcher, folio, subdivision='', owner_models=None,
                      this_case=None):
    """Execute the planned name searches. Index what comes back, and report the encumbrances that
    sit on the SUBJECT parcel under a name the owner search never used.

    What this reports is evidence, not a verdict. It does not touch `records_liens.analyze`, so no
    equity number moves because of it: an instrument found here is a question for the operator to
    put to the owner, on the same footing as Palm Beach's "unaccounted mortgage" line. Folding it
    into the chain arithmetic is a separate decision, and taking it here would smuggle a new input
    into the equity engine through a document-reading branch.

    Exact folios are parcel matches. A conflicting folio cannot fall back to subdivision;
    subdivision-only hits are uncertain candidates. Money judgments and tax liens remain
    search-result candidates even without parcel linkage, never established attachments.

    `this_case` (from this_case_of) moves this foreclosure's own recorded judgments and lis
    pendens out of the claims into `own_case_instruments`, each marked `own_case`.
    """
    import records_liens as R
    fol = R.norm_folio(folio)
    sub = (subdivision or '').strip().upper()
    searched, found, candidates, uncertain, claims, gaps = [], [], [], [], [], []
    own = []
    releases = []
    baseline = None if owner_models is None else {
        key_of(m.get('reC_BOOK'), m.get('reC_PAGE')) for m in owner_models}
    for candidate in plan or []:
        name = candidate['name']
        try:
            models = searcher.search(name)
        except Exception as exc:
            searched.append(dict(candidate, outcome='error',
                                 coverage='unknown',
                                 reason='%s: %s' % (type(exc).__name__, str(exc)[:140])))
            gaps.append(dict(searched[-1]))
            continue
        if models is None:
            # The county was not reached. That is NOT "this name is clean" and must never read as
            # it: an empty search that prints like a clean title check is the most dangerous
            # output this repo can produce (records_liens says so in as many words).
            searched.append(dict(candidate, outcome='not_reached',
                                 coverage='unknown',
                                 reason='no search token could be obtained for this name'))
            gaps.append(dict(searched[-1]))
            continue
        index.add_models(models)
        on_parcel = []
        for model in models:
            doc_type = str(model.get('doC_TYPE') or '').strip()
            if _OPEN_KILLER_RE.search(doc_type):
                releases.append({'book': model.get('reC_BOOK'), 'page_no': model.get('reC_PAGE'),
                                 'doc_type': doc_type, 'rec_date': model.get('reC_DATE'),
                                 'under_name': name, 'reference_status': 'not_checked',
                                 'record': dict(model)})
                continue
            money_claim = bool(re.search(r'JUDGMENT|(?:FEDERAL|STATE).*TAX.*LIEN|TAX.*LIEN', doc_type, re.I))
            if (not _ENCUMBRANCE_RE.match(doc_type) and not money_claim) or _OPEN_KILLER_RE.search(doc_type):
                continue
            rf = R.norm_folio(model.get('foliO_NUMBER'))
            sd = str(model.get('subdiV_NAME') or '').strip().upper()
            exact = bool(fol and rf == fol)
            subdivision_only = bool(not rf and sub and sd == sub)
            row = {
                'doc_type': doc_type, 'rec_date': model.get('reC_DATE'),
                'book': model.get('reC_BOOK'), 'page_no': model.get('reC_PAGE'),
                'other_party': str(model.get('seconD_PARTY') or '')[:60],
                'anchored_by': 'folio' if exact else ('subdivision' if subdivision_only else None),
                'parcel_status': 'matched' if exact else 'unknown',
                'under_name': name, 'why': candidate.get('why', ''),
                'amount': model.get('amount', model.get('consideratioN_1')),
                'amount_basis': 'index_metadata_unverified',
                'owner_search_missed': None if baseline is None else key_of(model.get('reC_BOOK'), model.get('reC_PAGE')) not in baseline,
            }
            basis = own_case_basis(model, this_case)
            if basis:
                row.update(own_case=True, this_case=basis)
                own.append(row)
            elif money_claim:
                claims.append(dict(row, attachment_status='unknown',
                    identity_status='search_result_only', satisfaction_status='unknown'))
            if exact:
                on_parcel.append(row)
                candidates.append(row)
            elif subdivision_only:
                uncertain.append(row)
        searched.append(dict(candidate, outcome='searched', records=len(models),
                             on_parcel=len(on_parcel), coverage='unknown' if len(models) >= 500 else 'returned_records',
                             reason='500-record search cap; remaining records unknown' if len(models) >= 500 else ''))
        if len(models) >= 500:
            gaps.append(dict(searched[-1]))
        for row in on_parcel:
            if row['owner_search_missed'] is True:
                found.append(row)
    return {
        'searched': searched,
        'found_under_other_names': found,
        'parcel_candidates': candidates,
        'uncertain_parcel_candidates': uncertain,
        'potential_title_party_claims': claims,
        'own_case_instruments': own,
        'satisfaction_candidates': releases,
        'gaps': gaps,
        'tokens_free': getattr(searcher, 'spent_free', 0),
        'tokens_paid': getattr(searcher, 'spent_paid', 0),
        'note': ('Only exact-folio instruments absent from a supplied owner-search baseline are '
                 'reported as missed. Subdivision and debtor-name matches remain uncertain; '
                 'attachment and satisfaction require document evidence. No equity changes.'),
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
            'anchor': anchor_of(models, folio, subdivision),
            'why': ('Each name is one more Official Records search. The owner-name search cannot '
                    'see a lien recorded against a prior owner; these names can.')}


def stored_rows(county, case):
    """Documents already on disk for this case, as classified rows the walk can read citations off.

    This is what makes the by-hand CLI worth running. The pilot CLI had already fetched and read
    the pilot judgment four times when this was written, and `document_walk --case` still printed
    "index holds 0 instruments" and stopped, because nothing connected the two. A document that
    has been read is evidence whichever tool read it.
    """
    import case_dossier
    import document_store as DS
    rows = []
    for manifest, reading in DS.stored_documents(county, case):
        row = {'source_ref': manifest.get('source_ref') or manifest.get('doc_name') or '',
               'status': 'stored', 'doc_type': manifest.get('doc_name'),
               'document_key': manifest.get('document_key'),
               'pages': manifest.get('pages'),
               'read_status': reading.get('read_status'),
               'path': manifest.get('path'), 'reading': reading,
               'from_store': True}
        rows.append(row)
    case_dossier.classify_documents(rows, case=case)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--case', required=True)
    parser.add_argument('--county', default=COUNTY)
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH)
    parser.add_argument('--budget', type=int, default=DEFAULT_BUDGET)
    parser.add_argument('--index-only', action='store_true',
                        help='print what the index knows and stop; no network')
    parser.add_argument('--dry-run', action='store_true',
                        help='list the citations and how each one would resolve; fetch nothing')
    parser.add_argument('--records', default='',
                        help='a saved recordingModels file (or_rows.json) to fold into the index '
                             'first, so its instruments become addressable without a search')
    args = parser.parse_args(argv)
    index = RecordIndex()
    if args.index_only:
        print('%s: %d instrument(s) addressable' % (INDEX, len(index.rows)))
        return 0
    if args.records:
        with open(args.records, encoding='utf-8') as fh:
            models = json.load(fh)
        added = index.add_models(models if isinstance(models, list) else [])
        index.save()
        print('%s: %d instrument(s) indexed (%d new)' % (args.records, len(models), added))

    rows = stored_rows(args.county, args.case)
    read = [r for r in rows if (r.get('reading') or {}).get('pages')]
    print('%s %s: %d document(s) in the store, %d with text'
          % (args.county, args.case, len(rows), len(read)))
    if not read:
        print('Nothing has been read for this case, so there are no citations to follow. A walk '
              'reads what a document SAYS; it cannot invent citations for a document nobody has '
              'opened. Fetch and read first:')
        print('  python -u miami_judgment.py %s --records or_rows.json --keep-images' % args.case)
        return 4

    # Seed with the documents' OWN recording spans, exactly as walk() does. This CLI used to
    # pass an empty set, so `--dry-run` listed a judgment's own stamps back as citations of
    # itself: seven lines where the case has one real citation. The suppression is not a
    # property of walk(), it is a property of what counts as a citation, so both paths seed it.
    citations = pending_citations(rows, own_spans(rows))
    print('%d citation(s) in the read text:' % len(citations))
    for cite in citations:
        outcome = resolve(cite['book'], cite['page_no'], index)
        state = ('addressable (%s)' % outcome['via']) if outcome.get('record') else outcome['reason']
        print('  %s/%s  cited by %s p%s  -> %s'
              % (cite['book'], cite['page_no'], cite['cited_by'], cite['cited_on_page'], state))
    if args.dry_run:
        print('--dry-run: nothing fetched.')
        return 0

    new_rows, report = walk(args.case, rows, county=args.county, index=index,
                            depth=args.depth, budget=args.budget)
    print('fetched %d document(s); %d citation(s) unresolved'
          % (report['documents_fetched'], len(report['unresolved'])))
    for row in new_rows:
        print('  %s  %s  %s' % (row.get('source_ref'), row.get('status'),
                                (row.get('classification') or {}).get('kind', '?')))
    for miss in report['unresolved']:
        print('  UNRESOLVED %s/%s: %s' % (miss.get('book'), miss.get('page_no'),
                                          miss.get('reason')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
