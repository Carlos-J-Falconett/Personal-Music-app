"""Lyrics for the whole library: the All Songs page's Get Lyrics button, which downloads lyrics for
every song, and the Missing Lyrics dialog (main menu → Missing Lyrics…), which lists the songs whose
lyrics couldn't be found and lets you add them from a local .lrc or .txt file.

Lyrics added here are copied into the app's own lyrics cache (LyricsService.add_from_file); the
music folder and the audio files are never modified."""

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..art import THUMB
from ..library import TrackItem, sort_key
from .widgets import CoverArt, follow_lyrics, lyrics_missing_mark, weak_self

LYRICS_ICON = "musicapp-lyrics-symbolic"


def _songs(n: int) -> str:
    """"1 song", "12 songs"."""
    return f"{n:,} song{'s' if n != 1 else ''}"


class LyricsDownloadButton(Gtk.Button):
    """The Get Lyrics button on the All Songs page: a round lyrics icon beside Play and Shuffle (so
    the page title keeps its room), widening while it runs to show a spinner and "34 / 210". A click
    downloads lyrics for every song in the library (hidden artists included, so the Artists tab's
    "!" counts are complete); clicking again stops it. When it's done a toast sums it up.

    The Missing Lyrics dialog starts its downloads through start() too, so progress always shows
    here, and follows it through the "progress" and "finished" signals."""
    __gsignals__ = {
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),  # (done, total)
        "finished": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, win):
        super().__init__(valign=Gtk.Align.CENTER)
        self.win = win
        self.lyrics = win.app.lyrics
        self.add_css_class("lyrics-download")  # theme colours "Get Lyrics Button" / "… Text"
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Get Lyrics"])  # icon only: name it
        # Idle: the lyrics icon alone (round); running: a spinner and "34 / 210" (a pill).
        self.icons = Gtk.Stack()
        self.icons.add_named(Gtk.Image(icon_name=LYRICS_ICON), "idle")
        self.icons.add_named(Adw.Spinner(), "running")
        self.label = Gtk.Label()
        box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        box.append(self.icons)
        box.append(self.label)
        self.set_child(box)
        self.connect("clicked", lambda *_: self._on_clicked())
        self._show_idle()

    @property
    def running(self) -> bool:
        """Whether a download is under way."""
        return self.lyrics.downloading

    def start(self, tracks=None, fresh=False):
        """Download lyrics for `tracks` (default: the whole library). fresh: forget earlier results
        first, to search lrclib.net again (the dialog's Search Online Again)."""
        if self.running:
            return
        tracks = list(self.win.library.items.values()) if tracks is None else list(tracks)
        if not tracks:
            self.win.toast("No songs to get lyrics for")
            return
        self._show_progress(0, len(tracks))
        self.lyrics.download_all(tracks, self._on_progress, self._on_done, fresh=fresh)

    def _on_clicked(self):
        """Start downloading, or stop the download that's running."""
        if self.running:
            self.lyrics.cancel_download()
            self.set_sensitive(False)  # until it stops, after the song it's on
        else:
            self.start()

    def _show_idle(self):
        """The resting look: icon and "Get Lyrics"."""
        self.icons.set_visible_child_name("idle")
        self.label.set_visible(False)
        self.remove_css_class("pill")
        self.add_css_class("circular")
        self.set_tooltip_text("Get Lyrics: download lyrics for all songs")
        self.set_sensitive(True)

    def _show_progress(self, done, total):
        """While downloading: spinner and "done / total"."""
        self.icons.set_visible_child_name("running")
        self.label.set_text(f"{done:,} / {total:,}")
        self.label.set_visible(True)
        self.remove_css_class("circular")
        self.add_css_class("pill")
        self.set_tooltip_text("Stop getting lyrics")

    def _on_progress(self, done, total):
        """A song was looked up (main thread)."""
        self._show_progress(done, total)
        self.emit("progress", done, total)

    def _on_done(self, found, missing, errors, cancelled):
        """The download finished or was stopped (main thread): sum it up in a toast."""
        self._show_idle()
        summary = f"{found:,} found · {missing:,} missing"
        if errors:
            summary += f" · couldn't reach lrclib.net for {_songs(errors)}"
        self.win.toast(f"Stopped getting lyrics: {summary}" if cancelled else f"Lyrics: {summary}")
        self.emit("finished")


class LyricsDialog(Adw.Dialog):
    """Missing Lyrics: every song whose lyrics couldn't be found, with a search field. Each row has
    an Add… button (clicking the row does the same) that takes lyrics from a .lrc or .txt file;
    a file can also be dropped on the row. Search Online Again asks lrclib.net once more for all of
    them. Rows leave the list as soon as their song has lyrics."""

    def __init__(self, win):
        super().__init__(title="Missing Lyrics", content_width=480, content_height=640)
        self.win = win
        self.lyrics = win.app.lyrics
        self.downloader: LyricsDownloadButton = win.lyrics_button
        self._terms: list[str] = []
        me = weak_self(self)  # for handlers of widgets this dialog owns (see widgets.weak_self)
        self.add_css_class("lyrics-manager")

        # ---- the list ----
        self.store = Gio.ListStore(item_type=TrackItem)
        self.filter = Gtk.CustomFilter.new(lambda item: all(t in item.search_text for t in me._terms))
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.filtered = filtered
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: me._setup_row(li))
        factory.connect("bind", lambda _f, li: me._bind_row(li))
        listview = Gtk.ListView(model=Gtk.NoSelection(model=filtered), factory=factory, single_click_activate=True)
        listview.add_css_class("navigation-sidebar")
        listview.connect("activate", lambda _lv, pos: me._choose_file(filtered.get_item(pos)))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=listview, vexpand=True)

        # Empty states: nothing missing, or nothing looked up yet (with a way to start).
        done_page = Adw.StatusPage(icon_name="object-select-symbolic", title="Every Song Has Lyrics",
                                   description="Songs whose lyrics can't be found will show up here")
        get = Gtk.Button(label="Get Lyrics", halign=Gtk.Align.CENTER)
        get.add_css_class("pill")
        get.add_css_class("lyrics-add")
        get.connect("clicked", lambda *_: me.downloader.start())
        unchecked_page = Adw.StatusPage(icon_name=LYRICS_ICON, title="No Lyrics Checked Yet",
                                        description="Get lyrics for all your songs to see which ones are missing. "
                                                    "The Get Lyrics button in All Songs does the same.",
                                        child=get)
        self.no_match = Adw.StatusPage(icon_name="system-search-symbolic", title="No Matching Songs")
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(done_page, "done")
        self.stack.add_named(unchecked_page, "unchecked")
        self.stack.add_named(self.no_match, "no-match")
        filtered.connect("items-changed", lambda *_: me._sync_page())

        # ---- header: title with the count, and the search field ----
        self.title_widget = Adw.WindowTitle(title="Missing Lyrics")
        search = Gtk.SearchEntry(placeholder_text="Search songs", margin_start=12, margin_end=12, margin_bottom=6)
        search.connect("search-changed", lambda entry: me._on_search(entry))
        self.search = search

        # ---- bottom bar: a hint, the download's progress, and Search Online Again ----
        bottom = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        self.status = Gtk.Label(xalign=0, hexpand=True, wrap=True,
                                label="Click a song or drop a .lrc / .txt file on it to add its lyrics")
        self.status.add_css_class("lyrics-manager-details")
        self.status.add_css_class("caption")
        self.again = Gtk.Button(child=Adw.ButtonContent(icon_name="view-refresh-symbolic",
                                                        label="Search Online Again"))
        self.again.add_css_class("pill")
        self.again.add_css_class("lyrics-download")
        self.again.set_tooltip_text("Ask lrclib.net again for every song in this list")
        self.again.connect("clicked", lambda *_: me._search_again())
        bottom.append(self.status)
        bottom.append(self.again)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar(title_widget=self.title_widget))
        toolbar.add_top_bar(search)
        toolbar.set_content(self.stack)
        toolbar.add_bottom_bar(bottom)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_child(self.toasts)

        # Follow the lyrics and the downloads (stopping when the dialog closes).
        follow_lyrics(self.lyrics, self, lambda dialog, _changed: dialog._reload())
        self._handlers = [
            self.downloader.connect("progress", lambda _b, done, total: me._on_progress(done, total)),
            self.downloader.connect("finished", lambda *_: me._on_finished()),
        ]
        self.connect("closed", lambda *_: me._on_closed())
        self._reload()
        self._sync_download()

    def _on_closed(self):
        """Stop following the downloads."""
        for handler in self._handlers:
            self.downloader.disconnect(handler)
        self._handlers = []

    # ---- list ----

    def _reload(self):
        """Refill the list with the songs that have no lyrics, in library order."""
        tracks = sorted(self.lyrics.missing_tracks(self.win.library.items.values()), key=sort_key)
        self.store.splice(0, self.store.get_n_items(), tracks)
        n = len(tracks)
        self.title_widget.set_subtitle(f"{_songs(n)} without lyrics" if n else "")
        self.again.set_visible(n > 0)
        self._sync_page()

    def _sync_page(self):
        """Show the list, or the right empty page."""
        if self.store.get_n_items() == 0:
            self.stack.set_visible_child_name("done" if self.lyrics.checked else "unchecked")
        elif self.filtered.get_n_items() == 0:
            self.stack.set_visible_child_name("no-match")
        else:
            self.stack.set_visible_child_name("list")
        self.search.set_sensitive(self.store.get_n_items() > 0)

    def _on_search(self, entry):
        """Filter the list by the search words (title, artist, album, genre)."""
        self._terms = entry.get_text().casefold().split()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sync_page()

    def _setup_row(self, li):
        """A song row: cover, title, "artist · album", the "!" and the Add… button. Dropping a file
        on the row adds it too."""
        me = weak_self(self)
        row = Gtk.Box(spacing=12, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        row.add_css_class("lyrics-manager-row")
        cover = CoverArt(self.win.art, 44, art_size=THUMB)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(xalign=0, ellipsize=3)
        title.add_css_class("lyrics-manager-title")
        detail = Gtk.Label(xalign=0, ellipsize=3)
        detail.add_css_class("lyrics-manager-details")
        detail.add_css_class("caption")
        labels.append(title)
        labels.append(detail)
        mark = lyrics_missing_mark("Lyrics aren't available at the moment")
        mark.set_visible(True)  # every song listed here is missing its lyrics
        add = Gtk.Button(child=Adw.ButtonContent(icon_name="document-open-symbolic", label="Add…"),
                         valign=Gtk.Align.CENTER, tooltip_text="Add lyrics from a .lrc or .txt file")
        add.add_css_class("pill")
        add.add_css_class("lyrics-add")  # theme colours "Add Button" / "Add Button Text"
        add.connect("clicked", lambda *_: me._choose_file(row._item))
        for w in (cover, labels, mark, add):
            row.append(w)
        # Drop a lyrics file straight onto the song.
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, file, _x, _y: me._on_drop(row._item, file))
        drop.connect("enter", lambda *_: LyricsDialog._highlight_drop(row, True))
        drop.connect("leave", lambda *_: LyricsDialog._highlight_drop(row, False))
        row.add_controller(drop)
        row._widgets = (cover, title, detail)
        row._item = None
        li.set_child(row)

    @staticmethod
    def _highlight_drop(row, on) -> Gdk.DragAction:
        """Tint a row while a file is dragged over it."""
        (row.add_css_class if on else row.remove_css_class)("drop-target")
        return Gdk.DragAction.COPY

    def _bind_row(self, li):
        """Fill a song row."""
        row, item = li.get_child(), li.get_item()
        cover, title, detail = row._widgets
        row._item = item
        cover.set_track(item)
        title.set_text(item.title)
        detail.set_text(f"{item.artist} · {item.album}" if item.album else item.artist)

    # ---- adding lyrics ----

    def _choose_file(self, item):
        """Pick a .lrc / .txt file for a song (starting in Downloads)."""
        if item is None:
            return
        lyrics_files = Gtk.FileFilter(name="Lyrics files (.lrc, .txt)")
        for pattern in ("*.lrc", "*.LRC", "*.txt", "*.TXT"):
            lyrics_files.add_pattern(pattern)
        lyrics_files.add_mime_type("text/plain")
        everything = Gtk.FileFilter(name="All files")
        everything.add_pattern("*")
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(lyrics_files)
        filters.append(everything)
        chooser = Gtk.FileDialog(title=f"Lyrics for “{item.title}”", filters=filters, default_filter=lyrics_files)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            chooser.set_initial_folder(Gio.File.new_for_path(downloads))
        me = weak_self(self)
        chooser.open(self.win, None, lambda c, result: me._on_chosen(c, result, item))

    def _on_chosen(self, chooser, result, item):
        """A file was picked (or the picker was cancelled)."""
        try:
            file = chooser.open_finish(result)
        except GLib.Error:
            return  # cancelled
        self._add(item, file)

    def _on_drop(self, item, file) -> bool:
        """A file was dropped on a song's row."""
        if item is None:
            return False
        GLib.idle_add(lambda: self._add(item, file) and False)  # after the drop is over
        return True

    def _add(self, item, file):
        """Use a local file as a song's lyrics; the row leaves the list once it's done."""
        path = file.get_path()
        if not path:
            self.toast("Only local files can be used")
            return
        try:
            self.lyrics.add_from_file(item, path)
        except ValueError as exc:
            self.toast(str(exc))
            return
        self.toast(f"Added lyrics for “{item.title}”")

    # ---- searching online again ----

    def _search_again(self):
        """Ask lrclib.net once more for every song in the list."""
        tracks = [self.store.get_item(i) for i in range(self.store.get_n_items())]
        self.downloader.start(tracks, fresh=True)
        self._sync_download()

    def _sync_download(self):
        """While a download runs, the bottom bar shows it and Search Online Again waits."""
        running = self.downloader.running
        self.again.set_sensitive(not running)
        if running:
            self.status.set_text("Getting lyrics…")
        else:
            self.status.set_text("Click a song or drop a .lrc / .txt file on it to add its lyrics")

    def _on_progress(self, done, total):
        """The download moved on a song."""
        self.again.set_sensitive(False)
        self.status.set_text(f"Getting lyrics… {done:,} / {total:,}")

    def _on_finished(self):
        """The download finished: the list is already up to date (see _reload)."""
        self._sync_download()
        self._reload()

    def toast(self, text):
        """Show a short message inside the dialog."""
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
