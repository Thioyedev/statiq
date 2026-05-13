"""
LLM-as-judge for StatIQ analysis quality.

Takes a full analysis trace (question, tool calls + results, narrative)
and asks Claude to score it on 4 criteria using structured tool_use output.
"""
from __future__ import annotations

import anthropic
from pydantic import BaseModel

from backend.config import get_settings

settings = get_settings()


# ── Score schema ──────────────────────────────────────────────────────────────

class EvalScore(BaseModel):
    grounding: int        # 0-3: narrative cites real numbers from tool results
    precision: int        # 0-3: statistical interpretations are correct
    relevance: int        # 0-3: answer addresses the question asked
    hallucination: bool   # true if narrative contains data NOT in any tool result
    explanation: str      # brief justification (1-3 sentences)

    @property
    def total(self) -> int:
        return self.grounding + self.precision + self.relevance

    @property
    def grade(self) -> str:
        if self.hallucination:
            return "FAIL (hallucination)"
        t = self.total
        if t >= 8:
            return "A"
        if t >= 6:
            return "B"
        if t >= 4:
            return "C"
        return "D"


class ToolCall(BaseModel):
    tool: str
    input_summary: str
    result_summary: str


class AnalysisTrace(BaseModel):
    question: str
    tool_calls: list[ToolCall]
    narrative: str


# ── Judge tool schema (forces structured output) ──────────────────────────────

_JUDGE_TOOL = {
    "name": "submit_evaluation",
    "description": "Submit the evaluation scores for this analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "grounding": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
                "description": (
                    "Does the narrative cite specific numbers from tool results? "
                    "3=all key figures cited, 2=most cited, 1=vague references, 0=none."
                ),
            },
            "precision": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
                "description": (
                    "Are statistical interpretations correct? "
                    "3=all correct, 2=minor errors, 1=significant errors, 0=wrong."
                ),
            },
            "relevance": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
                "description": (
                    "Does the response answer the user's question? "
                    "3=fully, 2=partially, 1=loosely related, 0=off-topic."
                ),
            },
            "hallucination": {
                "type": "boolean",
                "description": (
                    "true if the narrative contains data or numbers NOT present "
                    "in any tool result. false otherwise."
                ),
            },
            "explanation": {
                "type": "string",
                "description": "1-3 sentences justifying your scores.",
            },
        },
        "required": ["grounding", "precision", "relevance", "hallucination", "explanation"],
    },
}

_JUDGE_SYSTEM = """Tu es un évaluateur expert en data science et en IA.
Tu dois évaluer la qualité d'une réponse produite par un système d'analyse de données multi-agents.

On te donne :
- La question de l'utilisateur
- La liste des outils appelés avec leurs entrées et résultats
- La narrative finale produite par le système

Tu dois appeler submit_evaluation avec des scores honnêtes et une justification concise.
Sois strict : un score de 3 signifie excellent, pas "correct"."""


# ── Judge class ───────────────────────────────────────────────────────────────

class LLMJudge:
    def __init__(self, model: str | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.anthropic_model

    def evaluate(self, trace: AnalysisTrace) -> EvalScore:
        """Synchronous evaluation — suitable for test scripts."""
        user_content = _build_prompt(trace)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=_JUDGE_SYSTEM,
            tools=[_JUDGE_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_content}],
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_evaluation":
                return EvalScore(**block.input)

        raise RuntimeError("Judge did not call submit_evaluation — check prompt or model response.")


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(trace: AnalysisTrace) -> str:
    lines = [
        f"## Question de l'utilisateur\n{trace.question}\n",
        "## Outils appelés et résultats",
    ]
    for i, tc in enumerate(trace.tool_calls, 1):
        lines.append(f"\n### Appel {i} — `{tc.tool}`")
        lines.append(f"**Entrée :** {tc.input_summary}")
        lines.append(f"**Résultat :** {tc.result_summary}")

    lines.append(f"\n## Narrative finale\n{trace.narrative or '*(aucune narrative produite)*'}")
    return "\n".join(lines)
