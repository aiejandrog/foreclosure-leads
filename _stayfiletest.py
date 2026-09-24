"""_stayfiletest -- §362 stay flags must be in leads_final.json, not only on the built board.

Run:  python _stayfiletest.py     (exit 0 = safe; no network, no browser, synthetic data only)

2026-09-24: a rebuild on main was blocked by healthcheck's compliance rule "§362 stay flags reach
the build" while the board it had just built carried 80 stays. The rule counts stays in the LEAD
FILES (cache -> leads -> board). main() wrote leads_final.json straight from the scrape, which
carries no stay flags, and only make_tracker restored them from sale_history_cache.json -- in
memory, after the file was already on disk. Unless sale_history.py re-stamped the file later in the
run (run-leads.bat never runs it), the file read 0 stays and the gate failed a correct board.
"""
import json
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if 'playwright' not in sys.modules:                  # import shim: browser dependency, not under test
    _pw = types.ModuleType('playwright'); _sa = types.ModuleType('playwright.sync_api')
    _sa.sync_playwright = lambda *a, **k: None
    sys.modules['playwright'], sys.modules['playwright.sync_api'] = _pw, _sa

import foreclosure_leads as F

FAILS = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond:
        FAILS.append(name)


CACHE = {
    '2099-000001-CA-01': {'a': True, 'bd': '09/01/2026', 'b': 1, 's': 2, 'n': 3},
    '2099-000002-CA-01': {'a': True, 'bd': '08/15/2026'},
    '2099-000003-CA-01': {'a': False, 'b': 1, 'sl': True},        # stay lifted: not active
    '2099-000005-CA-01': {'a': True, 'bd': '07/01/2026'},
}
scrape = [{'Case #': c} for c in ('2099-000001-CA-01', '2099-000002-CA-01', '2099-000003-CA-01',
                                  '2099-000004-CA-01', '2099-000005-CA-01')]

tmp = tempfile.mkdtemp()
json.dump(CACHE, open(os.path.join(tmp, 'sale_history_cache.json'), 'w'))
_here = F.HERE
F.HERE = tmp
try:
    cache_act = sum(1 for e in CACHE.values() if e.get('a'))
    before = sum(1 for r in scrape if r.get('sale_bk_active'))
    check('reproduced: a fresh scrape carries no stay flags (healthcheck read leads 0 -> STRIPPED)',
          before == 0 and cache_act == 3)
    n = F.restore_stays_from_cache(scrape)
    after = sum(1 for r in scrape if r.get('sale_bk_active'))
    check('restore puts every active cached stay back on the lead rows', n == 3 and after == cache_act,
          '%d restored, %d on rows' % (n, after))
    check('a lifted stay is not re-activated',
          not scrape[2].get('sale_bk_active') and scrape[2].get('sale_stay_lifted') is True)
    check('the stay date rides along', scrape[0].get('sale_bk_date') == '09/01/2026')
    check('a case the cache does not know is untouched', scrape[3] == {'Case #': '2099-000004-CA-01'})
    check('running it twice changes nothing', F.restore_stays_from_cache(scrape) == 0
          and sum(1 for r in scrape if r.get('sale_bk_active')) == 3)

    # THE PRODUCTION WRITE: run main() itself with the network stages stubbed, then read the file it
    # wrote. A check that dumps its own restored rows proves nothing about what main() persists.
    _stubs = {k: getattr(F, k) for k in ('scrape', 'enrich', 'enrich_clerk', 'qualify', 'make_tracker',
                                         '_load_codes')}
    _env = {k: os.environ.get(k) for k in ('DEALFLOW_FORCE', 'DEALFLOW_NO_DESKTOP')}
    seen_by_board = []
    def _qualify(rows):
        for r in rows:
            r.update(score=1, tier='C', sale_type='FC')
        return rows
    try:
        os.environ['DEALFLOW_FORCE'] = '1'; os.environ['DEALFLOW_NO_DESKTOP'] = '1'
        F.scrape = lambda: [{'Case #': c} for c in CACHE] + [{'Case #': '2099-000004-CA-01'}]
        F.enrich = F.enrich_clerk = lambda rows: rows
        F.qualify = _qualify
        F.make_tracker = lambda rows: seen_by_board.extend(r for r in rows if r.get('sale_bk_active'))
        F._load_codes = lambda: []
        F.main()
    finally:
        for k, v in _stubs.items():
            setattr(F, k, v)
        for k, v in _env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    on_disk = [r for r in json.load(open(os.path.join(tmp, 'leads_final.json'))) if r.get('sale_bk_active')]
    check('main() writes the active cached stays into leads_final.json (healthcheck: leads == board)',
          len(on_disk) == cache_act == len(seen_by_board), '%d on disk, %d to the board' % (len(on_disk), len(seen_by_board)))

    # A LIFT MUST STILL WIN. The nightly runs sale_history.py [3e/5] AFTER main() wrote those flags.
    # When a live docket read now shows the stay closed, the row has to lose the flag, and the
    # [4/5] rebuild's make_tracker restore must not put it back from the (now updated) cache.
    import sale_history as SH
    BK = lambda d, n='': {'docketDescrition': 'Suggestion of Bankruptcy', 'eventDate': d, 'comments': n}
    CLOSE = lambda d, t='Order Granting Relief from Automatic Stay', n='': {'docketDescrition': t, 'eventDate': d, 'comments': n}
    DISMISS = lambda d, n='': CLOSE(d, 'Order of Dismissal', n)
    dockets = {                                               # real docket arrays through the real parser
        '2099-000001-CA-01': [BK('09/01/2026'), CLOSE('09/20/2026')],     # lifted since it was cached
        '2099-000002-CA-01': None,                                         # fetch fails
        '2099-000005-CA-01': [],                                           # empty answer
        '2099-000006-CA-01': [CLOSE('08/01/2026', 'Order of Dismissal')],  # close line, filing line not shown
        '2099-000007-CA-01': [],                                           # row flagged, cache entry gone
        '2099-000008-CA-01': [BK('05/01/2026'), CLOSE('06/01/2026')],     # only an OLDER stay, closed
        '2099-000009-CA-01': [BK('09/15/2026')],                           # new stay, row has an old lift
        '2099-000010-CA-01': [BK('05/01/2026'), CLOSE('06/01/2026')],     # older stay closed; prior date slash-form
        '2099-000011-CA-01': [BK('03/01/2026'), BK('08/01/2026'), CLOSE('09/15/2026')],   # only the NEWER case closed
        '2099-000012-CA-01': [BK('03/01/2026'), CLOSE('04/01/2026'), BK('08/01/2026'), CLOSE('09/15/2026')],  # both closed
        '2099-000013-CA-01': [BK('03/01/2026', '26-11111'), BK('03/01/2026', '26-22222'),
                              DISMISS('09/15/2026', '26-22222')],          # two cases filed the same day, one closed
        '2099-000014-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'),
                              DISMISS('09/01/2026', '26-33333'), DISMISS('09/15/2026', '26-11111')],  # held case dismissed late
        '2099-000015-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'),
                              DISMISS('09/15/2026', '26-33333')],          # numbered: only the newer case closed
        '2099-000016-CA-01': [BK('03/01/2026', '26-11111'), BK('03/01/2026', '26-22222'),
                              DISMISS('09/10/2026', '26-11111'), DISMISS('09/15/2026', '26-22222')],  # same-day pair, both closed
        '2099-000017-CA-01': [BK('03/01/2026'), BK('08/01/2026'), DISMISS('09/01/2026'), DISMISS('09/15/2026')],  # numberless late closes
        '2099-000018-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'),
                              DISMISS('09/15/2026', '26-11111')],          # held case closed late, NEWER case still open
        '2099-000019-CA-01': [BK('03/01/2026', '26-11111'), DISMISS('05/01/2026', '26-11111'),
                              {'docketDescrition': 'Notice of Reinstatement', 'eventDate': '06/01/2026',
                               'comments': 'bankruptcy 26-11111 reinstated'}],   # dismissed, then reinstated
        '2099-000020-CA-01': [BK('03/01/2026', '26-11111'),
                              {'docketDescrition': 'Sale Cancelled', 'comments': 'CANCELLED PER BANKRUPTCY', 'eventDate': '03/10/2026'},
                              DISMISS('06/01/2026', '26-11111')],          # the stay acting, then its own dismissal
        '2099-000021-CA-01': [BK('03/01/2026'), {'docketDescrition': 'Notice of Bankruptcy', 'eventDate': '03/01/2026'},
                              CLOSE('06/01/2026', 'Order Dismissing Bankruptcy Case')],  # two petition lines one day, one case
        '2099-000022-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'),
                              DISMISS('09/15/2026', '26-11111')],          # same as 18, but no stay held yet
        '2099-000023-CA-01': [BK('03/01/2026', '26-11111'), BK('05/01/2026', '26-22222'), BK('08/01/2026', '26-33333'),
                              DISMISS('09/10/2026', '26-11111'), DISMISS('09/15/2026', '26-33333')],  # middle case open
        '2099-000024-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'),
                              {'docketDescrition': 'Order Case Pending Bankruptcy Stay', 'eventDate': '08/15/2026',
                               'comments': '26-11111'},
                              DISMISS('09/15/2026', '26-11111')],          # older case acts after the newer filing, then closes
        '2099-000025-CA-01': [BK('03/01/2026', '26-11111'), DISMISS('04/01/2026', '26-11111'),
                              BK('08/01/2026', '26-33333'), DISMISS('09/15/2026', '26-33333')],  # the held 05-01 filing is missing
        '2099-000026-CA-01': [BK('03/01/2026', '26-11111'), BK('08/01/2026', '26-33333'), DISMISS('08/20/2026', '26-33333'),
                              DISMISS('09/01/2026', '26-11111'),
                              {'docketDescrition': 'Notice of Reinstatement', 'eventDate': '09/10/2026',
                               'comments': 'bankruptcy 26-11111 reinstated'}],   # older case reinstated after both closed
    }
    extra_cache = {'2099-000006-CA-01': {'a': True, 'bd': '2026-07-10'},
                   '2099-000008-CA-01': {'a': True, 'bd': '2026-09-10'},
                   '2099-000010-CA-01': {'a': True, 'bd': '09/10/2026'},
                   '2099-000011-CA-01': {'a': True, 'bd': '2026-03-01'},
                   '2099-000012-CA-01': {'a': True, 'bd': '2026-03-01'},
                   **{'2099-0000%d-CA-01' % i: {'a': True, 'bd': '2026-03-01'} for i in (*range(13, 22), 23, 26)},
                   '2099-000025-CA-01': {'a': True, 'bd': '2026-05-01'}}
    _cf = os.path.join(tmp, 'sale_history_cache.json')
    json.dump(dict(json.load(open(_cf)), **extra_cache), open(_cf, 'w'))
    _lf = os.path.join(tmp, 'leads_final.json')
    json.dump(json.load(open(_lf)) + [
        {'Case #': '2099-000006-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-07-10'},
        {'Case #': '2099-000007-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-06-01'},
        {'Case #': '2099-000008-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-09-10'},
        {'Case #': '2099-000009-CA-01', 'sale_stay_lifted': '2026-01-01'},
        {'Case #': '2099-000010-CA-01', 'sale_bk_active': True, 'sale_bk_date': '09/10/2026'},
        {'Case #': '2099-000011-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-03-01'},
        {'Case #': '2099-000012-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-03-01'}] +
        [{'Case #': '2099-0000%d-CA-01' % i, 'sale_bk_active': True, 'sale_bk_date': '2026-03-01'} for i in (*range(13, 22), 23)] +
        [{'Case #': '2099-000022-CA-01'}, {'Case #': '2099-000024-CA-01'},
         {'Case #': '2099-000025-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-05-01'},
         {'Case #': '2099-000026-CA-01', 'sale_bk_active': True, 'sale_bk_date': '2026-03-01'}],
        open(_lf, 'w'))
    _sh = {k: getattr(SH, k) for k in ('HERE', 'CACHE', '_fetch', 'time')}
    _argv = sys.argv
    try:
        SH.HERE = tmp; SH.CACHE = _cf
        SH._fetch = lambda session, case: dockets.get(case, [])
        SH.time = types.SimpleNamespace(time=__import__('time').time, sleep=lambda s: None)
        sys.argv = ['sale_history.py']
        SH.main()
    finally:
        for k, v in _sh.items():
            setattr(SH, k, v)
        sys.argv = _argv
    after_sh = {r['Case #']: r for r in json.load(open(_lf))}
    _cache_now = json.load(open(_cf))
    on = lambda c: after_sh[c].get('sale_bk_active') is True
    check('a live read showing the stay lifted clears the flag the scrape step wrote',
          not on('2099-000001-CA-01') and after_sh['2099-000001-CA-01'].get('sale_stay_lifted') == '2026-09-20')
    check('a failed live read never clears a stay', on('2099-000002-CA-01'))
    check('an empty docket never ends a cached stay (row or cache)',
          on('2099-000005-CA-01') and after_sh['2099-000005-CA-01'].get('sale_bk_date') == '07/01/2026'
          and _cache_now['2099-000005-CA-01'].get('a') is True)
    check('a closing line with no filing line does not end a cached stay (it may close another matter)',
          on('2099-000006-CA-01') and _cache_now['2099-000006-CA-01'].get('a') is True
          and not _cache_now['2099-000006-CA-01'].get('sl'))
    check('an empty docket never ends a stay flagged on the row when its cache entry is gone',
          on('2099-000007-CA-01') and _cache_now['2099-000007-CA-01'].get('a') is True)
    check('closing an OLDER stay does not end the newer one we hold, and leaves no lift date',
          on('2099-000008-CA-01') and not after_sh['2099-000008-CA-01'].get('sale_stay_lifted')
          and _cache_now['2099-000008-CA-01'].get('a') is True and not _cache_now['2099-000008-CA-01'].get('sl'))
    check('an older stay closing does not end the one we hold when its date is slash-form',
          on('2099-000010-CA-01') and _cache_now['2099-000010-CA-01'].get('a') is True)
    check('a NEWER case closing does not end the older stay we hold (no close between the two filings)',
          on('2099-000011-CA-01') and _cache_now['2099-000011-CA-01'].get('a') is True
          and not after_sh['2099-000011-CA-01'].get('sale_stay_lifted'))
    check('when both the held stay and a newer one show a closing line, the stay ends',
          not on('2099-000012-CA-01') and _cache_now['2099-000012-CA-01'].get('a') is False
          and _cache_now['2099-000012-CA-01'].get('sl') == '2026-09-15')
    check('two cases filed the same day: the other one closing does not end ours',
          on('2099-000013-CA-01') and _cache_now['2099-000013-CA-01'].get('a') is True
          and not after_sh['2099-000013-CA-01'].get('sale_stay_lifted'))
    check('the held case dismissed AFTER a newer filing (its number cited) ends the stay once both closed',
          not on('2099-000014-CA-01') and _cache_now['2099-000014-CA-01'].get('a') is False
          and _cache_now['2099-000014-CA-01'].get('sl') == '2026-09-15')
    check('numbered lines: only the newer case closing does not end the older stay we hold',
          on('2099-000015-CA-01') and _cache_now['2099-000015-CA-01'].get('a') is True)
    check('two cases filed the same day, each with its own closing line: the stay ends',
          not on('2099-000016-CA-01') and _cache_now['2099-000016-CA-01'].get('a') is False)
    check('numberless closes after a newer filing cannot be tied to ours: the stay is kept',
          on('2099-000017-CA-01') and _cache_now['2099-000017-CA-01'].get('a') is True)
    check('the held case closing late does not end a NEWER case still open',
          on('2099-000018-CA-01') and _cache_now['2099-000018-CA-01'].get('a') is True
          and not after_sh['2099-000018-CA-01'].get('sale_stay_lifted'))
    check('a case reinstated after its dismissal is active again',
          on('2099-000019-CA-01') and _cache_now['2099-000019-CA-01'].get('a') is True)
    check('a sale cancelled per bankruptcy is the same case acting; its dismissal ends the stay',
          not on('2099-000020-CA-01') and _cache_now['2099-000020-CA-01'].get('sl') == '2026-06-01')
    check('two petition lines on one day read as one case; its dismissal ends the stay',
          not on('2099-000021-CA-01') and _cache_now['2099-000021-CA-01'].get('sl') == '2026-06-01')
    check('with no stay held yet, an older case closing late does not hide a newer open one',
          on('2099-000022-CA-01') and after_sh['2099-000022-CA-01'].get('sale_bk_date') == '2026-08-01')
    check('a case filed between ours and the newest, still open, keeps the stay',
          on('2099-000023-CA-01') and _cache_now['2099-000023-CA-01'].get('a') is True)
    check('an older case acting after a newer filing stays that older case; its close does not hide the newer one',
          on('2099-000024-CA-01'))
    check('a read that no longer shows the held filing never ends the stay, even when the cases around it closed',
          on('2099-000025-CA-01') and _cache_now['2099-000025-CA-01'].get('a') is True)
    check('an older case reinstated after the newer one closed is active again',
          on('2099-000026-CA-01') and _cache_now['2099-000026-CA-01'].get('a') is True)
    # DEFECT 9 (12-case verification 2026-09-24): bankruptcy orders reach the state docket as
    # "Notice of Filing: ..." and name the chapter, not the word bankruptcy. The shapes it takes:
    NOF = lambda d, t, c='': {'docketDescrition': 'Notice of Filing: ' + t, 'eventDate': d, 'comments': c}
    _stay = lambda *rows: SH._bk_stay(list(rows))
    check('a chapter 13 dismissal filed as a Notice of Filing ends the stay',
          _stay(BK('01/10/2024', '23-17967'), NOF('01/20/2024', 'Order Dismissing Chapter 13 Case'))[0] is False)
    check('a chapter 13 REINSTATEMENT after that dismissal makes the stay active again',
          _stay(BK('01/10/2024', '23-17967'), NOF('01/20/2024', 'Order Dismissing Chapter 13 Case'),
                NOF('01/31/2024', 'Order Reinstating Chapter 13 Case'))[0] is True)
    check('a reinstatement naming only the federal case number counts too',
          _stay(BK('01/10/2024', '23-17967'), NOF('01/20/2024', 'Order Dismissing Case 23-17967'),
                NOF('01/31/2024', 'Order Granting Motion to Reinstate Case 23-17967'))[0] is True)
    check('a dismissal naming only the federal case number ends that stay',
          _stay(BK('01/10/2024', '23-17967'), NOF('01/20/2024', 'Order Dismissing Case 23-17967'))[0] is False)
    check('an order vacating the dismissal and reinstating the case is a reinstatement, not a dismissal',
          _stay(BK('01/10/2024', '23-17967'), NOF('01/20/2024', 'Order Dismissing Chapter 13 Case'),
                NOF('01/31/2024', 'Order Vacating Dismissal and Reinstating Chapter 13 Case'))[0] is True)
    # The clerk's own entries on 2018-026274-CA-01 (events 209208510 and 209744732, code NFILCV),
    # copied verbatim from the raw docket by the desktop session: the description is the bare
    # "Notice of Filing:" and the order is only in the comments. The first comment never says
    # "bankruptcy", so only the chapter words find it.
    RAW = lambda d, c: {'docketCode': 'NFILCV', 'docketDescrition': 'Notice of Filing:', 'eventDate': d, 'comments': c}
    _dismissed = RAW('01/12/2024', 'ORDER DENYING CONFIRMATION AND DISMISSING CHAPTER 13 CASE')
    _reinstated = RAW('02/02/2024', 'copy Order Reinstating Chapter 13 Bankruptcy')
    check("the clerk's verbatim chapter 13 dismissal notice ends the stay",
          _stay(BK('06/15/2023', '23-17967'), _dismissed)[0] is False)
    check("the clerk's verbatim reinstatement notice brings the stay back",
          _stay(BK('06/15/2023', '23-17967'), _dismissed, _reinstated)[0] is True)
    check('a voluntary chapter 13 petition filed as a Notice of Filing opens a stay',
          _stay(NOF('02/01/2026', 'Voluntary Petition Chapter 13'))[0] is True)
    check('a bare dismissal of a defendant never ends a bankruptcy stay',
          _stay(BK('02/01/2026'), CLOSE('03/01/2026', 'Voluntary Dismissal as to Defendant Unknown Tenant'))[0] is True)
    # 2025-012246-CA-01 (sweep of all Miami leads, 2026-09-24), verbatim from the fresh docket: the
    # foreclosure court's agreed order DENYING a motion to dismiss the foreclosure, with no bankruptcy
    # number on it, read as the stay's dismissal and left a 10-05 sale callable under a live petition.
    _sg = lambda c: {'docketCode': 'SGBK', 'docketDescrition': 'Suggestion of Bankruptcy', 'eventDate': '09/30/2025', 'comments': c}
    check('an order denying a motion to dismiss the foreclosure never ends a bankruptcy stay',
          _stay(_sg('AMENDED BKC: 25-20935-RAM'), _sg('NO BANKRUPTCY CASE NUMBER'),
                {'docketCode': 'NCHRCV', 'docketDescrition': 'Notice of Cancellation of Hearing', 'eventDate': '02/17/2026', 'comments': ''},
                {'docketCode': 'ODMDCV', 'docketDescrition': 'Order Denying Motion to Dismiss', 'eventDate': '02/17/2026',
                 'comments': 'AGREED ORDER DENYING MOTION TO DISMISS'})[0] is True)
    check('a MORTGAGE reinstatement is not a bankruptcy',
          _stay({'docketDescrition': 'Emergency Motion to Cancel Sale', 'eventDate': '09/24/2026',
                 'comments': 'reinstatement amount 230283.71'}) == (False, '', ''))
    check('relief from the automatic stay still ends it with no bankruptcy word on the line',
          _stay(BK('02/01/2026'), CLOSE('03/01/2026'))[0] is False)
    # NEAR SALES (sweep of all Miami leads, 2026-09-24): suggestions of bankruptcy filed 09-22 to
    # 09-24 on 09-28 sales, under a 7-day TTL a read from the week before stood until the auction.
    _nt = tempfile.mkdtemp()
    _now = __import__('time').time()
    _ad = lambda days: __import__('time').strftime('%m/%d/%Y', __import__('time').localtime(_now + days * 86400))
    _old = {'s': 0, 'n': 1, 'd': 0, 'w': '', 'b': 0, 'a': False, 'bd': '', 'sl': '', 't': _now - 3 * 86400, 'v': SH.CACHE_VER}
    json.dump({'2099-000031-CA-01': dict(_old), '2099-000032-CA-01': dict(_old)},
              open(os.path.join(_nt, 'sale_history_cache.json'), 'w'))
    json.dump([{'Case #': '2099-000032-CA-01', 'AuctionDate': _ad(30)},       # far sale, fresh read
               {'Case #': '2099-000033-CA-01', 'AuctionDate': _ad(40)},       # far sale, never read
               {'Case #': '2099-000031-CA-01', 'AuctionDate': _ad(4)},        # near sale, fresh read
               {'Case #': '2099-000034-CA-01', 'AuctionDate': _ad(5)}],       # near sale, never read
              open(os.path.join(_nt, 'leads_final.json'), 'w'))
    _fetched = []
    _sh = {k: getattr(SH, k) for k in ('HERE', 'CACHE', '_fetch', 'time')}
    try:
        SH.HERE = _nt; SH.CACHE = os.path.join(_nt, 'sale_history_cache.json')
        SH._fetch = lambda session, case: _fetched.append(case) or [BK(_ad(-1)[:10])]
        SH.time = types.SimpleNamespace(time=__import__('time').time, sleep=lambda s: None)
        sys.argv = ['sale_history.py', '--limit', '2']
        SH.main()
    finally:
        for k, v in _sh.items():
            setattr(SH, k, v)
        sys.argv = _argv
    _near = {r['Case #']: r for r in json.load(open(os.path.join(_nt, 'leads_final.json')))}
    check('a near sale is re-read although its cached read is 3 days old, and shows the new stay',
          '2099-000031-CA-01' in _fetched and _near['2099-000031-CA-01'].get('sale_bk_active') is True, _fetched)
    check('a far sale keeps its 3-day-old read (7-day TTL)', '2099-000032-CA-01' not in _fetched, _fetched)
    check('near sales are fetched before the --limit budget reaches distant ones',
          sorted(_fetched) == ['2099-000031-CA-01', '2099-000034-CA-01'], _fetched)
    check('the leads file keeps its order', list(_near) == ['2099-000032-CA-01', '2099-000033-CA-01',
                                                              '2099-000031-CA-01', '2099-000034-CA-01'], list(_near))
    check('a new active stay drops a stale lift date from the row (gates read it as "contact is legal")',
          on('2099-000009-CA-01') and not after_sh['2099-000009-CA-01'].get('sale_stay_lifted'))
    rebuilt = list(after_sh.values())
    F.restore_stays_from_cache(rebuilt)
    check('the rebuild does not re-activate the lifted stay from the cache',
          [r['Case #'] for r in rebuilt if r.get('sale_bk_active')] == ['2099-000002-CA-01', '2099-000005-CA-01', '2099-000006-CA-01', '2099-000007-CA-01',
                                                                   '2099-000008-CA-01', '2099-000009-CA-01', '2099-000010-CA-01',
                                                                   '2099-000011-CA-01', '2099-000013-CA-01', '2099-000015-CA-01',
                                                                   '2099-000017-CA-01', '2099-000018-CA-01', '2099-000019-CA-01', '2099-000023-CA-01',
                                                                   '2099-000022-CA-01', '2099-000024-CA-01', '2099-000025-CA-01',
                                                                   '2099-000026-CA-01'],
          [r['Case #'] for r in rebuilt if r.get('sale_bk_active')])
    os.remove(os.path.join(tmp, 'sale_history_cache.json'))
    check('no cache file: nothing restored, nothing raised', F.restore_stays_from_cache([{'Case #': 'x'}]) == 0)
finally:
    F.HERE = _here

src = open(os.path.join(HERE, 'foreclosure_leads.py'), encoding='utf-8').read()
main_src = src[src.index('\ndef main('):]
_dump = main_src.index("json.dump(leads, open(os.path.join(HERE,'leads_final.json')")
check('main() restores the stays BEFORE it writes leads_final.json',
      0 <= main_src.rfind('restore_stays_from_cache(leads)', 0, _dump))
mt = src[src.index('\ndef make_tracker('):src.index('\n    slim = []\n', src.index('\ndef make_tracker('))]
check('make_tracker still restores them for the board (runs that skip main)',
      'restore_stays_from_cache(leads)' in mt)
check('one copy of the field mapping, not two', src.count("r['sale_bk_active'] = True") == 1,
      src.count("r['sale_bk_active'] = True"))

print('\n%s: %d failure(s)' % ('FAIL' if FAILS else 'OK', len(FAILS)))
sys.exit(1 if FAILS else 0)
