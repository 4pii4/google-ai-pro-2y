# google-ai-pro-2y

Google AI Pro (5TB) の **無料トライアル offer を Play 課金経由で取得**し、
6ヶ月オファーを重ねて **最長 2 年（+予備で 30 ヶ月）** まで延ばすためのスクリプト一式です。

Play の `BillingClient` は「無料トライアル offer」を **アプリ内課金経由でしか返さない** ため、
Google One のプロセスに Frida で課金モジュールを差し込み、offer を列挙 → 0 円のときだけ
チェックアウト画面を開いて登録します。

- 元スクリプト（`scripts/ultra0.py`）は **Android 12/13 のエミュレータ + frida 16.5.9 + Windows** 前提
- このリポジトリは **Android 14/15/16 の実機（root）+ frida 17** でも動くようにした `scripts/ultra0-17.py` と
  その Frida エージェント（`agent/`）を追加したものです

## これは何か

Google AI Pro (5TB) の無料トライアルを、Android 実機の Play 課金経由で調べて積み上げる、
日本語のツールキットです。中身は 3 層に分かれています。

1. 読み取り専用のスキャナ（`scripts/sku_scanner*.py` / `./scripts/run.sh scan ...`）。
   端末にどんな SKU / offer があるかを読むだけで、登録も課金もしません。
2. コンボ計画・実行（`scripts/combo*.py` / `./scripts/run.sh combo plan|run`）。
   アカウントの状態を判定して積み上げ計画を立てます。既定はドライランで、実行には
   `--execute --yes` が必要です。「登録」の自動タップはせず、支払いは人が行います。
3. 元の固定手順（`scripts/ultra0-17.py` + `./scripts/run.sh step1`〜`step4`）。
   すでに有効なサブスクがある前提で、12ヶ月 → +6ヶ月 → +6ヶ月 → 予備、と順に重ねます。

## 免責・注意とセキュリティ

- 本リポジトリは非公式です。実際の課金が発生しうること、`micros=0` が何を意味するか、
  リスクの範囲は [免責・注意（DISCLAIMER.md）](DISCLAIMER.md) にまとめています。実行前に必ず目を通してください。
- 購入トークン・アカウント情報の扱い、実行前の確認事項、脆弱性の報告方法は
  [セキュリティ（SECURITY.md）](SECURITY.md) にまとめています。
- 権利関係は [権利と由来（NOTICE.md）](NOTICE.md) を参照してください。由来するコードには
  ライセンスを付与できず、このリポジトリで新たに書いた部分は CC0 1.0 としています。

## 実測結果（2026-11-19 / Pixel 7a / Android 16 / KernelSU）

| 手順 | 適用した offer | 有効期限 |
|---|---|---|
| 開始時 | 学生プラン `g1.play.2tb.ai.student`（￥720/月） | 2026-12-18 |
| step1 | `12months-eft`（12ヶ月無料） | 2027-11-19 |
| step2 | `6months-eft`（+6ヶ月） | 2028-05-19（546日） |
| step3 | `6months-eft-2`（+6ヶ月） | **2028-11-19（730日 = 2年）** |
| step4 | `6months-eft-ss-2026`（予備 +6ヶ月） | Play 側が即エラー（このアカウントでは不可） |

Play のサブスク画面で `有効 / Google AI Pro (5 TB) / 次回のお支払い: ￥2,900（2028/11/19）` を確認済み。

## 実績スクリーンショット（本人ポスト）

同じ手順で 24 ヶ月まで積めたときの記録です（投稿者本人のポスト。アカウント名・メールは伏せています）。

- <https://x.com/lvz3s/status/2123112734460170354>
- <https://x.com/lvz3s/status/2123109098443121001>

![Play の定期購入画面（次回のお支払い）](docs/images/result-subscription.jpg)

![結果サマリ](docs/images/result-summary.jpg)

![作業ログ](docs/images/result-session-log.jpg)

## 2 つの実行経路

| | 経路 A（legacy / frida 16） | 経路 B（このリポジトリの追加分 / frida 17） |
|---|---|---|
| 端末 | Android 12/13 エミュレータ（MuMu 等） | Android 14/15/16 の実機（root） |
| ホスト | Windows（.bat ダブルクリック） | Linux / macOS / Windows |
| frida | 16.5.9（Java bridge 同梱） | 17.22.2（Java bridge を同梱ビルド） |
| スクリプト | `scripts/ultra0.py` | `scripts/ultra0-17.py` |
| ランチャ | `windows/frida16/*.bat` | `./scripts/run.sh` または `windows/frida17/*.bat` |

Android 14 以降は **書き込み可能な dex を拒否**する、**frida 16 が spawn で固まる**、
**frida 17 は Java bridge を同梱しない** などの差があるため、経路 B では後述の工夫を入れています
（詳細: [docs/ANDROID16-NOTES.md](docs/ANDROID16-NOTES.md)）。

## クイックスタート（Linux / macOS + Android 14+ 実機）

前提: Python 3.9+、node/npm（任意・ビルドしないなら同梱バンドルを使用）、adb、
root 済み端末で USB デバッグ許可済み、Play にログイン＋支払い方法登録済み。

```bash
./scripts/setup.sh               # venv + frida 17.22.2 + agent ビルド + adb
./scripts/run.sh check           # 端末 / root / frida / 課金モジュールの自検
./scripts/run.sh list            # offer 一覧（課金しない・ゼロリスク）

# 1) SKU / offer を読むだけ（登録・課金しない）
./scripts/run.sh scan generate                 # 照会対象の SKU 候補を生成する（端末不要）
./scripts/run.sh scan query --candidates       # 実機から offer を読み取る
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-YYYYMMDD-HHMMSS.json

# 2) 状態に応じたコンボ計画（既定はドライラン）
./scripts/run.sh combo plan --device           # 計画だけ表示（実行しない）
./scripts/run.sh combo run --device --account you@example.com --execute --yes
                                               # 1 ステップずつ実行（支払いは人が行う）

# 3) テスト（端末も frida も不要）
./scripts/run.sh test
```

SKU / offer を**読むだけ**で調べたい場合は `scan` を使ってください（登録・課金は行いません）。
`scan` の各サブコマンドと `--candidates` / `--skus` / `--timeout` の説明は
[docs/SKU-SCANNER.md](docs/SKU-SCANNER.md) にあります。
`combo` の判定ロジックと注意点は [docs/COMBO.md](docs/COMBO.md) にあります。

`list` で以下が出れば前提 OK です（数値は例）。

```
offer 12months-eft → 0 (P1Y)
offer 6months-eft → 0 (P6M)
offer 6months-eft-2 → 0 (P6M)
offer 6months-eft-ss-2026 → 0 (P6M)
offer null → 2900000000 (P1M)     # 通常価格（￥2,900/月）
```

そのまま順番に実行します。

```bash
./scripts/run.sh step1      # 12ヶ月無料トライアル
./scripts/run.sh step2      # +6ヶ月（subscription upgrade で重ねる）
./scripts/run.sh step3      # +6ヶ月
./scripts/run.sh step4      # 予備 +6ヶ月（エラーになる場合あり）
```

各ステップ後に Play の「定期購入」画面で **次回のお支払い日が後ろにずれている**ことを確認してください。

### コンボ計画（アカウント状態に応じた判定）

`step1`〜`step4` は「すでに有効なサブスクがある」前提の固定手順です。
新規アカウントや期限切れの場合は順番が変わるため、状態を読んでから計画する
`combo` を使ってください。

```bash
./scripts/run.sh combo plan --device                     # 計画だけ表示（実行しない）
./scripts/run.sh combo run --device --execute --yes      # 1 ステップずつ実行
```

- `fresh`（サブスクなし）: 最長の単体無料オファー `18months_eft`（+18ヶ月）から開始
- `active`（有効）: 積み上げ可否を判定し、可能なら `subs_variant` の無料オファーだけを積む
- `expired`（期限切れ）: 新規登録として `18months_eft` から開始

支払いの確定は必ず人が行います。スクリプトは課金画面を開いて待つだけで、
「登録」の自動タップや `confirm_zero()` の呼び出しはしません。
実行は `--execute` と `--yes` の両方が必要で、`--device` を付けない限り端末に触れません。
詳細と注意点は [docs/COMBO.md](docs/COMBO.md) を参照してください。

### Windows / MuMu（経路 A）

```bat
pip install frida==16.5.9
python scripts\setup_env_frida16.py
windows\frida16\0_環境自検.bat
windows\frida16\1_offer一覧.bat
windows\frida16\2_Pro_12个月_第1步.bat
...
```

### Windows / 実機 Android 14+（経路 B）

```bat
:: 1. setup
.venv\Scripts\python.exe -m pip install frida==17.22.2
cd agent && npm install && cd ..
.venv\Scripts\python.exe scripts\apply_bridge_patch.py
.venv\Scripts\python.exe scripts\build_agent.py
:: 2. 実行
windows\frida17\0_環境自検.bat
```

## リポジトリ構成

```
scripts/
  setup.sh              Linux/macOS の環境構築
  run.sh                check / list / scan / test / step1-4 のランチャ
  sku_scanner.py        SKU スキャナ CLI（読み取り専用）
  sku_scanner_core.py   SKU 候補生成・offer 整形の純粋ロジック
  combo.py              コンボ計画 CLI（既定ドライラン・支払いは人が行う）
  combo_core.py         状態判定・積み上げプランの純粋ロジック
  build_agent.py        agent/iife バンドルのビルド
  apply_bridge_patch.py frida-java-bridge への Android 16 パッチ
  fix_frida.sh          frida-server のバージョン不一致を解消
  ultra0.py             元スクリプト（無改変）
  ultra0-17.py          Android 14+ / frida 17 対応版
  setup_env_frida16.py  元 zip の環境セットアップ（frida 16.5.9）
  query_sku.py          元 zip の SKU 確認ツール
agent/
  injected.js           元 FRIDA_JS に registerClass パッチを入れたもの
  agent.js              自動生成（bridge import + injected.js）
  agent.iife.js         ビルド済みバンドル（同梱）
  package.json          frida-java-bridge 7.1.0
windows/
  frida16/              経路 A の .bat
  frida17/              経路 B の .bat
docs/
  SETUP.md              詳細セットアップ
  README.md             docs のインデックス（読む順番）
  SKU-SCANNER.md        SKU スキャナの使い方（読み取り専用）
  COMBO.md              コンボ計画の使い方と注意点
  ANDROID16-NOTES.md    なぜ改造が必要だったか（技術メモ）
  TROUBLESHOOTING.md    エラー別の対処
  RESULTS.md            実測ログ
tests/
  test_sku_*.py         スキャナの単体テスト（端末・frida 不要）
  test_combo_*.py       コンボ計画・実行の単体テスト（端末・frida 不要）
```

## テスト

```bash
./scripts/run.sh test        # python3 -m unittest discover -s tests -v
```

- Python 標準の `unittest` だけで動きます。追加の依存はありません。
- 端末も frida も不要です。adb が無い PC でもそのまま実行できます。
- 現在 118 個のテストがあり、すべて通ると `OK` と表示されます。
- `tests/` はスキャナ（`test_sku_*.py`）とコンボ（`test_combo_*.py`）に分かれています。

## 主なつまずきどころ（詳細は docs/TROUBLESHOOTING.md）

| 症状 | 原因 | 対処 |
|---|---|---|
| `unable to communicate with remote frida-server` | client/server のメジャー不一致 | `./scripts/run.sh fix-frida` |
| `malformed package` | エージェントを `esm` 形式でビルドした | `bundle_format="iife"` で再ビルド |
| `'Java' is not defined` | frida 17 は Java bridge 非同梱 | `agent/` のバンドルを使う |
| 注入後に何も起きない（無反応） | Android 16 で `Java.registerClass` がハング | `injected.js` の `skipInterfaceExpansion:true` |
| `Writable dex file ... is not allowed` | Android 14+ の dex 制約 | `module_521.apk` を `chmod 444` |
| `app_not_started` | アプリ起動待ち 7 秒固定 | `ultra0-17.py` は 20 秒ポーリング |
| チェックアウトの文言が取れない | 端末が日本語ロケール | ja/en/zh + `¥` 対応済み（`ultra0-17.py`） |

## 注意

- **トライアル終了日から自動課金**されます（今回なら 2028-11-19 から ￥2,900/月）。
  忘れないよう Play のサブスク画面で解約してください（解約後も期限までは利用可）。
- 重ね掛けは「サブスクが有効（自動更新オン）」なことが条件です。先に解約すると重ねられません。
- Play は同じ操作を連続すると一時的にエラーを返すことがあります。時間を置いて再試行してください。
- KernelSU + LSPosed + Android 16 の実機では、Frida を長時間動かすとまれにブートループに入ることがありました。
  その場合は電源長押しで再起動してください（サブスク状態はサーバー側に残ります）。
- 自己責任でご利用ください。
