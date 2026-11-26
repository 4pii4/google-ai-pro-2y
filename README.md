# google-ai-pro-2y

A set of scripts to **obtain the free trial offer for Google AI Pro (5TB) via Play billing** and stack 6-month offers to extend it **up to 2 years (or 30 months with the reserve)**.

Play's `BillingClient` only returns the "free trial offer" **through in-app billing**, so this injects a billing module into the Google One process using Frida, enumerates offers, and opens the checkout screen only when the price is 0 yen to complete registration.

- The original script (`scripts/ultra0.py`) assumes **Android 12/13 emulator + frida 16.5.9 + Windows**
- This repository adds `scripts/ultra0-17.py` (which also works on **rooted Android 14/15/16 devices + frida 17**) and its Frida agent (`agent/`)

## What this is

A Japanese toolkit for investigating and stacking free trials for Google AI Pro (5TB) via Play billing on a real Android device. It consists of three layers:

1. Read-only scanner (`scripts/sku_scanner*.py` / `./scripts/run.sh scan ...`).
   Only reads which SKUs / offers exist on the device. Does not register or charge anything.
2. Combo planning & execution (`scripts/combo*.py` / `./scripts/run.sh combo plan|run`).
   Determines the account state and builds a stacking plan. Defaults to dry-run; actual execution requires `--execute --yes`. Does not auto-tap "Register"; payment is done by a human.
3. Original fixed procedure (`scripts/ultra0-17.py` + `./scripts/run.sh step1` to `step4`).
   Assumes an active subscription already exists and stacks in order: 12 months → +6 months → +6 months → reserve.

## Disclaimer, Warnings & Security

- This repository is unofficial. Actual charges may occur, what `micros=0` means, and the scope of risks are summarized in [Disclaimer & Warnings (DISCLAIMER.md)](DISCLAIMER.md). Please read it carefully before running.
- Handling of purchase tokens / account information, pre-execution checklist, and vulnerability reporting methods are summarized in [Security (SECURITY.md)](SECURITY.md).
- Rights and origins are described in [Rights & Origins (NOTICE.md)](NOTICE.md). Derived code cannot be licensed; newly written parts of this repository are under CC0 1.0.

## Measured Results (2026-11-19 / Pixel 7a / Android 16 / KernelSU)

| Step | Applied offer | Expiration |
|---|---|---|
| Initial | Student plan `g1.play.2tb.ai.student` (¥720/month) | 2026-12-18 |
| step1 | `12months-eft` (12 months free) | 2027-11-19 |
| step2 | `6months-eft` (+6 months) | 2028-05-19 (546 days) |
| step3 | `6months-eft-2` (+6 months) | **2028-11-19 (730 days = 2 years)** |
| step4 | `6months-eft-ss-2026` (reserve +6 months) | Immediate error on Play side (not possible on this account) |

Confirmed on the Play subscription screen: `Active / Google AI Pro (5 TB) / Next payment: ¥2,900 (2028/11/19)`.

## Result Screenshots (original author's posts)

Records of stacking up to 24 months with the same procedure (posts by the original author. Account names and emails are redacted).

- <https://x.com/lvz3s/status/2123112734460170354>
- <https://x.com/lvz3s/status/2123109098443121001>

![Play subscription screen (next payment)](docs/images/result-subscription.jpg)

![Result summary](docs/images/result-summary.jpg)

![Session log](docs/images/result-session-log.jpg)

## Two Execution Paths

| | Path A (legacy / frida 16) | Path B (additions in this repo / frida 17) |
|---|---|---|
| Device | Android 12/13 emulator (MuMu etc.) | Rooted real device running Android 14/15/16 |
| Host | Windows (.bat double-click) | Linux / macOS / Windows |
| frida | 16.5.9 (Java bridge bundled) | 17.22.2 (Java bridge bundled in build) |
| Script | `scripts/ultra0.py` | `scripts/ultra0-17.py` |
| Launcher | `windows/frida16/*.bat` | `./scripts/run.sh` or `windows/frida17/*.bat` |

Android 14+ **rejects writable dex files**, **frida 16 hangs on spawn**, and **frida 17 does not bundle the Java bridge**. Path B includes workarounds for these (details: [docs/ANDROID16-NOTES.md](docs/ANDROID16-NOTES.md)).

## Quick Start (Linux / macOS + Android 14+ real device)

Prerequisites: Python 3.9+, node/npm (optional — use the bundled agent if you don't build), adb, rooted device with USB debugging allowed, logged into Play with a payment method registered.

```bash
./scripts/setup.sh               # venv + frida 17.22.2 + agent build + adb
./scripts/run.sh check           # self-check of device / root / frida / billing module
./scripts/run.sh list            # list offers (no charging · zero risk)

# 1) Read SKUs / offers only (no registration or charging)
./scripts/run.sh scan generate                 # generate candidate SKUs to query (no device needed)
./scripts/run.sh scan query --candidates       # read offers from the real device
./scripts/run.sh scan summarize --input ultra0-report/sku-scan-YYYYMMDD-HHMMSS.json

# 2) Combo plan based on state (dry-run by default)
./scripts/run.sh combo plan --device           # show plan only (do not execute)
./scripts/run.sh combo run --device --account you@example.com --execute --yes
                                               # execute one step at a time (payment done by human)

# 3) Tests (no device or frida needed)
./scripts/run.sh test
```

If you only want to **read** SKUs / offers, use `scan` (it does not register or charge).
Descriptions of `scan` subcommands and options such as `--candidates` / `--skus` / `--timeout` are in [docs/SKU-SCANNER.md](docs/SKU-SCANNER.md).
`combo` judgment logic and notes are in [docs/COMBO.md](docs/COMBO.md).

If `list` shows the following, the prerequisites are OK (numbers are examples):

```
offer 12months-eft → 0 (P1Y)
offer 6months-eft → 0 (P6M)
offer 6months-eft-2 → 0 (P6M)
offer 6months-eft-ss-2026 → 0 (P6M)
offer null → 2900000000 (P1M)     # regular price (¥2,900/month)
```

Then execute in order:

```bash
./scripts/run.sh step1      # 12-month free trial
./scripts/run.sh step2      # +6 months (stack via subscription upgrade)
./scripts/run.sh step3      # +6 months
./scripts/run.sh step4      # reserve +6 months (may error)
```

After each step, confirm on Play's "Subscriptions" screen that **the next payment date has been pushed back**.

### Combo Planning (judgment based on account state)

`step1`–`step4` are a fixed procedure that assumes "an active subscription already exists".
For new accounts or expired ones the order changes, so use `combo` which reads the state and plans accordingly.

```bash
./scripts/run.sh combo plan --device                     # show plan only (do not execute)
./scripts/run.sh combo run --device --execute --yes      # execute one step at a time
```

- `fresh` (no subscription): start from the longest single free offer `18months_eft` (+18 months)
- `active` (active): determine whether stacking is possible and, if so, only stack free offers of `subs_variant`
- `expired` (expired): start from `18months_eft` as a new registration

Payment confirmation is always done by a human. The script only opens the billing screen and waits;
it does not auto-tap "Register" or call `confirm_zero()`.
Execution requires both `--execute` and `--yes`, and it will not touch the device unless `--device` is specified.
See [docs/COMBO.md](docs/COMBO.md) for details and notes.

### Windows / MuMu (Path A)

```bat
pip install frida==16.5.9
python scripts\setup_env_frida16.py
windows\frida16\0_環境自検.bat
windows\frida16\1_offer一覧.bat
windows\frida16\2_Pro_12个月_第1步.bat
...
```

### Windows / Real device Android 14+ (Path B)

```bat
:: 1. setup
.venv\Scripts\python.exe -m pip install frida==17.22.2
cd agent && npm install && cd ..
.venv\Scripts\python.exe scripts\apply_bridge_patch.py
.venv\Scripts\python.exe scripts\build_agent.py
:: 2. execute
windows\frida17\0_環境自検.bat
```

## Repository Structure

```
scripts/
  setup.sh              Environment setup for Linux/macOS
  run.sh                Launcher for check / list / scan / test / step1-4
  sku_scanner.py        SKU scanner CLI (read-only)
  sku_scanner_core.py   Pure logic for SKU candidate generation & offer formatting
  combo.py              Combo planning CLI (dry-run by default · payment done by human)
  combo_core.py         Pure logic for state judgment & stacking plan
  build_agent.py        Build agent/iife bundle
  apply_bridge_patch.py Android 16 patch for frida-java-bridge
  fix_frida.sh          Resolve frida-server version mismatch
  ultra0.py             Original script (unmodified)
  ultra0-17.py          Android 14+ / frida 17 compatible version
  setup_env_frida16.py  Environment setup from original zip (frida 16.5.9)
  query_sku.py          SKU check tool from original zip
agent/
  injected.js           Original FRIDA_JS with registerClass patch applied
  agent.js              Auto-generated (bridge import + injected.js)
  agent.iife.js         Pre-built bundle (included)
  package.json          frida-java-bridge 7.1.0
windows/
  frida16/              Path A .bat files
  frida17/              Path B .bat files
docs/
  SETUP.md              Detailed setup
  README.md             docs index (reading order)
  SKU-SCANNER.md        How to use the SKU scanner (read-only)
  COMBO.md              How to use combo planning & notes
  ANDROID16-NOTES.md    Why modifications were needed (technical notes)
  TROUBLESHOOTING.md    Error-specific remedies
  RESULTS.md            Measured logs
tests/
  test_sku_*.py         Unit tests for scanner (no device/frida needed)
  test_combo_*.py       Unit tests for combo planning & execution (no device/frida needed)
```

## Tests

```bash
./scripts/run.sh test        # python3 -m unittest discover -s tests -v
```

- Runs with Python's standard `unittest` only. No additional dependencies.
- No device or frida needed. Can be run even on a PC without adb.
- Currently 118 tests; all passing shows `OK`.
- `tests/` is split into scanner (`test_sku_*.py`) and combo (`test_combo_*.py`).

## Common Pitfalls (details in docs/TROUBLESHOOTING.md)

| Symptom | Cause | Remedy |
|---|---|---|
| `unable to communicate with remote frida-server` | Major version mismatch between client/server | `./scripts/run.sh fix-frida` |
| `malformed package` | Agent was built in `esm` format | Rebuild with `bundle_format="iife"` |
| `'Java' is not defined` | frida 17 does not bundle Java bridge | Use the bundle in `agent/` |
| Nothing happens after injection (no response) | `Java.registerClass` hangs on Android 16 | `skipInterfaceExpansion:true` in `injected.js` |
| `Writable dex file ... is not allowed` | Android 14+ dex restrictions | `chmod 444` on `module_521.apk` |
| `app_not_started` | Fixed 7-second wait for app start | `ultra0-17.py` polls for 20 seconds |
| Cannot get checkout screen text | Device is in Japanese locale | ja/en/zh + `¥` support already included (`ultra0-17.py`) |

## Notes

- **Automatic charging starts from the trial end date** (in this case, ¥2,900/month from 2028-11-19).
  Remember to cancel on the Play subscription screen (usable until the end date even after cancellation).
- Stacking requires the subscription to be active (auto-renew on). Canceling beforehand prevents stacking.
- Play may temporarily return errors if the same operation is repeated in succession. Wait and retry.
- On real devices with KernelSU + LSPosed + Android 16, long-running Frida has occasionally caused boot loops.
  In that case, long-press the power button to reboot (subscription state remains on the server side).
- Use at your own risk.
