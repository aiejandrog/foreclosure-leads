"""Persistent full-case collection and subscription interpretation.

collect --county MIAMI-DADE --case CASE [--records private.json]
resume --county MIAMI-DADE --case CASE [--limit 10] [--interpret]
report --county MIAMI-DADE --case CASE
Collection and interpretation completion are distinct, evidence-backed states.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid
import case_dossier

import case_review
import document_agents as agents
import document_classify as classify
import document_collectors as collectors
import document_store as store
from document_queue import DocumentQueue

VERSION = 1


def page_inventory_complete(result):
    expected = result.get('manifest', {}).get('pages')
    numbers = [page.get('page') for page in result.get('reading', {}).get('pages', [])]
    return (type(expected) is int and expected > 0
            and all(type(number) is int for number in numbers)
            and sorted(numbers) == list(range(1, expected + 1)))


def interpretation_complete(result):
    interpretation = result.get('interpretation', {})
    return (page_inventory_complete(result)
            and interpretation.get('status') == 'complete'
            and interpretation.get('pages_assessed') == result['manifest']['pages']
            and not interpretation.get('unresolved'))


def folder(county, case):
    return case_review.output_path('document_pipeline/' + store._slug(county) + '/' + store._slug(case) + '/inventory.json').parent


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    store._atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False))


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def collect(county, case, records=None):
    base = folder(county, case)
    client = collectors.collector_for(county)
    inventory = client.enumerate_documents(case)
    jobs = []
    for entry in inventory['entries']:
        count = entry['expected_documents']
        if count == 0:
            entry['inventory_status'] = 'county_reports_no_document'
            continue
        try:
            attachments = client.attachments(case, entry['metadata'])
            entry['attachments'] = attachments
            entry['inventory_status'] = 'enumerated'
            for index, attachment in enumerate(attachments):
                ref = 'court:' + entry['source_id'] + ':' + str(attachment.get('documentID', index))
                jobs.append((ref, 'acquire', attachment))
        except (collectors.AccessGap, ValueError, OSError) as exc:
            entry.update(inventory_status='gap', gap=str(exc)[:300])
        except Exception as exc:
            entry.update(inventory_status='gap', gap=type(exc).__name__)
    for record in records or []:
        ref = 'recorded:' + str(record.get('cfN_MASTER_ID') or record.get('instrument') or '')
        if ref == 'recorded:':
            raise ValueError('Recorded document missing stable identifier')
        jobs.append((ref, 'acquire', record))
    inventory.update(county=county, case=case, official_records_supplied=records is not None,
                     recorded_search_may_be_capped=bool(records and len(records) >= 500))
    write(base / 'inventory.json', inventory)
    with DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        queue.add_many(county, case, jobs)
    return report(county, case)


def resume(county, case, limit=10, interpret=False):
    base = folder(county, case)
    if not (base / 'inventory.json').exists():
        raise ValueError('Collect the case inventory first')
    client = collectors.collector_for(county)
    owner = uuid.uuid4().hex
    with DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        for job in queue.jobs(county, case):
            if job['kind'] != 'acquire':
                continue
            if limit <= 0:
                break
            ref = job['source_ref']
            key = hashlib.sha256(ref.encode()).hexdigest()
            saved = base / (key + '.json')
            result = load(saved)
            if (job['status'] == 'done' and result and page_inventory_complete(result)
                    and result.get('reading', {}).get('read_status') == 'read'
                    and (not interpret or interpretation_complete(result))):
                continue
            if job['status'] != 'done':
                claimed = queue.claim_ref(owner, county, case, ref, 'acquire')
                if claimed is None:
                    continue
                try:
                    retrieved = client.retrieve_document(job['payload'])
                    manifest = store.store(county, case, retrieved, source_ref=ref,
                        doc_name=job['payload'].get('documentName') or job['payload'].get('doC_TYPE', ''))
                    result = {'manifest': manifest, 'source_ref': ref}
                    write(saved, result)
                    if not queue.complete(claimed['id'], owner, sha256=manifest['document_key']):
                        raise RuntimeError('Collection lease lost')
                except collectors.AccessGap as exc:
                    queue.gap(claimed['id'], owner, exc)
                    continue
                except Exception as exc:
                    queue.fail(claimed['id'], owner, type(exc).__name__)
                    continue
            if not result:
                continue
            # Each extraction has its own persistent lease; a completed download is not a completed read.
            queue.add(county, case, ref, 'read')
            reading_job = queue.claim_ref(owner, county, case, ref, 'read', reader_version=VERSION,
                                          can_ocr=True, force=not page_inventory_complete(result))
            if reading_job:
                try:
                    manifest = result['manifest']
                    images = base / (key + '-pages')
                    reading = store.read_pages(manifest['path'], ocr=store.winocr,
                        keep_images_in=images, gray_cutoff=0)
                    store.record_read(manifest['meta_path'], reading)
                    result.update(reading=reading, classification=classify.classify(reading, manifest['document_name']),
                                  references=classify.cited_instruments(reading))
                    write(saved, result)
                    queue.complete(reading_job['id'], owner, reader_version=VERSION, read_status=reading['read_status'])
                except Exception as exc:
                    queue.fail(reading_job['id'], owner, type(exc).__name__)
                    continue
            if interpret and result.get('reading'):
                verdict = interpret_document(base, key, result)
                if verdict.get('resumable'):
                    break
            limit -= 1
    return report(county, case)


def interpret_document(base, key, result):
    manifest, reading = result['manifest'], result['reading']
    if not page_inventory_complete(result):
        result['interpretation'] = {
            'status': 'incomplete', 'findings': [], 'pages_assessed': 0,
            'unresolved': ['Page inventory does not match the stored document; re-extract all pages.']}
        write(base / (key + '.json'), result)
        return dict(result['interpretation'], resumable=False)
    digest = hashlib.sha256(Path(manifest['path']).read_bytes()).hexdigest()
    findings, gaps = [], []
    for start in range(0, len(reading['pages']), 5):
        batch = reading['pages'][start:start + 5]
        pages = [dict(document_hash=digest, page=p['page'], text=p.get('text', ''),
                      **({'image_path': p['image']} if p.get('image') else {})) for p in batch]
        checkpoint = base / (key + '-interpret-' + str(start) + '.json')
        cached = load(checkpoint)
        if cached and cached.get('digest') == digest and cached.get('status') == 'complete':
            findings.extend(cached['findings']); gaps.extend(cached.get('unresolved', [])); continue
        reader = agents.run_agent('reader', pages, timeout=300)
        if reader['status'] != 'complete':
            write(checkpoint, reader); return reader
        verified = agents.run_agent('verifier', pages, reader['findings'], timeout=300)
        # Reader uncertainties are evidence gaps even when the verifier accepts other facts.
        verified['unresolved'] = reader.get('unresolved', []) + verified.get('unresolved', [])
        write(checkpoint, dict(verified, digest=digest))
        if verified['status'] != 'complete':
            return verified
        findings.extend(verified['findings'])
        gaps.extend(verified.get('unresolved', []))
    result['interpretation'] = {'status': 'complete' if not gaps else 'incomplete',
        'findings': findings, 'unresolved': gaps, 'pages_assessed': len(reading['pages'])}
    write(base / (key + '.json'), result)
    return dict(result['interpretation'], resumable=False)


def report(county, case):
    base = folder(county, case)
    inventory = load(base / 'inventory.json', {})
    rows = [load(p) for p in base.glob('*.json') if len(p.stem) == 64]
    rows = [r for r in rows if r and 'manifest' in r]
    gaps = [e.get('gap') for e in inventory.get('entries', []) if e.get('inventory_status') == 'gap']
    if not inventory.get('pagination_verified'):
        gaps.append('Full docket pagination not yet corroborated')
    if not inventory.get('official_records_supplied'):
        gaps.append('Official records search not completed')
    if inventory.get('recorded_search_may_be_capped'):
        gaps.append('Official records search may be truncated at 500')
    with DocumentQueue(str(base / 'queue.sqlite3')) as queue:
        jobs = queue.jobs(county, case)
    outstanding = [j for j in jobs if j['status'] != 'done']
    acquisition_outstanding = [j for j in outstanding if j['kind'] == 'acquire']
    findings = [f for row in rows for f in row.get('interpretation', {}).get('findings', []) if f.get('status') == 'verified']
    unresolved_refs = []
    for row in rows:
        for reference in row.get('references', []):
            matches = []
            for target in rows:
                record_key = target['manifest'].get('record_key') or {}
                book, first = record_key.get('book'), record_key.get('page')
                if str(book) == str(reference['book']) and first is not None:
                    if int(first) <= int(reference['page_no']) < int(first) + target['manifest']['pages']:
                        matches.append(target['manifest']['document_key'])
            reference['fetched'] = bool(matches)
            reference['resolved_document_keys'] = sorted(set(matches))
            if not matches:
                unresolved_refs.append(dict(reference, parent_source_ref=row['source_ref']))
    if unresolved_refs:
        gaps.append('%d cited references need county lookup' % len(unresolved_refs))
    dossier_rows = [dict(r['manifest'], source_ref=r['source_ref'], status='stored',
        reading=r.get('reading'), classification=r.get('classification'),
        cited_instruments=r.get('references', []), read_status=r.get('reading', {}).get('read_status', 'unread')) for r in rows]
    chains = load(Path(__file__).parent / 'records_liens.json', {})
    dossier = case_dossier.build(case, county, inventory=inventory, chain=chains.get(case), documents=dossier_rows)
    dossier['c_documents']['verified_findings'] = findings
    dossier['c_documents']['interpretation_complete'] = bool(rows) and all(interpretation_complete(r) for r in rows)
    dossier['complete'] = False  # Cross-document reconciliation and full-source coverage still required.
    write(base / 'dossier.json', dossier)
    return {'case': case, 'county': county, 'entries': len(inventory.get('entries', [])),
        'documents_obtained': len(rows), 'pages_obtained': sum(r['manifest']['pages'] for r in rows),
        'pages_extracted': sum(sum(p['outcome'] in ('text', 'ocr_text') for p in r.get('reading', {}).get('pages', [])) for r in rows),
        'verified_findings': findings, 'gaps': gaps, 'unresolved_references': unresolved_refs, 'outstanding_jobs': len(outstanding),
        'collection_complete': bool(rows) and not gaps and not acquisition_outstanding,
        'interpretation_complete': bool(rows) and all(interpretation_complete(r) for r in rows),
        'document_summaries': [{'source_ref': r['source_ref'], 'classification': r.get('classification'),
            'references': r.get('references', []), 'interpretation': r.get('interpretation', {}).get('status', 'pending')} for r in rows]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['collect', 'resume', 'report'])
    parser.add_argument('--case', required=True)
    parser.add_argument('--county', required=True)
    parser.add_argument('--records')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--interpret', action='store_true')
    args = parser.parse_args()
    if args.command == 'collect':
        result = collect(args.county, args.case, load(args.records) if args.records else None)
    elif args.command == 'resume':
        result = resume(args.county, args.case, args.limit, args.interpret)
    else:
        result = report(args.county, args.case)
    write(folder(args.county, args.case) / 'report.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('verified_findings', 'document_summaries')}, indent=2))


if __name__ == '__main__':
    main()
