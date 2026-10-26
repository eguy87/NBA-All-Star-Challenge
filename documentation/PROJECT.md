# NBA All Star Challenge — Project Notes

## League
- 4 teams, drafted offline. Rosters are entered in `config/league.json` after the draft.
- Scoring: player PRA = PTS + REB + AST. Team score = sum of roster PRA.
- Running total from the draft date; most PRA at season end wins.
- `draft_date` in `config/league.json` is null until the draft happens. Until set, games count from the 2026-27 season opener (Oct 21, 2026).

## Data source
- ESPN public API, no key: `site.api.espn.com/apis/site/v2/sports/basketball/nba/`
  - `scoreboard?dates=YYYYMMDD` → completed game IDs for a date
  - `summary?event=<id>` → full box score (MIN, PTS, REB, AST, ...)
- Verified working Oct 25, 2026 from a plain curl (8 games for Oct 24, full player lines).
- stats.nba.com was unreachable from the dev VM (connection fails) and cdn.nba.com returns 403 there, so ESPN is the primary source. stats.nba.com remains a possible fallback if ESPN ever breaks — it would need its own header set and testing from a GitHub Actions runner.
- balldontlie now requires an API key even on the free tier (401 without one) — not used.

## How the nightly job works
- GitHub Action, cron `45 11 * * *` (7:45 AM ET), plus manual `workflow_dispatch`.
- `app/update.py`:
  1. Loads `config/league.json` and `docs/history.json`.
  2. For each date from the draft date (or season start) through yesterday that isn't in history yet (max 45 days back), fetches the scoreboard, then each finished game's box score.
  3. Matches roster players by normalized name; appends that night's PTS/REB/AST/PRA to the player's game log in history.
  4. Builds the standings from the accumulated logs, renders `docs/index.html`, `docs/data.json`, `docs/manifest.json`, and screenshots `docs/dashboard.png` via Playwright.
  5. Commits `docs/` back to main.
- If a player has no line for a date, he's simply not credited that night (DNP, no game, or a name mismatch — the log warns). A misspelled name is fixed in config; history for that player starts accumulating from the next run, and the backfill window covers games since the draft date.

## Offseason / pausing
- To pause the morning runs without deleting anything, comment out the `schedule:` block in `.github/workflows/daily.yml` (keep `workflow_dispatch:` for manual runs) — same as was done for the HR Derby League in Oct 2026. Uncomment to resume.

## Front end
- v1 is a functional standings + roster + stats-table page in the same visual family as the HR Derby dashboard (dark, green LED accent). A redesigned front end is a later step, decided with Ethan.
