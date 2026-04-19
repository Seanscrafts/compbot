# CompBot Status — 2026-04-19

## What's Built

- **Core CLI** (`compbot.py`) — Typer commands: `add`, `list`, `show`, `review`, `fill`, `skip`, `fill-all`, `re-eval`, `discover`, `export`
- **Database** (`db.py`) — SQLite, full status lifecycle, auto-export to CSV
- **Scam detection** (`scam.py`) — SA-specific red flags scoring
- **Evaluation** (`evaluate.py`) — Prize/location/usability filtering (beauty/wellness = usable, wife will use)
- **Discovery** (`discover.py`) — Date-sorted listing page scraping, early-stop on known URLs, 4 sources
- **Browser automation** (`compbot_proto.py`) — Playwright + Claude structured form filling
- **Pre-fill vet dialog** — Browser opens first, Enter/Skip dialog lets you manually approve before any filling
- **Closing date check** — `_is_closing_date_past()` at add/discover time, before wasting API calls
- **Fallback field extraction** — `_ask_claude_field()` via Claude Sonnet if selector fails

## Key Commands

```bash
source .venv/Scripts/activate

python compbot.py discover --limit 20       # Find new competitions
python compbot.py list --status pending     # See what's waiting
python compbot.py fill-all                  # Fill all enter+review in sequence
python compbot.py fill-all --rec enter      # Only ENTER recommendations
python compbot.py re-eval --status skipped  # Re-evaluate skipped with updated profile
python compbot.py re-eval --status skipped --dry-run  # Preview changes first
python compbot.py fill <id>                 # Fill a single competition
python compbot.py skip <id>                 # Skip a single competition
```

## fill-all Flow

1. Browser opens to competition page
2. **Vet dialog appears** — green Enter / red Skip buttons (always on top)
   - **Enter** → bot fills the form, then OK/Cancel appears post-fill
   - **Skip** → marks as skipped, moves to next (zero API calls wasted)
3. Post-fill **OK/Cancel** dialog:
   - **OK** = mark as filled
   - **Cancel** = mark as skipped

## Profile Notes

- Cape Town based, married
- Beauty/spa/wellness/skincare prizes = usable (wife will use)
- Kids-only products (nappies, potty pants, kids clothing) = skip
- One-off experiences anywhere in SA = usable (can travel or gift)
- Recurring out-of-city attendance = skip

## Competition Sources

- **GivingMore** — `givingmore.co.za/online-competition-club/all-prizes/?orderby=date` (working)
- **Prized** — `prized.co.za/competitions/` (added, but JS-rendered forms — no fields detected)
- **WinWinSA** — `winwinsa.co.za/competitions/` (DNS failing)
- **AllCompetitions** — `allcompetitions.co.za/` (DNS failing)

## Session Stats (2026-04-19)

- 87 competitions in DB total
- 31 filled (entered)
- 43 skipped
- 13 pending
- 0 submitted (submit flow not yet built)

## Local LLM — In Progress

- Ollama installed, model downloading: `llama3.1:70b-instruct-q4_K_M` (~40GB)
- GPU: NVIDIA RTX 4090 Laptop (16GB VRAM) — can run 70B quantized
- Models stored at: `D:\AI\ollama\models`
- Plan: swap `_check_if_closed()`, `evaluate()`, `_ask_claude_field()` to local Ollama
- Form extraction stays on Claude Sonnet (too complex for local)
- Expected API cost reduction: ~60-70%

## Next Priorities

1. **Submit flow** — `--allow-submit` flag with human confirmation
2. **Wire up Ollama** — swap cheaper calls to local LLM once model finishes downloading
3. **Fix Prized** — use Playwright instead of httpx to detect JS-rendered forms
4. **Scheduler** — APScheduler periodic discovery
