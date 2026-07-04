# GitHub source management

작성일: 2026-07-04

## 목표

GitHub에는 소스와 Docker 배포 스크립트만 올린다. 운영 데이터, 인증 파일, 세션, SQLite DB, 생성 영상은 올리지 않는다.

## GitHub에 포함할 파일

```text
Python source files
templates/
static/
scripts/
docker/
docs/
requirements.txt
docker-compose.yml
.dockerignore
.env.example
.env.docker.example
README.md
channels.json
```

## GitHub에 포함하면 안 되는 파일

```text
.env
.env.docker
.env.docker.*
client_secret.json
client_secrets.json
youtube_token.json
*.session
*.session-journal
*.sqlite
*.sqlite3
outputs/
backups/
logs/
static/media/
assets/backgrounds/
assets/bgm/generated/
vendor/
```

이 목록은 `.gitignore`와 `.dockerignore`에 반영되어 있다.

## 배포 방식

WSL에서 실행한다.

```bash
cd /mnt/c/Users/Administrator/code/jokak-260427
bash scripts/docker_preflight.sh
bash scripts/docker_build_deploy.sh
curl -i http://127.0.0.1:8050/health
```

`scripts/docker_build_deploy.sh`는 Windows 파일시스템의 Docker build context 문제를 피하기 위해 소스를 WSL 내부 `~/jokak-build`로 복사한 뒤 이미지를 빌드한다. 컨테이너 실행은 원래 운영 폴더 `/mnt/c/Users/Administrator/code/jokak-260427`에서 수행하므로 SQLite, 세션, 토큰, outputs 같은 운영 데이터는 기존 위치를 그대로 사용한다.

## 최초 GitHub 연결

아직 원격 저장소가 없다면 GitHub에서 빈 저장소를 만든 뒤 아래 명령을 실행한다.

```bash
git remote add origin https://github.com/<owner>/<repo>.git
git branch -M main
```

민감정보가 포함되지 않았는지 확인한다.

```powershell
.\scripts\scan_secrets.ps1
```

변경 파일 확인:

```bash
git status --short
git diff -- .gitignore .dockerignore docker-compose.yml docker docs scripts/docker_preflight.sh scripts/docker_build_deploy.sh
```

커밋:

```bash
git add .gitignore .dockerignore .env.docker.example docker-compose.yml docker docs scripts/docker_preflight.ps1 scripts/docker_preflight.sh scripts/docker_build_deploy.sh dashboard.py monitor.py
git commit -m "Add Docker deployment workflow"
git push -u origin main
```

주의: 현재 작업 트리에 다른 변경이 많이 있을 수 있으므로, 관련 없는 변경은 함께 커밋하지 않는다.

## 운영 명령

상태 확인:

```bash
docker compose --profile monitor ps
```

로그 확인:

```bash
docker compose --profile monitor logs --tail 100
```

재배포:

```bash
bash scripts/docker_build_deploy.sh
```

중지:

```bash
docker compose --profile monitor stop
```

롤백은 기존 Windows 서비스가 `Stopped + Manual`로 보존되어 있을 때만 수행한다.
