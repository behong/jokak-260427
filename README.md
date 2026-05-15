# Telegram Channel Logger & Video Publisher

Telethon으로 `글반장` 텔레그램 글을 수집해 SQLite에 저장하고, 글반장 또는 직접 입력한 좋은글을 9:16 영상으로 생성한 뒤 필요할 때 수동으로 YouTube 업로드까지 운영하는 도구입니다.

## 현재 상태

- 수집 채널: `글반장`
- 저장 DB: `telegram_logs.sqlite3`
- 대시보드: http://127.0.0.1:8050
- 배경 보관함: Pexels 세로 영상 100개
- 활성 배경 풀: `2026-Q2` 100개
- 기존 정적/구버전 영상: `outputs/legacy_static`으로 보관
- 수동 입력: 대시보드의 `수동 좋은글`에서 직접 입력 후 대본/영상 생성
- YouTube 업로드: 생성 영상 화면에서 수동 업로드

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

외부 프로그램:

- `ffmpeg`
- `ffprobe`

두 명령이 PowerShell에서 실행 가능해야 TTS 오디오 길이 확인, 영상 합성, 오디오 mux가 정상 동작합니다.

## 환경 설정

운영 값은 DB의 `app_settings` 테이블에서 우선 관리합니다. `.env`는 초기 부팅과 로컬 기본값 용도로만 사용합니다. 실제 키와 토큰 파일은 Git에 포함하지 않습니다.

```text
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION=telegram_monitor
TELEGRAM_LOG_DB=telegram_logs.sqlite3
TELEGRAM_CATCH_UP_LIMIT=50
TELEGRAM_CATCH_UP_INTERVAL_SECONDS=3600
LOG_LEVEL=INFO

DASHBOARD_PASSWORD=dashboard_password
DASHBOARD_SECRET_KEY=random_long_secret

PEXELS_API_KEY=your_pexels_api_key
```

`DASHBOARD_PASSWORD`가 없으면 대시보드는 인증 없이 열립니다. 외부 접속이 필요할 때만 `DASHBOARD_HOST=0.0.0.0`을 설정하세요.
초기 설정 파일은 [.env.example](C:/Users/Administrator/code/260427/.env.example)을 복사해 만들거나, 대시보드의 `운영 설정`에서 수정합니다. 대시보드에서 저장한 값은 `.env`가 아니라 DB의 `app_settings`에 저장됩니다.

## 채널 설정

모니터링 대상은 [channels.json](C:/Users/Administrator/code/260427/channels.json)에서 관리합니다.

```json
[
  { "id": -1001038361551, "alias": "글반장" }
]
```

채널을 추가하거나 별칭을 바꾼 뒤에는 수집기를 재시작해야 적용됩니다.

## 실행

개별 실행:

```powershell
python .\monitor.py
python .\dashboard.py
```

관리형 루프 실행:

```powershell
.\scripts\start_managed.ps1
```

`start_managed.ps1`는 수집기와 대시보드를 각각 재시작 루프로 실행합니다.

## 대시보드

대시보드에서 할 수 있는 작업:

- 수집 로그 조회
- 운영 설정 관리
- Telegram/Pexels/YouTube 인증 상태 확인
- 이미지 메시지 썸네일 확인
- 백업 생성/정리
- 포터블 백업 생성
- 영상 대본 확인
- TTS 미리듣기
- 영상 생성
- 수동 좋은글 입력 및 영상 생성
- 생성 영상 목록 확인
- YouTube 메타데이터 확인/수정/업로드
- 상단 `배경영상` 메뉴에서 Pexels 배경 검색/다운로드/미리보기
- 분기별 사용 배경 영상 최대 100개 활성화

## DB 구조 요약

주요 테이블:

- `telegram_logs`: 수집 메시지
- `video_jobs`: 영상 생성 작업
- `background_assets`: Pexels 배경 영상 보관함
- `youtube_uploads`: YouTube 업로드 이력

`telegram_logs`는 `(source, msg_id)` 조합으로 중복을 막습니다. 텔레그램 메시지 ID는 채널별로 중복될 수 있으므로 `msg_id` 단독 unique를 사용하지 않습니다.

## 영상 생성

수동 생성:

```powershell
python .\render_video.py 107
```

렌더링 특징:

- 9:16 세로 영상
- Pexels 활성 배경 랜덤 사용
- 영상 내부에서 약 10초 단위로 활성 배경 랜덤 전환
- Edge TTS 음성 합성 지원
- `assets/bgm`에 음원이 있으면 낮은 볼륨으로 BGM 자동 합성, 없으면 기본 BGM 자동 생성
- 원본 텔레그램 이미지는 영상에 사용하지 않음
- YouTube 설명에는 Pexels 출처 문구를 넣지 않음

## BGM

BGM은 `assets/bgm` 아래의 로컬 음원을 사용합니다. 지원 확장자는 `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.ogg`입니다.

- 음원이 없으면 `assets/bgm/generated-soft-pad.wav` 기본 BGM을 자동 생성해서 사용
- 음원이 있으면 새 영상 생성 때 랜덤 선택
- 최근 사용한 BGM 10개는 우선 제외
- TTS가 있는 영상 기본 볼륨은 `VIDEO_BGM_TTS_VOLUME=0.10`
- TTS가 없는 영상 기본 볼륨은 `VIDEO_BGM_ONLY_VOLUME=0.14`
- 끄려면 운영 설정에서 `BGM 자동 삽입`을 끄거나 `VIDEO_BGM_ENABLED=0` 설정

유튜브 업로드용 음원은 YouTube Audio Library의 `Attribution not required` 음원을 우선 권장합니다.

## 배경 영상 관리

배경 영상은 상단 `배경영상` 메뉴에서 관리합니다. 제작 팝업에서는 배경을 고르지 않고, 현재 활성화된 분기 배경 100개 안에서 자동으로 랜덤 선택합니다.

배경 영상은 `assets/backgrounds`에 저장되고, 메타데이터는 `background_assets`에 기록됩니다.

보관함 운영 방식:

- 목록은 가볍게 표시
- `보기`를 눌렀을 때만 원본 영상 로드
- `활성` 배경만 렌더링 랜덤 풀에 포함
- `분류`에 `2026-Q2` 같은 분기값 저장
- `분기 활성화`를 누르면 해당 분기 배경 중 조건을 만족하는 최신 100개까지만 활성화
- 6초 미만 또는 1080px 미만 배경은 분기 활성화 때 자동 제외

배경 100개 자동 보충:

```powershell
python .\download_backgrounds.py --target 100
```

현재 분기 배정:

- `2026-Q2`: 100개, 활성 100개

## YouTube 업로드

준비:

1. Google Cloud Console에서 YouTube Data API v3 활성화
2. OAuth 클라이언트 ID를 `데스크톱 앱` 유형으로 생성
3. 내려받은 OAuth 파일을 프로젝트 루트에 `client_secret.json` 또는 `client_secrets.json`으로 저장
4. 첫 인증 후 생성되는 `youtube_token.json`은 개인 인증 정보라 Git에 포함하지 않음

업로드 방식:

- 기본 공개 상태는 `private`
- 대시보드에서 제목, 설명, 태그 확인 가능
- 업로드 후 YouTube Studio 링크 제공
- 업로드된 영상은 대시보드에서 YouTube 메타데이터 수정 가능

메타데이터 정리:

- 제목 100자 제한
- 설명 5000자 제한
- 제어문자 제거
- `<`, `>` 제거
- 태그 중복/쉼표/길이 정리
- Pexels 출처 문구 제외

현재 운영 상태:

- 글반장 로그와 직접입력 로그만 보관
- 수집기 시작 시 최근 글을 보정 수집하고, 실행 중에는 `TELEGRAM_CATCH_UP_INTERVAL_SECONDS=3600` 기준으로 1시간마다 신규 글을 다시 확인
- `AUTO_UPLOAD_ENABLED=1`이면 모니터가 글반장 새 글을 자동 체크해 영상 생성 후 YouTube 예약 업로드
- `AUTO_UPLOAD_DAILY_LIMIT=10` 기준으로 하루 자동 생성/업로드 개수를 제한
- 글이 부족할 때 `SARAMRO_QUOTES_ENABLED=1`로 사람로 명언을 보충 수집 가능
- 기본 예약 시간대는 `AUTO_UPLOAD_SCHEDULE_WINDOWS=07:30-09:00,12:00-13:30,18:00-23:00`
- 기본값은 자동화 시작 이후 저장된 글만 처리하며, 기존 DB 글까지 포함하려면 `AUTO_UPLOAD_INCLUDE_EXISTING=1`
- 자동 업로드 실패 건은 `auto_upload_jobs`에 실패 상태로 남기며, 재시도는 `AUTO_UPLOAD_RETRY_FAILED=1`로 켤 수 있음

## 백업

대시보드의 `백업` 버튼은 SQLite DB와 미디어 파일을 `backups` 폴더에 저장합니다. `백업 정리` 버튼은 최신 10개 백업 세트를 남기고 오래된 파일을 삭제합니다.

수동 실행:

```powershell
python .\backup.py
```

## 다른 PC 복구용 패키지

컴퓨터 고장에 대비하려면 일반 백업보다 포터블 패키지를 주기적으로 만들어 외장디스크나 클라우드 드라이브에 보관합니다.

```powershell
.\scripts\export_portable.ps1
```

패키지는 `backups/portable-glbanjang-YYYYMMDD-HHMMSS.zip`으로 생성됩니다. 이 압축에는 코드, `.env`, SQLite DB, Telegram 세션, YouTube 토큰, 배경 영상, 생성 영상이 포함됩니다. 실제 키와 토큰이 들어 있으므로 공개 저장소나 타인에게 공유하지 않습니다.

다른 Windows PC에서 복구:

```powershell
.\scripts\restore_portable.ps1 -Archive C:\path\portable-glbanjang-YYYYMMDD-HHMMSS.zip -Destination C:\glbanjang-video
cd C:\glbanjang-video
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\scripts\start_managed.ps1
```

새 PC에는 Python 3.11 이상과 `ffmpeg`, `ffprobe`가 설치되어 있어야 합니다. 복구 후 대시보드는 http://127.0.0.1:8050 에서 엽니다.

## GitHub 관리

소스 코드는 GitHub에 올리고, 운영 데이터와 인증 파일은 로컬에만 둡니다.

GitHub에 포함:

- Python 코드
- `templates/`, `static/`
- `scripts/`
- `requirements.txt`
- `README.md`
- `.env.example`
- `channels.json`

GitHub에 제외:

- `.env`
- `telegram_logs.sqlite3`
- `telegram_monitor.session`
- `youtube_token.json`
- `client_secret.json`
- `client_secrets.json`
- `assets/backgrounds/`
- `outputs/`
- `backups/`
- `logs/`

업로드 전 점검:

```powershell
.\scripts\scan_secrets.ps1
```

복구 방향:

1. GitHub에서 소스 받기
2. DB 파일 또는 향후 Supabase/Neon 연결 정보 적용
3. `app_settings`에서 Telegram/Pexels/YouTube 설정 자동 로드
4. YouTube OAuth 파일과 Telegram 세션 파일은 DB 값에서 자동 복원

현재는 SQLite의 `app_settings`를 사용합니다. Supabase/Neon으로 이전할 때는 `app_settings`, `telegram_logs`, `video_jobs`, `background_assets`, `youtube_uploads` 테이블을 Postgres로 옮기는 방식으로 확장합니다.

## 주요 파일

- [monitor.py](C:/Users/Administrator/code/260427/monitor.py): 텔레그램 수집기
- [dashboard.py](C:/Users/Administrator/code/260427/dashboard.py): 웹 대시보드/API
- [render_video.py](C:/Users/Administrator/code/260427/render_video.py): 영상 렌더러
- [youtube_upload.py](C:/Users/Administrator/code/260427/youtube_upload.py): YouTube API 연동
- [backgrounds.py](C:/Users/Administrator/code/260427/backgrounds.py): Pexels 배경 관리
- [db.py](C:/Users/Administrator/code/260427/db.py): SQLite 스키마/저장 로직
- [download_backgrounds.py](C:/Users/Administrator/code/260427/download_backgrounds.py): 배경 100개 보충 스크립트

## 보관/무시 파일

Git에 포함하지 않는 파일:

- `.env`
- `client_secret.json`
- `client_secrets.json`
- `youtube_token.json`
- `telegram_logs.sqlite3`
- `*.session`
- `outputs/`
- `assets/backgrounds/`
- `static/media/`
- `backups/`
- `logs/`
- `vendor/`
