# Ledger

A personal finance desktop app for Windows. Import bank statements, see where the money
went, and work out what a savings goal needs each month.

Everything stays on your computer. One SQLite file, no account, no sign-in, no cloud.

## What it does

- **Imports CSV, Excel and PDF statements** — several files at once, one per account.
- **Never double-counts.** Every line is fingerprinted with SHA-256; re-importing an
  overlapping statement skips what is already there.
- **Cancels internal transfers.** £500 leaving current account and arriving on a credit
  card is one movement, not £500 earned and £500 spent — even when the two halves arrive
  in separate imports.
- **Shifting billing cycles.** Bills on the 25th? Anchor the cycle to day 25 and everything
  follows. Day 31 clamps safely through February and April.
- **Learning categories.** Change a category once and apply it to that line, that exact
  description, or a keyword — the rule then applies to future imports.
- **Goals and forecasts.** Required monthly rate, plus what happens if you cut flagged
  spending by 50% or 100%.
- **Analysis without the cloud.** Five deterministic checks (spikes, repeating charges,
  possible double charges, movers, goal pace) run in plain Python. A local model turns them
  into sentences. Only open-ended questions go to Gemini, and only after you agree.

## Install

Download `Ledger-1.0.0-x64.msi` from [Releases](../../releases) and run it.

- Installs **per user** to `%LOCALAPPDATA%\Programs\Ledger` — no administrator rights and no
  UAC prompt.
- Adds a Start Menu entry and a Desktop shortcut.
- Your data lives beside the executable (`budget_data.db`), with timestamped copies in
  `.backups\` written on every exit. Uninstalling leaves your data in place.

Windows 10 or 11, 64-bit.

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
  --collect-all customtkinter --collect-all matplotlib ^
  --collect-data pdfplumber --collect-data pdfminer --collect-data openpyxl ^
  --hidden-import websocket --hidden-import PIL._tkinter_finder ^
  --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module IPython --exclude-module pytest --exclude-module tkinter.test ^
  ledger.py
```

Then build the installer with [WiX](https://wixtoolset.org) 5:

```
wix build installer.wxs -arch x64 -o Ledger-1.0.0-x64.msi
```

## Tests

```
python check_ledger.py    # 246 checks: calendar maths, parsers, de-duplication, transfers
python check_ui.py        #  43 checks: opens the window and visits every screen
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
