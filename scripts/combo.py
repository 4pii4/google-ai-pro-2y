#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""combo.py —— アカウント状態に応じて無料オファーの積み上げを計画・実行する。

設計の要点:

- 既定はドライラン。端末に触れるのは ``--device`` を明示したときだけ。
  （``--device`` 無しで端末を読みに行くと、意図しない端末で事故るため）
- 実行には ``--execute`` と ``--yes`` の両方が必要。
- 課金画面を開くところまでがスクリプトの仕事で、支払いの確定は必ず人が行う。
  タップ・確定・カード追加は一切しない（``ultra0-17.confirm_zero()`` も呼ばない）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import combo_core as core

VENDING_PKG = "com.android.vending"
LIBRARY_DB = f"/data/data/{VENDING_PKG}/databases/library.db"

# 2026-11-24 の実測で無料だった SKU。1month_eft は何も返らないため含めない。
DEFAULT_OFFER_SKUS = (
    "g1.2tb.ai.2months_eft",
    "g1.2tb.ai.3months_eft",
    "g1.2tb.ai.4months_eft",
    "g1.2tb.ai.6months_eft",
    "g1.2tb.ai.12months_eft",
    "g1.2tb.ai.15months_eft",
    "g1.2tb.ai.18months_eft",
    "g1.2tb.ai.subs_variant",
)

# build_js(plan="auto", list_only=False) の生出力に 1 回ずつ現れるリテラル。
PRODUCTS_LITERAL = 'var PRODUCTS = ["g1.play.20tb", "g1.play.ai.pro", "g1.30tb"];'
PREFER_LITERAL = 'var PREFER = "";'
LIST_ONLY_LITERAL = "var LIST_ONLY=true;"
STACK_LITERAL = "var STACK=true;"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_DEVICE = 3

DEVICE_HINT = (
    "端末から状態と offer を読むには --device を指定してください"
    "（このコマンドは既定では端末に触れません）。"
)
EXECUTE_HINT = "実行するには --execute --yes が必要です"

COMMANDS = ("plan", "run")

TERMINAL_FAILURE_EVENTS = frozenset({"no_trial", "no_offers", "no_activity"})


# --------------------------------------------------------------------------
# 端末からの読み取り（読み取り専用）
# --------------------------------------------------------------------------
GOOGLE_ONE_PACKAGE = "com.google.android.apps.subscriptions.red"


def _is_google_one_foreground(dump: Optional[str]) -> bool:
    """`dumpsys activity activities` の出力に Google One が前面かを見る。"""
    if not dump:
        return False
    return GOOGLE_ONE_PACKAGE in str(dump)


def _no_activity_hint() -> str:
    return (
        "Google One に生きた画面（Activity）がありません。"
        "端末で Google One を開いて前面に表示してから、もう一度実行してください"
        "（アプリを起動しない設定の場合はご自身で開く必要があります）。"
    )


def _foreground_dump(timeout: float = 30) -> str:
    """前面アクティビティの情報を読むだけ（書き込みなし）。"""
    try:
        return _adb("shell", "dumpsys activity activities", timeout=timeout)
    except RuntimeError:
        return ""


def _adb_prefix() -> list:
    adb = os.environ.get("ADB_PATH") or "adb"
    serial = os.environ.get("ADB_SERIAL") or ""
    return [adb] + (["-s", serial] if serial else [])


def _adb(*args: str, timeout: float = 60) -> str:
    """adb を 1 回実行する。失敗は日本語 1 行の RuntimeError にまとめる。"""
    command = _adb_prefix() + list(args)
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "adb が見つかりません（platform-tools を入れるか ADB_PATH を設定してください）"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"adb がタイムアウトしました: adb {args[0]}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "ignore").strip()[:200]
        raise RuntimeError(f"adb が失敗しました（adb {args[0]}）: {detail}")
    return completed.stdout.decode("utf-8", "ignore")


def read_state(timeout: float = 60) -> list:
    """Play の library.db から所有行を読む（端末側・ホスト側のコピーは必ず消す）。

    書き込みはせず、/data/local/tmp と一時ファイルの 2 つを作って削除するだけ。
    """
    remote = f"/data/local/tmp/combo_library_{os.getpid()}.db"
    local = Path(tempfile.gettempdir()) / f"combo_library_{os.getpid()}.db"
    try:
        _adb(
            "shell",
            f"cp {LIBRARY_DB} {remote} 2>/dev/null; chmod 666 {remote}",
            timeout=timeout,
        )
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        _adb("pull", remote, str(local), timeout=timeout)
        if not local.exists() or local.stat().st_size == 0:
            raise RuntimeError(
                "library.db を取得できませんでした"
                "（root 権限と Play へのログインを確認してください）"
            )
        connection = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select doc_id, account, subs_valid_until_time from ownership"
            ).fetchall()
        finally:
            connection.close()
        return core.parse_ownership_rows(rows)
    finally:
        try:
            _adb("shell", f"rm -f {remote}", timeout=timeout)
        except Exception:  # noqa: BLE001 - 後始末の失敗で元の例外を隠さない
            pass
        try:
            local.unlink()
        except OSError:
            pass


def read_stack_token(account: str, sku: str = "g1.2tb.ai.subs_variant", timeout: float = 60) -> str:
    """Play の library.db から、対象アカウントの purchaseToken を読む。

    agent の `queryPurchasesAsync` は実機でコールバックが返らず積み上げが
    止まったため、このリポジトリで実績のある「トークンを直接渡す」経路を使う。
    読み取りのみで、端末側・ホスト側のコピーは必ず削除する。
    """
    remote = f"/data/local/tmp/combo_token_{os.getpid()}.db"
    local = Path(tempfile.gettempdir()) / f"combo_token_{os.getpid()}.db"
    try:
        _adb(
            "shell",
            f"cp {LIBRARY_DB} {remote} 2>/dev/null; chmod 666 {remote}",
            timeout=timeout,
        )
        try:
            local.unlink()
        except FileNotFoundError:
            pass
        _adb("pull", remote, str(local), timeout=timeout)
        if not local.exists() or local.stat().st_size == 0:
            return ""
        connection = sqlite3.connect(f"file:{local}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select account, subs_valid_until_time, inapp_purchase_data "
                "from ownership where doc_id like ?",
                (f"%{sku}%",),
            ).fetchall()
        finally:
            connection.close()
    finally:
        try:
            _adb("shell", f"rm -f {remote}", timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        try:
            local.unlink()
        except OSError:
            pass

    want = (account or "").strip().lower()
    best = None
    for row_account, valid, data in rows:
        if want and str(row_account or "").strip().lower() != want:
            continue
        if not data:
            continue
        try:
            token = (json.loads(data) or {}).get("purchaseToken")
        except ValueError:
            continue
        if not token:
            continue
        try:
            alive = int(valid or 0) > int(time.time() * 1000)
        except (TypeError, ValueError):
            alive = False
        if best is None or (alive and not best[1]):
            best = (token, alive)
    return best[0] if best else ""


def read_offers(
    timeout: float = 20.0,
    batch_size: int = 20,
    skus: Optional[Sequence[str]] = None,
) -> list:
    """読み取り専用スキャナ（list_only）で offer を集める。エージェントは再実装しない。"""
    import sku_scanner
    import sku_scanner_core

    source = sku_scanner.FridaSource(batch_size)
    try:
        events = source.fetch(list(skus or DEFAULT_OFFER_SKUS), float(timeout))
    finally:
        source.close()
    if getattr(source, "timed_out", False):
        print(
            "警告: offer の読み取りがタイムアウトしました（結果は不完全な可能性があります）。",
            file=sys.stderr,
        )
    return [
        {
            "sku": row.sku,
            "offer_id": row.offer_id,
            "period": row.period,
            "micros": row.micros,
        }
        for row in sku_scanner_core.parse_offer_events(events)
    ]


# --------------------------------------------------------------------------
# 課金画面を開く実行者（タップ・確定はしない）
# --------------------------------------------------------------------------
class RealExecutor:
    """対象 SKU / offerId で課金画面を開くだけの実行者。

    ``ultra0-17.py`` の ``build_js(plan="auto", list_only=False)`` を読み込み、
    PRODUCTS と PREFER のリテラルを対象に差し替える。読み取り専用フラグ
    （LIST_ONLY）が残っていたら課金画面が開かないので、その場合は中止する。
    ``launch_app()`` / ``confirm_zero()`` / ``add_card()`` は呼ばない。
    """

    def __init__(
        self,
        timeout: float = 20.0,
        open_app: bool = False,
        account: str = "",
        token_reader: Optional[Callable[[], str]] = None,
    ) -> None:
        self.timeout = float(timeout)
        self.open_app = bool(open_app)
        self.account = account or ""
        self._token_reader = token_reader

    def _stack_token(self) -> str:
        if self._token_reader is not None:
            return self._token_reader()
        if not self.account:
            return ""
        return read_stack_token(self.account, timeout=self.timeout)

    def _ensure_foreground(self) -> None:
        """課金画面は Google One の生きた Activity からしか開けない。

        前面に無いと agent は `no_activity` を送って何も表示せずに終わる。
        そのため実行前に前面かどうかを確かめ、必要なら明示指定時だけ前面化する。
        """
        if _is_google_one_foreground(_foreground_dump(self.timeout)):
            return
        if self.open_app:
            try:
                _adb(
                    "shell",
                    f"monkey -p {GOOGLE_ONE_PACKAGE} "
                    "-c android.intent.category.LAUNCHER 1",
                    timeout=self.timeout,
                )
            except RuntimeError:
                raise RuntimeError(_no_activity_hint()) from None
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                time.sleep(1.0)
                if _is_google_one_foreground(_foreground_dump(self.timeout)):
                    return
        raise RuntimeError(_no_activity_hint())

    @staticmethod
    def _load_build_js():
        path = Path(__file__).resolve().parent / "ultra0-17.py"
        spec = importlib.util.spec_from_file_location("ultra0_17_combo", str(path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"エージェント生成モジュールを読み込めません: {path}")
        module = importlib.util.module_from_spec(spec)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = previous
        build_js = getattr(module, "build_js", None)
        if build_js is None:
            raise RuntimeError(f"build_js() が見つかりません: {path}")
        return build_js

    def build_js(self, action: core.ComboAction, old_token: str = "") -> str:
        """対象 SKU / offerId を差し込んだ JS を作り、差し替えを検証する。"""
        return self._assemble(action, old_token=old_token)

    def _assemble(self, action: core.ComboAction, old_token: str = "") -> str:
        """JS を組み立てる（端末に触れないのでテストから直接呼べる）。

        積み上げでは ``STACK`` を立てる。これが無いと agent は
        ``launch(pd, offer)``（新規購入）を呼び、既存サブスクには適用されない。
        """
        is_stack = getattr(action, "kind", "") == "stack"
        js = self._load_build_js()(
            plan="auto",
            list_only=False,
            stack=is_stack,
            old_token=old_token or "",
        )
        if not isinstance(js, str) or not js:
            raise RuntimeError("エージェント JS が空です（中止します）")
        if LIST_ONLY_LITERAL in js:
            raise RuntimeError(
                "エージェント JS に読み取り専用フラグが残っています"
                "（このままでは課金画面が開かないため中止します）"
            )
        for literal, label in ((PRODUCTS_LITERAL, "PRODUCTS"), (PREFER_LITERAL, "PREFER")):
            if js.count(literal) != 1:
                raise RuntimeError(
                    f"エージェント JS の {label} リテラルがちょうど 1 つ見つかりません"
                    "（対象を安全に差し替えられないため中止します）"
                )
        if is_stack and STACK_LITERAL not in js:
            raise RuntimeError(
                "積み上げ用フラグ（STACK）を立てられませんでした。"
                "このままでは新規購入として扱われ、積み上げできません（中止します）"
            )

        products = f"var PRODUCTS=[{json.dumps(action.sku, ensure_ascii=False)}];"
        prefer = f"var PREFER={json.dumps(action.offer_id, ensure_ascii=False)};"
        injected = js.replace(PRODUCTS_LITERAL, products, 1)
        injected = injected.replace(PREFER_LITERAL, prefer, 1)

        if PRODUCTS_LITERAL in injected or PREFER_LITERAL in injected:
            raise RuntimeError("SKU / offerId の差し替えに失敗しました（中止します）")
        if LIST_ONLY_LITERAL in injected:
            raise RuntimeError("差し替え後の JS に読み取り専用フラグが入りました（中止します）")
        if products not in injected or prefer not in injected:
            raise RuntimeError("差し替え後の JS を検証できませんでした（中止します）")
        if is_stack and STACK_LITERAL not in injected:
            raise RuntimeError("差し替えで積み上げ用フラグが失われました（中止します）")
        return injected

    @staticmethod
    def _find_pid(device) -> int:
        for process in device.enumerate_processes():
            name = getattr(process, "name", "") or ""
            if "subscriptions.red" in name or name == "Google One":
                return int(process.pid)
        raise RuntimeError(
            "Google One (com.google.android.apps.subscriptions.red) のプロセスが"
            "見つかりません（端末で Google One を開いてください）"
        )

    def __call__(self, action: core.ComboAction) -> dict:
        old_token = ""
        if getattr(action, "kind", "") == "stack":
            old_token = self._stack_token()
            if not old_token:
                raise RuntimeError(
                    "積み上げに必要な購入トークンを library.db から取得できませんでした。"
                    "Play にログイン中のアカウントが --account と一致しているか、"
                    "サブスクが有効（自動更新オン）かを確認してください。"
                )
        js = self.build_js(action, old_token=old_token)
        self._ensure_foreground()
        try:
            import frida
        except ImportError as exc:
            raise RuntimeError(
                "frida が入っていません（./scripts/setup.sh を実行してください）"
            ) from exc

        try:
            device = frida.get_usb_device(timeout=10)
            pid = self._find_pid(device)
            session = device.attach(pid)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - frida 例外は RuntimeError ではない
            raise RuntimeError(
                "端末に接続できませんでした"
                f"（USB デバッグと Google One の起動を確認してください）: {exc}"
            ) from exc

        done = threading.Event()
        events: list = []
        state: dict = {"launched": None, "failure": None}
        script = None

        def on_message(message, _data) -> None:
            payload = message.get("payload") if isinstance(message, dict) else None
            if not isinstance(payload, dict):
                return
            event = payload.get("ev")
            if not isinstance(event, str):
                return
            # purchaseToken などの機微情報は保持しない。
            events.append(
                {
                    "ev": event,
                    "product": payload.get("product"),
                    "id": payload.get("id"),
                    "period": payload.get("period"),
                    "code": payload.get("code"),
                }
            )
            if event == "launched":
                state["launched"] = payload
                done.set()
            elif event in TERMINAL_FAILURE_EVENTS or event.endswith("_err"):
                state["failure"] = event
                done.set()

        try:
            script = session.create_script(js)
            script.on("message", on_message)
            script.load()
            completed = done.wait(timeout=self.timeout)
        finally:
            if script is not None:
                try:
                    script.unload()
                except Exception:  # noqa: BLE001
                    pass
            try:
                session.detach()
            except Exception:  # noqa: BLE001
                pass

        if state["launched"] is not None:
            return {"launched": True, "event": "launched", "events": events}
        if state["failure"] is not None:
            return {"launched": False, "event": state["failure"], "events": events}
        return {
            "launched": False,
            "event": None,
            "timed_out": not completed,
            "events": events,
        }


# --------------------------------------------------------------------------
# プラン組み立て
# --------------------------------------------------------------------------
@dataclass
class PlanBundle:
    state_class: str
    latest_end_ms: Optional[int]
    has_stack_offers: bool
    possible: bool
    verdict: str
    actions: list
    now_ms: int
    account_warning: Optional[str]
    accounts: list = field(default_factory=list)


def _normalize_states(items: Iterable[object]) -> list:
    """SubscriptionState でも生の所有行（tuple）でも受け付ける。"""
    states: list = []
    raw_rows: list = []
    for item in items:
        if isinstance(item, core.SubscriptionState):
            states.append(item)
        elif isinstance(item, dict) and "doc_id" in item:
            raw_rows.append(
                (item["doc_id"], item.get("account", ""), item.get("subs_valid_until_time"))
            )
        elif isinstance(item, (tuple, list)) and len(item) >= 3:
            raw_rows.append((item[0], item[1], item[2]))
        else:
            raise ValueError(f"所有行として解釈できません: {item!r}")
    if raw_rows:
        states.extend(core.parse_ownership_rows(raw_rows))
    return states


def _account_warning(states: Sequence[core.SubscriptionState], account: str) -> Optional[str]:
    """指定アカウントが library.db の所有行と一致するかを確認する（読み取りのみ）。

    既存の ``current_play_account()`` は Play の UI をタップするため、支払い以外で
    画面操作をしない方針に合わせて使わない。所有行の account 列で確認する。
    """
    want = account.strip().lower()
    if not want:
        return None
    accounts = sorted({state.account.strip().lower() for state in states if state.account.strip()})
    if not accounts:
        return (
            f"警告: 指定アカウント {account} を library.db の所有行から確認できませんでした"
            "（サブスクの所有行がありません）。"
        )
    if want not in accounts:
        return (
            f"警告: 指定アカウント {account} は library.db の所有行と一致しません"
            f"（検出: {', '.join(accounts)}）。Play にログイン中のアカウントを確認してください。"
        )
    return None


def _collect(
    args: argparse.Namespace,
    state_reader: Callable[[], list],
    offer_reader: Callable[[], list],
) -> PlanBundle:
    now_ms = int(time.time() * 1000)
    raw_states = _normalize_states(list(state_reader()))
    states = (
        core.filter_by_account(raw_states, args.account)
        if getattr(args, "account", None)
        else raw_states
    )
    offers = list(offer_reader())
    state_class = core.classify_state(states, now_ms)
    latest = core.latest_end_ms(states)
    stack_available = core.has_stack_offers(offers)
    possible, verdict = core.stack_is_possible(state_class, stack_available, args.auto_renew)
    actions = core.plan_actions(
        state_class,
        offers,
        now_ms=now_ms,
        max_steps=args.max_steps,
        base_end_ms=latest,
    )
    return PlanBundle(
        state_class=state_class,
        latest_end_ms=latest,
        has_stack_offers=stack_available,
        possible=possible,
        verdict=verdict,
        actions=actions,
        now_ms=now_ms,
        account_warning=_account_warning(raw_states, args.account) if args.account else None,
        accounts=core.distinct_accounts(raw_states),
    )


def _print_plan(bundle: PlanBundle, args: argparse.Namespace) -> None:
    account = getattr(args, "account", None)
    if account:
        print(f"対象アカウント: {account}")
    elif len(bundle.accounts) > 1:
        print(f"対象アカウント: 未指定（{len(bundle.accounts)} 件の所有行のうち最も遅い期限で計算）")
    for line in core.format_plan(
        bundle.actions,
        bundle.state_class,
        bundle.latest_end_ms,
        auto_renew=args.auto_renew,
        has_stack_offers=bundle.has_stack_offers,
    ):
        print(line)
    if bundle.account_warning:
        print(bundle.account_warning, file=sys.stderr)


def _resolve_readers(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
) -> Optional[tuple]:
    state = state_reader
    offers = offer_reader
    if args.device:
        state = state or read_state
        offers = offers or read_offers
    if state is None or offers is None:
        return None
    return state, offers


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------
def _wait_seconds_type(value: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"秒数を数値で指定してください: {value!r}") from None
    if seconds < 0:
        raise argparse.ArgumentTypeError(f"0 以上で指定してください: {value}")
    return seconds


def _max_steps_type(value: str) -> int:
    try:
        steps = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"整数で指定してください: {value!r}") from None
    if steps < 0:
        raise argparse.ArgumentTypeError(f"0 以上で指定してください: {value}")
    return steps


def _print_step(index: int, total: int, action: core.ComboAction) -> None:
    label = "新規登録" if action.kind == core.KIND_NEW else "積み上げ"
    expected = (
        datetime.fromtimestamp(action.expected_end_ms / 1000).strftime("%Y-%m-%d")
        if action.expected_end_ms
        else "-"
    )
    print()
    print(
        f"[{index}/{total}] {label}: {action.sku} / {action.offer_id}"
        f"（+{action.months}ヶ月 → {expected}）"
    )
    print("  端末の Play の課金画面を確認し、支払いを完了してください")
    print("  ・金額が 0 円（無料）であることを確認する")
    print("  ・「定期購入を解約」は押さない（解約すると積み上げできなくなります）")
    if action.kind == core.KIND_NEW:
        print("  ・別 SKU のオファーは、今のサブスクの置き換えになることがあります")


def _wait_for_human(args: argparse.Namespace, index: int, total: int) -> None:
    if args.non_interactive:
        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        return
    print(
        f"  支払いを完了したら Enter を押してください（{index}/{total}）… ",
        end="",
        flush=True,
    )
    try:
        input()
    except EOFError as exc:
        raise RuntimeError(
            "Enter を待てませんでした（標準入力が閉じています）。"
            "端末の前で実行するか、--non-interactive --wait-seconds N を使ってください"
        ) from exc
    print()


def _execute_actions(
    args: argparse.Namespace,
    bundle: PlanBundle,
    state_reader: Callable[[], list],
    executor: Callable[[core.ComboAction], object],
) -> int:
    """プランを 1 ステップずつ実行する。

    すでに使用済みのオファーが候補に残っていることがある（「途中までプランを
    持っている人」がまさにこの状態）。そこで中断すると、後ろにある未使用の
    オファーに到達できないため、既定では失敗したステップを飛ばして続行する。
    `--stop-on-failure` を付けたときだけ、最初の失敗で中断する。
    """
    total = len(bundle.actions)
    before = bundle.latest_end_ms if bundle.latest_end_ms is not None else bundle.now_ms
    succeeded: list = []
    skipped: list = []
    strict = bool(getattr(args, "stop_on_failure", False))

    for index, action in enumerate(bundle.actions, start=1):
        _print_step(index, total, action)
        result = executor(action)
        if isinstance(result, dict) and result.get("launched") is False:
            event = result.get("event") or ("タイムアウト" if result.get("timed_out") else "不明")
            print(
                f"  課金画面を開けませんでした（{event}）。"
                "このオファーは使用済みか、このアカウントでは使えない可能性があります。",
            )
            skipped.append(action.offer_id)
            if strict:
                print("  --stop-on-failure が指定されているため中断します。")
                return EXIT_FAILED
            print("  次のステップへ進みます。")
            continue

        _wait_for_human(args, index, total)

        states = _normalize_states(list(state_reader()))
        if getattr(args, "account", None):
            states = core.filter_by_account(states, args.account)
        after = core.latest_end_ms(states)
        if not core.is_meaningful_extension(before, after):
            print(
                "  有効期限が伸びていません。"
                "このオファーはすでに使用済みか、適用条件を満たしていない可能性があります。",
            )
            skipped.append(action.offer_id)
            if strict:
                print("  --stop-on-failure が指定されているため中断します。")
                return EXIT_FAILED
            print("  次のステップへ進みます。")
            continue
        print(f"  ✅ 有効期限が {_date(before)} → {_date(after)} に伸びました。")
        succeeded.append(action.offer_id)
        before = after

    print()
    if skipped:
        print(f"適用できなかったオファー（{len(skipped)} 件）: {', '.join(skipped)}")
        print("  使用済み、またはこのアカウントでは使えないオファーです。")
    if not succeeded:
        print("有効期限が伸びたステップはありませんでした。")
        print(
            "Play の「お支払いと定期購入」で現在の状態を確認してください。"
            "積み上げ先がすべて使用済みなら、これ以上延ばせない可能性があります。"
        )
        return EXIT_FAILED

    print(f"{len(succeeded)} 件のステップで有効期限が伸びました。")
    print("最後に Play の「お支払いと定期購入」で期限と金額を確認してください。")
    return EXIT_OK


def _date(ms_value: Optional[int]) -> str:
    if ms_value is None:
        return "-"
    return time.strftime("%Y-%m-%d", time.localtime(ms_value / 1000))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device", action="store_true",
        help="端末から状態と offer を読む（指定しない場合、端末には一切触れません）",
    )
    parser.add_argument(
        "--account", metavar="EMAIL",
        help="想定アカウント。library.db の所有行と違えば警告する",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0, metavar="SECONDS",
        help="offer 読み取りと課金画面の待ち時間（既定: 20）",
    )
    parser.add_argument(
        "--open-app", action="store_true",
        help="Google One が前面に無いとき、端末側で Google One を前面に出す"
             "（既定は出さない＝ご自身で開いてください）",
    )
    parser.add_argument(
        "--max-steps", type=_max_steps_type, default=4, metavar="N",
        help="プランの最大ステップ数（既定: 4）",
    )
    parser.add_argument(
        "--auto-renew", action=argparse.BooleanOptionalAction, default=True,
        help="サブスクの自動更新がオンかどうか（積み上げ可否の判定に使う・既定: オン）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="combo.py",
        description=(
            "Google AI Pro の無料オファーを、アカウント状態に応じて計画・実行する"
            "（既定はドライラン・支払いは人が行う）"
        ),
    )
    commands = parser.add_subparsers(dest="command")

    plan = commands.add_parser("plan", help="状態と offer を読んでプランを表示する（実行しない）")
    _add_common_flags(plan)

    run = commands.add_parser("run", help="プランを実行する（--execute --yes が必要）")
    _add_common_flags(run)
    run.add_argument("--execute", action="store_true", help="実行を許可する（--yes と同時に必要）")
    run.add_argument("--yes", action="store_true", help="確認を省略して実行する")
    run.add_argument(
        "--non-interactive", action="store_true",
        help="Enter 待ちをせず、--wait-seconds だけ待つ（自動化・検証用）",
    )
    run.add_argument(
        "--wait-seconds", type=_wait_seconds_type, default=30.0, metavar="SECONDS",
        help="--non-interactive のときに待つ秒数（既定: 30）",
    )
    run.add_argument(
        "--stop-on-failure", action="store_true",
        help="1 ステップでも期限が伸びなければ中断する（既定は飛ばして続行）",
    )
    return parser


def _normalize_argv(argv: Optional[Sequence[str]]) -> list:
    """サブコマンド省略時は plan。フラグだけ渡された場合も plan として扱う。"""
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        return ["plan"]
    if values[0] in COMMANDS or values[0] in ("-h", "--help"):
        return values
    return ["plan"] + values


def _plan(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
) -> int:
    readers = _resolve_readers(args, state_reader, offer_reader)
    if readers is None:
        print(DEVICE_HINT, file=sys.stderr)
        return EXIT_NO_DEVICE
    bundle = _collect(args, *readers)
    _print_plan(bundle, args)
    return EXIT_OK


def _run(
    args: argparse.Namespace,
    state_reader: Optional[Callable[[], list]],
    offer_reader: Optional[Callable[[], list]],
    executor: Optional[Callable[[core.ComboAction], object]],
) -> int:
    if not (args.execute and args.yes):
        readers = _resolve_readers(args, state_reader, offer_reader)
        if readers is None:
            print(DEVICE_HINT, file=sys.stderr)
        else:
            _print_plan(_collect(args, *readers), args)
            print()
        print(EXECUTE_HINT)
        return EXIT_USAGE

    readers = _resolve_readers(args, state_reader, offer_reader)
    if readers is None:
        print(DEVICE_HINT, file=sys.stderr)
        return EXIT_NO_DEVICE
    state_fn, offer_fn = readers

    bundle = _collect(args, state_fn, offer_fn)
    _print_plan(bundle, args)
    print()
    if len(bundle.accounts) > 1 and not getattr(args, "account", None):
        print(
            "複数アカウントの所有行が見つかりました。"
            "どのアカウントを対象にするか --account <email> で指定してください。",
            file=sys.stderr,
        )
        for name in bundle.accounts:
            print(f"  - {name}", file=sys.stderr)
        print(
            "（アカウントが混ざると、別アカウントの期限を比較して"
            "「伸びた」と誤判定します）",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if not bundle.possible:
        print(f"この状態では実行しません: {bundle.verdict}", file=sys.stderr)
        return EXIT_FAILED
    if not bundle.actions:
        print("実行できる無料オファーが無いため中止します。", file=sys.stderr)
        return EXIT_FAILED

    runner = (
        executor
        if executor is not None
        else RealExecutor(
            timeout=args.timeout,
            open_app=getattr(args, "open_app", False),
            account=getattr(args, "account", "") or "",
        )
    )
    return _execute_actions(args, bundle, state_fn, runner)


def main(
    argv: Optional[Sequence[str]] = None,
    state_reader: Optional[Callable[[], list]] = None,
    offer_reader: Optional[Callable[[], list]] = None,
    executor: Optional[Callable[[core.ComboAction], object]] = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(argv))
    try:
        if args.command == "run":
            return _run(args, state_reader, offer_reader, executor)
        return _plan(args, state_reader, offer_reader)
    except Exception as exc:  # noqa: BLE001 - adb/frida の例外も日本語 1 行にまとめる
        print(f"エラー: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
