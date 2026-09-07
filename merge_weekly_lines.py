"""
Merges a week's mainstream-book odds (data/week_NN.csv, wide format --
one column set per bookmaker) with Circa's transcribed opening lines
(data/circa_week_NN.csv, if it exists for that week) into a single
data/combined_week_NN.csv: one row per game, with a consensus
(median) spread/total across all books plus Circa's own numbers side
by side. This is the file the Google Sheet's Apps Script fetches.

Usage: python3 merge_weekly_lines.py --week 2
       python3 merge_weekly_lines.py --all   # merges every week_NN.csv found
"""

import argparse
import csv
import glob
import re
import statistics
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

COMBINED_COLUMNS = [
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

        combined.append(
            {
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
        )

    # Games Circa listed that the books file didn't have at all (rare, but
    # keeps this file authoritative for "every game we have a line for").
    for game_id, circa in circa_by_game.items():
        if game_id in seen_game_ids:
            continue
        combined.append(
            {
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
        )

    combined.sort(key=lambda r: (r.get("commence_time") or ""))

    out_path = DATA_DIR / f"combined_week_{week:02d}.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMBINED_COLUMNS)
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


def main():
    parser = argparse.ArgumentParser(description="Merge mainstream-book and Circa lines into one row-per-game CSV.")
    parser.add_argument("--week", type=int, help="Merge a single week")
    parser.add_argument("--all", action="store_true", help="Merge every week found in data/")
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


if __name__ == "__main__":
    main()
