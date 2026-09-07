"""
Merges a week's mainstream-book odds (data/week_NN.csv, wide format --
one column set per bookmaker) with Circa's transcribed opening lines
(data/circa_week_NN.csv, if it exists for that week) into a single
data/combined_week_NN.csv: one row per game, with each individual
book's spread/total, a consensus (median) across all of them, and
Circa's own numbers, all side by side. Also refreshes
data/combined_current_week.csv (always this week's data, same
filename every week) so the Google Sheet's IMPORTDATA formula never
needs to be re-pasted -- only the file's content changes.

Usage: python3 merge_weekly_lines.py --week 2
       python3 merge_weekly_lines.py --all   # merges every week_NN.csv found,
                                              # then updates the current-week alias
"""

import argparse
import csv
import glob
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

BASE_COLUMNS = [
    "cfbd_week",
    "cfbd_game_id",
    "commence_time",
    "away_team",
    "home_team",
    "book_count",
    "consensus_home_spread",
    "consensus_away_spread",
    "consensus_total",
    "circa_home_spread",
    "circa_away_spread",
    "circa_total",
]


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def detect_book_names(columns: list[str]) -> list[str]:
    names = set()
    for col in columns:
        m = re.match(r"^(.+?)_(home_spread|away_spread|home_ml|away_ml|total)$", col)
        if m:
            names.add(m.group(1))
    return sorted(names)


def to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def median_or_blank(values: list[float]):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else ""


def merge_week(week: int) -> Path | None:
    books_rows = load_csv(DATA_DIR / f"week_{week:02d}.csv")
    circa_rows = load_csv(DATA_DIR / f"circa_week_{week:02d}.csv")

    if not books_rows and not circa_rows:
        return None

    circa_by_game = {r["cfbd_game_id"]: r for r in circa_rows if r.get("cfbd_game_id")}
    book_names = detect_book_names(books_rows[0].keys()) if books_rows else []

    per_book_columns = []
    for book in book_names:
        per_book_columns += [f"{book}_home_spread", f"{book}_away_spread", f"{book}_total"]
    columns = BASE_COLUMNS + per_book_columns

    combined = []
    seen_game_ids = set()

    for row in books_rows:
        game_id = row.get("cfbd_game_id", "")
        seen_game_ids.add(game_id)

        home_spreads = [to_float(row.get(f"{b}_home_spread")) for b in book_names]
        away_spreads = [to_float(row.get(f"{b}_away_spread")) for b in book_names]
        totals = [to_float(row.get(f"{b}_total")) for b in book_names]
        book_count = sum(1 for v in home_spreads if v is not None)

        circa = circa_by_game.get(game_id, {})

        combined_row = {
            "cfbd_week": row.get("cfbd_week", week),
            "cfbd_game_id": game_id,
            "commence_time": row.get("commence_time", ""),
            "away_team": row.get("away_team", ""),
            "home_team": row.get("home_team", ""),
            "book_count": book_count,
            "consensus_home_spread": median_or_blank(home_spreads),
            "consensus_away_spread": median_or_blank(away_spreads),
            "consensus_total": median_or_blank(totals),
            "circa_home_spread": circa.get("circa_home_spread", ""),
            "circa_away_spread": circa.get("circa_away_spread", ""),
            "circa_total": circa.get("circa_total", ""),
        }
        for book in book_names:
            combined_row[f"{book}_home_spread"] = row.get(f"{book}_home_spread", "")
            combined_row[f"{book}_away_spread"] = row.get(f"{book}_away_spread", "")
            combined_row[f"{book}_total"] = row.get(f"{book}_total", "")
        combined.append(combined_row)

    # Games Circa listed that the books file didn't have at all (rare, but
    # keeps this file authoritative for "every game we have a line for").
    for game_id, circa in circa_by_game.items():
        if game_id in seen_game_ids:
            continue
        combined_row = {
            "cfbd_week": circa.get("cfbd_week", week),
            "cfbd_game_id": game_id,
            "commence_time": "",
            "away_team": circa.get("away_team", ""),
            "home_team": circa.get("home_team", ""),
            "book_count": 0,
            "consensus_home_spread": "",
            "consensus_away_spread": "",
            "consensus_total": "",
            "circa_home_spread": circa.get("circa_home_spread", ""),
            "circa_away_spread": circa.get("circa_away_spread", ""),
            "circa_total": circa.get("circa_total", ""),
        }
        for book in book_names:
            combined_row[f"{book}_home_spread"] = ""
            combined_row[f"{book}_away_spread"] = ""
            combined_row[f"{book}_total"] = ""
        combined.append(combined_row)

    combined.sort(key=lambda r: (r.get("commence_time") or ""))

    out_path = DATA_DIR / f"combined_week_{week:02d}.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(combined)
    return out_path


def discover_weeks() -> list[int]:
    weeks = set()
    for path in glob.glob(str(DATA_DIR / "week_*.csv")) + glob.glob(str(DATA_DIR / "circa_week_*.csv")):
        m = re.search(r"week_(\d+)\.csv$", path)
        if m:
            weeks.add(int(m.group(1)))
    return sorted(weeks)


def determine_current_week(cfbd_api_key: str, season: int) -> int | None:
    """The 'current' week is whichever CFBD calendar week contains today's
    date (or the next upcoming one if today falls in a gap) -- NOT simply
    the highest week number present in data/, since a book sometimes posts
    a game's line far in advance (e.g. a November game already has a
    week_12.csv while it's still Week 2)."""
    resp = requests.get(
        "https://api.collegefootballdata.com/calendar",
        params={"year": season},
        headers={"Authorization": f"Bearer {cfbd_api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    calendar = [w for w in resp.json() if w.get("seasonType") == "regular"]
    now = datetime.now(timezone.utc)

    for week_info in calendar:
        start = datetime.fromisoformat(week_info["startDate"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(week_info["endDate"].replace("Z", "+00:00"))
        if start <= now <= end:
            return week_info["week"]

    upcoming = [w for w in calendar if datetime.fromisoformat(w["startDate"].replace("Z", "+00:00")) > now]
    return min(upcoming, key=lambda w: w["startDate"])["week"] if upcoming else None


def update_current_week_alias(current_week: int) -> None:
    """Overwrites data/combined_current_week.csv with that week's combined
    data, so the Google Sheet's IMPORTDATA formula (pointed at this one
    stable filename) never needs to be re-pasted week to week -- only the
    *content* changes, not the URL."""
    src = DATA_DIR / f"combined_week_{current_week:02d}.csv"
    if not src.exists():
        print(f"No combined_week_{current_week:02d}.csv yet -- current-week alias not updated.")
        return
    dest = DATA_DIR / "combined_current_week.csv"
    dest.write_text(src.read_text())
    print(f"Updated {dest} -> week {current_week}")


def main():
    import os
    import sys

    parser = argparse.ArgumentParser(description="Merge mainstream-book and Circa lines into one row-per-game CSV.")
    parser.add_argument("--week", type=int, help="Merge a single week")
    parser.add_argument("--all", action="store_true", help="Merge every week found in data/")
    parser.add_argument("--cfbd-api-key", default=os.environ.get("CFBD_API_KEY"), help="Needed to determine the current week for combined_current_week.csv")
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()

    weeks = discover_weeks() if args.all else ([args.week] if args.week else [])
    if not weeks:
        print("Nothing to do: pass --week N or --all")
        return

    for week in weeks:
        out_path = merge_week(week)
        if out_path:
            print(f"Wrote {out_path}")
        else:
            print(f"No data found for week {week}, skipped")

    if not args.cfbd_api_key:
        print("No CFBD API key provided -- skipping combined_current_week.csv refresh.", file=sys.stderr)
        return

    season = args.season or datetime.now(timezone.utc).year
    current_week = determine_current_week(args.cfbd_api_key, season)
    if current_week is None:
        print("Could not determine current CFBD week -- skipping combined_current_week.csv refresh.", file=sys.stderr)
        return
    update_current_week_alias(current_week)


if __name__ == "__main__":
    main()
