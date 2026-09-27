"""Reusable, sortable song table used by every track-based page: a compact column
with the title and artist stacked in one cell, the duration and a favorite heart."""

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ..library import TrackItem, format_total
from .menus import AppMenu
from .widgets import (HEART_OFF, HEART_ON, PLAYLIST_ICON, fill_playlist_checks, follow_compact, ordering, song_header,
                      update_heart)


class SongList(Gtk.Box):
    """
    `win` is the main window, which provides .player, .library, .toast(), .show_artist()
    and .new_playlist_with(). Override hooks via constructor kwargs:
      playlist_id: enables "Remove from Playlist" in the context menu.
      queue_from: a callable taking the double-clicked song and returning (songs to queue, their
        queue_source); it plays from that song. By default the list itself. Search results use the
        window's library_queue (All Songs, or a hidden artist's own songs).
      queue_source: passed to the player with this list's own queue ("library" for All Songs and
        genre pages, so hiding an artist takes their songs out of it).
    """

    def __init__(self, win, *, title: str = "", empty_icon: str = "audio-x-generic-symbolic",
                 empty_title: str = "No Songs", empty_description: str = "",
                 show_track_no: bool = False, show_header: bool = True, queue_from=None,
                 queue_source: str | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.playlist_id: int | None = None
        self.queue_from = queue_from
        self.queue_source = queue_source
        self._menu_items: list[TrackItem] = []
        self._summary = ""

        # ---- header ----
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.add_css_class("page-header")
        self.header = header
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label=title, xalign=0, ellipsize=3)
        self.title_label.add_css_class("page-title")
        self.subtitle_label = Gtk.Label(xalign=0)
        self.subtitle_label.add_css_class("page-subtitle")
        titles.append(self.title_label)
        titles.append(self.subtitle_label)
        header.append(titles)

        self.header_actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.play_button = Gtk.Button()
        self.play_button.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Play"))
        self.play_button.add_css_class("suggested-action")
        self.play_button.add_css_class("pill")
        self.play_button.add_css_class("play-all")
        self.play_button.connect("clicked", lambda *_: self.play_all(shuffle=False))
        self.shuffle_button = Gtk.Button()
        self.shuffle_button.set_child(Adw.ButtonContent(icon_name="media-playlist-shuffle-symbolic", label="Shuffle"))
        self.shuffle_button.add_css_class("pill")
        self.shuffle_button.add_css_class("shuffle-all")
        self.shuffle_button.connect("clicked", lambda *_: self.play_all(shuffle=True))
        self.header_actions.append(self.play_button)
        self.header_actions.append(self.shuffle_button)
        header.append(self.header_actions)
        header.set_visible(show_header)
        self.append(header)
        follow_compact(win, self, SongList._apply_compact)

        # ---- model ----
        self.store = Gio.ListStore(item_type=TrackItem)

        self.view = Gtk.ColumnView(show_row_separators=False, reorderable=False, hexpand=True, vexpand=True)
        self.view.add_css_class("song-list")
        self.view.add_css_class("data-table")
        self.view.set_margin_start(12)
        self.sort_model = Gtk.SortListModel(model=self.store, sorter=self.view.get_sorter())
        self.selection = Gtk.MultiSelection(model=self.sort_model)
        self.view.set_model(self.selection)
        self.view.connect("activate", self._on_activate)

        if show_track_no:
            self._add_column("#", *self._text_cell(lambda i: str(i.track_no) if i.track_no else "", xalign=1,
                                                   css="number-cell track-number"),
                             fixed=40, sorter=lambda a, b: ordering((a.disc_no, a.track_no), (b.disc_no, b.track_no)))
        self._add_column("Song", self._setup_song, self._bind_song, expand=True,
                         sorter=lambda a, b: ordering(a.title_key, b.title_key))
        self._add_column("Duration", *self._text_cell(lambda i: i.duration_text, xalign=1, css="number-cell duration-cell"),
                         fixed=84, sorter=lambda a, b: ordering(a.duration, b.duration))
        self._add_column("", self._setup_heart, self._bind_heart, fixed=48)

        # Right-click / long-press context menu
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda g, n, x, y: self._on_context(x, y))
        self.view.add_controller(click)
        press = Gtk.GestureLongPress(touch_only=True)
        press.connect("pressed", lambda g, x, y: self._on_context(x, y))
        self.view.add_controller(press)
        self._install_actions()

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.view)

        self.empty_page = Adw.StatusPage(icon_name=empty_icon, title=empty_title, description=empty_description)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(self.empty_page, "empty")
        self.append(self.stack)

    @staticmethod
    def _apply_compact(song_list, level):
        """Phone-sized windows: the header's buttons go under the title."""
        tiny = level == 2
        song_list.header.set_orientation(Gtk.Orientation.VERTICAL if tiny else Gtk.Orientation.HORIZONTAL)
        song_list.header_actions.set_halign(Gtk.Align.START if tiny else Gtk.Align.FILL)

    # ---- columns ----

    def _add_column(self, title, setup, bind, *, expand=False, fixed=None, sorter=None):
        """Add a column with its cell factory and optional sorter."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", self._unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory, expand=expand, resizable=expand)
        if fixed:
            col.set_fixed_width(fixed)
        if sorter:
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _data: sorter(a, b)))
        self.view.append_column(col)
        return col

    def _text_cell(self, getter, xalign=0.0, css=None):
        """setup/bind callbacks for a plain text cell showing getter(item)."""
        def setup(_f, li):
            label = Gtk.Label(xalign=xalign, ellipsize=3, hexpand=True)  # 3 = Pango.EllipsizeMode.END
            for name in (css or "").split():
                label.add_css_class(name)
            li.set_child(label)

        def bind(_f, li):
            label, item = li.get_child(), li.get_item()
            label.set_text(getter(item))
            label._item = item
            self._track_playing(label, item)

        return setup, bind

    def _setup_song(self, _f, li):
        """The Song cell: title above artist."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(xalign=0, ellipsize=3)
        title.add_css_class("song-title")
        artist = Gtk.Label(xalign=0, ellipsize=3)
        artist.add_css_class("song-artist")
        artist.add_css_class("caption")
        box.append(title)
        box.append(artist)
        box._labels = (title, artist)
        li.set_child(box)

    def _bind_song(self, _f, li):
        """Fill the Song cell and follow the song's `playing` flag."""
        box, item = li.get_child(), li.get_item()
        title, artist = box._labels
        title.set_text(item.title)
        artist.set_text(item.artist)
        box._item = item
        self._track_playing(title, item)
        box._handlers = title._handlers  # _unbind looks for handlers on the cell's child

    def _setup_heart(self, _f, li):
        """The favorite heart cell."""
        btn = Gtk.Button(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.add_css_class("circular")
        btn.add_css_class("heart-button")
        btn.connect("clicked", lambda b: self.win.library.toggle_favorite(b._item))
        li.set_child(btn)

    def _bind_heart(self, _f, li):
        """Fill the heart and follow the song's `favorite` flag."""
        btn, item = li.get_child(), li.get_item()
        btn._item = item
        update_heart(btn, item.favorite)
        btn._handlers = [(item, item.connect("notify::favorite", lambda it, _p: update_heart(btn, it.favorite)))]

    def _track_playing(self, widget, item):
        """Highlight `widget` while `item` is the playing song (the handler is dropped on unbind)."""
        def update(*_):
            if item.playing:
                widget.add_css_class("playing-row")
            else:
                widget.remove_css_class("playing-row")
        update()
        widget._handlers = [(item, item.connect("notify::playing", update))]

    @staticmethod
    def _unbind(_f, li):
        """A row is being reused: disconnect the handlers its cell had on the old song."""
        child = li.get_child()
        for obj, hid in getattr(child, "_handlers", []):
            obj.disconnect(hid)
        child._handlers = []
        child._item = None

    # ---- data ----

    def set_items(self, items: list[TrackItem]):
        """Show these songs (replacing the current ones) and update the summary."""
        self.store.splice(0, self.store.get_n_items(), items)
        total = sum(i.duration for i in items)
        n = len(items)
        self._summary = f"{n:,} song{'s' if n != 1 else ''} · {format_total(total)}" if n else ""
        self._update_state()

    def set_title(self, title: str):
        """Change the page title."""
        self.title_label.set_text(title)

    def visible_items(self) -> list[TrackItem]:
        """The songs in the order they're shown (after sorting)."""
        return [self.sort_model.get_item(i) for i in range(self.sort_model.get_n_items())]

    def _update_state(self):
        """Show the list or the empty page, and enable Play/Shuffle when there are songs."""
        has_items = self.store.get_n_items() > 0
        self.stack.set_visible_child_name("list" if has_items else "empty")
        self.subtitle_label.set_text(self._summary)
        self.play_button.set_sensitive(has_items)
        self.shuffle_button.set_sensitive(has_items)

    # ---- playback ----

    def play_all(self, shuffle=False):
        """Play every shown song in order, or shuffled."""
        items = self.visible_items()
        if shuffle:
            self.win.player.shuffle_play(items, self.queue_source)
        else:
            self.win.player.set_shuffle(False)
            self.win.player.play_tracks(items, 0, self.queue_source)

    def _on_activate(self, _view, position):
        """A song was double-clicked (or activated): play the list from it, or with `queue_from`,
        the list it gives for this song, from this song."""
        if self.queue_from is None:
            self.win.player.play_tracks(self.visible_items(), position, self.queue_source)
            return
        item = self.sort_model.get_item(position)
        queue, source = self.queue_from(item)
        self.win.player.play_tracks(queue, queue.index(item) if item in queue else 0, source)

    # ---- context menu ----

    def _item_at(self, x, y):
        """The song under the pointer, or None."""
        widget = self.view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not self.view:
            for candidate in (widget, widget.get_first_child(),
                              widget.get_first_child().get_first_child() if widget.get_first_child() else None):
                item = getattr(candidate, "_item", None) if candidate is not None else None
                if item is not None:
                    return item
            widget = widget.get_parent()
        return None

    def _position_of(self, item):
        """A song's row position in the sorted list, or -1."""
        for i in range(self.sort_model.get_n_items()):
            if self.sort_model.get_item(i) is item:
                return i
        return -1

    def _selected_items(self):
        """The selected songs."""
        bitset = self.selection.get_selection()
        return [self.sort_model.get_item(bitset.get_nth(i)) for i in range(bitset.get_size())]

    def _on_context(self, x, y):
        """Right-click / long-press: open the song menu for the song (or the selection it's part of)."""
        item = self._item_at(x, y)
        if item is None:
            return
        pos = self._position_of(item)
        if pos >= 0 and self.selection.is_selected(pos):
            self._menu_items = self._selected_items()
        else:
            if pos >= 0:
                self.selection.select_item(pos, True)
            self._menu_items = [item]

        self._show_menu(x, y)

    def _show_menu(self, x, y):
        """Build and pop up the song menu for self._menu_items."""
        items = list(self._menu_items)
        menu = AppMenu(width=270)
        page = menu.page
        page.add_header(song_header(self.win, items))
        act = lambda name: (lambda: self.activate_action(f"songs.{name}", None))  # noqa: E731
        page.add_section()
        page.add_item("media-playback-start-symbolic", "Play", act("play"))
        page.add_item("musicapp-queue-symbolic", "Add to Queue", act("enqueue"))
        page.add_section()
        page.add_submenu(PLAYLIST_ICON, "Add to Playlist",
                         lambda sub: fill_playlist_checks(sub, self.win, items))
        all_fav = all(i.favorite for i in items)
        page.add_item(HEART_OFF if all_fav else HEART_ON,
                      "Remove from Favorites" if all_fav else "Add to Favorites", act("favorite"))
        if self.playlist_id is not None:
            page.add_item("list-remove-symbolic", "Remove from This Playlist", act("remove-from-playlist"),
                          destructive=True)
        if len(items) == 1:
            page.add_section()
            page.add_item("media-optical-symbolic", "Go to Album", act("goto-album"))
            page.add_item("avatar-default-symbolic", "Go to Artist", act("goto-artist"))

        menu.set_halign(Gtk.Align.START)
        menu.set_parent(self.view)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        menu.set_pointing_to(rect)
        menu.set_position(Gtk.PositionType.BOTTOM)
        menu.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() if p.get_parent() else None))
        menu.popup()

    def _install_actions(self):
        """The song menu's actions (songs.*), acting on self._menu_items."""
        group = Gio.SimpleActionGroup()

        def add(name, callback, param_type=None):
            action = Gio.SimpleAction.new(name, param_type)
            action.connect("activate", callback)
            group.add_action(action)

        player = self.win.player
        library = self.win.library
        add("play", lambda *_: player.play_tracks(self._menu_items, 0))
        add("enqueue", lambda *_: (player.enqueue(self._menu_items), self.win.toast(self._count_text("will play next"))))
        add("favorite", lambda *_: self._toggle_favorite())
        add("goto-artist", lambda *_: self.win.show_artist(self._menu_items[0].artist_key))
        add("goto-album", lambda *_: self.win.show_album((self._menu_items[0].artist_key, self._menu_items[0].album)))
        add("new-playlist", lambda *_: self.win.new_playlist_with(list(self._menu_items)))
        add("remove-from-playlist", lambda *_: library.remove_from_playlist(self.playlist_id, self._menu_items))
        add("add-to-playlist", self._on_add_to_playlist, GLib.VariantType.new("i"))
        self.insert_action_group("songs", group)

    def _count_text(self, suffix):
        """"“Title” <suffix>" for one song, "N songs <suffix>" for several."""
        n = len(self._menu_items)
        return f"“{self._menu_items[0].title}” {suffix}" if n == 1 else f"{n} songs {suffix}"

    def _toggle_favorite(self):
        """Favorite all the menu's songs, or unfavorite them if all already are."""
        target = not all(i.favorite for i in self._menu_items)
        for item in self._menu_items:
            self.win.library.set_favorite(item, target)

    def _on_add_to_playlist(self, _action, param):
        """Add the menu's songs to a playlist and say how many were added."""
        pid = param.unpack()
        added = self.win.library.add_to_playlist(pid, self._menu_items)
        name = self.win.library.playlist_name(pid) or "playlist"
        if added:
            self.win.toast(f"Added {added} song{'s' if added != 1 else ''} to “{name}”")
        else:
            self.win.toast(f"Already in “{name}”")

