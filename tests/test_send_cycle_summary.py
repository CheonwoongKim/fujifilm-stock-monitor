from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import send_cycle_summary


class SendCycleSummaryTests(unittest.TestCase):
    def test_build_cycle_key_uses_local_date(self) -> None:
        key = send_cycle_summary.build_cycle_key(
            window_label="09:50~10:10 KST",
            timezone_name="Asia/Seoul",
            now=datetime.fromisoformat("2026-04-29T10:11:00+09:00"),
        )

        self.assertEqual(key, "2026-04-29::09:50~10:10 KST")

    def test_format_latest_variants(self) -> None:
        summary = send_cycle_summary.format_latest_variants(
            {
                "latest_variants": [
                    {"short": "실버", "in_stock": True},
                    {"short": "블랙", "in_stock": False},
                ]
            }
        )

        self.assertEqual(summary, "✅ 실버 / ❌ 블랙")

    def test_main_sends_no_stock_completion_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            cycle_state_path.write_text(
                json.dumps(
                    {
                        "started_at": "2026-04-29T00:50:00+00:00",
                        "last_checked_at": "2026-04-29T01:10:00+00:00",
                        "checks": 20,
                        "saw_any_in_stock": False,
                        "alerts_sent": 0,
                        "latest_variants": [
                            {"short": "실버", "in_stock": False},
                            {"short": "블랙", "in_stock": False},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "STATE_PATH": str(state_path),
                    "PRODUCT_URL": "https://example.com/product",
                    "WINDOW_LABEL": "09:50~10:10 KST",
                    "WINDOW_TIMEZONE": "Asia/Seoul",
                },
                clear=False,
            ):
                with patch(
                    "send_cycle_summary.build_cycle_key",
                    return_value="2026-04-29::09:50~10:10 KST",
                ), patch("send_cycle_summary.notify.send") as send_mock:
                    result = send_cycle_summary.main()

            self.assertEqual(result, 0)
            self.assertEqual(send_mock.call_count, 1)
            sent_text = send_mock.call_args.args[0]
            self.assertIn("정상 완료", sent_text)
            self.assertIn("10:10 KST까지 재고가 확인되지 않았습니다.", sent_text)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["last_cycle_summary_key"],
                "2026-04-29::09:50~10:10 KST",
            )

    def test_main_sends_in_stock_without_transition_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            cycle_state_path.write_text(
                json.dumps(
                    {
                        "started_at": "2026-04-29T00:50:00+00:00",
                        "last_checked_at": "2026-04-29T01:10:00+00:00",
                        "checks": 20,
                        "saw_any_in_stock": True,
                        "alerts_sent": 0,
                        "latest_variants": [
                            {"short": "실버", "in_stock": True},
                            {"short": "블랙", "in_stock": False},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "STATE_PATH": str(state_path),
                    "PRODUCT_URL": "https://example.com/product",
                    "WINDOW_LABEL": "09:50~10:10 KST",
                },
                clear=False,
            ):
                with patch(
                    "send_cycle_summary.build_cycle_key",
                    return_value="2026-04-29::09:50~10:10 KST",
                ), patch("send_cycle_summary.notify.send") as send_mock:
                    result = send_cycle_summary.main()

            self.assertEqual(result, 0)
            sent_text = send_mock.call_args.args[0]
            self.assertIn("새 전이는 감지되지 않았습니다.", sent_text)

    def test_main_sends_skipped_window_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text("{}", encoding="utf-8")
            cycle_state_path.write_text("{}", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "STATE_PATH": str(state_path),
                    "PRODUCT_URL": "https://example.com/product",
                    "WINDOW_LABEL": "09:50~10:10 KST",
                    "SKIP_DENSE_POLL": "1",
                    "CYCLE_END_REASON": "started_after_window",
                },
                clear=False,
            ):
                with patch(
                    "send_cycle_summary.build_cycle_key",
                    return_value="2026-04-29::09:50~10:10 KST",
                ), patch("send_cycle_summary.notify.send") as send_mock:
                    result = send_cycle_summary.main()

            self.assertEqual(result, 0)
            sent_text = send_mock.call_args.args[0]
            self.assertIn("윈도우를 놓쳤습니다", sent_text)
            self.assertIn("started_after_window", sent_text)

    def test_main_skips_duplicate_cycle_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {"last_cycle_summary_key": "2026-04-29::09:50~10:10 KST"}
                ),
                encoding="utf-8",
            )
            cycle_state_path.write_text("{}", encoding="utf-8")

            with patch.dict(
                os.environ,
                {
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "STATE_PATH": str(state_path),
                    "PRODUCT_URL": "https://example.com/product",
                    "WINDOW_LABEL": "09:50~10:10 KST",
                },
                clear=False,
            ):
                with patch(
                    "send_cycle_summary.build_cycle_key",
                    return_value="2026-04-29::09:50~10:10 KST",
                ), patch("send_cycle_summary.notify.send") as send_mock:
                    result = send_cycle_summary.main()

            self.assertEqual(result, 0)
            send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
