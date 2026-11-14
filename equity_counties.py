#!/usr/bin/env python3
"""equity_counties.py - the Broward and Palm Beach side of the equity queue (equity_queue.py).

WHY (2026-11-13, Alejandro): "Broward and Palm Beach can do the same ... as miami dade do it for me."
equity_queue.py v2 has a scheduled Claude session copy the WHOLE Miami-Dade name search off the
clerk's page in the real Chrome. This module is the same thing for the other two recorders. The
Miami-Dade path in equity_queue.py is untouched: it dispatches here only for a lead whose `county`
is BROWARD or PALM BEACH.

WHAT WAS MEASURED ON THE LIVE SITES THAT DAY (real Chrome, one probe each)
  Broward   officialrecords.broward.org/AcclaimWeb   disclaimer button, then the Name search. No
            challenge: only the invisible reCAPTCHA badge. The grid shows 500 rows a page; the
            page's own data call (POST Search/GridResults page=N size=500) returns every page, so
            a 982-row name came back whole where the nightly curl read stops at 400.
  PalmBeach erec.mypalmbeachclerk.com (Landmark Web)  disclaimer, then a reCAPTCHA v2 CHECKBOX on
            the search form. Search/ShowCaptcha answered True and a search sent without the box
            ticked came back "Invalid Captcha", zero rows. Nothing here ticks it. A person does.

SAME HAND-OFF AS MIAMI-DADE. `js` prints a page script; the worker runs it in the tab that holds
the search result. The first answer is a header, the rest are checksummed chunks of rows (the
browser tool cuts an answer at about 1,000 characters). `rows` stores ONE search only when every
chunk arrived, every checksum matched, the row count equals what the county said it has, and the
rows belong to the search that was asked for. `finish` hands the stored searches to the county's
own scraper (`broward_liens.py --docs-file`, `palmbeach_liens.py --docs-file`), so the chain
analysis is the one the nightly run uses.

WHAT COUNTS AS A FULL (VERIFIED) READ. Every search on the lead was stored, none hit the county's
row cap, and the lead has a known owner. Anything less is saved TRUNCATED, never as a clear, and
never over a full chain that is already saved. A blocked, challenged or half-copied search stores
nothing at all.

PALM BEACH NEEDS A PERSON. When the checkbox is up the worker runs `captcha` and those leads become
`needs_human_captcha`; they are never read as "no liens". `next --human` hands them out again for a
session where Alejandro is at the screen to tick the box himself.

Rows are packed to fit the answer size:
a repeated type / party / legal / case string is sent once and then as the index of its first use,
and empty fields at the end of a row are left off.
"""
import json
import os
import re
import sys

MD, BROWARD, PB = 'MIAMI-DADE', 'BROWARD', 'PALM BEACH'
HUMAN = 'needs_human_captcha'


def norm(county):
    """Any spelling of a county -> MIAMI-DADE / BROWARD / PALM BEACH. Blank and unknown are Miami-Dade,
    which is what every row written before this module existed means."""
    c = re.sub(r'[^A-Z]', '', str(county or '').upper())
    if c.startswith('BROW'):
        return BROWARD
    if c.startswith('PALM') or c == 'PB':
        return PB
    return MD


def handled(it_or_county):
    c = it_or_county.get('county') if isinstance(it_or_county, dict) else it_or_county
    return norm(c) in (BROWARD, PB)


# ---- the page scripts ---------------------------------------------------------------------------
# One packer for both counties. COLS says how each column travels:
#   'd' text, sent once and after that as the index of that first use (document types included: a
#       list of them in the header would not fit one answer)
#   'r' as is          'm' "/Date(ms)/" sent as the integer ms          't' index into header `types`
_PACK = r"""
  const types = [], dict = new Map();
  const ti = v => { const s = (v == null) ? null : String(v); let i = types.indexOf(s); if(i < 0){ types.push(s); i = types.length - 1; } return i; };
  const di = v => { if(v == null) return null; const s = String(v); if(dict.has(s)) return dict.get(s); dict.set(s, dict.size); return s; };
  const dm = v => { const m = /^\/Date\((-?\d+)\)\/$/.exec(String(v == null ? '' : v)); return m ? Number(m[1]) : (v == null ? null : String(v)); };
  const rows = all.map(r => COLS.map(c => { const v = r[c[0]]; return c[1] === 't' ? ti(v) : c[1] === 'd' ? di(v) : c[1] === 'm' ? dm(v) : (v === undefined ? null : v); }));
  rows.forEach(r => { while(r.length && r[r.length - 1] == null) r.pop(); });
  const cuts = []; let cur = [], len = 0;
  rows.forEach(r => { const L = JSON.stringify(r).length + 1; if(cur.length && len + L > BUD){ cuts.push(cur); cur = []; len = 0; } cur.push(r); len += L; });
  if(cur.length) cuts.push(cur);
"""
_TAIL = r"""
  if(!out){ const E = window.__eq; out = JSON.stringify(Object.assign({hdr: 1, n: E.n, of: E.cuts.length, types: E.types}, E.meta)); }
"""

# One data chunk. Short on purpose: a big name is dozens of these. It reads only what the header
# script left on this page, and a page that was reloaded or searched again has nothing to give.
JS_CHUNK = r"""var out = null; { const K = %(chunk)d, MOD = %(mod)d, E = window.__eq;
if(!E || location.hostname !== %(host)s){ out = JSON.stringify({err: 'run the header script (--chunk -1) on the result page first'}); }
else if(K >= E.cuts.length){ out = JSON.stringify({err: 'no such chunk', of: E.cuts.length}); }
else { const part = E.cuts[K];
  let s = 0; part.forEach(r => { const c = r.map(v => v == null ? '' : String(v)).join('|'); for(let i = 0; i < c.length; i++) s = (s * 31 + c.charCodeAt(i)) %% MOD; });
  out = JSON.stringify({k: K, of: E.cuts.length, sum: s, rows: part}); } }
out"""

# Broward: the grid's own data call, every page. The header is never served from memory: K = -1
# always asks the county again, so a header can not describe an earlier search.
JS_BROWARD = (r"""var out = null; { const K = -1, BUD = %(budget)d, CAP = %(max)d;
const COLS = %(cols)s;
const box = document.querySelector('[name=SearchOnName]');
const shown = e => !!(e && e.offsetParent && e.offsetWidth > 100 && e.offsetHeight > 100);
if(!/\/AcclaimWeb\/Search\/SearchTypeName/i.test(location.pathname) || !box){ out = JSON.stringify({err: 'not the Broward name search page'}); }
else if([...document.querySelectorAll('iframe')].some(f => /recaptcha\/api2\/bframe|challenges\.cloudflare/.test(f.src || '') && shown(f)) || /just a moment/i.test(document.title)){ out = JSON.stringify({err: 'captcha', what: 'a verification challenge is on the page'}); }
else if(K < 0){
  window.__eq = null;
  const pt = (document.querySelector('[name=PartyType]') || {}).value || '', dt = (document.querySelector('[name="DocTypesDisplay-input"]') || {}).value || (document.querySelector('[name=DocTypesDisplay]') || {}).value || '', bk = (document.querySelector('[name=BookTypesDisplay]') || {}).value || '';
  if(pt !== 'Both' || !/^all$/i.test(dt.trim()) || !/^all$/i.test(bk.trim())){ out = JSON.stringify({err: 'the search form is not on its defaults (Show Names: All, Doc Type: All, Book Type: All): reload the page and search again'}); }
  else if(/Please start your search now/i.test(document.body.innerText || '')){ out = JSON.stringify({err: 'no search has been run on this page yet'}); }
  else {
    const all = []; let total = -1, bad = '';
    for(let p = 1; p <= 40; p++){
      const r = await fetch('/AcclaimWeb/Search/GridResults', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest'}, body: 'page=' + p + '&size=500&sort=&group=&filter='});
      let j = null; try { j = JSON.parse(await r.text()); } catch(e) {}
      if(!r.ok || !j || !Array.isArray(j.data)){ bad = 'the county did not answer the results call (status ' + r.status + ')'; break; }
      total = Number(j.total); j.data.forEach(d => all.push(d));
      if(!j.data.length || all.length >= total || all.length > CAP) break;
    }
    if(bad){ out = JSON.stringify({err: bad}); }
    else if(all.length > CAP){ out = JSON.stringify({err: 'too many rows', total: total}); }
    else {""" + _PACK + r"""
      window.__eq = {n: rows.length, types: types, cuts: cuts, meta: {total: total, from: (document.querySelector('[name=RecordDateFrom]') || {}).value || ''}};
    }
  }
}
""" + _TAIL + r"""}
out""")

# Palm Beach (Landmark Web): the results table's own data call. Never touches the checkbox.
JS_PB = (r"""var out = null; { const K = -1, BUD = %(budget)d, CAP = %(max)d;
const COLS = %(cols)s;
const post = (u, b) => fetch(u, {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest'}, body: b});
if(!/mypalmbeachclerk\.com$/i.test(location.hostname) || !/^\/search\//i.test(location.pathname)){ out = JSON.stringify({err: 'not the Palm Beach search page'}); }
else if(K < 0){
  window.__eq = null;
  const vis = e => !!(e && e.offsetParent);
  const said = [...document.querySelectorAll('div,span,label,p')].some(e => vis(e) && e.children.length === 0 && /invalid captcha/i.test(e.textContent || ''));
  const nrows = document.querySelectorAll('#resultsTable tbody tr').length;
  const gate = /true/i.test(await post('/Search/ShowCaptcha', '').then(r => r.text()).catch(() => ''));
  if(said || (gate && !nrows)){ out = JSON.stringify({err: 'captcha', what: said ? 'the page says Invalid Captcha' : 'the checkbox is required and no result is on the page'}); }
  else {
    const sel = [...document.querySelectorAll('select[id^=numberOfRecords]')].find(vis);
    const cap = sel ? Number(sel.value) : 0;
    const all = []; let total = -1, bad = '';
    for(let s = 0; s < 40; s++){
      let b = 'draw=' + (s + 1) + '&start=' + all.length + '&length=500&search%%5Bvalue%%5D=&search%%5Bregex%%5D=false&order%%5B0%%5D%%5Bcolumn%%5D=0&order%%5B0%%5D%%5Bdir%%5D=desc';
      for(let i = 0; i < 12; i++) b += '&columns%%5B' + i + '%%5D%%5Bdata%%5D=' + i + '&columns%%5B' + i + '%%5D%%5Bname%%5D=&columns%%5B' + i + '%%5D%%5Bsearchable%%5D=true&columns%%5B' + i + '%%5D%%5Borderable%%5D=true&columns%%5B' + i + '%%5D%%5Bsearch%%5D%%5Bvalue%%5D=&columns%%5B' + i + '%%5D%%5Bsearch%%5D%%5Bregex%%5D=false';
      const r = await post('/Search/GetSearchResults', b);
      let j = null; try { j = JSON.parse(await r.text()); } catch(e) {}
      const d = j && (j.data || j.aaData);
      if(!r.ok || !j || !Array.isArray(d)){ bad = 'the county did not answer the results call (status ' + r.status + ')'; break; }
      total = Number(j.recordsTotal == null ? j.recordsFiltered : j.recordsTotal); d.forEach(x => all.push(x));
      if(!d.length || all.length >= total || all.length > CAP) break;
    }
    if(bad){ out = JSON.stringify({err: bad}); }
    else if(all.length > CAP){ out = JSON.stringify({err: 'too many rows', total: total}); }
    else {""" + _PACK + r"""
      window.__eq = {n: rows.length, types: types, cuts: cuts, meta: {total: total, cap: cap}};
    }
  }
}
""" + _TAIL + r"""}
out""")

# column -> how it travels. The order is the order of a packed row.
COLS_BROWARD = (('DocTypeDescription', 'd'), ('Name', 'd'), ('Party', 'd'), ('CrossPartyName', 'd'), ('RecordDate', 'm'),
                ('BookType', 'd'), ('BookPage', 'r'), ('InstrumentNumber', 'r'), ('Consideration', 'r'),
                ('CaseNumber', 'd'), ('DocLegalDescription', 'd'), ('ParcelNumber', 'd'))
# Landmark GetSearchResults columns (palmbeach_liens.py: 4 parcel or matched name, 5 Direct, 6 Reverse,
# 7 record date, 9 doc type, 10 book type, 11 book, 12 page, 13 instrument, 15 legal / case)
COLS_PB = (('9', 'd'), ('4', 'd'), ('5', 'd'), ('6', 'd'), ('7', 'd'), ('10', 'd'), ('11', 'r'), ('12', 'r'), ('13', 'r'), ('15', 'd'))

H = {
    BROWARD: {'js': JS_BROWARD, 'cols': COLS_BROWARD, 'max': 3000, 'script': 'broward_liens.py',
              'host': 'officialrecords.broward.org', 'site': 'https://officialrecords.broward.org/AcclaimWeb/Search/Disclaimer'},
    PB: {'js': JS_PB, 'cols': COLS_PB, 'max': 3000, 'script': 'palmbeach_liens.py',
         'host': 'erec.mypalmbeachclerk.com', 'site': 'https://erec.mypalmbeachclerk.com/'},
}


def _Q():
    import equity_queue as Q
    return Q


def js_for(it, chunk=-1):
    h = H[norm(it.get('county'))]
    Q = _Q()
    if int(chunk) >= 0:
        return JS_CHUNK % {'chunk': int(chunk), 'mod': Q.MOD, 'host': json.dumps(h['host'])}
    return h['js'] % {'budget': Q.BUDGET, 'max': h['max'], 'cols': json.dumps([list(c) for c in h['cols']])}


# ---- what to search for a lead ------------------------------------------------------------------
def _alnum(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _sane_person(q):
    """'LAST, FIRST' / 'LAST FIRST' with two real words. 'ARNOLD, &' and '1471, NW' are not names."""
    parts = [p for p in re.split(r'[,\s]+', str(q or '').upper()) if p]
    return len(parts) >= 2 and all(re.fullmatch(r"[A-Z][A-Z'\-]+", p) for p in parts[:2])


def searches_broward(lead, people=None):
    """-> (searches, owner_known). A lead with an owner is searched the way the nightly searches it
    (broward_liens._search_name). A lead whose owner is not known yet is searched by the people the
    case names as defendants; that read can never be a full one."""
    import broward_liens as B
    owner = (lead.get('owners') or '').strip()
    out = []
    known = bool(owner) and '(owner via' not in owner
    if known:
        q = B._search_name(lead)
        if B.COMPANY_RE.search(owner) or _sane_person(q):
            out.append({'kind': 'name', 'q': q})
        else:
            known = False                                  # an owner string that is not a name
    if not known:
        if people is None:
            try:
                import stub_resolve as _sr
                people = _sr.people_from(lead.get('defs') or '')
            except Exception:
                people = []
        for last, first in list(people)[:4]:
            q = '%s, %s' % (last, first)
            if _sane_person(q) and not any(s['q'] == q for s in out):
                out.append({'kind': 'defendant', 'q': q})
    return out, known


def searches_pb(lead):
    """-> (searches, owner_known): the 17-digit parcel first, then the owner's name, as the nightly does."""
    import palmbeach_liens as PBL
    from records_liens import untraceable_owner
    owner = (lead.get('owners') or '').strip()
    pcn = re.sub(r'\D', '', str(lead.get('folio') or ''))
    out = []
    if len(pcn) >= 14:
        out.append({'kind': 'parcel', 'q': pcn})
    known = bool(owner) and '(owner via' not in owner
    if known and not untraceable_owner(owner):
        q = PBL._name_query(owner)
        if q and (re.search(r'\b(LLC|INC|CORP|LTD|LP|TRUST|ASSOC|ASSN)\b', q) or _sane_person(q)):
            out.append({'kind': 'name', 'q': q})
    return out, known or bool(out)


def live_ok(module_file, out_path, leads_path, out_name, leads_name):
    """True only for the checkout's own nightly run. A test, a scratch OUT / LEADS, or a linked git
    worktree with no DEALFLOW_DIR set never reaches the live queue (2026-11-13: a test put invented
    cases into it)."""
    here = os.path.dirname(os.path.abspath(module_file))
    if os.environ.get('PYTEST_CURRENT_TEST'):
        return False
    if os.path.abspath(out_path) != os.path.join(here, out_name) or os.path.abspath(leads_path) != os.path.join(here, leads_name):
        return False
    if os.path.isfile(os.path.join(here, '.git')) and not os.environ.get('DEALFLOW_DIR'):
        return False                                       # a linked worktree, not the engine checkout
    return True


def queue_lead(county, lead, reason, people=None):
    """Hand one unread Broward / Palm Beach lead to the queue. Never raises. -> True when queued."""
    try:
        county = norm(county)
        ss, known = (searches_broward(lead, people) if county == BROWARD else searches_pb(lead))
        if not ss or not lead.get('case'):
            return False
        Q = _Q()
        owner = (lead.get('owners') or '').strip()
        Q.add(lead['case'], owner, folio=str(lead.get('folio') or ''), county=county, reason=reason,
              kind='person', names=[(s['q'], s['kind']) for s in ss], address=lead.get('addr') or '',
              searches=ss, owner_known=known)
        return True
    except Exception:
        return False


# ---- worker side --------------------------------------------------------------------------------
def searches(it):
    return it.get('searches') or []


def next_row(it):
    return {'case': it['case'], 'county': norm(it.get('county')), 'status': it.get('status', 'pending'),
            'searches': [{'index': i, 'kind': s['kind'], 'q': s['q']} for i, s in enumerate(searches(it))]}


def _resolve(val, strings, how):
    if how != 'd' or val is None:
        return val
    if isinstance(val, bool):
        raise ValueError('a text field holds true/false')
    if isinstance(val, int):
        if not 0 <= val < len(strings):
            raise ValueError('a row points at text that was never sent')
        return strings[val]
    if not isinstance(val, str):
        raise ValueError('a text field holds something that is not text')
    strings.append(val)
    return val


def unpack(county, rows, types):
    """Packed rows -> the county's own row dicts. Raises ValueError on anything malformed."""
    cols = H[norm(county)]['cols']
    strings, out = [], []
    for row in rows:
        if not isinstance(row, list) or len(row) > len(cols):
            raise ValueError('a row has %s fields, expected at most %d' % (len(row) if isinstance(row, list) else '?', len(cols)))
        row = row + [None] * (len(cols) - len(row))        # the page drops empty fields off the end of a row
        d = {}
        for (name, how), v in zip(cols, row):
            if how == 't':
                if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < len(types):
                    raise ValueError('a row names a document type the header does not list')
                v = types[v]
            elif how == 'm':
                if isinstance(v, bool):
                    raise ValueError('a date holds true/false')
                if isinstance(v, int):
                    v = '/Date(%d)/' % v
            else:
                v = _resolve(v, strings, how)
            d[name] = v
        out.append(d)
    return out


def belongs(county, search, models):
    """Do these rows answer THIS search? The county keeps "the current search" on its side, so a
    script run before the new search finished would hand back the previous lead's rows. -> '' or why."""
    county = norm(county)
    q = _alnum(search['q'])
    if county == BROWARD:
        # a name search matches on the start of the indexed name, comma and spaces aside
        off = sum(1 for m in models if not _alnum(m.get('Name')).startswith(q))
        return '' if not off else '%d of %d row(s) are not for the name searched' % (off, len(models))
    if search['kind'] == 'parcel':
        want = q.lstrip('0')
        off = sum(1 for m in models if re.sub(r'\D', '', re.sub(r'<[^>]+>', '', str(m.get('4') or ''))).lstrip('0') != want)
        return '' if not off else '%d of %d row(s) are not on the parcel searched' % (off, len(models))
    words = [w for w in re.split(r'[^A-Z0-9]+', str(search['q']).upper()) if w]

    def hit(m):
        blob = ' '.join(re.sub(r'<[^>]+>', ' ', str(m.get(k) or '')).upper() for k in ('4', '5', '6'))
        return bool(words) and words[0] in blob
    off = sum(1 for m in models if not hit(m))
    return '' if not off else '%d of %d row(s) do not carry the name searched' % (off, len(models))


def accept_search(case, payload, index=0):
    """Store ONE search of a Broward / Palm Beach lead. All or nothing. -> (ok, message)."""
    Q = _Q()
    q = Q._read(Q.qpath(), {'items': []})
    it = next((x for x in q['items'] if x['case'] == case), None)
    if not it or it.get('status') not in ('pending', 'rows', HUMAN):
        return False, 'case is not waiting on today\'s list'
    county = norm(it.get('county'))
    ss = searches(it)
    if not 0 <= int(index) < len(ss):
        return False, 'this lead has %d search(es); --name-index must be 0..%d' % (len(ss), len(ss) - 1)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return False, 'the file must be a list that starts with the header object (the --chunk -1 answer)'
    if payload[0].get('err'):
        return False, 'the page script reported "%s": nothing stored' % str(payload[0]['err'])[:80]
    if not payload[0].get('hdr'):
        return False, 'the file must be a list that starts with the header object (the --chunk -1 answer)'
    hdr = payload[0]
    try:
        n, of, total, types = int(hdr['n']), int(hdr['of']), int(hdr['total']), list(hdr['types'])
    except Exception:
        return False, 'the header is missing n / of / total / types'
    if n != total:
        return False, 'the county says %d row(s), the page read %d: not the whole search, nothing stored' % (total, n)
    if n > H[county]['max']:
        return False, 'more than %d rows' % H[county]['max']
    chunks = payload[1:]
    if len(chunks) != of:
        return False, 'the header says %d chunk(s), the file has %d' % (of, len(chunks))
    rows = []
    for i, ch in enumerate(chunks):
        try:
            k, part, want = int(ch['k']), ch['rows'], int(ch['sum'])
        except Exception:
            return False, 'chunk %d is missing k / sum / rows' % i
        if k != i:
            return False, 'chunks out of order: position %d holds chunk %d' % (i, k)
        if not isinstance(part, list) or Q.checksum(part) != want:
            return False, 'checksum mismatch in chunk %d: the rows were not copied exactly. Run the script for chunk %d again.' % (k, k)
        rows += part
    if len(rows) != n:
        return False, 'the header says %d row(s), the chunks hold %d' % (n, len(rows))
    try:
        models = unpack(county, rows, types)
    except ValueError as e:
        return False, str(e)
    why = belongs(county, ss[int(index)], models)
    if why:
        return False, why + ': the page was still showing another search. Run the search again, wait, then run the header script again.'
    cap = 0
    if county == PB:
        try:
            cap = int(hdr.get('cap') or 0)
        except Exception:
            cap = 0
    capped = bool(cap and n >= cap)
    path = Q.rows_path(case)
    cur = Q._read(path, None) or {'case': case, 'parts': []}
    cur['parts'] = [p for p in cur['parts'] if p.get('i') != int(index)]
    cur['parts'].append({'i': int(index), 'name': ss[int(index)]['q'], 'kind': ss[int(index)]['kind'],
                         'n': n, 'capped': capped, 'models': models})
    Q._write(path, cur)
    Q._set(case, status='rows', rows=sum(p['n'] for p in cur['parts']), searches_done=len(cur['parts']))
    left = [i for i in range(len(ss)) if i not in {p['i'] for p in cur['parts']}]
    return True, ('%d row(s) stored for search %d%s. ' % (n, int(index), ' (hit the county\'s row limit: not a full read)' if capped else '')) + (
        'Searches still to run: %s' % left if left else 'Every search on this lead is stored.')


def docs_file(it):
    """Merge a lead's stored searches into the file the county scraper reads. -> (path, full)."""
    Q = _Q()
    path = Q.rows_path(it['case'])
    cur = Q._read(path, None) or {'parts': []}
    parts = sorted(cur['parts'], key=lambda p: p['i'])
    ss = searches(it)
    full = (bool(parts) and len(parts) == len(ss) and not any(p.get('capped') for p in parts)
            and bool(it.get('owner_known', True)))
    owner_as = ''
    if not it.get('owner_known', True):
        first = next((p for p in parts if p.get('kind') == 'defendant' and p['n']), None)
        owner_as = first['name'] if first else ''
    out = path[:-5] + '.docs.json'
    Q._write(out, {'case': it['case'], 'county': norm(it.get('county')), 'full': full,
                   'searched': '; '.join('%s %s' % (p.get('kind', 'name'), p['name']) for p in parts),
                   'searches': len(parts), 'searches_on_lead': len(ss), 'owner_as': owner_as,
                   'parts': [{'kind': p.get('kind', 'name'), 'q': p['name'], 'rows': p['models']} for p in parts]})
    return out, full


def finish_argv(it, path):
    argv = [sys.executable, '-u', H[norm(it.get('county'))]['script'], '--case', it['case'], '--docs-file', path]
    d = os.environ.get('EQUITY_QUEUE_LIENS_DIR')
    if d:
        # a proof or test run: read the leads from, and write the chain to, a scratch folder
        stem = 'broward' if norm(it.get('county')) == BROWARD else 'palmbeach'
        argv += ['--out', os.path.join(d, stem + '_liens.json'), '--leads', os.path.join(d, stem + '_leads.json')]
    return argv


def mark_human(county='', case='', why=''):
    """Palm Beach showed its checkbox: the waiting leads become needs_human_captcha. Never a miss,
    never 'no liens'. -> how many were marked."""
    Q = _Q()
    q = Q._read(Q.qpath(), None)
    if not q:
        return 0
    n = 0
    for it in q['items']:
        if it.get('status') != 'pending' or not handled(it):
            continue
        if case and it['case'] != case:
            continue
        if not case and norm(it.get('county')) != norm(county or PB):
            continue
        it['status'] = HUMAN
        it['why'] = str(why or 'the county showed a verification checkbox')[:160]
        n += 1
    if n:
        Q._write(Q.qpath(), q)
    return n
