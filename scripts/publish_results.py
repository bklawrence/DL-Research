#!/usr/bin/env python3
"""Publish candidate aggregate metrics with explicit validation limits."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys

PUBLIC_FILES = (
    'access_metric_candidates.csv', 'academic_metric_candidates.csv', 'match_audit.csv',
    'field_inventory.csv', 'source_column_inventory.csv',
    'school_inventory_with_collection_status.csv', 'collection_report.json')
MAX_PUBLIC_CSV_BYTES = 8 * 1024 * 1024

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, default=Path('output'))
    p.add_argument('--output', type=Path, default=Path('results/latest'))
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    try:
        r = json.loads((a.input / 'collection_report.json').read_text())
    except (OSError, ValueError) as ex:
        r = {'parsed_files':[], 'downloads':[], 'error':str(ex)}
    good = [x for x in r.get('parsed_files',[]) if any(y.get('status')=='processed' for y in x.get('sheets',[]))]
    access = r.get('nonexcluded_schools_with_access_candidates',0)
    academic = r.get('nonexcluded_schools_with_academic_candidates',0)
    if a.check:
        if not good:
            print('::error::No state source successfully parsed.')
            return 2
        if not access:
            print('::warning::No non-excluded schools with candidate ACCESS data.')
        if not academic:
            print('::warning::No non-excluded schools with candidate academic data.')
        for d in r.get('downloads',[]):
            if d.get('status')=='error':
                print(f"::warning::Download failed for {d.get('state')}: {d.get('message')}")
        return 0
    a.output.mkdir(parents=True,exist_ok=True)
    skipped=[]
    for name in PUBLIC_FILES:
        src=a.input/name; dst=a.output/name
        if src.exists() and not (src.suffix=='.csv' and src.stat().st_size>MAX_PUBLIC_CSV_BYTES):
            shutil.copyfile(src,dst)
        else:
            if dst.exists():dst.unlink()
            if src.exists():skipped.append(name)
    lines=['# Public school-level education collection — latest run','',
       f"Source files successfully parsed: **{len(good)}**",'',
       f"Inventory entries: **{r.get('total_inventory_schools',0)}**",'',
       f"Non-excluded schools with candidate ACCESS fields: **{access}**",'',
       f"Non-excluded schools with candidate academic fields: **{academic}**",'',
       '**Validated analytical observations: 0 (validation not automated).**','',
       'All metric values retain the source names and require manual confirmation of assessment, year, subgroup, grade, denominator, and applicability to DL students.','',
       'Excluded schools remain in match auditing, but are omitted from the published candidate metric CSVs.','',
       '## Source status','', '| State | Download status | Note |', '|---|---|---|']
    for d in r.get('downloads',[]):
        lines.append(f"| {d.get('state','')} | {d.get('status','')} | {str(d.get('message','')).replace('|','/')[:150]} |")
    lines += ['', '## Parsed files', '', '| State | File | Processed sheets |', '|---|---|---:|']
    for x in r.get('parsed_files',[]):
        n=sum(y.get('status')=='processed' for y in x.get('sheets',[]))
        lines.append(f"| {x.get('state','')} | {Path(x.get('file','')).name} | {n} |")
    if skipped: lines+=['','Oversized files (Actions artifact only): '+', '.join(skipped)]
    lines+=['','Raw state files are not committed. School-name matches and whole-school versus DL-strand comparability must be verified before inference.','']
    md='\n'.join(lines)
    (a.output/'README.md').write_text(md,encoding='utf-8')
    print(md)
    return 0
if __name__=='__main__':sys.exit(main())
