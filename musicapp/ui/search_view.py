"""Global search across artists, albums and songs."""

from gi.repository import Adw, Gio, Gtk

from ..library import AlbumItem
from .artists_view import ArtistItem, grid_scroller
from .cards import AlbumCard, ArtistCard
from .song_list import SongList
from .widgets import NarrowColumn

PREVIEW_CARDS = 6
PREVIEW_SONGS = 8


def _glass(widget):
    widget.add_css_class("content-glass")
    widget.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
    return widget


def _matches(terms, text):
    return all(t in text for t in terms)


class SearchView(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self._query = ""
        self._artists: list[tuple[str, list]] = []
        self._albums: list[AlbumItem] = []
        self._songs: list = []

        bar = Gtk.Box(margin_top=12, margin_bottom=6, margin_start=24, margin_end=24)
        self.toggles = Adw.ToggleGroup(halign=Gtk.Align.START)
        self.toggles.add_css_class("round")
        for name in ("all", "songs", "artists", "albums"):
            self.toggles.add(Adw.Toggle(name=name, label=name.title()))
        self.toggles.connect("notify::active-name", lambda *_: self._show_active())
        bar.append(self.toggles)
        self.append(bar)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)

        # "All": a preview of each category
        self.overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                margin_start=24, margin_end=24, margin_bottom=24)
        overview_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.overview)
        overview_scroller.add_css_class("content-glass")
        overview_scroller.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.stack.add_named(overview_scroller, "all")

        # Full lists
        self.songs = SongList(win, show_header=False)
        self.stack.add_named(NarrowColumn(self.songs), "songs")

        self.artist_store = Gio.ListStore(item_type=ArtistItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(ArtistCard(win.art)))
        factory.connect("bind", lambda _f, li: li.get_child().set_artist(li.get_item().name, li.get_item().tracks))
        artist_grid = Gtk.GridView(model=Gtk.NoSelection(model=self.artist_store), factory=factory,
                                   single_click_activate=True, max_columns=12)
        artist_grid.add_css_class("card-grid")
        artist_grid.connect("activate", lambda g, pos: win.show_artist(self.artist_store.get_item(pos).name))
        self.stack.add_named(_glass(grid_scroller(artist_grid)), "artists")

        self.album_store = Gio.ListStore(item_type=AlbumItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(AlbumCard(win.art, show_artist=True)))
        factory.connect("bind", lambda _f, li: li.get_child().set_album(li.get_item()))
        album_grid = Gtk.GridView(model=Gtk.NoSelection(model=self.album_store), factory=factory,
                                  single_click_activate=True, max_columns=12)
        album_grid.add_css_class("card-grid")
        album_grid.connect("activate", lambda g, pos: win.show_album(self.album_store.get_item(pos).key))
        self.stack.add_named(_glass(grid_scroller(album_grid)), "albums")

        self.no_results = Adw.StatusPage(icon_name="system-search-symbolic", title="No Results")
        self.stack.add_named(_glass(self.no_results), "none")
        self.append(self.stack)

    # ---- searching ----

    def search(self, text: str):
        self._query = text.strip()
        terms = text.casefold().split()
        library = self.win.library
        self._artists = [(n, t) for n, t in library.artists() if _matches(terms, n.casefold())]
        self._albums = [a for a in library.all_albums() if _matches(terms, a.search_text)]
        self._songs = [i for i in library.sorted_items if _matches(terms, i.search_text)]

        self.songs.set_items(self._songs)
        self.artist_store.splice(0, self.artist_store.get_n_items(), [ArtistItem(n, t) for n, t in self._artists])
        self.album_store.splice(0, self.album_store.get_n_items(), self._albums)
        for name, items in (("songs", self._songs), ("artists", self._artists), ("albums", self._albums)):
            self.toggles.get_toggle_by_name(name).set_label(f"{name.title()} · {len(items)}")
        self._build_overview()
        self._show_active()

    def refresh(self):
        if self._query:
            self.search(self._query)

    def reset(self):
        self.toggles.set_active_name("all")

    def _show_active(self):
        if not (self._artists or self._albums or self._songs):
            self.no_results.set_description(f"Nothing matches “{self._query}”")
            self.stack.set_visible_child_name("none")
            return
        self.stack.set_visible_child_name(self.toggles.get_active_name() or "all")

    # ---- "All" overview ----

    def _build_overview(self):
        while child := self.overview.get_first_child():
            self.overview.remove(child)

        if self._artists:
            self._section("Artists", len(self._artists), "artists")
            flow = self._flowbox()
            for name, tracks in self._artists[:PREVIEW_CARDS]:
                card = ArtistCard(self.win.art)
                card.set_artist(name, tracks)
                flow.append(card)
            flow.connect("child-activated", lambda f, child: self.win.show_artist(child.get_child().name))
            self.overview.append(flow)

        if self._albums:
            self._section("Albums", len(self._albums), "albums")
            flow = self._flowbox()
            for album in self._albums[:PREVIEW_CARDS]:
                card = AlbumCard(self.win.art, show_artist=True)
                card.set_album(album)
                flow.append(card)
            flow.connect("child-activated", lambda f, child: self.win.show_album(child.get_child().album.key))
            self.overview.append(flow)

        if self._songs:
            self._section("Songs", len(self._songs), "songs")
            rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            rows.add_css_class("boxed-list")
            for index, item in enumerate(self._songs[:PREVIEW_SONGS]):
                # use_markup must be off before the text is set (names often contain "&").
                row = Adw.ActionRow(use_markup=False, activatable=True, title_lines=1, subtitle_lines=1)
                row.set_title(item.title)
                row.set_subtitle(f"{item.artist} — {item.album}")
                duration = Gtk.Label(label=item.duration_text)
                duration.add_css_class("number-cell")
                row.add_suffix(duration)
                row.connect("activated", lambda _r, i=index: self.win.player.play_tracks(self._songs, i))
                rows.append(row)
            self.overview.append(rows)

    def _section(self, title: str, count: int, target: str):
        header = Gtk.Box(margin_top=18, margin_bottom=6)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("title-3")
        label.add_css_class("section-title")
        header.append(label)
        if count > (PREVIEW_SONGS if target == "songs" else PREVIEW_CARDS):
            see_all = Gtk.Button(label=f"See All {count}")
            see_all.add_css_class("flat")
            see_all.connect("clicked", lambda *_: self.toggles.set_active_name(target))
            header.append(see_all)
        self.overview.append(header)

    @staticmethod
    def _flowbox() -> Gtk.FlowBox:
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, activate_on_single_click=True,
                           max_children_per_line=PREVIEW_CARDS, min_children_per_line=1,
                           column_spacing=6, row_spacing=6)
        flow.add_css_class("card-flow")
        return flow
