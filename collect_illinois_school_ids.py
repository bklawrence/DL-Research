
#!/usr/bin/env python3
"""Discover Illinois RCDTS school IDs from official ISBE data.

Version: 2.1

Processes every Illinois entry in the research inventory,
including excluded schools and district-level programs.

Only unique, high-confidence matches may be used for
automated ACCESS PDF retrieval.

Requires collect_access.py in the repository root.
Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import csv
import re
from difflib import SequenceMatcher
from pathlib import Path

from collect_access import (
    DEFAULT_SOURCES,
    fetch,
    xlsx_tables,
)

VERSION = "2.1"

ABBREVIATIONS = {
    "elem": "elementary",
    "el": "elementary",
    "sch": "school",
    "intl": "international",
    "intrnl": "international",
    "ctr": "center",
    "cntr": "center",
    "lang": "language",
    "acad": "academy",
    "mag": "magnet",
    "imm": "immersion",
    "immrsn": "immersion",
    "biling": "bilingual",
    "prep": "preparatory",
}


def clean(value):
    """Normalize capitalization and punctuation."""
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_school(value):
    """Expand common abbreviations without removing identity."""
    words = clean(value).split()

    return " ".join(
        ABBREVIATIONS.get(word, word)
        for word in words
    )


def normalize_district(value):
    """Normalize common Illinois district naming variations."""
    text = clean(value)

    if "chicago public schools" in text:
        return "chicago 299"

    text = re.sub(r"\bdistrict\b", " ", text)
    text = re.sub(r"\bschools\b", " ", text)
    text = re.sub(r"\bschool\b", " ", text)
    text = re.sub(r"\bpublic\b", " ", text)
    text = re.sub(r"\bsd\b", " ", text)

    return " ".join(text.split())


def district_agrees(a, b):
    """Require agreement rather than guessing from school names."""
    if not a or not b:
        return False

    x = normalize_district(a)
    y = normalize_district(b)

    if x == y:
        return True

    # Allow common CUSD / USD wording differences only
    # when the remaining district identity agrees.
    def strip_type(text):
        return " ".join(
            word for word in text.split()
            if word not in (
                "cusd", "usd", "csd", "esd",
                "unit", "community", "consolidated",
            )
        )

    return bool(
        strip_type(x)
        and strip_type(x) == strip_type(y)
    )


def is_district_entry(name):
    """Identify inventory entries covering multiple school sites."""
    text = clean(name)

    patterns = (
        "district dl sites",
        "district dual language",
        "district level",
        "multiple sites",
        "district wide",
        "districtwide",
    )

    return any(pattern in text for pattern in patterns)


def school_level(name):
    """Recognize obvious incompatible school levels."""
    words = set(normalize_school(name).split())

    if "middle" in words or "junior" in words:
        return "middle"

    if "elementary" in words or "primary" in words:
        return "elementary"

    if "high" in words:
        return "high"

    return None


def incompatible_levels(a, b):
    x = school_level(a)
    y = school_level(b)

    return bool(x and y and x != y)


def rcdts_digits(value):
    """Retain all 15 digits, including leading zeros."""
    return re.sub(r"\D", "", str(value or ""))


def get_records(workbook):
    """Read school IDs from the ISBE General worksheet."""
    records = []

    for sheet, rows in xlsx_tables(workbook):
        if sheet != "General":
            continue

        initial = []

        for _ in range(32):
            try:
                initial.append(next(rows))
            except StopIteration:
                break

        columns = None
        header_position = None

        for position, row in enumerate(initial):
            mapping = {
                clean(value): index
                for index, value in row.items()
            }

            required = (
                "rcdts",
                "school name",
                "district",
            )

            if all(key in mapping for key in required):
                columns = mapping
                header_position = position
                break

        if columns is None:
            raise RuntimeError(
                "The General worksheet lacks required "
                "RCDTS, School Name, or District columns."
            )

        def capture(row):
            name = str(
                row.get(columns["school name"], "")
            ).strip()

            district = str(
                row.get(columns["district"], "")
            ).strip()

            school_id = rcdts_digits(
                row.get(columns["rcdts"], "")
            )

            if name and len(school_id) == 15:
                records.append({
                    "school": name,
                    "district": district,
                    "id": school_id,
                })

        for row in initial[header_position + 1:]:
            capture(row)

        for row in rows:
            capture(row)

        break

    if not records:
        raise RuntimeError(
            "No valid school IDs found in Illinois workbook."
        )

    # Deduplicate repeated records.
    unique = {}

    for record in records:
        key = (
            record["id"],
            record["school"],
            record["district"],
        )
        unique[key] = record

    return list(unique.values())


def compare(item, candidate):
    """Calculate school-name similarity and district agreement."""
    target_name = normalize_school(item["School"])
    source_name = normalize_school(candidate["school"])

    if incompatible_levels(
        item["School"],
        candidate["school"],
    ):
        return 0.0, False

    score = SequenceMatcher(
        None, target_name, source_name
    ).ratio()

    district_match = district_agrees(
        item.get("District", ""),
        candidate["district"],
    )

    return score, district_match


def match_school(item, records):
    """Choose a match conservatively, retaining alternatives."""
    if is_district_entry(item["School"]):
        return (
            None,
            "district_level_review",
            [],
            0.0,
        )

    scores = []

    for candidate in records:
        name_score, district_match = compare(
            item, candidate
        )

        # District agreement assists ranking, but cannot
        # compensate for a substantially incorrect school name.
        ranking = (
            name_score + 0.05
            if district_match
            else name_score
        )

        scores.append((
            ranking,
            name_score,
            district_match,
            candidate,
        ))

    scores.sort(
        key=lambda result: result[0],
        reverse=True,
    )

    if not scores:
        return None, "unresolved_review", [], 0.0

    best = scores[0]

    ranking = best[0]
    name_score = best[1]
    district_ok = best[2]
    selected = best[3]

    second_ranking = (
        scores[1][0]
        if len(scores) > 1
        else -1
    )

    margin = ranking - second_ranking
    alternatives = scores[:5]

    # Only a unique, near-exact name match AND district
    # agreement qualifies for automatic PDF retrieval.
    if (
        name_score >= 0.94
        and district_ok
        and margin >= 0.08
    ):
        if name_score >= 0.9999:
            status = "confirmed_normalized_match"
        else:
            status = "high_confidence_fuzzy_match"

        return selected, status, alternatives, name_score

    if name_score >= 0.85:
        return (
            selected,
            "probable_match_review",
            alternatives,
            name_score,
        )

    return (
        None,
        "unresolved_review",
        alternatives,
        name_score,
    )


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
            "output/raw/IL/"
            "IL_2025_report_card_public.xlsx"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/illinois_school_ids.csv"),
    )

    args = parser.parse_args()

    print(
        f"Illinois School ID Collector v{VERSION}",
        flush=True,
    )

    # Retrieve the official workbook if necessary.
    if not args.workbook.exists():
        result = fetch(
            DEFAULT_SOURCES["IL"]["url"],
            args.workbook,
        )

        if result["status"] not in (
            "downloaded",
            "cached",
        ):
            raise RuntimeError(
                f"Illinois workbook download failed: {result}"
            )

    # Load every Illinois inventory entry.
    with args.inventory.open(
        newline="",
        encoding="utf-8-sig",
    ) as file:
        inventory = [
            row
            for row in csv.DictReader(file)
            if row["State"].strip().upper() == "IL"
        ]

    if not inventory:
        raise RuntimeError(
            "No Illinois schools found in inventory."
        )

    records = get_records(args.workbook)

    print(
        f"Official school records loaded: {len(records)}",
        flush=True,
    )

    output = []

    for item in inventory:
        selected, status, alternatives, score = (
            match_school(item, records)
        )

        approved = status in (
            "confirmed_normalized_match",
            "high_confidence_fuzzy_match",
        )

        # An unapproved ID is retained as a candidate,
        # but cannot be used for automatic retrieval.
        output.append({
            "collector_version": VERSION,
            "Row_ID": item.get("Row_ID", ""),
            "School": item["School"],
            "District_inventory": item.get(
                "District", ""
            ),
            "Screening_status": item.get(
                "Screening_status", ""
            ),
            "Provisional_Analysis_Bucket": item.get(
                "Provisional_Analysis_Bucket", ""
            ),
            "RCDTS_15": (
                selected["id"] if selected else ""
            ),
            "School_name_reported": (
                selected["school"]
                if selected else ""
            ),
            "District_reported": (
                selected["district"]
                if selected else ""
            ),
            "match_status": status,
            "name_match_score": round(score, 4),
            "auto_pdf_eligible": (
                "yes" if approved else "no"
            ),
            "candidate_matches_for_review": (
                " | ".join(
                    (
                        f'{record["school"]} '
                        f'[{record["district"]}] '
                        f'({record["id"]})'
                    )
                    for _, _, _, record
                    in alternatives
                )
            ),
            "source_file": args.workbook.name,
        })

        print(
            f"{item['School']}: {status}"
            + (
                f" ({selected['id']})"
                if selected else ""
            ),
            flush=True,
        )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.output.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(output[0]),
        )

        writer.writeheader()
        writer.writerows(output)

    approved_count = sum(
        row["auto_pdf_eligible"] == "yes"
        for row in output
    )

    print()
    print("Illinois School ID Collection Complete")
    print("--------------------------------------")
    print(f"Collector version: {VERSION}")
    print(f"Inventory schools: {len(output)}")
    print(f"Approved matches: {approved_count}")
    print(f"Review needed: {len(output) - approved_count}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
