#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sku_scanner.py —— Google One の SKU / offer を「読むだけ」の CLI。

購入・チェックアウト・起動操作は一切行わない。frida は実機照会時にだけ
遅延 import するので、frida が無い環境でも generate / summarize が動く。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

import sku_scanner_core as core

DEFAULT_REPORT_DIR = Path("ultra0-report")
REPORT_PREFIX = "sku-scan-"

MIN_TIMEOUT = 1.0
MAX_TIMEOUT = 30.0
DEFAULT_BATCH_SIZE = 20

# injected.js の PRODUCTS は build_js(plan="5x", list_only=True) でこの形に置換される。
PRODUCTS_LITERAL = 'var PRODUCTS=["g1.play.20tb"];'
# build_js(list_only=True) が 1 箇所だけ差し込む読み取り専用フラグ。
LIST_ONLY_LITERAL = "var LIST_ONLY=true;"


@dataclass
class ScanOutcome:
    """1 回のスキャンの結果と、バッチの成否をまとめたもの。

    成否を呼び出し側（終了コード）に渡すために、rows だけでは足りない。
    """

    rows: list
    batches_ok: int
    batches_failed: int
    timed_out: bool


def _exit_code_for(outcome: ScanOutcome) -> int:
    """1 バッチも完了しなかった場合は失敗として扱う。

    一部のバッチがタイムアウトしても、完了したバッチが 1 つでもあれば成功（0）。
    """
    return 0 if outcome.batches_ok > 0 else 1


def _split_values(values: Sequence[str]) -> list:
    """--periods 18months,12months と --periods 18months 12months の両方を受け付ける。"""
    result = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


def _add_generation_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tiers", nargs="+", default=list(core.DEFAULT_TIERS), metavar="TIER",
        help="対象 tier（カンマ区切り・空白区切りどちらも可）",
    )
    parser.add_argument(
        "--periods", nargs="+", default=list(core.DEFAULT_PERIODS), metavar="PERIOD",
        help="対象期間（例: 18months,12months）",
    )
    parser.add_argument(
        "--suffixes", nargs="+", default=list(core.DEFAULT_SUFFIXES), metavar="SUFFIX",
        help="対象 offer サフィックス",
    )
    parser.add_argument("--limit", type=int, default=None, help="候補数の上限")
    parser.add_argument(
        "--no-variants", action="store_true",
        help="subs_variant の SKU を候補に含めない",
    )


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="出力形式（既定: table）",
    )
    parser.add_argument(
        "--output", metavar="PATH",
        help="レポートの保存先（省略時は既定ディレクトリに保存）",
    )


def _timeout_seconds(value: str) -> float:
    """--timeout を 1〜30 秒に制限する（0 や過大な値は端末側で事故になる）。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--timeout は秒数を数値で指定してください: {value!r}"
        ) from None
    if not MIN_TIMEOUT <= seconds <= MAX_TIMEOUT:
        raise argparse.ArgumentTypeError(
            f"--timeout は {MIN_TIMEOUT:g}〜{MAX_TIMEOUT:g} 秒の範囲で指定してください: {value}"
        )
    return seconds


def _batch_size(value: str) -> int:
    """--batch-size を 1 以上に制限する（0 だと 1 件も照会されない）。"""
    try:
        size = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"--batch-size は整数で指定してください: {value!r}"
        ) from None
    if size < 1:
        raise argparse.ArgumentTypeError(
            f"--batch-size は 1 以上で指定してください: {value}"
        )
    return size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sku_scanner.py",
        description="Google One の SKU / offer を読み取るだけのスキャナ（登録操作は行いません）",
    )
    commands = parser.add_subparsers(dest="command")

    generate = commands.add_parser("generate", help="照会対象の SKU 候補を生成する")
    _add_generation_flags(generate)
    _add_output_flags(generate)

    query = commands.add_parser("query", help="実機から offer を読み取る（読み取り専用）")
    target = query.add_mutually_exclusive_group()
    target.add_argument("--skus", nargs="+", metavar="SKU", help="照会する SKU を直接指定")
    target.add_argument("--candidates", action="store_true", help="生成した候補を全部照会")
    _add_generation_flags(query)
    query.add_argument(
        "--batch-size", type=_batch_size, default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"1 回の注入で照会する SKU 数（1 以上・既定: {DEFAULT_BATCH_SIZE}）",
    )
    query.add_argument(
        "--timeout", type=_timeout_seconds, default=8.0, metavar="SECONDS",
        help=f"各バッチの待ち時間（{MIN_TIMEOUT:g}〜{MAX_TIMEOUT:g} 秒・既定: 8）",
    )
    _add_output_flags(query)

    summarize = commands.add_parser("summarize", help="保存済みレポートを読みやすく整形する")
    summarize.add_argument("--input", required=True, metavar="PATH", help="入力レポート（JSON）")
    _add_output_flags(summarize)
    return parser


def _secure_write(path: str, text: str) -> None:
    """一時ファイル → os.replace で、.mode 0600 のレポートを残す。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".sku-scan-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _default_report_path() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(DEFAULT_REPORT_DIR / f"{REPORT_PREFIX}{stamp}.json")


def _candidate_kwargs(args: argparse.Namespace) -> dict:
    return {
        "tiers": _split_values(args.tiers),
        "periods": _split_values(args.periods),
        "suffixes": _split_values(args.suffixes),
        "include_variants": not args.no_variants,
        "limit": args.limit,
    }


def _format_candidate_table(skus: Sequence[str]) -> str:
    lines = ["SKU 候補:", "-" * 40]
    if not skus:
        lines.append("該当する候補はありません。")
    lines.extend(f"{index + 1:>3}. {sku}" for index, sku in enumerate(skus))
    return "\n".join(lines)


def _emit(text: str, args: argparse.Namespace, report_text: Optional[str] = None) -> Optional[str]:
    """標準出力に結果を出し、必要ならレポートも保存して保存先パスを返す。"""
    saved: Optional[str] = None
    if report_text is not None:
        saved = str(args.output or _default_report_path())
        _secure_write(saved, report_text)
    elif args.output:
        saved = str(args.output)
        _secure_write(saved, text)
    print(text)
    return saved


def _read_note() -> str:
    return (
        "注: この結果は読み取り専用スキャンの参考値です。"
        "適格性や実際の請求額を証明するものではありません。"
    )


class FridaSource:
    """実機の Google One に読み取り専用エージェントを注入して offer を集める。

    launch_app() や課金フローは呼ばない。既に動いているプロセスにだけ attach する。
    """

    # バッチの終端になりうるイベント。no_offers は商品ごとに送られて continue
    # するため（agent/injected.js:69）、ここに入れると残りの SKU が無言で
    # 未照会のまま捨てられる。
    TERMINAL_EVENTS = frozenset({"list_only", "no_trial", "query_err", "disc"})
    # 読み取り専用モードでは絶対に観測してはいけないイベント（課金導線の証拠）。
    FORBIDDEN_EVENTS = frozenset(
        {"launched", "stack_start", "activity", "purchase_cb"}
    )

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.batch_size = max(1, int(batch_size))
        self._timeout = 8.0
        self._device = None
        self._pid: Optional[int] = None
        self.timed_out = False
        self.batches_ok = 0
        self.batches_failed = 0

    @staticmethod
    def _find_pid(device) -> int:
        for process in device.enumerate_processes():
            name = getattr(process, "name", "") or ""
            if "subscriptions.red" in name or name == "Google One":
                return int(process.pid)
        raise RuntimeError(
            "Google One (com.google.android.apps.subscriptions.red) のプロセスが見つかりません"
        )

    @classmethod
    def _is_terminal(cls, event: object) -> bool:
        """バッチの完了通知かどうか。文字列以外は決して例外にしない。"""
        if not isinstance(event, str):
            return False
        if event in cls.TERMINAL_EVENTS:
            return True
        # boot_err / launch_err / mode_err ... はすべて異常終了として扱う。
        return event.endswith("_err") or event == "disc"

    @classmethod
    def _is_forbidden(cls, event: object) -> bool:
        """課金導線のイベントかどうか。文字列以外は False。"""
        return isinstance(event, str) and event in cls.FORBIDDEN_EVENTS

    @staticmethod
    def _pd_terminates(code: object, count: object) -> bool:
        """この pd（商品照会の応答）でもう結果は来ないか。

        agent は `pd` の code が 0 以外、または返却リストが空のとき、
        何も送らずに return する（agent/injected.js の onProductDetailsResponse）。
        終端扱いにしないと、存在しない SKU を照会するたびに必ずタイムアウトし、
        端末異常と誤読させる。
        """

        def _as_int(value: object) -> Optional[int]:
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        error_code = _as_int(code)
        size = _as_int(count)
        if size is not None and size <= 0:
            return True
        return error_code is not None and error_code != 0

    @staticmethod
    def _assert_read_only(js: str) -> None:
        """注入前の JS が本当に読み取り専用かを検証する（fail-closed）。

        build_js() の文字列置換は無検証だと静かに失敗し、エージェントが
        launchBillingFlow まで到達しうる。置換結果をここで必ず確認する。
        """
        if not isinstance(js, str) or not js:
            raise RuntimeError(
                "エージェント JS が空です（読み取り専用モードを検証できないため中止します）"
            )
        if js.count(LIST_ONLY_LITERAL) != 1:
            raise RuntimeError(
                "エージェント JS に読み取り専用フラグ "
                f"`{LIST_ONLY_LITERAL}` がちょうど 1 つ見つかりません"
                "（課金導線に到達する恐れがあるため中止します）"
            )
        if js.count(PRODUCTS_LITERAL) != 1:
            raise RuntimeError(
                "エージェント JS の PRODUCTS リテラルがちょうど 1 つ見つかりません"
                "（対象 SKU を安全に差し替えられないため中止します）"
            )

    @staticmethod
    def _load_js() -> str:
        """ハイフン入りファイル名を importlib で読み込み、list_only の JS を作る。

        .pyc を新規に作るとリポジトリを汚すため、exec_module の間だけ
        バイトコード書き込みを止める。読み込み後は必ず _assert_read_only を通す。
        """
        path = Path(__file__).resolve().parent / "ultra0-17.py"
        spec = importlib.util.spec_from_file_location("ultra0_17", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"エージェント生成モジュールを読み込めません: {path}")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        if not hasattr(module, "build_js"):
            raise RuntimeError(f"build_js() が見つかりません: {path}")
        js = module.build_js(plan="5x", list_only=True)
        FridaSource._assert_read_only(js)
        return js

    @staticmethod
    def _inject_products(js: str, batch: Sequence[str]) -> str:
        if PRODUCTS_LITERAL not in js:
            raise RuntimeError(
                "エージェント JS に PRODUCTS リテラルが見つかりません（build_js の出力を確認してください）"
            )
        replacement = "var PRODUCTS=" + json.dumps(list(batch), ensure_ascii=False) + ";"
        injected = js.replace(PRODUCTS_LITERAL, replacement, 1)
        # 置換で読み取り専用フラグを壊していないことを差し替え後にも確認する。
        if injected.count(LIST_ONLY_LITERAL) != 1:
            raise RuntimeError(
                "SKU 差し替え後の JS で読み取り専用フラグが失われました"
                "（課金導線に到達する恐れがあるため中止します）"
            )
        return injected

    def _run_batch(self, js: str) -> tuple:
        """1 バッチを実行し (収集イベント, タイムアウトしたか, 禁止イベント, スクリプトエラー) を返す。"""
        session = self._device.attach(self._pid)
        done = threading.Event()
        collected: list = []
        state: dict = {"forbidden": None, "script_error": False}
        script = None

        def on_message(message, _data) -> None:
            payload = message.get("payload") if isinstance(message, dict) else None
            if isinstance(payload, dict):
                event = payload.get("ev")
                if self._is_forbidden(event):
                    # LIST_ONLY が万一効いていなくても、ここで即座に止める。
                    state["forbidden"] = event
                    done.set()
                    return
                if event == "offer":
                    collected.append(payload)
                elif event == "pd":
                    # 0 件・エラーコードなら以降の結果は来ないので打ち切る。
                    if self._pd_terminates(payload.get("code"), payload.get("n")):
                        done.set()
                elif self._is_terminal(event):
                    done.set()
            elif isinstance(message, dict) and message.get("type") == "error":
                state["script_error"] = True
                done.set()

        try:
            script = session.create_script(js)
            script.on("message", on_message)
            script.load()
            completed = done.wait(timeout=self._timeout)
        finally:
            if script is not None:
                try:
                    script.unload()
                except Exception:
                    pass
            try:
                session.detach()
            except Exception:
                pass
        return collected, not completed, state["forbidden"], bool(state["script_error"])

    def _connect(self) -> None:
        """frida の import と接続だけを行い、失敗は日本語 1 行の RuntimeError にまとめる。

        frida の例外は Exception 直系で RuntimeError ではないため、
        呼び出し側の except 節をすり抜けてトレースバックにならないようここで包む。
        """
        try:
            import frida  # 実機接続時だけ import（generate/summarize は不要）

            self._device = frida.get_usb_device(timeout=self._timeout)
            self._pid = self._find_pid(self._device)
        except Exception as exc:  # noqa: BLE001 - frida 例外は RuntimeError ではない
            self._device = None
            raise RuntimeError(
                "端末に接続できませんでした"
                f"（USB デバッグ・frida-server 起動・Google One 起動を確認してください）: {exc}"
            ) from exc

    def fetch(self, skus: Sequence[str], timeout: float) -> list:
        self._timeout = float(timeout)
        self.timed_out = False
        self.batches_ok = 0
        self.batches_failed = 0
        events: list = []
        try:
            self._connect()
            template = self._load_js()
            for start in range(0, len(skus), self.batch_size):
                batch = list(skus[start : start + self.batch_size])
                try:
                    injected = self._inject_products(template, batch)
                    collected, timed_out, forbidden, script_error = self._run_batch(injected)
                except RuntimeError:
                    raise
                except Exception as exc:  # noqa: BLE001 - frida 例外も日本語 1 行にまとめる
                    self.batches_failed += 1
                    raise RuntimeError(
                        "バッチの注入に失敗しました"
                        f"（{start + 1}〜{start + len(batch)} 件目）: {exc}"
                    ) from exc
                if forbidden is not None:
                    self.batches_failed += 1
                    raise RuntimeError(
                        "読み取り専用モードに反する事象を検出したため中止しました"
                        f"（課金導線のイベント: {forbidden}）"
                    )
                if timed_out:
                    self.timed_out = True
                if timed_out or script_error:
                    self.batches_failed += 1
                else:
                    self.batches_ok += 1
                events.extend(collected)
        finally:
            self.close()
        return events

    def close(self) -> None:
        # セッションとスクリプトはバッチごとの finally で後始末済み。
        self._device = None
        self._pid = None


def _run_generate(args: argparse.Namespace) -> int:
    skus = core.generate_candidates(**_candidate_kwargs(args))
    text = (
        json.dumps(skus, ensure_ascii=False, indent=2)
        if args.format == "json"
        else _format_candidate_table(skus)
    )
    _emit(text, args)
    return 0


def _run_summarize(args: argparse.Namespace) -> int:
    try:
        with open(args.input, encoding="utf-8") as handle:
            payload = handle.read()
    except OSError as exc:
        raise ValueError(f"レポートを読み込めません: {args.input}: {exc}") from exc
    rows = core.parse_report(payload)
    text = (
        core.to_json(rows)
        if args.format == "json"
        else "\n".join(core.format_table(rows))
    )
    _emit(text, args)
    print(_read_note(), file=sys.stderr)
    return 0


def _run_query(args: argparse.Namespace, source_factory: Optional[Callable]) -> int:
    # 照会対象は明示させる。既定の候補を勝手に生成して端末に流すと、
    # 何を照会したのか分からないまま 100 件以上を注入してしまう。
    if not args.skus and not args.candidates:
        print(
            "エラー: query には --skus（照会する SKU）か --candidates（生成した候補を全部）の"
            "どちらかを指定してください。"
            "候補を確認するだけなら `scan generate` を使ってください。",
            file=sys.stderr,
        )
        return 2

    if args.skus:
        skus = list(args.skus)
    else:
        skus = core.generate_candidates(**_candidate_kwargs(args))
    if not skus:
        raise ValueError("照会する SKU がありません（--skus または候補生成条件を見直してください）")

    source = source_factory() if source_factory is not None else FridaSource(args.batch_size)
    try:
        events = source.fetch(skus, float(args.timeout))
    finally:
        source.close()

    timed_out = bool(getattr(source, "timed_out", False))
    # タイムアウトしたのにバッチ成否を報告しない情報源は成功とみなさない（fail-closed）。
    default_ok = 0 if timed_out else 1
    outcome = ScanOutcome(
        rows=core.dedupe_rows(core.parse_offer_events(events)),
        batches_ok=int(getattr(source, "batches_ok", default_ok)),
        batches_failed=int(getattr(source, "batches_failed", 0)),
        timed_out=timed_out,
    )

    report = core.to_json(outcome.rows)
    text = (
        report
        if args.format == "json"
        else "\n".join(core.format_table(outcome.rows))
    )
    saved = _emit(text, args, report_text=report)
    if saved is not None:
        print(f"レポートを保存しました: {saved}", file=sys.stderr)
    if outcome.timed_out:
        print(
            "警告: バッチがタイムアウトしました（終端イベントを受け取れませんでした）。"
            "端末の状態を確認し、--timeout を延ばすか "
            "`./scripts/run.sh check` で自検してください。",
            file=sys.stderr,
        )
    print(_read_note(), file=sys.stderr)
    return _exit_code_for(outcome)


def main(argv: Optional[Sequence[str]] = None, source_factory: Optional[Callable] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # argparse のエラーは SystemExit のまま返す
    try:
        if args.command == "generate":
            return _run_generate(args)
        if args.command == "summarize":
            return _run_summarize(args)
        if args.command == "query":
            return _run_query(args, source_factory)
        parser.print_help()
        return 2
    except Exception as exc:  # noqa: BLE001 - frida/端末の例外も日本語 1 行にまとめる
        # SystemExit / KeyboardInterrupt は Exception ではないので素通りする。
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
