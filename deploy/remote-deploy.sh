#!/usr/bin/env bash
# Runs on the EC2 server: unpacks the uploaded release and (re)starts the Docker stack.
# Expects /tmp/inspectdb-release.tar.gz (repo files + .env.production) and /tmp/remote-setup.sh.
set -euo pipefail

APP_DIR="$HOME/inspectdb"
RELEASE=/tmp/inspectdb-release.tar.gz

sed -i 's/\r$//' /tmp/remote-setup.sh
sudo bash /tmp/remote-setup.sh

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
tar -xzf "$RELEASE" -C "$APP_DIR"
rm -f "$RELEASE"
sed -i 's/\r$//' "$APP_DIR/.env.production"
chmod 600 "$APP_DIR/.env.production"

cd "$APP_DIR"
sudo docker compose --env-file .env.production up -d --build --remove-orphans --wait --wait-timeout 300
sudo docker image prune -f
sudo docker compose --env-file .env.production ps

echo "Health:"
sudo docker compose --env-file .env.production exec -T caddy wget -qO- http://backend:8000/api/health
echo
