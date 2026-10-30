"""Render the shared report format as standalone HTML and PDF."""

import html
from datetime import datetime
import re
import math
import os
from pathlib import Path
import subprocess
import tempfile


COLORS = ('#315c72', '#5d8e91', '#bd8c5c', '#899da9', '#a9706e', '#7b8261')


def esc(value):
    return html.escape(str(value if value is not None else ''), quote=True)


def fmt(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value:,}'
    return str(value if value is not None else '')


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def svg(content):
    return f'<svg viewBox="0 0 400 200" role="img" aria-label="Chart" preserveAspectRatio="xMidYMid meet">{content}</svg>'


def compact(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return fmt(value)
    for scale, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(value) >= scale:
            return f'{value / scale:.2f}'.rstrip('0').rstrip('.') + suffix
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def chart(visual):
    kind = visual.get('type', '')
    rows = [r for r in (visual.get('rows') or []) if isinstance(r, (list, tuple)) and r]
    if kind in ('lineChart', 'lineStackedColumnComboChart', 'lineClusteredColumnComboChart', 'clusteredColumnChart', 'stackedColumnChart', 'columnChart', 'hundredPercentStackedColumnChart'):
        # Sort only unambiguous year-month labels; leave other categories in reader order.
        try:
            if rows and all(isinstance(r[0], str) and re.fullmatch(r'\d{4}-[A-Za-z]{3}', r[0]) for r in rows):
                rows.sort(key=lambda r: datetime.strptime(r[0], '%Y-%b'))
        except ValueError:
            pass
    if not rows:
        return svg('<text x="200" y="100" text-anchor="middle">No data in this sample</text>')
    parts = []
    if kind in ('pieChart', 'donutChart', 'treemap'):
        data = [(r[0], r[1], max(0, number(r[1]))) for r in rows if len(r) > 1]
        total = sum(v for _, _, v in data)
        if not total:
            return svg('<text x="200" y="100" text-anchor="middle">No positive values to display</text>')
        offset = 0
        for i, (label, raw, value) in enumerate(data):
            color = COLORS[i % len(COLORS)]
            title = esc(f'{label}: {fmt(raw)}')
            if kind == 'treemap':
                width = 380 * value / total
                parts.append(f'<rect x="{10 + offset:.3f}" y="25" width="{width:.3f}" height="150" fill="{color}"><title>{title}</title></rect>')
                if width > 42:
                    parts.append(f'<text x="{15 + offset:.3f}" y="48" fill="white">{esc(label)}</text>')
                offset += width
            elif value:
                fraction = value / total
                if kind == 'donutChart':
                    circumference = 2 * math.pi * 65
                    parts.append(f'<circle cx="100" cy="100" r="65" fill="none" stroke="{color}" stroke-width="30" stroke-dasharray="{fraction * circumference:.5f} {circumference:.5f}" stroke-dashoffset="{-offset * circumference:.5f}" transform="rotate(-90 100 100)"><title>{title}</title></circle>')
                elif fraction >= 1:
                    parts.append(f'<circle cx="100" cy="100" r="80" fill="{color}"><title>{title}</title></circle>')
                else:
                    start, end = 2 * math.pi * offset - math.pi / 2, 2 * math.pi * (offset + fraction) - math.pi / 2
                    x1, y1 = 100 + 80 * math.cos(start), 100 + 80 * math.sin(start)
                    x2, y2 = 100 + 80 * math.cos(end), 100 + 80 * math.sin(end)
                    parts.append(f'<path d="M 100 100 L {x1:.3f} {y1:.3f} A 80 80 0 {int(fraction > .5)} 1 {x2:.3f} {y2:.3f} Z" fill="{color}"><title>{title}</title></path>')
                offset += fraction
            if kind != 'treemap' and i < 8:
                parts.append(f'<rect x="205" y="{18+i*21}" width="10" height="10" fill="{color}"/><text x="221" y="{27+i*21}">{esc(label)}: {esc(fmt(raw))}</text>')
    else:
        horizontal = 'Bar' in kind or kind == 'barChart'
        combo = kind in ('lineStackedColumnComboChart', 'lineClusteredColumnComboChart')
        line_only = kind == 'lineChart'
        series = max(1, max(len(r) - 1 for r in rows))
        bar_series = 0 if line_only else (1 if combo else series)
        label_size = min(11, max(5, 150 / len(rows)))
        prefixes = {str(row[0]).rsplit('-', 1)[0] for row in rows if '-' in str(row[0])}
        shorten_dates = combo and len(rows) > 8 and len(prefixes) == 1 and all('-' in str(row[0]) for row in rows)
        nums = [number(x) for r in rows for x in r[1:1 + bar_series]] if bar_series else [number(x) for r in rows for x in r[1:] if x is not None]
        lo, hi = min(nums + [0]), max(nums + [0])
        span = hi - lo or 1
        if horizontal:
            zero = 125 + (0 - lo) / span * 220
            parts.append(f'<text x="125" y="195">{esc(compact(lo))}</text><text x="345" y="195" text-anchor="end">{esc(compact(hi))}</text>')
        else:
            zero = 165 - (0 - lo) / span * 140
            parts.append(f'<text x="35" y="28" text-anchor="end">{esc(compact(hi))}</text><text x="35" y="168" text-anchor="end">{esc(compact(lo))}</text>')
        if bar_series:
            for i, row in enumerate(rows):
                for j, raw in enumerate(row[1:1 + bar_series]):
                    if raw is None:
                        continue
                    val = number(raw)
                    color = COLORS[j % len(COLORS)]
                    title = esc(f'{row[0]}: {fmt(raw)}')
                    if horizontal:
                        end = 125 + (val - lo) / span * 220
                        h = 160 / len(rows) / bar_series * .75
                        y = 8 + i * 160 / len(rows) + j * 160 / len(rows) / bar_series
                        parts.append(f'<rect x="{min(zero,end):.2f}" y="{y:.2f}" width="{abs(end-zero):.2f}" height="{h:.2f}" fill="{color}"><title>{title}</title></rect>')
                        parts.append(f'<text font-size="{label_size:.1f}" x="{end + (3 if val >= 0 else -3):.2f}" y="{y+h*.8:.2f}" text-anchor="{"start" if val >= 0 else "end"}">{esc(compact(raw))}</text>')
                    else:
                        end = 165 - (val - lo) / span * 140
                        w = 330 / len(rows) / bar_series * .75
                        x = 45 + i * 330 / len(rows) + j * 330 / len(rows) / bar_series
                        parts.append(f'<rect x="{x:.2f}" y="{min(zero,end):.2f}" width="{w:.2f}" height="{abs(end-zero):.2f}" fill="{color}"><title>{title}</title></rect>')
                        parts.append(f'<text font-size="{min(label_size, 7 if len(rows) > 8 else 11):.1f}" x="{x+w/2:.2f}" y="{max(10, min(178, end - 3 if val >= 0 else end + 11)):.2f}" text-anchor="middle">{esc(compact(raw))}</text>')
                if horizontal:
                    parts.append(f'<text font-size="{label_size:.1f}" x="120" y="{8+(i+.7)*160/len(rows):.2f}" text-anchor="end">{esc(row[0])}</text>')
                else:
                    parts.append(f'<text font-size="{label_size:.1f}" x="{45+(i+.4)*330/len(rows):.2f}" y="185" text-anchor="middle">{esc(str(row[0]).rsplit("-", 1)[-1] if shorten_dates else row[0])}</text>')
        if line_only or combo:
            if combo:
                labels = visual.get('values') or []
                for j, label in enumerate(labels):
                    parts.append(f'<text x="{45 + j * 110}" y="12" fill="{COLORS[(j + (1 if j else 0)) % len(COLORS)]}">{esc(label)}</text>')
            vals = [number(value) for row in rows for value in row[2 if combo else 1:] if value is not None]
            for j in range(1 if combo else 0, series):
                llo, lhi = min(vals + [0]), max(vals + [0])
                lspan = lhi - llo or 1
                points = []
                for i, row in enumerate(rows + [None]):
                    if row is None or len(row) <= j + 1 or row[j + 1] is None:
                        if points:
                            parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{COLORS[(j + 1) % len(COLORS)]}" stroke-width="2"/>')
                            points = []
                        continue
                    x = (45 + (i + .375) * 330 / len(rows)) if combo else (45 + i * 330 / max(1, len(rows) - 1))
                    y = 165 - (number(row[j + 1]) - llo) / lspan * 140
                    points.append(f'{x:.2f},{y:.2f}')
                    parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="{COLORS[(j + 1) % len(COLORS)]}"><title>{esc(row[0])}: {esc(fmt(row[j + 1]))}</title></circle>')
            if line_only:
                parts.append(f'<text x="45" y="185">{esc(rows[0][0])}</text><text x="375" y="185" text-anchor="end">{esc(rows[-1][0])}</text>')
            elif series > 1:
                parts.append(f'<text x="398" y="28" text-anchor="end">{esc(compact(lhi))}</text><text x="398" y="168" text-anchor="end">{esc(compact(llo))}</text>')
    return svg(''.join(parts))

CHARTS = {'clusteredBarChart', 'stackedBarChart', 'barChart', 'clusteredColumnChart', 'stackedColumnChart', 'columnChart', 'hundredPercentStackedBarChart', 'hundredPercentStackedColumnChart', 'lineChart', 'lineStackedColumnComboChart', 'lineClusteredColumnComboChart', 'pieChart', 'donutChart', 'treemap'}


def visual_body(v):
    kind, rows = v.get('type', ''), v.get('rows') or []
    if kind in CHARTS:
        return chart(v)
    if kind in ('card', 'cardVisual'):
        if not rows or len(rows[0]) < 2:
            return '<div class="empty">No data in this sample</div>'
        labels = v.get('values') or []
        if len(rows) > 1:
            headings = [v.get('category') or 'Category'] + labels
            body = ''.join('<tr>' + ''.join(f'<td>{esc(fmt(cell))}</td>' for cell in row) + '</tr>' for row in rows)
            return '<div class="scroll"><p>Multiple rows cannot be summarized as a card.</p><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headings) + '</tr></thead><tbody>' + body + '</tbody></table></div>'
        cards = []
        for i, value in enumerate(rows[0][1:]):
            label = str(labels[i]) if i < len(labels) else ''
            prefix = '$' if 'revenue' in label.lower() else ''
            cards.append(f'<div class="card-item"><strong title="{prefix}{esc(fmt(value))}">{prefix}{esc(compact(value))}</strong><span>{esc(label)}</span></div>')
        return '<div class="card">' + ''.join(cards) + '</div>'
    if kind in ('tableEx', 'table'):
        if not rows:
            return '<div class="empty">No data in this sample</div>'
        headers = v.get('values') or []
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{esc(fmt(c))}</td>' for c in row) + '</tr>' for row in rows) + '</tbody></table></div>'
    if kind == 'textbox':
        return f'<div class="textbox">{esc(v.get("text"))}</div>' if v.get('text') else '<div class="empty">No data in this sample</div>'
    if kind == 'slicer':
        if not rows:
            return '<div class="empty">No data in this sample</div>'
        return '<ul class="slicer">' + ''.join(f'<li>{esc(fmt(row[0]))}</li>' for row in rows if row) + '</ul>'
    return f'<div class="unsupported">{esc(kind or "Visual")} is not supported yet.</div>'


def to_html(report):
    pages = []
    for page in report.get('pages', []):
        width, height = number(page.get('width')) or 1280, number(page.get('height')) or 720
        width, height = abs(width), abs(height)
        visuals = []
        for v in page.get('visuals', []):
            x, y = number(v.get('x')) / width * 100, number(v.get('y')) / height * 100
            w, h = max(0, number(v.get('w'))) / width * 100, max(0, number(v.get('h'))) / height * 100
            label = v.get('title')
            if label == 'Values by Category':
                values, category = v.get('values') or [], v.get('category')
                label = ' and '.join(map(str, values)) + (f' by {category}' if category else '') if values else (str(category) if category else '')
            title = f'<h3>{esc(label)}</h3>' if label else ''
            visuals.append(f'<article class="visual" style="left:{x:.4f}%;top:{y:.4f}%;width:{w:.4f}%;height:{h:.4f}%">{title}{visual_body(v)}</article>')
        pages.append(f'<section class="page"><h2>{esc(page.get("name") or "Report page")}</h2><div class="canvas" style="aspect-ratio:{width}/{height}">{"".join(visuals)}</div></section>')
    warnings = report.get('warnings') or []
    warning_html = '<aside><strong>Report warnings</strong><ul>' + ''.join(f'<li>{esc(w)}</li>' for w in warnings) + '</ul></aside>' if warnings else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(report.get('title') or 'Report')}</title><style>
@page {{ size: landscape; margin: 8mm; }} * {{ box-sizing: border-box; }} body {{ margin:0; background:#e9eef0; color:#213843; font:14px Arial,sans-serif; }} .page {{ background:white; margin:20px auto; padding:12px; width:min(100%, 1400px); break-before:page; page-break-before:always; }} .page:first-child {{ break-before:auto; page-break-before:auto; }} h2 {{ font-size:18px; margin:0 0 8px; }} .canvas {{ position:relative; width:100%; background:#f5f8f8; overflow:hidden; }} .visual {{ position:absolute; padding:6px; background:white; border:1px solid #dce5e6; overflow:hidden; display:flex; flex-direction:column; }} h3 {{ font-size:12px; line-height:1.2; margin:0 0 4px; font-weight:600; }} svg {{ width:100%; min-height:0; flex:1; font:11px Arial,sans-serif; fill:#425b66; }} .card {{ margin:auto; display:flex; gap:16px; text-align:center; }} .card-item strong {{ display:block; font-size:clamp(16px,2.5vw,36px); }} .card-item span {{ display:block; font-size:11px; color:#647981; }} .empty {{ margin:auto; color:#899b9f; }} .scroll {{ overflow:auto; }} table {{ border-collapse:collapse; width:100%; font-size:11px; }} th,td {{ padding:4px; border-bottom:1px solid #e5ebec; text-align:left; }} th {{ color:#315c72; }} .textbox {{ white-space:pre-wrap; }} .slicer {{ list-style:none; padding:0; margin:0; overflow:auto; }} .slicer li {{ padding:3px 0; }} .slicer li:before {{ content:'□'; margin-right:6px; }} .unsupported {{ margin:auto; color:#647981; }} aside {{ background:white; margin:20px auto; padding:16px; max-width:1400px; }} @media screen and (max-width:700px) {{ .page {{ overflow-x:auto; }} .canvas {{ min-width:900px; }} }} @media print {{ body {{ background:white; }} .page {{ width:100%; margin:0; padding:0; }} .canvas {{ max-height:85vh; }} aside {{ break-before:page; }} }}
</style></head><body>{''.join(pages)}{warning_html}</body></html>'''


def to_pdf(html_path, pdf_path, password=None):
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    if password is not None and (not password or '\n' in password or '\r' in password):
        raise ValueError('PDF password must be nonempty and cannot contain newlines')
    # Keep the unlocked file and Chromium profile private and remove both on failure.
    with tempfile.TemporaryDirectory(prefix='.pbi2html-', dir=pdf_path.parent) as tmp:
        unlocked = Path(tmp) / 'unlocked.pdf'
        subprocess.run(['chromium', '--headless', '--no-sandbox', '--disable-gpu', '--no-pdf-header-footer', f'--user-data-dir={tmp}/profile', f'--print-to-pdf={unlocked}', html_path.as_uri()], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if password is None:
            os.replace(unlocked, pdf_path)
        else:
            args = Path(tmp) / 'qpdf-args'
            fd = os.open(args, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(f'--user-password={password}\n--owner-password={password}\n')
            encrypted = Path(tmp) / 'locked.pdf'
            subprocess.run(['qpdf', '--encrypt', f'@{args}', '--bits=256', '--', str(unlocked), str(encrypted)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            os.replace(encrypted, pdf_path)
