"""Verification harness for ledger.py.

Run with:  python check_ledger.py

Exercises the calendar engine, parsers, de-duplication, rule matching,
transfer pairing and the database layer against a throwaway database.
"""

from __future__ import annotations

import http.server
import json
import sqlite3
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

import ledger as L

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def equal(label: str, actual: object, expected: object) -> None:
    check(label, actual == expected, f"got {actual!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# 1. Billing cycle
# ---------------------------------------------------------------------------

print("billing cycle")
equal("anchor 1 october", L.cycle_bounds(2026, 10, 1), (date(2026, 10, 1), date(2026, 10, 31)))
equal("anchor 1 february", L.cycle_bounds(2026, 2, 1), (date(2026, 2, 1), date(2026, 2, 28)))
equal(
    "anchor 25 october",
    L.cycle_bounds(2026, 10, 25),
    (date(2026, 10, 25), date(2026, 11, 24)),
)
equal(
    "anchor 31 january (short february)",
    L.cycle_bounds(2026, 1, 31),
    (date(2026, 1, 31), date(2026, 2, 27)),
)
equal(
    "anchor 31 february clamps to 28",
    L.cycle_bounds(2026, 2, 31),
    (date(2026, 2, 28), date(2026, 3, 30)),
)
equal(
    "anchor 31 april clamps to 30",
    L.cycle_bounds(2026, 4, 31),
    (date(2026, 4, 30), date(2026, 5, 30)),
)
equal(
    "anchor 30 february clamps",
    L.cycle_bounds(2026, 2, 30),
    (date(2026, 2, 28), date(2026, 3, 29)),
)
equal(
    "anchor 25 containment before anchor",
    L.cycle_containing(date(2026, 10, 24), 25),
    (date(2026, 9, 25), date(2026, 10, 24)),
)
equal(
    "anchor 25 containment on anchor",
    L.cycle_containing(date(2026, 10, 25), 25),
    (date(2026, 10, 25), date(2026, 11, 24)),
)
equal(
    "anchor 1 containment",
    L.cycle_containing(date(2026, 3, 15), 1),
    (date(2026, 3, 1), date(2026, 3, 31)),
)
equal(
    "leap year february 2024",
    L.cycle_bounds(2024, 2, 29),
    (date(2024, 2, 29), date(2024, 3, 28)),
)

# every day of 2026 belongs to exactly one cycle, and cycles are contiguous
for anchor in (1, 5, 25, 28, 29, 30, 31):
    day = date(2026, 1, 1)
    previous_end = None
    gaps = 0
    while day < date(2027, 1, 1):
        start, end = L.cycle_containing(day, anchor)
        check(f"anchor {anchor} contains {day}", start <= day <= end, f"{start}..{end}")
        if previous_end is not None and start != previous_end + timedelta(days=1):
            gaps += 1
        previous_end = end
        day = end + timedelta(days=1)
    check(f"anchor {anchor} has no gaps", gaps == 0, f"{gaps} gaps")

# ---------------------------------------------------------------------------
# 2. Value parsing
# ---------------------------------------------------------------------------

print("value parsing")
equal("plain", L.parse_amount("12.50"), 12.5)
equal("currency", L.parse_amount("\u00a31,234.56"), 1234.56)
equal("parentheses", L.parse_amount("(45.00)"), -45.0)
equal("trailing minus", L.parse_amount("45.00-"), -45.0)
equal("dr suffix", L.parse_amount("45.00 DR"), -45.0)
equal("cr suffix", L.parse_amount("45.00 CR"), 45.0)
equal("plus sign", L.parse_amount("+99.99"), 99.99)
equal("european comma", L.parse_amount("1,200"), 1200.0)
equal("numeric input", L.parse_amount(7), 7.0)
equal("empty", L.parse_amount(""), None)
equal("text", L.parse_amount("n/a"), None)

equal("iso date", L.parse_date("2026-10-27"), date(2026, 10, 27))
equal("day first", L.parse_date("05/11/2026", dayfirst=True), date(2026, 11, 5))
equal("month first", L.parse_date("05/11/2026", dayfirst=False), date(2026, 5, 11))
equal("month name", L.parse_date("27 Oct 2026"), date(2026, 10, 27))
equal("datetime object", L.parse_date(__import__("datetime").datetime(2026, 4, 2, 9, 30)),
      date(2026, 4, 2))
check("dayfirst detection", L.detect_dayfirst(["01/02/2026", "25/02/2026"]) is True)
check("monthfirst detection", L.detect_dayfirst(["01/25/2026", "02/03/2026"]) is False)

# ---------------------------------------------------------------------------
# 3. Hashing and rules
# ---------------------------------------------------------------------------

print("hashing and rules")
first = L.transaction_hash(date(2026, 10, 1), -45.2, "TESCO  STORES")
second = L.transaction_hash(date(2026, 10, 1), -45.2, "tesco stores")
equal("hash ignores case and spacing", first, second)
check(
    "hash separates sign",
    L.transaction_hash(date(2026, 10, 1), -45.2, "X")
    != L.transaction_hash(date(2026, 10, 1), 45.2, "X"),
)

rules = [
    L.Rule(1, "uber eats", "contains", 20, 100),
    L.Rule(2, "uber", "contains", 10, 100),
    L.Rule(3, "tesco", "contains", 30, 100),
]
equal("longest match wins", L.match_category("UBER EATS 12 LONDON", rules), 20)
equal("shorter match used", L.match_category("UBER TRIP", rules), 10)
equal("keyword inside text", L.match_category("CARD PAYMENT TESCO STORES", rules), 30)
equal("no match", L.match_category("UNKNOWN MERCHANT", rules), None)

# ---------------------------------------------------------------------------
# 4. Transfer pairing
# ---------------------------------------------------------------------------

print("transfer pairing")
base = date(2026, 10, 1)
pairs = L.pair_transfers([
    L.TransferKey("a", base, -500.0, "Barclays Current"),
    L.TransferKey("b", base + timedelta(days=2), 500.0, "Amex"),
])
check("two days apart pairs", set(pairs) == {"a", "b"}, str(pairs))

pairs = L.pair_transfers([
    L.TransferKey("a", base, -500.0, "Barclays Current"),
    L.TransferKey("b", base + timedelta(days=3), 500.0, "Amex"),
])
check("three days apart does not pair", set(pairs) == set(), str(pairs))

pairs = L.pair_transfers([
    L.TransferKey("a", base, -500.0, "Barclays Current"),
    L.TransferKey("b", base, 500.0, "Barclays Current"),
])
check("same account does not pair", set(pairs) == set(), str(pairs))

pairs = L.pair_transfers([
    L.TransferKey("a", base, -500.0, ""),
    L.TransferKey("b", base, 500.0, "Amex"),
])
check("missing account does not pair", set(pairs) == set(), str(pairs))

pairs = L.pair_transfers([
    L.TransferKey("a", base, -500.0, "Barclays"),
    L.TransferKey("b", base + timedelta(hours=5), 500.0, "Amex"),
    L.TransferKey("c", base + timedelta(days=3), 500.0, "Amex"),
])
check("nearest match chosen", set(pairs) == {"a", "b"}, str(pairs))

# ---------------------------------------------------------------------------
# 5. Database layer
# ---------------------------------------------------------------------------

print("database layer")
tmp = Path(tempfile.mkdtemp(prefix="ledger_check_"))
store = L.Store(tmp / "budget_data.db")
check("seeded categories", len(store.categories()) >= 14, str(len(store.categories())))
check("seeded rules", len(store.rules()) > 60, str(len(store.rules())))
equal("default anchor", store.anchor, 1)
store.set_setting("billing_cycle_start_day", "25")
equal("anchor round trip", store.anchor, 25)
store.set_setting("billing_cycle_start_day", "1")

rows = [
    {
        "day": date(2026, 10, 1),
        "amount": -45.2,
        "description": "TESCO STORES",
        "category_id": store.category_id("Groceries & Dining"),
        "hash": L.transaction_hash(date(2026, 10, 1), -45.2, "TESCO STORES"),
        "account": "Barclays",
        "source_file": "oct.csv",
    },
    {
        "day": date(2026, 10, 2),
        "amount": 2400.0,
        "description": "SALARY OCTOBER",
        "category_id": store.category_id("Income"),
        "hash": L.transaction_hash(date(2026, 10, 2), 2400.0, "SALARY OCTOBER"),
        "account": "Barclays",
        "source_file": "oct.csv",
    },
]
added, skipped = store.insert_transactions(rows)
equal("first insert adds", (added, skipped), (2, 0))
added, skipped = store.insert_transactions(rows)
equal("second insert is deduplicated", (added, skipped), (0, 2))
equal("stored once", len(store.transactions_between(date(2026, 10, 1), date(2026, 10, 31))), 2)

summary = L.summarise(
    store.transactions_between(date(2026, 10, 1), date(2026, 10, 31)),
    date(2026, 10, 1),
    date(2026, 10, 31),
)
equal("income", summary.income, 2400.0)
equal("expenses", summary.expenses, 45.2)
equal("net", round(summary.net, 2), 2354.8)

# exact and keyword propagation
groceries = store.category_id("Groceries & Dining")
shopping = store.category_id("Shopping")
equal("exact propagation affects one", store.apply_exact_description("TESCO STORES", shopping), 1)
store.add_rule("TESCO", "contains", shopping)
equal("keyword propagation", store.apply_keyword("TESCO", shopping), 1)
equal("keyword count", store.count_keyword("tesco"), 1)

# internal transfers
transfer_rows = [
    {
        "day": date(2026, 10, 5),
        "amount": -500.0,
        "description": "TRANSFER TO AMEX",
        "category_id": None,
        "hash": L.transaction_hash(date(2026, 10, 5), -500.0, "TRANSFER TO AMEX"),
        "account": "Barclays",
        "source_file": "oct.csv",
    },
    {
        "day": date(2026, 10, 6),
        "amount": 500.0,
        "description": "PAYMENT RECEIVED",
        "category_id": None,
        "hash": L.transaction_hash(date(2026, 10, 6), 500.0, "PAYMENT RECEIVED"),
        "account": "Amex",
        "source_file": "amex.csv",
    },
]
store.insert_transactions(transfer_rows)
matched = store.reconcile_transfers()
equal("one transfer pair matched", matched, 1)
flagged = [
    row for row in store.transactions_between(date(2026, 10, 1), date(2026, 10, 31))
    if int(row["is_transfer"])
]
equal("both legs flagged", len(flagged), 2)

summary = L.summarise(
    store.transactions_between(date(2026, 10, 1), date(2026, 10, 31)),
    date(2026, 10, 1),
    date(2026, 10, 31),
)
equal("transfers excluded from income", summary.income, 2400.0)
equal("transfers excluded from expenses", summary.expenses, 45.2)

# savings flag
payments = [
    row for row in store.transactions_between(date(2026, 10, 1), date(2026, 10, 31))
    if float(row["amount"]) < 0 and not int(row["is_transfer"])
]
store.set_saving(int(payments[0]["id"]), True)
summary = L.summarise(
    store.transactions_between(date(2026, 10, 1), date(2026, 10, 31)),
    date(2026, 10, 1),
    date(2026, 10, 31),
)
check("saving flag counted", summary.flagged > 0, str(summary.flagged))
check("saving count", summary.flagged_count == 1, str(summary.flagged_count))

# backup
backup = store.backup_now()
check("backup written", backup.exists() and backup.stat().st_size > 0, str(backup))
check("backup in .backups", backup.parent.name == ".backups", str(backup.parent))

# ---------------------------------------------------------------------------
# 6. Statement parsing
# ---------------------------------------------------------------------------

print("statement parsing")
csv_path = tmp / "october.csv"
csv_path.write_text(
    "Date,Description,Amount\n"
    "01/10/2026,TESCO STORES 3421,-45.20\n"
    "02/10/2026,SALARY OCTOBER,2400.00\n"
    "03/10/2026,NETFLIX.COM,-10.99\n",
    encoding="utf-8",
)
parsed = L.parse_statement(csv_path)
equal("csv rows", len(parsed.rows), 3)
equal("csv first amount", parsed.rows[0]["amount"], -45.2)
equal("csv day", parsed.rows[0]["day"], date(2026, 10, 1))

debit_credit = tmp / "debits.csv"
debit_credit.write_text(
    "Posted Date,Details,Debit,Credit\n"
    "2026-10-04,UBER TRIP,12.40,\n"
    "2026-10-05,SALARY,,\"2,400.00\"\n",
    encoding="utf-8",
)
parsed = L.parse_statement(debit_credit)
equal("debit column read", parsed.rows[0]["amount"], -12.4)
equal("credit column read", parsed.rows[1]["amount"], 2400.0)

no_header = tmp / "plain.csv"
no_header.write_text(
    "01/10/2026,COSTA COFFEE LONDON,-3.60\n"
    "02/10/2026,ALDI STORES,-27.14\n",
    encoding="utf-8",
)
parsed = L.parse_statement(no_header)
equal("headerless rows", len(parsed.rows), 2)
equal("headerless amount", parsed.rows[0]["amount"], -3.6)
equal("headerless description", parsed.rows[0]["description"], "COSTA COFFEE LONDON")

try:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Transaction Date", "Narrative", "Amount"])
    sheet.append(["04/10/2026", "SHELL PETROL", -55.0])
    sheet.append(["05/10/2026", "AMAZON MARKETPLACE", -21.5])
    xlsx_path = tmp / "october.xlsx"
    workbook.save(xlsx_path)
    parsed = L.parse_statement(xlsx_path)
    equal("xlsx rows", len(parsed.rows), 2)
    equal("xlsx amount", parsed.rows[0]["amount"], -55.0)
except ImportError:
    print("  skipped xlsx (openpyxl missing)")

# ---------------------------------------------------------------------------
# 7. Import pipeline
# ---------------------------------------------------------------------------

print("import pipeline")
parsed = L.parse_statement(csv_path)
prepared = L.prepare_import(store, parsed, "Barclays")
equal("prepared count", len(prepared), 3)
equal("only the identical line is a duplicate",
      sum(1 for row in prepared if row.is_duplicate), 1)
equal("the other two are new", sum(1 for row in prepared if not row.is_duplicate), 2)
fresh = tmp / "november.csv"
fresh.write_text(
    "Date,Description,Amount\n"
    "01/11/2026,TESCO STORES 3421,-45.20\n"
    "02/11/2026,STEAM GAMES,-14.99\n"
    "03/11/2026,UNKNOWN SHOP LTD,-8.00\n",
    encoding="utf-8",
)
prepared = L.prepare_import(store, L.parse_statement(fresh), "Barclays")
equal("fresh rows are new", len(prepared), 3)
equal(
    "tesco categorised",
    prepared[0].category_name,
    "Shopping" if store.rule_rows() else "Groceries & Dining",
)
equal("steam categorised", prepared[1].category_name, "Entertainment & Subscriptions")
equal("unknown falls back", prepared[2].category_name, "Uncategorised")
added, skipped, _matched = L.commit_import(store, prepared)
equal("committed", (added, skipped), (3, 0))

# re-importing the same file adds nothing
prepared = L.prepare_import(store, L.parse_statement(fresh), "Barclays")
added, skipped, _matched = L.commit_import(store, prepared)
equal("re-import is a no-op", (added, skipped), (0, 3))

# internal duplicate inside a single file
dupe = tmp / "dupes.csv"
dupe.write_text(
    "Date,Description,Amount\n"
    "01/12/2026,PRET A MANGER,-6.50\n"
    "01/12/2026,PRET A MANGER,-6.50\n",
    encoding="utf-8",
)
prepared = L.prepare_import(store, L.parse_statement(dupe), "Barclays")
equal("intra-file duplicates are both kept",
      sum(1 for r in prepared if r.is_duplicate), 0)
check("identical lines get distinct hashes", len({r.hash for r in prepared}) == 2)
L.commit_import(store, prepared)
again = L.prepare_import(store, L.parse_statement(dupe), "Barclays")
equal("re-importing that file still dedupes both",
      sum(1 for r in again if r.is_duplicate), 2)

# ---------------------------------------------------------------------------
# 8. Batch import across several statements
# ---------------------------------------------------------------------------

print("batch import")
batch_store = L.Store(tmp / "batch.db")
checking = tmp / "checking.csv"
checking.write_text(
    "Date,Description,Amount\n"
    "01/10/2026,TRANSFER TO CREDIT CARD,-500.00\n"
    "02/10/2026,TESCO STORES 3421,-40.00\n",
    encoding="utf-8",
)
card = tmp / "card.csv"
card.write_text(
    "Date,Description,Amount\n"
    "02/10/2026,PAYMENT THANK YOU,500.00\n"
    "02/10/2026,TESCO STORES 3421,-40.00\n",
    encoding="utf-8",
)
sources = [(L.parse_statement(checking), "Barclays"), (L.parse_statement(card), "Amex")]
prepared = L.prepare_batch(batch_store, sources)
equal("batch collects every row", len(prepared), 4)
equal("cross-file transfer flagged", sum(1 for r in prepared if r.is_transfer), 2)
equal("cross-file duplicate flagged", sum(1 for r in prepared if r.is_duplicate), 1)
equal("batch accounts kept separate",
      {r.account for r in prepared}, {"Barclays", "Amex"})
added, skipped, matches = L.commit_import(batch_store, prepared)
equal("batch commit", (added, skipped), (3, 1))
equal("batch reports the transfer pair", matches, 1)
stored = batch_store.transactions_between(date(2026, 10, 1), date(2026, 10, 31))
equal("transfer legs stored as transfers",
      sum(1 for row in stored if int(row["is_transfer"])), 2)

# changing an account in the dialog re-runs matching without side effects
relabelled = [(L.parse_statement(checking), "Barclays"), (L.parse_statement(card), "Barclays")]
prepared = L.prepare_batch(batch_store, relabelled)
equal("same account stops transfer match",
      sum(1 for r in prepared if r.is_transfer), 0)

# a transfer split across two separate imports must still cancel out
split_store = L.Store(tmp / "split.db")
day_a = tmp / "day_a.csv"
day_a.write_text(
    "Date,Description,Amount\n10/10/2026,TRANSFER OUT,-500.00\n", encoding="utf-8"
)
first_batch = L.prepare_batch(split_store, [(L.parse_statement(day_a), "Barclays")])
L.commit_import(split_store, first_batch)

day_b = tmp / "day_b.csv"
day_b.write_text(
    "Date,Description,Amount\n11/10/2026,TRANSFER IN,500.00\n", encoding="utf-8"
)
second_batch = L.prepare_batch(split_store, [(L.parse_statement(day_b), "Amex")])
equal("second batch marks its own leg",
      sum(1 for r in second_batch if r.is_transfer), 1)
equal("second batch records the counterpart",
      sum(len(r.counterpart_ids) for r in second_batch), 1)
added, skipped, pairs_found = L.commit_import(split_store, second_batch)
equal("second batch adds one row", (added, skipped), (1, 0))
equal("second batch reports one pair", pairs_found, 1)
split_rows = split_store.transactions_between(date(2026, 10, 1), date(2026, 10, 31))
equal("both legs stored as transfers",
      sum(1 for row in split_rows if int(row["is_transfer"])), 2)
split_names = {int(row["id"]): str(row["name"]) for row in split_store.categories()}
check("transfer legs use the transfer category",
      all(
          split_names.get(int(row["category_id"] or 0)) == "Transfers"
          for row in split_rows
          if int(row["is_transfer"])
      ),
      [split_names.get(int(row["category_id"] or 0)) for row in split_rows])
split_summary = L.summarise(split_rows, date(2026, 10, 1), date(2026, 10, 31))
equal("transfer never counts as spending", split_summary.expenses, 0.0)
equal("transfer never counts as income", split_summary.income, 0.0)

# ---------------------------------------------------------------------------
# 9. Projection maths
# ---------------------------------------------------------------------------

print("projection maths")
equal("required months", L.months_to_target(0, 1200, 100), 12)
equal("already reached", L.months_to_target(2000, 1200, 100), 0)
equal("no progress", L.months_to_target(0, 1200, 0), None)
equal("negative rate", L.months_to_target(0, 1200, -50), None)
equal("rounds up", L.months_to_target(0, 1000, 300), 4)

# ---------------------------------------------------------------------------
# 10. Local model routing and computed skills
# ---------------------------------------------------------------------------

print("local routing")


class _NoNano:
    def availability(self, force: bool = False) -> str:
        return "unavailable"

    def stop(self) -> None:
        pass


def record(day: str, amount: float, description: str, category: str) -> dict:
    return {
        "day": date.fromisoformat(day),
        "amount": amount,
        "description": description,
        "category": category,
    }


equal("empty question stays local", L.needs_reasoning(""), False)
equal("short summary stays local", L.needs_reasoning("summary"), False)
equal("why needs reasoning", L.needs_reasoning("why is groceries up"), True)
equal("should needs reasoning", L.needs_reasoning("should I cancel Netflix"), True)
equal("long question escalates",
      L.needs_reasoning("what changed in my spending this cycle compared with last"), True)

equal("prefers the small model",
      L.pick_ollama_model(["qwen3:8b", "gemma3:4b", "llama3.2:3b"]), "gemma3:4b")
equal("handles latest suffix", L.pick_ollama_model(["gemma3:4b:latest"]), "gemma3:4b:latest")
equal("ignores embedding models", L.pick_ollama_model(["nomic-embed-text"]), None)
equal("empty list", L.pick_ollama_model([]), None)
equal("falls back to any chat model", L.pick_ollama_model(["llama3.1:8b"]), "llama3.1:8b")

dead = L.OllamaClient(host="http://127.0.0.1:59991")
equal("closed port reads as not running", dead.probe(), ("not-running", []))

engine = L.LocalEngine(_NoNano(), dead, store)  # type: ignore[arg-type]
status = engine.resolve(force=True)
equal("no local backend", status.backend, "")
check("reason explains both", "Ollama" in status.detail and "Chrome" in status.detail,
      status.detail)
equal("cached status reused", engine.resolve(force=False), status)


class _ExplodingStore:
    """Fails loudly if a background thread reaches for the database."""

    def get_setting(self, *_args: object, **_kwargs: object) -> str:
        raise AssertionError("the database was touched off the main thread")

    def category_id(self, _name: str) -> int | None:
        raise AssertionError("the database was touched off the main thread")


offscreen = L.LocalEngine(_NoNano(), dead, _ExplodingStore())  # type: ignore[arg-type]
worker_status = offscreen.resolve(force=True, preference="ollama")
equal("resolve without database access", worker_status.backend, "")
check("resolve explains ollama alone", "Ollama" in worker_status.detail,
      worker_status.detail)

print("computed skills")
period_one = [
    record("2026-08-03", -10.99, "NETFLIX.COM", "Entertainment & Subscriptions"),
    record("2026-08-09", -50.0, "TESCO STORES", "Groceries & Dining"),
    record("2026-08-01", 2400.0, "SALARY", "Income"),
]
period_two = [
    record("2026-09-03", -10.99, "NETFLIX.COM", "Entertainment & Subscriptions"),
    record("2026-09-09", -60.0, "TESCO STORES", "Groceries & Dining"),
    record("2026-09-01", 2400.0, "SALARY", "Income"),
]
period_three = [
    record("2026-10-03", -10.99, "NETFLIX.COM", "Entertainment & Subscriptions"),
    record("2026-10-09", -140.0, "TESCO STORES", "Groceries & Dining"),
    record("2026-10-01", 2400.0, "SALARY", "Income"),
]

current_spend = L.category_spend(period_three)
history_spend = [L.category_spend(period_one), L.category_spend(period_two)]
equal("category spend ignores income", round(current_spend["Groceries & Dining"], 2), 140.0)
check("income not in spend map", "Income" not in current_spend, str(current_spend))

spikes = L.skill_spikes(current_spend, history_spend)
check("tesco spike found", any("Groceries" in f.title for f in spikes), str(spikes))
check("spike quotes the baseline", any("55" in f.body for f in spikes), str(spikes))

creep = L.skill_creep([period_one, period_two, period_three])
check("repeating charge found",
      any("netflix" in (f.title + f.body).lower() for f in creep), str(creep))

dupe_period = [
    record("2026-10-09", -42.5, "AMAZON", "Shopping"),
    record("2026-10-10", -42.5, "AMAZON", "Shopping"),
]
dupes = L.skill_duplicates(dupe_period)
equal("double charge detected", len(dupes), 1)
check("double charge names the merchant", "Amazon" in dupes[0].body, dupes[0].body)

spread = [record("2026-10-01", -42.5, "AMAZON", "Shopping"),
          record("2026-10-20", -42.5, "AMAZON", "Shopping")]
equal("distant charges are not duplicates", L.skill_duplicates(spread), [])

movers = L.skill_movers(current_spend, history_spend[-1])
check("mover found for groceries", any("Groceries" in f.title for f in movers), str(movers))

on_track = L.CycleSummary(start=date(2026, 10, 1), end=date(2026, 10, 31),
                          income=2400.0, expenses=900.0)
velocity = L.skill_velocity(on_track, (6000.0, 6, 0.0))
equal("on track detection", velocity[0].tone, "good")
behind = L.skill_velocity(on_track, (20000.0, 12, 0.0))
equal("behind detection", behind[0].tone, "warn")

combined = L.run_skills([period_one, period_two, period_three], on_track, (6000.0, 6, 0.0))
check("run_skills caps output", 1 <= len(combined) <= 3, len(combined))
check("run_skills returns findings", combined and isinstance(combined[0], L.Finding),
      type(combined[0]).__name__ if combined else "none")
combined_text = L.findings_text(combined)
check("findings text is readable", combined_text.startswith("- "), combined_text[:60])

# files that cannot be understood fail cleanly rather than crashing
garbage = tmp / "garbage.csv"
garbage.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
try:
    L.parse_statement(garbage)
    check("garbage file rejected", False, "no error raised")
except L.ParseError as error:
    check("garbage file gives a clear message", "column" in str(error).lower(), str(error))

empty = tmp / "empty.csv"
empty.write_text("", encoding="utf-8")
try:
    L.parse_statement(empty)
    check("empty file rejected", False, "no error raised")
except L.ParseError as error:
    check("empty file gives a clear message", "empty" in str(error).lower(), str(error))

try:
    L.parse_statement(tmp / "statement.xls")
    check("legacy xls rejected", False, "no error raised")
except L.ParseError as error:
    check("legacy xls asks for xlsx", "xlsx" in str(error).lower(), str(error))

# ---------------------------------------------------------------------------
# 10b. Bank export shapes that used to corrupt the figures
# ---------------------------------------------------------------------------

print("bank export shapes")

bank_headers = [
    "Transaction Date", "Transaction Type", "Sort Code", "Account Number",
    "Transaction Description", "Debit Amount", "Credit Amount", "Balance",
]
bank_mapping = L.map_columns(bank_headers)
check("debit and credit mapped",
      bank_mapping.get("debit") == 5 and bank_mapping.get("credit") == 6,
      bank_mapping)
check("amount does not claim the debit column",
      bank_mapping.get("amount") not in (5, 6), bank_mapping)

bank_file = tmp / "apostrophe.csv"
bank_file.write_text(
    "Transaction Date,Transaction Type,Sort Code,Account Number,"
    "Transaction Description,Debit Amount,Credit Amount,Balance\n"
    "27/10/2026,FPO,'11-52-26,01496868,ZOPA,1003.86,,-248.76\n"
    "21/10/2026,BGC,'11-52-26,01496868,ADYEN N.V.,,9.46,755.10\n"
    "24/09/2026,CHG,'11-52-26,01496868,DAILY OD INT,.34,,-376.18\n",
    encoding="utf-8",
)
parsed = L.parse_statement(bank_file)
equal("apostrophe sort codes do not merge rows", len(parsed.rows), 3)
equal("debit becomes negative", parsed.rows[0]["amount"], -1003.86)
equal("credit stays positive", parsed.rows[1]["amount"], 9.46)
equal("leading-dot amount parses", parsed.rows[2]["amount"], -0.34)
equal("first description intact", parsed.rows[0]["description"], "ZOPA")
equal("first date intact", parsed.rows[0]["day"], date(2026, 10, 27))

# ignored transactions drop out of every figure
ignore_store = L.Store(tmp / "ignore.db")
ignore_rows = [
    {
        "day": date(2026, 10, 2),
        "amount": -80.0,
        "description": "IGNORED PAYMENT",
        "category_id": None,
        "hash": L.transaction_hash(date(2026, 10, 2), -80.0, "IGNORED PAYMENT"),
        "account": "Barclays",
        "source_file": "ignore.csv",
    },
    {
        "day": date(2026, 10, 3),
        "amount": -20.0,
        "description": "COUNTED PAYMENT",
        "category_id": None,
        "hash": L.transaction_hash(date(2026, 10, 3), -20.0, "COUNTED PAYMENT"),
        "account": "Barclays",
        "source_file": "ignore.csv",
    },
]
ignore_store.insert_transactions(ignore_rows)
window = (date(2026, 10, 1), date(2026, 10, 31))
before = L.summarise(ignore_store.transactions_between(*window), *window)
equal("spending before ignoring", before.expenses, 100.0)
target = [
    row for row in ignore_store.transactions_between(*window)
    if row["description"] == "IGNORED PAYMENT"
][0]
ignore_store.set_ignored(int(target["id"]), True)
after = L.summarise(ignore_store.transactions_between(*window), *window)
equal("ignored payment leaves spending", after.expenses, 20.0)
equal("ignored count reported", after.ignored, 1)
totals = L.category_totals(ignore_store.transactions_between(*window),
                           {r["id"]: r["name"] for r in ignore_store.categories()})
check("ignored payment leaves the category chart",
      all(value <= 20.0 for _name, value in totals), str(totals))

# statements can be listed and removed whole
listing = ignore_store.statements()
equal("statement listed", len(listing), 1)
equal("statement row count", int(listing[0]["rows"]), 2)
equal("statement removed", ignore_store.delete_statement("ignore.csv"), 2)
equal("ledger emptied", len(ignore_store.statements()), 0)

# a database from the previous version gains the new column without losing rows
legacy = tmp / "legacy.db"
connection = sqlite3.connect(legacy)
connection.executescript(
    """
    CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'expense',
        colour TEXT NOT NULL DEFAULT '#8A93A6');
    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, txn_date TEXT NOT NULL,
        amount REAL NOT NULL, description TEXT NOT NULL, category_id INTEGER,
        is_transfer INTEGER NOT NULL DEFAULT 0, is_saving INTEGER NOT NULL DEFAULT 0,
        account TEXT NOT NULL DEFAULT '', source_file TEXT NOT NULL DEFAULT '',
        imported_at TEXT NOT NULL, hash TEXT NOT NULL UNIQUE);
    INSERT INTO transactions
        (txn_date, amount, description, imported_at, hash)
        VALUES ('2026-09-01', -12.5, 'OLD ROW', '2026-09-01T00:00:00', 'old-hash');
    """
)
connection.commit()
connection.close()
migrated = L.Store(legacy)
columns = {row["name"] for row in migrated.conn.execute("PRAGMA table_info(transactions)")}
check("migration adds is_ignored", "is_ignored" in columns, sorted(columns))
kept = migrated.transactions_between(date(2000, 1, 1), date(2100, 1, 1))
equal("migration keeps existing rows", len(kept), 1)
equal("migrated row keeps its amount", kept[0]["amount"], -12.5)
equal("migrated row defaults to not ignored", int(kept[0]["is_ignored"]), 0)
check("migration wrote a backup",
      any((tmp / ".backups").glob("*.db")), "no backup")
migrated.close()

# ---------------------------------------------------------------------------
# 11. Ollama wire protocol (against a stub server, no Ollama needed)
# ---------------------------------------------------------------------------

print("ollama protocol")


class _OllamaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/api/tags":
            body = json.dumps(
                {"models": [{"name": "gemma3:4b"}, {"name": "nomic-embed-text"}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({"response": f"reply from {payload.get('model')}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


stub = http.server.HTTPServer(("127.0.0.1", 0), _OllamaHandler)
stub_thread = threading.Thread(target=stub.serve_forever, daemon=True)
stub_thread.start()
try:
    ollama = L.OllamaClient(host=f"http://127.0.0.1:{stub.server_address[1]}")
    state, models = ollama.probe()
    equal("ollama probe state", state, "ready")
    check("ollama lists models", "gemma3:4b" in models, str(models))
    equal("ollama picks the small model", L.pick_ollama_model(models), "gemma3:4b")
    equal("ollama reply", ollama.ask("INSTRUCTION", "PROMPT", "gemma3:4b"),
          "reply from gemma3:4b")

    routed = L.LocalEngine(_NoNano(), ollama, store)  # type: ignore[arg-type]
    resolved = routed.resolve(force=True, preference="ollama")
    equal("engine prefers ollama when ready", resolved.backend, "ollama")
    equal("engine reports the model", resolved.detail, "gemma3:4b")
    text, source = routed.ask("INSTRUCTION", "PROMPT", preference="ollama")
    equal("engine labels the source", source, "Local \u00b7 Ollama gemma3:4b")
    check("engine returns local text", text.startswith("reply from"), text)
finally:
    stub.shutdown()

# ---------------------------------------------------------------------------
# 12. Chrome / Gemini Nano bridge (skipped when Chrome is absent)
# ---------------------------------------------------------------------------

print("chrome bridge")
if L.find_chrome() is None:
    print("  skipped (Chrome not installed)")
else:
    nano = L.NanoClient(profile=tmp / ".nano-profile")
    state = nano.availability(force=True)
    nano.stop()
    check("availability returns a known state",
          state in {"available", "downloadable", "downloading", "unavailable"},
          f"{state} ({nano.detail()})")
    print(f"  reported: {state} ({nano.detail()})")

    # the prompt script is checked against a stubbed model, so no download is triggered
    stub = L.NanoClient(profile=tmp / ".nano-stub")
    captured: dict[str, str] = {}

    def fake_evaluate(expression: str, timeout: float = 60.0) -> dict:
        captured["script"] = expression
        return {"ok": True, "text": "A stubbed sentence about the cycle."}

    stub._server_alive = lambda: True  # type: ignore[method-assign]
    stub._evaluate = fake_evaluate  # type: ignore[method-assign]
    answer = stub.ask("INSTRUCTION", 'Say "this" with \u00a3 and a\nnewline', timeout=5)
    equal("nano ask returns text", answer, "A stubbed sentence about the cycle.")
    call_line = next(
        line for line in captured["script"].splitlines() if "session.prompt(" in line
    )
    literal = call_line.split("session.prompt(", 1)[1]
    literal = literal[: literal.rfind(")")]
    embedded = json.loads(literal)
    check("prompt is embedded safely", '"this"' in embedded, embedded[:50])
    check("newlines are escaped", "\n" in embedded, "missing newline")
    check("pound sign survives", "\u00a3" in embedded, embedded[:50])
    check("instruction is carried through", embedded.startswith("INSTRUCTION"), embedded[:20])

# ---------------------------------------------------------------------------
# 13. Update check
# ---------------------------------------------------------------------------

print("update check")
equal("parse plain version", L.parse_version("1.2.3"), (1, 2, 3))
equal("parse tagged version", L.parse_version("v1.2.3"), (1, 2, 3))
equal("parse partial version", L.parse_version("2.0"), (2, 0))
equal("parse nonsense", L.parse_version("release"), (0,))
check("patch bump is newer", L.is_newer("1.1.1", "1.1.0"))
check("minor bump beats patch", L.is_newer("1.2.0", "1.1.9"))
check("major bump", L.is_newer("2.0.0", "1.9.9"))
check("same version is not newer", not L.is_newer("1.1.0", "1.1.0"))
check("older version is not newer", not L.is_newer("1.0.9", "1.1.0"))

release = L.interpret_release(
    {"tag_name": "v9.9.9", "html_url": "https://example.invalid/rel", "body": "notes"},
    "1.1.0",
)
check("newer release reported", release.available)
equal("release version stripped of tag", release.latest, "9.9.9")
equal("release url kept", release.url, "https://example.invalid/rel")

current_release = L.interpret_release({"tag_name": "v1.1.0"}, "1.1.0")
check("current release not flagged", not current_release.available)
empty_release = L.interpret_release({}, "1.1.0")
check("empty payload is safe", empty_release.latest == "1.1.0" and not empty_release.available)


class _ReleaseHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/latest":
            body = json.dumps(
                {"tag_name": "v9.9.9", "html_url": "https://example.invalid/rel", "body": "x"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/missing":
            self.send_response(404)
            self.end_headers()
        elif self.path == "/limited":
            self.send_response(403)
            self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


release_server = http.server.HTTPServer(("127.0.0.1", 0), _ReleaseHandler)
release_thread = threading.Thread(target=release_server.serve_forever, daemon=True)
release_thread.start()
try:
    base = f"http://127.0.0.1:{release_server.server_address[1]}"
    found = L.check_for_update(current="1.1.0", endpoint=f"{base}/latest")
    check("http check finds the release", found.available, found.latest)
    equal("http check reads the tag", found.latest, "9.9.9")

    for path, expected in (("/missing", "no releases"), ("/limited", "limiting")):
        try:
            L.check_for_update(current="1.1.0", endpoint=f"{base}{path}")
            check(f"{path} raises", False, "no error")
        except RuntimeError as error:
            check(f"{path} explains itself", expected in str(error).lower(), str(error))
finally:
    release_server.shutdown()

# ---------------------------------------------------------------------------
# 13. Accounts
# ---------------------------------------------------------------------------

print("accounts")
acct_store = L.Store(tmp / "accounts.db")
acct_rows = []
for index, (account, amount) in enumerate(
    [
        ("Current", -10.0), ("Current", -20.0),
        ("Credit card", -30.0), ("Credit card", -40.0), ("Credit card", 5.0),
    ]
):
    day = date(2026, 10, 1 + index)
    description = f"ROW {index}"
    acct_rows.append(
        {
            "day": day,
            "amount": amount,
            "description": description,
            "category_id": None,
            "hash": L.transaction_hash(day, amount, description),
            "account": account,
            "source_file": "mix.csv",
        }
    )
acct_store.insert_transactions(acct_rows)
accounts = acct_store.accounts()
by_name = {str(row["account"]): int(row["rows"]) for row in accounts}
equal("two accounts listed", len(by_name), 2)
equal("current count", by_name.get("Current"), 2)
equal("credit count", by_name.get("Credit card"), 3)

window_start, window_end = date(2026, 10, 1), date(2026, 10, 31)
scoped = L.period_records(acct_store, {}, window_start, window_end, account="Credit card")
equal("scope narrows records", len(scoped), 3)
equal("unknown scope returns nothing",
      len(L.period_records(acct_store, {}, window_start, window_end, account="Nope")), 0)
equal("no scope returns everything",
      len(L.period_records(acct_store, {}, window_start, window_end)), 5)

equal("rename moves every row", acct_store.rename_account("Credit card", "Amex"), 3)
equal("still two accounts", len(acct_store.accounts()), 2)
renamed = L.period_records(acct_store, {}, window_start, window_end, account="Amex")
equal("renamed rows visible under the new name", len(renamed), 3)
equal("old name is gone",
      len(L.period_records(acct_store, {}, window_start, window_end, account="Credit card")), 0)

equal("renaming onto an existing account merges", acct_store.rename_account("Amex", "Current"), 3)
merged = acct_store.accounts()
equal("one account after merge", len(merged), 1)
equal("merge keeps every row", int(merged[0]["rows"]), 5)
equal("renaming to itself does nothing", acct_store.rename_account("Current", "Current"), 0)

print("goals")
goal_store = L.Store(tmp / "goals.db")
goal_store.add_goal("House", 30000, 36, 0, 50)
goal_store.add_goal("Car", 8000, 24, 2000, 30)
equal("two goals stored", len(goal_store.goals()), 2)

plans, leftover = L.plan_goals(goal_store.goals(), 1000.0)
equal("share split", round(plans[0].allocated, 2), 500.0)
equal("second share split", round(plans[1].allocated, 2), 300.0)
equal("leftover reported", round(leftover, 2), 200.0)
equal("required per month", round(plans[0].required, 2), round(30000 / 36, 2))
check("house is late at this rate", not plans[0].on_time, plans[0].months_needed)
check("car is on time", plans[1].on_time, plans[1].months_needed)

# shares over 100 are scaled down, never overspending the pot
car_id = int(goal_store.goals()[1]["id"])
goal_store.update_goal(car_id, "Car", 8000, 24, 2000, 100)
plans_over, leftover_over = L.plan_goals(goal_store.goals(), 1000.0)
equal("over-allocation scaled to the pot",
      round(sum(plan.allocated for plan in plans_over), 2), 1000.0)
equal("nothing left when over-allocated", round(leftover_over, 2), 0.0)

# a single old-style goal is carried over on first run
legacy_goal = tmp / "legacy_goal.db"
seed_store = L.Store(legacy_goal)
seed_store.set_setting("goal_target", "7500")
seed_store.set_setting("goal_months", "15")
seed_store.set_setting("goal_saved", "500")
seed_store.close()
reopened = L.Store(legacy_goal)
migrated_goals = reopened.goals()
equal("legacy goal migrated", len(migrated_goals), 1)
equal("legacy target kept", float(migrated_goals[0]["target"]), 7500.0)
equal("legacy months kept", int(migrated_goals[0]["months"]), 15)
reopened.delete_goal(int(migrated_goals[0]["id"]))
equal("goal removed", len(reopened.goals()), 0)
reopened.close()

# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
