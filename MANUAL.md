# Ledger — user manual

A personal finance desktop app that keeps everything on your own computer.

No account, no sign-in, no cloud. One file holds your data, and a copy is written every
time you close the app.

---

## Contents

1. [What it does](#1-what-it-does)
2. [Getting started](#2-getting-started)
3. [The five screens](#3-the-five-screens)
4. [Importing statements](#4-importing-statements)
5. [Fixing a wrong category](#5-fixing-a-wrong-category)
6. [Marking potential savings](#6-marking-potential-savings)
7. [Goals and the forecast](#7-goals-and-the-forecast)
8. [Insights: local model, cloud model, and what gets sent](#8-insights-local-model-cloud-model-and-what-gets-sent)
9. [Installing Ollama (start here if you have never done it)](#9-installing-ollama-start-here-if-you-have-never-done-it)
10. [Chrome's built-in model (optional)](#10-chromes-built-in-model-optional)
11. [Billing cycles and short months](#11-billing-cycles-and-short-months)
12. [Backups and restoring](#12-backups-and-restoring)
13. [Building the .exe yourself](#13-building-the-exe-yourself)
14. [Troubleshooting](#14-troubleshooting)
15. [Privacy](#15-privacy)
16. [What it does not do](#16-what-it-does-not-do)

---

## 1. What it does

You give it bank statements (CSV, Excel or PDF). It turns them into one clean list of
payments, works out where the money went, and shows you what a goal needs each month.

Three things it is built to do well:

- **Import the same statement twice without doubling your spending.** Every line is
  fingerprinted; a repeat is skipped.
- **Spot transfers that are not really spending.** Move £500 from current account to
  credit card and the two lines cancel out instead of showing as £500 earned and £500
  spent.
- **Answer questions without sending your data anywhere**, until you say otherwise.

---

## 2. Getting started

**If you downloaded the installer (`Ledger-1.0.0-x64.msi`):** double-click it, then
**Install**. It installs for your Windows account only — no administrator password — and
adds a Desktop shortcut and a Start Menu entry. The app and your data live in
`%LOCALAPPDATA%\Programs\Ledger`. Uninstalling leaves your data in place.

**If you were given the `Ledger` folder:** double-click `Ledger.exe` inside it. Keep the
`_internal` folder beside it.

**If you are running the Python file:** `python ledger.py`

On first launch the app creates two things next to itself:

| Name | What it is |
|---|---|
| `budget_data.db` | All of your data, in one file |
| `.backups/` | A timestamped copy, written every time you close the app |
| `.nano-profile/` | Only appears if you use Chrome's local model |

**Keep those together.** Copy all of them if you move the app to another computer. Do not
put the app in Program Files — it needs to write next to itself.

The first screen is empty by design. Press **Import statement** to begin.

---

## 3. The five screens

**Dashboard** — four figures for the cycle (income, spending, net, potential savings), a
six-cycle income-versus-spending chart, where the money went by category, and your five
largest payments.

**Transactions** — every line in the cycle. Click a category to change it. Click the
potential-saving column to flag a payment. Filter with the row of buttons or search by
description or category.

**Plan** — your target, the months you have, and what that requires each month. The chart
shows three futures: doing nothing, cutting flagged spending in half, cutting it entirely.

**Insights** — three findings worked out on your computer, plus a written review. See
[section 8](#8-insights-local-model-cloud-model-and-what-gets-sent).

**Settings** — billing cycle, categories, rules, API key, local model, data and backups.

The arrows either side of the date range at the top move you between cycles. Everything on
screen follows that range.

---

## 4. Importing statements

Press **Import statement**. You can select **several files at once** — hold Ctrl and click,
or drag a box around them.

**Do this:** import all the accounts for one month together — current account, credit card,
savings. That is what lets the app match transfers between them.

### What happens next

A review window opens. Nothing is saved until you press the button at the bottom.

```
┌ Review import ──────────────────────── 3 files · 214 lines ┐
│  barclays-oct.csv   [Barclays    ]   amex-oct.csv  [Amex  ] │
│  savings-oct.csv    [Savings     ]                          │
│  Dates were read day-first (31/12/2026 is 31 December).     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ DATE       DESCRIPTION      CATEGORY      AMOUNT  STATUS│ │
│  │ 01 Oct 26  TESCO STORES     Groceries    -£45.20  New   │ │
│  │ 02 Oct 26  PAYMENT RECEIVED Transfers    +£500.00 Transfer│ │
│  └────────────────────────────────────────────────────────┘ │
│  120 new · 8 already imported · 2 internal transfers · 3 files │
└─────────────────────────────────────────────────────────────┘
```

- **Account** — one per file. It defaults to the file name. It only matters for matching
  transfers, but the names are worth keeping sensible.
- **Duplicate** — a line with the same date, amount and description is already in the
  database. It will be skipped, not imported twice.
- **Transfer** — this line pairs with an equal and opposite amount in a *different account*
  within two days. Both halves are excluded from income and spending.
- **Category** — filled in from the rule list. Anything unrecognised is `Uncategorised`.

Change an account name and the preview recalculates, because transfer matching depends on
it.

### Which file types work

| Type | Notes |
|---|---|
| **CSV** | Best. Most banks can export it. Any of `,` `;` tab or `\|` separators. |
| **Excel (.xlsx)** | Good. Old `.xls` must be re-saved as `.xlsx` first. |
| **PDF** | Works when the PDF has a text layer. Scanned PDFs cannot be read. Slower and less reliable than CSV — check the preview carefully. |

The app finds the date, description and amount columns by their headings, including
`Posted Date`, `Narrative`, `Money Out` and similar. If there are no headings at all it
works them out from the shape of the data.

**Two things to check in the preview:**

1. **Signs.** If the statement shows spending as a positive number with no debit/credit
   column, the PDF reader assumes money out. Fix it by exporting CSV instead.
2. **Dates.** It reads `03/04/2026` as 3 April unless the file proves otherwise. The
   preview will say which way it read them.

---

## 5. Fixing a wrong category

Click the category cell in the transaction list. A dropdown appears in place. Choose the
right category, and a window asks how far to apply it:

| Option | What it does |
|---|---|
| **Only this transaction** | Changes that one line. No rule is saved. |
| **Every transaction with this exact description** | Changes all past and future lines with the identical description, and saves a rule. |
| **Every transaction containing a keyword** | Changes anything containing a keyword you choose, and saves a rule. |

The third one is the useful one. A line reading `TESCO STORES 3421 LONDON` becomes
`Tesco` — every Tesco payment, past and future, is then categorised. The box under the
option shows how many lines would change before you commit.

Rules you create take priority over the built-in list. To see or remove them, go to
**Settings → Categorisation rules → Open rule list**.

---

## 6. Marking potential savings

On the Transactions screen, the last column is **Potential saving**. Click a row to flag it,
click again to unflag.

This is how you tell the app "this is not essential". Flagged payments:

- colour the row,
- add up into the Potential savings figure on the dashboard,
- drive the two "what if" lines on the Plan screen,
- and are listed for the model when you ask for analysis.

Good candidates: takeaways, streaming services you forget about, the gym you never visit.

---

## 7. Goals and the forecast

On the **Plan** screen:

| Field | Meaning |
|---|---|
| Target amount | What you are saving for |
| Months to save | Your deadline |
| Already saved | What you have put aside so far |

Press **Recalculate**. You get:

- **Required each month** — target minus what you have, divided by the months.
- **Net this cycle** — what you actually kept after spending.
- **Shortfall** — the gap, if there is one.
- **Months at current rate** — when you would get there at this pace.
- **Months after a 50% / 100% cut** — the same, assuming you cut flagged spending.

The chart draws all three at once against the target line. If your net is zero or negative
it says *not at this rate* rather than inventing a date.

You can ask **Insights** the same question in words.

---

## 8. Insights: local model, cloud model, and what gets sent

Open **Insights**. Three things happen there, cheapest first.

### Tier 0 — worked out on your computer, always

The card at the top is plain arithmetic, no AI. It looks for:

- **Category spikes** — a category up 25% or more over its average.
- **Possible double charges** — the same amount to the same merchant within three days.
- **Repeating charges** — the same amount in every recent cycle (subscription creep).
- **Big movers** — the categories that changed most since the previous cycle.
- **Your goal** — whether your current rate reaches the target.

These are exact figures from your own data. They appear instantly, offline, with no setup.

### Tier 1 — a local model

Press **Answer** with a short question (or none) and it stays on your machine. Two possible
engines, checked in this order:

1. **Ollama** — if it is running and has a model installed. See [section 9](#9-installing-ollama-start-here-if-you-have-never-done-it).
2. **Chrome's built-in model (Gemini Nano)** — if it is already downloaded. See [section 10](#10-chromes-built-in-model-optional).

You can pin one or the other in **Settings → Local model**.

The local model is given the figures the app already worked out and asked to write three
or four sentences. It is not asked to do arithmetic — that is the app's job, and it is why
the local answers are trustworthy even from a small model.

### Tier 2 — Gemini 3.5 Flash-Lite

Anything open-ended goes here: "why is my spending up?", "should I cancel this?",
comparisons across months, or a question longer than a few words.

**It asks first.** Before the first cloud request you see exactly what would be sent:

> Income and spending totals for the cycle, 9 category figures, 12 flagged payments, and
> six cycles of totals. No account numbers and no transaction list beyond that.

Your answer is remembered: **Just this once**, **Always allow**, or **Cancel**. Change your
mind any time with the switch in **Settings → Gemini API key → Allow a summary to be sent
to Gemini**. Turn it off and nothing ever leaves the machine — the app says so instead of
quietly sending.

The label above the answer always tells you where it came from: *Local · Ollama gemma3:4b*,
*Local · Gemini Nano*, or *Cloud · gemini-3.5-flash-lite*.

### Getting a Gemini API key

Only needed if you want the cloud tier.

1. Go to `aistudio.google.com/app/apikey` and sign in with a Google account.
2. Press **Create API key** and copy it (it starts with `AIza`).
3. In Ledger: **Settings → Gemini API key**, paste, **Save**.

The key is encrypted with your Windows account before it is written to the database, so a
copy of the database on another machine cannot read it.

Gemini 3.5 Flash-Lite is not free, but it is cheap: about 30 US cents per million input
tokens and $2.50 per million output tokens. One analysis is a fraction of a penny. Google's
free tier may cover normal use — check your quota.

---

## 9. Installing Ollama (start here if you have never done it)

> A standalone, printable version of this section is in **`OLLAMA-SETUP.md`**, with a
> step-by-step checklist at the end.

Ollama is a small program that runs an AI model on your own computer. It downloads once,
then works without internet.

**Time:** about 15 minutes, most of it downloading.
**Space:** about 3 GB for the model.
**Memory:** about 4 GB free while it is answering.

### Step 1 — Download

Open `ollama.com/download` in a browser. Click **Download for Windows**.

You get a file called `OllamaSetup.exe`.

### Step 2 — Install

Double-click `OllamaSetup.exe`. Press **Install**. It installs for your user only, so it
does not need an administrator password.

When it finishes, Ollama is already running — look for its icon in the notification area
(the ^ arrow at the bottom-right of the taskbar, next to the clock). You do not need to
start it every time; it starts with Windows.

### Step 3 — Open a terminal

Press the **Windows key**, type `powershell`, and press **Enter**. A dark blue window opens.

This is where you type commands. Type the command, then press **Enter**.

### Step 4 — Download the model

Type this and press Enter:

```
ollama pull gemma3:4b
```

You will see a progress bar. It downloads about 3 GB. Leave the window open until it
finishes and you get your prompt back.

### Step 5 — Check it worked

Type:

```
ollama list
```

You should see a line with `gemma3:4b` in it.

You can also try a one-off conversation:

```
ollama run gemma3:4b
```

Type a question, press Enter, and it answers. Type `/bye` and press Enter to leave.

### Step 6 — Tell Ledger to use it

Open Ledger → **Settings** → **Local model**.

1. Set **Use** to **Automatic** (or **Ollama** to force it).
2. Press **Check**.
3. It should say **Ready: Ollama gemma3:4b**.

Now go to **Insights** and press **Answer**. The label above the review will say
`Local · Ollama gemma3:4b`, and the network is not touched.

### If you have a bigger computer

`gemma3:4b` is the sweet spot for a laptop. If you have 16 GB of memory or more and want
better answers:

```
ollama pull qwen3:8b
```

Ledger prefers the smaller model when both are installed. To use the bigger one, remove
the small one with `ollama rm gemma3:4b`.

### If something goes wrong

| Message | Fix |
|---|---|
| "Ollama is not running" | Open the Start menu, type `Ollama`, and launch it. Give it 10 seconds, then press Check again. |
| "Ollama is running but has no model installed" | You have not done Step 4. Run `ollama pull gemma3:4b`. |
| Windows asks about a firewall | Allow it on private networks. Ollama only listens on your own machine. |
| `ollama` is not recognised | Close the terminal and open a new one — the installer updates the path, and old windows do not see it. |
| Answers are very slow | Normal on a laptop without a graphics card. A 4B model is the practical limit; try shorter questions. |
| Answers repeat themselves or ramble | Try `qwen3:4b` instead. `ollama pull qwen3:4b` then `ollama rm gemma3:4b`. |

### Removing it later

```
ollama rm gemma3:4b
```

Then uninstall **Ollama** from Windows Settings → Apps. That frees the space.

---

## 10. Chrome's built-in model (optional)

Newer versions of Google Chrome include a small model called Gemini Nano. Ledger can use it
through Chrome, which means no extra download beyond the model itself.

**This is optional.** Ollama is more predictable. Use Nano only if you would rather not
install anything else.

### Setting it up

1. **Settings → Local model → Download Chrome model.**
2. The first time, Chrome downloads a few gigabytes. It can take several minutes and shows
   little visible progress — leave the app open.
3. Press **Check** afterwards. It should say **Ready: Gemini Nano inside Chrome**.

Chrome runs invisibly in the background while the app is open. It is closed when you exit
Ledger.

### Good to know

- Nano only works on a **secure origin**, so the page Chrome uses is loaded from
  `127.0.0.1`. This is why it needs a browser at all.
- Chrome updates can change how the model is reached. If it stops working, the app falls
  back to Ollama or the cloud, and you will see that in the label.
- **On this machine it reported `downloadable`**, meaning Chrome supports it and the model
  simply has not been downloaded yet.

If both are set up, Automatic prefers Ollama — it is faster to start and does not need a
browser running.

---

## 11. Billing cycles and short months

Most statements do not run from the 1st to the end of the month. If your card bills on the
25th, set that in **Settings → Billing cycle → Start on day: 25**.

With day 25, the cycle shown as "25 Sep – 24 Oct" covers exactly those dates. Every figure,
chart and table follows it.

### Short months

If you pick day 31, some months have no 31st. The app clamps to the last real day rather
than skipping anything:

| Cycle | Covers |
|---|---|
| 31 January | 31 Jan – 27 Feb |
| 28 February (2026) | 28 Feb – 30 Mar |
| 31 March | 31 Mar – 29 Apr |
| 30 April | 30 Apr – 30 May |

No gaps, no overlaps, no lost transactions. Day 1 always means the plain calendar month.

The app tells you the range above the transaction list, and **Settings** shows it too.

---

## 12. Backups and restoring

Every time you close the window, a copy of the database is written to `.backups/` with a
timestamp:

```
.backups/budget_data_2026-10-27_143022.db
```

The newest 30 are kept and older ones are removed automatically.

To make one on demand: **Settings → Data → Back up now**.
To see them: **Settings → Data → Open backup folder**.

### Restoring

1. Close Ledger.
2. Open `.backups/` and find the copy you want.
3. Copy it to the folder holding `Ledger.exe`.
4. Rename that copy to `budget_data.db`, replacing the current file.
5. Start Ledger.

**If you copy your data to another computer**, take the whole folder — `budget_data.db`
plus `.backups/`. Your Gemini key will not carry over; it is encrypted to your Windows
account, so paste it again on the new machine.

---

## 13. Building the .exe yourself

Only needed if you are starting from `ledger.py`.

```
pyinstaller --noconfirm --clean --windowed --onedir --name Ledger ^
  --collect-all customtkinter --collect-all matplotlib ^
  --collect-data pdfplumber --collect-data pdfminer --collect-data openpyxl ^
  --hidden-import websocket --hidden-import PIL._tkinter_finder ^
  --exclude-module PyQt5 --exclude-module PySide6 ^
  --exclude-module IPython --exclude-module pytest --exclude-module tkinter.test ^
  ledger.py
```

Install the tools first:

```
pip install customtkinter matplotlib pdfplumber openpyxl requests websocket-client pyinstaller
```

The finished program is `dist\Ledger\Ledger.exe`. The whole `dist\Ledger` folder is the app
— copy the folder, not just the .exe.

**A note on `--onedir`:** the app starts in about a second. If you switch to `--onefile` you
get a single file, but Windows unpacks several hundred megabytes to a temporary folder on
every launch, so it takes 5–10 seconds to open.

### Checking a build

Two self-tests ship with the source:

```
python check_ledger.py    # 246 checks: calendar maths, parsers, de-duplication, transfers
python check_ui.py        #  43 checks: opens the window, visits every screen
```

The app also accepts `--smoke`, which opens every screen, closes, and writes
`smoke_report.txt` next to the executable. Useful to confirm a build is not missing a
library.

---

## 14. Troubleshooting

| Problem | Cause and fix |
|---|---|
| **"No transaction rows were found"** | The file has no date or amount the app recognises. Open it in Excel and check the first few rows. |
| **Every row says Duplicate** | You have imported this file before. That is the intended behaviour. |
| **The same payment appears twice** | Genuinely two identical payments would look like a duplicate. Check the statement. |
| **A transfer was not matched** | Both halves must be in *different* accounts, within two days. Check the account names in the import window. |
| **Amounts are all positive** | The statement has no debit/credit column. Export CSV from your bank instead of PDF. |
| **A rule keeps overriding the right category** | A rule is too broad. Settings → Categorisation rules → remove it. |
| **The window opens partly off-screen** | It sizes itself to the monitor. Resize once and Windows remembers it. |
| **Import is slow on a PDF** | Normal for large PDFs. CSV is far quicker. |
| **"Chrome was not found"** | Nano needs Google Chrome installed, not Edge or Firefox. |
| **A message says the database could not be opened** | Another copy of Ledger may already be running, or the folder is read-only. Do not run it from Program Files. |
| **The app closes immediately** | Open `ledger_error.log` next to the executable; it names the cause. |

If something goes wrong that is not on this list, look for `ledger_error.log` in the app
folder — every crash is written there with a timestamp.

---

## 15. Privacy

- **Your transactions stay on this computer.** The database is a local file.
- **Tier 0 and tier 1 never use the network.** Ollama listens on your own machine; Chrome
  runs locally.
- **Tier 2 is off by default.** The first cloud request shows exactly what would be sent,
  and you can refuse permanently.
- **The API key is encrypted** with your Windows user account, using the same protection
  Windows uses for saved passwords.
- **What is never sent:** account numbers, transaction descriptions beyond the flagged
  items you chose, balances, or the database file itself.

The one thing worth knowing: when you allow a cloud analysis, the *summary* goes to Google —
totals, category figures, flagged payments and the six-month trend. If you would rather
that never happened, set **Allow a summary to be sent to Gemini** to off.

---

## 16. What it does not do

- **It does not connect to your bank.** You export a statement and import it.
- **It does not handle multiple currencies.** Figures are pounds, with no conversion.
- **It does not track investments** or account balances over time — only what moved.
- **It does not forecast next month** from trends. The Plan screen projects a savings goal,
  not your spending.
- **It does not sync between computers.** Copy the folder if you want to move it.
