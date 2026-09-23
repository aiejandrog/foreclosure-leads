"""Opt-in Miami title-interest investigation. Never changes equity or sends."""
import copy
import re
import json
from datetime import datetime, timezone


def record_from_manifest(manifest):
    key = manifest.get('record_key') or {}
    return {target:key.get(source) for target, source in (
        ('reC_BOOK','book'), ('reC_PAGE','page'), ('cfN_MASTER_ID','cfn_master_id'),
        ('doC_TYPE','doc_type'), ('reC_DATE','rec_date'), ('booK_TYPE','book_type'))}


def discovery_names(entry, title):
    candidates = [{'name':entry['owner'], 'why':'lead owner; title not independently established', 'source_ref':'lead'}]
    candidates.extend(title.get('search_names', []))
    candidates.extend(dict(p, why='OCS defendant; title interest not established')
                      for p in title.get('defendants', []))
    result, seen = [], set()
    for candidate in candidates:
        name = candidate['name'].strip()
        if name and name.upper() not in seen:
            seen.add(name.upper())
            result.append(candidate)
    return result


def reconcile_claims(claims, documents):
    from document_walk import key_of
    results = copy.deepcopy(claims)
    for claim in results:
        claim['satisfaction_status'] = 'unknown'
        claim['satisfaction_evidence'] = []
        for row in documents:
            kind = str((row.get('classification') or {}).get('text_kind') or
                       (row.get('classification') or {}).get('kind') or '')
            if not re.search(r'satisf|release|discharge', kind, re.I):
                continue
            for cite in row.get('cited_instruments') or []:
                if key_of(cite.get('book'), cite.get('page_no')) == key_of(claim.get('book'), claim.get('page_no')):
                    claim['satisfaction_status'] = 'referenced_release_found_unresolved'
                    claim['satisfaction_evidence'].append(dict(cite, source_ref=row.get('source_ref')))
    return results


def stored_evidence(case):
    """Recover the previous full-page reader's evidence, validating its PDF hash."""
    import hashlib
    from pathlib import Path
    import document_store as DS
    import document_walk as W
    rows = W.stored_rows('MIAMI-DADE', case)
    manifests = {m.get('document_key'):m for m, _ in DS.stored_documents('MIAMI-DADE', case)}
    valid = {}
    for key, manifest in manifests.items():
        pdf = Path(manifest.get('path') or '')
        expected = manifest.get('rebuilt_sha256') or manifest.get('source_sha256')
        if pdf.is_file() and hashlib.sha256(pdf.read_bytes()).hexdigest() == expected:
            valid[key] = manifest
    rows = [dict(row, stored=valid[row['document_key']],
                 doc_type=valid[row['document_key']].get('document_name') or row.get('doc_type'))
            for row in rows if row.get('document_key') in valid]
    manifests = valid
    for path in DS.pipeline_folder('MIAMI-DADE', case).glob('*.json'):
        saved = DS.pipeline_load(path, {})
        manifest = saved.get('manifest') or {}
        current = manifests.get(manifest.get('document_key'))
        if not current or current.get('case') != case:
            continue
        pdf = Path(current['path'])
        expected = current.get('rebuilt_sha256') or current.get('source_sha256')
        if not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != expected:
            continue
        reading = saved.get('reading') or {}
        if not DS.page_inventory_complete({'manifest':current, 'reading':reading}):
            continue
        rows = [r for r in rows if r.get('document_key') != current['document_key']]
        rows.append({'source_ref':current['source_ref'], 'stored':current,
                     'document_key':current['document_key'], 'path':current['path'],
                     'doc_type':current.get('document_name'), 'pages':current.get('pages'),
                     'status':'stored', 'reading':reading, 'read_status':reading.get('read_status')})
    return rows, [record_from_manifest(m) for m in manifests.values() if m.get('record_key')]


class CapturedSearch:
    """Reuse successful results within a run; a failed search is not an empty result."""
    def __init__(self, searcher):
        self.searcher = searcher
        self.results = {}

    def search(self, name):
        if name in self.results:
            return self.results[name]
        result = self.searcher.search(name)
        if result is not None:
            self.results[name] = result
        return result


def investigate(entry, searcher, document_limit=30):
    """Read-only county requests; private evidence writes; no equity computation."""
    import document_walk as W
    import document_store as DS
    import document_collectors as DC
    import miami_judgment as MJ
    import miami_title_parties as TP
    import case_dossier as CD
    from document_queue import DocumentQueue
    case = validate_case(entry['case'])
    folio = entry.get('folio') or ''
    gaps = []
    capture = CapturedSearch(searcher)
    owner_models = capture.search(entry['owner'])
    if owner_models is None:
        gaps.append('Owner baseline unavailable; owner-only omissions cannot be established.')
    elif len(owner_models) >= 500:
        gaps.append('Owner baseline reached 500 records; owner-search coverage unknown.')
    models = list(owner_models or [])
    collector = DC.MiamiCollector()
    try:
        inventory = collector.enumerate_documents(case)
    except Exception as exc:
        inventory = {'raw': {}, 'pagination_verified': False}
        gaps.append('OCS inventory unavailable: ' + type(exc).__name__)
    rows, seeds = stored_evidence(case)
    known = {W.key_of(m.get('reC_BOOK'),m.get('reC_PAGE')) for m in models}
    models.extend(m for m in seeds if W.key_of(m.get('reC_BOOK'),m.get('reC_PAGE')) not in known)
    index = W.RecordIndex()
    index.add_models(models)
    queue = DocumentQueue()
    fetched = 0
    searched = set()
    searches = []
    try:
        # Fixed point over newly recovered deed parties. Bound work, not claimed coverage.
        for round_no in range(3):
            present = {r.get('source_ref') for r in rows}
            ordered = sorted(models, key=lambda m: 0 if str(m.get('foliO_NUMBER') or '') == str(folio) else 1)
            for model in ordered:
                if not re.search(r'\bDEED\b|CERTIFICATE OF TITLE', str(model.get('doC_TYPE')), re.I):
                    continue
                target = re.sub(r'\D', '', str(folio)).lstrip('0')
                recorded = re.sub(r'\D', '', str(model.get('foliO_NUMBER') or '')).lstrip('0')
                if recorded and recorded != target:
                    continue
                ref = 'official_records/%s-%s' % (model.get('reC_BOOK'), model.get('reC_PAGE'))
                if ref in present or 'recorded:%s' % model.get('cfN_MASTER_ID') in present:
                    continue
                if not recorded:
                    gaps.append(ref + ': unanchored deed candidate not fetched; parcel linkage unknown.')
                    continue
                if fetched >= document_limit:
                    gaps.append(ref + ': deed not fetched; document work cap reached.')
                    continue
                fetched += 1
                new = MJ.collect_recorded(case, [model], collector=collector, queue=queue,
                    ocr=DS.winocr, keep_images=True, resume=True, reuse_done=True)
                rows.extend(new)
                present.add(ref)
            title = TP.build_title_parties(models, rows, inventory.get('raw'), folio)
            plan = [p for p in discovery_names(entry, title) if p['name'] not in searched]
            if not plan:
                break
            report = W.run_name_searches(plan, index, capture, folio, owner_models=owner_models)
            searches.append(report)
            searched.update(p['name'] for p in plan)
            known = {W.key_of(m.get('reC_BOOK'), m.get('reC_PAGE')) for m in models}
            for records in capture.results.values():
                for model in records:
                    key = W.key_of(model.get('reC_BOOK'), model.get('reC_PAGE'))
                    if key not in known:
                        known.add(key)
                        models.append(model)
        title = TP.build_title_parties(models, rows, inventory.get('raw'), folio)
        for party in title['search_names']:
            if party['name'] not in searched:
                gaps.append(party['name'] + ': unknown; discovery-round limit left name unsearched.')
        CD.classify_documents(rows, case)
        new_rows, citations = W.walk(case, rows, models=models, collector=collector,
            queue=queue, ocr=DS.winocr, index=index, depth=3,
            budget=max(0, document_limit-fetched), keep_images=True)
        rows.extend(new_rows)
        known = {W.key_of(m.get('reC_BOOK'), m.get('reC_PAGE')) for m in models}
        new_manifests = [r.get('stored') for r in new_rows if isinstance(r.get('stored'), dict)]
        new_manifests.extend(m for m, _ in DS.stored_documents('MIAMI-DADE', case))
        for manifest in new_manifests:
            if not manifest.get('record_key'):
                continue
            record = record_from_manifest(manifest)
            key = W.key_of(record.get('reC_BOOK'), record.get('reC_PAGE'))
            if key not in known:
                known.add(key)
                models.append(record)
        title = TP.build_title_parties(models, rows, inventory.get('raw'), folio)
        for party in title['search_names']:
            if party['name'] not in searched:
                gaps.append(party['name'] + ': unknown; citation-discovered name needs another search pass.')
        for search in searches:
            search['potential_title_party_claims'] = reconcile_claims(
                search.get('potential_title_party_claims', []), rows)
    finally:
        queue.close()
    gaps.extend(title['gaps'])
    for report in searches:
        gaps.extend('%s: %s' % (g['name'], g.get('reason') or 'search coverage unknown')
                    for g in report.get('gaps', []))
    gaps.extend('%s/%s: %s' % (c.get('book'), c.get('page_no'), c.get('reason'))
                for c in citations.get('unresolved', []))
    # No negative title finding is inferred from a successful but bounded name search.
    gaps.append('Judgment attachment, debtor identity and satisfaction reconciliation remain unknown unless supported by document evidence.')
    return {'case':case, 'county':'MIAMI-DADE', 'status':'unknown',
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'title_parties':title, 'other_name_searches':searches,
            'citations':citations, 'gaps':list(dict.fromkeys(gaps)),
            'owner_baseline_records':None if owner_models is None else len(owner_models),
            'stored_instruments_absent_from_owner_query':[
                {'book':m.get('reC_BOOK'), 'page_no':m.get('reC_PAGE'),
                 'doc_type':m.get('doC_TYPE'), 'recorded_date':m.get('reC_DATE'),
                 'basis':'Already-stored evidence absent from this run owner query; not necessarily new debt'}
                for m in missed_records(seeds, owner_models)],
            'private_search_results':capture.results,
            'vision_actual_usd':0.0,
            'vision_note':'Existing OCR/vision reused; new documents use local OCR. Unreadable content remains unknown.'}


def attach_report(dossier, report):
    result = copy.deepcopy(dossier)
    result['title_discovery'] = report
    result['open_gaps'] = [g for g in result.get('open_gaps', [])
                           if not g.startswith('title discovery: ')]
    result['open_gaps'].extend('title discovery: ' + str(g) for g in report.get('gaps', []))
    result['complete'] = bool(result.get('complete')) and not result['open_gaps'] and report.get('status') == 'complete'
    return result


def missed_records(records, owner_records):
    if owner_records is None:
        return []
    from document_walk import key_of
    def key(row):
        return key_of(row.get('reC_BOOK'), row.get('reC_PAGE'))
    known = {key(row) for row in owner_records}
    return [row for row in records if key(row) not in known]


def validate_case(case):
    if not re.fullmatch(r'20\d{2}-\d{6}-(?:CA|CC)-\d{2}', case):
        raise ValueError('Expected a Miami civil case identifier')
    return case


def main(argv=None):
    import argparse
    import math
    import run_documents as RD
    import case_review
    import document_store as DS
    from miami_search_budget import CaptchaBudget, CappedNameSearcher
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', required=True, type=validate_case)
    parser.add_argument('--captcha-max-spend', type=float, required=True)
    parser.add_argument('--vision-max-spend', type=float, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    for cap in (args.captcha_max_spend, args.vision_max_spend):
        if not math.isfinite(cap) or cap <= 0:
            parser.error('Spend caps must be finite and positive')
    chains = RD._load(RD.CHAINS, {})
    entries = []
    for case in dict.fromkeys(args.case):
        chosen = RD.pick_cases(RD._lead_rows(case), chains, 0, only=case)
        if len(chosen) != 1:
            parser.error('Expected exactly one Miami lead for ' + case)
        entries.extend(chosen)
    if args.dry_run:
        print(json.dumps({'cases':[e['case'] for e in entries], 'vision_cap':args.vision_max_spend,
                          'captcha_cap':args.captcha_max_spend, 'actual_usd':0}))
        return 0
    private = case_review.output_path('title_discovery')
    private.mkdir(parents=True, exist_ok=True)
    cache_path = private / 'records_qs.json'
    qs = RD._load(RD.QS_CACHE, {})
    qs.update(RD._load(cache_path, {}))
    with CaptchaBudget(private / 'captcha-budget.json', captcha_max_spend=args.captcha_max_spend) as budget:
        searcher = CappedNameSearcher(budget, qs)
        try:
            for entry in entries:
                report = investigate(entry, searcher)
                report['vision_cap_usd'] = args.vision_max_spend
                report['captcha'] = budget.report()
                report['captcha_gaps'] = searcher.gaps
                report['gaps'].extend(str(g) for g in searcher.gaps)
                DS.pipeline_write(private / (entry['case'] + '.json'), report)
                path = RD.dossier_path('MIAMI-DADE', entry['case'])
                old = RD._load(path, {'case':entry['case'], 'county':'MIAMI-DADE', 'complete':False})
                public = {k:v for k,v in report.items() if k != 'private_search_results'}
                DS.pipeline_write(path, attach_report(old, public))
                DS.pipeline_write(cache_path, qs)
                print(json.dumps({'case':entry['case'], 'status':report['status'],
                    'owner_baseline_records':report['owner_baseline_records'],
                    'names_searched':sum(len(s['searched']) for s in report['other_name_searches']),
                    'gaps':len(report['gaps']), 'captcha':budget.report(), 'vision_actual_usd':0}), flush=True)
        finally:
            searcher.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
