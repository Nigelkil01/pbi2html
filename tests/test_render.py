import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from pbi2html.render import chart, formatted, to_html, to_pdf, visual_body


REPORT = {
    'title': 'Example & report',
    'pages': [{'name': 'Overview', 'width': 800, 'height': 450, 'visuals': [
        {'type': kind, 'title': kind, 'x': i * 80, 'y': 0, 'w': 80, 'h': 200,
         'values': ['Amount'], 'rows': [['East', 1234], ['West', 500]]}
        for i, kind in enumerate(('clusteredBarChart', 'stackedColumnChart', 'lineChart', 'pieChart', 'donutChart', 'treemap'))
    ] + [
        {'type': 'card', 'x': 0, 'y': 200, 'w': 100, 'h': 100, 'rows': [[None, 1234]]},
        {'type': 'tableEx', 'x': 100, 'y': 200, 'w': 100, 'h': 100, 'values': ['Name', 'Total'], 'rows': [['East', 1234]]},
        {'type': 'textbox', 'x': 200, 'y': 200, 'w': 100, 'h': 100, 'text': '<hello>'},
        {'type': 'slicer', 'x': 300, 'y': 200, 'w': 100, 'h': 100, 'rows': [['East']]},
        {'type': 'map', 'x': 400, 'y': 200, 'w': 100, 'h': 100, 'rows': []},
    ]}], 'warnings': ['Sample warning']}


class RenderTests(unittest.TestCase):
    def test_html_visuals_and_escaping(self):
        result = to_html(REPORT)
        self.assertEqual(result.count('<svg '), 6)
        self.assertIn('<title>East: 1,234</title>', result)
        self.assertIn('left:10.0000%', result)
        self.assertIn('&lt;hello&gt;', result)
        self.assertIn('<table>', result)
        self.assertIn('map is not supported yet', result)
        self.assertIn('Sample warning', result)
        self.assertIn('page-break-before:always', result)

    def test_subtitle_is_escaped(self):
        report = {'pages': [{'visuals': [{'subtitle': 'A & B'}]}]}
        self.assertIn('A &amp; B', to_html(report))

    def test_chart_uses_report_theme(self):
        result = chart({'type': 'treemap', 'rows': [['A', 1], ['B', 2]], 'colors': ['#123456', '#abcdef']})
        self.assertIn('fill="#123456"', result)
        self.assertIn('fill="#abcdef"', result)

    def test_matrix_totals_and_currency_format(self):
        body = visual_body({'type': 'pivotTable', 'values': ['Group', 'Revenue'],
            'rows': [['A', 15.6], ['B', 20.4]], 'formats': [None, r'\$#,0'],
            'matrix_categories': 1, 'matrix_totals': [[['A', 15.6], ['B', 20.4]]],
            'grand_total': [36.0]})
        self.assertEqual(body.count('class="total"'), 1)
        self.assertIn('$16', body)
        self.assertIn('$36', body)
        nested = visual_body({'type': 'pivotTable', 'values': ['Size', 'Region', 'Partner', 'Count'],
            'rows': [['Large', 'East', 'A', 1], ['Large', 'East', 'B', 2]],
            'matrix_categories': 3,
            'matrix_totals': [[['Large', 3]], [['Large', 'East', 3]],
                              [['Large', 'East', 'A', 1], ['Large', 'East', 'B', 2]]],
            'grand_total': [3]})
        self.assertEqual(nested.count('class="total"'), 3)

    def test_percentage_formatting_in_tables_and_cards(self):
        self.assertEqual(formatted(0.25, '0.00%'), '25.00%')
        self.assertEqual(formatted(-0.125, '0.0%'), '-12.5%')
        body = visual_body({'type': 'pivotTable', 'values': ['Rate'], 'rows': [[0.25]], 'formats': ['0.00%']})
        self.assertIn('<td>25.00%</td>', body)
        card = visual_body({'type': 'card', 'values': ['Rate'], 'rows': [[None, 0.25]], 'formats': ['0.00%']})
        self.assertIn('>25.00%</strong>', card)

    def test_print_tables_are_paginated_outside_the_fixed_canvas(self):
        result = to_html(REPORT)
        canvas_end = result.index('</div><div class="print-table">')
        self.assertLess(result.index('class="visual table-visual"'), canvas_end)
        self.assertIn('Full table follows after this page.', result)
        self.assertIn('class="print-table"', result[canvas_end:])
        self.assertIn('.print-table .scroll { overflow:visible; }', result)
        self.assertIn('.print-table thead { display:table-header-group; }', result)

    def test_axis_titles_use_fields(self):
        body = chart({'type': 'barChart', 'category': 'Stage', 'values': ['Count'], 'rows': [['A', 4]]})
        self.assertIn('>Stage</text>', body)
        self.assertIn('>Count</text>', body)

    def test_maps_are_labeled_unsupported(self):
        for kind in ('map', 'shapeMap', 'filledMap'):
            with self.subTest(kind=kind):
                body = visual_body({'type': kind, 'rows': [['East', 4]]})
                self.assertIn(f'{kind} is not supported yet', body)
                self.assertNotIn('<svg', body)

    def test_year_month_chart_is_chronological(self):
        rows = [['2015-Dec', 12, 120], ['2015-Apr', 4, 40], ['2015-Jan', 1, 10], ['2014-Dec', 0, 0]]
        result = chart({'type': 'lineStackedColumnComboChart', 'rows': rows})
        self.assertLess(result.index('2014-Dec: 0'), result.index('2015-Jan: 1'))
        self.assertLess(result.index('2015-Jan: 1'), result.index('2015-Apr: 4'))
        self.assertLess(result.index('2015-Apr: 4'), result.index('2015-Dec: 12'))
        self.assertEqual(rows[0][0], '2015-Dec')
        non_dates = chart({'type': 'lineChart', 'rows': [['West', 1], ['East', 2]]})
        self.assertLess(non_dates.index('West: 1'), non_dates.index('East: 2'))

    def test_multirow_card_is_not_misrepresented(self):
        body = visual_body({'type': 'cardVisual', 'category': 'Month', 'values': ['Count'],
                            'rows': [['April', 45], ['May', 31]]})
        self.assertIn('Multiple rows cannot be summarized as a card', body)
        self.assertIn('April', body)
        self.assertIn('May', body)
        self.assertNotIn('<strong', body)
        card = visual_body({'type': 'card', 'values': ['Revenue'], 'formats': [r'\$#,0'], 'rows': [[None, 1968250939.0]]})
        self.assertIn('title="$1,968,250,939"', card)
        self.assertIn('$1.97bn', card)

    @unittest.skipUnless(shutil.which('chromium') and shutil.which('qpdf'), 'chromium and qpdf required')
    def test_locked_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path, pdf_path = Path(tmp) / 'report.html', Path(tmp) / 'report.pdf'
            html_path.write_text(to_html(REPORT), encoding='utf-8')
            password = '--example password'
            to_pdf(html_path, pdf_path, password)
            self.assertTrue(pdf_path.is_file())
            result = subprocess.run(['qpdf', '--requires-password', str(pdf_path)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            opened = subprocess.run(['qpdf', '--password=' + password, '--check', str(pdf_path)], capture_output=True)
            self.assertEqual(opened.returncode, 0, opened.stderr.decode())
            rejected = subprocess.run(['qpdf', '--password=wrong', '--check', str(pdf_path)], capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(sorted(os.listdir(tmp)), ['report.html', 'report.pdf'])


if __name__ == '__main__':
    unittest.main()
