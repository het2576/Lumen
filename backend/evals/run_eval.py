"""Run Lumen's golden-set evals against the real chat pipeline.

Usage (from backend/):
    python -m evals.run_eval --check                         # validate dataset, no network
    python -m evals.run_eval --user-email eval@example.com   # full run with LLM judge
    python -m evals.run_eval --no-judge --category lookup    # quick, cheap subset
    python -m evals.run_eval --context-mode hybrid           # force top-k retrieval
    python -m evals.run_eval --save-baseline                 # make this run the reference

See evals/README.md for what every metric means.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import GENERATION_MODEL
from app.models.schemas import ChatRequest
from app.routers import chat as chat_module
from app.services import gemini_client
from evals import judge as judge_module
from evals.checks import error_record, score_case
from evals.dataset import EVALS_DIR, load_cases, load_registry, validate
from evals.report import aggregate, compare, render_markdown
from evals.sources import ensure_documents, find_user_id
from evals.spies import CONTEXT_MODES, RECORDER, install, set_context_mode

BASELINE_PATH = EVALS_DIR / "baseline.json"
RESULTS_DIR = EVALS_DIR / "results"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=EVALS_DIR / "datasets" / "golden.jsonl")
    parser.add_argument("--user-email", default=os.getenv("EVAL_USER_EMAIL"),
                        help="account that owns the eval documents (use a dedicated eval account)")
    parser.add_argument("--user-id", default=os.getenv("EVAL_USER_ID"), help="skip the email lookup")
    parser.add_argument("--context-mode", choices=sorted(CONTEXT_MODES), default="production",
                        help="production = app behaviour; hybrid = always top-k; whole = always whole documents")
    parser.add_argument("--no-judge", dest="judge", action="store_false", help="skip LLM-as-judge scoring")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--category", help="comma-separated categories")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--reingest", action="store_true", help="re-ingest fixtures (after editing ingestion code)")
    parser.add_argument("--label", default="", help="short name appended to the run id, e.g. rrf-fusion")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--fail-on-regression", action="store_true", help="exit 1 if a quality metric regresses")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between cases (free-tier rate limits)")
    parser.add_argument("--retries", type=int, default=2, help="retries per case on rate-limit errors")
    parser.add_argument("--check", action="store_true", help="validate the dataset and fixtures, then exit")
    return parser.parse_args()


def _select(cases: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.only:
        wanted = {item.strip() for item in args.only.split(",")}
        cases = [case for case in cases if case["id"] in wanted]
    if args.category:
        wanted = {item.strip() for item in args.category.split(",")}
        cases = [case for case in cases if case["category"] in wanted]
    return cases[: args.limit] if args.limit else cases


_TRANSIENT_MARKERS = (
    "429", "quota", "resourceexhausted",  # rate limits
    "server disconnected", "connection reset", "timed out", "deadline", "503", "502", "504", "unavailable",
)


def _is_transient(exc: Exception) -> bool:
    """Rate limits and network blips are infrastructure noise, not answer quality: retry them."""
    if gemini_client.is_transient(exc) or gemini_client.is_transient(exc.__cause__ or exc):
        return True
    message = f"{type(exc).__name__} {exc} {getattr(exc, 'detail', '')}".lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def _ask(question: str, document_ids: list[str], user_id: str, conversation_id: str | None):
    request = ChatRequest(document_id=document_ids[0], document_ids=document_ids,
                          question=question, conversation_id=conversation_id)
    return chat_module.chat(request, user_id=user_id)


def run_case(case: dict, documents: dict[str, dict], user_id: str, key_by_id: dict[str, str], retries: int) -> dict:
    document_ids = [documents[key]["id"] for key in case["docs"]]
    for attempt in range(retries + 1):
        try:
            RECORDER.active = False
            conversation_id = None
            # Prior turns build real conversation state but are not scored.
            for prior in case.get("history", []):
                conversation_id = _ask(prior, document_ids, user_id, conversation_id).conversation_id

            RECORDER.reset()
            RECORDER.active = True
            started = RECORDER.started_at = time.perf_counter()
            response = _ask(case["question"], document_ids, user_id, conversation_id)
            total_ms = (time.perf_counter() - started) * 1000
            RECORDER.active = False
            return score_case(case, response, RECORDER, key_by_id, total_ms)
        except Exception as exc:
            RECORDER.active = False
            if attempt < retries and _is_transient(exc):
                wait = 20 * (attempt + 1)
                print(f"      transient error ({type(exc).__name__}); retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            return error_record(case, exc)
    raise AssertionError("unreachable")


def _git_revision() -> str:
    try:
        revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
        return f"{revision}{'+uncommitted' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    args = _parse_args()
    registry = load_registry()
    cases = load_cases(args.dataset)
    problems = validate(cases, registry)
    if problems:
        print("Dataset problems:\n  " + "\n  ".join(problems))
        return 2
    cases = _select(cases, args)
    if not cases:
        print("No cases selected.")
        return 2
    if args.check:
        counts = Counter(case["category"] for case in cases)
        print(f"OK: {len(cases)} cases · " + " · ".join(f"{name} {count}" for name, count in sorted(counts.items())))
        return 0

    if not (args.user_id or args.user_email):
        print("Pass --user-email (or set EVAL_USER_EMAIL) for the account that will own the eval documents.")
        return 2
    user_id = args.user_id or find_user_id(args.user_email)

    print("Preparing fixtures…", flush=True)
    keys = sorted({key for case in cases for key in case["docs"]})
    documents = ensure_documents(keys, registry, user_id, reingest=args.reingest)
    key_by_id = {document["id"]: key for key, document in documents.items()}

    install()
    set_context_mode(args.context_mode)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else "")
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    judge_model = judge_module.JUDGE_MODEL if args.judge else None
    print(f"Running {len(cases)} cases · mode={args.context_mode} · judge={judge_model or 'off'}\n", flush=True)

    records = []
    with open(run_dir / "cases.jsonl", "w") as out:
        for index, case in enumerate(cases, start=1):
            record = run_case(case, documents, user_id, key_by_id, args.retries)
            if args.judge and judge_module.should_judge(record):
                judge_module.apply_verdict(record, judge_module.judge(case, record, RECORDER.context))
            records.append(record)
            out.write(json.dumps(record) + "\n")
            out.flush()

            failed = [name for name, ok in record["checks"].items() if not ok]
            status = "ERROR" if record.get("error") else "PASS " if record["passed"] else "FAIL "
            detail = record.get("error") or ", ".join(failed)
            seconds = record["latency_ms"].get("total", 0) / 1000
            print(f"[{index:>2}/{len(cases)}] {status} {record['id']:<20} {seconds:5.1f}s  {detail}", flush=True)
            if args.delay and index < len(cases):
                time.sleep(args.delay)

    summary = aggregate(records)
    meta = {
        "run_id": run_id, "dataset": str(args.dataset.relative_to(EVALS_DIR.parent)) if args.dataset.is_relative_to(EVALS_DIR.parent) else str(args.dataset),
        "context_mode": args.context_mode, "generation_model": GENERATION_MODEL,
        "judge_model": judge_model, "git": _git_revision(),
    }
    summary["meta"] = meta
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
    comparison = compare(summary, baseline)

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    report = render_markdown(summary, records, comparison, meta)
    (run_dir / "report.md").write_text(report)

    print("\n" + report.split("\n## By category")[0])
    print(f"\nFull report: {run_dir / 'report.md'}")
    if baseline and baseline.get("meta", {}).get("context_mode") != args.context_mode:
        print("Note: baseline was run in a different context mode; deltas are not like-for-like.")
    if args.save_baseline:
        shutil.copyfile(run_dir / "summary.json", BASELINE_PATH)
        print(f"Saved as baseline: {BASELINE_PATH}")

    regressions = [row for row in comparison if row["regressed"] and row["gating"]]
    if regressions:
        print("\nRegressions vs baseline: " + ", ".join(row["label"] for row in regressions))
        if args.fail_on_regression:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
