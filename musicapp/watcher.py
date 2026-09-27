"""Watches the music folder tree for changes and triggers debounced partial rescans."""

import os

from gi.repository import Gio, GLib

DEBOUNCE_MS = 2000

_RELEVANT = {
    Gio.FileMonitorEvent.CHANGES_DONE_HINT,
    Gio.FileMonitorEvent.DELETED,
    Gio.FileMonitorEvent.CREATED,
    Gio.FileMonitorEvent.MOVED_IN,
    Gio.FileMonitorEvent.MOVED_OUT,
    Gio.FileMonitorEvent.RENAMED,
}


class LibraryWatcher:
    """GIO monitors are not recursive, so one monitor is kept per directory."""

    def __init__(self, scanner, root: str):
        self.scanner = scanner
        self.root = str(root)
        self._monitors: dict[str, Gio.FileMonitor] = {}
        self._dirty: set[str] = set()
        self._timeout_id = 0
        scanner.connect("finished", lambda *_: self.refresh())

    def start(self):
        """Start watching (after the first scan)."""
        self.refresh()

    def refresh(self):
        """Sync monitors with the current directory tree."""
        current = set()
        for dirpath, dirnames, _ in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            current.add(dirpath)
        for gone in set(self._monitors) - current:
            self._monitors.pop(gone).cancel()
        for new in current - set(self._monitors):
            try:
                mon = Gio.File.new_for_path(new).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error as exc:
                print(f"[watcher] cannot watch {new}: {exc.message}")
                continue
            mon.connect("changed", self._on_changed, new)
            self._monitors[new] = mon

    def _on_changed(self, _mon, file, other, event, directory):
        """A file or folder changed: remember its folder and (re)start the debounce timer."""
        if event not in _RELEVANT:
            return
        self._dirty.add(directory)
        # A moved-in folder is a new subtree: scan it directly as well.
        for f in (file, other):
            path = f.get_path() if f else None
            if path and os.path.isdir(path):
                self._dirty.add(path)
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
        self._timeout_id = GLib.timeout_add(DEBOUNCE_MS, self._flush)

    def _flush(self):
        """Debounce time is up: rescan the folders that changed."""
        self._timeout_id = 0
        dirs, self._dirty = self._dirty, set()
        self.scanner.scan(dirs)
        return False

    def stop(self):
        """Stop watching."""
        for mon in self._monitors.values():
            mon.cancel()
        self._monitors.clear()
