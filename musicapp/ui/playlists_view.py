"""Playlists tab: list of playlists → a playlist's songs."""

from gi.repository import Adw, Gio, GLib, Gtk

from ..library import format_total
from .artists_view import back_button
from .song_list import SongList
from .widgets import NarrowColumn, icon_button

ROOT_TAG = "playlists"


class PlaylistsView(Adw.Bin):
    """Wraps an Adw.NavigationView (which can't be subclassed)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Gtk.Box(spacing=12)
        header.add_css_class("page-header")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="Playlists", xalign=0)
        title.add_css_class("page-title")
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("page-subtitle")
        titles.append(title)
        titles.append(self.count_label)
        header.append(titles)
        new = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label="New Playlist"),
                         valign=Gtk.Align.CENTER, action_name="win.new-playlist")
        new.add_css_class("suggested-action")
        new.add_css_class("pill")
        header.append(new)
        box.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START,
                                margin_start=24, margin_end=24, margin_bottom=24)
        self.list.add_css_class("boxed-list")
        self.list.add_css_class("playlists-list")
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(child=self.list, maximum_size=900))
        empty = Adw.StatusPage(icon_name="musicapp-playlist-symbolic", title="No Playlists Yet",
                               description="Create a playlist, then right-click songs to add them")
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(scroller, "list")
        self.stack.add_named(empty, "empty")
        box.append(self.stack)
        box.add_css_class("content-glass")
        box.set_overflow(Gtk.Overflow.HIDDEN)  # clip to the rounded card
        self.nav.add(Adw.NavigationPage(title="Playlists", tag=ROOT_TAG, child=box))

    def refresh(self):
        library = self.win.library
        playlists = library.playlists()
        contents = library.db.playlist_contents()
        self.list.remove_all()
        for p in playlists:
            self.list.append(self._row(p["id"], p["name"], library.resolve(contents.get(p["id"], []))))
        n = len(playlists)
        self.count_label.set_text(f"{n} playlist{'s' if n != 1 else ''}")
        self.stack.set_visible_child_name("list" if playlists else "empty")
        visible = self.nav.get_visible_page()
        if isinstance(visible, PlaylistPage) and not visible.refresh():
            self.nav.pop_to_tag(ROOT_TAG)

    def _row(self, pid: int, name: str, items: list) -> Adw.ActionRow:
        row = Adw.ActionRow(use_markup=False, activatable=True, title_lines=1)
        row.add_css_class("playlist-row")
        row.set_title(name)
        row.set_subtitle(f"{len(items)} song{'s' if len(items) != 1 else ''}"
                         + (f" · {format_total(sum(i.duration for i in items))}" if items else ""))
        play = icon_button("media-playback-start-symbolic", "Play", circular=True)
        play.set_action_name("win.play-playlist")
        play.set_action_target_value(GLib.Variant("i", pid))
        play.set_sensitive(bool(items))
        row.add_suffix(play)
        menu = Gio.Menu()
        menu.append("Rename…", f"win.rename-playlist({pid})")
        menu.append("Delete…", f"win.delete-playlist({pid})")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text="More")
        more.add_css_class("flat")
        row.add_suffix(more)
        row.connect("activated", lambda *_: self.show_playlist(pid))
        return row

    def show_playlist(self, pid: int):
        visible = self.nav.get_visible_page()
        if isinstance(visible, PlaylistPage) and visible.playlist_id == pid:
            return
        self.nav.replace([self.nav.find_page(ROOT_TAG), PlaylistPage(self, pid)])

    @property
    def open_playlist_id(self):
        visible = self.nav.get_visible_page()
        return visible.playlist_id if isinstance(visible, PlaylistPage) else None


class PlaylistPage(Adw.NavigationPage):
    def __init__(self, view: PlaylistsView, pid: int):
        super().__init__(title="Playlist", tag=f"playlist:{pid}")
        self.view = view
        self.win = view.win
        self.playlist_id = pid

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(back_button("Playlists", lambda: view.nav.pop_to_tag(ROOT_TAG)))
        self.songs = SongList(
            self.win,
            empty_icon="musicapp-playlist-symbolic",
            empty_title="This Playlist Is Empty",
            empty_description="Right-click songs anywhere and choose “Add to Playlist”",
        )
        self.songs.playlist_id = pid
        self.songs.set_vexpand(True)
        actions = self.songs.header_actions
        actions.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))
        rename = icon_button("document-edit-symbolic", "Rename Playlist", circular=True)
        rename.connect("clicked", lambda *_: self.win.rename_playlist(pid))
        delete = icon_button("user-trash-symbolic", "Delete Playlist", circular=True)
        delete.connect("clicked", lambda *_: self.win.delete_playlist(pid))
        actions.append(rename)
        actions.append(delete)
        box.append(self.songs)
        self.set_child(NarrowColumn(box))
        self.refresh()

    def refresh(self) -> bool:
        name = self.win.library.playlist_name(self.playlist_id)
        if name is None:
            return False
        self.set_title(name)
        self.songs.set_title(name)
        self.songs.set_items(self.win.library.playlist_items(self.playlist_id))
        return True

