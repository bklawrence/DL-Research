
#!/usr/bin/env python3
"""Illinois school-level historical report-card backfill (2019-2025).

Uses public ISBE annual workbooks, extracts published IAR school
and EL-subgroup figures, and retains audit/missing-field information.

No inferred ACCESS scores, no inference about dual-language cohort
membership, and no invented 2020 tests.

Run alongside collect_access.py and midwest_school_inventory.csv.
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


SOURCE_FILENAMES = {
    2025: "2025-Report-Card-Public-Data-Set.xlsx",
    2024: "24-RC-Pub-Data-Set.xlsx",
    2023: "23-RC-Pub-Data-Set.xlsx",
    2022: "2022-Report-Card-Public-Data-Set.xlsx",
    2021: "2021-RC-Pub-Data-Set.xlsx",
    2020: "2020-Report-Card-Public-Data-Set.xlsx",
    2019: "2019-Report-Card-Public-Data-Set.xlsx",
}

ARCHIVE = (
    "https://www.isbe.net/pages/"
    "illinois-state-report-card-data.aspx"
)


def source_url(filename):
    return (
        "https://www.isbe.net/_layouts/Download.aspx?SourceUrl="
        + quote("/Documents/" + filename, safe="")
    )


def normal(value):
    words = re.findall(
        r"[a-z0-9]+",
        str(value or "").lower().replace("&", " and ")
    )

    abbr = {
        "elem": "elementary",
        "sch": "school",
        "intl": "international",
        "acad": "academy",
        "ctr": "center",
        "mag": "magnet",
        "biling": "bilingual",
        "imm": "immersion",
        "el": "elementary",
    }

    return " ".join(abbr.get(x, x) for x in words)


def district_key(value):
    v = normal(value)

    if "chicago" in v and (
        "299" in v or "public schools" in v
    ):
        return "chicago 299"

    v = re.sub(
        r"\b(district|school|schools|public|community|"
        r"consolidated|unit|cusd|ccsd|usd|sd|csd|esd)\b",
        "",
        v,
    )

    return " ".join(v.split())


def inventory_rows(path):
    with path.open(
        newline="",
        encoding="utf-8-sig"
    ) as f:
        return [
            r for r in csv.DictReader(f)
            if r.get("State", "").upper().strip() == "IL"
        ]


def crosswalk_rows(path):
    if not path or not path.is_file():
        return {}

    with path.open(
        newline="",
        encoding="utf-8-sig"
    ) as f:
        return {
            r["Row_ID"]: r
            for r in csv.DictReader(f)
            if r.get("Row_ID")
        }


def numeric(value):
    s = str(value or "").strip()

    if s in ("", "*", "‡", "-", "N/A", "NA", "—"):
        return (
            "",
            "suppressed" if s == "‡" else "not_reported"
        )

    try:
        float(s.rstrip("%").replace(",", ""))
    except ValueError:
        return "", "non_numeric"

    return (
        s.rstrip("%").replace(",", ""),
        "published_numeric",
    )


def classify(sheet, field):
    f = normal(field)

    if sheet.lower() == "iar":

        if (
            "iar ela proficiency rate" in f
            and f.endswith("total")
        ):
            return "IAR_ELA_PROFICIENCY_TOTAL"

        if (
            "iar math proficiency rate" in f
            and f.endswith("total")
        ):
            return "IAR_MATH_PROFICIENCY_TOTAL"

        if (
            "iar ela proficiency rate" in f
            and (
                "english learner" in f
                or f.endswith(" el")
            )
        ):
            return "IAR_ELA_PROFICIENCY_EL"

        if (
            "iar math proficiency rate" in f
            and (
                "english learner" in f
                or f.endswith(" el")
            )
        ):
            return "IAR_MATH_PROFICIENCY_EL"

    return None


def load_known_id(item, crosswalk):
    row = crosswalk.get(
        item.get("Row_ID", ""),
        {}
    )

    sid = re.sub(
        r"\D",
        "",
        row.get("RCDTS_15", "")
    )

    if (
        len(sid) == 15
        and row.get(
            "auto_pdf_eligible", ""
        ).lower() == "yes"
    ):
        return sid

    return ""


def prepare_header(first):
    for i, row in enumerate(first):
        hs = {
            j: str(v).strip()
            for j, v in row.items()
            if str(v).strip()
        }

        vals = {
            normal(v): j
            for j, v in hs.items()
        }

        if all(
            k in vals
            for k in ("school name", "rcdts", "district")
        ):
            return i, hs, vals

    return None


def process_workbook(path, year, inventory, crosswalk):

    matched = []
    field_audit = []
    sheet_results = []

    status = {
        "year": year,
        "file": path.name,
        "source_url": source_url(
            SOURCE_FILENAMES[year]
        ),
        "sheets": [],
        "records": 0,
        "warnings": [],
    }

    approved = {
        load_known_id(r, crosswalk): r
        for r in inventory
        if load_known_id(r, crosswalk)
    }

    for sheet, stream in xlsx_tables(path):

        if sheet not in (
            "IAR",
            "General",
            "ELAMathScience",
        ):
            continue

        initial = []

        for _ in range(30):
            try:
                initial.append(next(stream))
            except StopIteration:
                break

        header = prepare_header(initial)

        if not header:
            status["sheets"].append({
                "sheet": sheet,
                "status": "header_unrecognized",
            })
            continue

        pos, headers, idx = header

        metrics = []

        for col, title in headers.items():
            cls = classify(sheet, title)

            if not cls:
                continue

            grade_match = re.search(
                r"\bgrade\s+(\d+)\b",
                normal(title)
            )

            grade_label = (
                grade_match.group(1)
                if grade_match
                else "schoolwide"
            )

            metrics.append((
                col,
                title,
                cls,
                grade_label,
            ))

        for col, title, cls, grade_label in metrics:
            field_audit.append({
                "year": year,
                "sheet": sheet,
                "source_column": title,
                "metric_code": cls,
            })

        outputs = []

        def consume(row):

            ident = re.sub(
                r"\D",
                "",
                str(row.get(idx["rcdts"], ""))
            )

            name = str(
                row.get(idx["school name"], "")
            ).strip()

            district = str(
                row.get(idx["district"], "")
            ).strip()

            if not name or len(ident) != 15:
                return

            item = approved.get(ident)

            match_method = (
                "approved_rcdts" if item else ""
            )

            if not item:
                candidates = [
                    i for i in inventory
                    if normal(i["School"]) == normal(name)
                    and district_key(
                        i.get("District")
                    ) == district_key(district)
                    and district_key(district)
                ]

                if len(candidates) == 1:
                    item = candidates[0]
                    match_method = (
                        "exact_normalized_name_district"
                    )

            if not item:
                return

            matched.append({
                "year": year,
                "reporting_school": name,
                "reporting_district": district,
                "RCDTS_15": ident,
                "Row_ID": item.get("Row_ID", ""),
                "inventory_school": item["School"],
                "match_method": match_method,
                "sheet": sheet,
                "fields_available": len(metrics),
            })

            for col, title, metric, grade_label in metrics:

                raw = str(
                    row.get(col, "")
                ).strip()

                val, cell_status = numeric(raw)

                outputs.append({
                    "state": "IL",
                    "school_year": (
                        f"{year-1}-{str(year)[-2:]}"
                    ),
                    "report_card_year": year,
                    "inventory_id": item.get(
                        "Row_ID", ""
                    ),
                    "school": item["School"],
                    "RCDTS_15": ident,
                    "reported_school": name,
                    "reported_district": district,
                    "measure": metric,
                    "field_name_AS_PUBLISHED": title,
                    "value_AS_PUBLISHED": raw,
                    "numeric_value": val,
                    "value_status": cell_status,
                    "assessment": "IAR",
                    "reporting_population": (
                        "english_learners"
                        if metric.endswith("_EL")
                        else "all_tested"
                    ),
                    "grade": grade_label,
                    "source_sheet": sheet,
                    "source_file": path.name,
                    "source_url": status["source_url"],
                    "school_match_method": match_method,
                    "comparability_note": (
                        "2025 IAR standard-setting break; "
                        "do not compare rates directly "
                        "across this boundary"
                        if year >= 2025
                        else (
                            "Historical IAR measure; check "
                            "year-specific definitions"
                        )
                    ),
                    "validation_status": (
                        "candidate_not_validated"
                    ),
                })

        for row in initial[pos + 1:]:
            consume(row)

        for row in stream:
            consume(row)

        status["sheets"].append({
            "sheet": sheet,
            "status": "processed",
            "metric_columns": len(metrics),
        })

        sheet_results.extend(outputs)

    status["records"] = len(sheet_results)

    return (
        sheet_results,
        matched,
        field_audit,
        status,
    )


def write_csv(path, rows, fields):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
        )

        writer.writeheader()
        writer.writerows(rows)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path(
            "midwest_school_inventory.csv"
        ),
    )

    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path(
            "output/illinois_school_ids.csv"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "output/illinois_history"
        ),
    )

    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(
            "output/raw/IL/history"
        ),
    )

    parser.add_argument(
        "--years",
        default="2019,2020,2021,2022,2023,2024,2025",
    )

    parser.add_argument(
        "--no-download",
        action="store_true",
    )

    args = parser.parse_args()

    years = [
        int(x.strip())
        for x in args.years.split(",")
        if x.strip()
    ]

    if (
        not years
        or any(
            y not in SOURCE_FILENAMES
            for y in years
        )
    ):
        parser.error(
            "Supported report-card years: 2019-2025"
        )

    inv = inventory_rows(args.inventory)

    if not inv:
        parser.error(
            "No Illinois schools in inventory"
        )

    cross = crosswalk_rows(args.crosswalk)

    args.output.mkdir(
        parents=True,
        exist_ok=True
    )

    all_metrics = []
    all_matches = []
    all_fields = []
    history = []

    for year in years:

        filename = SOURCE_FILENAMES[year]
        path = args.cache / filename

        if not path.exists():

            if args.no_download:
                history.append({
                    "year": year,
                    "status": "missing_cached_file",
                    "file": filename,
                })
                continue

            print(
                "Downloading",
                year,
                filename,
                flush=True
            )

            result = fetch(
                source_url(filename),
                path,
                max_mb=400,
            )

            if result.get("status") not in (
                "downloaded",
                "cached",
            ):
                history.append({
                    "year": year,
                    "status": "download_failed",
                    "error": result.get(
                        "message", ""
                    ),
                })
                continue

        print("Parsing", year, flush=True)

        try:
            metrics, matches, fields, detail = (
                process_workbook(
                    path,
                    year,
                    inv,
                    cross,
                )
            )

            all_metrics.extend(metrics)
            all_matches.extend(matches)
            all_fields.extend(fields)

            detail["status"] = "parsed"
            history.append(detail)

        except Exception as exc:
            history.append({
                "year": year,
                "status": "parsing_failed",
                "error": str(exc),
            })

    metrics_cols = [
        "state",
        "school_year",
        "report_card_year",
        "inventory_id",
        "school",
        "RCDTS_15",
        "reported_school",
        "reported_district",
        "measure",
        "field_name_AS_PUBLISHED",
        "value_AS_PUBLISHED",
        "numeric_value",
        "value_status",
        "assessment",
        "reporting_population",
        "grade",
        "source_sheet",
        "source_file",
        "source_url",
        "school_match_method",
        "comparability_note",
        "validation_status",
    ]

    write_csv(
        args.output
        / "illinois_historical_academic_candidates.csv",
        all_metrics,
        metrics_cols,
    )

    write_csv(
        args.output
        / "illinois_historical_match_audit.csv",
        all_matches,
        [
            "year",
            "reporting_school",
            "reporting_district",
            "RCDTS_15",
            "Row_ID",
            "inventory_school",
            "match_method",
            "sheet",
            "fields_available",
        ],
    )

    write_csv(
        args.output
        / "illinois_historical_field_audit.csv",
        all_fields,
        [
            "year",
            "sheet",
            "source_column",
            "metric_code",
        ],
    )

    # Include missing years and schools in coverage.
    present = {
        (
            str(r["report_card_year"]),
            r["inventory_id"],
        )
        for r in all_metrics
    }

    coverage = []

    for y in years:
        for item in inv:

            coverage.append({
                "year": y,
                "Row_ID": item.get("Row_ID", ""),
                "School": item["School"],
                "has_IAR_candidate": (
                    "yes"
                    if (
                        str(y),
                        item.get("Row_ID", ""),
                    ) in present
                    else "no"
                ),
                "model_history_verified": "no",
                "note": (
                    "Program model must be assigned by "
                    "entering cohort, not current school label"
                ),
            })

    write_csv(
        args.output
        / "illinois_historical_coverage.csv",
        coverage,
        [
            "year",
            "Row_ID",
            "School",
            "has_IAR_candidate",
            "model_history_verified",
            "note",
        ],
    )

    report = {
        "created_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "source_archive": ARCHIVE,
        "inventory_schools": len(inv),
        "requested_years": years,
        "results": history,
        "candidate_cells": len(all_metrics),
        "valid_analytical_observations": 0,
        "notes": [
            (
                "Annual schoolwide results are not "
                "DL-strand-specific unless independently verified"
            ),
            "Missing or suppressed values are not zeros",
            (
                "Do not infer ACCESS from IAR; "
                "ACCESS PDF collection is separate"
            ),
            (
                "2025 IAR proficiency cut scores changed; "
                "flag cross-year comparisons"
            ),
            (
                "Model-transition history must "
                "be cohort-specific"
            ),
        ],
    }

    (
        args.output
        / "historical_collection_report.json"
    ).write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "candidate_cells": len(all_metrics),
                "source_status": [
                    (
                        x["year"],
                        x["status"],
                    )
                    for x in history
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
