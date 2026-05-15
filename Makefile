.PHONY: dev backend frontend stop

VENV := .venv/bin

# ── Load local env ────────────────────────────────────────────────────────────
include .env.local
export

# ── Start backend (blocking) ──────────────────────────────────────────────────
backend:
	$(VENV)/uvicorn backend.main:app --reload --port 8080

# ── Start frontend (blocking) ─────────────────────────────────────────────────
frontend:
	$(VENV)/streamlit run frontend/app.py --server.port 8503

# ── Kill both ports ───────────────────────────────────────────────────────────
stop:
	-lsof -ti:8080,8503 | xargs kill -9
	@echo "Stopped backend (8080) and frontend (8503)"
