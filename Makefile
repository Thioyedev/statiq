.PHONY: help dev backend frontend stop \
        install install-dev \
        lint format typecheck test test-cov \
        docker-build docker-up docker-down docker-logs \
        hetzner-up hetzner-down hetzner-logs hetzner-pull \
        setup-env clean

VENV      := .venv/bin
COMPOSE   := docker compose
HETZNER   := docker compose -f docker-compose.hetzner.yml

# ── Load local env (optional) ─────────────────────────────────────────────────
-include .env.local
export

# ─────────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────────
help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────
install: ## Installer les dépendances (prod)
	pip install -e "."

install-dev: ## Installer les dépendances (dev + tests)
	pip install -e ".[dev]"

setup-env: ## Copier .env.example → .env.local
	@[ -f .env.local ] && echo ".env.local existe déjà" || (cp .env.example .env.local && echo ".env.local créé — remplis les secrets")

# ─────────────────────────────────────────────────────────────────────────────
# Développement local (sans Docker)
# ─────────────────────────────────────────────────────────────────────────────
backend: ## Démarrer le backend FastAPI (hot reload)
	$(VENV)/uvicorn backend.main:app --reload --port 8080

frontend: ## Démarrer le frontend Streamlit
	$(VENV)/streamlit run frontend/app.py --server.port 8503

stop: ## Arrêter backend (8080) et frontend (8503)
	-lsof -ti:8080,8503 | xargs kill -9
	@echo "Stoppé backend (8080) et frontend (8503)"

# ─────────────────────────────────────────────────────────────────────────────
# Qualité du code
# ─────────────────────────────────────────────────────────────────────────────
lint: ## Vérifier le code (ruff)
	$(VENV)/ruff check backend/ tests/

format: ## Formatter le code (ruff)
	$(VENV)/ruff format backend/ tests/

typecheck: ## Vérification des types (mypy)
	$(VENV)/mypy backend/ --ignore-missing-imports

# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────
test: ## Lancer les tests unitaires
	ANTHROPIC_API_KEY=sk-ant-test GCP_PROJECT_ID=test GCS_BUCKET_NAME=test \
	PYTHONPATH=. $(VENV)/pytest tests/unit/ -v --tb=short

test-cov: ## Tests + rapport de couverture
	ANTHROPIC_API_KEY=sk-ant-test GCP_PROJECT_ID=test GCS_BUCKET_NAME=test \
	PYTHONPATH=. $(VENV)/pytest tests/unit/ -v --tb=short \
		--cov=backend --cov-report=term-missing --cov-report=html
	@echo "Rapport HTML : htmlcov/index.html"

check: lint typecheck test ## lint + typecheck + tests (CI complet en local)

# ─────────────────────────────────────────────────────────────────────────────
# Docker — développement local
# ─────────────────────────────────────────────────────────────────────────────
docker-build: ## Builder les images Docker (local)
	$(COMPOSE) build

docker-up: ## Démarrer tous les services (Docker Compose)
	$(COMPOSE) up -d
	@echo "Backend  → http://localhost:8080"
	@echo "Frontend → http://localhost:8501"

docker-down: ## Arrêter et supprimer les conteneurs
	$(COMPOSE) down

docker-logs: ## Suivre les logs (tous les services)
	$(COMPOSE) logs -f

docker-ps: ## Statut des conteneurs
	$(COMPOSE) ps

# ─────────────────────────────────────────────────────────────────────────────
# Hetzner — production
# ─────────────────────────────────────────────────────────────────────────────
hetzner-up: ## Démarrer Statiq sur Hetzner
	$(HETZNER) up -d --remove-orphans
	@echo "Statiq démarré sur Hetzner"

hetzner-down: ## Arrêter Statiq sur Hetzner
	$(HETZNER) down

hetzner-pull: ## Mettre à jour les images depuis GHCR
	$(HETZNER) pull

hetzner-restart: hetzner-pull hetzner-up ## Pull + redémarrage

hetzner-logs: ## Suivre les logs Hetzner
	$(HETZNER) logs -f

hetzner-ps: ## Statut des conteneurs Hetzner
	$(HETZNER) ps

hetzner-setup: ## Bootstrap initial du serveur (à lancer une seule fois)
	@echo "Lance : ssh root@65.109.143.85 'bash -s' < infra/hetzner/setup.sh"
	@read -p "Confirmer ? [y/N] " confirm && [ "$${confirm}" = "y" ] && \
		ssh root@65.109.143.85 'bash -s' < infra/hetzner/setup.sh

# ─────────────────────────────────────────────────────────────────────────────
# Nettoyage
# ─────────────────────────────────────────────────────────────────────────────
clean: ## Supprimer les fichiers temporaires
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "Nettoyé"
