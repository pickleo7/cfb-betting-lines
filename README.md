# CFB Betting Lines

Scrapes weekly college football odds (spread, moneyline, total) from
[The Odds API](https://the-odds-api.com), matches each game to its real
CFBD schedule entry, and writes `data/week_NN.csv` every Sunday via
GitHub Actions (`.github/workflows/weekly-cfb-lines.yml`) — one file
per CFBD season week, one row per game. A companion pipeline (below)
captures Circa Sports' opening lines from their weekly graphic, and
`merge_weekly_lines.py` combines both into `data/combined_week_NN.csv`
— one row per game with consensus book lines and Circa's lines side by
side — which a Google Sheet pulls in automatically via Apps Script.

This repo is public so the Sheet's Apps Script can fetch the raw CSV
without needing a stored credential. The data itself (odds numbers) 
isn't sensitive.

## Why not Circa specifically?

Circa Sports doesn't syndicate its lines to any odds aggregator or API
provider — their numbers are only visible via their own app, in person
at their sportsbooks, or occasional line-release posts on X/Twitter.
This script instead pulls the books The Odds API does cover
(DraftKings, FanDuel, BetMGM, Caesars, BetRivers, and others) as the
closest practical substitute for tracking weekly opening numbers.

## Output format

One file per week, e.g. `data/week_03.csv`. Each row is one game:

| column | meaning |
|---|---|
| `cfbd_game_id` | CollegeFootballData.com's numeric game id — same id used by `dc_games.cfbd_game_id` elsewhere in this repo |
| `cfbd_week` | CFBD's own season week number (what "Week 3" etc. actually means here) |
| `cfbd_season_type` | `regular` or `postseason` |
| `commence_time`, `home_team`, `away_team` | from The Odds API |
| `matched_cfbd_schedule` | `yes`/`no` — flags any game the script couldn't line up against the CFBD schedule (script prints a warning listing these) |
| `{Book}_home_spread`, `{Book}_away_spread`, `..._price` | per-bookmaker spread + juice, one column set per book (e.g. `DraftKings_home_spread`) |
| `{Book}_home_ml`, `{Book}_away_ml` | per-bookmaker moneyline |
| `{Book}_total`, `{Book}_over_price`, `{Book}_under_price` | per-bookmaker total + juice |

Games that couldn't be matched to a CFBD schedule entry land in
`data/week_unmatched.csv` instead of a numbered week file.

## Setup

1. Get a free Odds API key at https://the-odds-api.com (500 credits/month, no cost).
2. This repo already has a `CFBD_API_KEY` secret (from collegefootballdata.com,
   used by `update-season-stats.yml`) — the workflow reuses it, no new key needed.
3. Add the Odds API key as a repository secret named `ODDS_API_KEY`
   (Settings → Secrets and variables → Actions → New repository secret).
4. The workflow runs automatically every Sunday at 8:00 AM ET and commits
   the new week file(s) back to `data/`. You can also trigger it manually
   from the Actions tab ("Run workflow").

## Running locally

```bash
cd "cfb-betting-lines"
pip install -r requirements.txt
export ODDS_API_KEY="your_odds_api_key"
export CFBD_API_KEY="your_cfbd_api_key"
python3 scrape_cfb_lines.py
```

## Circa's actual opening lines

Circa doesn't have a web page or API — they post a graphic (screenshot
above, team pairs + spread + total) via their own channels each week.
Because it's colored text on a black background, generic OCR is
unreliable, so this isn't fully automated — someone (a person, or
Claude in a session) has to read the image and hand off structured
data. The pipeline:

1. Each week, save Circa's graphic to a Photos album named **"Circa
   Lines"** (create it once in the Photos app).
2. Run `python3 find_circa_photo.py` — finds the newest photo in that
   album (falls back to the single most recent photo in your whole
   library if the album's empty) and saves it to
   `circa_inbox/latest_circa_board.jpg`.
3. Open that image in a Claude Code session and have Claude transcribe
   it into a JSON list like:
   ```json
   [{"away": "Boston College", "home": "Rutgers", "spread": -4, "favored": "home", "total": 56}, ...]
   ```
   `favored` is whichever side ended up with the number on Circa's
   board (their layout always prints it next to the home/bottom team,
   i.e. `favored` is `"home"` unless the number is 0/pick'em).
4. Run `python3 write_circa_lines.py --games-json path/to/that.json
   --season 2026` — matches each game to the CFBD schedule the same
   way `scrape_cfb_lines.py` does and writes
   `data/circa_week_NN.csv` (one row per game: `circa_home_spread`,
   `circa_away_spread`, `circa_total`, plus `cfbd_game_id`/`cfbd_week`).

This part doesn't run in GitHub Actions — CI can't reach your Photos
library, and the image-reading step needs a human/Claude in the loop
anyway.

## Merging into one row-per-game file

```bash
python3 merge_weekly_lines.py --week 2      # one week
python3 merge_weekly_lines.py --all          # every week found in data/
```

Writes `data/combined_week_NN.csv`: `consensus_home_spread` /
`consensus_away_spread` / `consensus_total` are the **median** across
whatever books posted a line for that game, sitting next to
`circa_home_spread` / `circa_away_spread` / `circa_total`. Consensus
and Circa can legitimately disagree on which side is favored — that's
real line movement/divergence, not a bug (seen live: Circa opened
Kansas -6.5 over Missouri, market had since moved to Missouri -6.5).

## Google Sheet

A Google Sheet pulls `data/combined_week_NN.csv` on a schedule via
Apps Script (`IMPORTDATA`-style fetch against
`https://raw.githubusercontent.com/pickleo7/cfb-betting-lines/main/data/combined_week_NN.csv`).
Run `merge_weekly_lines.py` and push to `main` to refresh it.
