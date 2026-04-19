# CompBot Status — 2026-04-19 (end of day)

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
- **ConsumerRewards** — `consumerrewards.co.za` (working — Playwright fetch, survey forms fully filled)
- **WinWinSA** — `winwinsa.co.za/competitions/` (DNS failing)
- **AllCompetitions** — `allcompetitions.co.za/` (DNS failing)

## Session Stats (2026-04-19 EOD)

- 103 competitions in DB total
- **39 filled (entered)**
- 57 skipped
- 0 pending
- 0 submitted (submit flow not yet built)

## Local LLM — LIVE

- Ollama running: `llama3.1:70b-instruct-q4_K_M`
- GPU: NVIDIA RTX 4090 Laptop (16GB VRAM)
- Models stored at: `D:\AI\ollama\models`
- `ollama_client.py` wired into `_ask_claude_field()` — tries Ollama first, falls back to Claude
- Form extraction stays on Claude Sonnet (too complex for local)

## Next Priorities

1. **Submit flow** — `--allow-submit` flag with human confirmation
2. **Wire Ollama into evaluate** — swap evaluation calls to local LLM
3. **Scheduler** — APScheduler periodic daily discovery
4. **More sources** — investigate loquax.co.uk for international competitions
