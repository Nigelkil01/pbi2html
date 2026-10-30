# 0002: Lock shared PDFs with qpdf and keep passwords off the command line

- Status: accepted
- Date: 2026-10-30

## Context

Executives read these reports as PDFs today. Converted reports can hold sensitive figures and may be emailed or dropped in shared folders, so a leaked file should be unreadable on its own. The readers should not need new software, and the tool should add no new libraries for this.

A password given as a plain command argument shows up in the process list and shell history.

## Decision

Print the HTML to PDF with headless Chromium, then encrypt it with `qpdf --encrypt` at 256-bit AES and delete the unlocked copy. Take the password only from a prompt (`--lock`, asked twice) or an environment variable (`--password-env NAME`); never as an argument. Pass it to qpdf through an `@file` argument written to a private temporary folder, so it does not appear in qpdf's process list.

For a wider rollout, store the files behind the company's existing work logins (SharePoint or Teams on Microsoft 365), with the VPN as an extra wall. That choice belongs to the company's IT and security team.

## Consequences

- Any PDF reader opens the file after asking for the password; nothing new to install.
- Everyone with the password has the same access: no per-person access, no revoking, no record of who opened it. Logins in SharePoint or Teams cover that.
- The HTML file is not encrypted. Share the PDF, or keep the HTML behind logins.
- Chromium and qpdf must be installed where the tool runs.
- Encryption protects the file, not the password: a password sent next to the file protects nothing.

## Verification

- `tests/test_render.py`: `to_pdf` with a password gives a file that `qpdf --requires-password` reports as locked.
- `tests/test_cli.py`: the command with `--password-env` writes an HTML and a locked PDF.
- A password given as an unknown argument is rejected without echoing its value.
