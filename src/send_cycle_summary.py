"""Send an end-of-cycle Telegram summary for the daily polling window."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import notify

DEFAULT_URL = "https://www.fujifilm-korea.co.kr/products/id/1330"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def format_latest_variants(cycle_state: dict) -> str:
    variants = cycle_state.get("latest_variants") or []
    if not variants:
        return "상태 정보 없음"
    parts = []
    for variant in variants:
        mark = "✅" if variant.get("in_stock") else "❌"
        parts.append(f"{mark} {variant.get('short', variant.get('name', '알 수 없음'))}")
    return " / ".join(parts)


def build_cycle_key(*, window_label: str, timezone_name: str, now: datetime | None = None) -> str:
    current = now or datetime.now(ZoneInfo(timezone_name))
    return f"{current.date().isoformat()}::{window_label}"


def main() -> int:
    product_url = os.environ.get("PRODUCT_URL", DEFAULT_URL)
    cycle_state_path = Path(os.environ["CYCLE_STATE_PATH"])
    cycle_state = load_json(cycle_state_path)
    state_path = Path(os.environ.get("STATE_PATH", "state.json"))
    persisted_state = load_json(state_path)

    skipped = os.environ.get("SKIP_DENSE_POLL") == "1"
    cycle_end_reason = (os.environ.get("CYCLE_END_REASON") or "completed").strip()
    window_label = (os.environ.get("WINDOW_LABEL") or "09:50~10:10 KST").strip()
    timezone_name = (os.environ.get("WINDOW_TIMEZONE") or "Asia/Seoul").strip()
    cycle_key = build_cycle_key(window_label=window_label, timezone_name=timezone_name)
    checks = int(cycle_state.get("checks", 0))
    alerts_sent = int(cycle_state.get("alerts_sent", 0))
    saw_any_in_stock = bool(cycle_state.get("saw_any_in_stock", False))
    latest_summary = format_latest_variants(cycle_state)
    started_at = cycle_state.get("started_at") or "-"
    last_checked_at = cycle_state.get("last_checked_at") or "-"

    if persisted_state.get("last_cycle_summary_key") == cycle_key:
        print(f"[info] cycle summary already sent for {cycle_key}")
        return 0

    if skipped:
        text = (
            "⚠️ <b>재고 모니터링 윈도우를 놓쳤습니다</b>\n"
            f"{window_label} 구간이 지난 뒤 워크플로우가 시작되어 오늘 폴링은 건너뛰었습니다.\n"
            f"사유: <code>{cycle_end_reason}</code>"
        )
        notify.send(text, product_url=product_url)
    elif checks == 0:
        text = (
            "⚠️ <b>재고 모니터링이 정상 완료되지 않았습니다</b>\n"
            f"{window_label} 동안 실행 기록이 없습니다.\n"
            f"사유: <code>{cycle_end_reason}</code>"
        )
        notify.send(text, product_url=product_url)
    else:
        head = "✅ <b>오늘 재고 모니터링이 정상 완료되었습니다</b>"
        if alerts_sent > 0:
            stock_line = "윈도우 중 재고 변동을 감지했습니다."
        elif saw_any_in_stock:
            stock_line = "재고가 입고 상태로 확인되었지만, 이번 윈도우에서 새 전이는 감지되지 않았습니다."
        else:
            stock_line = "10:10 KST까지 재고가 확인되지 않았습니다."

        text = (
            f"{head}\n"
            f"{stock_line}\n\n"
            f"폴링 횟수: <b>{checks}</b>\n"
            f"입고 알림 횟수: <b>{alerts_sent}</b>\n"
            f"마지막 상태: {latest_summary}\n"
            f"시작: <code>{started_at}</code>\n"
            f"마지막 확인: <code>{last_checked_at}</code>"
        )
        notify.send(text, product_url=product_url)

    persisted_state["last_cycle_summary_key"] = cycle_key
    persisted_state["last_cycle_summary_sent_at"] = datetime.now(
        ZoneInfo(timezone_name)
    ).isoformat(timespec="seconds")
    state_path.write_text(
        json.dumps(persisted_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
