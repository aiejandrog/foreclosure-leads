/**
 * bsg_lead_webhook.gs — lead intake for the BSG landing page (Hostinger form -> here).
 *
 * WHERE THIS LIVES: inside the Team CRM spreadsheet itself. Open the CRM sheet ->
 * Extensions -> Apps Script -> paste this file -> Deploy -> New deployment -> Web app ->
 * "Execute as: Me", "Who has access: Anyone" -> copy the /exec URL. Binding it to the
 * sheet is what makes it zero-config: SpreadsheetApp.getActive() needs no ID, so this
 * file holds no identifier worth protecting and is safe in the public repo. The only
 * secret is the shared token, which lives in Script Properties, never in code:
 *   Project Settings -> Script Properties -> add  BSG_FORM_TOKEN = <any long random string>
 * and put the same string in the form embed's TOKEN constant on Hostinger.
 *
 * WHY A TOKEN AT ALL: the web app URL is effectively public once it ships in page JS.
 * The token doesn't make it secret — it makes junk POSTs cheap to drop, and it means a
 * scraped URL alone can't fill the CRM with garbage. The honeypot field catches the
 * dumber bots before that.
 *
 * WHAT IT DOES with a valid POST:
 *   1. Appends a row to the "Leads" tab (created on first hit — nothing to pre-build).
 *   2. Emails the lead to the team INSTANTLY. Speed-to-lead is the whole point of a
 *      web form: an inbound caller is 80-90% close per Jesse; an inbound form lead
 *      decays by the hour. The email subject carries situation + city so triage
 *      happens from the phone's lock screen.
 *
 * The nightly sheets_crm.py push REPLACES the "DealFlow" tab wholesale — that is why
 * leads get their OWN tab here and never touch "DealFlow".
 */

var LEADS_TAB = 'Leads';
// Team inbox rule (memory: never infer teammate addresses): celusa13 + Alejandro only.
var NOTIFY = 'celusa13@gmail.com,agonzalez0311707@gmail.com';

function doPost(e) {
  try {
    var p = (e && e.parameter) || {};

    // Honeypot: the embed renders a visually-hidden "company" field humans never fill.
    if (p.company) return _json({ok: true});   // lie to the bot, log nothing

    var want = PropertiesService.getScriptProperties().getProperty('BSG_FORM_TOKEN') || '';
    if (!want || p.token !== want) return _json({ok: false, error: 'bad token'});

    var name = _s(p.name), phone = _s(p.phone), addr = _s(p.address);
    if (!name || !phone) return _json({ok: false, error: 'name and phone required'});

    var row = [
      new Date(),                    // A timestamp
      name,                          // B
      phone,                         // C
      addr,                          // D property address
      _s(p.situation),               // E triage lane
      _s(p.owed),                    // F rough amount owed
      _s(p.behind),                  // G months behind
      _s(p.when),                    // H best time to call
      _s(p.lang) || 'en',            // I page language, if the site sends it
      _s(p.source) || 'bsgflorida',  // J which page/campaign
      'NEW'                          // K status — the team works this column
    ];

    var ss = SpreadsheetApp.getActive();
    var sh = ss.getSheetByName(LEADS_TAB);
    if (!sh) {
      sh = ss.insertSheet(LEADS_TAB);
      sh.appendRow(['Received', 'Name', 'Phone', 'Property address', 'Situation',
                    'Owed (approx)', 'Months behind', 'Best time', 'Lang', 'Source', 'Status']);
      sh.setFrozenRows(1);
    }
    sh.appendRow(row);

    MailApp.sendEmail({
      to: NOTIFY,
      subject: '🔔 NEW LEAD · ' + _s(p.situation).slice(0, 40) +
               (addr ? ' · ' + addr.slice(0, 50) : ''),
      body:
        name + '  ·  ' + phone + '\n' +
        (addr ? addr + '\n' : '') +
        'Situation:   ' + _s(p.situation) + '\n' +
        'Owed approx: ' + _s(p.owed) + '\n' +
        'Behind:      ' + _s(p.behind) + ' months\n' +
        'Best time:   ' + _s(p.when) + '\n\n' +
        'CRM row appended to the "' + LEADS_TAB + '" tab.\n' +
        'CALL WITHIN THE HOUR — an inbound lead is the one that closes.'
    });

    return _json({ok: true});
  } catch (err) {
    // Fail loud in the log, soft to the browser — the embed shows the phone number on any error.
    console.error(err);
    return _json({ok: false, error: 'server'});
  }
}

// GET = health check only. Proves the deploy is live without exposing anything.
function doGet() {
  return _json({ok: true, service: 'bsg-lead-webhook'});
}

function _s(v) { return String(v == null ? '' : v).trim().slice(0, 300); }

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
