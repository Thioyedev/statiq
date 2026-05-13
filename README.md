# StatIQ — AI Analytics Platform

[![CI](https://github.com/Senstat/statiq/actions/workflows/ci.yml/badge.svg)](https://github.com/Senstat/statiq/actions)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-purple.svg)](https://langchain-ai.github.io/langgraph)
[![GCP](https://img.shields.io/badge/deployed-Cloud%20Run-orange.svg)](https://cloud.google.com/run)

> Ask questions about your data in plain language — get statistical analysis, interactive charts, and forecasts in seconds. No code required.

**Live:** https://statiq-frontend-3220091638.europe-west1.run.app

---

## Overview

StatIQ is a production-grade multi-agent analytics platform that lets non-technical stakeholders explore data through natural language. A LangGraph-orchestrated agent selects the right analytical tool (SQL, statistics, visualization, forecasting) based on the question, streams the result in real time, and narrates the findings in plain French or English.

---

## Architecture

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
│               (DuckDB)  (scipy/      (Plotly)    (Prophet/     │  │
│                          statsmodels)             N-HiTS)      │  │
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

---

## Tech Stack

| Layer | Technology |
|---|---|
| **AI Orchestration** | LangGraph `StateGraph` + Anthropic Claude Sonnet 4.6 |
| **Agent Protocol** | Tool use via `langchain-anthropic` · SSE streaming |
| **Backend API** | FastAPI · Uvicorn · Server-Sent Events |
| **Data Engine** | DuckDB (in-process) + BigQuery (GCP open data) |
| **Statistics** | scipy · statsmodels · pingouin |
| **Forecasting** | Prophet · N-HiTS (NeuralForecast) |
| **Visualization** | Plotly |
| **Frontend** | Streamlit (dark theme · Inter font) |
| **Session Memory** | Redis (Cloud Memorystore) with in-memory fallback |
| **Storage** | Google Cloud Storage |
| **Observability** | Langfuse v4 — LLM traces, token cost, latency |
| **Evaluation** | LLM-as-judge (grounding · precision · relevance · hallucination) |
| **Security** | API key auth · CORS lock · PII detection · rate limiting · audit trail |
| **CI/CD** | GitHub Actions + Cloud Build + Artifact Registry |
| **Runtime** | Cloud Run (serverless, min-instances=0) |

---

## Multi-Agent Design

The `RouterAgent` runs a LangGraph `StateGraph` with two nodes:

```
START → call_model → run_tools → call_model → ... → END
```

- **`call_model`** — invokes `ChatAnthropic` with all available tools bound; streams text tokens via `on_chat_model_stream`
- **`run_tools`** — dispatches tool calls to the appropriate specialist agent, accumulates SSE events
- **Conditional edge** — loops back to `call_model` if tools were called, otherwise ends

Each specialist agent is a pure function: receives a `DataLoader` + parameters, returns a structured result.

| Agent | Trigger | Output |
|---|---|---|
| `SQLAgent` | Aggregation, filtering, grouping | DataFrame + summary |
| `StatAgent` | Correlation, normality, t-test, regression | Statistical result + interpretation |
| `VizAgent` | Trend, distribution, comparison | Plotly JSON chart |
| `ForecastAgent` | "prévois", "forecast", "next N days" | Prophet/N-HiTS forecast chart |

---

## Security

| Control | Implementation |
|---|---|
| **API authentication** | `X-Api-Key` header required on all write/analyze endpoints |
| **CORS** | Locked to frontend URL in production (`ALLOWED_ORIGINS` env var) |
| **PII detection** | 40+ regex patterns (FR + EN) scan column names on upload — RGPD warning returned |
| **Rate limiting** | Token bucket: 20 analyze requests/minute per IP |
| **Audit trail** | Every connect/upload/analyze logged to Cloud Logging with IP + session |

---

## Observability

Langfuse traces every LangGraph run automatically via `CallbackHandler`:
- LLM call latency and token usage per node
- Tool execution timing
- LLM-as-judge eval scores (grounding, precision, relevance, hallucination) attached to traces

Run the evaluation suite:

```bash
python tests/eval/run_eval.py              # all 5 golden questions
python tests/eval/run_eval.py --question 2 # single question
pytest tests/eval/ -v -m eval              # via pytest
```

---

## GCP Open Datasets

| Dataset | Description |
|---|---|
| **Chicago Taxi Trips** | 40M+ trips (2013–2023) — fare, duration, geolocation |
| **New York Citi Bike** | Bike-share trips NYC — duration, stations, subscriber type |
| **Austin Bikeshare** | Bike-share trips Austin, Texas |
| **World Bank Education** | Global education indicators (literacy, enrollment) |

---

## Quick Start (local)

```bash
# 1. Clone & install
git clone https://github.com/Senstat/statiq.git
cd statiq
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,frontend]"

# 2. Configure
cp .env.example .env
# Set: ANTHROPIC_API_KEY, GCP_PROJECT_ID, GCS_BUCKET_NAME

# 3. Start Redis (optional — in-memory fallback used if not running)
docker compose up -d redis

# 4. Run
uvicorn backend.main:app --reload --port 8080 &
streamlit run frontend/app.py --server.port 8503
```

Open http://localhost:8503

---

## Deploy to GCP

```bash
# 1. Bootstrap infrastructure (one-time — creates Redis, VPC connector, secrets, SA)
bash infra/setup_gcp.sh <PROJECT_ID> europe-west1 <GITHUB_USER>

# 2. Authenticate Docker with Artifact Registry
gcloud auth configure-docker europe-west1-docker.pkg.dev

# 3. Push to main — Cloud Build deploys automatically
git push origin main
```

The Cloud Build pipeline (`infra/cloudbuild.yaml`) runs:
1. Unit tests (pytest)
2. Build backend + frontend images (`linux/amd64`)
3. Push to Artifact Registry
4. Deploy backend → deploy frontend
5. Smoke test (`/health`)

---

## Project Structure

```
statiq/
├── backend/
│   ├── main.py                  # FastAPI app, all endpoints
│   ├── config.py                # Pydantic settings (env-driven)
│   ├── security.py              # API key auth, PII detection, rate limiter, audit
│   ├── agents/
│   │   ├── base.py              # DataLoader — BigQuery + DuckDB
│   │   ├── router.py            # LangGraph orchestrator
│   │   ├── sql_agent.py         # SQL execution
│   │   ├── stat_agent.py        # Statistical analysis
│   │   ├── viz_agent.py         # Plotly chart generation
│   │   └── forecast_agent.py    # Time-series forecasting
│   ├── eval/
│   │   └── judge.py             # LLM-as-judge (EvalScore, LLMJudge)
│   ├── tools/
│   │   └── definitions.py       # Tool schemas for Claude
│   ├── memory/
│   │   └── session.py           # Redis session manager
│   ├── observability/
│   │   └── langfuse_client.py   # Langfuse v4 integration
│   └── models/
│       └── schemas.py           # Pydantic models
├── frontend/
│   └── app.py                   # Streamlit UI (dark theme)
├── infra/
│   ├── cloudbuild.yaml          # Cloud Build CI/CD pipeline
│   ├── setup_gcp.sh             # One-time GCP bootstrap script
│   ├── trigger.json             # Cloud Build trigger config
│   └── gcs_lifecycle.json       # GCS auto-cleanup policy
├── tests/
│   ├── unit/                    # Agent unit tests (11 tests)
│   └── eval/                    # LLM-as-judge golden set (5 questions)
├── .github/
│   └── workflows/ci.yml         # GitHub Actions — lint, type-check, test, docker build
├── .streamlit/
│   └── config.toml              # Streamlit dark theme
├── docker-compose.yml           # Local dev (Redis + backend + frontend)
├── Dockerfile.backend           # Multi-stage, linux/amd64
├── Dockerfile.frontend          # Cloud Run compatible (PORT env var)
└── pyproject.toml               # Dependencies + pytest config
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `GCP_PROJECT_ID` | Prod | GCP project ID |
| `GCS_BUCKET_NAME` | Prod | GCS bucket for file uploads |
| `REDIS_HOST` | Prod | Cloud Memorystore IP |
| `API_KEY` | Prod | Backend API key (`X-Api-Key` header) |
| `ALLOWED_ORIGINS` | Prod | Comma-separated allowed CORS origins |
| `LANGFUSE_PUBLIC_KEY` | Optional | Langfuse observability |
| `LANGFUSE_SECRET_KEY` | Optional | Langfuse observability |
| `APP_ENV` | No | `development` \| `production` |

---

## Running Tests

```bash
# Unit tests (fast, no API calls)
pytest tests/unit/ -v --cov=backend

# LLM-as-judge evaluation (requires ANTHROPIC_API_KEY)
pytest tests/eval/ -v -m eval
```

---

## Sample Questions

```
"Quelle est la durée moyenne d'un trajet par heure de la journée ?"
"Y a-t-il une corrélation entre la distance et le pourboire ?"
"Montre l'évolution mensuelle du nombre de trajets en 2022."
"Prévois le nombre de trajets pour les 30 prochains jours."
"Compare la distribution des tarifs le weekend vs la semaine."
"Quelles zones génèrent le plus de revenus ?"
```

---

## License

MIT — Built with [Anthropic Claude](https://anthropic.com) · [LangGraph](https://langchain-ai.github.io/langgraph) · [GCP](https://cloud.google.com)
