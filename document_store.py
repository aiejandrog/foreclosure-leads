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
        if prior.get('sha256') == digest:
            prior['stored'] = False
            prior['last_seen_at'] = manifest['retrieved_at']
            # A later fetch that now HAS an index count upgrades the verdict; it never downgrades
            # a verified document to unverified.
            if manifest['page_count_verified'] and not prior.get('page_count_verified'):
                prior.update({k: manifest[k] for k in
                              ('page_count_verified', 'page_count_note', 'pages_expected',
                               'page_count_source')})
            meta_path.write_text(json.dumps(prior, indent=2) + '\n', encoding='utf-8')
            return prior
    pdf_path.write_bytes(record['content'])
    manifest['stored'] = True
    meta_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def load_manifest(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


# ---- reading ----------------------------------------------------------------------------------
# A page whose embedded text layer yields fewer than this many characters is treated as an image.
# Scanned clerk documents routinely carry a handful of stray characters from a stamp or a fax
# header; accepting those as "the page" is how a 5-page judgment reads as blank and is called read.
MIN_PAGE_CHARS = 40


def read_pages(content_or_path, min_chars=MIN_PAGE_CHARS):
    """Extract embedded text per page. EVERY page gets an outcome; none is skipped.

    outcomes: 'text'       — an embedded text layer we can quote from
              'needs_ocr'  — the page rendered but carries no usable text (a scan)
              'unreadable' — the page itself failed to open
    """
    fitz = _fitz()
    if isinstance(content_or_path, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(content_or_path), filetype='pdf')
    else:
        doc = fitz.open(str(content_or_path))
    pages = []
    try:
        for index in range(doc.page_count):
            entry = {'page': index + 1, 'chars': 0, 'text': '', 'outcome': 'unreadable'}
            try:
                text = doc.load_page(index).get_text('text') or ''
            except Exception as exc:                       # a single corrupt page must not lose the rest
                entry['error'] = str(exc)[:200]
                pages.append(entry)
                continue
            entry['text'] = text
            entry['chars'] = len(text.strip())
            entry['outcome'] = 'text' if entry['chars'] >= min_chars else 'needs_ocr'
            pages.append(entry)
    finally:
        doc.close()
    unresolved = [p['page'] for p in pages if p['outcome'] != 'text']
    return {'pages': pages,
            'page_count': len(pages),
            'pages_with_text': sum(1 for p in pages if p['outcome'] == 'text'),
            'pages_unresolved': unresolved,
            # 'read' requires an outcome of 'text' on every page. Anything else is 'partial', and
            # a document with zero readable pages is 'image_only' — a real state, not a failure.
            'read_status': ('read' if pages and not unresolved
                            else 'image_only' if pages and not any(p['outcome'] == 'text' for p in pages)
                            else 'partial' if pages else 'empty'),
            'complete': bool(pages) and not unresolved}


def record_read(meta_path, reading):
    """Fold a reading result back into the stored sidecar. Text itself is NOT written to the
    sidecar — it is the document's content and belongs in the document, not duplicated beside it."""
    manifest = load_manifest(meta_path)
    manifest.update({
        'read_status': reading['read_status'],
        'pages_with_text': reading['pages_with_text'],
        'pages_unresolved': reading['pages_unresolved'],
        'read_at': _now(),
    })
    with open(meta_path, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
        fh.write('\n')
    return manifest
