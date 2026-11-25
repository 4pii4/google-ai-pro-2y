# iOS での Google One / AI Pro オファー調査メモ（2026-11-25）

Android 側（`scripts/ultra0-17.py` など）とは別に、iOS の Google アプリで
何ができるかを試した記録。確定した事実と未検証を分けて書く。

## 結論

- iOS の Google アプリは `one.google.com/ai` の HTML（約 1.2 MB）に SKU 一覧を持つ。実測で 11 件取得した。
- その一覧に**無料トライアル（`_eft`）は 1 件も無い**。すべて `_ip_`（割引）。
- ただしアカウント個別に Ultra 1ヶ月無料が付与されることはある。実験中に実際に取得できた。
- SKU 文字列の書き換え（`g1-acq` 方式）でオファーを作れるかは**まだ検証できていない**。
  今回の成功は書き換えを使っていない。

## やったこと（時系列）

1. mitmproxy を Tailscale 経由で立て、iPhone の HTTPS を復号した。
   証明書ピニングは Google 系ホストには無く、復号できた。
2. 観測のみのモードで `one.google.com/ai` から SKU 一覧を取得（11 件）。
3. `g1-acq` v7.6.0 と同じ書き換えマッピングを実装して有効化した。
4. 購入を試すと失敗した。原因は Apple の決済エンドポイント
   （`bag.itunes.apple.com`、`gateway.icloud.com`）がピニングで
   MITM を拒否していたこと。プロキシ経由では購入自体が不可能だった。
5. Apple 系ドメインを MITM 対象外（`--ignore-hosts`）にした。
6. 購入が通り、**Ultra 1ヶ月無料**を取得できた。

## いちばん大事な切り分け

**書き換えは一度も実行されていない。**

- 書き換えは `one.google.com/ai` の取得時にしか動かない
- 再起動後、`/ai` は WebView のキャッシュから読まれ、再取得されなかった
- ログの `REWRITE` 行は 0 件

したがって「Ultra 1ヶ月無料」は、**アカウントに元から用意されていたオファー**が、
購入経路の修復によって普通に通ったもの。`g1-acq` の手法が効いた証拠にはならない。

## iOS で観測した SKU（実測）

| SKU | 期間 | 内容 |
|---|---|---|
| `g1.ios.ai.2tb.m.ip_1mo_90p.acq` | 月払い | 初月 90% オフ |
| `g1.ios.ai.2tb.a.ip_1yr_10p.acq` | 年払い | 1 年 10% オフ |
| `g1.ios.helium.m.1month_ip_90p.acq` | 月払い | 初月 90% オフ |
| `g1.ios.helium.a.ip_1yr_10p.acq` | 年払い | 1 年 10% オフ |
| `g1.ios.2tb.m.3month_ip_75p` | 月払い | 3 ヶ月 75% オフ |
| `g1.ios.100gb.m.3month_ip_75p` | 月払い | 3 ヶ月 75% オフ |
| `g1.ios.2tb.a.12month_ip_15p` | 年払い | 12 ヶ月 15% オフ |
| `g1.ios.100gb.a.12month_ip_15p` | 年払い | 12 ヶ月 15% オフ |
| `g1.ios.20tb.m.v2` | 月払い | Ultra 20TB の入口 |
| `g1.ios.30tb.ai.pro.m` | 月払い | Ultra 30TB の入口 |
| `com.google.one.generic` | — | 汎用 |

命名は `g1.ios.<tier>.<m|a>.<期間>_ip_<割引率>p[.acq]`。

- `m` / `a` は月払い / 年払い
- `ip_90p` は 90% オフ。`15p` なら 15% オフ
- `.acq` は獲得向けプロモの目印
- `helium`、`ai.2tb`、`100gb` は Android 側のスキャン対象に無い tier

## Android 側との違い

| | iOS | Android |
|---|---|---|
| 接頭 | `g1.ios.` | `g1.` |
| tier の語順 | `ai.2tb`（容量が後） | `2tb.ai`（容量が先） |
| 無料トライアル | カタログには無い（個別付与はあり） | `_eft` が存在（例 `g1.2tb.ai.18months_eft`） |

iOS の ID は Android の ID とそのまま対応しない。名前が似ていても別の商品。

## `g1-acq` v7.6.0 の主張

<https://github.com/crhywun/g1-acq>

モジュール説明文:

```
v7.6.0：支持 Ultra 1个月免费试用，20TB g1.20tb.1month_eft.acq、
        30TB g1.ai.ultra.1month_eft.acq，各档独立回退三个月90p备用
```

| 元 | 変換先 |
|---|---|
| `g1.ios.2tb.` で始まる ID | `g1.2tb.ai.12months_ip_90p` |
| `g1.ios.20tb.m.v2` | `g1.20tb.1month_eft.acq` |
| `g1.ios.30tb.ai.pro.m` | `g1.ai.ultra.1month_eft.acq` |

同じ README に「試用が現在のアカウントに表示されるかは App Store の記録次第」と
いう留保が書かれている。

### 未解決の報告

- issue #2（open）: 書き換え自体は成功するが、Apple の收銀台で
  "Unable to Complete Purchase" になる。モジュールを切ると通常購入は成功する
- issue #1（open）: レポートが空で、Google アプリは原価のまま

書き換えで購入まで通ったという報告は、確認した範囲では存在しない。

## 再現に使ったもの

`~/Documents/repos/g1-mitm-observe/`（このリポジトリの外）

- `addon.py` … mitmproxy アドオン。観測と、任意の SKU 書き換え
- `g1_observe.py` … SKU 抽出・書き換え・集計の純粋ロジック
- `report.py` … 観測レポートの集計 CLI
- `docs/ios-sku-naming.md` … 命名規則の詳細
- テスト 47 件（`python3 -m unittest discover -s tests`）

起動例:

```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 -s addon.py \
  --set g1_report=./g1-observe.json \
  --set block_global=false \
  --set g1_hosts='*.google.com,*.googleapis.com' \
  --ignore-hosts '^(.*\.)?apple\.com:443|^(.*\.)?icloud\.com:443'
```

`--ignore-hosts` が無いと Apple の決済が失敗する。ここが今回いちばんの要点。

書き換えを試すときは `--set g1_rewrite=true` と `g1_map` / `g1_prefix` を足す。
ただし `/ai` が再取得されないと動かないため、事前に WebView のキャッシュを
消す必要がある（未検証）。

## 未検証・注意

- 書き換えで Ultra 無料を「作れる」かは未検証。今回の取得は書き換え無し
- `g1.20tb.1month_eft.acq` は実測カタログに存在しない ID。書き換えても
  存在しない商品を要求することになる可能性が高い
- iOS のトライアルはアカウント個別付与なので、同じ手順で誰でも取れるとは限らない
- 無料トライアルは期限後に自動更新される。解約期限の確認を忘れないこと
- プロキシを立てている間は端末の全通信が経由する。不要になったら止める
