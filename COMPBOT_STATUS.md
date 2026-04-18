# CompBot Status — 2026-04-18

## What's Built

- **Core CLI** (`compbot.py`) — Typer commands: `add`, `list`, `show`, `fill`, `skip`, `fill-all`, `re-eval`, `discover`, `export`
- **Database** (`db.py`) — SQLite, full status lifecycle, all statuses in CSV
- **Scam detection** (`scam.py`) — SA-specific red flags scoring
- **Evaluation** (`evaluate.py`) — Prize/location/usability filtering (beauty/wellness = usable, wife will use)
- **Discovery** (`discover.py`) — Date-sorted listing page scraping, early-stop on known URLs, 3 sources
- **Browser automation** (`compbot_proto.py`) — Playwright + Claude structured form filling
- **Closed competition check** — `_check_if_closed()` via Claude Haiku at fill time
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

## fill-all Dialog

Each competition shows a browser with filled form. Dialog has **OK / Cancel**:
- **OK** = mark as filled, move to next
- **Cancel** = mark as skipped, move to next (use this if competition looks wrong/closed)

## Profile Notes

- Cape Town based, married
- Beauty/spa/wellness/skincare prizes = usable (wife will use)
- Kids-only products (nappies, potty pants, kids clothing) = skip
- One-off experiences anywhere in SA = usable (can travel or gift)
- Recurring out-of-city attendance = skip

## Competition Sources

- **GivingMore** — `givingmore.co.za/online-competition-club/all-prizes/?orderby=date`
- **WinWinSA** — `winwinsa.co.za/competitions/` (untested)
- **AllCompetitions** — `allcompetitions.co.za/` (untested)

## Session Stats (2026-04-18)

- 47 competitions processed total
- ~18 filled (entered)
- Many correctly auto-skipped as closed at fill time
- Re-eval rescued 5 competitions that were wrongly skipped

## Next Priorities

1. Test `discover` against fresh competitions from all 3 sources
2. Re-eval pending SKIP items (`python compbot.py re-eval --status pending`) for beauty/wellness misses
3. Submit flow — `--allow-submit` flag with human confirmation
4. Scheduler — APScheduler periodic discovery
