import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


DEFAULT_API_URL = "http://ticket.cgv.co.kr/CGV2011/RIA/CJ000.aspx/CJ_TICKET_SCHEDULE_TOTAL_PLAY_YMD"
STATE_AVAILABLE = "AVAILABLE"
STATE_BLOCKED = "BLOCKED"
STATE_UNKNOWN = "UNKNOWN"


@dataclass
class MonitorConfig:
    telegram_bot_token: str
    telegram_chat_id: str
    movie_name: str
    theater_name: str
    play_date: str
    screen_format: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    state_file: Path
    api_url: str
    cgv_cookies: Dict[str, str]
    cgv_headers: Dict[str, str]
    cgv_payload: Dict[str, Any]
    booking_page_url: str


class CgvMonitor:
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.session = requests.Session()
        self.running = True
        self.last_state = self._load_last_state()

    def run(self) -> None:
        self._send_telegram_safe("시작되었습니다.")
        logging.info("모니터링 시작: %s / %s / %s / %s", self.config.movie_name, self.config.theater_name, self.config.play_date, self.config.screen_format)

        while self.running:
            try:
                state, detail = self._check_booking_state()
                logging.info("조회 결과 state=%s detail=%s", state, detail)

                if state != self.last_state:
                    self._save_last_state(state)

                if state == STATE_AVAILABLE and self.last_state != STATE_AVAILABLE:
                    message = (
                        "예매 가능 상태가 감지되었습니다!\n"
                        f"영화: {self.config.movie_name}\n"
                        f"극장: {self.config.theater_name}\n"
                        f"날짜: {self.config.play_date}\n"
                        f"포맷: {self.config.screen_format}\n"
                        f"상세: {detail}"
                    )
                    self._send_telegram_safe(message)

                self.last_state = state
            except Exception:
                logging.exception("모니터링 루프에서 처리되지 않은 오류가 발생했습니다.")
                self._send_telegram_safe("에러가 발생했습니다.")

            time.sleep(self.config.poll_interval_seconds)

        self._send_telegram_safe("종료되었습니다.")
        logging.info("모니터링 종료")

    def stop(self) -> None:
        self.running = False

    def _check_booking_state(self) -> tuple[str, str]:
        # 1) 저장소(v1)에서 사용 중인 RIA API 우선 시도
        if self.config.cgv_payload:
            try:
                state, detail = self._check_with_ria_api()
                if state != STATE_UNKNOWN:
                    return state, detail
            except requests.RequestException:
                logging.exception("CGV RIA API 요청 실패")
            except Exception:
                logging.exception("CGV RIA API 파싱 실패")

        # 2) HTML 페이지 기반 폴백
        try:
            return self._check_with_booking_page()
        except requests.RequestException:
            logging.exception("CGV 예약 페이지 요청 실패")
            raise

    def _check_with_ria_api(self) -> tuple[str, str]:
        response = self.session.post(
            self.config.api_url,
            json=self.config.cgv_payload,
            headers=self.config.cgv_headers or None,
            cookies=self.config.cgv_cookies or None,
            timeout=self.config.request_timeout_seconds,
            verify=False,
        )
        response.raise_for_status()

        body = response.json()
        xml_payload = body.get("d", {}).get("DATA", "")
        if not xml_payload:
            return STATE_UNKNOWN, "RIA API 응답에 DATA가 비어 있습니다."

        return _infer_state_from_text(xml_payload)

    def _check_with_booking_page(self) -> tuple[str, str]:
        if not self.config.booking_page_url:
            return STATE_UNKNOWN, "BOOKING_PAGE_URL 미설정"

        response = self.session.get(
            self.config.booking_page_url,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        return _infer_state_from_text(text)

    def _send_telegram_safe(self, text: str) -> None:
        try:
            self._send_telegram(text)
        except Exception:
            logging.exception("Telegram 전송 실패")

    def _send_telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        response = self.session.post(
            url,
            data={"chat_id": self.config.telegram_chat_id, "text": text},
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API 실패 응답: {payload}")

    def _load_last_state(self) -> str:
        if not self.config.state_file.exists():
            return STATE_UNKNOWN

        try:
            data = json.loads(self.config.state_file.read_text(encoding="utf-8"))
            return data.get("last_state", STATE_UNKNOWN)
        except Exception:
            logging.exception("상태 파일 로드 실패")
            return STATE_UNKNOWN

    def _save_last_state(self, state: str) -> None:
        try:
            self.config.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.config.state_file.write_text(
                json.dumps({"last_state": state, "updated_at": int(time.time())}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logging.exception("상태 파일 저장 실패")


def _infer_state_from_text(text: str) -> tuple[str, str]:
    lowered = text.lower()

    keywords_available = ["예매가능", "예매 가능", "booking", "buy ticket"]
    keywords_blocked = ["예매불가", "예매 불가", "예매준비중", "준비중", "미오픈"]

    for kw in keywords_blocked:
        if kw in text or kw in lowered:
            return STATE_BLOCKED, f"차단 키워드 감지: {kw}"

    for kw in keywords_available:
        if kw in text or kw in lowered:
            return STATE_AVAILABLE, f"가능 키워드 감지: {kw}"

    return STATE_UNKNOWN, "상태 키워드를 찾지 못했습니다."


def _parse_json_env(name: str) -> Dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} 값이 JSON 형식이 아닙니다: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError(f"{name} 값은 JSON object여야 합니다.")
    return obj


def load_config() -> MonitorConfig:
    load_dotenv()

    return MonitorConfig(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        movie_name=os.getenv("CGV_MOVIE_NAME", "프로젝트 헤일메리"),
        theater_name=os.getenv("CGV_THEATER_NAME", "용산아이파크몰"),
        play_date=os.getenv("CGV_PLAY_DATE", "2026-03-29"),
        screen_format=os.getenv("CGV_SCREEN_FORMAT", "IMAX"),
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
        state_file=Path(os.getenv("STATE_FILE", "state/last_status.json")),
        api_url=os.getenv("CGV_API_URL", DEFAULT_API_URL),
        cgv_cookies=_parse_json_env("CGV_COOKIES_JSON"),
        cgv_headers=_parse_json_env("CGV_HEADERS_JSON"),
        cgv_payload=_parse_json_env("CGV_PAYLOAD_JSON"),
        booking_page_url=os.getenv("BOOKING_PAGE_URL", ""),
    )


def validate_config(config: MonitorConfig) -> None:
    missing = []
    if not config.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not config.telegram_chat_id:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        raise ValueError(f"필수 환경변수가 누락되었습니다: {', '.join(missing)}")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("monitor.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]


def main() -> int:
    setup_logging()

    try:
        config = load_config()
        validate_config(config)
    except Exception:
        logging.exception("설정 로딩 실패")
        return 1

    monitor = CgvMonitor(config)

    def _signal_handler(signum, frame):
        logging.info("종료 시그널 수신: %s", signum)
        monitor.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    monitor.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
