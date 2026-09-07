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


def update_opening_lines_archive(weeks: list[int]) -> None:
    """data/opening_lines_archive.csv is append-only: the first time a
    given cfbd_game_id is ever seen in a merged weekly file, that row is
    the permanent record of its opening line, written here and never
    touched again on later runs -- unlike week_NN.csv, which
    scrape_cfb_lines.py fully overwrites every Sunday with that week's
    latest snapshot (so week_NN.csv alone loses the true opener after the
    line moves)."""
    archive_path = DATA_DIR / "opening_lines_archive.csv"
    existing = load_csv(archive_path)
    existing_ids = {r["cfbd_game_id"] for r in existing if r.get("cfbd_game_id")}
    columns = list(existing[0].keys()) if existing else None

    new_rows = []
    for week in weeks:
        rows = load_csv(DATA_DIR / f"combined_week_{week:02d}.csv")
        if not rows:
            continue
        if columns is None:
            columns = list(rows[0].keys())
        for row in rows:
            game_id = row.get("cfbd_game_id")
            if not game_id or game_id in existing_ids:
                continue
            new_rows.append(row)
            existing_ids.add(game_id)

    if not new_rows:
        print("No new games to add to opening_lines_archive.csv.")
        return

    all_rows = existing + new_rows
    all_rows.sort(key=lambda r: (r.get("cfbd_week") or "", r.get("commence_time") or ""))

    with open(archive_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Added {len(new_rows)} game(s) to {archive_path} (total {len(all_rows)})")


def determine_current_week(weeks: list[int]) -> int | None:
    """The 'current' week for the Sheet's front page is whichever week has
    the most *upcoming* (not yet kicked off) games in what we've actually
    scraped. Deliberately not: a CFBD calendar date range (Week 1's
    Thu-Wed window is still "current" by date through Tuesday even after
    nearly every Week 1 game has finished); the single soonest-upcoming
    game (picks a lone Sunday-night straggler over a week with 40+ games
    still to come, e.g. Week 1's last leftover game vs. Week 2's full
    slate); or the highest week number present (a game's line sometimes
    posts months ahead, e.g. week_12.csv existing during Week 2)."""
    now = datetime.now(timezone.utc)
    upcoming_counts: dict[int, int] = {}
    for week in weeks:
        rows = load_csv(DATA_DIR / f"combined_week_{week:02d}.csv")
        count = 0
        for row in rows:
            ct = row.get("commence_time")
            if not ct:
                continue
            try:
                commence = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except ValueError:
                continue
            if commence > now:
                count += 1
        if count:
            upcoming_counts[week] = count
    if not upcoming_counts:
        return None
    return max(upcoming_counts, key=upcoming_counts.get)


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

    update_opening_lines_archive(weeks)

    current_week = determine_current_week(weeks)
    if current_week is None:
        print("Could not determine current week (no upcoming games found) -- current-week alias not updated.")
        return
    update_current_week_alias(current_week)


if __name__ == "__main__":
    main()
