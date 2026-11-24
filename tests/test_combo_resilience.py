"""コンボ実行の耐障害性に関するテスト。

「途中までプランを持っている人」は、すでに使用済みのオファーが候補に残る。
そこで中断してしまうと、後ろにある未使用の +6ヶ月オファーに到達できない。
使用済み・不成立のステップは飛ばして続行し、1 つでも成功すれば成功として
扱うことを固定する。
"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import combo
import combo_core as core

NOW_MS = 1_764_000_000_000  # 2025-11-24 相当（テスト内の固定値）
DAY_MS = 24 * 60 * 60 * 1000


def _states(end_ms):
    if end_ms is None:
        return []
    return [
        core.SubscriptionState(
            sku="g1.2tb.ai.subs_variant",
            valid_until_ms=end_ms,
            account="a@example.com",
            active=True,
        )
    ]


def _offers():
    return [
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "12months-eft", "period": "P1Y", "micros": 0},
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft", "period": "P6M", "micros": 0},
        {"sku": "g1.2tb.ai.subs_variant", "offer_id": "6months-eft-2", "period": "P6M", "micros": 0},
    ]


class _SteppingState:
    """executor が呼ばれるたびに期限を進める状態読み出し。"""

    def __init__(self, start_ms, advances):
        self.end = start_ms
        self.advances = list(advances)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > 1 and self.advances:
            step = self.advances.pop(0)
            if step:
                self.end += step
        return _states(self.end)


class ResilientExecutionTests(unittest.TestCase):
    def _run(self, argv, state_reader, executor):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.object(
            combo, "input", side_effect=EOFError, create=True
        ):
            code = combo.main(
                argv,
                state_reader=state_reader,
                offer_reader=_offers,
                executor=executor,
            )
        return code, out.getvalue(), err.getvalue()

    def test_already_used_offer_is_skipped_and_later_steps_still_run(self):
        """1手目（使用済み）が伸びなくても、後続の +6ヶ月 は実行される。"""
        state = _SteppingState(NOW_MS, advances=[0, 6 * 30 * DAY_MS, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertEqual(
            executed,
            ["12months-eft", "6months-eft", "6months-eft-2"],
            "使用済みでも全ステップを試すこと",
        )
        self.assertEqual(code, 0, "1 つでも成功していれば成功として扱う")
        self.assertIn("6months-eft", out)

    def test_all_steps_failing_returns_non_zero(self):
        state = _SteppingState(NOW_MS, advances=[0, 0, 0])

        def executor(action):
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertNotEqual(code, 0)
        self.assertIn("1", out)

    def test_stop_on_failure_keeps_strict_behaviour_when_requested(self):
        state = _SteppingState(NOW_MS, advances=[0, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            return {"launched": True}

        code, _, _ = self._run(
            [
                "run", "--execute", "--yes", "--stop-on-failure",
                "--non-interactive", "--wait-seconds", "0",
            ],
            state,
            executor,
        )

        self.assertEqual(executed, ["12months-eft"], "厳格モードでは最初の失敗で止まる")
        self.assertNotEqual(code, 0)

    def test_cannot_open_billing_screen_also_continues_by_default(self):
        state = _SteppingState(NOW_MS, advances=[6 * 30 * DAY_MS, 6 * 30 * DAY_MS, 6 * 30 * DAY_MS])
        executed = []

        def executor(action):
            executed.append(action.offer_id)
            if action.offer_id == "12months-eft":
                return {"launched": False, "event": "query_err"}
            return {"launched": True}

        code, out, _ = self._run(
            ["run", "--execute", "--yes", "--non-interactive", "--wait-seconds", "0"],
            state,
            executor,
        )

        self.assertEqual(
            executed, ["12months-eft", "6months-eft", "6months-eft-2"]
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
