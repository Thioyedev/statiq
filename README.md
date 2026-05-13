# StatIQ — AI-Powered Multi-Agent Analytics Platform

[![CI](https://github.com/YOUR_USER/statiq/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USER/statiq/actions)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![GCP](https://img.shields.io/badge/GCP-Cloud%20Run-orange.svg)](https://cloud.google.com/run)

> Ask your data anything. Get publication-ready statistical insights powered by a multi-agent AI system.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        GCP Cloud                            │
│                                                             │
│  Streamlit UI ──► API Gateway ──► FastAPI (Cloud Run)       │
│                                        │                    │
│                          ┌─────────────┼──────────────┐     │
│                          ▼             ▼              ▼     │
│                      SQLAgent     StatAgent       VizAgent  │
│                          │        ForecastAgent             │
│                          └─────────────┬──────────────┘     │
│                                        │                    │
│                               Anthropic API                 │
│                           (tool_use orchestration)          │
│                                                             │
│  GCS (uploads) · BigQuery (opendata) · Redis (sessions)     │
└─────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|---|---|
| **AI Orchestration** | Anthropic Claude (tool_use loop) |
| **Backend API** | FastAPI + SSE streaming |
| **Data Engine** | DuckDB (local) + BigQuery (cloud) |
| **Statistics** | scipy · statsmodels · pingouin |
| **Forecasting** | Prophet |
| **Visualization** | Plotly |
| **Frontend** | Streamlit |
| **Memory** | Redis (Cloud Memorystore) |
| **Storage** | Google Cloud Storage |
| **CI/CD** | Cloud Build + Artifact Registry |
| **Runtime** | Cloud Run (serverless containers) |

## GCP Open Datasets included

- **Chicago Taxi Trips** — 40M+ trajets (2013–2023)
- **New York Citi Bike** — trajets vélos NYC
- **Austin Bikeshare** — trajets vélos Austin
- **World Bank Education** — indicateurs éducatifs mondiaux

## Quick Start (local)

```bash
# 1. Clone & setup
git clone https://github.com/YOUR_USER/statiq.git
cd statiq
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,frontend]"

# 2. Configure
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY, GCP_PROJECT_ID

# 3. Place your GCP service account key
mkdir credentials
cp ~/Downloads/your-sa-key.json credentials/sa-key.json

# 4. Start services
docker compose up -d redis
uvicorn backend.main:app --reload --port 8080 &
streamlit run frontend/app.py
```

Open http://localhost:8501

## Deploy to GCP

```bash
# 1. Bootstrap infrastructure (one-time)
chmod +x infra/setup_gcp.sh
./infra/setup_gcp.sh YOUR_PROJECT_ID europe-west1

# 2. Authenticate Docker with Artifact Registry
gcloud auth configure-docker europe-west1-docker.pkg.dev

# 3. Push to main → Cloud Build triggers automatically
git push origin main
```

## Project Structure

```
statiq/
├── backend/
│   ├── main.py              # FastAPI app + all endpoints
│   ├── config.py            # Pydantic settings
│   ├── agents/
│   │   ├── base.py          # DataLoader (BigQuery + DuckDB)
│   │   ├── router.py        # Orchestrator (tool_use loop)
│   │   ├── sql_agent.py     # SQL execution
│   │   ├── stat_agent.py    # Statistical analysis
│   │   ├── viz_agent.py     # Plotly chart generation
│   │   └── forecast_agent.py # Prophet time-series
│   ├── tools/
│   │   └── definitions.py   # JSON schema tool specs for Claude
│   ├── memory/
│   │   └── session.py       # Redis session manager
│   └── models/
│       └── schemas.py       # Pydantic models
├── frontend/
│   └── app.py               # Streamlit UI
├── infra/
│   ├── cloudbuild.yaml      # CI/CD pipeline
│   ├── setup_gcp.sh         # Infrastructure bootstrap
│   └── gcs_lifecycle.json   # GCS cleanup policy
├── tests/
│   └── unit/                # pytest unit tests
├── .github/
│   └── workflows/ci.yml     # GitHub Actions
├── docker-compose.yml       # Local dev
├── Dockerfile.backend
├── Dockerfile.frontend
└── pyproject.toml
```

## Sample Questions

```
"Quelle est la durée moyenne d'un trajet par heure de la journée ?"
"Y a-t-il une corrélation entre la distance et le pourboire ?"
"Montre l'évolution mensuelle du nombre de trajets en 2022."
"Prévois le nombre de trajets pour les 30 prochains jours."
"Quelles zones géographiques génèrent le plus de revenus ?"
"Compare la distribution des tarifs le weekend vs la semaine."
```

## Running Tests

```bash
pytest tests/unit/ -v --cov=backend
```

## CV Summary

> **StatIQ — Multi-Agent Analytics Platform** *(GenAI · MLOps · GCP)*
> Plateforme d'analyse statistique conversationnelle pilotée par LLM. Architecture multi-agents avec orchestration Anthropic tool_use (SQL/DuckDB, statsmodels, Plotly, Prophet). Backend FastAPI containerisé sur Cloud Run, CI/CD Cloud Build, mémoire Redis, stockage GCS/BigQuery opendata.
> *Python · FastAPI · Anthropic SDK · DuckDB · BigQuery · GCP (Cloud Run · Cloud Build · Artifact Registry · GCS · Memorystore)*

---

MIT License · Built with Anthropic Claude
