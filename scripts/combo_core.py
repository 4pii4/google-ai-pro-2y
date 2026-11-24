"""combo_core.py —— 無料オファー積み上げ（コンボ）プランの純粋ロジック。

frida / adb / ネットワークに一切依存しない。デバイスから読んだ所有行と
offer 一覧を受け取り、「今どの状態か」「何をどの順で実行するか」だけを決める。
支払いの確定はこのモジュールの責務ではない（人が行う）。
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

# library.db の doc_id は `subs:com.google.android.apps.subscriptions.red:<sku>`。
# `subs:` を要求するのは、同じパッケージの買い切り（inapp:...）を
# サブスク行と誤認しないため。
SUBSCRIPTION_PREFIX = "subs:"
SUBSCRIPTION_MARKER = "subscriptions.red:"
# 積み上げ用オファーを持つ SKU の末尾。
STACK_SKU_SUFFIX = ".subs_variant"
# subs_variant の通常プラン（無料ではない）を表す offerId。
BASE_OFFER_ID = "null"
# 新規登録に使う単体オファーの SKU 末尾。
NEW_SUBSCRIPTION_SUFFIX = "_eft"

STATE_FRESH = "fresh"
STATE_ACTIVE = "active"
STATE_EXPIRED = "expired"
STATE_CLASSES = (STATE_FRESH, STATE_ACTIVE, STATE_EXPIRED)

KIND_NEW = "new_subscription"
KIND_STACK = "stack"

_PERIOD_RE = re.compile(r"P(?:(\d+)Y)?(?:(\d+)M)?")
_INT_RE = re.compile(r"[+-]?\d+")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _coerce_ms(value: object) -> Optional[int]:
    """epoch ミリ秒として読める値だけ int にする。不明は None（推測しない）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


@dataclass
class SubscriptionState:
    """library.db の所有行 1 件に対応するサブスク状態。"""

    sku: str
    valid_until_ms: Optional[int]
    account: str
    active: bool


def parse_ownership_rows(
    rows: Iterable[object], now_ms: Optional[int] = None
) -> list:
    """ownership の行からサブスク行だけを取り出す。

    rows は `(doc_id, account, subs_valid_until_time)` のタプル列。
    サブスク行は `subs:com.google.android.apps.subscriptions.red:<sku>` の形で、
    SKU は最後の `:` より後ろ。壊れた行は例外にせず読み飛ばす
    （読めない行 1 件で全体を止めない）。
    """
    now = _now_ms() if now_ms is None else int(now_ms)
    states: list = []
    for row in rows:
        try:
            doc_id, account, valid_until = row
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(doc_id, str)
            or not doc_id.startswith(SUBSCRIPTION_PREFIX)
            or SUBSCRIPTION_MARKER not in doc_id
        ):
            continue
        sku = doc_id.rsplit(":", 1)[-1].strip()
        if not sku:
            continue
        valid_ms = _coerce_ms(valid_until)
        states.append(
            SubscriptionState(
                sku=sku,
                valid_until_ms=valid_ms,
                account=account.strip() if isinstance(account, str) else "",
                active=valid_ms is not None and valid_ms > now,
            )
        )
    return states


def latest_end_ms(states: Sequence[SubscriptionState]) -> Optional[int]:
    """有効な状態のうち最も遅い期限。有効な行が無ければ None。"""
    ends = [
        state.valid_until_ms
        for state in states
        if state.active and state.valid_until_ms is not None
    ]
    return max(ends) if ends else None


def classify_state(
    states: Sequence[SubscriptionState], now_ms: Optional[int] = None
) -> str:
    """fresh（サブスク行なし）/ active（1 件以上有効）/ expired（全部期限切れ）。"""
    now = _now_ms() if now_ms is None else int(now_ms)
    if not states:
        return STATE_FRESH
    if any(
        state.valid_until_ms is not None and state.valid_until_ms > now
        for state in states
    ):
        return STATE_ACTIVE
    return STATE_EXPIRED


@dataclass
class ComboAction:
    """1 ステップ分の実行内容。expected_end_ms はカレンダー加算の見込み値。"""

    kind: str
    sku: str
    offer_id: str
    months: int
    expected_end_ms: Optional[int]
    note: str


def months_from_period(period: object) -> int:
    """Play の billingPeriod（P1Y6M など）を月数にする。不明は 0。"""
    if not isinstance(period, str):
        return 0
    match = _PERIOD_RE.fullmatch(period.strip().upper())
    if match is None:
        return 0
    years = int(match.group(1) or 0)
    months = int(match.group(2) or 0)
    return 12 * years + months


def add_months_ms(base_ms: int, months: int) -> int:
    """暦月で加算する（1/31 + 1ヶ月 は 2/28 に丸める）。"""
    moment = datetime.fromtimestamp(int(base_ms) / 1000)
    total = moment.month - 1 + int(months)
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return int(moment.replace(year=year, month=month, day=day).timestamp() * 1000)


def _format_date(ms_value: Optional[int]) -> str:
    if ms_value is None:
        return "-"
    return datetime.fromtimestamp(int(ms_value) / 1000).strftime("%Y-%m-%d")


def _offer_field(offer: object, key: str) -> str:
    if not isinstance(offer, dict):
        return ""
    value = offer.get(key)
    return value.strip() if isinstance(value, str) else ""


def _is_free(offer: object) -> bool:
    """micros がちょうど 0 のときだけ無料とみなす（不明は無料扱いしない）。"""
    if not isinstance(offer, dict):
        return False
    micros = _coerce_ms(offer.get("micros"))
    return micros == 0


def stack_offers(offers: Iterable[object]) -> list:
    """積み上げに使える無料オファー（subs_variant・無料・期間既知）を月数の降順で返す。"""
    picked: dict = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        sku = _offer_field(offer, "sku")
        offer_id = _offer_field(offer, "offer_id")
        if not sku.endswith(STACK_SKU_SUFFIX) or not offer_id:
            continue
        if offer_id == BASE_OFFER_ID or not _is_free(offer):
            continue
        months = months_from_period(offer.get("period"))
        if months <= 0 or offer_id in picked:
            continue
        picked[offer_id] = offer
    return sorted(
        picked.values(),
        key=lambda offer: (
            -months_from_period(offer.get("period")),
            _offer_field(offer, "offer_id"),
        ),
    )


def has_stack_offers(offers: Iterable[object]) -> bool:
    """積み上げ可能な無料オファーが 1 件でもあるか。"""
    return bool(stack_offers(offers))


def new_subscription_offer(offers: Iterable[object]) -> Optional[dict]:
    """新規登録に使う「期間が最長の無料単体オファー」を返す。"""
    candidates = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        sku = _offer_field(offer, "sku")
        offer_id = _offer_field(offer, "offer_id")
        if not sku.endswith(NEW_SUBSCRIPTION_SUFFIX) or not offer_id:
            continue
        if sku.endswith(STACK_SKU_SUFFIX) or offer_id == BASE_OFFER_ID:
            continue
        if not _is_free(offer):
            continue
        months = months_from_period(offer.get("period"))
        if months <= 0:
            continue
        candidates.append((months, sku, offer_id, offer))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return candidates[0][3]


def stack_is_possible(
    state_class: str, has_stack_offers: bool, auto_renew: bool
) -> tuple:
    """積み上げ可否と、その理由（日本語 1 文）を返す。"""
    if state_class == STATE_FRESH:
        return True, "新規登録として最長の無料オファーから開始できます"
    if state_class == STATE_EXPIRED:
        return True, "期限切れのため新規登録として扱えます"
    if state_class == STATE_ACTIVE:
        if not has_stack_offers:
            return False, "積み上げ可能な無料オファーが見つかりません"
        if not auto_renew:
            return False, "自動更新がオフのため積み上げできません（先に解約しないこと）"
        return True, "有効なサブスクがあるため積み上げ可能"
    raise ValueError(f"不明な状態です: {state_class!r}")


def plan_actions(
    state_class: str,
    offers: Iterable[object],
    *,
    now_ms: Optional[int] = None,
    max_steps: Optional[int] = 4,
    base_end_ms: Optional[int] = None,
) -> list:
    """実行するステップ列を組み立てる。

    - fresh / expired: 最長の無料単体オファーで新規登録し、その後 subs_variant の
      無料オファーを月数の降順で積み上げる。
    - active: subs_variant の無料オファーだけを積み上げる。有効なサブスクを
      置き換える `new_subscription` は絶対に作らない。
    - base_end_ms を渡すと、その日付から月数を積む（渡さなければ now_ms から）。
    """
    if state_class not in STATE_CLASSES:
        raise ValueError(f"不明な状態です: {state_class!r}")
    now = _now_ms() if now_ms is None else int(now_ms)
    steps = 4 if max_steps is None else max(0, int(max_steps))
    if steps == 0:
        return []

    candidates: list = []
    if state_class == STATE_ACTIVE:
        candidates.extend((KIND_STACK, offer) for offer in stack_offers(offers))
    else:
        chosen = new_subscription_offer(offers)
        if chosen is not None:
            candidates.append((KIND_NEW, chosen))
        candidates.extend((KIND_STACK, offer) for offer in stack_offers(offers))

    actions: list = []
    seen_offer_ids: set = set()
    end_ms = int(base_end_ms) if base_end_ms is not None else now
    for kind, offer in candidates:
        if len(actions) >= steps:
            break
        offer_id = _offer_field(offer, "offer_id")
        months = months_from_period(offer.get("period"))
        if not offer_id or months <= 0 or offer_id in seen_offer_ids:
            continue
        seen_offer_ids.add(offer_id)
        end_ms = add_months_ms(end_ms, months)
        actions.append(
            ComboAction(
                kind=kind,
                sku=_offer_field(offer, "sku"),
                offer_id=offer_id,
                months=months,
                expected_end_ms=end_ms,
                note=_action_note(kind, months),
            )
        )
    return actions


def _action_note(kind: str, months: int) -> str:
    if kind == KIND_NEW:
        return (
            f"新規登録として +{months}ヶ月の無料オファーを使います。"
            "有効なサブスクがある場合は置き換えになるため実行しません。"
        )
    return (
        f"既存のサブスクに +{months}ヶ月を積み上げます。"
        "購入前に Play の画面で金額が 0 円であることを確認してください。"
    )


def format_plan(
    actions: Sequence[ComboAction],
    state_class: str,
    latest_end_ms: Optional[int],
    *,
    auto_renew: bool = True,
    has_stack_offers: Optional[bool] = None,
) -> list:
    """人が読むプラン（日本語の行リスト）を返す。"""
    lines = ["Google AI Pro 無料オファー コンボプラン", "-" * 40]
    if latest_end_ms is None:
        lines.append("現在の有効期限: なし（未加入または期限切れ）")
    else:
        lines.append(f"現在の有効期限: {_format_date(latest_end_ms)}")

    stack_available = (
        any(action.kind == KIND_STACK for action in actions)
        if has_stack_offers is None
        else bool(has_stack_offers)
    )
    possible, verdict = stack_is_possible(state_class, stack_available, auto_renew)
    lines.append(f"判定: {verdict}")
    if not possible:
        lines.append("この状態では実行しません（上の判定の条件を満たしてから実行してください）。")

    if actions:
        lines.append("実行プラン:")
        for index, action in enumerate(actions, start=1):
            label = "新規" if action.kind == KIND_NEW else "積み上げ"
            lines.append(
                f"{index}. [{label}] {action.sku} / {action.offer_id} / "
                f"+{action.months}ヶ月 → {_format_date(action.expected_end_ms)}"
            )
    else:
        lines.append(
            "実行できる無料オファーが見つかりませんでした"
            "（読み取り専用スキャンの結果を確認してください）。"
        )

    lines.extend(
        [
            "注意: micros=0 は最初の請求段階の価格が 0 であることしか示しません。",
            "      無料トライアルの適格性や実際の請求額を証明するものではないため、"
            "実際の請求額と日付は Play の課金画面で必ず確認してください。",
            "注意: 有効なサブスクがある状態で別 SKU のオファーを買うと、"
            "積み上げではなく現在のサブスクの置き換えになることがあります。",
            "注意: 支払いの確定は必ず人が行います（このスクリプトは「登録」を押しません）。",
        ]
    )
    return lines


# --------------------------------------------------------------------------
# アカウント単位の判定と「本当に伸びたか」の判定
# --------------------------------------------------------------------------
#: これ未満の増加は「期限が伸びた」と見なさない（Play が時刻を書き直すことがある）。
MIN_EXTENSION_MS = 24 * 60 * 60 * 1000


def is_meaningful_extension(before_ms: Optional[int], after_ms: Optional[int]) -> bool:
    """期限が実質的に伸びたか。

    実機では同じ日のまま数時間だけ値が動くことがあった（Play が所有行を
    書き直すため）。ミリ秒の僅差を成功と誤判定すると、適用できていないのに
    「伸びた」と表示してしまうので、1 日以上の増加だけを成功とする。
    """
    if after_ms is None:
        return False
    if before_ms is None:
        return True
    return int(after_ms) - int(before_ms) >= MIN_EXTENSION_MS


def filter_by_account(states: Iterable[object], account: str) -> list:
    """指定アカウントの行だけに絞る（複数アカウントの混線を防ぐ）。"""
    target = (account or "").strip().lower()
    if not target:
        return list(states)
    return [s for s in states if str(getattr(s, "account", "")).strip().lower() == target]


def distinct_accounts(states: Iterable[object]) -> list:
    """所有行に現れるアカウントの一覧（重複なし・昇順）。"""
    names = {
        str(getattr(s, "account", "")).strip()
        for s in states
        if str(getattr(s, "account", "")).strip()
    }
    return sorted(names)
