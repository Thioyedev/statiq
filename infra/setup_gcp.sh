#!/usr/bin/env bash
# setup_gcp.sh — Bootstrap StatIQ infrastructure on GCP
# Usage: ./infra/setup_gcp.sh <PROJECT_ID> [REGION] [GITHUB_USER]
set -euo pipefail

PROJECT_ID="${1:?Usage: ./infra/setup_gcp.sh <PROJECT_ID> [REGION] [GITHUB_USER]}"
REGION="${2:-europe-west1}"
GITHUB_USER="${3:-}"
BUCKET_NAME="${PROJECT_ID}-statiq-data"
SA_NAME="statiq-backend-sa"
REGISTRY_NAME="statiq"
REDIS_INSTANCE="statiq-redis"
VPC_CONNECTOR="statiq-connector"

echo "Setting up StatIQ on GCP project: ${PROJECT_ID} (${REGION})"

gcloud config set project "${PROJECT_ID}"
PROJECT_NUM=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')

# ── Enable APIs ───────────────────────────────────────────────────────────────
echo "Enabling GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  redis.googleapis.com \
  vpcaccess.googleapis.com \
  iam.googleapis.com \
  --quiet

# ── Artifact Registry ─────────────────────────────────────────────────────────
echo "Creating Artifact Registry..."
gcloud artifacts repositories create "${REGISTRY_NAME}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="StatIQ Docker images" \
  2>/dev/null || echo "  Registry already exists"

# Grant Cloud Build SA write access
gcloud artifacts repositories add-iam-policy-binding "${REGISTRY_NAME}" \
  --location="${REGION}" \
  --member="serviceAccount:${PROJECT_NUM}@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.writer" \
  --quiet

# ── GCS bucket ───────────────────────────────────────────────────────────────
echo "Creating GCS bucket..."
gsutil mb -p "${PROJECT_ID}" -c STANDARD -l "${REGION}" "gs://${BUCKET_NAME}" \
  2>/dev/null || echo "  Bucket already exists"
gsutil lifecycle set infra/gcs_lifecycle.json "gs://${BUCKET_NAME}"

# ── Service account ───────────────────────────────────────────────────────────
echo "Creating service account..."
gcloud iam service-accounts create "${SA_NAME}" \
  --project="${PROJECT_ID}" \
  --display-name="StatIQ Backend Service Account" \
  2>/dev/null || \
  gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" > /dev/null \
  || (echo "ERROR: could not create or find service account ${SA_EMAIL}" && exit 1)

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

ROLES=(
  "roles/bigquery.dataViewer"
  "roles/bigquery.jobUser"
  "roles/storage.objectAdmin"
  "roles/secretmanager.secretAccessor"
  "roles/redis.viewer"
)

for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --quiet
done

# ── Secret Manager ────────────────────────────────────────────────────────────
echo "Creating secrets in Secret Manager..."

_create_secret() {
  local name="$1" value="$2"
  echo -n "${value}" | gcloud secrets create "${name}" --data-file=- --quiet 2>/dev/null || \
  echo -n "${value}" | gcloud secrets versions add "${name}" --data-file=-
}

echo "Enter your Anthropic API key:"
read -rs ANTHROPIC_KEY
echo
_create_secret "anthropic-api-key" "${ANTHROPIC_KEY}"
_create_secret "gcp-project-id"    "${PROJECT_ID}"
_create_secret "gcs-bucket-name"   "${BUCKET_NAME}"

echo "Enter your Langfuse public key (leave blank to skip):"
read -rs LANGFUSE_PK
echo
if [ -n "${LANGFUSE_PK}" ]; then
  echo "Enter your Langfuse secret key:"
  read -rs LANGFUSE_SK
  echo
  _create_secret "langfuse-public-key" "${LANGFUSE_PK}"
  _create_secret "langfuse-secret-key" "${LANGFUSE_SK}"
  _create_secret "langfuse-host"       "https://cloud.langfuse.com"
fi

# ── Cloud Memorystore (Redis) ─────────────────────────────────────────────────
echo "Creating Redis instance (Cloud Memorystore)..."
gcloud redis instances create "${REDIS_INSTANCE}" \
  --size=1 \
  --region="${REGION}" \
  --redis-version=redis_7_0 \
  --tier=basic \
  2>/dev/null || echo "  Redis instance already exists"

REDIS_HOST=$(gcloud redis instances describe "${REDIS_INSTANCE}" \
  --region="${REGION}" --format="value(host)")
echo "  Redis host: ${REDIS_HOST}"

# ── VPC Access Connector (needed for Cloud Run → Redis) ──────────────────────
echo "Creating VPC Access Connector..."
gcloud compute networks vpc-access connectors create "${VPC_CONNECTOR}" \
  --region="${REGION}" \
  --range="10.8.0.0/28" \
  2>/dev/null || echo "  Connector already exists"

VPC_CONNECTOR_FULL="projects/${PROJECT_ID}/locations/${REGION}/connectors/${VPC_CONNECTOR}"

# ── Cloud Build trigger ───────────────────────────────────────────────────────
if [ -n "${GITHUB_USER}" ]; then
  echo "Creating Cloud Build trigger..."
  gcloud builds triggers create github \
    --repo-name="statiq" \
    --repo-owner="${GITHUB_USER}" \
    --branch-pattern="^main$" \
    --build-config="infra/cloudbuild.yaml" \
    --substitutions="_REGION=${REGION},_REDIS_HOST=${REDIS_HOST},_VPC_CONNECTOR=${VPC_CONNECTOR_FULL}" \
    --description="StatIQ CI/CD — deploy on push to main" \
    2>/dev/null || echo "  Trigger already exists or needs manual setup in Cloud Console"
else
  echo "  Skipping Cloud Build trigger (no GITHUB_USER provided)."
  echo "  Create it manually at https://console.cloud.google.com/cloud-build/triggers"
  echo "  Set substitutions: _REGION=${REGION} _REDIS_HOST=${REDIS_HOST} _VPC_CONNECTOR=${VPC_CONNECTOR_FULL}"
fi

echo ""
echo "Infrastructure ready!"
echo ""
echo "Next steps:"
echo "  1. Add REDIS_HOST to your .env:"
echo "     REDIS_HOST=${REDIS_HOST}"
echo ""
echo "  2. Push to main (or trigger Cloud Build manually) to deploy:"
echo "     git push origin main"
echo ""
echo "  3. First build URL:"
echo "     https://console.cloud.google.com/cloud-build/builds?project=${PROJECT_ID}"
