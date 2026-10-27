# Installing Ollama for Ledger

A walkthrough for someone who has never installed a developer tool before.

**What you are doing:** Ollama is a small free program that runs an AI model on your own
computer. Ledger will use it to write short summaries of your spending — without sending
anything to the internet.

| | |
|---|---|
| **Time** | 15–20 minutes, most of it downloading |
| **Disk space** | about 3.5 GB |
| **Memory** | 8 GB or more (16 GB if you pick the bigger model) |
| **Cost** | nothing |
| **Internet** | needed once, for the download. Works offline afterwards |

---

## Step 1 — Download

1. Open your browser.
2. Go to **`ollama.com/download`**
3. Click the button that says **Download for Windows**.

A file called `OllamaSetup.exe` lands in your **Downloads** folder. It is about 700 MB.

---

## Step 2 — Install it

1. Open **Downloads** and double-click **OllamaSetup.exe**.
2. If Windows asks *"Do you want to allow this app to make changes?"*, click **Yes**.
   (That is the normal Windows prompt for any installer.)
3. Click **Install**. It takes a minute or two.
4. When it finishes, click **Close**.

You do not need to do anything else to start it. Ollama is now running quietly in the
background, and it will start by itself every time you switch the computer on.

**To check it is running:** look at the bottom-right of your screen, near the clock. Click
the small **^** arrow to show hidden icons. You should see the Ollama icon.

If you cannot find it, that is fine — Step 5 will tell you for certain.

---

## Step 3 — Open a terminal

This is the part that feels technical. It is not: it is just a box where you type a command
and press Enter.

1. Press the **Windows key** (or click Start).
2. Type **`powershell`**
3. Press **Enter**.

A dark blue or black window opens with a prompt like:

```
PS C:\Users\YourName>
```

That blinking cursor means it is ready for a command.

> **Careful:** you cannot paste with Ctrl+V in some older terminals. If pasting does not
> work, right-click instead — Windows terminals paste on right-click.

---

## Step 4 — Download the model

Type this exactly, then press **Enter**:

```
ollama pull gemma3:4b
```

What you will see:

- A progress bar, several lines tall, filling up.
- About **3.3 GB** downloading. On a normal home connection this is 5–15 minutes.
- When it finishes, the progress bars disappear and you get your prompt back:
  `PS C:\Users\YourName>`

**Do not close the window until you see the prompt again.**

Nothing appears to happen for the first 10–20 seconds. That is normal — it is asking the
server how big the download is.

---

## Step 5 — Check it worked

Type:

```
ollama list
```

Press Enter. You should see something like:

```
NAME           ID              SIZE     MODIFIED
gemma3:4b      a2af6cc3eb7f    3.3 GB   2 minutes ago
```

If `gemma3:4b` is there, the hard part is done.

### Optional: talk to it directly

```
ollama run gemma3:4b
```

Then type `Hello` and press Enter. It will answer. Type `/bye` and press Enter to leave.

This proves the model works. You will not normally need this — Ledger does it for you.

---

## Step 6 — Connect Ledger

1. Open **Ledger**.
2. Go to **Settings** (bottom of the left-hand list).
3. Find the **Local model** card.
4. Set **Use** to **Automatic**. (Or **Ollama** if you want to force it.)
5. Press **Check**.

You should now see, in green:

```
Ready: Ollama gemma3:4b
```

6. Go to **Insights** and press **Answer**.

The label above the written review will say **Local · Ollama gemma3:4b**, and no network
connection is used.

---

## Which model should I choose?

| Model | Command | Memory needed | Choose it if |
|---|---|---|---|
| **gemma3:4b** | `ollama pull gemma3:4b` | ~8 GB | Default. Fast, good enough, runs on a normal laptop |
| **qwen3:8b** | `ollama pull qwen3:8b` | ~16 GB | You have a desktop with plenty of memory and want better phrasing |

Below 3B the writing quality drops enough that the summaries are worse than the plain
figures Ledger already shows you. Do not go smaller.

### Switching later

```
ollama pull qwen3:8b
ollama rm gemma3:4b
```

Ledger prefers the smallest model it recognises, so remove the one you no longer want.
Press **Check** in Settings afterwards.

---

## If something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| `ollama` is not recognised as a command | The terminal opened before the installer finished | Close the terminal, open a new one, try again |
| `Error: pull model manifest: file does not exist` | A typo in the model name | Copy `gemma3:4b` exactly |
| Download stalls at 0% | Slow server or a blocked connection | Wait 2 minutes, then press Ctrl+C and run the command again |
| Ledger says **"Ollama is not running"** | Ollama is not started | Start menu → type `Ollama` → open it. Wait 10 seconds, press Check |
| Ledger says **"Ollama is running but has no model installed"** | Step 4 was skipped | Run `ollama pull gemma3:4b` |
| Windows Firewall asks about Ollama | It wants permission to listen locally | Allow it on **private** networks |
| Answers take 30 seconds or more | Normal on a laptop without a graphics card | Ask shorter questions, or drop to a smaller model |
| Answers ramble or repeat | The model is too small for the question | Try `qwen3:4b`, or ask a shorter question |
| Ledger says "Ready" but pressing Answer does nothing | A very long question was sent to the local model | Shorten it, or let Ledger send reasoning questions to Gemini |

### Where to look when it misbehaves

- **Is Ollama alive?** Open `http://127.0.0.1:11434` in a browser. If it says
  *"Ollama is running"*, it is fine.
- **Which models are installed?** `ollama list`
- **Is Ledger pointing at it?** Settings → Local model → Check.

---

## Removing it later

```
ollama rm gemma3:4b
```

Then **Windows Settings → Apps → Installed apps → Ollama → Uninstall**.

That frees the space and stops it starting with Windows. Ledger keeps working — it falls
back to Chrome's built-in model or asks before using Gemini.

---

## What Ollama does, and does not do

- **It only listens on your own machine** (`127.0.0.1`). Nothing is uploaded.
- **No account, no sign-in, no subscription.**
- **It is a separate program.** Ledger talks to it the way a browser talks to a website,
  except the "website" is on your own computer.
- **It does not see your database.** Ledger sends it a short summary of figures, one
  question at a time — the same summary you can see under *What the numbers say*.

---

## The whole thing, as a checklist

```
[ ] Download OllamaSetup.exe from ollama.com/download
[ ] Install it, click Close
[ ] Windows key → type powershell → Enter
[ ] ollama pull gemma3:4b          (wait for the prompt to come back)
[ ] ollama list                    (gemma3:4b should be listed)
[ ] Ledger → Settings → Local model → Use: Automatic → Check
[ ] Ledger → Insights → Answer     (label should say Local · Ollama gemma3:4b)
```
