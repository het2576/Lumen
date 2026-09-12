"""Verified structured-data queries.

The model selects a constrained JSON query plan; pandas performs arithmetic.
Arbitrary model-generated Python is never executed.
"""
import json
import logging
import re
from typing import Any

import pandas as pd

from app.config import GENERATION_MODEL
from app.services.gemini_client import get_genai

logger = logging.getLogger(__name__)

_TABULAR_KEYWORDS = {
    "average", "avg", "mean", "sum", "total", "count", "max", "maximum", "min", "minimum",
    "median", "variance", "percent", "percentage", "rows where", "filter", "greater than", "less than",
    "highest", "lowest", "column", "sheet", "table", "spreadsheet", "csv", "xlsx", "chart", "trend",
    "compare", "difference", "increase", "decrease",
}
_ACTIONS = {"aggregate", "rows", "top", "chart", "compare"}
_AGGREGATES = {"sum", "mean", "median", "min", "max", "count"}
_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}


def is_tabular_question(question: str) -> bool:
    return any(term in question.lower() for term in _TABULAR_KEYWORDS)


def _metadata(tables: list[dict]) -> list[dict[str, Any]]:
    return [{"id": table.get("id"), "name": table.get("table_name"), "document_id": table.get("document_id"), "columns": table.get("column_headers", [])} for table in tables]


def _query_plan(question: str, tables: list[dict]) -> dict[str, Any] | None:
    prompt = f"""Turn this spreadsheet question into one JSON query plan. Do not calculate values.
Available tables: {json.dumps(_metadata(tables))}
Question: {question}
Return only JSON: table_id, table_ids (optional array; required for compare), action (aggregate|rows|top|chart|compare), column (or null), aggregate (sum|mean|median|min|max|count or null), filters (array of {{column, operator: eq|ne|gt|gte|lt|lte|contains, value}}), sort_column (or null), descending (boolean), limit (1-25). For comparisons use compare and every relevant table ID. Use only exact IDs and columns shown. If impossible return {{"cannot_answer": true}}."""
    try:
        text = (get_genai().GenerativeModel(GENERATION_MODEL).generate_content(prompt).text or "").strip()
        return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip())
    except Exception:
        logger.exception("Structured query planning failed")
        return None


def _fallback_plan(question: str, tables: list[dict]) -> dict[str, Any] | None:
    if not tables:
        return None
    table = tables[0]
    columns = table.get("column_headers", [])
    q = question.lower()
    column = next((column for column in columns if str(column).lower() in q), None)
    aggregate = next((name for name in _AGGREGATES if name in q or (name == "mean" and "average" in q)), None)
    action = "compare" if len(tables) > 1 and any(word in q for word in ("compare", "difference", "increase", "decrease", "across")) else "chart" if any(word in q for word in ("chart", "trend", "plot", "graph")) else "aggregate" if aggregate else "rows"
    return {"table_id": table.get("id"), "table_ids": [table.get("id") for table in tables] if action == "compare" else None, "action": action, "column": column, "aggregate": aggregate, "filters": [], "sort_column": column, "descending": "lowest" not in q, "limit": 10}


def _apply_filters(frame: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    for item in filters:
        column, operator, value = item.get("column"), item.get("operator"), item.get("value")
        if column not in frame.columns or operator not in _OPERATORS:
            continue
        series = frame[column]
        numeric = pd.to_numeric(series, errors="coerce")
        if operator == "contains":
            frame = frame[series.astype(str).str.contains(str(value), case=False, na=False)]
        elif operator in {"gt", "gte", "lt", "lte"}:
            expected = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.notna(expected):
                comparison = {"gt": numeric > expected, "gte": numeric >= expected, "lt": numeric < expected, "lte": numeric <= expected}[operator]
                frame = frame[comparison]
        else:
            matches = series.astype(str).str.lower() == str(value).lower()
            frame = frame[~matches if operator == "ne" else matches]
    return frame


def _markdown(frame: pd.DataFrame, limit: int) -> str:
    frame = frame.head(max(1, min(int(limit or 10), 25))).fillna("")
    headers = [str(column) for column in frame.columns]
    rows = [[str(value).replace("|", "\\|") for value in row] for row in frame.astype(str).values.tolist()]
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "\n".join("| " + " | ".join(row) + " |" for row in rows)


def execute_structured_query(question: str, tables: list[dict]) -> dict[str, Any]:
    if not tables:
        return {"answer": "I couldn't find verified structured data for that request.", "success": False}
    plan = _query_plan(question, tables) or _fallback_plan(question, tables) or {}
    table = next((candidate for candidate in tables if str(candidate.get("id")) == str(plan.get("table_id"))), None)
    if plan.get("cannot_answer") or not table or plan.get("action") not in _ACTIONS:
        return {"answer": "I couldn't match that request to a verified table or column.", "success": False}
    columns = table.get("column_headers") or []
    column = plan.get("column")
    if column and column not in columns or plan.get("aggregate") and plan["aggregate"] not in _AGGREGATES:
        return {"answer": "I couldn't match that request to a verified table or column.", "success": False}

    frame = _apply_filters(pd.DataFrame(table.get("row_data") or [], columns=columns), plan.get("filters") or [])
    action = plan["action"]
    if action == "compare":
        table_ids = {str(table_id) for table_id in plan.get("table_ids") or []}
        comparison_tables = [candidate for candidate in tables if str(candidate.get("id")) in table_ids]
        aggregate = plan.get("aggregate") or "sum"
        if not column or aggregate not in _AGGREGATES or len(comparison_tables) < 2:
            return {"answer": "Choose a shared numeric column and at least two verified tables to compare.", "success": False}
        result_rows = []
        for candidate in comparison_tables:
            if column not in (candidate.get("column_headers") or []):
                continue
            candidate_frame = _apply_filters(pd.DataFrame(candidate.get("row_data") or [], columns=candidate.get("column_headers") or []), plan.get("filters") or [])
            values = pd.to_numeric(candidate_frame[column], errors="coerce").dropna()
            if values.empty:
                continue
            value = int(values.notna().sum()) if aggregate == "count" else getattr(values, aggregate)()
            result_rows.append({"Source": candidate.get("document_name") or candidate.get("table_name"), "Value": value})
        if len(result_rows) < 2:
            return {"answer": f"I couldn't find comparable numeric values for {column} in at least two verified tables.", "success": False}
        result_frame = pd.DataFrame(result_rows)
        points = [{"label": str(row["Source"]), "value": float(row["Value"])} for row in result_rows]
        return {"answer": f"Verified comparison of {aggregate} {column}:\n\n" + _markdown(result_frame, 25), "success": True, "table": comparison_tables[0], "tables": comparison_tables, "verification": "verified", "chart": {"type": "bar", "title": f"{column} · verified comparison", "data": points}}
    if action == "aggregate":
        aggregate = plan.get("aggregate") or "count"
        if aggregate == "count":
            value = int(frame[column].notna().sum()) if column else len(frame)
        elif not column:
            return {"answer": "Choose a column to calculate that value from.", "success": False}
        else:
            values = pd.to_numeric(frame[column], errors="coerce").dropna()
            if values.empty:
                return {"answer": f"{column} does not contain enough numeric values for a verified {aggregate}.", "success": False}
            value = getattr(values, aggregate)()
        label = {"mean": "average", "sum": "sum", "median": "median", "min": "minimum", "max": "maximum", "count": "count"}[aggregate]
        display = f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"
        return {"answer": f"Verified result: the {label} of {column or 'rows'} is **{display}**.", "success": True, "table": table, "verification": "verified"}

    sort_column = plan.get("sort_column") if plan.get("sort_column") in frame.columns else column
    if sort_column:
        frame = frame.assign(__sort=pd.to_numeric(frame[sort_column], errors="coerce")).sort_values("__sort", ascending=not bool(plan.get("descending", True)), na_position="last").drop(columns="__sort")
    chart = None
    if action == "chart" and column:
        values = pd.to_numeric(frame[column], errors="coerce")
        label_column = next((candidate for candidate in frame.columns if candidate != column), None)
        points = [{"label": str(frame.loc[index, label_column]) if label_column else str(index + 1), "value": float(value)} for index, value in values.items() if pd.notna(value)][:25]
        if points:
            chart = {"type": "line" if len(points) > 2 else "bar", "title": f"{column} · verified data", "data": points}
    return {"answer": "Here are the verified rows from the structured data:\n\n" + _markdown(frame, plan.get("limit", 10)), "success": True, "table": table, "verification": "verified", "chart": chart}
