import os
from functools import lru_cache
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env.local first (local dev), then .env (production/fallback).
# Values in .env.local take precedence.
load_dotenv(".env.local", override=False)
load_dotenv(".env", override=False)

_env_files = (".env.local", ".env") if os.path.exists(".env.local") else (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_env_files, env_file_encoding="utf-8", extra="ignore")

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-6"

    # GCP (optional for local CSV-only mode)
    gcp_project_id: str = ""
    gcs_bucket_name: str = ""
    gcp_region: str = "europe-west1"

    # Redis (Cloud Memorystore on GCP)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_ttl_seconds: int = 7200  # 2h session

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    max_file_size_mb: int = 100

    # Security
    api_key: str = ""                          # empty = auth disabled (local dev)
    allowed_origins: str = "*"                 # comma-separated list or "*"
    rate_limit_per_minute: int = 20            # analyze endpoint cap per IP

    # Langfuse (optional — observability)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # BigQuery public datasets (demo)
    bq_demo_project: str = "bigquery-public-data"
    bq_demo_dataset: str = "chicago_taxi_trips"
    bq_demo_table: str = "taxi_trips"


@lru_cache
def get_settings() -> Settings:
    return Settings()
