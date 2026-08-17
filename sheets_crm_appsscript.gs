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

    // PER-PROSPECT TABS — one tab per consult-stage lead, full workup, rewritten every push.
    // A tab whose prospect left the consult list is KEPT (the team may have written on it) but
    // stamped inactive. Tab names come from the pipeline pre-sanitized and deduped.
    (body.prospects || []).forEach(function (p) {
      if (!p || !p.tab || !p.rows || !p.rows.length) return;
      var ps = ss.getSheetByName(p.tab) || ss.insertSheet(p.tab);
      ps.clearContents();
      var rows2 = p.rows.map(function (r2) {
        while (r2.length < 2) r2.push('');
        return r2.slice(0, 2);
      });
      rows2.unshift(['Updated ' + (body.stamp || ''), p.case || '']);
      ps.getRange(1, 1, rows2.length, 2).setValues(rows2);
      ps.setColumnWidth(1, 150); ps.setColumnWidth(2, 420);
      for (var i2 = 0; i2 < rows2.length; i2++) {
        if (String(rows2[i2][0]).indexOf('—') === 0)
          ps.getRange(i2 + 1, 1, 1, 2).setFontWeight('bold').setBackground('#eef1f6');
      }
      ps.setTabColor('#C6A14B');
    });
    // stamp tabs that stopped updating (prospect no longer consult-stage)
    var live = {};
    (body.prospects || []).forEach(function (p) { if (p && p.tab) live[p.tab] = 1; });
    ss.getSheets().forEach(function (s2) {
      var nm = s2.getName();
      if (nm === TAB || live[nm]) return;
      if (s2.getTabColor() === '#c6a14b' || s2.getTabColor() === '#C6A14B')
        s2.setTabColor('#8a94a8');
    });
    return ContentService.createTextOutput('ok ' + body.rows.length + ' rows, ' + (body.prospects || []).length + ' prospect tab(s)');
  } catch (err) {
    return ContentService.createTextOutput('error: ' + err);
  } finally {
    lock.releaseLock();
  }
}
