# Ledger

A personal finance desktop app for Windows. Import bank statements, see where the money
went, and work out what a savings goal needs each month.

Everything stays on your computer. One SQLite file, no account, no sign-in, no cloud.

## What it does

- **Imports CSV, Excel and PDF statements** — several files at once, one per account.
- **Reads debit and credit columns correctly** - money out is negative, money in is
  positive, whatever the bank calls the columns.
- **Never double-counts.** Every line is fingerprinted with SHA-256; re-importing an
  overlapping statement skips what is already there. Genuinely repeated payments are kept.
- **Cancels internal transfers.** £500 leaving current account and arriving on a credit
  card is one movement, not £500 earned and £500 spent — even when the two halves arrive
  in separate imports.
- **Shifting billing cycles.** Bills on the 25th? Anchor the cycle to day 25 and everything
  follows. Day 31 clamps safely through February and April. An **All time** switch shows
  your full history.
- **Learning categories.** Change a category once and apply it to that line, that exact
  description, or a keyword - the rule then applies to future imports. Create a new category
  from the same window.
- **Ignore anything that isn't yours to count.** Ignored transactions leave every figure and
  stay in the list.
- **Work per account.** Rename an account, and narrow every screen to one account from the
  header selector once you have more than one.
- **Undo an import.** Settings lists every imported statement; removing one deletes exactly
  the rows it brought in.
- **Goals and forecasts.** Required monthly rate, plus what happens if you cut flagged
  spending by 50% or 100%.
- **Analysis without the cloud.** Five deterministic checks (spikes, repeating charges,
  possible double charges, movers, goal pace) run in plain Python. A local model turns them
  into sentences. Only open-ended questions go to Gemini, and only after you agree.

## Install

Download `Ledger-1.1.2-x64.msi` from [Releases](../../releases) and run it.

- Installs **per user** to `%LOCALAPPDATA%\Programs\Ledger` — no administrator rights and no
  UAC prompt.
- Adds a Start Menu entry and a Desktop shortcut.
- Your data lives beside the executable (`budget_data.db`), with timestamped copies in
  `.backups\` written on every exit. Uninstalling leaves your data in place.

Windows 10 or 11, 64-bit.

### macOS

Two builds, because the two kinds of Mac cannot run each other's binaries:

| Your Mac | Download |
|---|---|
| **Apple Silicon** — M1, M2, M3, M4 | `Ledger-1.1.2-arm64.dmg` |
| **Intel** — Core i5, i7, i9, Xeon | `Ledger-1.1.2-x86_64.dmg` |

Not sure which you have? **Apple menu → About This Mac**. Look at **Chip** (says "Apple M…"
→ Apple Silicon) or **Processor** (says "Intel Core…" → Intel).

Open the DMG, drag **Ledger** to Applications, then **right-click the app → Open** the first
time — neither build is signed, and macOS blocks unsigned apps on a normal double-click.

Your data lives in `~/Library/Application Support/Ledger`, outside the app bundle, so
replacing the app on upgrade never touches it.

## Optional extras

| Adds | Needs |
|---|---|
| Local AI summaries | [Ollama](https://ollama.com) with `gemma3:4b`, or Chrome's built-in Gemini Nano |
| Deeper analysis | A Gemini API key, using `gemini-3.5-flash-lite` |

Both are optional. The dashboard, imports, categories and forecasts work with neither.
See [OLLAMA-SETUP.md](OLLAMA-SETUP.md) and section 9 of [MANUAL.md](MANUAL.md).

## Build from source

```
pip install customtkinter matplotlib pdfplumber openpyxl requests websocket-client pyinstaller
pyinstaller --noconfirm --clean --windowed --onedir --name Ledger ^
  --icon "assets\ledger.ico" --add-data "assets\ledger-256.png;assets" ^
  --collect-all customtkinter --collect-all matplotlib ^
  --collect-data pdfplumber --collect-data pdfminer --collect-data openpyxl ^
  --hidden-import websocket --hidden-import PIL._tkinter_finder ^
  --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module IPython --exclude-module pytest --exclude-module tkinter.test ^
  ledger.py
```

Then build the installer with [WiX](https://wixtoolset.org) 5:

```
python tools/make_payload.py
$v = python tools/version.py
wix build installer.wxs payload.wxs -arch x64 -d Version=$v -o "Ledger-$v-x64.msi"
```

`make_payload.py` reads the PyInstaller output and generates the directory tree for
`payload.wxs`. `version.py` prints `APP_VERSION` from `ledger.py`, so the installer and the
app can never disagree. Both must be re-run after every PyInstaller build.

## Tests

```
python check_ledger.py    # 287 checks: calendar maths, parsers, de-duplication, transfers
python check_ui.py        #  50 checks: opens the window, visits every screen
```

`ledger.py --smoke` opens every screen, closes, and writes `smoke_report.txt` next to the
executable. Useful for confirming a frozen build.

## Privacy

- Transactions never leave the machine.
- The Gemini key is encrypted with Windows DPAPI, bound to your user account.
- Cloud analysis is off until you allow it, and the app shows exactly what would be sent
  before the first request.
- Nothing is sent when the local model can answer.

## Licence

MIT — see [LICENSE](LICENSE).

Bundled third-party components keep their own licences: CustomTkinter (MIT), matplotlib
(PSF-based), pdfplumber (MIT), openpyxl (MIT), requests (Apache-2.0), websocket-client
(Apache-2.0), Pillow (HPND).
