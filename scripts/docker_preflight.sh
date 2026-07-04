#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

has_error=0

check_path() {
  local path="$1"
  local required="${2:-required}"

  if [[ -e "$path" ]]; then
    printf 'OK      %s\n' "$path"
    return
  fi

  if [[ "$required" == "required" ]]; then
    printf 'MISSING %s\n' "$path"
    has_error=1
  else
    printf 'WARN    %s\n' "$path"
  fi
}

if ! command -v docker >/dev/null 2>&1; then
  echo "MISSING docker command"
  has_error=1
else
  docker --version
  docker compose version
fi

check_path ".env.docker"
check_path "telegram_logs.sqlite3"
check_path "telegram_monitor.session"
check_path "youtube_token.json"
check_path "client_secret.json"
check_path "outputs"
check_path "assets/backgrounds"
check_path "assets/bgm"
check_path "backups"
check_path "logs"
check_path "telegram_dashboard_refresh.session" optional

if [[ -f ".env.docker" ]] && grep -q '^DASHBOARD_HOST=' .env.docker && ! grep -q '^DASHBOARD_HOST=0\.0\.0\.0$' .env.docker; then
  echo "WARN    .env.docker DASHBOARD_HOST is not 0.0.0.0; compose overrides it for dashboard"
fi

if [[ "$has_error" -ne 0 ]]; then
  echo "Docker preflight failed"
  exit 1
fi

echo "Docker preflight passed"
