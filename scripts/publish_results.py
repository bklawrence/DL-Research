
#!/usr/bin/env python3
"""Publish candidate aggregate metrics with explicit validation limits."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import sys

PUBLIC_FILES = (
    "access_metric_candidates.csv",
    "academic_metric_candidates.csv",
    "match_audit.csv",
    "field_inventory.csv",
    "source_column_inventory.csv",
    "school_inventory_with_collection_status.csv",
    "collection_report.json",
)

MAX_PUBLIC_CSV_BYTES = 8 * 1024 * 1024


def candidate_coverage(folder: Path, filename: str):
    """Count distinct schools and candidate cells in an output CSV."""
    path = folder / filename

    if not path.exists():
        return 0, 0

    schools = set()
    cells = 0

    with path.open(
        newline="", encoding="utf-8-sig"
    ) as fh:
        for row in csv.DictReader(fh):
            cells += 1

            school = row.get("school", "")
            if school:
                schools.add((
                    row.get("state", ""),
                    row.get("inventory_id", ""),
                    school,
                ))

    return len(schools), cells


def load_report(folder: Path):
    path = folder / "collection_report.json"

    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        return {
            "parsed_files": [],
            "downloads": [],
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/latest"),
    )

    parser.add_argument(
        "--check",
        action="store_true",
    )

    args = parser.parse_args()

    report = load_report(args.input)

    successful = [
        source
        for source in report.get("parsed_files", [])
        if any(
            sheet.get("status") == "processed"
            for sheet in source.get("sheets", [])
        )
    ]

    # Calculate coverage from actual candidate CSVs.
    # Do not rely on optional collection_report keys.
    access_schools, access_cells = candidate_coverage(
        args.input,
        "access_metric_candidates.csv",
    )

    academic_schools, academic_cells = candidate_coverage(
        args.input,
        "academic_metric_candidates.csv",
    )

    # GitHub Actions final validation step.
    if args.check:
        if not successful:
            print(
                "::error::No state source was successfully parsed."
            )
            return 2

        if not access_schools:
            print(
                "::warning::No non-excluded schools "
                "with candidate ACCESS data."
            )

        if not academic_schools:
            print(
                "::warning::No non-excluded schools "
                "with candidate academic data."
            )

        for download in report.get("downloads", []):
            if download.get("status") == "error":
                state = download.get("state", "unknown")
                message = download.get("message", "")

                print(
                    f"::warning::Download failed "
                    f"for {state}: {message}"
                )

        print(
            f"Parsed {len(successful)} source files. "
            f"ACCESS schools: {access_schools}; "
            f"academic schools: {academic_schools}."
        )

        return 0

    # Publish output files.
    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    skipped = []

    for filename in PUBLIC_FILES:
        source = args.input / filename
        destination = args.output / filename

        if (
            source.exists()
            and not (
                source.suffix == ".csv"
                and source.stat().st_size > MAX_PUBLIC_CSV_BYTES
            )
        ):
            shutil.copyfile(source, destination)

        else:
            # Prevent stale files from appearing current.
            if destination.exists():
                destination.unlink()

            if source.exists():
                skipped.append(filename)

    # Build the public Markdown report.
    lines = [
        "# Public school-level education collection — latest run",
        "",
        f"Source files successfully parsed: **{len(successful)}**",
        "",
        (
            "Inventory entries: "
            f"**{report.get('total_inventory_schools', 0)}**"
        ),
        "",
        (
            "Non-excluded schools with candidate "
            f"ACCESS fields: **{access_schools}**"
        ),
        "",
        (
            "Non-excluded schools with candidate "
            f"academic fields: **{academic_schools}**"
        ),
        "",
        (
            f"Published ACCESS candidate cells: **{access_cells}**; "
            f"academic candidate cells: **{academic_cells}**."
        ),
        "",
        (
            "**Validated analytical observations: 0 "
            "(validation not automated).**"
        ),
        "",
        (
            "All metric values retain their source names and "
            "require confirmation of assessment, year, subgroup, "
            "grade, denominator, and applicability to "
            "dual-language students."
        ),
        "",
        (
            "Excluded schools remain in match auditing, "
            "but are omitted from published candidate metric CSVs."
        ),
        "",
        "## Source status",
        "",
        "| State | Download status | Note |",
        "|---|---|---|",
    ]

    for download in report.get("downloads", []):
        state = download.get("state", "")
        status = download.get("status", "")

        note = str(
            download.get("message", "")
        ).replace("|", "/").replace("\n", " ")[:150]

        lines.append(
            f"| {state} | {status} | {note} |"
        )

    lines.extend([
        "",
        "## Parsed files",
        "",
        "| State | File | Processed sheets |",
        "|---|---|---:|",
    ])

    for source in report.get("parsed_files", []):
        processed = sum(
            sheet.get("status") == "processed"
            for sheet in source.get("sheets", [])
        )

        state = source.get("state", "")
        filename = Path(
            source.get("file", "")
        ).name

        lines.append(
            f"| {state} | {filename} | {processed} |"
        )

    if skipped:
        lines.extend([
            "",
            "## Oversized files",
            "",
            (
                "The following files were omitted from the "
                "published repository snapshot:"
            ),
            "",
        ])

        for filename in skipped:
            lines.append(f"- {filename}")

    lines.extend([
        "",
        "## Research safeguards",
        "",
        "- Raw state spreadsheets are not committed.",
        "- Candidate metrics are not validated outcomes.",
        "- Name-based school matches require verification.",
        "- Whole-school data may include non-DL students.",
        "- Assessment years and denominators must be verified.",
        "- ACCESS proficiency must not be confused with exit rates.",
        "",
    ])

    markdown = "\n".join(lines)

    readme = args.output / "README.md"

    readme.write_text(
        markdown,
        encoding="utf-8",
    )

    # Also write the report to GitHub Actions' run summary.
    github_summary = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if github_summary:
        with open(
            github_summary,
            "a",
            encoding="utf-8",
        ) as fh:
            fh.write(markdown + "\n")

    print(markdown)

    return 0


if __name__ == "__main__":
    sys.exit(main())
