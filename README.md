# Telegram Channel Logger

Telethon으로 지정된 텔레그램 채널 메시지를 실시간 수집해 SQLite에 저장하는 스크립트입니다.

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 설정

Telegram API 정보는 https://my.telegram.org 에서 발급받은 뒤 환경변수로 넣습니다.

```powershell
$env:TELEGRAM_API_ID="123456"
$env:TELEGRAM_API_HASH="your_api_hash"
```

선택 환경변수:

```powershell
$env:TELEGRAM_SESSION="telegram_monitor"
$env:TELEGRAM_LOG_DB="telegram_logs.sqlite3"
$env:LOG_LEVEL="INFO"
$env:DASHBOARD_PASSWORD="dashboard_password"
$env:DASHBOARD_SECRET_KEY="random_long_secret"
```

`DASHBOARD_PASSWORD`가 없으면 대시보드는 인증 없이 열립니다. 비밀번호를 설정하면 `/login`에서 로그인해야 접근할 수 있습니다. 외부 접속이 필요할 때만 `DASHBOARD_HOST=0.0.0.0`을 설정하세요.

## 채널 설정

모니터링 대상 채널은 `channels.json`에서 관리합니다.

```json
[
  { "id": -1001047477854, "alias": "뽐질" },
  { "id": -1002381848987, "alias": "퍼나정" },
  { "id": -1001038361551, "alias": "글반장" }
]
```

채널을 추가하거나 별칭을 바꾼 뒤에는 수집기를 재시작해야 적용됩니다.

## 수집기 실행

```powershell
python .\monitor.py
```

첫 실행 시 Telethon 로그인 인증이 필요할 수 있습니다. 인증이 끝나면 같은 세션 파일을 재사용합니다.

## 웹 대시보드 실행

수집기와 같은 `TELEGRAM_LOG_DB` 값을 사용하면 동일한 SQLite DB를 조회합니다.

```powershell
python .\dashboard.py
```

브라우저에서 http://127.0.0.1:8050 을 열면 됩니다.

대시보드는 전체 건수, 최근 수신 시간, 채널별 건수, 로그 목록을 보여주며 채널/검색어/표시 수 필터를 지원합니다. 화면은 5초마다 자동 갱신됩니다.

## 저장 테이블

`telegram_logs`

| Column | Type | Description |
| --- | --- | --- |
| id | INTEGER | Primary key, auto increment |
| source | TEXT | 채널 별칭 |
| msg_id | INTEGER | 텔레그램 메시지 ID, unique |
| content | TEXT | 앞뒤 공백이 제거된 메시지 본문 |
| created_at | DATETIME | 메시지 수신 시간 |
| saved_at | TIMESTAMP | DB 저장 시간 |
| media_path | TEXT | 저장된 이미지 파일 경로 |
| media_kind | TEXT | 미디어 종류 |
| group_key | TEXT | 텔레그램 앨범 묶음 키 |

`source`, `created_at` 컬럼에는 조회용 인덱스가 생성됩니다.

이미지 메시지는 `static/media` 폴더에 저장되고 대시보드에서 썸네일로 표시됩니다.

## Windows 서비스 관리

권장 운영 방식은 WinSW를 사용해 Windows 서비스로 등록하는 것입니다. 관리자 PowerShell에서 실행하세요.

```powershell
.\service\download_winsw.ps1
.\service\install.ps1
.\service\start.ps1
```

일반 PowerShell에서 UAC 관리자 창을 띄워 처리하려면:

```powershell
.\service\install_admin.ps1
.\service\start_admin.ps1
```

상태 확인:

```powershell
.\service\status.ps1
```

중지/재시작/삭제:

```powershell
.\service\stop.ps1
.\service\restart.ps1
.\service\uninstall.ps1
```

WinSW 서비스 로그는 `service\logs`에 저장되고, 애플리케이션 로그는 `monitor.log`에 저장됩니다.

현재 세션에서만 간단히 재시작 루프로 실행하려면 아래 스크립트를 사용할 수 있습니다.

```powershell
.\scripts\start_managed.ps1
```

## 백업

대시보드의 `백업` 버튼을 누르면 `backups` 폴더에 SQLite DB와 이미지 zip 파일이 생성됩니다.
`백업 정리` 버튼은 최신 10개 백업 세트를 남기고 오래된 백업 파일을 삭제합니다.

수동 실행:

```powershell
python .\backup.py
```

## 영상 샘플 생성

글반장 로그 ID를 지정하면 `지혜로운 조각들` 9:16 무음 샘플 영상을 `outputs` 폴더에 생성합니다.

```powershell
python .\render_video.py 107
```

## Pexels 배경 영상 소스

웹 대시보드의 영상 대본 모달에서 Pexels 세로형 배경 영상을 검색하고 다운로드할 수 있습니다.

`.env`에 Pexels API 키를 추가한 뒤 대시보드를 재시작하세요.

```powershell
PEXELS_API_KEY="your_pexels_api_key"
```

다운로드한 영상은 `assets/backgrounds` 폴더에 저장되고, DB의 `background_assets` 테이블에 출처와 작성자 정보가 기록됩니다.
보관함에서 배경 영상을 선택한 뒤 `영상 생성`을 누르면 해당 영상을 배경으로 합성합니다.

원본 이미지는 영상에 사용하지 않고 자체 배경 템플릿만 사용합니다.

## YouTube 업로드

생성된 영상 카드에서 `유튜브 메타데이터`를 열고 제목, 설명, 태그를 확인한 뒤 `유튜브 업로드`를 누르면 YouTube Data API로 업로드합니다.

Google Cloud Console에서 YouTube Data API v3를 활성화하고 OAuth 클라이언트 ID를 `데스크톱 앱` 유형으로 만든 뒤, 내려받은 파일을 프로젝트 루트의 `client_secrets.json`으로 저장하세요.

첫 업로드 때 브라우저 로그인 창이 열리고, 인증이 끝나면 `youtube_token.json`이 저장되어 다음 업로드부터 재사용됩니다. 두 파일은 개인 인증 정보라 Git에 포함하지 않습니다.
