"""Aggregate case records into headline metrics, compare against a baseline, render Markdown."""
import os
from collections import defaultdict

from evals.judge import CORRECTNESS, FAITHFULNESS, RELEVANCE

# (key, label, higher_is_better, is_quality). Only quality metrics gate --fail-on-regression;
# latency and cost are noisy run to run, so they are reported as warnings.
METRICS = [
    ("pass_rate", "Case pass rate", True, True),
    ("hit@1", "Retrieval hit@1", True, True),
    ("hit@3", "Retrieval hit@3", True, True),
    ("mrr", "Retrieval MRR", True, True),
    ("context_recall", "Context recall", True, True),
    ("doc_coverage", "Multi-doc coverage", True, True),
    ("route_accuracy", "Route accuracy", True, True),
    ("false_refusal_rate", "False refusal rate", False, True),
    ("hallucination_rate", "Hallucination rate (answered the unanswerable)", False, True),
    ("must_include_rate", "Must-include facts present", True, True),
    ("must_not_include_rate", "Forbidden content avoided", True, True),
    ("number_accuracy", "Numeric exactness", True, True),
    ("uncited_rate", "Uncited answer rate", False, True),
    ("judge_faithfulness", "Judge: faithfulness", True, True),
    ("judge_relevance", "Judge: relevance", True, True),
    ("judge_correctness", "Judge: correctness", True, True),
    ("exact_refusal_phrase_rate", "Declines using the exact refusal phrase", True, False),
    ("first_token_p50_ms", "Time to first token p50 (ms)", False, False),
    ("latency_p50_ms", "Latency p50 (ms)", False, False),
    ("latency_p95_ms", "Latency p95 (ms)", False, False),
    ("retrieve_p50_ms", "Retrieve p50 (ms)", False, False),
    ("generate_p50_ms", "Generate p50 (ms)", False, False),
    ("db_calls_mean", "DB round trips / question", False, False),
    ("input_tokens_mean", "Input tokens / question", False, False),
    ("output_tokens_mean", "Output tokens / question", False, False),
    ("cached_tokens_mean", "Cached input tokens / question", True, False),
    ("cost_per_question_usd", "Est. cost / question (USD)", False, False),
]
QUALITY_TOLERANCE = 0.05  # absolute drop in a rate before it counts as a regression
PERF_TOLERANCE = 0.25  # relative increase in latency/tokens/cost before it is flagged


def _mean(values: list[float]) -> float | None:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def _rate(flags: list[bool]) -> float | None:
    return _mean([1.0 if flag else 0.0 for flag in flags])


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; fine for eval-sized samples."""
    values = sorted(values)
    if not values:
        return None
    rank = max(1, round(pct / 100 * len(values) + 0.4999))
    return values[min(rank, len(values)) - 1]


def _price(name: str) -> float | None:
    raw = os.getenv(name)
    return float(raw) if raw else None


def aggregate(records: list[dict]) -> dict:
    ok = [record for record in records if not record.get("error")]
    answerable = [record for record in ok if record["answerable"]]
    unanswerable = [record for record in ok if not record["answerable"]]
    retrieval = [record["retrieval"] for record in ok if record.get("retrieval")]
    judged = [record["judge"] for record in ok if record.get("judge") and not record["judge"].get("error")]

    def check_rate(name: str) -> float | None:
        return _rate([record["checks"][name] for record in ok if name in record["checks"]])

    def judge_score(field: str, scale: dict) -> float | None:
        return _mean([scale[verdict[field]] for verdict in judged if verdict.get(field) in scale])

    def latency(stage: str, pct: float) -> float | None:
        return percentile([record["latency_ms"][stage] for record in ok if stage in record["latency_ms"]], pct)

    summary = {
        "cases": len(records),
        "errors": len(records) - len(ok),
        "pass_rate": _rate([record["passed"] for record in records]),
        **{key: _mean([float(item[key]) for item in retrieval if key in item])
           for key in ("hit@1", "hit@3", "hit@5", "mrr", "context_recall", "doc_coverage")},
        "route_accuracy": check_rate("route"),
        "false_refusal_rate": _rate([not record["checks"].get("no_false_refusal", True) for record in answerable]),
        "hallucination_rate": _rate([not record["checks"].get("declined", False) for record in unanswerable]),
        "exact_refusal_phrase_rate": _rate([record["strict_refusal"] for record in unanswerable if record["declined"]]),
        "must_include_rate": check_rate("must_include"),
        "must_not_include_rate": check_rate("must_not_include"),
        "number_accuracy": check_rate("number"),
        "uncited_rate": _rate([record["uncited"] for record in answerable if record["observed_route"] == "text" and not record["declined"]]),
        "judge_faithfulness": judge_score("faithfulness", FAITHFULNESS),
        "judge_relevance": judge_score("relevance", RELEVANCE),
        "judge_correctness": judge_score("correctness", CORRECTNESS),
        "judged_cases": len(judged),
        "first_token_p50_ms": latency("first_token", 50),
        "latency_p50_ms": latency("total", 50),
        "latency_p95_ms": latency("total", 95),
        "retrieve_p50_ms": latency("retrieve", 50),
        "generate_p50_ms": latency("generate", 50),
        "db_calls_mean": _mean([record["calls"].get("db", 0) for record in ok]),
        "input_tokens_mean": _mean([record["tokens"]["input"] for record in ok]),
        "output_tokens_mean": _mean([record["tokens"]["output"] for record in ok]),
        "cached_tokens_mean": _mean([record["tokens"]["cached"] for record in ok]),
        "context_tokens_mean": _mean([record["context_tokens"] for record in ok]),
    }

    price_in, price_out = _price("EVAL_PRICE_INPUT_PER_M"), _price("EVAL_PRICE_OUTPUT_PER_M")
    price_cached = _price("EVAL_PRICE_CACHED_PER_M") or price_in
    if price_in is not None and price_out is not None and ok:
        costs = [
            ((record["tokens"]["input"] - record["tokens"]["cached"]) * price_in
             + record["tokens"]["cached"] * price_cached
             + record["tokens"]["output"] * price_out) / 1_000_000
            for record in ok
        ]
        summary["cost_per_question_usd"] = _mean(costs)

    by_category: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_category[record["category"]].append(record)
    summary["by_category"] = {
        category: {
            "n": len(items),
            "pass_rate": _rate([item["passed"] for item in items]),
            "hit@3": _mean([float(item["retrieval"]["hit@3"]) for item in items if item.get("retrieval")]),
            "latency_p50_ms": percentile([item["latency_ms"]["total"] for item in items if item.get("latency_ms")], 50),
        }
        for category, items in sorted(by_category.items())
    }
    return summary


def compare(summary: dict, baseline: dict | None) -> list[dict]:
    if not baseline:
        return []
    rows = []
    for key, label, higher_is_better, is_quality in METRICS:
        now, before = summary.get(key), baseline.get(key)
        if now is None or before is None:
            continue
        delta = now - before
        worse = -delta if higher_is_better else delta
        if is_quality:
            regressed = worse > QUALITY_TOLERANCE
        else:
            regressed = before > 0 and worse / before > PERF_TOLERANCE
        rows.append({"key": key, "label": label, "before": before, "now": now, "delta": delta,
                     "regressed": regressed, "gating": is_quality})
    return rows


def _is_rate(key: str) -> bool:
    return not (key == "mrr" or key.endswith(("_ms", "_mean", "_usd")))


def _fmt(key: str, value) -> str:
    if value is None:
        return "–"
    if key == "cost_per_question_usd":
        return f"${value:.5f}"
    if key in ("mrr", "db_calls_mean"):
        return f"{value:.2f}"
    if not _is_rate(key):
        return f"{value:,.0f}"
    return f"{100 * value:.0f}%"


def _fmt_delta(key: str, delta: float) -> str:
    if _is_rate(key):
        return f"{delta * 100:+.0f} pts"
    if key == "cost_per_question_usd":
        return f"{delta:+.5f}"
    return f"{delta:+,.2f}" if key in ("mrr", "db_calls_mean") else f"{delta:+,.0f}"


def render_markdown(summary: dict, records: list[dict], comparison: list[dict], meta: dict) -> str:
    lines = [
        f"# Lumen eval report: {meta['run_id']}",
        "",
        f"- Dataset: `{meta['dataset']}` · {summary['cases']} cases · {summary['errors']} errors",
        f"- Context mode: `{meta['context_mode']}` · generation model `{meta['generation_model']}` · "
        f"judge `{meta['judge_model'] or 'off'}` ({summary['judged_cases']} judged)",
        f"- Git: `{meta['git']}`",
        "",
        "## Headline metrics",
        "",
        "| Metric | Value | Baseline | Δ |",
        "|---|---|---|---|",
    ]
    by_key = {row["key"]: row for row in comparison}
    for key, label, _, _ in METRICS:
        if summary.get(key) is None:
            continue
        row = by_key.get(key)
        flag = " ⚠️" if row and row["regressed"] else ""
        before = _fmt(key, row["before"]) if row else "–"
        delta = _fmt_delta(key, row["delta"]) if row else "–"
        lines.append(f"| {label} | {_fmt(key, summary[key])} | {before} | {delta}{flag} |")

    lines += ["", "## By category", "", "| Category | Cases | Pass | hit@3 | p50 latency |", "|---|---|---|---|---|"]
    for category, stats in summary["by_category"].items():
        lines.append(f"| {category} | {stats['n']} | {_fmt('pass_rate', stats['pass_rate'])} | "
                     f"{_fmt('hit@3', stats['hit@3'])} | {_fmt('latency_p50_ms', stats['latency_p50_ms'])} |")

    failures = [record for record in records if not record["passed"]]
    lines += ["", f"## Failures ({len(failures)})", "",
              "Read these by hand: patterns here tell you what to fix next.", ""]
    for record in failures:
        failed = [name for name, ok in record["checks"].items() if not ok]
        lines.append(f"### `{record['id']}` · {record['category']}")
        lines.append("")
        if record.get("error"):
            lines.append(f"- **Error:** {record['error']}")
        else:
            lines.append(f"- **Failed checks:** {', '.join(failed) or '–'}")
            lines.append(f"- **Route:** expected `{record['expected_route']}`, got `{record['observed_route']}`"
                         f" (keyword router fired: {record['router_triggered']})")
            if record.get("retrieval"):
                stats = record["retrieval"]
                lines.append(f"- **Retrieval:** hit@3={stats['hit@3']} · MRR={stats['mrr']:.2f} · "
                             f"context recall={stats['context_recall']:.0%}")
            claims = (record.get("judge") or {}).get("unsupported_claims") or []
            if claims:
                lines.append(f"- **Judge, unsupported claims:** {'; '.join(claims)}")
        lines.append(f"- **Q:** {record['question']}")
        answer = (record.get("answer") or "").replace("\n", " ")
        lines.append(f"- **A:** {answer[:400]}{'…' if len(answer) > 400 else ''}")
        lines.append("")
    return "\n".join(lines)
