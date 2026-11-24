# docs インデックス

`docs/` にある文書の一覧です。詳細は各ファイルを開いてください。

| ファイル | 内容 |
|---|---|
| [SETUP.md](SETUP.md) | 環境構築と実行順序（PC 側・端末側・frida の準備） |
| [SKU-SCANNER.md](SKU-SCANNER.md) | SKU スキャナ（読み取り専用）の使い方と出力の読み方 |
| [COMBO.md](COMBO.md) | コンボ計画・実行の使い方、判定ロジック、注意点 |
| [ANDROID16-NOTES.md](ANDROID16-NOTES.md) | Android 16 / frida 17 で必要だった改造の技術メモ |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 実際に踏んだエラーと、症状別の対処 |
| [RESULTS.md](RESULTS.md) | 実測ログ（どのオファーで期限がどう伸びたか） |

## 読む順番

初めて触る場合は、次の順で読むと迷いません。

1. [SETUP.md](SETUP.md) から読みます。まず環境を作ります。ここが通らないと先に進めません。
2. 状態確認です。[SKU-SCANNER.md](SKU-SCANNER.md) でどんな offer があるか読み、
   [COMBO.md](COMBO.md) の `combo plan --device` で自分のアカウントの状態と計画を確認します。
   ここまでは読み取りだけで、課金は起きません。
3. 実行です。計画に納得してから [COMBO.md](COMBO.md) の `combo run`、または
   `step1`〜`step4` を実行します。金額と期間は端末の画面で自分で確認してください。

うまく動かないときは [TROUBLESHOOTING.md](TROUBLESHOOTING.md)、
なぜ改造が必要だったかは [ANDROID16-NOTES.md](ANDROID16-NOTES.md) を参照してください。

実行前に [../DISCLAIMER.md](../DISCLAIMER.md) と [../SECURITY.md](../SECURITY.md) も必ず目を通してください。
