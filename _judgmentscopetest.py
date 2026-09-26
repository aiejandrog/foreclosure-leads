#!/usr/bin/env python3
"""_judgmentscopetest.py — E1 deep-analysis gaps, on invented dockets and invented judgment text.

  * the controlling final judgment's BODY is read for scope: in rem, deficiency, which docket
    defendants it names (report only; the verdict does not move)
  * an attachment's own title is kept as attached_document_title
  * the calendar-hearing override keeps the label it replaced (pre_calendar_kind) and is counted
  * the public OR copy the docket cites by book/page is fetched for free (index CFN + anonymous
    image endpoint) and nothing is fetched when nothing is cited

Run: python _judgmentscopetest.py
"""
import sys

import miami_case_timeline as T
import document_coverage as COV
import run_case_timeline as RCT

FAIL = []


def check(name, cond, got=None):
    if not cond:
        FAIL.append(name)
        print('FAIL  %s  -> %r' % (name, got))
    else:
        print('ok    %s' % name)


def entry(n, text, date=None, **meta):
    return {'source_id': str(n), 'expected_documents': 0, 'metadata': dict(
        eventID=n, eventDate=date or '09/%02d/2026' % n, docketDescrition=text, **meta)}


def page(n, text):
    return {'page': n, 'text': text, 'outcome': 'ocr_text'}


PARTIES = [{'partyName': 'ALPHA BETA', 'partyTypeDesc': 'DEFENDANT'},
           {'partyName': 'GAMMA HOLDINGS LLC', 'partyTypeDesc': 'DEFENDANT'},
           {'partyName': 'EXAMPLE BANK NA', 'partyTypeDesc': 'PLAINTIFF'}]


def run(entries, docs=(), parties=PARTIES):
    inv = {'entries': entries, 'pagination_verified': True, 'raw': {'parties': parties}}
    return T.build_timeline('SYNTHETIC', inv, list(docs), '2026-09-23')


# ---- judgment_scope on its own
JB = ('FINAL JUDGMENT OF FORECLOSURE\nThis action was heard against Defendant ALPHA BETA.\n'
      'Plaintiff is due $123,456.78.\n')
JB3 = 'Page three.\nThe Court reserves jurisdiction to enter a deficiency judgment.\n'
s = T.judgment_scope(JB + JB3, ['ALPHA BETA', 'GAMMA HOLDINGS LLC'])
check('scope: body read', s['read'] is True, s)
check('scope: deficiency reserved', s['deficiency'] == 'reserved', s)
check('scope: one defendant named, the other listed as not named',
      s['defendants_named'] == 1 and s['defendants_not_named'] == ['GAMMA HOLDINGS LLC'], s)
check('scope: the deficiency passage is quoted', any('deficiency' in p for p in s['passages']), s)
check('scope: carries its qualification', 'verify' in s['qualification'], s)
s = T.judgment_scope('Judgment in rem only. No deficiency judgment shall be entered.', [])
check('scope: in rem only', s['in_rem_only'] is True, s)
check('scope: no deficiency -> denied_or_waived', s['deficiency'] == 'denied_or_waived', s)
s = T.judgment_scope('A deficiency judgment is hereby entered against the borrower.', [])
check('scope: deficiency awarded', s['deficiency'] == 'awarded' and s['in_rem_only'] is False, s)
check('scope: empty body is not read', T.judgment_scope('   ', [])['read'] is False)
s = T.judgment_scope('Final judgment as to Count II only.', [])
check('scope: a count limitation is limited scope', s['limited_scope'] is True, s)

# ---- in the timeline: the controlling judgment's body, page 3 included
r = run([entry(1, 'Complaint'), entry(2, 'Final Judgment')],
        [{'entry_ref': '2', 'reading': {'pages': [page(1, JB), page(2, 'Legal description.'), page(3, 'More.'),
                                                  page(4, JB3)]}}])
e2 = next(e for e in r['entries'] if e['entry_id'] == '2')
check('timeline: the final judgment carries judgment_scope', (e2.get('judgment_scope') or {}).get('read') is True, e2)
check('timeline: page 4 is read for scope (not just the first pages)',
      e2['judgment_scope']['deficiency'] == 'reserved', e2['judgment_scope'])
check('timeline: controlling_scope is the controlling judgment\'s',
      r['judgments'].get('controlling_entry') == '2'
      and r['judgments'].get('controlling_scope', {}).get('defendants_not_named') == ['GAMMA HOLDINGS LLC'],
      r['judgments'])
check('timeline: the verdict is untouched', r['status']['kind'] == 'judgment_entered', r['status'])
check('timeline: no private scope text is left on the entry', '_scope_body' not in e2 and '_body' not in e2, sorted(e2))
r = run([entry(1, 'Final Judgment')])
check('timeline: a controlling judgment with no body says so',
      r['judgments'].get('controlling_scope', {}).get('read') is False, r['judgments'])

# ---- attached_document_title kept
r = run([entry(3, 'Affidavit of Indebtedness', expected=1)],
        [{'entry_ref': '3', 'reading': {'pages': [page(1, 'FINAL JUDGMENT OF MORTGAGE FORECLOSURE\nIT IS ADJUDGED')]}}])
e3 = r['entries'][0]
check('attached: the entry keeps its docket kind', e3['kind'] == 'affidavit', e3['kind'])
check('attached: the attachment kind is reported', e3.get('attached_document_kind') == 'final_judgment', e3)
check('attached: the attachment title is kept', bool(e3.get('attached_document_title'))
      and 'JUDGMENT' in e3['attached_document_title'].upper(), e3.get('attached_document_title'))

# ---- calendar override measured, behaviour unchanged
r = run([entry(4, 'Order granting motion to dismiss', eventType='Hearing'),
         entry(5, 'Notice of Hearing on Motion for Summary Judgment', eventType='Hearing'),
         entry(6, 'Notice of sale on 10/20/2026', eventType='Hearing'),
         entry(7, 'Complaint')])
k = {e['entry_id']: e for e in r['entries']}
check('calendar: override still wins', k['4']['kind'] == 'hearing', k['4'])
check('calendar: the label it replaced is kept (an order of dismissal the override hid)',
      k['4'].get('pre_calendar_kind') == 'order_of_dismissal', k['4'])
check('calendar: an entry already a hearing gets no pre_calendar_kind', 'pre_calendar_kind' not in k['5'], k['5'])
check('calendar: notice of sale is still exempt', k['6']['kind'] == 'notice_of_sale', k['6'])
co = r.get('calendar_override') or {}
check('calendar: counts', co.get('calendar_entries') == 3 and co.get('relabelled') == 1
      and sum(co.get('by_original_kind', {}).values()) == 1, co)

# ---- alternate OR copy fetch
class Idx:
    def __init__(self, rows): self.rows = rows
    def get(self, b, p): return self.rows.get('%s-%s' % (b, p))

calls = []
def collect(case, records, collector=None, ocr=None, resume=False):
    calls.append((case, records[0].get('cfN_MASTER_ID'), resume))
    return [{'status': 'stored', 'source_ref': 'official_records/%s-%s' % (records[0]['reC_BOOK'], records[0]['reC_PAGE'])}]

idx = Idx({'33456-1201': {'reC_BOOK': '33456', 'reC_PAGE': '1201', 'cfN_MASTER_ID': 900001, 'doC_TYPE': 'JUD'}})
needed = [{'entry_id': '10', 'kind': 'final_judgment', 'cited_book_page': ['33456-1201', '33456-1201']},
          {'entry_id': '11', 'kind': 'final_judgment', 'cited_book_page': ['40000-0001']},
          {'entry_id': '12', 'kind': 'certificate_of_title', 'cited_book_page': []}]
out = COV.fetch_alternates(needed, 'SYNTHETIC', index=idx, collect=collect)
by = {(o['entry_id'], o['book_page']): o for o in out}
check('fetch: an indexed book/page is fetched once, resumable', calls == [('SYNTHETIC', 900001, True)], calls)
check('fetch: reported fetched with its source_ref', by[('10', '33456-1201')]['status'] == 'fetched'
      and by[('10', '33456-1201')]['source_ref'] == 'official_records/33456-1201', out)
check('fetch: a book/page not in the index is a named gap, not fetched', by[('11', '40000-0001')]['status'] == 'not_in_index', out)
check('fetch: an entry citing nothing is named', by[('12', None)]['status'] == 'no_citation', out)
calls.clear()
many = [{'entry_id': str(i), 'cited_book_page': ['33456-1201']} for i in range(1)] + \
       [{'entry_id': 'x%d' % i, 'cited_book_page': ['5000%d-1' % i]} for i in range(3)]
idx2 = Idx({'33456-1201': idx.rows['33456-1201'], **{'5000%d-1' % i: dict(idx.rows['33456-1201'], reC_BOOK='5000%d' % i, reC_PAGE='1') for i in range(3)}})
out = COV.fetch_alternates(many, 'SYNTHETIC', index=idx2, collect=collect, limit=2)
check('fetch: the per-run limit holds, the rest say limit', len(calls) == 2
      and sum(o['status'] == 'limit' for o in out) == 2, out)
def boom(*a, **k): raise RuntimeError('endpoint down')
out = COV.fetch_alternates(needed[:1], 'SYNTHETIC', index=idx, collect=boom)
check('fetch: a failing fetch is an error row, never raised', out[0]['status'] == 'error', out)
check('runner: nothing cited -> no fetch at all',
      RCT.fetch_alternate_copies('SYNTHETIC', {'alternate_copy_needed': [needed[2]]}, collect=collect) is None)
check('runner: --no-fetch-alternates exists', '--no-fetch-alternates' in open(RCT.__file__, encoding='utf-8').read())

print()
if FAIL:
    print('%d FAILED: %s' % (len(FAIL), ', '.join(FAIL)))
    sys.exit(1)
print('all passed')
