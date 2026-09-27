"""Artists tab: artist grid → artist's albums → album's songs, as a navigation stack."""

from gi.repository import Adw, Gio, GObject, Gtk

from ..art import LARGE
from ..library import AlbumItem, format_total
from .cards import AlbumCard, ArtistCard
from .song_list import SongList
from .widgets import CoverArt, NarrowColumn, follow_compact

ROOT_TAG = "artists"


class ArtistItem(GObject.Object):
    """An artist and their tracks, for list models."""
    __gtype_name__ = "ArtistItem"

    def __init__(self, name: str, tracks: list):
        super().__init__()
        self.name = name
        self.tracks = tracks


def back_button(label: str, on_click) -> Gtk.Button:
    """A flat "‹ label" button at the top of a pushed page."""
    btn = Gtk.Button(halign=Gtk.Align.START, margin_start=12, margin_top=8)
    btn.set_child(Adw.ButtonContent(icon_name="go-previous-symbolic", label=label))
    btn.add_css_class("flat")
    btn.connect("clicked", lambda *_: on_click())
    return btn


def play_buttons(on_play, on_shuffle) -> Gtk.Box:
    """The Play and Shuffle pills of a page header."""
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    play = Gtk.Button(child=Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Play"))
    play.add_css_class("suggested-action")
    play.add_css_class("pill")
    play.add_css_class("play-all")
    play.connect("clicked", lambda *_: on_play())
    shuffle = Gtk.Button(child=Adw.ButtonContent(icon_name="media-playlist-shuffle-symbolic", label="Shuffle"))
    shuffle.add_css_class("pill")
    shuffle.add_css_class("shuffle-all")
    shuffle.connect("clicked", lambda *_: on_shuffle())
    box.append(play)
    box.append(shuffle)
    return box


def grid_scroller(grid: Gtk.Widget) -> Gtk.ScrolledWindow:
    """A vertical scroller around a card grid."""
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    scroller.set_child(grid)
    return scroller


class ArtistsView(Adw.Bin):
    """Wraps an Adw.NavigationView (which can't be subclassed)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)

        self.store = Gio.ListStore(item_type=ArtistItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(ArtistCard(win.art)))
        factory.connect("bind", lambda _f, li: li.get_child().set_artist(
            li.get_item().name, li.get_item().tracks, win.library.is_hidden(li.get_item().name)))
        grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                            single_click_activate=True, max_columns=12, min_columns=2)
        grid.add_css_class("card-grid")
        grid.connect("activate", lambda g, pos: self.show_artist(self.store.get_item(pos).name))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("page-header")
        title = Gtk.Label(label="Artists", xalign=0)
        title.add_css_class("page-title")
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("page-subtitle")
        header.append(title)
        header.append(self.count_label)
        box.append(header)
        self.grid_stack = Gtk.Stack(vexpand=True)
        self.grid_stack.add_named(grid_scroller(grid), "grid")
        self.grid_stack.add_named(Adw.StatusPage(icon_name="avatar-default-symbolic", title="No Artists",
                                                 description="Songs in your music folder will show up here"), "empty")
        box.append(self.grid_stack)
        box.add_css_class("content-glass")
        box.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.nav.add(Adw.NavigationPage(title="Artists", tag=ROOT_TAG, child=box))

    # ---- data ----

    def refresh(self):
        """Reload the artist grid, and refresh (or leave) the pages opened from it."""
        artists = self.win.library.artists()
        self.store.splice(0, self.store.get_n_items(), [ArtistItem(n, t) for n, t in artists])
        self.count_label.set_text(f"{len(artists):,} artists")
        self.grid_stack.set_visible_child_name("grid" if artists else "empty")
        # Refresh pushed pages; drop back to the grid if what they showed is gone.
        stack = self.nav.get_navigation_stack()
        for i in range(1, stack.get_n_items()):
            page = stack.get_item(i)
            if hasattr(page, "refresh") and not page.refresh():
                self.nav.pop_to_tag(ROOT_TAG)
                break

    # ---- navigation ----

    def show_artist(self, name: str):
        """Show an artist's page."""
        visible = self.nav.get_visible_page()
        if isinstance(visible, ArtistPage) and visible.artist == name:
            return
        root = self.nav.find_page(ROOT_TAG)
        self.nav.replace([root, ArtistPage(self, name)])

    def show_album(self, album: AlbumItem):
        """Show an album's page, with its artist's page behind it."""
        root = self.nav.find_page(ROOT_TAG)
        artist_page = ArtistPage(self, album.artist)
        self.nav.replace([root, artist_page, AlbumPage(self, album.key)])

    def push_album(self, album: AlbumItem):
        """Open an album from the artist page."""
        self.nav.push(AlbumPage(self, album.key))


class ArtistPage(Adw.NavigationPage):
    """An artist's albums, with Play / Shuffle, Change Artist Image, and the Hide toggle that keeps
    their songs out of All Songs and the library queue (they still play from here and playlists)."""

    def __init__(self, view: ArtistsView, artist: str):
        super().__init__(title=artist, tag=f"artist:{artist}")
        self.view = view
        self.win = view.win
        self.artist = artist

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(back_button("Artists", lambda: view.nav.pop_to_tag(ROOT_TAG)))

        header = Gtk.Box(spacing=18)
        header.add_css_class("page-header")
        self.avatar = Adw.Avatar(size=96, text=artist, show_initials=True)
        header.append(self.avatar)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        name = Gtk.Label(label=artist, xalign=0, ellipsize=3)
        name.add_css_class("page-title")
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.add_css_class("page-subtitle")
        titles.append(name)
        titles.append(self.subtitle)
        header.append(titles)
        edit = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Change Artist Image",
                          valign=Gtk.Align.START)
        edit.add_css_class("flat")
        edit.add_css_class("circular")
        edit.connect("clicked", lambda *_: self.win.show_artist_images(artist))
        # Hide from All Songs and the library queue (Library.set_artist_hidden); refresh() keeps
        # the toggle in step when it changes.
        self.hide_toggle = Gtk.ToggleButton(icon_name="view-conceal-symbolic", valign=Gtk.Align.START)
        self.hide_toggle.add_css_class("flat")
        self.hide_toggle.add_css_class("circular")
        self.hide_toggle.connect("toggled", lambda b: self.win.library.set_artist_hidden(artist, b.get_active()))
        header.append(self.hide_toggle)
        header.append(edit)
        self.buttons = play_buttons(lambda: self._play(False), lambda: self._play(True))
        self.buttons.set_halign(Gtk.Align.START)
        self.buttons.set_margin_top(8)
        titles.append(self.buttons)
        self.header = header
        follow_compact(self.win, self, ArtistPage._apply_compact)
        box.append(header)

        self.store = Gio.ListStore(item_type=AlbumItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, li: li.set_child(AlbumCard(self.win.art)))
        factory.connect("bind", lambda _f, li: li.get_child().set_album(li.get_item()))
        grid = Gtk.GridView(model=Gtk.NoSelection(model=self.store), factory=factory,
                            single_click_activate=True, max_columns=12, min_columns=1)
        grid.add_css_class("card-grid")
        grid.connect("activate", lambda g, pos: view.push_album(self.store.get_item(pos)))
        box.append(grid_scroller(grid))
        box.add_css_class("content-glass")
        box.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.set_child(box)
        self.refresh()

    @staticmethod
    def _apply_compact(page, level):
        """Phone-sized windows: a smaller avatar."""
        page.avatar.set_size(72 if level == 2 else 96)
        page.header.set_spacing(12 if level == 2 else 18)

    def refresh(self) -> bool:
        """Reload the albums and summary. False if the artist has no songs any more."""
        albums = self.win.library.albums_for_artist(self.artist)
        if not albums:
            return False
        self.store.splice(0, self.store.get_n_items(), albums)
        tracks = [t for a in albums for t in a.tracks]
        self.tracks = tracks
        hidden = self.win.library.is_hidden(self.artist)
        summary = (f"{len(albums)} album{'s' if len(albums) != 1 else ''} · "
                   f"{len(tracks)} song{'s' if len(tracks) != 1 else ''} · "
                   f"{format_total(sum(t.duration for t in tracks))}")
        self.subtitle.set_text(f"{summary} · Hidden from All Songs" if hidden else summary)
        # Setting the same state doesn't emit "toggled", so this never re-hides or re-shows.
        self.hide_toggle.set_active(hidden)
        self.hide_toggle.set_tooltip_text("Show in All Songs and Queue" if hidden
                                          else "Hide from All Songs and Queue")

        def apply(texture):
            """Show the artist's picture (None falls back to initials)."""
            self.avatar.set_custom_image(texture)  # None falls back to initials
        self.win.art.request_artist(self.artist, tracks[0], 128, apply)
        return True

    def _play(self, shuffle):
        """Play all of the artist's songs, in order or shuffled."""
        if shuffle:
            self.win.player.shuffle_play(self.tracks)
        else:
            self.win.player.set_shuffle(False)
            self.win.player.play_tracks(self.tracks, 0)


class AlbumPage(Adw.NavigationPage):
    """An album's songs in track order."""

    def __init__(self, view: ArtistsView, key):
        super().__init__(title=key[1], tag=f"album:{key[0]}\0{key[1]}")
        self.view = view
        self.win = view.win
        self.key = tuple(key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(back_button(key[0], lambda: view.nav.pop()))

        header = Gtk.Box(spacing=20)
        header.add_css_class("page-header")
        self.cover = CoverArt(self.win.art, 160, art_size=LARGE, large=True)
        header.append(self.cover)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        kind = Gtk.Label(label="ALBUM", xalign=0)
        kind.add_css_class("eyebrow")
        title = Gtk.Label(label=key[1], xalign=0, wrap=True, lines=2, ellipsize=3)
        title.add_css_class("page-title")
        artist = Gtk.Button(label=key[0], halign=Gtk.Align.START)
        artist.add_css_class("flat")
        artist.add_css_class("artist-link")
        artist.connect("clicked", lambda *_: view.nav.pop())
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.add_css_class("page-subtitle")
        for w in (kind, title, artist, self.subtitle):
            info.append(w)
        buttons = play_buttons(lambda: self.songs.play_all(False), lambda: self.songs.play_all(True))
        buttons.set_margin_top(8)
        buttons.set_halign(Gtk.Align.START)
        info.append(buttons)
        header.append(info)
        self.header = header
        follow_compact(self.win, self, AlbumPage._apply_compact)
        box.append(header)

        self.songs = SongList(self.win, show_header=False, show_track_no=True)
        self.songs.set_vexpand(True)
        box.append(self.songs)
        self.set_child(NarrowColumn(box))
        self.refresh()

    @staticmethod
    def _apply_compact(page, level):
        """Phone-sized windows: a smaller cover."""
        # Phone-sized: a smaller cover beside the details, so the songs keep room below.
        page.cover.set_size(96 if level == 2 else 160)
        page.header.set_spacing(14 if level == 2 else 20)

    def refresh(self) -> bool:
        """Reload the songs and summary. False if the album is gone."""
        album = self.win.library.find_album(self.key)
        if album is None:
            return False
        self.songs.set_items(album.tracks)
        self.cover.set_track(album.tracks[0])
        n = len(album.tracks)
        parts = [album.year] if album.year else []
        parts += [f"{n} song{'s' if n != 1 else ''}", format_total(album.duration)]
        self.subtitle.set_text(" · ".join(parts))
        return True
