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
                    "label": projection.get("displayName") or projection.get("nativeQueryRef") or item.get("Property"),
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


def _resolve_sources(value, aliases):
    if isinstance(value, list):
        return [_resolve_sources(item, aliases) for item in value]
    if isinstance(value, dict):
        result = {key: _resolve_sources(item, aliases) for key, item in value.items()}
        if "SourceRef" in result and "Source" in result["SourceRef"]:
            source = result["SourceRef"].pop("Source")
            result["SourceRef"]["Entity"] = aliases.get(source, source)
        return result
    return value


def _filter_conditions(filters):
    conditions = []
    for item in filters or []:
        definition = item.get("filter")
        if not definition:
            continue
        aliases = {source["Name"]: source["Entity"] for source in definition.get("From", [])}
        clauses = definition.get("Where")
        if not clauses:
            conditions.append({"UnsupportedFilter": definition})
            continue
        for clause in clauses:
            conditions.append(_resolve_sources(clause.get("Condition", {"UnsupportedFilter": clause}), aliases))
    return conditions


def _filter_literal(expression):
    value = expression.get("Literal", {}).get("Value")
    if value is None:
        raise ValueError("unsupported filter value")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.lower() in ("null", "blank"):
        return None
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        return float(value.rstrip("LlDdMm"))
    except ValueError:
        raise ValueError("unsupported filter literal: " + value)


def _legacy_visual(container, config, parents):
    visual = config["singleVisual"]
    prototype = visual.get("prototypeQuery", {})
    aliases = {item["Name"]: item["Entity"] for item in prototype.get("From", [])}
    selects = {item["Name"]: _resolve_sources(item, aliases) for item in prototype.get("Select", [])}
    states = {}
    for role, projections in visual.get("projections", {}).items():
        if role in ("Category", "Group", "Axis") and any(item.get("active") for item in projections):
            projections = [item for item in projections if item.get("active")]
        fields = []
        for item in projections:
            ref = item["queryRef"]
            if ref not in selects:
                continue
            fields.append({"field": selects[ref], "displayName": visual.get("columnProperties", {}).get(ref, {}).get("displayName")})
        states[role] = {"projections": fields}
    sorts = []
    for item in prototype.get("OrderBy", []):
        sorts.append({"field": _resolve_sources(item.get("Expression", {}), aliases),
                      "direction": "Descending" if item.get("Direction") == 2 else "Ascending"})
    parent = parents.get(config.get("parentGroupName"), {})
    position = {key: container.get(key, 0) for key in ("x", "y", "width", "height")}
    position["x"] += parent.get("x", 0)
    position["y"] += parent.get("y", 0)
    container_objects = dict(visual.get("vcObjects", {}))
    if visual.get("visualType") == "card":
        titles = container_objects.get("title", [])
        if not any(item.get("properties", {}).get("show", {}).get("expr", {}).get("Literal", {}).get("Value") == "true" for item in titles):
            container_objects.pop("title", None)
    return {"name": config.get("name", ""), "position": position,
            "filterConfig": {"filters": json.loads(container.get("filters") or "[]")},
            "visual": {"visualType": visual.get("visualType", "unknown"),
                       "objects": visual.get("objects", {}),
                       "visualContainerObjects": container_objects,
                       "legacyProjections": visual.get("projections", {}),
                       "query": {"queryState": states, "sortDefinition": {"sort": sorts}}}}


class _Model:
    def __init__(self, path, warnings=None):
        self.model = PBIXRay(str(path))
        self.tables = {}
        self.measures = self.model.dax_measures.to_dict("records")
        self.relationships = self.model.relationships.to_dict("records")
        self.formats = {}
        self.sort_columns = {}
        source = getattr(getattr(self.model, "_metadata", None), "source", None)
        if source is not None and hasattr(source, "_db"):
            try:
                self.formats = {(table, name): fmt for table, name, fmt in source._db.conn.execute(
                    "SELECT t.Name, m.Name, m.FormatString FROM Measure m JOIN [Table] t ON m.TableID = t.ID")}
                self.sort_columns = {(table, column): sort for table, column, sort in source._db.conn.execute(
                    "SELECT t.Name, COALESCE(c.ExplicitName, c.InferredName), COALESCE(s.ExplicitName, s.InferredName) FROM [Column] c JOIN [Table] t ON c.TableID=t.ID JOIN [Column] s ON c.SortByColumnID=s.ID WHERE s.TableID=c.TableID")}
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

    def filter_mask(self, table, conditions):
        frame = self.table(table).reset_index(drop=True)

        def evaluate(condition):
            if "Not" in condition:
                return ~evaluate(condition["Not"]["Expression"])
            for operator in ("And", "Or"):
                if operator in condition:
                    item = condition[operator]
                    left, right = evaluate(item["Left"]), evaluate(item["Right"])
                    return left & right if operator == "And" else left | right
            if "In" in condition:
                item = condition["In"]
                expressions, choices = item["Expressions"], item["Values"]
                combined = pd.Series(False, index=frame.index)
                for choice in choices:
                    if len(choice) != len(expressions):
                        raise ValueError("invalid filter tuple")
                    match = pd.Series(True, index=frame.index)
                    for expression, literal in zip(expressions, choice):
                        field = _field({"field": expression})
                        joined, column = self.join_category(table, field)
                        series = joined[column].reset_index(drop=True)
                        value = _filter_literal(literal)
                        match &= series.isna() if value is None else series.eq(value)
                    combined |= match
                return combined
            if "Comparison" in condition:
                item = condition["Comparison"]
                field = _field({"field": item["Left"]})
                joined, column = self.join_category(table, field)
                series = joined[column].reset_index(drop=True)
                value = _filter_literal(item["Right"])
                operation = item["ComparisonKind"]
                if value is None and operation == 0:
                    return series.isna()
                methods = {0: series.eq, 1: series.gt, 2: series.ge, 3: series.lt, 4: series.le}
                if operation not in methods:
                    raise ValueError("unsupported filter comparison")
                return methods[operation](value)
            raise ValueError("unsupported saved filter condition")

        mask = pd.Series(True, index=frame.index)
        reachable = {table}
        while True:
            expanded = reachable | {r.get("ToTableName") for r in self.relationships
                                    if r.get("IsActive") and r.get("FromTableName") in reachable}
            if expanded == reachable:
                break
            reachable = expanded

        def sources(value):
            if isinstance(value, dict):
                return ({value["SourceRef"]["Entity"]} if "SourceRef" in value and "Entity" in value["SourceRef"] else set()).union(*(sources(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(sources(v) for v in value))
            return set()

        for condition in conditions:
            tables = sources(condition)
            if tables and tables.isdisjoint(reachable):
                continue
            if not tables.issubset(reachable):
                raise ValueError("filter combines related and unrelated tables")
            mask &= evaluate(condition)
        return mask

    def filtered_table(self, table, conditions):
        frame = self.table(table).reset_index(drop=True)
        return frame.loc[self.filter_mask(table, conditions)] if conditions else frame

    def matrix_rows(self, categories, values, filters=()):
        expressions = [self.expression(value) for value in values]
        if not expressions or len({item[0] for item in expressions}) != 1:
            raise ValueError("matrix measures from different tables are unsupported")
        table = expressions[0][0]
        frame = self.table(table).reset_index(drop=True).copy()
        groups = []
        for index, category in enumerate(categories):
            joined, column = self.join_category(table, category)
            name = f"__matrix_{index}"
            frame[name] = joined[column].reset_index(drop=True)
            groups.append(name)
        if filters:
            frame = frame.loc[self.filter_mask(table, filters)]
        levels = []
        for length in range(1, len(groups) + 1):
            current = groups[:length]
            grouped = frame.groupby(current, dropna=False, sort=True)
            columns = []
            for _, column, function in expressions:
                series = grouped[column]
                if function == "SUM":
                    aggregate = series.sum(min_count=1)
                elif function == "AVERAGE":
                    aggregate = series.mean()
                elif function == "COUNT":
                    numeric = pd.to_numeric(frame[column], errors="coerce")
                    aggregate = numeric.groupby([frame[group] for group in current], dropna=False).count()
                elif function == "COUNTA":
                    aggregate = series.count()
                elif function == "DISTINCTCOUNT":
                    aggregate = series.nunique(dropna=False)
                elif function == "MIN":
                    aggregate = series.min()
                else:
                    aggregate = series.max()
                columns.append(aggregate)
            result = pd.concat(columns, axis=1).reset_index()
            levels.append([[_scalar(value) for value in row] for row in result.itertuples(index=False, name=None)])
        return levels[-1], levels, self.rows(None, values, filters)[0][1:]

    def rows(self, category, values, filters=()):
        result = None
        for value in values:
            table, column, function = self.expression(value)
            if category:
                frame, group = self.join_category(table, category)
                if filters:
                    frame = frame.loc[self.filter_mask(table, filters)]
                grouped = frame.groupby(group, dropna=False)[column]
                aggregate = {"SUM": lambda: grouped.sum(min_count=1), "AVERAGE": grouped.mean,
                             "COUNT": lambda: frame.assign(__numeric=pd.to_numeric(frame[column], errors="coerce")).groupby(group, dropna=False)["__numeric"].count(), "COUNTA": grouped.count,
                             "DISTINCTCOUNT": lambda: grouped.nunique(dropna=False),
                             "MIN": grouped.min, "MAX": grouped.max}[function]()
                part = aggregate.rename(value["label"]).reset_index()
                part.columns = ["category", "value"]
            else:
                series = self.filtered_table(table, filters)[column]
                number = {"SUM": lambda: series.sum(min_count=1), "AVERAGE": series.mean,
                          "COUNT": lambda: pd.to_numeric(series, errors="coerce").count(), "COUNTA": series.count,
                          "DISTINCTCOUNT": lambda: series.nunique(dropna=False),
                          "MIN": series.min, "MAX": series.max}[function]()
                part = pd.DataFrame({"category": [None], "value": [number]})
            part.columns = ["category", f"value_{len(result.columns)-1 if result is not None else 0}"]
            result = part if result is None else result.merge(part, on="category", how="outer", validate="one_to_one")
        return [[_scalar(value) for value in row] for row in result.itertuples(index=False, name=None)] if result is not None else []


def _visual(data, model, warnings, page, page_filters=()):
    visual = data.get("visual", {})
    kind = visual.get("visualType", "unknown")
    position = data.get("position", {})
    title = None
    for entry in visual.get("visualContainerObjects", {}).get("title", []):
        text = _literal(entry.get("properties", {}).get("text", {}))
        if text is not None:
            title = text
    subtitle = ""
    for entry in visual.get("visualContainerObjects", {}).get("subTitle", []):
        subtitle = _literal(entry.get("properties", {}).get("text", {})) or subtitle
    result = {"id": data.get("name", ""), "type": kind, "title": title or "", "subtitle": subtitle,
              "x": position.get("x", 0), "y": position.get("y", 0),
              "w": position.get("width", 0), "h": position.get("height", 0),
              "category": None, "values": [], "rows": [], "text": None}
    prefix = f"Page '{page}', visual '{result['id']}' ({kind})"
    if kind == "textbox":
        result["title"] = ""
        result["text"] = _text(visual)
        if not result["text"]:
            warnings.append(prefix + ": textbox has no supported text")
        return result
    try:
        filters = list(page_filters) + _filter_conditions(data.get("filterConfig", {}).get("filters", [])) + _filter_conditions(visual.get("filterConfig", {}).get("filters", []))
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
        if len(categories) > 1 and kind != "pivotTable":
            raise ValueError("multiple category fields are unsupported")
        if kind == "pivotTable" and categories and values:
            result["values"] = [field["label"] for field in categories + values]
            result["rows"], result["matrix_totals"], result["grand_total"] = model.matrix_rows(categories, values, filters)
            result["matrix_categories"] = len(categories)
            result["formats"] = [None] * len(categories) + [model.formats.get((v["table"], v["column"])) for v in values]
            return result
        category = categories[0] if categories else None
        result["category"] = category["label"] if category else None
        result["values"] = [v["label"] for v in values]
        points = visual.get("objects", {}).get("dataPoint", [])
        for point in points:
            expression = point.get("properties", {}).get("fill", {}).get("solid", {}).get("color", {}).get("expr", {})
            rule = expression.get("FillRule", {}).get("FillRule", {}).get("linearGradient2", {})
            if rule:
                ends = [rule.get(end, {}).get("color", {}).get("Literal", {}).get("Value", "").strip("'") for end in ("min", "max")]
                if all(re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in ends):
                    result["color_gradient"] = ends
            color_id = expression.get("ThemeDataColor", {}).get("ColorId")
            if color_id is not None and expression["ThemeDataColor"].get("Percent", 0) == 0:
                refs = [projection.get("queryRef") for role, entries in visual.get("legacyProjections", {}).items()
                        if role not in ("Category", "Group", "Rows", "Axis", "Tooltips") for projection in entries]
                ref = point.get("selector", {}).get("metadata")
                index = refs.index(ref) if ref in refs else 0 if not ref else None
                if index is not None and color_id >= 2:
                    result.setdefault("series_color_indices", {})[index] = color_id - 2
        result["formats"] = [model.formats.get((v["table"], v["column"])) for v in values]
        if title is None and kind not in ("card", "cardVisual", "slicer"):
            result["title"] = " and ".join(result["values"])
            if category:
                result["title"] += (" by " if result["title"] else "") + category["label"]
        row_fields = [category] + values
        if kind in ("slicer", "advancedSlicerVisual") and not category and len(values) == 1 and values[0]["kind"] == "Column":
            field = values[0]
            row_fields = values
            result["category"] = field["label"]
            result["values"] = []
            result["rows"] = [[_scalar(value)] for value in model.filtered_table(field["table"], filters)[field["column"]].drop_duplicates()]
        elif kind == "tableEx" and not category and values and all(v["kind"] == "Column" and not v["aggregation"] for v in values):
            if len({v["table"] for v in values}) != 1:
                raise ValueError("table columns from different tables are unsupported")
            frame = model.filtered_table(values[0]["table"], filters)
            row_fields = values
            result["rows"] = [[_scalar(x) for x in row] for row in frame[[v["column"] for v in values]].itertuples(index=False, name=None)]
        elif values:
            result["rows"] = model.rows(category, values, filters) if filters else model.rows(category, values)
        else:
            raise ValueError("no supported values")
        sorts = visual.get("query", {}).get("sortDefinition", {}).get("sort", [])
        if kind == "treemap" and not sorts and category and values:
            result["rows"].sort(key=lambda row: (row[1] is not None, row[1] or 0), reverse=True)
        if sorts and result["rows"]:
            sort = sorts[0]
            try:
                sort_field = _field({"field": sort.get("field", {})})
            except ValueError:
                sort_field = {"kind": None, "table": None, "column": None}
                warnings.append(prefix + ": sort field is unsupported")
            index = next((i for i, field in enumerate(row_fields) if field and
                          (field["kind"], field["table"], field["column"], field["aggregation"]) ==
                          (sort_field["kind"], sort_field["table"], sort_field["column"], sort_field.get("aggregation"))), None)
            if index is not None:
                present = [row for row in result["rows"] if row[index] is not None]
                missing = [row for row in result["rows"] if row[index] is None]
                try:
                    key = lambda row: row[index]
                    sort_column = getattr(model, "sort_columns", {}).get((sort_field["table"], sort_field["column"]))
                    if sort_column:
                        frame = model.table(sort_field["table"])[[sort_field["column"], sort_column]].drop_duplicates()
                        if frame[sort_field["column"]].duplicated().any():
                            raise ValueError("sort-by column is not unique for each category")
                        order = dict(frame.itertuples(index=False, name=None))
                        key = lambda row: order[row[index]]
                    result["rows"] = sorted(present, key=key,
                                            reverse=sort.get("direction") == "Descending") + missing
                except TypeError:
                    warnings.append(prefix + ": sort values are not comparable")
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
                if "Report/Layout" not in archive.namelist():
                    report["warnings"].append("Missing report layout")
                    return report
                layout = json.loads(archive.read("Report/Layout").decode("utf-16-le"))
                config = json.loads(layout.get("config") or "{}")
                theme = config.get("themeCollection", {}).get("customTheme", {}).get("name")
                theme_path = "Report/StaticResources/RegisteredResources/" + (theme or "")
                if theme and theme_path in archive.namelist():
                    report["colors"] = json.loads(archive.read(theme_path)).get("dataColors", [])
                report_filters = _filter_conditions(json.loads(layout.get("filters") or "[]"))
                for page in layout.get("sections", []):
                    entry = {"name": page.get("displayName", ""), "width": page.get("width", 0),
                             "height": page.get("height", 0), "visuals": []}
                    report["pages"].append(entry)
                    filters = report_filters + _filter_conditions(json.loads(page.get("filters") or "[]"))
                    containers = page.get("visualContainers", [])
                    configs = [(item, json.loads(item.get("config") or "{}")) for item in containers]
                    parents = {config.get("name"): item for item, config in configs if "singleVisual" not in config}
                    for item, config in configs:
                        if "singleVisual" in config:
                            entry["visuals"].append(_visual(_legacy_visual(item, config, parents), model, report["warnings"], entry["name"], filters))
                return report
            report_path = "Report/definition/report.json"
            if report_path in archive.namelist():
                definition = json.loads(archive.read(report_path))
                theme = definition.get("themeCollection", {}).get("customTheme", {}).get("name")
                if theme:
                    theme_path = "Report/StaticResources/RegisteredResources/" + theme
                    if theme_path in archive.namelist():
                        colors = json.loads(archive.read(theme_path)).get("dataColors", [])
                        report["colors"] = [c for c in colors if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c)]
                report_filters = _filter_conditions(definition.get("filterConfig", {}).get("filters", []))
            report_filters = locals().get("report_filters", [])
            metadata = json.loads(archive.read(root + "pages.json"))
            for page_id in metadata.get("pageOrder", []):
                try:
                    path = root + page_id + "/"
                    page = json.loads(archive.read(path + "page.json"))
                    entry = {"name": page.get("displayName", page_id), "width": page.get("width", 0),
                             "height": page.get("height", 0), "visuals": []}
                    report["pages"].append(entry)
                    filters = report_filters + _filter_conditions(page.get("filterConfig", {}).get("filters", []))
                    for name in archive.namelist():
                        if name.startswith(path + "visuals/") and name.endswith("/visual.json"):
                            try:
                                entry["visuals"].append(_visual(json.loads(archive.read(name)), model, report["warnings"], entry["name"], filters))
                            except Exception as exc:
                                report["warnings"].append(f"Page '{entry['name']}', visual '{name}': {exc}")
                except Exception as exc:
                    report["warnings"].append(f"Page '{page_id}': {exc}")
    except Exception as exc:
        report["warnings"].append(f"Cannot read report layout: {exc}")
    return report
