
#!/usr/bin/env python3
"""Build an Illinois school-ID crosswalk from ISBE public data."""

import argparse
import csv
from pathlib import Path
from difflib import SequenceMatcher

from collect_access import (
    DEFAULT_SOURCES,
    fetch,
    xlsx_tables,
    strict_norm,
    normalize,
    choose_header,
)


def digits(value):
    return "".join(c for c in str(value) if c.isdigit())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("midwest_school_inventory.csv"),
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=Path(
            "output/raw/IL/IL_2025_report_card_public.xlsx"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/illinois_school_ids.csv"),
    )
    args = parser.parse_args()

    # Download the workbook only if not already present.
    if not args.workbook.exists():
        result = fetch(
            DEFAULT_SOURCES["IL"]["url"],
            args.workbook,
        )
        if result["status"] not in ("downloaded", "cached"):
            raise RuntimeError(f"Download failed: {result}")

    with args.inventory.open(
        newline="", encoding="utf-8-sig"
    ) as f:
        inventory = [
            r for r in csv.DictReader(f)
            if r["State"].strip().upper() == "IL"
        ]

    records = []

    # Only process General, not all assessment sheets.
    for sheet, rows in xlsx_tables(args.workbook):
        if sheet != "General":
            continue

        first = []
        for _ in range(32):
            try:
                first.append(next(rows))
            except StopIteration:
                break

        found = choose_header(first)
        if not found:
            raise RuntimeError("General header not found")

        header_row, headers = found

        columns = {
            str(v).strip().lower(): k
            for k, v in headers.items()
        }

        required = ("rcdts", "school name", "district")
        if not all(k in columns for k in required):
            raise RuntimeError(
                f"Required columns missing: {required}"
            )

        def capture(row):
            name = row.get(
                columns["school name"], ""
            ).strip()
            district = row.get(
                columns["district"], ""
            ).strip()
            school_id = digits(
                row.get(columns["rcdts"], "")
            )

            if name and len(school_id) == 15:
                records.append(
                    (name, district, school_id)
                )

        for row in first[header_row + 1:]:
            capture(row)

        for row in rows:
            capture(row)

        break

    if not records:
        raise RuntimeError("No Illinois school IDs found")

    records = list(dict.fromkeys(records))
    output = []

    for school in inventory:
        name = school["School"].strip()
        district = school.get("District", "").strip()

        exact = [
            r for r in records
            if strict_norm(r[0]) == strict_norm(name)
        ]

        same_district = [
            r for r in exact
            if strict_norm(r[1]) == strict_norm(district)
        ]

        normalized_district = [
            r for r in exact
            if district
            and normalize(r[1]) == normalize(district)
        ]

        selected = None

        if len({r[2] for r in same_district}) == 1:
            selected = same_district[0]
            status = "exact_name_and_district"

        elif len({r[2] for r in normalized_district}) == 1:
            selected = normalized_district[0]
            status = "normalized_district_review"

        elif len({r[2] for r in exact}) == 1:
            selected = exact[0]
            status = "district_unconfirmed_review"

        elif exact:
            status = "ambiguous_name_review"

        else:
            status = "no_exact_match_review"

        candidates = exact

        if not candidates:
            candidates = sorted(
                records,
                key=lambda r: SequenceMatcher(
                    None,
                    normalize(name),
                    normalize(r[0]),
                ).ratio(),
                reverse=True,
            )[:3]

        output.append({
            "Row_ID": school.get("Row_ID", ""),
            "School": name,
            "District_inventory": district,
            "Screening_status": school.get(
                "Screening_status", ""
            ),
            "Provisional_Analysis_Bucket": school.get(
                "Provisional_Analysis_Bucket", ""
            ),
            "RCDTS_15": selected[2] if selected else "",
            "School_name_reported": (
                selected[0] if selected else ""
            ),
            "District_reported": (
                selected[1] if selected else ""
            ),
            "match_status": status,
            "auto_pdf_eligible": (
                "yes"
                if status == "exact_name_and_district"
                else "no"
            ),
            "candidate_matches_for_review": " | ".join(
                f"{n} [{d}] ({code})"
                for n, d, code in candidates[:5]
            ),
            "source_file": args.workbook.name,
        })

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(output[0])
        )
        writer.writeheader()
        writer.writerows(output)

    confirmed = sum(
        r["auto_pdf_eligible"] == "yes"
        for r in output
    )

    print(f"Illinois inventory schools: {len(output)}")
    print(f"Exact name/district matches: {confirmed}")
    print(f"Need review: {len(output) - confirmed}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
