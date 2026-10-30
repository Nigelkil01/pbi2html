"""Convert a Power BI report to HTML and PDF."""

import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert a PBIX report to HTML and PDF.", allow_abbrev=False)
    parser.add_argument("report", help="Path to the .pbix report")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    passwords = parser.add_mutually_exclusive_group()
    passwords.add_argument("--lock", action="store_true", help="Ask for a PDF password")
    passwords.add_argument("--password-env", metavar="NAME", help="Read the PDF password from an environment variable")
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        parser.error("unrecognized arguments")

    password = None
    if args.lock:
        password = getpass.getpass("PDF password: ")
        if password != getpass.getpass("Confirm PDF password: "):
            parser.error("passwords do not match")
    elif args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            parser.error("the password environment variable is not set")
    if password == "":
        parser.error("the PDF password must not be empty")

    try:
        from .read import read
        from .render import to_html, to_pdf

        report = read(args.report)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        html_path = output / (Path(args.report).stem + ".html")
        pdf_path = output / (Path(args.report).stem + ".pdf")
        html_path.write_text(to_html(report), encoding="utf-8")
        for warning in report.get("warnings", []):
            print(f"Warning: {warning}")
        print(f"HTML: {html_path}")
        to_pdf(str(html_path), str(pdf_path), password)
        print(f"PDF: {pdf_path}")
    except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
