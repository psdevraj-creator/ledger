"""Ledger - a local personal finance desktop application.

Everything lives in this one file: SQLite storage, statement parsing,
spending analytics, the projection engine and the CustomTkinter interface.
The database sits beside the executable (or beside this source file) and is
backed up automatically when the window closes.
"""

from __future__ import annotations

import atexit
import base64
import calendar
import csv
import hashlib
import io
import json
import math
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import customtkinter as ctk
import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

matplotlib.use("TkAgg", force=True)

APP_NAME = "Ledger"
APP_VERSION = "1.3.0"
GITHUB_REPO = "psdevraj-creator/ledger"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
DB_FILENAME = "budget_data.db"
BACKUP_DIRNAME = ".backups"
BACKUP_KEEP = 30
CURRENCY = "\u00a3"
UNCATEGORISED = "Uncategorised"
TRANSFER_CATEGORY = "Transfers"
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:streamGenerateContent?alt=sse"
)
TRANSFER_WINDOW_DAYS = 2
PAGE_LIMIT = 900


# ---------------------------------------------------------------------------
# Palette and typography
# ---------------------------------------------------------------------------


class Palette:
    """Single source of truth for colour."""

    BG = "#F4F6F8"
    SURFACE = "#FFFFFF"
    RAISED = "#EAEEF3"
    BORDER = "#DFE4EA"
    TEXT = "#111A24"
    MUTED = "#586373"
    FAINT = "#7A8595"
    ACCENT = "#0F8A6A"
    ACCENT_HOVER = "#0C7459"
    ACCENT_DIM = "#CFE7DF"
    FLAG = "#A96400"
    RED = "#B8412F"
    BLUE = "#2C63BF"
    SELECT = "#D8E4EF"
    SCROLL = "#C6CFD9"
    SCROLL_HOVER = "#AEB8C4"


SERIES = (
    "#0F8A6A",
    "#2C63BF",
    "#C08A2E",
    "#6B4FB8",
    "#3A8296",
    "#B85C52",
    "#62855A",
    "#A37486",
)


_SCALE = {
    8: 10,
    9: 11,
    10: 12,
    11: 13,
    12: 14,
    14: 16,
    15: 17,
    16: 18,
    17: 19,
    18: 20,
    22: 24,
    26: 28,
    27: 30,
}


_FAMILY = (
    "Segoe UI"
    if sys.platform == "win32"
    else "Helvetica Neue"
    if sys.platform == "darwin"
    else "DejaVu Sans"
)
_MONO_FAMILY = "Consolas" if sys.platform == "win32" else "Menlo"


def font(size: int = 11, weight: str = "normal") -> tuple[str, int, str]:
    """Type scale: sizes are authored small and rendered one or two steps larger."""
    return (_FAMILY, _SCALE.get(size, size), weight)


def mono(size: int = 10) -> tuple[str, int, str]:
    return (_MONO_FAMILY, _SCALE.get(size, size), "normal")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def app_dir() -> Path:
    """Directory holding the executable when frozen, else the source folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def database_path() -> Path:
    return data_dir() / DB_FILENAME


def backup_dir() -> Path:
    return data_dir() / BACKUP_DIRNAME


def data_dir() -> Path:
    """Where user data lives.

    On macOS a frozen app runs from inside its bundle, which is replaced on
    upgrade, so the database goes to Application Support instead.
    """
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return app_dir()


def resource_path(relative: str) -> Path:
    """Locate a bundled asset whether running from source or frozen."""
    bundle = getattr(sys, "_MEIPASS", None)
    base = Path(bundle) if bundle else app_dir()
    return base / relative


# ---------------------------------------------------------------------------
# Update check
# ---------------------------------------------------------------------------


@dataclass
class UpdateInfo:
    latest: str
    available: bool
    url: str
    notes: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """Turn 'v1.2.3' or '1.2' into a comparable tuple."""
    numbers = re.findall(r"\d+", str(text))
    if not numbers:
        return (0,)
    return tuple(int(part) for part in numbers[:3])


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def interpret_release(payload: dict[str, Any], current: str) -> UpdateInfo:
    """Read a GitHub release payload without trusting its shape."""
    tag = str(payload.get("tag_name") or "").strip().lstrip("vV")
    url = str(payload.get("html_url") or RELEASES_PAGE)
    notes = str(payload.get("body") or "").strip()[:600]
    available = bool(tag) and is_newer(tag, current)
    return UpdateInfo(
        latest=tag or current,
        available=available,
        url=url,
        notes=notes,
    )


def check_for_update(
    current: str = APP_VERSION,
    endpoint: str = RELEASES_API,
    timeout: float = 6.0,
) -> UpdateInfo:
    """Ask GitHub for the newest published release."""
    import requests

    response = requests.get(
        endpoint,
        timeout=timeout,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APP_NAME}/{current}",
        },
    )
    if response.status_code == 404:
        raise RuntimeError("No releases have been published yet.")
    if response.status_code == 403:
        raise RuntimeError("GitHub is limiting update checks. Try again later.")
    response.raise_for_status()
    return interpret_release(response.json(), current)


# ---------------------------------------------------------------------------
# Calendar and billing cycle engine
# ---------------------------------------------------------------------------


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamp_day(year: int, month: int, day: int) -> date:
    """Return the requested day, clamped to the real length of the month."""
    return date(year, month, min(max(day, 1), days_in_month(year, month)))


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def cycle_start(year: int, month: int, anchor: int) -> date:
    """Opening date of the cycle that begins inside the given month."""
    return clamp_day(year, month, anchor)


def cycle_bounds(year: int, month: int, anchor: int) -> tuple[date, date]:
    """Start and end (inclusive) of the cycle opening in ``month``.

    Anchoring on day 1 yields exactly the calendar month. Any other anchor
    runs from the N-th of one month to the day before the N-th of the next,
    with the N-th clamped to the final day of shorter months.
    """
    start = cycle_start(year, month, anchor)
    next_year, next_month = shift_month(year, month, 1)
    end = cycle_start(next_year, next_month, anchor) - timedelta(days=1)
    return start, end


def cycle_containing(moment: date, anchor: int) -> tuple[date, date]:
    """Cycle that contains ``moment``."""
    start_this = cycle_start(moment.year, moment.month, anchor)
    if moment >= start_this:
        return start_this, cycle_bounds(moment.year, moment.month, anchor)[1]
    prev_year, prev_month = shift_month(moment.year, moment.month, -1)
    return cycle_start(prev_year, prev_month, anchor), start_this - timedelta(days=1)


def cycle_sort_key(bounds: tuple[date, date]) -> tuple[date, date]:
    return bounds


def previous_cycles(year: int, month: int, anchor: int, count: int) -> list[tuple[date, date]]:
    """The ``count`` most recent cycles, oldest first, ending at (year, month)."""
    result: list[tuple[date, date]] = []
    for offset in range(count - 1, -1, -1):
        year_i, month_i = shift_month(year, month, -offset)
        result.append(cycle_bounds(year_i, month_i, anchor))
    return result


def format_day(moment: date) -> str:
    return f"{moment.day} {moment.strftime('%b %Y')}"


def format_range(start: date, end: date) -> str:
    if start.year == end.year and start.month == end.month:
        return f"{start.day} - {end.day} {end.strftime('%b %Y')}"
    if start.year == end.year:
        return f"{start.day} {start.strftime('%b')} - {end.day} {end.strftime('%b %Y')}"
    return f"{format_day(start)} - {format_day(end)}"


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


def parse_amount(value: Any) -> float | None:
    """Parse a money cell from almost any statement dialect."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return None
        return round(float(value), 2)

    text = str(value).replace("\u00a0", " ").strip()
    if not text:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    upper = text.upper()
    if upper.endswith(" DR"):
        negative = True
        text = text[:-3]
    elif upper.endswith(" CR"):
        text = text[:-3]
    elif upper.endswith("DR") and len(text) > 2:
        negative = True
        text = text[:-2]
    elif upper.endswith("CR") and len(text) > 2:
        text = text[:-2]

    for symbol in (CURRENCY, "$", "\u20ac", "\u00a5", ",", " ", "'"):
        text = text.replace(symbol, "")
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        negative = True
        text = text[1:]

    if text.startswith("."):
        text = "0" + text
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    amount = float(text)
    return -amount if negative else amount


def _date_formats(dayfirst: bool) -> tuple[str, ...]:
    numeric_short = ("%d/%m/%y", "%m/%d/%y")
    numeric_long = ("%d/%m/%Y", "%m/%d/%Y")
    if not dayfirst:
        numeric_short = tuple(reversed(numeric_short))
        numeric_long = tuple(reversed(numeric_long))
    return (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        *numeric_long,
        *numeric_short,
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d %b %y",
        "%b %d, %Y",
        "%b %d %Y",
    )


def parse_date(value: Any, dayfirst: bool = True) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    for fmt in _date_formats(dayfirst):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    match = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        day, month = (first, second) if dayfirst else (second, first)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def detect_dayfirst(samples: Iterable[Any]) -> bool:
    """Decide between D/M/Y and M/D/Y by looking for unambiguous rows."""
    dayfirst_votes = 0
    month_first_votes = 0
    for sample in samples:
        text = str(sample or "").strip()
        match = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}", text)
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12 >= second:
            dayfirst_votes += 1
        elif second > 12 >= first:
            month_first_votes += 1
    if dayfirst_votes or month_first_votes:
        return dayfirst_votes >= month_first_votes
    return True


def normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def transaction_hash(
    day: date, amount: float, description: str, sequence: int = 0
) -> str:
    payload = f"{day.isoformat()}|{amount:.2f}|{normalise_text(description)}"
    if sequence:
        payload += f"|{sequence}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def money(value: float, decimals: int = 2, signed: bool = False) -> str:
    body = f"{abs(float(value)):,.{decimals}f}"
    if signed:
        prefix = "+" if value > 0 else "-" if value < 0 else ""
    else:
        prefix = "-" if value < 0 else ""
    return f"{prefix}{CURRENCY}{body}"


def compact_money(value: float) -> str:
    magnitude = abs(float(value))
    if magnitude >= 1_000_000:
        return f"{CURRENCY}{magnitude / 1_000_000:,.1f}m"
    if magnitude >= 10_000:
        return f"{CURRENCY}{magnitude / 1_000:,.1f}k"
    return money(value, decimals=0)


# ---------------------------------------------------------------------------
# Rules and transfers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: int
    pattern: str
    match_type: str
    category_id: int
    priority: int


def match_category(description: str, rules: Sequence[Rule]) -> int | None:
    """First matching rule wins; rules arrive pre-sorted by specificity."""
    text = normalise_text(description)
    for rule in rules:
        needle = normalise_text(rule.pattern)
        if not needle:
            continue
        if rule.match_type == "exact":
            if text == needle:
                return rule.category_id
        elif needle in text:
            return rule.category_id
    return None


@dataclass(frozen=True)
class TransferKey:
    key: str
    day: date
    amount: float
    account: str


def pair_transfers(items: Sequence[TransferKey]) -> dict[str, str]:
    """Pair equal-and-opposite entries up to two days apart in different accounts."""
    buckets: dict[float, list[TransferKey]] = {}
    for item in items:
        if abs(item.amount) < 0.01:
            continue
        buckets.setdefault(round(abs(item.amount), 2), []).append(item)

    paired: dict[str, str] = {}
    window = timedelta(days=TRANSFER_WINDOW_DAYS).total_seconds()

    for bucket in buckets.values():
        outflows = sorted((i for i in bucket if i.amount < 0), key=lambda i: i.day)
        inflows = sorted((i for i in bucket if i.amount > 0), key=lambda i: i.day)
        if not outflows or not inflows:
            continue
        consumed: set[str] = set()
        for outflow in outflows:
            best: TransferKey | None = None
            best_gap = window + 1.0
            for inflow in inflows:
                if inflow.key in consumed:
                    continue
                if not inflow.account.strip() or not outflow.account.strip():
                    continue
                if inflow.account.strip().lower() == outflow.account.strip().lower():
                    continue
                gap = abs((inflow.day - outflow.day).total_seconds())
                if gap <= window and gap < best_gap:
                    best = inflow
                    best_gap = gap
            if best is not None:
                consumed.add(outflow.key)
                consumed.add(best.key)
                paired[outflow.key] = best.key
                paired[best.key] = outflow.key
    return paired


def months_to_target(start: float, target: float, monthly: float, cap: int = 120) -> int | None:
    """Months needed to reach the target, or None when the rate is not positive."""
    remaining = target - start
    if remaining <= 0:
        return 0
    if monthly <= 0:
        return None
    return min(int(math.ceil(remaining / monthly)), cap)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("Income", "income", "#0F8A6A"),
    ("Housing", "expense", "#2C63BF"),
    ("Groceries & Dining", "expense", "#B8802A"),
    ("Transport & Travel", "expense", "#3A8296"),
    ("Entertainment & Subscriptions", "expense", "#6B4FB8"),
    ("Shopping", "expense", "#B85C52"),
    ("Utilities", "expense", "#62855A"),
    ("Health & Fitness", "expense", "#2F8C74"),
    ("Insurance", "expense", "#5B6B85"),
    ("Personal & Care", "expense", "#9E6B7C"),
    ("Fees & Charges", "expense", "#A05A4E"),
    ("Savings", "transfer", "#2F7F8C"),
    ("Transfers", "transfer", "#6B7280"),
    (UNCATEGORISED, "expense", "#7A8595"),
)

SEED_RULES: dict[str, tuple[str, ...]] = {
    "Transport & Travel": (
        "uber", "lyft", "shell", "exxon", "train", "national rail", "tfl", "uber trip",
        "london underground", "bus fare", "taxi", "petrol", "bp", "texaco", "parking",
        "easyjet", "ryanair", "british airways", "flight", "oyster", "avanti", "lner",
        "scotrail", "megabus", "bolt", "addison lee", "enterprise rent", "car park",
    ),
    "Entertainment & Subscriptions": (
        "netflix", "spotify", "hulu", "steam", "nintendo", "disney", "prime video",
        "youtube premium", "apple tv", "playstation", "xbox", "cinema", "odeon", "vue",
        "now tv", "audible", "patreon", "twitch", "icloud", "google one", "dropbox",
    ),
    "Groceries & Dining": (
        "tesco", "walmart", "kroger", "sainsbury", "aldi", "mcdonald", "starbucks",
        "asda", "morrisons", "waitrose", "lidl", "co-op", "co op", "costco", "deliveroo",
        "uber eats", "just eat", "pret", "costa", "greggs", "nando", "wagamama",
        "restaurant", "cafe", "pub", "bakery", "marks spencer food", "whole foods",
        "trader joe", "iceland", "farmfoods", "ocado", "deliveroo", "pizza", "sushi",
    ),
    "Shopping": (
        "amazon", "ebay", "argos", "john lewis", "ikea", "currys", "boots", "next",
        "zara", "h&m", "primark", "etsy", "shein", "temu", "tk maxx", "sports direct",
        "decathlon", "apple store", "ao com", "very", "asos", "uniqlo", "homebase",
    ),
    "Housing": ("rent", "mortgage", "landlord", "service charge", "ground rent"),
    "Utilities": (
        "british gas", "edf", "e.on", "eon energy", "octopus energy", "thames water",
        "council tax", "broadband", "virgin media", "sky", "bt", "vodafone", "o2",
        "ee ", "three", "giffgaff", "tv licence", "water bill", "southern water",
    ),
    "Health & Fitness": (
        "gym", "puregym", "dentist", "pharmacy", "holland barrett", "optician",
        "specsavers", "nhs", "physio", "boots pharmacy", "well pharmacy",
    ),
    "Insurance": (
        "insurance", "aviva", "direct line", "admiral", "axa", "churchill", "hastings",
        "lv=", "saga",
    ),
    "Personal & Care": (
        "barber", "haircut", "salon", "massage", "spa", "laundrette", "dry cleaning",
        "barbers",
    ),
    "Fees & Charges": (
        "interest charge", "overdraft", "atm fee", "service fee", "admin fee",
        "late fee", "penalty", "commission", "fee",
    ),
    "Savings": (
        "savings", "isa", "vanguard", "nutmeg", "moneybox", "premium bonds", "pension",
        "aj bell", "hargreaves",
    ),
    "Income": (
        "salary", "payroll", "wages", "refund", "dividend", "interest paid", "stipend",
        "bonus", "reimbursement", "cashback", "tax rebate", "hmrc",
    ),
    "Transfers": ("transfer", "top up", "internal", "own account"),
}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    kind    TEXT NOT NULL DEFAULT 'expense',
    colour  TEXT NOT NULL DEFAULT '#8A93A6'
);

CREATE TABLE IF NOT EXISTS categorisation_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,
    match_type  TEXT NOT NULL DEFAULT 'contains',
    category_id INTEGER NOT NULL REFERENCES categories(id),
    priority    INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date    TEXT NOT NULL,
    amount      REAL NOT NULL,
    description TEXT NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    is_transfer INTEGER NOT NULL DEFAULT 0,
    is_saving   INTEGER NOT NULL DEFAULT 0,
    is_ignored  INTEGER NOT NULL DEFAULT 0,
    account     TEXT NOT NULL DEFAULT '',
    source_file TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    hash        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_rules_priority ON categorisation_rules(priority);
"""


class Store:
    """All database access for the application."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()
        self._seed()

    def _migrate(self) -> None:
        """Add columns that later versions introduced, without touching data."""
        columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(transactions)")
        }
        if "is_ignored" not in columns:
            try:
                self.backup_now()
            except (sqlite3.Error, OSError):
                pass
            self.conn.execute(
                "ALTER TABLE transactions ADD COLUMN is_ignored INTEGER NOT NULL DEFAULT 0"
            )
            self.conn.execute(
                "INSERT INTO app_settings(key, value) VALUES('schema_version', '2')"
                " ON CONFLICT(key) DO UPDATE SET value = '2'"
            )
            self.conn.commit()

    # -- bootstrap ---------------------------------------------------------

    def _seed(self) -> None:
        existing = self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if not existing:
            self.conn.executemany(
                "INSERT OR IGNORE INTO categories(name, kind, colour) VALUES(?, ?, ?)",
                SEED_CATEGORIES,
            )
        rules = self.conn.execute("SELECT COUNT(*) FROM categorisation_rules").fetchone()[0]
        if not rules:
            stamp = datetime.now().isoformat(timespec="seconds")
            for category, patterns in SEED_RULES.items():
                category_id = self.category_id(category)
                if category_id is None:
                    continue
                self.conn.executemany(
                    "INSERT INTO categorisation_rules"
                    "(pattern, match_type, category_id, priority, created_at)"
                    " VALUES(?, 'contains', ?, 100, ?)",
                    [(pattern, category_id, stamp) for pattern in patterns],
                )
        self.conn.commit()

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO app_settings(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def remove_setting(self, key: str) -> None:
        self.conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        self.conn.commit()

    @property
    def anchor(self) -> int:
        try:
            value = int(self.get_setting("billing_cycle_start_day", "1"))
        except (TypeError, ValueError):
            return 1
        return min(max(value, 1), 31)

    # -- categories --------------------------------------------------------

    def categories(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM categories ORDER BY name COLLATE NOCASE"))

    def category_id(self, name: str) -> int | None:
        row = self.conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        return int(row["id"]) if row is not None else None

    def ensure_category(self, name: str, kind: str = "expense", colour: str = "#8A93A6") -> int:
        clean = name.strip()
        if not clean:
            raise ValueError("Category name cannot be empty")
        existing = self.category_id(clean)
        if existing is not None:
            return existing
        cursor = self.conn.execute(
            "INSERT INTO categories(name, kind, colour) VALUES(?, ?, ?)", (clean, kind, colour)
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    # -- rules -------------------------------------------------------------

    def rules(self) -> list[Rule]:
        rows = self.conn.execute(
            "SELECT * FROM categorisation_rules"
            " ORDER BY priority ASC, LENGTH(pattern) DESC, id DESC"
        )
        return [
            Rule(
                id=int(r["id"]),
                pattern=str(r["pattern"]),
                match_type=str(r["match_type"]),
                category_id=int(r["category_id"]),
                priority=int(r["priority"]),
            )
            for r in rows
        ]

    def add_rule(self, pattern: str, match_type: str, category_id: int, priority: int = 10) -> None:
        clean = pattern.strip()
        if not clean:
            return
        self.conn.execute(
            "INSERT INTO categorisation_rules"
            "(pattern, match_type, category_id, priority, created_at) VALUES(?, ?, ?, ?, ?)",
            (
                clean,
                match_type,
                category_id,
                priority,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()

    def delete_rule(self, rule_id: int) -> None:
        self.conn.execute("DELETE FROM categorisation_rules WHERE id = ?", (rule_id,))
        self.conn.commit()

    def rule_rows(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT r.id, r.pattern, r.match_type, c.name AS category, r.priority"
                " FROM categorisation_rules r"
                " LEFT JOIN categories c ON c.id = r.category_id"
                " ORDER BY r.priority ASC, LENGTH(r.pattern) DESC, r.id DESC"
            )
        )

    # -- transactions ------------------------------------------------------

    def transactions_between(self, start: date, end: date) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM transactions WHERE txn_date BETWEEN ? AND ?"
                " ORDER BY txn_date DESC, id DESC",
                (start.isoformat(), end.isoformat()),
            )
        )

    def all_hashes(self) -> set[str]:
        return {str(row["hash"]) for row in self.conn.execute("SELECT hash FROM transactions")}

    def insert_transactions(self, rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
        added = 0
        skipped = 0
        stamp = datetime.now().isoformat(timespec="seconds")
        for row in rows:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO transactions"
                "(txn_date, amount, description, category_id, is_transfer, is_saving,"
                " account, source_file, imported_at, hash)"
                " VALUES(?, ?, ?, ?, ?, 0, ?, ?, ?, ?)",
                (
                    row["day"].isoformat(),
                    round(float(row["amount"]), 2),
                    row["description"],
                    row.get("category_id"),
                    1 if row.get("is_transfer") else 0,
                    row.get("account", ""),
                    row.get("source_file", ""),
                    stamp,
                    row["hash"],
                ),
            )
            if cursor.rowcount:
                added += 1
            else:
                skipped += 1
        self.conn.commit()
        return added, skipped

    def update_category(self, transaction_id: int, category_id: int) -> int:
        cursor = self.conn.execute(
            "UPDATE transactions SET category_id = ? WHERE id = ?", (category_id, transaction_id)
        )
        self.conn.commit()
        return cursor.rowcount

    def apply_exact_description(self, description: str, category_id: int) -> int:
        cursor = self.conn.execute(
            "UPDATE transactions SET category_id = ? WHERE LOWER(TRIM(description)) = ?",
            (category_id, description.strip().lower()),
        )
        self.conn.commit()
        return cursor.rowcount

    def apply_keyword(self, keyword: str, category_id: int) -> int:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cursor = self.conn.execute(
            "UPDATE transactions SET category_id = ?"
            " WHERE LOWER(description) LIKE ? ESCAPE '\\'",
            (category_id, f"%{escaped.lower()}%"),
        )
        self.conn.commit()
        return cursor.rowcount

    def set_saving(self, transaction_id: int, flagged: bool) -> None:
        self.conn.execute(
            "UPDATE transactions SET is_saving = ? WHERE id = ?",
            (1 if flagged else 0, transaction_id),
        )
        self.conn.commit()

    def set_ignored(self, transaction_id: int, ignored: bool) -> None:
        self.conn.execute(
            "UPDATE transactions SET is_ignored = ? WHERE id = ?",
            (1 if ignored else 0, transaction_id),
        )
        self.conn.commit()

    def statements(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT source_file, COUNT(*) AS rows, MIN(txn_date) AS first_day,"
                " MAX(txn_date) AS last_day FROM transactions"
                " GROUP BY source_file ORDER BY last_day DESC"
            )
        )

    def accounts(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT account, COUNT(*) AS rows, MIN(txn_date) AS first_day,"
                " MAX(txn_date) AS last_day FROM transactions"
                " GROUP BY account ORDER BY account COLLATE NOCASE"
            )
        )

    def rename_account(self, old: str, new: str) -> int:
        """Rename across every transaction. A name already in use merges the two."""
        clean = new.strip()
        if not clean or clean == old:
            return 0
        cursor = self.conn.execute(
            "UPDATE transactions SET account = ? WHERE account = ?", (clean, old)
        )
        self.conn.commit()
        return cursor.rowcount

    def delete_statement(self, source_file: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM transactions WHERE source_file = ?", (source_file,)
        )
        self.conn.commit()
        return cursor.rowcount

    def delete_transaction(self, transaction_id: int) -> None:
        self.conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        self.conn.commit()

    def count_exact(self, description: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE LOWER(TRIM(description)) = ?",
            (description.strip().lower(),),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def count_keyword(self, keyword: str) -> int:
        clean = keyword.strip()
        if not clean:
            return 0
        escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        row = self.conn.execute(
            "SELECT COUNT(*) FROM transactions"
            " WHERE LOWER(description) LIKE ? ESCAPE '\\'",
            (f"%{escaped.lower()}%",),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def mark_transfers(self, ids: Sequence[int], category_id: int | None = None) -> int:
        """Flag existing rows as transfers, optionally moving them to the transfer category."""
        if not ids:
            return 0
        cursor = self.conn.executemany(
            "UPDATE transactions SET is_transfer = 1,"
            " category_id = COALESCE(?, category_id) WHERE id = ?",
            [(category_id, int(item)) for item in ids],
        )
        self.conn.commit()
        return max(cursor.rowcount, 0)

    def reconcile_transfers(self) -> int:
        """Flag equal-and-opposite entries across accounts inside the 48 hour window."""
        rows = self.conn.execute(
            "SELECT id, txn_date, amount, account FROM transactions WHERE is_transfer = 0"
        ).fetchall()
        items = [
            TransferKey(
                key=str(row["id"]),
                day=date.fromisoformat(str(row["txn_date"])),
                amount=float(row["amount"]),
                account=str(row["account"]),
            )
            for row in rows
        ]
        pairs = pair_transfers(items)
        if not pairs:
            return 0
        transfers_id = self.category_id(TRANSFER_CATEGORY)
        self.conn.executemany(
            "UPDATE transactions SET is_transfer = 1,"
            " category_id = COALESCE(?, category_id) WHERE id = ?",
            [(transfers_id, int(key)) for key in pairs],
        )
        self.conn.commit()
        return len(pairs) // 2

    # -- maintenance -------------------------------------------------------

    def backup_now(self) -> Path:
        folder = self.path.parent / BACKUP_DIRNAME
        folder.mkdir(parents=True, exist_ok=True)
        try:
            self.conn.commit()
        except sqlite3.Error:
            pass
        try:
            self.conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:
            pass
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        destination = folder / f"{self.path.stem}_{stamp}.db"
        target = sqlite3.connect(str(destination))
        try:
            self.conn.backup(target)
        finally:
            target.close()
        self._prune_backups(folder)
        return destination

    def _prune_backups(self, folder: Path) -> None:
        try:
            files = sorted(folder.glob("*.db"), key=lambda p: p.name, reverse=True)
            for stale in files[BACKUP_KEEP:]:
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    def last_backup_time(self) -> datetime | None:
        try:
            folder = self.path.parent / BACKUP_DIRNAME
            files = sorted(folder.glob("*.db"), key=lambda p: p.name, reverse=True)
            if not files:
                return None
            return datetime.fromtimestamp(files[0].stat().st_mtime)
        except OSError:
            return None

    def close(self) -> None:
        try:
            self.conn.commit()
        except sqlite3.Error:
            pass
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Statement parsing
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised when a statement cannot be read well enough to import."""


DATE_KEYS = (
    "date",
    "transaction date",
    "posted date",
    "posting date",
    "value date",
    "completed date",
    "date completed",
    "txn date",
    "booking date",
    "process date",
)
AMOUNT_KEYS = (
    "amount",
    "transaction amount",
    "amount gbp",
    "amount in gbp",
    "net amount",
    "total amount",
    "value",
)
DEBIT_KEYS = (
    "debit",
    "debit amount",
    "money out",
    "paid out",
    "withdrawal",
    "withdrawals",
    "amount debited",
)
CREDIT_KEYS = (
    "credit",
    "credit amount",
    "money in",
    "paid in",
    "deposit",
    "deposits",
    "amount credited",
)
DESC_KEYS = (
    "description",
    "transaction description",
    "details",
    "narrative",
    "memo",
    "payee",
    "reference",
    "merchant",
    "particulars",
    "name",
    "transaction",
    "type",
)
DIRECTION_KEYS = ("type", "debit credit", "direction", "payment type", "cr dr")


def normalise_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _header_matches(header: str, key: str) -> bool:
    if not header:
        return False
    return header == key or header.startswith(key + " ") or header.endswith(" " + key) or (
        " " + key + " " in header
    )


def _find_column(
    headers: Sequence[str], keys: Sequence[str], exclude: Iterable[int] = ()
) -> int | None:
    skip = set(exclude)
    for key in keys:
        for index, header in enumerate(headers):
            if index in skip:
                continue
            if header == key:
                return index
    for key in keys:
        for index, header in enumerate(headers):
            if index in skip:
                continue
            if _header_matches(header, key):
                return index
    return None


def map_columns(headers: Sequence[Any]) -> dict[str, int]:
    """Map date, debit, credit, description and direction headers.

    Debit and credit are resolved first so that a generic "amount" match can
    never claim the debit column. That mistake turns every payment positive.
    """
    normalised = [normalise_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for field, keys in (
        ("date", DATE_KEYS),
        ("debit", DEBIT_KEYS),
        ("credit", CREDIT_KEYS),
        ("description", DESC_KEYS),
        ("direction", DIRECTION_KEYS),
    ):
        index = _find_column(normalised, keys)
        if index is not None:
            mapping[field] = index

    split_columns = [index for key, index in mapping.items() if key in ("debit", "credit")]
    index = _find_column(normalised, AMOUNT_KEYS, exclude=split_columns)
    if index is not None:
        mapping["amount"] = index
    return mapping


def _usable_mapping(mapping: dict[str, int]) -> bool:
    has_amount = "amount" in mapping or "debit" in mapping or "credit" in mapping
    return "date" in mapping and "description" in mapping and has_amount


def _find_header_row(table: Sequence[Sequence[Any]]) -> tuple[int | None, dict[str, int]]:
    for index, row in enumerate(table[:20]):
        mapping = map_columns(row)
        if _usable_mapping(mapping):
            return index, mapping
    return None, {}


def _cell(row: Sequence[Any], index: int) -> Any:
    if index < 0 or index >= len(row):
        return ""
    return row[index]


def _positional_mapping(table: Sequence[Sequence[Any]]) -> dict[str, int]:
    """Guess columns when a statement arrives without a header row."""
    sample = [row for row in table[:60] if any(str(c or "").strip() for c in row)]
    if not sample:
        return {}
    width = max(len(row) for row in sample)
    date_score = [0] * width
    amount_score = [0] * width
    text_score = [0] * width
    for row in sample:
        for index in range(width):
            value = _cell(row, index)
            text = str(value or "").strip()
            if not text:
                continue
            if parse_date(value, dayfirst=True) is not None:
                date_score[index] += 1
            elif parse_amount(value) is not None:
                amount_score[index] += 1
            else:
                text_score[index] += min(len(text), 60)

    mapping: dict[str, int] = {}
    if max(date_score) > 0:
        mapping["date"] = date_score.index(max(date_score))
    amount_index = None
    for index, score in enumerate(amount_score):
        if index == mapping.get("date"):
            continue
        if score > 0 and (amount_index is None or score > amount_score[amount_index]):
            amount_index = index
    if amount_index is not None:
        mapping["amount"] = amount_index
    for index, score in sorted(enumerate(text_score), key=lambda pair: pair[1], reverse=True):
        if index in (mapping.get("date"), mapping.get("amount")):
            continue
        if score > 0:
            mapping["description"] = index
            break
    return mapping


def _sign_from_direction(label: str) -> int:
    text = normalise_header(label)
    out_markers = ("debit", " dr", "out", "payment", "withdrawal", "sale", "paid out")
    in_markers = ("credit", " cr", "in", "deposit", "refund", "paid in", "received")
    padded = f" {text} "
    if any(marker in padded for marker in out_markers):
        return -1
    if any(marker in padded for marker in in_markers):
        return 1
    return 0


def _table_to_rows(
    table: Sequence[Sequence[Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    warnings: list[str] = []
    header_index, mapping = _find_header_row(table)
    if not _usable_mapping(mapping):
        mapping = _positional_mapping(table)
        header_index = None
        if not _usable_mapping(mapping):
            raise ParseError(
                "Could not find date, description and amount columns in this file. "
                "Open it in a spreadsheet and check the first rows."
            )

    headers = list(table[header_index]) if header_index is not None else []

    def label(field: str) -> str:
        index = mapping.get(field)
        if index is None:
            return ""
        if headers and index < len(headers):
            text = str(headers[index] or "").strip()
            if text:
                return text
        return f"column {index + 1}"

    body = table[(header_index + 1) if header_index is not None else 0 :]
    date_values = [_cell(row, mapping["date"]) for row in body[:120]] if "date" in mapping else []
    dayfirst = detect_dayfirst(date_values)
    if dayfirst:
        warnings.append("Dates were read day-first (31/12/2026 is 31 December).")

    rows: list[dict[str, Any]] = []
    skipped = 0
    for raw in body:
        if not any(str(value or "").strip() for value in raw):
            continue
        moment = parse_date(_cell(raw, mapping["date"]), dayfirst=dayfirst)
        if moment is None:
            skipped += 1
            continue

        amount: float | None = None
        debit = parse_amount(_cell(raw, mapping["debit"])) if "debit" in mapping else None
        credit = parse_amount(_cell(raw, mapping["credit"])) if "credit" in mapping else None
        debit = abs(debit) if debit is not None else None
        credit = abs(credit) if credit is not None else None
        if debit or credit:
            amount = (credit or 0.0) - (debit or 0.0)
        elif "amount" in mapping:
            amount = parse_amount(_cell(raw, mapping["amount"]))
        if amount is None:
            skipped += 1
            continue
        if abs(amount) < 0.005:
            skipped += 1
            continue

        if "direction" in mapping:
            direction = _sign_from_direction(str(_cell(raw, mapping["direction"]) or ""))
            if direction < 0:
                amount = -abs(amount)
            elif direction > 0:
                amount = abs(amount)

        description = re.sub(r"\s+", " ", str(_cell(raw, mapping["description"]) or "")).strip()
        if not description:
            description = "Statement line"
        if _looks_like_noise(description):
            skipped += 1
            continue
        rows.append({"day": moment, "amount": round(amount, 2), "description": description})

    if skipped:
        warnings.append(f"{skipped} row(s) had no usable date or amount and were ignored.")
    mapping_labels = {
        field: label(field) for field in ("date", "amount", "debit", "credit", "description")
    }
    return rows, mapping_labels, warnings


def _looks_like_noise(description: str) -> bool:
    text = normalise_text(description)
    markers = (
        "balance brought forward",
        "balance carried forward",
        "opening balance",
        "closing balance",
        "total for",
        "statement total",
    )
    return any(marker in text for marker in markers)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


CSV_DELIMITERS = (",", ";", "\t", "|")


def _detect_delimiter(text: str) -> str:
    """Pick the delimiter that splits the header into the most columns.

    The sniffer's quote character is deliberately ignored: bank exports often
    start sort codes with an apostrophe, which makes it guess "'" as the quote
    character and merge every record into the next one.
    """
    header = ""
    for line in text.splitlines():
        if line.strip():
            header = line
            break
    try:
        sniffed = csv.Sniffer().sniff(
            text[:8000], delimiters="".join(CSV_DELIMITERS)
        ).delimiter
    except csv.Error:
        sniffed = ","

    counts: dict[str, int] = {}
    for delimiter in CSV_DELIMITERS:
        try:
            counts[delimiter] = len(
                next(csv.reader([header], delimiter=delimiter, quotechar='"'), [])
            )
        except csv.Error:
            counts[delimiter] = 0
    best = max(counts.values()) if counts else 0
    if best < 3:
        return sniffed
    if counts.get(sniffed, 0) == best:
        return sniffed
    return max(counts, key=lambda key: counts[key])


def parse_csv(path: Path) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    text = _decode(path.read_bytes())
    if not text.strip():
        raise ParseError("That file is empty.")
    delimiter = _detect_delimiter(text)
    table = [
        row
        for row in csv.reader(
            io.StringIO(text), delimiter=delimiter, quotechar='"', doublequote=True
        )
    ]
    return _table_to_rows(table)


def parse_excel(path: Path) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("Excel support is unavailable in this build.") from exc
    try:
        workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(
            "That workbook could not be opened. Older .xls files must be saved as .xlsx first."
        ) from exc
    try:
        sheet = workbook.active
        table = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()
    return _table_to_rows(table)


_DATE_TOKEN = r"(?:\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})"
_AMOUNT_TOKEN = (
    r"\(?-?(?:["
    + CURRENCY
    + r"$"
    + "\u20ac"
    + r"]\s?)?\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?(?:\s?(?:CR|DR))?"
)
_PDF_LINE = re.compile(
    rf"^(?P<date>{_DATE_TOKEN})\s+(?P<desc>.+?)\s+(?P<amount>{_AMOUNT_TOKEN})"
    rf"(?:\s+(?P<balance>{_AMOUNT_TOKEN}))?\s*$"
)


def _pdf_amount(text: str) -> float | None:
    value = parse_amount(text)
    return abs(value) if value is not None else None


def parse_pdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ParseError("PDF support is unavailable in this build.") from exc

    warnings: list[str] = []
    tables: list[list[Any]] = []
    text_lines: list[str] = []
    try:
        with pdfplumber.open(str(path)) as document:
            for page in document.pages:
                text_lines.extend((page.extract_text() or "").splitlines())
                for table in page.extract_tables() or []:
                    for row in table:
                        if row:
                            tables.append(list(row))
    except Exception as exc:
        raise ParseError(f"That PDF could not be read: {exc}") from exc

    if tables:
        has_dates = sum(1 for row in tables for cell in row if parse_date(cell) is not None)
        if has_dates >= 2:
            try:
                rows, mapping, table_warnings = _table_to_rows(tables)
            except ParseError:
                rows = []
            else:
                if rows:
                    warnings.append("Read from a ruled table inside the PDF.")
                    warnings.extend(table_warnings)
                    return rows, mapping, warnings

    if not text_lines:
        raise ParseError("No readable text was found. The PDF may be a scan or an image.")

    year_matches = re.findall(r"\b(20\d{2})\b", "\n".join(text_lines))
    fallback_year = date.today().year
    if year_matches:
        fallback_year = max((int(value) for value in year_matches), key=year_matches.count)

    has_markers = bool(re.search(r"\b(CR|DR)\b", "\n".join(text_lines)))
    rows: list[dict[str, Any]] = []
    for line in text_lines:
        stripped = line.strip()
        if not stripped:
            continue
        match = _PDF_LINE.match(stripped)
        if not match:
            continue
        raw_date = match.group("date")
        moment = parse_date(raw_date, dayfirst=True)
        if moment is None:
            moment = _infer_pdf_date(raw_date, fallback_year)
        if moment is None:
            continue
        amount_token = match.group("amount")
        amount = _pdf_amount(amount_token)
        if amount is None or amount < 0.005:
            continue
        description = re.sub(r"\s+", " ", match.group("desc")).strip()
        if _looks_like_noise(description):
            continue
        sign = -1
        if "CR" in amount_token.upper():
            sign = 1
        elif "DR" in amount_token.upper():
            sign = -1
        elif not has_markers and re.search(
            r"\b(credit|refund|deposit|salary)\b", description, re.I
        ):
            sign = 1
        rows.append({"day": moment, "amount": round(sign * amount, 2), "description": description})

    if not rows:
        raise ParseError(
            "No transaction lines could be recognised in this PDF. "
            "CSV or Excel exports from your bank are far more reliable."
        )
    if not has_markers:
        warnings.append(
            "No debit/credit markers were found, so amounts were treated as money out. "
            "Check the signs in the preview."
        )
    warnings.append("Read from the PDF text layer; verify the preview before adding.")
    mapping = {"date": "text", "amount": "text", "description": "text"}
    return rows, mapping, warnings


def _infer_pdf_date(raw: str, fallback_year: int) -> date | None:
    parts = re.match(r"^(\d{1,2})[/\-.](\d{1,2})$", raw.strip())
    if parts:
        day, month = int(parts.group(1)), int(parts.group(2))
        try:
            moment = date(fallback_year, month, day)
        except ValueError:
            return None
    else:
        moment = parse_date(f"{raw} {fallback_year}", dayfirst=True)
        if moment is None:
            return None
    if moment > date.today() + timedelta(days=60):
        try:
            moment = moment.replace(year=moment.year - 1)
        except ValueError:
            return None
    return moment


@dataclass
class ParsedStatement:
    path: Path
    kind: str
    rows: list[dict[str, Any]]
    mapping: dict[str, str]
    warnings: list[str]


def parse_statement(path: Path) -> ParsedStatement:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        rows, mapping, warnings = parse_csv(path)
        kind = "CSV"
    elif suffix in (".xlsx", ".xlsm"):
        rows, mapping, warnings = parse_excel(path)
        kind = "Excel"
    elif suffix == ".pdf":
        rows, mapping, warnings = parse_pdf(path)
        kind = "PDF"
    elif suffix == ".xls":
        raise ParseError(
            "Save the spreadsheet as .xlsx and try again; the old .xls format is not supported."
        )
    else:
        raise ParseError(f"{suffix or 'That file type'} is not supported. Use CSV, XLSX or PDF.")
    if not rows:
        raise ParseError("No transaction rows were found in that statement.")
    return ParsedStatement(path=path, kind=kind, rows=rows, mapping=mapping, warnings=warnings)


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------


@dataclass
class PreparedRow:
    day: date
    amount: float
    description: str
    category_id: int | None
    category_name: str
    hash: str
    status: str
    account: str
    source_file: str
    auto_category_id: int | None = None
    counterpart_ids: list[int] = dc_field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.status == "Duplicate"

    @property
    def is_transfer(self) -> bool:
        return self.status == "Transfer"

    def as_record(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "amount": self.amount,
            "description": self.description,
            "category_id": self.category_id,
            "is_transfer": self.is_transfer,
            "account": self.account,
            "source_file": self.source_file,
            "hash": self.hash,
        }


def prepare_import(
    store: Store,
    parsed: ParsedStatement,
    account: str,
    known_extra: set[str] | None = None,
) -> list[PreparedRow]:
    """Hash, de-duplicate, categorise and transfer-match every parsed line."""
    rules = store.rules()
    known = store.all_hashes()
    if known_extra:
        known |= known_extra
    uncategorised_id = store.category_id(UNCATEGORISED)
    account_name = account.strip() or parsed.path.stem
    source = parsed.path.name

    prepared: list[PreparedRow] = []
    seen: set[str] = set()
    occurrences: dict[str, int] = {}
    for row in parsed.rows:
        base = transaction_hash(row["day"], row["amount"], row["description"])
        count = occurrences.get(base, 0)
        occurrences[base] = count + 1
        digest = transaction_hash(
            row["day"], row["amount"], row["description"], sequence=count
        )
        duplicate = digest in known or digest in seen
        seen.add(digest)
        category_id = match_category(row["description"], rules)
        if category_id is None:
            category_id = uncategorised_id
        prepared.append(
            PreparedRow(
                day=row["day"],
                amount=round(float(row["amount"]), 2),
                description=row["description"],
                category_id=category_id,
                category_name="",
                hash=digest,
                status="Duplicate" if duplicate else "New",
                account=account_name,
                source_file=source,
                auto_category_id=category_id,
            )
        )

    apply_transfer_matching(store, prepared)
    names = {int(row["id"]): str(row["name"]) for row in store.categories()}
    for row in prepared:
        row.category_name = names.get(int(row.category_id or 0), UNCATEGORISED)
    return prepared


def apply_transfer_matching(store: Store, prepared: Sequence[PreparedRow]) -> None:
    """Flag equal-and-opposite lines across accounts, including other files in the batch."""
    fresh = [row for row in prepared if not row.is_duplicate]
    for row in fresh:
        row.status = "New"
        row.category_id = row.auto_category_id
        row.counterpart_ids.clear()
    if not fresh:
        return

    transfers_id = store.category_id(TRANSFER_CATEGORY)
    days = [row.day for row in fresh]
    window_start = min(days) - timedelta(days=TRANSFER_WINDOW_DAYS)
    window_end = max(days) + timedelta(days=TRANSFER_WINDOW_DAYS)
    existing = store.transactions_between(window_start, window_end)
    candidates = [
        TransferKey(
            key=f"db:{int(row['id'])}",
            day=date.fromisoformat(str(row["txn_date"])),
            amount=float(row["amount"]),
            account=str(row["account"]),
        )
        for row in existing
        if not int(row["is_transfer"])
    ]
    candidates.extend(
        TransferKey(key=f"new:{row.hash}", day=row.day, amount=row.amount, account=row.account)
        for row in fresh
    )
    pairs = pair_transfers(candidates)
    for row in fresh:
        partner = pairs.get(f"new:{row.hash}")
        if not partner:
            continue
        row.status = "Transfer"
        row.category_id = transfers_id or row.category_id
        if partner.startswith("db:"):
            try:
                row.counterpart_ids.append(int(partner.split(":", 1)[1]))
            except ValueError:
                pass


def commit_import(store: Store, rows: Sequence[PreparedRow]) -> tuple[int, int, int]:
    duplicates = sum(1 for row in rows if row.is_duplicate)
    fresh = [row for row in rows if not row.is_duplicate]
    added, skipped = store.insert_transactions([row.as_record() for row in fresh])
    counterparts = sorted({tid for row in fresh for tid in row.counterpart_ids})
    marked = store.mark_transfers(counterparts, store.category_id(TRANSFER_CATEGORY))
    newly_paired = store.reconcile_transfers()
    legs = sum(1 for row in fresh if row.is_transfer) + marked
    return added, duplicates + skipped, newly_paired + legs // 2


def prepare_batch(
    store: Store, sources: Sequence[tuple[ParsedStatement, str]]
) -> list[PreparedRow]:
    """Prepare every file together so duplicates and transfers match across files."""
    prepared: list[PreparedRow] = []
    seen: set[str] = set()
    for parsed, account in sources:
        rows = prepare_import(store, parsed, account, known_extra=seen)
        seen |= {row.hash for row in rows if not row.is_duplicate}
        prepared.extend(rows)
    apply_transfer_matching(store, prepared)
    names = {int(row["id"]): str(row["name"]) for row in store.categories()}
    for row in prepared:
        row.category_name = names.get(int(row.category_id or 0), UNCATEGORISED)
    return prepared


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@dataclass
class CycleSummary:
    start: date
    end: date
    income: float = 0.0
    expenses: float = 0.0
    flagged: float = 0.0
    deposits: int = 0
    payments: int = 0
    flagged_count: int = 0
    transfers: int = 0
    ignored: int = 0

    @property
    def net(self) -> float:
        return self.income - self.expenses

    @property
    def savings_rate(self) -> float:
        return (self.net / self.income * 100.0) if self.income > 0 else 0.0


def summarise(rows: Sequence[sqlite3.Row], start: date, end: date) -> CycleSummary:
    summary = CycleSummary(start=start, end=end)
    for row in rows:
        amount = float(row["amount"])
        if int(row["is_ignored"]):
            summary.ignored += 1
            continue
        if int(row["is_transfer"]):
            summary.transfers += 1
            continue
        if amount > 0:
            summary.income += amount
            summary.deposits += 1
        elif amount < 0:
            summary.expenses += -amount
            summary.payments += 1
            if int(row["is_saving"]):
                summary.flagged += -amount
                summary.flagged_count += 1
    return summary


def category_totals(
    rows: Sequence[sqlite3.Row], names: dict[int, str]
) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for row in rows:
        amount = float(row["amount"])
        if amount >= 0 or int(row["is_transfer"]) or int(row["is_ignored"]):
            continue
        name = names.get(int(row["category_id"] or 0), UNCATEGORISED)
        totals[name] = totals.get(name, 0.0) - amount
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def flagged_breakdown(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        amount = float(row["amount"])
        if amount >= 0 or int(row["is_transfer"]) or int(row["is_ignored"]):
            continue
        if not int(row["is_saving"]):
            continue
        key = str(row["description"])
        entry = grouped.setdefault(key, {"description": key, "total": 0.0, "count": 0})
        entry["total"] += -amount
        entry["count"] += 1
    return sorted(grouped.values(), key=lambda item: item["total"], reverse=True)


_SKIP_WORDS = {
    "the", "and", "for", "from", "payment", "payments", "card", "pos", "visa",
    "mastercard", "debit", "credit", "direct", "transfer", "standing", "order",
    "via", "ref", "reference", "ltd", "limited", "inc", "gbp", "eur", "usd",
}


def suggest_keyword(description: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z&'.-]{2,}", description)
    for word in words:
        if word.lower() not in _SKIP_WORDS:
            return word[:32]
    return re.sub(r"[^A-Za-z0-9 ]", "", description).strip()[:32]


# ---------------------------------------------------------------------------
# Secure key storage
# ---------------------------------------------------------------------------


def _dpapi(raw: bytes, protect: bool) -> bytes | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        buffer = ctypes.create_string_buffer(raw, len(raw))
        blob_in = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DataBlob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if protect:
            ok = crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
            )
        else:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
            )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def save_api_key(store: Store, key: str) -> None:
    clean = key.strip()
    if not clean:
        store.remove_setting("gemini_api_key")
        return
    protected = _dpapi(clean.encode("utf-8"), protect=True)
    if protected is not None:
        store.set_setting("gemini_api_key", "dpapi:" + base64.b64encode(protected).decode())
    else:
        store.set_setting("gemini_api_key", "plain:" + base64.b64encode(clean.encode()).decode())


def load_api_key(store: Store) -> str:
    stored = store.get_setting("gemini_api_key")
    if not stored:
        return ""
    try:
        prefix, _, payload = stored.partition(":")
        raw = base64.b64decode(payload.encode())
        if prefix == "dpapi":
            clear = _dpapi(raw, protect=False)
            return clear.decode("utf-8") if clear else ""
        return raw.decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------


def run_in_thread(
    widget: Any, work: Callable[[], Any], done: Callable[[str, Any], None]
) -> None:
    """Run work() off the UI thread and deliver the outcome on the UI thread."""
    box: queue.Queue[tuple[str, Any]] = queue.Queue()

    def runner() -> None:
        try:
            box.put(("ok", work()))
        except Exception as error:
            box.put(("err", error))

    threading.Thread(target=runner, daemon=True).start()

    def poll() -> None:
        try:
            kind, value = box.get_nowait()
        except queue.Empty:
            widget.after(80, poll)
            return
        done(kind, value)

    widget.after(80, poll)


class StatCard(ctk.CTkFrame):
    def __init__(self, master: Any, caption: str, accent: str = Palette.ACCENT) -> None:
        super().__init__(
            master,
            fg_color=Palette.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=Palette.BORDER,
        )
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=caption.upper(), font=font(9, "bold"),
            text_color=Palette.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 0))
        ctk.CTkFrame(self, height=2, width=24, corner_radius=1, fg_color=accent).grid(
            row=1, column=0, sticky="w", padx=18, pady=(7, 0)
        )
        self.value = ctk.CTkLabel(
            self, text="\u2014", font=font(27, "bold"), text_color=Palette.TEXT, anchor="w"
        )
        self.value.grid(row=2, column=0, sticky="w", padx=18, pady=(10, 0))
        self.detail = ctk.CTkLabel(
            self, text="", font=font(10), text_color=Palette.MUTED, anchor="w"
        )
        self.detail.grid(row=3, column=0, sticky="w", padx=18, pady=(3, 16))

    def set(self, value: str, detail: str = "", colour: str | None = None) -> None:
        self.value.configure(text=value, text_color=colour or Palette.TEXT)
        self.detail.configure(text=detail)


class ChartPanel(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        title: str,
        subtitle: str = "",
        margins: tuple[float, float, float, float] = (0.11, 0.98, 0.90, 0.16),
    ) -> None:
        super().__init__(
            master,
            fg_color=Palette.SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=Palette.BORDER,
        )
        self.margins = margins
        self._axes: Any = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text=title, font=font(12, "bold"), text_color=Palette.TEXT, anchor="w"
        ).grid(row=0, column=0, sticky="w")
        self.hint = ctk.CTkLabel(
            header, text=subtitle, font=font(9), text_color=Palette.FAINT, anchor="e"
        )
        self.hint.grid(row=0, column=1, sticky="e")
        self.figure = Figure(figsize=(4.8, 2.9), dpi=100, facecolor=Palette.SURFACE)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        widget = self.canvas.get_tk_widget()
        widget.configure(background=Palette.SURFACE, highlightthickness=0, bd=0)
        widget.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 10))

    def axes(self) -> Any:
        """Reuse one axes object; clearing it is far cheaper than rebuilding the figure."""
        if self._axes is None:
            self._axes = self.figure.add_subplot(111)
            left, right, top, bottom = self.margins
            self.figure.subplots_adjust(
                left=left, right=right, top=top, bottom=bottom
            )
        plot = self._axes
        plot.clear()
        plot.set_facecolor(Palette.SURFACE)
        for spine in plot.spines.values():
            spine.set_visible(False)
        plot.tick_params(colors=Palette.MUTED, labelsize=9, length=0)
        plot.grid(axis="y", color=Palette.BORDER, linewidth=0.7, alpha=0.8)
        plot.set_axisbelow(True)
        return plot

    def empty(self, message: str) -> None:
        plot = self.axes()
        plot.set_xticks([])
        plot.set_yticks([])
        plot.grid(False)
        plot.text(
            0.5, 0.5, message, ha="center", va="center",
            color=Palette.FAINT, fontsize=10, transform=plot.transAxes,
        )
        self.canvas.draw_idle()

    def redraw(self) -> None:
        self.canvas.draw_idle()


class LedgerTree(ttk.Treeview):
    """Treeview pre-styled for the dark palette."""

    STYLE = "Ledger.Treeview"

    def __init__(self, master: Any, columns: Sequence[tuple[str, str, int, str]]) -> None:
        style = ttk.Style(master)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            self.STYLE,
            background=Palette.SURFACE,
            fieldbackground=Palette.SURFACE,
            foreground=Palette.TEXT,
            rowheight=34,
            borderwidth=0,
            font=font(10),
        )
        style.configure(
            "Ledger.Treeview.Heading",
            background="#F1F4F7",
            foreground=Palette.MUTED,
            relief="flat",
            borderwidth=0,
            font=font(8, "bold"),
            padding=(10, 10),
        )
        style.map(
            "Ledger.Treeview",
            background=[("selected", Palette.SELECT)],
            foreground=[("selected", Palette.TEXT)],
        )
        style.map("Ledger.Treeview.Heading", background=[("active", "#E9EDF2")])

        super().__init__(
            master,
            columns=[column[0] for column in columns],
            show="headings",
            style=self.STYLE,
            selectmode="browse",
        )
        for key, label, width, anchor in columns:
            self.heading(key, text=label, anchor=anchor)
            self.column(key, width=width, minwidth=max(60, width // 2), anchor=anchor,
                        stretch=key == "description")
        for base, background in (("odd", Palette.SURFACE), ("even", "#F7F9FB")):
            self.tag_configure(base, background=background, foreground=Palette.TEXT)
            self.tag_configure(f"{base}_flag", background=background, foreground="#8A5600")
            self.tag_configure(f"{base}_transfer", background=background, foreground=Palette.FAINT)
            self.tag_configure(f"{base}_income", background=background, foreground=Palette.ACCENT)


# ---------------------------------------------------------------------------
# Application shell
# ---------------------------------------------------------------------------

PAGE_TITLES = {
    "dashboard": "Dashboard",
    "ledger": "Transactions",
    "plan": "Plan",
    "insights": "Insights",
    "settings": "Settings",
}


class App(ctk.CTk):
    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store
        self.cycle_anchor = store.anchor
        self.account_scope = store.get_setting("account_scope", "") or None
        today = date.today()
        self.cursor_year = today.year
        self.cursor_month = today.month
        self.current_page = ""
        self.nav_items: dict[str, tuple[ctk.CTkFrame, ctk.CTkButton]] = {}
        self.pages: dict[str, Page] = {}
        self._stale: set[str] = set()
        self._notify_job: str | None = None
        self._editor: Any = None
        self._icon_ref: Any = None
        self.failures: list[str] = []
        self.nano = NanoClient()
        self.local = LocalEngine(self.nano, OllamaClient(), store)

        self.title(APP_NAME)
        self._fit_to_screen()
        self.configure(fg_color=Palette.BG)
        self._set_icon()
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_sidebar()
        self._build_header()
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=1, sticky="nsew", padx=28, pady=(0, 22))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.pages = {
            "dashboard": DashboardPage(self.body, self),
            "ledger": TransactionsPage(self.body, self),
            "plan": PlanPage(self.body, self),
            "insights": InsightsPage(self.body, self),
            "settings": SettingsPage(self.body, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

        self.show_page("dashboard")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-q>", lambda _event: self.close())
        self.notify(f"Database ready at {self.store.path.name}")

    # -- chrome ------------------------------------------------------------

    def _fit_to_screen(self) -> None:
        """Size the window in logical units so it fits the current monitor."""
        try:
            scale = self.winfo_fpixels("1i") / 96.0
        except tk.TclError:
            scale = 1.0
        if scale < 0.5:
            scale = 1.0
        available_width = int((self.winfo_screenwidth() - 80) / scale)
        available_height = int((self.winfo_screenheight() - 150) / scale)
        width = max(1060, min(1320, available_width))
        height = max(660, min(860, available_height))
        self.geometry(f"{width}x{height}")
        self.minsize(min(1080, width), min(660, height))

    def _set_icon(self) -> None:
        path = resource_path("assets/ledger-256.png")
        try:
            from PIL import Image, ImageTk

            image = Image.open(path)
            self._icon_ref = ImageTk.PhotoImage(image)
            self.iconphoto(True, self._icon_ref)
            return
        except Exception:
            pass
        try:
            size = 64
            fallback = tk.PhotoImage(width=size, height=size)
            fallback.put(Palette.ACCENT, to=(0, 0, size, size))
            fallback.put("#FFFFFF", to=(20, 14, 30, 50))
            fallback.put("#FFFFFF", to=(20, 40, 46, 50))
            self.iconphoto(True, fallback)
            self._icon_ref = fallback
        except tk.TclError:
            self._icon_ref = None

    def _build_sidebar(self) -> None:
        self.sidebar = ctk.CTkFrame(
            self, width=228, corner_radius=0, fg_color=Palette.SURFACE,
            border_width=1, border_color=Palette.BORDER,
        )
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(2, weight=1)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=26, pady=(32, 8))
        ctk.CTkFrame(brand, width=3, height=26, corner_radius=2, fg_color=Palette.ACCENT).grid(
            row=0, column=0, rowspan=2, sticky="w", padx=(0, 10)
        )
        ctk.CTkLabel(
            brand, text=APP_NAME, font=font(17, "bold"), text_color=Palette.TEXT, anchor="w"
        ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(
            brand, text="Personal finance", font=font(9), text_color=Palette.FAINT, anchor="w"
        ).grid(row=1, column=1, sticky="w")

        nav = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="new", padx=14, pady=(26, 0))
        nav.grid_columnconfigure(0, weight=1)
        for index, key in enumerate(("dashboard", "ledger", "plan", "insights", "settings")):
            holder = ctk.CTkFrame(nav, fg_color="transparent", height=42)
            holder.grid(row=index, column=0, sticky="ew", pady=2)
            holder.grid_propagate(False)
            holder.grid_columnconfigure(1, weight=1)
            strip = ctk.CTkFrame(
                holder, width=3, height=18, corner_radius=2, fg_color="transparent"
            )
            strip.grid(row=0, column=0, padx=(6, 9))
            button = ctk.CTkButton(
                holder,
                text=PAGE_TITLES[key],
                anchor="w",
                width=170,
                height=38,
                corner_radius=7,
                fg_color="transparent",
                hover_color=Palette.RAISED,
                text_color=Palette.MUTED,
                font=font(11),
                command=lambda page_key=key: self.show_page(page_key),
            )
            button.grid(row=0, column=1, sticky="ew", padx=(0, 6))
            self.nav_items[key] = (strip, button)

        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=26, pady=(0, 26))
        self.backup_label = ctk.CTkLabel(
            footer, text="", font=font(9), text_color=Palette.FAINT,
            anchor="w", justify="left", wraplength=170,
        )
        self.backup_label.pack(anchor="w")

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=1, sticky="ew", padx=28, pady=(28, 16))
        header.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        self.page_title = ctk.CTkLabel(
            titles, text="Dashboard", font=font(22, "bold"), text_color=Palette.TEXT, anchor="w"
        )
        self.page_title.grid(row=0, column=0, sticky="w")
        self.range_label = ctk.CTkLabel(
            titles, text="", font=font(10), text_color=Palette.MUTED, anchor="w"
        )
        self.range_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e")

        self.account_menu = ctk.CTkOptionMenu(
            controls, values=["All accounts"], width=190, height=38, corner_radius=8,
            fg_color=Palette.SURFACE, button_color=Palette.SURFACE,
            button_hover_color=Palette.RAISED, dropdown_fg_color=Palette.SURFACE,
            dropdown_hover_color=Palette.RAISED, dropdown_text_color=Palette.TEXT,
            text_color=Palette.TEXT, font=font(10), dropdown_font=font(10),
            command=self._scope_changed,
        )
        self.account_menu.set("All accounts")
        self.account_menu.grid(row=0, column=0, padx=(0, 12))

        stepper = ctk.CTkFrame(
            controls, fg_color=Palette.SURFACE, corner_radius=8,
            border_width=1, border_color=Palette.BORDER,
        )
        stepper.grid(row=0, column=1, padx=(0, 12))
        ctk.CTkButton(
            stepper, text="\u2039", width=30, height=30, corner_radius=6,
            fg_color="transparent", hover_color=Palette.RAISED,
            text_color=Palette.MUTED, font=font(15),
            command=lambda: self.shift_cycle(-1),
        ).grid(row=0, column=0, padx=(4, 0), pady=4)
        self.cycle_label = ctk.CTkLabel(
            stepper, text="", font=font(10), text_color=Palette.TEXT, width=150
        )
        self.cycle_label.grid(row=0, column=1, padx=4)
        ctk.CTkButton(
            stepper, text="\u203a", width=30, height=30, corner_radius=6,
            fg_color="transparent", hover_color=Palette.RAISED,
            text_color=Palette.MUTED, font=font(15),
            command=lambda: self.shift_cycle(1),
        ).grid(row=0, column=2, padx=(0, 4), pady=4)

        ctk.CTkButton(
            controls, text="Import statement", width=158, height=38, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(11, "bold"),
            command=self.import_statement,
        ).grid(row=0, column=2)

        self.status_label = ctk.CTkLabel(
            self, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.status_label.grid(row=2, column=1, sticky="ew", padx=30, pady=(0, 10))

    # -- state -------------------------------------------------------------

    def cycle(self) -> tuple[date, date]:
        return cycle_bounds(self.cursor_year, self.cursor_month, self.cycle_anchor)

    def transactions(self, start: date, end: date) -> list[sqlite3.Row]:
        """Rows in the window, narrowed to the selected account when one is chosen."""
        rows = self.store.transactions_between(start, end)
        if self.account_scope:
            return [row for row in rows if str(row["account"]) == self.account_scope]
        return rows

    def shift_cycle(self, delta: int) -> None:
        self.cursor_year, self.cursor_month = shift_month(
            self.cursor_year, self.cursor_month, delta
        )
        self.refresh_all()

    def set_anchor(self, day: int) -> None:
        self.cycle_anchor = min(max(int(day), 1), 31)
        self.store.set_setting("billing_cycle_start_day", self.cycle_anchor)
        self.refresh_all()
        start, end = self.cycle()
        self.notify(f"Billing cycle now runs {format_range(start, end)}.")

    def category_map(self) -> dict[int, str]:
        return {int(row["id"]): str(row["name"]) for row in self.store.categories()}

    def category_names(self) -> list[str]:
        names = [str(row["name"]) for row in self.store.categories()]
        names.sort()
        if UNCATEGORISED in names:
            names.remove(UNCATEGORISED)
        return [UNCATEGORISED, *names]

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        for name, page in self.pages.items():
            if name == key:
                page.grid()
            else:
                page.grid_remove()
        for name, (strip, button) in self.nav_items.items():
            active = name == key
            strip.configure(fg_color=Palette.ACCENT if active else "transparent")
            button.configure(
                fg_color=Palette.RAISED if active else "transparent",
                text_color=Palette.TEXT if active else Palette.MUTED,
            )
        self.current_page = key
        self.page_title.configure(text=PAGE_TITLES[key])
        self._refresh_page(self.pages[key])
        self._stale.discard(key)
        self._sync_header()

    def refresh_all(self) -> None:
        for page in self.pages.values():
            self._refresh_page(page)
        self._stale.clear()
        self._sync_header()
        self._sync_backup_label()

    def invalidate(self, *names: str) -> None:
        """Mark pages as stale, refreshing only the one currently on screen."""
        self._stale.update(names)
        if self.current_page in self._stale:
            page = self.pages.get(self.current_page)
            if page is not None:
                self._refresh_page(page)
            self._stale.discard(self.current_page)

    def _refresh_page(self, page: Page) -> None:
        try:
            page.refresh()
        except Exception as error:
            traceback.print_exc()
            self.failures.append(f"{type(page).__name__}: {error}")

    def _sync_header(self) -> None:
        start, end = self.cycle()
        self.cycle_label.configure(text=format_range(start, end))
        today = date.today()
        current = start <= today <= end
        marker = "current cycle" if current else f"opened {format_day(start)}"
        self.range_label.configure(text=f"{format_range(start, end)}  \u00b7  {marker}")

        names = [str(row["account"]) for row in self.store.accounts()]
        if self.account_scope and self.account_scope not in names:
            self.account_scope = None
            self.store.set_setting("account_scope", "")
        self.account_menu.configure(values=["All accounts", *names])
        self.account_menu.set(self.account_scope or "All accounts")
        if len(names) > 1:
            self.account_menu.grid()
        else:
            self.account_menu.grid_remove()

    def _scope_changed(self, value: str) -> None:
        self.account_scope = None if value == "All accounts" else value
        self.store.set_setting("account_scope", self.account_scope or "")
        self.refresh_all()

    def _sync_backup_label(self) -> None:
        moment = self.store.last_backup_time()
        if moment is None:
            text = "No backup yet\nOne is written when you close"
        else:
            text = f"Last backup\n{moment.strftime('%d %b %Y, %H:%M')}"
        self.backup_label.configure(text=text)

    def notify(self, message: str, colour: str | None = None) -> None:
        self.status_label.configure(text=message, text_color=colour or Palette.FAINT)
        if self._notify_job is not None:
            self.after_cancel(self._notify_job)
        self._notify_job = self.after(9000, lambda: self.status_label.configure(text=""))

    # -- import ------------------------------------------------------------

    def import_statement(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Choose one or more statements",
            filetypes=[
                ("Statements", "*.csv *.xlsx *.xlsm *.pdf"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx *.xlsm"),
                ("PDF", "*.pdf"),
                ("All files", "*.*"),
            ],
        )
        if not chosen:
            return
        if isinstance(chosen, str):
            chosen = (chosen,)
        paths = [Path(item) for item in chosen]
        self.notify(
            f"Reading {len(paths)} statement{'s' if len(paths) != 1 else ''}..."
        )
        run_in_thread(self, lambda: self._parse_files(paths), self._parsed_files)

    def _parse_files(self, paths: Sequence[Path]) -> tuple[list[Any], list[str]]:
        sources: list[tuple[ParsedStatement, str]] = []
        problems: list[str] = []
        for path in paths:
            try:
                parsed = parse_statement(path)
            except ParseError as error:
                problems.append(f"{path.name} - {error}")
                continue
            except Exception as error:
                traceback.print_exc()
                problems.append(f"{path.name} - {type(error).__name__}")
                continue
            sources.append((parsed, path.stem))
        return sources, problems

    def _parsed_files(self, kind: str, value: Any) -> None:
        if kind != "ok":
            self.notify(f"Could not read the statements: {value}", Palette.RED)
            return
        sources, problems = value
        self._show_import(sources, problems)

    def _show_import(
        self, sources: list[tuple[ParsedStatement, str]], problems: list[str]
    ) -> None:
        if not sources:
            self.notify(
                problems[0] if problems else "No statements could be read.", Palette.RED
            )
            return
        prepared = prepare_batch(self.store, sources)
        dialog = ImportDialog(self, sources, prepared, problems)
        self.wait_window(dialog)
        if not dialog.committed:
            self.notify("Import cancelled.")
            return
        added, skipped, matches = dialog.result
        self.refresh_all()
        parts = [f"{added} transaction{'s' if added != 1 else ''} added"]
        if skipped:
            parts.append(f"{skipped} duplicate{'s' if skipped != 1 else ''} skipped")
        if matches:
            parts.append(f"{matches} transfer pair{'s' if matches != 1 else ''} matched")
        if len(sources) > 1:
            parts.append(f"{len(sources)} files")
        if problems:
            parts.append(f"{len(problems)} file{'s' if len(problems) != 1 else ''} skipped")
        self.notify("  \u00b7  ".join(parts), Palette.ACCENT)

    # -- shutdown ----------------------------------------------------------

    def close(self) -> None:
        try:
            self.withdraw()
            self.update_idletasks()
        except tk.TclError:
            pass
        try:
            self.local.stop()
        except Exception:
            traceback.print_exc()
        try:
            self.store.backup_now()
        except Exception:
            traceback.print_exc()
        try:
            self.store.close()
        except Exception:
            traceback.print_exc()
        self.destroy()


class Page(ctk.CTkFrame):
    def __init__(self, master: Any, app: App) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.store
        self.build()

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardPage(Page):
    def build(self) -> None:
        self._trend_key: tuple[Any, ...] | None = None
        self._split_key: tuple[Any, ...] | None = None
        self.grid_columnconfigure((0, 1), weight=1, uniform="charts")
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        figures = ctk.CTkFrame(self, fg_color="transparent")
        figures.grid(row=0, column=0, columnspan=2, sticky="ew")
        figures.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")

        self.cards: dict[str, StatCard] = {}
        specs = (
            ("income", "Income", Palette.ACCENT),
            ("expenses", "Spending", Palette.RED),
            ("net", "Net", Palette.BLUE),
            ("flagged", "Potential savings", Palette.FLAG),
        )
        for index, (key, caption, colour) in enumerate(specs):
            card = StatCard(figures, caption, accent=colour)
            card.grid(
                row=0, column=index, sticky="nsew",
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )
            self.cards[key] = card

        self.trend = ChartPanel(self, "Income against spending", "six cycles")
        self.trend.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(16, 0))
        self.split = ChartPanel(
            self, "Where the money goes", "this cycle",
            margins=(0.30, 0.97, 0.90, 0.10),
        )
        self.split.grid(row=1, column=1, sticky="nsew", padx=(7, 0), pady=(16, 0))

        self.recent = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        self.recent.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        self.recent.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(self.recent, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Largest payments", font=font(12, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text="this cycle", font=font(9), text_color=Palette.FAINT, anchor="e"
        ).grid(row=0, column=1, sticky="e")
        self.recent_list = ctk.CTkFrame(self.recent, fg_color="transparent")
        self.recent_list.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))
        self.recent_list.grid_columnconfigure(1, weight=1)
        self.recent_rows: list[tuple[ctk.CTkLabel, ctk.CTkLabel, ctk.CTkLabel]] = []
        for index in range(5):
            description = ctk.CTkLabel(
                self.recent_list, text="", font=font(10),
                text_color=Palette.TEXT, anchor="w",
            )
            category = ctk.CTkLabel(
                self.recent_list, text="", font=font(9),
                text_color=Palette.FAINT, anchor="w",
            )
            amount = ctk.CTkLabel(
                self.recent_list, text="", font=mono(10),
                text_color=Palette.RED, anchor="e",
            )
            description.grid(row=index, column=0, sticky="w", pady=3)
            category.grid(row=index, column=1, sticky="w", padx=(16, 12), pady=3)
            amount.grid(row=index, column=2, sticky="e", pady=3)
            self.recent_rows.append((description, category, amount))
        self.recent_empty = ctk.CTkLabel(
            self.recent_list, text="Nothing recorded in this cycle yet.",
            font=font(10), text_color=Palette.FAINT, anchor="w",
        )
        self.recent_empty.grid(row=0, column=0, columnspan=3, sticky="w")
        self.recent_empty.grid_remove()

    def refresh(self) -> None:
        start, end = self.app.cycle()
        rows = self.app.transactions(start, end)
        summary = summarise(rows, start, end)

        self.cards["income"].set(money(summary.income, 0), f"{summary.deposits} deposits")
        self.cards["expenses"].set(money(summary.expenses, 0), f"{summary.payments} payments")
        self.cards["net"].set(
            money(summary.net, 0),
            f"{summary.savings_rate:.0f}% of income kept",
            Palette.RED if summary.net < 0 else Palette.TEXT,
        )
        self.cards["flagged"].set(
            money(summary.flagged, 0),
            f"{summary.flagged_count} marked cuttable",
            Palette.FLAG if summary.flagged > 0 else Palette.TEXT,
        )

        self._draw_trend()
        self._draw_split(rows)
        self._draw_recent(rows)

    def _draw_trend(self) -> None:
        cycles = previous_cycles(
            self.app.cursor_year, self.app.cursor_month, self.app.cycle_anchor, 6
        )
        rows = self.app.transactions(cycles[0][0], cycles[-1][1])
        labels: list[str] = []
        incomes: list[float] = []
        spends: list[float] = []
        for start, end in cycles:
            subset = [
                row
                for row in rows
                if start.isoformat() <= str(row["txn_date"]) <= end.isoformat()
            ]
            summary = summarise(subset, start, end)
            labels.append(start.strftime("%b") if start.month != 1 else start.strftime("%b %y"))
            incomes.append(summary.income)
            spends.append(summary.expenses)

        if not any(incomes) and not any(spends):
            key = ("empty",)
            if key == self._trend_key:
                return
            self._trend_key = key
            self.trend.empty("No activity in these cycles")
            return

        key = (
            tuple(labels),
            tuple(round(value, 2) for value in incomes),
            tuple(round(value, 2) for value in spends),
        )
        if key == self._trend_key:
            return
        self._trend_key = key

        plot = self.trend.axes()
        positions = list(range(len(labels)))
        width = 0.36
        plot.bar(
            [x - width / 2 for x in positions], incomes, width=width,
            color=Palette.ACCENT, label="Income", zorder=2,
        )
        plot.bar(
            [x + width / 2 for x in positions], spends, width=width,
            color="#C05C52", label="Spending", zorder=2,
        )
        plot.set_xticks(positions)
        plot.set_xticklabels(labels)
        plot.set_xlim(-0.6, len(labels) - 0.4)
        plot.yaxis.set_major_formatter(lambda value, _pos: compact_money(value))
        plot.margins(y=0.18)
        legend = plot.legend(
            frameon=False, loc="upper left", fontsize=9, handlelength=1.1,
            labelcolor=Palette.MUTED, ncols=2,
        )
        for text in legend.get_texts():
            text.set_color(Palette.MUTED)
        self.trend.redraw()

    def _draw_split(self, rows: Sequence[sqlite3.Row]) -> None:
        totals = category_totals(rows, self.app.category_map())
        if not totals:
            key = ("empty",)
            if key == self._split_key:
                return
            self._split_key = key
            self.split.empty("No spending in this cycle")
            return
        top = totals[:8]
        remainder = sum(value for _name, value in totals[8:])
        if remainder > 0.01:
            top.append(("Other", remainder))

        labels = [name for name, _value in top]
        values = [value for _name, value in top]
        key = (tuple(labels), tuple(round(value, 2) for value in values))
        if key == self._split_key:
            return
        self._split_key = key
        colours = [SERIES[index % len(SERIES)] for index in range(len(top))]

        plot = self.split.axes()
        plot.grid(False)
        plot.grid(axis="x", color=Palette.BORDER, linewidth=0.7, alpha=0.7)
        positions = list(range(len(values)))
        plot.barh(positions, values, color=colours, height=0.6, zorder=2)
        plot.set_yticks(positions)
        plot.set_yticklabels(labels, fontsize=9, color=Palette.MUTED)
        plot.invert_yaxis()
        plot.set_xticks([])
        plot.tick_params(axis="y", length=0, pad=2)
        ceiling = max(values) if values else 1.0
        plot.set_xlim(0, ceiling * 1.22)
        for position, value in zip(positions, values, strict=False):
            plot.text(
                value, position, "  " + compact_money(value),
                va="center", ha="left", fontsize=8.5, color=Palette.MUTED,
            )
        self.split.redraw()

    def _draw_recent(self, rows: Sequence[sqlite3.Row]) -> None:
        payments = sorted(
            (
                row for row in rows
                if float(row["amount"]) < 0 and not int(row["is_transfer"])
            ),
            key=lambda row: float(row["amount"]),
        )[:5]
        names = self.app.category_map()
        for index, (description, category, amount) in enumerate(self.recent_rows):
            if index >= len(payments):
                description.grid_remove()
                category.grid_remove()
                amount.grid_remove()
                continue
            row = payments[index]
            description.configure(text=str(row["description"])[:72])
            category.configure(
                text=names.get(int(row["category_id"] or 0), UNCATEGORISED)
            )
            amount.configure(text=money(float(row["amount"])))
            description.grid()
            category.grid()
            amount.grid()
        if payments:
            self.recent_empty.grid_remove()
        else:
            self.recent_empty.grid()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TransactionsPage(Page):
    def build(self) -> None:
        self._rows: list[sqlite3.Row] = []
        self._by_id: dict[int, sqlite3.Row] = {}
        self._search_job: str | None = None
        self.range_hint = "this cycle"

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        toolbar.grid_columnconfigure(1, weight=1)

        self.filter = ctk.CTkSegmentedButton(
            toolbar,
            values=["All", "Expenses", "Income", "Flagged", "Transfers", "Ignored"],
            font=font(10),
            height=32,
            corner_radius=8,
            fg_color=Palette.SURFACE,
            selected_color=Palette.RAISED,
            selected_hover_color=Palette.BORDER,
            unselected_color=Palette.SURFACE,
            unselected_hover_color=Palette.RAISED,
            text_color=Palette.TEXT,
            border_width=1,
            command=lambda _value: self._render(),
        )
        self.filter.set("All")
        self.filter.grid(row=0, column=0, sticky="w")

        self.search = ctk.CTkEntry(
            toolbar,
            placeholder_text="Search description or category",
            width=240,
            height=32,
            corner_radius=8,
            border_width=1,
            fg_color=Palette.SURFACE,
            border_color=Palette.BORDER,
            text_color=Palette.TEXT,
            placeholder_text_color=Palette.FAINT,
            font=font(10),
        )
        self.search.grid(row=0, column=1, sticky="e", padx=(12, 12))
        self.search.bind("<KeyRelease>", self._on_search)

        self.all_time = ctk.CTkSwitch(
            toolbar,
            text="All time",
            font=font(10),
            text_color=Palette.MUTED,
            progress_color=Palette.ACCENT,
            button_color=Palette.SURFACE,
            button_hover_color=Palette.RAISED,
            fg_color=Palette.BORDER,
            command=self.refresh,
        )
        self.all_time.grid(row=0, column=2, sticky="e", padx=(0, 14))

        self.count_label = ctk.CTkLabel(
            toolbar, text="", font=font(9), text_color=Palette.MUTED, anchor="e"
        )
        self.count_label.grid(row=0, column=3, sticky="e")

        self.shell = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        self.shell.grid(row=1, column=0, sticky="nsew")
        self.shell.grid_columnconfigure(0, weight=1)
        self.shell.grid_rowconfigure(0, weight=1)

        self.tree = LedgerTree(
            self.shell,
            columns=(
                ("date", "DATE", 112, "w"),
                ("description", "DESCRIPTION", 380, "w"),
                ("category", "CATEGORY", 200, "w"),
                ("amount", "AMOUNT", 120, "e"),
                ("saving", "POTENTIAL SAVING", 150, "center"),
            ),
        )
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scrollbar = ctk.CTkScrollbar(
            self.shell, command=self.tree.yview, width=12,
            fg_color="transparent", button_color=Palette.SCROLL,
            button_hover_color=Palette.SCROLL_HOVER,
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Button-1>", self._on_click)

        self.footer = ctk.CTkLabel(
            self, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.footer.grid(row=2, column=0, sticky="w", pady=(8, 0))

    # -- data --------------------------------------------------------------

    def refresh(self) -> None:
        if self.all_time.get():
            start, end = date(1900, 1, 1), date(2100, 1, 1)
            self.range_hint = "all time"
        else:
            start, end = self.app.cycle()
            self.range_hint = format_range(start, end)
        self._rows = self.app.transactions(start, end)
        self._by_id = {int(row["id"]): row for row in self._rows}
        self._render()

    def _on_search(self, _event: Any = None) -> None:
        if self._search_job is not None:
            self.after_cancel(self._search_job)
        self._search_job = self.after(220, self._render)

    def _render(self) -> None:
        query = self.search.get().strip().lower()
        mode = self.filter.get()
        names = self.app.category_map()

        visible: list[sqlite3.Row] = []
        for row in self._rows:
            amount = float(row["amount"])
            transfer = bool(int(row["is_transfer"]))
            if mode == "Expenses" and (amount >= 0 or transfer):
                continue
            if mode == "Income" and (amount <= 0 or transfer):
                continue
            if mode == "Flagged" and not int(row["is_saving"]):
                continue
            if mode == "Transfers" and not transfer:
                continue
            if mode == "Ignored" and not int(row["is_ignored"]):
                continue
            if query:
                haystack = (
                    f"{row['description']} {names.get(int(row['category_id'] or 0), '')}"
                ).lower()
                if query not in haystack:
                    continue
            visible.append(row)

        self.tree.delete(*self.tree.get_children())
        shown = visible[:PAGE_LIMIT]
        for index, row in enumerate(shown):
            base = "even" if index % 2 else "odd"
            transfer = bool(int(row["is_transfer"]))
            ignored = bool(int(row["is_ignored"]))
            amount = float(row["amount"])
            if ignored or transfer:
                tag = f"{base}_transfer"
            elif int(row["is_saving"]):
                tag = f"{base}_flag"
            elif amount > 0:
                tag = f"{base}_income"
            else:
                tag = base
            status = "Ignored" if ignored else ("Flagged" if int(row["is_saving"]) else "")
            self.tree.insert(
                "",
                "end",
                iid=str(int(row["id"])),
                values=(
                    date.fromisoformat(str(row["txn_date"])).strftime("%d %b %Y"),
                    str(row["description"])[:120],
                    names.get(int(row["category_id"] or 0), UNCATEGORISED),
                    money(amount, signed=True),
                    status,
                ),
                tags=(tag,),
            )

        counted = [
            r for r in visible
            if not int(r["is_transfer"]) and not int(r["is_ignored"])
        ]
        income = sum(float(r["amount"]) for r in counted if float(r["amount"]) > 0)
        spend = sum(-float(r["amount"]) for r in counted if float(r["amount"]) < 0)
        ignored_count = sum(1 for r in visible if int(r["is_ignored"]))
        summary = (
            f"{len(visible)} transaction{'s' if len(visible) != 1 else ''}"
            f"   \u00b7   {money(spend)} out   \u00b7   {money(income)} in"
        )
        if ignored_count:
            summary += f"   \u00b7   {ignored_count} ignored"
        if len(visible) > PAGE_LIMIT:
            summary += f"   \u00b7   showing the most recent {PAGE_LIMIT}"
        self.footer.configure(text=summary)
        self.count_label.configure(
            text=f"{self.app.store.path.name}  \u00b7  {len(self._rows)} in {self.range_hint}"
        )

    # -- interaction -------------------------------------------------------

    def _on_click(self, event: Any) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        column = self.tree.identify_column(event.x)
        if column == "#3":
            self._edit_row(int(item))
        elif column == "#5":
            self._toggle_flag(int(item))

    def _edit_row(self, transaction_id: int) -> None:
        row = self._by_id.get(transaction_id)
        if row is None:
            return
        names = self.app.category_map()
        current = names.get(int(row["category_id"] or 0), UNCATEGORISED)
        dialog = CategoryDialog(
            self,
            description=str(row["description"]),
            amount=float(row["amount"]),
            categories=self.app.category_names(),
            current=current,
            ignored=bool(int(row["is_ignored"])),
        )
        self.app.wait_window(dialog)
        result = dialog.result
        if result is None:
            return

        category, new_category, ignored = result
        changed = False
        if ignored != bool(int(row["is_ignored"])):
            self.store.set_ignored(transaction_id, ignored)
            changed = True
        if new_category:
            self.store.ensure_category(new_category)
            category = new_category
        if category and category != current:
            self._apply_category_change(transaction_id, row, current, category)
            changed = True
        self.refresh()
        self.app.invalidate("dashboard", "plan", "insights")
        if changed:
            self.app.notify("Transaction updated.", Palette.ACCENT)

    def _apply_category_change(
        self, transaction_id: int, row: sqlite3.Row, current: str, category: str
    ) -> None:
        dialog = RuleDialog(self, str(row["description"]), current, category)
        self.app.wait_window(dialog)
        if dialog.result is None:
            return
        mode, keyword = dialog.result
        category_id = self.store.ensure_category(category)
        if mode == "instance":
            changed = self.store.update_category(transaction_id, category_id)
        elif mode == "exact":
            changed = self.store.apply_exact_description(str(row["description"]), category_id)
            self.store.add_rule(str(row["description"]), "exact", category_id)
        else:
            changed = self.store.apply_keyword(keyword, category_id)
            self.store.add_rule(keyword, "contains", category_id)
        self.app.notify(
            f"{changed} transaction{'s' if changed != 1 else ''} moved to {category}.",
            Palette.ACCENT,
        )

    def _toggle_flag(self, transaction_id: int) -> None:
        row = self._by_id.get(transaction_id)
        if row is None:
            return
        flagged = not bool(int(row["is_saving"]))
        self.store.set_saving(transaction_id, flagged)
        self.refresh()
        self.app.invalidate("dashboard", "plan", "insights")
        self.app.notify(
            "Marked as potential saving."
            if flagged
            else "Removed from potential savings."
        )


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class ImportDialog(ctk.CTkToplevel):
    def __init__(
        self,
        app: App,
        sources: list[tuple[ParsedStatement, str]],
        prepared: list[PreparedRow],
        problems: list[str],
    ) -> None:
        super().__init__(app)
        self.app = app
        self.store = app.store
        self.sources = sources
        self.prepared = prepared
        self.problems = problems
        self.committed = False
        self.result: tuple[int, int, int] = (0, 0, 0)
        self.account_vars = [
            tk.StringVar(value=account) for _parsed, account in sources
        ]

        self.title("Review import")
        self.geometry("1120x780")
        self.minsize(940, 660)
        self.configure(fg_color=Palette.BG)
        self.transient(app)
        self._build()
        self._render()
        self.after(120, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 4))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="Review import", font=font(18, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        lines = sum(len(parsed.rows) for parsed, _account in self.sources)
        file_count = len(self.sources)
        ctk.CTkLabel(
            head,
            text=f"{file_count} file{'s' if file_count != 1 else ''}  \u00b7  {lines} lines",
            font=font(10), text_color=Palette.MUTED, anchor="e",
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            head,
            text="Each file belongs to one account. Nothing is saved until you add them.",
            font=font(9), text_color=Palette.FAINT, anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        files = ctk.CTkScrollableFrame(
            self, height=94, fg_color=Palette.SURFACE, corner_radius=10,
            border_width=1, border_color=Palette.BORDER,
            scrollbar_button_color=Palette.SCROLL,
            scrollbar_button_hover_color=Palette.SCROLL_HOVER,
        )
        files.grid(row=2, column=0, sticky="ew", padx=26, pady=(10, 0))
        files.grid_columnconfigure((0, 1), weight=1, uniform="files")
        for index, ((parsed, _account), var) in enumerate(
            zip(self.sources, self.account_vars, strict=False)
        ):
            row = ctk.CTkFrame(files, fg_color="transparent")
            row.grid(row=index // 2, column=index % 2, sticky="ew", padx=(6, 4), pady=5)
            ctk.CTkLabel(
                row, text=parsed.path.name[:32], font=font(9),
                text_color=Palette.MUTED, anchor="w", width=168,
            ).pack(side="left", padx=(0, 10))
            entry = ctk.CTkEntry(
                row, textvariable=var, width=160, height=28, corner_radius=6,
                fg_color=Palette.BG, border_color=Palette.BORDER, border_width=1,
                text_color=Palette.TEXT, font=font(9),
            )
            entry.pack(side="left")
            entry.bind("<Return>", lambda _event: self._reload())
            entry.bind("<FocusOut>", lambda _event: self._reload())

        notes: list[str] = []
        for problem in self.problems[:2]:
            notes.append(f"Not read: {problem}")
        for parsed, _account in self.sources:
            for warning in parsed.warnings:
                if warning and len(notes) < 4:
                    notes.append(f"{parsed.path.name}: {warning}")
        if not notes:
            notes.append("Columns detected automatically in every file.")
        ctk.CTkLabel(
            self,
            text="\n".join(notes),
            font=font(9),
            text_color=Palette.RED if self.problems else Palette.FLAG
            if len(notes) > 1 or self.sources[0][0].warnings
            else Palette.FAINT,
            anchor="w", justify="left", wraplength=1000,
        ).grid(row=3, column=0, sticky="w", padx=26, pady=(8, 10))

        shell = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        shell.grid(row=4, column=0, sticky="nsew", padx=26)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        self.tree = LedgerTree(
            shell,
            columns=(
                ("date", "DATE", 105, "w"),
                ("description", "DESCRIPTION", 330, "w"),
                ("category", "CATEGORY", 180, "w"),
                ("source", "SOURCE", 110, "w"),
                ("amount", "AMOUNT", 100, "e"),
                ("status", "STATUS", 110, "w"),
            ),
        )
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        bar = ctk.CTkScrollbar(
            shell, command=self.tree.yview, width=12, fg_color="transparent",
            button_color=Palette.SCROLL, button_hover_color=Palette.SCROLL_HOVER,
        )
        bar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.tree.configure(yscrollcommand=bar.set)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=26, pady=(14, 22))
        footer.grid_columnconfigure(0, weight=1)
        self.summary_label = ctk.CTkLabel(
            footer, text="", font=font(10), text_color=Palette.MUTED, anchor="w"
        )
        self.summary_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Cancel", width=96, height=36, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=0, column=1, padx=(0, 10))
        self.confirm_button = ctk.CTkButton(
            footer, text="Add to ledger", width=170, height=36, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"), command=self._confirm,
        )
        self.confirm_button.grid(row=0, column=2)

    def _batch(self) -> list[tuple[ParsedStatement, str]]:
        return [
            (parsed, var.get().strip() or parsed.path.stem)
            for (parsed, _account), var in zip(
                self.sources, self.account_vars, strict=False
            )
        ]

    def _reload(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.prepared = prepare_batch(self.store, self._batch())
        except tk.TclError:
            return
        self._render()

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.prepared[:400]):
            base = "even" if index % 2 else "odd"
            if row.is_duplicate:
                tag = f"{base}_transfer"
            elif row.is_transfer:
                tag = f"{base}_flag"
            else:
                tag = base
            self.tree.insert(
                "",
                "end",
                values=(
                    row.day.strftime("%d %b %Y"),
                    row.description[:110],
                    row.category_name,
                    row.source_file[:18],
                    money(row.amount, signed=True),
                    row.status,
                ),
                tags=(tag,),
            )
        fresh = sum(1 for row in self.prepared if not row.is_duplicate)
        duplicates = sum(1 for row in self.prepared if row.is_duplicate)
        transfers = sum(1 for row in self.prepared if row.is_transfer)
        parts = [f"{fresh} to add"]
        if transfers:
            parts.append(f"{transfers} internal transfer{'s' if transfers != 1 else ''}")
        if duplicates:
            parts.append(f"{duplicates} already imported")
        if len(self.sources) > 1:
            parts.append(f"{len(self.sources)} files")
        if len(self.prepared) > 400:
            parts.append(f"previewing the first 400 of {len(self.prepared)}")
        self.summary_label.configure(text="   \u00b7   ".join(parts))
        self.confirm_button.configure(
            text=f"Add {fresh} transaction{'s' if fresh != 1 else ''}",
            state="normal" if fresh else "disabled",
        )

    def _confirm(self) -> None:
        self.prepared = prepare_batch(self.store, self._batch())
        self.result = commit_import(self.store, self.prepared)
        self.committed = True
        self.destroy()


class CategoryDialog(ctk.CTkToplevel):
    """Pick a category, create one, or ignore the transaction entirely."""

    def __init__(
        self,
        master: Any,
        description: str,
        amount: float,
        categories: Sequence[str],
        current: str,
        ignored: bool,
    ) -> None:
        super().__init__(master)
        self.categories = [name for name in categories if name != UNCATEGORISED]
        self.selected = current
        self.new_category: str | None = None
        self.result: tuple[str | None, str | None, bool] | None = None

        self.title("Change category")
        self.geometry("560x640")
        self.minsize(480, 520)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self._build(description, amount, current, ignored)
        self.after(100, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(
        self, description: str, amount: float, current: str, ignored: bool
    ) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text=description[:60], font=font(13, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text=money(amount, signed=True), font=mono(11),
            text_color=Palette.RED if amount < 0 else Palette.ACCENT, anchor="e",
        ).grid(row=0, column=1, sticky="e")

        self.search = ctk.CTkEntry(
            self, placeholder_text="Search categories",
            height=34, corner_radius=8, border_width=1,
            fg_color=Palette.SURFACE, border_color=Palette.BORDER,
            text_color=Palette.TEXT, placeholder_text_color=Palette.FAINT, font=font(10),
        )
        self.search.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 8))
        self.search.bind("<KeyRelease>", lambda _event: self._fill(self.search.get()))

        self.list = ctk.CTkScrollableFrame(
            self, fg_color=Palette.SURFACE, corner_radius=10,
            border_width=1, border_color=Palette.BORDER,
            scrollbar_button_color=Palette.SCROLL,
            scrollbar_button_hover_color=Palette.SCROLL_HOVER,
        )
        self.list.grid(row=2, column=0, rowspan=2, sticky="nsew", padx=24)
        self.list.grid_columnconfigure(0, weight=1)

        self._fill("", current)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=24, pady=(10, 20))
        bottom.grid_columnconfigure(0, weight=1)

        self.ignore = ctk.CTkCheckBox(
            bottom,
            text="Ignore this transaction — leave it out of every figure",
            font=font(10),
            text_color=Palette.MUTED,
            fg_color=Palette.ACCENT,
            hover_color=Palette.ACCENT_HOVER,
            border_color=Palette.FAINT,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.ignore.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        if ignored:
            self.ignore.select()

        ctk.CTkButton(
            bottom, text="Cancel", width=96, height=36, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=1, column=1, padx=(0, 10))
        ctk.CTkButton(
            bottom, text="Apply", width=110, height=36, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"), command=self._apply,
        ).grid(row=1, column=2)

        self.bind("<Escape>", lambda _event: self.destroy())

    def _fill(self, query: str, current: str | None = None) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        needle = query.strip().lower()
        shown = [name for name in self.categories if needle in name.lower()]
        row = 0
        for name in shown:
            chosen = name == self.selected
            button = ctk.CTkButton(
                self.list,
                text=name,
                anchor="w",
                height=32,
                corner_radius=6,
                fg_color=Palette.RAISED if chosen else "transparent",
                hover_color=Palette.RAISED,
                text_color=Palette.TEXT if chosen else Palette.MUTED,
                font=font(10, "bold" if chosen else "normal"),
                command=lambda value=name: self._select(value),
            )
            button.grid(row=row, column=0, sticky="ew", padx=4, pady=1)
            row += 1

        create = ctk.CTkButton(
            self.list,
            text="+  Create new category",
            anchor="w",
            height=32,
            corner_radius=6,
            fg_color="transparent",
            hover_color=Palette.RAISED,
            text_color=Palette.ACCENT,
            font=font(10),
            command=self._show_create,
        )
        create.grid(row=row, column=0, sticky="ew", padx=4, pady=(6, 2))
        row += 1

        self.create_box = ctk.CTkFrame(self.list, fg_color="transparent")
        self.create_box.grid(row=row, column=0, sticky="ew", padx=4, pady=(0, 6))
        self.create_box.grid_columnconfigure(0, weight=1)
        self.new_entry = ctk.CTkEntry(
            self.create_box, placeholder_text="New category name",
            height=32, corner_radius=6, border_width=1,
            fg_color=Palette.BG, border_color=Palette.BORDER,
            text_color=Palette.TEXT, placeholder_text_color=Palette.FAINT, font=font(10),
        )
        self.new_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.create_box, text="Add", width=64, height=32, corner_radius=6,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER,
            text_color=Palette.TEXT, font=font(10), command=self._create,
        ).grid(row=0, column=1)
        self.create_box.grid_remove()

    def _show_create(self) -> None:
        self.create_box.grid()
        self.new_entry.focus_set()

    def _create(self) -> None:
        name = self.new_entry.get().strip()
        if not name:
            return
        self.new_category = name
        if name not in self.categories:
            self.categories.append(name)
        self.selected = name
        self.search.delete(0, "end")
        self._fill("")

    def _select(self, name: str) -> None:
        self.selected = name
        self.new_category = None
        self._fill(self.search.get())

    def _apply(self) -> None:
        typed = ""
        try:
            if self.create_box.winfo_manager():
                typed = self.new_entry.get().strip()
        except tk.TclError:
            typed = ""
        new_name = typed or self.new_category
        category = new_name or self.selected
        self.result = (category, new_name, bool(self.ignore.get()))
        self.destroy()


class ConfirmDialog(ctk.CTkToplevel):
    """Yes/no confirmation."""

    def __init__(
        self, master: Any, title: str, message: str, confirm: str = "Remove"
    ) -> None:
        super().__init__(master)
        self.result = False
        self.title(title)
        self.geometry("520x260")
        self.resizable(False, False)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=title, font=font(15, "bold"), text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 6))
        ctk.CTkLabel(
            self, text=message, font=font(10), text_color=Palette.MUTED,
            anchor="w", justify="left", wraplength=450,
        ).grid(row=1, column=0, sticky="w", padx=26)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e", padx=26, pady=(20, 22))
        ctk.CTkButton(
            buttons, text="Cancel", width=96, height=34, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            buttons, text=confirm, width=120, height=34, corner_radius=8,
            fg_color=Palette.RED, hover_color="#9C3626",
            text_color="#FFFFFF", font=font(10, "bold"), command=self._yes,
        ).grid(row=0, column=1)
        self.after(100, self._grab)
        self.bind("<Escape>", lambda _event: self.destroy())

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _yes(self) -> None:
        self.result = True
        self.destroy()


class TextPromptDialog(ctk.CTkToplevel):
    """Single-line text prompt."""

    def __init__(
        self, master: Any, title: str, label: str, initial: str = ""
    ) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.title(title)
        self.geometry("520x250")
        self.resizable(False, False)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text=title, font=font(15, "bold"), text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 4))
        ctk.CTkLabel(
            self, text=label, font=font(9), text_color=Palette.MUTED, anchor="w",
            wraplength=450, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=26)
        self.entry = ctk.CTkEntry(
            self, height=36, corner_radius=8, border_width=1,
            fg_color=Palette.SURFACE, border_color=Palette.BORDER,
            text_color=Palette.TEXT, font=font(11),
        )
        self.entry.grid(row=2, column=0, sticky="ew", padx=26, pady=(12, 0))
        self.entry.insert(0, initial)
        self.entry.select_range(0, "end")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="e", padx=26, pady=(16, 20))
        ctk.CTkButton(
            buttons, text="Cancel", width=96, height=34, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            buttons, text="Save", width=110, height=34, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"), command=self._save,
        ).grid(row=0, column=1)

        self.entry.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(100, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def _save(self) -> None:
        self.result = self.entry.get().strip()
        self.destroy()


class RuleDialog(ctk.CTkToplevel):
    def __init__(self, master: Any, description: str, current: str, target: str) -> None:
        super().__init__(master)
        self.store = master.store
        self.description = description
        self.result: tuple[str, str] | None = None
        self.mode = tk.StringVar(value="instance")
        self.keyword = tk.StringVar(value=suggest_keyword(description))

        self.title("Apply category")
        self.geometry("560x430")
        self.resizable(False, False)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self._build(current, target)
        self.after(100, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self, current: str, target: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="Update matching transactions?", font=font(16, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 2))
        ctk.CTkLabel(
            self,
            text=f"Moving this payment from {current} to {target}.",
            font=font(10), text_color=Palette.MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=26)
        ctk.CTkLabel(
            self, text=self.description[:88], font=mono(9),
            text_color=Palette.FAINT, anchor="w", wraplength=500, justify="left",
        ).grid(row=2, column=0, sticky="w", padx=26, pady=(6, 16))

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.grid(row=3, column=0, sticky="ew", padx=26)
        options.grid_columnconfigure(0, weight=1)
        self.count_label = ctk.CTkLabel(
            options, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )

        def option(value: str, title: str, detail: str) -> None:
            row = len(options.grid_slaves()) // 1
            radio = ctk.CTkRadioButton(
                options, text=title, variable=self.mode, value=value,
                font=font(10), text_color=Palette.TEXT,
                fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
                border_color=Palette.FAINT, radiobutton_width=16, radiobutton_height=16,
                command=self._refresh_count,
            )
            radio.grid(row=row, column=0, sticky="w", pady=5)
            ctk.CTkLabel(
                options, text=detail, font=font(9), text_color=Palette.FAINT, anchor="w"
            ).grid(row=row, column=1, sticky="w", padx=(12, 0))

        option("instance", "Only this transaction", "changes nothing else")
        option("exact", "Every transaction with this exact description", "saved as a rule")
        option("keyword", "Every transaction containing a keyword", "saved as a rule")

        self.keyword_entry = ctk.CTkEntry(
            self, textvariable=self.keyword, width=300, height=34, corner_radius=7,
            fg_color=Palette.SURFACE, border_color=Palette.BORDER, border_width=1,
            text_color=Palette.TEXT, font=font(10), placeholder_text="keyword",
        )
        self.keyword_entry.grid(row=4, column=0, sticky="w", padx=26, pady=(10, 4))
        self.keyword_entry.bind("<KeyRelease>", lambda _event: self._refresh_count())

        self.count_label.grid(row=5, column=0, sticky="w", padx=26, pady=(2, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, sticky="e", padx=26, pady=(18, 22))
        ctk.CTkButton(
            buttons, text="Cancel", width=96, height=36, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 10))
        ctk.CTkButton(
            buttons, text="Apply", width=110, height=36, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"), command=self._apply,
        ).grid(row=0, column=1)

        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._apply())
        self._refresh_count()

    def _refresh_count(self) -> None:
        mode = self.mode.get()
        if mode == "instance":
            message = "1 transaction"
        elif mode == "exact":
            message = f"{self.store.count_exact(self.description)} transaction(s)"
        else:
            keyword = self.keyword.get().strip()
            message = (
                f"{self.store.count_keyword(keyword)} transaction(s)"
                if keyword
                else "enter a keyword"
            )
        self.count_label.configure(text=message)
        self.keyword_entry.configure(state="normal" if mode == "keyword" else "disabled")

    def _apply(self) -> None:
        mode = self.mode.get()
        keyword = self.keyword.get().strip()
        if mode == "keyword" and not keyword:
            self.count_label.configure(text="Enter a keyword to match.", text_color=Palette.RED)
            return
        self.result = (mode, keyword)
        self.destroy()


class ConsentDialog(ctk.CTkToplevel):
    """Asks before anything is sent to the hosted model."""

    def __init__(self, master: Any, reason: str, payload: str) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.title("Send to Gemini")
        self.geometry("580x420")
        self.resizable(False, False)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self._build(reason, payload)
        self.after(100, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self, reason: str, payload: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self, text="Send this cycle to Gemini?", font=font(16, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 4))
        ctk.CTkLabel(
            self, text=reason, font=font(10), text_color=Palette.MUTED,
            anchor="w", justify="left", wraplength=500,
        ).grid(row=1, column=0, sticky="w", padx=26)

        box = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=10,
            border_width=1, border_color=Palette.BORDER,
        )
        box.grid(row=2, column=0, sticky="ew", padx=26, pady=(12, 0))
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            box, text="WHAT WOULD BE SENT", font=font(8, "bold"),
            text_color=Palette.FAINT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            box, text=payload, font=font(9), text_color=Palette.MUTED,
            anchor="w", justify="left", wraplength=460,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))

        ctk.CTkLabel(
            self,
            text="Your choice is remembered. You can change it in Settings.",
            font=font(9), text_color=Palette.FAINT, anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=26, pady=(10, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="e", padx=26, pady=(16, 22))
        ctk.CTkButton(
            buttons, text="Cancel", width=90, height=36, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Just this once", width=120, height=36, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=lambda: self._choose("once"),
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Always allow", width=130, height=36, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"),
            command=lambda: self._choose("always"),
        ).grid(row=0, column=2)

        self.bind("<Escape>", lambda _event: self.destroy())

    def _choose(self, choice: str) -> None:
        self.result = choice
        self.destroy()


class RulesWindow(ctk.CTkToplevel):
    def __init__(self, master: Any, app: App) -> None:
        super().__init__(master)
        self.app = app
        self.store = app.store
        self.title("Categorisation rules")
        self.geometry("820x640")
        self.minsize(700, 520)
        self.configure(fg_color=Palette.BG)
        self.transient(master)
        self._build()
        self._load()
        self.after(120, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(
            self, text="Categorisation rules", font=font(17, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=26, pady=(24, 2))
        ctk.CTkLabel(
            self,
            text="Checked top to bottom. Rules you create take priority over the starter set.",
            font=font(9), text_color=Palette.MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=26, pady=(0, 12))

        shell = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        shell.grid(row=2, column=0, sticky="nsew", padx=26)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(0, weight=1)
        self.tree = LedgerTree(
            shell,
            columns=(
                ("pattern", "PATTERN", 320, "w"),
                ("mode", "MATCH", 100, "w"),
                ("category", "CATEGORY", 240, "w"),
            ),
        )
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        bar = ctk.CTkScrollbar(
            shell, command=self.tree.yview, width=12, fg_color="transparent",
            button_color=Palette.SCROLL, button_hover_color=Palette.SCROLL_HOVER,
        )
        bar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.tree.configure(yscrollcommand=bar.set)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=26, pady=(14, 24))
        footer.grid_columnconfigure(0, weight=1)
        self.count_label = ctk.CTkLabel(
            footer, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.count_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Remove selected", width=140, height=34, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self._remove,
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            footer, text="Close", width=96, height=34, corner_radius=8,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER,
            text_color=Palette.TEXT, font=font(10), command=self.destroy,
        ).grid(row=0, column=2)

    def _load(self) -> None:
        self.tree.delete(*self.tree.get_children())
        rows = self.store.rule_rows()
        for index, row in enumerate(rows):
            base = "even" if index % 2 else "odd"
            self.tree.insert(
                "",
                "end",
                iid=str(int(row["id"])),
                values=(
                    str(row["pattern"]),
                    "whole line" if row["match_type"] == "exact" else "contains",
                    str(row["category"] or UNCATEGORISED),
                ),
                tags=(base,),
            )
        self.count_label.configure(text=f"{len(rows)} rules")

    def _remove(self) -> None:
        selected = self.tree.selection()
        if not selected:
            self.count_label.configure(text="Select a rule first.", text_color=Palette.FLAG)
            return
        for item in selected:
            self.store.delete_rule(int(item))
        self._load()
        self.count_label.configure(text="Rule removed.", text_color=Palette.ACCENT)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class PlanPage(Page):
    def build(self) -> None:
        self._projection_key: tuple[Any, ...] | None = None
        self.grid_columnconfigure(0, weight=0, minsize=352)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        form = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            form, text="Goal", font=font(12, "bold"), text_color=Palette.TEXT, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 0))
        ctk.CTkLabel(
            form, text="A target to work towards, and the months you have.",
            font=font(9), text_color=Palette.FAINT, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(3, 14))

        self.target_entry = self._field(
            form, 2, "Target amount", self._setting("goal_target", "12000")
        )
        self.months_entry = self._field(
            form, 3, "Months to save", self._setting("goal_months", "12")
        )
        self.saved_entry = self._field(form, 4, "Already saved", self._setting("goal_saved", "0"))

        ctk.CTkButton(
            form, text="Recalculate", height=36, corner_radius=8,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10, "bold"),
            command=lambda: self._calculate(save=True),
        ).grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=(4, 12))

        cut_box = ctk.CTkFrame(form, fg_color="transparent")
        cut_box.grid(row=15, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 12))
        cut_box.grid_columnconfigure(0, weight=1)
        cut_head = ctk.CTkFrame(cut_box, fg_color="transparent")
        cut_head.grid(row=0, column=0, sticky="ew")
        cut_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            cut_head, text="Cut spending by", font=font(9),
            text_color=Palette.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.cut_label = ctk.CTkLabel(
            cut_head, text="10%", font=font(9, "bold"),
            text_color=Palette.ACCENT, anchor="e",
        )
        self.cut_label.grid(row=0, column=1, sticky="e")
        self.cut_slider = ctk.CTkSlider(
            cut_box,
            from_=1,
            to=20,
            number_of_steps=19,
            height=16,
            corner_radius=8,
            fg_color=Palette.RAISED,
            progress_color=Palette.ACCENT,
            button_color=Palette.ACCENT,
            button_hover_color=Palette.ACCENT_HOVER,
            command=self._cut_changed,
        )
        self.cut_slider.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.cut_slider.set(self._saved_cut())

        ctk.CTkFrame(form, height=1, fg_color=Palette.BORDER).grid(
            row=6, column=0, sticky="ew", padx=20
        )

        ctk.CTkLabel(
            form, text="REQUIRED EACH MONTH", font=font(8, "bold"),
            text_color=Palette.MUTED, anchor="w",
        ).grid(row=7, column=0, sticky="w", padx=20, pady=(18, 0))
        self.required_label = ctk.CTkLabel(
            form, text="\u2014", font=font(26, "bold"), text_color=Palette.ACCENT, anchor="w"
        )
        self.required_label.grid(row=8, column=0, sticky="w", padx=20, pady=(2, 16))
        self.basis_label = ctk.CTkLabel(
            form, text="", font=font(8), text_color=Palette.FAINT, anchor="e",
            justify="right", wraplength=140,
        )
        self.basis_label.grid(row=8, column=1, sticky="se", padx=(0, 20), pady=(2, 16))

        self.metrics: dict[str, ctk.CTkLabel] = {}
        rows = (
            ("net", "Average net per month"),
            ("shortfall", "Shortfall each month"),
            ("months_now", "Months at current rate"),
            ("months_cut", "Months after the cut"),
        )
        for index, (key, caption) in enumerate(rows):
            row = 9 + index
            ctk.CTkLabel(
                form, text=caption, font=font(9), text_color=Palette.MUTED, anchor="w"
            ).grid(row=row, column=0, sticky="w", padx=(20, 8), pady=(6, 0))
            value = ctk.CTkLabel(
                form, text="\u2014", font=font(10, "bold"), text_color=Palette.TEXT, anchor="e"
            )
            value.grid(row=row, column=1, sticky="e", padx=(0, 20), pady=(6, 0))
            self.metrics[key] = value

        ctk.CTkLabel(
            form,
            text="The cut applies to your average monthly spending, not to a single month.",
            font=font(8), text_color=Palette.FAINT, anchor="w",
            wraplength=290, justify="left",
        ).grid(row=14, column=0, columnspan=2, sticky="w", padx=20, pady=(14, 6))

        self.chart = ChartPanel(self, "Projection", "balance against target")
        self.chart.grid(row=0, column=1, sticky="nsew")

        for entry in (self.target_entry, self.months_entry, self.saved_entry):
            entry.bind("<KeyRelease>", lambda _event: self._debounced())

        self._debounce_job: str | None = None
        self.after(200, self.refresh)

    def _setting(self, key: str, default: str) -> str:
        return self.store.get_setting(key, default) or default

    def _field(self, parent: Any, row: int, label: str, value: str) -> ctk.CTkEntry:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 12))
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            box, text=label, font=font(9), text_color=Palette.MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        entry = ctk.CTkEntry(
            box, width=280, height=34, corner_radius=7, border_width=1,
            fg_color=Palette.BG, border_color=Palette.BORDER, text_color=Palette.TEXT,
            font=font(11),
        )
        entry.insert(0, value)
        entry.grid(row=1, column=0, sticky="ew")
        return entry

    def _debounced(self) -> None:
        if self._debounce_job is not None:
            self.after_cancel(self._debounce_job)
        self._debounce_job = self.after(320, lambda: self._calculate(save=True))

    def refresh(self) -> None:
        self._calculate(save=False)

    def _basis(self) -> tuple[float, float, int]:
        """Average net and average spending per completed cycle.

        A single cycle is a bad basis for a projection: one large purchase makes
        the whole plan look impossible. Completed cycles only, so a half-finished
        month does not drag the average down.
        """
        rows = self.app.transactions(date(1900, 1, 1), date(2100, 1, 1))
        buckets: dict[date, list[sqlite3.Row]] = {}
        for row in rows:
            day = date.fromisoformat(str(row["txn_date"]))
            cycle_start, _cycle_end = cycle_containing(day, self.app.cycle_anchor)
            buckets.setdefault(cycle_start, []).append(row)
        if not buckets:
            return 0.0, 0.0, 0

        today = date.today()
        completed = [
            (start, items)
            for start, items in buckets.items()
            if cycle_containing(start, self.app.cycle_anchor)[1] < today
        ]
        if not completed:
            completed = list(buckets.items())

        nets: list[float] = []
        spends: list[float] = []
        for start, items in sorted(completed):
            end = cycle_containing(start, self.app.cycle_anchor)[1]
            cycle_summary = summarise(items, start, end)
            nets.append(cycle_summary.net)
            spends.append(cycle_summary.expenses)
        count = len(nets)
        return sum(nets) / count, sum(spends) / count, count

    def _saved_cut(self) -> int:
        try:
            value = int(float(self.store.get_setting("plan_cut_pct", "10") or 10))
        except ValueError:
            return 10
        return min(max(value, 1), 20)

    def _cut_changed(self, value: float) -> None:
        percent = int(round(value))
        self.cut_label.configure(text=f"{percent}%")
        self.store.set_setting("plan_cut_pct", str(percent))
        self._calculate(save=False)

    def _calculate(self, save: bool) -> None:
        target = parse_amount(self.target_entry.get()) or 0.0
        months = parse_amount(self.months_entry.get()) or 0
        saved = parse_amount(self.saved_entry.get()) or 0.0
        months = int(min(max(months, 0), 240))

        required = (target - saved) / months if months > 0 else 0.0
        net, spending, cycles = self._basis()
        percent = int(round(self.cut_slider.get()))
        cut_gain = spending * percent / 100.0
        projected = net + cut_gain
        shortfall = required - net

        self.basis_label.configure(
            text=(
                f"Average of {cycles} completed cycle{'s' if cycles != 1 else ''}"
                if cycles
                else "No transactions yet"
            )
        )

        self.required_label.configure(
            text=money(max(required, 0.0), decimals=0),
            text_color=Palette.ACCENT if required > 0 else Palette.MUTED,
        )
        self.metrics["net"].configure(
            text=money(net, decimals=0),
            text_color=Palette.RED if net < 0 else Palette.TEXT,
        )
        self.metrics["shortfall"].configure(
            text=money(shortfall, decimals=0) if shortfall > 0 else "on track",
            text_color=Palette.RED if shortfall > 0 else Palette.ACCENT,
        )
        self.metrics["months_now"].configure(text=self._format_months(saved, target, net))
        self.metrics["months_cut"].configure(
            text=self._format_months(saved, target, projected)
        )

        if save:
            self.store.set_setting("goal_target", f"{target:.2f}")
            self.store.set_setting("goal_months", str(months))
            self.store.set_setting("goal_saved", f"{saved:.2f}")

        self._draw_projection(target, saved, months, net, projected, percent)

    def _format_months(self, saved: float, target: float, rate: float) -> str:
        if target <= 0:
            return "\u2014"
        reached = months_to_target(saved, target, rate)
        if reached is None:
            return "not at this rate"
        if reached == 0:
            return "already there"
        if reached >= 120:
            return "over ten years"
        return f"{reached} month{'s' if reached != 1 else ''}"

    def _draw_projection(
        self,
        target: float,
        saved: float,
        months: int,
        net: float,
        projected: float,
        percent: int,
    ) -> None:
        key = (
            round(target, 2),
            round(saved, 2),
            months,
            round(net, 2),
            round(projected, 2),
            percent,
        )
        if key == self._projection_key:
            return
        self._projection_key = key
        if target <= 0:
            self.chart.empty("Set a target to see the projection")
            return
        needed = months_to_target(saved, target, max(projected, 0.01)) or months or 12
        horizon = int(min(max(months, needed, 6), 60))

        plot = self.chart.axes()
        positions = list(range(horizon + 1))
        tracks = (
            ("As things are", net, Palette.MUTED),
            (f"Spending cut by {percent}%", projected, Palette.ACCENT),
        )
        for label, rate, colour in tracks:
            values = [saved + max(rate, 0.0) * index for index in positions]
            plot.plot(positions, values, color=colour, linewidth=1.8, label=label, zorder=2)

        plot.axhline(
            target, color=Palette.FLAG, linewidth=1.0, linestyle=(0, (4, 4)), zorder=1
        )
        plot.text(
            0.5, target, "target", color=Palette.FLAG, fontsize=8.5,
            va="bottom", ha="left",
        )
        plot.set_xlabel("months", color=Palette.MUTED, fontsize=9, labelpad=2)
        plot.yaxis.set_major_formatter(lambda value, _pos: compact_money(value))
        plot.margins(y=0.18, x=0.02)
        plot.set_xlim(0, horizon)
        legend = plot.legend(
            frameon=False, fontsize=8.5, loc="lower right", labelcolor=Palette.MUTED,
        )
        for text in legend.get_texts():
            text.set_color(Palette.MUTED)
        self.chart.redraw()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTION = (
    "You are a financial analyst reviewing one person's monthly spending. "
    "Reply in plain British English, addressing the reader as 'you'. "
    "Use only the figures supplied; never invent a number. "
    "Structure the reply as four short sections with these exact headings: "
    "What stands out, Biggest drivers, Where to cut, One risk. "
    "Each section is two to four sentences or a short bulleted list. "
    "Quote figures in pounds where they strengthen the point. "
    "No introduction, no closing summary, no disclaimers, no emoji."
)


def build_context(
    app: App, rows: Sequence[sqlite3.Row], summary: CycleSummary, start: date, end: date
) -> dict[str, Any]:
    totals = category_totals(rows, app.category_map())
    total_spend = sum(value for _name, value in totals) or 1.0
    categories = [
        {
            "category": name,
            "spent": round(value, 2),
            "share_pct": round(value / total_spend * 100.0, 1),
        }
        for name, value in totals
    ]
    flagged = flagged_breakdown(rows)
    try:
        target = float(app.store.get_setting("goal_target", "0") or 0)
        months = int(float(app.store.get_setting("goal_months", "0") or 0))
        saved = float(app.store.get_setting("goal_saved", "0") or 0)
    except ValueError:
        target, months, saved = 0.0, 0, 0.0

    cycles = previous_cycles(
        app.cursor_year, app.cursor_month, app.cycle_anchor, 6
    )
    trend_rows = app.transactions(cycles[0][0], cycles[-1][1])
    trend: list[dict[str, Any]] = []
    for cycle_start_date, cycle_end_date in cycles:
        subset = [
            row
            for row in trend_rows
            if cycle_start_date.isoformat() <= str(row["txn_date"]) <= cycle_end_date.isoformat()
        ]
        cycle_summary = summarise(subset, cycle_start_date, cycle_end_date)
        trend.append(
            {
                "cycle": cycle_start_date.strftime("%Y-%m"),
                "income": round(cycle_summary.income, 2),
                "spending": round(cycle_summary.expenses, 2),
                "net": round(cycle_summary.net, 2),
            }
        )

    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "anchor_day": app.cycle_anchor,
        },
        "totals": {
            "income": round(summary.income, 2),
            "spending": round(summary.expenses, 2),
            "net": round(summary.net, 2),
            "potential_savings": round(summary.flagged, 2),
        },
        "categories": categories,
        "potential_savings": [
            {
                "description": item["description"][:60],
                "total": round(item["total"], 2),
                "count": item["count"],
            }
            for item in flagged[:12]
        ],
        "goal": {
            "target": round(target, 2),
            "months": months,
            "already_saved": round(saved, 2),
            "required_monthly": round((target - saved) / months, 2) if months else 0.0,
        },
        "trend": trend,
    }


class GeminiClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def stream(self, context: dict[str, Any], sink: Callable[[tuple[str, str]], None]) -> None:
        threading.Thread(target=self._worker, args=(context, sink), daemon=True).start()

    def _worker(self, context: dict[str, Any], sink: Callable[[tuple[str, str]], None]) -> None:
        try:
            import requests
        except ImportError:
            sink(("error", "The HTTP library is missing from this build."))
            return
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": "Here is my spending summary as JSON:\n"
                            + json.dumps(context, indent=2)
                        }
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.35, "maxOutputTokens": 2048},
        }
        try:
            with requests.post(
                GEMINI_ENDPOINT,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json=body,
                stream=True,
                timeout=(10, 90),
            ) as response:
                if response.status_code != 200:
                    sink(("error", self._failure(response)))
                    return
                for raw in response.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"):
                        continue
                    chunk = raw[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if "error" in event:
                        sink(("error", str(event["error"].get("message", "Unknown error"))))
                        return
                    text = self._extract(event)
                    if text:
                        sink(("chunk", text))
            sink(("done", ""))
        except Exception as error:
            sink(("error", f"{type(error).__name__}: {error}"))

    @staticmethod
    def _extract(event: dict[str, Any]) -> str:
        pieces: list[str] = []
        for candidate in event.get("candidates", []) or []:
            for part in candidate.get("content", {}).get("parts", []) or []:
                text = part.get("text")
                if text:
                    pieces.append(str(text))
        return "".join(pieces)

    @staticmethod
    def _failure(response: Any) -> str:
        try:
            detail = response.json().get("error", {}).get("message")
        except Exception:
            detail = ""
        if response.status_code in (400, 401, 403):
            return f"The API key was rejected ({response.status_code}). {detail}".strip()
        if response.status_code == 429:
            return "The API is rate limiting requests. Try again in a minute."
        return f"Request failed ({response.status_code}). {detail}".strip()


# ---------------------------------------------------------------------------
# Tier 0 and tier 1: computed skills, then Gemini Nano inside Chrome
# ---------------------------------------------------------------------------

NANO_PORT = 9333

LOCAL_INSTRUCTION = (
    "You are writing a short review of one month of personal spending for the person it "
    "belongs to. Use only the figures given and never invent one. Write three or four "
    "sentences in plain British English. No heading, no bullets, no introduction, no "
    "closing summary, no disclaimers, no emoji."
)

REASONING_MARKERS = (
    "why",
    "should",
    "compare",
    "plan",
    "recommend",
    "afford",
    "how can",
    "what if",
    "worth",
    "better",
    "reduce",
    "cut",
)


def needs_reasoning(question: str) -> bool:
    """Decide whether a question is too open-ended for the on-device model."""
    text = question.strip().lower()
    if not text:
        return False
    if len(text.split()) > 7:
        return True
    return any(marker in text for marker in REASONING_MARKERS)


def find_chrome() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("google-chrome")
    return Path(found) if found else None


class NanoClient:
    """Drives Chrome's built-in LanguageModel (Gemini Nano) over DevTools."""

    def __init__(
        self, port: int = NANO_PORT, profile: Path | None = None, headless: bool = True
    ) -> None:
        self.port = port
        self.profile = profile or (app_dir() / ".nano-profile")
        self.headless = headless
        self._process: subprocess.Popen[bytes] | None = None
        self._availability: str | None = None
        self._detail = ""
        self._lock = threading.Lock()
        atexit.register(self.stop)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _server_alive(self) -> bool:
        try:
            import requests

            response = requests.get(f"{self.endpoint}/json/version", timeout=1.2)
            return response.status_code == 200
        except Exception:
            return False

    def start(self) -> bool:
        with self._lock:
            if self._server_alive():
                return True
            chrome = find_chrome()
            if chrome is None:
                return False
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                flags = [
                    str(chrome),
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={self.profile}",
                    "--remote-allow-origins=*",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-extensions",
                ]
                if self.headless:
                    flags.append("--headless=new")
                else:
                    flags.extend(
                        ["--window-position=-2400,-2400", "--window-size=900,700"]
                    )
                flags.append("about:blank")
                self._process = subprocess.Popen(
                    flags,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                return False
            for _ in range(30):
                if self._server_alive():
                    return True
                time.sleep(0.3)
            return False

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                process.terminate()
        except OSError:
            pass

    def _evaluate(self, expression: str, timeout: float = 60.0) -> Any:
        import requests
        import websocket

        tab = requests.put(
            f"{self.endpoint}/json/new?{self.endpoint}/json/version", timeout=8
        ).json()
        connection = websocket.create_connection(
            tab["webSocketDebuggerUrl"],
            timeout=timeout,
            origin=f"http://127.0.0.1:{self.port}",
        )
        ready = (
            "(async () => {\n"
            "  for (let attempt = 0; attempt < 50; attempt++) {\n"
            "    if (typeof LanguageModel !== 'undefined') break;\n"
            "    await new Promise((done) => setTimeout(done, 100));\n"
            "  }\n"
            f"  return await ({expression});\n"
            "})()"
        )
        try:
            request = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": ready,
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            }
            connection.send(json.dumps(request))
            deadline = time.time() + timeout
            retries = 0
            while time.time() < deadline:
                message = json.loads(connection.recv())
                if message.get("id") != 1:
                    continue
                if "error" in message:
                    text = str(message["error"].get("message", "Chrome error"))
                    if "Execution context was destroyed" in text and retries < 3:
                        retries += 1
                        time.sleep(0.25)
                        connection.send(json.dumps(request))
                        deadline = time.time() + timeout
                        continue
                    raise RuntimeError(text)
                result = message.get("result", {})
                if result.get("exceptionDetails"):
                    detail = result["exceptionDetails"]
                    text = str(detail.get("text", "Script error"))
                    exception = detail.get("exception") or {}
                    description = exception.get("description")
                    if description:
                        text = str(description).splitlines()[0]
                    raise RuntimeError(text)
                return result.get("result", {}).get("value")
            raise TimeoutError("Chrome did not answer in time")
        finally:
            try:
                connection.close()
            except Exception:
                pass
            try:
                requests.get(f"{self.endpoint}/json/close/{tab['id']}", timeout=4)
            except Exception:
                pass

    def availability(self, force: bool = False) -> str:
        """Return the LanguageModel state without triggering a model download."""
        if self._availability and not force:
            return self._availability
        if not self.start():
            self._availability = "no-chrome"
            return self._availability
        script = (
            "(async () => { try { return await LanguageModel.availability(); }"
            " catch (error) { return 'unavailable: ' + String(error); } })()"
        )
        try:
            value = self._evaluate(script, timeout=30)
        except Exception as error:
            value = f"error: {error}"
        raw = str(value) if value else "unavailable"
        self._detail = raw
        self._availability = raw.split(":")[0].strip() or "unavailable"
        return self._availability

    def detail(self) -> str:
        return getattr(self, "_detail", "")

    def cached_availability(self) -> str:
        return self._availability or ""

    def prepare(self, timeout: float = 900.0) -> str:
        """Trigger the one-time model download without asking a question."""
        if not self.start():
            raise RuntimeError("Chrome could not be started")
        script = (
            "(async () => { try { const session = await LanguageModel.create();"
            " session.destroy(); return 'ready'; }"
            " catch (error) { return 'failed: ' + String(error); } })()"
        )
        value = self._evaluate(script, timeout=timeout)
        self._availability = None
        if str(value) != "ready":
            raise RuntimeError(str(value))
        return "ready"

    def ask(self, instruction: str, question: str, timeout: float = 240.0) -> str:
        if not self.start():
            raise RuntimeError("Chrome could not be started")
        prompt = f"{instruction}\n\n{question}"
        script = (
            "(async () => {\n"
            "  try {\n"
            "    const session = await LanguageModel.create();\n"
            f"    const text = await session.prompt({json.dumps(prompt)});\n"
            "    session.destroy();\n"
            "    return { ok: true, text: text };\n"
            "  } catch (error) {\n"
            "    return { ok: false, text: String(error) };\n"
            "  }\n"
            "})()"
        )
        value = self._evaluate(script, timeout=timeout)
        if not isinstance(value, dict) or not value.get("ok"):
            detail = (value or {}).get("text", "The local model did not answer")
            raise RuntimeError(str(detail))
        return str(value.get("text", "")).strip()


OLLAMA_PREFERENCES = (
    "gemma3:4b",
    "qwen3:4b",
    "qwen3:8b",
    "gemma3:12b",
    "llama3.2:3b",
    "phi4-mini",
    "mistral",
)


def pick_ollama_model(models: Sequence[str]) -> str | None:
    """Choose the smallest sensible installed model for writing short summaries."""
    for wanted in OLLAMA_PREFERENCES:
        for model in models:
            name = model.lower()
            if name == wanted or name == f"{wanted}:latest":
                return model
    for model in models:
        lowered = model.lower()
        if "embed" not in lowered and "vision" not in lowered:
            return model
    return None


class OllamaClient:
    """Talks to a locally running Ollama server over its HTTP API."""

    def __init__(self, host: str | None = None) -> None:
        resolved = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if not resolved.startswith("http"):
            resolved = "http://" + resolved
        self.host = resolved.rstrip("/")

    def probe(self) -> tuple[str, list[str]]:
        """Return (state, installed models) without raising."""
        try:
            import requests

            response = requests.get(f"{self.host}/api/tags", timeout=2.0)
        except Exception:
            return ("not-running", [])
        if response.status_code != 200:
            return ("not-running", [])
        try:
            payload = response.json()
        except ValueError:
            return ("not-running", [])
        models = [
            str(entry.get("name", ""))
            for entry in payload.get("models", [])
            if entry.get("name")
        ]
        return ("ready" if models else "no-model", models)

    def ask(
        self,
        instruction: str,
        prompt: str,
        model: str,
        timeout: float = 240.0,
    ) -> str:
        import requests

        body = {
            "model": model,
            "prompt": f"{instruction}\n\n{prompt}",
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 400},
        }
        response = requests.post(f"{self.host}/api/generate", json=body, timeout=timeout)
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()


@dataclass
class LocalStatus:
    backend: str
    detail: str

    @property
    def ready(self) -> bool:
        return bool(self.backend)


class LocalEngine:
    """Chooses between Ollama and Chrome's on-device model."""

    def __init__(self, nano: NanoClient, ollama: OllamaClient, store: Store) -> None:
        self.nano = nano
        self.ollama = ollama
        self.store = store
        self._cached: LocalStatus | None = None

    def cached(self) -> LocalStatus | None:
        return self._cached

    def invalidate(self) -> None:
        self._cached = None

    def preference(self) -> str:
        return self.store.get_setting("local_preference", "auto") or "auto"

    def resolve(self, force: bool = True, preference: str | None = None) -> LocalStatus:
        if self._cached is not None and not force:
            return self._cached

        preference = preference or self.preference()
        notes: list[str] = []

        if preference in ("auto", "ollama"):
            state, models = self.ollama.probe()
            model = pick_ollama_model(models)
            if state == "ready" and model:
                self._cached = LocalStatus("ollama", model)
                return self._cached
            if state == "not-running":
                notes.append("Ollama is not running.")
            elif state == "no-model":
                notes.append("Ollama is running but has no model installed.")

        if preference in ("auto", "nano"):
            status = self.nano.availability()
            if status == "available":
                self._cached = LocalStatus("nano", "Gemini Nano")
                return self._cached
            if status in ("downloadable", "downloading"):
                notes.append("Chrome's local model needs a one-time download.")
            elif status == "no-chrome":
                notes.append("Chrome was not found.")
            elif status == "unavailable":
                notes.append("Chrome offers no on-device model here.")
            else:
                notes.append("Chrome's local model could not be reached.")

        self._cached = LocalStatus("", " ".join(notes) or "No local model is available.")
        return self._cached

    def ask(
        self, instruction: str, prompt: str, preference: str | None = None
    ) -> tuple[str, str]:
        status = self.resolve(preference=preference)
        if status.backend == "ollama":
            text = self.ollama.ask(instruction, prompt, status.detail)
            return text, f"Local \u00b7 Ollama {status.detail}"
        if status.backend == "nano":
            text = self.nano.ask(instruction, prompt)
            return text, "Local \u00b7 Gemini Nano"
        raise RuntimeError(status.detail)

    def stop(self) -> None:
        self.nano.stop()


@dataclass
class Finding:
    title: str
    body: str
    tone: str = "info"


def period_records(
    store: Store,
    names: dict[int, str],
    start: date,
    end: date,
    account: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in store.transactions_between(start, end):
        if int(row["is_transfer"]) or int(row["is_ignored"]):
            continue
        if account and str(row["account"]) != account:
            continue
        records.append(
            {
                "day": date.fromisoformat(str(row["txn_date"])),
                "amount": float(row["amount"]),
                "description": str(row["description"]),
                "category": names.get(int(row["category_id"] or 0), UNCATEGORISED),
            }
        )
    return records


def category_spend(records: Sequence[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for record in records:
        amount = float(record["amount"])
        if amount < 0:
            category = str(record["category"])
            totals[category] = totals.get(category, 0.0) - amount
    return totals


def skill_spikes(
    current: dict[str, float], history: Sequence[dict[str, float]]
) -> list[Finding]:
    findings: list[Finding] = []
    if not history:
        return findings
    for category, value in current.items():
        past = [period.get(category, 0.0) for period in history]
        baseline = sum(past) / len(past)
        if baseline < 25 or value < 25:
            continue
        change = value - baseline
        if change >= max(25.0, baseline * 0.25):
            findings.append(
                Finding(
                    f"{category} is up {change / baseline * 100:.0f}%",
                    f"{money(value, 0)} this cycle against an average of {money(baseline, 0)} "
                    f"across the previous {len(history)} cycles.",
                    "warn",
                )
            )
    findings.sort(key=lambda finding: finding.body, reverse=True)
    return findings[:2]


def skill_duplicates(records: Sequence[dict[str, Any]]) -> list[Finding]:
    buckets: dict[tuple[str, float], list[date]] = {}
    for record in records:
        if float(record["amount"]) >= 0:
            continue
        key = (normalise_text(record["description"]), round(abs(float(record["amount"])), 2))
        buckets.setdefault(key, []).append(record["day"])
    findings: list[Finding] = []
    for (description, amount), days in buckets.items():
        days = sorted(days)
        for first, second in zip(days, days[1:], strict=False):
            gap = (second - first).days
            if gap <= 3:
                findings.append(
                    Finding(
                        "Possible double charge",
                        f"{description.title()[:60]} at {money(amount)} appears twice within "
                        f"{gap} day{'s' if gap != 1 else ''}.",
                        "warn",
                    )
                )
                break
    return findings[:2]


def skill_creep(periods: Sequence[Sequence[dict[str, Any]]]) -> list[Finding]:
    if len(periods) < 2:
        return []
    counts: dict[tuple[str, float], int] = {}
    for period in periods:
        keys = {
            (normalise_text(record["description"]), round(abs(float(record["amount"])), 2))
            for record in period
            if float(record["amount"]) < 0
        }
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    repeats = [
        (key, count) for key, count in counts.items() if count >= len(periods)
    ]
    repeats.sort(key=lambda item: -item[0][1])
    findings: list[Finding] = []
    for (description, amount), _count in repeats[:2]:
        findings.append(
            Finding(
                "Repeating charge",
                f"{description.title()[:60]} at {money(amount)} has been charged in each of the "
                f"last {len(periods)} cycles, {money(amount * len(periods))} in total.",
                "info",
            )
        )
    return findings


def skill_movers(
    current: dict[str, float], previous: dict[str, float]
) -> list[Finding]:
    changes: list[tuple[float, str, float]] = []
    for category in set(current) | set(previous):
        delta = current.get(category, 0.0) - previous.get(category, 0.0)
        if abs(delta) >= 30:
            changes.append((abs(delta), category, delta))
    changes.sort(reverse=True)
    findings: list[Finding] = []
    for _size, category, delta in changes[:2]:
        direction = "up" if delta > 0 else "down"
        findings.append(
            Finding(
                f"{category} {direction} {money(abs(delta), 0)}",
                f"{money(current.get(category, 0.0), 0)} this cycle against "
                f"{money(previous.get(category, 0.0), 0)} in the one before.",
                "warn" if delta > 0 else "good",
            )
        )
    return findings


def skill_velocity(summary: CycleSummary, goal: tuple[float, int, float]) -> list[Finding]:
    target, months, saved = goal
    if target <= 0 or months <= 0:
        return []
    required = max((target - saved) / months, 0.0)
    net = summary.net
    if net >= required:
        return [
            Finding(
                "On track for the target",
                f"You are keeping {money(net, 0)} a month against the {money(required, 0)} "
                f"the goal needs.",
                "good",
            )
        ]
    return [
        Finding(
            "The goal needs more each month",
            f"The target needs {money(required, 0)} a month and you are keeping {money(net, 0)}. "
            f"The gap is {money(required - net, 0)}.",
            "warn",
        )
    ]


def run_skills(
    periods: Sequence[Sequence[dict[str, Any]]],
    summary: CycleSummary,
    goal: tuple[float, int, float],
) -> list[Finding]:
    if not periods:
        return []
    current = category_spend(periods[-1])
    history = [category_spend(period) for period in periods[:-1]]
    findings: list[Finding] = []
    findings.extend(skill_spikes(current, history))
    findings.extend(skill_duplicates(periods[-1]))
    if len(periods) >= 2:
        findings.extend(skill_creep(periods))
    if history:
        findings.extend(skill_movers(current, history[-1]))
    findings.extend(skill_velocity(summary, goal))
    order = {"warn": 0, "good": 1, "info": 2}
    findings.sort(key=lambda finding: order.get(finding.tone, 3))
    return findings[:3]


def findings_text(findings: Sequence[Finding]) -> str:
    return "\n".join(f"- {finding.title}: {finding.body}" for finding in findings)


class InsightsRouter:
    """Chooses between the on-device model and the hosted model."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def consent(self) -> str:
        return self.store.get_setting("cloud_consent", "ask") or "ask"

    def tier(self, question: str) -> str:
        if self.consent == "never":
            return "local"
        return "cloud" if needs_reasoning(question) else "local"


class InsightsPage(Page):
    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._queue: queue.Queue[tuple[str, str]] | None = None
        self._streaming = False
        self._local_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._local_question = ""
        self.router = InsightsRouter(self.store)
        self.findings: list[Finding] = []

        ask = ctk.CTkFrame(self, fg_color="transparent")
        ask.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ask.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            ask, text="Ask", font=font(9, "bold"), text_color=Palette.MUTED, anchor="w"
        ).grid(row=0, column=0, sticky="w", padx=(2, 12))
        self.question = ctk.CTkEntry(
            ask,
            placeholder_text="Leave this blank for a summary, or ask something specific",
            height=38, corner_radius=8, border_width=1,
            fg_color=Palette.SURFACE, border_color=Palette.BORDER,
            text_color=Palette.TEXT, placeholder_text_color=Palette.FAINT, font=font(10),
        )
        self.question.grid(row=0, column=1, sticky="ew")
        self.question.bind("<KeyRelease>", lambda _event: self._sync_meta())
        self.question.bind("<Return>", lambda _event: self.generate())
        self.run_button = ctk.CTkButton(
            ask, text="Answer", width=110, height=38, corner_radius=8,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(10, "bold"), command=self.generate,
        )
        self.run_button.grid(row=0, column=2, padx=(10, 0))

        self.meta = ctk.CTkLabel(
            self, text="", font=font(9), text_color=Palette.FAINT, anchor="w",
            justify="left", wraplength=900,
        )
        self.meta.grid(row=1, column=0, sticky="w", padx=2, pady=(0, 12))

        card = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        card.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="What the numbers say", font=font(11, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text="calculated here, no model involved", font=font(9),
            text_color=Palette.FAINT, anchor="e",
        ).grid(row=0, column=1, sticky="e")
        self.findings_box = ctk.CTkFrame(card, fg_color="transparent")
        self.findings_box.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 14))
        self.findings_box.grid_columnconfigure(1, weight=1)

        shell = ctk.CTkFrame(
            self, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        shell.grid(row=3, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)
        answer_head = ctk.CTkFrame(shell, fg_color="transparent")
        answer_head.grid(row=0, column=0, sticky="ew", padx=22, pady=(14, 0))
        answer_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            answer_head, text="Written review", font=font(11, "bold"),
            text_color=Palette.TEXT, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.source_label = ctk.CTkLabel(
            answer_head, text="nothing generated yet", font=font(9),
            text_color=Palette.FAINT, anchor="e",
        )
        self.source_label.grid(row=0, column=1, sticky="e")

        self.textbox = ctk.CTkTextbox(
            shell, fg_color=Palette.SURFACE, text_color=Palette.TEXT,
            font=font(11), wrap="word", corner_radius=0, border_width=0,
            scrollbar_button_color=Palette.SCROLL,
            scrollbar_button_hover_color=Palette.SCROLL_HOVER,
        )
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=22, pady=(8, 16))
        self._write(
            "The three findings above are worked out on this machine from your own figures.\n\n"
            "Press Answer and the simplest explanation comes from the local model inside Chrome. "
            "Anything that needs real reasoning goes to Gemini, and only after you agree to it."
        )

    # -- state -------------------------------------------------------------

    def _goal(self) -> tuple[float, int, float]:
        try:
            return (
                float(self.store.get_setting("goal_target", "0") or 0),
                int(float(self.store.get_setting("goal_months", "0") or 0)),
                float(self.store.get_setting("goal_saved", "0") or 0),
            )
        except ValueError:
            return (0.0, 0, 0.0)

    def _periods(self) -> list[list[dict[str, Any]]]:
        names = self.app.category_map()
        cycles = previous_cycles(
            self.app.cursor_year, self.app.cursor_month, self.app.cycle_anchor, 4
        )
        return [
            period_records(self.store, names, start, end, self.app.account_scope)
            for start, end in cycles
        ]

    def _context_payload(self, question: str = "") -> dict[str, Any]:
        start, end = self.app.cycle()
        rows = self.app.transactions(start, end)
        summary = summarise(rows, start, end)
        payload = build_context(self.app, rows, summary, start, end)
        payload["question"] = question or (
            "Summarise this cycle and say where the money could go further."
        )
        payload["computed_findings"] = [
            {"title": finding.title, "detail": finding.body} for finding in self.findings
        ]
        return payload

    def refresh(self) -> None:
        start, end = self.app.cycle()
        rows = self.app.transactions(start, end)
        summary = summarise(rows, start, end)
        self.findings = run_skills(self._periods(), summary, self._goal())
        self._render_findings()
        self._sync_meta()

    def _render_findings(self) -> None:
        for child in self.findings_box.winfo_children():
            child.destroy()
        if not self.findings:
            ctk.CTkLabel(
                self.findings_box, text="Nothing unusual in this cycle.",
                font=font(10), text_color=Palette.FAINT, anchor="w",
            ).grid(row=0, column=1, sticky="w")
            return
        tones = {"warn": Palette.FLAG, "good": Palette.ACCENT, "info": Palette.BLUE}
        for index, finding in enumerate(self.findings):
            ctk.CTkFrame(
                self.findings_box, width=3, height=26, corner_radius=2,
                fg_color=tones.get(finding.tone, Palette.MUTED),
            ).grid(row=index * 2, column=0, rowspan=2, sticky="n", padx=(0, 12), pady=(4, 0))
            ctk.CTkLabel(
                self.findings_box, text=finding.title, font=font(10, "bold"),
                text_color=Palette.TEXT, anchor="w",
            ).grid(row=index * 2, column=1, sticky="w")
            ctk.CTkLabel(
                self.findings_box, text=finding.body, font=font(9),
                text_color=Palette.MUTED, anchor="w", justify="left", wraplength=820,
            ).grid(row=index * 2 + 1, column=1, sticky="w", pady=(0, 6))

    def _sync_meta(self) -> None:
        tier = self.router.tier(self.question.get())
        has_key = bool(load_api_key(self.store))
        if tier == "cloud":
            state = (
                "Gemini 3.5 Flash-Lite will answer, after you confirm what is sent."
                if has_key
                else "This question needs Gemini 3.5 Flash-Lite, and no API key is saved yet."
            )
        else:
            state = "A short answer runs on this machine."
        cached = self.app.local.cached()
        if cached is None:
            local_text = "Local model is checked on first use."
        elif cached.ready:
            label = "Ollama " + cached.detail if cached.backend == "ollama" else "Gemini Nano"
            local_text = f"Local model ready: {label}."
        else:
            local_text = cached.detail
        self.meta.configure(text=f"{state}  {local_text}")

    # -- running -----------------------------------------------------------

    def _write(self, text: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        if text:
            self.textbox.insert("1.0", text)
        self.textbox.configure(state="disabled")

    def _append(self, text: str) -> None:
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def _begin(self, message: str) -> None:
        self._streaming = True
        self.run_button.configure(state="disabled", text="Working...")
        self._write("")
        self.source_label.configure(text=message, text_color=Palette.MUTED)

    def _reset(self) -> None:
        self._streaming = False
        self.run_button.configure(state="normal", text="Answer")

    def _finish(self, text: str, source: str, error: bool = False) -> None:
        self._write(text)
        self.source_label.configure(
            text=source, text_color=Palette.RED if error else Palette.FAINT
        )
        self._reset()

    def generate(self) -> None:
        if self._streaming:
            return
        question = self.question.get().strip()
        if self.router.tier(question) == "cloud":
            self._start_cloud(question)
        else:
            self._start_local(question)

    def _start_local(self, question: str) -> None:
        self._begin("Checking the local model...")
        self._local_question = question
        self._local_queue = queue.Queue()
        payload = self._context_payload(question)
        preference = self.app.local.preference()
        threading.Thread(
            target=self._local_task,
            args=(payload, preference, self._local_queue.put),
            daemon=True,
        ).start()
        self.after(120, self._poll_local)

    def _local_task(
        self,
        payload: dict[str, Any],
        preference: str,
        sink: Callable[[tuple[str, Any]], None],
    ) -> None:
        status = self.app.local.resolve(preference=preference)
        if not status.ready:
            sink(("unavailable", status.detail))
            return
        sink(("stage", "Analysing locally..."))
        prompt = (
            f"Computed findings:\n{findings_text(self.findings)}\n\n"
            f"Totals:\n{json.dumps(payload['totals'], indent=2)}\n\n"
            f"Categories:\n{json.dumps(payload['categories'][:8], indent=2)}"
        )
        question = str(payload.get("question", "")).strip()
        if question and not question.startswith("Summarise this cycle"):
            prompt += f"\n\nQuestion: {question}"
        try:
            text, source = self.app.local.ask(
                LOCAL_INSTRUCTION, prompt, preference=preference
            )
        except Exception as error:
            sink(("failed", f"{type(error).__name__}: {error}"))
            return
        sink(("result", (text, source)))

    def _poll_local(self) -> None:
        finished = False
        while True:
            try:
                kind, payload = self._local_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "stage":
                self.source_label.configure(text=str(payload), text_color=Palette.MUTED)
            elif kind == "unavailable":
                finished = True
                if self.router.consent != "never" and self._confirm_cloud(str(payload)):
                    self._execute_cloud(self._local_question)
                    return
                self._finish(str(payload), "no local model")
            elif kind == "failed":
                finished = True
                self._finish(
                    f"The local model could not answer.\n\n{payload}",
                    "local model failed",
                    error=True,
                )
            elif kind == "result":
                finished = True
                text, source = payload
                self._finish(text, source)
        if not finished:
            self.after(120, self._poll_local)

    def _confirm_cloud(self, reason: str) -> bool:
        dialog = ConsentDialog(self.app, reason, self._payload_summary())
        self.app.wait_window(dialog)
        choice = dialog.result
        if choice is None:
            self._reset()
            return False
        if choice == "always":
            self.store.set_setting("cloud_consent", "always")
        return True

    def _payload_summary(self) -> str:
        payload = self._context_payload()
        categories = len(payload["categories"])
        flagged = len(payload["potential_savings"])
        return (
            f"Income and spending totals for the cycle, {categories} category figure"
            f"{'s' if categories != 1 else ''}, {flagged} flagged payment"
            f"{'s' if flagged != 1 else ''}, and six cycles of totals. "
            "No account numbers and no transaction list beyond that."
        )

    def _start_cloud(self, question: str) -> None:
        if not load_api_key(self.store):
            self._finish(
                "No Gemini API key is saved, so the deeper analysis is unavailable.\n\n"
                "Add a key in Settings, or ask a shorter question the local model can handle.",
                "not run",
            )
            return
        if self.router.consent == "ask":
            if not self._confirm_cloud("A deeper question was asked."):
                return
        self._execute_cloud(question)

    def _execute_cloud(self, question: str) -> None:
        api_key = load_api_key(self.store)
        if not api_key:
            self._finish("No Gemini API key is saved.", "not run")
            return
        payload = self._context_payload(question)
        self._begin(f"Sending to {GEMINI_MODEL}...")
        self.source_label.configure(
            text=f"Cloud \u00b7 {GEMINI_MODEL}", text_color=Palette.MUTED
        )
        self._queue = queue.Queue()
        self._streaming = True
        GeminiClient(api_key).stream(payload, self._queue.put)
        self.after(90, self._poll)

    def _poll(self) -> None:
        if self._queue is None:
            return
        finished = False
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "chunk":
                self._append(payload)
            elif kind == "error":
                self._append(f"\n\nCould not complete the analysis.\n\n{payload}")
                finished = True
            elif kind == "done":
                finished = True
        if finished:
            self._reset()
            return
        self.after(90, self._poll)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsPage(Page):
    def build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=Palette.SCROLL,
            scrollbar_button_hover_color=Palette.SCROLL_HOVER,
        )
        scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))

        columns = ctk.CTkFrame(scroll, fg_color="transparent")
        columns.pack(fill="x")
        columns.grid_columnconfigure((0, 1), weight=1, uniform="settings")
        left = ctk.CTkFrame(columns, fg_color="transparent")
        left.grid(row=0, column=0, sticky="new", padx=(0, 6))
        right = ctk.CTkFrame(columns, fg_color="transparent")
        right.grid(row=0, column=1, sticky="new", padx=(6, 0))

        # Billing cycle --------------------------------------------------
        card = self._card(
            left,
            "Billing cycle",
            "Where each statement period begins. Everything in the app follows this date.",
        )
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            row, text="Start on day", font=font(10), text_color=Palette.MUTED, anchor="w"
        ).pack(side="left", padx=(0, 12))
        self.anchor_menu = ctk.CTkOptionMenu(
            row, values=[str(day) for day in range(1, 32)], width=84, height=32,
            corner_radius=7, fg_color=Palette.RAISED, button_color=Palette.RAISED,
            button_hover_color=Palette.BORDER, dropdown_fg_color=Palette.RAISED,
            dropdown_hover_color=Palette.BORDER, dropdown_text_color=Palette.TEXT,
            text_color=Palette.TEXT, font=font(10), dropdown_font=font(10),
            command=self._anchor_changed,
        )
        self.anchor_menu.set(str(self.app.cycle_anchor))
        self.anchor_menu.pack(side="left")
        self.anchor_note = ctk.CTkLabel(
            card, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.anchor_note.grid(row=3, column=0, columnspan=3, sticky="w", padx=20, pady=(4, 18))

        # Categories -----------------------------------------------------
        card = self._card(
            left, "Categories", "Used by the import rules and the spending breakdown."
        )
        self.category_list = ctk.CTkFrame(card, fg_color="transparent")
        self.category_list.grid(row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 6))
        self.category_list.grid_columnconfigure((0, 1), weight=1)

        add_row = ctk.CTkFrame(card, fg_color="transparent")
        add_row.grid(row=3, column=0, columnspan=3, sticky="ew", padx=20, pady=(8, 18))
        add_row.grid_columnconfigure(0, weight=1)
        self.new_category = ctk.CTkEntry(
            add_row, placeholder_text="New category", height=32, corner_radius=7,
            border_width=1, fg_color=Palette.BG, border_color=Palette.BORDER,
            text_color=Palette.TEXT, placeholder_text_color=Palette.FAINT, font=font(10),
        )
        self.new_category.grid(row=0, column=0, sticky="ew")
        self.new_kind = ctk.CTkOptionMenu(
            add_row, values=["expense", "income", "transfer"], width=110, height=32,
            corner_radius=7, fg_color=Palette.RAISED, button_color=Palette.RAISED,
            button_hover_color=Palette.BORDER, dropdown_fg_color=Palette.RAISED,
            dropdown_hover_color=Palette.BORDER, dropdown_text_color=Palette.TEXT,
            text_color=Palette.TEXT, font=font(10), dropdown_font=font(10),
        )
        self.new_kind.grid(row=0, column=1, padx=(10, 10))
        ctk.CTkButton(
            add_row, text="Add", width=80, height=32, corner_radius=7,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=self._add_category,
        ).grid(row=0, column=2)

        # Rules ----------------------------------------------------------
        card = self._card(
            left,
            "Categorisation rules",
            "The keyword list applied to every new statement line.",
        )
        ctk.CTkButton(
            card, text="Open rule list", width=150, height=34, corner_radius=8,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=lambda: RulesWindow(self.app, self.app),
        ).grid(row=2, column=0, sticky="w", padx=20, pady=(14, 18))

        # Gemini ---------------------------------------------------------
        card = self._card(
            right,
            "Gemini API key",
            "Stored on this machine, encrypted with your Windows account when available.",
        )
        key_row = ctk.CTkFrame(card, fg_color="transparent")
        key_row.grid(row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(14, 18))
        key_row.grid_columnconfigure(0, weight=1)
        self.key_entry = ctk.CTkEntry(
            key_row, placeholder_text="Paste key", height=34, corner_radius=7,
            border_width=1, fg_color=Palette.BG, border_color=Palette.BORDER,
            text_color=Palette.TEXT, placeholder_text_color=Palette.FAINT, font=mono(10),
            show="\u2022",
        )
        self.key_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            key_row, text="Save", width=80, height=34, corner_radius=7,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=self._save_key,
        ).grid(row=0, column=1, padx=(10, 10))
        ctk.CTkButton(
            key_row, text="Remove", width=86, height=34, corner_radius=7,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self._remove_key,
        ).grid(row=0, column=2)
        self.key_status = ctk.CTkLabel(
            key_row, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.key_status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        consent_row = ctk.CTkFrame(card, fg_color="transparent")
        consent_row.grid(row=3, column=0, columnspan=3, sticky="w", padx=20, pady=(6, 16))
        self.cloud_switch = ctk.CTkSwitch(
            consent_row,
            text="Allow a summary to be sent to Gemini",
            font=font(10),
            text_color=Palette.MUTED,
            progress_color=Palette.ACCENT,
            button_color=Palette.SURFACE,
            button_hover_color=Palette.RAISED,
            fg_color=Palette.BORDER,
            command=self._toggle_consent,
        )
        self.cloud_switch.pack(side="left")

        # Local model ----------------------------------------------------
        card = self._card(
            right,
            "Local model",
            "Answers short questions on this machine. Chrome's built-in model is used when it "
            "is ready, otherwise a model served by Ollama. Nothing leaves the computer.",
        )
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(14, 6))
        ctk.CTkLabel(
            row, text="Use", font=font(10), text_color=Palette.MUTED, anchor="w"
        ).pack(side="left", padx=(0, 12))
        self.local_pref = ctk.CTkOptionMenu(
            row, values=["Automatic", "Ollama", "Chrome Nano"], width=140, height=32,
            corner_radius=7, fg_color=Palette.RAISED, button_color=Palette.RAISED,
            button_hover_color=Palette.BORDER, dropdown_fg_color=Palette.SURFACE,
            dropdown_hover_color=Palette.RAISED, dropdown_text_color=Palette.TEXT,
            text_color=Palette.TEXT, font=font(10), dropdown_font=font(10),
            command=self._preference_changed,
        )
        self.local_pref.pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="Check", width=76, height=32, corner_radius=7,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=self._check_local,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            row, text="Download Chrome model", width=180, height=32, corner_radius=7,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self._prepare_local,
        ).pack(side="left")

        self.local_status = ctk.CTkLabel(
            card, text="", font=font(9), text_color=Palette.FAINT, anchor="w",
            justify="left", wraplength=520,
        )
        self.local_status.grid(row=3, column=0, columnspan=3, sticky="w", padx=20, pady=(2, 18))

        # Data -----------------------------------------------------------
        card = self._card(
            right,
            "Data",
            "One SQLite file, plus a timestamped copy written every time you close the app.",
        )
        ctk.CTkLabel(
            card, text=str(self.store.path), font=mono(9), text_color=Palette.MUTED,
            anchor="w", wraplength=520, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(12, 10))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 10))
        ctk.CTkButton(
            actions, text="Back up now", width=130, height=34, corner_radius=8,
            fg_color=Palette.RAISED, hover_color=Palette.BORDER, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.TEXT, font=font(10),
            command=self._backup_now,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            actions, text="Open backup folder", width=160, height=34, corner_radius=8,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(10),
            command=self._open_backups,
        ).pack(side="left")
        self.data_status = ctk.CTkLabel(
            card, text="", font=font(9), text_color=Palette.FAINT, anchor="w"
        )
        self.data_status.grid(row=4, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 10))

        version_row = ctk.CTkFrame(card, fg_color="transparent")
        version_row.grid(row=5, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 4))
        self.version_label = ctk.CTkLabel(
            version_row, text=f"Version {APP_VERSION}", font=font(9),
            text_color=Palette.MUTED, anchor="w",
        )
        self.version_label.pack(side="left", padx=(0, 14))
        self.update_button = ctk.CTkButton(
            version_row, text="Check for updates", width=140, height=30, corner_radius=7,
            fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
            border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(9),
            command=self._check_updates,
        )
        self.update_button.pack(side="left")
        self.update_link = ctk.CTkButton(
            version_row, text="Open release page", width=150, height=30, corner_radius=7,
            fg_color=Palette.ACCENT, hover_color=Palette.ACCENT_HOVER,
            text_color="#FFFFFF", font=font(9, "bold"), command=self._open_release,
        )
        self.update_status = ctk.CTkLabel(
            card, text="", font=font(9), text_color=Palette.FAINT, anchor="w",
            justify="left", wraplength=520,
        )
        self.update_status.grid(
            row=6, column=0, columnspan=3, sticky="w", padx=20, pady=(0, 18)
        )
        self.release_url = RELEASES_PAGE

        # Imported statements -------------------------------------------
        card = self._card(
            right,
            "Imported statements",
            "Removing one deletes exactly the rows that file brought in. "
            "Everything else stays.",
        )
        self.statement_list = ctk.CTkFrame(card, fg_color="transparent")
        self.statement_list.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 16)
        )
        self.statement_list.grid_columnconfigure(0, weight=1)

        # Accounts ------------------------------------------------------
        card = self._card(
            right,
            "Accounts",
            "What each account is called. Rename one to fix an import default or to tidy "
            "the name shown in the header.",
        )
        self.account_list = ctk.CTkFrame(card, fg_color="transparent")
        self.account_list.grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=20, pady=(12, 16)
        )
        self.account_list.grid_columnconfigure(0, weight=1)

        self._load_categories()
        self._load_statements()
        self._load_accounts()
        self.refresh()

    def _card(self, parent: Any, title: str, subtitle: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=Palette.SURFACE, corner_radius=12,
            border_width=1, border_color=Palette.BORDER,
        )
        card.pack(fill="x", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            card, text=title, font=font(12, "bold"), text_color=Palette.TEXT, anchor="w"
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=20, pady=(16, 0))
        ctk.CTkLabel(
            card, text=subtitle, font=font(9), text_color=Palette.FAINT, anchor="w",
            justify="left", wraplength=520,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=20, pady=(3, 0))
        return card

    def refresh(self) -> None:
        if hasattr(self, "statement_list"):
            self._load_statements()
        if hasattr(self, "account_list"):
            self._load_accounts()
        start, end = self.cycle_note()
        anchor = self.app.cycle_anchor
        note = f"Statements run {format_range(start, end)}."
        if anchor > 28:
            note += " Months shorter than the chosen day end on their final day."
        self.anchor_note.configure(text=note)
        self.anchor_menu.set(str(anchor))
        moment = self.store.last_backup_time()
        self.data_status.configure(
            text=f"Last backup: {moment.strftime('%d %b %Y, %H:%M')}"
            if moment
            else "No backup has been written yet.",
        )
        has_key = bool(load_api_key(self.store))
        self.key_status.configure(
            text="A key is saved. Enter a new one to replace it."
            if has_key
            else "No key saved. The Insights page is disabled until you add one."
        )
        consent = self.store.get_setting("cloud_consent", "ask") or "ask"
        if consent == "never":
            self.cloud_switch.deselect()
        else:
            self.cloud_switch.select()
        preference = self.app.local.preference()
        self.local_pref.set(
            {"auto": "Automatic", "ollama": "Ollama", "nano": "Chrome Nano"}.get(
                preference, "Automatic"
            )
        )
        cached = self.app.local.cached()
        if cached is not None:
            self._show_local(cached)
        elif not self.local_status.cget("text"):
            self.local_status.configure(text="Not checked yet.", text_color=Palette.FAINT)

    def cycle_note(self) -> tuple[date, date]:
        return self.app.cycle()

    def _toggle_consent(self) -> None:
        allowed = bool(self.cloud_switch.get())
        self.store.set_setting("cloud_consent", "ask" if allowed else "never")
        self.app.notify(
            "Deeper questions may be sent to Gemini, after confirmation."
            if allowed
            else "Offline mode: nothing will be sent to Gemini."
        )

    def _preference_changed(self, value: str) -> None:
        mapping = {"Automatic": "auto", "Ollama": "ollama", "Chrome Nano": "nano"}
        self.store.set_setting("local_preference", mapping.get(value, "auto"))
        self.app.local.invalidate()
        self._check_local()

    def _check_local(self) -> None:
        self.local_status.configure(
            text="Checking the local model...", text_color=Palette.FAINT
        )
        preference = self.app.local.preference()
        run_in_thread(
            self,
            lambda: self.app.local.resolve(force=True, preference=preference),
            self._local_probed,
        )

    def _local_probed(self, kind: str, value: Any) -> None:
        if kind != "ok":
            self.local_status.configure(
                text=f"Could not check the local model: {value}", text_color=Palette.RED
            )
            return
        self._show_local(value)

    def _show_local(self, status: LocalStatus) -> None:
        if status.ready:
            label = (
                f"Ollama {status.detail}"
                if status.backend == "ollama"
                else "Gemini Nano inside Chrome"
            )
            self.local_status.configure(
                text=f"Ready: {label}", text_color=Palette.ACCENT
            )
        else:
            self.local_status.configure(text=status.detail, text_color=Palette.FLAG)

    def _prepare_local(self) -> None:
        self.local_status.configure(
            text="Starting Chrome and checking the model. A first-time download runs in the "
            "background and can take several minutes.",
            text_color=Palette.MUTED,
        )
        run_in_thread(self, self.app.nano.prepare, self._prepared)

    def _prepared(self, kind: str, value: Any) -> None:
        if kind != "ok":
            self.local_status.configure(
                text=f"The Chrome model could not be prepared. {value}",
                text_color=Palette.RED,
            )
            return
        self.app.local.invalidate()
        self._check_local()

    def _anchor_changed(self, value: str) -> None:
        try:
            self.app.set_anchor(int(value))
        except ValueError:
            return
        self.refresh()

    def _load_categories(self) -> None:
        for child in self.category_list.winfo_children():
            child.destroy()
        for index, row in enumerate(self.store.categories()):
            holder = ctk.CTkFrame(self.category_list, fg_color="transparent")
            holder.grid(row=index // 2, column=index % 2, sticky="w", pady=3)
            ctk.CTkFrame(
                holder, width=8, height=8, corner_radius=4,
                fg_color=str(row["colour"] or Palette.FAINT),
            ).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                holder, text=str(row["name"]), font=font(10),
                text_color=Palette.TEXT, anchor="w",
            ).pack(side="left")

    def _add_category(self) -> None:
        name = self.new_category.get().strip()
        if not name:
            self.app.notify("Type a category name first.", Palette.FLAG)
            return
        self.store.ensure_category(name, kind=self.new_kind.get())
        self.new_category.delete(0, "end")
        self._load_categories()
        self.app.refresh_all()
        self.app.notify(f"Category {name} added.", Palette.ACCENT)

    def _load_statements(self) -> None:
        for child in self.statement_list.winfo_children():
            child.destroy()
        rows = self.store.statements()
        if not rows:
            ctk.CTkLabel(
                self.statement_list, text="Nothing imported yet.",
                font=font(9), text_color=Palette.FAINT, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            return
        for index, row in enumerate(rows):
            name = str(row["source_file"])
            box = ctk.CTkFrame(self.statement_list, fg_color="transparent")
            box.grid(row=index, column=0, sticky="ew", pady=2)
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                box, text=name[:36] or "manual entry", font=font(9),
                text_color=Palette.TEXT, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            first_day = str(row["first_day"] or "")[:10]
            last_day = str(row["last_day"] or "")[:10]
            ctk.CTkLabel(
                box,
                text=f"{row['rows']} rows  \u00b7  {first_day} to {last_day}",
                font=font(8), text_color=Palette.FAINT, anchor="w",
            ).grid(row=1, column=0, sticky="w")
            ctk.CTkButton(
                box, text="Remove", width=78, height=28, corner_radius=6,
                fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
                border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(9),
                command=lambda value=name: self._remove_statement(value),
            ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))

    def _load_accounts(self) -> None:
        for child in self.account_list.winfo_children():
            child.destroy()
        rows = self.store.accounts()
        if not rows:
            ctk.CTkLabel(
                self.account_list, text="Nothing imported yet.",
                font=font(9), text_color=Palette.FAINT, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            return
        for index, row in enumerate(rows):
            name = str(row["account"])
            box = ctk.CTkFrame(self.account_list, fg_color="transparent")
            box.grid(row=index, column=0, sticky="ew", pady=2)
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                box, text=name or "unnamed", font=font(9),
                text_color=Palette.TEXT, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                box, text=f"{row['rows']} transactions",
                font=font(8), text_color=Palette.FAINT, anchor="w",
            ).grid(row=1, column=0, sticky="w")
            ctk.CTkButton(
                box, text="Rename", width=78, height=28, corner_radius=6,
                fg_color="transparent", hover_color=Palette.RAISED, border_width=1,
                border_color=Palette.BORDER, text_color=Palette.MUTED, font=font(9),
                command=lambda value=name: self._rename_account(value),
            ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))

    def _rename_account(self, old_name: str) -> None:
        dialog = TextPromptDialog(
            self.app,
            "Rename account",
            "Every transaction on this account takes the new name. Using a name that "
            "already exists merges the two accounts.",
            initial=old_name,
        )
        self.app.wait_window(dialog)
        new_name = dialog.result
        if not new_name or new_name == old_name:
            return
        changed = self.store.rename_account(old_name, new_name)
        if self.app.account_scope == old_name:
            self.app.account_scope = new_name
            self.store.set_setting("account_scope", new_name)
        self._load_accounts()
        self._load_statements()
        self.app.refresh_all()
        self.app.notify(
            f"{changed} transactions renamed to {new_name}.", Palette.ACCENT
        )

    def _remove_statement(self, source_file: str) -> None:
        count = 0
        for row in self.store.statements():
            if str(row["source_file"]) == source_file:
                count = int(row["rows"])
                break
        dialog = ConfirmDialog(
            self.app,
            "Remove this statement?",
            f"{source_file}\n\nThis deletes its {count} transactions from the ledger. "
            "Categories, rules, settings and your other statements are untouched. "
            "Backups already taken are not affected.",
        )
        self.app.wait_window(dialog)
        if not dialog.result:
            return
        removed = self.store.delete_statement(source_file)
        self._load_statements()
        self.app.refresh_all()
        self.app.notify(f"{removed} transactions removed.", Palette.ACCENT)

    def _save_key(self) -> None:
        value = self.key_entry.get().strip()
        if not value:
            self.app.notify("Paste a key first.", Palette.FLAG)
            return
        save_api_key(self.store, value)
        self.key_entry.delete(0, "end")
        self.refresh()
        self.pages_refresh_insights()
        self.app.notify("API key saved.", Palette.ACCENT)

    def _remove_key(self) -> None:
        save_api_key(self.store, "")
        self.key_entry.delete(0, "end")
        self.refresh()
        self.pages_refresh_insights()
        self.app.notify("API key removed.")

    def pages_refresh_insights(self) -> None:
        page = self.app.pages.get("insights")
        if page is not None:
            page.refresh()

    def _backup_now(self) -> None:
        try:
            destination = self.store.backup_now()
        except Exception as error:
            self.app.notify(f"Backup failed: {error}", Palette.RED)
            return
        self.refresh()
        self.app.notify(f"Backup written to {destination.name}.", Palette.ACCENT)

    def _open_backups(self) -> None:
        folder = self.store.path.parent / BACKUP_DIRNAME
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as error:
            self.app.notify(f"Could not open the folder: {error}", Palette.RED)

    def _check_updates(self) -> None:
        self.update_button.configure(state="disabled")
        self.update_status.configure(
            text="Asking GitHub for the latest release...", text_color=Palette.FAINT
        )
        run_in_thread(self, check_for_update, self._updates_checked)

    def _updates_checked(self, kind: str, value: Any) -> None:
        self.update_button.configure(state="normal")
        if kind != "ok":
            self.update_status.configure(
                text=f"Could not check for updates: {value}", text_color=Palette.RED
            )
            return
        info: UpdateInfo = value
        if info.available:
            self.release_url = info.url
            self.update_status.configure(
                text=f"Version {info.latest} is available. You are on {APP_VERSION}.",
                text_color=Palette.ACCENT,
            )
            self.update_link.pack(side="left", padx=(10, 0))
        else:
            self.release_url = RELEASES_PAGE
            self.update_status.configure(
                text=f"You are on the latest release ({APP_VERSION}).",
                text_color=Palette.FAINT,
            )
            self.update_link.pack_forget()

    def _open_release(self) -> None:
        try:
            webbrowser.open(self.release_url)
        except Exception as error:
            self.app.notify(f"Could not open the browser: {error}", Palette.RED)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _log_exception(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
    try:
        with open(app_dir() / "ledger_error.log", "a", encoding="utf-8") as handle:
            handle.write(f"\n{datetime.now().isoformat(timespec='seconds')}\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=handle)
    except OSError:
        pass


def _fatal(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    smoke = "--smoke" in sys.argv[1:]
    _enable_dpi_awareness()
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("dark-blue")
    sys.excepthook = _log_exception

    try:
        store = Store(database_path())
    except Exception as error:
        _fatal(f"The database could not be opened.\n\n{database_path()}\n\n{error}")
        return 1

    try:
        app = App(store)
    except Exception as error:
        _log_exception(type(error), error, error.__traceback__)
        store.close()
        _fatal(f"The interface could not start.\n\n{error}")
        return 1

    def report(exc_type: Any, exc_value: Any, exc_tb: Any) -> None:
        _log_exception(exc_type, exc_value, exc_tb)
        if smoke:
            app.failures.append(f"callback: {exc_value}")
            print(f"callback error: {exc_value}", file=sys.stderr)
            return
        try:
            messagebox.showerror(
                APP_NAME,
                f"Something went wrong.\n\n{exc_value}\n\n"
                "Details were written to ledger_error.log.",
            )
        except Exception:
            pass

    app.report_callback_exception = report

    if smoke:

        def tour() -> None:
            for key in ("dashboard", "ledger", "plan", "insights", "settings"):
                app.show_page(key)
                app.update_idletasks()
            app.after(500, finish)

        def finish() -> None:
            rows = len(app.pages["ledger"].tree.get_children())
            lines = [
                f"pages={len(app.pages)}",
                f"ledger_rows={rows}",
                f"failures={len(app.failures)}",
            ]
            for name in ("pdfplumber", "openpyxl", "requests", "matplotlib", "websocket"):
                try:
                    __import__(name)
                    lines.append(f"module {name}=ok")
                except Exception as error:
                    lines.append(f"module {name}=missing ({error})")
            lines.append(f"database={app.store.path}")
            for failure in app.failures:
                lines.append(f"failure: {failure}")
            print("SMOKE " + " | ".join(lines))
            try:
                (app_dir() / "smoke_report.txt").write_text(
                    "\n".join(lines), encoding="utf-8"
                )
            except OSError:
                pass
            app.close()

        app.after(450, tour)

    app.mainloop()
    if smoke and app.failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

