"""
CompBot Day 1 Prototype
=======================
Paste a competition URL -> Claude extracts form fields & drafts answers ->
Playwright fills the form -> screenshot -> STOPS (never submits).

Usage:
    python compbot_proto.py "https://some-competition.co.za/enter"

Requires:
    - .env with ANTHROPIC_API_KEY
    - profile.json with your personal details
"""

import asyncio
import json
import os
import random
import re
import sys
import time

import anthropic
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()
console = Console()

CLAUDE_MODEL = "claude-sonnet-4-6"

# The prompt we send to Claude to extract form fields from HTML
EXTRACTION_PROMPT = """You are a competition entry assistant. Analyze this HTML from a South African competition page and extract ALL form fields that need to be filled in.

For each form field, return this exact JSON structure:
{
  "fields": [
    {
      "label": "Human-readable label for this field",
      "name": "the name attribute of the input element, or empty string",
      "input_id": "the id attribute of the input element, or empty string",
      "placeholder": "the placeholder text, or empty string",
      "field_type": "text|email|tel|textarea|select|checkbox|radio|number|date|file|hidden",
      "selector": "your best CSS selector to target this specific element",
      "options": ["for select/radio only: list of option values"],
      "mapped_profile_key": "matching key from user profile (full_name, first_name, last_name, email, phone, city, province, country, age, gender, address, id_number) or null if no match",
      "draft_value": "for fields that need a custom answer, infer the answer from the page context first (competition name, prize description, sponsor logos, brand names mentioned). Only if truly unknowable, write 'CHECK PAGE: <what to look for>'. For 'why should you win' style fields, draft a genuine enthusiastic 1-3 sentence answer. null if mapped_profile_key is set",
      "required": true
    }
  ],
  "requirements": ["list of human-readable entry requirements"],
  "warnings": ["anything suspicious: purchase required, ID number asked, SMS costs, etc."],
  "competition_name": "name of the competition if found",
  "closing_date": "deadline if found, or null"
}

IMPORTANT RULES:
- Do NOT include hidden fields, CSRF tokens, or submit buttons
- For "why should you win" / motivation fields, draft a genuine answer (not generic)
- For select fields, include all option values
- Return ONLY valid JSON, no markdown, no explanation
- If the page has no form, return {"fields": [], "requirements": ["No form found on this page"], "warnings": ["Could not find an entry form"], "competition_name": null, "closing_date": null}

USER PROFILE (use this to map fields):
{profile_json}

COMPETITION PAGE URL (use for context clues like sponsor/brand names):
{url}

HTML TO ANALYZE:
{html}"""


def load_profile() -> dict:
    """Load user profile from profile.json."""
    profile_path = os.path.join(os.path.dirname(__file__), "profile.json")
    if not os.path.exists(profile_path):
        console.print("[red]ERROR: profile.json not found. Create it first.[/red]")
        sys.exit(1)
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    # Warn if still using defaults
    if profile.get("email") == "you@example.com":
        console.print("[yellow]WARNING: profile.json still has default values. Edit it with your real details.[/yellow]")
    return profile


def clean_html(raw_html: str) -> str:
    """Strip scripts, styles, and non-form content to reduce token usage."""
    soup = BeautifulSoup(raw_html, "html.parser")

    # Remove stuff Claude doesn't need
    for tag in soup(["script", "style", "noscript", "svg", "img", "link", "meta", "head"]):
        tag.decompose()

    # Try to isolate just the form(s) -- much cheaper on tokens
    forms = soup.find_all("form")
    if forms:
        # Keep forms + some surrounding context (labels, headings)
        parts = []
        for form in forms:
            # Include the parent section for context (labels, headings)
            parent = form.parent
            if parent and parent.name in ["div", "section", "main", "article"]:
                parts.append(str(parent))
            else:
                parts.append(str(form))
        return "\n".join(parts)

    # No <form> tag found -- return full cleaned body (some sites use JS forms)
    body = soup.find("body")
    return str(body) if body else str(soup)


def extract_visible_text(html: str) -> str:
    """Get visible text to check if page actually rendered."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def fetch_page_httpx(url: str) -> str:
    """Fast fetch with httpx (no JavaScript rendering)."""
    console.print(f"[dim]Fetching page with httpx...[/dim]")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-ZA,en;q=0.9",
    }
    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        console.print(f"[yellow]httpx fetch failed: {e}[/yellow]")
        return ""


async def fetch_page_playwright(url: str, browser) -> str:
    """Fallback: use Playwright to render JS-heavy pages."""
    console.print(f"[dim]Falling back to Playwright for page content...[/dim]")
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        content = await page.content()
        return content
    finally:
        await page.close()


def call_claude(prompt: str, retry: bool = True) -> dict:
    """Send prompt to Claude Sonnet and parse JSON response."""
    client = anthropic.Anthropic()  # uses ANTHROPIC_API_KEY from env

    console.print(f"[dim]Calling Claude ({CLAUDE_MODEL})...[/dim]")
    start = time.time()

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    elapsed = time.time() - start
    raw_text = response.content[0].text
    console.print(f"[dim]Claude responded in {elapsed:.1f}s ({response.usage.input_tokens} in / {response.usage.output_tokens} out)[/dim]")

    # Parse JSON -- Claude sometimes wraps in ```json blocks
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = re.sub(r"^```(?:json)?\s*", "", json_text)
        json_text = re.sub(r"\s*```$", "", json_text)

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        if retry:
            console.print(f"[yellow]Claude returned invalid JSON, retrying...[/yellow]")
            retry_prompt = (
                f"Your previous response was not valid JSON. Error: {e}\n"
                f"Original response:\n{raw_text[:500]}\n\n"
                f"Please fix and return ONLY valid JSON."
            )
            return call_claude(retry_prompt, retry=False)
        else:
            console.print(f"[red]Failed to parse Claude response as JSON:[/red]\n{raw_text[:300]}")
            sys.exit(1)


def display_extraction(data: dict):
    """Show extracted fields and answers in a nice table."""
    # Competition info
    name = data.get("competition_name", "Unknown")
    deadline = data.get("closing_date", "Unknown")
    console.print(Panel(f"[bold]{name}[/bold]\nDeadline: {deadline}", title="Competition"))

    # Warnings
    warnings = data.get("warnings", [])
    if warnings:
        for w in warnings:
            console.print(f"  [yellow]WARNING: {w}[/yellow]")

    # Requirements
    reqs = data.get("requirements", [])
    if reqs:
        console.print("\n[bold]Requirements:[/bold]")
        for r in reqs:
            console.print(f"  - {r}")

    # Fields table
    fields = data.get("fields", [])
    if not fields:
        console.print("[red]No form fields found![/red]")
        return

    table = Table(title="Form Fields to Fill")
    table.add_column("#", style="dim", width=3)
    table.add_column("Label", style="cyan")
    table.add_column("Type", style="green", width=10)
    table.add_column("Value", style="white")
    table.add_column("Source", style="dim", width=12)

    for i, field in enumerate(fields, 1):
        value = field.get("draft_value") or f"[profile: {field.get('mapped_profile_key', '?')}]"
        source = "drafted" if field.get("draft_value") else "profile"
        table.add_row(str(i), field.get("label", "?"), field.get("field_type", "?"), str(value)[:60], source)

    console.print(table)


def get_field_value(field: dict, profile: dict) -> str | None:
    """Determine the value to fill for a given field."""
    # If Claude drafted a custom answer, use that — but skip CHECK PAGE placeholders
    draft = field.get("draft_value")
    if draft and not str(draft).startswith("CHECK PAGE"):
        return draft

    # If mapped to a profile key, use profile value
    key = field.get("mapped_profile_key")
    if key and key in profile:
        value = profile[key]
        if value:  # skip empty profile values
            return value

    return None


async def find_element(page, field: dict, scope=None):
    """
    Try multiple strategies to find the form element.
    Uses Playwright locators (auto-retry, stale-safe) and picks first visible match.
    Fallback order: selector -> name -> id -> label -> placeholder.
    Returns the element handle or None.
    """
    strategies = []

    sel = field.get("selector", "")
    if sel:
        # Split compound selectors like "#a #b" into just "#b" for locator
        last = sel.strip().split()[-1]
        strategies.append(("selector", last))

    name = field.get("name", "")
    if name:
        strategies.append(("name", f'[name="{name}"]'))

    input_id = field.get("input_id", "")
    if input_id:
        strategies.append(("id", f"#{input_id}"))

    label = field.get("label", "")
    if label:
        strategies.append(("label_text", label))

    placeholder = field.get("placeholder", "")
    if placeholder:
        strategies.append(("placeholder", f'[placeholder*="{placeholder}" i]'))

    for strategy_name, value in strategies:
        try:
            if strategy_name == "label_text":
                loc = page.get_by_label(value, exact=False)
            else:
                loc = page.locator(value)

            # Among all matches, pick first that is visible
            count = await loc.count()
            for i in range(count):
                el = loc.nth(i)
                try:
                    if await el.is_visible():
                        console.print(f"    [dim]Found via {strategy_name}[/dim]")
                        return await el.element_handle()
                except Exception:
                    continue
        except Exception:
            continue

    return None


async def fill_field(page, element, field: dict, value: str):
    """Fill a single form field with human-like behavior."""
    field_type = field.get("field_type", "text")

    # Random pause before interacting (human-like)
    await asyncio.sleep(random.uniform(0.5, 2.0))

    try:
        if field_type == "select":
            # Try to select by value first, then by label
            try:
                await element.select_option(value=value)
            except Exception:
                await element.select_option(label=value)
            console.print(f"    [green]Selected: {value}[/green]")

        elif field_type in ("checkbox", "radio"):
            is_checked = await element.is_checked()
            if not is_checked:
                await element.click()
            console.print(f"    [green]Checked[/green]")

        elif field_type in ("text", "email", "tel", "number", "textarea", "date", "url"):
            # Click to focus
            await element.click()
            await asyncio.sleep(random.uniform(0.2, 0.5))

            # Clear existing value
            await element.fill("")
            await asyncio.sleep(random.uniform(0.1, 0.3))

            # Type with human-like speed
            for char in value:
                await element.type(char, delay=random.randint(50, 150))

            console.print(f"    [green]Typed: {value[:50]}{'...' if len(value) > 50 else ''}[/green]")

        else:
            # Fallback: try typing
            await element.click()
            await element.fill(value)
            console.print(f"    [green]Filled: {value[:50]}[/green]")

    except Exception as e:
        console.print(f"    [red]Fill error: {e}[/red]")


async def check_for_captcha(page) -> bool:
    """Check if page has a visible CAPTCHA."""
    captcha_selectors = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        ".g-recaptcha",
        ".h-captcha",
        "#captcha",
        "[class*='captcha']",
    ]
    for sel in captcha_selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return True
        except Exception:
            continue
    return False


async def run(url: str):
    """Main prototype flow."""

    # -----------------------------------------------------------------------
    # Step 1: Load profile
    # -----------------------------------------------------------------------
    profile = load_profile()
    console.print(Panel("[bold cyan]CompBot Prototype -- DRY RUN MODE[/bold cyan]\n"
                        "[yellow]Submit is DISABLED. Form will be filled but NOT submitted.[/yellow]",
                        border_style="yellow"))
    console.print(f"Target: [bold]{url}[/bold]\n")

    # -----------------------------------------------------------------------
    # Step 2: Fetch page HTML
    # -----------------------------------------------------------------------
    raw_html = fetch_page_httpx(url)
    visible_text = extract_visible_text(raw_html)

    # If httpx got very little content, the page probably needs JS rendering
    needs_js = len(visible_text) < 500

    # We'll need Playwright either way for form filling, so launch it now
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)  # always headed
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        locale="en-ZA",
    )

    if needs_js:
        console.print("[yellow]Page seems JS-heavy, using Playwright to render...[/yellow]")
        raw_html = await fetch_page_playwright(url, context)

    # -----------------------------------------------------------------------
    # Step 3: Clean HTML and extract with Claude
    # -----------------------------------------------------------------------
    cleaned = clean_html(raw_html)

    # Truncate to avoid huge token costs (keep first 30k chars)
    if len(cleaned) > 30000:
        console.print(f"[dim]HTML truncated from {len(cleaned)} to 30000 chars[/dim]")
        cleaned = cleaned[:30000]

    prompt = (EXTRACTION_PROMPT
              .replace("{profile_json}", json.dumps(profile, indent=2))
              .replace("{url}", url)
              .replace("{html}", cleaned))

    extraction = call_claude(prompt)

    # -----------------------------------------------------------------------
    # Step 4: Display what Claude found
    # -----------------------------------------------------------------------
    display_extraction(extraction)

    fields = extraction.get("fields", [])
    if not fields:
        console.print("[red]No fields to fill. The page might not have a standard form.[/red]")
        console.print("Browser is open -- you can inspect the page manually.")
        import tkinter, tkinter.messagebox
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showinfo("CompBot – No Form", "No form found.\nInspect the browser, then click OK to close.")
        root.destroy()
        await context.close()
        await browser.close()
        await pw.stop()
        return

    # -----------------------------------------------------------------------
    # Step 5: Open page in Playwright and fill fields
    # -----------------------------------------------------------------------
    console.print("\n[bold]Opening browser and filling form...[/bold]\n")
    page = await context.new_page()
    await page.goto(url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(2)  # let page settle

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
            console.print(f"    [red]Could not find element (all 5 strategies failed)[/red]")
            skipped_count += 1

    # -----------------------------------------------------------------------
    # Step 6: Screenshot and summary
    # -----------------------------------------------------------------------
    await asyncio.sleep(1)
    screenshot_path = os.path.join(os.path.dirname(__file__), "submission_preview.png")
    await page.screenshot(path=screenshot_path, full_page=True)
    console.print(f"\n[green]Screenshot saved: {screenshot_path}[/green]")

    console.print(Panel(
        f"[bold green]FIELDS FILLED[/bold green]\n\n"
        f"Fields filled: {filled_count}\n"
        f"Fields skipped: {skipped_count}\n"
        f"Total fields: {len(fields)}\n\n"
        f"[bold yellow]Form was NOT submitted.[/bold yellow]\n"
        f"Solve CAPTCHA if present, review, then close when done.",
        title="Result",
        border_style="green",
    ))

    # CAPTCHA pause AFTER filling -- user solves it, then closes browser manually
    import tkinter, tkinter.messagebox
    if await check_for_captcha(page):
        console.print("\n[bold yellow]CAPTCHA detected! Solve it in the browser, then click OK.[/bold yellow]")
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showinfo("CompBot – Solve CAPTCHA", "All fields are filled.\nSolve the CAPTCHA in the browser,\nthen click OK to close CompBot.")
        root.destroy()
    else:
        root = tkinter.Tk(); root.withdraw()
        tkinter.messagebox.showinfo("CompBot – Done", "All fields filled. No CAPTCHA detected.\nReview the browser, then click OK to close.")
        root.destroy()

    await context.close()
    await browser.close()
    await pw.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        console.print("[bold]Usage:[/bold] python compbot_proto.py <competition-url>")
        console.print("[dim]Example: python compbot_proto.py \"https://www.example.co.za/win\"[/dim]")
        sys.exit(1)

    url = sys.argv[1]

    # Basic URL validation
    if not url.startswith("http"):
        console.print("[red]ERROR: URL must start with http:// or https://[/red]")
        sys.exit(1)

    # Check API key
    if not os.environ.get("ANTHROPIC_API_KEY") or "PASTE" in os.environ.get("ANTHROPIC_API_KEY", ""):
        console.print("[red]ERROR: Set your ANTHROPIC_API_KEY in .env[/red]")
        sys.exit(1)

    asyncio.run(run(url))


if __name__ == "__main__":
    main()
