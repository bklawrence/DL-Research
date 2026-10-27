#!/usr/bin/env python3
"""Report and publish *unvalidated* school-level candidate ACCESS fields.

Copies only interpreted aggregate result files, not large raw state downloads.
A run without a parsed state source is an error, not a successful collection.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys

PUBLIC_FILES = [
    'access_metric_candidates.csv',
    'match_audit.csv',
    'field_inventory.csv',
    'school_inventory_with_collection_status.csv',
    'collection_report.json',
]
MAX_PUBLIC_CSV_BYTES = 8 * 1024 * 1024


def load_report(folder: Path):
    p = folder / 'collection_report.json'
    try:
        with p.open(encoding='utf-8') as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        return {'created_utc': datetime.now(timezone.utc).isoformat(),
                'downloads': [], 'parsed_files': [], 'error': f'Missing or invalid report: {exc}',
                'total_inventory_schools': 0, 'matched_candidate_records': 0,
                'candidate_metric_values': 0}


def md_report(r, files_skipped=()):
    downloads = r.get('downloads') or []
    parsed = r.get('parsed_files') or []
    successful = [x for x in parsed if any(y.get('status') == 'processed' for y in x.get('sheets', []))]
    if not successful:
        verdict = '**NO VERIFIED DATA SOURCE PARSED.** This run must not be treated as refreshed ACCESS evidence.'
    else:
        verdict = f'Parsed {len(successful)} source file(s); all extracted measures remain **unvalidated candidates**.'
    lines = [
        '# Midwest ACCESS collector — latest run', '',
        f"Last attempt (UTC): {r.get('created_utc','unknown')}", '', verdict, '',
        f"Inventory entries: **{r.get('total_inventory_schools',0)}**; "
        f"source school-row matches: **{r.get('matched_candidate_records',0)}**; "
        f"candidate metric cells: **{r.get('candidate_metric_values',0)}**.", '',
        '**These counts are not validated ACCESS exit rates or an analytical sample size.**', '',
        '## Source status', '',
        '| State | Download status | Note |', '|---|---|---|',
    ]
    for x in downloads:
        note = (x.get('message') or '').replace('|','/').replace('\n',' ')[:240]
        lines.append(f"| {x.get('state','')} | {x.get('status','unknown')} | {note} |")
    if not downloads:
        lines.append('| — | No download events | Collector may not have run |')
    lines += ['', '## Parsed source files', '', '| State | Source | Parsed sheets | Warnings |', '|---|---|---:|---|']
    for x in parsed:
        n = sum(y.get('status') == 'processed' for y in x.get('sheets', []))
        warnings = '; '.join(x.get('warnings') or []).replace('|','/').replace('\n',' ')[:250]
        lines.append(f"| {x.get('state','')} | {Path(x.get('file','')).name} | {n} | {warnings} |")
    if not parsed:
        lines.append('| — | None | 0 | No public school-level tables processed |')
    if files_skipped:
        lines.extend(['', '## Oversize files (available only in Actions artifact)', ''])
        lines.extend(f'- {name}' for name in files_skipped)
    lines += ['', '## Research safeguards', '',
        '- Raw state spreadsheets are not committed to the repository.',
        '- Candidate fields retain **exactly** their source names; no exit rates are inferred.',
        '- Name-based school matches must be reviewed before analysis.',
        '- Whole-school scores may include non-dual-language strands.',
        '- Historical 2024–25 ACCESS criteria vary by state; classifications follow kindergarten starting allocation.',
        '- For every run, download all CSVs under **Actions → Collect public ACCESS data → chosen run → Artifacts**.',
        '', 'The [school inventory](../../midwest_school_inventory.csv) is a working screening dataset, not verified experimental evidence.', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', type=Path, default=Path('output'))
    p.add_argument('--output', type=Path, default=Path('results/latest'))
    p.add_argument('--check', action='store_true')
    a = p.parse_args()
    report = load_report(a.input)
    successful = [x for x in report.get('parsed_files', [])
                  if any(y.get('status') == 'processed' for y in x.get('sheets', []))]
    if a.check:
        if not successful:
            print('::error::No publicly downloaded or supplied state school-level data file was successfully parsed.')
            print('Check the report in results/latest/README.md and attached artifact; download URLs may require updating.')
            return 2
        if not report.get('candidate_metric_values'):
            print('::warning::Sources parsed, but zero candidate ACCESS fields were matched to the inventory.')
        failed = [d for d in report.get('downloads', []) if d.get('status') == 'error']
        for x in failed:
            print(f"::warning::Download failed for {x.get('state')}: {x.get('message','unknown')}")
        print(f"Parsed {len(successful)} state source file(s); extracted values still require field-level verification.")
        return 0
    a.output.mkdir(parents=True, exist_ok=True)
    skipped = []
    for filename in PUBLIC_FILES:
        src = a.input / filename
        if src.exists():
            if src.suffix == '.csv' and src.stat().st_size > MAX_PUBLIC_CSV_BYTES:
                skipped.append(filename)
            else:
                shutil.copyfile(src, a.output / filename)
        else:
            stale = a.output / filename
            if stale.exists():
                stale.unlink()  # Never mistake last month's output for a new successful extraction.
    text = md_report(report, skipped)
    (a.output / 'README.md').write_text(text, encoding='utf-8')
    github_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if github_summary:
        with open(github_summary, 'a', encoding='utf-8') as fp:
            fp.write(text + '\n')
    print(text)
    return 0

if __name__ == '__main__':
    sys.exit(main())
