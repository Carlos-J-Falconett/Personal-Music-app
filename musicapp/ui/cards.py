"""Artist, album and genre cards, shared by the Artists and Genres pages and search results.
Artist cards given the lyrics service show a "!" after the name when some songs have no lyrics."""

from gi.repository import Adw, Gtk

from ..art import THUMB
from .widgets import CoverArt, lyrics_missing_mark

ALBUM_COVER_SIZE = 160
ARTIST_AVATAR_SIZE = 128


class AlbumCard(Gtk.Box):
    """An album in a grid: cover, title, and year with the song count (or the artist, in search)."""

    def __init__(self, art, show_artist: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        self.add_css_class("album-card")
        self.album = None
        self.show_artist = show_artist
        self.cover = CoverArt(art, ALBUM_COVER_SIZE, art_size=THUMB)
        self.cover.set_margin_bottom(4)
        self.title = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=18, width_chars=18)
        self.title.add_css_class("album-title")
        self.subtitle = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=18, width_chars=18)
        self.subtitle.add_css_class("dim")
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("card-detail")
        for w in (self.cover, self.title, self.subtitle):
            self.append(w)

    def set_album(self, album):
        """Show an album (cards are reused as the grid scrolls)."""
        self.album = album
        self.cover.set_track(album.tracks[0] if album.tracks else None)
        self.title.set_text(album.title)
        self.set_tooltip_text(f"{album.title}\n{album.artist}")
        n = len(album.tracks)
        detail = album.artist if self.show_artist else f"{n} song{'s' if n != 1 else ''}"
        self.subtitle.set_text(f"{album.year} · {detail}" if album.year else detail)


def missing_lyrics_text(n: int) -> str:
    """The tooltip of an artist's "!": how many of their songs have no lyrics."""
    return f"{n} song{'s' if n != 1 else ''} without lyrics"


class ArtistCard(Gtk.Box):
    """An artist in a grid: round picture, name and song count ("· Hidden" when hidden from All Songs).
    With `lyrics` (the LyricsService), a "!" after the name counts the songs without lyrics."""

    def __init__(self, art, lyrics=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        self.art = art
        self.lyrics = lyrics
        self.name = None
        self.tracks: list = []
        self.avatar = Adw.Avatar(size=ARTIST_AVATAR_SIZE, show_initials=True)
        self.label = Gtk.Label(ellipsize=3, max_width_chars=16, justify=Gtk.Justification.CENTER)
        self.label.add_css_class("artist-name")
        # The name, then the "!" (hidden while every song has lyrics, or without `lyrics`).
        name_row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        name_row.append(self.label)
        self.no_lyrics = lyrics_missing_mark()
        name_row.append(self.no_lyrics)
        self.count = Gtk.Label()
        self.count.add_css_class("dim")
        self.count.add_css_class("caption")
        self.count.add_css_class("card-detail")
        for w in (self.avatar, name_row, self.count):
            self.append(w)

    def set_artist(self, name: str, tracks: list, hidden: bool = False):
        """Show an artist (cards are reused as the grid scrolls). `hidden`: the artist is hidden from
        All Songs and the library queue (Library.set_artist_hidden)."""
        self.name = name
        self.tracks = tracks
        self.avatar.set_text(name)
        self.avatar.set_custom_image(None)
        self.label.set_text(name)
        self.label.set_tooltip_text(name)
        n = len(tracks)
        count = f"{n} song{'s' if n != 1 else ''}"
        self.count.set_text(f"{count} · Hidden" if hidden else count)
        self.sync_lyrics()

        def apply(texture, token=name):
            """Show the picture, unless the card now shows another artist."""
            if self.name == token and texture is not None:
                self.avatar.set_custom_image(texture)

        self.art.request_artist(name, tracks[0] if tracks else None, THUMB, apply)

    def sync_lyrics(self):
        """Show the "!" (with how many songs have no lyrics in its tooltip) if any do."""
        missing = len(self.lyrics.missing_tracks(self.tracks)) if self.lyrics is not None else 0
        self.no_lyrics.set_visible(missing > 0)
        self.no_lyrics.set_tooltip_text(missing_lyrics_text(missing) if missing else None)


class GenreCard(Gtk.Box):
    """A genre in a grid: the cover of its first song, its name, and its song and artist counts."""

    def __init__(self, art):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
        self.add_css_class("album-card")
        self.name = None
        self.cover = CoverArt(art, ALBUM_COVER_SIZE, art_size=THUMB)
        self.cover.set_margin_bottom(4)
        self.title = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=18, width_chars=18)
        self.title.add_css_class("genre-name")  # its own theme colour, "Genre Name"
        self.subtitle = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=18, width_chars=18)
        self.subtitle.add_css_class("dim")
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("card-detail")
        for w in (self.cover, self.title, self.subtitle):
            self.append(w)

    def set_genre(self, name: str, tracks: list):
        """Show a genre (cards are reused as the grid scrolls)."""
        self.name = name
        self.cover.set_track(tracks[0] if tracks else None)
        self.title.set_text(name)
        self.set_tooltip_text(name)
        n, artists = len(tracks), len({t.artist_key for t in tracks})
        self.subtitle.set_text(f"{n} song{'s' if n != 1 else ''} · {artists} artist{'s' if artists != 1 else ''}")


def card_button(card: Gtk.Widget, on_click) -> Gtk.Button:
    """Wrap a card in a flat button (used where cards aren't inside a GridView)."""
    button = Gtk.Button(child=card, valign=Gtk.Align.START)
    button.add_css_class("flat")
    button.add_css_class("card-button")
    button.connect("clicked", lambda *_: on_click())
    return button
