"""
Security layer for StatIQ.

- API key authentication (X-Api-Key header)
- PII column detection on file uploads
- Rate limiting (in-memory token bucket per IP)
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from threading import Lock
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from backend.config import get_settings

log = structlog.get_logger()

# ── API key auth ──────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


def require_api_key(
    request: Request,
    key: Annotated[str | None, Depends(_api_key_header)] = None,
) -> None:
    settings = get_settings()
    if not settings.api_key:
        return  # auth disabled in local dev
    if key != settings.api_key:
        log.warning("auth.rejected", ip=request.client.host if request.client else "unknown")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


# ── Rate limiter (token bucket, in-memory) ────────────────────────────────────

class _TokenBucket:
    def __init__(self, rate: int) -> None:
        self._rate = rate          # max requests per minute
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        with self._lock:
            timestamps = self._buckets[key]
            # drop entries older than the window
            self._buckets[key] = [t for t in timestamps if now - t < window]
            if len(self._buckets[key]) >= self._rate:
                return False
            self._buckets[key].append(now)
            return True


_bucket = _TokenBucket(rate=20)  # default, overridden per-request from settings


def require_rate_limit(request: Request) -> None:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    _bucket._rate = settings.rate_limit_per_minute
    if not _bucket.allow(ip):
        log.warning("rate_limit.exceeded", ip=ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de requêtes. Limite : {settings.rate_limit_per_minute} analyses/minute.",
        )


# ── PII detection ─────────────────────────────────────────────────────────────

_PII_PATTERNS = [
    r"\bssn\b", r"\bnss\b", r"\bsocial.?security\b",
    r"\bpassport\b", r"\bpasseport\b",
    r"\bcredit.?card\b", r"\bcarte.?bancaire\b", r"\bcard.?number\b", r"\bpan\b",
    r"\bcvv\b", r"\bcvc\b",
    r"\biban\b", r"\bbic\b", r"\bswift\b",
    r"\biban\b",
    r"\bpassword\b", r"\bmot.?de.?passe\b", r"\bpwd\b",
    r"\bdate.?of.?birth\b", r"\bdob\b", r"\bdate.?naissance\b",
    r"\bbiometric\b", r"\bbiom[eé]trique\b",
    r"\bip.?address\b", r"\badresse.?ip\b",
    r"\bgps\b", r"\blatitude\b", r"\blongitude\b",
    r"\bsalaire\b", r"\bsalary\b", r"\brevenu\b", r"\bincome\b",
    r"\bdiagno[sc]\w+\b", r"\bpatient\b", r"\bmedical\b",
    r"\bemail\b", r"\be.?mail\b", r"\bcourriel\b",
    r"\bphone\b", r"\bt[eé]l[eé]phone\b", r"\bmobile\b", r"\bcel+\b",
    r"\baddress\b", r"\badresse\b",
    r"\bfirst.?name\b", r"\blast.?name\b", r"\bpr[eé]nom\b", r"\bnom\b",
    r"\bfull.?name\b", r"\bnom.?complet\b",
    r"\bnational.?id\b", r"\bidentifiant\b", r"\bcin\b",
]

_PII_RE = [re.compile(p, re.IGNORECASE) for p in _PII_PATTERNS]


def check_pii_columns(columns: list[str]) -> list[str]:
    """Return list of column names that match PII patterns."""
    flagged = []
    for col in columns:
        if any(rx.search(col) for rx in _PII_RE):
            flagged.append(col)
    return flagged


def audit_log(request: Request, action: str, **kwargs) -> None:
    """Structured audit entry — picked up by Cloud Logging."""
    log.info(
        "audit",
        action=action,
        ip=request.client.host if request.client else "unknown",
        **kwargs,
    )
