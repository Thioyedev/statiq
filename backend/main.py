"""
StatIQ — FastAPI Backend
Endpoints:
  POST /api/datasets/connect   → connect to BigQuery table or GCS CSV
  POST /api/datasets/upload    → upload CSV directly
  GET  /api/datasets/profile   → column profile of current dataset
  POST /api/analyze            → non-streaming analysis
  GET  /api/analyze/stream     → SSE streaming analysis
  GET  /api/sessions/{id}      → conversation history
  DELETE /api/sessions/{id}    → clear session
  GET  /health                 → liveness probe
"""
from __future__ import annotations
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.cloud import storage

from backend.agents.base import DataLoader
from backend.agents.router import RouterAgent
from backend.config import get_settings
from backend.memory.session import session_memory
from backend.observability.langfuse_client import flush as langfuse_flush
from backend.models.schemas import (
    AnalyzeRequest,
    DataSourceType,
)
from backend.security import audit_log, check_pii_columns, require_api_key, require_rate_limit

log = structlog.get_logger()
settings = get_settings()

# ── App factory ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("statiq.startup", env=settings.app_env)
    yield
    await session_memory.close()
    langfuse_flush()
    log.info("statiq.shutdown")


app = FastAPI(
    title="StatIQ API",
    description="AI-Powered Multi-Agent Analytics Platform",
    version="0.1.0",
    lifespan=lifespan,
)

_origins = (
    ["*"] if settings.allowed_origins == "*"
    else [o.strip() for o in settings.allowed_origins.split(",")]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_MAX_SESSIONS = 100
_loaders: OrderedDict[str, DataLoader] = OrderedDict()


def _get_loader(session_id: str) -> DataLoader:
    if session_id not in _loaders:
        if len(_loaders) >= _MAX_SESSIONS:
            _loaders.popitem(last=False)  # evict oldest
        _loaders[session_id] = DataLoader()
    else:
        _loaders.move_to_end(session_id)  # mark as recently used
    return _loaders[session_id]


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ── Dataset endpoints ─────────────────────────────────────────────────────────

@app.post("/api/datasets/connect", dependencies=[Depends(require_api_key)])
async def connect_dataset(
    request: Request,
    session_id: str = Form(default_factory=lambda: str(uuid.uuid4())),
    source: DataSourceType = Form(DataSourceType.BIGQUERY),
    dataset_ref: str = Form(
        default="bigquery-public-data.chicago_taxi_trips.taxi_trips",
        description="Fully-qualified BigQuery table or GCS object path",
    ),
):
    """Connect to a BigQuery table or GCS CSV."""
    audit_log(request, "dataset.connect", source=source.value, ref=dataset_ref, session=session_id)
    loader = _get_loader(session_id)
    try:
        if source == DataSourceType.BIGQUERY:
            loader.setup_bigquery(dataset_ref)
        elif source == DataSourceType.GCS:
            loader.setup_gcs_csv(dataset_ref)
        else:
            raise HTTPException(400, "Unsupported source type")

        ctx = {"source": source.value, "ref": dataset_ref}
        await session_memory.set_dataset_context(session_id, ctx)
        profile = loader.profile()
        return {
            "session_id": session_id,
            "status": "connected",
            "profile": profile.model_dump(),
        }
    except Exception as exc:
        log.error("connect.error", error=str(exc))
        raise HTTPException(500, str(exc)) from exc


ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def _read_file_to_df(content: bytes, filename: str):
    """Parse CSV or Excel bytes into a pandas DataFrame."""
    import io
    import pandas as pd

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(io.BytesIO(content))
    return pd.read_csv(io.BytesIO(content))


@app.post("/api/datasets/upload", dependencies=[Depends(require_api_key)])
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(default_factory=lambda: str(uuid.uuid4())),
):
    """Upload a CSV or Excel file, store on GCS, load into DuckDB."""
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")

    if file.size and file.size > settings.max_file_size_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {settings.max_file_size_mb} MB)")

    content = await file.read()
    gcs_object = f"uploads/{session_id}/{file.filename}"

    # Upload to GCS
    try:
        gcs_client = storage.Client(project=settings.gcp_project_id)
        bucket = gcs_client.bucket(settings.gcs_bucket_name)
        blob = bucket.blob(gcs_object)
        blob.upload_from_string(content, content_type=file.content_type or "application/octet-stream")
    except Exception as exc:
        log.warning("gcs.upload_failed", error=str(exc))
        # Fallback: load directly into DuckDB without GCS
        loader = _get_loader(session_id)
        df = _read_file_to_df(content, file.filename)
        loader.duck.register("df", df)
        loader._loaded_table = "df"
        loader._source_type = DataSourceType.CSV_UPLOAD
        loader._dataset_ref = file.filename
    else:
        loader = _get_loader(session_id)
        # For Excel files, convert to in-memory DataFrame (GCS path is CSV-only)
        if ext in (".xlsx", ".xls"):
            df = _read_file_to_df(content, file.filename)
            loader.duck.register("df", df)
            loader._loaded_table = "df"
            loader._source_type = DataSourceType.CSV_UPLOAD
            loader._dataset_ref = file.filename
        else:
            loader.setup_gcs_csv(gcs_object)

    ctx = {"source": DataSourceType.CSV_UPLOAD.value, "ref": file.filename}
    await session_memory.set_dataset_context(session_id, ctx)
    profile = loader.profile()

    # PII detection — warn but don't block (audited)
    col_names = [c["name"] for c in profile.model_dump().get("columns", [])]
    pii_cols = check_pii_columns(col_names)
    if pii_cols:
        audit_log(request, "pii.detected", file=file.filename, columns=pii_cols, session=session_id)
        log.warning("pii.detected", file=file.filename, columns=pii_cols)

    audit_log(request, "dataset.upload", file=file.filename, rows=profile.n_rows, session=session_id)
    result = {"session_id": session_id, "gcs_object": gcs_object, "profile": profile.model_dump()}
    if pii_cols:
        result["pii_warning"] = f"Colonnes sensibles détectées : {', '.join(pii_cols)}. Vérifiez la conformité RGPD avant de partager cette analyse."
    return result


@app.get("/api/datasets/profile")
async def get_profile(session_id: str):
    loader = _get_loader(session_id)
    if loader._source_type is None:
        raise HTTPException(404, "No dataset connected for this session")
    return loader.profile().model_dump()


# ── Analysis endpoints ────────────────────────────────────────────────────────

@app.get("/api/analyze/stream", dependencies=[Depends(require_api_key), Depends(require_rate_limit)])
async def analyze_stream(request: Request, session_id: str, question: str):
    """Server-Sent Events streaming endpoint."""
    audit_log(request, "analyze.stream", session=session_id, question=question[:120])
    loader = _get_loader(session_id)
    if loader._source_type is None:
        raise HTTPException(404, "No dataset connected. Call /api/datasets/connect first.")

    agent = RouterAgent(loader)

    async def event_generator():
        async for event in agent.analyze_stream(question, session_id):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/analyze", dependencies=[Depends(require_api_key), Depends(require_rate_limit)])
async def analyze(request: Request, req: AnalyzeRequest):
    """Non-streaming analysis — collects all events and returns JSON."""
    session_id = req.session_id

    # Connect dataset if not already connected
    loader = _get_loader(session_id)
    if loader._source_type is None:
        if req.dataset_ref:
            loader.setup_bigquery(req.dataset_ref)
        elif req.file_id:
            loader.setup_gcs_csv(req.file_id)
        else:
            # Default to Chicago taxi open data
            default_ref = f"{settings.bq_demo_project}.{settings.bq_demo_dataset}.{settings.bq_demo_table}"
            loader.setup_bigquery(default_ref)

    agent = RouterAgent(loader)
    events: list[dict] = []
    async for event in agent.analyze_stream(req.question, session_id):
        events.append(event.model_dump())

    return {"session_id": session_id, "events": events}


# ── Session endpoints ─────────────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    history = await session_memory.get_history(session_id)
    ctx = await session_memory.get_dataset_context(session_id)
    return {"session_id": session_id, "history": history, "dataset_context": ctx}


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    await session_memory.clear_history(session_id)
    _loaders.pop(session_id, None)
    return {"status": "cleared"}


# ── GCP open data catalog ─────────────────────────────────────────────────────

OPEN_DATASETS = [
    {
        "id": "chicago_taxi",
        "name": "Chicago Taxi Trips",
        "ref": "bigquery-public-data.chicago_taxi_trips.taxi_trips",
        "description": "40M+ trajets de taxi à Chicago (2013–2023). Prix, durée, géolocalisation.",
        "sample_questions": [
            "Quelle est la durée moyenne d'un trajet par heure de la journée ?",
            "Montre l'évolution du nombre de trajets par mois en 2022.",
            "Y a-t-il une corrélation entre la distance et le pourboire ?",
            "Prévois le nombre de trajets pour les 30 prochains jours.",
        ],
    },
    {
        "id": "new_york_citibike",
        "name": "New York Citi Bike",
        "ref": "bigquery-public-data.new_york_citibike.citibike_trips",
        "description": "Trajets vélos en libre-service à New York. Durée, stations, type d'abonné.",
        "sample_questions": [
            "Quelles sont les 10 stations de départ les plus populaires ?",
            "Compare la durée de trajet entre abonnés et utilisateurs occasionnels.",
            "Quelle est la tendance hebdomadaire du nombre de trajets ?",
        ],
    },
    {
        "id": "austin_bikeshare",
        "name": "Austin Bikeshare",
        "ref": "bigquery-public-data.austin_bikeshare.bikeshare_trips",
        "description": "Données de vélos partagés à Austin, Texas.",
        "sample_questions": [
            "Quelle est la durée médiane par type de vélo ?",
            "Distribution des durées de trajet.",
        ],
    },
    {
        "id": "world_bank_education",
        "name": "World Bank — Education",
        "ref": "bigquery-public-data.world_bank_intl_education.international_education",
        "description": "Indicateurs d'éducation mondiaux (Banque Mondiale).",
        "sample_questions": [
            "Compare le taux d'alphabétisation entre régions.",
            "Tendance de la scolarisation primaire en Afrique sub-saharienne.",
        ],
    },
]


@app.get("/api/datasets/catalog")
async def get_catalog():
    """List available GCP open datasets with sample questions."""
    return {"datasets": OPEN_DATASETS}


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8080, reload=True)
