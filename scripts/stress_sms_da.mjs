#!/usr/bin/env node
/**
 * Adversarial stress test: Text/Phone Link + Call Sheet property hub
 * (Deal Analyzer demoted — Math lives on Call Sheet; no duplicate CONTACT/CLOCK).
 */
import fs from 'fs';
import path from 'path';
import os from 'os';
import { createRequire } from 'module';

const require = createRequire('/tmp/pup/package.json');
const puppeteer = require('puppeteer-core');

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const tpl = fs.readFileSync(path.join(root, 'tracker_template.html'), 'utf8');

const fixtures = [
  {
    case: 'FC-CLEAN-1', st: 'FC', tier: 'A', owners: 'SMITH JOHN A', addr: '100 Test St, Miami, FL',
    auction: '08/15/2026', days: 21, judg: 180000, value: 420000, plaintiff: 'BANK OF AMERICA NA',
    phones: ['3055551212'], phdnc: [false], phtype: ['mobile'], auc: 'https://example.com/auc/1',
    tax: 'https://example.com/tax/1', defs: 'MIAMI-DADE COUNTY; JOHN DOE',
    orliens: [
      { party: 'BANK OF AMERICA NA', amt: 200000, d: '01/05/2018', st: 'OPEN', bp: '1/2' },
      { party: 'OLD LENDER LLC', amt: 50000, d: '06/01/2010', st: 'SATISFIED', bp: '3/4' }
    ],
    orconf: 'ok', orsurv: 0, saleSurv: 0, _play: { t: 'BUY', w: 'Equity + contactable — buy direct.' }
  },
  {
    case: 'TD-TAX-1', st: 'TD', tier: 'A', owners: 'GARCIA MARIA', addr: '200 Tax Ave, Hialeah, FL',
    auction: '08/01/2026', days: 7, judg: 12450, value: 310000, plaintiff: 'MIAMI-DADE COUNTY',
    phones: ['7865559999'], phdnc: [false], phtype: ['mobile'], tax: 'https://example.com/tax/2',
    _play: { t: 'AUCTION', w: 'Tax deed — verify surplus / walk.' }
  },
  {
    case: 'LP-FRESH-1', st: 'LP', tier: 'A', owners: 'LEE PAT', addr: '300 Fresh Rd, Miami, FL',
    filed: '07/20/2026', filedDate: '07/20/2026', days: 9999, judg: 0, value: 0,
    plaintiff: 'WELLS FARGO BANK NA', phones: [], phdnc: [],
    _play: { t: 'LP-EARLY', w: 'Be first contact.' }
  },
  {
    case: 'FC-DNC-1', st: 'FC', tier: 'A', owners: 'DNC OWNER', addr: '400 Dnc St, Miami, FL',
    auction: '09/01/2026', days: 40, judg: 100000, value: 250000, plaintiff: 'US BANK',
    phones: ['3055550001'], phdnc: [true], phtype: ['mobile']
  },
  {
    case: 'FC-BK-1', st: 'FC', tier: 'A', owners: 'BK OWNER', addr: '500 Bk St, Miami, FL',
    auction: '09/10/2026', days: 50, judg: 90000, value: 200000, plaintiff: 'CHASE',
    phones: ['3055550002'], phdnc: [false], phtype: ['mobile'], saleBkAct: true
  },
  {
    case: 'FC-NOPHONE-1', st: 'FC', tier: 'A', owners: 'NO PHONE', addr: '600 Quiet St, Miami, FL',
    auction: '10/01/2026', days: 70, judg: 110000, value: 300000, plaintiff: 'NATIONSTAR',
    phones: [], phdnc: []
  },
  // Jose insight scenario (plan): HOA foreclosure, tiny judgment vs big value → fantasy equity until senior checked.
  {
    case: '2023-000212-CA-01', st: 'FC', tier: 'A', owners: 'MARIA C GONZALEZ', addr: '1450 SW 27TH AVE, MIAMI, FL 33145',
    auction: '08/03/2026', days: 20, filed: 2023, judg: 41000, value: 720000, eq: 94, eqfake: true, hs: true,
    ctype: 'HOA/Condo', plaintiff: 'Brickell Bay Condominium Association, Inc.',
    defs: 'Maria C Gonzalez; Mortgage Electronic Registration Systems',
    phones: ['3055550140'], phdnc: [false], phtype: ['mobile'], tax: 'https://example.com/tax/maria',
    _play: { t: 'RESEARCH', w: 'HOA judgment tiny vs value — senior not checked.' }
  }
];

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'stress-sms-da-'));
const pagePath = path.join(dir, 'index.html');
let html = tpl.replace('const RAW = __DATA__;', 'const RAW = ' + JSON.stringify(fixtures) + ';');
fs.writeFileSync(pagePath, html);

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME || '/usr/bin/google-chrome-stable',
  headless: true,
  args: [
    '--no-sandbox', '--disable-gpu', '--allow-file-access-from-files',
    '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check'
  ],
  userDataDir: fs.mkdtempSync(path.join(os.tmpdir(), 'chrome-stress-'))
});

const page = await browser.newPage();
page.on('pageerror', e => console.error('PAGEERROR', e.message));
await page.goto('file://' + pagePath, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => typeof dealModalBody === 'function' && typeof _callSheet === 'function' && typeof textablePhones === 'function', { timeout: 15000 });

const result = await page.evaluate(() => {
  const fails = [];
  const ok = [];
  function assert(name, cond, detail){
    (cond ? ok : fails).push(detail ? name + ' — ' + detail : name);
  }
  function sectionOrder(html, labels){
    let last = -1;
    for(const lab of labels){
      const i = html.indexOf(lab);
      if(i < 0) return {ok:false, missing: lab};
      if(i < last) return {ok:false, missing: lab + ' (out of order)'};
      last = i;
    }
    return {ok:true};
  }
  try {
    notes['OPT-LEDGER'] = { optout: '2026-07-01', optlog: [{act:'set-local', ts:'2026-07-01 10:00:00', tsu: Date.parse('2026-07-01T14:00:00Z')}] };
    DATA.push({
      case:'OPT-LEDGER', st:'FC', tier:'A', owners:'LEDGER OPT', addr:'700 Opt St, Miami, FL',
      auction:'11/01/2026', days:90, judg:50000, value:150000, plaintiff:'BANK',
      phones:['3055557777'], phdnc:[false], phtype:['mobile']
    });
    notes['WRONG-1'] = { wrongown:'said not owner', optlog:[{act:'wrong-owner', ts:'2026-07-02 10:00:00', tsu: Date.now()}] };
    DATA.push({
      case:'WRONG-1', st:'FC', tier:'A', owners:'WRONG PERSON', addr:'800 Wrong St, Miami, FL',
      auction:'11/05/2026', days:95, judg:60000, value:180000, plaintiff:'BANK',
      phones:['3055558888'], phdnc:[false], phtype:['mobile']
    });
    notes['SAVED-DNT'] = { phone: '3055553333' };
    DATA.push({
      case:'SAVED-DNT', st:'FC', tier:'A', owners:'SAVED DNT', addr:'900 Saved St, Miami, FL',
      auction:'11/10/2026', days:100, judg:70000, value:190000, plaintiff:'BANK',
      phones:[], phdnc:[]
    });
    _addDNT('3055553333');
    if(typeof recompute === 'function') recompute();

    // ---- Property hub: CS has clock/debt/play; Math (dealModalBody) does NOT duplicate them ----
    ['FC-CLEAN-1','TD-TAX-1','LP-FRESH-1','FC-BK-1'].forEach(c => {
      const r = DATA.find(x => x.case === c);
      const clock = _clockBodyHtml(r);
      const math = dealModalBody(r);
      const cs = _callSheet(r);
      assert(c+': clock in CS', cs.indexOf(clock) >= 0);
      assert(c+': Math has NO clock body', math.indexOf(clock) < 0);
      assert(c+': Math has NO THE CLOCK sec', math.indexOf('THE CLOCK') < 0);
      assert(c+': Math has NO Contact — Call', math.indexOf('Contact — Call · Text · WA') < 0);
      assert(c+': Math has NO Open full call sheet', math.indexOf('Open full call sheet') < 0);
      assert(c+': Math has NO Debt stack sec', math.indexOf('Debt stack — same as call sheet') < 0);
      const debt = _debtSheetBody(r);
      if(r.st === 'LP'){
        assert(c+': LP debt empty', debt === '');
        assert(c+': LP CS no THE DEBT', cs.indexOf('THE DEBT — who is owed what') < 0);
      } else {
        assert(c+': debt non-empty', !!debt);
        assert(c+': debt in CS', cs.indexOf(debt) >= 0);
        assert(c+': CS has THE DEBT', cs.indexOf('THE DEBT — who is owed what') >= 0);
        assert(c+': Math has no debt body', math.indexOf(debt) < 0);
      }
      const play = _csPlay(r);
      if(play){
        assert(c+': PLAY html in CS', cs.indexOf(play) >= 0);
        // Math may show play badge in verdict line, but not the full _csPlay block
        assert(c+': full PLAY block not duplicated in Math', math.indexOf(play) < 0);
      }
      assert(c+': CS has Math details', cs.indexOf('id="csmath"') >= 0 || r._nodata);
      assert(c+': CS Math collapsed by default', cs.indexOf('id="csmath" open') < 0);
      assert(c+': CS has Ask Jose footer', cs.indexOf('Ask Jose') >= 0);
      assert(c+': CS has LIVE footer', cs.indexOf('LIVE — alert Jose') >= 0);
      if(r.st !== 'LP' && r.st !== 'TD'){
        assert(c+': CS rule essay collapsed', cs.indexOf('cslaw-wrap') >= 0 && cs.indexOf('cslaw-wrap" open') < 0);
      }
    });

    const td = DATA.find(x => x.case === 'TD-TAX-1');
    assert('TD CS has Back taxes', _callSheet(td).indexOf('Back taxes') >= 0);
    assert('TD Math has underwrite', dealModalBody(td).indexOf('Profit paths') >= 0 || dealModalBody(td).length > 40);

    // Section order on Call Sheet hub
    const clean = DATA.find(x => x.case === 'FC-CLEAN-1');
    const cleanCs = _callSheet(clean);
    const ord = sectionOrder(cleanCs, [
      'CONTACT — Call · Text · WA',
      'THE CLOCK',
      'THE DEBT — who is owed what',
      'id="csmath"',
      'THE PROPERTY',
      'csfoot'
    ]);
    assert('CS section order', ord.ok, ord.missing || '');

    // openDealModal → Call Sheet + Math expanded; no dealmodal.show
    openDealModal('FC-CLEAN-1');
    const csModal = document.getElementById('csmodal');
    const dealModal = document.getElementById('dealmodal');
    assert('openDealModal shows CS', csModal && getComputedStyle(csModal).display !== 'none');
    assert('openDealModal no DA modal', !dealModal || !dealModal.classList.contains('show'));
    const mathEl = document.getElementById('csmath');
    assert('Math expanded via openDealModal', mathEl && mathEl.open);
    const hubHtml = document.getElementById('csbody').innerHTML;
    assert('hub has CONTACT once', (hubHtml.match(/CONTACT — Call · Text · WA/g) || []).length === 1);
    assert('hub has THE CLOCK once', (hubHtml.match(/THE CLOCK/g) || []).length === 1);
    assert('hub Math has no Contact dup', (document.querySelector('#csmath') || {innerHTML:''}).innerHTML.indexOf('Contact — Call · Text · WA') < 0);
    closeCallSheet();

    // Board Math label
    tier = 'ALL';
    if(typeof render === 'function') render();
    assert('board Math button', (document.body.innerHTML || '').indexOf('>Math</button>') >= 0);
    assert('board no Deal analysis label', (document.body.innerHTML || '').indexOf('>Deal analysis</button>') < 0);

    assert('clean textable', textablePhones(clean).length === 1);
    const href = _smsHref(textablePhones(clean)[0], smsMsg(clean, 'both', 'urgent'));
    assert('sms: href', /^sms:\+13055551212\?body=/.test(href), href.slice(0,100));
    assert('body has STOP', decodeURIComponent(href.split('body=')[1]||'').indexOf('STOP') >= 0);

    openTextSingle(clean);
    let tb = document.getElementById('textbody').innerHTML;
    const hoursOK = _withinTextHours();
    if(hoursOK){
      assert('in-hours sms send', tb.indexOf('class="txsend"') >= 0 && tb.indexOf('sms:') >= 0);
      assert('in-hours WA send', tb.indexOf('class="txwa"') >= 0);
    } else {
      assert('off-hours copy allowed', tb.indexOf('txcopy') >= 0);
      assert('off-hours sms gated', tb.indexOf('class="txsend"') < 0);
      assert('off-hours WA gated', tb.indexOf('class="txwa"') < 0);
    }
    closeTextModal();

    const np = DATA.find(x => x.case === 'FC-NOPHONE-1');
    assert('nophone textable empty', textablePhones(np).length === 0);
    assert('nophone not blocked', !_textContactBlocked(np));
    assert('nophone CS Text btn', _callSheet(np).indexOf('cstextbtn') >= 0);

    const dnc = DATA.find(x => x.case === 'FC-DNC-1');
    assert('dnc textable empty', textablePhones(dnc).length === 0);
    openTextSingle(dnc);
    tb = document.getElementById('textbody').innerHTML;
    assert('dnc no sms send', tb.indexOf('sms:+1') < 0 && tb.indexOf('href="sms:') < 0);
    closeTextModal();

    const bk = DATA.find(x => x.case === 'FC-BK-1');
    assert('bk blocked', _textContactBlocked(bk) === 'bk');
    assert('bk textable empty', textablePhones(bk).length === 0);
    assert('bk CS no Text btn', _callSheet(bk).indexOf('cstextbtn') < 0);
    openTextSingle(bk);
    tb = document.getElementById('textbody').innerHTML;
    assert('bk no copy', tb.indexOf('txcopy') < 0);
    assert('bk no wa send', tb.indexOf('class="txwa"') < 0);
    assert('bk gate', tb.indexOf('BANKRUPTCY') >= 0);
    closeTextModal();

    const opt = DATA.find(x => x.case === 'OPT-LEDGER');
    assert('ledger opt-out isOptedOut', _isOptedOut(notes['OPT-LEDGER']));
    assert('ledger blocked', _textContactBlocked(opt) === 'optout');
    assert('ledger textable empty', textablePhones(opt).length === 0);

    const wr = DATA.find(x => x.case === 'WRONG-1');
    assert('wrong blocked', _textContactBlocked(wr) === 'wrong');
    assert('wrong CS no Text', _callSheet(wr).indexOf('cstextbtn') < 0);

    const sd = DATA.find(x => x.case === 'SAVED-DNT');
    assert('saved DNT not textable', textablePhones(sd).length === 0);

    openBulkText();
    assert('bulk excludes BK', textQ.indexOf('FC-BK-1') < 0);
    assert('bulk excludes opt ledger', textQ.indexOf('OPT-LEDGER') < 0);
    assert('bulk excludes wrong', textQ.indexOf('WRONG-1') < 0);
    assert('bulk includes clean', textQ.indexOf('FC-CLEAN-1') >= 0);
    closeTextModal();

    // Structured CONTACT: Call · Text · WA on CS + board
    assert('CS has Call button', cleanCs.indexOf('ctact-call') >= 0);
    assert('CS has Text button', cleanCs.indexOf('ctact-text') >= 0 || cleanCs.indexOf('Messages') >= 0);
    assert('CS has WA button', cleanCs.indexOf('ctact-wa') >= 0);
    assert('CS CONTACT label', cleanCs.indexOf('Call · Text · WA') >= 0);

    const msgBtn = document.getElementById('bulktext');
    assert('toolbar Messages', !!msgBtn && /Messages/i.test(msgBtn.textContent||''));
    assert('board Contact label', (document.body.innerHTML||'').indexOf('Contact — Call · Text · WA') >= 0);
    const smsLinks = document.querySelectorAll('a.textgen[data-c="FC-CLEAN-1"]');
    assert('clean row has Text', smsLinks.length >= 1, 'count='+smsLinks.length);
    assert('clean row has Call', document.querySelectorAll('a.ctact-call[href^="tel:"]').length >= 1);
    const npSms = document.querySelectorAll('a.textgen[data-c="FC-NOPHONE-1"]');
    assert('nophone row has Text', npSms.length >= 1, 'count='+npSms.length);
    const bkSms = document.querySelectorAll('a.textgen[data-c="FC-BK-1"]');
    assert('bk row no Text', bkSms.length === 0, 'count='+bkSms.length);
    const optSms = document.querySelectorAll('a.textgen[data-c="OPT-LEDGER"]');
    assert('opt row no Text', optSms.length === 0, 'count='+optSms.length);

    // ---- Jose insight on Maria (one Call Sheet, no DA) ----
    const maria = DATA.find(x => x.case === '2023-000212-CA-01');
    assert('maria present', !!maria);
    if(typeof recompute === 'function') recompute();
    const mCs = _callSheet(maria);
    assert('maria judgment $41,000', mCs.indexOf('$41,000') >= 0 || mCs.indexOf('$41,000') >= 0 || /\$41,?000/.test(mCs));
    assert('maria worth ~$720k', /\$720,?000/.test(mCs));
    assert('maria survivors not checked', mCs.indexOf('NOT CHECKED') >= 0);
    assert('maria net equity warning', mCs.indexOf('assumes nothing survives') >= 0 || mCs.indexOf('NOT CHECKED') >= 0);
    assert('maria Call·Text·WA', mCs.indexOf('ctact-call') >= 0 && mCs.indexOf('ctact-wa') >= 0);
    assert('maria Math section', mCs.indexOf('id="csmath"') >= 0);
    const mMath = dealModalBody(maria);
    assert('maria Math underwrite present', mMath.length > 80);
    // Expand Math via hub
    openCallSheet(maria.case, {math:true});
    assert('maria hub open', getComputedStyle(document.getElementById('csmodal')).display !== 'none');
    assert('maria Math open', document.getElementById('csmath') && document.getElementById('csmath').open);
    const jose = _joseMsg(maria);
    assert('jose brief addr', jose.indexOf('1450 SW 27TH AVE') >= 0);
    assert('jose brief case', jose.indexOf('2023-000212-CA-01') >= 0);
    assert('jose brief judgment', jose.indexOf('41,000') >= 0 || jose.indexOf('41000') >= 0);
    assert('jose brief senior', /Surviving senior mortgage:/i.test(jose));
    assert('jose brief net', /Net equity after the stack:/i.test(jose));
    assert('jose brief verdict', /Tracker verdict:/i.test(jose));
    assert('jose brief deep link', jose.indexOf('#case=') >= 0);
    closeCallSheet();

  } catch (e) {
    fails.push('EXCEPTION: ' + (e && e.stack ? e.stack : String(e)));
  }
  return { ok, fails, pass: fails.length === 0, nOk: ok.length, nFail: fails.length, hoursOK: (typeof _withinTextHours==='function') && _withinTextHours() };
});

await browser.close();
console.log(JSON.stringify(result, null, 2));
if(!result.pass){
  console.error('\nFAILED', result.nFail);
  process.exit(1);
}
console.error('\nPASS', result.nOk, 'assertions; hoursOK=', result.hoursOK);
