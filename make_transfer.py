#!/usr/bin/env python3
"""make_transfer — stage everything a SECOND machine needs to run DealFlow.

WHY THIS EXISTS AND WHY IT IS NOT A ZIP IN THE CLOUD (2026-08-22/23)
A file named DEALFLOW_TRANSFER_2026-08-20.zip was found sitting on the OneDrive-synced Desktop.
Inside it: captcha.key, gmail.key, tracerfy.key, whitepages.key, zerobounce.key, streetview.key,
tracerfy_mcp.url and sheets_crm_webhook.url -- every credential the operation owns, replicated to
consumer cloud storage for three days, every one still live. That is the failure this script is
built to not repeat.

So:
  * output goes to a path you name, DEFAULT OUTSIDE OneDrive, and the script refuses to write
    inside a known sync root;
  * secrets land in their own clearly-labelled folder so nobody mistakes them for data;
  * the bundle is NOT encrypted here on purpose. Encryption needs a passphrase, and a passphrase
    this script generates is a passphrase this script has seen and logged. It prints the exact
    command to encrypt it with one only you know, then you carry it on a USB stick.

WHAT MOVES (and what does not)
  code      -> NOT included. `git clone` the repo on the other machine; that is what git is for.
  secrets   -> 10 files, tiny, irreplaceable.
  ledgers   -> ~11 MB. This is the actual WORK: who was contacted, who opted out, what bounced,
               what was traced, what was verified. Losing it is worse than losing the code.
  caches    -> optional; regenerable but expensive (lien chains, county values, comps).
  photos    -> never. docs/img is ~240 MB and rebuilds itself.

    python make_transfer.py                     # stage to ~/DEALFLOW-transfer
    python make_transfer.py --out E:/dealflow   # straight onto the stick
    python make_transfer.py --no-caches         # secrets + ledgers only
"""
import argparse
import datetime
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SECRETS = ['gmail.key', 'captcha.key', 'tracerfy.key', 'zerobounce.key', 'whitepages.key',
           'streetview.key', 'sheets_crm_webhook.url', 'tracerfy_mcp.url', 'site.codes',
           'sender.json', 'batchdata.key', 'lob.key']
LEDGERS = ['skiptrace_results.json', 'worker_notes.json', 'mail_sent.json', 'optouts.json',
           'deads.json', 'bounced_emails.json', 'verified_emails.json', 'retrace_queue.json',
           'bd_budget.json', 'leads_final.json', 'lp_leads.json', 'broward_leads.json',
           'palmbeach_leads.json', 'lis_pendens.json']
CACHES = ['pa_values_cache.json', 'broward_liens.json', 'diligence_cache.json', 'comps.json',
          'geocode_cache.json', 'redfin_cache.json', 'listing_status_cache.json',
          'sale_history_cache.json', 'property_types_cache.json', 'auction_archive.json',
          'rf_parcels.json', 'pa_property_cache.json']

SYNCED = ('onedrive', 'dropbox', 'google drive', 'googledrive', 'icloud', 'box sync')


def _copy(names, dest, label):
    os.makedirs(dest, exist_ok=True)
    n = tot = 0
    missing = []
    for f in names:
        src = os.path.join(HERE, f)
        if not os.path.exists(src):
            missing.append(f)
            continue
        shutil.copy2(src, os.path.join(dest, f))
        n += 1
        tot += os.path.getsize(src)
    print('  %-10s %2d file(s)  %7.1f MB' % (label, n, tot / 1048576))
    if missing:
        print('             (absent, skipped: %s)' % ', '.join(missing[:6]))
    return n, tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(os.path.expanduser('~'), 'DEALFLOW-transfer'))
    ap.add_argument('--no-caches', action='store_true')
    a = ap.parse_args()

    out = os.path.abspath(a.out)
    # THE WHOLE POINT. Refuse to stage credentials into anything that syncs.
    low = out.replace('\\', '/').lower()
    if any(s in low for s in SYNCED):
        sys.exit('REFUSED: %s is inside a cloud-sync folder. That is exactly how eight live keys\n'
                 '         ended up replicated to OneDrive for three days. Pick a local path.' % out)
    if os.path.exists(out) and os.listdir(out):
        sys.exit('REFUSED: %s already exists and is not empty. Name a fresh folder so nothing\n'
                 '         half-old is carried to the other machine.' % out)

    print('staging DealFlow transfer -> %s\n' % out)
    _copy(SECRETS, os.path.join(out, '1-SECRETS-handle-carefully'), 'secrets')
    _copy(LEDGERS, os.path.join(out, '2-LEDGERS-the-work'), 'ledgers')
    if not a.no_caches:
        _copy(CACHES, os.path.join(out, '3-CACHES-regenerable'), 'caches')

    today = datetime.date.today().isoformat()
    readme = """DEALFLOW — set up on a second machine        staged %s

1. GET THE CODE (not in this bundle; git carries it)
     git clone https://github.com/aiejandrog/foreclosure-leads.git
     cd foreclosure-leads
     pip install -r requirements.txt        (or: pip install requests playwright beautifulsoup4)
     python -m playwright install chromium

2. DROP THESE FILES IN, all into the repo root, flattened:
     1-SECRETS-handle-carefully\\*   -> repo root
     2-LEDGERS-the-work\\*           -> repo root
     3-CACHES-regenerable\\*         -> repo root   (optional; saves hours of re-scraping)
   Every one of them is gitignored. Do NOT git add them. The repo is PUBLIC.

3. CHECK IT BREATHES, before trusting anything:
     python rotate_key.py 2captcha --check
     python rotate_key.py tracerfy  --check
     python rotate_key.py gmail     --check
     python healthcheck.py

4. BUILD THE BOARD:
     python -c "import json,foreclosure_leads as F; F.make_tracker(json.load(open('leads_final.json',encoding='utf-8')))"
   Writes docs/index.html (published, encrypted) and the plaintext twin under ~/DEALFLOW.

5. THE NIGHTLY JOB is refresh-dealflow.bat. Only ONE machine should run it. Two machines
   pushing the same repo is how the live site froze for two days in August.

WHAT IS NOT HERE, deliberately
  * docs/img (~240 MB of property photos) — rebuilds itself.
  * The git history — clone it.
  * Any password. Nothing in this bundle is encrypted; see the note printed at staging time.

IF THIS BUNDLE IS LOST: rotate every key in 1-SECRETS immediately —
  2Captcha, Tracerfy, ZeroBounce, Whitepages, StreetView, the Gmail app password, and
  REDEPLOY the Apps Script web app (its URL is the secret). python rotate_key.py --list
"""
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'READ-ME-FIRST.txt'), 'w', encoding='utf-8').write(readme % today)

    tot = sum(os.path.getsize(os.path.join(r, f))
              for r, _, fs in os.walk(out) for f in fs)
    print('\nbundle: %.1f MB at %s' % (tot / 1048576, out))
    print('\nNOW ENCRYPT IT WITH A PASSPHRASE ONLY YOU KNOW, then copy to the stick:')
    print('   7z a -p -mhe=on "%s.7z" "%s\\*"' % (out, out))
    print('   (-p prompts for the passphrase, -mhe=on encrypts the FILE NAMES too)')
    print('\nThis script does not generate that passphrase on purpose: one it generates is one it')
    print('has seen. After the copy lands and works, delete the plain folder.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
