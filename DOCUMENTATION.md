# StatIQ — Complete Technical Documentation

> AI-Powered Multi-Agent Analytics Platform  
> Version 0.1.0 · Python 3.11 · FastAPI · LangGraph · Anthropic Claude · GCP

**Live:** https://statiq-frontend-3220091638.europe-west1.run.app  
**API:** https://statiq-backend-3220091638.europe-west1.run.app

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Project Structure](#3-project-structure)
4. [Backend](#4-backend)
   - [Configuration](#41-configuration)
   - [Data Layer — DataLoader](#42-data-layer--dataloader)
   - [Multi-Agent Orchestration — RouterAgent](#43-multi-agent-orchestration--routeragent)
   - [Specialist Agents](#44-specialist-agents)
   - [Tool Definitions](#45-tool-definitions)
   - [Session Memory](#46-session-memory)
   - [Observability — Langfuse](#47-observability--langfuse)
   - [Security Layer](#48-security-layer)
   - [API Endpoints](#49-api-endpoints)
   - [Data Models](#410-data-models)
5. [Frontend](#5-frontend)
6. [Evaluation — LLM-as-Judge](#6-evaluation--llm-as-judge)
7. [CI/CD](#7-cicd)
8. [Infrastructure](#8-infrastructure)
9. [Environment Variables](#9-environment-variables)
10. [Local Development](#10-local-development)
11. [Running Tests](#11-running-tests)
12. [Data Sources](#12-data-sources)
13. [Dependency Reference](#13-dependency-reference)

---

## 1. Overview

StatIQ lets non-technical users ask plain-language questions about structured data and receive statistical analysis, interactive charts, and forecasts in seconds — no code required.

**How it works, end-to-end:**

1. User uploads a CSV/Excel file or connects to a public BigQuery dataset.
2. The user types a question in natural language (French or English).
3. The `RouterAgent` (LangGraph `StateGraph`) invokes Claude Sonnet 4.6 with a set of bound tools.
4. Claude selects the right tool(s) — SQL, statistics, visualization, forecasting — and the agent loop executes them.
5. Results stream back to the frontend via SSE (Server-Sent Events) in real time.
6. Claude narrates the findings in plain language, and a Plotly chart is rendered interactively.
7. The user can export the full report as HTML, PDF, or Markdown.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          GCP (europe-west1)                      │
│                                                                  │
│   Streamlit Frontend ──[X-Api-Key]──► FastAPI Backend            │
│   (Cloud Run)                         (Cloud Run)                │
│                                            │                     │
│                              LangGraph StateGraph                │
│                                            │                     │
│                    ┌───────────────────────┼──────────────────┐  │
│                    ▼           ▼           ▼          ▼        │  │
│               SQLAgent    StatAgent    VizAgent   ForecastAgent│  │
│               (DuckDB/  (scipy/      (Plotly)    (Prophet/     │  │
│               BigQuery)  statsmodels)             N-HiTS)      │  │
│                    └───────────────────────┬──────────────────┘  │
│                                            ▼                     │
│                                   Anthropic Claude               │
│                              (claude-sonnet-4-6)                 │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌──────────────┐   │
│  │   GCS    │  │ BigQuery │  │   Redis    │  │   Langfuse   │   │
│  │(uploads) │  │(opendata)│  │ (sessions) │  │(observability│   │
│  └──────────┘  └──────────┘  └────────────┘  └──────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- **Streaming first:** All analysis is delivered via SSE so the user sees tokens and agent progress in real time rather than waiting for a full response.
- **Data locality:** BigQuery tables are queried directly for large aggregations. A 50 000-row sample is pulled into DuckDB for stat/viz operations that need fast local access.
- **Stateless backend:** Each request re-creates agent state from Redis history + the current `DataLoader`. The `DataLoader` is kept in an LRU cache (`_loaders`, max 100 entries) keyed by `session_id`.
- **Graceful degradation:** Redis unavailable → in-memory fallback. GCS unavailable → DuckDB direct. Langfuse unavailable → no-op callbacks. Auth disabled when `API_KEY` is empty (local dev).

---

## 3. Project Structure

```
statiq/
├── backend/
│   ├── main.py                  # FastAPI app — all HTTP endpoints
│   ├── config.py                # Pydantic settings (env-driven, cached)
│   ├── security.py              # API key auth, rate limiter, PII detection, audit
│   ├── agents/
│   │   ├── base.py              # DataLoader — unified BigQuery + DuckDB access
│   │   ├── router.py            # LangGraph StateGraph orchestrator
│   │   ├── sql_agent.py         # SQL execution → SQLResult
│   │   ├── stat_agent.py        # Statistical analysis → StatResult
│   │   ├── viz_agent.py         # Plotly chart generation → VizResult
│   │   └── forecast_agent.py    # Time-series forecasting → ForecastResult
│   ├── eval/
│   │   └── judge.py             # LLM-as-judge (EvalScore, LLMJudge)
│   ├── tools/
│   │   └── definitions.py       # Tool schemas + system prompt for Claude
│   ├── memory/
│   │   └── session.py           # Redis SessionMemory (with in-memory fallback)
│   ├── observability/
│   │   └── langfuse_client.py   # Langfuse v4 callback + score logging
│   └── models/
│       └── schemas.py           # All Pydantic models
├── frontend/
│   └── app.py                   # Streamlit UI (dark theme, SSE consumer)
├── infra/
│   ├── cloudbuild.yaml          # Cloud Build CI/CD — 7 steps
│   ├── setup_gcp.sh             # One-time GCP bootstrap script
│   ├── trigger.json             # Cloud Build trigger config
│   └── gcs_lifecycle.json       # GCS auto-cleanup policy (30-day TTL)
├── tests/
│   ├── unit/                    # 11 unit tests (no API calls)
│   └── eval/
│       └── run_eval.py          # LLM-as-judge golden set (5 questions)
├── .github/
│   └── workflows/ci.yml         # GitHub Actions — lint, type-check, test, docker build
├── .streamlit/
│   └── config.toml              # Dark theme config
├── docker-compose.yml           # Local dev (Redis + backend + frontend)
├── Dockerfile.backend           # Multi-stage, linux/amd64
├── Dockerfile.frontend          # Cloud Run compatible (PORT env var)
└── pyproject.toml               # Dependencies + pytest config
```

---

## 4. Backend

### 4.1 Configuration

**File:** `backend/config.py`

Settings are loaded from environment variables (and `.env` in development) via `pydantic-settings`. All values are typed and validated at startup.

```python
class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"

    # GCP
    gcp_project_id: str = ""
    gcs_bucket_name: str = ""
    gcp_region: str = "europe-west1"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_ttl_seconds: int = 7200  # 2h session TTL

    # App
    app_env: str = "development"
    max_file_size_mb: int = 100

    # Security
    api_key: str = ""               # empty = auth disabled
    allowed_origins: str = "*"
    rate_limit_per_minute: int = 20

    # Langfuse (optional)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # BigQuery demo
    bq_demo_project: str = "bigquery-public-data"
    bq_demo_dataset: str = "chicago_taxi_trips"
    bq_demo_table:   str = "taxi_trips"
```

The singleton is accessed via `get_settings()` (cached with `@lru_cache`).

---

### 4.2 Data Layer — DataLoader

**File:** `backend/agents/base.py`

`DataLoader` is the single entry point for all data access. Every specialist agent holds a reference to the same `DataLoader` instance for a given session.

#### Initialization

```python
loader = DataLoader()
loader.setup_bigquery("bigquery-public-data.chicago_taxi_trips.taxi_trips")
# or
loader.setup_gcs_csv("uploads/<session_id>/data.csv")
# or (for direct DataFrame loading after upload)
loader.duck.register("df", df)
loader._loaded_table = "df"
loader._source_type = DataSourceType.CSV_UPLOAD
```

#### `run_sql(sql, max_rows=10_000)`

Routes SQL execution:
- **BigQuery source:** runs directly via `bigquery.Client`. On error (e.g., table alias mismatch), falls back to a 50 000-row DuckDB sample.
- **DuckDB source (CSV/GCS):** executes locally. Auto-fixes `STRFTIME` errors on VARCHAR date columns by wrapping the column in `TRY_CAST(col AS DATE)`.

Returns `(DataFrame, elapsed_ms)`.

#### `profile()`

Returns a `DatasetProfile` with per-column stats (dtype, null %, unique count, min/max/mean/std, sample values). Used by the frontend to display dataset info and passed to the agent as context. For BigQuery, pulls a 5 000-row sample. Result is **not** cached (each call re-profiles).

#### `context_string()`

Generates a human-readable dataset summary injected into the Claude system prompt. Includes column names, types, stats, and the correct SQL table alias (`df` for DuckDB, fully-qualified name for BigQuery). **Cached** per `DataLoader` instance.

---

### 4.3 Multi-Agent Orchestration — RouterAgent

**File:** `backend/agents/router.py`

The `RouterAgent` wraps a LangGraph `StateGraph` that implements a standard tool-use loop:

```
START → call_model ──(tool_calls present?)──► run_tools ──► call_model
                    └──(no tool calls)──────► END
```

The graph compiles with a `MemorySaver` checkpointer keyed by `thread_id=session_id`, preserving full message history within a process.

#### Graph State

```python
class StatIQState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]  # conversation history
    dataset_context: str      # injected once per request
    session_id: str
    sse_events: Annotated[list[dict], operator.add]        # accumulated SSE payloads
    steps: Annotated[list[dict], operator.add]             # tool execution metadata
```

#### `call_model` node

Prepends a `SystemMessage` containing `ROUTER_SYSTEM_PROMPT` (with `{dataset_context}` filled in), then invokes `ChatAnthropic` with all four tools bound. Streams text tokens in `analyze_stream` via the `on_chat_model_stream` LangGraph event.

#### `run_tools` node

Iterates over `tool_calls` in the last `AIMessage`. For each call:
1. Emits `agent_start` SSE event.
2. Dispatches to the appropriate specialist (`_dispatch` method).
3. Emits `agent_done` + `agent_result` SSE events.
4. Emits `chart` SSE event if the result contains a Plotly JSON chart.
5. Appends a `ToolMessage` with the serialized result back into the graph state.

#### `analyze_stream(question, session_id)`

The public async generator consumed by the FastAPI SSE endpoint:

1. Loads Redis conversation history, converts to `HumanMessage`/`AIMessage` objects.
2. Runs `astream_events(..., version="v2")`.
3. Yields `StreamEvent` objects for every `on_chat_model_stream` (text token) and every `on_chain_end[run_tools]` (tool results and chart data).
4. On completion, appends user question and assistant narrative to Redis.
5. Yields a final `done` event with `total_ms` and `n_steps`.

**Recursion limit:** 16 iterations (prevents infinite tool loops).

---

### 4.4 Specialist Agents

All specialist agents are pure synchronous classes that receive a `DataLoader` and return a typed result model.

#### SQLAgent (`backend/agents/sql_agent.py`)

Executes a SQL string via `loader.run_sql()` and returns a `SQLResult` with:
- `query` — the SQL executed
- `rows` — up to 500 records as `list[dict]`
- `row_count` — total rows before capping
- `columns` — column names
- `execution_time_ms`

#### StatAgent (`backend/agents/stat_agent.py`)

Loads the relevant columns (up to 50 000 rows) then dispatches to one of seven handlers:

| `analysis_type` | Method | Libraries |
|---|---|---|
| `descriptive` | `_descriptive` | pandas describe + skewness/kurtosis |
| `correlation` | `_correlation` | scipy `pearsonr`, pandas `.corr()` |
| `normality_test` | `_normality` | scipy `shapiro` + `kstest` |
| `t_test` | `_ttest` | scipy `ttest_ind` (Welch) |
| `anova` | `_anova` | scipy `f_oneway` |
| `chi_square` | `_chi_square` | scipy `chi2_contingency` |
| `linear_regression` | `_regression` | statsmodels OLS |

Each handler returns a `StatResult` with:
- `analysis_type` — the test performed
- `summary` — dict with test statistics, p-values, etc.
- `interpretation` — plain French narrative of the result (e.g., "Différence significative (p<0.05)")

#### VizAgent (`backend/agents/viz_agent.py`)

Executes the `sql_query`, optionally applies an in-memory aggregation (sum, mean, count…), then builds a Plotly figure.

Supported chart types: `bar`, `line`, `scatter`, `histogram`, `box`, `heatmap`, `pie`, `area`.

Uses the `statiq` Plotly template with dark colors:
- Background: `rgba(17,24,39,0.8)` (dark navy)
- Color sequence: `["#00E5FF", "#FF6B6B", "#69FF47", "#FFD93D", "#C77DFF", "#FF9A3C"]`

Returns a `VizResult` with `plotly_json` (full Plotly JSON string for frontend rendering).

#### ForecastAgent (`backend/agents/forecast_agent.py`)

Loads time-series data via `sql_query`, then selects a forecasting model:

**Model selection logic:**
```
"auto"  → N-HiTS  if n ≥ 50 AND n ≥ 3×horizon_days
          Prophet otherwise
"nhits" → N-HiTS  (forced; falls back to Prophet if data too small)
"deepar"→ DeepAR  (forced; falls back to Prophet if data too small)
"prophet"→ Prophet (forced)
```

**Prophet** (`_run_prophet`):
- Yearly + weekly seasonality, 90% confidence interval
- In-sample MAE and RMSE computed against training predictions

**N-HiTS / DeepAR** (`_run_neural`):
- Uses `neuralforecast` library
- Frequency auto-detected from date diffs (D/W/MS/QS/YS)
- `input_size = max(2×horizon, min(60, n//3))`
- In-sample metrics via 1-fold cross-validation

Returns a `ForecastResult` with:
- `forecast_df` — list of `{date, forecast, lower_90, upper_90}` records
- `mae`, `rmse` — accuracy metrics
- `plotly_json` — styled chart (history in cyan + forecast in red + confidence band)

---

### 4.5 Tool Definitions

**File:** `backend/tools/definitions.py`

Defines four tools passed to Claude via `ChatAnthropic(...).bind_tools(LC_TOOLS)`:

| Tool | When Claude uses it | Key parameters |
|---|---|---|
| `run_sql_query` | Aggregation, filtering, exploration | `sql`, `purpose` |
| `run_statistical_analysis` | Correlation, tests, regression | `analysis_type`, `columns`, `group_by`, `sql_filter` |
| `create_visualization` | Charts, trends, distributions | `chart_type`, `x_column`, `y_column`, `sql_query`, `title`, `aggregation` |
| `run_forecast` | Future predictions | `date_column`, `value_column`, `horizon_days`, `sql_query`, `model` |

**`ROUTER_SYSTEM_PROMPT`** instructs Claude to:
- Always start with SQL if the data is unknown
- Chain tools logically: SQL → Stats → Viz
- Generate one primary visualization
- Use `run_forecast` only for forward-looking questions
- Always respond in French
- Never fabricate data — all figures must come from tool results

---

### 4.6 Session Memory

**File:** `backend/memory/session.py`

`SessionMemory` stores per-session state in Redis (Cloud Memorystore on GCP), with an automatic in-memory dict fallback when Redis is unreachable.

**Redis keys:**
| Key | Content | TTL |
|---|---|---|
| `session:{id}:history` | JSON list of `{role, content}` dicts (last 20 turns) | `REDIS_TTL_SECONDS` (default 7200s) |
| `session:{id}:dataset` | JSON dict `{source, ref}` | `REDIS_TTL_SECONDS` |
| `qcache:{hash}` | Query result cache | 300s |

**Methods:**
- `get_history(session_id)` / `append_history(session_id, role, content)` / `clear_history(session_id)`
- `set_dataset_context(session_id, ctx)` / `get_dataset_context(session_id)`
- `cache_query_result(cache_key, result, ttl)` / `get_cached_query(cache_key)`

The singleton `session_memory` is imported in `main.py` and `router.py`.

---

### 4.7 Observability — Langfuse

**File:** `backend/observability/langfuse_client.py`

Integration with [Langfuse](https://langfuse.com) v4 for LLM observability.

**`create_callback(session_id, question, dataset_ref)`**

Returns a `[CallbackHandler]` list injected into the LangGraph config. When Langfuse is configured, all LLM calls, node executions, and token usage are traced automatically via LangChain's callback interface.

A deterministic `trace_id` is generated from `session_id` using `Langfuse.create_trace_id(seed=session_id)`, so all turns of the same session are grouped under one trace in the Langfuse dashboard.

**`log_eval_scores(trace_id, scores)`**

Attaches LLM-as-judge scores to a Langfuse trace via `client.create_score()`. Called from `run_eval.py`.

**`flush()`**

Force-flushes pending events — called at app shutdown in the FastAPI lifespan.

**Graceful degradation:** if `LANGFUSE_PUBLIC_KEY` is not set (e.g., in local dev or CI), all functions are no-ops.

---

### 4.8 Security Layer

**File:** `backend/security.py`

#### API Key Authentication

```python
_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)

def require_api_key(request, key=None):
    if not settings.api_key: return   # disabled in local dev
    if key != settings.api_key:
        raise HTTPException(401, "Invalid or missing API key")
```

Applied via `dependencies=[Depends(require_api_key)]` on `/api/datasets/connect`, `/api/datasets/upload`, `/api/analyze`, and `/api/analyze/stream`.

#### Rate Limiting

Token bucket algorithm: sliding 60-second window per client IP. Default: 20 requests/minute.

```python
class _TokenBucket:
    def allow(self, key: str) -> bool:
        # drop entries older than 60s, check count, append timestamp
```

Applied to all `/api/analyze` endpoints. Returns `HTTP 429` with a French error message.

#### PII Detection

Scans uploaded file column names against 40+ regex patterns (French and English):

- Identity: `\bfirst.?name\b`, `\blast.?name\b`, `\bnom\b`, `\bpr[eé]nom\b`, `\bnational.?id\b`
- Contact: `\bemail\b`, `\bphone\b`, `\bt[eé]l[eé]phone\b`, `\baddress\b`, `\badresse\b`
- Financial: `\biban\b`, `\bcredit.?card\b`, `\bsalaire\b`, `\bincome\b`
- Medical: `\bpatient\b`, `\bdiagno[sc]\w+\b`, `\bmedical\b`
- Technical: `\bip.?address\b`, `\bgps\b`, `\blatitude\b`, `\blongitude\b`

When PII columns are detected, the upload response includes a `pii_warning` field. The frontend displays a **blocking consent gate** — the user must acknowledge before the analysis can proceed.

#### Audit Trail

Every `connect`, `upload`, and `analyze` action is logged via `structlog` with `action`, `ip`, `session`, and contextual fields. In production, these are picked up by Cloud Logging.

---

### 4.9 API Endpoints

**Base URL:** `https://statiq-backend-3220091638.europe-west1.run.app`

All protected endpoints require the `X-Api-Key` header.

#### `GET /health`
Health check for Cloud Run liveness probe.
```json
{"status": "ok", "version": "0.1.0"}
```

#### `POST /api/datasets/connect` *(requires API key)*
Connect to a BigQuery table or GCS CSV.

Form fields:
- `session_id` (auto-generated UUID if omitted)
- `source`: `"bigquery"` | `"gcs"`
- `dataset_ref`: BigQuery table reference or GCS object path

Response: `{session_id, status, profile: DatasetProfile}`

#### `POST /api/datasets/upload` *(requires API key)*
Upload a CSV or Excel file (`.csv`, `.xlsx`, `.xls`). Max size controlled by `MAX_FILE_SIZE_MB`.

1. File is uploaded to GCS at `uploads/{session_id}/{filename}`.
2. For Excel files or on GCS failure: loaded directly into DuckDB.
3. PII detection runs on column names.

Response: `{session_id, gcs_object, profile, pii_warning?}`

#### `GET /api/datasets/profile`
Returns a `DatasetProfile` for the current session dataset.

Query: `?session_id=<uuid>`

#### `GET /api/analyze/stream` *(requires API key + rate limit)*
SSE streaming endpoint. Yields `data: {event, data}\n\n` lines.

Query: `?session_id=<uuid>&question=<string>`

**Event types:**

| Event | `data` | Description |
|---|---|---|
| `agent_start` | `{agent, input}` | Tool invocation started |
| `agent_done` | `{agent, duration_ms}` or `{agent, error}` | Tool completed |
| `agent_result` | `{agent, summary}` | Tool result summary |
| `chart` | Plotly JSON string | Interactive chart |
| `text_chunk` | string | Claude narrative token |
| `done` | `{total_ms, n_steps}` | Analysis complete |
| `error` | string | Friendly error message |

#### `POST /api/analyze` *(requires API key + rate limit)*
Non-streaming version. Collects all SSE events and returns them as JSON.

Body: `AnalyzeRequest`

#### `GET /api/sessions/{session_id}`
Returns conversation history and dataset context for a session.

#### `DELETE /api/sessions/{session_id}`
Clears history from Redis and evicts the `DataLoader` from the in-process cache.

#### `GET /api/datasets/catalog`
Returns the list of available GCP open datasets with sample questions (no auth required).

---

### 4.10 Data Models

**File:** `backend/models/schemas.py`

| Model | Purpose |
|---|---|
| `DataSourceType` | Enum: `csv_upload`, `bigquery`, `gcs` |
| `AgentType` | Enum: `router`, `sql`, `stat`, `viz`, `narrator`, `forecast` |
| `AnalyzeRequest` | POST body for `/api/analyze` |
| `SQLResult` | Output of `SQLAgent.run()` |
| `StatResult` | Output of `StatAgent.run()` |
| `VizResult` | Output of `VizAgent.run()` (includes `plotly_json`) |
| `ForecastResult` | Output of `ForecastAgent.run()` (includes `forecast_df`, `mae`, `rmse`, `plotly_json`) |
| `StreamEvent` | One SSE event: `{event: str, data: Any}` |
| `ColumnProfile` | Per-column stats from `DataLoader.profile()` |
| `DatasetProfile` | Full dataset profile: `{name, source, n_rows, n_cols, columns}` |

---

## 5. Frontend

**File:** `frontend/app.py`

Built with Streamlit. Uses a dark navy theme configured in `.streamlit/config.toml`:

```toml
[theme]
base = "dark"
primaryColor = "#3B82F6"
backgroundColor = "#0F172A"
secondaryBackgroundColor = "#1E293B"
textColor = "#E2E8F0"
```

### Session State

| Key | Type | Description |
|---|---|---|
| `session_id` | `str` | UUID assigned on first load |
| `messages` | `list[dict]` | Rendered conversation history |
| `dataset_connected` | `bool` | Whether a dataset is active |
| `dataset_name` | `str` | Display name for current dataset |
| `pii_warning` | `str \| None` | PII warning from upload response |
| `pii_acknowledged` | `bool` | Whether user has accepted PII gate |

### Main UI Flow

1. **Sidebar** — dataset connection panel:
   - Radio: "Données publiques GCP" | "Importer un fichier"
   - For GCP data: select dataset from catalog → "Connecter"
   - For file upload: drag-and-drop CSV/Excel → "Analyser ce fichier"
   - Shows dataset profile (row count, column count, preview table)

2. **PII Consent Gate** — if `pii_warning` is set and not acknowledged:
   - `st.warning()` with column names
   - "J'ai compris, continuer" button sets `pii_acknowledged = True`
   - "Annuler" button clears the session
   - `st.stop()` blocks all rendering below until acknowledged

3. **Chat Interface** — replays `st.session_state.messages` on each rerun:
   - User messages: right-aligned bubble
   - Assistant messages: left-aligned bubble with narrative text
   - Charts: `st.plotly_chart()` rendered from stored `chart_json`
   - Agent steps: expandable `st.expander` showing tool name and duration
   - Export button: single `st.popover("📤 Exporter le rapport")` with three download options

4. **Question Input** — `st.chat_input()` at the bottom:
   - Calls `GET /api/analyze/stream` with `EventSource`-style SSE consumption
   - Streams text tokens into a growing `st.empty()` placeholder
   - On `chart` event: stores Plotly JSON in `st.session_state`
   - On `done` event: finalizes the message and triggers `st.rerun()`

### Export Formats

All three formats are generated on demand inside the `st.popover`:

| Format | Function | Notes |
|---|---|---|
| HTML | `_build_report_html()` | Self-contained page with embedded Plotly chart via CDN. Markdown in narrative converted to HTML (headers, bold, italic, lists, code). |
| PDF | `_build_report_pdf()` | ReportLab `SimpleDocTemplate` with styled paragraphs, `HRFlowable` dividers, and chart image via `fig.to_image(format="png")` (requires kaleido). |
| Markdown | `_build_report_markdown()` | Plain text with frontmatter metadata. Suitable for Notion, GitHub, Obsidian. |

### API Communication

All requests include `headers={"X-Api-Key": API_KEY}` where `API_KEY = os.environ.get("API_KEY", "")`.

The backend URL is read from `BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8080")`.

---

## 6. Evaluation — LLM-as-Judge

### Judge Architecture

**File:** `backend/eval/judge.py`

`LLMJudge` takes an `AnalysisTrace` (question + tool calls + narrative) and asks Claude to score it on four criteria using a forced `tool_use` call with `tool_choice={"type": "any"}`.

#### Scoring Criteria

| Criterion | Scale | Description |
|---|---|---|
| `grounding` | 0–3 | Does the narrative cite specific numbers from tool results? |
| `precision` | 0–3 | Are statistical interpretations correct? |
| `relevance` | 0–3 | Does the response answer the question asked? |
| `hallucination` | bool | Are there any figures in the narrative NOT from tool results? |

**Grade mapping:**
- A: total ≥ 8 and no hallucination
- B: total ≥ 6
- C: total ≥ 4
- D: total < 4
- FAIL: hallucination = true

### Golden Question Set

**File:** `tests/eval/run_eval.py`

5 questions on a synthetic 500-row taxi dataset (reproducible with `rng.seed=42`):

| ID | Question | Expected Tools |
|---|---|---|
| Q1 | Durée moyenne et distribution | `run_sql_query`, `run_statistical_analysis` |
| Q2 | Corrélation distance/pourboire | `run_statistical_analysis` |
| Q3 | Tarif moyen par type de paiement | `run_sql_query` |
| Q4 | Évolution mensuelle des trajets | `run_sql_query`, `create_visualization` |
| Q5 | Prévision tarif 30 prochains jours | `run_sql_query`, `run_forecast` |

**Pass threshold:** score ≥ 4/9 and no hallucination for each question.

#### Running Evaluation

```bash
# All 5 questions
python tests/eval/run_eval.py

# Single question (1-indexed)
python tests/eval/run_eval.py --question 2

# Via pytest (requires -m eval flag)
pytest tests/eval/ -v -m eval
```

Scores are automatically logged to Langfuse if configured.

---

## 7. CI/CD

### GitHub Actions

**File:** `.github/workflows/ci.yml`  
Triggers on push to `main` or `develop`, and on pull requests to `main`.

Steps:
1. **Lint** — `ruff check backend/ tests/`
2. **Type check** — `mypy backend/ --ignore-missing-imports`
3. **Unit tests** — `pytest tests/unit/ -v --tb=short --cov=backend`
4. **Docker build** — builds both images (no push — build validation only)

### Cloud Build

**File:** `infra/cloudbuild.yaml`  
Triggered automatically on push to `main` via a Cloud Build trigger connected to the GitHub repository (`Senstat/statiq`).

**Pipeline steps:**

| Step | `waitFor` | Description |
|---|---|---|
| `test` | — | Run unit tests in `python:3.11-slim` |
| `build-backend` | `test` | `docker build -f Dockerfile.backend` |
| `build-frontend` | `test` | `docker build -f Dockerfile.frontend` |
| `push-backend` | `build-backend` | Push to Artifact Registry |
| `push-frontend` | `build-frontend` | Push to Artifact Registry |
| `deploy-backend` | `push-backend` | `gcloud run deploy statiq-backend` |
| `deploy-frontend` | `push-frontend`, `deploy-backend` | `gcloud run deploy statiq-frontend` |
| `smoke-test` | `deploy-backend` | `curl /health` → assert HTTP 200 |

**Backend Cloud Run config:** 2 GiB memory, 2 CPU, 80 concurrency, 0–10 instances, 300s timeout, VPC connector for Redis access.

**Frontend Cloud Run config:** 1 GiB memory, 1 CPU, 50 concurrency, 0–5 instances. `BACKEND_URL` is auto-populated from the deployed backend service URL.

---

## 8. Infrastructure

### GCP Services Used

| Service | Purpose |
|---|---|
| **Cloud Run** | Serverless container runtime (backend + frontend) |
| **Artifact Registry** | Docker image storage (`europe-west1-docker.pkg.dev`) |
| **Cloud Build** | CI/CD pipeline |
| **Cloud Memorystore (Redis)** | Session persistence |
| **VPC Connector** | Private network access from Cloud Run to Redis |
| **Google Cloud Storage** | File uploads (`uploads/{session_id}/{filename}`) |
| **BigQuery** | Open dataset queries |
| **Secret Manager** | Secure storage of API keys and credentials |
| **Cloud Logging** | Structured audit logs (via structlog) |

### One-Time Bootstrap

**File:** `infra/setup_gcp.sh`

```bash
bash infra/setup_gcp.sh <PROJECT_ID> <REGION> <GITHUB_USER>
```

Creates: service account (`statiq-backend-sa`) with required roles, secrets in Secret Manager, GCS bucket, Artifact Registry repository, Redis instance, VPC connector.

### Secret Manager Keys

| Secret | Value |
|---|---|
| `anthropic-api-key` | Anthropic API key |
| `gcp-project-id` | GCP project ID |
| `gcs-bucket-name` | GCS bucket name |
| `langfuse-public-key` | Langfuse public key |
| `langfuse-secret-key` | Langfuse secret key |
| `langfuse-host` | `https://cloud.langfuse.com` |

---

## 9. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `GCP_PROJECT_ID` | Prod | `""` | GCP project ID |
| `GCS_BUCKET_NAME` | Prod | `""` | GCS bucket for file uploads |
| `REDIS_HOST` | Prod | `localhost` | Cloud Memorystore IP |
| `REDIS_PORT` | No | `6379` | Redis port |
| `REDIS_TTL_SECONDS` | No | `7200` | Session TTL in seconds |
| `API_KEY` | Prod | `""` | Backend API key (empty = auth disabled) |
| `ALLOWED_ORIGINS` | Prod | `*` | Comma-separated CORS origins |
| `RATE_LIMIT_PER_MINUTE` | No | `20` | Analyze requests per IP per minute |
| `LANGFUSE_PUBLIC_KEY` | Optional | `""` | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | Optional | `""` | Langfuse secret key |
| `LANGFUSE_HOST` | Optional | `https://cloud.langfuse.com` | Langfuse server URL |
| `APP_ENV` | No | `development` | `development` or `production` |
| `LOG_LEVEL` | No | `INFO` | Structlog level |
| `MAX_FILE_SIZE_MB` | No | `100` | Maximum upload file size |
| `BACKEND_URL` | Frontend | `http://localhost:8080` | Backend base URL (set on Cloud Run) |
| `GCP_REGION` | No | `europe-west1` | GCP region |

---

## 10. Local Development

### Prerequisites

- Python 3.11+
- Docker (for Redis)
- `gcloud` CLI (for GCP features)

### Setup

```bash
# Clone and install
git clone https://github.com/SenStat/statiq.git
cd statiq
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,frontend]"

# Configure
cp .env.example .env
# Set ANTHROPIC_API_KEY at minimum

# Start Redis (optional — in-memory fallback used if unavailable)
docker compose up -d redis

# Run backend
uvicorn backend.main:app --reload --port 8080

# Run frontend (in a separate terminal)
streamlit run frontend/app.py --server.port 8503
```

Open http://localhost:8503

### CSV-Only Mode (no GCP)

If `GCP_PROJECT_ID` and `GCS_BUCKET_NAME` are empty:
- BigQuery datasets are unavailable
- File uploads fall back to direct DuckDB loading (no GCS)
- All other features work normally

### Local Frontend Against Production Backend

```bash
BACKEND_URL=https://statiq-backend-3220091638.europe-west1.run.app \
API_KEY=<your-key> \
streamlit run frontend/app.py --server.port 8503
```

---

## 11. Running Tests

### Unit Tests (fast, no API calls)

```bash
pytest tests/unit/ -v --tb=short --cov=backend --cov-report=term-missing
```

Tests cover: `SQLAgent`, `StatAgent`, `VizAgent`. Mock data is injected directly into `DataLoader.duck`.

### Eval Tests (LLM-as-judge, requires `ANTHROPIC_API_KEY`)

```bash
# All 5 golden questions
python tests/eval/run_eval.py

# Single question
python tests/eval/run_eval.py --question 3

# Via pytest (excluded from default run by `-m 'not eval'`)
pytest tests/eval/ -v -m eval
```

### Lint and Type Check

```bash
ruff check backend/ tests/
mypy backend/ --ignore-missing-imports
```

---

## 12. Data Sources

### GCP Public Datasets

| Dataset | BigQuery Reference | Size | Sample Questions |
|---|---|---|---|
| **Chicago Taxi Trips** | `bigquery-public-data.chicago_taxi_trips.taxi_trips` | 40M+ rows | Average trip duration by hour? Monthly trend 2022? Correlation distance/tip? |
| **New York Citi Bike** | `bigquery-public-data.new_york_citibike.citibike_trips` | — | Top 10 departure stations? Duration: subscriber vs casual? Weekly trip trend? |
| **Austin Bikeshare** | `bigquery-public-data.austin_bikeshare.bikeshare_trips` | — | Median duration by bike type? Trip duration distribution? |
| **World Bank Education** | `bigquery-public-data.world_bank_intl_education.international_education` | — | Literacy rate by region? Primary enrollment trend in Sub-Saharan Africa? |

### File Upload

Supported formats: `.csv`, `.xlsx`, `.xls`. Maximum size: 100 MB (configurable via `MAX_FILE_SIZE_MB`).

Files are stored in GCS at `uploads/{session_id}/{filename}` with a 30-day lifecycle policy (`infra/gcs_lifecycle.json`).

---

## 13. Dependency Reference

| Package | Version | Role |
|---|---|---|
| `fastapi` | ≥0.111 | HTTP API framework |
| `uvicorn[standard]` | ≥0.29 | ASGI server |
| `anthropic` | ≥0.28 | Anthropic Claude API client |
| `langgraph` | ≥0.2 | LangGraph StateGraph orchestration |
| `langchain` | ≥0.3 | LangChain message types |
| `langchain-anthropic` | ≥0.3 | `ChatAnthropic` with tool binding |
| `langfuse` | ≥2.0 | LLM observability (v4 API used) |
| `duckdb` | ≥0.10 | In-process SQL engine for CSV/GCS data |
| `pandas` | ≥2.2 | DataFrame manipulation |
| `scipy` | ≥1.13 | Statistical tests |
| `statsmodels` | ≥0.14 | OLS regression |
| `pingouin` | ≥0.5.4 | Additional statistical tests |
| `plotly` | ≥5.22 | Interactive charts |
| `kaleido` | ≥0.2.1 | Plotly to PNG/PDF rendering |
| `prophet` | ≥1.1.5 | Time-series forecasting (short/irregular series) |
| `neuralforecast` | ≥1.7 | N-HiTS + DeepAR neural forecasting |
| `google-cloud-bigquery` | ≥3.23 | BigQuery client |
| `google-cloud-storage` | ≥2.17 | GCS client |
| `redis` | ≥5.0 | Redis async client |
| `pydantic` | ≥2.7 | Data validation and settings |
| `pydantic-settings` | ≥2.3 | Env-driven configuration |
| `structlog` | ≥24.2 | Structured logging |
| `reportlab` | ≥4.2 | PDF generation |
| `openpyxl` | ≥3.1 | Excel file reading |
| `streamlit` | ≥1.35 | Frontend UI framework |

---

*Generated 2026-05-14 — StatIQ v0.1.0*
