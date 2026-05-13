"""
DataLoader : abstraction over BigQuery & DuckDB (local CSV).
All agents go through this to get a pandas DataFrame or run SQL.
"""
from __future__ import annotations
import hashlib
import time
import duckdb
import pandas as pd
import structlog
from google.cloud import bigquery, storage
from backend.config import get_settings
from backend.models.schemas import DataSourceType, DatasetProfile, ColumnProfile

log = structlog.get_logger()
settings = get_settings()


class DataLoader:
    """
    Unified data access layer.
    - BigQuery  → google-cloud-bigquery
    - CSV/GCS   → download to local, load into DuckDB in-memory
    """

    def __init__(self) -> None:
        self._bq_client: bigquery.Client | None = None
        self._gcs_client: storage.Client | None = None
        self._duckdb_conn: duckdb.DuckDBPyConnection | None = None
        self._loaded_table: str | None = None   # alias in DuckDB
        self._source_type: DataSourceType | None = None
        self._dataset_ref: str | None = None
        self._context_cache: str | None = None

    # ── GCP clients (lazy) ────────────────────────────────────────────────────

    @property
    def bq(self) -> bigquery.Client:
        if self._bq_client is None:
            self._bq_client = bigquery.Client(project=settings.gcp_project_id)
        return self._bq_client

    @property
    def gcs(self) -> storage.Client:
        if self._gcs_client is None:
            self._gcs_client = storage.Client(project=settings.gcp_project_id)
        return self._gcs_client

    @property
    def duck(self) -> duckdb.DuckDBPyConnection:
        if self._duckdb_conn is None:
            self._duckdb_conn = duckdb.connect(database=":memory:")
        return self._duckdb_conn

    # ── Setup ─────────────────────────────────────────────────────────────────

    def setup_bigquery(self, table_ref: str) -> None:
        """
        table_ref e.g. 'bigquery-public-data.chicago_taxi_trips.taxi_trips'
        We download a reasonable sample into DuckDB for fast local queries,
        but also keep the BQ client for large aggregation queries.
        """
        self._source_type = DataSourceType.BIGQUERY
        self._dataset_ref = table_ref
        self._loaded_table = "bq_data"
        log.info("bigquery.setup", table=table_ref)

    def setup_gcs_csv(self, gcs_object: str) -> None:
        """Download CSV from GCS bucket and load into DuckDB."""
        self._source_type = DataSourceType.GCS
        self._dataset_ref = gcs_object
        log.info("gcs.loading", object=gcs_object)

        bucket = self.gcs.bucket(settings.gcs_bucket_name)
        blob = bucket.blob(gcs_object)
        csv_bytes = blob.download_as_bytes()

        import io
        df = pd.read_csv(io.BytesIO(csv_bytes))
        self.duck.register("df", df)
        self._loaded_table = "df"
        log.info("gcs.loaded", rows=len(df), cols=len(df.columns))

    # ── SQL execution ─────────────────────────────────────────────────────────

    def run_sql(self, sql: str, max_rows: int = 10_000) -> tuple[pd.DataFrame, float]:
        """
        Run SQL and return (DataFrame, execution_time_ms).
        Automatically routes to BQ for aggregation-heavy queries,
        DuckDB for light queries on loaded sample.
        """
        t0 = time.perf_counter()

        if self._source_type == DataSourceType.BIGQUERY:
            # Replace table alias shorthand with fully-qualified name if needed
            resolved_sql = sql.replace("{{TABLE}}", self._dataset_ref or "")
            try:
                df = self.bq.query(resolved_sql).to_dataframe()
            except Exception as bq_err:
                log.warning("bigquery.fallback_to_duckdb", error=str(bq_err))
                sample = self._load_bq_sample()
                self.duck.register(self._loaded_table, sample)
                duck_sql = resolved_sql.replace(f"`{self._dataset_ref}`", self._loaded_table)
                df = self.duck.execute(duck_sql).df()
        else:
            df = self._duck_execute_with_date_fix(sql)

        elapsed = (time.perf_counter() - t0) * 1000
        if len(df) > max_rows:
            df = df.head(max_rows)
        return df, elapsed

    def _duck_execute_with_date_fix(self, sql: str) -> pd.DataFrame:
        """Execute DuckDB SQL, auto-fixing STRFTIME type errors on VARCHAR date columns."""
        import re
        try:
            return self.duck.execute(sql).df()
        except Exception as err:
            if "strftime" not in str(err).lower():
                raise
            # Replace STRFTIME(col, fmt) or STRFTIME(fmt, col) calls where
            # the date arg is a bare column name → wrap with TRY_CAST(col AS DATE)
            fixed = re.sub(
                r"STRFTIME\((['\"][^'\"]+['\"])\s*,\s*(\w+)\)",
                r"STRFTIME(TRY_CAST(\2 AS DATE), \1)",
                sql,
                flags=re.IGNORECASE,
            )
            fixed = re.sub(
                r"STRFTIME\((\w+)\s*,\s*(['\"][^'\"]+['\"])\)",
                r"STRFTIME(TRY_CAST(\1 AS DATE), \2)",
                fixed,
                flags=re.IGNORECASE,
            )
            if fixed == sql:
                raise
            log.info("duckdb.strftime_autofix", original=sql[:120], fixed=fixed[:120])
            return self.duck.execute(fixed).df()

    def _load_bq_sample(self, n: int = 50_000) -> pd.DataFrame:
        """Pull a sample from BigQuery for local DuckDB analysis."""
        sample_sql = f"""
            SELECT * FROM `{self._dataset_ref}`
            WHERE RAND() < 0.01
            LIMIT {n}
        """
        return self.bq.query(sample_sql).to_dataframe()

    # ── Profile ───────────────────────────────────────────────────────────────

    def profile(self) -> DatasetProfile:
        """Compute a quick column-level profile of the loaded dataset."""
        if self._source_type == DataSourceType.BIGQUERY:
            df_sample = self._load_bq_sample(n=5_000)
            self.duck.register("_profile_sample", df_sample)
            df = df_sample
        else:
            df = self.duck.execute(f"SELECT * FROM {self._loaded_table} LIMIT 5000").df()

        cols = []
        for col in df.columns:
            s = df[col]
            dtype = str(s.dtype)
            n_unique = s.nunique()
            null_pct = round(s.isna().mean() * 100, 2)
            sample = s.dropna().head(5).tolist()
            cp = ColumnProfile(
                name=col,
                dtype=dtype,
                null_pct=null_pct,
                n_unique=n_unique,
                sample_values=sample,
            )
            if pd.api.types.is_numeric_dtype(s):
                cp.min_val = float(s.min()) if not s.isna().all() else None
                cp.max_val = float(s.max()) if not s.isna().all() else None
                cp.mean = round(float(s.mean()), 4) if not s.isna().all() else None
                cp.std = round(float(s.std()), 4) if not s.isna().all() else None
            cols.append(cp)

        return DatasetProfile(
            name=self._dataset_ref or "dataset",
            source=self._source_type,
            n_rows=len(df),
            n_cols=len(df.columns),
            columns=cols,
        )

    def context_string(self) -> str:
        """Human-readable summary passed to Claude as dataset context. Cached per session."""
        if self._context_cache is not None:
            return self._context_cache
        profile = self.profile()
        lines = [
            f"Dataset: {profile.name}",
            f"Source: {profile.source.value}",
            f"Dimensions: ~{profile.n_rows:,} rows × {profile.n_cols} columns",
            "",
            "Columns:",
        ]
        for c in profile.columns:
            stats = f"  null={c.null_pct}%  unique={c.n_unique}"
            if c.mean is not None:
                stats += f"  mean={c.mean}  std={c.std}"
            lines.append(f"  • {c.name} ({c.dtype}){stats}")
            lines.append(f"    samples: {c.sample_values[:3]}")

        if self._source_type == DataSourceType.BIGQUERY:
            lines += [
                "",
                f"SQL table reference: `{self._dataset_ref}`",
                "Use fully-qualified name in SQL: `project.dataset.table`",
            ]
        else:
            lines += ["", "SQL table alias: 'df'  (DuckDB in-memory)"]

        self._context_cache = "\n".join(lines)
        return self._context_cache
