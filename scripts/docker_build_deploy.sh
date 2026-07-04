#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="${RUNTIME_DIR:-/mnt/c/Users/Administrator/code/jokak-260427}"
BUILD_DIR="${BUILD_DIR:-$HOME/jokak-build}"
IMAGE_NAME="${IMAGE_NAME:-jokak-app:latest}"

cd "$RUNTIME_DIR"

if [[ ! -f ".env.docker" ]]; then
  echo "Missing .env.docker. Copy .env to .env.docker first." >&2
  exit 1
fi

mkdir -p "$BUILD_DIR"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.gitdata' \
  --exclude '.tmp' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude 'outputs' \
  --exclude 'backups' \
  --exclude 'logs' \
  --exclude 'static/media' \
  --exclude 'assets/backgrounds' \
  --exclude 'assets/bgm/generated' \
  --exclude 'vendor' \
  --exclude '.env' \
  --exclude '.env.docker' \
  --exclude '.env.docker.*' \
  --exclude 'client_secret.json' \
  --exclude 'client_secrets.json' \
  --exclude 'youtube_token.json' \
  --exclude '*.session' \
  --exclude '*.session-journal' \
  --exclude '*.sqlite' \
  --exclude '*.sqlite3' \
  --exclude '*.sqlite3-*' \
  "$RUNTIME_DIR/" "$BUILD_DIR/"

cd "$BUILD_DIR"
docker build --progress=plain -f docker/Dockerfile -t "$IMAGE_NAME" .

cd "$RUNTIME_DIR"
docker compose --profile monitor up -d --no-build --force-recreate
docker compose --profile monitor ps
