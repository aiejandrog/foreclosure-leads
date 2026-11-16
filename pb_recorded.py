#!/usr/bin/env python3
"""pb_recorded.py -- a Palm Beach RECORDED document as a PDF, by book/page or CFN. Free, no captcha.

MEASURED LIVE 2026-11-16 (Alex: "test it and review it and then find the fix"). Until now every
Palm Beach record read in this project went through Landmark's SEARCH, and search is gated: with no
token NameSearch, ParcelIdSearch, BookPageSearch, RecordDateSearch and DocumentTypeSearch all answer
'Invalid Captcha'. That was written down as "Palm Beach documents need a captcha", and it is wrong.
Landmark also publishes direct document links that never touch search:

    /Document/GetDocumentByBookPage/?booktype=O&booknumber=<book>&pagenumber=<page>
    /Document/GetDocumentByCFN/?cfn=<cfn>

Both answer application/pdf with the WHOLE instrument (5-page judgments came back as 5 pages), with
no session cookie, no disclaimer and no token. booktype must be the letter 'O': 'OR' and the
numeric '3' return a 6 KB error page. A page number inside a document returns that page onward, so
the first page of the instrument is the one to ask for.

palmbeach_liens.json already stores the book/page of every lien and judgment it found ('O 36315/910'),
so their documents are readable now. The bodies are scans (no text layer): read them with
document_store.read_pages(..., ocr=document_store.winocr).
"""
import os
import re
import subprocess
import tempfile

BASE = 'https://erec.mypalmbeachclerk.com'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/126.0 Safari/537.36')
CURL = (r'C:\Windows\System32\curl.exe'
        if os.name == 'nt' and os.path.exists(r'C:\Windows\System32\curl.exe') else 'curl')
MAX_BYTES = 60 * 1024 * 1024
_BP_RE = re.compile(r'^\s*(?:O(?:R)?\s+)?0*(\d{1,6})\s*/\s*0*(\d{1,5})\s*$', re.I)


class Unavailable(RuntimeError):
    """The county did not return this document. Never means 'no such document'."""


def parse_bp(value):
    """'O 36315/910', '36315/0910' -> ('36315', '910'). None when it is not an Official Records ref."""
    m = _BP_RE.match(str(value or ''))
    if not m or not int(m.group(1)) or not int(m.group(2)):
        return None
    return m.group(1), m.group(2)


def _get(url, run=None):
    """-> bytes. `run` is injectable for tests: run(url) -> bytes."""
    if run is not None:
        return run(url)
    fd, dest = tempfile.mkstemp(prefix='pbrec-', suffix='.bin')
    os.close(fd)
    try:
        subprocess.run([CURL, '-s', '-m', '120', '-A', UA, '-L', '--max-filesize', str(MAX_BYTES),
                        '-o', dest, url], capture_output=True, timeout=150)
        with open(dest, 'rb') as f:
            return f.read()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable('transport: %s' % str(exc)[:120])
    finally:
        try:
            os.remove(dest)
        except OSError:
            pass


def _pdf(body, what):
    if not body or not body.startswith(b'%PDF-'):
        raise Unavailable('%s: the county returned a non-PDF body (%d bytes)' % (what, len(body or b'')))
    return body


def by_book_page(book, page, run=None):
    book, page = str(book).strip(), str(page).strip()
    if not (re.fullmatch(r'\d{1,6}', book) and re.fullmatch(r'\d{1,5}', page)):
        raise ValueError('book and page must be numeric')
    url = '%s/Document/GetDocumentByBookPage/?booktype=O&booknumber=%s&pagenumber=%s' % (BASE, book, page)
    return _pdf(_get(url, run), 'book/page %s/%s' % (book, page))


def by_cfn(cfn, run=None):
    cfn = str(cfn).strip()
    if not re.fullmatch(r'\d{8,14}', cfn):
        raise ValueError('CFN must be numeric')
    return _pdf(_get('%s/Document/GetDocumentByCFN/?cfn=%s' % (BASE, cfn), run), 'CFN %s' % cfn)


def fetch(ref, run=None):
    """A stored reference ('O 36315/910' or a CFN) -> PDF bytes."""
    bp = parse_bp(ref)
    if bp:
        return by_book_page(bp[0], bp[1], run)
    if re.fullmatch(r'\s*\d{8,14}\s*', str(ref or '')):
        return by_cfn(str(ref).strip(), run)
    raise ValueError('not a Palm Beach book/page or CFN reference')
