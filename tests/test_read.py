import json
import unittest
from unittest.mock import patch

import pandas as pd

from pbi2html.read import _Model, _visual, read


class ReaderTests(unittest.TestCase):
    def test_revenue_sample(self):
        report = read("samples/Revenue-Opportunities.pbix")
        self.assertEqual(len(report["pages"]), 3)
        self.assertEqual(report["colors"][:3], ["#4A8DDC", "#4C5D8A", "#F3C911"])
        self.assertEqual(report["pages"][0]["name"], "Opportunity Overview")
        self.assertTrue(any(visual["rows"] for page in report["pages"] for visual in page["visuals"]))
        region = next(v for v in report["pages"][0]["visuals"] if v["id"] == "eaf58d9fd08140688010")
        self.assertEqual(region["category"], "Region")
        self.assertEqual(len(region["rows"]), 3)
        self.assertEqual(region["title"], "Opportunity Count by Region")
        slicer = next(v for v in report["pages"][0]["visuals"] if v["type"] == "slicer")
        self.assertEqual(slicer["category"], "Product Code")
        self.assertTrue(slicer["rows"])
        self.assertEqual(len(slicer["rows"]), len({row[0] for row in slicer["rows"]}))
        products = _Model("samples/Revenue-Opportunities.pbix").table("Product")["Product Code"].drop_duplicates()
        self.assertEqual({row[0] for row in slicer["rows"]}, set(products))
        self.assertEqual(next(v for v in report["pages"][0]["visuals"] if v["id"] == "772b1906500006b78011")["title"], "Pipeline by Stage")
        json.dumps(report)

    def test_saved_pipeline_sort_is_applied(self):
        report = read("samples/Revenue-Opportunities.pbix")
        chart = next(v for v in report["pages"][0]["visuals"] if v["id"] == "772b1906500006b78011")
        self.assertEqual([row[0] for row in chart["rows"]], ["Lead", "Qualify", "Solution", "Proposal", "Finalize"])
        self.assertEqual([row[1] for row in chart["rows"]], [239, 94, 74, 37, 14])

    def test_detail_matrix_has_model_totals(self):
        report = read("samples/Revenue-Opportunities.pbix")
        matrix = next(v for v in report["pages"][1]["visuals"] if v["type"] == "pivotTable")
        self.assertEqual(matrix["values"], ["Opportunity Size", "Region", "Partner", "Opportunities", "Revenue", "Factored Revenue", "Avg Revenue"])
        self.assertEqual(sum(row[3] for row in matrix["rows"]), 458)
        self.assertEqual(sum(row[4] for row in matrix["rows"]), 1968250939)
        model = _Model("samples/Revenue-Opportunities.pbix")
        self.assertAlmostEqual(sum(row[5] for row in matrix["rows"]), model.table("Calculations")["FactoredProductRevenue"].sum())

    def test_sort_respects_raw_row_shape_and_aggregation(self):
        model = _Model("samples/Revenue-Opportunities.pbix")
        def column(table, name):
            return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}
        product = column("Product", "Product Code")
        identifier = column("Product", "Product ID")
        def visual(kind, projections, sort):
            return {"visual": {"visualType": kind, "query": {
                "queryState": {"Values": {"projections": [{"field": p} for p in projections]}},
                "sortDefinition": {"sort": [{"field": sort, "direction": "Ascending"}]}}}}
        for kind, fields, sort in (("slicer", [product], product), ("tableEx", [product, identifier], identifier)):
            warnings = []
            result = _visual(visual(kind, fields, sort), model, warnings, "test")
            self.assertEqual(len(result["rows"]), 25)
            index = 0 if kind == "slicer" else 1
            self.assertEqual([row[index] for row in result["rows"]], sorted(row[index] for row in result["rows"]))
            self.assertFalse(warnings)
        class FakeModel:
            formats = {}
            def rows(self, category, values):
                return [["A", 100, 10], ["B", 80, 40]]
        cat = column("T", "Group")
        amount = column("T", "Amount")
        total = {"Aggregation": {"Expression": amount, "Function": 0}}
        average = {"Aggregation": {"Expression": amount, "Function": 1}}
        data = {"visual": {"visualType": "barChart", "query": {"queryState": {
            "Category": {"projections": [{"field": cat}]},
            "Y": {"projections": [{"field": total}, {"field": average}]}},
            "sortDefinition": {"sort": [{"field": average, "direction": "Descending"}]}}}}
        self.assertEqual([r[0] for r in _visual(data, FakeModel(), [], "test")["rows"]], ["B", "A"])

    def test_original_reference_filter_differs_from_current_card(self):
        model = _Model("samples/Revenue-Opportunities.pbix")
        facts, stage = model.join_category("Calculations", {"table": "Sales stage", "column": "Sales Stage"})
        self.assertEqual(facts.loc[facts[stage] != "Finalize", "Opportunity ID"].count(), 444)
        report = read("samples/Revenue-Opportunities.pbix")
        card = next(v for v in report["pages"][0]["visuals"] if v["id"] == "d76a83b307156a9b4281")
        self.assertEqual(card["rows"][0][2], 458)

    def test_original_visual_and_page_filters(self):
        report = read("samples/Revenue-Opportunities-original.pbix")
        overview, detail = report["pages"][:2]
        count = next(v for v in overview["visuals"] if v["id"] == "2a48b7b9c411369725ca")
        revenue = next(v for v in overview["visuals"] if v["id"] == "0fa4715079a4d5b14dc6")
        matrix = next(v for v in detail["visuals"] if v["type"] == "pivotTable")
        month = next(v for v in overview["visuals"] if v["type"] == "lineStackedColumnComboChart")
        self.assertEqual([row[0] for row in month["rows"]], ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        pipeline = next(v for v in overview["visuals"] if v["title"] == "Pipeline by Stage")
        self.assertEqual(pipeline["color_gradient"], ["#b7bed0", "#4C5D8A"])
        self.assertEqual(month["series_color_indices"], {0: 1, 1: 2})
        self.assertEqual(count["rows"][0][1], 444)
        self.assertEqual(revenue["rows"][0][1], 1968250939)
        self.assertEqual(matrix["grand_total"][:3], [199, 840243113, 195370648])
        self.assertTrue(all(row[1] == "East" for row in matrix["rows"]))
        self.assertAlmostEqual(count["x"], 291.78)
        self.assertAlmostEqual(count["y"], 92.4)

    def test_saved_filter_operators_and_unrelated_tables(self):
        model = _Model("samples/Revenue-Opportunities-original.pbix")
        from pbi2html.read import _filter_conditions
        def column(table, name):
            return {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}
        condition = {"Comparison": {"ComparisonKind": 0, "Left": column("SalesStage", "Sales Stage"),
                                     "Right": {"Literal": {"Value": "'Finalize'"}}}}
        mask = model.filter_mask("Fact", [condition])
        self.assertEqual(int(mask.sum()), 14)
        inverse = model.filter_mask("Fact", [{"Not": {"Expression": condition}}])
        self.assertEqual(int(inverse.sum()), 444)
        self.assertEqual(_filter_conditions([{"filter": {"Where": [{"Condition": condition}]}}]), [condition])
        for operator in (1, 2, 3, 4):
            comparison = {"Comparison": {"ComparisonKind": operator, "Left": column("Fact", "Month_Number"),
                                         "Right": {"Literal": {"Value": "12L"}}}}
            actual = model.filter_mask("Fact", [comparison])
            fact = model.table("Fact")["Month_Number"]
            checks = {1: fact > 12, 2: fact >= 12, 3: fact < 12, 4: fact <= 12}
            self.assertEqual(actual.tolist(), checks[operator].tolist())
        with self.assertRaisesRegex(ValueError, "unsupported saved filter"):
            model.filter_mask("Fact", [{"Unknown": {}}])
        unsupported = _filter_conditions([{"filter": {"Top": {"Count": 3}}}])
        with self.assertRaisesRegex(ValueError, "unsupported saved filter"):
            model.filter_mask("Fact", unsupported)

    def test_detail_subtotals_recompute_weighted_average(self):
        report = read("samples/Revenue-Opportunities.pbix")
        matrix = next(v for v in report["pages"][1]["visuals"] if v["type"] == "pivotTable")
        model = _Model("samples/Revenue-Opportunities.pbix")
        facts = model.table("Calculations")
        self.assertEqual(matrix["grand_total"][0], 458)
        self.assertEqual(matrix["grand_total"][1], 1968250939)
        self.assertAlmostEqual(matrix["grand_total"][3], facts["ProductRevenue"].mean())
        self.assertIn('$', matrix["formats"][-1])
        self.assertEqual([row[0] for row in next(v for v in report["pages"][0]["visuals"] if v["type"] == "treemap")["rows"]], ["East", "Central", "West"])

    def test_revenue_card_matches_plain_sum(self):
        path = "samples/Revenue-Opportunities.pbix"
        report = read(path)
        card = next(v for p in report["pages"] for v in p["visuals"] if v["id"] == "d76a83b307156a9b4281")
        self.assertEqual(card["subtitle"], "Opportunity overview")
        revenue = card["rows"][0][1 + card["values"].index("Revenue")]
        self.assertEqual(revenue, _Model(path).table("Calculations")["ProductRevenue"].sum())
        self.assertEqual(revenue, 1968250939)

    def test_calendar_relationship_populates_chart_and_tooltip(self):
        report = read("samples/Revenue-Opportunities.pbix")
        visuals = {v["id"]: v for p in report["pages"] for v in p["visuals"]}
        chart = visuals["efc42a2f1a28997b4092"]
        tooltip = visuals["e8ebd9f08a255541ab4c"]
        self.assertEqual(len(chart["rows"]), 12)
        self.assertEqual(sum(row[1] for row in chart["rows"]), 1968250939)
        self.assertEqual(sum(row[2] for row in chart["rows"]), 458)
        self.assertEqual(chart["title"], "Revenue and Opportunity Count by Year Month")
        self.assertEqual(sum(row[1] for row in tooltip["rows"]), 458)
        self.assertFalse(any("no active relationship from Calculations to Calendar" in w for w in report["warnings"]))

    def test_corporate_sample_reports_unsupported_measures(self):
        report = read("samples/Corporate-Spend.pbix")
        self.assertEqual(len(report["pages"]), 3)
        self.assertTrue(any("unsupported measure" in warning for warning in report["warnings"]))
        self.assertTrue(any(v["text"] for page in report["pages"] for v in page["visuals"] if v["type"] == "textbox"))
        visuals = {v["id"]: v for page in report["pages"] for v in page["visuals"]}
        model = _Model("samples/Corporate-Spend.pbix")
        for visual_id, table, column, count in (
            ("aca2e102c59a26b226c2", "Toggle for variance to plan", "# / %", 2),
            ("3c278b3793b3d06103cb", "Toggle for breakdown", "Breakdown by", 5),
        ):
            visual = visuals[visual_id]
            self.assertEqual(visual["category"], column)
            self.assertEqual(len(visual["rows"]), count)
            self.assertEqual({row[0] for row in visual["rows"]}, set(model.table(table)[column].drop_duplicates()))
            self.assertFalse(any(visual_id in warning and "column has no aggregation" in warning for warning in report["warnings"]))

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
        model.tables["Groups"] = pd.DataFrame({"Key": [1, 1, 2], "Name": ["A", "A", "B"]})
        with self.assertRaises(pd.errors.MergeError):
            model.rows(category, values)

    def test_invalid_file_is_a_warning(self):
        report = read("missing.pbix")
        self.assertEqual(report["pages"], [])
        self.assertTrue(report["warnings"])


if __name__ == "__main__":
    unittest.main()
