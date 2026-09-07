"""
Writes Circa Sports' weekly college football opening lines to
data/circa_week_NN.csv, matched against the CFBD schedule the same way
scrape_cfb_lines.py matches Odds API games (see build_school_name_matcher).

Circa doesn't publish an API or web page -- they post a graphic (see
find_circa_photo.py) that a human/Claude has to read each week, since
generic OCR struggles with colored text on Circa's black background.
This script takes that already-read data as plain Python input (no
image processing itself) and handles: CFBD schedule matching, CSV
column layout, and file writing -- the parts that should be identical
run to run instead of re-derived from the image by hand each time.

Usage: import GAMES below (or pass a JSON file via --games-json) as a
list of dicts:
    {"away": "Boston College", "home": "Rutgers", "spread": -4.0,
     "total": 56.0, "favored": "home"}
"favored" says which side the spread number's minus sign belongs to,
since Circa's board lists a single spread number under the favored team.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_cfb_lines import build_cfbd_index, fetch_cfbd_games  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

CSV_COLUMNS = [
    "pulled_at_utc",
    "cfbd_game_id",
    "cfbd_week",
    "cfbd_season_type",
    "home_team",
    "away_team",
    "matched_cfbd_schedule",
    "circa_home_spread",
    "circa_away_spread",
    "circa_total",
]


def build_rows(games: list[dict], cfbd_index: dict, resolve, pulled_at: str) -> list[dict]:
    rows = []
    for g in games:
        home_school = resolve(g["home"]) or g["home"]
        away_school = resolve(g["away"]) or g["away"]
        cfbd_game = cfbd_index.get((home_school, away_school)) or cfbd_index.get((away_school, home_school))

        if g.get("favored") == "home":
            home_spread, away_spread = -abs(g["spread"]), abs(g["spread"])
        elif g.get("favored") == "away":
            home_spread, away_spread = abs(g["spread"]), -abs(g["spread"])
        else:
            home_spread, away_spread = g["spread"], -g["spread"]

        rows.append(
            {
                "pulled_at_utc": pulled_at,
                "cfbd_game_id": cfbd_game.get("id", "") if cfbd_game else "",
                "cfbd_week": cfbd_game.get("week", "") if cfbd_game else "",
                "cfbd_season_type": cfbd_game.get("seasonType", "") if cfbd_game else "",
                "home_team": g["home"],
                "away_team": g["away"],
                "matched_cfbd_schedule": "yes" if cfbd_game else "no",
                "circa_home_spread": home_spread,
                "circa_away_spread": away_spread,
                "circa_total": g["total"],
            }
        )
    return rows


def write_week_csv(rows: list[dict], week, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"circa_week_{int(week):02d}.csv" if week not in ("", "unmatched") else "circa_week_unmatched.csv"
    path = output_dir / name
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main():
    parser = argparse.ArgumentParser(description="Write Circa's weekly opening lines, matched to the CFBD schedule.")
    parser.add_argument("--games-json", required=True, help="Path to a JSON file: list of {away, home, spread, total, favored}")
    parser.add_argument("--cfbd-api-key", default=os.environ.get("CFBD_API_KEY"))
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--output-dir", default=str(DATA_DIR))
    args = parser.parse_args()

    if not args.cfbd_api_key:
        print("ERROR: no CFBD API key. Set CFBD_API_KEY env var or pass --cfbd-api-key.", file=sys.stderr)
        sys.exit(1)

    with open(args.games_json) as f:
        games = json.load(f)

    now = datetime.now(timezone.utc)
    pulled_at = now.isoformat()
    season = args.season or now.year

    print(f"Fetching CFBD schedule for season={season}...")
    cfbd_games = fetch_cfbd_games(args.cfbd_api_key, season)
    cfbd_index, resolve = build_cfbd_index(cfbd_games)

    rows = build_rows(games, cfbd_index, resolve, pulled_at)
    unmatched = [r for r in rows if r["matched_cfbd_schedule"] == "no"]
    if unmatched:
        print(f"WARNING: {len(unmatched)} game(s) could not be matched to a CFBD schedule entry:")
        for r in unmatched:
            print(f"  - {r['away_team']} @ {r['home_team']}")

    by_week: dict = {}
    for row in rows:
        week = row["cfbd_week"] if row["cfbd_week"] != "" else "unmatched"
        by_week.setdefault(week, []).append(row)

    output_dir = Path(args.output_dir)
    for week, week_rows in by_week.items():
        path = write_week_csv(week_rows, week, output_dir)
        print(f"Wrote {len(week_rows)} game(s) to {path}")


if __name__ == "__main__":
    main()
