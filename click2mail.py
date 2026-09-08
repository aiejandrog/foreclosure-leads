#!/usr/bin/env python
"""click2mail -- send letters through Click2Mail's REST API.

Second-source vendor for outreach_mail.py. Same shape as send_via_lob(): give it an HTML letter,
a recipient dict, and a from_addr dict, get back a job identifier. Written 2026-09-08 because
Lob's new-account review has our production key returning 403 on every write and 73 letters are
sitting in a queue with a hard sale-date deadline.

WHY THIS EXISTS AS A SEPARATE FILE
The pipeline already speaks to one direct-mail vendor (Lob). Bolting a second vendor's flow into
outreach_mail.py would tangle two very different transaction models -- Lob is a single POST that
returns a letter id, Click2Mail is FOUR sequential POSTs (document -> address list -> job ->
submit) whose errors happen at different steps. Keep the transaction here; outreach_mail routes to
one file or the other via a --vendor flag.

SECURITY (same rule as lob.key/postscan.key/quo.key)
The credential is read from a gitignored `click2mail.key` file and NEVER hardcoded. Click2Mail auth
is HTTP Basic with the account username and password -- that is a login credential, not just an
API key, so rotating it means changing your dashboard login. The file's format is JSON:

    {"username": "...", "password": "...", "env": "stage"}

`env` picks staging (default, no real mail) vs `prod` (real mail, real charges). The staging URL
lets us shake this out without spending a cent -- confirm we can render the HTML to PDF, upload it,
create the address list, and get a job id back, before flipping to prod.

CLICK2MAIL DOES NOT ACCEPT HTML. Their /documents endpoint takes PDF/DOC/DOCX/PPT/PNG/JPEG only
(https://developers.click2mail.com/docs/supported-document-types, 2026-09-08). So we render our
existing HTML letter to PDF locally via Chrome headless -- Chrome is already installed for the
browser MCP, no new dependency -- and upload the PDF. This ALSO means the same rendered letter can
ship through either vendor: no vendor-specific document template.

RESPONSES ARE XML. Not JSON. Click2Mail returns `<document><id>...</id></document>` etc. Parsed
with xml.etree, not re-typed. The one shape check that matters: HTTP 201 with a numeric <id>.

READ-ONLY IS NOT A CONCEPT HERE. Every call in this file spends money or takes an action. There is
no equivalent to postscan.py's deliberate `/discard` and `/shred` omission -- Click2Mail's whole
purpose is to move paper. The safety is the ENV: staging is free, prod is not. Default is staging.

Run smoke test:
    python click2mail.py --check         # verify creds against staging (no charge)
    python click2mail.py --check --prod  # verify against production
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, 'click2mail.key')

# Staging is free and won't ship real mail; production spends credits and mails.
# Base paths verified in the docs 2026-09-08:
#   https://developers.click2mail.com/docs/building-your-first-api-call  (stage-rest, /molpro)
# Production URL follows the same convention (rest.click2mail.com/molpro); if a signup ever
# surfaces a different prod host, override the URL in click2mail.key with an "endpoint" field.
STAGE_BASE = 'https://stage-rest.click2mail.com/molpro'
PROD_BASE = 'https://rest.click2mail.com/molpro'

# Chrome headless renders our HTML letter to PDF. Standard install path on this laptop; if you
# install Chrome somewhere else, change CHROME or set CHROME_BIN.
CHROME = os.environ.get('CHROME_BIN', r'C:\Program Files\Google\Chrome\Application\chrome.exe')


class ClickMailError(RuntimeError):
    pass


def _load_creds(prod=False):
    """Fail LOUD. A missing key must never degrade into 'oh well, dry run'."""
    if not os.path.exists(KEY_FILE):
        raise ClickMailError(
            'click2mail: no credentials at %s. Create it as JSON:\n'
            '  {"username": "...", "password": "...", "env": "stage"}\n'
            'and make sure .gitignore covers it (the *.key rule already does).' % KEY_FILE)
    # utf-8-sig strips Notepad's UTF-8 BOM transparently -- without this, a file saved from Notepad
    # produces a "line 1 column 1 char 0" JSONDecodeError that reads as if it's totally empty when
    # actually it just has three invisible bytes in front. Learned 2026-09-08.
    try:
        raw = io.open(KEY_FILE, encoding='utf-8-sig').read().strip()
    except Exception as e:
        raise ClickMailError('click2mail: cannot read %s (%s).' % (KEY_FILE, e))
    if not raw:
        raise ClickMailError(
            'click2mail: %s exists but is EMPTY -- the file was created but never saved with '
            'content. Put a JSON object in it:\n  {"username":"...","password":"...","env":"prod"}'
            % KEY_FILE)
    try:
        d = json.loads(raw)
    except Exception as e:
        raise ClickMailError('click2mail: %s is not valid JSON (%s).' % (KEY_FILE, e))
    if not d.get('username') or not d.get('password'):
        raise ClickMailError('click2mail: username/password missing from %s' % KEY_FILE)
    # --prod on the CLI wins; otherwise the file's env decides.
    env = 'prod' if prod else (d.get('env') or 'stage').lower()
    base = d.get('endpoint') or (PROD_BASE if env.startswith('prod') else STAGE_BASE)
    return d['username'], d['password'], base, env


def _post(base, path, username, password, *, data=None, files=None, xml_body=None,
          accept='application/xml'):
    """One place for the HTTP shape. Basic auth, XML accept, timeout, and error normalization."""
    import requests
    url = base + path
    headers = {'Accept': accept, 'User-Agent': 'dealflow-outreach/1.0'}
    kwargs = dict(auth=(username, password), headers=headers, timeout=45)
    if xml_body is not None:
        kwargs['data'] = xml_body
        kwargs['headers']['Content-Type'] = 'application/xml'
    elif files is not None:
        kwargs['files'] = files
        if data:
            kwargs['data'] = data
    else:
        kwargs['data'] = data or {}
    r = requests.post(url, **kwargs)
    if r.status_code >= 400:
        # Extract the human message if it's there; otherwise show a snippet of the body.
        body = (r.text or '')[:300]
        raise ClickMailError('click2mail: POST %s -> HTTP %d. %s' % (path, r.status_code, body))
    return r


def _parse_id(xml_text, root_tag):
    """Every /documents /addressLists /jobs response is <{tag}><id>N</id>...</{tag}>."""
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        raise ClickMailError('click2mail: unparseable XML from %s response: %s' % (root_tag, e))
    if root.tag != root_tag:
        raise ClickMailError('click2mail: expected <%s> root, got <%s>. Body: %s'
                             % (root_tag, root.tag, xml_text[:200]))
    id_el = root.find('id')
    if id_el is None or not (id_el.text or '').strip():
        raise ClickMailError('click2mail: <%s> response has no <id>. Body: %s'
                             % (root_tag, xml_text[:200]))
    return id_el.text.strip()


def html_to_pdf(html, out_pdf=None):
    """Render an HTML letter to PDF using Chrome headless.

    Chrome is already installed for the browser MCP; using it avoids adding weasyprint (which
    needs GTK on Windows) or wkhtmltopdf (external binary install). If Chrome ever moves, set
    CHROME_BIN in the environment.

    The letter's CSS pins .page to 8.5x11in and the render targets US Letter; --no-margins keeps
    Chrome from adding its own header/footer bars.
    """
    if not os.path.exists(CHROME):
        raise ClickMailError('click2mail: Chrome not found at %s. Set CHROME_BIN.' % CHROME)
    tmp_html = tempfile.NamedTemporaryFile(delete=False, suffix='.html', mode='w', encoding='utf-8')
    tmp_html.write(html); tmp_html.close()
    out_pdf = out_pdf or tempfile.mktemp(suffix='.pdf')
    try:
        # file:// URL because Chrome needs an absolute location. `/` in-place of `\` because Chrome
        # on Windows accepts either but the URL parser prefers forward slashes.
        url = 'file:///' + tmp_html.name.replace('\\', '/')
        cmd = [CHROME, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
               '--print-to-pdf-no-header', '--print-to-pdf=' + out_pdf, url]
        # Chrome writes progress to stderr; capture but don't dump on success.
        p = subprocess.run(cmd, capture_output=True, timeout=90)
        if p.returncode != 0 or not os.path.exists(out_pdf) or os.path.getsize(out_pdf) < 1000:
            raise ClickMailError('click2mail: Chrome PDF render failed (exit %d). stderr: %s'
                                 % (p.returncode, (p.stderr or b'')[:200].decode('utf-8', 'replace')))
        return out_pdf
    finally:
        try: os.unlink(tmp_html.name)
        except Exception: pass


def upload_document(pdf_path, name, base, username, password):
    """POST /documents. Returns the document id."""
    with open(pdf_path, 'rb') as f:
        files = {'file': ('letter.pdf', f, 'application/pdf')}
        data = {'documentFormat': 'PDF', 'documentName': name[:80],
                'documentClass': 'Letter 8.5 x 11'}
        r = _post(base, '/documents', username, password, data=data, files=files)
    return _parse_id(r.text, 'document')


def _xml_escape(s):
    return (str(s or '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;').replace("'", '&apos;'))


def create_address_list(recipient, name, base, username, password):
    """POST /addressLists. `recipient` is a dict with the same shape our Lob code uses:
       {name, address_line1, address_city, address_state, address_zip}.

    Firstname/Lastname split from a single 'name' field: Click2Mail requires both. We split on the
    LAST space so 'Alejandro Gonzalez' -> Firstname 'Alejandro', Lastname 'Gonzalez'. If there is
    no space, the whole name goes in Lastname and Firstname is left empty (rare -- county rows are
    'LAST,FIRST' which our sender flips to two tokens before this ever gets called).
    """
    full = str(recipient.get('name') or '').strip()
    if ' ' in full:
        first, last = full.rsplit(' ', 1)
    else:
        first, last = '', full
    body = ('<addressList>'
            '<addressListName>%s</addressListName>'
            '<addressMappingId>1</addressMappingId>'
            '<addresses><address>'
            '<Firstname>%s</Firstname>'
            '<Lastname>%s</Lastname>'
            '<Address1>%s</Address1>'
            '<City>%s</City>'
            '<State>%s</State>'
            '<Postalcode>%s</Postalcode>'
            '</address></addresses>'
            '</addressList>' % (
                _xml_escape(name),
                _xml_escape(first), _xml_escape(last),
                _xml_escape(recipient.get('address_line1')),
                _xml_escape(recipient.get('address_city')),
                _xml_escape(recipient.get('address_state')),
                _xml_escape(recipient.get('address_zip'))))
    r = _post(base, '/addressLists', username, password, xml_body=body)
    return _parse_id(r.text, 'addressList')


def create_job(document_id, address_id, from_addr, base, username, password):
    """POST /jobs. `from_addr` matches the Lob shape (name, company, address_line1, address_line2,
    address_city, address_state, address_zip).

    The `#10 Double Window` envelope shows both the recipient AND return address through the
    window, which is why Click2Mail wants the from block as job params rather than inside the PDF.
    `Address on Separate Page` layout adds a page carrying the recipient address in the window
    band -- this preserves our carefully tuned two-fold body layout, but does add a page. If a
    single-page overlay layout is needed later ("Address on Letter"), swap the layout string.
    """
    data = {
        'documentClass': 'Letter 8.5 x 11',
        'layout': 'Address on Separate Page',
        'productionTime': 'Next Day',
        'envelope': '#10 Double Window',
        'color': 'Black and White',
        'paperType': 'White 24#',
        'printOption': 'Printing One side',
        'documentId': str(document_id),
        'addressId': str(address_id),
        # Return address. address2 carries the PMB (see sender.json addr_line2).
        'returnAddressName': (from_addr.get('name') or '')[:40],
        'returnAddressOrganization': (from_addr.get('company') or '')[:40],
        'returnAddressAddress1': from_addr.get('address_line1', ''),
        'returnAddressAddress2': from_addr.get('address_line2', '') or '',
        'returnAddressCity': from_addr.get('address_city', ''),
        'returnAddressState': from_addr.get('address_state', ''),
        'returnAddressPostalCode': from_addr.get('address_zip', ''),
    }
    r = _post(base, '/jobs', username, password, data=data)
    return _parse_id(r.text, 'job')


def submit_job(job_id, base, username, password, billing='User Credit'):
    """POST /jobs/{id}/submit. The one action that actually spends money and puts paper in the mail."""
    r = _post(base, '/jobs/' + str(job_id) + '/submit', username, password,
              data={'billingType': billing})
    # /submit returns a <job> or <jobSubmit> shape depending on version; we treat any 2xx with no
    # error string as success. The important thing is the transaction already happened server-side.
    return r.text


def send_letter(html_letter, recipient, from_addr, *, name='DealFlow letter', prod=False):
    """The public entry point mirrors send_via_lob(): one call, four API hops, one return dict.

    Returns:
        {'ok': True, 'vendor': 'click2mail', 'document_id': ..., 'address_id': ...,
         'job_id': ..., 'env': 'stage'|'prod', 'raw': <submit response text>}
    Raises ClickMailError on any step failure -- caller uses the same try/except pattern that wraps
    send_via_lob. The message names WHICH step failed, so retries can target it.
    """
    username, password, base, env = _load_creds(prod=prod)
    pdf_path = html_to_pdf(html_letter)
    try:
        doc_id = upload_document(pdf_path, name, base, username, password)
        list_id = create_address_list(recipient, name, base, username, password)
        job_id = create_job(doc_id, list_id, from_addr, base, username, password)
        raw = submit_job(job_id, base, username, password)
        return {'ok': True, 'vendor': 'click2mail', 'env': env,
                'document_id': doc_id, 'address_id': list_id, 'job_id': job_id, 'raw': raw[:400]}
    finally:
        try: os.unlink(pdf_path)
        except Exception: pass


def check_credentials(prod=False):
    """Smoke test: GET /credit against the chosen environment. No mail, no charge.

    A 200 with a <credit><balance> body means auth works; anything else is a config problem the
    caller needs to fix BEFORE queueing a batch. Cheapest possible integration test.
    """
    import requests
    username, password, base, env = _load_creds(prod=prod)
    r = requests.get(base + '/credit', auth=(username, password),
                     headers={'Accept': 'application/xml'}, timeout=30)
    if r.status_code == 401:
        return {'ok': False, 'env': env, 'reason': 'auth rejected -- wrong username/password'}
    if r.status_code >= 400:
        return {'ok': False, 'env': env, 'reason': 'HTTP %d: %s' % (r.status_code, r.text[:120])}
    try:
        bal = ET.fromstring(r.text).find('balance').text
    except Exception:
        bal = '?'
    return {'ok': True, 'env': env, 'base': base, 'balance': bal}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Click2Mail client for the outreach pipeline.')
    ap.add_argument('--check', action='store_true', help='smoke-test credentials against /credit')
    ap.add_argument('--prod', action='store_true', help='use PRODUCTION endpoint (default: staging)')
    a = ap.parse_args(argv)
    if a.check:
        r = check_credentials(prod=a.prod)
        print(json.dumps(r, indent=2))
        return 0 if r.get('ok') else 1
    print('nothing to do without --check. See send_letter() for programmatic use.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
