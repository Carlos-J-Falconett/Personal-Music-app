"""In-memory library model shared by every view. One TrackItem object exists per track,
so favorite / now-playing state changes show up everywhere at once."""

from gi.repository import GObject

from .database import Database


def format_time(seconds: float) -> str:
    """Seconds as "m:ss", or "h:mm:ss" from an hour up."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_total(seconds: float) -> str:
    """A total length for page subtitles: "42 min" or "2 h 5 min"."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min"


UNKNOWN_GENRE = "Unknown Genre"  # the Genres tab's group for songs without a genre tag


def sort_key(item):
    """The library's natural order: artist, album, disc, track number, title."""
    return (item.artist_key.casefold(), item.album.casefold(), item.disc_no, item.track_no, item.title_key)


class TrackItem(GObject.Object):
    """One song, shared by every view. `favorite` and `playing` are GObject properties, so every list
    showing the song updates when they change. Built from (and refreshed by) a database row."""
    __gtype_name__ = "TrackItem"

    favorite = GObject.Property(type=bool, default=False)
    playing = GObject.Property(type=bool, default=False)

    def __init__(self, row):
        super().__init__()
        self.id = row["id"]
        self.update(row)

    def update(self, row):
        """Refresh the fields from a database row (after a rescan re-read its tags)."""
        self.path = row["path"]
        self.dir = row["dir"]
        self.title = row["title"]
        self.artist = row["artist"]
        self.album = row["album"]
        self.album_artist = row["album_artist"]
        self.track_no = row["track_no"]
        self.disc_no = row["disc_no"]
        self.duration = row["duration"]
        self.genre = row["genre"]
        self.year = row["year"]
        self.duration_text = format_time(self.duration)
        self.title_key = self.title.casefold()  # for sorting
        # Title, artists, album and genre: searching a genre finds its songs too.
        self.search_text = (f"{self.title}\n{self.artist}\n{self.album}\n{self.album_artist}\n"
                            f"{self.genre}").casefold()

    @property
    def artist_key(self) -> str:
        """Artist used for grouping in the Artists view."""
        return self.album_artist or self.artist

    @property
    def genre_key(self) -> str:
        """Genre used for grouping in the Genres view (UNKNOWN_GENRE when the tag is empty)."""
        return self.genre.strip() or UNKNOWN_GENRE


class AlbumItem(GObject.Object):
    """An album by one artist (grouped on the artist used by the Artists view)."""

    __gtype_name__ = "AlbumItem"

    def __init__(self, title: str, artist: str, tracks: list[TrackItem]):
        super().__init__()
        self.title = title
        self.artist = artist
        self.tracks = tracks
        years = [t.year for t in tracks if t.year]
        self.year = min(years) if years else ""
        self.duration = sum(t.duration for t in tracks)
        self.search_text = f"{title}\n{artist}".casefold()

    @property
    def key(self):
        """(artist, album title): identifies the album for navigation (Library.find_album)."""
        return (self.artist, self.title)


def _group_albums(tracks: list[TrackItem]) -> list[AlbumItem]:
    """Group tracks into albums by (grouping artist, album title), sorted by artist, year, title."""
    groups: dict[tuple[str, str], list[TrackItem]] = {}
    for t in tracks:
        groups.setdefault((t.artist_key, t.album), []).append(t)
    albums = [AlbumItem(album, artist, items) for (artist, album), items in groups.items()]
    albums.sort(key=lambda a: (a.artist.casefold(), a.year or "9999", a.title.casefold()))
    return albums


class Library(GObject.Object):
    """The in-memory library: one TrackItem per track, the grouped artists and albums (cached until
    the next reload), and favorites and playlists (stored in the database, changes announced by signals).

    Hidden artists (`hidden_artists`, artist_key names saved in the "hidden_artists" setting) are left
    out of `visible_items`, the list All Songs shows and the library queue is built from, and out of
    the Genres view (genres()). Their songs stay everywhere else (Artists, albums, playlists,
    Favorites, Search)."""
    __gsignals__ = {
        # Tracks were added/removed/retagged.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "favorites-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "playlists-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # playlist id whose contents changed
        "playlist-contents-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # artist name that was hidden or shown again (see set_artist_hidden)
        "hidden-artists-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.items: dict[int, TrackItem] = {}
        self.sorted_items: list[TrackItem] = []
        self.visible_items: list[TrackItem] = []  # sorted_items without hidden artists' songs
        self.hidden_artists: set[str] = set(db.get_setting("hidden_artists", []))
        self.reload(emit=False)

    def reload(self, emit=True):
        """Re-read every track from the database, keeping existing TrackItem objects so views stay bound."""
        seen = set()
        for row in self.db.all_tracks():
            item = self.items.get(row["id"])
            if item is None:
                self.items[row["id"]] = TrackItem(row)
            else:
                item.update(row)
            seen.add(row["id"])
        for tid in [t for t in self.items if t not in seen]:
            del self.items[tid]
        favorites = set(self.db.favorite_ids())
        for tid, item in self.items.items():
            want = tid in favorites
            if item.favorite != want:
                item.favorite = want
        self.sorted_items = sorted(self.items.values(), key=sort_key)
        self._update_visible()
        self._albums = None
        self._artists = None
        if emit:
            self.emit("changed")

    def get(self, track_id: int) -> TrackItem | None:
        """The TrackItem for an id, or None."""
        return self.items.get(track_id)

    def resolve(self, track_ids) -> list[TrackItem]:
        """TrackItems for ids, skipping ids no longer in the library."""
        return [self.items[t] for t in track_ids if t in self.items]

    # ---- artists ----

    def artists(self) -> list[tuple[str, list[TrackItem]]]:
        """(artist, tracks) sorted by name. Cached until the next reload."""
        if self._artists is None:
            groups: dict[str, list[TrackItem]] = {}
            for item in self.sorted_items:
                groups.setdefault(item.artist_key, []).append(item)
            self._artists = sorted(groups.items(), key=lambda kv: kv[0].casefold())
            self._artist_index = dict(self._artists)
            self._artist_folded = [name.casefold() for name, _tracks in self._artists]
        return self._artists

    def find_artists(self, terms: list[str]) -> list[tuple[str, list[TrackItem]]]:
        """Artists whose name contains every (casefolded) term."""
        artists = self.artists()
        return [a for a, folded in zip(artists, self._artist_folded) if all(t in folded for t in terms)]

    def artist_tracks(self, name: str) -> list[TrackItem]:
        """An artist's tracks in library order."""
        self.artists()
        return list(self._artist_index.get(name, []))

    # ---- hidden artists ----

    def is_hidden(self, name: str) -> bool:
        """Whether this artist is hidden from All Songs and the library queue."""
        return name in self.hidden_artists

    def set_artist_hidden(self, name: str, hidden: bool):
        """Hide an artist from All Songs and the library queue, or show them again (saved, and
        announced with hidden-artists-changed)."""
        if hidden == self.is_hidden(name):
            return
        if hidden:
            self.hidden_artists.add(name)
        else:
            self.hidden_artists.discard(name)
        self.db.set_setting("hidden_artists", sorted(self.hidden_artists))
        self._update_visible()
        self.emit("hidden-artists-changed", name)

    def _update_visible(self):
        """Rebuild visible_items: the library order, without hidden artists' songs."""
        hidden = self.hidden_artists
        self.visible_items = [t for t in self.sorted_items if t.artist_key not in hidden]
        self._genres = None  # genres are built from visible_items

    # ---- genres ----

    def genres(self) -> list[tuple[str, list[TrackItem]]]:
        """(genre, tracks) sorted by name, Unknown Genre last, without hidden artists' songs.
        Cached until the next reload or hidden-artist change."""
        if self._genres is None:
            groups: dict[str, list[TrackItem]] = {}
            for item in self.visible_items:
                groups.setdefault(item.genre_key, []).append(item)
            self._genres = sorted(groups.items(), key=lambda kv: (kv[0] == UNKNOWN_GENRE, kv[0].casefold()))
            self._genre_index = dict(self._genres)
            self._genre_folded = [name.casefold() for name, _tracks in self._genres]
        return self._genres

    def find_genres(self, terms: list[str]) -> list[tuple[str, list[TrackItem]]]:
        """Genres whose name contains every (casefolded) term."""
        genres = self.genres()
        return [g for g, folded in zip(genres, self._genre_folded) if all(t in folded for t in terms)]

    def genre_tracks(self, name: str) -> list[TrackItem]:
        """A genre's tracks in library order (without hidden artists)."""
        self.genres()
        return list(self._genre_index.get(name, []))

    # ---- albums ----

    def all_albums(self) -> list[AlbumItem]:
        """Every album, sorted by artist, then year, then title. Cached until the next reload."""
        if self._albums is None:
            self._albums = _group_albums(self.sorted_items)
            self._albums_by_key = {a.key: a for a in self._albums}
            self._albums_by_artist: dict[str, list[AlbumItem]] = {}
            for a in self._albums:
                self._albums_by_artist.setdefault(a.artist, []).append(a)
        return self._albums

    def albums_for_artist(self, name: str) -> list[AlbumItem]:
        """An artist's albums, oldest first."""
        self.all_albums()
        return list(self._albums_by_artist.get(name, []))

    def find_album(self, key) -> AlbumItem | None:
        """The album with this (artist, title) key, or None."""
        self.all_albums()
        return self._albums_by_key.get(tuple(key))

    # ---- favorites ----

    def favorite_items(self) -> list[TrackItem]:
        """Favorite tracks, most recently favorited first."""
        return self.resolve(self.db.favorite_ids())

    def set_favorite(self, item: TrackItem, favorite: bool):
        """Mark or unmark a favorite (saved, and announced with favorites-changed)."""
        if item.favorite == favorite:
            return
        self.db.set_favorite(item.id, favorite)
        item.favorite = favorite
        self.emit("favorites-changed")

    def toggle_favorite(self, item: TrackItem):
        self.set_favorite(item, not item.favorite)

    # ---- playlists ----

    def playlists(self):
        """Every playlist as rows of (id, name, count)."""
        return self.db.playlists()

    def playlist_name(self, playlist_id: int) -> str | None:
        """A playlist's name, or None if it doesn't exist."""
        for p in self.db.playlists():
            if p["id"] == playlist_id:
                return p["name"]
        return None

    def playlist_items(self, playlist_id: int) -> list[TrackItem]:
        """A playlist's tracks in order."""
        return self.resolve(self.db.playlist_track_ids(playlist_id))

    def playlists_with(self, item: TrackItem) -> set[int]:
        """Ids of the playlists containing this track."""
        return self.db.playlists_with(item.id)

    def create_playlist(self, name: str) -> int:
        pid = self.db.create_playlist(name)
        self.emit("playlists-changed")
        return pid

    def rename_playlist(self, playlist_id: int, name: str):
        self.db.rename_playlist(playlist_id, name)
        self.emit("playlists-changed")

    def delete_playlist(self, playlist_id: int):
        self.db.delete_playlist(playlist_id)
        self.emit("playlists-changed")

    def add_to_playlist(self, playlist_id: int, items: list[TrackItem]) -> int:
        """Append tracks not already in the playlist; returns how many were added."""
        added = self.db.add_to_playlist(playlist_id, [i.id for i in items])
        if added:
            self.emit("playlists-changed")
            self.emit("playlist-contents-changed", playlist_id)
        return added

    def remove_from_playlist(self, playlist_id: int, items: list[TrackItem]):
        self.db.remove_from_playlist(playlist_id, [i.id for i in items])
        self.emit("playlists-changed")
        self.emit("playlist-contents-changed", playlist_id)
