
#!/usr/bin/env python3
"""Test automated retrieval of Illinois ACCESS report-card data.

School: International Prep Academy
School ID: 090100040262028
Reporting year: 2024-25

Diagnostic only. Does not modify the research dataset.
"""

from pathlib import Path
import json
import re
import shutil
import subprocess
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SCHOOL_ID = "090100040262028"

BASE_URL = (
    "https://apps.isbe.net/customizablepdf/"
    "Public/Index/en/School/"
)

OUTPUT = Path("output/ipa_access_test")
OUTPUT.mkdir(parents=True, exist_ok=True)

URL = BASE_URL + SCHOOL_ID

EXPECTED = {
    "exit_rate": "4.2%",
    "exit_count": "16",
    "participants": "379",
}

report = {
    "school": "International Prep Academy",
    "school_id": SCHOOL_ID,
    "url": URL,
    "retrieval_status": "not_attempted",
    "response_type": None,
    "pdf_received": False,
    "access_table_found": False,
    "expected_values_found": {},
    "warnings": [],
}


def save_report():
    path = OUTPUT / "diagnostic.json"
    path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )


def main():
    print(f"Testing: {URL}", flush=True)

    request = Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/pdf,text/html,*/*",
        },
    )

    try:
        with urlopen(request, timeout=45) as response:
            content_type = response.headers.get(
                "Content-Type", ""
            )

            data = response.read(20 * 1024 * 1024)

            report["response_type"] = content_type
            report["final_url"] = response.geturl()
            report["response_bytes"] = len(data)

    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        report["retrieval_status"] = "request_failed"
        report["warnings"].append(str(exc))
        save_report()
        print(f"Request failed: {exc}")
        return

    # A PDF file begins with the bytes %PDF-
    if not data.startswith(b"%PDF-"):
        report["retrieval_status"] = "non_pdf_response"
        report["warnings"].append(
            "Endpoint did not return a PDF. "
            "It may require report-builder selections."
        )

        # Save a short HTML diagnostic for inspection.
        preview = data[:100000].decode(
            "utf-8", errors="replace"
        )

        (OUTPUT / "response_preview.html").write_text(
            preview,
            encoding="utf-8",
        )

        save_report()
        print("The endpoint did not return a PDF.")
        return

    pdf_path = OUTPUT / "ipa_report.pdf"
    pdf_path.write_bytes(data)

    report["retrieval_status"] = "pdf_received"
    report["pdf_received"] = True

    # pdftotext is installed by the GitHub workflow.
    if not shutil.which("pdftotext"):
        report["warnings"].append(
            "pdftotext is unavailable."
        )
        save_report()
        return

    text_path = OUTPUT / "ipa_report.txt"

    process = subprocess.run(
        [
            "pdftotext",
            "-layout",
            str(pdf_path),
            str(text_path),
        ],
        capture_output=True,
        text=True,
    )

    if process.returncode != 0:
        report["warnings"].append(
            "PDF text extraction failed: "
            + process.stderr[:500]
        )
        save_report()
        return

    text = text_path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    report["access_table_found"] = bool(
        re.search(
            r"EL\s+on\s+ACCESS|EL\s+Exit\s+Student\s+Rate",
            text,
            flags=re.IGNORECASE,
        )
    )

    for label, expected in EXPECTED.items():
        report["expected_values_found"][label] = (
            expected in text
        )

    save_report()

    print("\nDiagnostic results")
    print("------------------")

    print(
        "ACCESS table found:",
        report["access_table_found"],
    )

    for label, found in report[
        "expected_values_found"
    ].items():
        print(f"{label}: {found}")

    if (
        report["access_table_found"]
        and all(report["expected_values_found"].values())
    ):
        print(
            "\nSUCCESS: Expected IPA values appear "
            "in the downloaded PDF."
        )
    else:
        print(
            "\nThe PDF was retrieved, but the expected "
            "ACCESS values were not fully confirmed."
        )


if __name__ == "__main__":
    main()
