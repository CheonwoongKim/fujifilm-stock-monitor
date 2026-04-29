from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import check


class CheckLogicTests(unittest.TestCase):
    def test_detect_transitions_only_returns_newly_in_stock(self) -> None:
        previous = {
            "X100VI Silver": {"in_stock": False},
            "X100VI Black": {"in_stock": True},
        }
        current = [
            check.VariantStatus("X100VI Silver", "실버", True, "₩2,250,000"),
            check.VariantStatus("X100VI Black", "블랙", True, "₩2,250,000"),
        ]

        transitions = check.detect_transitions(previous, current)

        self.assertEqual([variant.short for variant in transitions], ["실버"])

    def test_update_cycle_state_accumulates_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cycle-state.json"
            variants_first = [
                check.VariantStatus("X100VI Silver", "실버", False, "품절"),
                check.VariantStatus("X100VI Black", "블랙", False, "품절"),
            ]
            variants_second = [
                check.VariantStatus("X100VI Silver", "실버", True, "₩2,250,000"),
                check.VariantStatus("X100VI Black", "블랙", False, "품절"),
            ]

            check.update_cycle_state(
                path,
                checked_at="2026-04-29T00:50:00+00:00",
                variants=variants_first,
                transitions=[],
            )
            check.update_cycle_state(
                path,
                checked_at="2026-04-29T00:51:00+00:00",
                variants=variants_second,
                transitions=[variants_second[0]],
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["started_at"], "2026-04-29T00:50:00+00:00")
            self.assertEqual(saved["last_checked_at"], "2026-04-29T00:51:00+00:00")
            self.assertEqual(saved["checks"], 2)
            self.assertTrue(saved["saw_any_in_stock"])
            self.assertEqual(saved["transitions_detected"], 1)
            self.assertEqual(saved["alerted_variants"], ["실버"])


class CheckMainTests(unittest.TestCase):
    def test_main_sends_stock_alert_and_updates_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "variants": {
                            "X100VI Silver": {
                                "name": "X100VI Silver",
                                "short": "실버",
                                "in_stock": False,
                                "price": "품절",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            variants = [
                check.VariantStatus("X100VI Silver", "실버", True, "₩2,250,000"),
                check.VariantStatus("X100VI Black", "블랙", False, "품절"),
            ]

            with patch.dict(
                os.environ,
                {
                    "PRODUCT_URL": "https://example.com/product",
                    "STATE_PATH": str(state_path),
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "HEARTBEAT_HOURS": "0",
                },
                clear=False,
            ):
                with patch("check.fetch", return_value=variants), patch(
                    "check.now_iso", return_value="2026-04-29T00:50:00+00:00"
                ), patch("check.notify.send") as send_mock:
                    result = check.main()

            self.assertEqual(result, 0)
            self.assertEqual(send_mock.call_count, 1)
            sent_text = send_mock.call_args.args[0]
            self.assertIn("재고 입고", sent_text)
            self.assertIn("실버", sent_text)

            saved_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved_state["variants"]["X100VI Silver"]["in_stock"],
                True,
            )
            self.assertEqual(saved_state["last_alerted_at"], "2026-04-29T00:50:00+00:00")

            cycle_state = json.loads(cycle_state_path.read_text(encoding="utf-8"))
            self.assertEqual(cycle_state["checks"], 1)
            self.assertTrue(cycle_state["saw_any_in_stock"])
            self.assertEqual(cycle_state["transitions_detected"], 1)

    def test_main_records_cycle_state_even_when_alert_send_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            cycle_state_path = Path(tmpdir) / "cycle-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "variants": {
                            "X100VI Silver": {
                                "name": "X100VI Silver",
                                "short": "실버",
                                "in_stock": False,
                                "price": "품절",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            variants = [
                check.VariantStatus("X100VI Silver", "실버", True, "₩2,250,000"),
            ]

            with patch.dict(
                os.environ,
                {
                    "PRODUCT_URL": "https://example.com/product",
                    "STATE_PATH": str(state_path),
                    "CYCLE_STATE_PATH": str(cycle_state_path),
                    "HEARTBEAT_HOURS": "0",
                },
                clear=False,
            ):
                with patch("check.fetch", return_value=variants), patch(
                    "check.now_iso", return_value="2026-04-29T00:50:00+00:00"
                ), patch(
                    "check.notify.send", side_effect=RuntimeError("telegram down")
                ):
                    with self.assertRaises(RuntimeError):
                        check.main()

            cycle_state = json.loads(cycle_state_path.read_text(encoding="utf-8"))
            self.assertEqual(cycle_state["checks"], 1)
            self.assertTrue(cycle_state["saw_any_in_stock"])
            self.assertEqual(cycle_state["transitions_detected"], 1)


if __name__ == "__main__":
    unittest.main()
