"""
Weekly college football betting lines scraper.

Pulls current spread/moneyline/total odds for NCAAF from The Odds API
(https://the-odds-api.com), matches each game to its real CFBD schedule
entry (via the CollegeFootballData.com REST API -- same source
scripts/game_logs/load_schedule.cjs uses for dc_games), and writes one
CSV per CFBD season week with one row per game (each bookmaker's
spread/ML/total as its own set of columns).

Runs every Sunday morning via a GitHub Actions schedule (see
../.github/workflows/weekly-cfb-lines.yml), early enough that most
games' lines are still close to their opening numbers for the week.

NOTE: The Odds API does not carry Circa Sports as a bookmaker (Circa
doesn't syndicate its lines to any aggregator or API provider - their
numbers are only published via their own app/in-person or occasional
X/Twitter posts). This script pulls the books The Odds API does offer
(DraftKings, FanDuel, BetMGM, Caesars, BetRivers, etc.) as the closest
available substitute for weekly opening-number tracking.

Setup:
    1. Get a free Odds API key at https://the-odds-api.com (500 credits/month, no cost)
       Set it as ODDS_API_KEY (or pass --odds-api-key)
    2. Reuse this repo's existing CFBD_API_KEY (from collegefootballdata.com,
       already used by scripts/game_logs) -- set as CFBD_API_KEY (or pass --cfbd-api-key)
    3. Run manually to test: python3 scrape_cfb_lines.py
"""

import argparse
import csv
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds"
CFBD_API_BASE = "https://api.collegefootballdata.com/games"
MARKETS = "h2h,spreads,totals"
REGIONS = "us"
ODDS_FORMAT = "american"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"

# Names that don't share a prefix with CFBD's school name at all, so
# prefix-matching can't bridge them on its own. Covers both Odds API's
# mascot-bearing names (own mismatches, same handful as
# scripts/game_logs/common.py's TEAM_NAME_ALIASES) and the common
# shorthand/abbreviations Circa's odds board uses (ODU, FIU, FAU, ...).
TEAM_NAME_ALIASES = {
    "Appalachian State Mountaineers": "App State",
    "Hawaii Rainbow Warriors": "Hawai'i",
    "Louisiana Ragin' Cajuns": "Louisiana",
    "Louisiana Monroe Warhawks": "UL Monroe",
    "Ole Miss Rebels": "Ole Miss",
    "San Jose State Spartans": "San José State",
    "ODU": "Old Dominion",
    "FIU": "Florida International",
    "FAU": "Florida Atlantic",
    "Hawaii": "Hawai'i",
    "UNC": "North Carolina",
    "USF": "South Florida",
    "UTEP": "UTEP",
    "SMU": "SMU",
    "UConn": "Connecticut",
    "Pitt": "Pittsburgh",
    "Ole Miss": "Ole Miss",
}


def ascii_fold(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", name).strip()


def build_school_name_matcher(cfbd_school_names: set[str]):
    """CFBD names carry no mascot ('Washington'); Odds API names do
    ('Washington Huskies'). Match by longest CFBD school name that prefixes
    the Odds API name, trying explicit aliases first for the few names that
    aren't prefixes at all (e.g. 'Hawaii Rainbow Warriors' -> "Hawai'i")."""
    by_length = sorted(cfbd_school_names, key=len, reverse=True)

    def resolve(odds_team_name: str) -> str | None:
        aliased = TEAM_NAME_ALIASES.get(odds_team_name)
        if aliased and aliased in cfbd_school_names:
            return aliased
        folded = ascii_fold(odds_team_name).lower()
        for school in by_length:
            if folded.startswith(ascii_fold(school).lower()):
                return school
        return None

    return resolve


def fetch_odds(api_key: str) -> list[dict]:
    params = {
        "apiKey": api_key,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": "iso",
    }
    resp = requests.get(ODDS_API_BASE, params=params, timeout=30)
    resp.raise_for_status()

    remaining = resp.headers.get("x-requests-remaining")
    used = resp.headers.get("x-requests-used")
    if remaining is not None:
        print(f"Odds API credits used this call: {used} | remaining this period: {remaining}")

    return resp.json()


def fetch_cfbd_games(api_key: str, season: int) -> list[dict]:
    """Pull the whole season's schedule (regular + postseason) so games near
    a season boundary or bye-week gaps still resolve without guessing week."""
    games = []
    for season_type in ("regular", "postseason"):
        resp = requests.get(
            CFBD_API_BASE,
            params={"year": season, "seasonType": season_type},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        games.extend(resp.json())
    return games


def build_cfbd_index(cfbd_games: list[dict]) -> tuple[dict, callable]:
    """Key CFBD games by (home_school, away_school) -> game record, and
    return a resolver that maps an Odds API team name (which carries a
    mascot, e.g. 'Washington Huskies') to CFBD's mascot-less school name
    (e.g. 'Washington') by prefix match against every school name actually
    appearing in this season's schedule."""
    school_names = {g.get("homeTeam", "") for g in cfbd_games} | {g.get("awayTeam", "") for g in cfbd_games}
    school_names.discard("")
    resolve = build_school_name_matcher(school_names)

    index = {}
    for g in cfbd_games:
        index[(g.get("homeTeam", ""), g.get("awayTeam", ""))] = g
    return index, resolve


def match_game(odds_game: dict, cfbd_index: dict, resolve) -> dict | None:
    home = resolve(odds_game.get("home_team", ""))
    away = resolve(odds_game.get("away_team", ""))
    if home is None or away is None:
        return None
    match = cfbd_index.get((home, away))
    if match is not None:
        return match
    # Fall back to swapped home/away in case a neutral-site game flips them.
    return cfbd_index.get((away, home))


def american_to_str(price) -> str:
    if price is None or price == "":
        return ""
    price = int(price)
    return f"+{price}" if price > 0 else str(price)


def build_wide_rows(odds_games: list[dict], cfbd_index: dict, resolve, pulled_at: str) -> list[dict]:
    """One row per game; each bookmaker contributes a spread/ML/total block
    of columns (e.g. DraftKings_home_spread, DraftKings_home_ml, ...)."""
    rows = []
    all_bookmakers: set[str] = set()
    game_rows: dict[str, dict] = {}

    for game in odds_games:
        cfbd_game = match_game(game, cfbd_index, resolve)

        base = {
            "pulled_at_utc": pulled_at,
            "cfbd_game_id": cfbd_game.get("id", "") if cfbd_game else "",
            "cfbd_week": cfbd_game.get("week", "") if cfbd_game else "",
            "cfbd_season_type": cfbd_game.get("seasonType", "") if cfbd_game else "",
            "commence_time": game.get("commence_time"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "matched_cfbd_schedule": "yes" if cfbd_game else "no",
        }

        key = game.get("id")
        game_rows[key] = dict(base)

        for bookmaker in game.get("bookmakers", []):
            book = bookmaker.get("title", "unknown").replace(" ", "")
            all_bookmakers.add(book)
            for market in bookmaker.get("markets", []):
                mkey = market.get("key")
                outcomes = {o.get("name"): o for o in market.get("outcomes", [])}

                if mkey == "spreads":
                    home_o = outcomes.get(game.get("home_team"), {})
                    away_o = outcomes.get(game.get("away_team"), {})
                    game_rows[key][f"{book}_home_spread"] = home_o.get("point", "")
                    game_rows[key][f"{book}_home_spread_price"] = american_to_str(home_o.get("price"))
                    game_rows[key][f"{book}_away_spread"] = away_o.get("point", "")
                    game_rows[key][f"{book}_away_spread_price"] = american_to_str(away_o.get("price"))
                elif mkey == "h2h":
                    home_o = outcomes.get(game.get("home_team"), {})
                    away_o = outcomes.get(game.get("away_team"), {})
                    game_rows[key][f"{book}_home_ml"] = american_to_str(home_o.get("price"))
                    game_rows[key][f"{book}_away_ml"] = american_to_str(away_o.get("price"))
                elif mkey == "totals":
                    over_o = outcomes.get("Over", {})
                    under_o = outcomes.get("Under", {})
                    game_rows[key][f"{book}_total"] = over_o.get("point", under_o.get("point", ""))
                    game_rows[key][f"{book}_over_price"] = american_to_str(over_o.get("price"))
                    game_rows[key][f"{book}_under_price"] = american_to_str(under_o.get("price"))

    rows = list(game_rows.values())

    # Every row needs every column present (blank if a book didn't post that
    # market) so csv.DictWriter doesn't choke on ragged rows.
    per_book_cols = []
    for book in sorted(all_bookmakers):
        per_book_cols += [
            f"{book}_home_spread", f"{book}_home_spread_price",
            f"{book}_away_spread", f"{book}_away_spread_price",
            f"{book}_home_ml", f"{book}_away_ml",
            f"{book}_total", f"{book}_over_price", f"{book}_under_price",
        ]
    base_cols = [
        "pulled_at_utc", "cfbd_game_id", "cfbd_week", "cfbd_season_type",
        "commence_time", "home_team", "away_team", "matched_cfbd_schedule",
    ]
    all_cols = base_cols + per_book_cols
    for row in rows:
        for col in all_cols:
            row.setdefault(col, "")

    rows.sort(key=lambda r: (r.get("commence_time") or ""))
    return rows, all_cols


def write_week_csv(rows: list[dict], columns: list[str], week: int, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"week_{week:02d}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main():
    parser = argparse.ArgumentParser(description="Scrape weekly NCAAF betting lines and label by CFBD season week.")
    parser.add_argument("--odds-api-key", default=os.environ.get("ODDS_API_KEY"), help="The Odds API key (or set ODDS_API_KEY env var)")
    parser.add_argument("--cfbd-api-key", default=os.environ.get("CFBD_API_KEY"), help="CollegeFootballData.com API key (or set CFBD_API_KEY env var)")
    parser.add_argument("--season", type=int, default=None, help="CFBD season year (defaults to current calendar year)")
    parser.add_argument("--output-dir", default=str(DATA_DIR), help="Directory to write week_NN.csv files into")
    args = parser.parse_args()

    if not args.odds_api_key:
        print("ERROR: no Odds API key provided. Set ODDS_API_KEY env var or pass --odds-api-key.", file=sys.stderr)
        print("Get a free key at https://the-odds-api.com", file=sys.stderr)
        sys.exit(1)
    if not args.cfbd_api_key:
        print("ERROR: no CFBD API key provided. Set CFBD_API_KEY env var or pass --cfbd-api-key.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    pulled_at = now.isoformat()
    season = args.season or now.year

    print(f"Fetching NCAAF odds ({MARKETS}) from The Odds API...")
    odds_games = fetch_odds(args.odds_api_key)
    print(f"Retrieved {len(odds_games)} games from Odds API.")

    print(f"Fetching CFBD schedule for season={season}...")
    cfbd_games = fetch_cfbd_games(args.cfbd_api_key, season)
    print(f"Retrieved {len(cfbd_games)} CFBD scheduled games.")
    cfbd_index, resolve = build_cfbd_index(cfbd_games)

    rows, columns = build_wide_rows(odds_games, cfbd_index, resolve, pulled_at)
    unmatched = [r for r in rows if r["matched_cfbd_schedule"] == "no"]
    if unmatched:
        print(f"WARNING: {len(unmatched)} game(s) could not be matched to a CFBD schedule entry:")
        for r in unmatched:
            print(f"  - {r['away_team']} @ {r['home_team']} ({r['commence_time']})")

    by_week: dict[int, list[dict]] = {}
    for row in rows:
        week = row["cfbd_week"] if row["cfbd_week"] != "" else "unmatched"
        by_week.setdefault(week, []).append(row)

    output_dir = Path(args.output_dir)
    for week, week_rows in sorted(by_week.items(), key=lambda kv: (kv[0] == "unmatched", kv[0])):
        if week == "unmatched":
            path = output_dir / "week_unmatched.csv"
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerows(week_rows)
        else:
            path = write_week_csv(week_rows, columns, int(week), output_dir)
        print(f"Wrote {len(week_rows)} game(s) to {path}")


if __name__ == "__main__":
    main()
