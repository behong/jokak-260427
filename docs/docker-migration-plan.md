# Jokak Docker migration plan

작성일: 2026-07-04

이 문서는 기존 Windows/PM2 또는 WinSW 기반 `monitor.py`, `dashboard.py` 운영을 Docker Desktop + WSL2 기반으로 단계 전환하기 위한 절차다.

## 1. 전환 대상

현재 저장소의 전환 대상은 아래 2개 프로세스다.

```text
jokak-dashboard  -> python dashboard.py
jokak-monitor    -> python monitor.py
```

`참고/서버이전.md`의 `collector-api`, `publisher-api`, `*-worker` 구성은 이 저장소의 실제 서비스명이 아니므로 그대로 사용하지 않는다.

## 2. 유지해야 하는 로컬 데이터

아래 파일과 디렉터리는 Docker 이미지에 넣지 않고 컨테이너에 bind mount 한다.

```text
.env.docker
telegram_logs.sqlite3
telegram_monitor.session
telegram_dashboard_refresh.session
youtube_token.json
client_secret.json
outputs/
assets/backgrounds/
assets/bgm/
backups/
logs/
```

`.env.docker`는 실제 비밀값을 포함하므로 Git에 커밋하지 않는다. 최초 1회는 기존 `.env`를 복사해서 시작한다.

```powershell
Copy-Item .env .env.docker
```

## 3. 1단계: dashboard만 Docker로 실행

사전 점검을 실행한다.

```powershell
.\scripts\docker_preflight.ps1
```

WSL Ubuntu에서 실행한다면 다음 명령을 사용한다.

```bash
cd /mnt/c/Users/Administrator/code/jokak-260427
bash scripts/docker_preflight.sh
```

기존 dashboard 프로세스와 포트 충돌을 피하기 위해 먼저 기존 dashboard만 중지한다.

```powershell
pm2 stop jijogak-dashboard
```

PM2가 아니라 WinSW 서비스로 운영 중이면 다음 이름을 확인한 뒤 중지한다.

```powershell
Get-Service *Telegram*
Stop-Service TelegramDashboard
```

Docker dashboard를 빌드하고 실행한다.

```powershell
docker compose up -d --build dashboard
```

확인한다.

```powershell
docker compose ps
docker logs jokak-dashboard --tail 100
curl -i http://127.0.0.1:8050/health
```

브라우저에서 확인한다.

```text
http://127.0.0.1:8050
```

문제가 있으면 Docker dashboard를 내리고 기존 dashboard를 되돌린다.

```powershell
docker compose stop dashboard
pm2 start jijogak-dashboard
```

## 4. 2단계: monitor를 Docker로 실행

dashboard가 정상이라면 기존 monitor를 중지한다.

```powershell
pm2 stop jijogak-monitor
```

WinSW 서비스로 운영 중이면 다음을 사용한다.

```powershell
Stop-Service TelegramMonitor
```

Docker monitor를 실행한다.

```powershell
docker compose --profile monitor up -d monitor
```

확인한다.

```powershell
docker compose --profile monitor ps
docker logs jokak-monitor --tail 100
```

대시보드에서 최근 수집 시간과 monitor heartbeat가 갱신되는지 확인한다.

문제가 있으면 Docker monitor를 중지하고 기존 monitor를 되돌린다.

```powershell
docker compose --profile monitor stop monitor
pm2 start jijogak-monitor
```

## 5. 운영 명령

코드 변경 후 재빌드/재배포:

```bash
cd /mnt/c/Users/Administrator/code/jokak-260427
bash scripts/docker_build_deploy.sh
```

상태 확인:

```powershell
docker compose --profile monitor ps
```

로그 확인:

```powershell
docker logs jokak-dashboard --tail 100
docker logs jokak-monitor --tail 100
```

전체 재시작:

```powershell
docker compose --profile monitor restart
```

중지:

```powershell
docker compose --profile monitor stop
```

컨테이너 제거가 필요할 때만 사용:

```powershell
docker compose --profile monitor down
```

`down -v`는 데이터 볼륨 삭제 위험이 있으므로 사용하지 않는다.

## 6. 안정화 후 정리

최소 24시간 동안 dashboard와 monitor가 Docker에서 정상 동작하는지 본다.

문제가 없으면 기존 PM2 프로세스 또는 Windows 서비스는 즉시 삭제하지 말고 자동 시작만 끈다.

```powershell
pm2 save
```

WinSW 서비스인 경우:

```powershell
Set-Service TelegramDashboard -StartupType Manual
Set-Service TelegramMonitor -StartupType Manual
```

3~7일간 문제가 없을 때 서비스 등록 삭제를 검토한다. 파일과 백업은 바로 삭제하지 않는다.
