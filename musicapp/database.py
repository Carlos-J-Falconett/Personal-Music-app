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
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _write(self, sql, params=()):
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
        if not paths:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in paths])
            self._conn.commit()

    def all_tracks(self):
        return self._query("SELECT * FROM tracks")

    # ---- favorites ----------------------------------------------------

    def favorite_ids(self) -> list[int]:
        return [r[0] for r in self._query("SELECT track_id FROM favorites ORDER BY added DESC")]

    def set_favorite(self, track_id: int, favorite: bool):
        if favorite:
            self._write("INSERT OR IGNORE INTO favorites (track_id, added) VALUES (?, ?)", (track_id, time.time()))
        else:
            self._write("DELETE FROM favorites WHERE track_id = ?", (track_id,))

    # ---- playlists ----------------------------------------------------

    def playlists(self):
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
        with self._lock:
            self._conn.executemany(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                [(playlist_id, t) for t in track_ids],
            )
            self._conn.commit()

    # ---- per-song equalizer -------------------------------------------

    def get_track_eq(self, track_id: int) -> list[float] | None:
        rows = self._query("SELECT gains FROM track_eq WHERE track_id = ?", (track_id,))
        if not rows:
            return None
        try:
            return [float(g) for g in json.loads(rows[0][0])]
        except (ValueError, TypeError):
            return None

    def set_track_eq(self, track_id: int, gains: list[float]):
        self._write(
            "INSERT INTO track_eq (track_id, gains) VALUES (?, ?) ON CONFLICT(track_id) DO UPDATE SET gains=excluded.gains",
            (track_id, json.dumps([round(g, 2) for g in gains])),
        )

    def delete_track_eq(self, track_id: int):
        self._write("DELETE FROM track_eq WHERE track_id = ?", (track_id,))

    # ---- settings -----------------------------------------------------

    def get_setting(self, key: str, default=None):
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
        self._write(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
