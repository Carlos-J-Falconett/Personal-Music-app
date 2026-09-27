"""SQLite storage for the library cache, favorites, playlists and settings."""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY,
    path         TEXT NOT NULL UNIQUE,
    dir          TEXT NOT NULL,
    mtime        REAL NOT NULL,
    size         INTEGER NOT NULL,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL,
    album        TEXT NOT NULL,
    album_artist TEXT NOT NULL DEFAULT '',
    track_no     INTEGER NOT NULL DEFAULT 0,
    disc_no      INTEGER NOT NULL DEFAULT 0,
    duration     REAL NOT NULL DEFAULT 0,
    genre        TEXT NOT NULL DEFAULT '',
    year         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tracks_dir ON tracks(dir);

CREATE TABLE IF NOT EXISTS favorites (
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    added    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlists (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS track_eq (
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    gains    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Listening history (stats.py): one row per listening session of a song. The song's details are
-- copied in, so the history survives rescans, moved files and deleted tracks.
CREATE TABLE IF NOT EXISTS plays (
    id        INTEGER PRIMARY KEY,
    started   REAL NOT NULL,              -- unix time the session began
    listened  REAL NOT NULL,              -- seconds actually playing
    completed INTEGER NOT NULL DEFAULT 0, -- 1 once it counts as a play (half the song or 4 min)
    track_id  INTEGER,
    title     TEXT NOT NULL,
    artist    TEXT NOT NULL,              -- the grouping artist (album artist, else artist)
    album     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS plays_started ON plays(started);
"""

TRACK_FIELDS = (
    "path", "dir", "mtime", "size", "title", "artist", "album", "album_artist",
    "track_no", "disc_no", "duration", "genre", "year",
)


class Database:
    """One connection shared by the UI and the scanner thread, serialized by a lock."""

    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _query(self, sql, params=()):
        """Run a SELECT and return all rows."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _write(self, sql, params=()):
        """Run one write statement and commit it."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def close(self):
        with self._lock:
            self._conn.close()

    # ---- tracks -------------------------------------------------------

    def file_index(self, root: str) -> dict[str, tuple[float, int]]:
        """{path: (mtime, size)} for every known file under `root`."""
        prefix = root.rstrip("/") + "/"
        # A range on `path` (rather than substr) lets SQLite use its unique index.
        rows = self._query(
            "SELECT path, mtime, size FROM tracks WHERE path >= ? AND path < ?",
            (prefix, prefix + "\U0010ffff"),
        )
        return {r["path"]: (r["mtime"], r["size"]) for r in rows}

    def upsert_tracks(self, records: list[dict]):
        """Insert new tracks and update existing ones (matched by path) in one transaction."""
        if not records:
            return
        cols = ", ".join(TRACK_FIELDS)
        marks = ", ".join("?" for _ in TRACK_FIELDS)
        updates = ", ".join(f"{f}=excluded.{f}" for f in TRACK_FIELDS if f != "path")
        sql = f"INSERT INTO tracks ({cols}) VALUES ({marks}) ON CONFLICT(path) DO UPDATE SET {updates}"
        with self._lock:
            self._conn.executemany(sql, [tuple(r[f] for f in TRACK_FIELDS) for r in records])
            self._conn.commit()

    def delete_paths(self, paths: list[str]):
        """Forget tracks by path; their favorites, playlist entries and EQ go with them (ON DELETE CASCADE).
"""
        if not paths:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in paths])
            self._conn.commit()

    def all_tracks(self):
        """Every track row."""
        return self._query("SELECT * FROM tracks")

    # ---- favorites ----------------------------------------------------

    def favorite_ids(self) -> list[int]:
        """Favorite track ids, most recently added first."""
        return [r[0] for r in self._query("SELECT track_id FROM favorites ORDER BY added DESC")]

    def set_favorite(self, track_id: int, favorite: bool):
        if favorite:
            self._write("INSERT OR IGNORE INTO favorites (track_id, added) VALUES (?, ?)", (track_id, time.time()))
        else:
            self._write("DELETE FROM favorites WHERE track_id = ?", (track_id,))

    # ---- playlists ----------------------------------------------------

    def playlists(self):
        """Every playlist as rows of (id, name, count), sorted by name."""
        return self._query(
            "SELECT p.id, p.name, COUNT(pt.track_id) AS count FROM playlists p "
            "LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id "
            "GROUP BY p.id ORDER BY p.name COLLATE NOCASE"
        )

    def create_playlist(self, name: str) -> int:
        return self._write("INSERT INTO playlists (name, created) VALUES (?, ?)", (name, time.time())).lastrowid

    def rename_playlist(self, playlist_id: int, name: str):
        self._write("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))

    def delete_playlist(self, playlist_id: int):
        self._write("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def playlist_track_ids(self, playlist_id: int) -> list[int]:
        """A playlist's track ids in playlist order."""
        rows = self._query(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)
        )
        return [r[0] for r in rows]

    def playlist_contents(self) -> dict[int, list[int]]:
        """{playlist id: track ids in order} for every playlist with songs, in one query."""
        contents: dict[int, list[int]] = {}
        for r in self._query("SELECT playlist_id, track_id FROM playlist_tracks ORDER BY playlist_id, position"):
            contents.setdefault(r[0], []).append(r[1])
        return contents

    def add_to_playlist(self, playlist_id: int, track_ids: list[int]) -> int:
        """Append tracks, skipping ones already in the playlist. Returns how many were added."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()
            pos = row[0] + 1
            added = 0
            for tid in track_ids:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                    (playlist_id, tid, pos),
                )
                if cur.rowcount:
                    pos += 1
                    added += 1
            self._conn.commit()
            return added

    def playlists_with(self, track_id: int) -> set[int]:
        """Ids of the playlists that contain this track."""
        return {r[0] for r in self._query("SELECT playlist_id FROM playlist_tracks WHERE track_id = ?", (track_id,))}

    def remove_from_playlist(self, playlist_id: int, track_ids: list[int]):
        """Take tracks out of a playlist."""
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                [(playlist_id, t) for t in track_ids],
            )
            self._conn.commit()

    # ---- per-song equalizer -------------------------------------------

    def get_track_eq(self, track_id: int) -> list[float] | None:
        """A track's saved equalizer gains (dB per band), or None when it has none."""
        rows = self._query("SELECT gains FROM track_eq WHERE track_id = ?", (track_id,))
        if not rows:
            return None
        try:
            return [float(g) for g in json.loads(rows[0][0])]
        except (ValueError, TypeError):
            return None

    def set_track_eq(self, track_id: int, gains: list[float]):
        """Save a track's equalizer gains."""
        self._write(
            "INSERT INTO track_eq (track_id, gains) VALUES (?, ?) ON CONFLICT(track_id) DO UPDATE SET gains=excluded.gains",
            (track_id, json.dumps([round(g, 2) for g in gains])),
        )

    def delete_track_eq(self, track_id: int):
        self._write("DELETE FROM track_eq WHERE track_id = ?", (track_id,))

    # ---- listening history (see stats.py) ------------------------------

    def save_play(self, play_id: int | None, started: float, listened: float, completed: bool,
                  track_id: int, title: str, artist: str, album: str) -> int:
        """Insert a listening session (play_id None) or update its listened time and completion.
        Returns its id."""
        if play_id is None:
            return self._write(
                "INSERT INTO plays (started, listened, completed, track_id, title, artist, album) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (started, listened, int(completed), track_id, title, artist, album)).lastrowid
        self._write("UPDATE plays SET listened = ?, completed = ? WHERE id = ?", (listened, int(completed), play_id))
        return play_id

    def play_totals(self, since: float) -> tuple[float, int, int, int]:
        """(seconds listened, plays, distinct songs, distinct artists) since a unix time."""
        row = self._query(
            "SELECT COALESCE(SUM(listened), 0), COALESCE(SUM(completed), 0), "
            "COUNT(DISTINCT title || char(0) || artist), COUNT(DISTINCT artist) "
            "FROM plays WHERE started >= ?", (since,))[0]
        return float(row[0]), int(row[1]), int(row[2]), int(row[3])

    def play_top(self, kind: str, since: float, limit: int):
        """The most listened artists, albums or songs since a unix time, as rows of
        (artist, album, title, track_id, seconds); album/title are '' and track_id NULL where they
        don't apply (track_id: the song's latest id, to play it if it's still in the library)."""
        columns = {"artists": "artist, '' AS album, '' AS title, NULL AS track_id",
                   "albums": "artist, album, '' AS title, NULL AS track_id",
                   "songs": "artist, MAX(album) AS album, title, MAX(track_id) AS track_id"}[kind]
        group = {"artists": "artist", "albums": "artist, album", "songs": "title, artist"}[kind]
        return self._query(
            f"SELECT {columns}, SUM(listened) AS seconds FROM plays WHERE started >= ? "
            f"GROUP BY {group} ORDER BY seconds DESC LIMIT ?", (since, limit))

    def play_days(self, since: float, until: float) -> dict[str, tuple[float, int]]:
        """{'YYYY-MM-DD': (seconds, plays)} per local day, for sessions started in [since, until)."""
        rows = self._query(
            "SELECT date(started, 'unixepoch', 'localtime') AS day, SUM(listened), SUM(completed) "
            "FROM plays WHERE started >= ? AND started < ? GROUP BY day", (since, until))
        return {r[0]: (float(r[1]), int(r[2])) for r in rows}

    def listening_days(self) -> list[str]:
        """Every local day ('YYYY-MM-DD') with any listening, for streaks."""
        return [r[0] for r in self._query(
            "SELECT DISTINCT date(started, 'unixepoch', 'localtime') FROM plays ORDER BY 1")]

    def first_play(self) -> float | None:
        """When the first recorded session started, or None."""
        return self._query("SELECT MIN(started) FROM plays")[0][0]

    # ---- settings -----------------------------------------------------

    def get_setting(self, key: str, default=None):
        """A saved setting (stored as JSON), or `default`."""
        rows = self._query("SELECT value FROM settings WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0][0])
        except ValueError:
            return default

    def delete_setting(self, key: str):
        self._write("DELETE FROM settings WHERE key = ?", (key,))

    def set_setting(self, key: str, value):
        """Save a setting; any JSON-serializable value."""
        self._write(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
