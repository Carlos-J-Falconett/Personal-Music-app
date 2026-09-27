"""Lyrics lookup: embedded tags → sidecar .lrc/.txt → disk cache → lrclib.net."""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from gi.repository import GLib

from .config import DATA_DIR, VERSION

LYRICS_DIR = DATA_DIR / "lyrics"
LRCLIB = "https://lrclib.net/api"
USER_AGENT = f"MusicAppv5/{VERSION} (local GNOME music player)"
TIMEOUT = 8
NONE_RETRY_SECONDS = 7 * 24 * 3600
DURATION_TOLERANCE = 5

_TIME_TAG = re.compile(r"\[(\d+):(\d{1,2}(?:[.:]\d{1,3})?)\]")
_META_TAG = re.compile(r"^\[(\w+):(.*)\]\s*$")
_FEAT = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|with)\s.*$", re.IGNORECASE)


@dataclass
class Lyrics:
    """A lookup result. `lines` are (seconds, text) for synced lyrics, (None, text) for plain ones."""
    kind: str  # "synced" | "plain" | "instrumental" | "none" | "error"
    lines: list = field(default_factory=list)  # [(seconds or None, text)]
    source: str = ""  # "embedded" | "file" | "lrclib"

    def to_json(self):
        """The cache file's contents (with the time it was saved)."""
        return {"kind": self.kind, "lines": self.lines, "source": self.source, "saved": time.time()}


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into time-ordered (seconds, line) pairs. Handles several time tags on
    one line and the [offset:ms] tag; other [xx:...] metadata lines are skipped."""
    offset = 0.0
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        tags = _TIME_TAG.findall(raw)
        if not tags:
            meta = _META_TAG.match(raw)
            if meta and meta.group(1).lower() == "offset":
                try:
                    offset = int(meta.group(2).strip()) / 1000
                except ValueError:
                    pass
            continue
        words = _TIME_TAG.sub("", raw).strip()
        for minutes, seconds in tags:
            t = int(minutes) * 60 + float(seconds.replace(":", "."))
            lines.append((max(0.0, t - offset), words))
    lines.sort(key=lambda x: x[0])
    return lines


def from_text(text: str, source: str) -> Lyrics | None:
    """Build Lyrics from raw text that may or may not be LRC."""
    text = (text or "").strip()
    if not text:
        return None
    synced = parse_lrc(text)
    if len(synced) >= 3:
        return Lyrics("synced", synced, source)
    plain = [(None, line.strip()) for line in text.splitlines()]
    return Lyrics("plain", plain, source)


def _clean(name: str) -> str:
    """A title or artist without "feat. …" / "(with …)" parts, which confuse the lookup."""
    return _FEAT.sub("", name or "").strip()


class LyricsService:
    """Finds a song's lyrics on worker threads and remembers results for the session."""

    def __init__(self):
        LYRICS_DIR.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics")
        self._memory: dict[str, Lyrics] = {}

    def request(self, track, callback):
        """callback(track, Lyrics) runs on the main thread."""
        cached = self._memory.get(track.path)
        if cached is not None:
            callback(track, cached)
            return
        self._pool.submit(self._lookup_async, track, callback)

    def forget(self, track):
        """Drop cached results for a track (used by Retry)."""
        self._memory.pop(track.path, None)
        self._cache_file(track).unlink(missing_ok=True)

    def _lookup_async(self, track, callback):
        """Worker thread: look up, remember the result, deliver it on the main thread."""
        try:
            result = self.lookup(track)
        except Exception as exc:  # never leave the UI spinning
            print(f"[lyrics] {exc}")
            result = Lyrics("error")
        if result.kind != "error":
            self._memory[track.path] = result
        GLib.idle_add(callback, track, result)

    # ---- lookup chain (runs on worker threads) ----

    def lookup(self, track) -> Lyrics:
        """The lookup chain: embedded tags, then a sidecar file, then the disk cache, then lrclib.net."""
        return (self._embedded(track) or self._sidecar(track) or self._cached(track)
                or self._online(track))

    @staticmethod
    def _embedded(track) -> Lyrics | None:
        """Lyrics stored in the audio file's tags (ID3 SYLT/USLT, Vorbis/FLAC/MP4 fields)."""
        try:
            audio = mutagen.File(track.path)
        except Exception:
            return None
        tags = getattr(audio, "tags", None)
        if tags is None:
            return None
        if hasattr(tags, "getall"):  # ID3
            for frame in tags.getall("SYLT"):
                lines = [(ms / 1000, text) for text, ms in frame.text] if frame.format == 2 else []
                if lines:
                    return Lyrics("synced", sorted(lines), "embedded")
            for frame in tags.getall("USLT"):
                found = from_text(frame.text, "embedded")
                if found:
                    return found
            return None
        for key in ("lyrics", "unsyncedlyrics", "LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"):
            try:
                value = tags.get(key)
            except (KeyError, ValueError):
                value = None
            if value:
                found = from_text(value[0] if isinstance(value, list) else str(value), "embedded")
                if found:
                    return found
        return None

    @staticmethod
    def _sidecar(track) -> Lyrics | None:
        """Lyrics in a .lrc or .txt file next to the song."""
        base = Path(track.path)
        for ext in (".lrc", ".LRC", ".txt"):
            candidate = base.with_suffix(ext)
            if candidate.is_file():
                try:
                    found = from_text(candidate.read_text(encoding="utf-8", errors="replace"), "file")
                except OSError:
                    continue
                if found:
                    return found
        return None

    def _cache_file(self, track) -> Path:
        """The disk cache file for a song's online lookup."""
        return LYRICS_DIR / f"{hashlib.sha1(track.path.encode()).hexdigest()}.json"

    def _cached(self, track) -> Lyrics | None:
        """A previous online result; "not found" results are retried after a week."""
        path = self._cache_file(track)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        try:
            if data.get("kind") == "none" and time.time() - data.get("saved", 0) > NONE_RETRY_SECONDS:
                return None
            return Lyrics(data["kind"], [tuple(x) for x in data.get("lines", [])], data.get("source", ""))
        except (KeyError, TypeError, AttributeError):  # a damaged cache file: look again
            return None

    def _save(self, track, lyrics: Lyrics):
        """Cache an online result."""
        try:
            self._cache_file(track).write_text(json.dumps(lyrics.to_json()))
        except OSError:
            pass

    def _online(self, track) -> Lyrics:
        """Ask lrclib.net (cached answers first, then a full lookup, then a search by duration).
        Network trouble gives an "error" result, which isn't cached."""
        title, artist = _clean(track.title), _clean(track.artist)
        params = {
            "track_name": title,
            "artist_name": artist,
            "album_name": track.album,
            "duration": round(track.duration),
        }
        record = None
        for attempt in range(2):  # LRCLIB is occasionally slow; retry a timeout once
            try:
                # get-cached answers instantly from LRCLIB's own database; get may also
                # consult external sources, which is slower.
                record = (self._fetch("get-cached", params) or self._fetch("get", params)
                          or self._search(title, artist, track.duration))
                break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                print(f"[lyrics] lrclib attempt {attempt + 1} failed: {exc}")
        else:
            return Lyrics("error")

        if record is None:
            result = Lyrics("none", [], "lrclib")
        elif record.get("instrumental"):
            result = Lyrics("instrumental", [], "lrclib")
        else:
            result = (from_text(record.get("syncedLyrics") or "", "lrclib")
                      or from_text(record.get("plainLyrics") or "", "lrclib")
                      or Lyrics("none", [], "lrclib"))
        self._save(track, result)
        return result

    @staticmethod
    def _fetch(endpoint: str, params: dict):
        """GET an lrclib.net endpoint as JSON; None for "not found"."""
        url = f"{LRCLIB}/{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise

    def _search(self, title, artist, duration):
        """The best search result within DURATION_TOLERANCE seconds, synced lyrics preferred."""
        results = self._fetch("search", {"track_name": title, "artist_name": artist}) or []
        close = [r for r in results if abs((r.get("duration") or 0) - duration) <= DURATION_TOLERANCE]
        if not close:
            return None
        # Prefer synced lyrics, then the closest duration.
        close.sort(key=lambda r: (not r.get("syncedLyrics"), abs((r.get("duration") or 0) - duration)))
        return close[0]

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
