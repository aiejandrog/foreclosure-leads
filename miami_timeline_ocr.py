"""Free, hash-bound supplemental OCR; does not replace canonical embedded text."""
import copy
import hashlib
import json
from pathlib import Path
import document_store as DS


def supplement(row, cache_dir, backend=None):
    result = copy.deepcopy(row)
    pages = (result.get('reading') or {}).get('pages', [])
    targets = [p for p in pages if p.get('outcome') == 'text' or p.get('text_source') == 'embedded']
    if not targets: return result
    manifest = row.get('manifest') or {}
    path = manifest.get('path') or manifest.get('pdf_path') or row.get('path')
    result['supplemental_ocr_gaps'] = []
    try:
        source = Path(path).read_bytes()
        digest = hashlib.sha256(source).hexdigest()
    except (OSError, TypeError) as exc:
        for page in targets:
            reason = 'Original PDF unavailable for supplemental OCR: ' + type(exc).__name__
            page['supplemental_ocr'] = {'outcome': 'ocr_failed', 'reason': reason}
            result['supplemental_ocr_gaps'].append({'page': page['page'], 'reason': reason})
        return result
    cache = Path(cache_dir) / (digest + '-ocr300-v1.json')
    try:
        saved = json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('sha256') != digest: saved = {}
    except (OSError, ValueError): saved = {}
    saved.setdefault('sha256', digest); saved.setdefault('pages', {})
    missing = [p for p in targets if str(p['page']) not in saved['pages']]
    if missing:
        doc = DS._fitz().open(stream=source, filetype='pdf')
        try:
            # One page per checkpoint avoids repeating completed work after interruption.
            for page in missing:
                n = page['page']
                text, errors, _ = DS._render_and_ocr(doc, [n-1], backend or DS.winocr, dpi=300, gray_cutoff=0)
                outcome = {'outcome': 'ocr_text', 'text': text[n], 'dpi': 300} if text.get(n) else {'outcome': 'ocr_failed', 'reason': errors.get(n, 'OCR returned no text'), 'dpi': 300}
                saved['pages'][str(n)] = outcome
                cache.parent.mkdir(parents=True, exist_ok=True)
                DS._atomic_write_text(str(cache), json.dumps(saved, ensure_ascii=False, indent=2))
        finally:
            doc.close()
    for page in targets:
        outcome = copy.deepcopy(saved['pages'][str(page['page'])])
        page['supplemental_ocr'] = outcome
        if outcome['outcome'] != 'ocr_text': result['supplemental_ocr_gaps'].append({'page': page['page'], 'reason': outcome['reason']})
    return result
