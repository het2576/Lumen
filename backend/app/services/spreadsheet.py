"""
Phase 1 – Spreadsheet query service.

Responsibilities:
  1. is_tabular_question()     — heuristic to route queries to pandas vs. vector search
  2. execute_pandas_query()    — translate NL question → pandas operation → execute safely
  3. format_table_response()   — convert a DataFrame to a markdown table string
"""
import io
import json
import logging
import subprocess
import sys
import textwrap
from typing import Optional

from app.config import GENERATION_MODEL
from app.services.gemini_client import get_genai

logger = logging.getLogger(__name__)

# ─── Keywords that signal a tabular/numeric question ──────────────────────────
_TABULAR_KEYWORDS = {
    "average", "avg", "mean", "sum", "total", "count", "max", "maximum",
    "min", "minimum", "median", "std", "variance", "percent", "percentage",
    "rows where", "filter", "where", "greater than", "less than", "equal to",
    "top ", "bottom ", "sort", "rank", "highest", "lowest", "column",
    "sheet", "table", "spreadsheet", "csv", "xlsx",
}


def is_tabular_question(question: str) -> bool:
    """
    Returns True if the question looks like it targets structured/tabular data.
    Uses a fast keyword scan first; only calls the LLM if uncertain.
    """
    q_lower = question.lower()
    if any(kw in q_lower for kw in _TABULAR_KEYWORDS):
        return True
    return False


def _build_translate_prompt(question: str, column_headers: list[str], sheet_name: Optional[str]) -> str:
    context = f"Sheet: {sheet_name}\n" if sheet_name else ""
    cols = ", ".join(f'"{c}"' for c in column_headers)
    return textwrap.dedent(f"""
        You are a pandas code generator. Given a question about a DataFrame called `df`,
        produce a single Python expression (or a short block ending with a print statement)
        that answers the question. Use only the pandas library (already imported as pd).
        Do NOT import anything else. Do NOT use eval(), exec(), open(), or any I/O.

        {context}DataFrame columns: {cols}

        Rules:
        - If the answer is a scalar (number, string), assign it to a variable `result`
          and end with: print(result)
        - If the answer is a DataFrame or Series (table), assign it to `result`
          and end with: print(result.to_markdown(index=False))
        - Use .fillna('') for any operation that might break on NaN values.
        - If the question cannot be answered with the available columns, print: CANNOT_ANSWER

        Question: {question}

        Respond with ONLY the Python code, no explanation, no markdown fences.
    """).strip()


def execute_pandas_query(
    question: str,
    tables: list[dict],
) -> dict:
    """
    Given a natural-language question and a list of structured table dicts
    (each having column_headers and row_data), translate the question to a
    pandas operation via the LLM and execute it in an isolated subprocess.

    Returns:
        {
            "answer": str,           # human-readable answer
            "table_result": str | None,  # markdown table if result is tabular
            "data_source": "computed",
            "success": bool,
        }
    """
    if not tables:
        return {
            "answer": "No structured table data found for this document.",
            "table_result": None,
            "data_source": "computed",
            "success": False,
        }

    # Use the first table for single-doc queries; for multi-doc the caller
    # has already filtered to the relevant table.
    table = tables[0]
    column_headers = table.get("column_headers") or []
    if isinstance(column_headers, str):
        column_headers = json.loads(column_headers)
    row_data = table.get("row_data") or []
    if isinstance(row_data, str):
        row_data = json.loads(row_data)
    sheet_name = table.get("sheet_name")

    # Step 1: LLM translates the question to pandas code
    genai = get_genai()
    model = genai.GenerativeModel(GENERATION_MODEL)
    prompt = _build_translate_prompt(question, column_headers, sheet_name)
    try:
        response = model.generate_content(prompt)
        pandas_code = (response.text or "").strip()
        # Strip markdown fences if model wrapped the code despite instructions
        if pandas_code.startswith("```"):
            lines = pandas_code.split("\n")
            pandas_code = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    except Exception as exc:
        logger.exception("LLM pandas translation failed")
        return {
            "answer": f"Could not translate question to a computation: {exc}",
            "table_result": None,
            "data_source": "computed",
            "success": False,
        }

    if "CANNOT_ANSWER" in pandas_code:
        return {
            "answer": "The available table columns don't contain the data needed to answer this question.",
            "table_result": None,
            "data_source": "computed",
            "success": False,
        }

    # Step 2: Execute the generated pandas code in a restricted subprocess
    runner_script = textwrap.dedent(f"""
import pandas as pd
import json
import sys

_headers = {json.dumps(column_headers)}
_rows = {json.dumps(row_data)}

df = pd.DataFrame(_rows, columns=_headers)
# Coerce numeric columns
for _col in df.columns:
    try:
        df[_col] = pd.to_numeric(df[_col], errors='ignore')
    except Exception:
        pass

{pandas_code}
    """).strip()

    try:
        proc = subprocess.run(
            [sys.executable, "-c", runner_script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0 or not stdout:
            logger.warning("Pandas subprocess error: %s", stderr)
            return {
                "answer": "The computation produced an error. Please rephrase your question.",
                "table_result": None,
                "data_source": "computed",
                "success": False,
            }

        # Detect if result is a markdown table (has | separators)
        is_table = "|" in stdout and "\n" in stdout
        return {
            "answer": stdout if not is_table else f"Here is the result:\n\n{stdout}",
            "table_result": stdout if is_table else None,
            "data_source": "computed",
            "success": True,
        }

    except subprocess.TimeoutExpired:
        return {
            "answer": "The computation timed out. Try a simpler question.",
            "table_result": None,
            "data_source": "computed",
            "success": False,
        }
    except Exception as exc:
        logger.exception("Pandas execution failed")
        return {
            "answer": f"Computation failed: {exc}",
            "table_result": None,
            "data_source": "computed",
            "success": False,
        }


def format_table_preview(tables: list[dict], max_rows: int = 5) -> str:
    """
    Returns a short markdown preview of the first table's columns and sample rows,
    used to enrich the LLM context when it needs to understand a spreadsheet's structure.
    """
    if not tables:
        return ""
    parts = []
    for t in tables[:3]:  # preview up to 3 tables/sheets
        headers = t.get("column_headers") or []
        if isinstance(headers, str):
            headers = json.loads(headers)
        rows = t.get("row_data") or []
        if isinstance(rows, str):
            rows = json.loads(rows)
        sheet = t.get("sheet_name", "")
        label = f"Sheet: {sheet}\n" if sheet else ""
        header_line = " | ".join(str(h) for h in headers)
        sep = " | ".join("---" for _ in headers)
        row_lines = [" | ".join(str(r.get(h, "")) for h in headers) for r in rows[:max_rows]]
        parts.append(f"{label}| {header_line} |\n| {sep} |\n" + "\n".join(f"| {r} |" for r in row_lines))
    return "\n\n".join(parts)
