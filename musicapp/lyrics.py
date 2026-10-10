"""Lyrics lookup: embedded tags → sidecar .lrc/.txt → disk cache → lrclib.net.

The service also remembers which songs have no lyrics ("missing": a lookup found none), saved
across launches, so the Now Playing and Artists views can mark them and the Missing Lyrics dialog
can list them. It can download lyrics for the whole library at once (download_all), and take
lyrics for a song from a local .lrc/.txt file (add_from_file). Lyrics added by hand are kept in
the app's own cache; the music folder and audio files are never modified."""

import hashlib
import json
import re
import threading
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
MISSING_FILE = DATA_DIR / "lyrics-missing.json"  # paths of songs whose lookup found no lyrics
NOTIFY_INTERVAL_S = 1.0  # during a bulk download, tell listeners about changes at most this often
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
    source: str = ""  # "embedded" | "file" | "lrclib" | "manual" (added from a local file)

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
    """Finds a song's lyrics on worker threads and remembers results for the session. Keeps the
    persistent set of songs without lyrics (`missing`, track paths), and tells `listeners`
    (callables taking the set of changed paths, run on the main thread) when it changes."""

    def __init__(self):
        LYRICS_DIR.mkdir(parents=True, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics")
        # Bulk downloads get their own worker, so songs opened meanwhile don't wait behind them.
        self._bulk = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lyrics-bulk")
        self._memory: dict[str, Lyrics] = {}
        self._lock = threading.Lock()  # guards missing, _pending and _dirty (worker threads write them)
        self.missing: set[str] = self._load_missing()
        self.checked = MISSING_FILE.exists()  # whether any lookup has recorded a status yet
        self.listeners: list = []
        self._pending: set[str] = set()  # changed paths not yet announced to the listeners
        self._dirty = False  # missing changed since it was last saved
        self._cancel = threading.Event()
        self._downloading = False

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
        self._record(track, result)
        self._flush()
        GLib.idle_add(callback, track, result)

    # ---- songs without lyrics ----

    def is_missing(self, track) -> bool:
        """Whether a lookup found no lyrics for this song (instrumentals and network errors don't count)."""
        return track is not None and track.path in self.missing

    def missing_tracks(self, tracks) -> list:
        """The songs among `tracks` that have no lyrics (paths of deleted songs are ignored)."""
        return [t for t in tracks if t.path in self.missing]

    def _record(self, track, result: Lyrics):
        """Update the missing set from a lookup result (any thread). "none" marks the song; found
        lyrics or an instrumental clear it; an error leaves it as it was."""
        if result.kind == "error":
            return
        with self._lock:
            was = track.path in self.missing
            now = result.kind == "none"
            if not self.checked:
                self.checked = self._dirty = True  # save once, so "never checked" is remembered as over
            if was != now:
                (self.missing.add if now else self.missing.discard)(track.path)
                self._pending.add(track.path)
                self._dirty = True

    def _flush(self):
        """Save the missing set if it changed and announce the changed songs (any thread)."""
        with self._lock:
            changed, self._pending = self._pending, set()
            dirty, self._dirty = self._dirty, False
            snapshot = sorted(self.missing) if dirty else None
        if snapshot is not None:
            try:
                MISSING_FILE.write_text(json.dumps(snapshot))
            except OSError as exc:
                print(f"[lyrics] couldn't save {MISSING_FILE}: {exc}")
        if changed:
            GLib.idle_add(self._notify, changed)

    def _notify(self, changed: set[str]):
        """Main thread: call the listeners with the paths whose status changed."""
        for listener in list(self.listeners):
            listener(changed)
        return False

    @staticmethod
    def _load_missing() -> set[str]:
        """The saved missing set (empty if there's none or it's damaged)."""
        try:
            data = json.loads(MISSING_FILE.read_text())
            return {str(p) for p in data} if isinstance(data, list) else set()
        except (OSError, ValueError):
            return set()

    # ---- whole-library download ----

    @property
    def downloading(self) -> bool:
        """Whether a bulk download is running."""
        return self._downloading

    def download_all(self, tracks, on_progress=None, on_done=None, fresh=False):
        """Look up lyrics for every song in `tracks` on a background worker (the usual chain:
        tags, sidecar file, disk cache, lrclib.net), recording which have none.
        on_progress(done, total) and on_done(found, missing, errors, cancelled) run on the main
        thread. fresh: forget cached results first (to search online again for missing songs)."""
        if self._downloading:
            return
        self._downloading = True
        self._cancel.clear()
        tracks = list(tracks)
        if fresh:
            for track in tracks:
                self.forget(track)
        self._bulk.submit(self._download_worker, tracks, on_progress, on_done)

    def cancel_download(self):
        """Stop a running bulk download after the song it's on."""
        self._cancel.set()

    def _download_worker(self, tracks, on_progress, on_done):
        """Worker thread for download_all."""
        found = missing = errors = 0
        total, last_notify = len(tracks), time.monotonic()
        for done, track in enumerate(tracks, 1):
            if self._cancel.is_set():
                break
            result = self._memory.get(track.path)
            if result is None:
                try:
                    result = self.lookup(track)
                except Exception as exc:  # one bad file mustn't stop the rest
                    print(f"[lyrics] {track.path}: {exc}")
                    result = Lyrics("error")
                if result.kind != "error":
                    self._memory[track.path] = result
            self._record(track, result)
            if result.kind == "none":
                missing += 1
            elif result.kind == "error":
                errors += 1
            else:
                found += 1
            # Batch the announcements so the views aren't redrawn for every song.
            if time.monotonic() - last_notify >= NOTIFY_INTERVAL_S:
                last_notify = time.monotonic()
                self._flush()
            if on_progress is not None:
                GLib.idle_add(on_progress, done, total)
        self._flush()
        cancelled = self._cancel.is_set()
        self._downloading = False
        if on_done is not None:
            GLib.idle_add(on_done, found, missing, errors, cancelled)

    # ---- lyrics added by hand ----

    def add_from_file(self, track, path) -> Lyrics:
        """Use a local .lrc/.txt file as a song's lyrics (main thread). The lyrics are copied into
        the app's cache (they never expire there) and the song stops counting as missing.
        Raises ValueError if the file can't be read or has no lyrics in it."""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"Couldn't read the file: {exc.strerror or exc}") from exc
        lyrics = from_text(text, "manual")
        if lyrics is None:
            raise ValueError("No lyrics in that file")
        self._save(track, lyrics)
        self._memory[track.path] = lyrics
        self._record(track, lyrics)
        self._flush()
        return lyrics

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
        busy = False  # LRCLIB's slow external lookup was overloaded, so its answer is unknown
        for attempt in range(2):  # LRCLIB is occasionally slow; retry a timeout once
            try:
                # get-cached answers instantly from LRCLIB's own database; get may also
                # consult external sources, which is slower (and often answers 503 when busy:
                # then the search still gets its chance, see _fetch_get).
                record = self._fetch("get-cached", params)
                if record is None:
                    record, busy = self._fetch_get(params)
                record = record or self._search(title, artist, track.duration)
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
        # "Not found" while the full lookup was busy isn't certain: don't keep it for a week
        # (the song still counts as missing until a later lookup finds its lyrics).
        if not (busy and result.kind == "none"):
            self._save(track, result)
        return result

    def _fetch_get(self, params) -> tuple[dict | None, bool]:
        """LRCLIB's full lookup: (record or None, whether it was too busy to answer)."""
        try:
            return self._fetch("get", params), False
        except urllib.error.HTTPError as err:
            if err.code >= 500:
                print(f"[lyrics] lrclib get unavailable ({err.code}), searching instead")
                return None, True
            raise

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
        """Stop the workers (a bulk download stops after the song it's on)."""
        self._cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._bulk.shutdown(wait=False, cancel_futures=True)
