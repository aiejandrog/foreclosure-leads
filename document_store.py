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
    """The hash of what the CLERK served on THIS fetch. A per-fetch record, NOT an identity.

    Two things are true and were confused in the first version of this module:

      * PyMuPDF's `tobytes()` is not byte-reproducible — it writes a fresh document ID on every
        call, so hashing the file we rebuild is not stable either;
      * and, the 2026-09-22 pilot proved, **neither is what the clerk sends**. The same five pages
        of book 35287 page 4642 hashed `3f37483d...` on one fetch and `94f3f6cf...` on the next.
        The county re-generates the PDF per request; something inside it (a timestamp, a document
        ID) differs each time.

    So neither hash can be a filename, and both were tried. Identity is `document_key` below.
    This value stays because it is worth recording what arrived on a given fetch — it just cannot
    answer "do we already have this document?".
    """
    pages = retrieved.get('pages')
    if pages:
        return hashlib.sha256(b''.join(pages)).hexdigest()
    return hashlib.sha256(retrieved.get('content') or b'').hexdigest()


def document_key(county, case, record, source_ref=''):
    """Stable identity of a DOCUMENT: who published it and which instrument it is.

    Not the bytes. The bytes change between fetches (see `source_digest`), and a filename that
    changes between fetches means the same document is stored twice, the queue's dedupe never
    matches, and a resumed run re-downloads everything. That is precisely what happened on the
    pilot: two copies of the same five-page judgment in one case folder.

    The key is built from the identifiers that name the instrument in the county's own index —
    book, page, book type, CFN for a recorded instrument — plus the page count, so a document
    re-recorded at a different length is a different key rather than a silent overwrite.

    Returns (key, basis). `basis` says what the key rests on, because it matters how strong the
    identity is: 'record_key' is the county's own, 'source_ref' is ours, and 'source_bytes' is the
    last resort and is NOT stable — a document identified that way will re-store on every fetch,
    which is visible in the sidecar rather than hidden.
    """
    keys = record.get('record_key') or {}
    pages = record.get('pages_received') or ''
    if keys:
        basis = 'record_key'
        ident = '|'.join('%s=%s' % (k, keys[k]) for k in sorted(keys) if keys[k] not in (None, ''))
    elif source_ref:
        basis = 'source_ref'
        ident = str(source_ref)
    else:
        basis = 'source_bytes'
        ident = record.get('sha256') or ''
    material = '\x1f'.join([str(county), str(case), str(record.get('transport') or ''),
                            ident, str(pages)])
    return hashlib.sha256(material.encode('utf-8')).hexdigest(), basis


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
    """Write the original + its provenance sidecar, keyed by document identity.

    Returns the manifest dict. `stored` is False when we already hold this document — which is
    decided by `document_key`, not by the bytes: the county re-serialises the PDF on every request,
    so the same instrument arrives with a different hash each time and byte identity would store
    it again under a new name every run.

    Each fetch is appended to `fetches` with the hash of what arrived, so "the clerk's bytes
    changed between these two fetches" stays visible instead of becoming a second copy.
    """
    record = validate(retrieved)
    folder = case_dir(county, case)
    folder.mkdir(parents=True, exist_ok=True)
    key, basis = document_key(county, case, record, source_ref)
    source_sha = record['sha256']
    stem = key[:16]
    pdf_path = folder / (stem + '.pdf')
    meta_path = folder / (stem + '.json')
    fetch = {'at': _now(), 'source_sha256': source_sha, 'bytes': record['bytes'],
             'rebuilt_sha256': record.get('rebuilt_sha256')}
    manifest = {
        'schema_version': SCHEMA_VERSION,
        'county': county, 'case': case,
        'source_ref': source_ref, 'document_name': doc_name,
        'transport': record.get('transport'),
        'source_urls': record.get('source_urls') or [],
        'record_key': record.get('record_key'),
        'document_key': key, 'identity_basis': basis,
        # What arrived on the latest fetch. Cite this for "what the county sent us on <date>";
        # never use it to decide whether we already have the document.
        'source_sha256': source_sha,
        'rebuilt_sha256': record.get('rebuilt_sha256'),
        'fetches': [fetch],
        'bytes': record['bytes'],
        'pages': record['pages_received'],
        'pages_expected': record.get('pages_expected'),
        'page_count_source': record.get('page_count_source'),
        'page_count_verified': record['page_count_verified'],
        'page_count_note': record.get('page_count_note'),
        'retrieved_at': fetch['at'],
        'path': str(pdf_path), 'meta_path': str(meta_path),
        # Set by the reader, not here. Until then this document has been OBTAINED, not read.
        'read_status': 'unread',
        'reader_version': None,
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
        intact = prior.get('rebuilt_sha256') in (None, on_disk)
        if intact and prior.get('pages') == record['pages_received']:
            prior['stored'] = False
            prior['last_seen_at'] = fetch['at']
            prior['integrity_rechecked'] = True
            prior['fetches'] = (prior.get('fetches') or [])[-9:] + [fetch]
            if prior.get('source_sha256') and prior['source_sha256'] != source_sha:
                # Expected, and worth saying out loud: it is why identity is not the bytes.
                prior['bytes_differ_between_fetches'] = True
            # A later fetch that now HAS an index count upgrades the verdict; it never downgrades
            # a verified document to unverified.
            if manifest['page_count_verified'] and not prior.get('page_count_verified'):
                prior.update({k: manifest[k] for k in
                              ('page_count_verified', 'page_count_note', 'pages_expected',
                               'page_count_source')})
            _atomic_write_text(meta_path, json.dumps(prior, indent=2) + '\n')
            return prior
        if not intact:
            # Same document, different file on disk: the stored copy is damaged. Replace it and
            # say so, rather than trusting either side silently.
            manifest['replaced_damaged_copy'] = True
        else:
            # Same key, different length. The key includes the page count, so this can only happen
            # when an older sidecar was written under a different scheme. Record the change.
            manifest['replaced_on_page_count_change'] = True
            manifest['prior_pages'] = prior.get('pages')
        manifest['fetches'] = (prior.get('fetches') or [])[-9:] + [fetch]
    _atomic_write_bytes(pdf_path, record['content'])
    manifest['stored'] = True
    _atomic_write_text(meta_path, json.dumps(manifest, indent=2) + '\n')
    return manifest


def reconcile(county, case, apply=False):
    """Find sidecars in a case folder that describe the SAME document and report the duplicates.

    The pilot left two copies of one five-page judgment because identity used to be the clerk's
    bytes and the clerk's bytes changed between fetches. Those copies are already on disk; this
    names them and, with apply=True, marks the losers `superseded`.

    It does NOT delete anything. These are homeowner court records obtained once; a wrong call
    here destroys evidence, and a superseded sidecar costs a few kilobytes. Grouping is by
    (source_ref, record_key, pages) — the same identity the new key is built from — and the
    keeper is the copy that has been read furthest, then the most recently retrieved.
    """
    folder = case_dir(county, case)
    if not folder.exists():
        return {'county': county, 'case': case, 'groups': [], 'duplicates': 0, 'applied': apply}
    rank = {'read': 3, 'partial': 2, 'image_only': 1}
    groups = {}
    for meta_path in sorted(folder.glob('*.json')):
        try:
            man = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if man.get('superseded'):
            continue
        keys = man.get('record_key') or {}
        ident = ('|'.join('%s=%s' % (k, keys[k]) for k in sorted(keys)) or man.get('source_ref')
                 or str(meta_path.name))
        groups.setdefault((ident, man.get('pages')), []).append((meta_path, man))
    out = []
    duplicates = 0
    for (ident, pages), members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        members.sort(key=lambda mp: (rank.get(mp[1].get('read_status'), 0),
                                     mp[1].get('reader_version') or 0,
                                     mp[1].get('retrieved_at') or ''), reverse=True)
        keeper, losers = members[0], members[1:]
        duplicates += len(losers)
        out.append({
            'identity': ident, 'pages': pages,
            'keep': {'meta_path': str(keeper[0]), 'read_status': keeper[1].get('read_status'),
                     'retrieved_at': keeper[1].get('retrieved_at')},
            'superseded': [{'meta_path': str(m[0]), 'read_status': m[1].get('read_status'),
                            'retrieved_at': m[1].get('retrieved_at'),
                            'source_sha256': m[1].get('source_sha256')} for m in losers],
        })
        if apply:
            for meta_path, man in losers:
                man['superseded'] = True
                man['superseded_by'] = str(keeper[0])
                man['superseded_at'] = _now()
                man['superseded_note'] = (
                    'same document as the keeper; stored twice because identity used to be the '
                    'clerk\'s bytes, which differ between fetches. File left on disk on purpose.')
                _atomic_write_text(meta_path, json.dumps(man, indent=2) + '\n')
    return {'county': county, 'case': case, 'groups': out, 'duplicates': duplicates,
            'applied': bool(apply)}


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
#
# READER_VERSION is the version of THAT judgement, and it is recorded on every document read.
# Bump it whenever read_pages gets materially better at reading a page. The queue uses it to
# decide whether a job marked `done` may be re-read: the 2026-09-22 pilot fetched this judgment
# with the character-floor reader, which called a watermark a read page and marked the job done,
# and the fixed reader then refused to look at it again. A document is not finished being read
# because an older, worse reader said so.
READER_VERSION = 5
MIN_PAGE_CHARS = 0  # Compatibility argument only; content is not judged by length.
MAX_IMAGE_COVERAGE = 0.10
MIN_SANE_RATIO = 0.70
OCR_DPI = 300

# WHY THE RENDER IS CLEANED BEFORE OCR
# Every page image the Miami clerk serves carries a light-grey diagonal watermark reading
# "NOT AN OFFICIAL COPY - PUBLIC ACCESS", repeated corner to corner. On the pilot judgment it runs
# straight through the amounts column, and the OCR errors line up with it exactly: 6,796.61 read
# as 5,796.61 and 2,010.74 read as "40" where the watermark crosses them, 317.05 and 31.19 dropped
# where it sits on them — while 4,056.25, 1,835.00 and the 14,698.60 grand total, which it does
# not touch, all read perfectly. Every Miami scan carries it, so this is the general case.
#
# The watermark is light grey and the print is black, so whitening everything above a grey cutoff
# removes it and leaves the text. The cutoff is a starting value, not a measured one: raise it if
# faint genuine print is being lost, lower it if watermark strokes survive. `--gray-cutoff 0`
# disables the cleaning entirely, and --keep-images keeps both renders so the two can be compared.
WATERMARK_GRAY_CUTOFF = 160

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
    if not chars:
        return 'no extractable content'
    if coverage >= MAX_IMAGE_COVERAGE:
        return 'page is %.0f%% raster image; embedded text is a stamp, not the page' % (coverage * 100)
    if sane < MIN_SANE_RATIO:
        return 'extracted text is %.0f%% unreadable glyphs' % ((1 - sane) * 100)
    return ''


def content_text(text):
    """Discard known clerk stamps, not short document content."""
    return '\n'.join(line for line in (text or '').splitlines()
                     if not re.search(r'NOT AN OFFICIAL COPY|PUBLIC ACCESS|^\s*Page \d+ of \d+\s*$',
                                      line, re.I)).strip()


def label_only(text):
    return bool(re.fullmatch(r'\s*exhibit\s*[\"\u201c\u201d\u2018\u2019\x27]?\s*[A-Za-z0-9]+\s*[\"\u201c\u201d\u2018\u2019\x27]?\s*', text or '', re.I))


READ_OUTCOMES = ('text', 'ocr_text', 'vision_text', 'read_as_label', 'exhibit_divider')
ASSESSED_OUTCOMES = READ_OUTCOMES + ('redacted_or_blank',)


def summarize_reading(reading):
    pages = reading['pages']
    unresolved = [p['page'] for p in pages if p.get('outcome') not in ASSESSED_OUTCOMES]
    gaps = [p['page'] for p in pages if p.get('outcome') not in READ_OUTCOMES]
    reading.update(page_count=len(pages), pages_unresolved=unresolved,
        pages_content_gaps=gaps, pages_assessed=len(pages)-len(unresolved),
        pages_with_text=sum(p.get('text_source') == 'embedded' for p in pages),
        pages_from_ocr=sum(p.get('text_source') == 'ocr' for p in pages),
        complete=bool(pages) and not gaps,
        read_status=('read' if pages and not gaps else 'partial' if any(
            p.get('outcome') in ASSESSED_OUTCOMES for p in pages) else 'image_only' if pages else 'empty'))
    return reading


def apply_page_assessment(reading, page_no, assessment):
    """Apply explicit image evidence; never turn a redacted page into a read page."""
    matches = [p for p in reading['pages'] if p['page'] == page_no]
    if len(matches) != 1:
        raise ValueError('page must occur exactly once')
    outcome = assessment.get('outcome')
    if outcome not in ('vision_text', 'read_as_label', 'exhibit_divider', 'redacted_or_blank', 'unreadable'):
        raise ValueError('invalid page assessment')
    if not assessment.get('confident'):
        outcome = 'unreadable'
    text = assessment.get('text') or ''
    if outcome in ('vision_text', 'read_as_label') and not content_text(text):
        outcome = 'unreadable'
    matches[0].update(outcome=outcome, text=text, chars=len(text), text_source='vision',
                      assessment=assessment)
    return summarize_reading(reading)


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


def _render_and_ocr(doc, indexes, backend, keep_dir=None, dpi=OCR_DPI,
                    gray_cutoff=WATERMARK_GRAY_CUTOFF):
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
    table = (bytes(255 if value >= gray_cutoff else value for value in range(256))
             if gray_cutoff else None)
    for index in indexes:
        target = os.path.join(workdir, 'p%d.png' % (index + 1))
        try:
            pix = doc.load_page(index).get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            if table is not None:
                if keep_dir:
                    # Keep the untouched render too, so the cleaning can be judged by eye rather
                    # than trusted. This is a read of a homeowner's court record; if the cutoff
                    # ate real print, that has to be visible.
                    pix.save(os.path.join(workdir, 'p%d-raw.png' % (index + 1)))
                pix = fitz.Pixmap(fitz.csGRAY, pix.width, pix.height,
                                  pix.samples.translate(table), False)
            pix.save(target)
            paths[target] = index + 1
            if keep_dir:
                images[index + 1] = target
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


def read_pages(content_or_path, min_chars=MIN_PAGE_CHARS, ocr=None, keep_images_in=None,
               gray_cutoff=WATERMARK_GRAY_CUTOFF):
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
                text = content_text(page.get_text('text') or '')
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
                entry.update({'outcome': 'read_as_label' if label_only(text) else 'text',
                              'text_source': 'embedded'})
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
            got, errors, images = _render_and_ocr(doc, weak_indexes, ocr,
                                                  keep_dir=keep_images_in,
                                                  gray_cutoff=gray_cutoff)
            for p in pages:
                if images.get(p['page']):
                    p['image'] = images[p['page']]
                value = content_text(got.get(p['page']))
                if value and _sane_ratio(value) >= MIN_SANE_RATIO:
                    p.update({'outcome': 'read_as_label' if label_only(value) else 'ocr_text', 'text': value,
                              'chars': len(value.strip()), 'text_source': 'ocr',
                              'watermark_removed': bool(gray_cutoff),
                              'gray_cutoff': gray_cutoff or None})
                    if images.get(p['page']):
                        p['image'] = images[p['page']]
                elif p['page'] in errors:
                    p['ocr_error'] = errors[p['page']]
                elif p['page'] in got:
                    # Short OCR can be an exhibit divider or a fragment of obscured content.
                    # Preserve it for image assessment, but do not admit it as complete text.
                    p['provisional_ocr_text'] = value
                    p['ocr_error'] = 'OCR returned only boilerplate or unreadable glyphs'
    finally:
        doc.close()
    # Repeated OCR boilerplate is no more evidence than repeated embedded stamps.
    content_pages = [p for p in pages if p['outcome'] in ('text', 'ocr_text')]
    if (len(content_pages) == len(pages) and len(pages) > 1
            and len({_boilerplate_key(p['text']) for p in content_pages}) == 1):
        for p in content_pages:
            p.update(outcome='needs_ocr', provisional_ocr_text=p['text'], text='',
                     weak_reason='identical boilerplate on every page', text_source=None)
    unresolved = [p['page'] for p in pages if p['outcome'] not in ('text', 'ocr_text')]
    return summarize_reading({'pages': pages,
            'reader_version': READER_VERSION,
            'gray_cutoff': gray_cutoff or None,
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
            'complete': bool(pages) and not unresolved})


def record_read(meta_path, reading):
    """Fold a reading result back into the stored sidecar. Text itself is NOT written to the
    sidecar — it is the document's content and belongs in the document, not duplicated beside it."""
    manifest = load_manifest(meta_path)
    manifest.update({
        'read_status': reading['read_status'],
        'reader_version': reading.get('reader_version', READER_VERSION),
        'pages_with_text': reading['pages_with_text'],
        'pages_from_ocr': reading.get('pages_from_ocr', 0),
        'pages_unresolved': reading['pages_unresolved'],
        'ocr_attempted': reading.get('ocr_attempted', False),
        # `weak_reason` is a verdict on the page's TEXT LAYER, not on the page. A scan whose
        # stamp was rejected and which OCR then read has a weak_reason AND outcome 'ocr_text' —
        # it was read. Carrying the outcome here is what lets a caller tell the two apart instead
        # of printing "NOT READ" over a page it just read, as the pilot run did.
        'weak_pages': [{'page': p['page'], 'reason': p.get('weak_reason'),
                        'outcome': p.get('outcome'),
                        'image_coverage': p.get('image_coverage'),
                        'ocr_error': p.get('ocr_error')}
                       for p in reading['pages'] if p.get('weak_reason')],
        'read_at': _now(),
    })
    _atomic_write_text(meta_path, json.dumps(manifest, indent=2) + '\n')
    return manifest


def save_page_text(manifest, reading):
    """Write the text of every page beside the PDF, and return the folder.

    The pilot run OCR'd all five pages and then threw the text away: the report carried page
    outcomes and no words, so the only way to see what OCR had actually read was to run it again
    by hand. Text that cost a 300-DPI render and an OCR pass is evidence; it gets written down.

    One `pNN.txt` per page plus `pages.json` with the outcome, source and character count. Inside
    the same guarded case folder as the PDF — never a path built by string concatenation, and
    never anywhere git or OneDrive can reach.
    """
    stem = (manifest.get('document_key') or manifest.get('sha256') or '')[:16]
    folder = case_dir(manifest['county'], manifest['case']) / (stem + '-text')
    folder.mkdir(parents=True, exist_ok=True)
    index = []
    for page in reading['pages']:
        name = 'p%02d.txt' % page['page']
        body = page.get('text') or page.get('embedded_text') or ''
        _atomic_write_text(folder / name, body)
        index.append({'page': page['page'], 'file': name, 'outcome': page['outcome'],
                      'text_source': page.get('text_source'), 'chars': page.get('chars'),
                      'weak_reason': page.get('weak_reason'),
                      'ocr_error': page.get('ocr_error'),
                      'image': page.get('image')})
    _atomic_write_text(folder / 'pages.json', json.dumps(
        {'county': manifest['county'], 'case': manifest['case'],
         'document_key': manifest.get('document_key'), 'source_ref': manifest.get('source_ref'),
         'reader_version': reading.get('reader_version'),
         'read_status': reading['read_status'], 'written_at': _now(),
         'pages': index}, indent=2) + '\n')
    return str(folder)


def stored_documents(county, case):
    """Every document already stored for this case, with the text we already read off it.

    WHY THIS EXISTS (2026-09-22). The pilot CLI fetched and read the Garden Lake Towers judgment
    four times into the case folder, wrote the page text beside it, and then every other tool in
    the project started from zero — `document_walk` could not see a single word of it, because the
    only way to get a reading was to fetch and read the document again. Text that cost a clerk
    fetch and an OCR pass is evidence on disk; reading it back is not a new capability, it is the
    one that was missing.

    Returns [(manifest, reading)] in a stable order. `reading` is the same shape `read_pages`
    returns, rebuilt from the saved `pNN.txt` files, so anything that consumes a reading —
    `document_classify`, `cited_instruments`, `judgment_amount_candidates` — works on it unchanged.
    A document stored but never read yields a reading with no page text and says so; it is not
    skipped, because "fetched and unread" is a different state from "not here".
    """
    folder = case_dir(county, case)
    out = []
    for meta_path in sorted(folder.glob('*.json')):
        try:
            manifest = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or not manifest.get('document_key'):
            continue
        if manifest.get('superseded'):
            continue
        manifest.setdefault('meta_path', str(meta_path))
        out.append((manifest, _read_back(folder, manifest)))
    return out


def _read_back(folder, manifest):
    """The saved page text for one stored document, as a reading. Never raises."""
    stem = (manifest.get('document_key') or manifest.get('sha256') or '')[:16]
    text_dir = folder / (stem + '-text')
    try:
        index = json.loads((text_dir / 'pages.json').read_text(encoding='utf-8'))
        rows = index['pages']
    except (OSError, ValueError, KeyError, TypeError):
        # Stored, never read — or the text folder was removed. Report the pages as unresolved
        # rather than returning nothing, so a caller can tell this from a case with no documents.
        pages = int(manifest.get('pages') or 0)
        return {'pages': [], 'page_count': pages, 'pages_with_text': 0, 'pages_from_ocr': 0,
                'pages_unresolved': list(range(1, pages + 1)),
                'read_status': 'not_read_back', 'complete': False, 'from_store': True}
    pages, unresolved, from_ocr = [], [], 0
    for row in rows:
        try:
            body = (text_dir / row['file']).read_text(encoding='utf-8')
        except (OSError, KeyError):
            body = ''
        if (row.get('text_source') or '') == 'ocr':
            from_ocr += 1
        if not body.strip():
            unresolved.append(row.get('page'))
        pages.append({'page': row.get('page'), 'text': body, 'chars': len(body),
                      'outcome': row.get('outcome'), 'text_source': row.get('text_source'),
                      'weak_reason': row.get('weak_reason'), 'ocr_error': row.get('ocr_error')})
    return {'pages': pages, 'page_count': len(pages),
            'pages_with_text': len(pages) - len(unresolved), 'pages_from_ocr': from_ocr,
            'pages_unresolved': unresolved,
            'read_status': index.get('read_status') or 'read',
            'reader_version': index.get('reader_version'),
            'complete': not unresolved, 'from_store': True}


def _main(argv=None):
    """`python document_store.py reconcile <county> <case> [--apply]` — nothing else.

    Reporting only by default; --apply marks duplicates superseded and deletes nothing.
    """
    import argparse
    parser = argparse.ArgumentParser(description='document store maintenance')
    sub = parser.add_subparsers(dest='command', required=True)
    rec = sub.add_parser('reconcile', help='report documents stored twice in one case folder')
    rec.add_argument('county')
    rec.add_argument('case')
    rec.add_argument('--apply', action='store_true',
                     help='mark the duplicates superseded (files are never deleted)')
    args = parser.parse_args(argv)
    result = reconcile(args.county, args.case, apply=args.apply)
    if not result['groups']:
        print('%s %s: no duplicates' % (args.county, args.case))
        return 0
    for group in result['groups']:
        print('%s (%s pages)' % (group['identity'], group['pages']))
        print('  KEEP       %s  read=%s  %s' % (group['keep']['meta_path'],
                                                group['keep']['read_status'],
                                                group['keep']['retrieved_at']))
        for loser in group['superseded']:
            print('  SUPERSEDED %s  read=%s  %s' % (loser['meta_path'], loser['read_status'],
                                                    loser['retrieved_at']))
    print('%d duplicate copy(ies)%s' % (result['duplicates'],
                                        '; marked superseded' if result['applied']
                                        else '; run again with --apply to mark them'))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
