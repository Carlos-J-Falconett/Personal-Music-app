"""Background library scanner. Only files that are new or whose mtime/size changed get re-read."""

import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from gi.repository import GLib, GObject

from .config import AUDIO_EXTENSIONS
from .tags import safe_read_tags

BATCH_SIZE = 200
PROCESS_POOL_THRESHOLD = 100


def walk_audio_files(root: str) -> dict[str, tuple[float, int]]:
    """Recursively collect {path: (mtime, size)} for audio files under `root`."""
    found = {}
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif os.path.splitext(entry.name)[1].lower() in AUDIO_EXTENSIONS:
                            st = entry.stat()
                            found[entry.path] = (st.st_mtime, st.st_size)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


class Scanner(GObject.Object):
    """Signals are always emitted on the main thread."""

    __gsignals__ = {
        "started": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # done, total files to read
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        # added-or-updated, removed
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self, db, root: str):
        super().__init__()
        self.db = db
        self.root = str(root)
        self._lock = threading.Lock()
        self._running = False
        self._pending: set[str] = set()

    @property
    def running(self):
        return self._running

    def scan(self, dirs=None):
        """Scan `dirs` (default: the whole library). Requests made while a scan
        is running are merged and run right after it."""
        dirs = set(dirs or [self.root])
        with self._lock:
            self._pending |= dirs
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._worker, daemon=True, name="library-scan").start()

    def _worker(self):
        """Background thread: run pending scans (merging requests that arrive meanwhile) until none remain."""
        GLib.idle_add(self.emit, "started")
        total_changed = total_removed = 0
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    break
                dirs = _collapse(self._pending)
                self._pending = set()
            for d in dirs:
                try:
                    changed, removed = self._scan_dir(d)
                except Exception as exc:  # keep the app alive if one folder misbehaves
                    print(f"[scanner] error scanning {d}: {exc}")
                    changed = removed = 0
                total_changed += changed
                total_removed += removed
        GLib.idle_add(self.emit, "finished", total_changed, total_removed)

    def _scan_dir(self, root: str) -> tuple[int, int]:
        """Bring the database in line with one folder tree: read new or changed files, forget removed ones.
        Returns (files read, files removed)."""
        found = walk_audio_files(root) if os.path.isdir(root) else {}
        known = self.db.file_index(root)
        # A missing or empty library folder (renamed, unmounted) must not wipe the library:
        # deleting tracks also deletes their favorites, playlist entries and EQ.
        if root.rstrip("/") == self.root.rstrip("/") and known and not found:
            print(f"[scanner] {root} is missing or has no music; keeping the library as it is")
            return 0, 0

        to_read = [p for p, sig in found.items() if known.get(p) != sig]
        removed = [p for p in known if p not in found]
        self.db.delete_paths(removed)

        total = len(to_read)
        if total:
            batch = []
            jobs = [(p, self.root) for p in to_read]
            # Tag parsing is pure Python, so big scans use processes to get past the GIL.
            if total >= PROCESS_POOL_THRESHOLD:
                pool = ProcessPoolExecutor(
                    max_workers=min(8, os.cpu_count() or 2),
                    mp_context=multiprocessing.get_context("forkserver"),
                )
                results = pool.map(safe_read_tags, jobs, chunksize=32)
            else:
                pool = ThreadPoolExecutor(max_workers=4)
                results = pool.map(safe_read_tags, jobs)
            with pool:
                for done, record in enumerate(results, 1):
                    if record:
                        batch.append(record)
                    if len(batch) >= BATCH_SIZE:
                        self.db.upsert_tracks(batch)
                        batch = []
                    if done % 50 == 0 or done == total:
                        GLib.idle_add(self.emit, "progress", done, total)
            self.db.upsert_tracks(batch)
        return total, len(removed)


def _collapse(dirs: set[str]) -> list[str]:
    """Drop directories already covered by an ancestor in the set."""
    result = []
    for d in sorted(dirs, key=len):
        if not any(d == r or d.startswith(r.rstrip("/") + "/") for r in result):
            result.append(d)
    return result
