"""Fujifilm Korea product stock checker.

Loads the product page with a headless browser, classifies stock state,
compares against the previous state, and triggers a Telegram alert on the
out-of-stock -> in-stock transition.

Environment variables:
    PRODUCT_URL          target product page (default: X100VI page id 1330)
    STATE_PATH           path to state.json (default: ./state.json)
    DEBUG_DUMP_DIR       if set, render dumps go here (HTML + screenshot)
    HEARTBEAT_HOURS      send a "still alive" silent ping every N hours (default: 0 = off)
    TELEGRAM_BOT_TOKEN   required for notifications
    TELEGRAM_CHAT_ID     required for notifications
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

import notify

DEFAULT_URL = "https://www.fujifilm-korea.co.kr/products/id/1330"
DEFAULT_STATE_PATH = "state.json"

OUT_OF_STOCK_KEYWORDS = ("품절", "일시품절", "재입고", "SOLD OUT", "Sold Out")
IN_STOCK_BUTTON_KEYWORDS = ("구매하기", "바로구매", "장바구니")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class CheckResult:
    status: str  # "IN_STOCK" | "OUT_OF_STOCK" | "UNKNOWN"
    detail: str
    checked_at: str

    def to_dict(self) -> dict:
        return {"status": self.status, "detail": self.detail, "checked_at": self.checked_at}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_previous_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def classify(page: Page) -> CheckResult:
    """Best-effort stock classification from rendered DOM.

    Strategy:
        1. If any OUT keyword is visible in the buy area -> OUT_OF_STOCK.
        2. Else if a buy/cart button exists and is enabled -> IN_STOCK.
        3. Else UNKNOWN (treated as OUT to avoid false alerts).
    """
    body_text = page.locator("body").inner_text(timeout=5000)

    out_hits = [kw for kw in OUT_OF_STOCK_KEYWORDS if kw in body_text]
    if out_hits:
        return CheckResult("OUT_OF_STOCK", f"keywords={out_hits}", now_iso())

    for keyword in IN_STOCK_BUTTON_KEYWORDS:
        candidates = page.get_by_text(keyword, exact=False)
        count = candidates.count()
        for index in range(count):
            button = candidates.nth(index)
            try:
                if button.is_visible() and button.is_enabled():
                    return CheckResult(
                        "IN_STOCK", f"button='{keyword}' enabled", now_iso()
                    )
            except Exception:
                continue

    return CheckResult("UNKNOWN", "no decisive signal", now_iso())


def dump_debug(page: Page, dump_dir: Path) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = dump_dir / f"page-{timestamp}.html"
    png_path = dump_dir / f"page-{timestamp}.png"
    html_path.write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(png_path), full_page=True)
    print(f"[debug] dumped {html_path} and {png_path}", file=sys.stderr)


def fetch(url: str, debug_dir: Path | None) -> CheckResult:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="ko-KR",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=45_000)
        except PlaywrightTimeoutError:
            # Fall back to a looser wait - the page may keep firing analytics.
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)

        result = classify(page)

        if debug_dir is not None:
            dump_debug(page, debug_dir)

        context.close()
        browser.close()
        return result


def should_send_heartbeat(previous: dict, hours: int) -> bool:
    if hours <= 0:
        return False
    last = previous.get("last_heartbeat_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return elapsed >= hours * 3600


def main() -> int:
    url = os.environ.get("PRODUCT_URL", DEFAULT_URL)
    state_path = Path(os.environ.get("STATE_PATH", DEFAULT_STATE_PATH))
    debug_dir_env = os.environ.get("DEBUG_DUMP_DIR")
    debug_dir = Path(debug_dir_env) if debug_dir_env else None
    heartbeat_hours = int(os.environ.get("HEARTBEAT_HOURS", "0"))

    previous = load_previous_state(state_path)
    previous_status = previous.get("status", "UNKNOWN")

    attempts = 3
    last_error: Exception | None = None
    result: CheckResult | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = fetch(url, debug_dir if attempt == 1 else None)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[warn] attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)
            time.sleep(5 * attempt)

    if result is None:
        message = (
            "⚠️ <b>재고 확인 실패</b>\n"
            f"3회 시도 모두 실패했습니다.\n<code>{last_error}</code>"
        )
        try:
            notify.send(message, product_url=url, silent=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] failed to notify error: {exc}", file=sys.stderr)
        return 1

    print(f"[info] status={result.status} detail={result.detail}")

    transitioned_to_in_stock = (
        result.status == "IN_STOCK" and previous_status != "IN_STOCK"
    )

    new_state = {
        "status": result.status,
        "detail": result.detail,
        "checked_at": result.checked_at,
        "last_heartbeat_at": previous.get("last_heartbeat_at"),
    }

    if transitioned_to_in_stock:
        message = (
            "🔥 <b>X100VI 재고 입고!</b>\n"
            "지금 바로 결제하세요. 보통 5~10분 안에 다시 품절됩니다.\n\n"
            f"<i>감지: {result.checked_at} UTC</i>"
        )
        notify.send(message, product_url=url)
        new_state["last_alerted_at"] = result.checked_at

    if should_send_heartbeat(previous, heartbeat_hours):
        notify.send(
            f"💤 모니터 정상 작동 중 ({result.status})",
            product_url=url,
            silent=True,
        )
        new_state["last_heartbeat_at"] = result.checked_at

    save_state(state_path, new_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
