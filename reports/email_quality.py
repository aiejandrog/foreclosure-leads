#!/usr/bin/env python
"""reports/email_quality.py -- how clean is the email list, in counts only.

READ-ONLY. Local files only. No network, no SMTP/IMAP, no spend. Prints counts: never an address,
name, phone or street. Safe to paste into a PR.

WHERE THE DATA IS. The ledgers sit in one of two places depending on the file and the box:
DEALFLOW_DIR (paths.py, outside OneDrive) or next to the code (gitignored). Each file is looked up
in DEALFLOW_DIR first, then in --data-dir if given, then beside the repo. A file that is not found
is reported as missing, never silently counted as zero.

Files read (all optional): leads_final.json, *_leads.json (rows with `emails`),
skiptrace_results.json ({case: {emails, ...}}), mail_sent.json (list; `to`, `bcc`),
bounced_emails.json (keys = addresses), optouts.json (envelope {notes:{key: truthy}}; keys are a
case number or '@address'), verified_emails.json ({address: {v, why}}).

SOURCE = where an address first appeared: `skiptrace[:<provider>]` for skiptrace_results.json
(provider = the row's `source`/`provider` field when it has one), else `leads:<file>`. Leads rows
carry no per-email provenance of their own, so that is the finest split the data supports.

Run:  python reports/email_quality.py [--data-dir PATH]
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths as P

_EMAIL_RE = re.compile(r'^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$')


def _find(name, dirs):
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return ''


def _load(path):
    """(data, status) with status in ok / missing / unreadable."""
    if not path:
        return None, 'missing'
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f), 'ok'
    except Exception:
        return None, 'unreadable'


def _norm(a):
    a = str(a or '').strip().lower()
    return a if _EMAIL_RE.match(a) else ''


def _case_key(r):
    for k in ('case', 'Case #', 'cert'):
        v = r.get(k)
        if v:
            return str(v).strip().lower()
    return ''


def collect(dirs):
    """-> stats dict. Pure reads."""
    st = {'files': {}}
    addr_src = {}          # address -> source (first seen wins)
    addr_case = {}         # address -> set of case keys (for case-level opt-outs)

    def add(a, src, case=''):
        a = _norm(a)
        if not a:
            return
        addr_src.setdefault(a, src)
        if case:
            addr_case.setdefault(a, set()).add(case)

    lead_files = []
    p = _find('leads_final.json', dirs)
    if p:
        lead_files.append(p)
    seen = {os.path.basename(x) for x in lead_files}
    for d in dirs:
        for xf in sorted(glob.glob(os.path.join(d, '*_leads.json'))):
            b = os.path.basename(xf)
            if b.startswith('_') or b in ('leads_raw.json', 'balloon_leads.json') or b in seen:
                continue
            seen.add(b)
            lead_files.append(xf)
    st['files']['leads'] = ('ok (%d file%s)' % (len(lead_files), '' if len(lead_files) == 1 else 's')
                            if lead_files else 'missing')
    for xf in lead_files:
        data, s = _load(xf)
        if s != 'ok':
            st['files']['leads'] = 'unreadable'
            continue
        if isinstance(data, list):
            for r in data:
                if isinstance(r, dict):
                    for e in (r.get('emails') or []):
                        add(e, 'leads:' + os.path.basename(xf), _case_key(r))

    data, s = _load(_find('skiptrace_results.json', dirs))
    st['files']['skiptrace_results'] = s
    if isinstance(data, dict):
        for case, row in data.items():
            if not isinstance(row, dict):
                continue
            prov = str(row.get('source') or row.get('provider') or '').strip().lower()
            for e in (row.get('emails') or []):
                add(e, 'skiptrace' + (':' + prov if prov else ''), str(case).strip().lower())

    data, s = _load(_find('mail_sent.json', dirs))
    st['files']['mail_sent'] = s
    mailed = set()
    if isinstance(data, list):
        for e in data:
            if not isinstance(e, dict) or e.get('ch', 'email') != 'email':
                continue
            if e.get('error') or e.get('test_mode'):
                continue
            for a in [e.get('to') or ''] + str(e.get('bcc') or '').split(','):
                a = _norm(a)
                if a:
                    mailed.add(a)

    data, s = _load(_find('bounced_emails.json', dirs))
    st['files']['bounced_emails'] = s
    bounced = set()
    if isinstance(data, (dict, list)):
        bounced = {_norm(k) for k in data} - {''}

    data, s = _load(_find('optouts.json', dirs))
    st['files']['optouts'] = s
    opt_addr, opt_case = set(), set()
    notes = data.get('notes') if isinstance(data, dict) else (data if isinstance(data, list) else None)
    items = notes.items() if isinstance(notes, dict) else ((k, True) for k in (notes or []))
    for k, v in items:
        if not v:
            continue
        k = str(k).strip().lower()
        if k.startswith('@'):
            opt_addr.add(k[1:])
        elif '@' in k:
            opt_addr.add(k)
        else:
            opt_case.add(k.lstrip('#'))

    data, s = _load(_find('verified_emails.json', dirs))
    st['files']['verified_emails'] = s
    vdead = set()
    if isinstance(data, dict):
        vdead = {_norm(k) for k, v in data.items() if isinstance(v, dict) and v.get('v') == 'dead'} - {''}

    def opted(a):
        return a in opt_addr or bool({c.lstrip('#') for c in addr_case.get(a, ())} & opt_case)

    by_src = {}
    n_b = n_o = n_d = n_sent = clean = clean_new = 0
    for a, src in addr_src.items():
        b, o, d, m = a in bounced, opted(a), a in vdead, a in mailed
        n_b += b
        n_o += o
        n_d += d
        n_sent += m
        ok = not (b or o or d)
        clean += ok
        clean_new += ok and not m
        r = by_src.setdefault(src, {'total': 0, 'bounced': 0, 'opted_out': 0, 'verified_dead': 0,
                                    'never_sent': 0, 'clean_sendable': 0})
        r['total'] += 1
        r['bounced'] += b
        r['opted_out'] += o
        r['verified_dead'] += d
        r['never_sent'] += (not m)
        r['clean_sendable'] += ok
    st.update({'unique': len(addr_src), 'by_source': by_src, 'prior_bounces': n_b, 'opted_out': n_o,
               'verified_dead': n_d, 'already_mailed': n_sent, 'never_sent': len(addr_src) - n_sent,
               'clean_sendable': clean, 'clean_never_sent': clean_new,
               'ledger_addresses': len(mailed), 'bounce_list': len(bounced)})
    return st


def render(st):
    L = ['EMAIL QUALITY (counts only)']
    L.append('files: ' + ', '.join('%s=%s' % (k, v) for k, v in sorted(st['files'].items())))
    L.append('unique addresses:      %d' % st['unique'])
    L.append('by source (total / bounced / opted-out / verifier-dead / never-sent / clean):')
    for s, r in sorted(st['by_source'].items(), key=lambda kv: -kv[1]['total']):
        L.append('  %-34s %6d %6d %6d %6d %6d %6d' % (s, r['total'], r['bounced'], r['opted_out'],
                                                      r['verified_dead'], r['never_sent'], r['clean_sendable']))
    L.append('prior bounces:         %d' % st['prior_bounces'])
    L.append('opted out:             %d' % st['opted_out'])
    L.append('verifier dead:         %d' % st['verified_dead'])
    L.append('never sent:            %d  (already mailed: %d)' % (st['never_sent'], st['already_mailed']))
    L.append('clean sendable:        %d  (no bounce, no opt-out, not verifier-dead)' % st['clean_sendable'])
    L.append('clean AND never sent:  %d  (the first-touch pool)' % st['clean_never_sent'])
    miss = [k for k, v in st['files'].items() if v.startswith(('missing', 'unreadable'))]
    if miss:
        L.append('WARNING: %s missing or unreadable: the counts above are a floor, not the list.'
                 % ', '.join(sorted(miss)))
    return L


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-dir', default='', help='extra folder to look in after DEALFLOW_DIR')
    a = ap.parse_args(argv)
    dirs = [P.DEALFLOW_DIR] + ([a.data_dir] if a.data_dir else []) + [HERE]
    print('\n'.join(render(collect(dirs))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
