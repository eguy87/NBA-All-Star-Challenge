"""
NBA All Star Challenge — daily dashboard generator.

Scoring: each player's points + rebounds + assists (PRA).
Team score = the sum of its players' PRA. Running total all season;
whoever has the most PRA at the end of the season wins the league.

Only games on or after the draft date count (config/league.json
"draft_date"). Before the draft is set, games count from the season
opener so a late roster add still backfills correctly.

Data: ESPN public API (no key required).
  - Scoreboard by date -> game IDs
  - Per-game summary -> full box score (MIN, PTS, REB, AST, ...)
History accumulates in docs/history.json, so a missed day is backfilled
on the next run (up to BACKFILL_DAYS back).

Outputs (committed by the GitHub Action):
  - docs/index.html      (live dashboard, served by GitHub Pages)
  - docs/dashboard.png   (phone-friendly image for the group chat)
  - docs/data.json       (raw snapshot)
  - docs/history.json    (per-player game log + processed dates)
"""

import json
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
EASTERN = ZoneInfo("America/New_York")
SEASON_START = "2026-10-21"   # 2026-27 regular-season opener
BACKFILL_DAYS = 45            # how far back a run will fill missed dates
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"}


# ---------------------------------------------------------------- helpers ---

def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def get(url: str, params: dict | None = None) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - retry, then surface
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


# ------------------------------------------------------------ data access ---

def fetch_final_games(date: str) -> list[dict]:
    """Game IDs (and teams) for completed games on a date (YYYYMMDD)."""
    data = get(ESPN_SCOREBOARD, {"dates": date})
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = comp.get("status", {}).get("type", {})
        if status.get("state") != "post":
            continue
        teams = {}
        for t in comp.get("competitors", []):
            teams[t.get("homeAway")] = t.get("team", {}).get("displayName", "")
        games.append({"id": ev["id"], "home": teams.get("home", ""),
                      "away": teams.get("away", "")})
    return games


def fetch_boxscore(game_id: str) -> dict[str, dict]:
    """Normalized player name -> stat line for one finished game."""
    data = get(ESPN_SUMMARY, {"event": game_id})
    out = {}
    for team_block in data.get("boxscore", {}).get("players", []):
        team_name = team_block.get("team", {}).get("displayName", "")
        for group in team_block.get("statistics", []):
            labels = group.get("labels", [])
            if "PTS" not in labels:
                continue
            idx = {label: i for i, label in enumerate(labels)}
            for row in group.get("athletes", []):
                athlete = row.get("athlete", {})
                stats = row.get("stats", [])
                if not athlete.get("displayName") or len(stats) < len(labels):
                    continue
                try:
                    out[norm(athlete["displayName"])] = {
                        "name": athlete["displayName"],
                        "team": team_name,
                        "min": stats[idx.get("MIN", 0)],
                        "pts": int(stats[idx["PTS"]]),
                        "reb": int(stats[idx["REB"]]),
                        "ast": int(stats[idx["AST"]]),
                    }
                except (ValueError, IndexError, KeyError):
                    continue
    return out


# -------------------------------------------------------------- computing ---

def pra(line: dict) -> int:
    """The league's scoring: points + rebounds + assists."""
    return line["pts"] + line["reb"] + line["ast"]


def load_history() -> dict:
    path = DOCS / "history.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - corrupt history, start fresh
            print("WARNING: history.json unreadable; starting fresh", file=sys.stderr)
    return {"dates": [], "players": {}}


def build_state(league: dict, history: dict, game_date: str) -> dict:
    now = datetime.now(EASTERN)
    teams, played, all_players = [], [], []
    for team in league["teams"]:
        roster = []
        for p in team["players"]:
            rec = history["players"].get(p["name"], {"games": []})
            games = rec["games"]
            last = next((g for g in reversed(games) if g["date"] == game_date), None)
            row = {
                **p,
                "pts": sum(g["pts"] for g in games),
                "reb": sum(g["reb"] for g in games),
                "ast": sum(g["ast"] for g in games),
                "pra": sum(g["pra"] for g in games),
                "gp": len(games),
                "line": last,
                "games": games,
            }
            roster.append(row)
            all_players.append({**row, "team_name": team["name"]})
            if last:
                played.append({**row, "team_name": team["name"]})
        roster.sort(key=lambda r: r["pra"], reverse=True)
        teams.append({
            "name": team["name"],
            "total": sum(r["pra"] for r in roster),
            "last_night": sum((r["line"] or {}).get("pra", 0) for r in roster),
            "roster": roster,
        })
    teams.sort(key=lambda t: t["total"], reverse=True)

    potn = max(played, key=lambda p: p["line"]["pra"], default=None)
    if potn and potn["line"]["pra"] <= 0:
        potn = None

    return {
        "league_name": league["league_name"],
        "game_date": game_date,
        "generated_at": now.strftime("%b %d, %Y %I:%M %p ET"),
        "teams": teams,
        "potn": potn,
        "any_games": bool(played),
    }


# -------------------------------------------------------------- rendering ---

def fmt_line(line: dict | None) -> str:
    if not line:
        return "No game"
    if line["min"] in ("0", "0:00", ""):
        return "Did not play"
    return (f"{line['pts']} PTS · {line['reb']} REB · {line['ast']} AST "
            f"— {line['pra']} PRA")


def render_html(state: dict) -> str:
    date_label = datetime.strptime(state["game_date"], "%Y-%m-%d").strftime("%A, %B %d")
    rank_labels = ["1ST", "2ND", "3RD", "4TH"]

    def banner(tag, cls, p):
        if not p:
            return ""
        return f"""
        <div class="banner {cls}">
          <span class="banner-tag">{tag}</span>
          <div><span class="banner-name">{p['name']}</span>
          <span class="banner-team">{p['team_name']}</span><br>
          <span class="banner-line">{fmt_line(p['line'])}</span></div>
        </div>"""

    stat_rows = ""
    for t in state["teams"]:
        for r in t["roster"]:
            stat_rows += (
                f'<tr data-team="{t["name"]}" '
                f'data-search="{r["name"].lower()} {r.get("nba_team", "").lower()}">'
                f'<td data-v="{r["name"]}">{r["name"]}</td>'
                f'<td data-v="{t["name"]}">{t["name"].replace("Team ", "")}</td>'
                f'<td data-v="{r.get("nba_team", "")}">{r.get("nba_team", "")}</td>'
                f'<td data-v="{r["gp"]}" class="n">{r["gp"]}</td>'
                f'<td data-v="{r["pts"]}" class="n">{r["pts"]}</td>'
                f'<td data-v="{r["reb"]}" class="n">{r["reb"]}</td>'
                f'<td data-v="{r["ast"]}" class="n">{r["ast"]}</td>'
                f'<td data-v="{r["pra"]}" class="n pracol">{r["pra"]}</td></tr>')

    team_opts = "".join(f'<option value="{t["name"]}">{t["name"]}</option>'
                        for t in state["teams"])
    stats_tab = f"""
    <div class="statsbar">
      <input id="search" type="search" placeholder="Search player or NBA team…"
             oninput="filterStats()">
      <select id="teamfilter" onchange="filterStats()">
        <option value="ALL">All teams</option>{team_opts}
      </select>
    </div>
    <div class="tablewrap"><table class="statstable">
      <thead><tr>
        <th onclick="sortStats(0,'txt')">PLAYER</th>
        <th onclick="sortStats(1,'txt')">TEAM</th>
        <th onclick="sortStats(2,'txt')">NBA</th>
        <th onclick="sortStats(3,'num')">GP</th>
        <th onclick="sortStats(4,'num')">PTS</th>
        <th onclick="sortStats(5,'num')">REB</th>
        <th onclick="sortStats(6,'num')">AST</th>
        <th onclick="sortStats(7,'num')" class="sorted desc">PRA</th>
      </tr></thead>
      <tbody id="statsbody">{stat_rows}</tbody>
    </table></div>"""

    teams_html = ""
    for i, t in enumerate(state["teams"]):
        rows = ""
        for r in t["roster"]:
            night = f'<span class="nightpill">+{r["line"]["pra"]} last night</span>' \
                if r["line"] else ""
            rows += f"""
            <div class="row">
              <span class="pos">{r.get('pos', '')}</span>
              <div class="mid">
                <span class="pname">{r['name']} <em>{r.get('nba_team', '')}</em></span>
                <span class="pline">{fmt_line(r['line'])}{night}</span>
              </div>
              <span class="phr">{r['pra']}<em>{r['pts']} PTS · {r['reb']} REB · {r['ast']} AST</em></span>
            </div>"""
        if not rows:
            rows = '<div class="row empty">Roster not set yet — add players in config/league.json.</div>'
        lead = ' teamlead' if i == 0 else ''
        night = f'<span class="nighthr">+{t["last_night"]} last night</span>' \
            if t["last_night"] else ''
        teams_html += f"""
        <details class="team{lead}"{' open' if i == 0 else ''}>
          <summary class="teamhead">
            <div><span class="rank">{rank_labels[i] if i < len(rank_labels) else f"{i+1}TH"}</span>
            <h2>{t['name']}</h2>{night}</div>
            <div class="scorewrap">
              <div class="total"><span class="led">{t['total']}</span>
                <span class="totlbl">SEASON PRA</span></div>
              <span class="chevron" aria-hidden="true"></span>
            </div>
          </summary>
          <div class="roster">{rows}</div>
        </details>"""

    no_games = ('<div class="banner nogames">No games last night — '
                'scoreboard unchanged.</div>') if not state["any_games"] else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{state['league_name']}</title>
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Chivo+Mono:wght@400;700&display=swap" rel="stylesheet">
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#101418">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="NBA All Star">
<link rel="apple-touch-icon" href="icon.png">
<style>
  :root {{
    --field:#101418; --panel:#181E24; --hair:#2A333D; --led:#4ADE80;
    --chalk:#EDF2F7; --flare:#F87171; --dim:#8A97A5; --ice:#60A5FA;
  }}
  * {{ box-sizing:border-box; margin:0; }}
  body {{ background:var(--field); color:var(--chalk);
         font:16px/1.35 "Chivo Mono", ui-monospace, monospace;
         width:min(1420px, 100%); margin:0 auto; padding:26px 26px 30px; }}
  h1 {{ font-family:"Archivo Black", system-ui, sans-serif; font-size:40px;
       letter-spacing:.5px; text-transform:uppercase; }}
  .masthead {{ display:flex; align-items:baseline; justify-content:space-between;
               border-bottom:4px solid var(--led); padding-bottom:10px; }}
  .date {{ color:var(--dim); font-size:16px; text-align:right; }}
  .toprow {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }}
  .banner {{ display:flex; gap:14px; align-items:center; padding:12px 16px;
             border:1px solid var(--hair); background:var(--panel); }}
  .banner-tag {{ font-family:"Archivo Black",sans-serif; font-size:13px;
                 letter-spacing:1px; padding:4px 9px; white-space:nowrap; }}
  .potn .banner-tag {{ background:var(--led); color:var(--field); }}
  .banner-name {{ font-weight:700; font-size:18px; }}
  .banner-team {{ color:var(--dim); font-size:14px; }}
  .banner-line {{ color:var(--led); font-size:15px; }}
  .nogames {{ color:var(--dim); justify-content:center; grid-column:1 / -1; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px;
            align-items:start; }}
  .team {{ border:1px solid var(--hair); background:var(--panel); }}
  .teamlead {{ border-color:var(--led); box-shadow:0 0 0 1px var(--led); }}
  .teamhead {{ display:flex; align-items:center; justify-content:space-between;
               padding:12px 14px 10px; cursor:pointer; list-style:none; }}
  .teamhead::-webkit-details-marker {{ display:none; }}
  .team[open] .teamhead {{ border-bottom:1px solid var(--hair); }}
  .rank {{ font-family:"Archivo Black",sans-serif; font-size:12px; color:var(--dim); }}
  .teamlead .rank {{ color:var(--led); }}
  .teamhead h2 {{ font-family:"Archivo Black",sans-serif; font-size:21px;
                  text-transform:uppercase; letter-spacing:.5px; }}
  .nighthr {{ display:block; color:var(--ice); font-size:13px; font-weight:700; }}
  .total {{ text-align:right; }}
  .led {{ font-weight:700; font-size:38px; color:var(--led);
          text-shadow:0 0 14px rgba(74,222,128,.45); line-height:1; }}
  .totlbl {{ display:block; color:var(--dim); font-size:11px; margin-top:2px;
              white-space:nowrap; }}
  .scorewrap {{ display:flex; align-items:center; gap:12px; }}
  .chevron {{ width:9px; height:9px; border-right:2px solid var(--dim);
              border-bottom:2px solid var(--dim); transform:rotate(45deg);
              transition:transform .18s ease; }}
  .team[open] .chevron {{ transform:rotate(225deg); }}
  .row {{ display:grid; grid-template-columns:34px 1fr 150px; gap:8px;
           padding:8px 14px; align-items:center;
           border-bottom:1px solid rgba(41,64,47,.5); }}
  .row.empty {{ display:block; color:var(--dim); font-size:13px; }}
  .pos {{ color:var(--dim); font-size:12px; }}
  .mid {{ min-width:0; }}
  .pname {{ display:block; font-weight:700; font-size:15.5px; }}
  .pname em {{ font-style:normal; color:var(--dim); font-size:12px; font-weight:400; }}
  .pline {{ display:block; font-size:13px; color:#C9D6C6; margin-top:1px; }}
  .nightpill {{ background:var(--led); color:var(--field); font-weight:700;
             font-size:11px; padding:1px 7px; margin-left:7px; border-radius:2px; }}
  .phr {{ text-align:right; font-weight:700; font-size:20px; color:var(--ice); }}
  .phr em {{ display:block; font-style:normal; color:var(--dim); font-size:11px;
              font-weight:400; }}
  .tabs {{ display:flex; gap:8px; margin-top:14px; }}
  .tabbtn {{ font:700 14px "Chivo Mono",monospace; letter-spacing:.5px;
             background:var(--panel); color:var(--dim); border:1px solid var(--hair);
             padding:8px 18px; cursor:pointer; }}
  .tabbtn.active {{ color:var(--field); background:var(--led); border-color:var(--led); }}
  .tabpane {{ display:none; }}
  .tabpane.active {{ display:block; }}
  .statsbar {{ display:flex; gap:10px; margin-top:14px; }}
  .statsbar input, .statsbar select {{
    font:14px "Chivo Mono",monospace; color:var(--chalk); background:var(--panel);
    border:1px solid var(--hair); padding:8px 12px; }}
  .statsbar input {{ flex:1; max-width:360px; }}
  .tablewrap {{ overflow-x:auto; margin-top:10px; border:1px solid var(--hair); }}
  .statstable {{ width:100%; border-collapse:collapse; background:var(--panel);
                 font-size:14px; }}
  .statstable th {{ font-family:"Archivo Black",sans-serif; font-size:11px;
                    letter-spacing:1px; color:var(--dim); text-align:left;
                    padding:10px 12px; border-bottom:2px solid var(--hair);
                    cursor:pointer; white-space:nowrap; user-select:none; }}
  .statstable th.sorted {{ color:var(--led); }}
  .statstable th.sorted::after {{ content:" ▲"; font-size:9px; }}
  .statstable th.sorted.desc::after {{ content:" ▼"; }}
  .statstable td {{ padding:8px 12px; border-bottom:1px solid rgba(42,51,61,.5);
                    color:var(--chalk); }}
  .statstable td.n {{ text-align:right; font-weight:700; }}
  .statstable td.pracol {{ color:var(--ice); }}
  footer {{ color:var(--dim); font-size:12px; margin-top:14px; text-align:center; }}
  @media (max-width: 900px) {{
    body {{ padding:16px 12px 24px; font-size:15px; }}
    h1 {{ font-size:26px; }}
    .grid2, .toprow {{ grid-template-columns:1fr; }}
    .masthead {{ flex-direction:column; gap:2px; }}
    .date {{ text-align:left; }}
    .led {{ font-size:34px; }}
  }}
</style></head><body>
  <div class="masthead">
    <h1>{state['league_name']}</h1>
    <div class="date">{date_label}<br>points + rebounds + assists</div>
  </div>
  <nav class="tabs">
    <button class="tabbtn active" onclick="showTab('tab-main', this)">Dashboard</button>
    <button class="tabbtn" onclick="showTab('tab-stats', this)">Stats</button>
  </nav>
  <div id="tab-main" class="tabpane active">
    <div class="toprow">
    {no_games}
    {banner('PLAYER OF THE NIGHT', 'potn', state['potn'])}
    </div>
    <div class="grid2">
    {teams_html}
    </div>
  </div>
  <div id="tab-stats" class="tabpane">
    {stats_tab}
  </div>
  <footer>Updated {state['generated_at']} &middot; data: ESPN API &middot;
  scoring: PTS + REB + AST, running total — most PRA at season end wins</footer>
<script>
function showTab(id, btn) {{
  document.querySelectorAll('.tabpane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tabbtn').forEach(el => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}}
function filterStats() {{
  const q = document.getElementById('search').value.toLowerCase();
  const team = document.getElementById('teamfilter').value;
  document.querySelectorAll('#statsbody tr').forEach(tr => {{
    const matchQ = tr.dataset.search.includes(q);
    const matchT = team === 'ALL' || tr.dataset.team === team;
    tr.style.display = (matchQ && matchT) ? '' : 'none';
  }});
}}
let sortState = {{ col: 7, dir: -1 }};
function sortStats(col, type) {{
  const dir = (sortState.col === col) ? -sortState.dir : (type === 'num' ? -1 : 1);
  sortState = {{ col: col, dir: dir }};
  const body = document.getElementById('statsbody');
  const rows = Array.from(body.rows);
  rows.sort((a, b) => {{
    const av = a.cells[col].dataset.v, bv = b.cells[col].dataset.v;
    return type === 'num' ? (av - bv) * dir : av.localeCompare(bv) * dir;
  }});
  rows.forEach(r => body.appendChild(r));
  document.querySelectorAll('.statstable th').forEach((th, i) => {{
    th.classList.toggle('sorted', i === col);
    th.classList.toggle('desc', i === col && dir === -1);
  }});
}}
</script>
</body></html>"""


def screenshot(html_path: Path, png_path: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1420, "height": 900},
                                device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(1200)
        page.screenshot(path=str(png_path), full_page=True)
        browser.close()


def make_icon(png_path: Path) -> None:
    """One-time 180x180 home-screen icon."""
    html = ("<body style='margin:0;width:180px;height:180px;background:#101418;"
            "display:flex;align-items:center;justify-content:center;"
            "font:900 44px Arial'><span style='color:#4ADE80;"
            "text-shadow:0 0 18px rgba(74,222,128,.6)'>NBA</span></body>")
    tmp = png_path.parent / "_icon.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            page = b.new_page(viewport={"width": 180, "height": 180})
            page.goto(tmp.resolve().as_uri())
            page.screenshot(path=str(png_path))
            b.close()
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------- main ---

def daterange(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= last:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def main() -> int:
    league = json.loads((ROOT / "config" / "league.json").read_text(encoding="utf-8"))
    yesterday = (datetime.now(EASTERN) - timedelta(days=1)).strftime("%Y-%m-%d")
    start = league.get("draft_date") or SEASON_START
    window_start = max(start, (datetime.now(EASTERN) - timedelta(days=BACKFILL_DAYS))
                       .strftime("%Y-%m-%d"))

    DOCS.mkdir(exist_ok=True)
    history = load_history()
    done = set(history["dates"])
    names = [p["name"] for t in league["teams"] for p in t["players"]]
    name_index = {norm(n): n for n in names}

    for date in daterange(window_start, yesterday):
        if date in done:
            continue
        compact = date.replace("-", "")
        try:
            games = fetch_final_games(compact)
        except Exception as e:  # noqa: BLE001 - log and move on; next run retries
            print(f"WARNING: scoreboard fetch failed for {date}: {e}", file=sys.stderr)
            continue
        lines: dict[str, dict] = {}
        for g in games:
            try:
                lines.update(fetch_boxscore(g["id"]))
            except Exception as e:  # noqa: BLE001 - one bad game skips, rest count
                print(f"WARNING: boxscore fetch failed for game {g['id']}: {e}",
                      file=sys.stderr)
        for key, roster_name in name_index.items():
            rec = history["players"].setdefault(roster_name, {"games": []})
            line = lines.get(key)
            if line is None:
                continue  # DNP, no game that night, or name mismatch
            if any(g["date"] == date for g in rec["games"]):
                continue
            rec["games"].append({
                "date": date, "pts": line["pts"], "reb": line["reb"],
                "ast": line["ast"], "pra": pra(line),
                "min": line["min"], "opponent": line["team"],
            })
        history["dates"].append(date)
        print(f"Processed {date}: {len(games)} final games")

    never_matched = [n for n in names
                     if not history["players"].get(n, {}).get("games")]
    if never_matched:
        print(f"WARNING: no games found for: {sorted(never_matched)} "
              f"(hasn't played yet, or name mismatch — check spelling in config)",
              file=sys.stderr)

    history["dates"] = sorted(set(history["dates"]))
    (DOCS / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    state = build_state(league, history, yesterday)
    (DOCS / "index.html").write_text(render_html(state), encoding="utf-8")
    (DOCS / "manifest.json").write_text(json.dumps({
        "name": state["league_name"], "short_name": "NBA All Star",
        "start_url": ".", "display": "standalone",
        "background_color": "#101418", "theme_color": "#101418",
        "icons": [{"src": "icon.png", "sizes": "180x180", "type": "image/png"}],
    }, indent=2), encoding="utf-8")
    (DOCS / "data.json").write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )
    print(f"Rendered dashboard for {yesterday}: "
          + ", ".join(f"{t['name']} {t['total']} PRA" for t in state["teams"]))

    try:
        screenshot(DOCS / "index.html", DOCS / "dashboard.png")
        if not (DOCS / "icon.png").exists():
            make_icon(DOCS / "icon.png")
        print("Screenshot saved to docs/dashboard.png")
    except Exception as e:  # noqa: BLE001 - page still ships without the image
        print(f"WARNING: screenshot failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
