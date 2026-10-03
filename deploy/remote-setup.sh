#!/usr/bin/env bash
# Prepares an Amazon Linux 2023 server to run the InspectDB Docker stack.
# Idempotent: every deploy (deploy/remote-deploy.sh) runs it and it only installs what is missing.
set -euo pipefail

# Swap keeps Docker image builds from running out of memory on small instances
if [ ! -f /swapfile ]; then
  echo "Creating 2 GB swap file..."
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  dnf install -y docker
  systemctl enable --now docker
  usermod -aG docker ec2-user
fi
systemctl is-active --quiet docker || systemctl start docker

ARCH="$(uname -m)"            # x86_64 | aarch64
case "$ARCH" in
  x86_64) BUILDX_ARCH=amd64 ;;
  aarch64) BUILDX_ARCH=arm64 ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
mkdir -p "$PLUGIN_DIR"

if ! docker compose version >/dev/null 2>&1; then
  echo "Installing Docker Compose plugin..."
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" \
    -o "$PLUGIN_DIR/docker-compose"
  chmod +x "$PLUGIN_DIR/docker-compose"
fi

# Compose builds need buildx >= 0.17; Amazon Linux's docker package ships an older one.
# Plugins in $PLUGIN_DIR take precedence over the system copy in /usr/libexec/docker/cli-plugins.
MIN_BUILDX=0.17.0
CURRENT_BUILDX="$(docker buildx version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true)"
if [ -z "$CURRENT_BUILDX" ] || [ "$(printf '%s\n' "$MIN_BUILDX" "$CURRENT_BUILDX" | sort -V | head -n 1)" != "$MIN_BUILDX" ]; then
  echo "Installing Docker Buildx plugin (found: ${CURRENT_BUILDX:-none}, need >= $MIN_BUILDX)..."
  BUILDX_URL="$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
    | grep -o "https://[^\"]*linux-${BUILDX_ARCH}\"" | tr -d '"' | head -n 1)"
  curl -fsSL "$BUILDX_URL" -o "$PLUGIN_DIR/docker-buildx"
  chmod +x "$PLUGIN_DIR/docker-buildx"
fi

docker --version
docker compose version
