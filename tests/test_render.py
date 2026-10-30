import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from pbi2html.render import chart, to_html, to_pdf, visual_body


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
        card = visual_body({'type': 'card', 'values': ['Revenue'], 'rows': [[None, 1968250939.0]]})
        self.assertIn('title="$1,968,250,939.0"', card)
        self.assertIn('$1.97B', card)

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
