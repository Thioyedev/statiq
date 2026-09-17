#!/usr/bin/env bash
# One-time server bootstrap for Statiq on Hetzner CX31
# Run as root: bash infra/hetzner/setup.sh

set -euo pipefail

DEPLOY_DIR=/opt/statiq
DEPLOY_USER=deploy
REPO=https://github.com/senstat/statiq.git
BRANCH=deploy/hetzner

echo "==> Installing Docker & tools..."
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git ufw

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo "==> Creating deploy user..."
id -u "$DEPLOY_USER" &>/dev/null || useradd -m -s /bin/bash "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"

mkdir -p "/home/$DEPLOY_USER/.ssh"
[ -f /root/.ssh/authorized_keys ] && \
  cp /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
chmod 700 "/home/$DEPLOY_USER/.ssh"
chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys" 2>/dev/null || true

echo "==> Cloning repo..."
mkdir -p "$DEPLOY_DIR"
if [ -d "$DEPLOY_DIR/.git" ]; then
  cd "$DEPLOY_DIR"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO" "$DEPLOY_DIR"
fi

echo "==> Creating .env from example..."
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
  sed -i 's/APP_ENV=development/APP_ENV=production/' "$DEPLOY_DIR/.env"
  sed -i 's/LOG_LEVEL=DEBUG/LOG_LEVEL=INFO/' "$DEPLOY_DIR/.env"
  sed -i 's/REDIS_HOST=localhost/REDIS_HOST=redis/' "$DEPLOY_DIR/.env"
  echo "STATIQ_DOMAIN=" >> "$DEPLOY_DIR/.env"
fi

echo "==> Creating credentials directory..."
mkdir -p "$DEPLOY_DIR/credentials"

echo "==> Configuring firewall..."
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8081/tcp   # Statiq backend API (production)
ufw allow 8082/tcp   # Statiq backend API (staging)
ufw allow 8501/tcp   # Statiq frontend (production)
ufw allow 8502/tcp   # Statiq frontend (staging)
ufw --force enable

echo "==> Installing Redis backup cron..."
bash "$DEPLOY_DIR/infra/hetzner/setup-backup-cron.sh"

chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR"

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Fill in secrets:          nano $DEPLOY_DIR/.env"
echo "  2. Place GCP SA key:         $DEPLOY_DIR/credentials/sa-key.json"
echo "  3. Set STATIQ_DOMAIN in .env (optional, for HTTPS via Caddy)"
echo "  4. Start services:"
echo "       cd $DEPLOY_DIR && docker compose -f docker-compose.hetzner.yml up -d"
echo ""
echo "GitHub Actions secrets to add (Settings → Secrets → Actions):"
echo "  HETZNER_HOST    = $(hostname -I | awk '{print $1}')"
echo "  HETZNER_USER    = $DEPLOY_USER"
echo "  HETZNER_SSH_KEY = (your private SSH key for this server)"
echo "  GHCR_TOKEN      = (GitHub PAT with read:packages scope)"
echo "  GHCR_USER       = (your GitHub username/org)"
echo ""
echo "GitHub Actions environments to create (Settings → Environments):"
echo "  hetzner          — production (deploy/hetzner branch)"
echo "  hetzner-staging  — staging    (staging/hetzner branch)"
