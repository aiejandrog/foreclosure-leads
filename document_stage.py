"""Scheduled subscription document stage. Enabled by --enable or DEALFLOW_DOCS=1.

Invoke after the records stage: python -u document_stage.py --limit 10
Existing subscription only; login/usage failures leave interpretation pending.
"""
import argparse
import json
import os
from pathlib import Path

import document_agents
import document_pipeline as pipeline
import records_liens


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--documents-per-case', type=int, default=10)
    parser.add_argument('--leads', default=str(Path(__file__).parent / 'leads_final.json'))
    args = parser.parse_args(argv)
    if not args.enable and os.environ.get('DEALFLOW_DOCS') != '1':
        print('document_stage: disabled; set DEALFLOW_DOCS=1 after pilot acceptance')
        return 0
    leads = pipeline.load(args.leads, [])
    tokens = pipeline.load(Path(args.leads).parent / 'records_qs.json', {})
    if not isinstance(leads, list) or not leads:
        print('document_stage: no lead inventory available')
        return 2
    # An ISO-normalized auction date sorts imminence without depending on locale strings.
    from datetime import datetime
    def priority(row):
        try:
            return datetime.strptime(row.get('AuctionDate', ''), '%m/%d/%Y')
        except ValueError:
            return datetime.max
    seen = set()
    for lead in sorted(leads, key=priority):
        case = str(lead.get('Case #') or '')
        county = str(lead.get('county') or 'MIAMI-DADE').upper()
        if not case or (county, case) in seen:
            continue
        if len(seen) >= args.limit:
            break
        seen.add((county, case))
        try:
            token = tokens.get(lead.get('owner_clean', ''))
            records = records_liens.records_by_qs(token) if county == 'MIAMI-DADE' and token else None
            pipeline.collect(county, case, records)
            result = pipeline.resume(county, case, args.documents_per_case, interpret=True)
            print('%s: %d documents, %d extracted pages, complete=%s' %
                  (county, result['documents_obtained'], result['pages_extracted'], result['interpretation_complete']))
        except Exception as exc:
            # Keep a per-case failure without logging homeowner fields or authentication material.
            pipeline.write(pipeline.folder(county, case) / 'stage-error.json', {'status': 'incomplete', 'error_type': type(exc).__name__})
            print('%s: incomplete (%s)' % (county, type(exc).__name__))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
