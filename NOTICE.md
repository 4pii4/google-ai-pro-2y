# NOTICE — 権利と由来について

このリポジトリの権利関係は、コードの由来によって 2 つに分かれます。
以下のとおりで、ライセンスを付与する立場にない部分があります。

## 1. 由来するコード（ライセンスを付与できません）

`scripts/ultra0.py`、`scripts/ultra0-17.py`、`agent/`、`scripts/query_sku.py`、
`scripts/setup_env_frida16.py`、`windows/` 配下は、第三者が公開した実装を引き継いだものです。

- 元の実装には**ライセンス表記がありません**（SPDX 識別子・LICENSE ファイルともに無し）。
- そのため、このリポジトリの保守者は**これらのコードについて著作権を主張せず、
  利用許諾も付与しません**。保守者には許諾する権限がないためです。
- 利用する場合は、元の実装の条件と、適用される法令・利用規約を**利用者自身で確認**してください。

## 2. このリポジトリで新たに書いたコード（CC0 1.0）

以下はこのリポジトリで新規に書いたもので、**CC0 1.0 Universal（パブリックドメイン）**
として提供します。権利を主張しません。

- `scripts/sku_scanner.py`、`scripts/sku_scanner_core.py`
- `scripts/combo.py`、`scripts/combo_core.py`
- `scripts/build_agent.py`、`scripts/apply_bridge_patch.py`
- `scripts/run.sh`、`scripts/setup.sh`、`scripts/fix_frida.sh`（新規に書き起こした部分）
- `tests/` 配下のすべて
- `README.md`、`DISCLAIMER.md`、`SECURITY.md`、`NOTICE.md`、`docs/` 配下の文書

CC0 1.0 の全文: <https://creativecommons.org/publicdomain/zero/1.0/legalcode>

## 3. 免責

いずれの部分も**無保証**です。利用によって生じた損害について、保守者は責任を負いません。
詳しくは [DISCLAIMER.md](DISCLAIMER.md) を参照してください。

## 4. 削除の依頼

由来するコードについて権利上の問題がある場合は、GitHub の Issue で連絡してください。
確認のうえ、該当部分の削除または差し替えを行います。
