# NBA All Star Challenge — Daily Dashboard

Every morning at 7:45 AM ET, a GitHub Action pulls last night's box scores from the free ESPN API, updates the league scoreboard, and publishes:

- **Live dashboard:** `https://<your-username>.github.io/<repo-name>/`
- **Image for the group chat:** `https://<your-username>.github.io/<repo-name>/dashboard.png`

Scoring: each player's **points + rebounds + assists (PRA)**. A team's score is the sum of its players' PRA. Running total all season — whoever has the most PRA at the end of the season wins the league. Only games on or after the draft date count (set `draft_date` in `config/league.json` once the offline draft happens).

## Run locally

Requirements: Python 3.10+ and PowerShell.

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
```

The launcher refreshes the NBA data, serves `docs/` at
`http://127.0.0.1:8000`, and opens it in your browser. Later, use
`.\scripts\start.ps1 -SkipBuild` to serve the last generated dashboard without
calling the ESPN API. Choose another port with
`.\scripts\start.ps1 -Port 8080`.

On macOS/Linux, run the equivalent commands directly:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python app/update.py
python scripts/serve.py --open
```

## GitHub setup (~15 min)

1. **Create the repo.** On github.com: New repository (public — required for free Pages) → upload these files, keeping the folder structure (`.github/workflows/daily.yml` must keep its path).
2. **Enable Pages.** Repo → Settings → Pages → Source: *Deploy from a branch* → Branch: `main`, folder `/docs` → Save.
3. **Allow the Action to push.** Settings → Actions → General → Workflow permissions → select *Read and write permissions* → Save.
4. **First run.** Actions tab → *Daily NBA Dashboard* → *Run workflow*. Takes ~2 min. Then open your Pages URL to see the scoreboard.
5. **Check the rosters.** If any player shows "No game" every night, check his spelling in `config/league.json` (must match his official NBA name closely) — the Action log prints a `WARNING` for unmatched names.

## Auto-send to the group chat (iPhone Shortcuts)

1. Shortcuts app → **Shortcuts tab** → + → add these actions:
   - **Get Contents of URL** → paste your `.../dashboard.png` URL
   - **Send Message** → tap the input variable, set recipient to your group chat
   - Name it "NBA League".
2. **Automation tab** → + → *Time of Day* → 8:00 AM, Daily → Next → choose **Run Shortcut → NBA League** → set to **Run Immediately** (no confirmation prompt).

Done — the fresh image lands in the chat every morning after the 7:45 rebuild. (Note: "Run Immediately" for Send Message may still show a brief confirmation on some iOS versions; sending as an image attachment works in both iMessage and mixed groups.)

## Editing the league

Everything lives in `config/league.json` — add/drop players, fix spellings, or set `draft_date` once the offline draft happens. The next run picks it up automatically. Players are matched by name (accent- and punctuation-insensitive) against the ESPN box scores.

Scoring is fixed: PTS + REB + AST per player, summed per team. If the league ever changes the formula, edit the `pra()` function in `app/update.py` and the `scoring` block in `config/league.json`.

## How it stays accurate

Results accumulate in `docs/history.json` (one entry per player per game date). Each run only fetches dates it hasn't processed yet, going back up to 45 days — so a missed day (or a roster added a few days after the draft) is backfilled automatically on the next run.

## Project layout

| Path | Purpose |
|---|---|
| `app/` | Dashboard generator source code |
| `config/` | League rosters and scoring configuration |
| `scripts/` | Local setup, build, and localhost commands |
| `documentation/` | Detailed project and maintenance notes |
| `docs/` | Generated GitHub Pages site and dashboard data |
| `.github/workflows/` | Scheduled GitHub Actions automation |
