"""document_store — the evidence half of document acquisition: rebuild, VERIFY, persist, read.

document_collectors fetches bytes. Nothing it returns is evidence until this module has:

  1. rebuilt the pages into one PDF and counted them itself;
  2. reconciled that count against the recording index (`doC_PAGES`) — the only independent
     witness that we received the whole instrument, and the step whose absence has to be RECORDED
     rather than assumed passed;
  3. hashed the bytes and written them, with provenance, somewhere git and OneDrive cannot reach;
  4. extracted text per page and recorded an OUTCOME for every single page.

THE ONE RULE THIS MODULE ENFORCES
A document is `read` only when every page has a text outcome. A scanned judgment extracts to five
empty strings; calling that "read" would let the interpretation layer report "no judgment amount
found" about a document nobody has looked at. So an image-only page is `needs_ocr`, the document
is `partial`, and `complete` is False. OCR and the vision fallback are the next stage and are NOT
in this module — the seam is `page['outcome']`, which stays 'needs_ocr' until something fills it.

WHERE THE BYTES GO, AND WHY NOT HERE
Court filings in a foreclosure carry the homeowner's name, address and often their financial
position. They are exactly the class of file CLAUDE.md's "Never commit" list is about, and exactly
what paths.py was written to keep out of OneDrive. So every write resolves through
`case_review.output_path`, which already refuses OneDrive, any configured sync root, and anything
inside a git repository. Nothing in this module writes a path it built by string concatenation.
"""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone

import case_review
import paths as P

SCHEMA_VERSION = 1
DOCUMENTS_DIR = 'documents'


class DocumentRejected(ValueError):
    """The bytes arrived but do not survive validation. Not an access gap — a bad document."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def source_digest(retrieved):
    """The hash of what the CLERK served, not of the file we rebuilt from it.

    PyMuPDF's `tobytes()` is not byte-reproducible — it writes a fresh document ID (and, before
    metadata is cleared, a creation timestamp) on every call, so two merges of the identical page
    images hash differently. Content-addressing the rebuilt file would therefore break the one
    property the queue's dedupe rests on: a resumed run must recognise a document it already has.

    So identity is the source bytes, in page order. That is also the more defensible thing to cite
    in a finding — it attests to what the county sent us, not to how we re-wrapped it.
    """
    pages = retrieved.get('pages')
    if pages:
        return hashlib.sha256(b''.join(pages)).hexdigest()
    return hashlib.sha256(retrieved.get('content') or b'').hexdigest()


def _atomic_write_bytes(path, data):
    """Write through a temp file + fsync + os.replace.

    A direct write that dies half way leaves a truncated PDF on disk under a hash that says it is
    whole — and the dedupe path would then hand that truncated file to the reader forever.
    """
    path = str(path)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _atomic_write_text(path, text):
    _atomic_write_bytes(path, text.encode('utf-8'))


def _slug(value):
    """A case number is safe-ish, but it is external input naming a path. Treat it as hostile."""
    out = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '')).strip('-.')
    if not out:
        raise ValueError('Refusing to build a path from an empty identifier')
    return out[:80]


# ---- PDF backend ------------------------------------------------------------------------------
# PyMuPDF is already a local dependency of this repo (fl_lp/broward_pin.py, _cardtest.py). Imported
# lazily and reported as a configuration gap rather than an ImportError traceback, so a machine
# without it fails with a sentence an operator can act on.
def _fitz():
    # `import pymupdf` is the current name; `import fitz` is the legacy alias this repo already
    # uses in fl_lp/broward_pin.py and _cardtest.py and still works. Prefer the new name so this
    # module does not emit the deprecation warning on every run, and fall back for older installs.
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz
    except ImportError:
        raise DocumentRejected('PyMuPDF is not installed; run: pip install PyMuPDF')
    return fitz


def merge_pages(page_blobs):
    """Rebuild one PDF from the per-page proxypdf blobs and return (bytes, page_count).

    The page count returned is COUNTED from the rebuilt file, never taken from the manifest. A
    manifest that lies about its own length is the failure this exists to catch.
    """
    fitz = _fitz()
    if not page_blobs:
        raise DocumentRejected('No page content to merge')
    combined = fitz.open()
    try:
        for blob in page_blobs:
            if not blob.startswith(b'%PDF-'):
                raise DocumentRejected('Page content is not a PDF')
            with fitz.open(stream=blob, filetype='pdf') as page:
                if page.page_count < 1:
                    raise DocumentRejected('Page image contains no pages')
                combined.insert_pdf(page)
        return combined.tobytes(), combined.page_count
    finally:
        combined.close()


def page_count(content):
    fitz = _fitz()
    with fitz.open(stream=content, filetype='pdf') as doc:
        return doc.page_count


def validate(retrieved):
    """Turn a collector retrieval record into validated bytes + the page-count verdict.

    Returns the retrieval record with `content`, `pages_received` and `page_count_verified`
    settled. Raises DocumentRejected when the rebuilt document contradicts the recording index.
    """
    record = dict(retrieved)
    record['sha256'] = source_digest(record)
    if 'content' in record and record.get('content'):
        content = record['content']
        if not content.startswith(b'%PDF-'):
            raise DocumentRejected('Stored content is not a PDF')
        counted = page_count(content)
    else:
        content, counted = merge_pages(record.get('pages') or [])
        record['content'] = content
    record.pop('pages', None)
    record['pages_received'] = counted
    expected = record.get('pages_expected')
    if expected is None:
        # NOT a pass. We had no index figure to check against, and the manifest agreeing with
        # itself proves only that the clerk is self-consistent.
        record['page_count_verified'] = False
        record['page_count_note'] = 'no recording-index page count published for this document'
    elif int(expected) != counted:
        raise DocumentRejected(
            'Downloaded page count differs from recording index: index says %d, rebuilt %d'
            % (int(expected), counted))
    else:
        record['page_count_verified'] = True
        record['page_count_note'] = 'rebuilt page count matches the recording index'
    # The rebuilt file's own hash, recorded for completeness and explicitly NOT the identity —
    # it changes every time the same pages are merged. Never cite this one in a finding.
    record['rebuilt_sha256'] = sha256(content)
    record['bytes'] = len(content)
    return record


# ---- persistence ------------------------------------------------------------------------------
def case_dir(county, case):
    """The evidence folder for one case, inside paths.DEALFLOW_DIR and nowhere else."""
    rel = os.path.join(DOCUMENTS_DIR, _slug(county), _slug(case), '.keep')
    return case_review.output_path(rel).parent


def store(county, case, retrieved, source_ref='', doc_name=''):
    """Write the original + its provenance sidecar. Content-addressed, so a re-run dedupes.

    Returns the manifest dict. `stored` is False when the identical document was already on disk —
    the caller resumes rather than re-downloading, and the sidecar keeps its first-seen time.
    """
    record = validate(retrieved)
    folder = case_dir(county, case)
    folder.mkdir(parents=True, exist_ok=True)
    digest = record['sha256']
    pdf_path = folder / (digest[:16] + '.pdf')
    meta_path = folder / (digest[:16] + '.json')
    manifest = {
        'schema_version': SCHEMA_VERSION,
        'county': county, 'case': case,
        'source_ref': source_ref, 'document_name': doc_name,
        'transport': record.get('transport'),
        'source_urls': record.get('source_urls') or [],
        'record_key': record.get('record_key'),
        'sha256': digest, 'rebuilt_sha256': record.get('rebuilt_sha256'),
        'bytes': record['bytes'],
        'pages': record['pages_received'],
        'pages_expected': record.get('pages_expected'),
        'page_count_source': record.get('page_count_source'),
        'page_count_verified': record['page_count_verified'],
        'page_count_note': record.get('page_count_note'),
        'retrieved_at': _now(),
        'path': str(pdf_path), 'meta_path': str(meta_path),
        # Set by the reader, not here. Until then this document has been OBTAINED, not read.
        'read_status': 'unread',
    }
    if pdf_path.exists() and meta_path.exists():
        try:
            prior = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prior = {}
        # Re-hash the FILE, not the sidecar's claim about it. A sidecar saying "sha256 X" proves
        # only what we meant to write; a crash mid-write, or anything that touched the folder
        # since, leaves a file that no longer matches, and dedupe would hand that file to the
        # reader forever without ever looking at it.
        on_disk = sha256(pdf_path.read_bytes())
        if prior.get('sha256') == digest and prior.get('rebuilt_sha256') in (None, on_disk):
            prior['stored'] = False
            prior['last_seen_at'] = manifest['retrieved_at']
            prior['integrity_rechecked'] = True
            # A later fetch that now HAS an index count upgrades the verdict; it never downgrades
            # a verified document to unverified.
            if manifest['page_count_verified'] and not prior.get('page_count_verified'):
                prior.update({k: manifest[k] for k in
                              ('page_count_verified', 'page_count_note', 'pages_expected',
                               'page_count_source')})
            _atomic_write_text(meta_path, json.dumps(prior, indent=2) + '\n')
            return prior
        # Same source bytes, different file on disk: the stored copy is damaged. Replace it and
        # say so, rather than trusting either side silently.
        if prior.get('sha256') == digest:
            manifest['replaced_damaged_copy'] = True
    _atomic_write_bytes(pdf_path, record['content'])
    manifest['stored'] = True
    _atomic_write_text(meta_path, json.dumps(manifest, indent=2) + '\n')
    return manifest


def load_manifest(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


# ---- reading ----------------------------------------------------------------------------------
# WHY THERE ARE THREE SIGNALS AND NOT ONE
# The first version of this module used a character floor alone, and the Miami pilot walked
# straight through it on 2026-09-22: every page of the five-page Garden Lake Towers judgment is
# 100% raster, and the only text on each one is the clerk's 75-character watermark
# ("NOT AN OFFICIAL COPY - PUBLIC ACCESS..."). 75 > 40, so all five pages scored `text`, the
# document was reported `read`, and nothing had been read at all. Had that watermark happened to
# carry a dollar figure, it would have been reported as a judgment amount.
#
# A stamp is text. That is the whole problem, and no character count can tell a stamp from a page.
# So a page is weak if ANY of these holds:
#
#   too little text        under `min_chars` — a page with nothing on it
#   mostly image           raster images cover >= MAX_IMAGE_COVERAGE of the page. This is the one
#                          that catches the pilot: a scan is an image of a page, whatever text is
#                          stamped over it.
#   garbled glyphs         a broken embedded font extracts as noise that passes a length check
#
# and one document-level signal, because a watermark is identical on every page while real pages
# never are: if every page's text normalises to the same string, that text is boilerplate and none
# of the pages have been read.
MIN_PAGE_CHARS = 40
MAX_IMAGE_COVERAGE = 0.10
MIN_SANE_RATIO = 0.70
OCR_DPI = 300

_SANE = set(' \t\r\n0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            '.,;:\'"()[]/$%&#*+-=<>?!@_|\\')


def _sane_ratio(text):
    stripped = text.strip()
    if not stripped:
        return 1.0
    return sum(1 for ch in stripped if ch in _SANE) / len(stripped)


def _image_coverage(page):
    """Fraction of the page covered by raster images, clamped to 1.0.

    Overlapping images would double-count, so the sum is capped; for the question being asked —
    "is this a picture of a page?" — a cap is the right conservative shape.
    """
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0
    area = abs(page.rect)
    if area <= 0:
        return 0.0
    covered = 0.0
    for info in infos:
        try:
            box = _fitz().Rect(info['bbox']) & page.rect
            covered += abs(box)
        except Exception:
            continue
    return min(covered / area, 1.0)


def _boilerplate_key(text):
    """Normalise a page's text so a repeated stamp compares equal across pages.

    Digits go too: "Page 1 of 5" and "Page 2 of 5" are the same boilerplate.
    """
    return re.sub(r'[^a-z]+', '', (text or '').lower())


def _weakness(chars, coverage, sane, min_chars):
    if chars < min_chars:
        return 'too little extractable text (%d chars)' % chars
    if coverage >= MAX_IMAGE_COVERAGE:
        return 'page is %.0f%% raster image; embedded text is a stamp, not the page' % (coverage * 100)
    if sane < MIN_SANE_RATIO:
        return 'extracted text is %.0f%% unreadable glyphs' % ((1 - sane) * 100)
    return ''


def winocr(paths, timeout=600):
    """Windows' built-in OCR, via the helper fl_lp/broward_pin.py already drives.

    Read-only use of that module: the 14-page sampling parcel reader there is untouched, this
    only borrows its PowerShell bridge. Raises OcrUnavailable off Windows, or with no PowerShell,
    which is a recorded per-page reason and never a silent empty result.
    """
    from fl_lp.broward_pin import ocr_images
    return ocr_images(list(paths), timeout=timeout)


def ocr_unavailable_error():
    from fl_lp.broward_pin import OcrUnavailable
    return OcrUnavailable


def _render_and_ocr(doc, indexes, backend, keep_dir=None, dpi=OCR_DPI):
    """Render weak pages and OCR them. -> ({page_no: text}, {page_no: error}, {page_no: image}).

    When `keep_dir` is given the rendered PNG is kept, so a human can eyeball the page the OCR
    text came from. That matters more here than usual: an OCR'd digit in a judgment total is the
    difference between the right mortgage and the wrong one.
    """
    fitz = _fitz()
    workdir = keep_dir or tempfile.mkdtemp(prefix='dealflow-ocr-')
    os.makedirs(workdir, exist_ok=True)
    paths = {}
    errors = {}
    text = {}
    images = {}
    for index in indexes:
        target = os.path.join(workdir, 'p%d.png' % (index + 1))
        try:
            doc.load_page(index).get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).save(target)
            paths[target] = index + 1
        except Exception as exc:
            errors[index + 1] = 'could not render this page for OCR: %s' % str(exc)[:160]
    if paths:
        try:
            got = backend(list(paths))
        except Exception as exc:
            # Every requested page carries the SAME reason. An OCR bridge that cannot run is a
            # recorded gap on each page, not a quietly empty read.
            reason = '%s: %s' % (type(exc).__name__, str(exc)[:200])
            errors.update({page: reason for page in paths.values()})
            got = {}
        for path, page in paths.items():
            value = got.get(path)
            if page in errors:
                continue
            if value:
                text[page] = value
                if keep_dir:
                    images[page] = path
            else:
                errors[page] = 'OCR returned no text for this page'
    if not keep_dir:
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass
    return text, errors, images


def read_pages(content_or_path, min_chars=MIN_PAGE_CHARS, ocr=None, keep_images_in=None):
    """Read a document. EVERY page gets an outcome; none is skipped and none is assumed.

    outcomes: 'text'       an embedded text layer we can quote from
              'ocr_text'   no usable embedded text; these words came from OCR of the page image
              'needs_ocr'  no usable text and OCR did not run or returned nothing
              'unreadable' the page itself failed to open

    `ocr` is a callable taking a list of PNG paths and returning {path: text}; pass
    `document_store.winocr` for the Windows bridge. None means do not OCR, and weak pages stay
    `needs_ocr` — honest, and not a claim that the page is blank.
    """
    fitz = _fitz()
    if isinstance(content_or_path, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(content_or_path), filetype='pdf')
    else:
        doc = fitz.open(str(content_or_path))
    pages = []
    try:
        for index in range(doc.page_count):
            entry = {'page': index + 1, 'chars': 0, 'text': '', 'outcome': 'unreadable',
                     'text_source': None, 'image_coverage': None}
            try:
                page = doc.load_page(index)
                text = page.get_text('text') or ''
                coverage = _image_coverage(page)
            except Exception as exc:
                entry['error'] = str(exc)[:200]
                pages.append(entry)
                continue
            chars = len(text.strip())
            sane = _sane_ratio(text)
            weak = _weakness(chars, coverage, sane, min_chars)
            entry.update({'text': text, 'chars': chars, 'image_coverage': round(coverage, 4),
                          'sane_ratio': round(sane, 3)})
            if weak:
                entry.update({'outcome': 'needs_ocr', 'weak_reason': weak,
                              'embedded_text': text})
                entry['text'] = ''          # a stamp is not this page's text
            else:
                entry.update({'outcome': 'text', 'text_source': 'embedded'})
            pages.append(entry)

        # Document-level: identical text on every page is boilerplate, not content.
        readable = [p for p in pages if p['outcome'] == 'text']
        if len(readable) > 1 and len({_boilerplate_key(p['text']) for p in readable}) == 1:
            for p in readable:
                p.update({'outcome': 'needs_ocr', 'text_source': None,
                          'embedded_text': p['text'], 'text': '',
                          'weak_reason': 'identical text on every page: a stamp, not the page'})

        # OCR whatever is still weak.
        weak_indexes = [p['page'] - 1 for p in pages if p['outcome'] == 'needs_ocr']
        if ocr is not None and weak_indexes:
            got, errors, images = _render_and_ocr(doc, weak_indexes, ocr, keep_dir=keep_images_in)
            for p in pages:
                value = got.get(p['page'])
                if value and len(value.strip()) >= min_chars:
                    p.update({'outcome': 'ocr_text', 'text': value,
                              'chars': len(value.strip()), 'text_source': 'ocr'})
                    if images.get(p['page']):
                        p['image'] = images[p['page']]
                elif p['page'] in errors:
                    p['ocr_error'] = errors[p['page']]
                elif p['page'] in got:
                    p['ocr_error'] = 'OCR text was below the %d-character floor' % min_chars
    finally:
        doc.close()
    unresolved = [p['page'] for p in pages if p['outcome'] not in ('text', 'ocr_text')]
    return {'pages': pages,
            'page_count': len(pages),
            'pages_with_text': sum(1 for p in pages if p['outcome'] == 'text'),
            'pages_from_ocr': sum(1 for p in pages if p['outcome'] == 'ocr_text'),
            'pages_unresolved': unresolved,
            'ocr_attempted': ocr is not None,
            # 'read' needs an outcome on every page. `image_only` is a real state, not a failure:
            # it says the county gave us pictures and nothing has read them yet.
            'read_status': ('read' if pages and not unresolved
                            else 'image_only' if pages and not any(
                                p['outcome'] in ('text', 'ocr_text') for p in pages)
                            else 'partial' if pages else 'empty'),
            'complete': bool(pages) and not unresolved}


def record_read(meta_path, reading):
    """Fold a reading result back into the stored sidecar. Text itself is NOT written to the
    sidecar — it is the document's content and belongs in the document, not duplicated beside it."""
    manifest = load_manifest(meta_path)
    manifest.update({
        'read_status': reading['read_status'],
        'pages_with_text': reading['pages_with_text'],
        'pages_from_ocr': reading.get('pages_from_ocr', 0),
        'pages_unresolved': reading['pages_unresolved'],
        'ocr_attempted': reading.get('ocr_attempted', False),
        'weak_pages': [{'page': p['page'], 'reason': p.get('weak_reason'),
                        'image_coverage': p.get('image_coverage'),
                        'ocr_error': p.get('ocr_error')}
                       for p in reading['pages'] if p.get('weak_reason')],
        'read_at': _now(),
    })
    _atomic_write_text(meta_path, json.dumps(manifest, indent=2) + '\n')
    return manifest
