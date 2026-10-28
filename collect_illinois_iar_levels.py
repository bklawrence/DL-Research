
#!/usr/bin/env python3
"""Extract historical Illinois IAR performance-level results.

Years: 2019, 2021, 2022, 2023.
Preserves published values, units, grades and student groups.
Does not infer proficiency rates or ACCESS outcomes.

Requires collect_access.py and midwest_school_inventory.csv.
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
    2021: "2021-RC-Pub-Data-Set.xlsx",
    2022: "2022-Report-Card-Public-Data-Set.xlsx",
    2023: "23-RC-Pub-Data-Set.xlsx",
}

HEADING = re.compile(
    r"^(%\s*)?(All|EL) students IAR "
    r"(ELA|Mathematics) Level ([1-5]) - Grade ([3-8])$",
    re.I,
)

ABBR = {
    "elem": "elementary",
    "sch": "school",
    "intl": "international",
    "acad": "academy",
    "ctr": "center",
    "mag": "magnet",
}


def norm(value):
    words = re.findall(
        r"[a-z0-9]+",
        str(value or "").lower().replace("&", " and "),
    )
    return " ".join(ABBR.get(w, w) for w in words)


def district(value):
    v = norm(value)

    if "chicago" in v and (
        "299" in v or "public schools" in v
    ):
        return "chicago 299"

    remove = {
        "district", "school", "schools", "public",
        "community", "consolidated", "unit", "cusd",
        "ccsd", "usd", "sd", "csd", "esd",
    }

    return " ".join(
        w for w in v.split() if w not in remove
    )


def load_inventory(path):
    with path.open(
        newline="", encoding="utf-8-sig"
    ) as f:
        return [
            r for r in csv.DictReader(f)
            if r.get("State", "").strip().upper() == "IL"
        ]


def load_ids(path, inventory):
    if not path.is_file():
        return {}

    by_row = {
        r.get("Row_ID"): r for r in inventory
    }

    with path.open(
        newline="", encoding="utf-8-sig"
    ) as f:
        rows = list(csv.DictReader(f))

    ids = {}

    for r in rows:
        key = r.get("Row_ID", "")
        code = re.sub(
            r"\D", "", r.get("RCDTS_15", "")
        )

        if (
            key in by_row
            and len(code) == 15
            and r.get(
                "auto_pdf_eligible", ""
            ).lower() == "yes"
        ):
            ids.setdefault(code, []).append(by_row[key])

    return {
        k: v[0]
        for k, v in ids.items()
        if len(v) == 1
    }


def source_url(filename):
    return (
        "https://www.isbe.net/_layouts/Download.aspx?"
        "SourceUrl="
        + quote("/Documents/" + filename, safe="")
    )


def value_status(raw):
    x = raw.strip()

    if x == "‡":
        return "suppressed"

    if x in ("", "*", "N/A", "NA", "—", "-"):
        return "not_reported"

    try:
        float(x.rstrip("%").replace(",", ""))
    except ValueError:
        return "non_numeric"

    return "numeric_unvalidated"


def process(path, year, inventory, known_ids):
    extracted = []
    audit = []
    found_cols = 0
    school_matches = set()
    sheets = []

    for sheet, rows in xlsx_tables(path):
        if not sheet.startswith("IAR"):
            continue

        initial = []

        for _ in range(32):
            try:
                initial.append(next(rows))
            except StopIteration:
                break

        header = None

        for n, r in enumerate(initial):
            labels = {
                str(v).strip().lower(): i
                for i, v in r.items()
                if str(v).strip()
            }

            if {
                "rcdts", "school name", "district"
            } <= set(labels):
                header = (n, r, labels)
                break

        if header is None:
            sheets.append({
                "sheet": sheet,
                "status": "header_not_found",
            })
            continue

        headpos, head, idx = header
        measures = []

        for col, label in head.items():
            m = HEADING.fullmatch(str(label).strip())

            if not m:
                continue

            _, pop, subject, level, grade = m.groups()

            measures.append((
                col,
                str(label).strip(),
                (
                    "all_tested"
                    if pop.lower() == "all"
                    else "english_learners"
                ),
                (
                    "ELA"
                    if subject.lower() == "ela"
                    else "Math"
                ),
                int(level),
                int(grade),
                (
                    "percent_labeled"
                    if m.group(1)
                    else "unit_unverified"
                ),
            ))

        found_cols += len(measures)

        sheets.append({
            "sheet": sheet,
            "status": "processed",
            "matched_columns": len(measures),
        })

        def consume(r):
            code = re.sub(
                r"\D",
                "",
                str(r.get(idx["rcdts"], "")),
            )

            name = str(
                r.get(idx["school name"], "")
            ).strip()

            dist = str(
                r.get(idx["district"], "")
            ).strip()

            if len(code) != 15 or not name:
                return

            item = known_ids.get(code)

            method = (
                "approved_rcdts" if item else ""
            )

            if not item:
                choices = [
                    x for x in inventory
                    if norm(x["School"]) == norm(name)
                    and district(
                        x.get("District", "")
                    )
                    and district(
                        x.get("District", "")
                    ) == district(dist)
                ]

                if len(choices) == 1:
                    item = choices[0]
                    method = (
                        "exact_normalized_name_and_district"
                    )

            if not item:
                return

            row_id = item.get("Row_ID", "")

            school_matches.add((row_id, code))

            audit.append({
                "year": year,
                "sheet": sheet,
                "inventory_id": row_id,
                "school": item["School"],
                "reported_school": name,
                "reported_district": dist,
                "RCDTS_15": code,
                "match_method": method,
            })

            for (
                col, label, pop, subject,
                level, grade, unit
            ) in measures:

                raw = str(r.get(col, "")).strip()

                extracted.append({
                    "state": "IL",
                    "school_year": (
                        f"{year-1}-{year%100:02d}"
                    ),
                    "report_card_year": year,
                    "inventory_id": row_id,
                    "school": item["School"],
                    "RCDTS_15": code,
                    "reported_school": name,
                    "reported_district": dist,
                    "subject": subject,
                    "grade": grade,
                    "student_group": pop,
                    "performance_level": level,
                    "source_field": label,
                    "source_value": raw,
                    "unit_status": unit,
                    "value_status": value_status(raw),
                    "source_sheet": sheet,
                    "source_file": path.name,
                    "source_url": source_url(
                        SOURCES[year]
                    ),
                    "match_method": method,
                    "validation_status": (
                        "raw_candidate_unvalidated"
                    ),
                })

        for row in initial[headpos + 1:]:
            consume(row)

        for row in rows:
            consume(row)

    return extracted, audit, {
        "year": year,
        "status": "parsed",
        "sheets": sheets,
        "matched_level_columns": found_cols,
        "matched_schools": len(school_matches),
        "candidate_cells": len(extracted),
    }


def write_csv(path, records, columns):
    with path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=columns
        )
        writer.writeheader()
        writer.writerows(records)


def main():
    p = argparse.ArgumentParser()

    p.add_argument(
        "--inventory",
        type=Path,
        default=Path("midwest_school_inventory.csv"),
    )

    p.add_argument(
        "--crosswalk",
        type=Path,
        default=Path(
            "output/illinois_school_ids.csv"
        ),
    )

    p.add_argument(
        "--cache",
        type=Path,
        default=Path("output/raw/IL/history"),
    )

    p.add_argument(
        "--output",
        type=Path,
        default=Path("output/illinois_iar_levels"),
    )

    p.add_argument(
        "--years",
        default="2019,2021,2022,2023",
    )

    args = p.parse_args()

    args.output.mkdir(
        parents=True, exist_ok=True
    )

    years = [
        int(x.strip())
        for x in args.years.split(",")
        if x.strip()
    ]

    if not years or any(y not in SOURCES for y in years):
        p.error(
            "Supported years: 2019,2021,2022,2023"
        )

    inventory = load_inventory(args.inventory)
    known = load_ids(args.crosswalk, inventory)

    metrics = []
    audits = []
    reports = []

    for year in years:
        filename = SOURCES[year]
        path = args.cache / filename

        if not path.is_file():
            result = fetch(
                source_url(filename),
                path,
                max_mb=400,
            )

            if result.get("status") not in (
                "downloaded", "cached"
            ):
                reports.append({
                    "year": year,
                    "status": "download_failed",
                    "detail": result,
                })
                continue

        try:
            m, au, r = process(
                path, year, inventory, known
            )

            metrics += m
            audits += au
            reports.append(r)

            print(
                year,
                "schools", r["matched_schools"],
                "cells", r["candidate_cells"],
                flush=True,
            )

        except Exception as e:
            reports.append({
                "year": year,
                "status": "parse_failed",
                "error": str(e),
            })

    columns = [
        "state", "school_year", "report_card_year",
        "inventory_id", "school", "RCDTS_15",
        "reported_school", "reported_district",
        "subject", "grade", "student_group",
        "performance_level", "source_field",
        "source_value", "unit_status", "value_status",
        "source_sheet", "source_file", "source_url",
        "match_method", "validation_status",
    ]

    write_csv(
        args.output / "iar_performance_level_candidates.csv",
        metrics,
        columns,
    )

    write_csv(
        args.output / "school_match_audit.csv",
        audits,
        [
            "year", "sheet", "inventory_id",
            "school", "reported_school",
            "reported_district", "RCDTS_15",
            "match_method",
        ],
    )

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "results": reports,
        "candidate_cells": len(metrics),
        "validated_observations": 0,
        "important_notes": [
            "2019 units unverified; no rates calculated",
            "Percent-labeled levels remain unvalidated",
            "2020 assessment interruption is not zero",
            "No conversion to proficiency rates",
            "No merging with ACCESS outcomes",
            "Only unique approved IDs or exact school "
            "and district matches are retained",
        ],
    }

    (
        args.output / "collection_report.json"
    ).write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print("Saved", args.output)


if __name__ == "__main__":
    main()
