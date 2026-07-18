#!/usr/bin/env python3
# [Cursor cloud edit] local mailer for the tracker's "Mail batch" export — authored by the Cursor cloud agent.
"""Send the DEALFLOW "Mail batch" queue as real letters via Lob — locally, so the Lob key never touches
the browser or the public site.

Flow:  In the tracker click  "✉ Mail batch"  → uncheck anyone → "⬇ Download batch"  (a JSON queue).
Then:   python outreach_mail.py --queue dealflow-mail-batch-YYYY-MM-DD.json          # DRY RUN (no send, no cost)
        python outreach_mail.py --queue dealflow-mail-batch-YYYY-MM-DD.json --send    # actually mail via Lob

Dry run is the default: it parses every address, builds the letter, and prints exactly what WOULD be
sent — nothing leaves your machine and no charge is incurred. Add --send only when you are ready to pay.

Requirements for --send:
  * A funded Lob account. Put the live/secret key in a file named `lob.key` (gitignored), or pass --key.
  * A return address for the sender: fill `addr` in the tracker's Sender fields (it rides along in the
    export) or in `sender.json`. Lob requires a valid `from` address.

This is intentionally standalone and read-only against the tracker: it consumes the exported queue and
never touches leads_final.json or the site build.
"""
import argparse, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOB_LETTERS_URL = 'https://api.lob.com/v1/letters'


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_key(path):
    p = path if os.path.isabs(path) else os.path.join(HERE, path)
    if not os.path.exists(p):
        return ''
    return open(p, encoding='utf-8').read().strip()


def clean_owner(raw):
    """Reduce a raw owner string to the primary person 'First [Middle] Last'.
    Mirrors the tracker's _ownerName: drop suffixes/spouse markers, flip 'Last, First', take the first party."""
    s = (raw or '').split(';')[0].strip()
    s = re.sub(r'\b(ET\s?UX|ET\s?VIR|H/W|W/H|LE|REM|TRS|JR|SR|II|III|IV|ETAL|ET AL)\b', '', s, flags=re.I)
    s = re.sub(r'\s*&\s*[WH]\b.*$', '', s, flags=re.I)
    s = re.sub(r'\s*&\s*.*$', '', s)          # drop a second co-owner after '&'
    s = s.strip().strip(',')
    if ',' in s:
        a, _, b = s.partition(',')
        s = (b.strip() + ' ' + a.strip()).strip()
    s = re.sub(r'\s{2,}', ' ', s).strip()
    # Title-case an ALL-CAPS name; leave mixed case alone
    if s and s == s.upper():
        s = ' '.join(w.capitalize() for w in s.split())
    return s


def parse_address(s):
    """Parse a US mailing address string into Lob components. Handles an apartment/unit suffix
    ('APT 307', 'UNIT 5', '#307', 'STE 200') by pulling it into address_line2."""
    s = re.sub(r'\s{2,}', ' ', (s or '').strip()).rstrip(',').strip()
    parts = [p.strip() for p in s.split(',') if p.strip()]
    state, zipc = '', ''
    if parts:
        m = re.search(r'\b([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)\b', parts[-1])
        if m:
            state, zipc = m.group(1).upper(), m.group(2)
            parts = parts[:-1]
        else:
            m2 = re.search(r'\b(\d{5}(?:-\d{4})?)\b', parts[-1])
            if m2:
                zipc = m2.group(1)
                parts[-1] = parts[-1].replace(m2.group(1), '').strip()
    city = parts.pop().strip() if parts else ''
    street = ', '.join(parts).strip()
    line2 = ''
    m = re.search(r'\s+(#\s*[\w-]+|(?:APT|UNIT|STE|SUITE|BLDG|LOT|RM|PH)\.?\s*#?\s*[\w-]+)\s*$', street, re.I)
    if m:
        line2 = re.sub(r'\s{2,}', ' ', m.group(1).strip())
        street = street[:m.start()].strip().rstrip(',')
    return {'line1': street, 'line2': line2, 'city': city, 'state': state, 'zip': zipc}


def address_ok(a):
    return bool(a['line1'] and a['city'] and a['state'] and a['zip'])


def build_letter_text(lead, sender):
    """English letter body, mirroring the tracker's genLetter() so the mailed copy matches the on-screen one."""
    owner = lead.get('owner') or clean_owner(lead.get('owners', ''))
    addr = lead.get('addr', '')
    dt = lead.get('auction', '')
    td = (lead.get('sale_type') == 'TD')
    plaintiff = (lead.get('plaintiff') or '').strip()
    case = (lead.get('case') or '').strip()
    sN = sender.get('name') or '[YOUR NAME]'
    sP = sender.get('phone') or '[YOUR PHONE]'
    case_tag = ''
    if case:
        case_tag = f" (Certificate/Case No. {case})" if td else f" (Case No. {case})"
    sig = '\n'.join(x for x in [sN, sender.get('title', ''), sender.get('llc', ''),
                                (f"Phone: {sP}" if sP else ''), (f"Email: {sender.get('email','')}" if sender.get('email') else ''),
                                sender.get('addr', ''), sender.get('web', '')] if x and str(x).strip())
    if td:
        body = (f"Dear {owner},\n\n"
                f"I hope this letter finds you well. My name is {sN}, and I am a local real estate investor here in Miami. "
                f"I am writing regarding your property at {addr}, which county records show is scheduled for a tax deed sale on {dt}{case_tag} due to unpaid property taxes.\n\n"
                "I wanted to reach out in case it helps to know your options before that date:\n"
                "- You can still keep the property by paying the back taxes any time before the sale.\n"
                "- If keeping it is not realistic, selling before the sale can put cash in your pocket rather than losing it to the county.\n"
                "- If it sells for more than the taxes owed, any surplus may belong to you, though it has to be claimed.\n\n"
                "I am not an attorney, and nothing here is legal advice. I purchase properties directly, with cash, and can close before the deadline. "
                "If selling is not your best move, I will tell you honestly.\n\n"
                f"There is no cost and no obligation to talk. You can reach me at {sP}. Even if the sale is close, there may still be time.\n\n"
                "Thank you for your time.\n\nRespectfully,\n\n" + sig)
    else:
        by = f" by {plaintiff}" if plaintiff else ''
        body = (f"Dear {owner},\n\n"
                f"I hope this letter finds you well. My name is {sN}, and I am a local real estate investor here in Miami. "
                f"I am writing regarding your property at {addr}, which public records show is currently in foreclosure{by}{case_tag}, with a sale scheduled for {dt}.\n\n"
                "I understand this can be a stressful situation, and I wanted to reach out in case it helps to know your options before that date:\n"
                "- If the property sells for more than you owe, any surplus belongs to you, not the lender.\n"
                "- Selling before the auction is often better for you than losing it at the courthouse, and it can put cash in your pocket.\n"
                "- The amount on file is not always current, and it is worth confirming before the sale.\n\n"
                "I am not an attorney, and nothing here is legal advice. I purchase properties directly, with cash, and can close before the deadline. "
                "If buying is not the right fit for you, I am glad to point you in a better direction, even if it does not involve me.\n\n"
                f"There is no cost and no obligation to talk. You can reach me at {sP}. Even if the sale is close, there may still be time.\n\n"
                "Thank you for your time.\n\nRespectfully,\n\n" + sig)
    return owner, body


def letter_html(body):
    esc = (body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
    return ('<html><head><meta charset="utf-8"><style>'
            'body{font-family:Georgia,serif;font-size:12pt;line-height:1.5;color:#111;margin:1in}'
            '</style></head><body>' + esc.replace('\n', '<br>') + '</body></html>')


def send_via_lob(key, sender_addr, to_name, to_addr, html, description):
    import requests
    data = {
        'description': description,
        'to[name]': to_name,
        'to[address_line1]': to_addr['line1'],
        'to[address_city]': to_addr['city'],
        'to[address_state]': to_addr['state'],
        'to[address_zip]': to_addr['zip'],
        'from[name]': sender_addr['name'],
        'from[address_line1]': sender_addr['line1'],
        'from[address_city]': sender_addr['city'],
        'from[address_state]': sender_addr['state'],
        'from[address_zip]': sender_addr['zip'],
        'file': html,
        'color': 'false',
        'address_placement': 'top_first_page',
    }
    if to_addr['line2']:
        data['to[address_line2]'] = to_addr['line2']
    if sender_addr.get('line2'):
        data['from[address_line2]'] = sender_addr['line2']
    r = requests.post(LOB_LETTERS_URL, auth=(key, ''), data=data, timeout=30)
    return r


def main():
    ap = argparse.ArgumentParser(description='Mail the DEALFLOW "Mail batch" queue via Lob (dry run by default).')
    ap.add_argument('--queue', required=True, help='the JSON file exported by the tracker\'s "Mail batch" button')
    ap.add_argument('--send', action='store_true', help='actually create letters via Lob (costs money). Omit for a dry run.')
    ap.add_argument('--key', default='lob.key', help='path to the Lob API key file (default: lob.key)')
    ap.add_argument('--sender', default='sender.json', help='optional sender.json to supply/override the return address')
    ap.add_argument('--limit', type=int, default=0, help='cap the number of letters (0 = all)')
    args = ap.parse_args()

    q = load_json(args.queue)
    leads = q.get('leads', [])
    if args.limit:
        leads = leads[:args.limit]

    sender = dict(q.get('sender') or {})
    sp = args.sender if os.path.isabs(args.sender) else os.path.join(HERE, args.sender)
    if os.path.exists(sp):
        try:
            js = load_json(sp)
            for k, v in js.items():
                if v and not sender.get(k):
                    sender[k] = v
        except Exception as e:
            print(f"warn: could not read {args.sender}: {e}")

    sender_addr = parse_address(sender.get('addr', ''))
    sender_addr['name'] = sender.get('name') or sender.get('llc') or ''
    sender_addr['line2'] = sender_addr.get('line2', '')

    mode = 'SEND (Lob)' if args.send else 'DRY RUN (no send, no cost)'
    print(f"=== DEALFLOW mail batch · {mode} ===")
    print(f"queue: {os.path.basename(args.queue)} · {len(leads)} letter(s) · sender: {sender.get('name','?')} / {sender.get('llc','?')}")
    print(f"est. postage: ${len(leads)*float(q.get('cost_per_letter', 0.92)):.2f}\n")

    key = ''
    if args.send:
        key = read_key(args.key)
        if not key:
            print(f"ERROR: --send requires a Lob key in '{args.key}'. Aborting."); sys.exit(1)
        if not address_ok(sender_addr):
            print("ERROR: --send requires a valid sender return address (set 'addr' in Sender fields or sender.json). Aborting."); sys.exit(1)

    sent = skipped = 0
    for i, lead in enumerate(leads, 1):
        to_addr = parse_address(lead.get('mail') or lead.get('addr', ''))
        owner, body = build_letter_text(lead, sender)
        salutation = body.split('\n', 1)[0]
        ok = address_ok(to_addr)
        line2 = f"  [line2: {to_addr['line2']}]" if to_addr['line2'] else ''
        print(f"[{i}/{len(leads)}] {owner}  ({lead.get('tier','?')} · {lead.get('sale_type','FC')} · auction {lead.get('auction','?')})")
        print(f"      to: {to_addr['line1']}{('/ '+to_addr['line2']) if to_addr['line2'] else ''}, {to_addr['city']}, {to_addr['state']} {to_addr['zip']}")
        print(f"      salutation: {salutation}")
        if not ok:
            print("      !! address incomplete — SKIPPED (not mailable)\n"); skipped += 1; continue
        if not args.send:
            print("      (dry run — not sent)\n"); continue
        try:
            r = send_via_lob(key, sender_addr, owner, to_addr, letter_html(body),
                             f"DEALFLOW {lead.get('case','')}")
            if r.status_code in (200, 201):
                jid = r.json().get('id', '?')
                print(f"      SENT — Lob id {jid}\n"); sent += 1
            else:
                print(f"      !! Lob error {r.status_code}: {r.text[:160]}\n"); skipped += 1
        except Exception as e:
            print(f"      !! send failed: {str(e)[:160]}\n"); skipped += 1

    print("=== summary ===")
    if args.send:
        print(f"sent: {sent} · skipped/failed: {skipped}")
    else:
        mailable = sum(1 for l in leads if address_ok(parse_address(l.get('mail') or l.get('addr', ''))))
        print(f"dry run complete · {mailable}/{len(leads)} have a complete, mailable address · nothing was sent")
        print("Re-run with --send (and a funded lob.key) to mail.")


if __name__ == '__main__':
    main()
