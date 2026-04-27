"""Telegram notifier for stock alerts."""
from __future__ import annotations

import os

import httpx

API_BASE = "https://api.telegram.org"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is not set")
    return value


def send(text: str, *, product_url: str | None = None, silent: bool = False) -> None:
    token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")

    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "disable_notification": silent,
    }

    if product_url:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "상품 페이지 열기", "url": product_url}],
            ]
        }

    url = f"{API_BASE}/bot{token}/sendMessage"
    with httpx.Client(timeout=15) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()


if __name__ == "__main__":
    send(
        "✅ <b>테스트 알림</b>\n알림 채널이 정상 작동합니다.",
        product_url="https://www.fujifilm-korea.co.kr/products/id/1330",
        silent=True,
    )
