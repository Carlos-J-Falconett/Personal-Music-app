"""Genres tab: genre grid → the genre's songs, as a navigation stack (like the Artists tab).

Songs are grouped by their genre tag (Library.genres); songs without one are under "Unknown Genre".
Hidden artists are left out, as in All Songs, and a genre's queue is a "library" queue, so hiding
an artist also takes their songs out of it."""

from gi.repository import Adw, Gio, GObject, Gtk

from ..library import format_total
from .artists_view import back_button, grid_scroller, play_buttons
from .cards import GenreCard
from .song_list import SongList
from .widgets import NarrowColumn

ROOT_TAG = "genres"


class GenreItem(GObject.Object):
    """A genre and its tracks, for list models."""
    __gtype_name__ = "GenreItem"

    def __init__(self, name: str, tracks: list):
        super().__init__()
        self.name = name
        self.tracks = tracks


class GenresView(Adw.Bin):
    """The Genres tab (see the module docstring). Wraps an Adw.NavigationView (which can't be subclassed)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)

        self.store = Gio.ListStore(item_type=GenreItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(GenreCard(win.art)))
        factory.connect("bind", lambda _f, li: li.get_child().set_genre(li.get_item().name, li.get_item().tracks))
        grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                            single_click_activate=True, max_columns=12, min_columns=1)
        grid.add_css_class("card-grid")
        grid.connect("activate", lambda g, pos: self.show_genre(self.store.get_item(pos).name))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("page-header")
        title = Gtk.Label(label="Genres", xalign=0)
        title.add_css_class("page-title")
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("page-subtitle")
        header.append(title)
        header.append(self.count_label)
        box.append(header)
        self.grid_stack = Gtk.Stack(vexpand=True)
        self.grid_stack.add_named(grid_scroller(grid), "grid")
        self.grid_stack.add_named(Adw.StatusPage(icon_name="musicapp-genre-symbolic", title="No Genres",
                                                 description="Songs in your music folder will show up here, "
                                                             "grouped by their genre"), "empty")
        box.append(self.grid_stack)
        box.add_css_class("content-glass")
        box.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.nav.add(Adw.NavigationPage(title="Genres", tag=ROOT_TAG, child=box))

    # ---- data ----

    def refresh(self):
        """Reload the genre grid, and refresh (or leave) the genre page opened from it."""
        genres = self.win.library.genres()
        self.store.splice(0, self.store.get_n_items(), [GenreItem(n, t) for n, t in genres])
        self.count_label.set_text(f"{len(genres):,} genre{'s' if len(genres) != 1 else ''}")
        self.grid_stack.set_visible_child_name("grid" if genres else "empty")
        visible = self.nav.get_visible_page()
        if isinstance(visible, GenrePage) and not visible.refresh():
            self.nav.pop_to_tag(ROOT_TAG)

    # ---- navigation ----

    def show_genre(self, name: str):
        """Show a genre's page."""
        visible = self.nav.get_visible_page()
        if isinstance(visible, GenrePage) and visible.genre == name:
            return
        self.nav.replace([self.nav.find_page(ROOT_TAG), GenrePage(self, name)])


class GenrePage(Adw.NavigationPage):
    """A genre's songs in library order, with Play and Shuffle."""

    def __init__(self, view: GenresView, genre: str):
        super().__init__(title=genre, tag=f"genre:{genre}")
        self.view = view
        self.win = view.win
        self.genre = genre

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(back_button("Genres", lambda: view.nav.pop_to_tag(ROOT_TAG)))
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.add_css_class("page-header")
        kind = Gtk.Label(label="GENRE", xalign=0)
        kind.add_css_class("eyebrow")
        title = Gtk.Label(label=genre, xalign=0, wrap=True, lines=2, ellipsize=3)
        title.add_css_class("page-title")
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.add_css_class("page-subtitle")
        buttons = play_buttons(lambda: self.songs.play_all(False), lambda: self.songs.play_all(True))
        buttons.set_margin_top(8)
        buttons.set_halign(Gtk.Align.START)
        for w in (kind, title, self.subtitle, buttons):
            header.append(w)
        box.append(header)

        # A "library" queue: hiding an artist takes their songs out of it (see the window's _on_artist_hidden).
        self.songs = SongList(self.win, show_header=False, queue_source="library")
        self.songs.set_vexpand(True)
        box.append(self.songs)
        self.set_child(NarrowColumn(box))
        self.refresh()

    def refresh(self) -> bool:
        """Reload the songs and summary. False if the genre has no songs any more."""
        tracks = self.win.library.genre_tracks(self.genre)
        if not tracks:
            return False
        self.songs.set_items(tracks)
        n, artists = len(tracks), len({t.artist_key for t in tracks})
        self.subtitle.set_text(f"{n} song{'s' if n != 1 else ''} · {artists} artist{'s' if artists != 1 else ''} · "
                               f"{format_total(sum(t.duration for t in tracks))}")
        return True
