# How the pieces fit

Three parts, one file each, built at the same time. Each part keeps to its own files.

| Part | File | Function |
|---|---|---|
| Reader | `pbi2html/read.py` | `read(pbix_path: str) -> dict` |
| Renderer | `pbi2html/render.py` | `to_html(report: dict) -> str`, `to_pdf(html_path: str, pdf_path: str, password: str or None = None) -> None` |
| Command + skill + docs | `pbi2html/__main__.py`, `.claude/skills/pbi2html/SKILL.md`, `README.md` | `python -m pbi2html report.pbix -o out/ [--lock]` |

## The report dict (what `read` returns, what `to_html` takes)

```json
{
  "title": "Revenue Opportunities",
  "pages": [
    {
      "name": "Opportunity Overview",
      "width": 1920, "height": 1080,
      "visuals": [
        {
          "id": "eaf58d9fd08140688010",
          "type": "treemap",
          "title": "Opportunity Count by Region",
          "x": 388.1, "y": 256.2, "w": 337.3, "h": 284.1,
          "category": "Region",
          "values": ["Opportunity Count"],
          "rows": [["East", 120], ["West", 98], ["Central", 77]],
          "text": null
        }
      ]
    }
  ],
  "measures": [{"name": "Revenue", "expression": "SUM('Calculations'[ProductRevenue])", "description": "..."}],
  "warnings": ["Page 2, visual 'map': maps are not supported yet"]
}
```

- `type` is the Power BI `visualType` as written in the file (`clusteredBarChart`, `lineChart`, `pieChart`,
  `donutChart`, `treemap`, `card`, `tableEx`, `textbox`, `slicer`, ...).
- `rows`: one list per category value, the category first, then one number per entry of `values`. A card has
  no category: `category` is null and `rows` is `[[null, 1234.5]]`. A table: `values` holds every column's label.
- `text`: the words of a textbox, otherwise null.
- Positions are in the page's own units (`width` by `height`).
- Anything the reader cannot work out goes in `warnings`, never a crash: the visual is still listed, with
  empty `rows`.

## Samples and tests

- `samples/*.pbix`: Microsoft's public sample reports (MIT, from microsoft/powerbi-desktop-samples).
- Tests are plain `unittest`: `.venv/bin/python -m unittest discover tests`. One test file per part.
- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
- PDF: headless Chromium (`chromium --headless --print-to-pdf`), locked with `qpdf --encrypt` (AES-256). Both are
  already on this machine. No other new libraries without asking.
- No real company data anywhere, ever.
