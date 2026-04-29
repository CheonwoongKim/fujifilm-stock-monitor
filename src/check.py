"""Fujifilm Korea product stock checker — variant aware (Silver/Black).

Loads the product page with a headless browser, reads each color variant's
`data-soldout` flag, and triggers a Telegram alert whenever a variant
transitions from sold out to in stock. The alert names exactly which color
is available so the user can jump straight to checkout.

Environment variables:
    PRODUCT_URL          target product page (default: X100VI page id 1330)
    STATE_PATH           path to state.json (default: ./state.json)
    CYCLE_STATE_PATH     path to per-run cycle state json (optional)
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import notify

if TYPE_CHECKING:
    from playwright.sync_api import Page

DEFAULT_URL = "https://www.fujifilm-korea.co.kr/products/id/1330"
DEFAULT_STATE_PATH = "state.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class VariantStatus:
    name: str       # full name from DOM, e.g. "X100VI Silver"
    short: str      # Korean label, e.g. "실버"
    in_stock: bool
    price: str      # raw price text or "품절"


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


def update_cycle_state(path: Path | None,
                       *,
                       checked_at: str,
                       variants: list[VariantStatus],
                       transitions: list[VariantStatus]) -> None:
    if path is None:
        return

    existing = load_previous_state(path)
    checks = int(existing.get("checks", 0)) + 1
    saw_any_in_stock = bool(existing.get("saw_any_in_stock", False)) or any(
        variant.in_stock for variant in variants
    )
    alerts_sent = int(existing.get("alerts_sent", 0)) + (1 if transitions else 0)
    alerted_variants = set(existing.get("alerted_variants", []))
    alerted_variants.update(variant.short for variant in transitions)

    cycle_state = {
        "started_at": existing.get("started_at") or checked_at,
        "last_checked_at": checked_at,
        "checks": checks,
        "saw_any_in_stock": saw_any_in_stock,
        "alerts_sent": alerts_sent,
        "alerted_variants": sorted(alerted_variants),
        "latest_variants": [asdict(variant) for variant in variants],
    }
    save_state(path, cycle_state)


def short_label(full_name: str) -> str:
    lower = full_name.lower()
    if "silver" in lower:
        return "실버"
    if "black" in lower:
        return "블랙"
    return full_name


def classify_variants(page: Page) -> list[VariantStatus]:
    """Read each color variant's stock state from the buy panel.

    Each variant is rendered as `.selected-product__item` with a
    `data-soldout="true|false"` attribute that we trust as the source of
    truth. We also capture the visible price text so the alert can show
    the actual price.
    """
    items = page.locator(".selected-product__item")
    count = items.count()
    if count == 0:
        raise RuntimeError("no .selected-product__item elements found")

    results: list[VariantStatus] = []
    for index in range(count):
        item = items.nth(index)
        soldout_attr = (item.get_attribute("data-soldout") or "").strip().lower()
        name = (item.locator(".selected-product__name").text_content() or "").strip()
        price = (item.locator(".selected-product__price").text_content() or "").strip()
        results.append(
            VariantStatus(
                name=name,
                short=short_label(name),
                in_stock=soldout_attr != "true",
                price=price,
            )
        )
    return results


def dump_debug(page: Page, dump_dir: Path) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    html_path = dump_dir / f"page-{timestamp}.html"
    png_path = dump_dir / f"page-{timestamp}.png"
    html_path.write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(png_path), full_page=True)
    print(f"[debug] dumped {html_path} and {png_path}", file=sys.stderr)


def fetch(url: str, debug_dir: Path | None) -> list[VariantStatus]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

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
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(3_000)

        results = classify_variants(page)

        if debug_dir is not None:
            dump_debug(page, debug_dir)

        context.close()
        browser.close()
        return results


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


def compose_alert(newly_in_stock: list[VariantStatus],
                  all_variants: list[VariantStatus],
                  checked_at: str) -> str:
    if len(newly_in_stock) == 1:
        head = f"🔥 <b>X100VI {newly_in_stock[0].short} 재고 입고!</b>"
    else:
        labels = "/".join(v.short for v in newly_in_stock)
        head = f"🔥 <b>X100VI {labels} 재고 입고!</b>"

    detail_lines = []
    for v in all_variants:
        mark = "✅" if v.in_stock else "❌"
        detail_lines.append(f"{mark} <b>{v.short}</b> — {v.price}")

    return (
        f"{head}\n"
        "지금 바로 결제하세요. 보통 5~10분 안에 다시 품절됩니다.\n\n"
        + "\n".join(detail_lines)
        + f"\n\n<i>감지: {checked_at} UTC</i>"
    )


def detect_transitions(previous_variants: dict,
                       current: list[VariantStatus]) -> list[VariantStatus]:
    transitions: list[VariantStatus] = []
    for variant in current:
        prev = previous_variants.get(variant.name) or {}
        was_in_stock = bool(prev.get("in_stock", False))
        if variant.in_stock and not was_in_stock:
            transitions.append(variant)
    return transitions


def main() -> int:
    url = os.environ.get("PRODUCT_URL", DEFAULT_URL)
    state_path = Path(os.environ.get("STATE_PATH", DEFAULT_STATE_PATH))
    cycle_state_env = os.environ.get("CYCLE_STATE_PATH")
    cycle_state_path = Path(cycle_state_env) if cycle_state_env else None
    debug_dir_env = os.environ.get("DEBUG_DUMP_DIR")
    debug_dir = Path(debug_dir_env) if debug_dir_env else None
    heartbeat_hours = int(os.environ.get("HEARTBEAT_HOURS", "0"))

    previous = load_previous_state(state_path)
    previous_variants: dict = previous.get("variants", {})

    attempts = 3
    last_error: Exception | None = None
    variants: list[VariantStatus] | None = None
    for attempt in range(1, attempts + 1):
        try:
            variants = fetch(url, debug_dir if attempt == 1 else None)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[warn] attempt {attempt}/{attempts} failed: {exc}", file=sys.stderr)
            time.sleep(5 * attempt)

    if variants is None:
        message = (
            "⚠️ <b>재고 확인 실패</b>\n"
            f"3회 시도 모두 실패했습니다.\n<code>{last_error}</code>"
        )
        try:
            notify.send(message, product_url=url, silent=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] failed to notify error: {exc}", file=sys.stderr)
        return 1

    checked_at = now_iso()
    for variant in variants:
        print(f"[info] {variant.name} in_stock={variant.in_stock} price='{variant.price}'")

    transitions = detect_transitions(previous_variants, variants)

    new_state = {
        "checked_at": checked_at,
        "last_heartbeat_at": previous.get("last_heartbeat_at"),
        "variants": {variant.name: asdict(variant) for variant in variants},
    }

    if transitions:
        message = compose_alert(transitions, variants, checked_at)
        notify.send(message, product_url=url)
        new_state["last_alerted_at"] = checked_at

    if should_send_heartbeat(previous, heartbeat_hours):
        summary = ", ".join(
            f"{v.short}={'IN' if v.in_stock else 'OUT'}" for v in variants
        )
        notify.send(
            f"💤 모니터 정상 작동 중 ({summary})",
            product_url=url,
            silent=True,
        )
        new_state["last_heartbeat_at"] = checked_at

    save_state(state_path, new_state)
    update_cycle_state(
        cycle_state_path,
        checked_at=checked_at,
        variants=variants,
        transitions=transitions,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
