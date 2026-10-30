import json
import unittest
from unittest.mock import patch

import pandas as pd

from pbi2html.read import _Model, read


class ReaderTests(unittest.TestCase):
    def test_revenue_sample(self):
        report = read("samples/Revenue-Opportunities.pbix")
        self.assertEqual(len(report["pages"]), 3)
        self.assertEqual(report["pages"][0]["name"], "Opportunity Overview")
        self.assertTrue(any(visual["rows"] for page in report["pages"] for visual in page["visuals"]))
        region = next(v for v in report["pages"][0]["visuals"] if v["id"] == "eaf58d9fd08140688010")
        self.assertEqual(region["category"], "Region")
        self.assertEqual(len(region["rows"]), 3)
        json.dumps(report)

    def test_calendar_relationship_populates_chart_and_tooltip(self):
        report = read("samples/Revenue-Opportunities.pbix")
        visuals = {v["id"]: v for p in report["pages"] for v in p["visuals"]}
        chart = visuals["efc42a2f1a28997b4092"]
        tooltip = visuals["e8ebd9f08a255541ab4c"]
        self.assertEqual(len(chart["rows"]), 12)
        self.assertEqual(sum(row[1] for row in chart["rows"]), 1968250939)
        self.assertEqual(sum(row[2] for row in chart["rows"]), 458)
        self.assertEqual(sum(row[1] for row in tooltip["rows"]), 458)
        self.assertFalse(any("no active relationship from Calculations to Calendar" in w for w in report["warnings"]))

    def test_corporate_sample_reports_unsupported_measures(self):
        report = read("samples/Corporate-Spend.pbix")
        self.assertEqual(len(report["pages"]), 3)
        self.assertTrue(any("unsupported measure" in warning for warning in report["warnings"]))
        self.assertTrue(any(v["text"] for page in report["pages"] for v in page["visuals"] if v["type"] == "textbox"))

    def test_aggregations_and_relationship(self):
        class Model:
            dax_measures = pd.DataFrame([
                {"TableName": "Facts", "Name": name, "Expression": f"{fn}([Amount])"}
                for name, fn in [("total", "SUM"), ("avg", "AVERAGE"), ("count", "COUNT"),
                                 ("counta", "COUNTA"), ("distinct", "DISTINCTCOUNT")]
            ])
            relationships = pd.DataFrame([{"FromTableName": "Facts", "FromColumnName": "Key",
                                            "ToTableName": "Groups", "ToColumnName": "Key", "IsActive": 1}])

            def get_table(self, name):
                return {"Facts": pd.DataFrame({"Key": [1, 1, 2], "Amount": [2, 4, 8]}),
                        "Groups": pd.DataFrame({"Key": [1, 2], "Name": ["A", "B"]})}[name]

        with patch("pbi2html.read.PBIXRay", return_value=Model()):
            model = _Model("unused")
        category = {"table": "Groups", "column": "Name"}
        values = [{"kind": "Measure", "table": "Facts", "column": name, "label": name} for name in
                  ("total", "avg", "count", "counta", "distinct")]
        self.assertEqual(model.rows(category, values), [["A", 6, 3, 2, 2, 2], ["B", 8, 8, 1, 1, 1]])
        self.assertEqual(model.rows(None, values)[0][0:3], [None, 14, 14 / 3])

    def test_invalid_file_is_a_warning(self):
        report = read("missing.pbix")
        self.assertEqual(report["pages"], [])
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()
