"""combo.py CLI のテスト。

state_reader / offer_reader / executor を注入し、デバイスも frida も使わない。
"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo
import combo_core

DOC_PREFIX = "subs:com.google.android.apps.subscriptions.red:"
DAY_MS = 86_400_000


def ms(year, month, day, hour=12):
    return int(datetime(year, month, day, hour).timestamp() * 1000)


NOW = ms(2026, 11, 24)
END = ms(2028, 11, 21)

OFFERS = [
    {"sku": "g1.2tb.ai.6months_eft", "offer_id": "eft-6months", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.12months_eft", "offer_id": "eft-12months", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.18months_eft", "offer_id": "eft-18months", "period": "P1Y6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
    {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M", "micros": 2900000000},
]


class FakeDevice:
    """library.db の所有行をメモリ上で模した読み取り専用の状態リーダー。"""

    def __init__(self, rows, now_ms=NOW):
        self.rows = list(rows)
        self.now_ms = now_ms
        self.calls = 0

    def read_state(self):
        self.calls += 1
        return combo_core.parse_ownership_rows(self.rows, now_ms=self.now_ms)

    def read_offers(self):
        return [dict(offer) for offer in OFFERS]

    def bump_month(self, months):
        """購入が成立したものとして期限を months ヶ月ぶん伸ばす（概算で単調増加のみ担保）。"""
        current = combo_core.latest_end_ms(self.read_state()) or self.now_ms
        self.rows = [
            (DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com",
             current + months * 30 * DAY_MS)
        ]


def _run(argv, device, executor=None, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = combo.main(
            argv,
            state_reader=device.read_state,
            offer_reader=device.read_offers,
            executor=executor,
            **kwargs,
        )
    return code, out.getvalue(), err.getvalue()


class NoDeviceTests(unittest.TestCase):
    def test_plan_without_readers_refuses_and_touches_no_device(self):
        self.assertNotIn("frida", sys.modules)
        with mock.patch("subprocess.run") as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = combo.main(["plan"])
        self.assertNotEqual(code, 0)
        self.assertIn("--device", out.getvalue() + err.getvalue())
        run.assert_not_called()
        self.assertNotIn("frida", sys.modules)

    def test_no_subcommand_defaults_to_plan(self):
        with mock.patch("subprocess.run") as run:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = combo.main([])
        self.assertNotEqual(code, 0)
        run.assert_not_called()

    def test_run_without_flags_prints_usage_hint_and_never_executes(self):
        device = FakeDevice([])
        executor = mock.Mock()
        code, out, _ = _run(["run"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("実行するには --execute --yes が必要です", out)
        executor.assert_not_called()

    def test_run_needs_both_flags(self):
        for argv in (["run", "--execute"], ["run", "--yes"]):
            with self.subTest(argv=argv):
                device = FakeDevice([])
                executor = mock.Mock()
                code, out, _ = _run(argv, device, executor)
                self.assertEqual(code, 2)
                self.assertIn("--execute --yes", out)
                executor.assert_not_called()


class PlanTests(unittest.TestCase):
    def test_plan_prints_verdict_and_actions_and_exits_zero(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, _ = _run(["plan"], device)

        self.assertEqual(code, 0)
        self.assertIn("有効なサブスクがあるため積み上げ可能", out)
        self.assertIn("2028-11-21", out)
        self.assertIn("[積み上げ] g1.2tb.ai.subs_variant / 12months-eft", out)
        self.assertIn("micros=0", out)
        self.assertEqual(device.calls, 1)
        self.assertNotIn("frida", sys.modules)

    def test_plan_reports_auto_renew_off(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, _ = _run(["plan", "--no-auto-renew"], device)

        self.assertEqual(code, 0)
        self.assertIn("自動更新がオフ", out)

    def test_plan_for_fresh_account_starts_with_longest_offer(self):
        code, out, _ = _run(["plan"], FakeDevice([]))

        self.assertEqual(code, 0)
        self.assertIn("新規登録", out)
        self.assertIn("1. [新規] g1.2tb.ai.18months_eft / eft-18months / +18ヶ月", out)

    def test_plan_warns_when_account_differs(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, out, err = _run(["plan", "--account", "other@example.com"], device)

        self.assertEqual(code, 0)
        self.assertIn("other@example.com", err)
        self.assertIn("一致しません", err)

    def test_plan_silent_when_account_matches(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )

        code, _, err = _run(["plan", "--account", "user@example.com"], device)

        self.assertEqual(code, 0)
        self.assertNotIn("一致しません", err)


class RunTests(unittest.TestCase):
    def test_run_executes_every_step_when_the_end_date_grows(self):
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            device.bump_month(action.months)
            return {"launched": True, "event": "launched"}

        with mock.patch("builtins.input") as fake_input:
            code, out, _ = _run(
                ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
                device,
                executor,
            )

        self.assertEqual(code, 0)
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0].kind, "new_subscription")
        self.assertTrue(all(action.kind == "stack" for action in actions[1:]))
        self.assertIn("支払いを完了してください", out)
        self.assertIn("2028", out)
        fake_input.assert_not_called()

    def test_run_stops_after_the_first_step_when_the_end_date_does_not_grow(self):
        """厳格モード（--stop-on-failure）では期限が伸びない時点で中断する。

        既定は「使用済みオファーを飛ばして続行」なので、ここでは明示的に
        厳格モードを指定して中断の挙動を固定する。
        """
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            return {"launched": True}

        code, out, _ = _run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            device,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(len(actions), 1, "期限が伸びないときは次のステップへ進んではいけない")
        self.assertIn("有効期限", out)
        self.assertIn("中断", out)

    def test_run_stops_when_the_billing_view_cannot_be_opened(self):
        """厳格モードでは課金画面が開けない時点で中断する。"""
        device = FakeDevice([])
        actions = []

        def executor(action):
            actions.append(action)
            return {"launched": False, "event": "no_trial"}

        code, out, _ = _run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            device,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertEqual(len(actions), 1)
        self.assertIn("課金画面", out)

    def test_run_waits_for_enter_in_interactive_mode(self):
        device = FakeDevice([])

        def executor(action):
            device.bump_month(action.months)
            return {"launched": True}

        with mock.patch("builtins.input", return_value="") as fake_input:
            code, out, _ = _run(["run", "--execute", "--yes"], device, executor)

        self.assertEqual(code, 0)
        self.assertEqual(fake_input.call_count, 3)
        self.assertIn("Enter", out)

    def test_run_reports_a_closed_stdin_with_a_helpful_message(self):
        device = FakeDevice([])

        def executor(action):
            device.bump_month(action.months)
            return {"launched": True}

        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = _run(["run", "--execute", "--yes"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("--non-interactive", err)
        self.assertNotIn("Traceback", err)

    def test_run_refuses_an_active_plan_without_stack_offers(self):
        device = FakeDevice(
            [(DOC_PREFIX + "g1.2tb.ai.subs_variant", "user@example.com", END)]
        )
        device.read_offers = lambda: [
            {"sku": "g1.2tb.ai.subs_variant", "offer_id": "null", "period": "P1M",
             "micros": 2900000000}
        ]
        executor = mock.Mock()

        code, out, _ = _run(["run", "--execute", "--yes", "--non-interactive"], device, executor)

        self.assertNotEqual(code, 0)
        self.assertIn("積み上げ可能な無料オファーが見つかりません", out)
        executor.assert_not_called()

    def test_run_reports_executor_exceptions_without_traceback(self):
        device = FakeDevice([])

        def executor(action):
            raise RuntimeError("接続できませんでした")

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = combo.main(
                ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
                state_reader=device.read_state,
                offer_reader=device.read_offers,
                executor=executor,
            )

        self.assertNotEqual(code, 0)
        self.assertIn("接続できませんでした", err.getvalue())
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
