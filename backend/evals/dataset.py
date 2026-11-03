"""Load and validate golden datasets and the fixture registry."""
import json
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent

ROUTES = {"text", "structured", "any"}
REQUIRED_FIELDS = {"id", "category", "docs", "question", "answerable", "expected_route"}
OPTIONAL_FIELDS = {
    "history", "expected_pages", "must_include", "must_not_include",
    "expected_number", "expect_chart", "reference_answer", "notes",
}


def load_registry(path: Path = EVALS_DIR / "documents.json") -> dict[str, Path]:
    """Map each fixture key used by cases to its file on disk."""
    raw = json.loads(path.read_text())
    return {key: (path.parent / relative).resolve() for key, relative in raw.items()}


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number}: invalid JSON ({exc.msg})") from exc
        case["_line"] = line_number
        cases.append(case)
    return cases


def _valid_phrase_list(items: object) -> bool:
    # Each entry is a phrase, or a list of alternative phrases where any one may match.
    if not isinstance(items, list):
        return False
    return all(
        isinstance(item, str) or (isinstance(item, list) and item and all(isinstance(alt, str) for alt in item))
        for item in items
    )


def validate(cases: list[dict], registry: dict[str, Path]) -> list[str]:
    """Return every problem found, so a broken dataset fails before spending any API quota."""
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        where = f"line {case.get('_line')} ({case.get('id', '?')})"
        missing = REQUIRED_FIELDS - case.keys()
        if missing:
            problems.append(f"{where}: missing fields {sorted(missing)}")
            continue
        unknown = case.keys() - REQUIRED_FIELDS - OPTIONAL_FIELDS - {"_line"}
        if unknown:
            problems.append(f"{where}: unknown fields {sorted(unknown)}")
        if case["id"] in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(case["id"])

        docs = case["docs"]
        if not isinstance(docs, list) or not docs:
            problems.append(f"{where}: docs must be a non-empty list")
            continue
        for doc in docs:
            if doc not in registry:
                problems.append(f"{where}: unknown doc '{doc}' (add it to documents.json)")
            elif not registry[doc].exists():
                problems.append(f"{where}: fixture file missing for '{doc}': {registry[doc]}")

        if case["expected_route"] not in ROUTES:
            problems.append(f"{where}: expected_route must be one of {sorted(ROUTES)}")
        if not isinstance(case["answerable"], bool):
            problems.append(f"{where}: answerable must be true or false")
        if not case["answerable"] and (case.get("must_include") or case.get("expected_number") is not None):
            problems.append(f"{where}: an unanswerable case can't have must_include or expected_number")

        pages = case.get("expected_pages")
        if pages is not None:
            if not isinstance(pages, dict) or not all(
                doc in docs and isinstance(numbers, list) and all(isinstance(n, int) for n in numbers)
                for doc, numbers in pages.items()
            ):
                problems.append(f"{where}: expected_pages must map docs in this case to lists of page numbers")

        for field in ("must_include", "must_not_include"):
            if field in case and not _valid_phrase_list(case[field]):
                problems.append(f"{where}: {field} must be a list of phrases or lists of alternative phrases")
        if "history" in case and not (isinstance(case["history"], list) and all(isinstance(q, str) for q in case["history"])):
            problems.append(f"{where}: history must be a list of prior user questions")
    return problems
