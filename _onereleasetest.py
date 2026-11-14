"""One release frees one loan (2026-11-14 audit). Offline: records_liens.analyze on built rows.
    python _onereleasetest.py
"""
import sys
import records_liens as R

FOL = '0131234567890'
fails = 0


def rec(name, cond, detail=''):
    global fails
    print(('  PASS ' if cond else '  FAIL ') + name + ((' | %s' % (detail,)) if detail and not cond else ''))
    fails += 0 if cond else 1


def row(typ, date, book, page, second, amt_cfn, ob='', op='', amt=0):
    return {'doC_TYPE': typ, 'foliO_NUMBER': FOL, 'firsT_PARTY': 'SAMPLE JOHN', 'seconD_PARTY': second,
            'reC_DATE': date + ' 12:00:00 AM', 'reC_BOOK': book, 'reC_PAGE': page,
            'reC_BOOKPAGE': '%s/%s' % (book, page), 'cfN_MASTER_ID': amt_cfn,
            'oriG_REC_BOOK': ob, 'oriG_REC_PAGE': op, 'subdiV_NAME': 'EXAMPLE SUB',
            'consideratioN_1': amt}


models = [
    row('DEED - DEE', '1/5/2015', 29000, 1, 'SAMPLE JOHN', 1),
    row('MORTGAGE - MOR', '2/1/2015', 29001, 10, 'EXAMPLE BANK NA', 2, amt=300000),
    row('MORTGAGE - MOR', '3/1/2020', 31000, 20, 'EXAMPLE BANK NA', 3, amt=80000),
    row('SATISFACTION OF MORTGAGE - SMO', '9/1/2020', 31500, 30, 'EXAMPLE BANK NA', 4),   # names no book/page
]
res = R.analyze(models, FOL, 0, owner='SAMPLE JOHN')
st = sorted((l['d'], l['st'], l.get('sat_by', '')) for l in res['liens'])
opens = [l for l in res['liens'] if l['st'] == 'OPEN']
rec('two loans from one lender and ONE release: one loan stays open', len(opens) == 1, st)
rec('the release frees the older loan', bool(opens) and '2020' in opens[0]['d'], st)

models2 = [models[0], row('MORTGAGE - MOR', '2/1/2015', '', '', 'EXAMPLE BANK NA', 5, amt=300000),
           row('SATISFACTION OF MORTGAGE - SMO', '9/1/2016', 30000, 5, 'OTHER LENDER LLC', 6)]
res2 = R.analyze(models2, FOL, 0, owner='SAMPLE JOHN')
rec('a mortgage with a blank book/page is not paid off by an unrelated blank release',
    all(l['st'] == 'OPEN' for l in res2['liens']), [(l['d'], l['st']) for l in res2['liens']])

print('%d failure(s)' % fails)
sys.exit(1 if fails else 0)
