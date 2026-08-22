#!/usr/bin/env python
"""broward_judgment_dates — get Broward/PB judgment dates from RECORDED documents, not the docket.

THE PROBLEM
372 Broward + Palm Beach leads carry a final judgment totalling $114,113,063 with NO entry date, so
judgment_interest.py cannot accrue FS 55.03 interest on any of them. Every one of those leads shows a
payoff that is too small, which means the equity we print is too big — the exact direction of error
that sent a closer after a lead with no equity.

WHY NOT THE CLERK DOCKET
browardclerk.org is behind a Cloudflare turnstile that blocks headless automation outright (it passes
in a real logged-in browser, which is not something a nightly job can rely on). That is why this gap
sat open.

STATUS: ✅ WORKING via the NAME search (route 3 below). Two other routes were measured and eliminated
first — they are documented so nobody re-walks them.

HOW IT WORKS (route 3). Search a name -> the results grid carries a **CaseNumber** field (the
broward_liens docstring's field list omits it, which is why this looked impossible) -> keep the rows
whose CaseNumber matches the lead, and take the one whose DocTypeDescription is a Final Judgment.

🔑 THE CATCH THAT MAKES IT A TWO-HOP SEARCH: the judgment is indexed under whoever it was entered
AGAINST, which is frequently a co-defendant we do not have. Proof: for CACE-24-006635, searching the
owner we DO hold (JOSEPH, MILOUSE) returns 2 documents on the case and NO judgment, while the
co-defendant (JULSAINT, JULEUS — a name absent from our lead data entirely) returns the Final Judgment,
instrument 120947809. So _find_fj harvests the other party names off the case's own documents and
searches those too. Institutions are skipped: a bank name returns thousands of unrelated rows.

Measured first pass: 2 of 4 cases resolved. CONO (county-court/HOA) cases tend to return nothing —
smaller judgments are often never recorded as a separate instrument.

  ROUTE 1 — headless browser on AcclaimWeb: BLOCKED. The disclaimer page loads and the accept click
  works, then the search page returns "Sorry, you have been blocked" (Cloudflare WAF). Confirms the
  broward_liens.py note: python-requests' TLS fingerprint, headless Chromium and curl_cffi are all
  rejected; only the NATIVE Windows curl (Schannel) passes.

  ROUTE 2 — native-curl session + CASE-NUMBER search: session opens, grid comes back EMPTY every time,
  even with the full DocTypes code list posted. The live form explains it — it carries a **`captcha`**
  input. Broward's case-number search is reCAPTCHA-gated even though the name search is not.

PALM BEACH (`--pb`): 🔴 DEAD END — THE DATA IS NOT IN THE RECORDER. Do not spend more captcha solves.
The plumbing all works: session opens, 2Captcha solves the v2 widget (~40-75s), name search returns
40-256 documents per owner, and the parcel guard correctly narrows to 8-24 documents that genuinely sit
on our parcel. There is simply **no judgment among them**. The doc types actually present on our PB
parcels are DEED / LIEN / NOTICE / RELEASE / TAX LIEN / FINANCING STATEMENTS (UCC'S) / NOTICE OF
COMMENCEMENT / TERMINATION. Broward records a foreclosure judgment as its own instrument ("FJ - Final
Judgment"); Palm Beach does not appear to record them at all. PB judgment dates therefore have to come
from the COURT (mypalmbeachclerk case search), not the recorder — a different system with its own gate.
Two lesser traps found along the way, both already handled here: get_search_results returns the
DataTables ENVELOPE (unwrap `.data`, or you get a silent zero), and PB's CaseNumber column is
regex-derived from the legal text and returns noise ('CASE', 'L', 'U') so it cannot be a join key.

🔴 RECORDED DATE IS NOT THE ENTRY DATE, AND THE ERROR RUNS THE DANGEROUS WAY.
A judgment is signed first and recorded days-to-weeks later, so recorded >= entered. Accruing from the
recorded date therefore computes LESS interest than really accrued -> a payoff that is too LOW -> equity
that looks too HIGH. That is the Milouse direction of error. So every date this produces is stamped
src='recorded' and must be treated as a FLOOR on the payoff, never as the exact figure. For any lead
that reaches a live conversation, pull the actual judgment and read the entry date off page one.

Run:  python broward_judgment_dates.py --limit 5 --dry-run   # look, write nothing
      python broward_judgment_dates.py --limit 40            # merge into judgment_dates.json
"""
import argparse
import datetime
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'judgment_dates.json')
ACCLAIM = 'https://officialrecords.broward.org/AcclaimWeb'

# Recorded doc types that ARE a money judgment on the foreclosure. 'FJ' is Broward's code for Final
# Judgment; the spelled forms cover the description column. Deliberately excludes Lis Pendens, Notice
# of Sale, Certificate of Title and Satisfaction — none of which start interest running.
FJ_RX = re.compile(r'\bFJ\b|FINAL\s+JUDG|SUMMARY\s+FINAL\s+JUDG|JUDGMENT\s+OF\s+FORECLOSURE', re.I)


def _jsdate(s):
    """Telerik returns /Date(1750000000000)/ or an m/d/Y string; normalise both to ISO."""
    m = re.search(r'/Date\((-?\d+)', str(s or ''))
    if m:
        return datetime.datetime.utcfromtimestamp(int(m.group(1)) / 1000).date().isoformat()
    m = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', str(s or ''))
    if m:
        return datetime.datetime.strptime(m.group(1), '%m/%d/%Y').date().isoformat()
    return None


def _load(p, d):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return d


MISSES = os.path.join(HERE, 'judgment_dates_misses.json')
MISS_TTL_DAYS = 45      # a judgment can be recorded later, so a miss expires rather than sticking


def _fresh_miss(entry):
    try:
        d = datetime.datetime.strptime(str(entry), '%Y-%m-%d').date()
    except Exception:
        return False
    return (datetime.date.today() - d).days < MISS_TTL_DAYS


def needed(limit=0):
    """Broward/PB leads with a judgment amount but no known entry date, soonest auction first.

    Skips cases we already searched and found nothing for. WITHOUT this the tool never converges:
    a miss is not written to judgment_dates.json, so the same ~75 unrecorded cases (mostly CONO
    county-court matters, where small judgments are often never recorded as a separate instrument)
    get re-queried on every single run forever. Misses expire after MISS_TTL_DAYS because a judgment
    can be recorded later than we looked.
    """
    have = _load(OUT, {})
    misses = _load(MISSES, {})
    rows = []
    for fn in ('broward_leads.json', 'palmbeach_leads.json'):
        for r in _load(os.path.join(HERE, fn), []):
            if not isinstance(r, dict):
                continue
            c, j = r.get('case'), r.get('judg') or 0
            if not c or not j or c in have:
                continue
            if _fresh_miss(misses.get(str(c))):
                continue                     # searched recently, nothing recorded — don't re-ask
            # BROWARD ONLY. Palm Beach cases (502025CA...XXXAMB) are not in Broward's recorder — sending
            # them there returns nothing and burns a page load each. PB needs its own recorder and is
            # deliberately out of scope for this module rather than silently failing inside it.
            if not re.match(r'^(CACE|CONO|COCE|COWE|COSO)', str(c).upper()):
                continue
            d = r.get('days')
            # Seed the search with every name form we hold. 'rname' is already recorder-shaped
            # ("LAST, FIRST"); oname/owners are the raw roll strings. The co-defendant who actually
            # holds the judgment usually is NOT here — _find_fj discovers them from the case's docs.
            names = []
            for k in ('rname', 'oname', 'owners'):
                v = str(r.get(k) or '').strip()
                if v and v not in names:
                    names.append(v)
            rows.append({'case': str(c), 'judg': j, 'names': names,
                         'days': d if isinstance(d, (int, float)) else 9999})
    rows.sort(key=lambda x: (x['days'], -x['judg']))
    return rows[:limit] if limit else rows


def scrape(cases, headless=True, verbose=True):
    """-> {case: {d, label, instrument, src}} from Broward Official Records, via the ONE transport
    Cloudflare lets through.

    A headless browser is BLOCKED here — measured: the disclaimer page loads and accepts, then the
    search page returns "Sorry, you have been blocked". broward_liens.py already solved this: Cloudflare
    rejects python-requests' TLS fingerprint, headless Chromium AND curl_cffi, and only the NATIVE
    Windows curl (Schannel) passes. So this reuses that module's curl session wholesale rather than
    re-deriving it — same jar, same UA, same disclaimer handshake.
    """
    import broward_liens as BL
    got = {}
    sess = None
    for _try in range(4):                     # Cloudflare throttles rapid repeat sessions; back off
        sess = BL.start_session()
        if sess:
            break
        if verbose:
            print('  session blocked — retry %d in 20s' % (_try + 1))
        time.sleep(20)
    if not sess:
        print('  !! could not open an Official Records session (blocked) — nothing scraped')
        return got
    for i, item in enumerate(cases, 1):
        c = item['case'] if isinstance(item, dict) else item
        seeds = item.get('names') if isinstance(item, dict) else []
        try:
            hit, ndocs, tried = _find_fj(BL, sess, c, seeds)
            if hit:
                iso = _jsdate(hit.get('RecordDate'))
                if iso:
                    got[c] = {'d': iso,
                              'label': (hit.get('DocTypeDescription') or 'Final Judgment') + ' (recorded)',
                              'instrument': str(hit.get('InstrumentNumber') or ''), 'rate': None,
                              'src': 'broward-official-records',
                              'checked': datetime.date.today().isoformat()}
                    if verbose:
                        print('  %3d/%d  %-22s -> %s  inst %s'
                              % (i, len(cases), c, iso, got[c]['instrument'] or '?'))
                    continue
            if verbose:
                print('  %3d/%d  %-22s -- no FJ (%d doc(s) on case, searched %d name(s))'
                      % (i, len(cases), c, ndocs, tried))
        except Exception as e:
            if verbose:
                print('  %3d/%d  %-22s !! %s' % (i, len(cases), c, str(e)[:60]))
    return got


def scrape_pb(items, verbose=True):
    """Palm Beach judgment dates via the clerk's Landmark Web (erec.mypalmbeachclerk.com).

    🔴 USE NAME SEARCH, NOT PARCEL SEARCH. Parcel search looked like the shortcut (PB supports it and
    PB leads carry the PCN) and it is a dead end for this purpose: measured live, a PCN search returns
    15-25 documents per parcel and **zero judgment-type documents** — parcels index DEEDS and MORTGAGES.
    A money judgment is indexed against the PERSON it was entered against, which is exactly how the
    Broward side works (the Milouse judgment was found under co-defendant JULSAINT by name, and never
    by parcel). Also note the grid's CaseNumber on PB is regex-derived from the legal description and
    comes back as noise ('CASE', 'L', 'U'), so it cannot be used as a join key — match on party name
    and doc type instead.

    THE GATE: every Landmark search endpoint is reCAPTCHA v2 (there is no programmatic mint), so each
    session needs a solved token. palmbeach_liens already owns that plumbing — sitekey, page URL,
    2Captcha call, curl transport — so this reuses it rather than re-deriving anything. Cost is about
    $0.003 per solved token; one token covers a session, not a case.

    🔴 STATUS 2026-08-15: WIRED BUT RETURNING NOTHING — do not trust a 0 result from this yet.
    First live test: session opened, 2Captcha solved the v2 widget cleanly (2,361-char token in 51s),
    the ParcelIdSearch POST was accepted (no 'Invalid Captcha'), and get_search_results came back with
    **0 documents on the parcel for all 3 cases** — which is not credible for real Palm Beach parcels.
    So the failure is AFTER the gate, not at it. PCN format is NOT the suspect: palmbeach_liens feeds
    the same undashed digits (`re.sub(r'\\D','',folio)`, len>=14) and works. Most likely the results
    handshake — Landmark returns an empty DataTables shell and the real grid comes from a SECOND POST
    (/Search/GetSearchResults); get_search_results() may need the paging/draw args that
    palmbeach_liens passes in its own flow, or the shell must be consumed before the grid call.
    NEXT: run palmbeach_liens.py on one of these same parcels, confirm it returns rows, and diff the
    exact call sequence against this one. Do not spend more captcha solves guessing.
    """
    import palmbeach_liens as PB
    got = {}
    if not PB.start_session():
        print('  !! Palm Beach: no session'); return got
    token = ''
    if PB.show_captcha():
        token = PB.solve_token_2captcha() or ''
        if not token:
            print('  !! Palm Beach: captcha-gated and 2Captcha returned no token — nothing scraped')
            return got
    for i, item in enumerate(items, 1):
        c = item['case']
        pcn = re.sub(r'\D', '', str(item.get('pcn') or ''))
        if not pcn:
            if verbose:
                print('  %3d/%d  %-24s -- no PCN on the lead' % (i, len(items), c))
            continue
        try:
            docs = []
            for nm in (item.get('names') or [])[:2]:
                q = PB._name_query(nm) if hasattr(PB, '_name_query') else nm
                if not q:
                    continue
                if PB.search(PB.name_payload(q), token) is None:
                    token = PB.solve_token_2captcha() or ''       # Landmark tokens are one-shot
                    if not token or PB.search(PB.name_payload(q), token) is None:
                        if verbose:
                            print('  %3d/%d  %-24s !! captcha rejected' % (i, len(items), c))
                        break
                # get_search_results returns the DataTables ENVELOPE ({recordsTotal, data:[...]}), while
                # gsr_rows_to_docs expects the inner row list. Passing the envelope yields zero documents
                # with no error — which reads exactly like "this person has no records". Unwrap .data.
                res = PB.get_search_results(length=200) or {}
                rows = res.get('data') if isinstance(res, dict) else (res or [])
                docs.extend(PB.gsr_rows_to_docs(rows or []) or [])
                token = PB.solve_token_2captcha() or '' if PB.show_captcha() else token
                time.sleep(1.0)
            # PB's CaseNumber column is regex-derived from the legal text and comes back as noise
            # ('CASE', 'L', 'U'), so it CANNOT confirm the match. Confirm on the PROPERTY instead: the
            # judgment must sit on our parcel. Without that guard a common name would staple a
            # stranger's old judgment onto this lead — the wrong-Garcia failure in a new costume.
            def _on_parcel(d):
                if pcn and re.sub(r'\D', '', str(d.get('ParcelNumber') or '')) == pcn:
                    return True
                return bool(pcn) and pcn in re.sub(r'\D', '', str(d.get('DocLegalDescription') or ''))
            mine = [d for d in docs if _on_parcel(d)]
            fj = next((d for d in mine
                       if FJ_RX.search(str(d.get('DocTypeDescription') or ''))), None)
            if fj:
                iso = _jsdate(fj.get('RecordDate'))
                if iso:
                    got[c] = {'d': iso,
                              'label': (fj.get('DocTypeDescription') or 'Final Judgment') + ' (recorded)',
                              'instrument': str(fj.get('InstrumentNumber') or ''), 'rate': None,
                              'src': 'palmbeach-official-records',
                              'checked': datetime.date.today().isoformat()}
                    if verbose:
                        print('  %3d/%d  %-24s -> %s  inst %s'
                              % (i, len(items), c, iso, got[c]['instrument'] or '?'))
                    continue
            if verbose:
                print('  %3d/%d  %-24s -- no FJ (%d doc(s) on parcel, %d on case)'
                      % (i, len(items), c, len(docs), len(mine)))
                if mine:
                    # Case numbers ARE coming back but none equal ours -> a FORMAT mismatch, not an
                    # absent judgment. Show what the recorder actually calls this case so the key can
                    # be normalised instead of guessed at across repeated captcha-paid runs.
                    types = sorted({str(d.get('DocTypeDescription') or '').strip()
                                    for d in mine if d.get('DocTypeDescription')})
                    print('        doc types ON OUR PARCEL: %r' % (types[:14],))
            time.sleep(1.5)
        except Exception as e:
            if verbose:
                print('  %3d/%d  %-24s !! %s' % (i, len(items), c, str(e)[:60]))
    return got


def _person_names(docs, case_norm):
    """Party names appearing on THIS case's documents — used to discover co-defendants we do not
    have. The judgment is indexed under whoever it was entered against, which is often the co-owner:
    Milouse Joseph's own name returns 2 docs on her case and NO judgment, while the co-defendant
    (Juleus Julsaint — a name absent from our lead data entirely) returns the Final Judgment."""
    out = []
    for d in docs:
        if re.sub(r'[^A-Z0-9]', '', str(d.get('CaseNumber') or '').upper()) != case_norm:
            continue
        for k in ('Name', 'CrossPartyName'):
            n = str(d.get(k) or '').strip()
            # skip institutions — a judgment indexed under the bank is the same doc, and bank names
            # return thousands of unrelated results.
            if n and not re.search(r'BANK|MORTGAGE|LLC|INC\b|CORP|ASSN|ASSOC|TRUST|COMPANY|N\.?A\.?$|'
                                   r'SERVICING|FUND|CLERK|COUNTY|STATE OF', n, re.I):
                if n not in out:
                    out.append(n)
    return out[:4]


def _find_fj(BL, sess, case, seed_names):
    """Search each known name, tie documents back by the grid's CaseNumber field, and if no judgment
    turns up, harvest the OTHER party names off this case's documents and search those too.
    -> (fj_doc|None, docs_on_case, names_tried)"""
    cn = re.sub(r'[^A-Z0-9]', '', str(case).upper())
    tried, on_case, queue = set(), [], [n for n in (seed_names or []) if n]
    while queue and len(tried) < 5:
        name = queue.pop(0)
        key = name.upper().strip()
        if key in tried:
            continue
        tried.add(key)
        docs = BL.search_docs(sess, name) or []
        mine = [d for d in docs
                if re.sub(r'[^A-Z0-9]', '', str(d.get('CaseNumber') or '').upper()) == cn]
        on_case.extend(mine)
        fj = next((d for d in mine if FJ_RX.search(str(d.get('DocTypeDescription') or ''))), None)
        if fj:
            return fj, len(on_case), len(tried)
        for extra in _person_names(mine, cn):     # second hop: co-defendants we never had
            if extra.upper().strip() not in tried:
                queue.append(extra)
        time.sleep(2)                              # be polite to the recorder
    return None, len(on_case), len(tried)


def _scrape_browser_UNUSED(cases, headless=True, verbose=True):
    """Kept only to document what does NOT work: headless Chromium is Cloudflare-blocked here."""
    from playwright.sync_api import sync_playwright
    got = {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        pg = b.new_page()
        def _accept():
            """The disclaimer gates the whole app; without it every search page renders without its
            form and #CaseNumber never appears (which looked like a bad selector, not a lost session)."""
            pg.goto(ACCLAIM, wait_until='domcontentloaded')
            for sel in ('text=I accept the conditions above.',
                        'input[value*="accept" i]', 'button:has-text("accept")'):
                try:
                    pg.click(sel, timeout=6000)
                    pg.wait_for_timeout(800)
                    return True
                except Exception:
                    continue
            return False

        _accept()
        for i, c in enumerate(cases, 1):
            try:
                pg.goto(ACCLAIM + '/search/SearchTypeCaseNumber', wait_until='domcontentloaded')
                try:
                    pg.wait_for_selector('#CaseNumber', timeout=8000)
                except Exception:
                    _accept()                    # session dropped — re-accept and retry once
                    pg.goto(ACCLAIM + '/search/SearchTypeCaseNumber', wait_until='domcontentloaded')
                    pg.wait_for_selector('#CaseNumber', timeout=10000)
                pg.fill('#CaseNumber', re.sub(r'[^A-Z0-9]', '', str(c).upper()))
                pg.click('button:has-text("Search")')
                pg.wait_for_timeout(2500)
                rows = pg.eval_on_selector_all(
                    'tr', 'els => els.map(e => e.innerText.replace(/\\s+/g," ").trim())')
                hit = None
                for t in rows:
                    if FJ_RX.search(t) and re.search(r'\d{1,2}/\d{1,2}/\d{4}', t):
                        hit = t
                        break
                if hit:
                    d = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', hit).group(1)
                    inst = re.search(r'\b(1\d{8})\b', hit)
                    iso = datetime.datetime.strptime(d, '%m/%d/%Y').date().isoformat()
                    got[c] = {'d': iso, 'label': 'Final Judgment (recorded)',
                              'instrument': inst.group(1) if inst else '', 'rate': None,
                              'src': 'broward-official-records',
                              'checked': datetime.date.today().isoformat()}
                    if verbose:
                        print('  %3d/%d  %-24s -> %s  inst %s'
                              % (i, len(cases), c, iso, got[c]['instrument'] or '?'))
                elif verbose:
                    print('  %3d/%d  %-24s -- no recorded final judgment found' % (i, len(cases), c))
            except Exception as e:
                if verbose:
                    print('  %3d/%d  %-24s !! %s' % (i, len(cases), c, str(e)[:60]))
        b.close()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=25)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--pb', action='store_true',
                    help='Palm Beach instead of Broward (needs a 2Captcha token, ~$0.003/session)')
    a = ap.parse_args()

    if a.pb:
        have = _load(OUT, {})
        pbrows = []
        for r in _load(os.path.join(HERE, 'palmbeach_leads.json'), []):
            if not isinstance(r, dict):
                continue
            c, j = r.get('case'), r.get('judg') or 0
            if not c or not j or c in have:
                continue
            d = r.get('days')
            nm = []
            for k in ('rname', 'oname', 'owners'):
                v = str(r.get(k) or '').strip()
                if v and v not in nm:
                    nm.append(v)
            pbrows.append({'case': str(c), 'judg': j, 'pcn': r.get('folio') or r.get('pcn') or '',
                           'names': nm,
                           'days': d if isinstance(d, (int, float)) else 9999})
        pbrows.sort(key=lambda x: (x['days'], -x['judg']))
        todo = pbrows[:a.limit] if a.limit else pbrows
        if not todo:
            print('Palm Beach: every judgment already dated.')
            return 0
        print('PALM BEACH: %d undated ($%s). Doing %d this run.'
              % (len(pbrows), format(int(sum(r['judg'] for r in pbrows)), ',d'), len(todo)))
        got = scrape_pb(todo)
        print('\nfound %d of %d' % (len(got), len(todo)))
        if a.dry_run:
            print('dry run — judgment_dates.json untouched.')
            return 0
        if got:
            cur = _load(OUT, {})
            cur.update(got)
            tmp = OUT + '.tmp'
            json.dump(cur, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
            os.replace(tmp, OUT)
            print('judgment_dates.json %d -> %d entries' % (len(cur) - len(got), len(cur)))
        return 0

    todo = needed(a.limit)
    if not todo:
        print('nothing to do — every Broward/PB judgment already has a date.')
        return 0
    allneed = needed(0)
    print('%d Broward/PB judgment(s) still undated ($%s total). Doing %d this run.'
          % (len(allneed), format(int(sum(r['judg'] for r in allneed)), ',d'), len(todo)))
    got = scrape(todo, headless=not a.headed)
    print('\nfound %d of %d' % (len(got), len(todo)))
    if a.dry_run:
        print('dry run — judgment_dates.json untouched.')
        return 0
    # Record the ones we searched and did not find, so the next run does not re-ask the same
    # unrecorded cases. Written even when nothing was found — that IS the useful information.
    miss = _load(MISSES, {})
    today_iso = datetime.date.today().isoformat()
    for r in todo:
        if r['case'] not in got:
            miss[r['case']] = today_iso
    try:
        tmpm = MISSES + '.tmp'
        json.dump(miss, open(tmpm, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmpm, MISSES)
        print('recorded %d miss(es) — they will be skipped for %d days'
              % (len(todo) - len(got), MISS_TTL_DAYS))
    except Exception:
        pass
    if got:
        cur = _load(OUT, {})
        cur.update(got)
        tmp = OUT + '.tmp'
        json.dump(cur, open(tmp, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        os.replace(tmp, OUT)
        print('judgment_dates.json %d -> %d entries' % (len(cur) - len(got), len(cur)))
        print('\nNOTE: these are RECORDED dates, not entry dates. Interest accrued from them is a')
        print('FLOOR — the real payoff is somewhat higher and the real equity somewhat lower.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
