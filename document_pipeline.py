"""Compatibility CLI; shared collectors, store and queue own all work.

Miami-only during pilot validation. No subscription execution or equity writes.
"""
import argparse
import json
import document_collectors as collectors
import document_store as store
from document_queue import resume_case_documents

page_inventory_complete = store.page_inventory_complete
interpretation_complete = store.interpretation_complete
folder = store.pipeline_folder
write = store.pipeline_write
load = store.pipeline_load

def _miami(county):
    if county != 'MIAMI-DADE':
        raise ValueError('Only Miami-Dade is enabled until the pilot passes')

def collect(county, case, records=None):
    _miami(county)
    return collectors.collect_case_documents(county, case, records)

def resume(county, case, limit=10, interpret=False):
    _miami(county)
    return resume_case_documents(county, case, limit, interpret)

def report(county, case):
    _miami(county)
    return store.pipeline_report(county, case)


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
