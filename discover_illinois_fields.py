
#!/usr/bin/env python3
"""Discover historical Illinois report-card field names.

Reads official ISBE Excel workbooks and creates column
inventories for year-specific IAR, PARCC, ACCESS, ELA,
mathematics, and English learner field mapping.

Requires collect_access.py in the same directory.
No student outcomes are inferred or validated.
"""

from __future__ import annotations

import argparse
import csv
import json
import re

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from collect_access import fetch, xlsx_tables


SOURCES = {
    2019: "2019-Report-Card-Public-Data-Set.xlsx",
    2020: "2020-Report-Card-Public-Data-Set.xlsx",
    2021: "2021-RC-Pub-Data-Set.xlsx",
    2022: "2022-Report-Card-Public-Data-Set.xlsx",
    2023: "23-RC-Pub-Data-Set.xlsx",
    2024: "24-RC-Pub-Data-Set.xlsx",
    2025: "2025-Report-Card-Public-Data-Set.xlsx",
}

TOPIC = re.compile(
    r"\b(iar|parcc|ela|math(?:ematics)?|"
    r"english language arts|proficien\w*|performance|"
    r"scale score|access|wida|english learner|"
    r"\bel\b|grade|assessment|tested|participat\w*|"
    r"denominator)\b",
    re.I,
)

IDENTITY = re.compile(
    r"\b(rcdts|school name|district|county|"
    r"school year|year)\b",
    re.I,
)


def url_for(filename):
    return (
        "https://www.isbe.net/_layouts/Download.aspx?"
        "SourceUrl="
        + quote("/Documents/" + filename, safe="")
    )


def identify_header(rows):
    """Find the likely header row in a worksheet."""
    best = None

    for i, row in enumerate(rows):
        values = [
            str(x).strip()
            for x in row.values()
            if str(x).strip()
        ]

        score = (
            12 * any(
                x.lower() == "rcdts"
                for x in values
            )
            + 10 * any(
                x.lower() in ("school name", "school")
                for x in values
            )
            + sum(
                bool(TOPIC.search(x))
                for x in values[:100]
            )
        )

        if best is None or score > best[0]:
            best = (score, i, row)

    if best and best[0] >= 10:
        return best[1], best[2]

    return None


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--years",
        default="2019,2020,2021,2022,2023",
    )

    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("output/raw/IL/history"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "output/illinois_field_discovery"
        ),
    )

    parser.add_argument(
        "--no-download",
        action="store_true",
    )

    parser.add_argument(
        "--source-file",
        action="append",
        default=[],
        metavar="YEAR=PATH",
    )

    args = parser.parse_args()

    years = [
        int(s.strip())
        for s in args.years.split(",")
        if s.strip()
    ]

    if (
        not years
        or any(y not in SOURCES for y in years)
    ):
        parser.error(
            "Supported years: 2019-2025"
        )

    overrides = {}

    for item in args.source_file:
        try:
            year, path = item.split("=", 1)
            overrides[int(year)] = Path(path)
        except ValueError:
            parser.error(
                "--source-file requires YEAR=PATH"
            )

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    columns = []
    status = []

    for year in years:
        filename = SOURCES[year]
        source_url = url_for(filename)

        path = overrides.get(
            year,
            args.cache / filename,
        )

        if not path.is_file():

            if args.no_download:
                status.append({
                    "year": year,
                    "status": "missing_local_file",
                    "file": str(path),
                    "columns": 0,
                })
                continue

            print(
                f"Downloading {year}: {filename}",
                flush=True,
            )

            outcome = fetch(
                source_url,
                path,
                max_mb=400,
            )

            if outcome["status"] not in (
                "cached",
                "downloaded",
            ):
                status.append({
                    "year": year,
                    "status": "download_failed",
                    "file": str(path),
                    "error": outcome.get(
                        "message", ""
                    ),
                    "columns": 0,
                })
                continue

        count = 0
        sheets = []

        try:
            for sheet, iterator in xlsx_tables(path):

                initial = []

                for _ in range(32):
                    try:
                        initial.append(next(iterator))
                    except StopIteration:
                        break

                result = identify_header(initial)

                if result is None:
                    sheets.append({
                        "sheet": sheet,
                        "status": "unrecognized_header",
                    })
                    continue

                header_row, labels = result

                for index, label in sorted(
                    labels.items()
                ):
                    label = str(label).strip()

                    if not label:
                        continue

                    relevant = bool(
                        TOPIC.search(label)
                    )

                    columns.append({
                        "year": year,
                        "sheet": sheet,
                        "column_number": index + 1,
                        "source_field_AS_PUBLISHED": label,
                        "topic_candidate": (
                            "yes" if relevant else "no"
                        ),
                        "identity_candidate": (
                            "yes"
                            if IDENTITY.search(label)
                            else "no"
                        ),
                        "source_file": filename,
                        "source_url": source_url,
                    })

                    count += 1

                sheets.append({
                    "sheet": sheet,
                    "status": "headers_extracted",
                    "header_row_within_preview": (
                        header_row + 1
                    ),
                    "columns": len(labels),
                })

        except Exception as exc:
            status.append({
                "year": year,
                "status": "parse_failed",
                "file": str(path),
                "error": str(exc),
                "columns": count,
                "sheets": sheets,
            })
            continue

        candidate_count = sum(
            row["year"] == year
            and row["topic_candidate"] == "yes"
            for row in columns
        )

        status.append({
            "year": year,
            "status": "parsed",
            "file": str(path),
            "columns": count,
            "topic_candidates": candidate_count,
            "sheets": sheets,
        })

        print(
            f"{year}: {count} headings, "
            f"{candidate_count} candidates",
            flush=True,
        )

    fields = [
        "year",
        "sheet",
        "column_number",
        "source_field_AS_PUBLISHED",
        "topic_candidate",
        "identity_candidate",
        "source_file",
        "source_url",
    ]

    outputs = [
        (
            "all_source_columns.csv",
            columns,
        ),
        (
            "candidate_assessment_columns.csv",
            [
                row for row in columns
                if row["topic_candidate"] == "yes"
            ],
        ),
    ]

    for name, records in outputs:
        with (
            args.output / name
        ).open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fields,
            )
            writer.writeheader()
            writer.writerows(records)

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "purpose": (
            "Historical source-field discovery; "
            "no outcome validation"
        ),
        "results": status,
        "candidate_columns": sum(
            row["topic_candidate"] == "yes"
            for row in columns
        ),
        "note": (
            "2020 is an assessment-interruption year. "
            "Do not infer IAR values from empty fields."
        ),
    }

    (
        args.output / "field_discovery_report.json"
    ).write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(
        f"Discovery complete: {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
