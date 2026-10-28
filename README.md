# dual-language research collector

This repository runs a **public, school-aggregate ACCESS collector in GitHub Actions**. It supports the existing 56-entry Illinois, Indiana, Michigan, Minnesota, Missouri, North Dakota, South Dakota, and Wisconsin school inventory. School classifications distinguish **90–10, 80–20, and 50–50 kindergarten starting models** from normal progression toward 50–50 in later grades.

**Important:** This does not yet produce validated, comparable school exit rates. It retrieves raw candidate fields, maintains provenance, and identifies review work. A successful download alone does not prove an ACCESS statistic has the right grade, denominator, measure, year, or subgroup.

## One-time setup (no local Python needed)

1. Create a new GitHub repository (public or private). You can use GitHub Desktop to upload the **contents** of this starter folder, including `.github/workflows/collect-access.yml` (not the ZIP file itself).
2. Open the repository's **Actions** tab and enable Actions for the repository if prompted.
3. Select **Collect public ACCESS data**, click **Run workflow**, then click the green **Run workflow** button.
4. When the run finishes, open it and scroll to **Artifacts** to download the results. Open [`results/latest/README.md`](results/latest/README.md) to see a permanent run summary, and the CSV files in `results/latest/` when small enough for the repository.

The collector then runs automatically **on the first day of each month at 14:17 UTC**. You can run it manually whenever a state updates its public releases. Schedules run on the default branch and can be delayed or disabled by GitHub due to inactivity. See GitHub's [scheduled-workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

### Uploading using GitHub Desktop (easiest)

Create the empty repository on GitHub, clone it using GitHub Desktop, then copy the unzipped **files and folders** in this starter package into the cloned repo folder, ensuring the hidden `.github` folder is included. Commit and push with GitHub Desktop. You can then manage the collector entirely from the GitHub website.

### Alternative: Terminal one-time push

After creating an **empty** repository on GitHub, in Terminal from the unzipped folder:

```bash
git init
git add .
git commit -m "Add public ACCESS collector"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

The first `git push` will require GitHub authentication. Replace the URL with your new repo URL. If the Mac does not have Git configured, GitHub Desktop avoids those steps.

### GitHub Actions permissions

The workflow needs **Repository contents: Read and write** to commit the run summary and CSV snapshots. It declares `permissions: contents: write`. For repos with restricted Actions token settings, under **Settings → Actions → General → Workflow permissions**, allow read/write if needed. If pushes are prohibited by your organization, remove the "Save updated CSVs" step; the downloadable Actions artifact still works.

## What is automated?

| State | Retrieval |
|---|---|
| Illinois | Attempts the official 2025 Report Card public workbook direct download |
| Minnesota | Attempts the official 2025 ACCESS public workbook direct download |
| Wisconsin, Michigan, Indiana, Missouri, South Dakota, North Dakota | **No validated automatic data URL yet**. The workflow logs this explicitly rather than fabricating a source |

The IL/MN links are publicly documented but **have not yet been tested from a real GitHub runner**. The workflow will report errors if the provider redirects to HTML, rejects automated requests, or changes its file path. Those states' sources and download links are editable in `collect_access.py` at `DEFAULT_SOURCES`.

For other states, supply the official public school-level CSV/XLSX files in `incoming/STATE/` (e.g. `incoming/WI/2025_access_public.csv`) by committing them, or configure a trusted direct HTTPS source URL in the Python script. **Never commit restricted or student-level files.** Large or sensitive source files should *not* be placed in a public repo. The code only processes public aggregates and skips tables with recognizable student-ID headers; this is a precaution, not a privacy guarantee.

## Data in a completed run

- `results/latest/README.md` — a concise and persistent status report.
- `results/latest/school_inventory_with_collection_status.csv` — every school with candidate-field counts, including zeros.
- `results/latest/access_metric_candidates.csv` — raw matched possible ACCESS fields (**not standardized**).
- `results/latest/match_audit.csv` — school name matching confidence.
- `results/latest/field_inventory.csv` — original state-reported variable names.
- `results/latest/collection_report.json` — download and parse events and any errors.

All these reports are also in an Actions artifact, including CSV files too large for the repo snapshot. Raw statewide XLSX/ZIP downloads are **not** committed or uploaded as an artifact.

## Changing the collection year

The current collector is intentionally fixed to **2024–25 assessment results**, even when run in a later year. To change the assessment year, use the correct state-published URLs and verify year-specific exit criteria, model assignments, student cohorts, and reporting definitions before combining results. Editing filenames alone does not change the actual assessment period.

## Validity notes

1. ACCESS progress-to-proficiency, threshold attainment, mean composite level, and **reclassification/exit** are different outcomes.
2. The 2024–25 exit rules differ across Midwest states; school percentages are not automatically comparable.
3. A name match is not an NCES or state ID match; duplicate and renamed schools require review.
4. 90–10 schools often reach 50–50 in later elementary grades. That is a **normal model sequence**, not a mid-program policy change.
5. Where whole-school results combine DL and mainstream strands, they must be excluded from the primary DL-model comparisons unless DL-specific results become available.
6. Suppressed data must remain missing, never converted to 0.

### Official source landing pages

- [Illinois report card downloads](https://www.isbe.net/ilreportcarddata)
- [Minnesota assessment downloads](https://public.education.mn.gov/MDEAnalytics/DataTopic.jsp?TOPICID=1)
- [Wisconsin WISEdash](https://dpi.wi.gov/wisedash)
- [Michigan WIDA Proficiency](https://www.mischooldata.org/wida-proficiency/)

The collector uses Python 3's standard library. To test the code on your own computer if ever needed:

```bash
python3 collect_access.py --no-download --output output
python3 scripts/publish_results.py --input output --output results/latest
```

### October 27 collection audit patch

The collector now records `source_test`, `source_subject_or_domain`, source school/district IDs (when present), and any included-population designation. The additional `source_column_inventory.csv` lists state workbook header names so coded or stacked Illinois report-card columns can be mapped without guessing. School matching refuses to merge separately named elementary and middle schools.

**Warning:** These are still candidate values, not verified exit rates. Illinois data are not considered extracted unless ACCESS/English-learner outcome fields are explicitly identified. Minnesota's published `Percent Proficient` is an ACCESS proficiency measure when on a **Composite** row, but test/domain metadata must be retained and reviewed.
