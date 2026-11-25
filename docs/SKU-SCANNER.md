# SKU スキャナ（読み取り専用）

`scripts/sku_scanner.py` は、Google One のサブスク SKU と offer（価格・期間）を
読むだけのツールです。`./scripts/run.sh scan ...` から呼び出します。

## 目的

`step1`〜`step4` は実際にトライアルを登録する流れですが、`scan` は
「どの SKU にどの offer が存在するか」を先に調べるためのものです。
候補 SKU の生成、実機からの offer 取得、保存済みレポートの整形ができます。

## 前提（実機照会する場合）

`query` は「すでに動いている Google One」に Frida で attach するだけのツールです。
アプリの起動は行いません。そのため、次の 4 つがすべて揃っていないと
`端末に接続できませんでした` などのエラーになります。

1. root 済み端末で USB デバッグを許可し、`adb devices` に 1 台だけ表示される
2. 端末側で frida-server が起動している（バージョン不一致は `./scripts/run.sh fix-frida`）
3. 端末側で Google One（`com.google.android.apps.subscriptions.red`）が起動している
   （ホーム画面から一度開いておく）
4. `./scripts/run.sh check` で自検が通る（端末 / root / frida / 課金モジュールを確認）

`generate` と `summarize` は端末も frida も不要です。

## 読み取り専用の境界

スキャナは以下の操作を一切行いません。

- チェックアウト画面の起動・購入・登録
- アプリの起動・再起動（`launch_app()` の呼び出しなし）
- トライアルの適用・判定・重ね掛け
- トークンやアカウント情報の出力・保存

実機接続時は、すでに動いている Google One のプロセスにだけ Frida で attach し、
エージェントを `list_only` モード（`build_js(plan="5x", list_only=True)`）で読み込みます。
課金フローに進むコードパスは使いません。さらに、注入する直前に次の 2 点を検証し、
どちらかが欠けていれば注入せずに中止します（fail-closed）。

- 読み取り専用フラグ `var LIST_ONLY=true;` がちょうど 1 つある
- 対象 SKU を差し替えるための `PRODUCTS` リテラルがちょうど 1 つある

万一 `LIST_ONLY` が効いていなくても、`launched` / `stack_start` / `activity` /
`purchase_cb` のいずれかを受信した時点でスキャンを中断します。

## 使い方

### 候補 SKU を生成する（端末不要）

```bash
./scripts/run.sh scan generate --tiers 2tb.ai --periods 18months,12months --suffixes _eft --format table
```

`--format json` なら SKU の配列だけが stdout に出ます（日本語の注記は stderr）。

### 実機から offer を読み取る

`query` は照会対象を明示する必要があります。`--skus` か `--candidates` の
どちらかを必ず指定してください（どちらも無い場合は、端末に接続する前にエラー終了します）。

```bash
# 既定の候補をまとめて照会（バッチ 20 件・タイムアウト 8 秒）
./scripts/run.sh scan query --candidates --batch-size 20 --timeout 8

# SKU を直接指定して JSON で表示
./scripts/run.sh scan query --skus g1.2tb.ai.subs_variant --format json
```

- `--skus` と `--candidates` は同時に指定できません。
- `--timeout` は 1〜30 秒（既定 8）。範囲外は端末に接続する前にエラーになります。
- `--batch-size` は 1 以上（既定 20）。0 はエラーになります。
- `--limit` は候補数の上限で、表の行数とは関係ありません。既定の候補は 343 件で、
  `--limit 120` のように絞ると ai.pro の長期間 SKU が気づかないうちに候補から外れます。
  全件を照会するなら `--limit` を省略してください。

結果は `ultra0-report/sku-scan-<タイムスタンプ>.json` に保存されます
（`--output` で保存先を変更可。権限は 0600）。保存先パスは stderr に表示されます
（stdout は整形結果だけなので、パイプで他のコマンドに渡せます）。

バッチがタイムアウトした場合は、結果はそのまま保存しつつ stderr に警告
（`タイムアウト`）を出します。1 バッチも完了しなかった場合だけ終了コードが非ゼロになります。
タイムアウトが続くときは端末の状態を確認し、`--timeout` を延ばすか
`./scripts/run.sh check` で自検してください。

### 保存済みレポートを整形する

```bash
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-20261124-130000.json
```

`--format json` で JSON のまま再出力できます。

### テスト

```bash
./scripts/run.sh test
# または
python3 -m unittest discover -s tests -v
```

## Ultra 系 tier と iOS 由来の知識

既定の候補には Ultra 系 tier（`20tb`、`play.20tb`、`ai.ultra`）と
サフィックス `_eft.acq`、`_ip_75p.acq` が含まれます。いずれも iOS プロジェクト
[g1-acq](https://github.com/crhywun/g1-acq) が扱う product id を手がかりに加えたもので、
既定の候補は 7 tier ×（1 variant + 8 期間 × 6 サフィックス）= 343 件になります。

- `_ip_90p` と `_ip_75p` は **90%オフ / 75%オフの有料割引**であり、無料トライアルではありません。
- `_eft.acq` は無料トライアルに `.acq` が付いた形です。g1-acq の v7.6.0 が
  Ultra 向けに使う `g1.20tb.1month_eft.acq` と `g1.ai.ultra.1month_eft.acq` がこれに当たります。
- `.acq` は獲得向けプロモ SKU の目印です（`_ip_90p.acq` のように期間の後ろに付きます）。
- `g1.30tb` は `g1.<tier>.<期間><サフィックス>` の文法に乗らない裸の SKU です。候補生成には
  含まれないため、`scan query --skus g1.30tb` のように明示指定します。

Ultra 系 tier と `g1.30tb` が実機で提供されているかは未検証です。候補に含まれることと、
実際に offer が返ることは別に確かめる必要があります。

### 表示データの書き換えと課金額

g1-acq は `one.google.com` の応答を MITM し、本文中の `g1.*` の product id 文字列を
正規表現で置換します。JSON の解析は行わず、価格・トライアル・適格性の項目には触れません。

Android 側で購入を決めるのは Play Billing（`queryProductDetailsAsync` → offerToken）です。
`one.google.com` の表示データを書き換えても課金額は変わらないため、表示の差し替えを
割引やトライアルの適用として扱わないでください。スキャナはどちらの経路も操作せず、
offer の内容を読むだけです。

## 終了コード

| コード | 意味 |
| ---: | --- |
| 0 | 成功（一部のバッチがタイムアウトしても、1 バッチでも完了していれば 0） |
| 1 | 実行エラー（端末に接続できない、読み取り専用の検証に失敗、1 バッチも完了しなかった など） |
| 2 | 引数の誤り（`--skus` / `--candidates` の未指定、`--timeout` / `--batch-size` が範囲外） |

エラーは日本語 1 行で stderr に出ます。トレースバックは表示しません。

## トークンとプライバシー

- offer イベントに含まれる `token` / `offerToken` / `purchaseToken` / `secret` /
  `email` / `account` は解析時に破棄し、`sku` / `offer_id` / `period` /
  `micros` / `title` の 5 項目だけを保持します。
- レポートにも標準出力にもトークンは書き出されません。画面共有や貼り付けの
  前に、念のため内容を確認してください。
- レポートは `ultra0-report/`（`.gitignore` に登録済み）に 0600 で保存されます。

## 注意事項

- **`micros=0` は初回（最初の請求段階）の価格を示すだけです**。トライアルの
  適格性や更新価格を証明するものではありません。実際の請求額・次回のお支払い日は
  Play の「定期購入」画面で確認してください。
- 端末への負荷に注意してください。長時間の連続スキャンや、同じ端末での
  繰り返し実行は避けてください（Frida を長時間動かすと不安定になることがあります）。
- スキャンの結果は参考値です。登録操作は `step1`〜`step4` を明示的に実行した
  場合にだけ行われます。
