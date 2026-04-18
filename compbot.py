"""
CompBot CLI
===========
Usage:
    python compbot.py add <url>        # Fetch + extract, save to DB
    python compbot.py list             # Show all competitions
    python compbot.py list --status pending
    python compbot.py show <id>        # Show full details for one entry
    python compbot.py fill <id>        # Open browser and fill the form
    python compbot.py skip <id>        # Mark as skipped
"""

import asyncio
import json
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import db
from compbot_proto import (
    EXTRACTION_PROMPT,
    call_claude,
    check_for_captcha,
    clean_html,
    display_extraction,
    extract_visible_text,
    fetch_page_httpx,
    fetch_page_playwright,
    fill_field,
    find_element,
    get_field_value,
    load_profile,
)

app = typer.Typer(help="CompBot -- SA Competition Entry Agent")
console = Console()

STATUS_COLOURS = {
    "pending": "yellow",
    "filled": "cyan",
    "submitted": "green",
    "skipped": "dim",
}


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

@app.command()
def add(url: str = typer.Argument(..., help="Competition page URL")):
    """Fetch page, extract fields with Claude, save to database."""
    db.init_db()

    if db.url_exists(url):
        console.print(f"[yellow]Already tracked:[/yellow] {url}")
        raise typer.Exit()

    profile = load_profile()

    raw_html = fetch_page_httpx(url)
    if len(extract_visible_text(raw_html)) < 500:
        console.print("[yellow]Sparse content -- page may need JS. Use 'fill' to render it fully.[/yellow]")

    cleaned = clean_html(raw_html)
    if len(cleaned) > 30000:
        cleaned = cleaned[:30000]

    prompt = (
        EXTRACTION_PROMPT
        .replace("{profile_json}", json.dumps(profile, indent=2))
        .replace("{url}", url)
        .replace("{html}", cleaned)
    )

    extraction = call_claude(prompt)
    display_extraction(extraction)

    if not extraction.get("fields"):
        console.print("[red]No form fields found -- not saving.[/red]")
        raise typer.Exit(1)

    comp_id = db.add_competition(url, extraction)
    console.print(f"\n[green]Saved as competition #{comp_id}[/green]")
    console.print(f"Run [bold]python compbot.py fill {comp_id}[/bold] when ready to fill the form.")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_comps(
    status: str = typer.Option(None, "--status", "-s", help="Filter by status (pending/filled/submitted/skipped)")
):
    """List all tracked competitions."""
    db.init_db()
    rows = db.list_competitions(status)

    if not rows:
        console.print("[dim]No competitions found.[/dim]")
        raise typer.Exit()

    table = Table(title="Competitions")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Name", style="cyan")
    table.add_column("Status", width=10)
    table.add_column("Closing", width=12)
    table.add_column("Added", width=12)
    table.add_column("URL", style="dim", overflow="fold")

    for row in rows:
        colour = STATUS_COLOURS.get(row["status"], "white")
        table.add_row(
            str(row["id"]),
            row["name"] or "Unknown",
            f"[{colour}]{row['status']}[/{colour}]",
            row["closing_date"] or "—",
            (row["added_at"] or "")[:10],
            row["url"],
        )

    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@app.command()
def show(comp_id: int = typer.Argument(..., help="Competition ID")):
    """Show full details for a competition."""
    db.init_db()
    row = db.get_competition(comp_id)
    if not row:
        console.print(f"[red]No competition with ID {comp_id}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]{row['name'] or 'Unknown'}[/bold]\n"
        f"URL: {row['url']}\n"
        f"Status: {row['status']}\n"
        f"Closing: {row['closing_date'] or '—'}\n"
        f"Added: {row['added_at']}\n"
        f"Filled: {row['filled_at'] or '—'}",
        title=f"Competition #{comp_id}",
    ))

    warnings = json.loads(row["warnings"] or "[]")
    if warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in warnings:
            console.print(f"  - {w}")

    fields = json.loads(row["fields"] or "[]")
    if fields:
        table = Table(title="Fields")
        table.add_column("Label", style="cyan")
        table.add_column("Type", width=10)
        table.add_column("Value / Source")
        profile = load_profile()
        for f in fields:
            value = get_field_value(f, profile) or "[dim]no value[/dim]"
            table.add_row(f.get("label", "?"), f.get("field_type", "?"), str(value)[:80])
        console.print(table)


# ---------------------------------------------------------------------------
# fill
# ---------------------------------------------------------------------------

@app.command()
def fill(comp_id: int = typer.Argument(..., help="Competition ID to fill")):
    """Open browser and fill the form for a competition."""
    db.init_db()
    row = db.get_competition(comp_id)
    if not row:
        console.print(f"[red]No competition with ID {comp_id}[/red]")
        raise typer.Exit(1)

    if row["status"] in ("submitted",):
        console.print(f"[yellow]Competition #{comp_id} is already {row['status']}.[/yellow]")
        raise typer.Exit()

    asyncio.run(_fill_async(comp_id, row))


async def _fill_async(comp_id: int, row):
    from playwright.async_api import async_playwright

    profile = load_profile()
    url = row["url"]
    fields = json.loads(row["fields"] or "[]")

    if not fields:
        console.print("[red]No fields stored for this competition. Re-run 'add' first.[/red]")
        return

    console.print(Panel(
        f"[bold cyan]CompBot -- DRY RUN MODE[/bold cyan]\n"
        f"[yellow]Form will be filled but NOT submitted.[/yellow]",
        border_style="yellow",
    ))
    console.print(f"Opening: [bold]{url}[/bold]\n")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-ZA",
    )
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle", timeout=30000)

    import asyncio as _asyncio
    await _asyncio.sleep(2)

    # Check if page needs re-extraction (JS-heavy, fields may differ)
    filled_count = 0
    skipped_count = 0

    for i, field in enumerate(fields, 1):
        label = field.get("label", "Unknown")
        value = get_field_value(field, profile)

        if not value:
            console.print(f"  [{i}] {label}: [dim]skipped (no value)[/dim]")
            skipped_count += 1
            continue

        console.print(f"  [{i}] {label}:")
        element = await find_element(page, field)

        if element:
            await fill_field(page, element, field, value)
            filled_count += 1
        else:
            console.print(f"    [red]Element not found[/red]")
            skipped_count += 1

    import os
    screenshot_path = os.path.join(os.path.dirname(__file__), f"preview_{comp_id}.png")
    await page.screenshot(path=screenshot_path, full_page=True)
    console.print(f"\n[green]Screenshot: {screenshot_path}[/green]")

    console.print(Panel(
        f"Fields filled: {filled_count} / {len(fields)}\n"
        f"[bold yellow]NOT submitted.[/bold yellow] Solve CAPTCHA if present, then close.",
        title="Done",
        border_style="green",
    ))

    import tkinter, tkinter.messagebox
    if await check_for_captcha(page):
        console.print("\n[bold yellow]CAPTCHA detected! Solve it in the browser, then click OK.[/bold yellow]")
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showinfo("CompBot – Solve CAPTCHA", "All fields filled.\nSolve the CAPTCHA, then click OK to close.")
        root.destroy()
    else:
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showinfo("CompBot – Done", "All fields filled.\nReview the browser, then click OK to close.")
        root.destroy()

    db.update_status(comp_id, "filled", filled_at=datetime.now(timezone.utc).isoformat())
    console.print(f"[green]Status updated to 'filled' for competition #{comp_id}[/green]")

    await context.close()
    await browser.close()
    await pw.stop()


# ---------------------------------------------------------------------------
# skip
# ---------------------------------------------------------------------------

@app.command()
def skip(comp_id: int = typer.Argument(..., help="Competition ID to skip")):
    """Mark a competition as skipped."""
    db.init_db()
    row = db.get_competition(comp_id)
    if not row:
        console.print(f"[red]No competition with ID {comp_id}[/red]")
        raise typer.Exit(1)
    db.update_status(comp_id, "skipped")
    console.print(f"[dim]Competition #{comp_id} marked as skipped.[/dim]")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@app.command()
def export(
    out: str = typer.Option("competitions.csv", "--out", "-o", help="Output CSV filename")
):
    """Export all competitions to a CSV file (opens in Excel)."""
    import csv, os
    db.init_db()
    rows = db.list_competitions()

    if not rows:
        console.print("[dim]No competitions to export.[/dim]")
        raise typer.Exit()

    path = os.path.join(os.path.dirname(__file__), out)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Name", "Status", "Closing Date", "Added", "Filled At", "URL", "Warnings"])
        for row in rows:
            import json
            warnings = ", ".join(json.loads(row["warnings"] or "[]"))
            writer.writerow([
                row["id"],
                row["name"] or "",
                row["status"],
                row["closing_date"] or "",
                (row["added_at"] or "")[:10],
                (row["filled_at"] or "")[:10],
                row["url"],
                warnings,
            ])

    console.print(f"[green]Exported {len(rows)} competitions to {path}[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
