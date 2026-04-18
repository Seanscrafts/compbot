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
import re
import sys
from datetime import datetime, date, timezone

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import db
import scam as scam_mod
import evaluate as eval_mod
import discover as discover_mod
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

    closing = extraction.get("closing_date")
    if _is_closing_date_past(closing):
        console.print(f"[yellow]Competition closing date ({closing}) is in the past — saving as skipped.[/yellow]")
        db.add_skipped(url, f"competition closed: {closing}")
        db.auto_export()
        raise typer.Exit()

    if not extraction.get("fields"):
        console.print("[red]No form fields found -- not saving.[/red]")
        raise typer.Exit(1)

    result = scam_mod.score(url, extraction)
    colour = result.colour
    console.print(f"\nScam score: [{colour}]{result.label} ({result.score}/100)[/{colour}]")
    for flag in result.flags:
        console.print(f"  [yellow]^ {flag}[/yellow]")

    if result.level == "high":
        console.print(f"\n[red bold]High scam risk detected.[/red bold]")
        force = typer.confirm("Save anyway?", default=False)
        if not force:
            raise typer.Exit()

    # --- Evaluate across 5 dimensions ---
    evaluation = eval_mod.evaluate(
        url=url,
        competition_name=extraction.get("competition_name"),
        html=cleaned,
        profile=profile,
        scam_score=result.score,
        scam_flags=result.flags,
    )

    rec = evaluation.get("recommendation", "review")
    prize_val = evaluation.get("prize_value_zar")
    prize_str = f"R{prize_val:,}" if prize_val else "unknown"

    console.print(f"\nRecommendation: {eval_mod.format_recommendation(rec)}")
    console.print(f"  Legitimacy:  {evaluation.get('legitimacy_score', '?')}/10")
    console.print(f"  Effort:      {evaluation.get('effort_level', '?')}")
    console.print(f"  Prize:       {prize_str} ({evaluation.get('prize_type', '?')})")
    console.print(f"  Draw type:   {evaluation.get('draw_type', '?')}")
    console.print(f"  Usable:      {'Yes' if evaluation.get('usable_for_you') else 'No'}")
    barriers = evaluation.get("barriers", [])
    if barriers:
        console.print(f"  Barriers:    {', '.join(barriers)}")
    console.print(f"  Reason:      [dim]{evaluation.get('reason', '')}[/dim]")

    comp_id = db.add_competition(url, extraction, scam_score=result.score, scam_flags=result.flags, evaluation=evaluation)
    db.auto_export()
    console.print(f"\n[green]Saved as competition #{comp_id}[/green]")
    console.print(f"Run [bold]python compbot.py fill {comp_id}[/bold] when ready.")


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

    table = Table(title="Competitions", expand=False)
    table.add_column("ID", style="dim", width=4, no_wrap=True)
    table.add_column("Name", style="cyan", max_width=32, no_wrap=True)
    table.add_column("Status", width=8, no_wrap=True)
    table.add_column("Rec.", width=7, no_wrap=True)
    table.add_column("Prize", width=9, no_wrap=True)

    for row in rows:
        colour = STATUS_COLOURS.get(row["status"], "white")
        rec = row["recommendation"] or ""
        prize_val = row["prize_value_zar"]
        prize_str = f"R{prize_val:,}" if prize_val else "-"
        name = (row["name"] or "Unknown")
        table.add_row(
            str(row["id"]),
            name,
            f"[{colour}]{row['status']}[/{colour}]",
            eval_mod.format_recommendation(rec) if rec else "[dim]-[/dim]",
            prize_str,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@app.command()
def review(comp_id: int = typer.Argument(..., help="Competition ID")):
    """Quick human-friendly summary before deciding to fill."""
    db.init_db()
    row = db.get_competition(comp_id)
    if not row:
        console.print(f"[red]No competition with ID {comp_id}[/red]")
        raise typer.Exit(1)

    rec = row["recommendation"] or "review"
    prize_val = row["prize_value_zar"]
    prize_str = f"R{prize_val:,}" if prize_val else "Unknown"
    barriers = json.loads(row["barriers"] or "[]")
    warnings = json.loads(row["warnings"] or "[]")
    fields = json.loads(row["fields"] or "[]")
    profile = load_profile()

    rec_colour = eval_mod.REC_COLOUR.get(rec, "white")

    console.print(Panel(
        f"[bold]{row['name'] or 'Unknown'}[/bold]\n"
        f"{row['url']}",
        title=f"#{comp_id}",
    ))

    console.print(f"\n  Decision:    [{rec_colour}][bold]{rec.upper()}[/bold][/{rec_colour}]")
    console.print(f"  Prize:       {prize_str} ({row['prize_type'] or '?'})")
    console.print(f"  Effort:      {row['effort_level'] or '?'}")
    console.print(f"  Legitimacy:  {row['legitimacy_score'] or '?'}/10")
    console.print(f"  Draw:        {row['draw_type'] or '?'}")
    console.print(f"  Closing:     {row['closing_date'] or 'Not listed'}")
    if barriers:
        console.print(f"  Barriers:    {', '.join(barriers)}")
    if row["eval_reason"]:
        console.print(f"\n  [dim]{row['eval_reason']}[/dim]")

    if warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in warnings:
            console.print(f"  - {w}")

    if fields:
        console.print("\n[bold]What will be filled:[/bold]")
        for f in fields:
            value = get_field_value(f, profile) or "[dim]-- you fill this --[/dim]"
            label = f.get("label", "?")
            console.print(f"  {label}: [cyan]{value}[/cyan]")

    console.print(f"\nRun [bold]python compbot.py fill {comp_id}[/bold] to proceed.")


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

    rec = row["recommendation"] or ""
    prize_val = row["prize_value_zar"]
    prize_str = f"R{prize_val:,}" if prize_val else "unknown"
    barriers = json.loads(row["barriers"] or "[]")

    console.print(Panel(
        f"[bold]{row['name'] or 'Unknown'}[/bold]\n"
        f"URL: {row['url']}\n"
        f"Status: {row['status']}    Recommendation: {eval_mod.format_recommendation(rec) if rec else '-'}\n"
        f"Closing: {row['closing_date'] or '-'}    Added: {(row['added_at'] or '')[:10]}    Filled: {(row['filled_at'] or '')[:10] or '-'}",
        title=f"Competition #{comp_id}",
    ))

    # Evaluation breakdown
    if row["legitimacy_score"] is not None:
        console.print("\n[bold]Evaluation[/bold]")
        console.print(f"  Legitimacy:   {row['legitimacy_score']}/10  |  Scam score: {row['scam_score'] or 0}/100")
        console.print(f"  Prize:        {prize_str} ({row['prize_type'] or '?'})")
        console.print(f"  Effort:       {row['effort_level'] or '?'}")
        console.print(f"  Entry method: {row['entry_method'] or '?'}")
        console.print(f"  Draw type:    {row['draw_type'] or '?'}")
        console.print(f"  Usable:       {'Yes' if row['usable_for_you'] else 'No' if row['usable_for_you'] == 0 else '?'}")
        if barriers:
            console.print(f"  Barriers:     {', '.join(barriers)}")
        if row["eval_reason"]:
            console.print(f"  Reason:       [dim]{row['eval_reason']}[/dim]")

    scam_flags = json.loads(row["scam_flags"] or "[]")
    if scam_flags:
        console.print("\n[bold yellow]Scam flags:[/bold yellow]")
        for f in scam_flags:
            console.print(f"  ^ {f}")

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


_DATE_PATTERNS = [
    r"(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})",    # 31/12/2024
    r"(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})",    # 2024-12-31
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})",
]
_MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

def _is_closing_date_past(date_str: str | None) -> bool:
    """Return True if date_str is a recognisable date in the past."""
    if not date_str:
        return False
    s = date_str.strip()
    today = date.today()
    for pat in _DATE_PATTERNS:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            try:
                g = m.groups()
                if len(g) == 3 and isinstance(g[1], str):  # month name
                    d = date(int(g[2]), _MONTHS[g[1].lower()[:3]], int(g[0]))
                elif len(g[0]) == 4:  # yyyy-mm-dd
                    d = date(int(g[0]), int(g[1]), int(g[2]))
                else:  # dd/mm/yyyy
                    d = date(int(g[2]), int(g[1]), int(g[0]))
                return d < today
            except Exception:
                pass
    return False


def _check_if_closed(page_text: str) -> str | None:
    """Return a reason string if the competition looks closed, else None."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": (
                f"Is this competition still open for entries? Look for closing dates, 'competition closed', 'winner announced', or past dates.\n\n"
                f"PAGE TEXT:\n{page_text[:2000]}\n\n"
                f"Reply with ONLY one of:\n"
                f"OPEN\n"
                f"CLOSED: <reason in under 10 words>"
            )}],
        )
        answer = msg.content[0].text.strip()
        if answer.upper().startswith("CLOSED"):
            return answer[7:].strip() if len(answer) > 7 else "competition is closed"
        return None
    except Exception:
        return None


def _ask_claude_field(label: str, page_text: str) -> str | None:
    """Ask Claude to answer a single form question from the live page text."""
    try:
        prompt = (
            f"Competition entry form. Extract the answer to this question from the page text below.\n"
            f"QUESTION: {label}\n"
            f"PAGE TEXT:\n{page_text[:3000]}\n\n"
            f"Rules: Reply with ONLY the answer (1-6 words, no punctuation, no explanation). "
            f"If the answer is not on the page, reply: UNKNOWN"
        )
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = msg.content[0].text.strip()
        if answer.upper() == "UNKNOWN" or not answer:
            return None
        return answer
    except Exception:
        return None


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
    await page.goto(url, wait_until="load", timeout=45000)

    import asyncio as _asyncio
    await _asyncio.sleep(4)  # extra time for JS-heavy pages to render forms

    filled_count = 0
    skipped_count = 0

    # Grab page text once for answering unknown questions + date check
    page_text = await page.evaluate("() => document.body.innerText")

    # Check if competition is still open
    closed = _check_if_closed(page_text)
    if closed:
        console.print(f"\n[red bold]Competition appears CLOSED: {closed}[/red bold]")
        console.print("[yellow]Marking as skipped.[/yellow]")
        await context.close()
        await browser.close()
        await pw.stop()
        db.update_status(comp_id, "skipped")
        db.auto_export()
        return

    for i, field in enumerate(fields, 1):
        label = field.get("label", "Unknown")
        value = get_field_value(field, profile)

        if not value:
            # Try to answer with Claude using live page text
            value = _ask_claude_field(label, page_text)

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

    try:
        import os
        screenshot_path = os.path.join(os.path.dirname(__file__), f"preview_{comp_id}.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        console.print(f"\n[green]Screenshot: {screenshot_path}[/green]")
    except Exception:
        console.print("[yellow]Could not take screenshot (browser may have been closed early).[/yellow]")

    console.print(Panel(
        f"Fields filled: {filled_count} / {len(fields)}\n"
        f"[bold yellow]NOT submitted.[/bold yellow] Solve CAPTCHA if present, then close.",
        title="Done",
        border_style="green",
    ))

    proceed = True
    try:
        import tkinter, tkinter.messagebox
        if await check_for_captcha(page):
            console.print("\n[bold yellow]CAPTCHA detected! Solve it in the browser, then click OK.[/bold yellow]")
            root = tkinter.Tk(); root.withdraw()
            proceed = tkinter.messagebox.askokcancel("CompBot – Solve CAPTCHA", "Solve the CAPTCHA, then click OK to mark as filled.\nClick Cancel to skip this competition.")
            root.destroy()
        else:
            root = tkinter.Tk(); root.withdraw()
            proceed = tkinter.messagebox.askokcancel("CompBot – Done", "Fields filled. Click OK to mark as filled.\nClick Cancel to skip this competition.")
            root.destroy()
    except Exception:
        console.print("[yellow]Browser closed early — marking as skipped.[/yellow]")
        proceed = False

    if proceed:
        db.update_status(comp_id, "filled", filled_at=datetime.now(timezone.utc).isoformat())
        db.auto_export()
        console.print(f"[green]#{comp_id} marked as filled.[/green]")
    else:
        db.update_status(comp_id, "skipped")
        db.auto_export()
        console.print(f"[yellow]#{comp_id} skipped.[/yellow]")

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
# fill-all
# ---------------------------------------------------------------------------

@app.command(name="fill-all")
def fill_all(
    rec: str = typer.Option("enter,review", "--rec", "-r", help="Comma-separated recommendations to include (enter/review/skip)"),
    limit: int = typer.Option(0, "--limit", "-l", help="Max competitions to fill (0 = no limit)"),
):
    """Fill all pending competitions matching the given recommendations, one after another."""
    db.init_db()

    allowed_recs = {r.strip().lower() for r in rec.split(",")}
    rows = db.list_competitions("pending")
    rows = [r for r in rows if (r["recommendation"] or "review") in allowed_recs]

    if limit:
        rows = rows[:limit]

    if not rows:
        console.print(f"[dim]No pending competitions matching rec={rec}[/dim]")
        raise typer.Exit()

    console.print(f"[bold]Fill-all: {len(rows)} competitions (rec={rec})[/bold]")
    done = errors = 0
    for i, row in enumerate(rows, 1):
        prize_val = row["prize_value_zar"]
        prize_str = f"R{prize_val:,}" if prize_val else "?"
        console.print(f"\n[bold cyan]── {i}/{len(rows)}: #{row['id']} {prize_str} — {row['name'] or '?'}[/bold cyan]")
        try:
            asyncio.run(_fill_async(row["id"], row))
            done += 1
        except Exception as e:
            console.print(f"[red]Error on #{row['id']}: {e}[/red] — continuing...")
            errors += 1

    console.print(Panel(f"[green]fill-all complete — {done} filled, {errors} errors.[/green]", border_style="green"))


# ---------------------------------------------------------------------------
# re-eval
# ---------------------------------------------------------------------------

@app.command(name="re-eval")
def re_eval(
    status: str = typer.Option("skipped", "--status", "-s", help="Which status to re-evaluate (skipped/pending/all)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change without writing to DB"),
):
    """Re-evaluate competitions with the current profile rules. Useful after changing evaluation logic."""
    db.init_db()
    profile = load_profile()

    if status == "all":
        rows = db.list_competitions()
    else:
        rows = db.list_competitions(status)

    # Never re-evaluate competitions already filled or submitted
    rows = [r for r in rows if r["status"] not in ("filled", "submitted")]
    # Only re-eval entries that have stored HTML fields (i.e. were fully extracted)
    rows = [r for r in rows if r["fields"] and r["fields"] != "[]"]

    if not rows:
        console.print(f"[dim]No competitions with status '{status}' and stored fields.[/dim]")
        raise typer.Exit()

    console.print(f"[bold]Re-evaluating {len(rows)} competitions (status={status})...[/bold]\n")

    changed = skipped = errors = 0

    for row in rows:
        comp_id = row["id"]
        url = row["url"]
        name = row["name"] or url.split("/")[-1]
        old_rec = row["recommendation"] or "?"
        old_status = row["status"]

        try:
            # Re-fetch page for fresh HTML (cheaper: use stored name + URL only, re-eval from stored data)
            raw_html = fetch_page_httpx(url)
            cleaned = clean_html(raw_html)
            if len(cleaned) > 30000:
                cleaned = cleaned[:30000]

            if len(extract_visible_text(raw_html)) < 200:
                console.print(f"  #{comp_id} [dim]no content — skipping[/dim]")
                skipped += 1
                continue

            scam_result = scam_mod.score(url, {"competition_name": row["name"], "fields": json.loads(row["fields"] or "[]")})
            evaluation = eval_mod.evaluate(
                url=url,
                competition_name=row["name"],
                html=cleaned,
                profile=profile,
                scam_score=scam_result.score,
                scam_flags=scam_result.flags,
            )

            new_rec = evaluation.get("recommendation", "review")
            new_status = old_status

            # If was skipped and now should be entered/reviewed, flip back to pending
            if old_status == "skipped" and new_rec in ("enter", "review"):
                new_status = "pending"

            rec_fmt = eval_mod.format_recommendation(new_rec)
            changed_marker = " [green]← CHANGED[/green]" if new_rec != old_rec or new_status != old_status else ""
            console.print(f"  #{comp_id} {rec_fmt} — {name[:55]}{changed_marker}")
            if new_rec != old_rec:
                console.print(f"    [dim]{old_rec} → {new_rec}: {evaluation.get('reason', '')}[/dim]")

            if not dry_run:
                with db._connect() as conn:
                    conn.execute(
                        """UPDATE competitions SET
                            recommendation=?, eval_reason=?, legitimacy_score=?,
                            effort_level=?, prize_value_zar=?, prize_type=?,
                            usable_for_you=?, entry_method=?, draw_type=?,
                            barriers=?, evaluated_at=?, status=?
                           WHERE id=?""",
                        (
                            new_rec, evaluation.get("reason"), evaluation.get("legitimacy_score"),
                            evaluation.get("effort_level"), evaluation.get("prize_value_zar"),
                            evaluation.get("prize_type"),
                            1 if evaluation.get("usable_for_you") else 0,
                            evaluation.get("entry_method"), evaluation.get("draw_type"),
                            json.dumps(evaluation.get("barriers", [])),
                            datetime.now(timezone.utc).isoformat(),
                            new_status, comp_id,
                        ),
                    )
            changed += 1

        except Exception as e:
            console.print(f"  #{comp_id} [red]Error: {e}[/red]")
            errors += 1

    if not dry_run:
        db.auto_export()

    console.print(Panel(
        f"Processed: {changed}    Skipped (no content): {skipped}    Errors: {errors}"
        + ("\n[yellow]Dry run — no changes written.[/yellow]" if dry_run else ""),
        title="Re-evaluation complete",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@app.command()
def discover(
    limit: int = typer.Option(50, "--limit", "-l", help="Max URLs to pull per source"),
    auto_add: bool = typer.Option(False, "--auto", "-a", help="Auto-add all new competitions without prompting"),
):
    """Scrape SA competition sites and add new competitions to the database."""
    db.init_db()
    profile = load_profile()

    console.print("[bold]Discovering competitions...[/bold]")
    known_urls = db.all_urls()
    found = discover_mod.discover_all(limit_per_source=limit, known_urls=known_urls)

    already_known = len([f for f in found if f["url"] in known_urls])
    new_items = [f for f in found if f["url"] not in known_urls]

    console.print(f"Found [cyan]{len(found)}[/cyan] URLs — [green]{len(new_items)} new[/green], [dim]{already_known} already tracked[/dim]\n")

    if not new_items:
        console.print("[dim]Nothing new to add.[/dim]")
        raise typer.Exit()

    added = skipped = errors = 0

    for item in new_items:
        url = item["url"]
        source = item["source"]
        console.print(f"[dim]─── {url}[/dim]")

        try:
            raw_html = fetch_page_httpx(url)
            if len(extract_visible_text(raw_html)) < 200:
                console.print(f"  [dim]No content — saving as skipped[/dim]")
                db.add_skipped(url, "page returned no content", source)
                skipped += 1
                continue

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

            # Check closing date before spending more API calls
            closing = extraction.get("closing_date")
            if _is_closing_date_past(closing):
                console.print(f"  [dim]Closed ({closing}) — saving as skipped[/dim]")
                db.add_skipped(url, f"competition closed: {closing}", source)
                skipped += 1
                continue

            if not extraction.get("fields"):
                console.print(f"  [dim]No form fields — saving as skipped[/dim]")
                db.add_skipped(url, "no entry form fields found", source)
                skipped += 1
                continue

            scam_result = scam_mod.score(url, extraction)
            evaluation = eval_mod.evaluate(
                url=url,
                competition_name=extraction.get("competition_name"),
                html=cleaned,
                profile=profile,
                scam_score=scam_result.score,
                scam_flags=scam_result.flags,
            )

            rec = evaluation.get("recommendation", "review")
            prize_val = evaluation.get("prize_value_zar")
            prize_str = f"R{prize_val:,}" if prize_val else "?"
            rec_fmt = eval_mod.format_recommendation(rec)

            comp_id = db.add_competition(url, extraction, scam_score=scam_result.score, scam_flags=scam_result.flags, evaluation=evaluation)
            name = extraction.get("competition_name") or url.split("/")[-1]

            # Save skip-recommended entries as skipped immediately
            if rec == "skip":
                db.update_status(comp_id, "skipped")
                console.print(f"  #{comp_id} [dim]SKIP[/dim] {prize_str} — {name[:60]}")
                skipped += 1
            else:
                console.print(f"  #{comp_id} {rec_fmt} {prize_str} — {name[:60]}")
                added += 1

        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")
            errors += 1

    db.auto_export()
    console.print(Panel(
        f"Added: {added}    Skipped: {skipped}    Errors: {errors}\n"
        f"Run [bold]python compbot.py list[/bold] to see all competitions.",
        title="Discovery complete",
        border_style="green",
    ))


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
        writer.writerow([
            "ID", "Name", "Status", "Recommendation", "Legitimacy", "Scam Score",
            "Prize (ZAR)", "Prize Type", "Effort", "Draw Type", "Usable", "Barriers",
            "Closing Date", "Added", "Filled At", "Reason", "URL",
        ])
        for row in rows:
            import json
            barriers = ", ".join(json.loads(row["barriers"] or "[]"))
            prize_val = row["prize_value_zar"]
            writer.writerow([
                row["id"],
                row["name"] or "",
                row["status"],
                row["recommendation"] or "",
                row["legitimacy_score"] or "",
                row["scam_score"] or 0,
                f"R{prize_val:,}" if prize_val else "",
                row["prize_type"] or "",
                row["effort_level"] or "",
                row["draw_type"] or "",
                "Yes" if row["usable_for_you"] else "No" if row["usable_for_you"] == 0 else "",
                barriers,
                row["closing_date"] or "",
                (row["added_at"] or "")[:10],
                (row["filled_at"] or "")[:10],
                row["eval_reason"] or "",
                row["url"],
            ])

    console.print(f"[green]Exported {len(rows)} competitions to {path}[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
