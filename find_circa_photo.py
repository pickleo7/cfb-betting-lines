"""
Finds the most recent Circa Sports odds-board screenshot in the user's
Photos library and saves it to a local path for Claude to read directly
(generic OCR struggles with Circa's colored-text-on-black graphic, so a
human/Claude reads it each week rather than a fully unattended pipeline
-- see cfb-betting-lines/README.md's "Circa lines" section).

Looks first in an album named "Circa Lines" (create this once in Photos
and save each week's graphic there); falls back to the single most
recent photo in the whole library if that album doesn't exist or is
empty, since Circa's graphic is usually the newest screenshot right
after you save it.

Usage:
    python3 find_circa_photo.py [--album "Circa Lines"] [--out /path/to/save.jpg]
"""

import argparse
import shutil
import sqlite3
import subprocess
from pathlib import Path

PHOTOS_DB = Path.home() / "Pictures" / "Photos Library.photoslibrary" / "database" / "Photos.sqlite"
ORIGINALS_DIR = Path.home() / "Pictures" / "Photos Library.photoslibrary" / "originals"

DEFAULT_ALBUM = "Circa Lines"
DEFAULT_OUT = Path(__file__).resolve().parent / "circa_inbox" / "latest_circa_board.jpg"


def find_original_file(uuid: str) -> Path | None:
    matches = list(ORIGINALS_DIR.glob(f"*/{uuid}.*"))
    return matches[0] if matches else None


def query_album_photo(album_name: str) -> tuple[str, str] | None:
    """Returns (uuid, created_at) of the most recent photo in the named album, if any."""
    con = sqlite3.connect(f"file:{PHOTOS_DB}?mode=ro", uri=True)
    try:
        cur = con.execute(
            """
            SELECT a.ZUUID, datetime(a.ZDATECREATED + 978307200, 'unixepoch', 'localtime')
            FROM ZASSET a
            JOIN Z_30ASSETS z30 ON z30.Z_3ASSETS = a.Z_PK
            JOIN ZGENERICALBUM alb ON alb.Z_PK = z30.Z_30ALBUMS
            WHERE alb.ZTITLE = ?
            ORDER BY a.ZDATECREATED DESC
            LIMIT 1
            """,
            (album_name,),
        )
        row = cur.fetchone()
        return tuple(row) if row else None
    except sqlite3.OperationalError:
        # Join table name (Z_30ASSETS etc.) varies across Photos library
        # schema versions/macOS releases -- if this breaks again, run
        # `.tables` and `.schema Z_NNASSETS` in sqlite3 against Photos.sqlite
        # to find the current album<->asset join table.
        return None
    finally:
        con.close()


def query_most_recent_photo() -> tuple[str, str] | None:
    con = sqlite3.connect(f"file:{PHOTOS_DB}?mode=ro", uri=True)
    try:
        cur = con.execute(
            """
            SELECT ZUUID, datetime(ZDATECREATED + 978307200, 'unixepoch', 'localtime')
            FROM ZASSET
            ORDER BY ZDATECREATED DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return tuple(row) if row else None
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description="Find the latest Circa Sports odds board screenshot in Photos.")
    parser.add_argument("--album", default=DEFAULT_ALBUM, help=f"Album name to look in first (default: '{DEFAULT_ALBUM}')")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Where to save the found image")
    args = parser.parse_args()

    result = query_album_photo(args.album)
    source = f"album '{args.album}'"
    if result is None:
        print(f"No photo found in album '{args.album}' (or album doesn't exist) -- falling back to most recent photo overall.")
        result = query_most_recent_photo()
        source = "most recent photo in library"

    if result is None:
        print("ERROR: no photos found at all.")
        raise SystemExit(1)

    uuid, created_at = result
    src_path = find_original_file(uuid)
    if src_path is None:
        print(f"ERROR: found asset record ({source}, created {created_at}) but couldn't locate its file on disk.")
        raise SystemExit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, out_path.with_suffix(src_path.suffix))

    print(f"Found photo from {source}, created {created_at}")
    print(f"Saved to: {out_path.with_suffix(src_path.suffix)}")


if __name__ == "__main__":
    main()
