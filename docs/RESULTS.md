# 実測ログ（2026-11-19）

> アカウント名・端末識別子などは伏せています（`example@gmail.com` は例示）。

環境:

| | |
|---|---|
| ホスト | Arch Linux / x86_64 |
| 端末 | Pixel 7a (`lynx`) / Android 16 / KernelSU |
| Frida | client 17.22.2 / server 17.22.2 |
| 課金経路 | Google One アプリ + `module_521.apk`（BillingClient 5.x 系） |
| アカウント | `example@gmail.com`（例。日本リージョン・JPY 請求） |

## 開始時の状態

- Play サブスク: **学生プラン `g1.play.2tb.ai.student`** 有効（￥720/月、次回 2026-12-18）
- `--list --plan pro12` で Pro 5TB の offer が 4 種見えている:

```
offer 12months-eft        → 0 micros, P1Y
offer 6months-eft         → 0 micros, P6M
offer 6months-eft-2       → 0 micros, P6M
offer 6months-eft-ss-2026 → 0 micros, P6M
offer null                → 2,900,000,000 micros, P1M   （通常価格 ¥2,900）
```

## 実行結果

| 日時 | 操作 | offer | `subs_valid_until_time` |
|---|---|---|---|
| 2026-11-19 02:34 | step1（`--plan pro12`） | `12months-eft` | 2027-11-19 |
| 2026-11-19 02:38 | step2（`--stack --plan pro6`） | `6months-eft` | 2028-05-19（546 日） |
| 2026-11-19 02:41 | step3（`--stack --plan pro6b`） | `6months-eft-2` | **2028-11-19（730 日）** |
| 2026-11-19 02:45〜 | step4（`--stack --plan pro6c`） | `6months-eft-ss-2026` | 変化なし（Play が即エラー） |

購入ごとに `purchaseToken` が更新される（`library.db` の `ownership` テーブル）。

> `purchaseToken` は購入情報に紐づく機密値のため、この文書には記載しません。
> 手元で確認する場合は Play の `library.db` を直接参照してください（取り扱いに注意）。

## 最終状態

`library.db`:

```
subs:com.google.android.apps.subscriptions.red:g1.2tb.ai.subs_variant
  valid_until: 2028-11-19
  残り日数: 730
```

Play の定期購入画面:

```
有効
Google One
Google AI Pro (5 TB)
次回のお支払い: ￥2,900（2028/11/19）
```

再起動後も同じ状態であることを確認済み（サーバー側で保持）。

## 2026-11-25 追加調査: Ultra 系 tier と割引オファーの有無

iOS 向けプロジェクト（`crhywun/g1-acq`）が扱っていた SKU 名を取り込み、候補を
132 件から 287 件に増やして再スキャンした。

| 項目 | 値 |
|---|---|
| 候補 | 7 tier × 41（1 variant + 8 期間 × 5 suffix）= 287 件 |
| tier | `2tb.ai`, `play.2tb.ai`, `5tb.ai`, `ai.pro`, `20tb`, `play.20tb`, `ai.ultra` |
| suffix | `_eft`, `_ip_90p`, `_ip_90p.acq`, `_ip_95p.acq`, `_ip_75p.acq` |
| 実行時間 | 36.6 秒（バッチ 20 件・タイムアウト 20 秒、タイムアウト 0 件） |

結果は、`g1.2tb.ai` 系の 8 SKU だけが出現し、
Ultra 系（`20tb` / `play.20tb` / `ai.ultra`）と割引系（`_ip_90p` / `_ip_95p.acq` /
`_ip_75p.acq`）は 0 件だった。

照会経路そのものが死んでいないことは、次の 2 点で確認した。

- 対照実験: 同じ実行で正常な `g1.2tb.ai.18months_eft` を指定すると
  `eft-18months`（P1Y6M・0 micros）が返った
- 個別確認: `g1.30tb`（文法に乗らない裸の SKU）、`g1.20tb`、`g1.ai.ultra` を
  直接指定しても 0 件だった

このアカウント（日本リージョン）では Pro 5TB の `g1.2tb.ai` だけが提供されており、
Ultra 系と割引オファーは Play から返らない。

## 2026-11-25 追記: g1-acq v7.6.0 の Ultra 1ヶ月無料トライアル検証

iOS 側プロジェクトが v7.6.0（2026-11-24）で Ultra の対象を
3ヶ月 90%オフから **1ヶ月無料トライアル**へ変更した。新しい SKU は
`g1.20tb.1month_eft.acq` と `g1.ai.ultra.1month_eft.acq` で、
`suffix` に `_eft.acq` を足して候補を 343 件に増やし、実機で確認した。

| 確認した SKU | 結果 |
|---|---|
| `g1.20tb.1month_eft.acq` | 0 件 |
| `g1.ai.ultra.1month_eft.acq` | 0 件 |
| `g1.20tb.1month_eft` | 0 件 |
| `g1.ai.ultra.1month_eft` | 0 件 |
| `g1.20tb.3months_ip_90p.acq` | 0 件 |
| `g1.ai.ultra.3months_ip_90p.acq` | 0 件 |

全体スキャン（343 件・46 秒・タイムアウト 0 件）でも、出現したのは
`g1.2tb.ai` 系の 8 SKU だけだった。同じ実行で `g1.2tb.ai.18months_eft` は
`eft-18months` を返しているため、照会経路は正常である。

つまり、このアカウント（日本リージョン）の Play には Ultra 系 SKU が存在しない。
g1-acq 側でも issue #2（Apple の收銀台で "Unable to Complete Purchase"）と
issue #1（Google アプリが原価のまま）が未解決のままで、購入成功の報告は確認できていない。

## 観測された注意点

- Frida 注入を繰り返すと Pixel 7a がブートループに入ることがあった
  （電源長押しで復帰、サブスクは無事）
- USB 接続が数回ドロップ（ハブ経由）。Wi-Fi ADB 併用で安定
- 4 回目（`-ss-2026`）だけ Play がシートを出さず「エラーが発生しました」ダイアログを表示

## スクリーンショット（本人ポスト）

- <https://x.com/lvz3s/status/2123112734460170354>
- <https://x.com/lvz3s/status/2123109098443121001>

![Play の定期購入画面](../images/result-subscription.jpg)

![結果サマリ](../images/result-summary.jpg)
