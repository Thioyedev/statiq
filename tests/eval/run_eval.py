"""
StatIQ LLM-as-judge evaluation.

Usage:
    python tests/eval/run_eval.py                 # all questions
    python tests/eval/run_eval.py --question 2    # single question (1-indexed)
    pytest tests/eval/run_eval.py -v -m eval      # via pytest (skipped by default)

Each golden question is run against a synthetic CSV dataset loaded locally
(no BigQuery / Redis required).
"""
from __future__ import annotations

import argparse
import asyncio
import textwrap
import numpy as np
import pandas as pd
import pytest

from backend.agents.base import DataLoader
from backend.agents.router import RouterAgent
from backend.eval.judge import AnalysisTrace, LLMJudge, ToolCall
from backend.models.schemas import DataSourceType, StreamEvent
from backend.observability.langfuse_client import log_eval_scores


# ── Synthetic dataset fixture ─────────────────────────────────────────────────

def _make_loader() -> DataLoader:
    """500-row taxi-trip dataset with known statistical properties."""
    rng = np.random.default_rng(42)
    n = 500

    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "trip_date":    dates.strftime("%Y-%m-%d"),
        "duration_min": rng.gamma(shape=2, scale=10, size=n).round(1),
        "fare_usd":     (rng.normal(15, 5, n).clip(3)).round(2),
        "distance_km":  rng.exponential(scale=5, size=n).round(2),
        "tip_usd":      (rng.exponential(scale=2, size=n)).round(2),
        "payment_type": rng.choice(["cash", "card", "app"], n),
        "hour":         rng.integers(0, 24, n),
    })

    loader = DataLoader()
    loader.duck.register("df", df)
    loader._loaded_table = "df"
    loader._source_type = DataSourceType.CSV_UPLOAD
    loader._dataset_ref = "synthetic_trips"
    return loader


# ── Golden set ────────────────────────────────────────────────────────────────

GOLDEN_SET = [
    {
        "id": "Q1",
        "question": "Quelle est la durée moyenne d'un trajet et quelle est sa distribution ?",
        "expected_tools": {"run_sql_query", "run_statistical_analysis"},
        "must_mention_keywords": ["durée", "moyenne", "min", "max"],
    },
    {
        "id": "Q2",
        "question": "Y a-t-il une corrélation entre la distance et le pourboire ?",
        "expected_tools": {"run_statistical_analysis"},
        "must_mention_keywords": ["corrélation", "distance", "pourboire"],
    },
    {
        "id": "Q3",
        "question": "Compare le tarif moyen entre les paiements cash, carte et app.",
        "expected_tools": {"run_sql_query"},
        "must_mention_keywords": ["cash", "carte", "app", "tarif"],
    },
    {
        "id": "Q4",
        "question": "Montre l'évolution du nombre de trajets par mois.",
        "expected_tools": {"run_sql_query", "create_visualization"},
        "must_mention_keywords": ["mois", "trajets"],
    },
    {
        "id": "Q5",
        "question": "Prévois le tarif total quotidien pour les 30 prochains jours.",
        "expected_tools": {"run_sql_query", "run_forecast"},
        "must_mention_keywords": ["prévision", "jours"],
    },
]


# ── Trace collector ───────────────────────────────────────────────────────────

async def _collect_trace(question: str, loader: DataLoader) -> AnalysisTrace:
    """Run the RouterAgent and capture tool calls + narrative from SSE events."""
    agent = RouterAgent(loader)

    tool_calls: list[ToolCall] = []
    narrative_parts: list[str] = []
    pending: dict = {}

    async for event in agent.analyze_stream(question, session_id="eval"):
        match event.event:
            case "agent_start":
                pending = {
                    "tool": event.data.get("agent", ""),
                    "input_summary": event.data.get("input", ""),
                    "result_summary": "",
                }
            case "agent_result":
                if pending:
                    pending["result_summary"] = event.data.get("summary", "")
                    tool_calls.append(ToolCall(**pending))
                    pending = {}
            case "text_chunk":
                narrative_parts.append(event.data)

    return AnalysisTrace(
        question=question,
        tool_calls=tool_calls,
        narrative="".join(narrative_parts),
    )


# ── Runner ────────────────────────────────────────────────────────────────────

async def _run_one(entry: dict, loader: DataLoader, judge: LLMJudge) -> dict:
    qid = entry["id"]
    question = entry["question"]
    print(f"\n{'─'*60}")
    print(f"[{qid}] {question}")

    trace = await _collect_trace(question, loader)
    used_tools = {tc.tool for tc in trace.tool_calls}

    score = judge.evaluate(trace)

    # Push scores to Langfuse (no-op if not configured)
    log_eval_scores(
        trace_id=f"eval-{qid}",
        scores={
            "grounding": score.grounding,
            "precision": score.precision,
            "relevance": score.relevance,
            "hallucination": int(score.hallucination),
        },
    )

    missing_tools = entry["expected_tools"] - used_tools
    unexpected_tools = used_tools - entry["expected_tools"]
    keyword_hits = [
        kw for kw in entry["must_mention_keywords"]
        if kw.lower() in trace.narrative.lower()
    ]
    missing_kw = [
        kw for kw in entry["must_mention_keywords"]
        if kw.lower() not in trace.narrative.lower()
    ]

    print(f"  Tools used    : {sorted(used_tools)}")
    if missing_tools:
        print(f"  ⚠ Missing     : {sorted(missing_tools)}")
    if unexpected_tools:
        print(f"  ℹ Extra       : {sorted(unexpected_tools)}")
    print(f"  Keywords      : {keyword_hits} ✓  |  {missing_kw} ✗")
    print(f"  Grounding     : {score.grounding}/3")
    print(f"  Precision     : {score.precision}/3")
    print(f"  Relevance     : {score.relevance}/3")
    print(f"  Hallucination : {'⚠ YES' if score.hallucination else 'no'}")
    print(f"  Grade         : {score.grade}  (total {score.total}/9)")
    print(f"  Explanation   : {textwrap.fill(score.explanation, 70, subsequent_indent='                  ')}")

    return {
        "id": qid,
        "score": score,
        "used_tools": used_tools,
        "missing_tools": missing_tools,
        "missing_keywords": missing_kw,
    }


async def run_eval(question_idx: int | None = None) -> list[dict]:
    loader = _make_loader()
    judge = LLMJudge()

    entries = GOLDEN_SET
    if question_idx is not None:
        entries = [GOLDEN_SET[question_idx - 1]]

    results = []
    for entry in entries:
        result = await _run_one(entry, loader, judge)
        results.append(result)

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"{'ID':<5} {'Grade':<18} {'Total':>5}  {'Hallucination'}")
    print(f"{'─'*60}")
    totals = []
    for r in results:
        s = r["score"]
        totals.append(s.total)
        print(f"{r['id']:<5} {s.grade:<18} {s.total:>5}/9  {'⚠ YES' if s.hallucination else 'no'}")
    avg = sum(totals) / len(totals) if totals else 0
    print(f"{'─'*60}")
    print(f"{'AVG':<5} {'':<18} {avg:>5.1f}/9")

    return results


# ── pytest integration (skipped unless -m eval) ───────────────────────────────

@pytest.mark.eval
@pytest.mark.asyncio
async def test_golden_set_scores():
    """All golden questions must score ≥ 4/9 with no hallucination."""
    results = await run_eval()
    for r in results:
        s = r["score"]
        assert not s.hallucination, f"{r['id']}: hallucination detected — {s.explanation}"
        assert s.total >= 4, f"{r['id']}: score {s.total}/9 is below threshold (4) — {s.explanation}"


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StatIQ LLM-as-judge evaluation")
    parser.add_argument("--question", type=int, default=None, help="Run a single question (1-indexed)")
    args = parser.parse_args()
    asyncio.run(run_eval(args.question))
