"""sku_scanner_core.py —— 読み取り専用 SKU スキャナの純粋ロジック。

frida / adb / ネットワークに依存しない。offer イベントからは許可リストの
5 項目だけを取り出し、トークンやアカウント情報は一切保持しない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

G1_PREFIX = "g1"
DEFAULT_TIERS = ("2tb.ai", "play.2tb.ai", "5tb.ai", "ai.pro")
DEFAULT_PERIODS = (
    "1month",
    "2months",
    "3months",
    "4months",
    "6months",
    "12months",
    "15months",
    "18months",
)
DEFAULT_SUFFIXES = ("_eft", "_ip_90p", "_ip_90p.acq", "_ip_95p.acq")
VARIANT_SUFFIX = ".subs_variant"

# 出力・入力の両方で使うフィールドの許可リスト（ここに無い鍵は保存しない）。
ROW_FIELDS = ("sku", "offer_id", "period", "micros", "title")

_PERIOD_RE = re.compile(r"(\d+)(months?)")
_TIER_RE = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_SUFFIX_RE = re.compile(r"[._][A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
_VARIANT_TAIL_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
_PERIOD_TOKEN_RE = re.compile(r"\.(\d+months?)([._].+)$")
_SKU_TAIL_RE = re.compile(r"(\d+months?)([._].+)$")
_MICROS_RE = re.compile(r"[+-]?\d+")
_YEAR_RE = re.compile(r"P(\d+)Y")
_MONTH_RE = re.compile(r"P(\d+)M")
_UNKNOWN_PERIOD_MONTHS = 10**6


def validate_period(period: str) -> str:
    """期間表記を正規化する（1 は単数形、それ以外は複数形）。"""
    if not isinstance(period, str):
        raise ValueError(f"period は文字列で指定してください: {period!r}")
    match = _PERIOD_RE.fullmatch(period.strip())
    if match is None or int(match.group(1)) < 1:
        raise ValueError(f"不正な period です: {period!r}")
    months = int(match.group(1))
    return "1month" if months == 1 else f"{months}months"


def build_sku(tier: str, period: str, suffix: str) -> str:
    """tier / period / suffix から g1 の SKU を組み立てる。"""
    if not isinstance(tier, str) or _TIER_RE.fullmatch(tier or "") is None:
        raise ValueError(f"不正な tier です: {tier!r}")
    if not isinstance(suffix, str) or _SUFFIX_RE.fullmatch(suffix or "") is None:
        raise ValueError(f"不正な suffix です: {suffix!r}")
    if period:
        return f"{G1_PREFIX}.{tier}.{validate_period(period)}{suffix}"
    return f"{G1_PREFIX}.{tier}{suffix}"


def _split_sku_tail(tier: str, tail: str) -> Optional[tuple]:
    """tier より後の文字列を (tier, period, suffix) に分ける。"""
    match = _SKU_TAIL_RE.fullmatch(tail)
    if match is not None:
        return (tier, match.group(1), match.group(2))
    if _VARIANT_TAIL_RE.fullmatch(tail) is not None:
        return (tier, "", "." + tail)
    return None


def parse_sku(sku: str) -> Optional[tuple]:
    """SKU を (tier, period, suffix) に分解する。不正なら None。

    ドットを含む tier（例: 2tb.ai）を誤って切らないよう、既知 tier を
    長い順に試してから、期間トークン／変種サフィックスで分割する。
    """
    if not isinstance(sku, str):
        return None
    prefix = G1_PREFIX + "."
    if not sku.startswith(prefix):
        return None
    body = sku[len(prefix) :]

    for tier in sorted(set(DEFAULT_TIERS), key=len, reverse=True):
        head = tier + "."
        if body.startswith(head):
            parsed = _split_sku_tail(tier, body[len(head) :])
            if parsed is not None:
                return parsed

    # 既知 tier に無い値でも、期間トークンがあれば分解できる。
    match = _PERIOD_TOKEN_RE.search(body)
    if match is not None:
        tier = body[: match.start()]
        if _TIER_RE.fullmatch(tier) is not None:
            return (tier, match.group(1), match.group(2))
    if body.endswith(VARIANT_SUFFIX):
        tier = body[: -len(VARIANT_SUFFIX)].rstrip(".")
        if _TIER_RE.fullmatch(tier) is not None:
            return (tier, "", VARIANT_SUFFIX)
    return None


def generate_candidates(
    tiers: Sequence[str] = DEFAULT_TIERS,
    periods: Sequence[str] = DEFAULT_PERIODS,
    suffixes: Sequence[str] = DEFAULT_SUFFIXES,
    include_variants: bool = True,
    limit: Optional[int] = None,
) -> list:
    """Unit + period + suffix の組み合わせを重複なく決定的に生成する。"""
    for period in periods:
        validate_period(period)
    normalized_periods = [validate_period(period) for period in periods]

    unique_tiers = list(dict.fromkeys(tiers))
    candidates: list = []
    seen = set()

    def add(sku: str) -> None:
        if sku not in seen:
            seen.add(sku)
            candidates.append(sku)

    for tier in unique_tiers:
        if include_variants:
            add(build_sku(tier, "", VARIANT_SUFFIX))
        for period in normalized_periods:
            for suffix in suffixes:
                add(build_sku(tier, period, suffix))
    return candidates[:limit] if limit is not None else candidates


@dataclass
class OfferRow:
    """offer イベントから抽出した、出力可能な項目だけを持つ行。"""

    sku: str
    offer_id: str
    period: str
    micros: Optional[int]
    title: str


def _coerce_micros(value: object) -> Optional[int]:
    """整数または数値文字列だけを int にする。その他は「不明」として None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _MICROS_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def parse_offer_events(events: Iterable[object]) -> list:
    """offer イベントだけを OfferRow に変換する（Raw / Frida envelope 両対応）。"""
    rows: list = []
    for event in events:
        payload = event
        if isinstance(event, dict) and isinstance(event.get("payload"), dict):
            payload = event["payload"]
        if not isinstance(payload, dict) or payload.get("ev") != "offer":
            continue
        sku = payload.get("product")
        offer_id = payload.get("id")
        if not isinstance(sku, str) or not sku or not isinstance(offer_id, str) or not offer_id:
            continue
        rows.append(
            OfferRow(
                sku=sku,
                offer_id=offer_id,
                period=str(payload.get("period") or ""),
                micros=_coerce_micros(payload.get("micros")),
                title=str(payload.get("title") or payload.get("name") or ""),
            )
        )
    return rows


def dedupe_rows(rows: Iterable[OfferRow]) -> list:
    """同一 SKU + offerId の行は最初のものだけ残す。"""
    seen = set()
    unique: list = []
    for row in rows:
        key = (row.sku, row.offer_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def period_months(period: str) -> int:
    """P1M / P1Y を月数にする。未知の表記は末尾に回すため大きい値を返す。"""
    if isinstance(period, str):
        match = _MONTH_RE.fullmatch(period)
        if match is not None:
            return int(match.group(1))
        match = _YEAR_RE.fullmatch(period)
        if match is not None:
            return 12 * int(match.group(1))
    return _UNKNOWN_PERIOD_MONTHS


def sort_rows(rows: Iterable[OfferRow]) -> list:
    """SKU → 期間 → offerId の順に安定ソートする。"""
    return sorted(
        rows,
        key=lambda row: (row.sku, period_months(row.period), row.offer_id or ""),
    )


def _table_footer() -> list:
    return [
        "注: micros=0 は初回（最初の請求段階）の価格を示すだけであり、"
        "トライアル適格性や更新価格を証明するものではありません。",
        "注: 本結果は読み取り専用スキャンの参考値です。実際の請求額は Play の定期購入画面で確認してください。",
    ]


def format_table(rows: Iterable[OfferRow], limit: int = 20) -> list:
    """人が読む表（ヘッダ + 行 + 注意書き）を返す。"""
    ordered = sort_rows(rows)
    lines = [
        "SKU | offerId | 期間 | micros | タイトル",
        "--- | --- | --- | ---: | ---",
    ]
    if not ordered:
        lines.append("該当する offer はありませんでした（読み取り専用スキャン）。")
        lines.extend(_table_footer())
        return lines

    shown = ordered[:limit] if limit is not None else ordered
    for row in shown:
        micros = "不明" if row.micros is None else f"{row.micros:,}"
        lines.append(
            f"{row.sku} | {row.offer_id} | {row.period or '-'} | "
            f"{micros} | {row.title or '-'}"
        )
    if limit is not None and len(ordered) > limit:
        # --limit は「候補数」の上限で、表の行数とは別物。混同させないため
        # フラグ名には触れず、省略件数だけを伝える。
        lines.append(
            f"… 他 {len(ordered) - limit} 件は表示していません（先頭 {limit} 件のみ表示）。"
        )
    lines.extend(_table_footer())
    return lines


def to_json(rows: Iterable[OfferRow], indent: int = 2) -> str:
    """許可リストのみの JSON レポートを返す（日本語はエスケープしない）。"""
    payload = [
        {field: getattr(row, field) for field in ROW_FIELDS}
        for row in sort_rows(rows)
    ]
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def parse_report(payload: str) -> list:
    """保存済み JSON レポートを検証して OfferRow の列表に戻す。"""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("レポートは UTF-8 テキストである必要があります") from exc
    if not isinstance(payload, str):
        raise ValueError("レポートは JSON 文字列である必要があります")
    try:
        data = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"レポートの JSON を解析できません: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("レポートは OfferRow の配列である必要があります")

    allowed = set(ROW_FIELDS)
    rows: list = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"レポート[{index}] はオブジェクトである必要があります")
        if set(item) != allowed:
            raise ValueError(
                f"レポート[{index}] の項目が不正です: {sorted(set(item) ^ allowed)}"
            )
        if not all(isinstance(item[key], str) for key in ("sku", "offer_id", "period")):
            raise ValueError(f"レポート[{index}] の sku / offer_id / period は文字列である必要があります")
        micros = item["micros"]
        if micros is not None and (isinstance(micros, bool) or not isinstance(micros, int)):
            raise ValueError(f"レポート[{index}] の micros は整数または null である必要があります")
        title = item["title"]
        if title is not None and not isinstance(title, str):
            raise ValueError(f"レポート[{index}] の title は文字列または null である必要があります")
        rows.append(
            OfferRow(
                sku=item["sku"],
                offer_id=item["offer_id"],
                period=item["period"],
                micros=micros,
                title=title,
            )
        )
    return rows
