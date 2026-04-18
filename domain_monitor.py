from __future__ import annotations

import html
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

DOMAINS = [
    "https://d31juwdie5cd9r.cloudfront.net",
    "https://d3vvyp77jrl6x3.cloudfront.net",
    "https://pandola.tv",
    "https://pdl01.cc",
    "https://pdl10.cc",
]
SUCCESS_RATE_THRESHOLD = 0.90
SUCCESS_STATUS_CODE = 200
MAX_RESPONSE_TIME_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 10
TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TG_CHAT_ID"]
LOG_FILE_PATH = Path(__file__).with_name("domain_monitor.log")
USER_AGENT = "domain-monitor/1.0"


@dataclass
class CheckResult:
    domain: str
    checked_at: str
    success: bool
    status_code: int | None
    response_time_ms: int | None
    failure_reason: str | None


def setup_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def now_string() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def check_domain(session: requests.Session, domain: str) -> CheckResult:
    started_at = time.perf_counter()
    checked_at = now_string()

    try:
        response = session.get(domain, timeout=REQUEST_TIMEOUT_SECONDS)
        response_time_ms = int((time.perf_counter() - started_at) * 1000)
        success = response.status_code == SUCCESS_STATUS_CODE and response_time_ms < MAX_RESPONSE_TIME_SECONDS * 1000
        failure_reason = None

        if not success:
            if response.status_code != SUCCESS_STATUS_CODE:
                failure_reason = f"HTTP {response.status_code}"
            else:
                failure_reason = f"response time >= {MAX_RESPONSE_TIME_SECONDS}s"

        return CheckResult(
            domain=domain,
            checked_at=checked_at,
            success=success,
            status_code=response.status_code,
            response_time_ms=response_time_ms,
            failure_reason=failure_reason,
        )
    except requests.Timeout:
        response_time_ms = int((time.perf_counter() - started_at) * 1000)
        return CheckResult(
            domain=domain,
            checked_at=checked_at,
            success=False,
            status_code=None,
            response_time_ms=response_time_ms,
            failure_reason="request timeout",
        )
    except requests.RequestException as exc:
        response_time_ms = int((time.perf_counter() - started_at) * 1000)
        return CheckResult(
            domain=domain,
            checked_at=checked_at,
            success=False,
            status_code=None,
            response_time_ms=response_time_ms,
            failure_reason=str(exc),
        )


def calculate_success_rate(results: list[CheckResult]) -> float:
    if not results:
        return 0.0
    success_count = sum(1 for result in results if result.success)
    return success_count / len(results)


def build_alert_message(results: list[CheckResult], success_rate: float) -> str:
    failed_results = [result for result in results if not result.success]
    lines = [
        "<b>[告警] 域名连通率低于 90%</b>",
        f"整体连通率: {success_rate * 100:.2f}%",
        "失败明细:",
    ]

    for result in failed_results:
        response_time_ms = "N/A" if result.response_time_ms is None else str(result.response_time_ms)
        lines.append(
            "- "
            f"失败时间: {html.escape(result.checked_at)} | "
            f"域名: {html.escape(result.domain)} | "
            f"失败原因: {html.escape(result.failure_reason or 'unknown')} | "
            f"响应时间(ms): {html.escape(response_time_ms)}"
        )

    return "\n".join(lines)


def send_telegram_message(session: requests.Session, message: str) -> None:
    response = session.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=TELEGRAM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API returned ok=false: {payload}")


def send_telegram_alert(session: requests.Session, message: str) -> None:
    max_length = 3500
    if len(message) <= max_length:
        send_telegram_message(session, message)
        return

    lines = message.splitlines()
    chunk: list[str] = []
    chunk_length = 0

    for line in lines:
        line_length = len(line) + 1
        if chunk and chunk_length + line_length > max_length:
            send_telegram_message(session, "\n".join(chunk))
            chunk = [line]
            chunk_length = len(line)
        else:
            chunk.append(line)
            chunk_length += line_length

    if chunk:
        send_telegram_message(session, "\n".join(chunk))


def main() -> int:
    setup_logging()
    session = build_session()
    results = [check_domain(session, domain) for domain in DOMAINS]

    for result in results:
        logging.info(
            "domain=%s success=%s status=%s response_time_ms=%s reason=%s",
            result.domain,
            result.success,
            result.status_code,
            result.response_time_ms,
            result.failure_reason,
        )

    success_rate = calculate_success_rate(results)
    logging.info("success_rate=%.2f%%", success_rate * 100)

    if success_rate < SUCCESS_RATE_THRESHOLD:
        message = build_alert_message(results, success_rate)
        send_telegram_alert(session, message)
        logging.warning("Alert sent to Telegram")
    else:
        logging.info("No alert sent")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
