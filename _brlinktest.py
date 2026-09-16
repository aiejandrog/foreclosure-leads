#!/usr/bin/env python
"""_brlinktest — Broward release pairing by the recorder's own links (2026-09-16).

Run:  python _brlinktest.py    (exit 0 = safe; no network)

WHAT BROKE. broward_liens.analyze() marked a mortgage SATISFIED when a later release named the "same
institution". Broward indexes most releases "To MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC", so every
MERS release matched every MERS mortgage and the rules paired them oldest-first. On a live five-mortgage
lis pendens chain that left a $152,000 loan OPEN although its release (Doc Extension on its own details
page) was recorded 2025-04-29, and marked a $319,750 loan SATISFIED although it was assigned in 2026.

FIXTURE. The real chain's STRUCTURE as read live on 2026-09-16 — record dates, document types, amounts,
lender names and every DocLink / Doc Extension — with the owner, private parties, instrument numbers and
book/pages replaced (the repo is public). 800000023 is an un-indexed child whose page was not read live;
its type here is an assumption.
"""
import datetime
import sys

import broward_liens as BL

fails = []


def chk(name, cond):
    if not cond:
        fails.append(name)


def _ms(date):
    t = datetime.datetime.strptime(date, '%Y-%m-%d') - datetime.datetime(1970, 1, 1)
    return '/Date(%d)/' % int(t.total_seconds() * 1000)


def row(date, dtype, party, cross, inst, amt, bp='', case='', name='DOE,JANE'):
    return {'RecordDate': _ms(date), 'DocTypeDescription': dtype, 'Party': party, 'CrossPartyName': cross,
            'InstrumentNumber': inst, 'Consideration': float(amt), 'BookPage': bp, 'CaseNumber': case,
            'Name': name}


MERS = 'MORTGAGE ELECTRONIC REGISTRATION SYSTEMS INC'
MTG, REL = 'Mortgage/ Modifications & Assumptions', 'Release/Revoke/Satisfy or Terminate'
CHAIN = [
    ('2014-05-02', 'Deed Transfers of Real Property', 'To', 'SELLER,ONE', '800000001', 95000, '40001/101', ''),
    ('2014-05-02', MTG, 'From', MERS, '800000002', 85500, '40002/102', ''),
    ('2014-06-03', 'Affidavit', 'From', '', '800000003', 0, '40003/103', ''),
    ('2014-11-25', 'Lien', 'To', 'SAMPLE CITY', '800000004', 0, '40004/104', ''),
    ('2015-05-22', 'Lien', 'To', 'SAMPLE CITY', '800000005', 0, '', ''),
    ('2018-10-30', 'Deed Transfers of Real Property', 'To', 'SELLER,TWO', '800000006', 150000, '', ''),
    ('2018-10-30', MTG, 'From', MERS, '800000007', 147283, '', ''),
    ('2021-04-26', 'Deed Transfers of Real Property', 'To', 'SELLER THREE L P', '800000008', 342500, '', ''),
    ('2021-04-26', MTG, 'From', MERS, '800000009', 319750, '', ''),
    ('2021-06-14', MTG, 'From', MERS, '800000010', 152000, '', ''),
    ('2021-06-18', REL, 'To', MERS, '800000011', 0, '', ''),
    ('2022-10-17', REL, 'To', MERS, '800000012', 0, '', ''),
    ('2024-02-23', REL, 'From', 'SAMPLE CITY', '800000013', 0, '', ''),
    ('2024-03-22', 'Notice', 'To', 'FLORIDA GREEN FINANCE AUTHORITY', '800000014', 0, '', ''),
    ('2025-04-18', 'Notice of Commencement', 'From', 'ACME ROOFING INC', '800000015', 0, '', ''),
    ('2025-04-22', MTG, 'From', 'HOMEXPRESS MORTGAGE CORP', '800000016', 217000, '', ''),
    ('2025-04-29', REL, 'To', MERS, '800000017', 0, '', ''),
    ('2025-05-12', REL, 'To', 'SAMPLE CITY', '800000018', 0, '', ''),
    ('2025-05-16', REL, 'To', 'FLORIDA PACE FUNDING AGENCY', '800000019', 0, '', ''),
    ('2026-03-03', 'Assignment', 'From', 'LOAN TRUST 2A', '800000020', 0, '', ''),
    ('2026-04-06', 'Assignment', 'From', 'NOTE BUYER LLC', '800000021', 0, '', ''),
    ('2026-06-18', 'Lis Pendens', 'To', 'ACME ROOFING INC', '800000022', 0, '', 'CONO-26-000002'),
    ('2026-08-14', 'Lis Pendens', 'To', 'U S BANK TRUST NATIONAL ASSN', '800000024', 0, '', 'CACE-26-000001'),
]
PAGES = {   # instrument -> (doc type, DocLink anchors, Doc Extension anchors)
    '800000002': ('M - Mortgage/ Modifications & Assumptions', [], ['800000012']),
    '800000007': ('M - Mortgage/ Modifications & Assumptions', [], ['800000011']),
    '800000009': ('M - Mortgage/ Modifications & Assumptions', [], ['800000021']),
    '800000010': ('M - Mortgage/ Modifications & Assumptions', [], ['800000017']),
    '800000011': ('RST - Release/Revoke/Satisfy or Terminate', ['800000007 [M]'], []),
    '800000012': ('RST - Release/Revoke/Satisfy or Terminate', ['O 800000002'], []),
    '800000013': ('RST - Release/Revoke/Satisfy or Terminate', ['800000005'], []),
    '800000016': ('M - Mortgage/ Modifications & Assumptions', [], ['800000023', '800000024']),
    '800000017': ('RST - Release/Revoke/Satisfy or Terminate', ['800000010 [M]'], []),
    '800000018': ('RST - Release/Revoke/Satisfy or Terminate', ['800000004'], []),
    '800000019': ('RST - Release/Revoke/Satisfy or Terminate', ['800000014 [NOT]'], []),
    '800000020': ('AST - Assignment', [], []),
    '800000021': ('AST - Assignment', ['800000009 [M]'], []),
    '800000022': ('LP - Lis Pendens', [], []),
    '800000023': ('AST - Assignment', ['800000016 [M]'], []),      # assumed (see docstring)
    '800000024': ('LP - Lis Pendens', ['800000016 [M]'], []),
}
OWNER, CASE = 'DOE,JANE', 'CACE-26-000001'


def page(inst):
    t, p, c = PAGES[inst]
    return {'i': inst, 't': t, 'p': [BL._anchor_ref(a) for a in p], 'c': [BL._anchor_ref(a) for a in c]}


def docs():
    return [row(*r) for r in CHAIN]


def by_date(res):
    return {(l['d'], l['amt']): l for l in res['liens']}


# --- 1. anchors: every link shape seen live -------------------------------------------------------------
chk('anchor: "115415213 [M]"', BL._anchor_ref('115415213 [M]') == {'k': ['I:115415213'], 't': 'M'})
chk('anchor: "O 112261923"', BL._anchor_ref('O 112261923') == {'k': ['I:112261923'], 't': ''})
chk('anchor: "O 32505/1578 101552188" carries both keys',
    BL._anchor_ref('O 32505/1578 101552188')['k'] == ['BP:32505/1578', 'I:101552188'])
chk('anchor: "O 20924/869" is a book/page', BL._anchor_ref('O 20924/869')['k'] == ['BP:20924/869'])
chk('anchor: "O 0/0 113151309" drops the empty book/page', BL._anchor_ref('O 0/0 113151309')['k'] == ['I:113151309'])
chk('anchor: bare book/page "42049/905"', BL._anchor_ref('42049/905')['k'] == ['BP:42049/905'])
chk('anchor: nothing linkable -> None', BL._anchor_ref('RS Code') is None)

# --- 2. the details-page parser: real markup shapes, and a challenge page is UNKNOWN --------------------
_ROW = ('<div class="docDetailRow">\n    <div class="detailLabel">\n        %s\n    </div>\n'
        '    <div class="%s" style="width: 200px; margin-left:25px;">\n       %s\n'
        '       <div class="expandDown" style="display:none;"><img src="/x.png" /></div>\n    </div>\n'
        '    <hr style="border: none; clear: both;" />\n</div>\n')
DETAIL = ('<div class="detailLabel">Instrument Number:</div><input id="InstrumentNumber" />'   # search form
          + '<h2>Search Results</h2>'
          + _ROW % ('Record Date:', 'formInput', '10/17/2022 3:48:05 PM')
          + _ROW.replace('<div class="docDetailRow">',
                         '<div data-bind="visible: negativeInstrumentNumber(\'800000012\') == false" class="docDetailRow">')
          % ('Instrument Number:&nbsp;&nbsp;', 'formInput', '800000012')
          + _ROW.replace('class="docDetailRow"', 'class="displaynone docDetailRow"') % ('Map #:&nbsp;&nbsp;', 'formInput', '')
          + _ROW % ('Doc Type:', 'listDocDetails', 'RST - Release/Revoke/Satisfy or Terminate')
          + _ROW % ('DocLink:', 'listDocDetails',
                    '<a onclick="JumpToTransactionItemId(\'30571998\')">O 800000002</a><br />'))
p = BL.parse_details(DETAIL)
chk('parser: instrument read from the details row, not the search form', p and p['i'] == '800000012')
chk('parser: doc type', p and p['t'].startswith('RST - Release'))
chk('parser: DocLink parent', p and p['p'] == [{'k': ['I:800000002'], 't': ''}])
chk('parser: no Doc Extension row -> no children', p and p['c'] == [])
EXT = (_ROW % ('Instrument Number:', 'formInput', '800000016') + _ROW % ('Doc Type:', 'listDocDetails', 'M - Mortgage')
       + _ROW % ('Doc Extension:', 'listDocDetails',
                 '<a onclick="J(\'1\')">800000023</a><br /><a onclick="J(\'2\')">800000024</a><br />'
                 '<a onclick="J(\'2\')">800000024</a><br />'))
p = BL.parse_details(EXT)
chk('parser: Doc Extension children, duplicates collapsed',
    p and [r['k'] for r in p['c']] == [['I:800000023'], ['I:800000024']])
CHALLENGE = '<html><body><p>Enable JavaScript and cookies to continue</p></body></html>'
chk('parser: a Cloudflare interstitial is not a details page', BL.parse_details(CHALLENGE) is None)

_curl, _sleep = BL._curl, BL.time.sleep
try:
    BL.time.sleep = lambda s: None
    seq = [CHALLENGE, EXT, DETAIL]                          # interstitial, WRONG instrument, the right page
    BL._curl = lambda url, post=None, timeout=45: seq.pop(0)
    BL._link_fails[0] = 0
    got = BL.fetch_details('800000012')
    chk('fetch: rides through an interstitial and a wrong-instrument page', got and got['i'] == '800000012')
    BL._curl = lambda url, post=None, timeout=45: CHALLENGE
    chk('fetch: all interstitials -> None (unknown), never "no links"', BL.fetch_details('800000012') is None)
    chk('fetch: a failure counts toward the breaker', BL._link_fails[0] == 1)
finally:
    BL._curl, BL.time.sleep = _curl, _sleep
    BL._link_fails[0] = 0

# --- 3. the bug, reproduced: the lender-name rules alone ----------------------------------------------
old = by_date(BL.analyze(docs(), OWNER, 0, ftype='MORTGAGE', lead_case=CASE, _legacy=True))
chk('legacy reproduces the bug: paid-off $152,000 read OPEN', old[('2021-06-14', 152000)]['st'] == 'OPEN')
chk('legacy reproduces the bug: live $319,750 read SATISFIED', old[('2021-04-26', 319750)]['st'] == 'SATISFIED')
chk('legacy output carries no new keys', all(set(l) == {'d', 'amt', 'party', 'bp', 'st'} for l in old.values()))

# --- 4. with the recorder's links ----------------------------------------------------------------------
LINKS = {k: page(k) for k in PAGES}
res = BL.analyze(docs(), OWNER, 0, ftype='MORTGAGE', lead_case=CASE, links=LINKS)
new = by_date(res)
chk('links: 2014 $85,500 SATISFIED by its linked release',
    new[('2014-05-02', 85500)]['st'] == 'SATISFIED' and new[('2014-05-02', 85500)].get('rel') == '800000012')
chk('links: 2018 $147,283 SATISFIED by 800000011', new[('2018-10-30', 147283)].get('rel') == '800000011')
chk('links: 2021 $319,750 OPEN (its only child is an assignment)',
    new[('2021-04-26', 319750)]['st'] == 'OPEN' and new[('2021-04-26', 319750)]['how'] == 'read')
chk('links: 2021 $152,000 SATISFIED by 800000017',
    new[('2021-06-14', 152000)]['st'] == 'SATISFIED' and new[('2021-06-14', 152000)].get('rel') == '800000017')
chk('links: 2025 $217,000 OPEN — the loan this lis pendens names',
    new[('2025-04-22', 217000)]['st'] == 'OPEN' and new[('2025-04-22', 217000)]['how'] == 'link')
chk('links: every row carries its instrument', all(l.get('inst') for l in res['liens']))
chk('links: open_count', res['open_count'] == 2)
chk('links: the foreclosing loan is the one the lis pendens names, not the largest open',
    res['first_est'] == 217000 and res['junior'] == 319750)
chk('links: MERS open + a second open still reads low (the other loan may sit on another parcel)',
    res['conf'] == 'low')
no_lp = dict(LINKS)
no_lp.pop('800000024')
chk('without the lis pendens page the anchor falls back to the largest open',
    BL.analyze(docs(), OWNER, 0, ftype='MORTGAGE', lead_case=CASE, links=no_lp)['first_est'] == 319750)

# --- 5. a linked release is never re-paired by the fallback ---------------------------------------------
two = [row('2010-01-05', MTG, 'From', MERS, '810000001', 200000),
       row('2015-03-02', MTG, 'From', MERS, '810000002', 180000),
       row('2016-01-10', REL, 'To', MERS, '810000003', 0)]
L2 = {'810000001': {'i': '810000001', 't': 'M - Mortgage', 'p': [], 'c': []},
      '810000002': {'i': '810000002', 't': 'M - Mortgage', 'p': [], 'c': []},
      '810000003': {'i': '810000003', 't': 'RST - Release', 'p': [BL._anchor_ref('810000002 [M]')], 'c': []}}
r2 = by_date(BL.analyze([dict(x) for x in two], OWNER, 0, links=L2))
chk('claimed: the release satisfies the loan it names', r2[('2015-03-02', 180000)]['st'] == 'SATISFIED')
chk('claimed: and cannot also kill the older loan', r2[('2010-01-05', 200000)]['st'] == 'OPEN')
r2old = by_date(BL.analyze([dict(x) for x in two], OWNER, 0, _legacy=True))
chk('claimed (control): legacy pairs it to the OLDER loan', r2old[('2010-01-05', 200000)]['st'] == 'SATISFIED'
    and r2old[('2015-03-02', 180000)]['st'] == 'OPEN')

# --- 6. the fallback still works where the recorder did not link ---------------------------------------
priv = [row('1998-09-03', MTG, 'From', 'LENDER,PRIVATE', '820000001', 20150, '28812/1278'),
        row('1999-08-27', REL, 'To', 'LENDER,PRIVATE', '820000002', 0, '29792/1611')]
L3 = {'820000001': {'i': '820000001', 't': 'M - Mortgage', 'p': [], 'c': []},
      '820000002': {'i': '820000002', 't': 'RST - Release', 'p': [], 'c': []}}
r3 = BL.analyze([dict(x) for x in priv], OWNER, 0, links=L3)
chk('unlinked pair: the lender-name rule still satisfies it',
    r3['liens'][0]['st'] == 'SATISFIED' and r3['liens'][0]['how'] == 'heur')
chk('unlinked pair: conf no higher than legacy',
    BL._CONF_RANK[r3['conf']] <= BL._CONF_RANK[BL.analyze([dict(x) for x in priv], OWNER, 0, _legacy=True)['conf']])

typo = [row('2005-10-04', MTG, 'From', 'WELLS FARGO BANK', '830000001', 150000, '40644/1728'),
        row('2006-06-15', REL, 'To', 'WELLS FARGO BANK', '830000002', 0, '42228/698')]
L4 = {'830000001': {'i': '830000001', 't': 'M - Mortgage', 'p': [], 'c': []},
      '830000002': {'i': '830000002', 't': 'RST - Release', 'p': [BL._anchor_ref('O 41644/1728')], 'c': []}}
chk('mistyped book/page link stays in the fallback pool (same-lender release still pairs)',
    BL.analyze([dict(x) for x in typo], OWNER, 0, links=L4)['liens'][0]['st'] == 'SATISFIED')

outside = [row('2021-01-11', MTG, 'From', MERS, '840000001', 352000),
           row('2021-06-01', REL, 'To', MERS, '840000002', 0)]
L5 = {'840000001': {'i': '840000001', 't': 'M - Mortgage', 'p': [], 'c': []},
      '840000002': {'i': '840000002', 't': 'RST - Release', 'p': [BL._anchor_ref('806037091')], 'c': []}}
r5 = BL.analyze([dict(x) for x in outside], OWNER, 0, links=L5)
chk('a release linked to an instrument outside the chain cannot kill this chain\'s loan',
    r5['liens'][0]['st'] == 'OPEN' and r5['liens'][0]['how'] == 'read')

cross = [row('2005-02-24', MTG, 'From', MERS, '880000001', 332800),
         row('2005-10-04', MTG, 'From', 'WELLS FARGO BANK', '880000002', 150000, '40644/1728'),
         row('2006-06-15', REL, 'To', 'WELLS FARGO BANK', '880000003', 0, '42228/698')]
L9 = {'880000001': {'i': '880000001', 't': 'M - Mortgage', 'p': [], 'c': []},
      '880000002': {'i': '880000002', 't': 'M - Mortgage', 'p': [], 'c': []},
      '880000003': {'i': '880000003', 't': 'RST - Release', 'p': [BL._anchor_ref('O 41644/1728')], 'c': []}}
r9 = by_date(BL.analyze([dict(x) for x in cross], OWNER, 0, links=L9))
chk('read pages: an unlinked release is not handed to ANOTHER lender\'s loan (rule 2 stays off)',
    r9[('2005-02-24', 332800)]['st'] == 'OPEN' and r9[('2005-10-04', 150000)]['st'] == 'SATISFIED')

aged = [row('1993-11-06', MTG, 'From', 'CENLAR FEDERAL SAVINGS BANK', '890000001', 81746),
        row('2000-03-01', MTG, 'From', 'OTHER BANK', '890000002', 90000),
        row('2026-05-27', REL, 'From', 'AUTO INSURER', '890000003', 0)]
L10 = {'890000001': {'i': '890000001', 't': 'M', 'p': [], 'c': []},
       '890000002': {'i': '890000002', 't': 'M', 'p': [], 'c': []}}
r10 = by_date(BL.analyze([dict(x) for x in aged], OWNER, 0, links=L10))
chk('matured: a 30+ year old loan with no release is not a live junior',
    r10[('1993-11-06', 81746)]['st'] == 'SATISFIED' and r10[('1993-11-06', 81746)]['how'] == 'heur')
chk('matured: a 26 year old loan with no release stays open', r10[('2000-03-01', 90000)]['st'] == 'OPEN')

bought = [row('2021-04-26', 'Deed Transfers of Real Property', 'To', 'SELLER THREE L P', '870000001', 342500),
          row('2021-04-26', MTG, 'From', MERS, '870000002', 319750)]
L8 = {'870000002': {'i': '870000002', 't': 'M - Mortgage', 'p': [], 'c': []}}
chk('a read page with no release is not "sold" by the owner\'s own purchase deed',
    BL.analyze([dict(x) for x in bought], OWNER, 0, links=L8)['liens'][0]['st'] == 'OPEN')
chk('rule 4 (control): legacy kills the purchase mortgage with its own purchase deed',
    BL.analyze([dict(x) for x in bought], OWNER, 0, _legacy=True)['liens'][0]['st'] == 'SATISFIED')

partial = [row('2004-01-05', MTG, 'From', 'INDYMAC BANK', '850000001', 300000),
           row('2008-05-08', 'PARTIAL RELEASE', 'To', 'INDYMAC BANK', '850000002', 0)]
L6 = {'850000001': {'i': '850000001', 't': 'M - Mortgage', 'p': [], 'c': [BL._anchor_ref('850000002')]},
      '850000002': {'i': '850000002', 't': 'PR - Partial Release', 'p': [BL._anchor_ref('850000001 [M]')], 'c': []}}
chk('a linked PARTIAL release does not satisfy the loan',
    BL.analyze([dict(x) for x in partial], OWNER, 0, links=L6)['liens'][0]['st'] == 'OPEN')

# --- 7. duplicate name-variant rows, and a page that could not be read ---------------------------------
dup = [row('2017-11-03', MTG, 'From', MERS, '860000001', 267073, name='DOE,JANE L'),
       row('2021-03-31', MTG, 'From', MERS, '860000002', 254017, name='DOE,JANE L'),
       row('2021-03-31', MTG, 'From', MERS, '860000002', 254017, name='DOE,JANE LOUISE'),
       row('2021-04-12', REL, 'To', MERS, '860000003', 0)]
L7 = {'860000001': {'i': '860000001', 't': 'M', 'p': [], 'c': [BL._anchor_ref('860000003')]},
      '860000002': {'i': '860000002', 't': 'M', 'p': [], 'c': []},
      '860000003': {'i': '860000003', 't': 'RST - Release', 'p': [BL._anchor_ref('860000001 [M]')], 'c': []}}
r7 = BL.analyze([dict(x) for x in dup], OWNER, 0, links=L7)
chk('duplicate rows: one lien per instrument', len(r7['liens']) == 2 and r7['open_count'] == 1)
chk('duplicate rows: fully linked single open reads ok', r7['conf'] == 'ok')
chk('duplicate rows (control): legacy counted the copy as a second open',
    BL.analyze([dict(x) for x in dup], OWNER, 0, _legacy=True)['open_count'] == 2)
r7h = BL.analyze([dict(x) for x in dup], OWNER, 0, links={})
chk('no pages at all: every row is heur and conf is capped at legacy', all(l['how'] == 'heur' for l in r7h['liens'])
    and r7h['conf'] == BL.analyze([dict(x) for x in dup], OWNER, 0, _legacy=True)['conf'])

miss = dict(LINKS)
miss.pop('800000009')
rm = BL.analyze(docs(), OWNER, 0, ftype='MORTGAGE', lead_case=CASE, links=miss)
chk('unreadable mortgage page -> that row is heur, the linked rows stay linked',
    by_date(rm)[('2021-04-26', 319750)]['how'] == 'heur' and by_date(rm)[('2021-06-14', 152000)]['how'] == 'link')

# --- 8. link_chain: what it reads, what it reuses, what it re-reads -----------------------------------
calls = []


def fake(inst):
    calls.append(inst)
    return page(inst) if inst in PAGES else None


cache = {}
links, complete = BL.link_chain(docs(), OWNER, CASE, cache, fetch=fake, today='2026-09-16', pause=0)
want = {'800000002', '800000007', '800000009', '800000010', '800000016',               # mortgages owed
        '800000011', '800000012', '800000017', '800000019',                            # releases that could pair
        '800000024',                                                                   # THIS case's lis pendens
        '800000023'}                                                                   # un-indexed child
chk('link_chain: reads mortgages, pairable releases, this lis pendens and un-indexed children — nothing else',
    set(calls) == want and len(calls) == len(want))
chk('link_chain: a city\'s lien releases are not worth a page', '800000013' not in calls and '800000018' not in calls)
chk('link_chain: complete', complete)
chk('link_chain: the other case\'s lis pendens and the deeds are not read', '800000022' not in calls)
calls.clear()
links2, complete2 = BL.link_chain(docs(), OWNER, CASE, cache, fetch=fake, today='2026-09-20', pause=0)
chk('link_chain: a fresh cache answers without a single request', calls == [] and complete2)
chk('link_chain: cached answers still reach analyze', set(links2) == want)
links3, _ = BL.link_chain(docs(), OWNER, CASE, cache, fetch=fake, today='2026-10-05', pause=0)
chk('link_chain: past the TTL only UNRELEASED mortgages are re-read', sorted(calls) == ['800000009', '800000016'])
calls.clear()
flaky = {}
_, c4 = BL.link_chain(docs(), OWNER, CASE, flaky, fetch=lambda i: None if i == '800000010' else fake(i),
                      today='2026-09-16', pause=0)
chk('link_chain: a failed read leaves the chain incomplete and caches nothing for it',
    not c4 and '800000010' not in flaky)
_, c5 = BL.link_chain(docs(), OWNER, CASE, {}, fetch=fake, today='2026-09-16', budget=3, pause=0)
chk('link_chain: the budget stops reading and marks the chain incomplete', not c5)
calls.clear()
_, c7 = BL.link_chain(docs(), OWNER, CASE, {}, fetch=fake, today='2026-09-16', pause=0, run_budget=BL._link_spent[0])
chk('link_chain: a spent RUN budget stops reads (the nightly cannot balloon)', calls == [] and not c7)
_, c8 = BL.link_chain(docs(), OWNER, CASE, {}, fetch=fake, today='2026-09-16', pause=0, run_seconds=0)
chk('link_chain: a spent RUN clock stops reads too', calls == [] and not c8)
BL._link_fails[0] = BL.LINK_TRIP
calls.clear()
_, c6 = BL.link_chain(docs(), OWNER, CASE, {}, fetch=fake, today='2026-09-16', pause=0)
chk('link_chain: the breaker stops all reads', calls == [] and not c6)
BL._link_fails[0] = 0

# --- 9. the Sheets CRM lien lines (the consumer the team reads) --------------------------------------
import sheets_crm as S   # noqa: E402

chain_rows = [{'d': '1995-08-17', 'amt': 82500, 'party': 'OLD LENDER %d' % i, 'bp': '', 'st': 'SATISFIED'} for i in range(9)]
chain_rows += [{'d': '2021-06-14', 'amt': 152000, 'party': MERS, 'bp': '', 'st': 'SATISFIED', 'inst': '800000010',
                'how': 'link', 'rel': '800000017'},
               {'d': '2025-04-22', 'amt': 217000, 'party': 'HOMEXPRESS MORTGAGE CORP', 'bp': '', 'st': 'OPEN',
                'inst': '800000016', 'how': 'link'}]
crm_lead = {'case': CASE, 'county': 'BROWARD', 'addr': '1 TEST ST', 'orliens': chain_rows}
tab = [x for x in S._prospect_block(crm_lead, {'status': 'Contacted'}, {}, {}, {CASE: {'liens': chain_rows}}, {})['rows']]
lien_lines = [x[1] for x in tab if x[0] == 'lien']
chk('crm: a broward chain row is never re-printed as "BD mortgage OPEN"', not any(x[0].startswith('BD ') for x in tab))
chk('crm: the OPEN loan leads the lien lines even behind 9 old payoffs', lien_lines and 'HOMEXPRESS' in lien_lines[0])
chk('crm: a linked satisfaction names its release', any('(release 800000017)' in x for x in lien_lines))
bd_lead = {'case': 'X', 'orliens': [{'t': 'mortgage', 'h': 'SOME BANK', 'bal': 100000, 'sat': ''}]}
chk('crm: a BatchData-shaped row still prints as BD',
    any(x[0] == 'BD mortgage' for x in S._prospect_block(bd_lead, {}, {}, {}, {}, {})['rows']))

# --- 10. the migration queue ------------------------------------------------------------------------------
chk('relink: a pre-link chain with liens is queued', BL._needs_relink({'liens': [{'st': 'OPEN'}]}))
chk('relink: a stamped chain is not', not BL._needs_relink({'liens': [{'st': 'OPEN'}], 'pv': BL.PAIRING_VERSION}))
chk('relink: a chain with no liens is not', not BL._needs_relink({'liens': []}))
q = sorted([{'id': 'long', 'ftype': 'MORTGAGE', 'open_count': 1, 'liens': [{'st': 'OPEN', 'party': 'BANK OF X'}] * 6},
            {'id': 'common', 'ftype': 'MORTGAGE', 'open_count': 0, 'nrec': 347, 'liens': [{'st': 'SATISFIED', 'party': MERS}] * 9},
            {'id': 'mers', 'ftype': 'MORTGAGE', 'open_count': 1, 'liens': [{'st': 'OPEN', 'party': MERS}] * 2},
            {'id': 'zero', 'ftype': 'MORTGAGE', 'open_count': 0, 'liens': [{'st': 'SATISFIED', 'party': 'BANK'}]}],
           key=BL._relink_rank)
chk('relink: no-open foreclosure first, then multi-MERS, then length; a common-name blend last',
    [x['id'] for x in q] == ['zero', 'mers', 'long', 'common'])

if fails:
    for f in fails:
        print('FAIL:', f)
    print('%d check(s) failed' % len(fails))
    sys.exit(1)
print('broward release pairing by link: all checks pass')
