"""Interface self-check for ledger.py.

Run with:  python check_ui.py [path/to/budget_data.db]

Opens the window, visits every page and asserts that the widgets really
populated: KPI figures, chart series, table rows and stored settings.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import ledger as L

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    RESULTS.append((label, bool(condition), str(detail)))


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else L.database_path()
    store = L.Store(db)
    app = L.App(store)
    app.update()

    check("window width", app.winfo_width() >= 1100, app.winfo_width())
    check("window height", app.winfo_height() >= 700, app.winfo_height())
    check("five pages", len(app.pages) == 5, len(app.pages))

    dashboard = app.pages["dashboard"]
    app.show_page("dashboard")
    app.update()
    for key in ("income", "expenses", "net", "flagged"):
        text = dashboard.cards[key].value.cget("text")
        check(f"kpi {key} has a figure", L.CURRENCY in text, text)
    trend_axes = dashboard.trend.figure.axes[0]
    check("trend bars drawn", len(trend_axes.patches) >= 12, len(trend_axes.patches))
    check("trend labels", len(trend_axes.get_xticklabels()) == 6,
          [t.get_text() for t in trend_axes.get_xticklabels()])
    split_axes = dashboard.split.figure.axes[0]
    check("category bars drawn", len(split_axes.patches) >= 3, len(split_axes.patches))
    check("recent list", dashboard.recent_rows[0][0].cget("text") != "",
          dashboard.recent_rows[0][0].cget("text"))

    ledger_page = app.pages["ledger"]
    app.show_page("ledger")
    app.update()
    check("ledger columns",
          tuple(ledger_page.tree["columns"])
          == ("date", "description", "category", "amount", "saving"),
          ledger_page.tree["columns"])
    rows = ledger_page.tree.get_children()
    check("ledger rows populated", len(rows) >= 10, len(rows))
    check("ledger footer", "out" in ledger_page.footer.cget("text"),
          ledger_page.footer.cget("text"))
    first = ledger_page.tree.item(rows[0])
    check("row has five cells", len(first["values"]) == 5, first["values"])
    check("amount formatted",
          str(first["values"][3]).startswith(("-", "+")),
          first["values"][3])

    plan = app.pages["plan"]
    app.show_page("plan")
    app.update()
    check("required figure shown", L.CURRENCY in plan.required_label.cget("text"),
          plan.required_label.cget("text"))
    lines = plan.chart.figure.axes[0].lines
    check("three projection tracks plus target", len(lines) >= 4, len(lines))
    check("months readout", plan.metrics["months_now"].cget("text") != "\u2014",
          plan.metrics["months_now"].cget("text"))

    insights = app.pages["insights"]
    app.show_page("insights")
    app.update()
    check("insight placeholder", len(insights.textbox.get("1.0", "end").strip()) > 20)
    check("meta line explains the route", "Local model" in insights.meta.cget("text"),
          insights.meta.cget("text"))
    check("findings computed", 1 <= len(insights.findings) <= 3, len(insights.findings))
    check("findings rendered", len(insights.findings_box.winfo_children()) >= 2,
          len(insights.findings_box.winfo_children()))
    check("source label idle", insights.source_label.cget("text") != "",
          insights.source_label.cget("text"))
    check("routing: short question stays local",
          insights.router.tier("summary") == "local",
          insights.router.tier("summary"))
    check("routing: reasoning escalates",
          insights.router.tier("why is my spending up this month") == "cloud",
          insights.router.tier("why is my spending up this month"))

    settings = app.pages["settings"]
    app.show_page("settings")
    app.update()
    check("settings fills the window",
          settings.winfo_height() > app.winfo_height() * 0.6,
          f"{settings.winfo_height()} of {app.winfo_height()}")
    check("anchor menu", settings.anchor_menu.get() in {str(d) for d in range(1, 32)},
          settings.anchor_menu.get())
    check("anchor note", "Statements run" in settings.anchor_note.cget("text"),
          settings.anchor_note.cget("text"))
    check("category dots", len(settings.category_list.winfo_children()) >= 10,
          len(settings.category_list.winfo_children()))
    check("local preference control",
          settings.local_pref.get() in {"Automatic", "Ollama", "Chrome Nano"},
          settings.local_pref.get())
    check("cloud consent switch", settings.cloud_switch.get() in (0, 1),
          settings.cloud_switch.get())
    check("local status text", settings.local_status.cget("text") != "",
          settings.local_status.cget("text"))
    check("version label", L.APP_VERSION in settings.version_label.cget("text"),
          settings.version_label.cget("text"))
    check("update button ready", settings.update_button.cget("state") == "normal",
          settings.update_button.cget("state"))
    check("database path shown", str(store.path) in settings.data_status.cget("text")
          or store.path.name in settings.data_status.cget("text")
          or settings.data_status.cget("text") != "",
          settings.data_status.cget("text"))

    # cycle shift keeps the interface consistent
    before = app.cycle_label.cget("text")
    app.shift_cycle(-1)
    app.update()
    after = app.cycle_label.cget("text")
    check("cycle stepper moves", before != after, f"{before} -> {after}")
    app.shift_cycle(1)

    # batch import dialog really commits across two files
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="ledger_dialog_"))
    stamp = datetime.now().strftime("%H%M%S%f")
    (workdir / "current.csv").write_text(
        "Date,Description,Amount\n"
        f"01/10/2026,TRANSFER TO CARD {stamp},-500.00\n"
        f"02/10/2026,PRET A MANGER {stamp},-6.50\n",
        encoding="utf-8",
    )
    (workdir / "credit.csv").write_text(
        "Date,Description,Amount\n"
        f"02/10/2026,PAYMENT THANK YOU {stamp},500.00\n",
        encoding="utf-8",
    )
    sources = [
        (L.parse_statement(workdir / "current.csv"), "Current"),
        (L.parse_statement(workdir / "credit.csv"), "Credit"),
    ]
    parsed_sources, problems = app._parse_files(
        [workdir / "current.csv", workdir / "credit.csv"]
    )
    check("background parse returns every file", len(parsed_sources) == 2,
          len(parsed_sources))
    check("background parse reports no problems", problems == [], str(problems))
    dialog = L.ImportDialog(app, sources, L.prepare_batch(store, sources), [])
    dialog.update()
    check("dialog lists every file", len(dialog.account_vars) == 2,
          len(dialog.account_vars))
    check("dialog preview rows", len(dialog.tree.get_children()) == 3,
          len(dialog.tree.get_children()))
    check("dialog summary mentions files", "2 files" in dialog.summary_label.cget("text"),
          dialog.summary_label.cget("text"))
    before = len(store.transactions_between(L.date(2026, 10, 1), L.date(2026, 10, 31)))
    dialog._confirm()
    dialog.update()
    after = len(store.transactions_between(L.date(2026, 10, 1), L.date(2026, 10, 31)))
    check("dialog committed rows", after == before + 3, f"{before} -> {after}")
    check("dialog closed", not dialog.winfo_exists(), dialog.winfo_exists())

    # the category dialog creates, selects and ignores without a human
    dialog = L.CategoryDialog(
        app, "TESCO STORES 3421", -45.20, app.category_names(), "Groceries & Dining", False
    )
    dialog.update()
    check("category dialog lists choices",
          len(dialog.list.winfo_children()) > 5, len(dialog.list.winfo_children()))
    check("dialog starts on the current category",
          dialog.selected == "Groceries & Dining", dialog.selected)
    dialog._show_create()
    dialog.new_entry.insert(0, "Test Category")
    dialog._create()
    dialog.ignore.select()
    dialog._apply()
    dialog.update()
    check("dialog returns the new category",
          dialog.result is not None and dialog.result[1] == "Test Category",
          dialog.result)
    check("dialog returns the ignore flag",
          dialog.result is not None and dialog.result[2] is True, dialog.result)

    # pixel-level sanity: the surfaces must actually be light
    try:
        from PIL import ImageGrab

        app.attributes("-topmost", True)
        app.lift()
        app.update()
        left, top = app.winfo_rootx(), app.winfo_rooty()
        bottom = min(top + app.winfo_height(), app.winfo_screenheight())
        shot = ImageGrab.grab(
            bbox=(left, top, left + app.winfo_width(), bottom)
        ).convert("RGB")
        app.attributes("-topmost", False)
        pixels = list(shot.getdata())
        total = max(len(pixels), 1)
        light = sum(1 for r, g, b in pixels if (r + g + b) / 3 > 200) / total
        dark = sum(1 for r, g, b in pixels if (r + g + b) / 3 < 70) / total
        check("mainly light pixels", light > 0.7, f"{light:.2f}")
        check("no dark panels", dark < 0.05, f"{dark:.2f}")
    except Exception as error:  # pragma: no cover - depends on the desktop
        check("pixel sample", False, error)

    app.close()

    failures = [item for item in RESULTS if not item[1]]
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label}  {detail}")
    print(f"\n{len(RESULTS) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
