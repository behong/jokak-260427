# Jokak Video Publisher

Telegram 글을 수집해 SQLite에 저장하고, 대시보드에서 영상 생성과 YouTube 업로드를 관리하는 도구입니다.

## 현재 운영

2026-07-04 기준 운영은 Docker 기준입니다.

```text
jokak-dashboard  http://127.0.0.1:8050
jokak-monitor    Telegram 수집/자동 처리
```

기존 Windows 서비스(`jijogak-dashboard`, `jijogak-monitor`)는 삭제하지 않고 약 1주일간 `Stopped + Manual` 상태로 보존합니다. 안정화 후 서비스 등록과 `C:\srv\services` 정리를 검토합니다.

## 상태 확인

WSL Ubuntu에서 실행합니다.

```bash
cd /mnt/c/Users/Administrator/code/jokak-260427
docker compose --profile monitor ps
curl -i http://127.0.0.1:8050/health
docker logs jokak-dashboard --tail 50
docker logs jokak-monitor --tail 50
```

정상 기준:

```text
jokak-dashboard  Up / healthy
jokak-monitor    Up
/health          200 OK
```

## 배포

코드 변경 후 재빌드/재배포:

```bash
cd /mnt/c/Users/Administrator/code/jokak-260427
bash scripts/docker_build_deploy.sh
```

## GitHub 관리

GitHub에는 소스와 Docker 배포 파일만 올립니다.

커밋 전 확인:

```bash
git status --short
git ls-files | grep -E '(^\.env$|\.env\.docker$|client_secret|youtube_token|telegram_logs|\.session|\.sqlite|outputs/|backups/|logs/|assets/backgrounds/)'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/scan_secrets.ps1
```

Git에 포함하지 않는 파일:

```text
.env
.env.docker
client_secret.json
youtube_token.json
telegram_logs.sqlite3
*.session
outputs/
backups/
logs/
assets/backgrounds/
```

## 주요 파일

```text
dashboard.py                  웹 대시보드/API
monitor.py                    Telegram 수집/자동 처리
docker-compose.yml            Docker 서비스 정의
docker/Dockerfile             Docker 이미지
scripts/docker_build_deploy.sh WSL 빌드/배포
docs/docker-migration-plan.md Docker 이전 절차
docs/github-source-management.md GitHub 관리 기준
```

## 롤백

문제 발생 시 Docker를 중지하고 보존 중인 Windows 서비스를 다시 켭니다.

```bash
docker compose --profile monitor stop
```

Windows PowerShell:

```powershell
Set-Service jijogak-dashboard -StartupType Automatic
Set-Service jijogak-monitor -StartupType Automatic
Start-Service jijogak-dashboard
Start-Service jijogak-monitor
```
