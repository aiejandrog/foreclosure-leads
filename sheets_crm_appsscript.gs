/**
 * DealFlow CRM receiver — paste into Extensions > Apps Script of the team's Google Sheet.
 *
 * Receives the nightly push from sheets_crm.py and rewrites the "DealFlow" tab.
 * That tab is a VIEW of the pipeline — anything typed on it is erased on the next push.
 * Team notes/claims go on other tabs (e.g. "Working"), which this script never touches.
 */
var TAB = 'DealFlow';

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);                      // pushes are rare; just serialize them
  try {
    var body = JSON.parse(e.postData.contents);
    if (!body || !body.headers || !body.rows) {
      return ContentService.createTextOutput('bad payload');
    }
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName(TAB) || ss.insertSheet(TAB, 0);
    sh.clearContents();
    var all = [body.headers].concat(body.rows);
    // normalize row widths so setValues never throws on a ragged row
    var w = body.headers.length;
    for (var i = 0; i < all.length; i++) {
      while (all[i].length < w) all[i].push('');
      if (all[i].length > w) all[i] = all[i].slice(0, w);
    }
    sh.getRange(1, 1, all.length, w).setValues(all);
    sh.getRange(1, 1, 1, w).setFontWeight('bold');
    sh.setFrozenRows(1);
    sh.getRange(1, w + 2).setValue('Updated ' + (body.stamp || new Date()));
    return ContentService.createTextOutput('ok ' + body.rows.length);
  } catch (err) {
    return ContentService.createTextOutput('error: ' + err);
  } finally {
    lock.releaseLock();
  }
}
