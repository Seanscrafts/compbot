# CompBot -- SA Competition Entry Agent

## Project Status: Day 3 — Working End-to-End (2026-04-19)

The core prototype is ready to test. No submissions yet -- DRY RUN only.

---

## What We Built

A single-script prototype that:
1. Takes a competition URL
2. Claude Sonnet 4.6 extracts form fields + drafts answers
3. Playwright opens a headed browser and fills the form
4. Takes a screenshot, **never submits**
5. You inspect and close

## Architecture Decision

**Structured Playwright + Claude** (NOT OpenClaw/computer-use agents).
- Claude reads HTML and returns structured JSON (fields, selectors, values)
- Playwright fills forms programmatically using CSS selectors
- 5-strategy selector fallback: CSS selector -> name attr -> id -> label lookup -> placeholder
- This is 5-10x cheaper and far more reliable than screenshot-based agents

## Files

```
D:\AI\COMPETITIONS\
  compbot_proto.py      # Full prototype (~270 lines)
  profile.json          # Your personal details (EDIT THIS)
  .env                  # ANTHROPIC_API_KEY (EDIT THIS)
  submission_preview.png # Generated after a run
```

## Setup (already done)

```bash
cd D:/AI/COMPETITIONS
source .venv/Scripts/activate
# Deps already installed: anthropic, playwright, httpx, beautifulsoup4, rich, python-dotenv
# Chromium already installed via playwright install
```

## How to Run

```bash
cd D:/AI/COMPETITIONS
source .venv/Scripts/activate
python compbot_proto.py "https://some-competition.co.za/enter"
```

## Before First Real Run

1. Edit `profile.json` with your real name, email, phone, etc.
2. Put your real Anthropic API key in `.env`

## Tech Stack

- Python 3.11.9 (Windows 11)
- Claude Sonnet 4.6 (`claude-sonnet-4-6`) for extraction + drafting
- Playwright (headed Chromium) for browser automation
- httpx for fast page fetching (Playwright fallback for JS-heavy pages)
- BeautifulSoup for HTML cleaning
- Rich for terminal output
- SQLite planned for Phase 2 (not yet)

## What's NOT Built Yet (Next Steps)

1. **Submit flow** -- `--allow-submit` flag with human confirmation
2. **Ollama integration** -- swap cheaper Claude calls to local Llama 3.1 70B
3. **Fix Prized source** -- use Playwright for JS-rendered forms
4. **Scheduler** -- APScheduler for periodic discovery runs

## Full Architecture Plan

Saved at: `C:\Users\preto\.claude\plans\velvety-knitting-whale.md`

Covers all 8 design areas:
- Architecture (component diagram)
- Tooling decisions (why Playwright+Claude, not OpenClaw)
- MVP plan (2-week roadmap)
- Risk management (SA-specific scam detection, bot avoidance)
- Automation strategy (what's automated vs human-in-the-loop vs never)
- Data model (SQLite schema with 5 tables)
- Scaling strategy (manual -> semi-auto -> fully agentic)
- OpenClaw assessment (don't use for MVP, maybe Phase 3 fallback)

## Key Constraints

- Always DRY RUN (no submit without explicit flag)
- One entry per competition per person
- Human-like browser behavior (random delays, headed browser)
- CAPTCHA pauses for human input
- Never mass-enter or spam
- Filter scams before wasting time

## Useful SA Competition Sites to Test

- checkers.co.za (Shoprite Group promos)
- picknpay.co.za (retailer competitions)
- mydeal.co.za (aggregator)
- winwinsa.co.za (aggregator)
- allcompetitions.co.za (aggregator)
