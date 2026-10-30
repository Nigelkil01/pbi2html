---
name: pbi2html
description: Convert a Power BI PBIX report to HTML and PDF, explain conversion warnings, and help fix unsupported visuals.
---

# Convert a Power BI report

When someone asks to convert a Power BI report:

1. Find the requested `.pbix` file. Use sample data for demos, never real company data.
2. Set up the project from its root: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. Chromium and qpdf must also be installed.
3. Run `.venv/bin/python -m pbi2html path/to/report.pbix -o out/`. If the user wants a locked PDF, add `--lock` and let them enter and confirm the password privately. For unattended use, use `--password-env NAME` with a password already in that environment variable. Never put a password in a command argument or print it.
4. Open the resulting HTML and PDF so the user can review them. If the PDF is locked, the user may need to open it with their password.
5. Explain each warning in plain words, including what did not convert. Offer to fix unsupported visuals in the code.
