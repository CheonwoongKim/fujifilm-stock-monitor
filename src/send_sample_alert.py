"""Send a sample stock-in-stock alert exactly as production would.

This is for previewing the alert format end-to-end (header, per-variant
status lines, price, timestamp, inline button) without waiting for an
actual restock event. Triggered manually via the test-telegram workflow.
"""
from __future__ import annotations

import os
import sys

from check import VariantStatus, compose_alert, now_iso
import notify


def main() -> int:
    scenario = (os.environ.get("SAMPLE_SCENARIO") or "silver").strip().lower()
    url = os.environ.get(
        "PRODUCT_URL", "https://www.fujifilm-korea.co.kr/products/id/1330"
    )

    silver_in = VariantStatus(
        name="X100VI Silver", short="실버", in_stock=True, price="₩2,250,000"
    )
    silver_out = VariantStatus(
        name="X100VI Silver", short="실버", in_stock=False, price="품절"
    )
    black_in = VariantStatus(
        name="X100VI Black", short="블랙", in_stock=True, price="₩2,250,000"
    )
    black_out = VariantStatus(
        name="X100VI Black", short="블랙", in_stock=False, price="품절"
    )

    if scenario == "black":
        all_variants = [silver_out, black_in]
        new_in_stock = [black_in]
    elif scenario == "both":
        all_variants = [silver_in, black_in]
        new_in_stock = [silver_in, black_in]
    else:
        all_variants = [silver_in, black_out]
        new_in_stock = [silver_in]

    message = compose_alert(new_in_stock, all_variants, now_iso())
    preview = "🧪 <b>[샘플]</b> 아래는 실제 재고 입고 시 받게 될 알림 형식입니다.\n\n" + message
    notify.send(preview, product_url=url)
    print(f"[ok] sample alert sent (scenario={scenario})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
