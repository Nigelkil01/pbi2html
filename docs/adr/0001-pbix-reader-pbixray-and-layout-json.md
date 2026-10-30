# 0001: Read Power BI files with pbixray and the report's own layout files

- Status: accepted
- Date: 2026-10-30

## Context

A `.pbix` file is a ZIP package. Its report layout (pages, visuals, positions, titles, filters) is plain JSON under `Report/definition/` in current files, or one UTF-16 `Report/Layout` file in older ones. Its data model is a separate `DataModel` part packed as an XPress9-compressed VertiPaq backup, which is not practical to decode by hand in a short build.

The first version had to be built and demonstrated in about two hours, run on one machine without Power BI, Windows, or a Microsoft account, and work on Microsoft's public sample reports. REA (Reverse Engineer Anything) was available and can inventory and inspect the package, but it is an investigation tool with a large footprint, not a library to call on every conversion.

## Decision

Read the layout JSON directly from the ZIP with the Python standard library, and read tables, measures, and relationships from the data model with pbixray (pinned in `requirements.txt`). Evaluate only simple measures (SUM, AVERAGE, COUNT, COUNTA, DISTINCTCOUNT of one column) in pandas, joining tables along the model's many-to-one relationships without multiplying rows. Everything else becomes a plain-language warning, never a crash.

The reader returns one plain dict (described in [docs/contract.md](../contract.md)); the renderer draws it as one self-contained HTML file with inline SVG charts and no chart library.

Use REA to investigate formats and features the reader does not cover (see the teardown of `samples/Corporate-Spend.pbix`), not as a runtime dependency.

## Consequences

- No Power BI install, licence, or cloud service is needed to convert a report.
- pbixray is the one new library; a format change it does not follow breaks data loading until it is updated.
- Complex DAX measures, report, page, and visual filters, hidden pages, themes, conditional formatting, bookmarks, and custom visuals are not reproduced yet; each is reported as a warning.
- Maps, action buttons, and multi-field pivot tables show as unsupported boxes.
- The HTML is static: no cross-filtering between visuals.
- New visual types are added in `pbi2html/render.py` without touching the reader, as long as the dict shape holds.

## Verification

- `.venv/bin/python -m unittest discover tests` passes (reader, renderer, and command tests).
- `python -m pbi2html samples/Revenue-Opportunities.pbix -o out/` writes an HTML and a PDF with 3 pages and lists its warnings.
- The Revenue card equals the plain SUM of `Calculations[ProductRevenue]` (`tests/test_read.py`).
