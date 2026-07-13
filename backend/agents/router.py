"""
RouterAgent — LangGraph-based multi-agent orchestration.

Graph structure:
    START → call_model ──(tool_use?)──► run_tools ──► call_model
                        └──(end_turn)──► END

Nodes:
  call_model  — invokes Claude (ChatAnthropic) with bound tools; streams text tokens
  run_tools   — dispatches tool calls to specialist agents (SQL/Stat/Viz/Forecast)

The MemorySaver checkpointer persists full graph state per thread_id (session),
enabling multi-turn conversations within a process.
Cross-process persistence is handled separately via Redis (session.py).
"""
from __future__ import annotations

import json
import operator
import time
from collections.abc import AsyncGenerator
from typing import Annotated, Any, Literal, TypedDict

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from backend.agents.base import DataLoader
from backend.agents.forecast_agent import ForecastAgent
from backend.agents.sql_agent import SQLAgent
from backend.agents.stat_agent import StatAgent
from backend.agents.viz_agent import VizAgent
from backend.config import get_settings
from backend.memory.session import session_memory
from backend.observability.langfuse_client import create_callback
from backend.models.schemas import (
    AgentType,
    ForecastResult,
    SQLResult,
    StatResult,
    StreamEvent,
    VizResult,
)
from backend.tools.definitions import LC_TOOLS, ROUTER_SYSTEM_PROMPT

log = structlog.get_logger()
settings = get_settings()


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "credit balance" in msg or "billing" in msg.lower():
        return "Crédit API Anthropic épuisé — rechargez votre compte sur console.anthropic.com/settings/billing"
    if "401" in msg or "authentication" in msg.lower():
        return "Clé API Anthropic invalide — vérifiez ANTHROPIC_API_KEY dans .env"
    if "rate_limit" in msg.lower() or "429" in msg:
        return "Limite de débit Anthropic atteinte — réessayez dans quelques secondes"
    if "recursion" in msg.lower():
        return "L'agent a dépassé le nombre maximum d'itérations — reformulez la question"
    return f"Erreur inattendue : {msg[:200]}"


# ── Graph state ───────────────────────────────────────────────────────────────

class StatIQState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    dataset_context: str
    session_id: str
    # Accumulated across all run_tools executions in one turn
    sse_events: Annotated[list[dict], operator.add]
    steps: Annotated[list[dict], operator.add]


# ── Agent ─────────────────────────────────────────────────────────────────────

class RouterAgent:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader
        self.sql = SQLAgent(loader)
        self.stat = StatAgent(loader)
        self.viz = VizAgent(loader)
        self.forecast = ForecastAgent(loader)

        self._model = ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            max_tokens=4096,
        ).bind_tools(LC_TOOLS)

        self._graph = self._build_graph()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        g = StateGraph(StatIQState)
        g.add_node("call_model", self._call_model_node)
        g.add_node("run_tools", self._run_tools_node)
        g.add_edge(START, "call_model")
        g.add_conditional_edges(
            "call_model",
            self._should_continue,
            {"run_tools": "run_tools", END: END},
        )
        g.add_edge("run_tools", "call_model")
        return g.compile(checkpointer=MemorySaver())

    def _should_continue(self, state: StatIQState) -> Literal["run_tools", "__end__"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "run_tools"
        return END

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def _call_model_node(self, state: StatIQState) -> dict:
        system = ROUTER_SYSTEM_PROMPT.format(dataset_context=state["dataset_context"])
        messages = [SystemMessage(content=system)] + state["messages"]
        log.info("router.call_model", n_messages=len(messages))
        response = self._model.invoke(messages)
        return {"messages": [response]}

    def _run_tools_node(self, state: StatIQState) -> dict:
        last = state["messages"][-1]
        tool_messages: list[ToolMessage] = []
        sse_events: list[dict] = []
        steps: list[dict] = []

        for tool_call in last.tool_calls:
            tool_name: str = tool_call["name"]
            tool_input: dict = tool_call["args"]
            t_tool = time.perf_counter()

            sse_events.append({
                "event": "agent_start",
                "data": {"agent": tool_name, "input": self._summarize_input(tool_input)},
            })

            try:
                result_obj, agent_type = self._dispatch(tool_name, tool_input)
                result_str = result_obj.model_dump_json()

                if hasattr(result_obj, "plotly_json") and result_obj.plotly_json:
                    sse_events.append({"event": "chart", "data": result_obj.plotly_json})

                duration_ms = round((time.perf_counter() - t_tool) * 1000, 1)
                steps.append({
                    "agent": agent_type.value,
                    "input_summary": self._summarize_input(tool_input),
                    "duration_ms": duration_ms,
                })
                sse_events.append({
                    "event": "agent_done",
                    "data": {"agent": tool_name, "duration_ms": duration_ms},
                })
                sse_events.append({
                    "event": "agent_result",
                    "data": {"agent": tool_name, "summary": self._summarize_result(result_obj)},
                })

            except Exception as exc:
                log.error("tool.error", tool=tool_name, error=str(exc))
                result_str = json.dumps({"error": str(exc)})
                sse_events.append({
                    "event": "agent_done",
                    "data": {"agent": tool_name, "error": str(exc)},
                })

            tool_messages.append(ToolMessage(
                content=result_str,
                tool_call_id=tool_call["id"],
            ))

        return {"messages": tool_messages, "sse_events": sse_events, "steps": steps}

    # ── Public streaming entry point ──────────────────────────────────────────

    async def analyze_stream(
        self, question: str, session_id: str
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Yields StreamEvent objects consumed by the FastAPI SSE endpoint.
        Uses LangGraph astream_events (v2) to interleave:
          - text tokens     → on_chat_model_stream
          - tool events     → on_chain_end[run_tools]
        """
        t_start = time.perf_counter()
        dataset_context = self.loader.context_string()

        history = await session_memory.get_history(session_id)
        prior_messages = self._history_to_messages(history)
        messages = prior_messages + [HumanMessage(content=question)]

        input_state: StatIQState = {
            "messages": messages,
            "dataset_context": dataset_context,
            "session_id": session_id,
            "sse_events": [],
            "steps": [],
        }
        dataset_ref = self.loader._dataset_ref or ""
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": 16,
            "callbacks": create_callback(session_id, question, dataset_ref),
        }

        accumulated_text: list[str] = []
        accumulated_steps: list[dict] = []

        try:
            async for event in self._graph.astream_events(input_state, config, version="v2"):
                kind: str = event["event"]
                name: str = event.get("name", "")

                # ── Stream text tokens from the model ─────────────────────────
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    text = self._extract_text(chunk)
                    if text:
                        accumulated_text.append(text)
                        yield StreamEvent(event="text_chunk", data=text)

                # ── Emit tool events when run_tools node completes ─────────────
                elif kind == "on_chain_end" and name == "run_tools":
                    output = event["data"].get("output", {})
                    for sse in output.get("sse_events", []):
                        yield StreamEvent(**sse)
                    accumulated_steps.extend(output.get("steps", []))

        except Exception as exc:
            log.error("router.stream_error", error=str(exc))
            user_msg = _friendly_error(exc)
            yield StreamEvent(event="error", data=user_msg)
            return

        # ── Persist to Redis ──────────────────────────────────────────────────
        await session_memory.append_history(session_id, "user", question)
        await session_memory.append_history(
            session_id, "assistant", "".join(accumulated_text)
        )

        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        yield StreamEvent(
            event="done",
            data={"total_ms": total_ms, "n_steps": len(accumulated_steps)},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(chunk: Any) -> str:
        content = chunk.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                p.get("text", "") if isinstance(p, dict) else str(p)
                for p in content
                if not isinstance(p, dict) or p.get("type") == "text"
            )
        return ""

    @staticmethod
    def _history_to_messages(history: list[dict]) -> list[BaseMessage]:
        """Convert Redis history dicts to LangChain message objects."""
        messages: list[BaseMessage] = []
        for msg in history:
            role = msg["role"]
            content = msg["content"]
            if role == "user" and isinstance(content, str):
                messages.append(HumanMessage(content=content))
            elif role == "assistant" and isinstance(content, str):
                messages.append(AIMessage(content=content))
        return messages

    def _dispatch(self, tool_name: str, tool_input: dict) -> tuple[Any, AgentType]:
        match tool_name:
            case "run_sql_query":
                return (
                    self.sql.run(tool_input["sql"], tool_input.get("purpose", "")),
                    AgentType.SQL,
                )
            case "run_statistical_analysis":
                return (
                    self.stat.run(
                        analysis_type=tool_input["analysis_type"],
                        columns=tool_input["columns"],
                        group_by=tool_input.get("group_by"),
                        sql_filter=tool_input.get("sql_filter"),
                    ),
                    AgentType.STAT,
                )
            case "create_visualization":
                return (
                    self.viz.run(
                        chart_type=tool_input["chart_type"],
                        x_column=tool_input["x_column"],
                        y_column=tool_input["y_column"],
                        sql_query=tool_input["sql_query"],
                        title=tool_input.get("title", ""),
                        color_column=tool_input.get("color_column"),
                        aggregation=tool_input.get("aggregation", "none"),
                    ),
                    AgentType.VIZ,
                )
            case "run_forecast":
                return (
                    self.forecast.run(
                        date_column=tool_input["date_column"],
                        value_column=tool_input["value_column"],
                        horizon_days=tool_input["horizon_days"],
                        sql_query=tool_input["sql_query"],
                        model=tool_input.get("model", "auto"),
                    ),
                    AgentType.FORECAST,
                )
            case _:
                raise ValueError(f"Unknown tool: {tool_name}")

    @staticmethod
    def _summarize_result(result_obj: Any) -> str:
        if isinstance(result_obj, SQLResult):
            return (
                f"{result_obj.row_count} rows, cols={result_obj.columns}, "
                f"sample={result_obj.rows[:20]}"
            )
        if isinstance(result_obj, StatResult):
            return f"{result_obj.analysis_type}: {str(result_obj.summary)[:400]}"
        if isinstance(result_obj, VizResult):
            return f"chart={result_obj.chart_type}, title={result_obj.title!r}"
        if isinstance(result_obj, ForecastResult):
            return (
                f"forecast {result_obj.target_column} +{result_obj.horizon_days}d, "
                f"MAE={result_obj.mae}, RMSE={result_obj.rmse}, "
                f"first_point={result_obj.forecast_df[0] if result_obj.forecast_df else None}"
            )
        return str(result_obj)[:300]

    @staticmethod
    def _summarize_input(tool_input: dict) -> str:
        if "sql" in tool_input:
            return tool_input["sql"][:120] + "..."
        if "analysis_type" in tool_input:
            return f"{tool_input['analysis_type']} on {tool_input.get('columns', [])}"
        if "chart_type" in tool_input:
            return f"{tool_input['chart_type']}: {tool_input.get('title', '')}"
        if "value_column" in tool_input:
            return f"forecast {tool_input['value_column']} +{tool_input.get('horizon_days')}d"
        return str(tool_input)[:100]
