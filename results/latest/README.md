# Midwest ACCESS collector — latest run

Last attempt (UTC): 2026-10-27T22:51:42.535399+00:00

Parsed 2 source file(s); all extracted measures remain **unvalidated candidates**.

Inventory entries: **56**; source school-row matches: **1490**; candidate metric cells: **23658**.

**These counts are not validated ACCESS exit rates or an analytical sample size.**

## Source status

| State | Download status | Note |
|---|---|---|
| IL | downloaded |  |
| MN | downloaded |  |
| WI | no_verified_direct_download | Use publicly downloadable certified ACCESS/WIDA file; exact downloadable resource not confirmed for unattended retrieval. |
| MI | no_verified_direct_download | Interactive report/export. Put public school-level export in incoming/MI or supply --source MI=FILE. |
| IN | no_verified_direct_download | Await verified public school-level ACCESS URL. |
| MO | no_verified_direct_download | Await verified public school-level ACCESS URL. |
| SD | no_verified_direct_download | Await verified public school-level ACCESS URL. |
| ND | no_verified_direct_download | Await verified public school-level ACCESS URL. |

## Parsed source files

| State | Source | Parsed sheets | Warnings |
|---|---|---:|---|
| IL | IL_2025_report_card_public.xlsx | 15 |  |
| MN | MN_2025_ACCESS_public.xlsx | 5 |  |

## Research safeguards

- Raw state spreadsheets are not committed to the repository.
- Candidate fields retain **exactly** their source names; no exit rates are inferred.
- Name-based school matches must be reviewed before analysis.
- Whole-school scores may include non-dual-language strands.
- Historical 2024–25 ACCESS criteria vary by state; classifications follow kindergarten starting allocation.
- For every run, download all CSVs under **Actions → Collect public ACCESS data → chosen run → Artifacts**.

The [school inventory](../../midwest_school_inventory.csv) is a working screening dataset, not verified experimental evidence.
