# CGV 예매 오픈 감시기 (Telegram) - Windows 실행 가이드

## 1) Python 설치
- Python 3.10+ 설치 후 `Add python.exe to PATH` 옵션을 켭니다.

## 2) 프로젝트 준비
```powershell
git clone https://github.com/0w0i0n0g0/cgv-open-push.git
cd cgv-open-push
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 3) 환경변수 설정
```powershell
copy .env.example .env
notepad .env
```

필수 값:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

기본 감시 대상(요구사항 반영):
- 영화명: `프로젝트 헤일메리`
- 극장명: `용산아이파크몰`
- 날짜: `2026-03-29`
- 포맷: `IMAX`

CGV 조회 방식은 2가지입니다.
1. **RIA API 방식**: `CGV_PAYLOAD_JSON`, `CGV_HEADERS_JSON`, `CGV_COOKIES_JSON`을 입력
2. **HTML 폴백 방식**: `BOOKING_PAGE_URL` 입력

## 4) 실행
```powershell
python cgv_booking_monitor.py
```

## 동작 요약
- 시작 시 Telegram: `시작되었습니다.`
- 종료 시 Telegram: `종료되었습니다.`
- 예매 가능 상태 첫 감지 시 1회 알림 전송
- 에러 발생 시 Telegram: `에러가 발생했습니다.` 전송 시도
- 마지막 상태는 `state/last_status.json`에 저장되어 중복 알림을 방지
- 오류/예외(네트워크, HTML 파싱, Telegram 전송 실패)는 `monitor.log`에 기록
