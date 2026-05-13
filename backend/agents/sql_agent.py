"""SQLAgent — executes SQL queries via DataLoader and returns structured results."""
from __future__ import annotations
import hashlib
import structlog
from backend.agents.base import DataLoader
from backend.models.schemas import SQLResult

log = structlog.get_logger()


class SQLAgent:
    def __init__(self, loader: DataLoader) -> None:
        self.loader = loader

    def run(self, sql: str, purpose: str = "") -> SQLResult:
        log.info("sql_agent.run", purpose=purpose, sql_preview=sql[:120])
        df, elapsed_ms = self.loader.run_sql(sql)

        return SQLResult(
            query=sql,
            rows=df.head(500).to_dict(orient="records"),  # cap payload
            row_count=len(df),
            columns=list(df.columns),
            execution_time_ms=round(elapsed_ms, 1),
        )
