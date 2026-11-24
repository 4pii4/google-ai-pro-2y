"""レビュー指摘に対する回帰テスト（読み取り専用の契約を固定する）。

根拠（実コードで確認済み）:
- agent/injected.js:69 の `no_offers` は商品ごとに送られ `continue` する。
  バッチ終端として扱うと残りの SKU が無言で未照会になる。
- scripts/ultra0-17.py:563-564 の LIST_ONLY 付与は無検証の文字列置換で、
  失敗すると agent は launchBillingFlow まで到達しうる（fail-open）。
- frida の例外は Exception 直系で RuntimeError ではない。
- done.wait() の戻り値（タイムアウト）は現状捨てられている。

device / frida / network は使わない。
"""

import dataclasses
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sku_scanner
import sku_scanner_core


class _StubScript:
    """load() 時に用意したイベントを順に流すだけのスクリプト。"""

    def __init__(self, events):
        self.events = list(events)
        self._callback = None
        self.unloaded = False

    def on(self, _name, callback):
        self._callback = callback

    def load(self):
        for event in self.events:
            self._callback({"type": "send", "payload": event}, None)

    def unload(self):
        self.unloaded = True


class _StubSession:
    def __init__(self, events):
        self._events = events
        self.detached = False

    def create_script(self, _js):
        return _StubScript(self._events)

    def detach(self):
        self.detached = True


class _StubDevice:
    def __init__(self, events):
        self._events = events

    def attach(self, _pid):
        return _StubSession(self._events)


class FakeSource:
    """fetch(skus, timeout) と close() だけを持つ最小の情報源。"""

    def __init__(self, events=None, error=None, timed_out=False, pd_events=None):
        self.events = list(events or [])
        self.error = error
        self.timed_out = timed_out
        self.pd_events = list(pd_events or [])
        self.calls = []
        self.closed = 0

    def fetch(self, skus, timeout):
        self.calls.append((list(skus), timeout))
        if self.error is not None:
            raise self.error
        return list(self.events)

    def close(self):
        self.closed += 1


def _run(argv, source=None, factory=None):
    """main() を stdout/stderr を捕まえて実行し、(code, out, err, calls) を返す。

    source を渡した場合はそれを返す factory を、factory を渡した場合はそれを
    そのまま使う。どちらも渡さない場合だけ main() 側の既定（実機接続）になる。
    レポートの既定保存先は一時ディレクトリへ向け、repo を汚さない。
    """
    calls = []
    probe = factory if factory is not None else (lambda: (calls.append(1), source)[1])

    out, err = io.StringIO(), io.StringIO()
    temporary = tempfile.TemporaryDirectory()
    try:
        with mock.patch.object(
            sku_scanner, "DEFAULT_REPORT_DIR", Path(temporary.name)
        ), redirect_stdout(out), redirect_stderr(err):
            try:
                code = sku_scanner.main(argv, source_factory=probe)
            except SystemExit as exc:  # argparse のエラー
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        temporary.cleanup()
    return code, out.getvalue(), err.getvalue(), calls


class TerminalEventContractTests(unittest.TestCase):
    def test_no_offers_is_not_a_batch_terminal_event(self):
        terminal = sku_scanner.FridaSource.TERMINAL_EVENTS
        self.assertNotIn("no_offers", terminal)
        self.assertFalse(sku_scanner.FridaSource._is_terminal("no_offers"))

    def test_normal_completion_events_are_terminal(self):
        for event in ("list_only", "no_trial", "query_err"):
            with self.subTest(event=event):
                self.assertTrue(sku_scanner.FridaSource._is_terminal(event))

    def test_error_and_disconnect_events_are_terminal(self):
        for event in ("boot_err", "launch_err", "disc", "query_err"):
            with self.subTest(event=event):
                self.assertTrue(sku_scanner.FridaSource._is_terminal(event))

    def test_non_terminal_and_non_hashable_inputs_are_safe(self):
        self.assertFalse(sku_scanner.FridaSource._is_terminal("offer"))
        self.assertFalse(sku_scanner.FridaSource._is_terminal(None))
        self.assertFalse(sku_scanner.FridaSource._is_terminal({"ev": "offer"}))

    def test_forbidden_launch_events_are_detected(self):
        forbidden = sku_scanner.FridaSource.FORBIDDEN_EVENTS
        for event in ("launched", "stack_start", "activity", "purchase_cb"):
            with self.subTest(event=event):
                self.assertIn(event, forbidden)
                self.assertTrue(sku_scanner.FridaSource._is_forbidden(event))
        self.assertFalse(sku_scanner.FridaSource._is_forbidden("offer"))
        self.assertFalse(sku_scanner.FridaSource._is_forbidden(None))

    def test_empty_or_failed_pd_ends_the_batch_without_waiting_for_timeout(self):
        """存在しない SKU では agent が no_trial を送らず return する。

        agent/injected.js は `pd` の code が 0 以外、または list が空のとき
        何も送らずに戻る。これを終端扱いしないと、存在しない SKU を照会する
        たびに必ずタイムアウトし、「端末異常」と誤読させる。
        """
        pd_ends = sku_scanner.FridaSource._pd_terminates
        self.assertTrue(pd_ends(0, 0), "0 件なら終端")
        self.assertTrue(pd_ends(-1, 3), "エラーコードなら終端")
        self.assertTrue(pd_ends(5, 0), "エラーかつ 0 件なら終端")
        self.assertFalse(pd_ends(0, 2), "正常かつ結果ありなら継続")

    def test_pd_termination_is_reported_as_empty_not_timeout(self):
        """空の pd を受けたバッチはタイムアウト扱いにならない。

        _run_batch をスタブのセッションで直接動かし、pd(0,0) だけを送る
        スクリプトが「完了」と判定されることを確かめる。
        """
        source = sku_scanner.FridaSource()
        source._device = _StubDevice([{"ev": "pd", "code": 0, "n": 0}])
        source._pid = 1
        source._timeout = 5.0

        collected, timed_out, forbidden, script_error = source._run_batch("js")

        self.assertEqual(collected, [])
        self.assertFalse(timed_out, "空の pd で完了していること")
        self.assertIsNone(forbidden)
        self.assertFalse(script_error)

    def test_batch_without_any_event_is_a_real_timeout(self):
        """何も送られないバッチはタイムアウトとして区別される。"""
        source = sku_scanner.FridaSource()
        source._device = _StubDevice([])
        source._pid = 1
        source._timeout = 0.2

        _, timed_out, _, _ = source._run_batch("js")

        self.assertTrue(timed_out)


class ReadOnlyGuaranteeTests(unittest.TestCase):
    def test_loaded_js_contains_exactly_one_list_only_flag(self):
        js = sku_scanner.FridaSource._load_js()
        self.assertEqual(js.count("var LIST_ONLY=true;"), 1)

    def test_loaded_js_contains_exactly_one_products_literal(self):
        js = sku_scanner.FridaSource._load_js()
        self.assertEqual(js.count(sku_scanner.PRODUCTS_LITERAL), 1)

    def test_assert_read_only_rejects_js_without_list_only(self):
        broken = 'var PREFER = "";\n'
        with self.assertRaises(RuntimeError):
            sku_scanner.FridaSource._assert_read_only(broken)

    def test_assert_read_only_accepts_real_agent_js(self):
        js = sku_scanner.FridaSource._load_js()
        self.assertIsNone(sku_scanner.FridaSource._assert_read_only(js))

    def test_inject_products_replaces_literal_and_rejects_missing(self):
        js = sku_scanner.FridaSource._load_js()
        injected = sku_scanner.FridaSource._inject_products(
            js, ["g1.2tb.ai.18months_eft"]
        )
        self.assertNotIn(sku_scanner.PRODUCTS_LITERAL, injected)
        self.assertIn('"g1.2tb.ai.18months_eft"', injected)
        with self.assertRaises(RuntimeError):
            sku_scanner.FridaSource._inject_products("var nothing;", ["g1.2tb.ai.1month_eft"])

    def test_load_js_does_not_write_new_bytecode(self):
        """既存 .pyc の有無に依存せず、書き込み停止の仕組みそのものを確かめる。

        既に .pyc があると before/after の比較は常に一致してしまい、
        何も検証できない。そこで「読み込み後に sys.dont_write_bytecode が
        元の値へ戻っていること」と「読み込み中は True だったこと」を見る。
        """
        cache = REPO_ROOT / "scripts" / "__pycache__"
        before = set(cache.glob("ultra0-17*.pyc")) if cache.is_dir() else set()
        original = sys.dont_write_bytecode
        observed = []
        real_exec_module = SourceFileLoader.exec_module

        def spy(loader, module):
            # 実際に .pyc を書きうるのは exec_module の内部。
            observed.append(sys.dont_write_bytecode)
            return real_exec_module(loader, module)

        with mock.patch.object(SourceFileLoader, "exec_module", spy):
            sku_scanner.FridaSource._load_js()

        self.assertEqual(observed, [True], "読み込み中は書き込みを止めていること")
        self.assertEqual(sys.dont_write_bytecode, original, "読み込み後に元へ戻すこと")
        after = set(cache.glob("ultra0-17*.pyc")) if cache.is_dir() else set()
        self.assertEqual(before, after)


class OutcomeReportingTests(unittest.TestCase):
    def test_scan_outcome_has_expected_fields(self):
        names = {f.name for f in dataclasses.fields(sku_scanner.ScanOutcome)}
        self.assertEqual(
            names, {"rows", "batches_ok", "batches_failed", "timed_out"}
        )

    def test_exit_code_is_non_zero_when_every_batch_failed(self):
        outcome = sku_scanner.ScanOutcome(
            rows=[], batches_ok=0, batches_failed=3, timed_out=True
        )
        self.assertNotEqual(sku_scanner._exit_code_for(outcome), 0)

    def test_exit_code_is_zero_when_at_least_one_batch_succeeded(self):
        outcome = sku_scanner.ScanOutcome(
            rows=[], batches_ok=1, batches_failed=2, timed_out=True
        )
        self.assertEqual(sku_scanner._exit_code_for(outcome), 0)

    def test_runtime_error_from_source_is_reported_without_traceback(self):
        source = FakeSource(error=RuntimeError("boom"))
        code, _, err, _ = _run(
            ["query", "--skus", "g1.2tb.ai.1month_eft"], source=source
        )
        self.assertNotEqual(code, 0)
        self.assertIn("boom", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(source.closed, 1)

    def test_generic_exception_from_source_is_reported_without_traceback(self):
        source = FakeSource(error=Exception("device offline"))
        code, _, err, _ = _run(
            ["query", "--skus", "g1.2tb.ai.1month_eft"], source=source
        )
        self.assertNotEqual(code, 0)
        self.assertIn("device offline", err)
        self.assertNotIn("Traceback", err)

    def test_timed_out_source_yields_non_zero_exit(self):
        source = FakeSource(events=[], timed_out=True)
        code, _, err, _ = _run(
            ["query", "--skus", "g1.2tb.ai.1month_eft"], source=source
        )
        self.assertNotEqual(code, 0)
        self.assertIn("タイムアウト", err)


class CliHygieneTests(unittest.TestCase):
    def test_query_requires_skus_or_candidates(self):
        code, _, err, calls = _run(["query", "--format", "json"], source=FakeSource())
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertTrue(err.strip())

    def test_timeout_bounds_are_enforced(self):
        for value in ("0", "-1", "60"):
            with self.subTest(timeout=value):
                code, _, err, calls = _run(
                    ["query", "--skus", "g1.2tb.ai.1month_eft", "--timeout", value],
                    source=FakeSource(),
                )
                self.assertNotEqual(code, 0)
                self.assertEqual(calls, [])
                self.assertTrue(err.strip())

    def test_timeout_within_bounds_is_accepted(self):
        source = FakeSource()
        code, _, _, calls = _run(
            ["query", "--skus", "g1.2tb.ai.1month_eft", "--timeout", "8"],
            source=source,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [1])
        self.assertEqual(float(source.calls[0][1]), 8.0)

    def test_batch_size_zero_is_rejected(self):
        code, _, err, calls = _run(
            ["query", "--skus", "g1.2tb.ai.1month_eft", "--batch-size", "0"],
            source=FakeSource(),
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertTrue(err.strip())

    def test_report_path_is_printed_and_output_is_0600(self):
        source = FakeSource(
            events=[
                {
                    "ev": "offer",
                    "product": "g1.2tb.ai.1month_eft",
                    "id": "offer-1",
                    "period": "P1M",
                    "micros": "0",
                    "token": "should-not-be-saved",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            code, out, err, _ = _run(
                [
                    "query",
                    "--skus", "g1.2tb.ai.1month_eft",
                    "--format", "json",
                    "--output", str(path),
                ],
                source=source,
            )
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())
            self.assertIn(str(path), err)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("token", path.read_text(encoding="utf-8").lower())
            self.assertNotIn("should-not-be-saved", out)


class TableMessageTests(unittest.TestCase):
    def test_omitted_message_does_not_point_at_limit_flag(self):
        rows = [
            sku_scanner_core.OfferRow(
                sku="g1.2tb.ai.1month_eft",
                offer_id=f"o{index}",
                period="P1M",
                micros=index,
                title="",
            )
            for index in range(5)
        ]
        text = "\n".join(sku_scanner_core.format_table(rows, limit=2))
        self.assertIn("3", text)
        self.assertNotIn("--limit", text)


if __name__ == "__main__":
    unittest.main()
