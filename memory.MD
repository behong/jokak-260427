# 작업 메모

## 2026-05-04 14:23 KST

- 사용자가 요청함: 앞으로 작업이 끝나면 다음 작업 때 참조할 수 있도록 `memory.MD`에 요약을 기록한다.
- 이 저장소는 `글반장` 텔레그램 글 수집, 대시보드, 수동 영상 생성, 수동 YouTube 업로드 중심으로 운영한다.
- 자동업로드 기능은 삭제됨. `auto_publish.py`, 자동업로드 API/페이지/대시보드 섹션, `auto_publish_jobs` 테이블, `AUTO_*` 설정은 제거되었다.
- 글 목록은 저장 시간 기준 정렬이며 완료 숨김 기능은 제거되었다. 목록은 카드 그리드 형태이고 `보기/제작` 버튼으로 대본/영상 팝업을 연다.
- `보기/제작` 팝업에서는 배경 영상 관리를 하지 않는다. 영상 생성 시 활성 배경 풀에서 서버가 자동 랜덤 선택한다.
- 배경 영상 관리는 상단 메뉴 `배경영상`(`/background-library`)으로 분리되었다.
- 배경 분기 활성화는 해당 분기에서 조건 충족 최신 100개까지만 활성화한다.
- 수집기 `monitor.py`는 시작 시 최근 글 보정 수집 후 `TELEGRAM_CATCH_UP_INTERVAL_SECONDS=3600` 기준으로 1시간마다 신규 글을 다시 확인한다.
- 변경 후에는 `python -m py_compile ...`와 Flask test client 라우트 확인을 주 검증 방식으로 사용했다.

## 2026-05-04 14:27 KST

- `보기/제작` 팝업에서 `영상 확인` 버튼을 제거했다.
- 팝업 버튼은 `음성 미리듣기`, `영상 생성`, `유튜브 전송` 구조다.
- `영상 생성`은 업로드까지 자동으로 이어지지 않는다. 생성된 영상이 있으면 `유튜브 전송` 버튼이 활성화되고, 이 버튼이 `/api/videos/<filename>/youtube/upload`로 전송한다.
- YouTube 업로드가 완료된 글은 로그 카드 상태가 `수집 / 유`로 표시된다. API는 `delivery_statuses` 배열을 내려주며, 향후 Threads/Instagram도 같은 배열에 짧은 배지값을 추가하는 방식으로 확장한다.

## 2026-05-04 15:00 KST

- TTS 음성 선택에 `랜덤 한국어 음성` 옵션을 추가했고 기본 선택값으로 사용한다.
- 설치된 Edge TTS의 순수 `ko-KR` 음성은 3개뿐이라, 한국어 발화가 가능한 멀티링구얼 음성 8개(Ava, Emma, Andrew, Brian, Vivienne, Remy, Seraphina, Florian)를 추가했다.
- `tts.resolve_voice()`가 `random` 또는 빈 값이면 등록된 음성 중 하나를 무작위 선택한다.
- 비디오 렌더링은 생성 시작 시 랜덤 음성을 실제 음성명으로 해석해서 한 영상 안에서는 같은 음성을 유지하고, `script["tts_voice"]`에도 실제 선택된 음성을 기록한다.
- 대시보드 `보기/제작` 팝업의 음성 목록은 `VOICE_OPTIONS`를 서버에서 받아 렌더링한다. 미리듣기에서 랜덤 음성이 선택되면 선택된 실제 음성명을 상태 문구에 보여준다.
- 검증: `python -m py_compile tts.py render_video.py dashboard.py`, Flask test client 대시보드 렌더링, 랜덤 음성 샘플 선택 확인.

## 2026-05-04 20:32 KST

- Andrew 멀티링구얼 음성은 외국인 느낌이 강하다는 요청에 따라 `VOICE_OPTIONS`에서 제거했다.
- 랜덤 음성 후보는 `VOICE_OPTIONS`에서 자동 생성되므로 Andrew도 랜덤 생성 대상에서 함께 제외된다.
- 검증: `python -m py_compile tts.py dashboard.py render_video.py`, 랜덤 샘플 목록에 Andrew 미포함 확인.

## 2026-05-05 10:02 KST

- 대시보드 새로고침 버튼이 DB 목록만 다시 읽고 텔레그램 신규글 체크를 하지 않던 문제를 수정했다.
- `telegram_sync.py`를 추가해 최근 텔레그램 메시지 보정 수집을 재사용 가능한 함수로 분리했다.
- `/api/logs/refresh` POST API를 추가했고, 대시보드 `새로고침` 버튼은 이 API로 신규글 체크 후 통계/목록/영상/설정을 다시 불러온다.
- 기존 모니터 세션과 충돌하지 않도록 수동 새로고침은 `telegram_dashboard_refresh.session` 복사본을 사용한다.
- 실제 텔레그램 체크 테스트 결과 최근 5개 기준 `inserted=3`, `updated=2`, `errors=[]`로 정상 저장됐다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile telegram_sync.py dashboard.py monitor.py`, Flask test client `/api/logs/refresh` 모킹 호출, 대시보드 템플릿 API 연결 확인.

## 2026-05-05 10:20 KST

- 업로드 완료된 짧은 영상들을 긴영상으로 묶는 기능을 추가했다.
- DB에 `long_video_jobs` 테이블을 추가했고, `/api/long-videos/candidates`, `/api/long-videos/create`, `/api/long-videos/jobs/<id>` API를 추가했다.
- 생성영상 페이지 상단에 `업로드 완료 영상 묶기` 컨트롤을 추가했다. 기본은 최신 업로드 완료 영상 10개이며 15개/20개도 선택 가능하다.
- 긴영상 생성은 백그라운드 잡으로 실행되며, ffmpeg concat 후 H.264/AAC로 재인코딩한다.
- 긴영상 파일명은 `long-wisdom-library-YYYYMMDD-HHMMSS.mp4` 형식이며 생성영상 목록에 함께 표시된다.
- 긴영상 유튜브 메타데이터는 `마음에 남는 좋은 글 N편 모음 | 지혜로운 조각들` 형식으로 별도 생성된다.
- 실제 최신 업로드 완료 영상 10개로 `outputs/long-wisdom-library-20260505-101807.mp4`를 생성했다. 길이는 7분 22초, 크기는 약 303MB다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile db.py dashboard.py telegram_sync.py`, 후보 API 10개 조회, 긴영상 생성 잡 완료, ffprobe duration 확인, 유튜브 메타데이터 확인.

## 2026-05-05 10:28 KST

- 텔레그램 수집에서 이미지 다운로드를 하지 않도록 변경했다. `monitor.py`와 수동 새로고침용 `telegram_sync.py` 모두 `download_image()`가 항상 `(None, None)`을 반환한다.
- 기존 DB/파일에 이미 저장된 이미지는 그대로 두지만, 앞으로 신규 수집/수동 새로고침에서는 이미지를 가져오지 않는다.
- 생성 영상 API의 긴영상 항목에 `is_long_video=true`, `kind_label="롱"`을 추가했다.
- 대시보드와 생성영상 페이지의 영상 카드에 `kind_label`이 있으면 `롱` 배지를 표시하도록 수정했다.
- 대시보드 프로세스와 텔레그램 모니터 프로세스를 재시작해 변경 사항을 실행 환경에 반영했다.
- 검증: `python -m py_compile monitor.py telegram_sync.py dashboard.py`, `/api/videos` 긴영상 배지값 확인, 생성영상 페이지 UI 문자열 확인.

## 2026-05-05 14:32 KST

- 긴영상 생성 비율을 세로 9:16에서 유튜브 일반 영상용 16:9로 변경했다.
- 긴영상 ffmpeg 생성 시 원본 세로 영상은 가운데에 1080px 높이로 배치하고, 좌우는 같은 영상을 1920x1080으로 확대한 뒤 블러 처리한 배경으로 채운다.
- 대시보드/생성영상 페이지의 긴영상 카드 미리보기도 16:9 비율로 표시되도록 `long-card` 스타일을 추가했다.
- 새 방식으로 `outputs/long-wisdom-library-20260505-142934.mp4`를 생성했다. 해상도는 1920x1080, 길이는 7분 22초, 크기는 약 198MB다.
- 8050 대시보드 프로세스를 재시작해 앞으로 버튼으로 만드는 긴영상도 16:9로 생성되게 반영했다.
- 검증: `python -m py_compile dashboard.py`, ffprobe 해상도/길이 확인, `/api/videos` 긴영상 목록 확인.

## 2026-05-05 14:42 KST

- 생성영상 목록에서 같은 글(`log_id`)로 여러 번 생성된 짧은 영상은 최신 1개만 표시하도록 변경했다.
- 실제 파일과 `video_jobs` 이력은 삭제하지 않고 유지한다. 목록 표시에서만 중복을 숨긴다.
- 긴영상은 `log_id`가 없고 별도 산출물이므로 여러 개가 모두 표시된다.
- 검증: `python -m py_compile dashboard.py`, `list_generated_videos(50)` 결과에서 짧은 영상 `log_id` 중복 없음 확인.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.

## 2026-05-05 14:47 KST

- 대시보드에서 이미지 관련 기능을 제거했다.
- 글 목록 조회 API는 더 이상 `has_image` 필터를 받거나 `media_path`, `media_kind`, `media_paths`, `media_count`를 응답에 포함하지 않는다.
- 대시보드 UI에서 `이미지만` 필터, 이미지 썸네일 그리드, 이미지 확대 모달, 이미지 클릭 핸들러, 이미지 통계 문구를 제거했다.
- 텔레그램 상시 수집(`monitor.py`)과 수동 새로고침(`telegram_sync.py`)도 이미지 처리 함수를 제거하고 텍스트만 저장한다.
- 기존 DB의 이미지 컬럼/파일은 삭제하지 않았지만 앞으로 대시보드와 수집 흐름에서는 사용하지 않는다.
- 대시보드 프로세스와 텔레그램 모니터 프로세스를 재시작해 변경 사항을 실행 환경에 반영했다.
- 검증: `python -m py_compile dashboard.py monitor.py telegram_sync.py`, `/api/logs` 응답 이미지 필드 없음 확인, `/api/stats` 이미지 통계 없음 확인, 대시보드 HTML 이미지 UI 문자열 없음 확인.

## 2026-05-05 16:10 KST

- 긴영상 생성 규칙을 10분 이상으로 변경했다. 후보 짧은 영상의 ffprobe 길이를 합산해 `LONG_VIDEO_TARGET_SECONDS=600` 이상이 될 때까지 선택한다.
- 이미 `long_video_jobs.source_filenames`에 들어간 짧은 영상은 새 긴영상 후보에서 제외한다. 기존 긴영상 1/2번에 들어간 10개도 재사용 금지 대상이다.
- 긴영상 후보는 `wisdom-library-*.mp4` 짧은 영상만 사용하므로 `long-wisdom-library-*.mp4` 긴영상이 다시 합쳐지는 일도 없다.
- 긴영상 제목은 `지혜로운조각 10분 시리즈 N` 형식으로 자동 생성한다. 기존 같은 제목 패턴의 최대 번호를 보고 다음 번호를 붙인다.
- 생성영상 페이지의 긴영상 컨트롤 문구를 `10분 이상`, `최대 개수` 기준으로 바꿨다. 기본 최대 개수는 40개다.
- 새 규칙으로 `outputs/long-wisdom-library-20260505-160546.mp4`를 생성했다. 제목은 `지혜로운조각 10분 시리즈 1`, 소스 20개, 길이 10분 16초, 해상도 1920x1080, 크기 약 383MB다.
- 생성 후 남은 미사용 후보는 3개/105초라 다음 `시리즈 2`는 새 업로드 완료 영상이 더 쌓여야 10분 이상 생성 가능하다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile dashboard.py`, 후보 제외/길이 합산 확인, ffprobe 해상도/길이 확인, 유튜브 메타데이터 제목 확인.

## 2026-05-05 16:20 KST

- 글 목록 상태 배지에 영상 생성 카운터를 추가했다.
- `/api/logs` 조회 시 각 `telegram_logs.id`별 `video_jobs`의 `ready` 상태이면서 `output_path`가 있는 고유 영상 파일 수를 `COUNT(DISTINCT output_path)`로 계산한다.
- 상태 표시는 `수집 / 영N / 유` 형식이다. 예: 같은 글에서 영상 4개가 생성되고 유튜브 업로드까지 있으면 `수집 / 영4 / 유`.
- 그룹으로 묶인 로그는 각 행의 고유 영상 수를 합산해 표시한다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile dashboard.py`, `/api/logs?limit=500`에서 `log_id 522 -> 수집 / 영4 / 유`, `log_id 521 -> 수집 / 영3 / 유` 확인.

## 2026-05-05 16:26 KST

- 글 목록 카드에 전체 목록 기준 순서를 추가했다.
- `/api/logs`의 `group_log_rows()`에서 저장 시간 최신순 정렬 후 `sequence`를 부여한다. 오래된 글이 1번, 최신 글이 마지막 번호다.
- 현재 전체 52개 기준 최신 글은 `순서 52`, 가장 오래된 글은 `순서 1`로 표시된다.
- 카드 상단 상태 배지 옆 메타 영역에 `순서 N · #msg_id` 형식으로 표시한다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile dashboard.py`, `/api/logs?limit=100` 결과 52개, 순서값 유니크, 최신 52/최오래 1 확인.

## 2026-05-05 16:43 KST

- 생성영상 목록에서 일부 파일이 한글 제목 대신 파일명으로 보이는 원인을 수정했다.
- `wisdom-library-524-20260505-162010-audio.mp4`처럼 `-audio`가 붙은 파일은 기존 `VIDEO_FILENAME_PATTERN`과 매칭되지 않아 `log_id`를 못 찾았다. 정규식을 `(?:-audio)?` 허용으로 변경했다.
- `video_jobs` 기록이 없는 짧은 영상 파일도 파일명에서 `log_id`를 추출해 `telegram_logs.content` 기반 `generate_video_script()` 제목을 추론하도록 했다.
- 긴영상은 `video_jobs`가 아니라 `long_video_jobs`에 제목이 있으므로 생성영상 목록 조회 시 `long_video_jobs`도 함께 조회해 제목을 채우도록 했다.
- 확인 결과 `wisdom-library-524-20260505-162010-audio.mp4`는 `뭐든 최상위권을 뚫고 싶으면`, `long-wisdom-library-20260505-142934.mp4`는 `마음에 남는 좋은 글 10편 | 지혜로운 조각들`로 표시된다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 검증: `python -m py_compile dashboard.py`, `list_generated_videos(100)` 제목 확인, `youtube_metadata_for_video()` 제목 확인.

## 2026-05-06 13:51 KST

- YouTube 업로드 중 `invalid_grant: Token has been expired or revoked` 오류가 발생한 상태를 처리했다.
- `youtube_upload.py`에 `clear_youtube_token()`과 `is_invalid_grant_error()`를 추가했다.
- 토큰 refresh 또는 API 호출 중 `invalid_grant`/`expired or revoked`가 나오면 `youtube_token.json` 파일과 DB `YOUTUBE_TOKEN_JSON` 값을 지우고, “YouTube 인증 토큰이 만료되었거나 취소되었습니다. YouTube 인증을 다시 진행해 주세요.” 오류를 반환한다.
- 현재 남아 있던 폐기 토큰도 삭제했다. `/api/youtube/status`는 `token_exists=False`, `token_has_required_scopes=False`, `client_secrets_exists=True` 상태다.
- 8050 대시보드 프로세스를 재시작해 변경 사항을 실행 서버에 반영했다.
- 다음 업로드 전 생성영상/대시보드의 유튜브 정보 팝업에서 `유튜브 인증` 버튼을 눌러 재인증해야 한다.
- 검증: `python -m py_compile youtube_upload.py dashboard.py`, 토큰 파일/DB 토큰 정리 확인, `/api/youtube/status` 확인.
## 2026-05-07 KST

- 긴 영상 업로드에서 브라우저 `Failed to fetch`가 발생한 원인을 확인했다. `long-wisdom-library-20260508-150043.mp4`는 약 263.8MB이고, 동기식 업로드 중 브라우저/호스트 연결이 끊겨 `youtube_uploads` id 98은 `[WinError 10053] ... 연결 중단`, id 99는 `uploading`으로 남아 있었다.
- YouTube 업로드 API를 백그라운드 스레드 방식으로 변경했다. `POST /api/videos/<filename>/youtube/upload`는 업로드 행을 만든 뒤 즉시 202를 반환하고, 실제 `upload_video()`는 `run_youtube_upload_job()` 스레드에서 수행한다.
- 대시보드와 생성영상 페이지 JS에 업로드 상태 폴링(`pollYoutubeUpload`)을 추가했다. 긴 영상 업로드 중에는 5초마다 `/api/videos/<filename>/youtube/upload` 상태를 확인하고, `uploaded` 또는 `failed`가 될 때까지 기다린다.
- 서버 재시작 시 남아 있던 `uploading` 행은 `mark_interrupted_youtube_uploads()`가 `failed / server restarted before upload finished`로 정리한다. 재시작 후 id 99가 failed로 정리된 것을 확인했다.
- 대시보드 재시작 후 새 PID 22856, `/login` 200 확인. 사용자는 긴 영상 업로드를 직접 수동으로 할 필요 없이 대시보드에서 다시 `예약 전송`을 누르면 된다.

- 사용자가 생성영상 페이지가 아니라 대시보드 `보기/제작` 팝업에서 바로 업로드한다고 알려줘서 해당 경로를 명확히 보강했다. 대시보드 팝업의 `유튜브 전송` 버튼 문구를 `예약 전송`으로 바꾸고, 전송 중/완료 상태 문구도 예약 업로드 기준으로 변경했다.
- 대시보드 메타데이터 팝업 및 생성영상 페이지 업로드 요청 body에 `schedule_publish: youtubeSchedulePublish.checked`가 실제로 들어가도록 누락된 부분을 보완했다. 기존 대시보드 영상 생성 후 바로 전송 경로는 이미 `schedule_publish: true`였고 유지했다.
- 확인: 예약 업로드 이력은 `wisdom-library-552-20260508-145850.mp4`, 예약 시간 `2026-05-08T20:17:07+09:00`, URL `https://www.youtube.com/watch?v=_Tdf-YTxJ-s`. 대시보드 재시작 후 새 PID 15696, `/login` 200 확인.

- 예약 업로드 UI를 더 명확하게 수정했다. 예약 체크가 켜져 있으면 업로드 버튼 문구가 `예약 업로드`로 표시되고, 1차 클릭 후 `확인 후 예약`, 진행 중에는 `예약 중...`으로 바뀐다. 이미 예약된 업로드 이력은 상태 박스에 `예약 업로드됨`과 예약 시간을 보여준다.
- DB 확인 결과 `wisdom-library-552-20260508-145850.mp4`가 이미 예약 업로드되어 있으며 YouTube URL은 `https://www.youtube.com/watch?v=_Tdf-YTxJ-s`, 예약 시간은 `2026-05-08T20:17:07+09:00`이다.
- 템플릿 수정 후 대시보드 재시작, 새 PID 25808, `/login` 200 확인.

- YouTube 예약 업로드 기능을 추가했다. 업로드 모달에 `평일 점심/저녁 랜덤 예약 업로드` 체크박스를 기본 체크로 넣었고, 체크 상태로 업로드하면 오늘 날짜 기준 가능한 평일 슬롯 중 랜덤 시간을 잡는다.
- 예약 슬롯은 KST 기준 평일 12:00~13:00 또는 18:00~21:00이며, 현재 시각보다 최소 20분 뒤의 시간만 선택한다. 오늘 슬롯이 모두 지났거나 주말이면 다음 평일 슬롯으로 넘어간다.
- YouTube API 업로드 시 예약이면 `privacyStatus=private`와 `publishAt` UTC ISO 값을 함께 보낸다. 응답에는 예약 KST/UTC 시간이 포함되고 업로드 이력 DB `youtube_uploads.scheduled_publish_at`에 KST 시간이 저장된다.
- `youtube_uploads`에 `scheduled_publish_at` 컬럼을 추가했고 기존 DB에는 `init_db()`에서 자동 마이그레이션된다.
- 대시보드의 영상 생성 후 즉시 YouTube 전송 경로도 `schedule_publish: true`로 보내도록 바꿨다.
- 검증: `python -m py_compile youtube_upload.py db.py dashboard.py`, DB 컬럼 확인, 2026-05-08 금요일 기준 예약 시간 샘플 확인, 대시보드 재시작 후 새 PID 11100 및 `/login` 200 확인.

- 배경 영상 소스를 500개 체계로 확장했다. `backgrounds.ACTIVE_BACKGROUND_LIMIT=500`, 배경 관리 UI 문구/API limit/download 기본 target을 500으로 변경했다.
- `download_backgrounds.py --target 500 --per-page 20`으로 Pexels 배경 영상 400개를 추가 다운로드해 총 500개가 됐다. 파일은 `assets/backgrounds`에 500개, 약 7.05GB이며 누락 파일 0개 확인.
- 새로 받은 400개는 `2026-Q2` 분류로 묶고, 기존 100개와 함께 총 500개 모두 활성화했다.
- 랜덤 배경 선택은 활성 500개 풀에서 고르며, 최근 `video_jobs` 30개에서 사용한 `background_asset_id`는 우선 제외하도록 `render_video.random_background_asset_id()`를 변경했다.
- 검증: `python -m py_compile download_backgrounds.py backgrounds.py render_video.py dashboard.py`, 활성 500개 조회 확인, 랜덤 배경 선택 확인. 대시보드 재시작 후 새 PID 21844, `/login` 200 확인.

- CTA 문구 삽입 방식을 기존 영상 마지막 3초 오버레이에서, 영상 종료 후 검정 화면 3초를 추가하고 중앙에 CTA 문구를 넣는 방식으로 변경했다. 오디오가 있는 영상은 3초 무음 오디오를 함께 concat하고, 오디오 없는 영상도 비디오만 concat한다. 4초 오디오/무음 테스트 영상 모두 7초로 늘어나는 것을 확인했다. 대시보드 재시작 후 새 PID 30564, `/login` 200 확인.

- 마지막 CTA 자막 위치를 하단(`y=h-190`)에서 화면 중앙(`y=(h-text_h)/2`)으로 변경했다. `render_video.py` py_compile 및 4초 테스트 mp4 ffmpeg 처리 확인 후 대시보드를 재시작했다. 새 대시보드 PID는 34512이고 `/login` 200 응답을 확인했다.

- 서버 재시작 완료: 기존 8050 대시보드 `python.exe` PID 31068을 `Stop-Process`로 종료했고, `service/restart.ps1` 실행 후 새 대시보드가 PID 18516으로 올라왔다. `/login` 응답 200 확인.

- 서버 재시작 이슈 확인: `scripts/stop_tasks.ps1`는 작업 스케줄러 태스크만 종료한다. 현재 대시보드는 작업 스케줄러가 아니라 `python.exe` PID 31068로 직접 실행 중이라 해당 스크립트가 멈추지 못했다. 일반 권한에서 `taskkill /PID 31068 /F`는 Access denied가 나와 관리자 권한 PowerShell 또는 작업 관리자 종료가 필요하다.

- 제목/태그 자동화를 Claude API 방식에서 완전 로컬 규칙 기반으로 교체했다.
- `youtube_metadata_ai.py`에서 Anthropic/Claude 호출 코드와 `ANTHROPIC_API_KEY` 의존성을 모두 제거했다.
- 제목 생성은 사용자가 준 `EMOTION_PATTERNS` 키워드 매핑을 사용한다. 매칭 시 `공감형 앞머리 | 첫 문장…` 형식으로 만들고, 매칭이 없으면 첫 문장 기반으로 만든다.
- 태그 생성은 `BASE_TAGS`, `TAG_POOL`, `KEYWORD_TO_CATEGORY` 기반으로 카테고리당 2개씩 추가하고 최대 15개로 중복 제거한다.
- 대시보드/DB 설정 키에서 `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`을 제거했다.
- CTA 마지막 자막은 `이 문장이 오늘 필요하셨나요? 댓글로 알려주세요`, `저장하고 싶은 문장이었나요?`, `공감되셨다면 구독 부탁드려요` 중 랜덤으로 나온다.
- 검증: `python -m py_compile youtube_metadata_ai.py db.py dashboard.py render_video.py`, 사용자가 준 예시 3개 제목/태그 생성 확인.

- 유튜브 업로드 메타데이터 자동화를 추가했다.
- 새 `youtube_metadata_ai.py` 모듈에서 `generate_title()`과 `generate_tags()`를 제공한다. 외부 AI API 없이 로컬 규칙 기반으로 제목/태그를 만든다.
- 숏츠 업로드 메타데이터는 글귀 본문 기반 공감형 제목을 만들고 뒤에 `| 지혜로운 조각들`을 붙인다. 태그는 고정 태그와 내용 기반 태그를 합쳐 자동 생성한다.
- 새 영상 생성 마지막 단계에 `render_video.add_cta_subtitle()`를 추가해 영상 마지막 3초 동안 `이 문장이 오늘 필요하셨나요?` 댓글 유도 자막을 ffmpeg `drawtext`로 넣는다.
- `youtube_uploads` DB에 `view_count`, `like_count`, `comment_count`, `stats_checked_at` 컬럼을 추가했다. `init_db()`가 기존 DB에도 컬럼을 자동 추가한다.
- YouTube 통계 수집용 `get_video_statistics()`를 추가하고, 대시보드에 `refresh_youtube_upload_stats()`와 `youtube_performance_patterns()`를 추가했다. 업로드 24~72시간 경과 영상 중 아직 조회수 없는 항목을 갱신한다.
- API `POST /api/youtube/stats/refresh`, `GET /api/youtube/stats/patterns`를 추가했다. 대시보드 서버 실행 시 백그라운드에서 `YOUTUBE_STATS_INTERVAL_SECONDS`(기본 3600초)마다 통계 갱신 루프가 돈다.
- 설정 키에 `YOUTUBE_STATS_INTERVAL_SECONDS`를 추가했다.
- 검증: `python -m py_compile youtube_metadata_ai.py db.py youtube_upload.py render_video.py dashboard.py`, DB 컬럼 마이그레이션 확인, 제목/태그 fallback 확인, 4초 테스트 mp4에서 CTA 자막 ffmpeg 처리 확인.
- 현재 환경 정책상 기존 8050 프로세스 강제 종료/백그라운드 재시작 명령이 차단됐다. `/login`은 200 응답 중이나 실행 중 서버가 새 코드를 반영하려면 사용자가 대시보드 프로세스를 재시작해야 할 수 있다.

- "데이터를 불러오지 못했습니다" 원인을 확인했다. 쉘에서 `/api/stats`가 401을 반환했고, Flask test client에서 인증 세션을 넣으면 `/api/stats`, `/api/logs?limit=10`, `/api/videos?limit=10`이 모두 200으로 정상 응답했다. 즉 DB/API 장애가 아니라 브라우저 로그인 세션이 무효화된 상태였다.
- 재시작 때마다 `app.secret_key`가 랜덤 생성되어 기존 로그인 쿠키가 계속 깨지는 문제가 있어, `dashboard_secret_key()`를 추가하고 DB 설정 `DASHBOARD_SECRET_KEY` 또는 환경변수를 우선 사용하도록 변경했다. 값이 없으면 최초 1회 생성해 DB에 저장한다.
- 설정 키 목록에 `DASHBOARD_SECRET_KEY`를 추가했다. 앞으로는 재시작 후에도 같은 키를 사용하므로 로그인 세션이 불필요하게 자주 깨지지 않는다.
- 검증: `python -m py_compile dashboard.py`, `from dashboard import app`로 secret key 로드 확인, `/login` 200 확인, 인증된 test client API 3개 200 확인. 브라우저는 기존 쿠키가 이미 깨졌기 때문에 한 번 새로 로그인해야 한다.

- 대시보드 글 목록이 "데이터를 불러오지 못했습니다"로 표시되는 추가 원인을 수정했다. `templates/dashboard.html`의 `loadLogs()`에서 글 목록이 있는 경우 정의되지 않은 `upload` 변수를 검사해 `ReferenceError`가 발생했고, 이 때문에 정상 `/api/logs` 응답을 받아도 화면 렌더링이 실패했다.
- `loadLogs()`의 잘못된 `upload` 실패 검사 분기를 제거했다. API 응답 처리는 공통 `readApiJson()` 헬퍼로 정리해 401 또는 `authentication_required`가 오면 `/login?next=...`로 이동하도록 했다.
- `loadStats()`, `loadVideoLibrary()`, `loadLogs()`가 `readApiJson()`을 사용하도록 바꿔 HTTP 오류를 명확히 처리한다.
- 기존 8050 리스닝 대시보드 PID 24024, 27452를 종료하고 새 대시보드를 PID 4900으로 재시작했다.
- 검증: `python -m py_compile dashboard.py`, Flask test client 대시보드 렌더링에서 `readApiJson` 포함 및 잘못된 `else if (upload && upload.status...)` 제거 확인, 인증된 `/api/logs?limit=10` 200 및 글 10개 확인, `/login` 200 확인.

- BGM 자동 삽입 기능을 추가했다. `assets/bgm` 폴더 아래의 `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.ogg` 파일을 로컬 BGM 라이브러리로 스캔한다. 음원이 없으면 기존처럼 BGM 없이 생성한다.
- `bgm.py`를 추가해 `bgm_assets` DB 테이블 등록/조회와 최근 사용 BGM 10개 우선 제외 랜덤 선택을 처리한다.
- `video_jobs`에 `bgm_asset_id` 컬럼을 추가하고, 영상 생성 완료 시 사용한 BGM ID를 저장한다.
- `render_video.py`에 `mix_bgm()`을 추가했다. CTA 검정 화면까지 포함한 최종 영상 길이에 맞춰 BGM을 loop/trim하고, fade in/out 후 기존 TTS 오디오와 낮은 볼륨으로 믹싱한다. 기본 볼륨은 TTS 영상 `0.10`, 무음 영상 `0.14`다.
- 운영 설정에 `VIDEO_BGM_ENABLED`, `VIDEO_BGM_TTS_VOLUME`, `VIDEO_BGM_ONLY_VOLUME`을 추가했고 대시보드/설정 페이지에서 조정할 수 있다. 끄면 다음 생성 영상부터 BGM을 넣지 않는다.
- 검증: `python -m py_compile bgm.py db.py render_video.py dashboard.py`, DB 마이그레이션에서 `bgm_assets`와 `video_jobs.bgm_asset_id` 확인, Flask test client 설정값/대시보드 입력 확인, 2초 테스트 mp4와 wav로 ffmpeg BGM 믹싱 후 오디오 트랙 생성 확인.

- BGM 설정이 메인 대시보드에서 바로 보이지 않는 문제가 있어 `templates/dashboard.html` 본문에 항상 표시되는 `BGM` 빠른 설정 패널을 추가했다. 위치는 백업 버튼 아래이며, `자동 삽입`, `TTS 볼륨`, `무음 볼륨`, `저장`을 바로 조작한다.
- 빠른 BGM 패널은 기존 `/api/settings`를 사용해 `VIDEO_BGM_ENABLED`, `VIDEO_BGM_TTS_VOLUME`, `VIDEO_BGM_ONLY_VOLUME`을 저장하고, `loadSettings()` 시 현재 값을 동기화한다.
- 검증: `python -m py_compile dashboard.py`, Flask test client 렌더링에서 `quickBgmEnabled`, `saveBgmSettings`, `saveQuickBgmSettings` 포함 확인. 대시보드 재시작 후 PID 20152, `/login` 200, `/api/settings` 200 및 `VIDEO_BGM_ENABLED=1` 확인.
