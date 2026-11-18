"""_pbmortgagetest -- Palm Beach mortgages priced from their own recorded documents.

Run:  python _pbmortgagetest.py   (exit 0 = safe; no network, invented people and parcels only)

WHAT THIS PINS
  1. readings(): the clerk stamp (AMT / DOC / INTANGIBLE), the stamp with its labels lost, the note.
  2. decide(): two independent readings must agree within 1%; the stamp figure wins when confirmed.
  3. with_mortgage_amounts(): fills Consideration; a loan printing a DIFFERENT parcel is dropped.
  4. build_chain / pb_chain_truth: every open loan priced -> a priced chain; one unpriced -> a ceiling;
     a common-name (low) chain stays a ceiling even fully priced.
  5. reanalyze(): no network, keeps the trace stamp, counts chains that became priced.
  6. lp_leads_for_pb(): Palm Beach lis pendens rows with a parcel come in; others do not.
  7. worklist(): borrower-side mortgages only, cases closest to done first, nothing read twice.
"""
import datetime as dt
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('DEALFLOW_DIR', tempfile.mkdtemp(prefix='pbmtg-'))
import pb_mortgage_amounts as PMA  # noqa: E402
import palmbeach_liens as PBL       # noqa: E402
import equity_state as ES           # noqa: E402

fails, checks = [], []


def rec(name, ok, extra=''):
    checks.append(name)
    print(('ok   ' if ok else 'FAIL ') + name + ((' | ' + str(extra)[:220]) if extra else ''))
    if not ok:
        fails.append(name)


# ---- 1. readings -------------------------------------------------------------------------------
S1 = 'CFN 20990000001 OR BK 99999 PG 1 RECORDED 1/2/2099 AMT $ 200,000.00 MTG DOC $700.00 INTANGIBLE $400.00'
r = dict(PMA.readings(S1))
rec('1a: labelled stamp gives three readings', r.get('stamp') == 200000 and r.get('intangible') == 200000
    and abs(r.get('doc_stamp', 0) - 200000) < 1, r)
S2 = 'CFN 20990000002 RECORDED 1/2/2099 344,389.00 1,205.40 688.78 Prepared by Sample Lender'
r = dict(PMA.readings(S2))
rec('1b: a stamp with its labels lost counts only through the tax arithmetic',
    r.get('stamp') == 344389 and r.get('intangible') == 344390 and 'doc_stamp' in r, r)
r = dict(PMA.readings('RECORDED 1/2/2099 344,389.00 9,999.00 12.00 nothing else'))
rec('1c: bare figures whose taxes do not add up are not a stamp', 'stamp' not in r, r)
r = dict(PMA.readings('RECORDED AMT$300,OOO.OO MTG DOC $1,O5O.OO INTANGIBLE $6OO.OO'))
rec('1d: OCR letter O read as zero', r.get('stamp') == 300000 and r.get('intangible') == 300000, r)
r = dict(PMA.readings('Borrower owes Lender ... Dollars (U.S.S 295, 000.00) plus interest'))
rec('1e: the note figure is read, OCR spacing repaired', r.get('note') == 295000, r)

# ---- 2. decide ---------------------------------------------------------------------------------
d = PMA.decide(PMA.readings(S1))
rec('2a: stamp confirmed by the taxes -> priced at the stamp figure', d['status'] == 'priced' and d['amt'] == 200000, d)
rec('2b: one reading alone is never priced', PMA.decide([('stamp', 200000.0)])['status'] == 'unconfirmed')
rec('2c: readings that disagree are never priced',
    PMA.decide([('stamp', 200000.0), ('note', 250000.0)])['status'] == 'unconfirmed')
rec('2d: the stamp wins over the doc stamp (rounded up to $100 by statute)',
    PMA.decide([('stamp', 344389.0), ('doc_stamp', 344400.0), ('intangible', 344390.0)])['amt'] == 344389)
rec('2e: two copies of the same source are not two readings',
    PMA.decide([('note', 200000.0), ('note', 200000.0)])['status'] == 'unconfirmed')
rec('2f: parcel numbers are read as 17 digits',
    PMA.parcels('Parcel ID 00-42-47-17-19-000-1730 and nothing else') == ['00424717190001730'])


# ---- fixtures: invented owner, invented loans ------------------------------------------------
def ms(y, m, d):
    return int((dt.datetime(y, m, d) - dt.datetime(1970, 1, 1)).total_seconds() * 1000)


def doc(dtype, bp, date, name='TESTER PAT', party='FROM', cross='SAMPLE BANK NA', **kw):
    out = {'Name': name, 'Party': party, 'CrossPartyName': cross, 'DocTypeDescription': dtype,
           'Consideration': '', 'RecordDate': '/Date(%d)/' % ms(*date), 'BookPage': bp,
           'InstrumentNumber': '', 'CaseNumber': kw.get('case', ''), 'DocLegalDescription': kw.get('legal', ''),
           'ParcelNumber': ''}
    return out


DOCS = [doc('DEED', 'O 20000/1', (2010, 1, 5), party='TO', cross='PRIOR OWNER'),
        doc('MORTGAGE', 'O 20000/2', (2010, 1, 5)),
        doc('MORTGAGE', 'O 30000/9', (2019, 6, 1), cross='SAMPLE CREDIT UNION'),
        doc('LIS PENDENS', 'O 40000/1', (2026, 3, 1), cross='SAMPLE BANK NA', case='502099CA000001XXXAMB')]


def entry(amt, pcns=()):
    return {'v': PMA.MTG_V, 'status': 'priced', 'amt': amt, 'basis': 'intangible+stamp', 'pcns': list(pcns)}


CASE, FOLIO = '502099CA000001XXXAMB', '00424717190001730'

# ---- 3. with_mortgage_amounts -----------------------------------------------------------------------
cache = {'PALM BEACH:MTG:20000/2': entry(250000), 'PALM BEACH:MTG:30000/9': entry(40000, [FOLIO])}
docs2, st = PBL.with_mortgage_amounts(DOCS, FOLIO, cache)
amts = {x['BookPage']: x['Consideration'] for x in docs2 if x['DocTypeDescription'] == 'MORTGAGE'}
rec('3a: both loans carry their document amounts', amts == {'O 20000/2': '250000', 'O 30000/9': '40000'} and st['priced'] == 2, (amts, st))
cache_other = {'PALM BEACH:MTG:20000/2': entry(250000), 'PALM BEACH:MTG:30000/9': entry(40000, ['00424717190009990'])}
docs3, st3 = PBL.with_mortgage_amounts(DOCS, FOLIO, cache_other)
rec('3b: a loan whose pages print a different parcel is dropped (the owner\'s other property)',
    st3['other_parcel'] == 1 and not any(x['BookPage'] == 'O 30000/9' for x in docs3), st3)
docs4, _ = PBL.with_mortgage_amounts(DOCS, '', cache_other)
rec('3c: with no parcel on the lead, nothing is dropped', any(x['BookPage'] == 'O 30000/9' for x in docs4))

# ---- 4. build_chain / pb_chain_truth ------------------------------------------------------------------
lead = {'county': 'PALM BEACH', 'ctype': 'Bank/Mortgage'}
ch = PBL.build_chain(DOCS, 'TESTER PAT', 0, 'MORTGAGE', CASE, FOLIO, cache)
rec('4a: every open loan priced -> no unpriced loans, liens itemized',
    ch['mtg_open_unpriced'] == 0 and len(ch['liens']) == 2 and ch.get('mtg_doc_priced') == 2, ch.get('chain_note'))
rec('4b: ...and the chain reads as a priced FACT', ES.state_of(ch, lead) == 'priced', (ES.state_of(ch, lead), ch.get('conf')))
ch1 = PBL.build_chain(DOCS, 'TESTER PAT', 0, 'MORTGAGE', CASE, FOLIO, {'PALM BEACH:MTG:20000/2': entry(250000)})
rec('4c: one loan still unpriced -> a ceiling, and the note says why',
    ch1['mtg_open_unpriced'] == 1 and ES.state_of(ch1, lead) == 'unpriced' and 'no confirmed amount' in ch1['chain_note'],
    (ch1['mtg_open_unpriced'], ES.state_of(ch1, lead)))
ch0 = PBL.build_chain(DOCS, 'TESTER PAT', 0, 'MORTGAGE', CASE, FOLIO, {})
rec('4d: no amounts at all -> unpriced as before', ch0['conf'] == 'unpriced' and ES.state_of(ch0, lead) == 'unpriced')
many = DOCS + [doc('JUDGMENT', 'O 1%04d/1' % i, (2000, 1, 1), cross='SAMPLE CREDITOR %d' % i) for i in range(40)]
chl = PBL.build_chain(many, 'TESTER PAT', 0, 'MORTGAGE', CASE, FOLIO, cache)
rec('4e: a common-name (low) chain stays a ceiling even when every loan is priced',
    chl['conf'] == 'low' and ES.state_of(chl, lead) != 'priced', (chl['conf'], ES.state_of(chl, lead)))
rel = DOCS + [doc('SATISFACTION', 'O 25000/5', (2015, 2, 2), cross='SAMPLE BANK NA', legal='SATISFIES O 20000/2')]
chr_ = PBL.build_chain(rel, 'TESTER PAT', 0, 'MORTGAGE', CASE, FOLIO, {'PALM BEACH:MTG:30000/9': entry(40000)})
rec('4f: a released loan with no amount does not hold the chain unpriced',
    chr_['mtg_released'] == 1 and chr_['mtg_open_unpriced'] == 0, (chr_['mtg_released'], chr_['mtg_open_unpriced']))

# ---- 5. reanalyze ----------------------------------------------------------------------------------------
out = {CASE: dict(ch0, traced='2099-01-01', captcha='2captcha-pool', owner='TESTER PAT')}
store = {CASE: {'docs': DOCS, 'owner': 'TESTER PAT', 'judg': 0, 'ftype': 'MORTGAGE', 'folio': FOLIO}}
n, moved = PBL.reanalyze(out, store=store, cache=cache, save=False, say=lambda m: None)
rec('5a: reanalyze rebuilds with the new amounts and counts the chain that became priced',
    n == 1 and moved == 1 and out[CASE]['mtg_open_unpriced'] == 0, (n, moved))
rec('5b: the trace stamp and captcha source are kept', out[CASE]['traced'] == '2099-01-01'
    and out[CASE]['captcha'] == '2captcha-pool' and out[CASE].get('reanalyzed'))
out2 = {}
n2, _ = PBL.reanalyze(out2, store=store, cache=cache, save=False, say=lambda m: None)
rec('5c: a case never traced is not invented from kept rows', n2 == 0 and out2 == {})

# ---- 6. lp_leads_for_pb ----------------------------------------------------------------------------------
import json  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, 'lp.json')
    json.dump([{'county': 'PALM BEACH', 'case': 'A1', 'oname': 'TESTER, PAT', 'folio': FOLIO, 'ftype': 'MORTGAGE'},
               {'county': 'PALM BEACH', 'case': 'A2', 'oname': 'TESTER, SAM', 'folio': ''},
               {'county': 'BROWARD', 'case': 'A3', 'oname': 'TESTER, LEE', 'folio': '494232140320'},
               {'county': 'PALM BEACH', 'case': 'A4', 'oname': 'TESTER, KIM', 'folio': FOLIO}], open(p, 'w'))
    got = PBL.lp_leads_for_pb({'A4'}, p)
rec('6a: only Palm Beach rows with a parcel, not already a lead, come in',
    [g['case'] for g in got] == ['A1'] and got[0]['owners'] == 'TESTER, PAT' and got[0]['folio'] == FOLIO, got)

# ---- 7. worklist -------------------------------------------------------------------------------------------
store7 = {'C1': {'docs': DOCS}, 'C2': {'docs': [doc('MORTGAGE', 'O 50000/1', (2020, 1, 1))]},
          'C3': {'docs': [doc('MORTGAGE', 'O 60000/1', (2020, 1, 1), party='TO')]}}
wl = PMA.worklist(store7, {}, dt.date(2099, 1, 1), live={'C1': 1, 'C2': 1, 'C3': 1})
rec('7a: borrower-side loans only; the case with one loan to read goes first',
    [k for _c, k, _bp in wl] == ['PALM BEACH:MTG:50000/1', 'PALM BEACH:MTG:30000/9', 'PALM BEACH:MTG:20000/2'], wl)
wl2 = PMA.worklist(store7, {'PALM BEACH:MTG:50000/1': entry(1000)}, dt.date(2099, 1, 1), live={'C2': 1})
rec('7b: a mortgage already read is not read again', wl2 == [], wl2)
rec('7c: a blocked download is asked again the next day; an unconfirmed read is not',
    PMA.due({'v': PMA.MTG_V, 'status': 'gap_pdf', 'read': '2099-01-01'}, dt.date(2099, 1, 2))
    and not PMA.due({'v': PMA.MTG_V, 'status': 'unconfirmed', 'read': '2099-01-01'}, dt.date(2099, 6, 1)))
e = PMA.read_one('O 70000/1', dt.date(2099, 1, 1), fetch=lambda b, p: b'%PDF', ocr_read=lambda c: (S1, {'pages': 1}))
rec('7d: read_one with a stamp page -> a priced entry', e['status'] == 'priced' and e['amt'] == 200000, e)
e = PMA.read_one('O 70000/1', dt.date(2099, 1, 1), fetch=lambda b, p: None, ocr_read=lambda c: ('', {}))
rec('7e: no document -> gap_pdf, never "no amount"', e['status'] == 'gap_pdf', e)

print('\n%d checks, %d failed' % (len(checks), len(fails)))
print('ALL PASS' if not fails else 'FAILED: ' + '; '.join(fails))
sys.exit(1 if fails else 0)
