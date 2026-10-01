#!/usr/bin/env python3
"""_xchecktest.py -- a second source can only demote a lien chain, never promote it."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lien_xcheck as X, equity_state as ES
F = []
def check(n, ok):
    print(('ok   ' if ok else 'FAIL ') + n); (ok or F.append(n))
XML = b'''<R><Status>Successful</Status><OfficialRecordList>
<OfficialRecords><DOC_TYPE>MOR</DOC_TYPE><REC_DATE>2024-03-05T00:00:00</REC_DATE><REC_BOOK>34000</REC_BOOK><REC_PAGE>0123</REC_PAGE><INTANGIBLE>600</INTANGIBLE><PARTY_CODE>D</PARTY_CODE></OfficialRecords>
<OfficialRecords><DOC_TYPE>MOR</DOC_TYPE><REC_DATE>2024-03-05T00:00:00</REC_DATE><REC_BOOK>34000</REC_BOOK><REC_PAGE>0123</REC_PAGE><INTANGIBLE>600</INTANGIBLE><PARTY_CODE>R</PARTY_CODE></OfficialRecords>
<OfficialRecords><DOC_TYPE>DEE</DOC_TYPE><REC_BOOK>1</REC_BOOK><REC_PAGE>2</REC_PAGE></OfficialRecords>
<OfficialRecords><DOC_TYPE>SAT</DOC_TYPE><REC_BOOK>40000</REC_BOOK><REC_PAGE>9</REC_PAGE><ORIG_REC_BOOK>30000</ORIG_REC_BOOK><ORIG_REC_PAGE>5</ORIG_REC_PAGE></OfficialRecords>
</OfficialRecordList></R>'''
rows = X.parse_cds_xml(XML)
check('dedupes per-party rows, drops deeds', [r['cat'] for r in rows] == ['mortgage', 'release'])
check('ISO date and intangible amount', rows[0]['d'] == '3/5/2024' and rows[0]['amt'] == 300000)
clean = {'conf': 'ok', 'liens': [], 'coverage': 'x'}
check('unseen mortgage is a conflict', X.conflicts(clean, rows) == ['34000/123'])
known = {'conf': 'ok', 'liens': [{'bp': '34000/0123', 'amt': 1}]}
check('known mortgage (zero-padded) is no conflict', X.conflicts(known, rows) == [])
check('release target is not a conflict', X.conflicts(clean, [{'cat': 'mortgage', 'bp': '30000/5'}, rows[1]]) == [])
priced = {'conf': 'ok', 'liens': [{'bp': '1/1', 'amt': 5}]}
check('priced chain is priced with no cache', ES.state_of(X.stamp(priced, 'c', {})) == 'priced')
st = X.stamp(priced, 'c', {'c': {'rows': rows}})
check('priced chain demoted when second source disagrees', ES.state_of(st) == 'unpriced')
check('stamp does not mutate the chain', 'xs_conflict' not in priced)
check('conflict never promotes an unverified chain', ES.state_of(X.stamp({'conf': 'low', 'liens': []}, 'c', {'c': {'rows': rows}})) == 'none')
check('no chain stays unchecked', ES.state_of(X.stamp(None, 'c', {'c': {'rows': rows}})) == 'unchecked')
sys.exit(1 if F else 0)
