from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
import uuid


class DataSourceType(str, Enum):
    CSV_UPLOAD = "csv_upload"
    BIGQUERY = "bigquery"
    GCS = "gcs"


class AgentType(str, Enum):
    ROUTER = "router"
    SQL = "sql"
    STAT = "stat"
    VIZ = "viz"
    NARRATOR = "narrator"
    FORECAST = "forecast"


# ── Requests ──────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    data_source: DataSourceType = DataSourceType.BIGQUERY
    dataset_ref: str | None = None   # e.g. "bigquery-public-data.chicago_taxi_trips.taxi_trips"
    file_id: str | None = None       # GCS object name after upload


class DatasetInfoRequest(BaseModel):
    data_source: DataSourceType
    dataset_ref: str | None = None
    file_id: str | None = None


# ── Tool call schemas (passed to Claude) ─────────────────────────────────────

class ToolResult(BaseModel):
    tool_use_id: str
    content: str


# ── Agent outputs ─────────────────────────────────────────────────────────────

class SQLResult(BaseModel):
    query: str
    rows: list[dict[str, Any]]
    row_count: int
    columns: list[str]
    execution_time_ms: float


class StatResult(BaseModel):
    analysis_type: str
    summary: dict[str, Any]
    interpretation: str


class VizResult(BaseModel):
    chart_type: str
    plotly_json: str          # JSON string of plotly figure
    title: str
    description: str


class ForecastResult(BaseModel):
    target_column: str
    horizon_days: int
    forecast_df: list[dict[str, Any]]
    mae: float | None = None
    rmse: float | None = None
    plotly_json: str


class AgentStep(BaseModel):
    agent: AgentType
    input_summary: str
    output: SQLResult | StatResult | VizResult | ForecastResult | str
    duration_ms: float


class AnalysisResponse(BaseModel):
    session_id: str
    question: str
    steps: list[AgentStep]
    narrative: str
    has_chart: bool
    chart_json: str | None = None
    total_duration_ms: float


# ── SSE streaming events ──────────────────────────────────────────────────────

class StreamEvent(BaseModel):
    event: str   # "agent_start" | "agent_done" | "text_chunk" | "chart" | "done" | "error"
    data: Any


# ── Dataset profile ───────────────────────────────────────────────────────────

class ColumnProfile(BaseModel):
    name: str
    dtype: str
    null_pct: float
    n_unique: int
    sample_values: list[Any]
    min_val: Any | None = None
    max_val: Any | None = None
    mean: float | None = None
    std: float | None = None


class DatasetProfile(BaseModel):
    name: str
    source: DataSourceType
    n_rows: int
    n_cols: int
    columns: list[ColumnProfile]
    size_mb: float | None = None
