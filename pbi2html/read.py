"""Read the portable layout and a small subset of the Power BI model."""

import json
import re
from collections import deque
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
from pbixray import PBIXRay


_MEASURE = re.compile(r"\s*(SUM|AVERAGE|COUNT|COUNTA|DISTINCTCOUNT)\s*\(\s*(?:(?:'((?:[^']|'')+)'|([^\[\s]+))\s*)?\[([^\]]+)\]\s*\)\s*", re.I)
_AGGREGATIONS = {0: "SUM", 1: "AVERAGE", 2: "COUNT", 3: "COUNT", 4: "MIN", 5: "MAX", 6: "DISTINCTCOUNT"}


def _field(projection):
    field = projection.get("field", {})
    aggregation = field.get("Aggregation")
    if aggregation:
        field = aggregation.get("Expression", {})
    for kind in ("Column", "Measure"):
        if kind in field:
            item = field[kind]
            table = item.get("Expression", {}).get("SourceRef", {}).get("Entity")
            return {"kind": kind, "table": table, "column": item.get("Property"),
                    "label": projection.get("nativeQueryRef") or item.get("Property"),
                    "aggregation": _AGGREGATIONS.get(aggregation.get("Function")) if aggregation else None}
    raise ValueError("unsupported query projection")


def _scalar(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _literal(item):
    value = item.get("expr", {}).get("Literal", {}).get("Value")
    if isinstance(value, str) and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return None


def _text(visual):
    paragraphs = visual.get("objects", {}).get("general", [])
    lines = []
    for entry in paragraphs:
        for paragraph in entry.get("properties", {}).get("paragraphs", []):
            lines.append("".join(run.get("value", "") for run in paragraph.get("textRuns", []) if isinstance(run.get("value"), str)))
    return "\n".join(lines)


class _Model:
    def __init__(self, path, warnings=None):
        self.model = PBIXRay(str(path))
        self.tables = {}
        self.measures = self.model.dax_measures.to_dict("records")
        self.relationships = self.model.relationships.to_dict("records")
        source = getattr(getattr(self.model, "_metadata", None), "source", None)
        if source is not None and hasattr(source, "_db"):
            try:
                # PBIXRay 0.15.5 excludes system-flagged tables from relationships.
                ft, fc, tt, tc = (source._rel_col(name) for name in
                                  ("FromTableID", "FromColumnID", "ToTableID", "ToColumnID"))
                sql = f"""SELECT f.Name, COALESCE(fc.ExplicitName, fc.InferredName),
                                 t.Name, COALESCE(tc.ExplicitName, tc.InferredName), r.IsActive
                          FROM Relationship r
                          JOIN [Table] f ON r.{ft} = f.ID
                          JOIN [Column] fc ON r.{fc} = fc.ID
                          JOIN [Table] t ON r.{tt} = t.ID
                          JOIN [Column] tc ON r.{tc} = tc.ID
                          WHERE r.IsActive = 1 AND (f.SystemFlags != 0 OR t.SystemFlags != 0)"""
                keys = ("FromTableName", "FromColumnName", "ToTableName", "ToColumnName", "IsActive")
                self.relationships.extend(dict(zip(keys, row)) for row in source._db.conn.execute(sql))
            except Exception as exc:
                if warnings is not None:
                    warnings.append(f"Cannot read system-table relationships: {exc}")

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = self.model.get_table(name)
        return self.tables[name]

    def expression(self, field):
        if field["kind"] == "Column":
            if not field["aggregation"]:
                raise ValueError("column has no aggregation")
            return field["table"], field["column"], field["aggregation"]
        matches = [m for m in self.measures if m["Name"] == field["column"] and m["TableName"] == field["table"]]
        if len(matches) != 1:
            raise ValueError("measure not found: " + str(field["column"]))
        match = _MEASURE.fullmatch(matches[0]["Expression"] or "")
        if not match:
            raise ValueError("unsupported measure: " + str(field["column"]))
        function, quoted, unquoted, column = match.groups()
        return (quoted.replace("''", "'") if quoted else unquoted or matches[0]["TableName"], column, function.upper())

    def join_category(self, table, category):
        target = category["table"]
        frame = self.table(table).copy()
        if table == target:
            return frame, category["column"]
        # Follow active many-to-one relationships away from the fact table.
        queue = deque([(table, [])])
        seen = {table}
        while queue:
            current, path = queue.popleft()
            for relation in self.relationships:
                if not relation.get("IsActive") or relation.get("FromTableName") != current:
                    continue
                next_table = relation.get("ToTableName")
                if next_table in seen:
                    continue
                next_path = path + [relation]
                if next_table == target:
                    for index, step in enumerate(next_path):
                        dest = self.table(step["ToTableName"])
                        key = step["ToColumnName"]
                        columns = [key]
                        if index + 1 < len(next_path):
                            columns.append(next_path[index + 1]["FromColumnName"])
                        else:
                            columns.append(category["column"])
                        columns = list(dict.fromkeys(columns))
                        right = dest[columns].rename(columns={c: f"__join_{index}_{c}" for c in columns})
                        frame = frame.merge(right, how="left", left_on=step["FromColumnName"] if index == 0 else f"__join_{index-1}_{step['FromColumnName']}", right_on=f"__join_{index}_{key}", validate="many_to_one")
                    return frame, f"__join_{len(next_path)-1}_{category['column']}"
                seen.add(next_table)
                queue.append((next_table, next_path))
        raise ValueError(f"no active relationship from {table} to {target}")

    def rows(self, category, values):
        result = None
        for value in values:
            table, column, function = self.expression(value)
            if category:
                frame, group = self.join_category(table, category)
                grouped = frame.groupby(group, dropna=False)[column]
                aggregate = {"SUM": lambda: grouped.sum(min_count=1), "AVERAGE": grouped.mean,
                             "COUNT": lambda: frame.assign(__numeric=pd.to_numeric(frame[column], errors="coerce")).groupby(group, dropna=False)["__numeric"].count(), "COUNTA": grouped.count,
                             "DISTINCTCOUNT": lambda: grouped.nunique(dropna=False),
                             "MIN": grouped.min, "MAX": grouped.max}[function]()
                part = aggregate.rename(value["label"]).reset_index()
                part.columns = ["category", "value"]
            else:
                series = self.table(table)[column]
                number = {"SUM": lambda: series.sum(min_count=1), "AVERAGE": series.mean,
                          "COUNT": lambda: pd.to_numeric(series, errors="coerce").count(), "COUNTA": series.count,
                          "DISTINCTCOUNT": lambda: series.nunique(dropna=False),
                          "MIN": series.min, "MAX": series.max}[function]()
                part = pd.DataFrame({"category": [None], "value": [number]})
            part.columns = ["category", f"value_{len(result.columns)-1 if result is not None else 0}"]
            result = part if result is None else result.merge(part, on="category", how="outer", validate="one_to_one")
        return [[_scalar(value) for value in row] for row in result.itertuples(index=False, name=None)] if result is not None else []


def _visual(data, model, warnings, page):
    visual = data.get("visual", {})
    kind = visual.get("visualType", "unknown")
    position = data.get("position", {})
    title = None
    for entry in visual.get("visualContainerObjects", {}).get("title", []):
        text = _literal(entry.get("properties", {}).get("text", {}))
        if text is not None:
            title = text
    result = {"id": data.get("name", ""), "type": kind, "title": title or "",
              "x": position.get("x", 0), "y": position.get("y", 0),
              "w": position.get("width", 0), "h": position.get("height", 0),
              "category": None, "values": [], "rows": [], "text": None}
    prefix = f"Page '{page}', visual '{result['id']}' ({kind})"
    if kind == "textbox":
        result["text"] = _text(visual)
        if not result["text"]:
            warnings.append(prefix + ": textbox has no supported text")
        return result
    try:
        states = visual.get("query", {}).get("queryState", {})
        categories, values = [], []
        for role, state in states.items():
            if role in ("Tooltips",):
                continue
            projections = [p for p in state.get("projections", []) if p.get("active") is not False]
            fields = [_field(p) for p in projections]
            if role in ("Category", "Group", "Rows", "Axis"):
                categories.extend(fields)
            elif role in ("Series", "Columns", "ExplainBy") and fields:
                raise ValueError(f"unsupported {role} grouping")
            else:
                values.extend(fields)
        if len(categories) > 1:
            raise ValueError("multiple category fields are unsupported")
        category = categories[0] if categories else None
        result["category"] = category["label"] if category else None
        result["values"] = [v["label"] for v in values]
        if title is None:
            result["title"] = " and ".join(result["values"])
            if category:
                result["title"] += (" by " if result["title"] else "") + category["label"]
        if kind in ("slicer", "advancedSlicerVisual") and not category and len(values) == 1 and values[0]["kind"] == "Column":
            field = values[0]
            result["category"] = field["label"]
            result["values"] = []
            result["rows"] = [[_scalar(value)] for value in model.table(field["table"])[field["column"]].drop_duplicates()]
        elif kind == "tableEx" and not category and values and all(v["kind"] == "Column" and not v["aggregation"] for v in values):
            if len({v["table"] for v in values}) != 1:
                raise ValueError("table columns from different tables are unsupported")
            frame = model.table(values[0]["table"])
            result["rows"] = [[_scalar(x) for x in row] for row in frame[[v["column"] for v in values]].itertuples(index=False, name=None)]
        elif values:
            result["rows"] = model.rows(category, values)
        else:
            raise ValueError("no supported values")
        if data.get("filterConfig", {}).get("filters") or visual.get("filterConfig", {}).get("filters"):
            warnings.append(prefix + ": visual filters are not applied")
    except Exception as exc:
        warnings.append(f"{prefix}: {exc}")
        result["rows"] = []
    return result


def read(pbix_path):
    """Return the shared report dictionary; unsupported content is reported as warnings."""
    report = {"title": Path(pbix_path).stem.replace("-", " ").replace("_", " "),
              "pages": [], "measures": [], "warnings": []}
    try:
        model = _Model(pbix_path, report["warnings"])
        report["measures"] = [{"name": m["Name"], "expression": m["Expression"],
                               "description": m.get("Description") or ""} for m in model.measures]
    except Exception as exc:
        model = None
        report["warnings"].append(f"Cannot read data model: {exc}")
    try:
        with ZipFile(pbix_path) as archive:
            root = "Report/definition/pages/"
            if root + "pages.json" not in archive.namelist():
                report["warnings"].append("Legacy Report/Layout or missing page definitions are unsupported")
                return report
            report_path = "Report/definition/report.json"
            if report_path in archive.namelist():
                definition = json.loads(archive.read(report_path))
                if definition.get("filterConfig", {}).get("filters"):
                    report["warnings"].append("Report filters are not applied")
            metadata = json.loads(archive.read(root + "pages.json"))
            for page_id in metadata.get("pageOrder", []):
                try:
                    path = root + page_id + "/"
                    page = json.loads(archive.read(path + "page.json"))
                    entry = {"name": page.get("displayName", page_id), "width": page.get("width", 0),
                             "height": page.get("height", 0), "visuals": []}
                    report["pages"].append(entry)
                    if page.get("filterConfig", {}).get("filters"):
                        report["warnings"].append(f"Page '{entry['name']}': page filters are not applied")
                    for name in archive.namelist():
                        if name.startswith(path + "visuals/") and name.endswith("/visual.json"):
                            try:
                                entry["visuals"].append(_visual(json.loads(archive.read(name)), model, report["warnings"], entry["name"]))
                            except Exception as exc:
                                report["warnings"].append(f"Page '{entry['name']}', visual '{name}': {exc}")
                except Exception as exc:
                    report["warnings"].append(f"Page '{page_id}': {exc}")
    except Exception as exc:
        report["warnings"].append(f"Cannot read report layout: {exc}")
    return report
